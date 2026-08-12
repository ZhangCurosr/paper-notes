#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MinerU API 客户端示例：演示如何用 API key 调用 mineru_api_server
================================================================
用法：
  # 提交 URL 任务并等待结果
  python scripts/mineru_api_client.py --key sk-user-xxx --urls "https://arxiv.org/pdf/2409.18839.pdf"

  # 提交本地文件（base64 上传）
  python scripts/mineru_api_client.py --key sk-user-xxx --input-dir ./docs --out ./results

  # 只提交不等待（拿 task_id 后自行轮询）
  python scripts/mineru_api_client.py --key sk-user-xxx --urls "https://a.pdf" --no-wait

  # 网页爬取转 md / 指定页范围 / docx 导出 / 强制重新解析
  python scripts/mineru_api_client.py --key sk-user-xxx --crawl "https://example.com/page"
  python scripts/mineru_api_client.py --key sk-user-xxx --urls "https://a.pdf" --pages "1-10" \
         --extra-formats docx --fresh
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error

DEFAULT_BASE = "http://127.0.0.1:8900"


def api(base, key, method, path, body=None, timeout=60):
    """通用请求：返回 (data_dict, http_status)"""
    req = urllib.request.Request(base + path, method=method)
    req.add_header("Authorization", "Bearer " + key)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, data=data, timeout=timeout)
        return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read()), e.code
        except Exception:
            return {"code": e.code, "msg": str(e)}, e.code


def wait_and_fetch(args, ids, base, key):
    """轮询等待全部任务完成并取结果"""
    t0 = time.time()
    while time.time() - t0 < 1800:
        states = []
        for tid in ids:
            d, _ = api(base, key, "GET", f"/v1/tasks/{tid}")
            states.append((tid, d["data"]["status"]))
        print(f"  [{int(time.time()-t0)}s] " + " ".join(f"{t}:{s}" for t, s in states))
        if all(s in ("done", "failed") for _, s in states):
            break
        time.sleep(5)

    os.makedirs(args.out, exist_ok=True)
    for tid, s in states:
        if s != "done":
            print(f"  {tid}: {s}，跳过")
            continue
        d, _ = api(base, key, "GET", f"/v1/tasks/{tid}/result")
        for _ in range(60):
            if d.get("data", {}).get("downloaded", True):
                break
            time.sleep(3)
            d, _ = api(base, key, "GET", f"/v1/tasks/{tid}/result")
        data = d["data"]
        md = data.get("markdown", "")
        fn = os.path.join(args.out, f"{tid}.md")
        with open(fn, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"  {tid}: markdown {len(md)} 字符 → {fn} | 文件 {len(data.get('files', []))} 个")
        if args.no_images:
            continue
        for fmeta in data.get("files", []):
            fname = fmeta["name"]
            if fname.startswith("images"):
                url_name = fname.replace("\\", "/")
                req = urllib.request.Request(f"{base}/v1/tasks/{tid}/file/{url_name}")
                req.add_header("Authorization", "Bearer " + key)
                resp = urllib.request.urlopen(req, timeout=30)
                p = os.path.join(args.out, fname.replace("\\", "_").replace("/", "_"))
                with open(p, "wb") as f:
                    f.write(resp.read())


def main():
    ap = argparse.ArgumentParser(description="MinerU API 客户端")
    ap.add_argument("--base", default=DEFAULT_BASE, help="服务地址")
    ap.add_argument("--key", required=True, help="API key")
    ap.add_argument("--urls", help="逗号分隔 URL")
    ap.add_argument("--url-file", help="URL 列表文件")
    ap.add_argument("--input-dir", help="本地文件目录（base64 上传）")
    ap.add_argument("--crawl", help="网页 URL（model=html 爬取转 md）")
    ap.add_argument("--out", default=".", help="结果保存目录")
    ap.add_argument("--no-wait", action="store_true", help="只提交不等待")
    ap.add_argument("--no-images", action="store_true", help="不下载图片")
    # 解析参数
    ap.add_argument("--formula", action="store_true")
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--language", help="语言（en/zh）")
    ap.add_argument("--model", default=None, help="pipeline/vlm/html")
    ap.add_argument("--pages", help="页范围，如 1-10,15")
    ap.add_argument("--extra-formats", help="额外导出格式，逗号分隔（如 docx）")
    ap.add_argument("--fresh", action="store_true", help="强制重新解析（默认复用已完成结果）")
    ap.add_argument("--retry", action="store_true", help="重试失败的 task（配合 --task-id）")
    ap.add_argument("--task-id", help="指定 task_id（配合 --retry / 单独查询）")
    args = ap.parse_args()

    # 重试模式
    if args.retry:
        if not args.task_id:
            sys.exit("错误: --retry 需要 --task-id")
        d, _ = api(args.base, args.key, "POST", f"/v1/tasks/{args.task_id}/retry")
        print(d)
        return

    # 查询模式
    if args.task_id and not (args.urls or args.url_file or args.input_dir or args.crawl):
        d, _ = api(args.base, args.key, "GET", f"/v1/tasks/{args.task_id}")
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return

    urls = []
    if args.urls:
        urls += [u.strip() for u in args.urls.split(",") if u.strip()]
    if args.url_file:
        with open(args.url_file, encoding="utf-8") as f:
            urls += [l.split("#")[0].strip() for l in f if l.strip()]

    files = []
    if args.input_dir:
        for fn in sorted(os.listdir(args.input_dir)):
            fp = os.path.join(args.input_dir, fn)
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    files.append({"name": fn, "data": base64.b64encode(f.read()).decode()})

    # 组装请求体
    body = {"urls": urls, "files": files}
    if args.formula:
        body["formula"] = True
    if args.table:
        body["table"] = True
    if args.ocr:
        body["ocr"] = True
    if args.language:
        body["language"] = args.language
    if args.model:
        body["model"] = args.model
    if args.pages:
        body["pages"] = args.pages
    if args.extra_formats:
        body["extra_formats"] = [x.strip() for x in args.extra_formats.split(",") if x.strip()]
    if args.fresh:
        body["fresh"] = True

    if args.crawl:
        if body.get("urls") or body.get("files"):
            sys.exit("错误: --crawl 与 --urls/--input-dir 互斥")
        print(f"爬取 {args.crawl} ...")
        d, _ = api(args.base, args.key, "POST", "/v1/crawl", {"urls": [args.crawl]})
        if d.get("code") != 0:
            sys.exit(f"爬取失败: {d}")
        ids = d["data"]["task_ids"]
        print(f"task_ids: {ids}")
        if args.no_wait:
            return
        wait_and_fetch(args, ids, args.base, args.key)
        return

    if not urls and not files:
        sys.exit("错误: --urls / --url-file / --input-dir / --crawl 至少一项")

    print(f"提交 {len(urls)} 个 URL + {len(files)} 个文件 ...")
    d, _ = api(args.base, args.key, "POST", "/v1/tasks", body)
    if d.get("code") != 0:
        sys.exit(f"提交失败: {d}")
    data = d["data"]
    ids = data["task_ids"]
    print(f"task_ids: {ids}")
    if data.get("reused"):
        print(f"  复用已完成结果: {data['reused_ids']}")
    if args.no_wait:
        return
    wait_and_fetch(args, ids, args.base, args.key)


if __name__ == "__main__":
    main()
