# 06 用户用量与 key 管理

**功能**：用户自查用量（/v1/me）；admin 管理用户 key（创建/列表/删除）。

**端点**：`GET /v1/me` · `POST /v1/keys` · `GET /v1/keys` · `DELETE /v1/keys/{key}`

## 6.1 GET /v1/me —— 我的用量（user key）

```bash
curl -s -H "Authorization: Bearer sk-user-xxx" \
  https://mineru-api-sdwh.onrender.com/v1/me
```

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

| 字段 | 说明 |
| --- | --- |
| `tasks_by_channel` | v4=token 池 / flash=免 token 通道 |
| `api_requests` | 累计请求数（下载类不计） |
| `rate_limit_per_min` | 当前限流额度（默认 60/分钟） |

## 6.2 POST /v1/keys —— 创建用户 key（admin）

```bash
curl -s -X POST -H "Authorization: Bearer sk-admin-xxxx" -H "Content-Type: application/json" \
  -d '{"name": "alice"}' \
  https://mineru-api-sdwh.onrender.com/v1/keys
```

```json
{"code": 0, "data": {"key": "sk-<64位随机>", "name": "alice"}}
```

- `name` ≤32 字符
- 新 key 为 user 权限（无管理端点权限）
- **key 只在响应中出现一次**，分发给使用者后服务端不再明文可查

## 6.3 GET /v1/keys —— 列表（admin）

```json
{"code": 0, "data": {"keys": [
  {"key": "sk-admin-...", "name": "", "admin": true, "created_at": 172..., "tasks": 0},
  {"key": "sk-xxxx", "name": "alice", "admin": false, "created_at": 172..., "tasks": 12}
]}}
```

## 6.4 DELETE /v1/keys/{key} —— 删除（admin）

```json
{"code": 0, "data": {"deleted": "sk-xxxx"}}
```

删除后该 key 立即失效；其任务记录保留（不可再查询）。

## 示例附件

```bash
# 用户侧
python examples/mineru_api/user_me.py

# admin 侧（MINERU_API_KEY 用 admin key）
python examples/mineru_api/admin_keys.py create alice
python examples/mineru_api/admin_keys.py list
python examples/mineru_api/admin_keys.py delete sk-xxx
```

## 授权模型速览

| key 类型 | 可调端点 |
| --- | --- |
| user | `/v1/tasks*`、`/v1/crawl`、`/v1/me` |
| admin | 全部（含 `/v1/keys`、`/v1/stats*`、`/v1/metrics`） |

## 注意事项

- 限流按 key 独立计数（60/min）；下载类端点不计
- 同 IP 60s 内鉴权失败 ≥10 次 → IP 封禁 10 分钟（爆破防护）
