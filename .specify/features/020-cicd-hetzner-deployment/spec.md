# 020 — CI/CD Pipeline & DigitalOcean Deployment

## Overview

Establish a complete CI/CD pipeline via GitHub Actions and deploy the prediction arbitrage scanner to a DigitalOcean VM. Includes automated quality gates on PR/push, Docker image builds pushed to GitHub Container Registry (GHCR), auto-deployment on merge to main, Terraform-managed infrastructure, Tailscale VPN for network access, bearer-token dashboard auth, and PostgreSQL backup automation.

## Motivation

The scanner currently runs only in local Docker Compose. To operate 24/7 — capturing fleeting arbitrage opportunities and running the auto-execution pipeline unattended — it needs cloud hosting. Manual deployment via SSH is error-prone and doesn't enforce quality gates. A proper CI/CD pipeline ensures every merge to main is linted, typed, tested, built, and deployed automatically, while infrastructure-as-code makes the environment reproducible.

## Prerequisites

- Features 001–019 complete (the full application stack).
- GitHub repository at `git@github.com:revelfire/prediction-arbitrage.git`.
- DigitalOcean account with API token.
- Tailscale account (free personal tier).
- Domain (optional): `spillwave.com` for subdomain pointing.

## Functional Requirements

### FR-001: GitHub Actions Quality Gate

On every push to any branch and every PR:

1. **Lint**: `ruff check src/ tests/` — zero errors.
2. **Format**: `ruff format --check src/ tests/` — clean.
3. **Type check**: `mypy src/ --strict` — zero errors.
4. **Unit tests**: `pytest tests/ -x --tb=short -m "not live"` — all pass.
5. **Coverage**: `pytest tests/ --cov=src/arb_scanner --cov-fail-under=70` — ≥70%.

Live API tests are **excluded** from CI (no `LIVE_TESTS=1`, no API keys in GitHub secrets). They remain a local/manual concern.

The quality gate workflow uses `uv` for dependency installation (matching local dev) and caches the uv environment between runs.

### FR-002: Docker Build & Push to GHCR

On merge to `main`, after the quality gate passes:

1. Build the Docker image using the existing multi-stage `Dockerfile`.
2. Tag with both `latest` and the short Git SHA (e.g., `ghcr.io/revelfire/prediction-arbitrage:a1b2c3d`).
3. Push to GitHub Container Registry (`ghcr.io/revelfire/prediction-arbitrage`).
4. Use Docker layer caching to speed up builds.

The GHCR token is the built-in `GITHUB_TOKEN` — no additional secrets needed for pushing.

### FR-003: Auto-Deploy to DigitalOcean on Merge

After the Docker image is pushed to GHCR:

1. SSH to the DigitalOcean VM (via Tailscale IP or public IP with key-based auth).
2. Run `docker compose pull` to fetch the new image.
3. Run `docker compose up -d` to restart services with the new image.
4. Run a health check: `curl -sf http://localhost:8000/api/health` with retry (up to 30s).
5. On success: send a Slack/Discord notification with the deployed SHA.
6. On failure: send a failure notification. Do **not** auto-rollback (operator investigates).

Deployment uses a dedicated SSH key stored as a GitHub Actions secret.

### FR-004: Terraform Infrastructure

Provision the DigitalOcean environment using Terraform:

1. **VM**: DigitalOcean s-2vcpu-4gb Droplet (2 vCPU, 4 GB RAM, 40 GB NVMe SSD, ~€4.35/mo).
   - Location: `nbg1` (Nuremberg) or `fsn1` (Falkenstein) — configurable.
   - Image: Ubuntu 24.04 LTS.
   - SSH key: Provisioned via Terraform.

2. **Firewall**: DigitalOcean Firewall attached to the VM.
   - Allow inbound SSH (TCP 22) from configurable IP allowlist.
   - Allow inbound Tailscale UDP (41641) from anywhere (Tailscale handles its own auth).
   - Deny all other inbound traffic. Port 8000 is **NOT** exposed to the internet.

