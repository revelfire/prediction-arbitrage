#!/usr/bin/env bash
# One-time bootstrap for arb-scanner on Hetzner (Ubuntu 24.04).
# Run as root: ssh arb-scanner 'bash -s' < infra/hetzner/bootstrap.sh
set -euo pipefail

echo "=== System update ==="
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

echo "=== Install Docker CE ==="
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "=== Create deploy user ==="
if ! id deploy &>/dev/null; then
  useradd -m -s /bin/bash deploy
fi
usermod -aG docker deploy
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys

echo "=== Create application directories ==="
mkdir -p /opt/arb-scanner/pgdata /opt/arb-scanner/backups/daily /opt/arb-scanner/backups/weekly
chown -R deploy:deploy /opt/arb-scanner
chown 999:999 /opt/arb-scanner/pgdata  # PostgreSQL container UID

echo "=== Configure UFW firewall ==="
apt-get install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw --force enable

echo "=== SSH hardening ==="
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl restart ssh

echo "=== Install backup cron ==="
cat > /etc/cron.d/arb-backup <<'CRON'
0 3 * * * deploy /opt/arb-scanner/backup.sh >> /var/log/arb-backup.log 2>&1
CRON
chmod 644 /etc/cron.d/arb-backup

echo "=== Bootstrap complete ==="
echo "Next steps:"
echo "  1. SCP configs: .env, config.yaml, kalshi-key.pem, docker-compose.prod.yml, backup.sh"
echo "  2. Add CI deploy key to /home/deploy/.ssh/authorized_keys"
echo "  3. docker login ghcr.io as deploy user"
echo "  4. docker compose -f docker-compose.prod.yml up -d"
