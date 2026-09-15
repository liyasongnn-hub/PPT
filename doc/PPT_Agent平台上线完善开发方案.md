# PPT 创作 Agent 平台上线完善开发方案

> 适用项目：`E:\PPT2\TrainPPTAgent-main`（TrainPPTAgent）  
> 参考项目：`E:\面试Agent平台\offerpilot-ai-interview-main`（OfferPilot 智能 AI 面试官平台）  
> 目标：在保留现有 PPT 生成与 PPTist 编辑能力的基础上，补齐产品化、工程化和运维能力，使平台可安全、稳定地部署上线。

## 1. 结论摘要

PPT2 已具备可演示的“主题/文件 → 大纲 → 逐页内容 → 模板渲染 → PPTist 编辑 → PPTX 导出”主链路，技术上采用 Vue3/Vite/TypeScript、FastAPI、Google ADK/A2A、MCP 搜索和 Chroma 向量库。当前更像单机 Demo/研发底座：主 API、大纲 Agent、内容 Agent、personaldb 是相互调用的独立进程，任务状态和会话主要在内存，用户数据没有账号和数据库隔离，生成结果缺少服务端项目存档、可恢复任务、质量评分和运营观测。

建议采用“**模块化单体主 API + 可独立扩缩的 Agent Worker**”作为第一版上线架构，逐步吸收 OfferPilot 的成熟做法：JWT 多用户、PostgreSQL 持久化、Redis Stream 异步任务、对象存储、Agent 决策/规划/执行/质检/总结、配置检查、质量基线、Docker Compose/Nginx 和 E2E 验收。首个生产版本优先保证数据不丢、任务可恢复、生成可追踪、导出可交付，再扩展协作与商业化。

## 2. 参考项目可复用的能力

OfferPilot 当前可复用的架构经验如下：

| 能力 | 参考实现 | 对 PPT 平台的借鉴 |
| --- | --- | --- |
| 统一入口与模块化 | FastAPI `app/main.py`，按 auth/resume/interview/knowledge/agent 模块注册路由 | 将 PPT 主 API 作为唯一 BFF，内部调用 Agent、知识库、导出服务 |
| 身份与隔离 | JWT 中间件、业务表 `user_id` 过滤、组织空间 | 增加用户、团队、项目、资源权限，避免通过 URL/文件 ID 越权 |
| 异步任务 | Redis Stream + 消费者组，状态可查询，失败重试 | 大纲、逐页生成、图片处理、导出、索引统一进入任务队列 |
| Agent 编排 | 决策 → 规划 → 执行 → 质检 → 总结，任务图可持久化 | 根据页数、数据源、模板复杂度选择快速/标准/复杂路径，支持断点续作 |
| 知识库 | 文档解析、向量检索、Rerank、GraphRAG | 将用户资料、品牌规范、历史 PPT 建成可引用知识库 |
| 文件与交付 | 文件校验、对象存储、PDF 导出 | PPTX/PDF/图片/源 JSON 统一存储并生成短期下载地址 |
| 生产可信度 | 配置启动检查、健康检查、Docker Compose、Nginx、测试脚本 | 增加依赖探活、指标、日志、备份、灰度与回滚流程 |

## 3. PPT2 现状盘点

### 3.1 已有功能

- **大纲生成**：`backend/simpleOutline` 使用 ADK/A2A 和联网搜索，`/tools/aippt_outline` 支持流式输出；支持从文件生成大纲。
- **逐页内容生成**：`backend/slide_agent` 使用 `PPTWriterSubAgent → CheckerAgent → ControllerAgent` 循环，按页生成 JSON，规则校验失败最多重试 3 次。
- **知识库**：`backend/personaldb` 可上传文件或 URL，MarkItDown/MinerU 转 Markdown，分块后调用 Embedding 写入 Chroma；提供 `/upload/`、`/vectorize/text`、`/files/{user_id}`。
- **模板与编辑器**：前端基于 PPTist，支持模板列表、封面/目录/过渡/内容/引用/结束页、文本/图片/图表/表格/音视频、画布编辑、演示模式和 PPTX/JSON/图片导出。
- **媒体能力**：内容 Agent 集成知识库搜索和图片搜索；前端已处理图片槽位（`pageFigure`、`itemFigure`、`background`）和图片自适应。
- **部署方式**：已有 `docker-compose.yml`、多 Dockerfile、`start.py`/`deploy.sh`、`.env` 模板、healthz；支持主 API 6800、大纲 10001、内容 10011、personaldb 9100、前端 8008/5173。

