# 020 — CI/CD & Hetzner Deployment — Implementation Plan

## Architecture Overview

```
Developer
  │
  ├─ git push / PR
  │     │
  │     ▼
  │  GitHub Actions
  │     ├─ quality-gate.yml ──→ ruff + mypy + pytest
  │     │
  │     └─ build-and-deploy.yml (main only)
  │           ├─ Docker build ──→ ghcr.io/revelfire/prediction-arbitrage
  │           └─ SSH deploy ──→ Hetzner CX22
  │                               ├─ docker compose pull
  │                               ├─ docker compose up -d
  │                               └─ curl /api/health
  │
  └─ Tailscale VPN ──→ http://<tailscale-ip>:8000
                         │
                         ▼
                    FastAPI + Bearer Token Auth
                         │
                         ▼
                    PostgreSQL (pgvector)
                         │
                         ▼
                    Daily backup → Hetzner Object Storage
```

## Phase 1: GitHub Actions CI Pipeline

### 1.1 Quality Gate Workflow (`.github/workflows/quality-gate.yml`)

**Trigger**: Push to any branch + PR to main.

```yaml
jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - Checkout
      - Install uv (astral-sh/setup-uv action)
      - uv sync --frozen --dev
      - ruff check src/ tests/
      - ruff format --check src/ tests/
      - mypy src/ --strict
      - pytest tests/ -x --tb=short -m "not live" --cov=src/arb_scanner --cov-fail-under=70
```

**Key decisions**:
- Use `astral-sh/setup-uv@v4` for consistent uv installation.
- Cache `~/.cache/uv` and `.venv` for speed.
- Single job (not parallelized) — the full gate runs in ~2-3 minutes.
- PostgreSQL service container for DB-dependent integration tests.
- `requires_postgres` tests need a real DB; run them in CI with a service container.

**PostgreSQL in CI**:
```yaml
services:
  postgres:
    image: pgvector/pgvector:pg15
    env:
      POSTGRES_USER: arb_scanner
      POSTGRES_PASSWORD: test
      POSTGRES_DB: arb_scanner
    ports:
      - 5432:5432
    options: >-
      --health-cmd pg_isready
      --health-interval 5s
      --health-timeout 5s
      --health-retries 5
```

Set `DATABASE_URL=postgresql://arb_scanner:test@localhost:5432/arb_scanner` in the test step env.
Run migrations before tests: `uv run arb-scanner migrate`.

### 1.2 Build & Deploy Workflow (`.github/workflows/build-and-deploy.yml`)

**Trigger**: Push to `main` (after quality gate passes).

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - Checkout
      - docker/setup-buildx-action
      - docker/login-action (ghcr.io, GITHUB_TOKEN)
      - docker/build-push-action
          tags: ghcr.io/revelfire/prediction-arbitrage:latest,
                ghcr.io/revelfire/prediction-arbitrage:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - SSH to Hetzner VM
      - cd /opt/arb-scanner
      - docker compose -f docker-compose.prod.yml pull
      - docker compose -f docker-compose.prod.yml up -d
      - sleep 5 && curl -sf http://localhost:8000/api/health
      - Notify Slack/Discord (success or failure)
```

**GitHub Actions Secrets needed**:
- `HETZNER_SSH_PRIVATE_KEY` — Deploy SSH key (ed25519).
- `HETZNER_HOST` — VM IP or Tailscale hostname.
- `DEPLOY_SLACK_WEBHOOK_URL` — Deploy notifications (optional, can reuse existing).

## Phase 2: Terraform Infrastructure

### 2.1 Directory Structure

```
infra/terraform/
  main.tf           # Provider config, resources
  variables.tf      # Input variables with defaults
  outputs.tf        # Useful outputs (IP, SSH command)
  cloud-init.yml    # First-boot script (templatefile)
  .gitignore        # *.tfstate, *.tfstate.backup, .terraform/
