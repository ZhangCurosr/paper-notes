#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 06：网页爬取转 Markdown（等价于 POST /v1/tasks + model=html）
用法：
  set MINERU_API_KEY=sk-user-xxx
  python examples/mineru_api/crawl.py "https://example.com/article" "https://example.com/page2"
"""
import sys
from common import call, wait_task, fetch_result, save_result

urls = sys.argv[1:]
if not urls:
    sys.exit("用法: python crawl.py <url1> [url2 ...]（≤20 个）")

r = call("POST", "/v1/crawl", {"urls": urls})
tids = r["data"]["task_ids"]
print(f"爬取任务: {tids}")

for tid in tids:
    data = wait_task(tid)
    if data["status"] == "failed":
        print(f"  {tid} 失败: {data.get('error')}")
        continue
    res = fetch_result(tid)
    saved = save_result(tid, res, out_dir="crawl_results")
    print(f"  {tid}: {len(res.get('markdown', ''))} 字符 → {saved[0]}")
