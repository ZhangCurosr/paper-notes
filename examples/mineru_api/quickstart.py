#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 01：快速开始——健康检查 → 提交 URL → 轮询 → 保存结果（最小闭环）
用法：
  set MINERU_API_KEY=sk-user-xxx
  python examples/mineru_api/quickstart.py "https://arxiv.org/pdf/2409.18839.pdf"
"""
import sys
from common import call, wait_task, fetch_result, save_result

URL = sys.argv[1] if len(sys.argv) > 1 else "https://arxiv.org/pdf/2409.18839.pdf"

# 1) 健康检查（无需鉴权）
info = call("GET", "/health")
print(f"服务: {info['data']['service']} v{info['data']['version']}")

# 2) 提交
r = call("POST", "/v1/tasks", {"urls": [URL]})
tid = r["data"]["task_ids"][0]
print(f"task_id: {tid}")

# 3) 轮询到终态
data = wait_task(tid)

# 4) 失败则直接退出并显示原因
if data["status"] == "failed":
    print(f"失败: {data.get('error')}")
    sys.exit(1)

# 5) 取结果并保存
res = fetch_result(tid)
saved = save_result(tid, res, out_dir="results")
print(f"完成: markdown {len(res.get('markdown', ''))} 字符")
for p in saved:
    print(f"  已保存: {p}")
