#!/usr/bin/env bash
# Daily PostgreSQL backup with local rotation.
# Installed by bootstrap.sh to run via cron at 03:00 UTC.
set -euo pipefail

BACKUP_DIR="/opt/arb-scanner/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DAY_OF_WEEK=$(date +%u)
FILENAME="arb_scanner_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"

# Dump and compress
docker compose -f /opt/arb-scanner/docker-compose.prod.yml exec -T db \
  pg_dump -U arb_scanner arb_scanner | gzip > "${BACKUP_DIR}/daily/${FILENAME}"

# Verify non-empty
if [ ! -s "${BACKUP_DIR}/daily/${FILENAME}" ]; then
  echo "ERROR: Empty backup file"
  exit 1
fi

# Keep Sunday backups as weekly
if [ "$DAY_OF_WEEK" = "7" ]; then
  cp "${BACKUP_DIR}/daily/${FILENAME}" "${BACKUP_DIR}/weekly/${FILENAME}"
fi

# Rotate: delete daily backups older than 7 days
find "$BACKUP_DIR/daily" -name "*.sql.gz" -mtime +7 -delete

# Rotate: delete weekly backups older than 28 days
find "$BACKUP_DIR/weekly" -name "*.sql.gz" -mtime +28 -delete

echo "$(date -Iseconds) Backup complete: ${FILENAME}"
