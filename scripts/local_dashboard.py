#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
本地 Web Dashboard —— 查看云端 MinerU API 任务情况
====================================================
零第三方依赖（纯标准库）。启动后浏览器自动打开 http://127.0.0.1:8901。

功能：
  - 服务概览 / KPI 卡片 / 每日配额进度条
  - 24h 任务趋势折线图（带悬停提示）+ 错误分布条形图（canvas 手绘，无外部资源）
  - Token 池明细表（搜索 / 排序 / 成功率进度条 / 导出 CSV）
  - 最近任务列表（状态筛选 / 搜索 / 点击查看详情弹窗 / 相对时间 / 导出 CSV）
  - 任务状态分布条 / 暗亮主题切换 / 自动刷新开关 + 倒计时
  - 本地代理缓存，浏览器不直连云端（云端 CORS 默认关）

配置（优先级：命令行 > 环境变量 > 配置文件）：
  python scripts/local_dashboard.py --key sk-xxx [--port 8901] [--refresh 15]
  python scripts/local_dashboard.py --config "%USERPROFILE%\.mineru_dashboard\config.json"
    首次无 key 运行会交互式输入，可选保存到用户目录（%USERPROFILE%\.mineru_dashboard\）
  Windows 可双击 start_dashboard.bat 一键启动

安全：
  - 默认仅监听 127.0.0.1
  - key 不写入仓库、不出现在页面；保存到配置文件仅在你确认时
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard_page  # 共享 Dashboard 页面（Tab 布局 + 历史）

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VERSION = "2.0"
DEFAULT_BASE = "https://mineru-api-sdwh.onrender.com"
DEFAULT_CFG_DIR = os.path.join(os.path.expanduser("~"), ".mineru_dashboard")
DEFAULT_CFG = os.path.join(DEFAULT_CFG_DIR, "config.json")
UA = "python-requests/2.32.3 (mineru-api-client)"
RETRY_CODES = (400, 404, 429, 500, 502, 503, 504)


# ─────────────────────────── 配置 ───────────────────────────

def load_config(path):
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(path, cfg):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def ensure_key(args, cfg):
    """返回 key；缺失时交互式输入，可选保存到配置。"""
    key = args.key or os.environ.get("MINERU_API_KEY", "") or \
          os.environ.get("MINERU_ADMIN_KEY", "") or cfg.get("key", "")
    if key:
        return key
    print("未配置 API key，请输入（admin key 看全局 / user key 看自己）：")
    try:
        key = input("API key: ").strip()
    except EOFError:
        sys.exit("未提供 key")
    if not key:
        sys.exit("未提供 key")
    save = input(f"保存到本地配置 {args.config} 吗？(y/N): ").strip().lower()
    if save in ("y", "yes"):
        cfg2 = load_config(args.config)
        cfg2["key"] = key
        cfg2["base"] = args.base
        save_config(args.config, cfg2)
        print(f"已保存（下次免输入）。删除该文件即可撤销。")
    return key


# ─────────────────────────── 云端代理 ───────────────────────────

class CloudClient:
    def __init__(self, base, key, cache_ttl=15):
        self.base = base.rstrip("/")
        self.key = key
        self.ttl = cache_ttl
        self.cache = {}
        self.lock = threading.Lock()

    def call(self, method, path, body=None, timeout=60, retries=5):
        req = urllib.request.Request(self.base + path, method=method)
        req.add_header("Authorization", f"Bearer {self.key}")
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
                return {"code": e.code, "msg": e.read().decode(errors="replace")[:200], "http": e.code}
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return {"code": -1, "msg": f"网络错误: {e}", "http": 0}

    def get(self, path, force=False):
        now = time.time()
        with self.lock:
            hit = self.cache.get(path)
            if hit and not force and hit[0] > now:
                return hit[1]
        data = self.call("GET", path)
        with self.lock:
            self.cache[path] = (now + self.ttl, data)
        return data

    def refresh(self):
        with self.lock:
            self.cache.clear()


# ─────────────────────────── 前端页面 ───────────────────────────



# ─────────────────────────── 本地 HTTP 服务 ───────────────────────────

