#!/usr/bin/env python3
"""Use the same validated import queue as the UI; no invented costs or return matches."""

from pathlib import Path
import hashlib
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import DATA_DIR
from app.db import ImportBatch, Job, Session
from app.domain import fingerprint
from app.store import latest, save
from app.worker import run_once


def main():
    manifest = json.loads((ROOT / "data" / "public_manifest.json").read_text())
    jobs = []
    with Session.begin() as db:
        source = latest(db, "public", "sources", name="UCI Online Retail 官方历史数据")
        if not source:
            source, _ = save(
                db,
                "public",
                "sources",
                {
                    "name": "UCI Online Retail 官方历史数据",
                    "platform": "UCI",
                    "permission_scope": ["transactions"],
                    "permission_note": "Daqing Chen (2015), DOI 10.24432/C5BW33，按 CC BY 4.0 署名使用",
                    "license": manifest["license"],
                    "origin_url": manifest["source"],
                },
            )
        for item in manifest["batches"]:
            path = ROOT / "data" / "prepared" / item["file"]
            digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
            if digest != item["sha256"]:
                raise ValueError("Prepared file checksum mismatch")
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            immutable = DATA_DIR / (digest + ".csv")
            if not immutable.exists():
                shutil.copyfile(path, immutable)
            with path.open(encoding="utf-8-sig") as input_file:
                header = input_file.readline().strip().split(",")
            options = {
                "timezone": "Europe/London",
                "percent_unit": "fraction",
                "defaults": {},
            }
            mapping = {column: column for column in header}
            key = fingerprint(
                ["public", source.id, "transactions", digest, mapping, options]
            )
            from sqlalchemy import select

            existing = db.scalar(
                select(ImportBatch).where(ImportBatch.import_key == key)
            )
            if existing:
                # Upgrade older local imports to an immutable evidence path.
                existing.path = str(immutable)
                continue
            batch = ImportBatch(
                workspace="public",
                source_id=source.id,
                kind="transactions",
                filename=path.name,
                file_hash=digest,
                path=str(immutable),
                options=options,
                mapping=mapping,
                import_key=key,
                status="QUEUED",
            )
            db.add(batch)
            db.flush()
            job = Job(
                workspace="public",
                kind="import",
                idem_key="import:" + batch.id,
                payload={"batch_id": batch.id},
            )
            db.add(job)
            db.flush()
            jobs.append(job.id)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    while run_once():
        pass
    with Session() as db:
        statuses = [
            {
                "id": j,
                "status": db.get(Job, j).status,
                "result": db.get(Job, j).result,
                "error": db.get(Job, j).error,
            }
            for j in jobs
        ]
        print(json.dumps(statuses, ensure_ascii=False))
        if any(s["status"] != "SUCCEEDED" for s in statuses):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
