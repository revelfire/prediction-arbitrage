# Hetzner Deployment

Server: Ubuntu 24.04, 4 vCPU / 8GB RAM / 160GB SSD, Helsinki.

## SSH Access

```bash
ssh arb-scanner          # configured in ~/.ssh/config
```

## Dashboard Access (SSH Tunnel)

The dashboard binds to `127.0.0.1:8060` and is not publicly accessible. Use an SSH tunnel:

```bash
ssh -L 8060:localhost:8060 arb-scanner
```

Then open http://localhost:8060 in your browser.

Background tunnel (no interactive shell):

```bash
ssh -fN -L 8060:localhost:8060 arb-scanner
```

Optional shell alias for `~/.zshrc`:

```bash
alias arb-dash='ssh -fN -L 8060:localhost:8060 arb-scanner && open http://localhost:8060'
```

## One-Time Bootstrap

```bash
# 1. Run bootstrap script
ssh arb-scanner 'bash -s' < infra/hetzner/bootstrap.sh

# 2. Generate deploy keypair for CI/CD
ssh-keygen -t ed25519 -f ~/.ssh/id_arb_deploy -C "arb-scanner-ci" -N ""

# 3. Add deploy public key to server
ssh arb-scanner "echo '$(cat ~/.ssh/id_arb_deploy.pub)' >> /home/deploy/.ssh/authorized_keys"

# 4. SCP production files
scp docker-compose.prod.yml arb-scanner:/opt/arb-scanner/
scp infra/hetzner/backup.sh arb-scanner:/opt/arb-scanner/
scp config.yaml arb-scanner:/opt/arb-scanner/
scp kalshi-key.pem arb-scanner:/opt/arb-scanner/
# Create .env from .env.example with real credentials, then:
scp .env arb-scanner:/opt/arb-scanner/

# 5. Fix permissions
ssh arb-scanner 'chmod 600 /opt/arb-scanner/.env /opt/arb-scanner/kalshi-key.pem && chmod 755 /opt/arb-scanner/backup.sh'

# 6. GHCR login + first deploy
ssh arb-scanner 'su - deploy -c "cd /opt/arb-scanner && docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d"'

# 7. Verify
ssh arb-scanner 'curl -sf http://localhost:8060/api/health'
```

## GitHub Secrets

Configure in repo Settings > Secrets and variables > Actions:

| Secret | Value |
|--------|-------|
| `DEPLOY_HOST` | `204.168.136.76` |
| `DEPLOY_SSH_PRIVATE_KEY` | Contents of `~/.ssh/id_arb_deploy` |
| `GHCR_TOKEN` | GitHub PAT with `read:packages` scope |
| `KALSHI_RSA_PRIVATE_KEY_PEM` | Kalshi RSA private key (if using execution) |
| `DEPLOY_SLACK_WEBHOOK_URL` | Slack webhook for deploy notifications |

## Backups

Daily at 03:00 UTC via cron. Stored in `/opt/arb-scanner/backups/`.

- Daily: 7-day retention
- Weekly (Sunday): 28-day retention

Manual backup: `ssh arb-scanner '/opt/arb-scanner/backup.sh'`

## Firewall

UFW allows only SSH (port 22) inbound. All other ports are blocked. The dashboard is accessible only via SSH tunnel.

## Future: DNS + HTTPS

To add `arb-scanner.spillwave.com`:

1. Add DNS A record pointing to `204.168.136.76`
2. Install Caddy on the server (auto-HTTPS)
3. `ufw allow 443/tcp`
4. Caddy reverse proxies to `localhost:8060`

## Service Management

```bash
# View running containers
ssh arb-scanner 'cd /opt/arb-scanner && docker compose -f docker-compose.prod.yml ps'

# View logs
ssh arb-scanner 'cd /opt/arb-scanner && docker compose -f docker-compose.prod.yml logs -f dashboard'

# Restart all services
ssh arb-scanner 'cd /opt/arb-scanner && docker compose -f docker-compose.prod.yml restart'

# Pull latest and redeploy
ssh arb-scanner 'cd /opt/arb-scanner && docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d'
```
