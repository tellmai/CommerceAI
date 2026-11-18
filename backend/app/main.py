import csv
from datetime import timedelta
import hashlib
import hmac
import io
import json
import logging
from pathlib import Path
import secrets
import time
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import Field, create_model
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import config
from app.db import Audit, ImportBatch, Job, LoginSession, Revision, Session, new_id, now
from app.domain import fingerprint, money, profit, safe_cell
from app.errors import BusinessError
from app.ingestion import IMPORTABLE, preview
from app.market_routes import register_market_routes
from app.opportunities import (
    ForecastBaseline,
    ForecastInput,
    ForecastResult,
    OpportunityPage,
    baseline,
    forecast,
    opportunities,
)
from app.schemas import (
    CONTRACTS,
    Cashflow,
    Contract,
    EntityOut,
    EntityPage,
    Login,
    MatchReview,
    ProfitInput,
    Transition,
)
from app.store import audit, get, latest, list_page, persist, require_source, row_out, save
from app.workflows import config_for, gate, review_match, transition

app = FastAPI(
    title="CommerceAI 本地经营研究 API",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
failed_logins = {}


@app.middleware("http")
async def request_context(request, call_next):
    request.state.request_id = new_id()
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > config.MAX_UPLOAD + 1024 * 1024:
        return JSONResponse(
            {
                "code": "FILE_TOO_LARGE",
                "message": "请求超过上传限制",
                "details": {},
                "request_id": request.state.request_id,
            },
            status_code=413,
        )
    try:
        response = await call_next(request)
    except Exception as exc:
        logging.getLogger("commerceai.api").error(
            "request_failed request_id=%s type=%s", request.state.request_id, type(exc).__name__
        )
        response = JSONResponse(
            {
                "code": "INTERNAL_ERROR",
                "message": "服务处理失败，请凭请求编号检查日志",
                "details": {},
                "request_id": request.state.request_id,
            },
            status_code=500,
        )
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(BusinessError)
async def business_error(request, exc):
    return JSONResponse(
        {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "request_id": request.state.request_id,
        },
        status_code=exc.status,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        {
            "code": "VALIDATION_ERROR",
            "message": "输入格式不正确，请检查字段",
            "request_id": request.state.request_id,
            "details": {"errors": [{"loc": list(e["loc"]), "message": e["msg"]} for e in exc.errors()]},
        },
        status_code=422,
    )


@app.exception_handler(IntegrityError)
async def integrity_error(request, _exc):
    return JSONResponse(
        {
            "code": "CONFLICT",
            "message": "记录重复、引用冲突或记录已被其他操作修改",
            "request_id": request.state.request_id,
            "details": {},
        },
        status_code=409,
    )


def database():
    with Session() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def workspace(x_workspace: str = Header("demo")):
    if x_workspace not in config.WORKSPACES:
        raise BusinessError("INVALID_WORKSPACE", "无效的数据空间")
    return x_workspace


def owner(request: Request, db=Depends(database, scope="function")):
    token = request.cookies.get("commerceai_session", "")
    session = db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest()) if token else None
    if not session or session.revoked or session.expires_at <= now():
        raise BusinessError("UNAUTHORIZED", "请先登录本地工作台", 401)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session.csrf):
            raise BusinessError("CSRF_REJECTED", "会话校验失败，请重新登录", 403)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise BusinessError("ORIGIN_REJECTED", "不允许此来源的写入请求", 403)
    return "owner"


Auth = Depends(owner)


@app.get("/api/health")
def health(db=Depends(database, scope="function")):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "postgresql", "version": "1.0.0"}


