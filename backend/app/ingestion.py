import csv
from datetime import datetime
from decimal import Decimal
import io
import json
from pathlib import Path
import zipfile
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import insert, select, update

from app.config import DATA_DIR, MAX_ROWS
from app.db import Audit, Entity, ImportBatch, Job, Link, Revision, Session, new_id, now
from app.domain import fingerprint, safe_cell
from app.errors import BusinessError
from app.schemas import CONTRACTS
from app.store import NATURAL_KEYS, require_source, save

IMPORTABLE = {"keywords", "listings", "prices", "offers", "funnels", "orders", "transactions", "signals"}


def file_rows(path, sheet=None):
    path = Path(path)
    if path.suffix == ".csv":
        with path.open("rb") as raw:
            sample = raw.read(16384)
            raw.seek(0)
            try:
                sample.decode("utf-8-sig")
                encoding = "utf-8-sig"
            except UnicodeDecodeError:
                encoding = "gb18030"
            with io.TextIOWrapper(raw, encoding=encoding, newline="") as text:
                reader = csv.reader(text)
                headers = next(reader, [])
                validate_headers(headers)
                for number, values in enumerate(reader, 2):
                    if not any(v != "" for v in values):
                        continue
                    if len(values) != len(headers):
                        yield number, {"__error__": "列数与标题不一致"}
                    else:
                        yield number, dict(zip(headers, values, strict=True))
    else:
        from openpyxl import load_workbook

        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 250 * 1024 * 1024 or len(z.infolist()) > 1000:
                raise BusinessError("ARCHIVE_TOO_LARGE", "XLSX 解压后超过限制")
            if any("vbaProject" in i.filename for i in z.infolist()):
                raise BusinessError("MACRO_NOT_ALLOWED", "不接受包含宏的工作簿")
        workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            if sheet and sheet not in workbook.sheetnames:
                raise BusinessError("SHEET_NOT_FOUND", "工作表不存在")
            selected = workbook[sheet] if sheet else workbook.worksheets[0]
            iterator = selected.iter_rows()
            headers = [str(c.value).strip() if c.value is not None else "" for c in next(iterator, [])]
            validate_headers(headers)
            for number, cells in enumerate(iterator, 2):
                if all(c.value is None for c in cells):
                    continue
                values = {}
                for key, cell in zip(headers, cells, strict=False):
                    values[key] = "__FORMULA__" if cell.data_type == "f" else cell.value
                yield number, values
        finally:
            workbook.close()


def validate_headers(headers):
    if not headers or any(not h for h in headers) or len(set(headers)) != len(headers):
        raise BusinessError("INVALID_HEADERS", "列标题不能为空或重复")
    if len(headers) > 150:
        raise BusinessError("TOO_MANY_COLUMNS", "最多支持150列")


def preview(path, sheet=None):
    output = []
    for _number, data in file_rows(path, sheet):
        output.append(data)
        if len(output) >= 20:
            break
    sheets = []
    if Path(path).suffix == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, keep_links=False)
        sheets = wb.sheetnames
        wb.close()
    return {"headers": list(output[0]) if output else [], "rows": output, "sheets": sheets}


def map_row(kind, raw, mapping, options, source_id, evidence_id):
    if "__error__" in raw:
        raise ValueError(raw["__error__"])
    defaults = options.get("defaults", {})
    values = dict(defaults)
    for field, column in mapping.items():
        value = raw.get(column)
        if value is None or value == "":
            continue
        if value == "__FORMULA__" or isinstance(value, str) and value.startswith("="):
            raise ValueError("FORMULA_NOT_ALLOWED: 计算列不能作为原始输入")
        values[field] = value
    fields = CONTRACTS[kind].model_fields
    for field in list(values):
        if field not in fields:
            raise ValueError("UNKNOWN_FIELD: " + field)
        value = values[field]
        annotation = str(fields[field].annotation)
        if "datetime" in annotation:
            dt = (
                value
                if isinstance(value, datetime)
                else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo(options.get("timezone", "Asia/Shanghai")))
            values[field] = dt
        elif "Decimal" in annotation:
            text = str(value).strip()
            if options.get("percent_unit") == "percent" and field in {"conversion", "mall_click_share", "r"}:
                text = str(Decimal(text.rstrip("%")) / 100)
            values[field] = text
        elif "dict" in annotation and isinstance(value, str):
            values[field] = json.loads(value)
        elif annotation == "<class 'bool'>" and isinstance(value, str):
            lookup = {"true": True, "false": False, "1": True, "0": False, "是": True, "否": False}
            if value.lower() not in lookup:
                raise ValueError("INVALID_BOOLEAN: " + field)
            values[field] = lookup[value.lower()]
        elif "str" in annotation:
            values[field] = str(value)
    if "source_id" in fields:
        if values.get("source_id") not in {None, source_id}:
            raise ValueError("SOURCE_MISMATCH")
        values["source_id"] = source_id
    if values.get("evidence_id") not in {None, evidence_id}:
        raise ValueError("EVIDENCE_MISMATCH: 文件导入使用当前原始文件作为证据")
    values["evidence_id"] = evidence_id
    data = CONTRACTS[kind].model_validate(values).model_dump(mode="json")
    data["evidence_version"] = 1
    if kind == "keywords" and datetime.fromisoformat(
        data["window_end"].replace("Z", "+00:00")
    ) > datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00")):
        raise ValueError("INVALID_WINDOW: 统计窗口尚未结束")
    if kind == "transactions":
        data["cancelled"] = data["invoice_no"].upper().startswith("C") or data["quantity"] < 0
        data["cancellation_status"] = "UNMATCHED" if data["cancelled"] else "SALE"
        data["amount"] = str(data["quantity"] * Decimal(data["unit_price"]))
        data["contribution"] = None
    return data


