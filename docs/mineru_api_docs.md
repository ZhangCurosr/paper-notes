# MinerU API 服务文档 v2.0

> 基于 `scripts/mineru_api_server.py`（HTTP/1.1 多线程服务）＋ `scripts/mineru_api_pool.py`（token 调度池）＋ `scripts/mineru_api_client.py`（官方客户端示例）。
> 线上示例：`https://mineru-api-sdwh.onrender.com`（Render 免费层，冷启动约 30~60s，由 heartbeat workflow 保活）。

---

## 1. 服务总览

| 项 | 说明 |
| --- | --- |
| 服务地址 | `https://mineru-api-sdwh.onrender.com`（本地默认 `http://127.0.0.1:8900`） |
| 协议 | HTTPS / HTTP JSON（下载类端点返回二进制） |
| 鉴权 | 请求头 `Authorization: Bearer <key>` |
| key 类型 | **admin key**（全部端点，含管理）；**user key**（任务/查询/下载） |
| 版本 | `v2.0`（`GET /` 可查） |

### 1.1 鉴权体系

| key 类型 | 来源 | 权限 |
| --- | --- | --- |
| `sk-admin-*` | 部署时 `MINERU_ADMIN_KEY` 环境变量 | 全部端点（含 `/v1/keys` 用户管理、`/v1/stats*` 统计） |
| `sk-<随机>` | `POST /v1/keys` 由 admin 创建 | 任务提交/查询/下载、`/v1/me`、`/v1/crawl` |

**限流**：user key 默认 **60 次/分钟**（部署参数 `--key-rate`，`GET /v1/me` 可查当前值）；下载类端点（`result` / `file` / `zip`）不计额度。超限返回 `401` + `请求过于频繁（限 60/分钟）`。

**IP 封禁**：同一 IP 60 秒内鉴权失败 ≥10 次 → 封禁 10 分钟（爆破防护）。

---

## 2. 通用约定

### 2.1 响应格式

```json
// 成功
{"code": 0, "data": { ... }}
// 失败
{"code": 400, "msg": "具体错误信息"}
```

| HTTP 状态 | 含义 |
| --- | --- |
| 200 | 请求成功（业务结果看 `code`，`code=0` 即成功） |
| 400 | 参数错误（JSON 非法 / URL 被 SSRF 拒绝 / 文件名不合法 / 超限等） |
| 401 | 鉴权失败 / 限流 / IP 封禁 |
| 404 | 路由或任务/文件不存在 |
| 500 | 服务内部错误 |

### 2.2 提交到云端被 Cloudflare 随机拦截

Render 免费层前置 Cloudflare Bot Fight Mode，对非浏览器流量**随机**返回 400/404/429/5xx。官方客户端已内置**指数退避重试**（5 次，`RETRY_CODES`）。自研调用建议：

- UA 使用 `python-requests/2.32.3 (mineru-api-client)` 风格；
- 对 400/404/429/500/502/503/504 重试 3~5 次，退避 `1.5s × attempt`；
- 出现"任务不存在"或 code≠0 时打印完整响应再排查。

---

