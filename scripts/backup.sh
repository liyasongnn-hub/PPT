#!/usr/bin/env sh
# 单次备份：PostgreSQL 逻辑备份 + Chroma/MinIO 数据卷打包。
# 由 backup 容器（backup-entrypoint.sh）周期性调用，也可手动执行：
#   docker compose -f docker-compose.prod.yml run --rm backup /scripts/backup.sh
set -eu

BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
RETENTION="${BACKUP_RETENTION_DAYS:-7}"

STAMP="$(date -u +%Y%m%d_%H%M%S)"
DEST="$BACKUP_ROOT/$STAMP"
mkdir -p "$DEST"

log() { echo "[backup $(date -u +%FT%TZ)] $*"; }

# 1) PostgreSQL 逻辑备份（连接参数来自 libpq 环境变量，见容器 environment）
if [ -z "${POSTGRES_PASSWORD:-}" ]; then
  echo "POSTGRES_PASSWORD is required" >&2
  exit 1
fi
export PGHOST="${PGHOST:-postgres}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${POSTGRES_USER:-ppt}"
export PGDATABASE="${POSTGRES_DB:-ppt_platform}"
export PGPASSWORD="${POSTGRES_PASSWORD}"

log "dumping PostgreSQL..."
pg_dump --no-owner --clean --if-exists | gzip > "$DEST/postgres.sql.gz"

# 2) Chroma 数据卷（短期单实例，直接打包；写盘窗口可能产生短暂不一致，夜间备份可接受）
if [ -d /chroma_data ] && [ -n "$(ls -A /chroma_data 2>/dev/null)" ]; then
  log "tar chroma volume..."
  tar -czf "$DEST/chroma.tar.gz" -C /chroma_data .
fi

# 3) MinIO 数据卷（同上；更稳妥的在线一致备份是 mc mirror，见 README_PRODUCTION）
if [ -d /minio_data ] && [ -n "$(ls -A /minio_data 2>/dev/null)" ]; then
  log "tar minio volume..."
  tar -czf "$DEST/minio.tar.gz" -C /minio_data .
fi

log "wrote backup to $DEST"

# 4) 仅保留最近 RETENTION 份
if [ "$RETENTION" -gt 0 ] 2>/dev/null; then
  ls -1d "$BACKUP_ROOT"/*/ 2>/dev/null | sort -r | tail -n +$((RETENTION + 1)) | while read -r old; do
    log "pruning old backup $old"
    rm -rf "$old"
  done
fi

log "done"
