#!/usr/bin/env bash
# TrainPPTAgent 生产部署脚本。
#
# 用法：
#   ./deploy.sh deploy           拉取代码（fast-forward）、校验 .env、构建并启动
#   ./deploy.sh update           拉取代码（fast-forward）、校验 .env、重建并更新
#   ./deploy.sh down             停止并移除所有服务
#   ./deploy.sh logs [svc]       查看日志（可选指定服务名）
#   ./deploy.sh backup           立即执行一次备份
#   ./deploy.sh restore <stamp>  从某次备份恢复（stamp 为 /backups 下的目录名）
set -euo pipefail

# 脚本目录即仓库根目录（去掉硬编码绝对路径）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env"
BRANCH_NAME="main"

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}==== [ $* ] ====${NC}"; }
fail() { echo -e "${RED}错误: $*${NC}" >&2; exit 1; }

# 生产必需的环境变量（值不能为空、不能是占位符）
REQUIRED_VARS=(
  POSTGRES_PASSWORD REDIS_PASSWORD MINIO_ACCESS_KEY MINIO_SECRET_KEY
  DEEPSEEK_API_KEY ALI_API_KEY JWT_SECRET PERSONALDB_INTERNAL_TOKEN CORS_ORIGINS
)

check_env() {
  [[ -f "$ENV_FILE" ]] || fail "缺少 $ENV_FILE，请先复制 .env.example 并填写真实密钥"
  local line key val
  while IFS='=' read -r key val; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    # 去掉行尾注释与首尾空白
    val="${val%%#*}"; val="$(printf '%s' "$val" | xargs)"
    key="$(printf '%s' "$key" | xargs)"
    for req in "${REQUIRED_VARS[@]}"; do
      if [[ "$key" == "$req" ]]; then
        if [[ -z "$val" || "$val" == replace-* ]]; then
          fail "$ENV_FILE 中的 $req 尚未填写（不能是空或占位符）"
        fi
      fi
    done
  done < "$ENV_FILE"
}

update_code() {
  log "拉取最新代码（$BRANCH_NAME，fast-forward only）"
  git fetch origin
  git checkout "$BRANCH_NAME"
  # 只用 fast-forward 合并，绝不 reset --hard，保护服务器上的未提交改动
  git pull --ff-only origin "$BRANCH_NAME"
}

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

deploy() {
  update_code
  check_env
  log "构建并启动服务"
  compose up -d --build
  log "部署完成，可用 'deploy.sh logs' 查看日志"
}

update() {
  update_code
  check_env
  log "重建并更新服务"
  compose up -d --build
  log "更新完成"
}

down() {
  log "停止并移除所有服务"
  compose down
}

logs() {
  if [[ $# -gt 0 ]]; then
    compose logs -f --tail=200 "$1"
  else
    compose logs -f --tail=200
  fi
}

backup() {
  check_env
  log "立即执行一次备份"
  compose run --rm backup /scripts/backup.sh
}

restore() {
  [[ $# -ge 1 ]] || fail "用法：deploy.sh restore <stamp>"
  check_env
  log "从备份 $1 恢复"
  compose run --rm backup /scripts/restore.sh "$1"
}

case "${1:-}" in
  deploy)  deploy ;;
  update)  update ;;
  down)    down ;;
  logs)    logs "${2:-}" ;;
  backup)  backup ;;
  restore) restore "${2:-}" ;;
  *)
    echo "用法：$0 {deploy|update|down|logs [svc]|backup|restore <stamp>}" >&2
    exit 2
    ;;
esac