## 3. 端点速查表

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/` `/health` | 无 | 服务信息 / 健康检查 |
| POST | `/v1/tasks` | user | 提交 URL / 本地文件解析 |
| GET | `/v1/tasks` | user | 我的任务列表（分页/状态过滤） |
| GET | `/v1/tasks/{id}` | user | 任务详情（状态机） |
| POST | `/v1/tasks/{id}/retry` | user | 重试 failed 任务 |
| GET | `/v1/tasks/{id}/result` | user | 解析结果（markdown + 文件清单） |
| GET | `/v1/tasks/{id}/file/{name}` | user | 下载产物文件（图片/单文件） |
| GET | `/v1/tasks/{id}/zip` | user | 下载全部产物 zip（≤1GB） |
| DELETE | `/v1/tasks/{id}` | user | 删除任务记录 + 产物 |
| POST | `/v1/crawl` | user | 网页转 Markdown（model=html） |
| GET | `/v1/me` | user | 我的用量概览 |
| POST | `/v1/keys` | admin | 创建用户 key |
| GET | `/v1/keys` | admin | key 列表 |
| DELETE | `/v1/keys/{key}` | admin | 删除 key |
| GET | `/v1/stats` | admin | 全局统计 + token 池健康 |
| GET | `/v1/stats/tokens` | admin | 各 token 完整明细 |
| GET | `/v1/stats/trends` | admin | 按小时/按天任务趋势 |
| GET | `/v1/metrics` | admin | Prometheus 文本指标 |

---

## 4. 端点详解

### 4.1 GET /health（健康检查）

```bash
curl -s https://mineru-api-sdwh.onrender.com/health
```

```json
{"code": 0, "data": {"service": "mineru-api-server", "version": "2.0", "endpoints": ["POST /v1/tasks", "..."]}}
```

### 4.2 POST /v1/tasks —— 提交解析任务（核心）

**请求体**（JSON）：

```json
{
  "urls": ["https://arxiv.org/pdf/2409.18839.pdf"],
  "files": [
    {"name": "论文.pdf", "data": "<base64>", "pages": "1-10", "ocr": true, "data_id": "doc_001"}
  ],
  "formula": true,
  "table": true,
  "ocr": true,
  "language": "zh",
  "pages": "1-10,15",
  "extra_formats": ["docx"],
  "model": "pipeline",
  "fresh": false,
  "flash": false
}
```

**参数详解**：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `urls` | string[] | - | 公网 http/https URL（**SSRF 防护**：内网/元数据/保留段一律拒绝）。`urls`/`files` 至少一项，合计 ≤50 |
| `files[].name` | string | - | 文件名，**禁止含 `/` `\` 路径分隔符**，扩展名白名单：`.pdf .doc .docx .ppt .pptx .xls .xlsx .png .jpg .jpeg .webp .bmp .tif .tiff`，单文件 ≤100MB |
| `files[].data` | string | - | 文件内容 **base64** |
| `files[].pages` | string | - | 单文件页范围，如 `1-10,15` |
| `files[].ocr` | bool | - | 单文件强制 OCR |
| `files[].data_id` | string | - | 自定义标识 |
| `formula` | bool | false | 公式识别 |
| `table` | bool | false | 表格识别 |
| `ocr` | bool | false | OCR（扫描件） |
| `language` | string | - | 语言，`zh`/`en` 等 |
| `pages` | string | - | 全部 URL 的页范围（文件级优先） |
| `extra_formats` | string[] | - | 额外导出格式，如 `["docx"]` |
| `model` | string | - | `pipeline`（默认）/ `vlm` / `html` |
| `fresh` | bool | false | `true` 强制重新解析；`false` 命中同 key 已完成 URL 时**自动复用**（见 6.3） |
| `flash` | bool | false | URL 走 **flash 免 token 通道**（≤10MB 本地文件自动走 flash，见 6.4） |

**响应**：

```json
{"code": 0, "data": {
  "task_ids": ["t_xxxx1", "t_xxxx2"],
  "reused_ids": ["t_prev_1"],
  "tasks": 2,
  "reused": 1
}}
```

> `reused_ids`：命中去重缓存的任务（`fresh=false` 时）。`flash=true` 的任务 channel 为 `flash`。

**Python**（直接调用）：

```python
import base64, json, urllib.request

BASE = "https://mineru-api-sdwh.onrender.com"
KEY  = "sk-user-xxxx"

def call(method, path, body=None, timeout=60, retries=5):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("User-Agent", "python-requests/2.32.3 (mineru-api-client)")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
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

# 提交 URL
r = call("POST", "/v1/tasks", {"urls": ["https://arxiv.org/pdf/2409.18839.pdf"],
                               "formula": True, "table": True})
assert r["code"] == 0, r
task_ids = r["data"]["task_ids"]
print(task_ids)

# 提交本地文件（base64）
with open("论文.pdf", "rb") as f:
    r2 = call("POST", "/v1/tasks", {"files": [{"name": "论文.pdf",
                                                "data": base64.b64encode(f.read()).decode()}]})
