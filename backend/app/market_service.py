"""Persistent online discovery, bounded provider calls, reports and P0 handoff."""

from copy import deepcopy
from datetime import timedelta

from sqlalchemy import select

from app.db import Job, Session, now
from app.domain import fingerprint, iso, money
from app.errors import BusinessError
from app.ingestion import assert_lease
from app.market_analysis import analyze
from app.market_connections import PLATFORMS, read_connection, require_connection
from app.market_provider import OneBound, safe_url
from app.store import get, latest, persist, row_out, save


def start_search(db, workspace, body):
    if workspace == "public" or (body.mode == "DEMO") != (workspace == "demo"):
        raise BusinessError(
            "SPACE_MODE_CONFLICT", "模拟查询只能在模拟空间使用；联网查询请切换真实经营空间", 409
        )
    connection = require_connection(body.platforms) if body.mode == "LIVE" else {}
    run = persist(
        db,
        workspace,
        "market_runs",
        {
            "request": body.model_dump(mode="json"),
            "status": "QUEUED",
            "items": [],
            "errors": [],
            "completed_platforms": [],
            "provider_version": connection.get("version", ""),
            "provider": "onebound" if body.mode == "LIVE" else "synthetic",
            "calls_reserved": 0,
            "call_limit": len(body.platforms) * (body.per_platform + 1),
            "created_at": now().isoformat(),
        },
        action="market_search_requested",
    )
    job = Job(
        workspace=workspace, kind="market_search", idem_key="market:" + run.id, payload={"run_id": run.id}
    )
    db.add(job)
    db.flush()
    return {"run": row_out(run), "job_id": job.id}


def run_detail(db, workspace, run_id):
    run = get(db, workspace, run_id, "market_runs")
    result = row_out(run)
    job = db.scalar(
        select(Job).where(
            Job.workspace == workspace, Job.kind == "market_search", Job.idem_key == "market:" + run.id
        )
    )
    result["job"] = (
        {"id": job.id, "status": job.status, "progress": job.progress, "error": job.error} if job else None
    )
    return result


def record_search_failure(db, job):
    run = get(db, job.workspace, job.payload["run_id"], "market_runs")
    data = {**run.data, "status": job.status}
    if job.status == "FAILED":
        data.update(finished_at=now().isoformat(), failure=job.error)
    persist(
        db,
        job.workspace,
        "market_runs",
        data,
        entity=run,
        expected_version=run.version,
        action="market_search_progress",
    )


def save_manual_run(db, workspace, body):
    if workspace != "live":
        raise BusinessError("SPACE_MODE_CONFLICT", "请在真实经营空间录入平台报价", 409)
    captured_at = now()
    items = []
    for quote in body.quotes:
        url = safe_url(quote.url, quote.platform)
        if not url:
            raise BusinessError("INVALID_PLATFORM_URL", f"请输入{PLATFORMS[quote.platform]}的商品链接")
        observed_at = quote.observed_at or captured_at
        if observed_at > captured_at + timedelta(minutes=5):
            raise BusinessError("INVALID_OBSERVATION_DATE", "采集时间不能晚于当前时间")
        items.append(
            {
                "id": quote.platform + ":" + fingerprint([url, quote.title])[:24],
                "external_id": "",
                "platform": quote.platform,
                "title": quote.title,
                "url": url,
                "price": money(quote.price),
                "promotion_price": None,
                "currency": "CNY",
                "currency_inferred": False,
                "pack_count": quote.pack_count,
                "attributes": {
                    key: getattr(quote, key)
                    for key in ("material", "size", "brand", "model")
                    if getattr(quote, key)
                },
                "variants": [],
                "shipping": money(quote.shipping) if quote.shipping is not None else None,
                "shipping_region": quote.shipping_region,
                "min_order": quote.min_order,
                "price_tiers": [],
                "seller": "",
                "seller_id": "",
                "dropship": None,
                "observed_at": observed_at.isoformat(),
                "source_updated_at": None,
                "detail_loaded": True,
                "evidence_note": quote.evidence_note,
                "warnings": ["人工采集的平台报价，价格、条件及规格以所留凭据为准"],
            }
        )
    platforms = [quote.platform for quote in body.quotes]
    timestamp = captured_at.isoformat()
    return persist(
        db,
        workspace,
        "market_runs",
        {
            "request": {"query": body.query, "mode": "MANUAL", "platforms": platforms},
            "status": "SUCCEEDED",
            "items": items,
            "errors": [],
            "completed_platforms": platforms,
            "provider": "manual",
            "provider_version": "",
            "calls_reserved": 0,
            "call_limit": 0,
            "created_at": timestamp,
            "finished_at": timestamp,
        },
        action="market_quotes_recorded",
    )


