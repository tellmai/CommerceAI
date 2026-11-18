#!/usr/bin/env python3
"""Generate credentials once; never print them into build logs."""

from pathlib import Path
import os
import secrets

ROOT = Path(__file__).resolve().parents[1]


def setup():
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    target = ROOT / ".env"
    if not target.exists():
        password = secrets.token_urlsafe(24)
        target.write_text(
            f"POSTGRES_PASSWORD={secrets.token_hex(24)}\nOWNER_PASSWORD={password}\nPORT=8080\n"
        )
        target.chmod(0o600)
        secret = local / "admin-password"
        secret.write_text(password + "\n")
        secret.chmod(0o600)
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in target.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    }


if __name__ == "__main__":
    setup()
    print("配置已准备。访问口令保存在 .local/admin-password（仅本机用户可读）。")
