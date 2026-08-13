# MinerU 全自动部署（免费云方案）

## 架构决策（2026-08 调研结论）

| 平台 | 免费能力 | 适合角色 | 结论 |
|---|---|---|---|
| **GitHub Actions**（public 仓库） | **无限分钟** + cron（≥5min 间隔）+ 并发 job + 自带 gh CLI | **主方案：定时批处理** | ✅ 零成本全自动 |
| **Render** 免费 Web Service | 750h/月，15min 无流量休眠（冷启动 30-60s） | 备选：常驻 API | ✅ 可用（休眠可接受） |
| **Hugging Face Spaces** CPU Basic | 免费 2核/16GB，但**出站 HTTPS 有超时风险**（社区报告） | 不推荐（mineru 出站是关键路径） | ⚠️ |
| **Oracle Always Free** ARM | 4核/24GB 永久免费 VPS | 最强备选（注册难、需绑卡） | ✅ 注册成功则最优 |

**推荐组合：GitHub Actions（批处理）+ GitHub 多仓库（存储）——完全免费、无服务器依赖。**

## 为什么 Actions 而不是常驻 API

我们的核心工作流是"定时批量解析"，不是实时交互：
- arXiv 每天新论文数量有限 → 每天跑 2 次即可覆盖
- Actions public 仓库免费**无限分钟** + **并行 job**（分类×日期矩阵）→ 6 小时/次上限内可跑完数千篇
- 产物天然在 GitHub（存储即交付），无需额外同步层
- 缺点：无常驻 HTTP API（如需 API 服务，见下文 Render 方案）

## 一、GitHub Actions 主方案

### 1. Secrets 配置（仓库 Settings → Secrets and variables → Actions）

| Secret | 内容 | 用途 |
|---|---|---|
| `CSV_PASSPHRASE` | `mineru_accounts.csv.gpg` 的解密口令（AES256 对称加密） | workflow 解密后**全量加载 CSV 中所有 token**（当前 255+，持续扩充自动生效） |
| `GH_TOKEN` | 带 `repo` 权限的 Personal Access Token | 创建/推送产物仓库 |

> **凭据管理**：`mineru_accounts.csv`（含 259 个账号的邮箱/手机/密码/api_key）**不入库**，
> 加密为 `mineru_accounts.csv.gpg` 入库（`gpg --symmetric --cipher-algo AES256`）。
> 扩充账号后重新加密上传即可，云端自动用全量 token，无需再改 secret。

### 2. 工作流文件（已提供 `.github/workflows/mineru_batch.yml`）

```
每天 02:00 / 14:00 UTC 自动触发（也可手动 workflow_dispatch）
  ├─ job 矩阵：cs.CL / cs.AI / cs.LG / cs.LG... 各 500 篇
  ├─ arxiv_fetcher：拉论文 URL（最近 1 天）
  ├─ mineru_api_pool：并发解析（token 池控速）
  └─ github_sync：产物打包 push 到 mineru-{分类}-{序号} 仓库
```

### 3. 存储容量规划（GitHub 仓库限制）

| 产物 | 单篇大小 |
|---|---|
| full.md | ~138KB |
| layout.json + content_list | ~200KB |
| **只存 md+json**（丢弃 zip/图片可选） | **~350KB/篇** |

- 单仓建议 <1GB（软限制），单文件 <100MB（硬限制）
- 500 篇/批 × 350KB ≈ **175MB** → 每分类每天 1 个仓库安全
- `github_sync.py` 自动：目录超阈值 → `gh repo create mineru-{prefix}-{n}` → push → 索引仓库记录映射
- 多仓库命名：`mineru-cs.CL-001`、`mineru-cs.AI-002`...

### 4. 完全自动化流程

```
GitHub Actions cron ──→ fetch arXiv → 批量解析（token 池）
     │                                   │
     │                                   ▼
     └── github_sync.py ──→ gh repo create（新仓库）→ git push 产物
                              └→ 索引仓库更新（url→仓库/路径 映射）
```

## 二、Render 常驻 API 方案（可选，如需对外提供 API）

### ✅ 已部署（2026-08-13）

- 服务地址：**https://mineru-api-sdwh.onrender.com**
- 状态：服务在线（/health 200），256 token 全量加载（MINERU_TOKENS env），admin key 已配（MINERU_ADMIN_KEY env）
- 心跳：`Render Heartbeat` workflow 每 10 分钟保活（RENDER_SERVICE_URL variable 已设）
- 使用：见下方 curl 示例；**客户端需用脚本 UA + 自动重试**（Render 前置 Cloudflare Bot Fight 会随机拦截非浏览器流量，`scripts/mineru_api_client.py` 已内置）
- 已知限制：MinerU v4 通道每日 5000 任务配额（按日重置）；flash 免 token 通道偶发服务端不可用（回落 v4 兜底）

