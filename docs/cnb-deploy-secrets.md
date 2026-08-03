# GitHub ↔ CNB 双端部署：密钥配置

手动点 GitHub Actions **Run workflow** 时：

1. 部署国外服务器（现有 `SSH_*` secrets，不变）
2. 把当前 `main` 强制推到 [cnb.cool/emoera/PLC-Sim](https://cnb.cool/emoera/PLC-Sim)
3. CNB 流水线（[`.cnb.yml`](../.cnb.yml)）再 rsync 到国内机 `81.69.12.254`

OPC UA 客户端地址仍是 `opc.tcp://81.69.12.254:4855/xuse_sim`（或域名），与部署路径无关。

## 1. GitHub Actions Secrets

仓库：https://github.com/raoyi971102-gif/PLC-Sim/settings/secrets/actions

| Name | 必填 | 说明 |
|---|---|---|
| `CNB_TOKEN` | 是 | cnb.cool 个人访问令牌，需对 `emoera/PLC-Sim` 有写权限 |
| `CNB_USERNAME` | 否 | HTTPS 推送用户名，默认 `cnb`；按 CNB 文档填你的用户名亦可 |

国外部署继续用已有的 `SSH_HOST` / `SSH_USER` / `SSH_KEY`（及可选 `SSH_PORT`）。

## 2. CNB 密钥仓库

1. 在 cnb.cool 创建**密钥仓库**，建议名：`emoera/plc-sim-secrets`
2. Web 界面新建文件 `deploy-cn.yml`（不可本地 git push 进密钥仓）：

```yaml
LOGIN_USER: root
PRIVATE_KEY: |
  -----BEGIN OPENSSH PRIVATE KEY-----
  ...粘贴能登录 81.69.12.254 的私钥全文...
  -----END OPENSSH PRIVATE KEY-----
allow_slugs:
  - emoera/PLC-Sim
allow_images:
  - tencentcom/rsync
allow_branches:
  - main
```

3. 确认 [`.cnb.yml`](../.cnb.yml) 里的 `imports` 路径与密钥仓一致：
   `https://cnb.cool/emoera/plc-sim-secrets/-/blob/main/deploy-cn.yml`

国内机若已放入 `~/.ssh/opcuasim_deploy.pub`，可复用对应私钥；否则生成新密钥对并写入 `root` 的 `authorized_keys`。

## 3. 验证

1. 推送到 GitHub `main` → 只跑 `test`
2. Actions → **OpcUaSim CI / 手动部署** → **Run workflow**
3. `deploy`（国外）与 `sync_cnb` 都应绿
4. CNB 构建页出现 `main` push 流水线且绿
5. `curl -fsS https://opcua.emoera.cn/api/version` 中 `release` 为新短 SHA