### 3.2 上线前的主要差距

1. **无正式身份体系**：主 API CORS 为 `*`，接口不校验登录；`user_id/sessionId` 来自请求字段，存在越权和冒用风险。
2. **无业务数据库**：项目、版本、任务、模板、素材、导出记录没有服务端持久化；ADK 使用 `InMemorySessionService`、`InMemoryTaskStore`、`InMemoryArtifactService`，重启即丢状态。
3. **任务不可恢复**：生成过程是长连接流式调用，缺少统一 Job ID、进度、取消、超时、断点续作和死信处理；单页失败时存在“跳过页面”的降级，可能产出不完整 PPT。
4. **向量库不适合多实例**：Chroma 持久化到本地 `cache/chromadb`，没有备份、租户级访问控制、索引任务状态和容量治理；上传/向量化为同步处理。
5. **模板管理硬编码**：`/templates` 返回固定 4 个模板，尚未形成模板上传、标注、版本、审核、租户可见范围和兼容性校验。
6. **生成质量缺少业务质检**：当前 Checker 主要验证 JSON/字段，尚无版式溢出、文字密度、引用完整性、图片可用性、品牌色、敏感内容和事实一致性检查。
7. **交付链路不闭环**：前端可下载 PPTX/JSON，但服务端无项目历史、分享链接、导出队列、导出结果保留和审计记录；`aippt_by_id` 中存在空的论文内容占位逻辑，应在上线前移除或重写。
8. **安全基线不足**：`/proxy?url=` 有 SSRF 风险；`/data/{filename}` 需防路径穿越；上传缺少大小/扩展名/MIME/病毒扫描和配额；日志可能记录提示词、URL 和密钥相关信息。
9. **可观测性不足**：缺少结构化日志、Trace ID、LLM token/成本、队列积压、P95 延迟、失败率和告警；生产脚本偏进程管理，缺少容器级探活和滚动升级。
10. **测试与契约不足**：已有少量后端测试，但没有覆盖认证、数据隔离、任务恢复、SSE 重连、模板兼容、导出回归和端到端验收的自动化门禁。

## 4. 目标产品功能（按上线优先级）

### P0：首个生产版本必须具备

**账号与工作台**

- 邮箱/手机号注册登录、JWT + Refresh Token、退出登录、密码重置；可选企业 SSO。
- 用户工作台：最近项目、生成中任务、模板/素材/知识库入口、用量统计。
- 用户、团队、角色（Owner/Editor/Viewer）和资源级权限；所有业务表带 `tenant_id/user_id`。

**项目与版本**

- 创建项目时指定主题、语言、受众、页数、风格、比例、模板、数据来源和品牌色。
- 项目状态：`DRAFT → OUTLINE_REVIEW → GENERATING → EDITING → EXPORTED/ARCHIVED`。
- 大纲和幻灯片 JSON 服务端保存，自动保存、手动保存、版本快照、版本比较、回滚。
- 支持从历史版本继续生成某一页；记录每页来源、Agent、模型和素材引用。

**生成工作流**

- 快速路径（小于 8 页、无知识库）：单次规划 + 生成 + 轻量质检。
- 标准路径：决策 → 大纲规划 → 内容/图片/图表并行生成 → 版式质检 → 总结。
- 复杂路径：持久化 DAG、分批并行、失败重试、人工确认节点、断点续作。
- Job API：创建、查询进度、SSE/WebSocket 订阅、取消、重试、失败详情、幂等键。

