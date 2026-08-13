# 11 本地监控 Dashboard

**功能**：监控云端 MinerU API 任务情况（零依赖，本地代理拉取数据，自动打开浏览器）。

**工具**：[local_dashboard.py](../../scripts/local_dashboard.py) + [start_dashboard.bat](../../start_dashboard.bat)（Windows 双击启动）

## 两种访问方式

### 方式 A：云端直连（无需本地进程，手机也能看）

```
浏览器打开 https://mineru-api-sdwh.onrender.com/dashboard
→ 页面右上角输入 API key（admin/user 均可，保存在浏览器 localStorage）
```

云端服务内嵌同一份 Dashboard 页面（`GET /dashboard`，同源直连 `/v1/*`，无 CORS 问题）。
数据接口全部仍需 Bearer 鉴权，页面本身无敏感数据。

### 方式 B：本地代理（推荐日常使用）

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

## 页面内容（v2.2 Tab 布局）

| Tab | 内容 |
| --- | --- |
| **总览** | 告警横幅、12 个 KPI（数字动画）、配额进度条 ×2、24h 趋势（悬停提示）、错误分布条形图 + 失败原因 |
| **Token 池** | 搜索/排序/成功率进度条/状态徽章/点击详情弹窗（完整健康档案）/导出 CSV |
| **任务** | 状态分段（带计数）/来源搜索/相对时间/详情弹窗（重试·删除·复制）/导出 CSV |
| **历史** | 7/14/30 天任务量折线 + 每日配额柱状 + 每日明细表（数据来自云端 GitHub 归档） |

其他：暗/亮主题、刷新间隔可调（5/15/30/60s）、自动刷新倒计时、窗口标题离线提示、KPI 数字滚动动画。

## 历史数据（云端存储）

- **采集**：GitHub Actions `history_archive.yml` 每小时（+手动）拉云端 `/v1/stats` → 追加到仓库 `data/history/<日期>.jsonl`（GitHub 即历史存储，云端服务重启不丢失）
- **读取**：云端 `GET /v1/history?days=N`（admin）从 raw.githubusercontent 拉取聚合，缓存 5 分钟；本地代理 `/api/history` 透传
- **依赖**：`secrets.MINERU_ADMIN_KEY`（已配置）

## 一键启动（本地）

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
