# 020 — CI/CD & Hetzner Deployment — Tasks

## Phase 1: GitHub Actions Quality Gate

### Task 1.1: Create quality gate workflow
- [ ] Create `.github/workflows/quality-gate.yml`
- [ ] Trigger on push to any branch + PR to main
- [ ] Use `astral-sh/setup-uv@v4` with caching
- [ ] Add pgvector/pgvector:pg15 service container for DB-dependent tests
- [ ] Set `DATABASE_URL` env var pointing to service container
- [ ] Run `uv run arb-scanner migrate` before tests
- [ ] Steps: checkout → setup-uv → uv sync → ruff check → ruff format --check → mypy --strict → pytest (with coverage)
- [ ] Exclude live tests: `-m "not live"`
- [ ] Coverage threshold: `--cov-fail-under=70`

### Task 1.2: Validate quality gate locally
- [ ] Run `act` or push a test branch to verify workflow executes correctly
- [ ] Confirm all 5 quality gates pass in CI environment
- [ ] Confirm PostgreSQL service container starts and migrations run
- [ ] Verify caching reduces subsequent run times

## Phase 2: Dashboard Bearer Token Auth

### Task 2.1: Add auth_token to DashboardConfig
- [ ] Add `auth_token: str | None = None` field to `DashboardConfig` in `models/config.py`
- [ ] Support `DASHBOARD_AUTH_TOKEN` env var interpolation in config.yaml
- [ ] Add `dashboard.auth_token` entry (commented out) to `config.example.yaml`
- [ ] Add `DASHBOARD_AUTH_TOKEN=` (commented out) to `.env.example`

### Task 2.2: Implement BearerTokenMiddleware
- [ ] Create `src/arb_scanner/api/auth.py` (~40 lines)
- [ ] `BearerTokenMiddleware` extends `BaseHTTPMiddleware`
- [ ] Accept token via constructor from config
- [ ] If token is None/empty, pass through all requests (auth disabled)
- [ ] Exempt `/api/health` and `/api/health/` from auth
- [ ] Check `Authorization: Bearer <token>` header
- [ ] Check `?token=<token>` query parameter as fallback
- [ ] Return 401 JSON `{"error": "Unauthorized"}` on failure
- [ ] Register middleware in `api/app.py` during lifespan/startup

### Task 2.3: Add WebSocket auth check
- [ ] In `routes_price_stream.py` WebSocket endpoint, check `token` query parameter on connect
- [ ] In `routes_ws_telemetry.py` WebSocket endpoint, check `token` query parameter on connect
- [ ] Close with code 1008 (Policy Violation) if token is configured but not provided/invalid
- [ ] Skip check if auth_token is not configured

### Task 2.4: Update dashboard JavaScript for auth
- [ ] In `api/app.py`, inject `<meta name="api-token" content="...">` in HTML response when token is set
- [ ] In `app.js`, read token from `<meta>` tag
- [ ] Add `Authorization: Bearer ${token}` header to all `fetch()` calls when token exists
- [ ] Add `?token=${token}` to WebSocket connection URLs when token exists
- [ ] Graceful fallback: if no meta tag, no auth headers added (backward compat)

### Task 2.5: Test bearer token auth
- [ ] Unit test: middleware allows requests with valid token
- [ ] Unit test: middleware blocks requests with invalid/missing token (401)
- [ ] Unit test: /api/health is exempt from auth
- [ ] Unit test: middleware passes all requests when token is None (disabled)
- [ ] Unit test: WebSocket rejects without token when configured
- [ ] Integration test: full request cycle with auth enabled

## Phase 3: Production Docker Compose

### Task 3.1: Create docker-compose.prod.yml
- [ ] Copy structure from `docker-compose.yml`
- [ ] Replace `build: .` with `image: ghcr.io/revelfire/prediction-arbitrage:latest` on all app services
- [ ] Remove source volume mounts (`./src:/app/src`)
- [ ] Mount config as `/opt/arb-scanner/config.yaml:ro`
- [ ] Mount Kalshi PEM as `/opt/arb-scanner/kalshi_key.pem:ro` (if present)
- [ ] Change pgdata volume to host path `/mnt/pgdata` for Hetzner Volume persistence
- [ ] Add logging driver config: `json-file`, `max-size: 10m`, `max-file: "3"`
- [ ] Keep same service names and dependencies (db, migrate, dashboard, scanner, scan)
- [ ] Add `env_file: .env` on all services

