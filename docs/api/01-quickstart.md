# 01 快速开始（最小闭环）

**功能**：健康检查 → 提交 URL → 轮询 → 保存结果。10 秒跑通全流程。

**相关端点**：`GET /health` · `POST /v1/tasks` · `GET /v1/tasks/{id}` · `GET /v1/tasks/{id}/result`

## 步骤

### 1. 健康检查（无需鉴权）

```bash
curl -s https://mineru-api-sdwh.onrender.com/health
```

```json
{"code": 0, "data": {"service": "mineru-api-server", "version": "2.0", "endpoints": [...]}}
```

### 2. 提交任务

```bash
curl -s -X POST -H "Authorization: Bearer sk-user-xxx" -H "Content-Type: application/json" \
  -d '{"urls": ["https://arxiv.org/pdf/2409.18839.pdf"]}' \
  https://mineru-api-sdwh.onrender.com/v1/tasks
```

```json
{"code": 0, "data": {"task_ids": ["t_xxxx"], "reused_ids": [], "tasks": 1, "reused": 0}}
```

### 3. 轮询状态（约 30~120s）

```bash
curl -s -H "Authorization: Bearer sk-user-xxx" \
  https://mineru-api-sdwh.onrender.com/v1/tasks/t_xxxx
```

状态流转：`pending` → `submitted` → `done` / `failed`（失败可用 `POST /v1/tasks/{id}/retry` 重试）。

### 4. 取结果

```bash
curl -s -H "Authorization: Bearer sk-user-xxx" \
  https://mineru-api-sdwh.onrender.com/v1/tasks/t_xxxx/result
```

返回 `markdown` 全文 + `files` 清单（图片等），逐个 `GET /v1/tasks/{id}/file/{name}` 下载，或 `GET /v1/tasks/{id}/zip` 打包。

## 示例附件

```bash
python examples/mineru_api/quickstart.py "https://arxiv.org/pdf/2409.18839.pdf"
# 输出: 服务信息 → task_id → 轮询进度 → 保存 results/{task_id}.md + images/
```

内部依赖 [common.py](../../examples/mineru_api/common.py) 的 `call / wait_task / fetch_result / save_result` 四个函数，即完整工作流的四步封装。

## 注意事项

- Render 免费层冷启动 30~60s，期间可能 5xx——示例已自动重试
- 首次调用建议用 `--fresh` 之前的已完成 URL 会被去重复用（见 02 文档）