```

### 4.3 GET /v1/tasks —— 我的任务列表

| 查询参数 | 默认 | 说明 |
| --- | --- | --- |
| `limit` | 50 | 1~200 |
| `offset` | 0 | 非负整数 |
| `status` | 全部 | `pending` / `submitted` / `done` / `failed` |

```bash
curl -s -H "Authorization: Bearer sk-user-xxx" \
  "https://mineru-api-sdwh.onrender.com/v1/tasks?limit=10&status=done"
```

```json
{"code": 0, "data": {"total": 87, "tasks": [
  {"task_id": "t_xxxx", "status": "done", "source": "https://...", "channel": "v4",
   "created_at": 1725000000.0, "error": ""}
]}}
```

### 4.4 GET /v1/tasks/{id} —— 任务详情

```bash
curl -s -H "Authorization: Bearer sk-user-xxx" \
  https://mineru-api-sdwh.onrender.com/v1/tasks/t_xxxx
```

```json
{"code": 0, "data": {
  "task_id": "t_xxxx", "status": "done", "source": "https://...",
  "channel": "v4", "batch_id": "batch_xxx", "created_at": 1725000000.0,
  "finished_at": 1725000120.0, "progress": {"pages": 12},
  "error": "", "downloaded": true
}}
```

### 4.5 POST /v1/tasks/{id}/retry —— 重试失败任务

仅 `failed` 可重试；重置为 `pending` 重新排队（换 token 再战）。

```json
{"code": 0, "data": {"task_id": "t_xxxx", "status": "pending", "message": "已重置，等待重新提交"}}
```

### 4.6 GET /v1/tasks/{id}/result —— 解析结果

任务 `done` 且产物下载完成后返回：

```json
{"code": 0, "data": {
  "task_id": "t_xxxx", "status": "done", "source": "https://...", "channel": "v4",
  "files": [{"name": "images/img_1.png", "size": 12345}, {"name": "full.md", "size": 1024}],
  "markdown": "完整 Markdown 全文..."
}}
```

> `latency_ms` 无样本时为 `null`；`daily` 为官方配额实时余量（5000 文件/天、1000 优先页/天）。

### 4.7 GET /v1/tasks/{id}/file/{name} —— 下载产物文件

`name` 为 result 中 `files[].name`（支持 `images/xxx.png` 子路径；路径穿越防护：必须落在产物目录内）。

```bash
curl -s -H "Authorization: Bearer sk-user-xxx" -o img.png \
  "https://mineru-api-sdwh.onrender.com/v1/tasks/t_xxxx/file/images/img_1.png"
```

### 4.8 GET /v1/tasks/{id}/zip —— 产物打包下载

产物总量 ≤1GB；返回 `application/zip`，文件名 `{task_id}_result.zip`。

```bash
curl -s -H "Authorization: Bearer sk-user-xxx" -o result.zip \
  https://mineru-api-sdwh.onrender.com/v1/tasks/t_xxxx/zip
```

### 4.9 DELETE /v1/tasks/{id} —— 删除任务

```json
{"code": 0, "data": {"deleted": "t_xxxx", "source": "https://..."}}
```

### 4.10 POST /v1/crawl —— 网页转 Markdown

等价于 `POST /v1/tasks` + `model=html`；`urls` 1~20 个。

```bash
curl -s -X POST -H "Authorization: Bearer sk-user-xxx" -H "Content-Type: application/json" \
  -d '{"urls": ["https://example.com/article"]}' \
  https://mineru-api-sdwh.onrender.com/v1/crawl
```

### 4.11 GET /v1/me —— 我的用量

```json
{"code": 0, "data": {
  "key": "sk-user-xxx...", "name": "user",
  "tasks_total": 87,
  "tasks_by_status": {"done": 80, "failed": 5, "pending": 2},
  "tasks_by_channel": {"v4": 85, "flash": 2},
  "tasks_by_model": {"pipeline": 87},
  "api_requests": 1234,
  "rate_limit_per_min": 60
}}
```

### 4.12 管理端点（admin）

**创建用户 key**：

```bash
curl -s -X POST -H "Authorization: Bearer sk-admin-xxxx" -H "Content-Type: application/json" \
  -d '{"name": "alice"}' \
  https://mineru-api-sdwh.onrender.com/v1/keys
