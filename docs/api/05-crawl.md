# 05 网页爬取转 Markdown

**功能**：把网页正文提取为 Markdown（等价 `POST /v1/tasks` + `model=html`），适合论文页/博客/文档站采集。

**端点**：`POST /v1/crawl`

## 请求

```bash
curl -s -X POST -H "Authorization: Bearer sk-user-xxx" -H "Content-Type: application/json" \
  -d '{"urls": ["https://example.com/article", "https://example.com/page2"]}' \
  https://mineru-api-sdwh.onrender.com/v1/crawl
```

| 规则 | 说明 |
| --- | --- |
| `urls` | 必填，1~20 个 |
| 其他参数 | 同 `/v1/tasks`（formula/table/language 等均可用，模型固定 html） |
| 复用 | `fresh=false` 时命中历史爬取结果自动复用 |

## 响应

```json
{"code": 0, "data": {"task_ids": ["t_1", "t_2"], "reused_ids": [], "tasks": 2, "reused": 0}}
```

后续轮询/取结果与普通任务完全一致（`GET /v1/tasks/{id}` → `GET /v1/tasks/{id}/result`，markdown 即网页正文）。

## 示例附件

```bash
python examples/mineru_api/crawl.py "https://example.com/article"
python examples/mineru_api/crawl.py "https://a.com/x" "https://b.com/y"   # 多个
# 结果保存到 crawl_results/{task_id}.md
```

## 注意事项

- 目标网站需公网可达；被 SSRF 黑名单（内网/元数据段）拒绝的 URL 返回 400
- 动态渲染页面（JS 加载内容）可能提取不完整——这类建议先落成 PDF 再走 `/v1/tasks`
- flash 通道同样适用（URL ≤10MB 自动尝试，失败回落）
