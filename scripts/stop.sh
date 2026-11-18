#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then docker_cmd=(sudo docker); fi
"${docker_cmd[@]}" compose stop
