#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
backup_dir=".local/backups/compose-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then docker_cmd=(sudo docker); fi
"${docker_cmd[@]}" compose stop worker api
trap '"${docker_cmd[@]}" compose up -d api worker' EXIT
"${docker_cmd[@]}" compose exec -T db pg_dump -U commerceai -d commerceai --format=custom --no-owner --no-acl --exclude-table-data=login_session > "$backup_dir/database.dump"
"${docker_cmd[@]}" compose run --rm --no-deps --entrypoint python api -c 'import tarfile; t=tarfile.open(fileobj=__import__("sys").stdout.buffer,mode="w|gz"); t.add("/app/storage",arcname="storage"); t.close()' > "$backup_dir/evidence.tar.gz"
chmod 600 "$backup_dir"/*
echo "备份已保存：$backup_dir"
