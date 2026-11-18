#!/usr/bin/env python3
from pathlib import Path
import json
import os
import signal
import subprocess
import sys

from devdb import database
from setup import setup

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
PIDFILE = ROOT / ".local" / "services.json"


def main():
    if "stop" in sys.argv:
        if PIDFILE.exists():
            import psutil

            for pid in json.loads(PIDFILE.read_text()).values():
                try:
                    process = psutil.Process(pid)
                    if Path(process.cwd()) == ROOT / "backend" and any(
                        "uvicorn" in p or "app.worker" in p for p in process.cmdline()
                    ):
                        process.terminate()
                        process.wait(timeout=10)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            PIDFILE.unlink(missing_ok=True)
        database(False)
        print("本机服务已停止，数据保留。")
        return
    if PIDFILE.exists():
        print("已有本机服务记录，请先停止或检查 .local/services.json。")
        return
    settings = setup()
    env = {
        **os.environ,
        **settings,
        "DATABASE_URL": database(),
        "PYTHONPATH": str(ROOT / "backend"),
        "DATA_DIR": str(ROOT / ".local" / "storage"),
    }
    subprocess.run(
        [str(PYTHON), "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "backend",
        env=env,
        check=True,
    )
    subprocess.run(
        [str(PYTHON), "-m", "app.seed"], cwd=ROOT / "backend", env=env, check=True
    )
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend", check=True)
    pids = {}
    for name, arguments in {
        "api": ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        "worker": ["app.worker"],
    }.items():
        with (ROOT / ".local" / (name + ".log")).open("a") as log:
            process = subprocess.Popen(
                [str(PYTHON), "-m", *arguments],
                cwd=ROOT / "backend",
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        pids[name] = process.pid
    PIDFILE.write_text(json.dumps(pids))
    print("本机工作台：http://127.0.0.1:8000；访问口令见 .local/admin-password。")


if __name__ == "__main__":
    main()
