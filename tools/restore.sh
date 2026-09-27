#!/usr/bin/env bash
# Restore a backup into a FRESH database and evidence directory, then verify (spec 31).
# Usage: tools/restore.sh <backup_dir> <target_db_name> <target_evidence_dir>
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
SRC="$1"; TARGET_DB="$2"; TARGET_EVID="$3"
( cd "$SRC" && sha256sum -c SHA256SUMS )
export PGPASSWORD="${DB_PASSWORD:-}"
PGARGS=(-h "${DB_HOST:-127.0.0.1}" -p "${DB_PORT:-5432}" -U "${DB_USER:-assureflow}")
createdb "${PGARGS[@]}" "$TARGET_DB"
pg_restore "${PGARGS[@]}" --no-owner -d "$TARGET_DB" "$SRC/assureflow.dump"
mkdir -p "$TARGET_EVID"
tar -C "$TARGET_EVID" --strip-components=1 -xzf "$SRC/evidence.tar.gz"
DB_NAME="$TARGET_DB" EVIDENCE_ROOT="$TARGET_EVID" python manage.py verify_evidence
DB_NAME="$TARGET_DB" EVIDENCE_ROOT="$TARGET_EVID" python manage.py verify_snapshots
echo "Restore into $TARGET_DB verified. Now spot-check key workflow pages against the restored instance."
