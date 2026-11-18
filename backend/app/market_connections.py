"""Local-only provider secrets, never returned through the API or stored in audits."""

import json
import os
from pathlib import Path
import tempfile

from app import config
from app.db import new_id, now
from app.errors import BusinessError

PLATFORMS = {"1688": "1688货源", "taobao": "淘宝", "jd": "京东", "pinduoduo": "拼多多"}
DOCS = "https://open.onebound.cn/help/api/"


def path():
    return config.DATA_DIR / "private" / "market-connection.json"


def read_connection():
    target = path()
    if not target.exists():
        return {"enabled": False, "platforms": list(PLATFORMS), "taobao_route": "tglobal", "version": ""}
    try:
        return json.loads(target.read_text())
    except (OSError, ValueError) as exc:
        raise BusinessError("CONNECTION_UNREADABLE", "平台连接配置读取失败，请重新配置", 503) from exc


def connection_status():
    value = read_connection()
    return {
        "provider": "onebound",
        "provider_name": "OneBound 万邦商品数据服务",
        "enabled": value.get("enabled", False),
        "configured": bool(value.get("api_key")),
        "has_secret": bool(value.get("api_secret")),
        "has_access_token": bool(value.get("access_token")),
        "platforms": value.get("platforms", list(PLATFORMS)),
        "taobao_route": value.get("taobao_route", "tglobal"),
        "permission_note": value.get("permission_note", ""),
        "version": value.get("version", ""),
        "updated_at": value.get("updated_at"),
        "docs_url": DOCS,
        "notice": "需要自行开通服务并确认四个平台权限。配置存在不代表接口已经联通；调用可能按次计费。",
    }


def write_connection(body):
    previous = read_connection()
    value = body.model_dump(mode="json")
    for field in ["api_key", "api_secret", "access_token"]:
        # Omitted/null keeps the saved secret; an empty string explicitly clears it.
        if value[field] is None:
            value[field] = previous.get(field, "")
    if value["enabled"] and (not value["api_key"] or not value["permission_note"]):
        raise BusinessError("CONNECTION_INCOMPLETE", "启用连接前请填写API Key和数据使用范围说明")
    value.update(version=new_id(), updated_at=now().isoformat())
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return connection_status()


def require_connection(platforms=None):
    value = read_connection()
    if not value.get("enabled") or not value.get("api_key"):
        raise BusinessError("PLATFORM_NOT_CONFIGURED", "请先在平台连接中配置商品数据服务的API Key", 409)
    if platforms and set(platforms) - set(value.get("platforms", [])):
        raise BusinessError("PLATFORM_DISABLED", "所选平台未在连接配置中启用", 409)
    return value
