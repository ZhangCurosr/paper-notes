# 11 本地监控 Dashboard

**功能**：在本地电脑浏览器查看云端任务情况（零依赖单文件工具，本地代理拉取云端数据，自动打开浏览器）。

**工具**：[local_dashboard.py](../../scripts/local_dashboard.py) + [start_dashboard.bat](../../start_dashboard.bat)（Windows 双击启动）

## 一键启动（推荐）

```bash
# Windows：双击 start_dashboard.bat
# 首次运行输入 API key（可选保存到 %USERPROFILE%\.mineru_dashboard\config.json，之后免输入）
# 自动打开浏览器 http://127.0.0.1:8901
```

或命令行：

```bash
set MINERU_API_KEY=sk-admin-xxx        # admin（看全局）或 user（看自己）key
python scripts/local_dashboard.py
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--key` | 环境变量 `MINERU_API_KEY`/`MINERU_ADMIN_KEY` | API key（优先级：命令行 > 环境变量 > 配置文件） |
| `--config` | `%USERPROFILE%\.mineru_dashboard\config.json` | 配置文件（key/base），首次运行交互式创建 |
| `--base` | `https://mineru-api-sdwh.onrender.com` | 云端地址 |
| `--port` | 8901 | 本地端口 |
| `--refresh` | 15 | 缓存/自动刷新秒数 |
| `--host` | 127.0.0.1 | 监听地址（默认仅本机） |

## 页面内容（v2）

| 区域 | 内容 |
| --- | --- |
| 顶部 | 在线状态（时长/策略/token 数/版本）、自动刷新倒计时、主题切换、暂停自动刷新、手动刷新 |
| KPI 卡片（12 个） | 提交成功/失败、成功率、解析页数、延迟 p99、熔断/暂停/冷却、今日提交、剩余文件、flash 任务、API 请求数 |
| 配额进度条 ×2 | 今日文件（5000）与优先页（1000）用量 + 剩余，超 70%/85% 变色预警 |
| 24h 趋势图 | 折线 + 渐变面积 + 网格 + **悬停提示**（canvas 手绘） |
| 错误分布 | err_dist 横向条形图 + 失败原因 Top10 |
| Token 池表 | **搜索过滤**、表头排序（▲▼）、成功率进度条、状态徽章、preflight、今日剩余/提交、**导出 CSV** |
| 最近任务 | 状态分段按钮（带计数）、**来源搜索**、相对时间/耗时、**点击行弹窗看详情**（进度/错误/batch/时间线）、**导出 CSV** |
| 其他 | 暗/亮主题（localStorage 记忆）、刷新加载动画、toast 提示 |

## 安全设计

- 仅监听 `127.0.0.1`，不暴露内网
- key 只从命令行/环境变量/用户目录配置文件读取（**不写入仓库、不出现在页面**）；配置文件在你确认后才创建
- 浏览器不直连云端（云端 CORS 默认关闭）——所有请求经本地代理转发
- 访问日志静默

## 实现要点

- 纯标准库（http.server + urllib），零 pip 依赖；前端单页内嵌，无外部 CDN
- 云端请求带 5 次指数退避重试（Cloudflare 随机拦截兜底）
- 本地缓存 `--refresh` 秒：重复刷新不打爆免费层；`/api/refresh` 可强制清缓存
- 新增代理端点：`/api/config`、`/api/task/{id}`（任务详情）、`/api/tasks`、`/api/tokens`、`/api/errbox`、`/api/overview`
