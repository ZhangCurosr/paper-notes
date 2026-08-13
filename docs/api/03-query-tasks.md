# 03 任务查询与轮询

**功能**：任务列表（分页/状态过滤）、详情、重试、删除。

**端点**：`GET /v1/tasks` · `GET /v1/tasks/{id}` · `POST /v1/tasks/{id}/retry` · `DELETE /v1/tasks/{id}`

## 3.1 列表

```
GET /v1/tasks?limit=50&offset=0&status=done
```

| 参数 | 默认 | 范围 | 说明 |
| --- | --- | --- | --- |
| `limit` | 50 | 1~200 | 每页条数 |
| `offset` | 0 | ≥0 | 偏移 |
| `status` | 全部 | pending/submitted/done/failed | 状态过滤 |

```json
{"code": 0, "data": {"total": 87, "tasks": [
  {"task_id": "t_1", "status": "done", "source": "https://...", "channel": "v4",
   "created_at": 1725000000.0, "error": ""}
]}}
```

## 3.2 详情

```json
{"code": 0, "data": {
  "task_id": "t_1", "status": "submitted", "source": "https://...",
  "channel": "v4", "batch_id": "batch_x", "created_at": 1725000000.0,
  "finished_at": null, "progress": {"pages": 5}, "error": "", "downloaded": false
}}
```

| 字段 | 说明 |
| --- | --- |
| `progress` | 官方返回的进度（页数等），轮询时观察用 |
| `error` | 失败原因（截断 120 字符） |
| `downloaded` | `done` 后产物是否已就绪（false 时 result 端点返回"产物下载中"） |
| `channel` | `v4`（token 池）/ `flash`（免 token） |

## 3.3 状态机

```
pending ──调度──▶ submitted ──轮询──▶ done
   │                  │                 │
   │                  ▼                 ▼
   └──失败──▶ failed ◀──retry──▶ pending
```

- 单任务最多尝试 6 次（自动换 token），仍失败才 `failed`
- **retry 仅 failed 可用**；服务重启自动恢复未完成任务

## 3.4 重试与删除

```bash
curl -s -X POST -H "Authorization: Bearer sk-user-xxx" \
  https://mineru-api-sdwh.onrender.com/v1/tasks/t_1/retry
# {"code": 0, "data": {"task_id": "t_1", "status": "pending", "message": "已重置，等待重新提交"}}

curl -s -X DELETE -H "Authorization: Bearer sk-user-xxx" \
  https://mineru-api-sdwh.onrender.com/v1/tasks/t_1
# {"code": 0, "data": {"deleted": "t_1", "source": "https://..."}}
```

## 示例附件

```bash
python examples/mineru_api/query_tasks.py                 # 最近 10 条
python examples/mineru_api/query_tasks.py --status done --limit 5
python examples/mineru_api/query_tasks.py --detail t_1    # 详情 JSON
python examples/mineru_api/query_tasks.py --retry t_1     # 重试
python examples/mineru_api/query_tasks.py --delete t_1    # 删除
```

## 轮询策略建议

- 间隔 5s，总超时 30 分钟（长 PDF 解析可能 10+ 分钟）
- `downloaded=false` 时等待 3s 重试 result（产物下载 1~2 分钟）
- 批量场景用 `ThreadPoolExecutor`（见 [batch_parse.py](../../examples/mineru_api/batch_parse.py)）
