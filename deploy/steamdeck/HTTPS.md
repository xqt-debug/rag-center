# Steam Deck 公网 HTTPS（Cloudflare Tunnel）

公网地址为 `https://rag.xqtdevelopment.xyz`。Cloudflare 负责边缘 HTTPS，
同一 Compose 项目中的 cloudflared 主动建立加密隧道，回源地址为 `http://api:8000`。
API 主机端口固定绑定 127.0.0.1:8000；数据库与连接器均无主机端口。
不再需要 Caddy、阿里云 DNS AccessKey、443 低端口权限或路由器端口转发。

## 环境变量

部署密钥统一在 GitHub 的 steamdeck Environment secrets 中管理。Compose 要求 >= 2.30.0（使用 raw env_file）。
Compose 默认固定官方 cloudflared 2026.11.1 的镜像摘要；如需更新，可在 .env 设置
`CLOUDFLARED_IMAGE=cloudflare/cloudflared@sha256:实际摘要`，禁止使用 latest。
原 RAG_BIND_IP、RAG_DOMAIN、RAG_HTTPS_PORT、CADDY_IMAGE 不再参与新配置。

在 GitHub 仓库打开 `Settings → Environments → steamdeck → Environment secrets`，
添加 Secret：名称 `CLOUDFLARED_TUNNEL_TOKEN`，值为 Cloudflare Tunnel 安装页面
`--token` 后面的完整令牌，不加引号，不填写整条 Docker 命令。

部署作业使用 `environment: steamdeck`，只在部署步骤将 Secret 映射到 TUNNEL_TOKEN。
部署脚本在锁内自动生成权限 600 的 cloudflared.env，原子替换后清除进程中的 TUNNEL_TOKEN。
不需要 SSH 手工配置令牌；真实令牌不会进入仓库、部署附件或脚本输出。
缺少或格式不正确的 Secret 时，预检退出，不覆盖现有令牌文件，也不停止现有服务。
更新或轮换令牌时，修改同名 Secret 并重新运行最新 main 的工作流。

同一 Environment 还支持以下 Secret（名称必须完全一致）：

| 名称 | 值 | 迁移行为 |
| --- | --- | --- |
| CLOUDFLARED_TUNNEL_TOKEN | Tunnel 安装页面的令牌 | 必填，每次部署自动写入 cloudflared.env |
| POSTGRES_PASSWORD | Deck 当前数据库密码 | 未填沿用 .env；填写时必须与现有数据库容器一致 |
| RAG_SECRET | Deck 当前生产签名密钥 | 未填沿用原配置；填写时必须与现有 API 容器一致 |
| RAG_EMBEDDING_API_KEY | 当前或新的 Embedding 服务 API Key | 未填沿用原配置；填写后自动更新 |

不要重新生成 POSTGRES_PASSWORD 或 RAG_SECRET 来代替不知道的旧值，迁移与轮换是不同操作。
数据库密码不能只修改容器配置；签名密钥变更会让既有 API 密钥和会话失效。
脚本会在停止服务前检查这两项是否一致，不一致则拒绝部署，不输出秘密值。
如果暂时不知道原值，先不添加对应 Secret，部署继续使用 Deck 上已有值，无需 SSH。

部署脚本生成权限 600 的 app-secrets.env 覆盖应用秘密；使用 raw 格式保留 $、引号、# 等字符。
已生成的覆盖值在 GitHub Secret 留空后仍保留；删除 Secret 不等于清除应用凭据。
所有 Secret 仅填原始单行值，不加引号，不填写 KEY= 前缀。
更新 Embedding API Key 时保持现有模型、维度及服务地址相匹配。
GITHUB_TOKEN 由 Actions 自动提供，不需要添加 PAT；原阿里云 Caddy 密钥已不再使用。

已有 `app.env` 的普通应用参数继续沿用，确认：

```dotenv
RAG_ENV=production
RAG_PUBLIC_URL=https://rag.xqtdevelopment.xyz
```

production 要求真实 Embedding、PostgreSQL 和随机签名密钥。账号注册保持开放。

## Cloudflare 后台

1. 将 xqtdevelopment.xyz 接入 Cloudflare DNS，核对原记录，更新注册商 NS 并等待 Active。
2. 创建正式的远程管理 Tunnel，选择 cloudflared，保存它的 Tunnel Token。
3. 添加 Published application route：域名 `rag.xqtdevelopment.xyz`，服务 `http://api:8000`，路径留空。
4. 如有旧 rag A/AAAA 记录，先记录其值，再移除冲突记录，由 Tunnel 创建代理 DNS 记录。
5. 不覆盖回源 Host，不在整个站点前设置交互式 Access 登录或 API/MCP 浏览器挑战。

## 启动与验收

日常部署通过仓库推送触发 GitHub Actions：在本机修改 Compose 和部署脚本、
通过检查后推送 main，由流水线发布应用镜像并将同次运行的部署附件交给 Deck runner。
不要用 SSH 直接覆盖 Deck 的 Compose 或手动更新运行容器。
Tunnel Token 在 GitHub 的 steamdeck Environment secrets 中管理，runner 自动写入 Deck。
其余三个 Secret 可分步迁移；未提供时保留已有配置，无需 SSH 手工填写。

令牌配置完成后，推送 main，或手动运行最新 main 的 `CI and Steam Deck deployment`。
确认 `STEAMDECK_DEPLOY_ENABLED=true`，并等待 deploy 作业成功。
以下命令只用于检查配置和运行状态：

```bash
cd /home/deck/rag-center-deploy
source ./docker-env.sh
docker compose --env-file .env --env-file release.env config --quiet
docker compose --env-file .env --env-file release.env ps cloudflared
```

正常部署脚本在停止 API/worker 前检查令牌格式、不可变镜像和 production 公网地址，
然后沿用备份、迁移和应用检查。连接器启动后从 API 内访问 `http://cloudflared:2000/ready`，
确认隧道连接成功，最后停止同项目旧 Caddy，并记录 CLOUDFLARED_IMAGE。
readiness 不代表公网 DNS、域名路由和证书都已验收。
首次部署后应继续做下述公网检查。

从手机移动网络检查首页、注册登录、上传、检索、下载以及带原 API 密钥的 `/mcp`。

```bash
curl --fail https://rag.xqtdevelopment.xyz/api/health
```

不要添加 -k。旧的 hosts、路由器本地 DNS 或代理 Fake-IP 规则可能仍把域名指向局域网，
须核对并清除过期覆盖。公网推广前应验证可信代理客户端 IP 和注册/登录限流，
避免全部用户共用连接器 IP 的限流桶。应用已对 /api/ 与 /mcp 返回 no-store，
Cloudflare 自定义规则也不能强制缓存这些路径。

迁移期间保留旧 Caddy 配置、证书目录及 caddy.env 的受保护备份；隧道未就绪前不要停旧入口。
后续确认阿里云密钥无其他用途，再撤销。不要用 down -v 或清理数据库解决连接问题。

参考：https://developers.cloudflare.com/tunnel/get-started/
参数：https://developers.cloudflare.com/tunnel/reference/run-parameters/