```

### 2.2 Resources (`main.tf`)

**Provider**: `hetznercloud/hcloud` (latest).

**Resources**:

1. `hcloud_ssh_key.deploy` — Public key for SSH access.

2. `hcloud_firewall.scanner` — Firewall rules:
   - Inbound TCP 22 from `var.allowed_ssh_cidrs` (your IP/range).
   - Inbound UDP 41641 from `0.0.0.0/0` (Tailscale).
   - All outbound allowed (API calls, GHCR pulls).
   - NO inbound on 8000, 5432, or any other port.

3. `hcloud_volume.pgdata` — 20 GB persistent volume for PostgreSQL data.
   - `delete_protection = true` (prevent accidental terraform destroy).

4. `hcloud_server.scanner` — CX22 VM:
   - `server_type = "cx22"` (2 vCPU, 4 GB RAM, 40 GB SSD).
   - `location = var.location` (default: `nbg1`).
   - `image = "ubuntu-24.04"`.
   - `ssh_keys = [hcloud_ssh_key.deploy.id]`.
   - `firewall_ids = [hcloud_firewall.scanner.id]`.
   - `user_data = templatefile("cloud-init.yml", { ... })`.

5. `hcloud_volume_attachment.pgdata` — Attach volume to server.

### 2.3 Variables (`variables.tf`)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `hcloud_token` | string | — | Hetzner API token (sensitive) |
| `location` | string | `nbg1` | Datacenter location |
| `server_type` | string | `cx22` | VM size |
| `ssh_public_key` | string | — | Deploy SSH public key |
| `allowed_ssh_cidrs` | list(string) | `["0.0.0.0/0"]` | SSH source CIDR allowlist |
| `tailscale_auth_key` | string | — | Tailscale pre-auth key (sensitive) |
| `ghcr_token` | string | — | GitHub PAT for GHCR pull (sensitive) |

### 2.4 Cloud-Init Script (`cloud-init.yml`)

Runs on first boot as root:

1. **System updates**: `apt-get update && apt-get upgrade -y`.
2. **Docker**: Install Docker CE + Compose plugin via official apt repo.
3. **Tailscale**: Install via `curl -fsSL https://tailscale.com/install.sh | sh`, then `tailscale up --auth-key=<key>`.
4. **Mount volume**: Format (if new) and mount `/mnt/pgdata`, add to `/etc/fstab`.
5. **App directory**: Create `/opt/arb-scanner/`, copy `docker-compose.prod.yml`.
6. **GHCR login**: `echo $GHCR_TOKEN | docker login ghcr.io -u revelfire --password-stdin`.
7. **SSH hardening**: Disable password auth, disable root login, restart sshd.
8. **Backup cron**: Install `backup.sh` and cron entry (see Phase 4).
9. **Start services**: `cd /opt/arb-scanner && docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d`.

After cloud-init completes, the operator must:
- Copy `.env` (with real secrets) to `/opt/arb-scanner/.env`.
- Copy `config.yaml` to `/opt/arb-scanner/config.yaml`.
- Copy Kalshi PEM to `/opt/arb-scanner/kalshi_key.pem` (chmod 600).
- Restart: `docker compose -f docker-compose.prod.yml up -d`.

## Phase 3: Dashboard Bearer Token Auth

### 3.1 Config Changes

Add to `DashboardConfig` in `models/config.py`:
```python
auth_token: str | None = None  # Set via DASHBOARD_AUTH_TOKEN env var or config.yaml
```

### 3.2 Auth Middleware

New file: `src/arb_scanner/api/auth.py` (~40 lines).

```python
from starlette.middleware.base import BaseHTTPMiddleware

class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require Bearer token for API/dashboard access."""

    EXEMPT_PATHS = {"/api/health", "/api/health/"}

    async def dispatch(self, request, call_next):
        if not self.token:
            return await call_next(request)  # Auth disabled
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)  # Health exempt
        # Check Authorization header or ?token= query param
        auth = request.headers.get("authorization", "")
        query_token = request.query_params.get("token", "")
        if auth == f"Bearer {self.token}" or query_token == self.token:
            return await call_next(request)
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
```

### 3.3 WebSocket Auth

For `/api/price-stream` and `/api/ws-telemetry`, check the `token` query parameter during the WebSocket handshake. Reject with 1008 (Policy Violation) close code if invalid.

### 3.4 Dashboard JavaScript

Inject the token into the HTML page via a `<meta name="api-token">` tag (set server-side from config). All `fetch()` calls in `app.js` include `Authorization: Bearer ${token}`. If no token is configured, the meta tag is absent and no header is added.