3. **Volume** (optional, for PostgreSQL persistence across VM recreation):
   - DigitalOcean Volume, 20 GB, mounted at `/mnt/pgdata`.
   - Survives `terraform destroy` + `terraform apply` cycle.

4. **Outputs**: VM public IP, Tailscale instructions, SSH command.

Terraform state is stored locally (single operator) with `.gitignore` for `*.tfstate*`. Optionally migrateable to Terraform Cloud later.

### FR-005: VM Bootstrap Script

A cloud-init or shell script that runs on first boot:

1. Install Docker Engine + Docker Compose plugin.
2. Install Tailscale and join the tailnet (using a pre-auth key).
3. Install ExpressVPN, activate with code, set auto-connect, connect to Mexico (geo-routing for prediction market APIs). **Must run after Tailscale** so the VPN tunnel is established before ExpressVPN routes traffic. Once VPN is active, SSH via public IP may be unreliable — use Tailscale IP for all management access.
4. Create the app directory (`/opt/arb-scanner/`).
5. Copy `docker-compose.prod.yml` and `.env` template.
6. Log in to GHCR (`docker login ghcr.io`).
7. Pull the latest image and start services.
8. Set up the backup cron job (FR-007).
9. Harden SSH: disable password auth, disable root login.

### FR-006: Dashboard Authentication (Bearer Token)

Add a lightweight auth middleware to the FastAPI app:

1. New config field: `dashboard.auth_token` (string, optional). When set, all `/api/*` endpoints and the dashboard static files require `Authorization: Bearer <token>` header or `?token=<token>` query parameter.
2. When `auth_token` is not set (empty/null), auth is disabled (backward compatible for local dev).
3. The `/api/health` endpoint is **exempt** from auth (needed for monitoring/deploy checks).
4. Unauthorized requests receive `401 Unauthorized` with a JSON error body.
5. The dashboard JavaScript includes the token in all fetch requests (read from a `<meta>` tag or prompt on first visit).
6. WebSocket endpoints (`/api/price-stream`, `/api/ws-telemetry`) check the token on connection handshake via query parameter.

**Token storage**: Set via `DASHBOARD_AUTH_TOKEN` env var or `dashboard.auth_token` in config.yaml. A random 32-character hex string is recommended.

This is defense-in-depth behind the Tailscale VPN. If the VPN is compromised, the token provides a second layer.

### FR-007: PostgreSQL Backup Automation

Automated database backups:

1. Cron job on the VM: `pg_dump` → gzip → upload to DigitalOcean Object Storage (S3-compatible).
2. Schedule: daily at 03:00 UTC.
3. Retention: 7 daily backups + 4 weekly backups (Sunday).
4. Backup script verifies the dump is non-empty before uploading.
5. On backup failure: log error to stdout (captured by systemd journal).

**Object Storage**: DigitalOcean S3-compatible bucket, configured via `s3cmd` or `aws cli` with DigitalOcean endpoint. Estimated cost: ~€1/month.

### FR-008: Production Docker Compose

A `docker-compose.prod.yml` that differs from the dev `docker-compose.yml`:

1. Uses GHCR image (`ghcr.io/revelfire/prediction-arbitrage:latest`) instead of local build.
2. No source volume mounts (immutable container).
3. Config mounted read-only from `/opt/arb-scanner/config.yaml`.
4. PostgreSQL data on the DigitalOcean Volume (`/mnt/pgdata`).
5. Restart policy: `unless-stopped` on all services.
6. Logging driver: `json-file` with `max-size: 10m`, `max-file: 3` (prevent disk fill).
7. Kalshi PEM file mounted read-only from `/opt/arb-scanner/kalshi_key.pem`.

### FR-009: Deploy Notifications

On every deployment (success or failure):

1. Send a Slack/Discord webhook message with:
   - Commit SHA + message.
   - Deploy status (success/failure).
   - Health check result.
   - Link to the GitHub Actions run.
2. Use the existing `ARBITRAGE_SLACK_WEBHOOK_URL` or a separate `DEPLOY_SLACK_WEBHOOK_URL`.

## Non-Functional Requirements

### NFR-001: Deploy Speed
Full CI/CD pipeline (quality gate + build + deploy) completes in under 10 minutes.