### 部署资产（已就绪）

| 文件 | 作用 |
|---|---|
| `deploy/Dockerfile` | python:3.11-slim 镜像，仅装 requests，跑 `mineru_api_server.py` |
| `render.yaml`（仓库根目录） | Render Blueprint 配置（免费 Web Service + 健康检查 /health） |
| `.github/workflows/heartbeat.yml` | 每 10 分钟 curl /health 保活（防 15min 休眠冷启动） |
| `scripts/mineru_api_server.py` v2.0 | token 池 + flash 免 token 双通道 HTTP 服务 |

### 部署步骤（约 10 分钟）

1. 注册 Render：https://render.com → Sign up（GitHub 账号登录最快，免费）
2. Dashboard → **New → Blueprint** → 连接 GitHub → 选择 `ZhangCurosr/paper-notes`
   （public 仓库，自动读到根目录 render.yaml，无需手动选服务类型）
3. 在 Blueprint 预览页（或 Service → Environment）设置两个 Secret：
   - `MINERU_TOKENS`：逗号分隔的 MinerU API token（可放 10-60 个，建议 30+）
   - `MINERU_ADMIN_KEY`：自定义管理员 key（如 `sk-admin-<32位随机>`，**重启后仍有效**）
4. **Apply** → 自动构建（Docker 镜像拉取约 1-2 分钟）→ 部署完成
5. 验证：浏览器打开 `https://{服务名}.onrender.com/health`（应返回 `mineru-api-server 2.0`）
6. （可选）仓库 Settings → Variables → 新建 `RENDER_SERVICE_URL` = 服务地址
   → heartbeat workflow 自动保活（不设也不影响，默认 URL 可改 workflow）

### 使用 API

```bash
# 创建用户 key（admin）
curl -X POST https://{svc}.onrender.com/v1/keys -H "Authorization: Bearer $ADMIN_KEY" \
     -d '{"name":"alice"}'
# 提交解析任务（用户 key）
curl -X POST https://{svc}.onrender.com/v1/tasks -H "Authorization: Bearer $USER_KEY" \
     -d '{"urls":["https://arxiv.org/pdf/2608.00001"]}'
# 查询状态 / 取结果
curl https://{svc}.onrender.com/v1/tasks/{id} -H "Authorization: Bearer $USER_KEY"
curl https://{svc}.onrender.com/v1/tasks/{id}/result -H "Authorization: Bearer $USER_KEY"
```

### 注意事项

- **免费层磁盘非持久**：重启后 `state.json`/产物丢失，任务记录清空；因此：
  - admin key 必须用 `MINERU_ADMIN_KEY` 环境变量注入（已支持）
  - 重要产物仍以 GitHub Actions 同步到仓库为准，API 服务仅作临时取用
- **免费层限流**：750 小时/月 ≈ 31 天（心跳 10min 一次约 48h/月，剩余 ~700h 够用）
- **冷启动**：休眠后首个请求 ~50s 才响应，心跳保活可避免（建议开启）
- **出站网络**：Render 免费层通常可直连 mineru.net / arxiv.org（若遇网络限制，用 Actions 主通道，API 仅作内部取用）

## 三、成本核算（全免费）

| 项目 | 成本 |
|---|---|
| GitHub Actions（public） | ¥0（无限分钟） |
| GitHub 存储（多仓库，md+json） | ¥0（<1GB/仓） |
| Render（若启用 API） | ¥0（750h/月内） |
| MinerU API | ¥0（当前无收费计划，账号级配额） |

## 四、注意事项

1. **Actions 6h/次上限**：单 job 500 篇 × ~40s ≈ 6h 边缘 → 用矩阵拆小（每 job 250-300 篇）
2. **GitHub Push Protection**：token 等敏感信息绝不能进产物仓库（mineru_accounts.csv 不入库）
3. **限额监控**：Actions 产物仓库满 1GB 前自动开新仓（github_sync 阈值可配）
4. **配额感知**：调度池已内置 5000 文件/天 + 1000 页/天追踪，Actions 里同样生效
5. **出站网络**：Actions runner 无出站限制（arXiv/mineru.net 均可直连）
