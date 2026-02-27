#!/usr/bin/env bash
# Backup PostgreSQL to Hetzner Object Storage (S3-compatible).
# Usage: backup.sh
# Requires: s3cmd configured with Hetzner Object Storage credentials.
set -euo pipefail

BACKUP_DIR="/tmp/arb-backup"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DAY_OF_WEEK=$(date +%u)
FILENAME="arb_scanner_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

# Dump and compress
docker compose -f /opt/arb-scanner/docker-compose.prod.yml exec -T db \
  pg_dump -U arb_scanner arb_scanner | gzip > "${BACKUP_DIR}/${FILENAME}"

# Verify non-empty
if [ ! -s "${BACKUP_DIR}/${FILENAME}" ]; then
  echo "ERROR: Empty backup file"
  exit 1
fi

# Upload to Hetzner Object Storage
s3cmd put "${BACKUP_DIR}/${FILENAME}" "s3://arb-scanner-backups/daily/${FILENAME}"

# Keep Sunday backups as weekly
if [ "$DAY_OF_WEEK" = "7" ]; then
  s3cmd put "${BACKUP_DIR}/${FILENAME}" "s3://arb-scanner-backups/weekly/${FILENAME}"
fi

# Clean up local temp
rm -f "${BACKUP_DIR}/${FILENAME}"

# Rotate remote: delete daily backups older than 7 days
CUTOFF=$(date -d '7 days ago' +%Y%m%d 2>/dev/null || date -v-7d +%Y%m%d 2>/dev/null || echo "")
if [ -n "$CUTOFF" ]; then
  s3cmd ls "s3://arb-scanner-backups/daily/" 2>/dev/null | while read -r line; do
    REMOTE_FILE=$(echo "$line" | awk '{print $NF}')
    REMOTE_DATE=$(echo "$REMOTE_FILE" | grep -oP '\d{8}' | head -1 || true)
    if [ -n "$REMOTE_DATE" ] && [ "$REMOTE_DATE" -lt "$CUTOFF" ] 2>/dev/null; then
      s3cmd del "$REMOTE_FILE" || true
    fi
  done
fi

# Rotate remote: delete weekly backups older than 28 days
WEEKLY_CUTOFF=$(date -d '28 days ago' +%Y%m%d 2>/dev/null || date -v-28d +%Y%m%d 2>/dev/null || echo "")
if [ -n "$WEEKLY_CUTOFF" ]; then
  s3cmd ls "s3://arb-scanner-backups/weekly/" 2>/dev/null | while read -r line; do
    REMOTE_FILE=$(echo "$line" | awk '{print $NF}')
    REMOTE_DATE=$(echo "$REMOTE_FILE" | grep -oP '\d{8}' | head -1 || true)
    if [ -n "$REMOTE_DATE" ] && [ "$REMOTE_DATE" -lt "$WEEKLY_CUTOFF" ] 2>/dev/null; then
      s3cmd del "$REMOTE_FILE" || true
    fi
  done
fi

echo "$(date -Iseconds) Backup complete: ${FILENAME}"
