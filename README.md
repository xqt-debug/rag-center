# RAG Center

个人知识库与 Agent 检索服务。Python / FastAPI + PostgreSQL / pgvector + Vue 3。

已实现开发环境账号密码登录、正式环境手机号验证码登录、知识库和文件管理、异步解析与索引、关键词／向量／混合检索、原文预览、访问密钥及 MCP 完整增删改查。

产品需求见 [documents/PRD.md](documents/PRD.md)，当前实现边界见 [documents/IMPLEMENTATION.md](documents/IMPLEMENTATION.md)。

支持 TXT、Markdown、文本 PDF，以及 PNG、JPEG、GIF、WebP、BMP 图片；网页可直接粘贴纯文本保存为 `.txt` 或 `.md`。

代码职责、扩展方式和关键并发约束见 [维护指南](documents/MAINTAINING.md)。

## 本地启动（Windows PowerShell）

需要 Python 3.11+、uv、Node.js 20.19+ 或 22.12+，以及运行中的 Docker Desktop。

```powershell
uv sync --locked
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d db
Set-Location frontend
npm.cmd ci
npm.cmd run build
Set-Location ..
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe scripts/dev.py
```

浏览器打开 [http://localhost:8000](http://localhost:8000)。开发启动器同时运行 API 与后台文件处理；修改前端后重新构建即可，后端修改需重启。

开发前端时也可在 `frontend` 中运行 `npm.cmd run dev`，访问其显示的本地 5173 地址。API 继续运行在 8000，前端开发服务器代理 API 与 MCP 请求。

开发环境直接使用账号密码登录，默认账号 **`admin`**、密码 **`admin123`**，无需短信。可在 `.env` 中修改：

```dotenv
RAG_ENV=development
RAG_DEV_USERNAME=admin
RAG_DEV_PASSWORD=admin123
```

测试账号首次成功登录时创建，之后登录保留原有知识库与文件。手机号可为空，不需要填写虚构号码。开发密码通过环境配置校验，不写入用户表或前端配置；修改配置后需重启服务。

生产环境不注册测试账号密码入口，登录页继续使用手机号验证码流程。升级已有项目时先运行 `.venv\Scripts\python.exe -m alembic upgrade head`，应用新增用户名及手机号可空的迁移。

`.env.example` 默认连接 compose 中的 PostgreSQL。未创建 `.env` 时使用 `data/development.sqlite` 便于快速体验；SQLite 仅作为开发回退，关键词排序与生产 PostgreSQL 不完全相同。

### 查看文件索引进度

文件列表显示等待、解析、分块、建立索引和“索引已完成”状态。完成后展示文本块数量及实际索引完成时间，并弹出完成提醒；失败项直接展示原因并允许重试。列表上方统计本页的完成、处理中和失败数量。

有任务时约每 3 秒更新，空闲时每 15 秒检查，便于发现 MCP 或其他页面提交的文件。更新失败时明确保留上一可检索版本；测试向量模式始终有提示，不将流程完成视为真实模型的检索效果验证。

升级已有项目时，需由用户手动运行 `.venv\Scripts\python.exe -m alembic upgrade head`，添加 `0003` 索引完成时间字段，再重启服务。历史文档没有记录过完成时间时只显示完成状态，不使用上传时间代替。

### 配置真实语义向量服务

默认使用真实向量服务 `openai-compatible`。未配置 API 密钥时，索引任务会明确失败，不会自动退回测试向量。请在 `.env` 中配置：

```dotenv
RAG_EMBEDDING_PROVIDER=openai-compatible
RAG_EMBEDDING_BASE_URL=https://your-provider.example/v1
RAG_EMBEDDING_API_KEY=your-provider-key
RAG_EMBEDDING_MODEL=your-text-embedding-model
RAG_EMBEDDING_DIMENSIONS=1024
```

供应商须支持 `/embeddings`、批量文本和 `dimensions` 参数。当前适配器最多每批 10 条，固定 800 字符分块、120 字符重叠；维度限制 8–2000。请确保服务支持所配置模型和维度。更换配置后重启 API 与 worker，在文件列表点击“重建索引”；失败任务点击“重试”，无需重新上传文件。重建成功前保留旧版供关键词检索，语义检索只使用与当前模型配置一致的索引。

`local-test` 仅供显式启用的离线流程测试，使用分词哈希向量，**不具备真实语义理解能力**。历史测试记录显示“测试索引（非模型）”，不计入真实索引完成数量；配置真实服务并重启后可直接重建。

### 图片、动图与 OCR

图片上传后由本地 RapidOCR（`rapidocr-onnxruntime`，模型随依赖包提供）识别中英文，无需 OCR API Key。识别出的文字沿用现有分块、关键词和文本向量检索；向量服务仍需上面的配置。图片保留原始字节，预览和下载不会转码，GIF 动画完整保留。MCP 使用原有上传、下载、读取和搜索工具管理图片，`read_document` 返回识别文字。

GIF 等多帧图片最多均匀抽取 12 帧（包括首尾帧），重复整帧文字去重，引用记录实际帧号；短暂出现的文字可能遗漏。静态图片识别整张图。识别副本最大边长 2500 像素，不修改原文件。OCR 不等于图片语义理解，不保证手写文字、公式或表格结构识别准确，请核对原图。

无可识别文字时显示“已保存（未识别到文字）”，可预览和下载，不建立空向量、不计入索引完成；OCR 或向量服务失败会显示失败原因，原图仍可下载并重试。此功能暂不为扫描 PDF 添加 OCR。

原图预览使用与下载相同的 5 分钟短期凭证，可点击“刷新图片预览”续取；每次访问仍检查密钥撤销、文件删除和版本变化。上传限制：单帧最多 2000 万像素、最多 300 帧、累计最多 1.2 亿像素，单文件大小仍受 `RAG_MAX_FILE_MB` 限制。

### 配置短信

第一版通过可替换 HTTPS webhook 接短信网关。请求为 `POST`，使用 `Authorization: Bearer <RAG_SMS_WEBHOOK_TOKEN>`，JSON 为 `phone`、`code`、`expires_in`；返回 2xx 表示发送成功。

```dotenv
RAG_SMS_PROVIDER=webhook
RAG_SMS_WEBHOOK_URL=https://your-sms-gateway.example/send
RAG_SMS_WEBHOOK_TOKEN=your-gateway-secret
```

短信网关负责对接实际供应商的签名、模板和凭证。生产环境禁止使用开发验证码。

## MCP 接入

在网页的“MCP 接入”创建密钥，选择授权知识库与操作权限，立即复制或下载配置。完整密钥仅展示一次；数据库不存明文。

以下为支持远程 HTTP 与自定义请求头客户端的示意，具体客户端需按其格式调整：

```json
{
  "mcpServers": {
    "rag-center": {
      "url": "http://localhost:8000/mcp",
      "headers": { "Authorization": "Bearer <YOUR_API_KEY>" }
    }
  }
}
```

提供 18 个工具，涵盖知识库 CRUD、文件 CRUD、图文联合保存、上传下载、任务查询与重试、混合检索和片段展开。创建、更新、删除等写入支持 `request_id`，重复请求使用相同标识和参数；更新／删除需先读取资源并提供 `expected_revision`。

- 小型 TXT／Markdown：直接通过 `create_document(text=...)` 或 `update_document(text=...)` 传入全文。

纯文本无需本地文件或上传步骤。例如调用 MCP 工具 `create_document`：

```json
{
  "knowledge_base_id": "已有知识库的 ID",
  "name": "学习笔记.txt",
  "text": "TCP 通过重传机制保证可靠传输。\n这是第二行笔记。",
  "request_id": "note-create-001"
}
```

`text` 最多 200000 个字符，不要同时传 `upload_id`。创建后用返回的 `job_id` 调用 `get_ingestion_job`；就绪后用 `read_document(document_id=...)` 直接读取正文，用 `search` 检索。读取结果的 `next_offset` 非空时，将它作为下一次的 `offset` 继续读取。修改时先调用 `get_document` 获取最新 `revision`，再通过 `update_document` 提交 `document_id`、`expected_revision` 和完整 `text`。保存中文与换行，下载原文件时返回对应 UTF-8 文本。
- PDF／大文件：`prepare_document_upload` → 对返回地址 HTTP PUT 原始字节 → 使用 `upload_id` 创建或更新文件。
- 下载：`download_document` 获取短期链接，再 HTTP GET。会重新检查资源状态和密钥权限，删除、撤销或版本变化后拒绝旧链接。
- 创建与替换内容是异步任务，通过 `get_ingestion_job` 轮询进度。
- 远程服务不能读取客户端本地路径；PDF 上传下载要求 Agent 具备 HTTP 文件传输能力。

仅 API 密钥认证已实现，OAuth 授权流程暂未接入。自动测试使用官方 Python MCP 客户端通过真实 HTTP 完成管理流程；未承诺所有桌面 Agent 的配置兼容性。

### MCP 同时保存图片与文字

调用 `create_image_document`，具备 `create` 权限且获授权的知识库即可使用。一次调用提交小图片与对应正文：

```json
{
  "knowledge_base_id": "已有知识库的 ID",
  "name": "握手流程.png",
  "image_base64": "图片原始字节的标准 Base64 编码，不含 data: 前缀",
  "text": "TCP 三次握手：客户端发送 SYN，服务端返回 SYN+ACK，客户端发送 ACK。",
  "request_id": "image-note-001"
}
```

也可先调用 `prepare_document_upload`、HTTP PUT 原始图片，再将上例中的 `image_base64` 替换为 `upload_id`，避免大图片占用 Agent 上下文。两种图片来源必须且只能提供一个。支持 PNG/JPEG/GIF/WebP/BMP，文字非空且最多 200000 字符；Base64 字段最多 28000000 字符，解码后的图片仍受服务器单文件上限与真实格式校验。

此接口直接使用传入文字建立索引，**不会再次 OCR 覆盖**；原始图片字节（含 GIF 动画）保持不变。图片和文字在同一文档版本中持久化，重启、失败重试和模型重建都保留文字，图片与 UTF-8 配套文字大小一同计入存储配额。返回 `job_id` 后用 `get_ingestion_job` 查询进度，就绪后用 `read_document` 读取正文、`search` 检索、`download_document` 下载原图。元数据 `text_source=provided` 表示调用方正文，网页预览会区分其与自动 OCR 文字。

升级需要运行 `.venv\Scripts\python.exe -m alembic upgrade head`，应用 `0004` 可空正文列；既有图片继续自动 OCR，既有文本与索引不变。普通自动 OCR 图片仍使用 `create_document(upload_id=...)`。

## 验证

```powershell
# 无外部服务的本地测试；HTTP / MCP 测试需要允许本机网络通信
.venv\Scripts\python.exe -m pytest -q

# 使用 compose PostgreSQL，逐测试创建独立临时 schema
.venv\Scripts\python.exe -m pytest --postgresql -q

.venv\Scripts\ruff.exe check rag_center tests scripts migrations
.venv\Scripts\ruff.exe format --check rag_center tests scripts migrations
Set-Location frontend
npm.cmd run check
npm.cmd test
npm.cmd run format:check
npm.cmd run build
```

`RAG_TEST_DATABASE_URL` 可覆盖测试数据库地址。PostgreSQL 测试只创建并清理随机 `test_<uuid>` schema，不清空业务表；不要指向不允许创建临时 schema 的数据库账号。

## 部署约束

- 生产设置 `RAG_ENV=production`、随机且至少 32 字符的 `RAG_SECRET`、HTTPS 公网地址、PostgreSQL、真实短信和真实 Embedding 服务；启动时检查配置，缺失则拒绝运行。
- 先运行 `alembic upgrade head`，再分别运行 API 与 worker：

```powershell
.venv\Scripts\python.exe -m uvicorn rag_center.app:app --host 127.0.0.1 --port 8000 --no-access-log
.venv\Scripts\python.exe -m rag_center.worker
```

- API 和 worker 必须共享相同配置、数据库及文件目录；当前使用本地文件存储，不支持多个无共享磁盘的主机。
- 反向代理配置 HTTPS、上传大小和超时；勿将短信、手机号或代理用户输入直接当作可信代理头。当前按直连 IP 限流，代理部署需在可信入口统一限流。
- 使用无访问日志的启动参数，避免日志保留携带文件传输凭证的 URL；代理同样应隐藏这些路径。
- 默认 compose 凭证仅供本地开发。部署前修改密码与连接串，不向公网暴露数据库。
- 备份需同时覆盖 PostgreSQL、文件目录和签名密钥；改变签名密钥会使会话、API 密钥与传输链接失效。
- 不使用自动重载模式运行生产任务；worker 使用持久化队列、租约和续租处理重启恢复。
