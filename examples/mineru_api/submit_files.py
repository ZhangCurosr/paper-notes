#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 03：任务提交——本地文件批量上传（base64），≤50 个/批
用法：
  set MINERU_API_KEY=sk-user-xxx
  python examples/mineru_api/submit_files.py ./docs ./more_docs
"""
import os
import sys
from common import call

DIRS = sys.argv[1:] or ["./docs"]

# 收集目录下全部文件（可扩展名过滤）
paths = []
for d in DIRS:
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if os.path.isfile(p) and os.path.getsize(p) <= 100 * 1024 * 1024:
                paths.append(p)
    else:
        paths.append(d)
if not paths:
    sys.exit("未找到文件")
print(f"共 {len(paths)} 个文件（每批 ≤50）")

for i in range(0, len(paths), 50):
    batch = paths[i:i + 50]
    files = []
    for p in batch:
        with open(p, "rb") as f:
            files.append({"name": os.path.basename(p),
                          "data": __import__("base64").b64encode(f.read()).decode()})
    # 单文件可加: "pages": "1-10", "ocr": true, "data_id": "doc_001"
    r = call("POST", "/v1/tasks", {"files": files, "formula": True, "table": True})
    print(f"批 {i // 50 + 1}: {r['data']['task_ids']}")
print("全部提交完成，用 query_tasks.py / wait_task 查询进度")
