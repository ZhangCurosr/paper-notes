# 11 本地监控 Dashboard

**功能**：在本地电脑浏览器查看云端任务情况（零依赖单文件工具，代理拉取云端数据）。

**工具**：[local_dashboard.py](../../scripts/local_dashboard.py)

## 启动

```bash
set MINERU_API_KEY=sk-admin-xxx        # admin（看全局）或 user（看自己）key
python scripts/local_dashboard.py
# → 浏览器打开 http://127.0.0.1:8901
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--key` | 环境变量 `MINERU_API_KEY` / `MINERU_ADMIN_KEY` | API key |
| `--base` | `https://mineru-api-sdwh.onrender.com` | 云端地址 |
| `--port` | 8901 | 本地端口 |
| `--refresh` | 15 | 云端数据缓存秒数 |
| `--host` | 127.0.0.1 | 监听地址（默认仅本机，勿改） |

## 页面内容

| 区域 | 内容 |
| --- | --- |
| 顶部状态条 | 云端在线/离线、运行时长、调度策略、token 数、服务版本 |
| KPI 卡片 | 提交成功/失败、成功率、解析页数、延迟 p99、熔断中、配额暂停、429 冷却、今日剩余文件/优先页、flash 任务、API 请求数 |
| 24h 趋势图 | 任务量折线（canvas 手绘，无外部资源） |
| Token 池表 | 状态（active/熔断/暂停/冷却）、成功率、ok/err/429、延迟、preflight、今日剩余；点表头排序 |
| 错误分布 | err_dist 错误码分布 + fail_reasons 失败原因 Top10 |
| 最近任务 | 我的 key 任务列表（状态徽章、通道、来源、错误），按钮筛选 pending/submitted/done/failed |

自动刷新 15s（与缓存同步），也可手动刷新。

## 安全设计

- 仅监听 `127.0.0.1`，不暴露内网
- key 只从命令行/环境变量读取，**不落盘、不出现在页面**
- 浏览器不直连云端（云端 CORS 默认关闭）——所有请求经本地代理转发，天然绕过且无跨域风险
- 访问日志静默

## 实现要点

- 纯标准库（http.server + urllib），无 pip 依赖
- 云端请求带 5 次指数退避重试（Cloudflare 随机拦截兜底，与客户端库同策略）
- 本地缓存 15s：重复刷新不打爆免费层
