#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/setup.py
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then docker_cmd=(sudo docker); fi
"${docker_cmd[@]}" compose up -d --build
echo '工作台：http://127.0.0.1:8080；访问口令见 .local/admin-password'
