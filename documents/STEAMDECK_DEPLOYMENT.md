# GitHub Actions 自动部署到 Steam Deck

仓库：`xqt-debug/rag-center`。目标为 Steam Deck（Linux / x86-64），已安装 Docker Engine 和 Docker Compose v2。

## 工作流程

1. PR 和 `main` 推送：GitHub 执行 Ruff、SQLite 和 PostgreSQL 全部后端测试，以及前端测试、格式检查、类型检查和构建。
2. 测试通过后，在 GitHub 构建包含前端、API 和本地 OCR 的 `linux/amd64` 镜像，并验证容器内迁移、OCR 模型加载和前端文件。PR 只验证；`main` 发布到 GHCR。
3. 启用部署后，Steam Deck 上带 `steamdeck` 标签的 runner 拉取这次构建的精确镜像摘要，备份数据库、停止 API/worker、迁移数据库，再启动并检查服务。

默认不启用 CD，避免尚未注册 runner 时任务一直排队；CI 和镜像发布无需 Steam Deck 在线。Deck 只需要向外连接 GitHub、GHCR 和配置的向量服务，无需公网 IP 或端口转发。休眠/关机时 runner 离线，部署需要等设备恢复在线；不要为此修改 SteamOS 系统分区。

## 一、注册 Steam Deck runner

在 Deck 桌面模式的 Konsole 中确认：

```bash
docker info
docker compose version
command -v bash flock openssl
```

执行 runner 的用户必须能直接运行 `docker info`，不能依赖交互式 `sudo`。当前 Deck 使用 Rootless Docker，CLI 通常位于 `~/bin/docker`，服务端 socket 位于 `/run/user/$(id -u)/docker.sock`。部署脚本会在没有显式 Docker host/context 配置时自动识别该 socket。

若还没有 Compose，可按 Docker 官方文档将 Linux x86-64 插件安装到 `~/.docker/cli-plugins/docker-compose`。流水线使用临时 `DOCKER_CONFIG` 保存本次 GHCR 登录信息，会自动链接用户目录中的 Compose 插件，避免出现终端能运行 Compose、runner 找不到插件的问题。

打开 GitHub 仓库 **Settings → Actions → Runners → New self-hosted runner**，选择 **Linux / x64**。在 Deck 的 `~/actions-runner` 下执行该页面生成的下载、解压和注册命令。注册时追加：

```bash
./config.sh --url https://github.com/xqt-debug/rag-center --token <页面生成的临时注册令牌> --labels steamdeck --name steamdeck
```

令牌只在 Deck 输入，不写进仓库或聊天。先运行 `./run.sh` 验证 GitHub 页面显示 Idle。

注册成功后，可使用用户级 systemd 服务常驻，不需要把 runner 安装进只读系统分区：

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/rag-center-runner.service <<'EOF'
[Unit]
Description=RAG Center GitHub Actions runner

[Service]
WorkingDirectory=%h/actions-runner
ExecStart=%h/actions-runner/run.sh
Environment="PATH=%h/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="DOCKER_HOST=unix://%t/docker.sock"
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

# 若之前前台运行 run.sh，先在原终端 Ctrl+C 停止，避免重复启动。
systemctl --user daemon-reload
systemctl --user enable --now rag-center-runner
sudo loginctl enable-linger "$USER"
systemctl --user status rag-center-runner
```

Rootless Docker 自身也需要开机启动：`systemctl --user enable --now docker`。若日后改用系统级 Docker，应同时移除 runner 服务中的 Rootless `DOCKER_HOST` 行，重新加载并重启 runner。

Docker 的镜像拉取由后台 Docker 服务发起。终端中设置的 `http_proxy`/`https_proxy` 不会自动传给已运行的用户服务；若拉取 GHCR 或 Docker Hub 超时，应为 `docker.service` 单独配置代理并重启验证，而不是把代理订阅或凭据提交到仓库。

如果注册页面提示缺少系统依赖，先根据实际报错处理；这里没有自动执行 `pacman` 或关闭 SteamOS 的只读保护。用户服务可以跨注销运行，但无法让关机或休眠中的设备执行任务。

## 二、准备 Deck 上的独立配置

创建 `~/apps/rag-center/.env`，内容按仓库 `deploy/steamdeck/.env.example` 填写。该配置只保留在 Deck，更新镜像时不会覆盖。

```bash
mkdir -p ~/apps/rag-center
chmod 700 ~/apps/rag-center
# 分别生成 RAG_SECRET 和 POSTGRES_PASSWORD，复制结果到 .env：
openssl rand -hex 32
openssl rand -hex 24
```

必须更换 `RAG_DEV_PASSWORD`、`RAG_SECRET`、`POSTGRES_PASSWORD`，并填入向量服务 API Key。数据库密码嵌入连接 URL，请使用生成的十六进制值，不要直接填包含 `@`、`:`、`/`、`#` 的密码。`.env` 中包含 `$` 等特殊字符的值应使用单引号包围，避免 Compose 插值。最后执行 `chmod 600 ~/apps/rag-center/.env`。