## Phase 4: Backup Automation

### 4.1 Backup Script (`infra/scripts/backup.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/tmp/arb-backup"
BUCKET="s3://arb-scanner-backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="arb_scanner_${TIMESTAMP}.sql.gz"

# Dump and compress
docker compose -f /opt/arb-scanner/docker-compose.prod.yml exec -T db \
  pg_dump -U arb_scanner arb_scanner | gzip > "${BACKUP_DIR}/${FILENAME}"

# Verify non-empty
[ -s "${BACKUP_DIR}/${FILENAME}" ] || { echo "ERROR: Empty backup"; exit 1; }

# Upload to Hetzner Object Storage
s3cmd put "${BACKUP_DIR}/${FILENAME}" "${BUCKET}/daily/${FILENAME}"

# Rotate: keep 7 daily, 4 weekly
# (cleanup logic for old backups)

rm "${BACKUP_DIR}/${FILENAME}"
echo "Backup complete: ${FILENAME}"
```

### 4.2 Restore Script (`infra/scripts/restore.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail
FILENAME="${1:?Usage: restore.sh <backup_filename>}"
s3cmd get "s3://arb-scanner-backups/daily/${FILENAME}" /tmp/restore.sql.gz
gunzip /tmp/restore.sql.gz
docker compose -f /opt/arb-scanner/docker-compose.prod.yml exec -T db \
  psql -U arb_scanner arb_scanner < /tmp/restore.sql
echo "Restore complete from ${FILENAME}"
```

### 4.3 Cron Entry

```cron
0 3 * * * /opt/arb-scanner/backup.sh >> /var/log/arb-backup.log 2>&1
```

## Phase 5: Production Docker Compose

### 5.1 `docker-compose.prod.yml`

Key differences from dev `docker-compose.yml`:

| Aspect | Dev | Prod |
|--------|-----|------|
| Image source | Local build (`.`) | `ghcr.io/revelfire/prediction-arbitrage:latest` |
| Source mounts | `./src:/app/src` | None (immutable) |
| Config mount | `./config.yaml` | `/opt/arb-scanner/config.yaml:ro` |
| PG data | Named volume `pgdata` | Host path `/mnt/pgdata` (Hetzner Volume) |
| Log driver | Default | `json-file` with rotation |
| Restart | `unless-stopped` | `unless-stopped` |
| Kalshi PEM | Local path | `/opt/arb-scanner/kalshi_key.pem:ro` |

### 5.2 Services

Same four services as dev: `db`, `migrate`, `dashboard`, `scanner`. The `scan` (one-shot) profile is available for manual runs.

## Implementation Order

| Step | Phase | Dependencies | Deliverables |
|------|-------|-------------|-------------|
| 1 | GitHub Actions quality gate | None | `.github/workflows/quality-gate.yml` |
| 2 | Dashboard bearer token auth | None | `api/auth.py`, config change, JS update |
| 3 | Production docker-compose | None | `docker-compose.prod.yml` |
| 4 | Terraform infrastructure | None | `infra/terraform/*.tf`, `cloud-init.yml` |
| 5 | Docker build & push workflow | Step 1 | Updated `build-and-deploy.yml` |
| 6 | Deploy workflow | Steps 3, 4, 5 | Deploy job in `build-and-deploy.yml` |
| 7 | Backup automation | Step 4 | `infra/scripts/backup.sh`, `restore.sh`, cron |
| 8 | Deploy notifications | Step 6 | Slack/Discord notify step in workflow |
| 9 | End-to-end validation | All | Test full pipeline, document runbook |

Steps 1–4 can be done in parallel. Steps 5–8 are sequential. Step 9 is final validation.

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Cloud-init fails silently | Medium | High | Test locally via `cloud-init devel`, verify via SSH after provision |
| Terraform state corruption | Low | Medium | Backup state file before changes, document import commands |
| Docker build too slow | Low | Low | GHA cache + buildx layer caching, target < 5 min |
| Tailscale auth key expires | Medium | Medium | Use reusable key with long TTL, document renewal |
| Database migration breaks deploy | Low | High | Test migrations in CI (service container), manual rollback procedure |
| Disk fills from logs/ticks | Medium | Medium | Log rotation config, tick pruning cron, monitoring at 80% |