def demo_items(platform, query, count):
    result = []
    for index in range(count):
        item_id = f"demo-{platform}-{index}"
        price = {"1688": "2.0000", "taobao": "12.9000", "jd": "15.9000", "pinduoduo": "9.9000"}[platform]
        result.append(
            {
                "id": platform + ":" + item_id,
                "external_id": item_id,
                "platform": platform,
                "title": f"模拟 {query} 成人手动2支装 {index + 1}",
                "url": "",
                "price": price,
                "promotion_price": None,
                "currency": "CNY",
                "currency_inferred": False,
                "pack_count": 2,
                "attributes": {"material": "模拟PBT刷毛/PP刷柄", "size": "180mm", "brand": "模拟无品牌"},
                "variants": [],
                "shipping": None,
                "shipping_region": "",
                "min_order": 1 if platform == "1688" else None,
                "price_tiers": [
                    {"min_quantity": 1, "price": "2.0000"},
                    {"min_quantity": 100, "price": "1.8000"},
                ]
                if platform == "1688"
                else [],
                "seller": f"模拟{PLATFORMS[platform]}商家{index + 1}",
                "seller_id": item_id,
                "dropship": True if platform == "1688" else None,
                "observed_at": now().isoformat(),
                "source_updated_at": None,
                "detail_loaded": True,
                "warnings": ["完全模拟的流程样例，不代表真实商品或报价"],
            }
        )
    return result


def execute_search(job_id, token):
    with Session.begin() as db:
        job = assert_lease(db, job_id, token)
        workspace, run_id = job.workspace, job.payload["run_id"]
        run = get(db, workspace, run_id, "market_runs")
        request = deepcopy(run.data["request"])
        saved_version = run.data["provider_version"]
    provider = OneBound(require_connection(request["platforms"])) if request["mode"] == "LIVE" else None

    def update_run(transform):
        with Session.begin() as db:
            job = assert_lease(db, job_id, token)
            current = get(db, workspace, run_id, "market_runs")
            data = deepcopy(current.data)
            transform(data, job)
            persist(
                db,
                workspace,
                "market_runs",
                data,
                entity=current,
                expected_version=current.version,
                action="market_search_progress",
            )
            return data

    def reserve():
        def change(data, _job):
            current = require_connection(request["platforms"])
            if current["version"] != saved_version:
                raise BusinessError("CONNECTION_REVISED", "连接配置已变更，请重新发起查询", 409)
            if data["calls_reserved"] >= data["call_limit"]:
                raise BusinessError(
                    "CALL_LIMIT", "本次查询调用次数已达上限；未自动增加费用，请重新检查结果", 409
                )
            data["calls_reserved"] += 1
            data["status"] = "RUNNING"

        update_run(change)

    try:
        for platform in request["platforms"]:
            with Session() as db:
                current = get(db, workspace, run_id, "market_runs")
                if platform in current.data["completed_platforms"]:
                    continue
            items, errors, metadata = [], [], {}
            try:
                if provider:
                    reserve()
                    items, metadata = provider.search(
                        platform, request["query"], request["page"], request["per_platform"]
                    )
                    for index, item in enumerate(items):
                        try:
                            reserve()
                            detailed = provider.detail(platform, item["external_id"])
                            # Keep the original search identifier so selections remain stable.
                            detailed["id"] = item["id"]
                            items[index] = detailed
                        except BusinessError as exc:
                            if exc.code in {"LEASE_LOST", "CONNECTION_REVISED", "CALL_LIMIT"}:
                                raise
                            item["warnings"].append(exc.message)
                else:
                    items = demo_items(platform, request["query"], request["per_platform"])
            except BusinessError as exc:
                if exc.code == "LEASE_LOST":
                    raise
                errors.append({"platform": platform, "code": exc.code, "message": exc.message})

            def completed(data, job, platform=platform, items=items, errors=errors, metadata=metadata):
                data["items"] = [i for i in data["items"] if i["platform"] != platform] + items
                data["errors"].extend(errors)
                data["completed_platforms"].append(platform)
                data.setdefault("provider_responses", {})[platform] = metadata
                job.progress = int(len(data["completed_platforms"]) / len(request["platforms"]) * 95)

            update_run(completed)

        def finish(data, job):
            data["status"] = (
                "FAILED"
                if not data["items"] and data["errors"]
                else "PARTIAL"
                if data["errors"]
                else "SUCCEEDED"
            )
            data["finished_at"] = now().isoformat()
            job.status = "FAILED" if data["status"] == "FAILED" else "SUCCEEDED"
            job.progress, job.result = 100, {"run_id": run_id, "items": len(data["items"])}
            if job.status == "FAILED":
                job.error = "PLATFORM_QUERY_FAILED: 平台查询失败，请检查各平台权限和配额"

        update_run(finish)
    finally:
        if provider:
            provider.close()