@app.post("/api/v1/auth/login")
def login(body: Login, request: Request, db=Depends(database, scope="function")):
    if not config.OWNER_PASSWORD:
        raise BusinessError("NOT_CONFIGURED", "尚未设置本机访问口令，请运行初始化脚本", 503)
    ip = request.client.host if request.client else "local"
    attempts = [t for t in failed_logins.get(ip, []) if time.monotonic() - t < 60]
    if len(attempts) >= 8:
        raise BusinessError("RATE_LIMITED", "登录尝试过多，请一分钟后重试", 429)
    origin = request.headers.get("origin")
    if origin and urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise BusinessError("ORIGIN_REJECTED", "不允许此来源的登录请求", 403)
    if not hmac.compare_digest(
        hashlib.sha256(body.password.encode()).digest(),
        hashlib.sha256(config.OWNER_PASSWORD.encode()).digest(),
    ):
        failed_logins[ip] = attempts + [time.monotonic()]
        raise BusinessError("INVALID_PASSWORD", "访问口令不正确", 401)
    failed_logins.pop(ip, None)
    token, csrf = secrets.token_urlsafe(40), secrets.token_hex(32)
    db.add(
        LoginSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            csrf=csrf,
            expires_at=now() + timedelta(hours=config.SESSION_HOURS),
        )
    )
    response = JSONResponse({"user": "owner", "csrf": csrf})
    for key, value, http_only in [("commerceai_session", token, True), ("commerceai_csrf", csrf, False)]:
        response.set_cookie(
            key,
            value,
            httponly=http_only,
            secure=config.COOKIE_SECURE,
            samesite="strict",
            max_age=config.SESSION_HOURS * 3600,
            path="/",
        )
    return response


@app.get("/api/v1/auth/me", dependencies=[Auth])
def me():
    return {"user": "owner"}


@app.post("/api/v1/auth/logout", dependencies=[Auth])
def logout(request: Request, db=Depends(database, scope="function")):
    token = request.cookies.get("commerceai_session", "")
    session = db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest())
    session.revoked = True
    response = JSONResponse({"ok": True})
    response.delete_cookie("commerceai_session")
    response.delete_cookie("commerceai_csrf")
    return response


@app.get("/api/v1/meta", dependencies=[Auth])
def meta():
    return {
        "workspaces": config.WORKSPACES,
        "resources": {kind: schema.model_json_schema() for kind, schema in CONTRACTS.items()},
        "importable": sorted(IMPORTABLE),
        "max_upload_mb": config.MAX_UPLOAD // 1024 // 1024,
        "max_import_rows": config.MAX_ROWS,
    }


@app.get("/api/v1/dashboard", dependencies=[Auth])
def get_dashboard(
    ws=Depends(workspace),
    db=Depends(database, scope="function"),
    start: str | None = None,
    end: str | None = None,
    category: str | None = None,
):
    from app.reporting import dashboard

    try:
        return dashboard(db, ws, start, end, category)
    except ValueError as exc:
        raise BusinessError("INVALID_DATE", "日期格式应为 YYYY-MM-DD") from exc


@app.post("/api/v1/profit/calculate", dependencies=[Auth])
def calculate_profit(body: ProfitInput):
    return profit(body.model_dump(mode="json"))


@app.get("/api/v1/opportunities", response_model=OpportunityPage, dependencies=[Auth], tags=["爆款推荐"])
def recommended_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    search: str = Query("", max_length=100),
    ws=Depends(workspace),
    db=Depends(database, scope="function"),
):
    return opportunities(db, ws, page, page_size, search)


@app.get(
    "/api/v1/opportunities/baseline",
    response_model=ForecastBaseline,
    dependencies=[Auth],
    tags=["爆款推荐"],
)
def forecast_baseline(
    candidate_id: str | None = None,
    report_id: str | None = None,
    ws=Depends(workspace),
    db=Depends(database, scope="function"),
):
    if bool(candidate_id) == bool(report_id):
        raise BusinessError("INVALID_FORECAST_SOURCE", "请选择一个候选或一个比价报告")
    return baseline(db, ws, candidate_id, report_id)


@app.post(
    "/api/v1/opportunities/forecast",
    response_model=ForecastResult,
    dependencies=[Auth],
    tags=["爆款推荐"],
)
def calculate_forecast(body: ForecastInput, ws=Depends(workspace), db=Depends(database, scope="function")):
    if ws == "public":
        raise BusinessError("SPACE_MODE_CONFLICT", "请在模拟演示或真实经营空间测算当前商品", 409)
    source = (
        baseline(db, ws, body.candidate_id, body.report_id) if body.candidate_id or body.report_id else None
    )
    return (
        forecast(body, source.reference_price, source.reference_count, source.notices)
        if source
        else forecast(body)
    )


@app.post("/api/v1/profit/cashflow", dependencies=[Auth])
def calculate_cashflow(body: Cashflow):
    return {
        "cash_required": money(
            body.daily_orders * body.upfront_cost * body.settlement_days
            + body.refund_reserve
            + body.advertising_budget
        )
    }


