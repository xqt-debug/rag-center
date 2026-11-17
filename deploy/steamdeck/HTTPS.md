# Steam Deck 局域网 HTTPS

目标地址：`https://rag.xqtdevelopment.xyz`。Caddy 使用阿里云 DNS-01 验证，
自动申请、续期公开可信证书。无需公网 IP、路由器端口转发或云服务器。
Deck 只拉取 GitHub 构建的应用和 Caddy 镜像，均使用同次 workflow 发布的摘要。

## 首次启用

1. 在阿里云 DNS 添加 A 记录：主机记录 `rag`，记录值为 Deck 的固定局域网 IP。
   若 DNS 重绑定保护或代理 Fake-IP 导致解析异常，在路由器本地 DNS 设置相同记录，
   并让代理对该域名直连。证书的 TXT 验证记录仍然由 Caddy 在公网 DNS 自动管理。
2. 创建专用 RAM 用户，创建 AccessKey，授予以下自定义策略。密钥只写入 Deck，
   不要放进聊天、仓库或 Actions 日志。该策略只允许插件需要的 DNS API：

   ```json
   {
     "Version": "1",
     "Statement": [{
       "Effect": "Allow",
       "Action": [
         "alidns:DescribeDomains",
         "alidns:DescribeDomainRecords",
         "alidns:AddDomainRecord",
         "alidns:UpdateDomainRecord",
         "alidns:DeleteDomainRecord"
       ],
       "Resource": "*"
     }]
   }
   ```

3. Deck 的 Rootless Docker 若不能绑定 443，需要一次管理员配置：

   ```bash
   sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443
   echo 'net.ipv4.ip_unprivileged_port_start=443' | sudo tee /etc/sysctl.d/90-rag-https.conf
   ```

   这允许普通用户绑定 443 及更高端口。若不想修改系统设置，则在下一步设置
   `RAG_HTTPS_PORT=8443`，并使用 `https://rag.xqtdevelopment.xyz:8443`。
   Compose 要求版本 >= 2.24.4（HTTPS 配置使用 `!override` 收回 API 的 LAN 端口）。

4. 保留 `/home/deck/rag-center-deploy` 原有数据库、文件和所有密钥。
   使用编辑器修改现有文件，避免覆盖原来的配置：

   `.env` 添加：

   ```dotenv
   RAG_DOMAIN=rag.xqtdevelopment.xyz
   RAG_HTTPS_PORT=443
   ```

   `app.env` 修改以下两项，保留其他配置及原来的 `RAG_SECRET`：

   ```dotenv
   RAG_ENV=production
   RAG_PUBLIC_URL=https://rag.xqtdevelopment.xyz
   ```

   新建 `caddy.env`，填入真实值：

   ```dotenv
   ALIYUN_ACCESS_KEY_ID=填写专用RAM用户的AccessKeyID
   ALIYUN_ACCESS_KEY_SECRET=填写对应的AccessKeySecret
   ```

   ```bash
   chmod 600 /home/deck/rag-center-deploy/{.env,app.env,caddy.env}
   ```

   `caddy.env` 的存在代表启用 HTTPS。请在配置全部完成后再触发部署。
   production 同时要求已有的真实 Embedding 配置、PostgreSQL 和有效的签名密钥。

5. 在 GitHub Actions 手动运行最新 main 的 `CI and Steam Deck deployment`，
   或推送该版本。原有 `STEAMDECK_DEPLOY_ENABLED=true` 及 Deck runner 需保持可用。
   第一次编译 Caddy 插件可能较慢，之后有构建缓存。

## 部署与验收

- 启用 HTTPS 后：预先拉取和验证 Caddy、检查凭证非空、应用地址和端口，
  首次启动前检查主机端口权限，再停止写服务、备份、迁移、启动应用。
- API 的 8000 端口仅绑定主机回环地址；HTTPS 的 443 只绑定 `RAG_BIND_IP`。
  不发布 80 或 Caddy 管理端口。不启用记录完整请求 URL 的访问日志。
- 最后启动 Caddy，等待证书并验证证书信任链、域名和代理后的健康接口。
  最多尝试 60 次；失败不记录成功发布，写服务保持停止，保留备份供排查。
- `caddy-data/` 保存证书、私钥和 ACME 账户，`caddy-config/` 保存 Caddy 状态。
  更新镜像不会删除这些目录；不要删掉它们来解决普通配置问题。
  原有备份会另外保存 Caddy 配置和 caddy.env；证书目录需另做受保护的备份。
- `release.env` 保存应用和 Caddy 的精确镜像摘要。临时部署附件清理后服务仍正常运行。
- 浏览器重新访问 HTTPS 域名并登录。旧 MCP 配置需要更新成 HTTPS 域名下的 `/mcp`，
  使用原有 API 密钥即可。浏览器若有旧页面缓存，强制刷新。

查看 Caddy 日志（不要把含密钥的完整环境文件或 Compose 展开配置发出来）：

```bash
cd /home/deck/rag-center-deploy
set -a
source release.env
set +a
docker compose --env-file .env -f compose.yaml -f compose.https.yaml logs --tail 80 caddy
```

检查 DNS / HTTPS：

```bash
nslookup rag.xqtdevelopment.xyz
curl --fail --resolve rag.xqtdevelopment.xyz:443:192.168.31.72 https://rag.xqtdevelopment.xyz/api/health
```

如果 Deck IP 不同，替换上面的 IP。不要加 `-k`，否则无法验证证书信任。
公网 DNS 指向私有 IP 不会让外网设备获得该局域网的访问能力。

## 暂不启用 HTTPS

没有 `caddy.env` 时继续走原有 HTTP 部署，不会因缺少 DNS 密钥而中断已有服务。
已经运行过 Caddy 后如需退回 HTTP，先手动停止 Caddy、移走 caddy.env，恢复
app.env 的原 HTTP 地址及 development 模式，再重新部署；证书目录保留。

参考：
- https://caddyserver.com/docs/automatic-https
- https://github.com/caddy-dns/alidns
- https://github.com/libdns/alidns
- https://docs.docker.com/engine/security/rootless/troubleshoot/