### Task 3.2: Test prod compose locally
- [ ] Build and push a test image locally
- [ ] Run `docker compose -f docker-compose.prod.yml up` and verify all services start
- [ ] Verify health endpoint responds
- [ ] Verify dashboard loads with auth token

## Phase 4: Terraform Infrastructure

### Task 4.1: Create Terraform project structure
- [ ] Create `infra/terraform/` directory
- [ ] Add `.gitignore` for `*.tfstate*`, `.terraform/`, `*.tfvars` (secrets)
- [ ] Create `versions.tf` with `hetznercloud/hcloud` provider requirement

### Task 4.2: Define variables.tf
- [ ] `hcloud_token` (string, sensitive) — Hetzner API token
- [ ] `location` (string, default `nbg1`) — datacenter
- [ ] `server_type` (string, default `cx22`) — VM size
- [ ] `ssh_public_key_path` (string) — path to deploy SSH public key file
- [ ] `allowed_ssh_cidrs` (list(string), default `["0.0.0.0/0"]`) — SSH allowlist
- [ ] `tailscale_auth_key` (string, sensitive) — pre-auth key for Tailscale
- [ ] `ghcr_username` (string, default `revelfire`) — GitHub username for GHCR
- [ ] `ghcr_token` (string, sensitive) — GitHub PAT for GHCR pull
- [ ] Create `terraform.tfvars.example` with placeholder values

### Task 4.3: Create main.tf resources
- [ ] `hcloud_ssh_key.deploy` — SSH public key resource
- [ ] `hcloud_firewall.scanner` — inbound TCP 22 from `var.allowed_ssh_cidrs`, inbound UDP 41641 from `0.0.0.0/0`, deny all other inbound
- [ ] `hcloud_volume.pgdata` — 20 GB, `delete_protection = true`, same location as server
- [ ] `hcloud_server.scanner` — CX22, Ubuntu 24.04, SSH key, firewall, cloud-init user_data
- [ ] `hcloud_volume_attachment.pgdata` — attach volume to server

### Task 4.4: Create cloud-init.yml
- [ ] System update and upgrade
- [ ] Install Docker CE + Compose plugin (official apt repository method)
- [ ] Install Tailscale (`curl -fsSL https://tailscale.com/install.sh | sh`)
- [ ] Run `tailscale up --auth-key=${tailscale_auth_key} --hostname=arb-scanner`
- [ ] Format and mount Hetzner Volume at `/mnt/pgdata` (check if already formatted)
- [ ] Add mount to `/etc/fstab`
- [ ] Create `/opt/arb-scanner/` directory
- [ ] Write `docker-compose.prod.yml` to `/opt/arb-scanner/`
- [ ] GHCR login: `echo ${ghcr_token} | docker login ghcr.io -u ${ghcr_username} --password-stdin`
- [ ] SSH hardening: disable `PasswordAuthentication`, disable `PermitRootLogin`, restart sshd
- [ ] Install `s3cmd` for backup uploads
- [ ] Write backup script and cron entry
- [ ] Create `.env` placeholder at `/opt/arb-scanner/.env` (operator fills in secrets)

### Task 4.5: Create outputs.tf
- [ ] Output: VM public IPv4 address
- [ ] Output: SSH command (`ssh -i <key> root@<ip>`)
- [ ] Output: Tailscale setup reminder
- [ ] Output: Post-provision instructions (copy .env, config.yaml, PEM, restart)

### Task 4.6: Test Terraform plan
- [ ] Run `terraform init` and `terraform validate`
- [ ] Run `terraform plan` with test variables (dry run)
- [ ] Document the expected resource creation list

## Phase 5: Docker Build & Push Workflow

### Task 5.1: Add build job to workflow
- [ ] Create `.github/workflows/build-and-deploy.yml`
- [ ] Trigger: push to `main`
- [ ] Job: `build` runs after quality gate (can reuse or depend on quality-gate.yml)
- [ ] Use `docker/setup-buildx-action`
- [ ] Use `docker/login-action` with `ghcr.io` and `GITHUB_TOKEN`
- [ ] Use `docker/build-push-action` with tags: `latest` + `${{ github.sha }}`
- [ ] Enable GHA cache: `cache-from: type=gha`, `cache-to: type=gha,mode=max`
- [ ] Set `permissions: packages: write`

