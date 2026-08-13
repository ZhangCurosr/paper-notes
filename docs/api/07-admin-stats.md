# 07 管理与监控（统计）

**功能**：token 池健康、全局统计、per-token 明细、趋势、Prometheus 指标。全部 admin 权限。

**端点**：`GET /v1/stats` · `GET /v1/stats/tokens` · `GET /v1/stats/trends` · `GET /v1/metrics`

## 7.1 GET /v1/stats —— 全局统计

```json
{"code": 0, "data": {
  "uptime": 86400,
  "tokens": {
    "tokens": 445, "rate": 40, "strategy": "weighted",
    "ok": 512, "err": 3, "rate_limited": 0, "suspended": 12,
    "parse_ok": 512, "parse_fail": 0, "pages_parsed": 3120,
    "bytes_uploaded": 104857600, "cooling": 0, "suspended_now": 0,
    "banned_now": 0, "auth_failed": 0,
    "avg_success_rate": 0.997, "avg_weight": 1.2,
    "err_dist": {"-60018": 3},
    "latency_ms": {"p50": 180, "p90": 350, "p99": 900},
    "preflight": {"ok": 445, "bad": 0, "skip": 0},
    "daily": {"date": "2026-08-13", "submits": 512, "files_left": 4488,
              "files_limit": 5000, "pages": 3120, "pages_priority_left": 1000,
              "pages_priority_limit": 1000, "quota_warn_tokens": 0}
  },
  "quota": {...}, "quota_obs": [...],
  "flash": {"rate_per_min": 20, "tasks": 15, "fallback_to_v4": 2},
  "tasks": {"total": 87, "done": 80, "failed": 5, "pending": 2},
  "stats": {"tasks_total": 87, "by_status": {...}, "by_channel": {...},
            "by_model": {...}, "pages_parsed": 3120, "uploads_bytes": 0,
            "api_requests": 1234, "fail_reasons": {"-60018": 3}},
  "trends_24h": {"08-12 12": 5, "08-13 10": 12},
  "users": 3
}}
```

**关键字段解读**：

| 字段 | 含义 | 健康标准 |
| --- | --- | --- |
| `tokens.preflight` | 启动预热探测结果 | `bad` 应为 0（bad=无效 token） |
| `tokens.banned_now` | 当前熔断 token 数 | 应为 0（熔断=连续失败，自动恢复） |
| `tokens.suspended_now` | 配额暂停中 token 数 | 12h 自动恢复，<10% 可接受 |
| `tokens.cooling` | 429 冷却中 | 30s 自动恢复 |
| `tokens.err_dist` | 错误码分布 | 应无 `-60018` 大量堆积 |
| `tokens.latency_ms` | 提交延迟分位（ms） | p99 <2000 正常；无样本为 null |
| `tokens.daily` | 官方每日配额实时余量 | `files_left`/`pages_priority_left` 为剩余 |
| `stats.fail_reasons` | 最近失败原因 Top10 | 排查配额/网络问题 |
| `flash.fallback_to_v4` | flash 失败回落 v4 次数 | 频繁回落说明 flash 通道不稳 |

## 7.2 GET /v1/stats/tokens —— token 明细

每个 token 的完整状态（按解析成功数降序）：`key`（打码前缀）、`preflight`、`status`（active/cooling/suspended/banned）、`weight`、`success_rate`、`ok/err/rate_limited/suspended` 计数、`latency_ms`、`err_codes`、`daily_submits`、`files_left` 等。

**用途**：定位低效/失效 token——`preflight=false` 或 `auth_failed>0` 即失效，应从台账替换。

## 7.3 GET /v1/stats/trends —— 趋势

```json
{"code": 0, "data": {
  "by_hour": {"08-12 12": 5, "08-13 09": 8, "08-13 10": 12},
  "by_day": {"08-12": 20, "08-13": 87}
}}
```

## 7.4 GET /v1/metrics —— Prometheus

`text/plain; version=0.0.4` 格式，可直接接入 Grafana/Prometheus：

```text
mineru_tokens 445
mineru_ok 512
mineru_err 3
mineru_rate_limited 0
mineru_suspended 12
mineru_banned_now 0
mineru_auth_failed 0
mineru_parse_ok 512
mineru_parse_fail 0
mineru_pages_parsed 3120
mineru_bytes_uploaded 104857600
mineru_avg_success_rate 0.997
mineru_latency_p99 900
mineru_api_requests 1234
mineru_tasks_total 87
```

## 示例附件

```bash
python examples/mineru_api/admin_stats.py               # 摘要（一行一项）
python examples/mineru_api/admin_stats.py --tokens      # token 明细
python examples/mineru_api/admin_stats.py --trends      # 趋势
python examples/mineru_api/admin_stats.py --metrics     # Prometheus 原文
```
