#!/usr/bin/env bash
# Back up the AssureFlow database and private evidence directory (spec 31).
# Usage: tools/backup.sh [backup_dir]   (reads DB_* and EVIDENCE_ROOT from the environment/.env)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="${1:-backups}/$STAMP"
mkdir -p "$DEST"
export PGPASSWORD="${DB_PASSWORD:-}"
pg_dump --format=custom --no-owner -h "${DB_HOST:-127.0.0.1}" -p "${DB_PORT:-5432}" -U "${DB_USER:-assureflow}" \
  -f "$DEST/assureflow.dump" "${DB_NAME:-assureflow}"
EVID="${EVIDENCE_ROOT:-./evidence}"
tar -C "$(dirname "$EVID")" -czf "$DEST/evidence.tar.gz" "$(basename "$EVID")"
( cd "$DEST" && sha256sum assureflow.dump evidence.tar.gz > SHA256SUMS )
echo "Backup written to $DEST"
