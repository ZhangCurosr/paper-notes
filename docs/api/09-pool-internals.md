# 09 调度池内部机制（了解即可，调用方无感）

**功能**：`mineru_api_pool.py` 多 token 调度池的行为说明——加权调度、熔断、配额暂停、健康检查、失败分类、预热探测。

> 用户侧无需感知：提交后服务端自动选择健康 token、自动换 token 重试、自动恢复。本文用于监控与排障。

## 9.1 调度策略

| 策略 | 说明 | 适用 |
| --- | --- | --- |
| `weighted` | 平滑加权轮询（new-api 风格），按动态权重分配，天然防热点 | **线上默认**（Render `MINERU_STRATEGY=weighted`） |
| `rr` | 简单轮转，实例级指针（修复过"永远选中 slots[0]"的 bug） | 少量同质 token |
| `score` | 成功率 + 延迟健康度评分 | 质量差异大的 token 池 |

**动态权重**：`0.5 + 成功率×1.5`，钳位 `[0.5, 2.0]`——成功率高的 token 拿更多任务，失败自动降权。

## 9.2 熔断（连续失败指数退避）

- 连续失败 ≥`ban_threshold`（默认 5）→ 该 token 进入 `banned`：指数退避禁用（30s→60s→120s→…）
- 健康检查线程每 `health_interval`（默认 300s）探测，通过后自动恢复（`banned_now` 归零）

## 9.3 失败分类（关键设计）

| 类别 | 触发 | 处理 |
| --- | --- | --- |
| quota（配额耗尽） | 官方 `-60018/-60019`（HTTP 200 + 业务码） | **暂停 12h 不熔断**，换下一个 token |
| auth（key 无效） | 401/403 | 暂停 30 天（基本弃用） |
| network（网络异常） | 5xx/timeout/连接失败 | 计数，≥5 次熔断 |
| parse（解析失败） | 官方返回业务失败（文件损坏等） | 不计成败，不熔断 |
| 429 | 官方限流 | 冷却 30s 重试 |

> 单任务最多尝试 `MAX_ATTEMPTS=6`，每次换 token/通道；重试期间 URL 任务不重复扣配额（除非 fresh）。

## 9.4 预热探测（preflight）

启动时对每个 token 发 `GET /api/v4/quota` 验权：

- `preflight.ok` = 有效 token 数（线上 445 全 ok）
- `preflight.bad` = 无效 token（应替换，如被官方封禁/过期）
- `preflight.skip` = 未测（健康检查关闭时）

`/v1/stats/tokens` 可看每个 token 的 preflight 与状态。

## 9.5 每日配额（官方限制）

| 配额 | 值 | 说明 |
| --- | --- | --- |
| 文件数 | 5000/天/账号 | `daily.files_left` 实时可见 |
| 优先页数 | 1000/天/账号 | 超出转普通排队 |

- 池级 `daily` 汇总：`submits`/`pages` 当日累计、`files_left`/`pages_priority_left` 剩余
- 配额耗尽 token 自动暂停 12h（`suspended_now`），**次日 00:00 UTC 自动恢复**——无需人工干预
- `quota_warn_tokens` >0 提示有 token 接近配额上限

## 9.6 flash 免 token 通道

- ≤10MB 本地文件、`flash=true` 的 URL 自动走 flash agent API（不消耗 token 配额）
- 限频：IP 级 20 次/分钟（`flash.rate_per_min`）
- 失败自动回落 v4 通道（`flash.fallback_to_v4` 计数），用户无感

## 9.7 监控指标映射

| 场景 | 看哪里 |
| --- | --- |
| 服务是否健康 | `/health`、`/v1/stats`（uptime/banned_now/preflight） |
| token 池是否够用 | `daily.files_left`、`quota_warn_tokens` |
| 是否有失效 token | `/v1/stats/tokens` 中 `preflight=false` / `auth_failed>0` |
| 最近失败原因 | `stats.fail_reasons` Top10 |
| 延迟趋势 | `latency_ms` p50/p90/p99（无样本为 null） |
| 错误码分布 | `err_dist`（-60018 堆积=配额问题） |
