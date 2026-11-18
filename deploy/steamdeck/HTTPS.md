# Steam Deck 公网 HTTPS（Cloudflare Tunnel）

公网地址为 `https://rag.xqtdevelopment.xyz`。Cloudflare 负责边缘 HTTPS，
同一 Compose 项目中的 cloudflared 主动建立加密隧道，回源地址为 `http://api:8000`。
API 主机端口固定绑定 127.0.0.1:8000；数据库与连接器均无主机端口。
不再需要 Caddy、阿里云 DNS AccessKey、443 低端口权限或路由器端口转发。

## 环境变量

保留 `/home/deck/rag-center-deploy/.env` 中的 POSTGRES_PASSWORD。
Compose 默认固定官方 cloudflared 2026.11.1 的镜像摘要；如需更新，可在 .env 设置
`CLOUDFLARED_IMAGE=cloudflare/cloudflared@sha256:实际摘要`，禁止使用 latest。
原 RAG_BIND_IP、RAG_DOMAIN、RAG_HTTPS_PORT、CADDY_IMAGE 不再参与新配置。

在同一目录创建权限 600 的 `cloudflared.env`，填入 Cloudflare Tunnel 安装页面
`--token` 后面的完整令牌，不加引号，不写整条 Docker 命令：

```dotenv
TUNNEL_TOKEN=填写真实的隧道令牌
```

令牌只在 Deck 本地保存，不提交仓库，不写 GitHub Actions 变量或日志。
已有 `app.env` 保留签名密钥、数据库和 Embedding 设置，确认：

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
Deck 本地只需预先配置 cloudflared.env 等秘密；缺少令牌时部署预检失败，不停止现有写服务。

令牌配置完成后，推送 main，或手动运行最新 main 的 `CI and Steam Deck deployment`。
确认 `STEAMDECK_DEPLOY_ENABLED=true`，并等待 deploy 作业成功。
以下命令只用于检查配置和运行状态：

```bash
cd /home/deck/rag-center-deploy
source ./docker-env.sh
chmod 600 .env app.env cloudflared.env
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