def make_handler(client, cfg):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _page(self):
            body = dashboard_page.PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                return self._page()
            if path == "/api/config":
                return self._json({"base": client.base, "refresh": client.ttl,
                                   "version": VERSION, "service": "local_dashboard"})
            if path == "/api/overview":
                d = client.get("/v1/stats")
                v = client.get("/health")
                return self._json({"data": d.get("data"), "code": d.get("code"),
                                   "msg": d.get("msg"), "version": (v.get("data") or {}).get("version", "?"),
                                   "http": d.get("http", 200)})
            if path == "/api/tokens":
                return self._json(client.get("/v1/stats/tokens"))
            if path == "/api/trends":
                return self._json(client.get("/v1/stats/trends"))
            if path == "/api/errbox":
                d = client.get("/v1/stats")
                data = d.get("data") or {}
                return self._json({"data": {"err_dist": (data.get("tokens") or {}).get("err_dist", {}),
                                            "fail_reasons": (data.get("stats") or {}).get("fail_reasons", {})}})
            if path.startswith("/api/task/"):
                tid = path[len("/api/task/"):]
                return self._json(client.get(f"/v1/tasks/{tid}"))
            if path.startswith("/api/task-result/"):
                tid = path[len("/api/task-result/"):]
                return self._json(client.get(f"/v1/tasks/{tid}/result"))
            if path == "/api/tasks":
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                limit = min(max(int(q.get("limit", ["100"])[0]), 1), 200)
                offset = max(int(q.get("offset", ["0"])[0]), 0)
                st = q.get("status", [""])[0]
                p = f"/v1/tasks?limit={limit}&offset={offset}"
                if st:
                    p += f"&status={st}"
                return self._json(client.get(p))
            if path == "/api/history":
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                days = q.get("days", ["7"])[0]
                return self._json(client.get(f"/v1/history?days={days}"))
            if path == "/api/refresh":
                client.refresh()
                return self._json({"code": 0})
            self._json({"code": 404, "msg": "not found"}, 404)

        def do_POST(self):
            path = self.path.split("?")[0]
            if path.startswith("/api/retry/"):
                tid = path[len("/api/retry/"):]
                return self._json(client.call("POST", f"/v1/tasks/{tid}/retry"))
            if path == "/api/submit":
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
                try:
                    body = json.loads(raw or b"{}")
                except Exception:
                    return self._json({"code": 400, "msg": "JSON 非法"}, 400)
                return self._json(client.call("POST", "/v1/tasks", body))
            self._json({"code": 404, "msg": "not found"}, 404)

        def do_DELETE(self):
            path = self.path.split("?")[0]
            if path.startswith("/api/task/"):
                tid = path[len("/api/task/"):]
                return self._json(client.call("DELETE", f"/v1/tasks/{tid}"))
            self._json({"code": 404, "msg": "not found"}, 404)
    return Handler


def main():
    ap = argparse.ArgumentParser(description="本地 MinerU API Dashboard")
    ap.add_argument("--base", default=os.environ.get("MINERU_API_BASE", DEFAULT_BASE),
                    help="云端服务地址")
    ap.add_argument("--key", default=os.environ.get("MINERU_API_KEY", "") or
                    os.environ.get("MINERU_ADMIN_KEY", ""), help="API key")
    ap.add_argument("--port", type=int, default=8901, help="本地端口")
    ap.add_argument("--refresh", type=int, default=15, help="缓存/自动刷新秒数")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    ap.add_argument("--config", default=DEFAULT_CFG, help="配置文件路径（key/base 等）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.base == DEFAULT_BASE and cfg.get("base"):
        args.base = cfg["base"]
    key = ensure_key(args, cfg)
    if not key:
        sys.exit("缺少 API key")

    client = CloudClient(args.base, key, cache_ttl=args.refresh)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(client, cfg))
    print(f"MinerU API Dashboard v{VERSION}")
    print(f"  云端: {args.base}")
    print(f"  本地: http://{args.host}:{args.port}")
    print(f"  配置: {args.config if os.path.exists(args.config) else '(未保存)'}")
    print(f"  缓存: {args.refresh}s | key: {key[:10]}...")
    import webbrowser
    threading.Timer(0.6, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