def assert_lease(db, job_id, token):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job or job.status != "RUNNING" or job.lease_token != token:
        raise BusinessError("LEASE_LOST", "任务已取消或被其他工作进程接管", 409)
    from datetime import timedelta

    job.lease_until = now() + timedelta(seconds=120)
    return job


def bulk_snapshots(db, workspace, kind, payloads):
    keys = {fingerprint([kind, *[p.get(k) for k in NATURAL_KEYS[kind]]]): p for p in payloads}
    existing = {
        r.external_key: r
        for r in db.scalars(
            select(Entity).where(
                Entity.workspace == workspace,
                Entity.kind == kind,
                Entity.external_key.in_(list(keys)),
            )
        )
    }
    inserts, revisions, audits, links = [], [], [], []
    accepted, unchanged = 0, 0
    stamp = now()
    for key, data in keys.items():
        old = existing.get(key)
        strip = lambda p: {
            k: v for k, v in p.items() if k not in {"evidence_id", "evidence_version", "_import"}
        }
        if old and strip(old.data) == strip(data):
            unchanged += 1
            continue
        entity_id, version = (old.id, old.version + 1) if old else (new_id(), 1)
        if old:
            updated = db.execute(
                update(Entity)
                .where(Entity.id == old.id, Entity.version == old.version)
                .values(version=version, data=data, updated_at=stamp)
            )
            if updated.rowcount != 1:
                raise BusinessError("VERSION_CONFLICT", "导入期间记录被修改，请重试", 409)
            from sqlalchemy import delete

            db.execute(delete(Link).where(Link.workspace == workspace, Link.from_id == old.id))
        else:
            inserts.append(
                {
                    "id": entity_id,
                    "workspace": workspace,
                    "kind": kind,
                    "external_key": key,
                    "data": data,
                    "version": version,
                    "created_at": stamp,
                    "updated_at": stamp,
                }
            )
        revisions.append(
            {
                "id": new_id(),
                "workspace": workspace,
                "entity_id": entity_id,
                "version": version,
                "data": data,
                "actor": "owner",
                "created_at": stamp,
            }
        )
        audits.append(
            {
                "id": new_id(),
                "workspace": workspace,
                "entity_id": entity_id,
                "action": "import",
                "actor": "owner",
                "details": {"kind": kind, "version": version},
                "created_at": stamp,
            }
        )
        for field in ["source_id", "evidence_id"]:
            links.append({"workspace": workspace, "from_id": entity_id, "field": field, "to_id": data[field]})
        accepted += 1
    if inserts:
        db.execute(insert(Entity), inserts)
    if revisions:
        db.execute(insert(Revision), revisions)
        db.execute(insert(Audit), audits)
        db.execute(insert(Link), links)
    return accepted, unchanged


