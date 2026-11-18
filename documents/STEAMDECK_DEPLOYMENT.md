# Steam Deck CI/CD

## 流程与边界

部署变更在本机仓库修改、提交并推送，由 GitHub Actions 的 Deck runner 应用同次运行的 Compose 附件；不直接 SSH 修改 Deck 上的部署配置或运行容器。Deck 本地仅配置秘密文件。

PR 与 main 在 GitHub 托管 runner 执行 Ruff、SQLite/PostgreSQL 测试、前端测试和构建。
部署配置任务执行 actionlint（含上下文/表达式检查）、bash -n、ShellCheck、Compose config，以及真实 deploy.sh 的 Docker 故障注入测试。
镜像在 GitHub 构建 linux/amd64，在镜像内验证迁移、OCR 和前端 HTTP；全部通过后才推送同一个已测试镜像到 GHCR，输出注册表 sha256 摘要。

Deck 不 checkout、不下载业务源码、不构建。它只通过 download-artifact 获取同次 workflow 的 compose.yaml、deploy.sh、docker-env.sh。凭据写入 RUNNER_TEMP 下的独立 DOCKER_CONFIG，链接用户已有 Compose 插件；登录动作退出及 always 清理步骤负责移除凭据和临时附件。

## 现有实例

默认目录 /home/deck/rag-center-deploy，Compose 项目 rag-center；数据库容器 rag-center-db-1，pgvector/pgvector:pg17，用户和库名 rag。
部署预检要求数据库已健康，且数据挂载源恰为该目录的 postgres；不会创建或重建数据库。

- .env：POSTGRES_PASSWORD、可选 CLOUDFLARED_IMAGE；保留 Deck 已有随机值，权限 600。
- app.env：RAG_* 应用参数、签名密钥、随机登录凭据和 Embedding 设置；保留原值，权限 600。
- postgres/：绑定 /var/lib/postgresql/data；数据库无主机端口。
- files/：绑定 /data/files；容器 user 0:0 通过 rootless 映射至主机 deck。
- backups/：每次部署一个私有目录，包含数据库归档、文件、应用、数据库与隧道 env、原 Compose 和可用的原部署脚本、成功摘要。

.env.example 与 app.env.example 只供新环境参考，流水线不会把它们下载到 Deck，也不会覆盖真实 env。
公网配置要求 production，PUBLIC_URL=https://rag.xqtdevelopment.xyz；API 主机端口仅绑定 127.0.0.1。
cloudflared 与 API 属于同一 Compose 项目；cloudflared.env 保存 TUNNEL_TOKEN，权限 600。
完整配置见 deploy/steamdeck/HTTPS.md。隧道未完成公网验收时，不应视为已上线。
Embedding Key 留空时实际索引明确失败；不回退 local-test。production 的 HTTPS 和真实 Embedding 校验保持原样。

## 部署顺序与失败语义

1. 锁定部署目录，验证数据库身份、挂载和配置；拉取精确摘要镜像；检查应用设置和 files 写权限。此阶段不停止写入。
2. 记录原运行容器 ID，停止 API 和 worker（60 秒宽限）。
3. pg_dump -Fc，pg_restore --list 验证归档可读；打包并验证 files；复制原配置和签名密钥，写校验和与 COMPLETE 标记。
4. 启动镜像内迁移，再启动 API/worker。迁移开始后失败，或启动/验收失败，停止两个写入服务，保留数据库和备份；不自动还原数据库或降级 schema。
5. 检查 HTTP 健康、实际首页 HTML、users 表查询、数据库 revision 等于镜像迁移 heads；确认 worker 30 秒持续运行且无重启。启动 cloudflared 并检查内部 /ready，隧道连接成功后停止同项目旧 Caddy。全部成功后保存配置并原子更新 release.env（应用与连接器摘要）。公网 DNS/TLS/路由另做验收。

备份之前或备份中失败，可尝试 docker start 原容器 ID。不会用新镜像重建旧容器。
INT/TERM 和普通错误通过 EXIT 处理；强制断电/SIGKILL 无法由脚本捕获，应人工确认两个写入服务状态。
不执行 down -v、清空卷、删数据或数据库自动恢复。备份包含秘密，不能上传 Actions，也不要输出完整 Compose 配置或 env。

## GitHub 设置

在仓库 Settings → Secrets and variables → Actions → Variables 设置 STEAMDECK_DEPLOY_ENABLED=true。
可选 STEAMDECK_DEPLOY_DIR 必须为现有部署目录绝对路径，默认 /home/deck/rag-center-deploy。
Settings → Environments 创建 steamdeck，只允许 main 部署；如需无人值守，不设置人工审核。
Settings → Actions → Runners 确认 runner 在线 Idle，包含 self-hosted、Linux、X64、steamdeck 标签。
已有 /home/deck/actions-runner、rag-center-runner.service 和隐藏令牌输入的 register-runner.sh，应复用，不在聊天中粘贴注册令牌。

Deck 上可执行以下只读检查：
```bash
systemctl --user status rag-center-runner.service --no-pager
systemctl --user status docker.service --no-pager
/home/deck/bin/docker info
/home/deck/.docker/cli-plugins/docker-compose version
```

若尚未注册，在 Deck 本地运行已准备的 register-runner.sh，按提示隐藏输入 GitHub 页面生成的短期注册令牌，然后启动 rag-center-runner.service；不要改动 Codex、VS Code 服务或 SteamOS 只读保护。

GHCR 发布用作业 GITHUB_TOKEN 的 packages:write，拉取用 packages:read，不需要长期 PAT。
若已有包返回 denied，在包 Settings → Manage Actions access 中给 xqt-debug/rag-center 仓库写入/读取权限。仓库或组织策略也须允许 packages 写入。
部署关闭时 deploy 作业会 skipped；runner 离线时会 queued，不能视为部署成功。

## 运维

成功后在部署目录运行：
```bash
source ./docker-env.sh
docker compose --env-file .env --env-file release.env ps
```

失败时从对应 Actions 运行获得目标摘要，以 RAG_IMAGE 环境变量调用 Compose 检查状态。日志可能含业务信息，不应公开上传。只有 backups 下 COMPLETE 存在、校验和通过的目录才是完整配套备份。

账号密码登录与注册在所有环境统一开放。升级 0005 时读取现有 app.env 中的旧开发账号配置迁移已有账号密码，保留资料；升级后不再需要 RAG_DEV_* 或 RAG_SMS_* 配置。首次安装请从页面注册。
