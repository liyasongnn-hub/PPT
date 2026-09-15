#!/usr/bin/env sh
# 从一次备份恢复：PostgreSQL + Chroma/MinIO 数据卷。
# 用法：restore.sh <时间戳目录>  （目录位于 /backups 下）
# 例：docker compose -f docker-compose.prod.yml run --rm backup /scripts/restore.sh 20260915_033000
set -eu

BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
if [ $# -lt 1 ]; then
  echo "usage: restore.sh <backup_stamp_dir>" >&2
  exit 2
fi
SRC="$BACKUP_ROOT/$1"
if [ ! -d "$SRC" ]; then
  echo "backup directory not found: $SRC" >&2
  exit 1
fi

log() { echo "[restore $(date -u +%FT%TZ)] $*"; }

if [ -z "${POSTGRES_PASSWORD:-}" ]; then
  echo "POSTGRES_PASSWORD is required" >&2
  exit 1
fi
export PGHOST="${PGHOST:-postgres}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${POSTGRES_USER:-ppt}"
export PGDATABASE="${POSTGRES_DB:-ppt_platform}"
export PGPASSWORD="${POSTGRES_PASSWORD}"

# 1) PostgreSQL（--clean --if-exists 会先 DROP 已有对象再重建）
if [ -f "$SRC/postgres.sql.gz" ]; then
  log "restoring PostgreSQL..."
  gunzip -c "$SRC/postgres.sql.gz" | psql --set ON_ERROR_STOP=1
fi

# 2) Chroma / MinIO 数据卷（覆盖式恢复；目标卷为空时最安全）
if [ -f "$SRC/chroma.tar.gz" ]; then
  log "restoring chroma volume..."
  tar -xzf "$SRC/chroma.tar.gz" -C /chroma_data
fi
if [ -f "$SRC/minio.tar.gz" ]; then
  log "restoring minio volume..."
  tar -xzf "$SRC/minio.tar.gz" -C /minio_data
fi

log "restore complete from $SRC"
