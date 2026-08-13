#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 04：任务查询——列表（分页/状态过滤）、详情、重试、删除
用法：
  set MINERU_API_KEY=sk-user-xxx
  python examples/mineru_api/query_tasks.py            # 最近 10 条
  python examples/mineru_api/query_tasks.py --status done --limit 5
  python examples/mineru_api/query_tasks.py --detail t_xxx
  python examples/mineru_api/query_tasks.py --retry t_xxx
  python examples/mineru_api/query_tasks.py --delete t_xxx
"""
import argparse
import json
from common import call

p = argparse.ArgumentParser(description="任务查询/管理")
p.add_argument("--status", help="过滤状态: pending/submitted/done/failed")
p.add_argument("--limit", type=int, default=10, help="数量 1-200")
p.add_argument("--offset", type=int, default=0)
p.add_argument("--detail", help="task_id 查看详情")
p.add_argument("--retry", help="task_id 重试（仅 failed）")
p.add_argument("--delete", help="task_id 删除（记录+产物）")
args = p.parse_args()

if args.detail:
    d = call("GET", f"/v1/tasks/{args.detail}")
    print(json.dumps(d["data"], ensure_ascii=False, indent=1))
elif args.retry:
    d = call("POST", f"/v1/tasks/{args.retry}/retry")
    print(d["data"])
elif args.delete:
    d = call("DELETE", f"/v1/tasks/{args.delete}")
    print(d["data"])
else:
    q = f"/v1/tasks?limit={args.limit}&offset={args.offset}"
    if args.status:
        q += f"&status={args.status}"
    d = call("GET", q)
    data = d["data"]
    print(f"我的任务总数: {data['total']}")
    for t in data["tasks"]:
        print(f"  {t['task_id']}  {t['status']:9s} {t['channel']:5s} {t['source'][:70]}")
