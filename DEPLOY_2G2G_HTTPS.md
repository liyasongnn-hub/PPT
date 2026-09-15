# TrainPPTAgent 上线部署手册（阿里云香港轻量 2C2G + HTTPS）

面向「1–2 人测试、非商用」的最小可上线部署。全部命令在服务器的 root 终端执行。

> 关键安全约定：**DeepSeek / 千问的真实密钥绝不进入聊天、Git 或任何脚本**，
> 一律由你登录服务器后手工填入 `.env`。本仓库的所有脚本只生成随机内部密钥，
> 不含真实模型密钥。

---

## 0. 你的环境

| 项 | 值 |
|----|----|
| 域名 | `hduyspptagent.xyz` |
| 公网 IP | `8.210.196.154` |
| 系统 | Ubuntu 24.04 |
| 规格 | 2 vCPU / 2GB / 40GB ESSD |
| 节点 | 中国香港（免备案） |
| 目标 | `https://hduyspptagent.xyz` |

---

## 1. 域名解析（阿里云控制台操作，先做）

去 **阿里云 → 域名 → 解析（DNS）**，为 `hduyspptagent.xyz` 添加两条 A 记录：

| 主机记录 | 记录类型 | 记录值 |
|---------|---------|--------|
| `@`      | A        | `8.210.196.154` |
| `www`    | A        | `8.210.196.154` |

解析全球生效通常几分钟到几小时。可先验证：

```bash
ping hduyspptagent.xyz      # 应解析到 8.210.196.154
```

> 证书签发（HTTP-01 挑战）会回查这条记录，**解析未生效前不要跑第 4 步**。

---

## 2. 加 swap（防 2GB 内存 OOM，先做）

生成任务瞬间内存峰值会超过 2GB，必须先加 2GB swap，否则生成到一半会被 OOM 杀掉。

```bash
git clone https://github.com/liyasongnn-hub/PPT.git /opt/ppt-agent
cd /opt/ppt-agent
sudo bash scripts/setup-swap.sh 2048
```

`free -h` 应看到 `Swap: 2.0Gi`。

---

## 3. 装 Docker / Compose / certbot，生成 `.env`

```bash
cd /opt/ppt-agent
sudo bash scripts/init-server.sh
```

脚本会装 Docker、certbot、nginx，克隆代码，并生成 `.env`（随机内部密钥已填好）。

### 3.1 填入真实密钥（关键，只在这台服务器上做）

编辑 `.env`，把下面两行的占位值替换成你的**新密钥**：

```bash
nano /opt/ppt-agent/.env
```

```
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx   ← 你的 DeepSeek 新密钥
ALI_API_KEY=sk-xxxxxxxxxxxxx           ← 你的千问 DashScope 新密钥
```

并确认这一行已指向你的域名：

```
CORS_ORIGINS=https://hduyspptagent.xyz
```

> 若你手头没有新密钥（旧的可能已暴露），务必先在 DeepSeek / 阿里云控制台
> 撤销旧密钥、生成新密钥，再填这里。填写后保存退出（`Ctrl+O` 回车，`Ctrl+X`）。

其余随机密钥（`POSTGRES_PASSWORD` / `REDIS_PASSWORD` / `MINIO_*` / `JWT_SECRET`
/ `PERSONALDB_INTERNAL_TOKEN`）由 init-server 已生成，无需你改。

---

## 4. 一键 HTTPS 上线（确认解析已生效后执行）

```bash
cd /opt/ppt-agent
sudo APP_DOMAIN=hduyspptagent.xyz bash deploy/setup-https.sh
```

脚本会：
1. 安装 nginx HTTPS 站点（`deploy/nginx-https.conf`）；
2. 先用 http 起精简栈，跑 `certbot --nginx` 签 Let's Encrypt 证书（HTTP-01）；
3. 域名证书到位后自动切 443，并装自动续期。

> 首次证书签发若报「验证失败」，十有八九是**域名解析还没生效**，等十几分钟重跑即可。

---

## 5. 验证上线

```bash
# 容器状态（精简后应无 scheduler / backup）
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.2g2g.yml ps

# 健康检查（主 API 就绪，含 PG / 大纲 / 内容 / personaldb 全链路）
curl -fsS http://127.0.0.1:8008/api/health/ready
curl -fsS https://hduyspptagent.xyz/api/health/ready
```

浏览器访问 `https://hduyspptagent.xyz`，走一遍：注册 → 登录 → 建项目 → 生成大纲 →
生成内容 → 编辑器自动保存 → PPTX 导出 → 保存到云端。

---

## 6. 日常维护

```bash
cd /opt/ppt-agent

# 看日志
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.2g2g.yml logs -f --tail=200 main_api
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.2g2g.yml logs -f --tail=200 worker

# 更新代码并重建（fast-forward，不丢 .env 与数据卷）
git -C /opt/ppt-agent pull --ff-only origin main
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.2g2g.yml up -d --build

# 临时需要 backup / scheduler 时（测试期通常不用）
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.2g2g.yml --profile full up -d
```

---

## 7. 2GB 内存做了什么优化

| 优化 | 作用 |
|------|------|
| `docker-compose.2g2g.yml` | 停 `scheduler`/`backup`；`content_api`→1G、`personaldb`→768m、`main_api`/`outline_api`→512m、`worker`→512m/1 并发 |
| `scripts/setup-swap.sh` | 2GB swap，兜底瞬时内存峰值 |
| 证书/反代 | `deploy/nginx-https.conf` 读/写超时 1 小时，SSE 长推流不断线 |

若测试期仍偶发 OOM（日志出现 `Killed`），可进一步：减少 `WORKER_CONSUMERS` 之外，
调低 `content_api` 的并发页数；或换 2C4G（见 README_PRODUCTION.md §8 扩展说明）。

---

## 8. 故障排查索引

| 现象 | 排查 |
|------|------|
| 证书签发失败 | 域名解析未生效；`ping` 验证，等生效重跑第 4 步 |
| 生成到一半 `Killed` | 内存 OOM，确认 swap 已加（第 2 步 `free -h`） |
| `health/ready` 非 200 | 看 `logs worker` 与 `logs main_api`；多半是密钥/依赖服务未就绪 |
| 页面能开但接口失败 | 检查 `CORS_ORIGINS` 是否等于最终 https 域名 |
| 上传/导出失败 | MinIO 桶是否建成（`logs minio-init`）；`MINIO_*` 密钥是否填对 |
