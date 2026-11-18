#!/usr/bin/env python3
"""Isolated native PostgreSQL fallback for development when Docker is unavailable."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def database(start=True):
    import pgserver

    binary = Path(pgserver.__file__).parent / "pginstall" / "bin"
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    pgdata, socket = local / "pgdata", local / "pgsocket"
    socket.mkdir(exist_ok=True)
    socket.chmod(0o700)
    if not (pgdata / "PG_VERSION").exists():
        subprocess.run(
            [
                str(binary / "initdb"),
                "-D",
                str(pgdata),
                "-U",
                "commerceai",
                "--auth=trust",
                "--encoding=UTF8",
                "--no-locale",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        with (pgdata / "postgresql.conf").open("a") as out:
            out.write(
                f"\nlisten_addresses = ''\nport = 55432\nunix_socket_directories = '{socket}'\n"
                "shared_buffers = 128MB\nwork_mem = 4MB\nmaintenance_work_mem = 64MB\nmax_connections = 20\n"
            )
    running = (
        subprocess.run(
            [str(binary / "pg_ctl"), "-D", str(pgdata), "status"], capture_output=True
        ).returncode
        == 0
    )
    if start and not running:
        subprocess.run(
            [
                str(binary / "pg_ctl"),
                "-D",
                str(pgdata),
                "-l",
                str(local / "postgres.log"),
                "-w",
                "start",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    if start:
        subprocess.run(
            [
                str(binary / "createdb"),
                "-h",
                str(socket),
                "-p",
                "55432",
                "-U",
                "commerceai",
                "commerceai",
            ],
            capture_output=True,
        )
    elif running:
        subprocess.run(
            [str(binary / "pg_ctl"), "-D", str(pgdata), "-m", "fast", "-w", "stop"],
            check=True,
        )
    return f"postgresql+psycopg://commerceai@/commerceai?host={socket}&port=55432"


if __name__ == "__main__":
    print(database(start="--stop" not in sys.argv))
