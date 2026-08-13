# MinerU API 功能文档目录

> 按功能拆分的手册，每篇配可直接运行的 Python 示例附件（`examples/mineru_api/`）。
> 总参考（全部端点/字段）：`docs/mineru_api_docs.md`

## 快速开始

```bash
set MINERU_API_KEY=sk-user-xxx          # Windows
export MINERU_API_KEY=sk-user-xxx       # Linux/Mac
python examples/mineru_api/quickstart.py "https://arxiv.org/pdf/2409.18839.pdf"
```

## 文档分册

| # | 功能 | 文档 | 示例附件 |
| --- | --- | --- | --- |
| 01 | 快速开始（最小闭环） | [01-quickstart.md](01-quickstart.md) | [quickstart.py](../../examples/mineru_api/quickstart.py) |
| 02 | 任务提交（URL/文件/参数） | [02-submit-tasks.md](02-submit-tasks.md) | [submit_url.py](../../examples/mineru_api/submit_url.py) · [submit_files.py](../../examples/mineru_api/submit_files.py) |
| 03 | 任务查询与轮询 | [03-query-tasks.md](03-query-tasks.md) | [query_tasks.py](../../examples/mineru_api/query_tasks.py) |
| 04 | 结果获取与下载 | [04-download-results.md](04-download-results.md) | [download_results.py](../../examples/mineru_api/download_results.py) |
| 05 | 网页爬取转 Markdown | [05-crawl.md](05-crawl.md) | [crawl.py](../../examples/mineru_api/crawl.py) |
| 06 | 用户用量与 key 管理 | [06-user-and-keys.md](06-user-and-keys.md) | [user_me.py](../../examples/mineru_api/user_me.py) · [admin_keys.py](../../examples/mineru_api/admin_keys.py) |
| 07 | 管理与监控（统计） | [07-admin-stats.md](07-admin-stats.md) | [admin_stats.py](../../examples/mineru_api/admin_stats.py) |
| 08 | 错误处理与限流 | [08-errors-rate-limit.md](08-errors-rate-limit.md) | [common.py](../../examples/mineru_api/common.py) |
| 09 | 调度池内部机制 | [09-pool-internals.md](09-pool-internals.md) | - |
| 10 | 本地自建部署 | [10-local-deploy.md](10-local-deploy.md) | - |

## 示例附件公共依赖

全部示例共用 [common.py](../../examples/mineru_api/common.py)（鉴权、重试、轮询、保存）。各示例用法：

```bash
# 从仓库根目录运行
python examples/mineru_api/quickstart.py https://example.com/a.pdf
python examples/mineru_api/submit_url.py https://example.com/b.pdf
python examples/mineru_api/submit_files.py ./docs
python examples/mineru_api/query_tasks.py --status done
python examples/mineru_api/download_results.py t_xxx --zip
python examples/mineru_api/crawl.py https://example.com/article
python examples/mineru_api/user_me.py
python examples/mineru_api/admin_keys.py create alice
python examples/mineru_api/admin_stats.py --tokens
python examples/mineru_api/batch_parse.py ./papers
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MINERU_API_KEY` | （必填） | API key（user 或 admin） |
| `MINERU_API_BASE` | `https://mineru-api-sdwh.onrender.com` | 服务地址 |