# {"code": 0, "data": {"key": "sk-<64hex>", "name": "alice"}}
```

**key 列表 / 删除**：

```bash
curl -s -H "Authorization: Bearer sk-admin-xxxx" \
  https://mineru-api-sdwh.onrender.com/v1/keys
curl -s -X DELETE -H "Authorization: Bearer sk-admin-xxxx" \
  https://mineru-api-sdwh.onrender.com/v1/keys/sk-xxxx
```

**全局统计**（`GET /v1/stats`）：

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

**token 明细**（`GET /v1/stats/tokens`，admin）：按 `parse_ok` 降序，每个 token 含前缀（打码）、`preflight`、`status`（active/cooling/suspended/banned）、`weight`、`success_rate`、`ok/err/rate_limited/suspended` 计数、`latency_ms`、`err_codes` 等。

**趋势**（`GET /v1/stats/trends`，admin）：`{"by_hour": {"08-13 10": 12, ...}, "by_day": {"08-13": 87}}`。

**Prometheus 指标**（`GET /v1/metrics`，admin）：

```text
# TYPE mineru_tokens gauge
mineru_tokens 445
# TYPE mineru_ok counter
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

---

## 5. 任务状态机

```
pending ──调度──▶ submitted ──轮询──▶ done
   │                  │                 │
   │                  ▼                 ▼
   └──失败──▶ failed ◀──retry──▶ pending
```

| 状态 | 含义 |
| --- | --- |
| `pending` | 已接受，排队等待 token 池调度 |
| `submitted` | 已提交给 MinerU 官方 API，轮询进度中 |
| `done` | 解析成功，结果产物已下载到本地（`downloaded=true` 后可取） |
| `failed` | 最终失败（重试 6 次仍失败）；可用 retry 重置 |

**自动行为**：
- 单任务最多尝试 **6 次**（`MAX_ATTEMPTS`），每次换 token/换通道；
- 失败原因记录在任务 `error`（截断 120 字符）与统计 `fail_reasons`；
- 服务重启后自动**恢复未完成任务**（pending/submitted 继续跑，done 但产物缺失重新下载）。

---

## 6. token 调度池机制（了解即可，用户无感）

| 机制 | 行为 |
| --- | --- |
| 调度策略 | `weighted` 平滑加权轮询（Render 线上）/ `rr` 轮转 / `score` 成功率+延迟健康度 |
| 动态权重 | `0.5 + 成功率×1.5`，钳位 `[0.5, 2.0]`；成功率高的 token 拿更多任务 |
| 熔断 | 连续失败 ≥5 次 → 指数退避禁用（30s→60s→120s→…），健康检查自动恢复（5 分钟一次） |
| 配额暂停 | 官方 `-60018/-60019`（日配额耗尽）→ 该 token 暂停 **12 小时**，自动换下一个 token（不熔断） |
| 429 限流 | 触发后冷却 30 秒再试 |
| 失败分类 | auth（无效 key，暂停 30 天）/ network（5 次熔断）/ parse（不计成败）/ quota（暂停不熔断） |
| 预热探测 | 启动时对每个 token 发 `GET /api/v4/quota` 验权（`preflight: ok/bad/skip`） |
| 每日配额 | 官方 5000 文件/天、1000 页优先/天（`daily` 字段实时可见剩余） |
| flash 通道 | ≤10MB 本地文件 / `flash=true` URL 走免 token 的 flash agent API（IP 级 20 次/分钟），失败自动回落 v4 |

**错误码对照**（来自官方 API，已由池消化，用户一般看不到）：

| 错误码 | 含义 | 池的处理 |
| --- | --- | --- |
| `-60018` / `-60019` | 账号日配额耗尽 | 暂停该 token 12h，换下一个 |
| `-10002` | 模型版本字段不支持 | 新注册 token 不传 `model_version`（已默认规避） |
| `429` | 请求过频 | 冷却 30s 重试 |
| `401` | token 无效 | 该 token 暂停 30 天 |
| 5xx / timeout | 官方服务异常 | 计入 network 失败，超阈值熔断 |

