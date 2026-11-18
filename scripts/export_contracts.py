#!/usr/bin/env python3
"""Export reviewable contracts from the owning Python definitions."""

import argparse
import difflib
import json
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.main import app
from app.schemas import CONTRACTS


def generated():
    output = {
        "openapi.yaml": yaml.safe_dump(
            app.openapi(), allow_unicode=True, sort_keys=False
        )
    }
    sql = subprocess.check_output(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=ROOT / "backend",
        text=True,
    )
    output["schema.sql"] = "\n".join(line.rstrip() for line in sql.splitlines()).rstrip() + "\n"
    output["strategy_config.yaml"] = (
        ROOT / "config" / "strategy_config.yaml"
    ).read_text()
    dictionary = [
        "# 字段字典",
        "",
        "由 Pydantic 合同生成；完整约束及请求/响应类型见 OpenAPI。金额为十进制字符串，比例为0–1，时间输入必须带时区。",
        "",
    ]
    for kind, contract in CONTRACTS.items():
        schema = contract.model_json_schema()
        required = schema.get("required", [])
        dictionary.extend(["## " + kind, "", "| 字段 | 必需 | 约束 |", "|---|---|---|"])
        for name, field in schema["properties"].items():
            constraints = {k: v for k, v in field.items() if k != "title"}
            dictionary.append(
                f"| `{name}` | {'是' if name in required else '否'} | `{json.dumps(constraints, ensure_ascii=False).replace('|', '/ ')}` |"
            )
        dictionary.append("")
    output["字段字典.md"] = "\n".join(dictionary)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    drift = []
    for name, content in generated().items():
        path = ROOT / "docs" / name
        if options.check:
            if not path.exists() or path.read_text() != content:
                drift.append(name)
        else:
            path.write_text(content)
    if drift:
        raise SystemExit("Contract drift: " + ", ".join(drift))
    print("Contracts verified" if options.check else "Contracts exported")