默认使用现有的账号密码登录模式 `RAG_ENV=development`，绑定 `127.0.0.1:8000`，在 Deck 浏览器打开 `http://localhost:8000`。

如需在自己电脑通过家庭局域网访问，修改：

```dotenv
RAG_BIND_ADDRESS=0.0.0.0
RAG_PUBLIC_URL=http://你的Deck局域网IP:8000
```

这里的 development 模式包括测试验证码入口，只适用于本机或可信私网。正式公网部署需改为 production，配置 HTTPS 和真实短信 webhook，遵守现有 `Settings.validate_deployment()` 约束；本流程不会绕过这些检查。

此部署使用独立命名的 `rag-center-steamdeck_postgres` 和 `rag-center-steamdeck_files` Docker 卷，不会自动迁移你 Windows 上现有的知识库和文件。首次部署是一套新实例。

## 三、启用 GitHub 自动部署

1. 将本次代码提交并推送至 `main`。打开 **Actions → CI and Steam Deck deployment**，确认测试与镜像任务成功。
2. 在 **Settings → Actions → General** 允许仓库运行 GitHub Actions；组织策略若禁止写入 packages，需要放行本工作流的 `packages: write`。发布和拉取均使用当次自动生成的 `GITHUB_TOKEN`，无需长期 PAT。若已有同名 GHCR 包，须为本仓库授予该包的 Actions 读写权限。
3. 在 **Settings → Secrets and variables → Actions → Variables** 新建仓库变量 `STEAMDECK_DEPLOY_ENABLED`，值为 `true`。可选变量 `STEAMDECK_DEPLOY_DIR` 用于自定义 Deck 上的绝对部署路径；默认 `~/apps/rag-center`。
4. 在 **Settings → Environments** 配置 `steamdeck` 环境，仅允许 `main` 部署。需要完全自动时，不添加人工审批要求。
5. 在 Actions 页面点 **Run workflow**，选择 `main`。之后每次推送 `main` 都自动执行测试、构建和部署。

只有 `main` 的推送/手动运行会进入 Deck，PR 测试始终在 GitHub 托管机器执行；不要将部署触发器改为 `pull_request_target`，或让不受信任的分支在此 runner 上执行。能修改并推送 `main` 的人也能控制部署机器，应限制仓库写入权限。

## 四、查看服务与处理失败

成功部署后，脚本写入 `release.env`，记录上次成功的精确镜像摘要。Deck 上执行：

```bash
cd ~/apps/rag-center
docker compose --env-file .env --env-file release.env ps
docker compose --env-file .env --env-file release.env logs --tail=100 api worker
curl --fail http://127.0.0.1:8000/api/health
```

每次部署前在 `~/apps/rag-center/backups/` 生成 `pg_dump` 自定义格式备份。备份仅包含数据库，原始文件另存于 files 卷；应定期备份文件卷，并将数据库和文件副本同步到其他设备。备份不会自动清理，需关注磁盘剩余空间。脚本从不执行 `docker compose down -v` 或清空卷。

迁移失败时 API 和 worker 保持停止，Actions 显示失败；不要在未知数据库状态下自动回退镜像或执行降级迁移。检查迁移日志并修复后重跑。启动失败也不会覆盖 `release.env`，但这不代表旧服务仍在运行。首次失败尚无 `release.env` 时，可从 Actions 日志取得该次摘要，再使用 `RAG_IMAGE='ghcr.io/…@sha256:…' docker compose --env-file .env logs --tail=100` 排查。

本流程有短暂维护窗口，不提供无停机发布或数据库自动回滚。后台 worker 的启动状态以及 API 健康检查不代表外部向量 API 已可用；部署后应在网页上传一条测试笔记确认实际索引结果。