---

## 7. 安全机制（自带，无需调用方处理）

| 防护 | 说明 |
| --- | --- |
| SSRF | 仅公网 `http/https`；`localhost`/`169.254.169.254`/内网/保留 IP 段一律拒绝（DNS 解析后校验） |
| 上传路径穿越 | 文件名含 `/` `\` 直接 400；扩展名白名单；≤100MB |
| 产物下载穿越 | `safe_join` realpath 边界校验 |
| Zip 安全 | 服务端解压防 Zip Slip + 单文件 512MB/总量 2GB 上限；产物 zip 打包 ≤1GB |
| 鉴权爆破 | IP 60s 内 10 次失败封禁 10 分钟 |
| CORS | 默认关闭，`--cors` 显式开启（防浏览器滥用） |
| 审计日志 | 仅记录非 2xx（含客户端 IP），防日志膨胀 |
| 结果 URL 校验 | 下载结果仅允许公网 https，拒绝内网/元数据域名 |

---

## 8. 完整工作流示例

### 8.1 Python：提交 → 轮询 → 取结果 → 下载图片

```python
"""一步到位：解析 PDF，等待完成，保存 markdown 与图片"""
import base64, json, os, sys, time, urllib.error, urllib.request

BASE = "https://mineru-api-sdwh.onrender.com"
KEY  = "sk-user-xxxx"
OUT  = "./results"

def call(method, path, body=None, timeout=120, retries=5):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("User-Agent", "python-requests/2.32.3 (mineru-api-client)")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
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

def parse_pdf(url, **opts):
    """提交单个 URL，轮询到终态，返回结果 dict"""
    r = call("POST", "/v1/tasks", {"urls": [url], **opts})
    assert r["code"] == 0, r
    tid = r["data"]["task_ids"][0]
    print(f"task: {tid}")

    t0 = time.time()
    while time.time() - t0 < 1800:
        d = call("GET", f"/v1/tasks/{tid}")
        st = d["data"]["status"]
        print(f"  [{int(time.time()-t0)}s] {st}")
        if st in ("done", "failed"):
            break
        time.sleep(5)

    if st == "failed":
        raise RuntimeError(f"任务失败: {d['data']['error']}")

    # 等产物下载完成
    for _ in range(120):
        res = call("GET", f"/v1/tasks/{tid}/result")
        if res["data"].get("downloaded", True):
            break
        time.sleep(3)
    return tid, res["data"]

