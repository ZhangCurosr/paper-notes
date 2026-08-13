# 🚀 paper-notes — 论文自动抓取 · 解析 · 归档流水线

**每天自动抓取 arXiv 与四大顶会的新论文，经 MinerU 云端解析后归档为完整论文笔记**（原版 PDF + 全文 Markdown + 论文图表 + 元数据），全程云端运行，无需本地机器。

<!-- 胶囊徽章带 -->
![Daily](https://img.shields.io/github/actions/workflow/status/ZhangCurosr/paper-notes/mineru_batch.yml?label=daily%20batch&style=flat-square)
![Dedup](https://img.shields.io/github/actions/workflow/status/ZhangCurosr/paper-notes/dedup_repos.yml?label=dedup&style=flat-square)
![Cron](https://img.shields.io/badge/Cron-02%3A00%20%2F%2014%3A00%20UTC-informational?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)

## 这是什么

- **抓取**：arXiv 4 个分类（cs.CL / cs.AI / cs.CV / cs.LG）+ 5 个会议源（ACL 2024 / EMNLP 2024 / NAACL 2024 / CVPR 2023 / CVPR 2024）
- **解析**：MinerU 云端 API（多 Token 池轮换调度，429 自适应退避，零依赖）
- **归档**：论文标题命名目录 → 9 个产物仓库（`zhangcursor-papers-*`），500MB 阈值自动开新仓
- **索引**：`zhangcursor-hub` 总厂库统一检索，去重 workflow 一键清理冗余

## 流水线架构

```
arXiv API / ACL Anthology / CVF Open Access
        │  fetch（每天 02:00 / 14:00 UTC cron 触发）
        ▼
   MinerU Token 池（10~30 个 token 轮换，限流自适应）
        │  提交 → 轮询 → 下载（zip → 裁剪产物）
        ▼
  github_sync（产物推送到 9 个论文仓库 + 追加 hub 索引）
        ▼
  zhangcursor-hub（统一索引）◀── Dedup Repos（手动去重重建）
```

| 组件 | 作用 |
|---|---|
| `arxiv_fetcher.py` | arXiv 分类抓取（去重 / 去重后保留当日新增） |
| `conference_fetcher.py` | 会议源抓取（ACL 家族 / CVF，含分页与重试） |
| `mineru_api_pool.py` | MinerU API 调度器：Token 池轮换、429 冷却、多线程提交/下载、断点续跑 |
| `github_sync.py` | 产物推送（自动开仓 / 分批 commit / 索引合并） |
| `hub_update.py` | 总厂库合并（同 source 覆盖 + 仓库名归一） |
| `dedup_repos.py` | 云端去重：同论文保留完整版，删除冗余目录 |
| `sync_hub_from_dedup.py` | 去重后重建 hub 索引 + README |

## 快速开始（Fork 部署）

1. **Fork** 本仓库（公开仓库即可，GitHub Actions 免费额度无限）
2. **配置 Secrets**（Settings → Secrets and variables → Actions）：

| Secret | 说明 |
|---|---|
| `MINERU_TOKENS` | MinerU API Token 列表，逗号分隔（越多越稳，建议 ≥10） |
| `GH_TOKEN` | 带 `repo` 权限的 GitHub Token（用于推送产物仓库） |

3. **运行**：Actions → `MinerU Daily Batch` → Run workflow（或等 cron 自动触发）

## 本地运行

```bash
pip install requests
# 抓取 → 解析 → 同步（需配置环境变量 MINERU_TOKENS / GH_TOKEN）
python scripts/arxiv_fetcher.py --category cs.CL --days 1 --max 250 --out logs/batch.json
python scripts/mineru_api_pool.py --url-file logs/batch_urls.txt --out-dir output/cs.CL --rate 25
python scripts/github_sync.py --dir output/cs.CL --prefix "zhangcursor-papers-arxiv-cl" \
  --gh-token "$GH_TOKEN" --index-out logs/index.json
```

## 产物格式

```
{日期}/{论文标题}_{batch8}/
├── paper.pdf      # 原版 PDF
├── full.md        # 解析全文 Markdown
├── images/        # 论文图表
└── meta.json      # 元数据（source / title / venue / created_at）
```

## Limits

- MinerU 服务不可达的论文源会被跳过（实测 mlr.press / neurips.cc / ojs.aaai.org 被拒）
- 单日单源抓取上限 250 篇；总厂库容量 500MB/仓，超限自动开新仓
- 解析为机器生成结果，公式 / 表格偶有误差，请以原文为准
- 产物为研究学习用途，论文版权归原作者/出版社所有

## License

MIT