### Task 5.2: Verify GHCR push
- [ ] Push a test commit to main
- [ ] Confirm image appears at `ghcr.io/revelfire/prediction-arbitrage`
- [ ] Confirm both `latest` and SHA tags are present
- [ ] Verify image can be pulled from another machine

## Phase 6: Deploy Workflow

### Task 6.1: Add deploy job to workflow
- [ ] Add `deploy` job to `build-and-deploy.yml`, `needs: [build]`
- [ ] Use `appleboy/ssh-action` or inline SSH with private key from `secrets.HETZNER_SSH_PRIVATE_KEY`
- [ ] SSH host from `secrets.HETZNER_HOST` (Tailscale IP or public IP)
- [ ] Commands: `cd /opt/arb-scanner && docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d`
- [ ] Health check: `sleep 10 && curl -sf http://localhost:8000/api/health` with 3 retries

### Task 6.2: Add GitHub Actions secrets
- [ ] Document required secrets: `HETZNER_SSH_PRIVATE_KEY`, `HETZNER_HOST`, `DEPLOY_SLACK_WEBHOOK_URL`
- [ ] Add to repository settings (manual step for operator)

## Phase 7: Backup Automation

### Task 7.1: Create backup script
- [ ] Create `infra/scripts/backup.sh`
- [ ] `pg_dump` via `docker compose exec -T db` to avoid installing pg_dump on host
- [ ] Gzip compress the dump
- [ ] Verify file is non-empty before upload
- [ ] Upload to Hetzner Object Storage via `s3cmd`
- [ ] Tag daily backups with timestamp
- [ ] Rotation: delete daily backups older than 7 days, keep Sunday backups for 4 weeks
- [ ] Log output for cron capture
- [ ] `chmod +x`

### Task 7.2: Create restore script
- [ ] Create `infra/scripts/restore.sh`
- [ ] Accept backup filename as argument
- [ ] Download from Object Storage
- [ ] Decompress and pipe into `psql` via docker compose exec
- [ ] Print success/failure message
- [ ] `chmod +x`

### Task 7.3: Configure backup cron in cloud-init
- [ ] Install `s3cmd` and configure with Hetzner Object Storage credentials
- [ ] Create Hetzner Object Storage bucket (document manual step)
- [ ] Add cron entry: `0 3 * * * /opt/arb-scanner/backup.sh >> /var/log/arb-backup.log 2>&1`
- [ ] Test backup/restore cycle manually

## Phase 8: Deploy Notifications

### Task 8.1: Add Slack/Discord notification to deploy workflow
- [ ] On deploy success: post commit SHA, message, status, link to GHA run
- [ ] On deploy failure: post failure details, link to GHA run
- [ ] Use `slackapi/slack-github-action` or plain `curl` to webhook
- [ ] Use `DEPLOY_SLACK_WEBHOOK_URL` secret (falls back to `ARBITRAGE_SLACK_WEBHOOK_URL` if not set)
- [ ] Include `if: always()` to fire on both success and failure

## Phase 9: End-to-End Validation

### Task 9.1: Full pipeline test
- [ ] Provision Hetzner VM via `terraform apply`
- [ ] SSH in, copy `.env`, `config.yaml`, Kalshi PEM
- [ ] Restart services, verify dashboard accessible via Tailscale
- [ ] Push a trivial commit to main
- [ ] Verify: quality gate → build → push → deploy → health check → notification
- [ ] Verify dashboard requires bearer token
- [ ] Verify `/api/health` works without token
- [ ] Verify port 8000 is NOT accessible from public IP

### Task 9.2: Backup validation
- [ ] Run backup script manually
- [ ] Verify backup file appears in Object Storage
- [ ] Destroy and recreate database container
- [ ] Restore from backup
- [ ] Verify data integrity (run a scan, check historical data)

### Task 9.3: Create operational runbook
- [ ] Document: initial provisioning steps (Terraform + manual secrets)
- [ ] Document: how to SSH into the VM
- [ ] Document: how to view logs (`docker compose logs -f`)
- [ ] Document: how to manually trigger a deploy
- [ ] Document: how to rollback to a previous image tag
- [ ] Document: how to run a manual backup/restore
- [ ] Document: how to update config.yaml on the VM
- [ ] Document: how to rotate the auth token
- [ ] Document: how to renew Tailscale auth key
- [ ] Document: Terraform import commands for state recovery
