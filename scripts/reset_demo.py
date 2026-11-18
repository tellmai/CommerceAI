#!/usr/bin/env python3
"""Explicitly reset disposable DEMO records. Public/live data are never touched."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import delete
from app.db import Audit, Entity, ImportBatch, Job, Link, Revision, Session
from app.seed import seed_demo


if __name__ == "__main__":
    if "--confirm-demo-only" not in sys.argv:
        raise SystemExit(
            "仅重置模拟空间。请显式传入 --confirm-demo-only；公开历史和真实经营数据不受影响。"
        )
    with Session.begin() as db:
        for model in [Job, ImportBatch, Audit, Link, Revision, Entity]:
            db.execute(delete(model).where(model.workspace == "demo"))
    seed_demo()
    print("模拟演示已重建；公开历史和真实经营空间保持原样。")