def execute_import(job_id, token):
    with Session.begin() as db:
        job = assert_lease(db, job_id, token)
        batch = db.get(ImportBatch, job.payload["batch_id"])
        require_source(db, batch.workspace, batch.source_id, batch.kind)
        if batch.kind == "transactions" and batch.workspace != "public":
            raise BusinessError("HISTORY_SPACE_REQUIRED", "历史交易只能导入公开历史空间")
        evidence, _ = save(
            db,
            batch.workspace,
            "evidence",
            {
                "source_id": batch.source_id,
                "locator": "sha256:" + batch.file_hash,
                "observed_at": batch.created_at.isoformat(),
                "note": "导入原始文件与列映射，文件哈希已保存",
            },
            upsert=True,
        )
        evidence_id = evidence.id
        batch.status = "VALIDATING"
        options, mapping, path = batch.options, batch.mapping, batch.path
        batch_id, workspace, kind, source_id = batch.id, batch.workspace, batch.kind, batch.source_id
        staged = bool(options.get("_staged"))
    stage_path = DATA_DIR / (batch_id + ".jsonl")
    errors_path = DATA_DIR / (batch_id + "-errors.csv")
    if staged and not stage_path.exists():
        raise BusinessError("STAGING_MISSING", "任务暂存文件缺失，请重新导入")
    if not staged:
        validate_stage(
            job_id, token, path, options, mapping, source_id, evidence_id, kind, stage_path, errors_path
        )
    with Session.begin() as db:
        assert_lease(db, job_id, token)
        batch = db.get(ImportBatch, batch_id)
        batch.status = "IMPORTING"
        processed = batch.options.get("_processed", 0)
    chunk = []
    with stage_path.open(encoding="utf-8") as stage:
        for index, line in enumerate(stage):
            if index < processed:
                continue
            chunk.append(json.loads(line))
            if len(chunk) >= 1000:
                import_chunk(job_id, token, batch_id, workspace, kind, source_id, chunk, errors_path)
                chunk = []
        if chunk:
            import_chunk(job_id, token, batch_id, workspace, kind, source_id, chunk, errors_path)
    with Session.begin() as db:
        job = assert_lease(db, job_id, token)
        batch = db.get(ImportBatch, batch_id)
        batch.status = "SUCCEEDED"
        job.status, job.progress = "SUCCEEDED", 100
        job.result = {
            "batch_id": batch_id,
            "accepted": batch.accepted,
            "rejected": batch.rejected,
            "unchanged": batch.unchanged,
        }
    stage_path.unlink(missing_ok=True)


def validate_stage(
    job_id, token, path, options, mapping, source_id, evidence_id, kind, stage_path, errors_path
):
    seen, rejected = set(), 0
    with (
        stage_path.open("w", encoding="utf-8") as stage,
        errors_path.open("w", newline="", encoding="utf-8-sig") as error_file,
    ):
        writer = csv.writer(error_file)
        writer.writerow(["row_number", "code", "message"])
        for count, (number, raw) in enumerate(file_rows(path, options.get("sheet")), 1):
            if count > MAX_ROWS:
                raise BusinessError("TOO_MANY_ROWS", f"单批最多{MAX_ROWS}行，请拆分文件")
            try:
                data = map_row(kind, raw, mapping, options, source_id, evidence_id)
                data["_import"] = {"batch_id": stage_path.stem, "row_number": number}
                if kind in NATURAL_KEYS:
                    key = fingerprint([data.get(k) for k in NATURAL_KEYS[kind]])
                    if key in seen:
                        raise ValueError("DUPLICATE_KEY: 文件内业务标识重复")
                    seen.add(key)
                stage.write(json.dumps({"row": number, "data": data}, ensure_ascii=False) + "\n")
            except (ValueError, TypeError, ValidationError) as exc:
                rejected += 1
                writer.writerow([number, "ROW_INVALID", safe_cell(str(exc)[:1200])])
            if count % 1000 == 0:
                with Session.begin() as db:
                    current = assert_lease(db, job_id, token)
                    current.progress = min(45, count * 45 // MAX_ROWS)
        error_file.flush()
    with Session.begin() as db:
        assert_lease(db, job_id, token)
        batch = db.get(ImportBatch, stage_path.stem)
        batch.status, batch.rejected, batch.errors_path = "IMPORTING", rejected, str(errors_path)
        batch.options = {**batch.options, "_staged": True, "_processed": 0}


def import_chunk(job_id, token, batch_id, workspace, kind, source_id, chunk, errors_path):
    with Session.begin() as db:
        job = assert_lease(db, job_id, token)
        require_source(db, workspace, source_id, kind)
        batch = db.get(ImportBatch, batch_id)
        if kind in {"keywords", "transactions"}:
            accepted, unchanged = bulk_snapshots(db, workspace, kind, [r["data"] for r in chunk])
            batch.accepted += accepted
            batch.unchanged += unchanged
        else:
            for item in chunk:
                try:
                    with db.begin_nested():
                        payload = {
                            k: v for k, v in item["data"].items() if k not in {"evidence_version", "_import"}
                        }
                        _row, status = save(
                            db,
                            workspace,
                            kind,
                            payload,
                            upsert=True,
                            import_metadata=item["data"].get("_import"),
                        )
                    if status == "unchanged":
                        batch.unchanged += 1
                    else:
                        batch.accepted += 1
                except (BusinessError, ValueError) as exc:
                    batch.rejected += 1
                    with errors_path.open("a", encoding="utf-8", newline="") as out:
                        csv.writer(out).writerow(
                            [item["row"], getattr(exc, "code", "ROW_INVALID"), safe_cell(str(exc)[:1200])]
                        )
        job.progress = min(95, 50 + (batch.accepted + batch.unchanged) * 45 // MAX_ROWS)
        batch.options = {**batch.options, "_processed": batch.options.get("_processed", 0) + len(chunk)}
