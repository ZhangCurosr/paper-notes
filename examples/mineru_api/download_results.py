#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 05：结果获取——result JSON / 单文件下载 / zip 打包 / 删除
用法：
  set MINERU_API_KEY=sk-user-xxx
  python examples/mineru_api/download_results.py t_xxx           # 保存 md+图片
  python examples/mineru_api/download_results.py t_xxx --zip     # 打包下载全部产物
  python examples/mineru_api/download_results.py t_xxx --file images/img_1.png -o out.png
"""
import argparse
import os
import sys
import urllib.request
from common import BASE, KEY, UA, call, save_result

p = argparse.ArgumentParser(description="结果下载")
p.add_argument("task_id")
p.add_argument("--zip", action="store_true", help="下载 zip 包")
p.add_argument("--file", help="下载单个产物文件（如 images/img_1.png）")
p.add_argument("-o", "--out", default="results")
args = p.parse_args()

tid = args.task_id

if args.zip:
    # GET /v1/tasks/{id}/zip → application/zip（产物总量 ≤1GB）
    req = urllib.request.Request(f"{BASE}/v1/tasks/{tid}/zip")
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("User-Agent", UA)
    os.makedirs(args.out, exist_ok=True)
    p_ = os.path.join(args.out, f"{tid}_result.zip")
    with open(p_, "wb") as f:
        f.write(urllib.request.urlopen(req, timeout=300).read())
    print(f"zip 已保存: {p_}")
elif args.file:
    # GET /v1/tasks/{id}/file/{name} → 二进制（路径穿越防护：name 须在产物目录内）
    req = urllib.request.Request(f"{BASE}/v1/tasks/{tid}/file/{args.file.replace(chr(92), '/')}")
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("User-Agent", UA)
    os.makedirs(args.out, exist_ok=True)
    p_ = os.path.join(args.out, args.file.replace("/", "_"))
    with open(p_, "wb") as f:
        f.write(urllib.request.urlopen(req, timeout=120).read())
    print(f"文件已保存: {p_}")
else:
    # GET /v1/tasks/{id}/result → JSON（files 清单 + markdown 全文）
    res = call("GET", f"/v1/tasks/{tid}/result")
    data = res["data"]
    if not data.get("downloaded", True):
        print("产物仍在下载，稍后重试（或等待）")
        sys.exit(1)
    print(f"状态: {data['status']} | 文件 {len(data['files'])} 个 | markdown {len(data.get('markdown', ''))} 字符")
    saved = save_result(tid, data, out_dir=args.out)
    for s in saved:
        print(f"  已保存: {s}")