### NFR-002: Zero-Downtime Tolerance
Brief downtime during container restart is acceptable (single-operator tool, not customer-facing). Target: < 30 seconds.

### NFR-003: Cost
Total monthly infrastructure cost under €10/month (VM + storage + backups).

### NFR-004: Reproducibility
Running `terraform apply` from a clean state + the bootstrap script produces a fully operational environment.

### NFR-005: Security
- No secrets in Docker images or git.
- SSH key-based auth only (no passwords).
- Dashboard not accessible from public internet.
- All API keys and wallet credentials in `.env` with `chmod 600`.
- Kalshi PEM file with `chmod 600`.

## Edge Cases

### EC-001: Deploy During Active Trade
If auto-execution is placing an order during deployment, the container restart interrupts it. Mitigation: the execution engine already handles partial fills and the circuit breaker will detect anomalies. Operator should pause auto-execution before deploying (or accept the risk for the brief restart window).

### EC-002: Database Migration on Deploy
New features may include SQL migrations. The `migrate` service in docker-compose runs before the app starts. If a migration fails, the deploy is blocked. Notification fires with failure status.

### EC-003: GHCR Rate Limiting
GHCR has rate limits for unauthenticated pulls. The VM authenticates to GHCR using a GitHub PAT or `GITHUB_TOKEN`, so limits are generous (5,000 pulls/hour). No issue for single-VM deploys.

### EC-004: Tailscale Down
If Tailscale is temporarily unavailable, the VPN tunnel drops. The app continues running; you just can't access the dashboard until the tunnel reconnects. Tailscale auto-reconnects. Emergency access: SSH via public IP (if firewall allows your IP).

### EC-005: VM Disk Full
Structlog JSON output and Docker logs can fill disk. Mitigated by log rotation (`max-size: 10m`, `max-file: 3`) and tick pruning (`flip-tick-prune --days 90`). Monitoring should alert at 80% disk usage.

### EC-006: Terraform State Loss
Local Terraform state file is lost. Recovery: `terraform import` the existing resources or rebuild from scratch (the VM is ephemeral, PostgreSQL data is on the persistent volume). Document import commands in the runbook.

## Success Criteria

### SC-001: Pipeline Executes End-to-End
A merge to main triggers: quality gate → Docker build → GHCR push → deploy → health check → notification. All steps pass.

### SC-002: Terraform Reproducibility
`terraform destroy && terraform apply` followed by the bootstrap script produces a working environment within 15 minutes.

### SC-003: Dashboard Access
Dashboard is accessible via `http://<tailscale-ip>:8000` with bearer token, and is NOT accessible via the public IP on port 8000.

### SC-004: Backup Restoration
A backup can be restored to a fresh PostgreSQL instance and the app starts correctly with all historical data intact.

### SC-005: Auth Enforcement
Requests to `/api/opportunities` without a valid bearer token return 401. Requests to `/api/health` succeed without a token.

## File Structure

```
.github/
  workflows/
    quality-gate.yml          # PR/push: lint, type, test
    build-and-deploy.yml      # Main merge: build → push → deploy
infra/
  terraform/
    main.tf                   # DigitalOcean VM, firewall, volume, SSH key
    variables.tf              # Configurable inputs (location, size, IPs)
    outputs.tf                # VM IP, SSH command, Tailscale instructions
    cloud-init.yml            # First-boot provisioning script
  scripts/
    backup.sh                 # pg_dump → gzip → S3 upload
    restore.sh                # S3 download → pg_restore
docker-compose.prod.yml       # Production compose (GHCR image, no volumes)
```

## Out of Scope

- Blue-green or canary deployments (single-VM, single-operator).
- Kubernetes, Nomad, or any orchestrator beyond Docker Compose.
- Multi-region or HA (can be added later with DigitalOcean Load Balancer).
- Grafana/Loki observability stack (future feature).
- SIWE wallet authentication (decided: bearer token + VPN is sufficient).
- Automatic rollback on deploy failure (operator investigates manually).
- Live API tests in CI (remain local/manual).
