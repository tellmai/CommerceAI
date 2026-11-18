#!/usr/bin/env python3
"""Database and evidence archive. Pause writes before invoking."""

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import DATA_DIR
from app.db import engine, ImportBatch, Session
from sqlalchemy import select


def pg_binary(name):
    found = shutil.which(name)
    if found:
        return found
    import pgserver

    return str(Path(pgserver.__file__).parent / "pginstall" / "bin" / name)


def pg_args(database=None):
    url = engine.url
    query = dict(url.query)
    env = {**os.environ}
    if url.password:
        env["PGPASSWORD"] = url.password
    args = [
        "-h",
        query.get("host") or url.host or "localhost",
        "-p",
        str(query.get("port") or url.port or 5432),
        "-U",
        url.username or "commerceai",
    ]
    return args + [database or url.database], env


def main():
    target = ROOT / ".local" / "backups"
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = target / f"commerceai-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(dir=target) as directory:
        directory = Path(directory)
        dump = directory / "database.dump"
        args, env = pg_args()
        subprocess.run(
            [
                pg_binary("pg_dump"),
                *args,
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--exclude-table-data=login_session",
                "--file",
                str(dump),
            ],
            env=env,
            check=True,
        )
        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "database_sha256": hashlib.file_digest(
                dump.open("rb"), "sha256"
            ).hexdigest(),
            "sessions": "excluded",
            "files": {},
            "file_references": {},
        }
        files = set(DATA_DIR.iterdir()) if DATA_DIR.exists() else set()
        with Session() as db:
            for batch in db.scalars(select(ImportBatch)):
                for original in [batch.path, batch.errors_path]:
                    if original and Path(original).exists():
                        files.add(Path(original))
        for path in files:
            if path.is_file():
                checksum = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
                name = checksum[:16] + "-" + path.name
                manifest["files"][name] = checksum
                manifest["file_references"][str(path)] = name
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2)
        )
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(dump, arcname="database.dump")
            tar.add(directory / "manifest.json", arcname="manifest.json")
            for path in files:
                if path.is_file():
                    tar.add(
                        path,
                        arcname="storage/" + manifest["file_references"][str(path)],
                    )
    archive.chmod(0o600)
    print(str(archive))


if __name__ == "__main__":
    main()