**编辑与模板**

- 保留 PPTist 编辑能力；补充 AI 操作：改写/缩写/扩写、换图、换版式、统一风格、翻译、演讲稿生成。
- 模板上传（PPTX/PPTist JSON）→ 解析 → 槽位标注 → 预览 → 审核 → 发布；模板包含画布尺寸、主题色、字体、可用页面类型和占位符 schema。
- 模板版本、租户私有模板、系统模板、兼容性检查和使用统计。

**知识库与素材**

- 上传 PDF/DOCX/PPTX/TXT/Markdown/图片，异步解析/OCR/分块/Embedding；展示索引状态和失败原因。
- 知识库按项目/团队授权；检索结果保留文档、页码、片段和置信度，生成页脚引用或备注。
- 图片素材库：来源、版权/许可证、尺寸、主题、标签、去重、失效检测；图片下载使用白名单和安全代理。

**质量与交付**

- 自动检查：JSON schema、页数、必填字段、文本溢出、最小字号、对比度、图片可达性、图表数据完整性、引用覆盖率、敏感内容。
- 质量报告按页给出分数、问题、修复建议和“一键修复”；低于阈值禁止标记为可交付（可人工强制发布并记录原因）。
- 服务端异步导出 PPTX、PDF、PNG/JPG、源 JSON、演讲者备注；导出文件进入对象存储，下载链接限时签名。
- 分享链接（只读/可评论/需密码/过期时间）、水印和导出审计。

### P1：上线后 1~2 个迭代

- 多人实时协作或项目锁；评论、批注、@成员、变更记录。
- 品牌中心：Logo、字体、色板、页眉页脚、公司禁用词和模板策略。
- 图表增强：从 CSV/Excel/数据库生成图表，自动选择图表类型并显示数据来源。
- 语音/演讲辅助：逐页演讲稿、计时、语速提示、演练记录和反馈。
- 用量/套餐：按页数、模型、图片、导出次数计量；额度预警、订单和发票接口。
- 管理后台：用户/团队、模板审核、任务队列、失败重试、成本与活跃度报表。

### P2：规模化与差异化

- 行业 Agent（汇报、投标、课程、路演、学术、营销）和可配置 Skill 市场。
- 历史 PPT 风格学习、企业知识图谱、跨项目素材复用、个性化推荐。
- 多模型路由与本地模型降级；批量生成、API/SDK、Webhook 和第三方办公套件集成。

## 5. 目标技术架构

```text
浏览器/PPTist
      │ HTTPS + JWT
      ▼
API Gateway / Nginx ── 限流、WAF、静态资源、SSE 转发
      ▼
Main API（模块化 FastAPI）
  ├─ Auth/Tenant/Project/Version
  ├─ Generation Job & Orchestrator
  ├─ Template/Asset/Knowledge Base
  ├─ Quality/Export/Share
  └─ Admin/Billing/Usage
      │
      ├── PostgreSQL (+ pgvector，项目/任务/模板/引用/权限)
      ├── Redis（Stream 队列、缓存、SSE 事件、限流）
      ├── S3/MinIO（PPTX、图片、源文件、导出物）
      └── Worker 集群
            ├─ Outline Agent（规划/搜索/引用）
            ├─ Slide Content Agent（逐页内容/图表）
            ├─ Media Agent（图片检索/裁剪/版权）
            ├─ Layout & Quality Agent（版式/事实/安全）
            └─ Export Worker（PPTX/PDF/缩略图）
```

### 5.1 服务边界与迁移策略

