#!/usr/bin/env python3
"""Restore into a NEW *_restorecheck database; never overwrite the working database."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from sqlalchemy import create_engine, text
from backup import ROOT, engine, pg_args, pg_binary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--database", default="commerceai_restorecheck")
    parser.add_argument("--report", type=Path, default=ROOT / "docs" / "restore-check.json")
    args = parser.parse_args()
    if (
        not args.database.endswith("_restorecheck")
        or not args.database.replace("_", "").isalnum()
    ):
        parser.error("仅允许全新的 *_restorecheck 数据库")
    local = ROOT / ".local" / "restored"
    local.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(dir=local))
    with tarfile.open(args.archive, "r:gz") as tar:
        tar.extractall(directory, filter="data")
    manifest = json.loads((directory / "manifest.json").read_text())
    dump = directory / "database.dump"
    assert (
        hashlib.file_digest(dump.open("rb"), "sha256").hexdigest()
        == manifest["database_sha256"]
    )
    for name, checksum in manifest["files"].items():
        if Path(name).name != name:
            raise ValueError("Invalid manifest file path")
        assert (
            hashlib.file_digest(
                (directory / "storage" / name).open("rb"), "sha256"
            ).hexdigest()
            == checksum
        )
    connection, env = pg_args(args.database)
    subprocess.run([pg_binary("createdb"), *connection], env=env, check=True)
    # pg_restore's database is an option, not createdb's final positional argument.
    subprocess.run(
        [
            pg_binary("pg_restore"),
            *connection[:-1],
            "--dbname",
            args.database,
            "--no-owner",
            "--no-acl",
            str(dump),
        ],
        env=env,
        check=True,
    )
    restored = create_engine(engine.url.set(database=args.database))
    with restored.begin() as db:
        # File references must point to the restored evidence directory.
        for original in db.execute(
            text("SELECT id,path,errors_path FROM import_batch")
        ).mappings():
            replacements = {}
            for field in ["path", "errors_path"]:
                if original[field]:
                    stored_name = manifest.get("file_references", {}).get(
                        original[field], Path(original[field]).name
                    )
                    candidate = directory / "storage" / stored_name
                    if candidate.exists():
                        replacements[field] = str(candidate)
            if replacements:
                assignments = ",".join(f"{field}=:{field}" for field in replacements)
                db.execute(
                    text(f"UPDATE import_batch SET {assignments} WHERE id=:id"),
                    {**replacements, "id": original["id"]},
                )
        counts = dict(
            db.execute(text("SELECT kind,count(*) FROM entity GROUP BY kind")).all()
        )
        sessions = db.scalar(text("SELECT count(*) FROM login_session"))
        assert sessions == 0
    result = {
        "database": args.database,
        "counts": counts,
        "active_sessions": sessions,
        "evidence_files": len(manifest["files"]),
        "restored_storage": str(directory / "storage"),
        "database_checksum_verified": True,
    }
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    restored.dispose()


if __name__ == "__main__":
    main()
