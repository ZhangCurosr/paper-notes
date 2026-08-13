# 04 结果获取与下载

**功能**：结果 JSON（markdown + 文件清单）、单文件下载、zip 打包、任务清理。
下载类端点（result/file/zip）**不占限流额度**。

**端点**：`GET /v1/tasks/{id}/result` · `GET /v1/tasks/{id}/file/{name}` · `GET /v1/tasks/{id}/zip`

## 4.1 result —— 结果 JSON

```bash
curl -s -H "Authorization: Bearer sk-user-xxx" \
  https://mineru-api-sdwh.onrender.com/v1/tasks/t_1/result
```

```json
{"code": 0, "data": {
  "task_id": "t_1", "status": "done", "source": "https://...", "channel": "v4",
  "files": [{"name": "images/img_1.png", "size": 12345},
            {"name": "images/img_2.png", "size": 6789},
            {"name": "full.md", "size": 1024}],
  "markdown": "全文 Markdown..."
}}
```

> `markdown` 即解析全文（含图片引用路径）。`files` 中 `images/` 前缀为图片产物。
> 任务未完成返回 `{"status": "pending", "message": "任务未完成"}`；产物下载中返回 `downloaded: false`，稍后重试。

## 4.2 file —— 单文件下载

```bash
curl -s -H "Authorization: Bearer sk-user-xxx" -o img1.png \
  "https://mineru-api-sdwh.onrender.com/v1/tasks/t_1/file/images/img_1.png"
```

- `name` = result 中 `files[].name`（子路径用 `/`，如 `images/img_1.png`）
- **路径穿越防护**：`name` 必须解析在产物目录内，否则 404
- 内容类型：图片 `image/jpeg`、md `text/markdown`、其他 `application/octet-stream`

## 4.3 zip —— 全部产物打包

```bash
curl -s -H "Authorization: Bearer sk-user-xxx" -o result.zip \
  https://mineru-api-sdwh.onrender.com/v1/tasks/t_1/zip
```

- 返回 `application/zip`，文件名 `{task_id}_result.zip`
- **产物总量 >1GB 拒绝**（400），按文件逐个下载

## 4.4 删除任务

```bash
curl -s -X DELETE -H "Authorization: Bearer sk-user-xxx" \
  https://mineru-api-sdwh.onrender.com/v1/tasks/t_1
```

同时删除任务记录与产物目录（不可恢复）。

## 示例附件

```bash
python examples/mineru_api/download_results.py t_1                    # 保存 md + 图片
python examples/mineru_api/download_results.py t_1 --zip              # 打包下载
python examples/mineru_api/download_results.py t_1 --file images/img_1.png -o out
```

## 注意事项

- 结果保存目录默认 `results/`；图片文件名做了 `/\` → `_` 扁平化，避免目录冲突
- 长文档 markdown 可能几 MB，`save_result` 直接写文件不载入内存