- 第一阶段不拆成更多微服务：将现有 `main_api` 升级为唯一对外 API，保留 `simpleOutline` 和 `slide_agent` 作为内部 Worker/A2A 服务。
- 把 ADK 的内存会话替换为数据库 `generation_jobs`、`agent_runs`、`slide_runs`；会话上下文按任务保存，敏感提示词可脱敏或按策略留存。
- personaldb 从同步 HTTP 接口改为“文件入库 Job”；向量数据优先迁移到 PostgreSQL + pgvector，保留 Chroma 作为开发/回滚方案。
- 所有外部模型、搜索、图片服务通过 Provider 适配器接入，统一超时、重试、熔断、成本记录和敏感词过滤。

### 5.2 建议核心数据表

`users`、`tenants`、`tenant_members`、`projects`、`project_versions`、`slides`、`generation_jobs`、`agent_runs`、`slide_runs`、`templates`、`template_versions`、`assets`、`knowledge_bases`、`documents`、`document_chunks`、`exports`、`share_links`、`usage_records`、`audit_logs`。

关键字段建议：所有表含 `id`、`tenant_id`、`created_by`、`created_at`、`updated_at`、`deleted_at`（软删除）；任务含 `status`、`progress`、`retry_count`、`idempotency_key`、`error_code`；版本含 `schema_version` 和 `content_json`。

## 6. Agent 编排与任务协议

### 6.1 统一执行阶段

1. **Decision**：识别语言、页数、数据源、模板复杂度、是否需要联网/知识库，输出路径和成本预算。
2. **Planning**：生成结构化大纲和页级任务图，标注页面类型、内容槽位、引用要求和依赖。
3. **Execution**：内容、图片、图表、引用等独立节点并行执行；每个节点使用独立上下文。
4. **Quality**：Schema、事实、版式、可访问性、版权和安全多维检查；失败时只重跑受影响节点。
5. **Summary**：汇总产物、质量分数、引用、成本、警告和下一步操作。

### 6.2 统一任务事件

```json
{
  "event_id": "uuid",
  "job_id": "uuid",
  "type": "job.started|slide.progress|quality.issue|job.completed|job.failed",
  "stage": "decision|planning|execution|quality|export",
  "progress": 42,
  "slide_index": 3,
  "payload": {},
  "trace_id": "uuid",
  "created_at": "ISO-8601"
}
```

要求：事件可重放、客户端 SSE 断线可按 `Last-Event-ID` 续传；消费者使用幂等键，重试次数和死信原因可查询；禁止以“跳过页面”作为默认成功，需显式标记 `PARTIAL` 并阻止无提示导出。

## 7. API 设计（建议 `/api/v1`）

### 7.1 认证与项目

- `POST /auth/register|login|refresh|logout`
- `GET /me`、`GET/POST /projects`、`GET/PATCH/DELETE /projects/{id}`
- `GET/POST /projects/{id}/versions`、`POST /versions/{id}/restore`

### 7.2 生成与任务

- `POST /projects/{id}/outline:generate`：主题、页数、风格、资料范围、幂等键。
- `POST /projects/{id}/slides:generate`：版本、页范围、模板版本、数据源。
- `GET /jobs/{job_id}`、`GET /jobs/{job_id}/events`（SSE）、`POST /jobs/{job_id}/cancel|retry`。
- `POST /projects/{id}/quality:check`、`GET /projects/{id}/quality-report`。

### 7.3 模板、知识库、导出

- `GET/POST /templates`、`POST /templates/{id}/versions`、`POST /templates/{id}:publish`。
- `POST /knowledge-bases/{id}/documents`、`GET /documents/{id}/status`、`POST /knowledge-bases/{id}:search`。
- `POST /projects/{id}/exports`、`GET /exports/{id}`、`GET /exports/{id}/download`、`POST /share-links`。

保留旧接口 `/tools/aippt_outline`、`/tools/aippt` 作为 1~2 个版本的兼容层，内部转发到 Job API，并在响应头返回 `Deprecation` 和 `X-Job-Id`。

## 8. 安全、合规与可靠性要求