def make_report(db, workspace, run_id, body):
    run = get(db, workspace, run_id, "market_runs")
    if run.version != body.expected_version:
        raise BusinessError("VERSION_CONFLICT", "查询结果已更新，请刷新后再分析", 409)
    if run.data["status"] not in {"SUCCEEDED", "PARTIAL"}:
        raise BusinessError("QUERY_NOT_READY", "请等待商品查询完成后再分析", 409)
    supply = next(
        (i for i in run.data["items"] if i["id"] == body.supply_id and i["platform"] == "1688"), None
    )
    retail = next(
        (i for i in run.data["items"] if i["id"] == body.retail_id and i["platform"] != "1688"), None
    )
    if not supply or not retail:
        raise BusinessError("QUOTE_NOT_FOUND", "请选择本次查询中的1688货源和零售商品", 409)
    mode = run.data["request"]["mode"]
    current = True
    if mode == "LIVE":
        connection = read_connection()
        current = (
            bool(connection.get("enabled")) and connection.get("version") == run.data["provider_version"]
        )
    result = analyze(supply, retail, body, mode, current)
    payload = body.model_dump(mode="json")
    data = {
        "market_run_id": run.id,
        "provider_version": run.data["provider_version"],
        "run_version": run.version,
        "mode": mode,
        "query": run.data["request"]["query"],
        "supply": supply,
        "retail": retail,
        "inputs": payload,
        "input_hash": fingerprint([payload, supply, retail]),
        "result": result,
        "created_at": now().isoformat(),
        "reviewed_by": "owner" if body.match_confirmed else None,
    }
    return persist(db, workspace, "market_reports", data, action="market_analysis")


def promote_report(db, workspace, report_id, body):
    report = get(db, workspace, report_id, "market_reports")
    result, source_data = report.data["result"], report.data
    if source_data.get("candidate_id"):
        return get(db, workspace, source_data["candidate_id"], "candidates")
    if any(
        (now() - iso(source_data[k]["observed_at"])).total_seconds() > 86400 for k in ["supply", "retail"]
    ):
        raise BusinessError("QUOTE_STALE", "报价已过期，请重新查询后再保存业务候选", 409)
    mode = source_data["mode"]
    if mode == "LIVE":
        run = get(db, workspace, source_data["market_run_id"], "market_runs")
        current = require_connection()
        if current["version"] != run.data["provider_version"]:
            raise BusinessError("CONNECTION_REVISED", "连接已变更，请重新查询核验", 409)
    if result["match_state"] != "CONFIRMED" or result["conflicts"]:
        raise BusinessError("MATCH_REQUIRED", "请先核验同款规格，再保存为业务候选", 409)
    source_name = {"DEMO": "模拟联网选品演练", "LIVE": "OneBound联网选品", "MANUAL": "人工采集平台报价"}[mode]
    source = latest(db, workspace, "sources", name=source_name)
    if source is None:
        source, _ = save(
            db,
            workspace,
            "sources",
            {
                "name": source_name,
                "platform": {"DEMO": "DEMO", "LIVE": "OneBound", "MANUAL": "MANUAL"}[mode],
                "permission_scope": ["*"],
                "permission_note": read_connection().get("permission_note", "")
                if mode == "LIVE"
                else "操作者仅录入有权使用的平台报价及证据"
                if mode == "MANUAL"
                else "模拟数据",
                "origin_url": "https://open.onebound.cn/help/api/" if mode == "LIVE" else "",
            },
        )
    evidence, _ = save(
        db,
        workspace,
        "evidence",
        {
            "source_id": source.id,
            "locator": "market-report:" + report.id,
            "observed_at": source_data["created_at"],
            "expires_at": (
                min(iso(source_data[k]["observed_at"]) for k in ["supply", "retail"]) + timedelta(hours=24)
            ).isoformat(),
            "note": source_data["inputs"]["verification_reason"],
        },
    )
    product, _ = save(db, workspace, "products", {"name": body.name, "category": body.category}, upsert=True)
    sku, _ = save(
        db,
        workspace,
        "skus",
        {
            "product_id": product.id,
            "name": body.name,
            "spec_key": fingerprint([result["retail_attributes"], result["retail_pack"]])[:20],
            "quantity": result["retail_pack"],
            "attributes": result["retail_attributes"],
        },
        upsert=True,
    )
    candidate = latest(db, workspace, "candidates", sku_id=sku.id)
    if candidate is None:
        candidate, _ = save(
            db,
            workspace,
            "candidates",
            {
                "sku_id": sku.id,
                "note": "来自找货与比价报告。样品、主备供货及风险仍需核验。报告：" + report.id,
            },
        )
    persist(
        db,
        workspace,
        "market_reports",
        {**report.data, "candidate_id": candidate.id, "evidence_id": evidence.id},
        entity=report,
        expected_version=report.version,
        action="market_candidate_saved",
    )
    return candidate
