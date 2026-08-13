# 02 任务提交（URL / 本地文件）

**功能**：向 `POST /v1/tasks` 提交解析任务。支持 URL 与 base64 文件混合，单次 ≤50 个。

**端点**：`POST /v1/tasks`

## 请求体

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

## 参数速查

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `urls` | string[] | - | 公网 http/https；**SSRF 防护**（内网/元数据/IP 段拒绝）。与 `files` 合计 ≤50 |
| `files[].name` | string | - | 文件名；**禁止 `/` `\`**；扩展名白名单 `.pdf .doc .docx .ppt .pptx .xls .xlsx .png .jpg .jpeg .webp .bmp .tif .tiff` |
| `files[].data` | string | - | 文件内容 base64；≤100MB |
| `files[].pages` | string | - | 单文件页范围 `1-10,15` |
| `files[].ocr` | bool | - | 单文件强制 OCR |
| `files[].data_id` | string | - | 自定义标识 |
| `formula` | bool | false | 公式识别 |
| `table` | bool | false | 表格识别 |
| `ocr` | bool | false | OCR（扫描件） |
| `language` | string | - | `zh` / `en` 等 |
| `pages` | string | - | URL 的页范围（文件级优先） |
| `extra_formats` | string[] | - | 额外导出，如 `["docx"]` |
| `model` | string | pipeline | `pipeline` / `vlm` / `html` |
| `fresh` | bool | false | `true` 强制重解析；`false` 命中已完成 URL 自动复用 |
| `flash` | bool | false | URL 走免 token flash 通道 |

## 响应

```json
{"code": 0, "data": {"task_ids": ["t_1", "t_2"], "reused_ids": ["t_old"], "tasks": 2, "reused": 1}}
```

- `task_ids`：新建任务
- `reused_ids`：`fresh=false` 时命中历史已完成结果的 URL（不重复扣配额）

## 示例附件

```bash
# URL 全参数版（含注释掉的进阶参数）
python examples/mineru_api/submit_url.py "https://arxiv.org/pdf/2409.18839.pdf"

# 本地目录批量上传（自动分批 ≤50）
python examples/mineru_api/submit_files.py ./docs ./more
```

## 注意事项

- **去重复用**：同一 key 重复提交相同 URL（`fresh=false`）直接返回历史结果，不产生新任务
- **flash 通道**：≤10MB 本地文件自动走免 token 的 flash API（IP 级 20 次/分钟），失败自动回落 v4
- 参数错误返回 400（如 URL 被 SSRF 拒绝、文件名含路径、扩展名不在白名单、超 100MB）
