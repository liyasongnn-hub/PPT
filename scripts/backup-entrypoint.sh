#!/usr/bin/env sh
# backup 容器入口：启动即做一次备份，之后按 BACKUP_INTERVAL 周期性备份。
# BACKUP_INTERVAL 默认 86400 秒（每天一次）。使用固定间隔而非 cron，
# 以便在 read_only / no-new-privileges 的非 root 加固下仍能可靠运行。
set -eu

INTERVAL="${BACKUP_INTERVAL:-86400}"
log() { echo "[backup-entry $(date -u +%FT%TZ)] $*"; }

log "starting with interval=${INTERVAL}s"
while true; do
  /scripts/backup.sh || log "backup run failed (will retry on next interval)"
  log "sleeping ${INTERVAL}s"
  sleep "$INTERVAL"
done
