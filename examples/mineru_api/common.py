#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MinerU API 示例公共库（全部示例的公共依赖）
==============================================
用法：
  1) 设置环境变量后直接运行各示例：
       set MINERU_API_KEY=sk-user-xxx          (Windows)
       export MINERU_API_KEY=sk-user-xxx        (Linux/Mac)
       set MINERU_API_BASE=https://mineru-api-sdwh.onrender.com
     （BASE 缺省即线上地址，可省略）
  2) 或作为模块 import 使用：
       from common import call, wait_task, save_result

包含：
  call()        通用请求（自动重试 + Cloudflare 随机拦截兜底）
  wait_task()   轮询任务到终态
  fetch_result()取结果（等待产物下载完成）
  save_result() 保存 markdown + 图片
  submit_urls() / submit_files()  快捷提交
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.environ.get("MINERU_API_BASE", "https://mineru-api-sdwh.onrender.com")
KEY = os.environ.get("MINERU_API_KEY", "")
UA = "python-requests/2.32.3 (mineru-api-client)"
RETRY_CODES = (400, 404, 429, 500, 502, 503, 504)


def call(method, path, body=None, timeout=120, retries=5, key=None):
    """通用请求。返回响应 dict（业务失败时抛 RuntimeError）。

    - 自动带 Bearer 鉴权
    - 对 Cloudflare 随机拦截码（400/404/429/5xx）指数退避重试
    """
    key = key or KEY
    if not key:
        raise RuntimeError("未设置 API key（环境变量 MINERU_API_KEY）")
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("User-Agent", UA)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    for attempt in range(retries):
        try:
            resp = urllib.request.urlopen(req, data=data, timeout=timeout)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in RETRY_CODES and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"网络错误: {e.reason}")
    raise RuntimeError("重试耗尽")


def wait_task(tid, timeout=1800, interval=5):
    """轮询任务到终态（done/failed），返回详情 dict。超时抛异常。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = call("GET", f"/v1/tasks/{tid}")
        st = d["data"]["status"]
        print(f"  [{int(time.time() - t0)}s] {st}")
        if st in ("done", "failed"):
            return d["data"]
        time.sleep(interval)
    raise RuntimeError(f"任务 {tid} 超时（>{timeout}s）")


def fetch_result(tid, wait_download=True):
    """取任务结果。产物下载中时轮询等待（wait_download=True）。"""
    for _ in range(120 if wait_download else 1):
        d = call("GET", f"/v1/tasks/{tid}/result")
        if d["data"].get("downloaded", True):
            return d["data"]
        time.sleep(3)
    raise RuntimeError(f"任务 {tid} 产物下载超时")


def save_result(tid, data, out_dir="results", with_images=True):
    """保存 markdown 全文 + 可选下载 images/ 下的图片。返回保存路径列表。"""
    import os as _os
    _os.makedirs(out_dir, exist_ok=True)
    saved = []
    md_path = _os.path.join(out_dir, f"{tid}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(data.get("markdown", ""))
    saved.append(md_path)
    if with_images:
        for meta in data.get("files", []):
            name = meta["name"]
            if name.startswith("images"):
                url_name = name.replace("\\", "/")
                req = urllib.request.Request(f"{BASE}/v1/tasks/{tid}/file/{url_name}")
                req.add_header("Authorization", f"Bearer {KEY}")
                req.add_header("User-Agent", UA)
                p = _os.path.join(out_dir, name.replace("\\", "_").replace("/", "_"))
                with open(p, "wb") as f:
                    f.write(urllib.request.urlopen(req, timeout=60).read())
                saved.append(p)
    return saved


def submit_urls(urls, **opts):
    """提交 URL 列表，返回 (task_ids, reused_ids)。opts 可传 formula/table/..."""
    r = call("POST", "/v1/tasks", {"urls": list(urls), **opts})
    if r.get("code") != 0:
        raise RuntimeError(f"提交失败: {r}")
    return r["data"]["task_ids"], r["data"].get("reused_ids", [])


def submit_files(paths, **opts):
    """提交本地文件列表（base64 上传），返回 task_ids。opts 可传 formula/table/..."""
    files = []
    for p in paths:
        with open(p, "rb") as f:
            files.append({"name": p.split("/")[-1].split("\\")[-1],
                          "data": base64.b64encode(f.read()).decode()})
    r = call("POST", "/v1/tasks", {"files": files, **opts})
    if r.get("code") != 0:
        raise RuntimeError(f"提交失败: {r}")
    return r["data"]["task_ids"]
