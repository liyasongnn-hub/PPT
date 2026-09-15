#!/usr/bin/env bash
# TrainPPTAgent 2G2G 精简部署 + HTTPS 接线脚本（在服务器上运行）。
#
# 用法（root，代码已 clone 到 /opt/ppt-agent）：
#   cd /opt/ppt-agent
#   sudo APP_DOMAIN=hduyspptagent.xyz bash deploy/setup-https.sh
#
# 它做三件事：
#   1. 装 certbot（若缺）+ 安装 nginx HTTPS 站点并启用
#   2. 先以 80 端口 http 反代跑起来，签 Let's Encrypt 证书（HTTP-01）
#   3. 用 2g2g overlay 起精简栈（去掉 scheduler/backup，内存收紧）
#
# 前置：域名 A 记录已指向 8.210.196.154；.env 已填好 DeepSeek/千问密钥。
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "需要 root 权限，请用 sudo 运行" >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/ppt-agent}"
DOMAIN="${APP_DOMAIN:-hduyspptagent.xyz}"
cd "$APP_DIR"

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}==== [ $* ] ====${NC}"; }
fail() { echo -e "${RED}错误: $*${NC}" >&2; exit 1; }

[[ -f .env ]] || fail "缺少 .env，请先 sudo bash scripts/init-server.sh 并填入密钥"

# ---- 1. certbot + nginx ----
command -v certbot >/dev/null 2>&1 || apt-get install -y --no-install-recommends certbot python3-certbot-nginx nginx

log "安装 nginx HTTPS 站点配置"
install -m 644 deploy/nginx-https.conf /etc/nginx/sites-available/ppt-agent
ln -sf /etc/nginx/sites-available/ppt-agent /etc/nginx/sites-enabled/ppt-agent
# 移除默认站点，避免 80 冲突
rm -f /etc/nginx/sites-enabled/default
mkdir -p /var/www/html

# ---- 2. 先启动容器栈（80 端口代理），供 certbot HTTP-01 挑战----
log "先启动精简栈（http 阶段）"
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.2g2g.yml \
  up -d --build

nginx -t && systemctl reload nginx || true

# ---- 3. 签证书 ----
log "签发 Let's Encrypt 证书（HTTP-01）"
if [[ -d "/etc/letsencrypt/live/${DOMAIN}" ]]; then
  log "已存在证书，尝试续期"
  certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos \
    -m "admin@${DOMAIN}" --redirect || true
else
  certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos \
    -m "admin@${DOMAIN}" --redirect
fi

# 自动续期 cron（certbot 自带 systemd timer，这里兜底一条）
if [[ ! -f /etc/cron.d/certbot-renew ]]; then
  echo '0 3 * * * root certbot renew --quiet --deploy-hook "systemctl reload nginx"' > /etc/cron.d/certbot-renew
fi

nginx -t && systemctl reload nginx

log "完成。访问 https://${DOMAIN}"
log "状态：docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.2g2g.yml ps"
