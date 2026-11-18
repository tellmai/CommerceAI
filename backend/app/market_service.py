"""Persistent online discovery, bounded provider calls, reports and P0 handoff."""

from copy import deepcopy
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select

from app.db import Entity, Job, Session, now
from app.domain import decimal, fingerprint, iso, money
from app.errors import BusinessError
from app.ingestion import assert_lease
from app.market_analysis import analyze
from app.market_connections import PLATFORMS, read_connection, require_connection
from app.market_provider import OneBound, safe_url
from app.schemas import MatchReview
from app.store import get, latest, persist, query, require_evidence, row_out, save
from app.workflows import candidate_mode, config_for, review_match


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


def quote_identity(platform, url, variant):
    parsed = urlsplit(url)
    identity_params = {"id", "item_id", "goods_id", "offerid", "sku", "sku_id", "skuid"}
    params = sorted((key, value) for key, value in parse_qsl(parsed.query) if key.lower() in identity_params)
    canonical = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), urlencode(params), ""))
    return platform + ":" + fingerprint([canonical, variant])[:24]


def save_manual_run(db, workspace, body, run_id=None):
    if workspace != "live":
        raise BusinessError("SPACE_MODE_CONFLICT", "请在真实经营空间录入平台报价", 409)
    captured_at = now()
    run = get(db, workspace, run_id, "market_runs") if run_id else None
    if run and (run.data["request"]["mode"] != "MANUAL" or run.version != body.expected_version):
        raise BusinessError("VERSION_CONFLICT", "只能更新当前版本的人工报价", 409)
    items = []
    identities = set()
    for quote in body.quotes:
        url = safe_url(quote.url, quote.platform)
        if not url:
            raise BusinessError("INVALID_PLATFORM_URL", f"请输入{PLATFORMS[quote.platform]}的商品链接")
        observed_at = quote.observed_at or captured_at
        if observed_at > captured_at:
            raise BusinessError("INVALID_OBSERVATION_DATE", "采集时间不能晚于当前时间")
        item_id = quote_identity(quote.platform, url, quote.variant_key)
        if item_id in identities:
            raise BusinessError("DUPLICATE_QUOTE", "同一链接与SKU已录入，请在同一条报价中填写阶梯和优惠条件")
        identities.add(item_id)
        items.append(
            {
                "id": item_id,
                "external_id": "",
                "platform": quote.platform,
                "title": quote.title,
                "url": url,
                "price": money(quote.price),
                "variant_key": quote.variant_key,
                "price_kind": quote.price_kind,
                "eligibility_verified": quote.eligibility_verified,
                "conditions": quote.conditions,
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
                "price_tiers": [
                    {"min_quantity": tier.min_quantity, "price": money(tier.price)}
                    for tier in sorted(quote.price_tiers, key=lambda tier: tier.min_quantity)
                ],
                "seller": quote.seller,
                "seller_id": "",
                "dropship": None,
                "observed_at": observed_at.isoformat(),
                "source_updated_at": None,
                "detail_loaded": True,
                "evidence_note": quote.evidence_note,
                "warnings": ["人工采集的平台报价，价格、条件及规格以所留凭据为准"],
            }
        )
    platforms = list(dict.fromkeys(quote.platform for quote in body.quotes))
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
            "created_at": run.data["created_at"] if run else timestamp,
            "finished_at": timestamp,
        },
        entity=run,
        expected_version=run.version if run else None,
        action="market_quotes_updated" if run else "market_quotes_recorded",
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
    quote_hours = config_for(db, workspace).data["config"]["freshness"]["price_hours"]
    result = analyze(supply, retail, body, mode, current, quote_hours)
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


def report_expires_at(db, workspace, report):
    hours = config_for(db, workspace).data["config"]["freshness"]["price_hours"]
    return min(iso(report.data[key]["observed_at"]) for key in ("supply", "retail")) + timedelta(hours=hours)


def require_report_current(db, workspace, report):
    data = report.data
    if report_expires_at(db, workspace, report) <= now():
        raise BusinessError("QUOTE_STALE", "报价已过期，请重新查询后再保存业务候选", 409)
    run = get(db, workspace, data["market_run_id"], "market_runs")
    if run.version != data["run_version"]:
        current_items = {item["id"]: item for item in run.data["items"]}
        if any(current_items.get(data[key]["id"]) != data[key] for key in ("supply", "retail")):
            raise BusinessError("QUOTE_REVISED", "原报价已更新，请根据新报价重新核验", 409)
    if data["mode"] == "LIVE":
        current = read_connection()
        if not current.get("enabled") or current.get("version") != data["provider_version"]:
            raise BusinessError("CONNECTION_REVISED", "连接已变更，请重新查询核验", 409)


