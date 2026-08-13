#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 08：用户 key 管理（admin）——创建 / 列表 / 删除
用法（需 admin key）：
  set MINERU_API_KEY=sk-admin-xxxx
  python examples/mineru_api/admin_keys.py create alice     # 创建用户 key
  python examples/mineru_api/admin_keys.py list             # 列出全部 key
  python examples/mineru_api/admin_keys.py delete sk-xxx    # 删除
"""
import argparse
from common import call

p = argparse.ArgumentParser(description="key 管理（admin）")
p.add_argument("action", choices=["create", "list", "delete"])
p.add_argument("arg", nargs="?", default="", help="create: 名称 / delete: key")
args = p.parse_args()

if args.action == "create":
    r = call("POST", "/v1/keys", {"name": args.arg or "user"})
    print(f"新 key: {r['data']['key']}  (name={r['data']['name']})")
    print("分发给使用者即可调 /v1/tasks 等端点")
elif args.action == "list":
    r = call("GET", "/v1/keys")
    for k in r["data"]["keys"]:
        flag = " [admin]" if k["admin"] else ""
        print(f"  {k['key'][:14]}...  name={k['name']}  任务={k['tasks']}{flag}")
elif args.action == "delete":
    if not args.arg.startswith("sk-"):
        sys.exit("需要提供要删除的 key")
    r = call("DELETE", f"/v1/keys/{args.arg}")
    print(f"已删除: {r['data']['deleted']}")
