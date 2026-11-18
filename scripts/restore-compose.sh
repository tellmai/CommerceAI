#!/usr/bin/env bash
# Restore into a separate Compose project, never the normal project's volumes.
set -euo pipefail
cd "$(dirname "$0")/.."
backup_dir="${1:?请提供 backup-compose.sh 生成的备份目录}"
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then docker_cmd=(sudo docker); fi
if [ -n "$("${docker_cmd[@]}" compose -p commerceai-restorecheck ps -aq)" ]; then
  echo 'commerceai-restorecheck 已存在，拒绝覆盖；请先检查旧的恢复验证环境。' >&2
  exit 1
fi
PORT=8081 "${docker_cmd[@]}" compose -p commerceai-restorecheck up -d --wait db
"${docker_cmd[@]}" compose -p commerceai-restorecheck exec -T db pg_restore -U commerceai -d commerceai --no-owner --no-acl < "$backup_dir/database.dump"
"${docker_cmd[@]}" compose -p commerceai-restorecheck run --rm --no-deps --entrypoint python -T api -c 'import sys,tarfile; t=tarfile.open(fileobj=sys.stdin.buffer,mode="r|gz"); t.extractall("/app",filter="data"); t.close()' < "$backup_dir/evidence.tar.gz"
PORT=8081 "${docker_cmd[@]}" compose -p commerceai-restorecheck up -d
echo '独立恢复环境：http://127.0.0.1:8081；验证后停止该项目，不影响原工作台。'
