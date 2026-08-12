# MinerU API 实测调研：功能、配额与多 Token 利用方案

> 日期：2026-08-12
> 方式：官方 SDK（mineru-open-sdk 0.2.5，PyPI 清华源）+ 自有 150+ token 直接实测
> 结论均来自真实接口调用（HTTP 状态码/响应体/响应头），非文档转述

---

## 1. API 全貌（实测确认）

### 1.1 精准解析 API（需 token）— `https://mineru.net/api/v4`

| 接口 | 方法 | 用途 | 实测结果 |
|---|---|---|---|
| `/extract/task/batch` | POST | 提交 URL 解析任务 | ✅ 单个或批量 URL，返回 `batch_id` |
| `/file-urls/batch` | POST | 本地文件上传（获取上传 URL 后 PUT） | ✅ SDK `_upload_and_submit` |
| `/extract-results/batch/{batch_id}` | GET | 轮询任务结果 | ✅ `data.extract_result[]`，含 `state`/`full_zip_url` |
| `/extract/task/{task_id}` | GET | 查单个任务 | SDK 内部使用 |
| `/quota` | GET | 查剩余配额 | ✅ 返回 `{user_left_quota, total_left_quota}`（见 §2.3） |

**请求体**（提交任务）：
```json
{
  "files": [{"url": "https://.../a.pdf", "is_ocr": true, "page_ranges": "1-10", "data_id": "自定义ID"}],
  "model_version": "pipeline",        // pipeline | vlm | html
  "enable_formula": true,
  "enable_table": true,
  "language": "en",                    // en | zh | ...
  "extra_formats": ["docx"]
}
```
- 单个请求可提交多个文件（实测 5 个 URL 一次成功）
- `model=html` 支持网页 URL 直接爬取转 Markdown（`crawl()`）
- 本地文件：先 `/file-urls/batch` 拿签名上传 URL → PUT → 同一 `batch_id` 轮询

### 1.2 产物包（full_zip_url，实测下载 5.2MB）

```
full.md                138KB   ← 完整 Markdown（公式/表格/图片引用）
layout.json            2.8MB   ← 版面分析（坐标/类型）
*_content_list.json    296KB   ← 结构化内容列表（OCR 文本+位置）
*_model.json           517KB   ← 模型原始输出
*_origin.pdf           774KB   ← 原文件
images/*.jpg           41 张   ← 提取图片
```

### 1.3 Agent 轻量 API（免 token）— `https://mineru.net/api/v1/agent`
- `POST /parse/file`：IP 限频（`RATE_LIMITED`），10MB / 20 页 / 仅 Markdown
- 适合高频小任务，与精准 API 互补

---

## 2. 配额实测（关键结论）

### 2.1 提交限流：滑动窗口 ~50 次/分钟/**每 token** ✅ 实测
- 单 token 冷启动连发：**第 47 次（33s 内）出现 HTTP 429**，窗口容量 ≈ 46-50
- 429 后恢复：非固定 60s，**滑动窗口**（+10s/+20s 429，+31s 恢复）
- 5 token 并发 4.2s 内各提交 10 次（共 50 次）：**全部成功，零 429** → 窗口 per-token 独立

### 2.2 解析吞吐 ✅ 实测
- 新文件（未缓存）：**12~35 秒/个**，10 并发同时处理正常
- 已缓存文件（他人/自己解析过）：**0.2~2 秒**秒出（内容级缓存）
- 文件限制：≤200MB、≤600 页（SDK 异常码 -60005/-60006）

### 2.3 每日配额接口 ✅ 实测
```json
GET /api/v4/quota → {"user_left_quota": 0, "total_left_quota": 0}
```
- 全部 150+ token 返回**相同数值** → 该接口按账号/全局维度
- **但 quota=0 不阻止任务**：实测 0 配额下持续提交/解析全部成功 → 该字段可能表示"高优先级额度"，用尽后任务走普通队列仍会处理
- 官方口径：每日 5000 文件提交上限（HTML 限 100/日）、1000 页高优先级/日（超出降级）

### 2.4 定价
- 官方：**暂无商业化收费计划**，限流策略可动态调整（保留权利）

---

## 3. 150 Token 利用方案

### 3.1 理论吞吐（per-token 限流 → 线性放大）

| 维度 | 单 token | 150 token |
|---|---|---|
| 提交 | ~50 次/分钟 | **~7500 次/分钟**（实测 50 次 4.2s 全过） |
| 新文件解析 | 12-35s/个（并发不限） | 实测 10 并发 done；**50-100 并发可期** |
| 缓存文件 | <2s | 秒级批量 |
| 每日提交上限 | 5000/日（若 per-token） | **75 万/日**（理论；受队列/其他维度限制） |

### 3.2 推荐架构：Token 池轮询调度器

```
┌─────────────┐   round-robin/加权   ┌──────────────────┐
│ 任务队列      │ ──────────────────→ │ 提交层 (token 池)  │
│ (URL/本地文件) │                     │ 150 token 轮换     │
└─────────────┘                     │ 每 token 控 ~40/min │
        ↑                           └────────┬─────────┘
        │                                    │ batch_id
        │                           ┌────────▼─────────┐
        │                           │ 结果轮询层          │
        └─────── 写回 (md/json/images) │ 按 token 分组轮询   │
                                      │ 完成→下载 zip→落盘  │
                                      └──────────────────┘
```

关键设计点：
1. **提交层**：round-robin 轮换 token，每 token 维护滑动窗口计数（40 次/分钟保险值），触发 429 自动退避 + 换 token
2. **结果层**：`extract-results` 接口限流 1000/min 宽松，按 batch_id 分组、指数退避轮询
3. **缓存优先**：同一 URL 重复提交秒出 → 先去重（URL 哈希），重复任务优先
4. **配额监控**：定时 `GET /quota`，user_left_quota 变化时调整速率
5. **失败重试**：429 → 换 token 重试；`-60018/-60019`（日配额耗尽）→ 暂停该 token；解析 failed → 记录重试

### 3.3 应用场景

| 场景 | 用法 | 吞吐量级 |
|---|---|---|
| **论文批量转 Markdown**（RAG/知识库） | arxiv/语义学者 URL 直接提交 | 1000+ 篇/小时 |
| **本地 PDF/扫描件 OCR 入库** | `/file-urls/batch` 上传 | 数百文件/小时 |
| **网页存档转 md** | `model=html` 爬取 | 取决于源站 |
| **LLM 训练数据清洗** | 批量解析 → full.md + content_list.json | 大数据集 |
| **表格/公式专项提取** | `enable_table/enable_formula` | 精准结构化 |

### 3.4 风险与注意

1. **账号级风险**：所有 token 属同一账号体系（quota 接口同值）——滥用可能触发账号级封禁，建议单 token ≤40/min 保守速率
2. **动态限流**：官方保留调整权利，需监控 429 率自适应降速
3. **URL 网络限制**：GitHub/AWS 等国外 URL 官方提示超时（实测 arxiv 可达）
4. **token 有效期**：90 天（TOKEN_LIFETIME_DAYS=90），`refresh` 模式可续
5. **产物存储**：zip 下载需本地落盘管理（建议按 batch_id/日期分目录）

---

## 4. 下一步建议

- [ ] 构建 `scripts/mineru_api_pool.py`：token 池轮询调度器（提交+轮询+下载+落盘）
- [ ] 实测 30 并发真实吞吐曲线（确认解析并发上限）
- [ ] 验证 per-token 每日上限（长期运行观察 429/配额拒绝模式）
- [ ] 与既有 `mineru_batch.py` 注册链路打通（新 token 自动入池）
