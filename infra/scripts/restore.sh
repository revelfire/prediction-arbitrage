#!/usr/bin/env bash
# Restore PostgreSQL from a Hetzner Object Storage backup.
# Usage: restore.sh <backup_filename>
# Example: restore.sh arb_scanner_20260227_030000.sql.gz
set -euo pipefail

FILENAME="${1:?Usage: restore.sh <backup_filename>}"
BUCKET_PREFIX="${2:-daily}"

echo "Downloading s3://arb-scanner-backups/${BUCKET_PREFIX}/${FILENAME}..."
s3cmd get "s3://arb-scanner-backups/${BUCKET_PREFIX}/${FILENAME}" /tmp/restore.sql.gz

echo "Decompressing..."
gunzip -f /tmp/restore.sql.gz

echo "Restoring to database..."
docker compose -f /opt/arb-scanner/docker-compose.prod.yml exec -T db \
  psql -U arb_scanner arb_scanner < /tmp/restore.sql

rm -f /tmp/restore.sql

echo "Restore complete from ${FILENAME}"