- CORS 改为配置白名单；所有非公开 API 强制 JWT，服务端从 Token 获取 `tenant_id/user_id`，禁止信任请求体中的 userId。
- 上传限制：单文件大小、总配额、扩展名/MIME/魔术字节校验、压缩炸弹防护、病毒扫描、文件名随机化；对象存储使用私有 Bucket 和签名 URL。
- `/proxy` 仅允许 HTTPS + 域名白名单，禁止访问内网 IP、回环、云元数据地址和任意端口；`/data/{filename}` 改为资源 ID 查表，不拼接用户输入路径。
- 模型输入输出做 Prompt 注入隔离、HTML/SVG 清洗、敏感信息脱敏、内容安全审核；保留来源和引用，支持删除用户数据。
- Redis/PostgreSQL/MinIO 不暴露公网；密钥使用 Secret 管理，`.env` 不入库；开启 TLS、备份加密和最小权限账号。
- 限流与配额：按用户/IP/租户限制并发任务、搜索次数、导出次数；模型调用设置超时、指数退避、熔断和预算上限。
- 可靠性目标：API 可用性 ≥99.5%，任务最终成功率 ≥98%，P95 首字节 ≤3 秒，断线重连不丢事件，数据库每日备份并演练恢复。

## 9. 部署与运维方案

### 9.1 生产 Compose 组成

`nginx`、`ppt-api`、`outline-worker`、`slide-worker`、`quality-worker`、`export-worker`、`postgres(pgvector)`、`redis`、`minio`、`worker-scheduler`、`migrate`。仅 Nginx 暴露公网端口；内部服务加入同一私有网络，均配置 healthcheck、资源上限和 `restart: unless-stopped`。

### 9.2 配置分层

- `.env.example`：仅变量说明和安全默认值。
- `.env.staging` / `.env.prod`：由部署系统注入，不提交仓库。
- 启动前执行配置检查：数据库、Redis、对象存储、LLM、Embedding、搜索、CORS、JWT 密钥、磁盘空间和模型连通性；ERROR 阻止启动，WARNING 允许降级。

### 9.3 发布流程

1. CI：Python lint/type/pytest、前端 type-check/build、依赖漏洞扫描、镜像扫描、OpenAPI 契约测试。
2. Staging：执行数据库迁移、导入样例模板/知识库、跑 E2E 和 10 份质量基线 PPT。
3. Production：蓝绿或滚动发布，先迁移再切流；保留上一镜像和数据库回滚脚本。
4. 监控：Prometheus 指标、结构化 JSON 日志、OpenTelemetry Trace、错误告警（Sentry/自建）；每日检查队列积压、失败率、成本和存储。

## 10. 测试与验收门槛

### 自动化测试

- 单元：模板 schema、Markdown/JSON 解析、页数拆分、图片裁剪、权限策略、SSRF 防护、导出映射。
- 集成：PostgreSQL/Redis/MinIO、Job 状态机、SSE 续传、Worker 重试/死信、向量检索、模型 Provider mock。
- E2E：注册 → 创建项目 → 生成大纲 → 修改确认 → 生成内容 → 质量检查 → 编辑 → PPTX/PDF 下载 → 分享 → 删除。
- 回归：固定主题/模板/语言数据集，比较页数、字段完整率、引用率、溢出率、可打开率和成本。

### 发布验收指标（建议）

| 指标 | 目标 |
| --- | --- |
| 任务可追踪率 | 100% 有 job_id、阶段、进度和最终状态 |
| 生成完整率 | ≥98% 任务无缺页；部分成功必须显式告警 |
| JSON/schema 通过率 | 首次 ≥95%，重试后 ≥99% |
| PPTX 可打开率 | ≥99%（PowerPoint/WPS 各抽样） |
| 版式问题率 | 自动检测溢出/遮挡页 ≤3% |
| 引用覆盖率 | 启用知识库/联网时 ≥90% 的事实块有来源 |
| API P95 | 普通接口 ≤500ms；流式首字节 ≤3s |
| 数据隔离 | 跨租户访问测试 0 成功 |
| 恢复演练 | 删除容器后，数据库/对象存储可恢复，RTO ≤2h、RPO ≤24h |

