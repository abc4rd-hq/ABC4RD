#!/usr/bin/env bash

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "ОШИБКА: запустите через sudo."
  exit 1
fi

source /etc/os-release
if [[ "${ID}" != "ubuntu" || "${VERSION_ID}" != "24.04" ]]; then
  echo "ОШИБКА: ожидается Ubuntu 24.04, получено ${ID} ${VERSION_ID}."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

hostnamectl set-hostname abc4rd-prod-01

apt-get update
apt-get dist-upgrade -y
apt-get install -y \
  ca-certificates \
  curl \
  fail2ban \
  gnupg \
  python3-venv \
  ufw \
  unattended-upgrades

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

apt-get update
apt-get install -y \
  containerd.io \
  docker-buildx-plugin \
  docker-ce \
  docker-ce-cli \
  docker-compose-plugin

usermod -aG docker ubuntu
systemctl enable --now docker
systemctl enable --now fail2ban

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

install -d -m 0755 /opt/abc4rd

echo "HOST_BOOTSTRAP_COMPLETE"
docker --version
docker compose version
ufw status | head -n 5
