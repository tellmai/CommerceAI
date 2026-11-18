import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / ".local" / "storage"))
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://commerceai:commerceai@localhost:55432/commerceai"
)
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
MAX_ROWS = int(os.getenv("MAX_IMPORT_ROWS", "100000"))
SESSION_HOURS = 12
WORKSPACES = {"demo": "模拟演示", "public": "公开历史", "live": "真实经营"}