## 11. 分阶段实施路线图

### 第 0 阶段：基线与止血（1 周）

- 冻结现有接口和模板 JSON schema，补充架构图、环境变量清单、样例数据。
- 修复 CORS、SSRF、路径穿越、上传大小/MIME 校验；移除 `aippt_by_id` 空占位逻辑。
- 增加 `/health/live`、`/health/ready`、Trace ID 和结构化日志。

### 第 1 阶段：可上线 MVP（2~3 周）

- PostgreSQL 表结构与 Alembic 迁移；JWT/租户/项目/版本 API。
- Redis Stream Job 状态机；大纲、逐页生成、索引、导出全部异步化。
- MinIO/S3 文件存储；前端项目列表、自动保存、任务进度和失败重试。

### 第 2 阶段：质量与交付（2~3 周）

- 模板管理与版本；质量 Agent（版式、引用、图片、敏感内容）；PPTX/PDF/图片服务端导出。
- 知识库异步索引和权限；引用回溯；分享链接、水印和审计。
- 完成 E2E、质量基线、配置检查和生产 Compose。

### 第 3 阶段：商业化与协作（3~4 周）

- 团队/角色、品牌中心、评论协作、用量套餐、管理后台和运营报表。
- 多模型路由、缓存、成本预算、行业模板和 API/SDK。

## 12. 重点风险与应对

| 风险 | 表现 | 应对 |
| --- | --- | --- |
| LLM 输出不稳定 | JSON、事实或版式波动 | 严格 schema、结构化输出、局部重试、质量门禁、基线评测 |
| 长任务超时 | SSE 断开、重复生成 | Job + 事件持久化、断点续作、幂等键、后台 Worker |
| 图片/版权不可控 | 外链失效、侵权 | 白名单、下载落盘、来源/许可证字段、失效检测、可替换素材 |
| Chroma 单机瓶颈 | 多实例数据不一致 | 迁移 pgvector/托管向量库，短期单实例并做好备份 |
| 模板兼容性 | 槽位缺失、尺寸错位 | 模板 schema、导入预检、渲染快照回归、版本锁定 |
| 成本失控 | 搜索/模型调用暴增 | 分级模型、预算/配额、缓存、并发限制、用量告警 |
| 敏感数据泄露 | 简历/企业资料进入日志或第三方 | 脱敏、最小留存、租户隔离、可配置数据驻留和删除 |

## 13. 开发执行清单（可直接建 Jira）

1. `P0-SEC`：JWT、租户隔离、CORS 白名单、上传与 SSRF 防护。
2. `P0-DATA`：PostgreSQL schema、迁移、项目/版本/任务模型。
3. `P0-JOB`：Redis Stream、Job API、SSE 事件、重试/取消/死信。
4. `P0-AGENT`：Decision/Planning/Execution/Quality/Summary 编排器和统一事件协议。
5. `P0-TPL`：模板上传、标注、版本、审核、兼容性校验。
6. `P0-KB`：异步文档索引、权限、引用和 pgvector 迁移。
7. `P0-EXPORT`：服务端 PPTX/PDF/缩略图导出、对象存储、签名下载。
8. `P0-FE`：登录、工作台、项目列表、生成进度、自动保存、质量报告。
9. `P0-OPS`：生产 Compose、配置检查、备份恢复、指标日志告警。
10. `P0-QA`：单元/集成/E2E/质量基线和发布门禁。

完成 P0 后，TrainPPTAgent 才具备“用户可登录、项目可保存、任务可恢复、结果可质检、文件可交付、问题可观测”的上线闭环；P1/P2 再围绕协作、商业化和行业差异化扩展。
