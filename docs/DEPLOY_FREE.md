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
| `MINERU_TOKENS` | 逗号分隔的 mineru API token（可只放 10-50 个，账号级配额） | 调度池 token |
| `GH_TOKEN` | 带 `repo` 权限的 Personal Access Token | 创建/推送产物仓库 |

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

1. 用仓库内 `deploy/Dockerfile` + `deploy/render.yaml`
2. Render 免费 Web Service：15min 无流量休眠，冷启动 ~50s
3. **保活**：上面 Actions workflow 加一个 job 每 10 分钟 `curl https://{svc}.onrender.com/health`（免费，顺带当心跳）
4. 状态/产物仍由 Actions 定期 push 到 GitHub

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