@app.post("/api/v1/candidates/{candidate_id}/transition", response_model=EntityOut, dependencies=[Auth])
def change_state(
    candidate_id: str, body: Transition, ws=Depends(workspace), db=Depends(database, scope="function")
):
    return row_out(transition(db, ws, candidate_id, body))


@app.get("/api/v1/candidates/{candidate_id}/research", dependencies=[Auth])
def research(candidate_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
    from app.selection import score_stale, strategies_for_candidate

    candidate = get(db, ws, candidate_id, "candidates")
    score = latest(db, ws, "scores", candidate_id=candidate_id)
    requirements = gate(db, ws, candidate)
    return {
        **strategies_for_candidate(db, ws, candidate_id),
        "requirements": requirements,
        "score": row_out(score) if score else None,
        "score_stale": score_stale(db, ws, score, candidate, requirements),
    }


@app.get("/api/v1/keywords/{keyword_id}/analysis", dependencies=[Auth])
def keyword_analysis(keyword_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
    from app.selection import keyword_analysis as analyze

    return analyze(db, ws, get(db, ws, keyword_id, "keywords"), config_for(db, ws).data["config"])


class ScoreRequest(Contract):
    strategy_id: str | None = None


@app.post("/api/v1/candidates/{candidate_id}/score", status_code=202, dependencies=[Auth])
def score_candidate(
    candidate_id: str,
    body: ScoreRequest,
    ws=Depends(workspace),
    db=Depends(database, scope="function"),
    idempotency_key: str = Header(default_factory=lambda: new_id(), alias="Idempotency-Key"),
):
    get(db, ws, candidate_id, "candidates")
    strategy = get(db, ws, body.strategy_id, "strategies") if body.strategy_id else config_for(db, ws)
    key = fingerprint([ws, "score", candidate_id, strategy.id, idempotency_key])
    job = db.scalar(select(Job).where(Job.idem_key == key))
    if not job:
        job = Job(
            workspace=ws,
            kind="score",
            idem_key=key,
            payload={"candidate_id": candidate_id, "strategy_id": strategy.id},
        )
        db.add(job)
        db.flush()
    return job_out(job)


@app.get("/api/v1/scores/{score_id}/replay", dependencies=[Auth])
def replay_score(score_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
    from app.selection import compute_score

    row = get(db, ws, score_id, "scores")
    return {
        "score_id": row.id,
        "input_hash": fingerprint(row.data["input_snapshot"]),
        "result": compute_score(row.data["input_snapshot"]),
    }


@app.post("/api/v1/matches/{match_id}/review", response_model=EntityOut, dependencies=[Auth])
def confirm_match(
    match_id: str, body: MatchReview, ws=Depends(workspace), db=Depends(database, scope="function")
):
    return row_out(review_match(db, ws, match_id, body))


@app.get("/api/v1/skus/{sku_id}/comparison", dependencies=[Auth])
def sku_comparison(
    sku_id: str,
    region: str = "全国",
    currency: str = "CNY",
    ws=Depends(workspace),
    db=Depends(database, scope="function"),
):
    from app.workflows import comparisons

    return comparisons(db, ws, sku_id, region, currency)


@app.get("/api/v1/experiments/{experiment_id}/summary", dependencies=[Auth])
def get_experiment_summary(experiment_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
    from app.reporting import experiment_summary

    return experiment_summary(db, ws, experiment_id)


@app.post("/api/v1/experiments/{experiment_id}/allocate-ads", dependencies=[Auth])
def allocate_experiment_ads(
    experiment_id: str, ws=Depends(workspace), db=Depends(database, scope="function")
):
    from app.reporting import allocate_ads

    return allocate_ads(db, ws, experiment_id)


def job_out(job):
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "attempts": job.attempts,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
    }


@app.get("/api/v1/jobs", dependencies=[Auth])
def jobs(ws=Depends(workspace), db=Depends(database, scope="function")):
    return {
        "items": [
            job_out(j)
            for j in db.scalars(
                select(Job).where(Job.workspace == ws).order_by(Job.created_at.desc()).limit(50)
            )
        ]
    }


@app.get("/api/v1/jobs/{job_id}", dependencies=[Auth])
def get_job(job_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
    job = db.get(Job, job_id)
    if not job or job.workspace != ws:
        raise BusinessError("NOT_FOUND", "任务不存在", 404)
    return job_out(job)


@app.post("/api/v1/jobs/{job_id}/cancel", dependencies=[Auth])
def cancel_job(job_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
    job = db.scalar(select(Job).where(Job.id == job_id, Job.workspace == ws).with_for_update())
    if not job:
        raise BusinessError("NOT_FOUND", "任务不存在", 404)
    if job.status in {"QUEUED", "RUNNING"}:
        job.status = "CANCELLED"
        if job.kind == "import":
            db.get(ImportBatch, job.payload["batch_id"]).status = "CANCELLED"
        if job.kind == "market_search":
            run = get(db, ws, job.payload["run_id"], "market_runs")
            persist(
                db,
                ws,
                "market_runs",
                {**run.data, "status": "CANCELLED"},
                entity=run,
                expected_version=run.version,
                action="cancel_market_search",
            )
        audit(db, ws, job.id, "cancel_job")
    return job_out(job)


def batch_out(batch):
    return {
        "id": batch.id,
        "kind": batch.kind,
        "filename": batch.filename,
        "status": batch.status,
        "accepted": batch.accepted,
        "rejected": batch.rejected,
        "unchanged": batch.unchanged,
        "source_id": batch.source_id,
        "file_hash": batch.file_hash,
        "created_at": batch.created_at,
    }


@app.get("/api/v1/imports", dependencies=[Auth])
def imports(ws=Depends(workspace), db=Depends(database, scope="function")):
    return {
        "items": [
            batch_out(b)
            for b in db.scalars(
                select(ImportBatch)
                .where(ImportBatch.workspace == ws)
                .order_by(ImportBatch.created_at.desc())
                .limit(50)
            )
        ]
    }


@app.post("/api/v1/imports/preview", dependencies=[Auth])
async def upload_preview(
    file: UploadFile = File(...),
    source_id: str = Form(...),
    kind: str = Form(...),
    sheet: str = Form(""),
    ws=Depends(workspace),
    db=Depends(database, scope="function"),
):
    if kind not in IMPORTABLE:
        raise BusinessError("INVALID_KIND", "此类数据不支持文件导入")
    require_source(db, ws, source_id, kind)
    name = file.filename or ""
    if "/" in name or "\\" in name or "\x00" in name or len(name) > 240:
        raise BusinessError("INVALID_FILENAME", "无效的文件名")
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise BusinessError("INVALID_FILE_TYPE", "仅接受 CSV 或 XLSX 文件")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = config.DATA_DIR / (new_id() + suffix)
    digest, size = hashlib.sha256(), 0
    try:
        with target.open("wb") as output:
            while content := await file.read(65536):
                size += len(content)
                if size > config.MAX_UPLOAD:
                    raise BusinessError("FILE_TOO_LARGE", "文件超过配置的上传限制")
                digest.update(content)
                output.write(content)
        info = preview(target, sheet or None)
        if not info["rows"]:
            raise BusinessError("EMPTY_FILE", "文件中没有可预览的数据")
    except Exception as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, BusinessError):
            raise
        raise BusinessError("FILE_PARSE_ERROR", "文件无法解析，请检查编码和格式") from exc
    batch = ImportBatch(
        workspace=ws,
        source_id=source_id,
        kind=kind,
        filename=name,
        file_hash=digest.hexdigest(),
        path=str(target),
        options={"sheet": sheet or None},
    )
    db.add(batch)
    db.flush()
    audit(db, ws, batch.id, "preview_import", {"kind": kind, "file_hash": batch.file_hash})
    return {**batch_out(batch), "preview": info}


class ImportConfirm(Contract):
    mapping: dict[str, str]
    defaults: dict = Field(default_factory=dict)
    timezone: str = "Asia/Shanghai"
    percent_unit: str = "fraction"


@app.post("/api/v1/imports/{batch_id}/confirm", status_code=202, dependencies=[Auth])
def confirm_import(
    batch_id: str, body: ImportConfirm, ws=Depends(workspace), db=Depends(database, scope="function")
):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    batch = db.scalar(
        select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.workspace == ws).with_for_update()
    )
    if not batch:
        raise BusinessError("NOT_FOUND", "导入批次不存在", 404)
    require_source(db, ws, batch.source_id, batch.kind)
    try:
        ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError as exc:
        raise BusinessError("INVALID_TIMEZONE", "无效的来源时区") from exc
    if body.percent_unit not in {"fraction", "percent"}:
        raise BusinessError("INVALID_UNIT", "比例单位只能选择 fraction 或 percent")
    fields = CONTRACTS[batch.kind].model_fields
    if any(k not in fields for k in [*body.mapping, *body.defaults]):
        raise BusinessError("UNKNOWN_FIELD", "映射中包含未定义的字段")
    options = {"sheet": batch.options.get("sheet"), **body.model_dump(exclude={"mapping"})}
    key = fingerprint([ws, batch.source_id, batch.kind, batch.file_hash, body.mapping, options])
    existing = db.scalar(select(ImportBatch).where(ImportBatch.import_key == key))
    if existing:
        job = db.scalar(select(Job).where(Job.idem_key == "import:" + existing.id))
        return job_out(job)
    if batch.status != "PREVIEW":
        raise BusinessError("IMPORT_ALREADY_CONFIRMED", "此批次已确认，请重新上传以改变映射", 409)
    batch.mapping, batch.options, batch.import_key, batch.status = body.mapping, options, key, "QUEUED"
    job = Job(workspace=ws, kind="import", idem_key="import:" + batch.id, payload={"batch_id": batch.id})
    db.add(job)
    db.flush()
    audit(db, ws, batch.id, "confirm_import")
    return job_out(job)


@app.get("/api/v1/imports/{batch_id}/errors", dependencies=[Auth])
def import_errors(batch_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.workspace != ws or not batch.errors_path:
        raise BusinessError("NOT_FOUND", "错误报告尚未生成", 404)
    return FileResponse(batch.errors_path, filename="import-errors.csv", media_type="text/csv")


@app.get("/api/v1/audit", dependencies=[Auth])
def audit_events(ws=Depends(workspace), db=Depends(database, scope="function"), entity_id: str | None = None):
    stmt = select(Audit).where(Audit.workspace == ws)
    if entity_id:
        stmt = stmt.where(Audit.entity_id == entity_id)
    return {
        "items": [
            {
                "id": a.id,
                "entity_id": a.entity_id,
                "action": a.action,
                "actor": a.actor,
                "details": a.details,
                "created_at": a.created_at,
            }
            for a in db.scalars(stmt.order_by(Audit.created_at.desc()).limit(100))
        ]
    }


@app.get("/api/v1/history/{entity_id}", dependencies=[Auth])
def history(entity_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
    get(db, ws, entity_id)
    return {
        "items": [
            {"version": r.version, "data": r.data, "created_at": r.created_at, "actor": r.actor}
            for r in db.scalars(
                select(Revision)
                .where(Revision.entity_id == entity_id, Revision.workspace == ws)
                .order_by(Revision.version.desc())
            )
        ]
    }


@app.get("/api/v1/exports/{kind}", dependencies=[Auth])
def export_data(
    kind: str,
    format: str = "csv",
    search: str = "",
    state: str = "",
    category: str = "",
    ws=Depends(workspace),
):
    if kind not in CONTRACTS and kind != "scores":
        raise BusinessError("INVALID_KIND", "不存在此数据类型")
    fields = (
        ["id", "version", "created_at", *CONTRACTS[kind].model_fields]
        if kind in CONTRACTS
        else ["id", "version", "data"]
    )

    def exported_rows():
        from app.store import query

        with Session() as db:
            stmt = query(ws, kind).order_by(__import__("app.db", fromlist=["Entity"]).Entity.id)
            from app.db import Entity

            if state:
                stmt = stmt.where(Entity.data["state"].as_string() == state)
            if category:
                stmt = stmt.where(Entity.data["category"].as_string() == category)
            if search:
                from app.store import apply_search

                stmt = apply_search(stmt, ws, search)
            for row in db.scalars(stmt.execution_options(yield_per=500)):
                data = {
                    **row.data,
                    "id": row.id,
                    "version": row.version,
                    "created_at": row.created_at.isoformat(),
                    "data": row.data,
                }
                yield [
                    safe_cell(
                        json.dumps(data.get(k), ensure_ascii=False)
                        if isinstance(data.get(k), (dict, list))
                        else data.get(k)
                    )
                    for k in fields
                ]

    if format == "csv":

        def stream():
            output = io.StringIO()
            writer = csv.writer(output)
            yield "\ufeff"
            for values in [fields]:
                writer.writerow(values)
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
            for values in exported_rows():
                writer.writerow(values)
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

        return StreamingResponse(
            stream(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'},
        )
    if format != "xlsx":
        raise BusinessError("INVALID_FORMAT", "支持 CSV 或 XLSX")
    from openpyxl import Workbook
    from starlette.background import BackgroundTask

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = config.DATA_DIR / (new_id() + ".xlsx")
    wb = Workbook(write_only=True)
    sheet = wb.create_sheet("Export")
    sheet.append(fields)
    for values in exported_rows():
        sheet.append(values)
    wb.save(target)
    return FileResponse(
        target, filename=f"{kind}.xlsx", background=BackgroundTask(target.unlink, missing_ok=True)
    )


@app.get("/api/v1/templates/{kind}", dependencies=[Auth])
def import_template(kind: str):
    if kind not in IMPORTABLE:
        raise BusinessError("INVALID_KIND", "此数据类型不支持导入")
    output = io.StringIO()
    csv.writer(output).writerow(
        [k for k in CONTRACTS[kind].model_fields if k not in {"source_id", "evidence_id"}]
    )
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{kind}-template.csv"'},
    )


def resource_routes(kind, schema):
    def listing(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        search: str = "",
        parent_id: str | None = None,
        state: str | None = None,
        category: str | None = None,
        ws=Depends(workspace),
        db=Depends(database, scope="function"),
    ):
        filters = {}
        if parent_id:
            field = {
                "skus": "product_id",
                "matches": "sku_id",
                "prices": "listing_id",
                "offers": "sku_id",
                "samples": "offer_id",
                "orders": "experiment_id",
                "funnels": "experiment_id",
            }.get(kind, "candidate_id")
            filters[field] = parent_id
        if state:
            filters["state"] = state
        if category:
            filters["category"] = category
        return list_page(db, ws, kind, page, page_size, search, filters)

    def detail(entity_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
        from app.store import display_label

        row = get(db, ws, entity_id, kind)
        result = row_out(row)
        label = display_label(db, ws, row)
        if label:
            result["data"] = {**result["data"], "_label": label}
        return result

    def create(body, ws=Depends(workspace), db=Depends(database, scope="function")):
        row, _ = save(db, ws, kind, body.model_dump(mode="json"))
        return row_out(row)

    create.__annotations__["body"] = schema
    update_schema = create_model(
        kind.title() + "Update", __base__=Contract, expected_version=(int, Field(ge=1)), payload=(schema, ...)
    )

    def edit(entity_id: str, body, ws=Depends(workspace), db=Depends(database, scope="function")):
        row = get(db, ws, entity_id, kind)
        row, _ = save(
            db,
            ws,
            kind,
            body.payload.model_dump(mode="json"),
            entity=row,
            expected_version=body.expected_version,
        )
        return row_out(row)

    edit.__annotations__["body"] = update_schema
    for path, endpoint, methods, model, code in [
        (f"/api/v1/{kind}", listing, ["GET"], EntityPage, 200),
        (f"/api/v1/{kind}/{{entity_id}}", detail, ["GET"], EntityOut, 200),
        (f"/api/v1/{kind}", create, ["POST"], EntityOut, 201),
        (f"/api/v1/{kind}/{{entity_id}}", edit, ["PUT"], EntityOut, 200),
    ]:
        app.add_api_route(
            path,
            endpoint,
            methods=methods,
            response_model=model,
            status_code=code,
            dependencies=[Auth],
            operation_id=methods[0].lower() + "_" + kind + ("_detail" if "entity_id" in path else ""),
            tags=[kind],
        )


for resource_kind, contract in CONTRACTS.items():
    resource_routes(resource_kind, contract)

register_market_routes(app, Auth, database, workspace)


def openapi_contract():
    from fastapi.openapi.utils import get_openapi

    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema.setdefault("components", {}).setdefault("securitySchemes", {}).update(
        {
            "sessionCookie": {"type": "apiKey", "in": "cookie", "name": "commerceai_session"},
            "csrfHeader": {"type": "apiKey", "in": "header", "name": "X-CSRF-Token"},
        }
    )
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            if path in {"/api/health", "/api/v1/auth/login"}:
                operation["security"] = []
            else:
                operation["security"] = [
                    {"sessionCookie": [], **({"csrfHeader": []} if method not in {"get", "head"} else {})}
                ]
    app.openapi_schema = schema
    return schema


app.openapi = openapi_contract

if (config.ROOT / "frontend" / "dist").exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=config.ROOT / "frontend" / "dist", html=True), name="workbench")
