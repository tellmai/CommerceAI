"""Authenticated discovery endpoints; no credential values are returned."""

import csv
import io
import json

from fastapi import Depends, Query
from fastapi.responses import Response

from app.domain import safe_cell
from app.errors import BusinessError
from app.market_connections import connection_status, require_connection, write_connection
from app.market_provider import OneBound
from app.market_schemas import CompareInput, ConnectionInput, ManualRunInput, PromoteInput, SearchInput
from app.market_service import make_report, promote_report, run_detail, save_manual_run, start_search
from app.schemas import Contract, EntityOut, EntityPage
from app.store import audit, get, list_page, row_out


class ConnectionTest(Contract):
    query: str = "牙刷"


def register_market_routes(app, auth, database, workspace):
    @app.get("/api/v1/market/connection", dependencies=[auth], tags=["联网选品"])
    def status():
        return connection_status()

    @app.put("/api/v1/market/connection", dependencies=[auth], tags=["联网选品"])
    def configure(body: ConnectionInput, ws=Depends(workspace), db=Depends(database, scope="function")):
        result = write_connection(body)
        audit(
            db,
            ws,
            "market-connection",
            "market_connection_updated",
            {"version": result["version"], "enabled": result["enabled"]},
        )
        return result

    @app.post("/api/v1/market/connection/test", dependencies=[auth], tags=["联网选品"])
    def test_connection(body: ConnectionTest):
        if not 1 <= len(body.query) <= 100:
            raise BusinessError("INVALID_QUERY", "测试关键词长度为1至100字")
        connection = require_connection()
        provider, results = OneBound(connection), []
        try:
            for platform in connection["platforms"]:
                try:
                    items, _ = provider.search(platform, body.query, 1, 1)
                    results.append(
                        {
                            "platform": platform,
                            "ok": True,
                            "items": len(items),
                            "message": "搜索接口响应成功；详情和价格仍需在查询中核验",
                        }
                    )
                except BusinessError as exc:
                    results.append(
                        {"platform": platform, "ok": False, "code": exc.code, "message": exc.message}
                    )
        finally:
            provider.close()
        return {"results": results, "calls": provider.calls}

    @app.post("/api/v1/market/runs", dependencies=[auth], status_code=202, tags=["联网选品"])
    def search(body: SearchInput, ws=Depends(workspace), db=Depends(database, scope="function")):
        return start_search(db, ws, body)

    @app.post(
        "/api/v1/market/runs/manual",
        dependencies=[auth],
        status_code=201,
        response_model=EntityOut,
        tags=["联网选品"],
    )
    def manual(body: ManualRunInput, ws=Depends(workspace), db=Depends(database, scope="function")):
        return row_out(save_manual_run(db, ws, body))

    @app.get("/api/v1/market/runs", dependencies=[auth], response_model=EntityPage, tags=["联网选品"])
    def runs(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        ws=Depends(workspace),
        db=Depends(database, scope="function"),
    ):
        return list_page(db, ws, "market_runs", page, page_size)

    @app.get("/api/v1/market/runs/{run_id}", dependencies=[auth], tags=["联网选品"])
    def detail(run_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
        return run_detail(db, ws, run_id)

    @app.post(
        "/api/v1/market/runs/{run_id}/compare",
        dependencies=[auth],
        response_model=EntityOut,
        status_code=201,
        tags=["联网选品"],
    )
    def compare(
        run_id: str, body: CompareInput, ws=Depends(workspace), db=Depends(database, scope="function")
    ):
        return row_out(make_report(db, ws, run_id, body))

    @app.get("/api/v1/market/reports", dependencies=[auth], response_model=EntityPage, tags=["联网选品"])
    def reports(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        ws=Depends(workspace),
        db=Depends(database, scope="function"),
    ):
        return list_page(db, ws, "market_reports", page, page_size)

    @app.get(
        "/api/v1/market/reports/{report_id}", dependencies=[auth], response_model=EntityOut, tags=["联网选品"]
    )
    def report(report_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
        return row_out(get(db, ws, report_id, "market_reports"))

    @app.post(
        "/api/v1/market/reports/{report_id}/candidate",
        dependencies=[auth],
        response_model=EntityOut,
        tags=["联网选品"],
    )
    def promote(
        report_id: str, body: PromoteInput, ws=Depends(workspace), db=Depends(database, scope="function")
    ):
        return row_out(promote_report(db, ws, report_id, body))

    @app.get("/api/v1/market/reports/{report_id}/export", dependencies=[auth], tags=["联网选品"])
    def export(report_id: str, ws=Depends(workspace), db=Depends(database, scope="function")):
        report = get(db, ws, report_id, "market_reports")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["字段", "内容"])
        for key, value in {
            "模式": report.data["mode"],
            "关键词": report.data["query"],
            "货源": report.data["supply"]["title"],
            "货源链接": report.data["supply"]["url"],
            "货源采集时间": report.data["supply"]["observed_at"],
            "货源报价依据": report.data["supply"].get("evidence_note", ""),
            "零售": report.data["retail"]["title"],
            "零售链接": report.data["retail"]["url"],
            "零售采集时间": report.data["retail"]["observed_at"],
            "零售报价依据": report.data["retail"].get("evidence_note", ""),
            "分析时间": report.data["created_at"],
            "核验与成本输入": report.data["inputs"],
            **report.data["result"],
        }.items():
            content = (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else "未知"
                if value is None
                else str(value)
            )
            writer.writerow([safe_cell(key), safe_cell(content)])
        return Response(
            output.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="sourcing-report-{report.id}.csv"'},
        )
