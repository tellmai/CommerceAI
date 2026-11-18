#!/usr/bin/env python3
"""Real PostgreSQL + API + separate worker benchmark, isolated in *_test DB."""

import csv
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time

import httpx
import psutil
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def main():
    url = os.environ["TEST_DATABASE_URL"]
    assert (make_url(url).database or "").endswith("_test"), (
        "Benchmark requires a dedicated *_test database"
    )
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    file = local / "benchmark-100k.csv"
    with file.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(
            ["keyword", "category", "search_index", "listing_count", "conversion"]
        )
        for index in range(100000):
            writer.writerow(
                [f"benchmark-{index:06d}", "性能验证", 1000 + index, 200, 0.08]
            )
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "OWNER_PASSWORD": "benchmark-local-only",
        "PYTHONPATH": str(ROOT / "backend"),
        "DATA_DIR": str(local / "benchmark-storage"),
    }
    api_log = (local / "benchmark-api.log").open("w")
    worker_log = (local / "benchmark-worker.log").open("w")
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8002",
        ],
        cwd=ROOT / "backend",
        env=env,
        stdout=api_log,
        stderr=api_log,
    )
    worker = subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=ROOT / "backend",
        env=env,
        stdout=worker_log,
        stderr=worker_log,
    )
    pg_pid = int((local / "pgdata" / "postmaster.pid").read_text().splitlines()[0])
    stopped = threading.Event()
    samples = []

    def sample():
        values = {}
        for name, process_id in [
            ("api", api.pid),
            ("worker", worker.pid),
            ("postgres", pg_pid),
        ]:
            try:
                parent = psutil.Process(process_id)
                processes = (
                    [parent, *parent.children(recursive=True)]
                    if name == "postgres"
                    else [parent]
                )
                values[name] = sum(
                    getattr(p.memory_full_info(), "pss", p.memory_info().rss)
                    for p in processes
                    if p.is_running()
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                values[name] = 0
        return values

    def monitor():
        while not stopped.wait(0.15):
            samples.append(sample())

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url="http://127.0.0.1:8002", timeout=60) as client:
            for _ in range(100):
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            logged = client.post(
                "/api/v1/auth/login", json={"password": "benchmark-local-only"}
            )
            logged.raise_for_status()
            client.headers.update(
                {"X-CSRF-Token": logged.json()["csrf"], "X-Workspace": "demo"}
            )
            idle = sample()
            source = client.post(
                "/api/v1/sources",
                json={
                    "name": "性能验证来源 " + datetime.now(UTC).isoformat(),
                    "platform": "BENCHMARK",
                    "permission_scope": ["keywords"],
                    "permission_note": "合成数据，隔离测试库性能测试",
                },
            )
            source.raise_for_status()
            with file.open("rb") as upload:
                uploaded = client.post(
                    "/api/v1/imports/preview",
                    data={"source_id": source.json()["id"], "kind": "keywords"},
                    files={"file": (file.name, upload, "text/csv")},
                )
            uploaded.raise_for_status()
            stamp = datetime.now(UTC) - timedelta(minutes=1)
            end = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
            confirm = client.post(
                f"/api/v1/imports/{uploaded.json()['id']}/confirm",
                json={
                    "mapping": {
                        k: k
                        for k in [
                            "keyword",
                            "category",
                            "search_index",
                            "listing_count",
                            "conversion",
                        ]
                    },
                    "defaults": {
                        "window_start": (end - timedelta(days=7)).isoformat(),
                        "window_end": end.isoformat(),
                        "observed_at": stamp.isoformat(),
                        "metric_definition": "benchmark",
                    },
                },
            )
            confirm.raise_for_status()
            job_id = confirm.json()["id"]
            started = time.perf_counter()
            while True:
                job = client.get("/api/v1/jobs/" + job_id).json()
                if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    break
                if time.perf_counter() - started > 650:
                    raise RuntimeError("Import exceeded benchmark timeout")
                time.sleep(0.25)
            seconds = time.perf_counter() - started
            assert job["status"] == "SUCCEEDED", job
            assert job["result"]["accepted"] == 100000, job
            latencies = []
            for page in (
                list(range(1, 21)) + [100, 500, 1000, 2000, 3000, 4000, 5000] * 2
            ):
                before = time.perf_counter()
                page_response = client.get(f"/api/v1/keywords?page={page}&page_size=20")
                page_response.raise_for_status()
                latencies.append((time.perf_counter() - before) * 1000)
            result = {
                "environment": "Native PostgreSQL 16 / Linux; separate API and worker processes; Docker unavailable",
                "rows": 100000,
                "file_bytes": file.stat().st_size,
                "import_seconds": round(seconds, 3),
                "pagination_samples": len(latencies),
                "pagination_p95_ms": round(
                    sorted(latencies)[int(len(latencies) * 0.95)], 3
                ),
                "pagination_median_ms": round(statistics.median(latencies), 3),
                "memory_metric": "PSS includes API, worker and all PostgreSQL cluster processes; excludes browser, build and OS file cache",
                "idle_mib": {k: round(v / 1024**2, 2) for k, v in idle.items()},
                "peak_mib": {
                    k: round(max(s[k] for s in samples) / 1024**2, 2) for k in idle
                },
                "peak_total_mib": round(
                    max(sum(s.values()) for s in samples) / 1024**2, 2
                ),
                "result": job["result"],
                "measured_at": datetime.now(UTC).isoformat(),
            }
            (ROOT / "docs" / "benchmark.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            )
            print(json.dumps(result, ensure_ascii=False))
    finally:
        stopped.set()
        thread.join(timeout=2)
        for process in [worker, api]:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        api_log.close()
        worker_log.close()


if __name__ == "__main__":
    main()
