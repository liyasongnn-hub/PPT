# TrainPPTAgent 生产部署指南

本文档描述生产环境的容器化部署方式。生产栈为 **Nginx（唯一公网入口）+ PostgreSQL + Redis Stream + MinIO**，主 API、大纲 Agent、内容 Agent 和个人知识库仅在 Docker 内网通信。

## 1. 架构

```
frontend ── nginx(80) ──> main_api(6800)
                            │  httpx/A2A ──> outline_api(10001) / content_api(10011)
                            │  X-Internal-Token ──> personaldb(9100) ── Chroma(volume)
                            │  SQLAlchemy async ──> PostgreSQL
                            │  redis.asyncio ──> Redis Stream(jobs) ──> worker(消费)
                            └─ minio(私有桶 uploads/exports，签名 URL 下载)
migration(一次性 alembic upgrade) / scheduler(心跳故障转移+清理) / backup(pg_dump+卷 tar)
```

- 生成任务：`main_api` 写 `jobs` 行并 `XADD` 到 Redis Stream；`worker` 消费、调用 A2A 生成并直写 PostgreSQL；`main_api` 的 SSE 端点只读数据库事件，多实例共享。
- 数据层：SQLAlchemy 2.0。生产配置 `DATABASE_URL` 走 PostgreSQL（Alembic 迁移）；本地开发未配置时自动回退 SQLite。
- 导出：PPTX 由浏览器端 pptxgenjs 生成，点「保存到云端」后上传 MinIO（`exports` 桶）并记录到 `files` 表；下载走预签名 URL。

## 2. 前置条件

- Docker Engine 24+ 与 Docker Compose v2（`docker compose version` 可检查）
- 至少 4 vCPU、8 GB 内存和 20 GB 可用磁盘；生成较大演示文稿建议 8 vCPU / 16 GB
- 一个可访问的 HTTPS 域名（反向代理或云负载均衡负责证书）
- DeepSeek 文本生成密钥，以及 DashScope/通义千问 `text-embedding-v3` 向量模型密钥

## 3. 配置密钥（不入 Git）

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"  # 生成 JWT_SECRET / PERSONALDB_INTERNAL_TOKEN
```

编辑 `.env`，至少填写以下变量（完整清单见 `.env.example`）：

```dotenv
# 数据层（连接串由 compose 自动派生，务必 URL 安全、不含 @ : / # %）
POSTGRES_PASSWORD=...          # PostgreSQL 密码
REDIS_PASSWORD=...             # Redis 密码
MINIO_ACCESS_KEY=...           # MinIO root 用户
MINIO_SECRET_KEY=...           # MinIO root 密码

# 必需密钥
DEEPSEEK_API_KEY=替换为新的密钥
ALI_API_KEY=替换为新的千问向量模型密钥
JWT_SECRET=随机值（至少 32 字符）
PERSONALDB_INTERNAL_TOKEN=随机值（至少 32 字符）
CORS_ORIGINS=https://ppt.example.com
```

不要把真实密钥写入 `.env.example`、源码、Issue 或提交记录。曾经在聊天或日志中暴露过的密钥应先在供应商控制台撤销并重新生成。

## 4. 启动与更新

可用 `deploy.sh` 封装命令：

```bash
./deploy.sh deploy            # 拉取代码（fast-forward）、校验 .env、构建并启动
./deploy.sh update            # 拉取代码并重建更新
./deploy.sh logs main_api     # 查看日志（可省略服务名看全部）
./deploy.sh down              # 停止并移除服务
./deploy.sh backup            # 立即执行一次备份
./deploy.sh restore <stamp>   # 从某次备份恢复
```

或直接使用 docker compose：

```bash
docker compose --env-file .env -f docker-compose.prod.yml up -d --build
docker compose --env-file .env -f docker-compose.prod.yml ps
```

首次启动会依次执行 `minio-init`（建桶）与 `migration`（`alembic upgrade head`），随后 `main_api`/`worker`/`scheduler` 才就绪。

生产 Compose 只发布 `${APP_PORT}`（默认 `8008`）到宿主机；不要额外映射 6800、10001、10011、9100、9000 或 6379。访问地址为 `http://服务器:8008`，建议在前面接 HTTPS 反向代理并将 `CORS_ORIGINS` 设置为最终浏览器 origin。

## 5. 服务清单

