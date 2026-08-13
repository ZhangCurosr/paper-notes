#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 10：批量并发——多文件并行提交 + 失败重试 + 汇总
用法：
  set MINERU_API_KEY=sk-user-xxx
  python examples/mineru_api/batch_parse.py ./docs/paper1.pdf ./docs/paper2.pdf ...
  python examples/mineru_api/batch_parse.py ./docs   # 目录自动展开

要点：
  - 提交与轮询均线程安全（common.call 每次独立 Request）
  - 失败任务自动 retry 一次（仅 failed 可重试）
  - 每 key 限流 60 次/分钟——并发过大时 401，重试退避即可
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from common import call, submit_files, wait_task, fetch_result, save_result


def expand(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += [os.path.join(p, f) for f in sorted(os.listdir(p))
                    if os.path.isfile(os.path.join(p, f))]
        else:
            out.append(p)
    return out


def parse_one(path):
    """单文件：提交 → 等待 → 失败重试一次 → 保存。返回 (path, ok, msg)"""
    tids = submit_files([path], formula=True, table=True)
    tid = tids[0]
    try:
        data = wait_task(tid, timeout=900)
        if data["status"] == "failed":
            err = data.get("error", "")
            print(f"  [{path}] 失败({err[:40]}...)，重试一次")
            call("POST", f"/v1/tasks/{tid}/retry")
            data = wait_task(tid, timeout=900)
        if data["status"] == "failed":
            return path, False, data.get("error", "")
        res = fetch_result(tid)
        save_result(tid, res, out_dir="batch_results", with_images=False)
        return path, True, f"{len(res.get('markdown', ''))} 字符"
    except Exception as e:
        return path, False, str(e)[:80]


def main():
    paths = expand(sys.argv[1:])
    if not paths:
        sys.exit("用法: python batch_parse.py <file|dir> ...")
    print(f"共 {len(paths)} 个文件，并发 4")
    t0 = time.time()
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for path, ok_, msg in ex.map(parse_one, paths):
            tag = "OK " if ok_ else "FAIL"
            print(f"  [{tag}] {path}: {msg}")
            ok += ok_
            fail += (not ok_)
    print(f"完成: 成功 {ok} / 失败 {fail}，耗时 {int(time.time() - t0)}s"
          f"（结果在 batch_results/）")


if __name__ == "__main__":
    main()