def publish_retail_quote(db, workspace, report, sku, evidence):
    data = report.data
    item, inputs, result = data["retail"], data["inputs"], data["result"]
    if result["retail_unit_price"] is None:
        return
    listing, _ = save(
        db,
        workspace,
        "listings",
        {
            "platform": PLATFORMS[item["platform"]],
            "external_id": item["external_id"] or quote_identity(item["platform"], item["url"], ""),
            "external_sku_id": inputs["retail_sku"] or item.get("variant_key") or item["id"],
            "title": item["title"],
            "url": item["url"],
            "quantity": result["retail_pack"],
            "attributes": result["retail_attributes"],
        },
        upsert=True,
    )
    match, _ = save(
        db,
        workspace,
        "matches",
        {
            "listing_id": listing.id,
            "sku_id": sku.id,
            "evidence_id": evidence.id,
            "reason": inputs["verification_reason"],
        },
        upsert=True,
    )
    review_match(
        db,
        workspace,
        match.id,
        MatchReview(
            state="MATCHED",
            evidence_id=evidence.id,
            reason=inputs["verification_reason"],
            expected_version=match.version,
        ),
    )
    price_data = {
        "listing_id": listing.id,
        "evidence_id": evidence.id,
        "observed_at": item["observed_at"],
        "region": inputs["region"],
        "quantity": result["retail_pack"],
        "price_kind": item.get("price_kind", "LIST"),
        "price": result["retail_unit_price"],
        "shipping": result.get("retail_shipping", inputs.get("retail_shipping")),
        "conditions": item.get("conditions", ""),
        "eligibility_verified": item.get("eligibility_verified", False),
    }
    price, _ = save(db, workspace, "prices", price_data, upsert=True)
    if price.data["evidence_id"] != evidence.id:
        save(db, workspace, "prices", price_data, entity=price, expected_version=price.version)


def promote_report(db, workspace, report_id, body):
    report = db.scalar(query(workspace, "market_reports").where(Entity.id == report_id).with_for_update())
    if report is None:
        raise BusinessError("NOT_FOUND", "记录不存在或不属于当前数据空间", 404)
    result, source_data = report.data["result"], report.data
    if source_data.get("candidate_id"):
        return get(db, workspace, source_data["candidate_id"], "candidates")
    require_report_current(db, workspace, report)
    mode = source_data["mode"]
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
            "expires_at": report_expires_at(db, workspace, report).isoformat(),
            "note": source_data["inputs"]["verification_reason"],
            "market_report_id": report.id,
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
                "procurement_mode": source_data["inputs"]["procurement_mode"],
                "market_report_id": report.id,
                "note": "来自找货与比价报告。样品、主备供货及风险仍需核验。",
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
    publish_retail_quote(db, workspace, report, sku, evidence)
    return candidate


def report_draft(db, workspace, report, candidate):
    data = report.data
    inputs, result, supply = data["inputs"], data["result"], data["supply"]
    draft = {
        "report_id": report.id,
        "supply_title": supply["title"],
        "supply_url": supply["url"],
        "mode": inputs["procurement_mode"],
        "reference_count_ready": result.get("retail_total") is not None,
        "stale_reason": None,
        "offer": None,
        "normal": None,
        "stress": None,
    }
    try:
        require_report_current(db, workspace, report)
        evidence_id = data.get("evidence_id")
        if evidence_id:
            require_evidence(db, workspace, evidence_id)
    except BusinessError as exc:
        return {**draft, "stale_reason": exc.message}
    stock = inputs["procurement_mode"] == "STOCK"
    if candidate_mode(candidate) != inputs["procurement_mode"]:
        return {**draft, "stale_reason": "候选采购方式与报告不一致，请在研究阶段调整"}
    incoming, outgoing = decimal(inputs.get("inbound_shipping")), decimal(inputs.get("outbound_shipping"))
    delivery = money(incoming + outgoing) if incoming is not None and outgoing is not None else None
    draft["offer"] = {
        "sku_id": candidate.data["sku_id"],
        "quantity": result["retail_pack"],
        "evidence_id": evidence_id,
        "procurement_mode": inputs["procurement_mode"],
        "unit_cost": result["supply_unit_price"],
        "shipping": inputs.get("inbound_shipping") if stock else delivery,
        "outbound_shipping": inputs.get("outbound_shipping") if stock else None,
        "supplier_pack": result["supply_pack"] if stock else None,
        "batch_quantity": inputs["purchase_quantity"] if stock else None,
        "min_order": result["min_order"],
        "region": inputs["region"],
        "expires_at": report_expires_at(db, workspace, report).isoformat(),
        "verified": False,
    }
    for key in ("normal", "stress"):
        values = dict(result.get(key + "_inputs") or {})
        if not stock and result["algorithm_version"] == "sourcing-economics-1.0" and incoming is not None:
            if values.get("c") is not None and values.get("f") is not None:
                moved = incoming * decimal("1.1" if key == "stress" else "1")
                values.update(c=decimal(values["c"]) - moved, f=decimal(values["f"]) + moved)
        draft[key] = {
            **{
                k: money(v) if k not in {"fee_rate", "r"} else float(v)
                for k, v in values.items()
                if v is not None
            },
            "candidate_id": candidate.id,
            "evidence_id": evidence_id,
            "label": key.upper(),
            "note": "来自比价报告 " + report.id,
        }
    return draft
