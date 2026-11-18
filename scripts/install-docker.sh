#!/usr/bin/env bash
# Ubuntu installation using Docker's official apt repository. Run explicitly with sudo.
set -euo pipefail
if [ "$(id -u)" != 0 ]; then
  echo '安装 Docker Engine 需要管理员权限，请运行 sudo bash scripts/install-docker.sh。' >&2
  exit 1
fi
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nSigned-By: /etc/apt/keyrings/docker.asc\nArchitectures: %s\n' "$VERSION_CODENAME" "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/docker.sources
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
echo 'Docker Engine 和 Compose 已安装。使用 sudo docker compose 启动；本脚本不修改用户组权限。'