def save(tid, data, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{tid}.md"), "w", encoding="utf-8") as f:
        f.write(data["markdown"])
    for meta in data["files"]:
        name = meta["name"]
        if name.startswith("images"):           # 下载图片
            req = urllib.request.Request(f"{BASE}/v1/tasks/{tid}/file/{name.replace(chr(92), '/')}")
            req.add_header("Authorization", f"Bearer {KEY}")
            p = os.path.join(out_dir, name.replace("/", "_").replace(chr(92), "_"))
            with open(p, "wb") as f:
                f.write(urllib.request.urlopen(req, timeout=60).read())
            print(f"  图片: {name} → {p}")

if __name__ == "__main__":
    tid, data = parse_pdf("https://arxiv.org/pdf/2409.18839.pdf",
                          formula=True, table=True, language="zh")
    save(tid, data, OUT)
    print(f"完成: {len(data['markdown'])} 字符 markdown, {len(data['files'])} 个文件")
```

### 8.2 批量：并发处理多文件

```python
from concurrent.futures import ThreadPoolExecutor
import base64, json, urllib.request

# 复用 8.1 的 call()（线程安全：每次独立 Request）

def submit_file(path):
    with open(path, "rb") as f:
        body = {"files": [{"name": path.split("/")[-1],
                           "data": base64.b64encode(f.read()).decode()}]}
    r = call("POST", "/v1/tasks", body)
    assert r["code"] == 0, r
    return r["data"]["task_ids"][0]

paths = ["a.pdf", "b.pdf", "c.pdf"]      # ≤50 一批
with ThreadPoolExecutor(max_workers=3) as ex:
    ids = list(ex.map(submit_file, paths))
print("已提交:", ids)
```

### 8.3 curl 最小闭环

```bash
# 1) 提交
TASK=$(curl -s -X POST -H "Authorization: Bearer sk-user-xxx" -H "Content-Type: application/json" \
  -d '{"urls":["https://arxiv.org/pdf/2409.18839.pdf"]}' \
  https://mineru-api-sdwh.onrender.com/v1/tasks | python -c "import sys,json;print(json.load(sys.stdin)['data']['task_ids'][0])")
echo "task=$TASK"

# 2) 轮询到 done（约 30~120s）
for i in $(seq 1 60); do
  ST=$(curl -s -H "Authorization: Bearer sk-user-xxx" \
    https://mineru-api-sdwh.onrender.com/v1/tasks/$TASK | python -c "import sys,json;print(json.load(sys.stdin)['data']['status'])")
  echo "[$i] $ST"; [ "$ST" = "done" -o "$ST" = "failed" ] && break; sleep 5
done

# 3) 取 markdown + 打包下载
curl -s -H "Authorization: Bearer sk-user-xxx" \
  https://mineru-api-sdwh.onrender.com/v1/tasks/$TASK/result | python -m json.tool
curl -s -H "Authorization: Bearer sk-user-xxx" -o result.zip \
  https://mineru-api-sdwh.onrender.com/v1/tasks/$TASK/zip
```

---

## 9. 官方客户端脚本（mineru_api_client.py）

```bash
# 提交 URL 并等待结果（自动轮询 + 保存 markdown + 图片）
python scripts/mineru_api_client.py --key sk-user-xxx --base https://mineru-api-sdwh.onrender.com \
  --urls "https://arxiv.org/pdf/2409.18839.pdf" --out ./results

# 批量解析本地目录文件
python scripts/mineru_api_client.py --key sk-user-xxx --input-dir ./docs --out ./results

# 只提交不等待（拿 task_id 自行轮询）
python scripts/mineru_api_client.py --key sk-user-xxx --urls "https://a.pdf" --no-wait

# 网页转 markdown
python scripts/mineru_api_client.py --key sk-user-xxx --crawl "https://example.com/page"

# 高级参数
python scripts/mineru_api_client.py --key sk-user-xxx --urls "https://a.pdf" \
  --pages "1-10" --extra-formats docx --fresh --formula --table --language zh

# 查询 / 重试
python scripts/mineru_api_client.py --key sk-user-xxx --task-id t_xxxx
python scripts/mineru_api_client.py --key sk-user-xxx --task-id t_xxxx --retry
```

---

## 10. 本地自建部署

```bash
# 依赖：python3 + requests（token 池用）；服务端纯标准库
python scripts/mineru_api_server.py \
  --host 0.0.0.0 --port 8900 \
  --admin-key sk-admin-$(python -c "import secrets;print(secrets.token_hex(16))") \
  --tokens "$(cat mineru_tokens.txt | tr '\n' ',' )" \
  --strategy weighted --key-rate 60
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--strategy` | `rr`（Render: `weighted`） | `rr` 轮转 / `weighted` 平滑加权 / `score` 健康度 |
| `--ban-threshold` | 5 | 连续失败熔断阈值 |
| `--health-interval` | 300 | 健康检查间隔秒（0=关） |
| `--rate` | 40 | 每 token 每分钟提交数 |
| `--key-rate` | 60 | 每用户 key 每分钟请求数 |
| `--model` | pipeline | pipeline / vlm / html |
| `--flash-rate` | 20 | flash 免 token 通道限频（/min） |
| `--no-flash` | - | 禁用 flash 通道 |
| `--cors` | 关 | 显式开启 CORS |
| `--data-dir` | `data/` | keys/state/uploads 持久化目录 |

> 环境变量：`MINERU_TOKENS`（逗号分隔）、`MINERU_ADMIN_KEY`、`MINERU_STRATEGY`、`MINERU_BAN_THRESHOLD`、`MINERU_HEALTH_INTERVAL`。

---

*文档版本 1.0（2026-08）· 与 `mineru_api_server.py` v2.0 对齐 · 生成自实测端点行为*
