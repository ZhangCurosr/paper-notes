# 论文笔记流水线（Paper Notes Pipeline）

PDF 解析全自动流水线：**零服务器成本**，基于 GitHub Actions 定时调度 + GitHub 多仓库存储，把论文/文档批量转换为可检索的 Markdown 知识库。

## 能力

- 🚀 **arXiv 论文批量采集**：免费 API 按分类/日期/关键词拉取论文 → 自动生成解析任务
- ⚡ **Token 池并发调度**：多 token 滑动窗口限速、429 冷却、日配额感知（5000 文件/1000 页/天）、断点续跑
- 🌐 **免 token 双通道**：小文件自动走 flash agent API（≤10MB），服务异常自动回落 v4
- 📦 **多仓库存储**：产物（Markdown+版面 JSON）按容量阈值自动开新仓库 + 索引映射
- 🖥️ **可选常驻 API**：Docker 镜像 + Render 免费 Web Service（15min 休眠 + 自动保活）

## 快速开始（全自动）

### 1. 部署

```bash
git clone https://github.com/ZhangCurosr/mineru-pipeline.git
cd mineru-pipeline
```

### 2. 配置 Secrets（仓库 Settings → Actions）

| Secret | 内容 |
|---|---|
| `MINERU_TOKENS` | 逗号分隔的 MinerU API token（账号级共享 5000 文件/天） |
| `GH_TOKEN` | GitHub PAT（repo 权限，用于自动建产物仓库） |

### 3. 触发

- 自动：每天 02:00 / 14:00 UTC（`.github/workflows/mineru_batch.yml` cron）
- 手动：Actions 页面 `workflow_dispatch`

### 4. 产物落点

```
mineru-{分类}-001/   ← 自动创建的 public 仓库
  └─ 2026-08-12/
      └─ {paper_id}/
          ├─ full.md               ← 完整 Markdown（公式 LaTeX/图片引用）
          ├─ layout.json           ← 版面分析（页数统计）
          └─ content_list.json     ← 内容结构化（RAG 直用）
mineru-index/        ← 索引仓库（url → 仓库/路径 映射）
```

## 本地快速试跑

```bash
pip install requests

# 拉 1 天 cs.CL 论文并解析（需 MINERU_TOKENS 环境变量）
python scripts/arxiv_fetcher.py --category cs.CL --days 1 --max 50 --out logs/b.json
python scripts/mineru_api_pool.py --url-file logs/b_urls.txt --out-dir output/ --rate 40

# 推送产物到 GitHub 多仓库（演练模式）
python scripts/github_sync.py --dir output/ --prefix mineru-cs.CL \
    --gh-token ghp_xxx --dry-run
```

## 架构

```
GitHub Actions cron ──→ arxiv_fetcher → mineru_api_pool（token 池并发）
     │                                        │
     └── github_sync ──→ gh repo create（自动建仓）→ push 产物
                            └→ mineru-index 索引更新
```

详见 `docs/DEPLOY_FREE.md`（平台调研/容量规划/成本核算）与 `docs/mineru_api_research.md`（API 实测报告）。

## 安全说明

- 真实凭据（token/密码）一律走 Secrets 或本地 `.mineru_secret`，**永不入库**
- `mineru_accounts.csv.example` 为格式示例（无真实值）
