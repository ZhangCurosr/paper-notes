# 08 错误处理与限流

**功能**：错误码体系、限流/封禁规则、Cloudflare 随机拦截应对、重试策略。

## 8.1 响应与 HTTP 状态

```json
{"code": 0, "data": {...}}      // 成功
{"code": 400, "msg": "..."}     // 失败（业务错误）
```

| HTTP | 常见场景 |
| --- | --- |
| 400 | JSON 非法 / URL 被 SSRF 拒绝 / 文件名含路径或扩展名不白名单 / 超 100MB / limit 非数字 / 单次 >50 |
| 401 | key 无效 / 需要管理员权限 / 限流超 60 次每分钟 / IP 封禁 |
| 404 | 路由不存在 / 任务不存在或非本人 / 产物文件不存在 |
| 500 | 服务内部错误（详见响应 msg） |

## 8.2 官方错误码（服务内部已消化，但 `err_dist`/`fail_reasons` 可见）

| 错误码 | 含义 | 池的处理 |
| --- | --- | --- |
| `-60018` / `-60019` | 账号日配额耗尽 | 暂停该 token 12h，自动换下一个 |
| `-10002` | 模型版本字段不支持 | 新 token 默认不传 `model_version` |
| `429` | 官方限流 | 冷却 30s 重试 |
| `401` | token 无效 | 暂停 30 天 |
| 5xx / timeout | 官方服务异常 | 计入 network 失败，超阈值熔断 |

> 用户侧提交任务基本不会直接看到这些码——池已自动换 token 重试（单任务最多 6 次）。

## 8.3 限流与封禁（调用方需要遵守）

| 规则 | 值 |
| --- | --- |
| 每 key 请求上限 | **60 次/分钟**（`GET /v1/me` 可查实际值） |
| 下载类端点 | result/file/zip **不计**额度 |
| 超限表现 | 401 `请求过于频繁（限 60/分钟）` |
| IP 封禁 | 60s 内鉴权失败 ≥10 次 → 封禁 10 分钟 |

**对策**：批量场景控制并发 ≤4（每任务 1 次提交 + 每 5s 1 次轮询，60/min 很宽裕）；不要在循环里高频无意义轮询。

## 8.4 Cloudflare 随机拦截（Render 免费层特有）

免费层前置 Bot Fight Mode，**非浏览器流量随机**返回 400/404/429/5xx。官方客户端（[common.py](../../examples/mineru_api/common.py)）已内置对策：

```python
RETRY_CODES = (400, 404, 429, 500, 502, 503, 504)

def call(method, path, body=None, timeout=120, retries=5, key=None):
    ...
    for attempt in range(retries):
        try:
            resp = urllib.request.urlopen(req, data=data, timeout=timeout)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in RETRY_CODES and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))    # 指数退避
                continue
            raise RuntimeError(f"HTTP {e.code}: ...")
```

要点：
- UA 用 `python-requests/2.32.3 (mineru-api-client)` 风格（实测通过率最高）
- 对上述状态码**退避重试 5 次**（`1.5s × attempt`）
- 仍失败时打印完整响应再排查（可能是真业务错误）

## 8.5 自研调用模板

```python
import json, time, urllib.error, urllib.request

BASE, KEY = "https://mineru-api-sdwh.onrender.com", "sk-user-xxx"

def call(method, path, body=None, timeout=120, retries=5):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("User-Agent", "python-requests/2.32.3 (mineru-api-client)")
    data = json.dumps(body).encode() if body is not None else None
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for attempt in range(retries):
        try:
            return json.loads(urllib.request.urlopen(req, data=data, timeout=timeout).read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404, 429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise RuntimeError("重试耗尽")
```

## 示例附件

```bash
# 完整错误处理 + 重试封装（其余示例均依赖它）
python examples/mineru_api/../mineru_api/batch_parse.py ./docs   # 批量场景含失败自动 retry
```
