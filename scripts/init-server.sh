#!/usr/bin/env bash
# TrainPPTAgent 服务器一次性初始化：装 Docker / Compose / certbot / nginx，
# 克隆代码，生成含随机密钥的 .env 模板（真实 DEEPSEEK/ALI 密钥留待你自己填）。
#
# 用法（root，Ubuntu 24.04，已在阿里云香港轻量 2G2G 上验证）：
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/liyasongnn-hub/PPT/main/scripts/init-server.sh)"
#
# 或先手动克隆后运行：
#   git clone https://github.com/liyasongnn-hub/PPT.git /opt/ppt-agent
#   cd /opt/ppt-agent && sudo bash scripts/init-server.sh
#
# 该脚本「不」包含任何真实密钥：DeepSeek / DashScope 密钥一律由你在下一条
# 步骤（生成 .env 后）手工填入，绝不进入本脚本或聊天上下文。

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "需要 root 权限，请用 sudo 运行" >&2
  exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/liyasongnn-hub/PPT.git}"
APP_DIR="${APP_DIR:-/opt/ppt-agent}"

GREEN='\033[0;32m'; NC='\033[0m'
log() { echo -e "${GREEN}==== [ $* ] ====${NC}"; }

log "更新软件包索引"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  curl ca-certificates git ufw \
  python3 python3-pip

# ---- Docker（官方脚本）----
if ! command -v docker >/dev/null 2>&1; then
  log "安装 Docker Engine"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
else
  log "Docker 已安装，跳过"
fi

# 允许非交互式运行 docker compose 插件（无 TTY）
mkdir -p /etc/docker
cat > /etc/docker/docker-compose.override.json <<'EOF'
{}
EOF

# ---- certbot + nginx（HTTP-01 证书用）----
log "安装 certbot"
apt-get install -y --no-install-recommends certbot python3-certbot-nginx

# ---- 拉取代码 ----
if [[ -d "${APP_DIR}/.git" ]]; then
  log "代码仓库已存在，fast-forward 更新"
  git -C "$APP_DIR" fetch origin
  git -C "$APP_DIR" checkout main
  git -C "$APP_DIR" pull --ff-only origin main || true
else
  log "克隆代码到 ${APP_DIR}"
  git clone "$REPO_URL" "$APP_DIR"
  git -C "$APP_DIR" checkout main
fi

# ---- 生成 .env（随机密钥 + 必填空位）----
log "生成 .env 模板"
cd "$APP_DIR"
if [[ ! -f .env ]]; then
  cp .env.example .env
  # 生成 URL 安全随机密码与密钥（不依赖 openssl 随机性参数）
  pg_pw="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
  redis_pw="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
  minio_pw="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
  jwt="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  internal="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"

  # 用 python 就地替换占位符，避免 sed 对特殊字符的转义问题
  python3 - "$pg_pw" "$redis_pw" "$minio_pw" "$jwt" "$internal" <<'PYEOF'
import sys
p=[sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5]]
with open('.env','r',encoding='utf-8') as f:
    t=f.read()
t=t.replace('replace-with-a-random-url-safe-password', p[0])
t=t.replace('replace-with-a-different-random-url-safe-password', p[1])
t=t.replace('replace-with-another-random-url-safe-password', p[2])
t=t.replace('replace-with-a-random-value-of-at-least-32-characters', p[3])
t=t.replace('replace-with-a-different-random-value-of-at-least-32-characters', p[4])
with open('.env','w',encoding='utf-8') as f:
    f.write(t)
print('已生成 .env（随机内部密钥）；DeepSeek/千问密钥请手工填入')
PYEOF

  # 预填域名到 CORS_ORIGINS
  if [[ -n "${APP_DOMAIN:-}" ]]; then
    sed -i "s|^CORS_ORIGINS=.*|CORS_ORIGINS=https://${APP_DOMAIN}|" .env
  fi
else
  log ".env 已存在，跳过生成（如需重建请先备份后删除 .env）"
fi

log "交换分区（若系统无 swap）——建议另跑 scripts/setup-swap.sh"
log "初始化完成。下一步："
echo ""
echo "  1) 编辑 ${APP_DIR}/.env，填入 DEEPSEEK_API_KEY 与 ALI_API_KEY（以及 CORS_ORIGINS 域名）"
echo "  2) sudo bash scripts/setup-swap.sh                  # 2GB swap 防 OOM"
echo "  3) 见 DEPLOY_2G2G_HTTPS.md 继续 HTTPS + 启动"