| 服务 | 镜像 | 职责 |
|------|------|------|
| `postgres` | `postgres:16-alpine` | 业务数据（users/projects/jobs/job_events/files） |
| `redis` | `redis:7-alpine` | 任务队列（Stream + consumer group），`appendonly yes` |
| `minio` | `minio/minio` | 对象存储（私有桶 `uploads`/`exports`），控制台仅内网 |
| `minio-init` | `minio/mc` | 一次性建桶 |
| `migration` | main_api 镜像 | 一次性 `alembic upgrade head` |
| `main_api` | main_api 镜像 | 对外 API + SSE 事件流 |
| `worker` | main_api 镜像 | 消费 Redis Stream 生成任务（`WORKER_CONSUMERS` 并发度） |
| `scheduler` | main_api 镜像 | 心跳故障转移 + 排队超时 + 事件保留期清理 |
| `backup` | `postgres:16-alpine` | 每日备份（`pg_dump` + tar 卷），保留 7 份 |
| `personaldb` / `outline_api` / `content_api` | 各自镜像 | 知识库 / 大纲 / 内容生成 |
| `frontend` | frontend 镜像 | Nginx 静态站点 + 反向代理（唯一公网入口） |

## 6. 健康检查与日志

```bash
curl http://127.0.0.1:8008/api/health/live
curl http://127.0.0.1:8008/api/health/ready
```

- `/api/health/live`：主 API 进程存活。
- `/api/health/ready`：检查数据库（PostgreSQL）、大纲 Agent、内容 Agent、personaldb。

```bash
docker compose -f docker-compose.prod.yml logs -f --tail=200 main_api
docker compose -f docker-compose.prod.yml logs -f --tail=200 worker
```

## 7. 数据与备份 / 恢复

数据分四处：PostgreSQL（业务）、Redis（队列，可丢）、MinIO（上传源文件与导出存档）、Chroma（知识库向量，短期单实例）。

`backup` 服务每天执行 `scripts/backup.sh`：`pg_dump` PostgreSQL，`tar` 打包 Chroma 与 MinIO 数据卷，只保留最近 `BACKUP_RETENTION_DAYS`（默认 7）份，归档在 `backup_data` 卷。手动触发：

```bash
./deploy.sh backup
```

恢复（先 `down` 停服，再恢复数据库，最后重启）：

```bash
./deploy.sh restore 20260915_033000
docker compose --env-file .env -f docker-compose.prod.yml up -d
```

> 说明：Chroma / MinIO 采用「夜间直接打包数据卷」的方式，写盘窗口内可能存在短暂不一致；需要在线严格一致备份 MinIO 时，改用 `mc mirror`。删除容器不会删除 named volume，只有 `docker compose down -v` 才删除数据卷，生产环境谨慎使用。

## 8. 扩展与可靠性

- **横向扩展**：`main_api` 与 `worker` 均为无状态，可水平扩容。`main_api` 只写队列、读库；`worker` 消费 Stream（consumer group + `XAUTOCLAIM` 回收未 ack 消息），崩溃后 pending 消息 60s 内被重新投递。
- **重试与死信**：投递超过 `JOB_MAX_DELIVERY_COUNT`（默认 3）次进入死信流 `jobs_dead_letter`，任务置为 `FAILED`。
- **故障转移**：`scheduler` 周期把心跳丢失的 `RUNNING` 任务置为 `FAILED`、超时未消费的 `QUEUED` 任务置为 `FAILED`，并清空超过保留期的终态任务事件。
- **限流**：当前限流为进程内滑窗，适合单实例；水平扩容后需替换为 Redis 共享限流（见 `main.py` 中 `SlidingWindowLimiter` 注释）。

## 9. 设计取舍与后续路径

- **Chroma 短期保留**：向量库暂为单实例 Chroma（`personaldb` 服务）。多实例/更大规模时迁移到 pgvector（PostgreSQL 已就绪，扩展名 `vector`），迁移路径：导出 Chroma 集合 → 生成 embedding → 写入 pgvector 表，业务侧只需替换 `personaldb` 的查询适配层。
- **Agent 运行记录**：`jobs` / `job_events` 表即 Agent Run 记录（覆盖大纲与内容两条路径）。重试/死信由队列层负责；「断点续作」= 重跑时复用已存储的 `outline` / `document` 上下文。ADK 的 `InMemory*` 组件不替换为持久化实现（成本高、收益低）。
- **导出在浏览器端完成**：后端不生成 PPTX/PDF/PNG，「导出任务状态」即上传存档记录（`files` 表 + MinIO）。

## 10. 本地开发

不使用 Docker 时，运行 `python start.py`（项目根目录）会以 SQLite + 进程内队列 + 本地文件存储启动全部服务，零外部依赖；`frontend` 内执行 `npm run dev`。开发配置使用本地 `.env`，生产验证始终以 `docker-compose.prod.yml` 为准。

## 11. 发布前检查

- `docker compose --env-file .env -f docker-compose.prod.yml config --quiet` 成功，且无未替换的必填变量
- CI 全绿：secret-hygiene / 后端 compileall / 前端 type+build / compose config
- `git grep` 未发现 `.env`、API key、JWT secret 或内部 token
- 注册、登录、创建项目、生成大纲、生成内容、编辑器自动保存、PPTX 导出与「保存到云端」均通过验收
- 备份→恢复演练通过；重启/删除容器后数据可恢复
- 仅 Nginx 公网可达，数据库、Redis、MinIO、向量库与 Agent 端口未暴露
