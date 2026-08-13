#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MinerU API 服务：把 Token 池调度器封装为 HTTP API
==================================================
其他人只需持有服务端发放的 API key，即可提交文档解析任务、查询状态、
获取结果（markdown / 单文件 / zip 打包）。后端自动用 Token 池完成
提交→轮询→下载，调用方完全感知不到多 token 的存在。

端点（v1，Authorization: Bearer <key>）：
  POST   /v1/tasks                  提交任务（urls / base64 文件 / 参数）
  GET    /v1/tasks/{id}             查询任务状态（含进度）
  GET    /v1/tasks/{id}/result      获取结果（markdown + files 清单）
  GET    /v1/tasks/{id}/file/{n}    下载单个产物文件
  GET    /v1/tasks/{id}/zip         打包下载全部产物
  POST   /v1/tasks/{id}/retry       重试 failed 任务
  POST   /v1/crawl                  网页爬取转 Markdown（model=html 快捷方式）
  GET    /v1/tasks                  任务列表（?limit=&offset=&status=）
  GET    /v1/me                     我的统计（任务数/状态分布/限流）
  POST   /v1/keys                   创建用户 key（需 admin）{"name":"xx"}
  GET    /v1/keys                   查看 keys（需 admin）
  DELETE /v1/keys/{key}             删除 key（需 admin）
  GET    /v1/stats                  服务统计（需 admin）

任务参数（POST /v1/tasks body）：
  urls: [str]                         URL 列表
  files: [{name, data(b64), pages?, ocr?, data_id?}]   本地文件（base64）
  model: "pipeline"|"vlm"|"html"      解析模型（默认服务端配置）
  formula/table/ocr: bool
  language: "en"|"zh"|...
  pages: "1-10,15"                    全局页范围
  extra_formats: ["docx"]             额外导出格式
  fresh: bool                         强制重新解析（默认复用已完成结果）

双通道架构：
  - v4 通道：token 池（全部 URL + 大文件），多 token 轮换突破限流
  - flash 通道：免 token agent API（≤10MB 本地文件自动走此通道），IP 限频
    由服务端 flash-rate 控制，节省主 token 池配额

用法：
  python scripts/mineru_api_server.py --port 8900
  # 首次启动自动生成 admin key 并打印（同时写入 server_data/admin_key.txt）
"""

import argparse
import base64
import datetime
import faulthandler
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import zipfile
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard_page  # 内嵌 Dashboard 页面（GET /dashboard）
import mineru_api_pool as mpool  # 复用 TokenPool / Task / submit / poll / download

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mineru_server_data")
FLASH_BASE = "https://mineru.net/api/v1/agent"
FLASH_MAX_SIZE = 10 * 1024 * 1024   # flash 通道文件上限 10MB
VERSION = "2.0"

# ─────────────────────────── 安全工具（SSRF / 路径穿越防护） ───────────────────────────

SAFE_SCHEMES = ("https", "http")
SAFE_UPLOAD_EXTS = (".pdf", ".docx", ".doc", ".ppt", ".pptx", ".xls", ".xlsx",
                    ".png", ".jpg", ".jpeg", ".webp", ".md", ".txt", ".csv", ".html")
MAX_UPLOAD_SIZE = 100 * 1024 * 1024   # 单文件上传上限 100MB（与 _read_body 一致）
MAX_URL_LEN = 2048                    # URL 长度上限
_PRIVATE_NETS = None


def _private_networks():
    """内网/环回/链路本地/云元数据/保留段（惰性初始化）"""
    global _PRIVATE_NETS
    if _PRIVATE_NETS is None:
        _PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
            "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
            "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
            "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
            "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32")]
    return _PRIVATE_NETS


def is_safe_url(url):
    """SSRF 防护：scheme 白名单 + 非内网字面量 + DNS 解析后 IP 非内网/保留段
    任一环节不确定 → 拒绝（防绕过优先）"""
    from urllib.parse import urlparse
    if not url or not isinstance(url, str) or len(url) > MAX_URL_LEN:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in SAFE_SCHEMES or not p.hostname:
        return False
    host = p.hostname.lower().rstrip(".")
    if not host:
        return False
    # 字面量黑名单：本机/内网惯用名/云元数据
    if host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".internal", ".lan")):
        return False
    if "metadata" in host or host in ("169.254.169.254",):
        return False
    # IP 字面量直接检查；域名解析后检查所有解析结果
    try:
        ips = set(socket.gethostbyname_ex(host)[2])
    except Exception:
        return False   # DNS 解析失败 → 拒绝（防绕过）
    for ip in ips:
        try:
            a = ipaddress.ip_address(ip)
            if a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast:
                return False
        except ValueError:
            return False
    return True


def safe_join(base, name):
    """防路径穿越：name 必须解析在 base 目录内，否则返回 None"""
    if not name:
        return None
    try:
        base_r = os.path.realpath(base)
        fp = os.path.realpath(os.path.join(base_r, name))
        if fp != base_r and not fp.startswith(base_r + os.sep):
            return None
        return fp
    except Exception:
        return None


def safe_filename(s):
    """下载响应头文件名 sanitize（去路径/引号/换行）"""
    s = os.path.basename(str(s).replace("\\", "/"))
    return re.sub(r'[\\/"\r\n\x00]', "_", s)[:120]


# ─────────────────────────── Flash 通道（免 token） ───────────────────────────

def flash_submit(local_path, opts):
    """免 token agent 通道：≤10MB 本地文件。返回 task_id / None(应回落v4) / \"rate_limited\""""
    try:
        payload = {"file_name": os.path.basename(local_path),
                   "language": getattr(opts, "language", None) or "ch"}
        if getattr(opts, "formula", False):
            payload["enable_formula"] = True
        if getattr(opts, "table", False):
            payload["enable_table"] = True
        if getattr(opts, "ocr", False):
            payload["is_ocr"] = True
        resp = mpool.requests.post(FLASH_BASE + "/parse/file", json=payload,
                                   headers={"Content-Type": "application/json"}, timeout=30)
        if resp.status_code == 429:
            return "rate_limited"
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            log_info(f"[flash file 业务拒绝] {body.get('msg', body)[:120]}")
            return None   # 类型不支持/页数超限等 → 回落 v4
        task_id = body["data"]["task_id"]
        file_url = body["data"]["file_url"]
        with open(local_path, "rb") as f:
            # ★ 签名 URL 校验严格：不带任何 header 的裸 PUT（带 Content-Type 会 403）
            r = mpool.requests.put(file_url, data=f, timeout=(30, 300))
            if r.status_code >= 400:
                log_info(f"[flash file 上传失败] HTTP {r.status_code}")
                return None
        return task_id
    except Exception as e:
        log_info(f"[flash file 提交异常] {str(e)[:100]}")
        return None


def flash_submit_url(url, opts):
    """免 token agent 通道：URL 直接解析（≤10MB/≤20页，超限服务端报错回落 v4）"""
    try:
        payload = {"url": url, "language": getattr(opts, "language", None) or "ch"}
        if getattr(opts, "formula", False):
            payload["enable_formula"] = True
        if getattr(opts, "table", False):
            payload["enable_table"] = True
        if getattr(opts, "ocr", False):
            payload["is_ocr"] = True
        resp = mpool.requests.post(FLASH_BASE + "/parse/url", json=payload,
                                   headers={"Content-Type": "application/json"}, timeout=30)
        if resp.status_code == 429:
            return "rate_limited"
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            log_info(f"[flash url 业务拒绝] {body.get('msg', body)[:120]}")
            return None
        return body["data"]["task_id"]
    except Exception as e:
        log_info(f"[flash url 提交异常] {str(e)[:100]}")
        return None


def flash_poll(task):
    """轮询 flash 任务。更新 task 状态"""
    try:
        d = mpool.requests.get(FLASH_BASE + f"/parse/{task.batch_id}", timeout=30).json()
    except Exception:
        task.poll_fails += 1
        return
    task.poll_fails = 0
    data = d.get("data", {})
    ep = data.get("extract_progress") or {}
    if ep:
        task.progress = {"extracted_pages": ep.get("extracted_pages", 0),
                         "total_pages": ep.get("total_pages", 0)}
    state = data.get("state")
    if state == "done":
        task.status = "done"
        task.result_url = data.get("full_zip_url", "")
        task.finished_at = time.time()
    elif state == "failed":
        task.status = "failed"
        task.error = data.get("err_msg", "")[:120]
        task.finished_at = time.time()


# ─────────────────────────── 全局状态 ───────────────────────────

class ServerState:
    """服务状态：keys / 用户任务 / token 池 / flash 限速，全部线程安全"""

    def __init__(self, data_dir, admin_key, key_rate, opts):
        self.data_dir = data_dir
        self.lock = threading.RLock()
        self.keys = {}            # key -> {name, admin, created_at}
        self.ratelimit = {}       # key -> deque 时间戳
        self.tasks = {}           # task_id -> UserTask
        self.user_tasks = {}      # key -> set(task_id)
        self.key_rate = key_rate  # 每 key 每分钟请求数
        self.opts = opts          # 解析参数
        self.flash_window = deque()  # flash 通道滑动窗口（IP 限频）
        self.dl_pool = ThreadPoolExecutor(max_workers=opts.download_workers)
        self.running = True
        self.started_at = time.time()
        self.stop_event = threading.Event()   # ★ 后台线程停止信号（健康检查等）
        self.auth_fail_ip = {}    # ip -> deque 鉴权失败时间戳
        self.auth_fail_ban = {}   # ip -> 封禁截止时间
        self.dl_futures = {}      # task_id -> future
        self.tokens = mpool.load_tokens(opts)
        self.pool = mpool.TokenPool(self.tokens, opts.rate,
                                    strategy=getattr(opts, "strategy", "rr"),
                                    ban_threshold=getattr(opts, "ban_threshold", 5),
                                    health_interval=getattr(opts, "health_interval", 300))
        mpool.set_event_log(os.path.join(self.data_dir, "events.jsonl"))   # ★ 结构化事件日志
        self.pool.start_preflight()          # ★ 启动预热探测：无效 key 立即禁用
        self.pool.start_health_check(self.stop_event)   # ★ 熔断 token 定期测活自动恢复
        # ★ 全局细粒度统计
        self.stats = {"tasks_total": 0, "by_status": Counter(),
                      "by_channel": Counter(), "by_model": Counter(),
                      "by_hour": Counter(), "fail_reasons": Counter(),
                      "uploads_bytes": 0, "flash_tasks": 0,
                      "flash_fallback": 0, "api_requests": 0}
        self.user_requests = {}   # key -> 请求计数（含限流拒绝）

        os.makedirs(data_dir, exist_ok=True)
        self._load()   # 先恢复持久化状态
        self.admin_key = admin_key or self._ensure_admin()
        # admin key 注册（_load 之后再注册，避免被覆盖）
        self.keys.setdefault(self.admin_key, {"name": "admin", "admin": True,
                                              "created_at": time.time()})

    # ── 持久化 ──
    def _ensure_admin(self):
        p = os.path.join(self.data_dir, "admin_key.txt")
        if os.path.exists(p):
            return open(p, encoding="utf-8").read().strip()
        k = "sk-admin-" + secrets.token_hex(16)
        with open(p, "w", encoding="utf-8") as f:
            f.write(k)
        log_info(f"管理员 key 已生成: {k}（已保存到 {p}，请妥善保管）")
        return k

    def _save(self):
        try:
            with self.lock:
                data = {
                    "keys": self.keys,
                    "tasks": {tid: ut.to_dict() for tid, ut in self.tasks.items()},
                    "user_tasks": {k: list(v) for k, v in self.user_tasks.items()},
                    # ★ 统计持久化（Counter 转 dict）
                    "stats": {k: dict(v) if isinstance(v, Counter) else v
                              for k, v in self.stats.items()},
                    "user_requests": self.user_requests,
                    "token_stats": [s.to_dict() for s in self.pool.slots],
                }
            # 原子写：tmp + replace，防止多线程并发写损坏
            p = os.path.join(self.data_dir, "state.json")
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception as e:
            log_info(f"[state 保存失败] {str(e)[:80]}")

    def _load(self):
        p = os.path.join(self.data_dir, "state.json")
        if not os.path.exists(p):
            return
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            self.keys = d.get("keys", {})
            self.user_tasks = {k: set(v) for k, v in d.get("user_tasks", {}).items()}
            for tid, td in d.get("tasks", {}).items():
                ut = UserTask.from_dict(td)
                self.tasks[tid] = ut
            # ★ 统计恢复（update 合并，保留默认键）
            sd = d.get("stats")
            if sd:
                for k, v in sd.items():
                    self.stats[k] = Counter(v) if isinstance(v, dict) else v
            self.user_requests = d.get("user_requests", {})
            token_stats = d.get("token_stats")
            if token_stats and hasattr(self, "pool"):
                self.pool.restore_stats(token_stats)
        except Exception as e:
            try:
                os.rename(p, p + f".corrupt_{int(time.time())}")
            except Exception:
                pass
            log_info(f"state.json 损坏已备份，重新初始化: {str(e)[:60]}")

    # ── 鉴权与限流 ──
    def check_key(self, key, need_admin=False, count=True):
        """返回 (ok, msg)。count=False 的端点（下载类）不占限流额度"""
        with self.lock:
            info = self.keys.get(key)
            if not info:
                return False, "无效的 API key"
            if need_admin and not info.get("admin"):
                return False, "需要管理员权限"
            if count:
                dq = self.ratelimit.setdefault(key, deque())
                now = time.time()
                while dq and now - dq[0] > 60:
                    dq.popleft()
                self.stats["api_requests"] += 1
                self.user_requests[key] = self.user_requests.get(key, 0) + 1
                if len(dq) >= self.key_rate:
                    return False, f"请求过于频繁（限 {self.key_rate}/分钟）"
                dq.append(now)
            return True, "ok"

    # ── 鉴权爆破防护：同 IP 60s 内失败 ≥10 次 → 封禁 10 分钟 ──
    def ip_allowed(self, ip):
        with self.lock:
            return self.auth_fail_ban.get(ip, 0) <= time.time()

    def note_auth_fail(self, ip):
        with self.lock:
            now = time.time()
            dq = self.auth_fail_ip.setdefault(ip, deque())
            while dq and now - dq[0] > 60:
                dq.popleft()
            dq.append(now)
            if len(dq) >= 10:
                self.auth_fail_ban[ip] = now + 600
                self.auth_fail_ip.pop(ip, None)
                log_info(f"[鉴权封禁] {ip} 60s 内鉴权失败 10 次，封禁 10 分钟")

    # ── flash 通道限速 ──
    def flash_acquire(self):
        """flash 通道滑动窗口（IP 级限频，保守执行）。成功=True"""
        with self.lock:
            now = time.time()
            while self.flash_window and now - self.flash_window[0] > 60:
                self.flash_window.popleft()
            if len(self.flash_window) >= self.opts.flash_rate:
                return False
            self.flash_window.append(now)
            return True

    # ── 任务操作 ──
    def create_task(self, key, source, kind, local_path=None, extra=None):
        tid = uuid.uuid4().hex[:12]
        t = mpool.Task(source, kind, local_path)
        t.extra = extra
        ut = UserTask(tid, key, t)
        with self.lock:
            self.tasks[tid] = ut
            self.user_tasks.setdefault(key, set()).add(tid)
        return ut

    def find_done_url(self, key, url):
        """URL 结果复用：同 key 已完成且产物在盘的任务"""
        with self.lock:
            for tid, ut in self.tasks.items():
                if (ut.key == key and ut.task.source == url
                        and ut.task.status == "done" and ut.downloaded):
                    return tid
        return None

    def snapshot(self):
        with self.lock:
            return {k: dict(v) for k, v in self.tasks.items()}


class UserTask:
    """用户任务：内部 Task + 用户归属 + 下载状态"""

    def __init__(self, task_id, key, task):
        self.task_id = task_id
        self.key = key
        self.task = task
        self.downloaded = False
        self.out_dir = None
        self.flash_retry = 0      # flash 通道回落 v4 次数

    def to_dict(self):
        return {"task_id": self.task_id, "key": self.key, "downloaded": self.downloaded,
                "out_dir": self.out_dir, "flash_retry": self.flash_retry,
                "task": self.task.to_dict()}

    @staticmethod
    def from_dict(d):
        t = mpool.Task.from_dict(d.get("task", {}))
        ut = UserTask(d.get("task_id"), d.get("key"), t)
        ut.downloaded = d.get("downloaded", False)
        ut.out_dir = d.get("out_dir")
        ut.flash_retry = d.get("flash_retry", 0)
        return ut

    @property
    def status(self):
        return self.task.status


# ─────────────────────────── 后台工作线程 ───────────────────────────

def background_worker(st):
    threads = [
        threading.Thread(target=_submit_loop, args=(st,), daemon=True),
        threading.Thread(target=_poll_loop, args=(st,), daemon=True),
        threading.Thread(target=_dl_loop, args=(st,), daemon=True),
        threading.Thread(target=_quota_observe, args=(st,), daemon=True),
    ]
    for t in threads:
        t.start()
    return threads


def _quota_observe(st):
    """定时查官方 quota 接口，记录观察值（user_left_quota 语义未文档化，仅观察）"""
    st.stats.setdefault("quota_obs", [])
    while st.running:
        try:
            d = mpool.api_get("/quota", st.pool.slots[0].token)
            q = d.get("data", {})
            with st.lock:
                st.stats["quota_obs"].append({
                    "t": time.strftime("%H:%M"),
                    "user_left": q.get("user_left_quota"),
                    "total_left": q.get("total_left_quota")})
                st.stats["quota_obs"] = st.stats["quota_obs"][-60:]
        except Exception:
            pass
        time.sleep(300)   # 5 分钟一次


def _submit_worker(st, ut):
    """单个任务提交（线程池 worker）：flash 优先，失败回落 v4"""
    t = ut.task
    try:
        # ── flash 通道：文件（≤10MB）或 URL（请求体 flash=true）──
        if t.channel == "flash":
            is_file = t.kind == "file" and os.path.getsize(t.local_path) <= FLASH_MAX_SIZE
            is_url = t.kind == "url"
            if (is_file or is_url) and st.flash_acquire():
                tid = (flash_submit(t.local_path, st.opts) if is_file
                       else flash_submit_url(t.source, st.opts))
                if tid == "rate_limited":
                    return   # 限流，下轮重试
                if tid:
                    t.batch_id = tid
                    t.status = "submitted"
                    with st.lock:
                        st.stats["flash_tasks"] += 1
                        if is_file:
                            st.stats["uploads_bytes"] += os.path.getsize(t.local_path)
                    log_info(f"[flash] {ut.task_id} 已提交 task={tid[:8]} ({t.source[:40]})")
                    return
            # flash 失败/超限 → 回落 v4（channel 改为 v4，不再尝试 flash）
            t.channel = "v4"
        # ── v4 通道：token 池 ──
        if mpool.submit_task(st.pool, t, st.opts):
            log_info(f"[v4] {ut.task_id} 已提交 batch={t.batch_id[:8]} ({t.source[:40]})")
    except Exception as e:
        log_info(f"[提交异常] {ut.task_id}: {str(e)[:80]}")


def _submit_loop(st):
    """多线程批量提交（token 池控速 + flash 全局限速，网络延迟并行化）"""
    pool = ThreadPoolExecutor(max_workers=st.opts.submit_workers)
    while st.running:
        with st.lock:
            cand = [ut for ut in st.tasks.values() if ut.task.status == "pending"]
        if not cand:
            time.sleep(1)
            continue
        # 每轮最多提交 submit_workers*2 个（避免积压过多 pending）
        batch = cand[: st.opts.submit_workers * 2]
        futs = [pool.submit(_submit_worker, st, ut) for ut in batch]
        for f in futs:
            try:
                f.result()
            except Exception:
                pass
        st._save()
        time.sleep(0.3)


def _poll_one(st, ut):
    """单个任务轮询（线程池 worker）：flash 回落判断 + 双通道轮询 + 终态记账"""
    t = ut.task
    now = time.time()
    try:
        t.last_poll = now
        if t.channel == "flash":
            # ★ flash 超时判断：先刷一次状态，仍在正常处理则继续等（避免误回落）
            if now - t.created_at > 120 and ut.flash_retry < 2:
                flash_poll(t)
                if t.status in ("done", "failed"):
                    pass   # flash 已终态 → 走下方终态处理
                elif t.status == "submitted" and t.poll_fails < 2:
                    return   # flash 仍在正常处理，继续等
                else:
                    # flash 卡死/异常 → 回落 v4 重提
                    ut.flash_retry += 1
                    t.channel = "v4"
                    t.batch_id = None
                    t.status = "pending"
                    t.poll_fails = 0
                    with st.lock:
                        st.stats["flash_fallback"] += 1
                    log_info(f"[flash 回落] {ut.task_id} 120s 未完成/异常 → 转 v4 重提")
                    return
            else:
                flash_poll(t)
        else:
            mpool.poll_batch(t)
        if t.status in ("done", "failed"):
            # ★ flash 服务端临时故障（unavailable/稍后再试）→ 回落 v4 重试
            if (t.status == "failed" and t.channel == "flash"
                    and ut.flash_retry < 2
                    and any(k in (t.error or "") for k in ("unavailable", "try again", "temporarily"))):
                ut.flash_retry += 1
                t.channel = "v4"
                t.batch_id = None
                t.status = "pending"
                t.poll_fails = 0
                log_info(f"[flash 服务异常回落] {ut.task_id} → 转 v4 重试")
                return
            log_info(f"[任务] {ut.task_id} → {t.status} ({t.source[:40]})")
            record_final(st, ut)   # ★ 终态记账
    except Exception:
        pass


def _poll_loop(st):
    """周期性并发轮询 submitted 任务（v4 + flash 双通道）"""
    pool = ThreadPoolExecutor(max_workers=st.opts.poll_workers)
    while st.running:
        with st.lock:
            cand = [ut for ut in st.tasks.values() if ut.task.status == "submitted"]
        now = time.time()
        todo = [ut for ut in cand
                if now - ut.task.last_poll >= mpool.POLL_MIN_DELAY
                and (ut.task.poll_fails < 3
                     or now - ut.task.last_poll >= mpool.POLL_FAIL_SKIP)]
        if todo:
            futs = [pool.submit(_poll_one, st, ut) for ut in todo]
            for f in futs:
                try:
                    f.result()
                except Exception:
                    pass
            st._save()
            time.sleep(2)
        elif cand:
            time.sleep(2)      # 有任务但未到轮询间隔
        else:
            time.sleep(30)     # 空闲降频：Render 免费层可休眠，不耗额度


def _dl_loop(st):
    """下载已完成任务（异步线程池）"""
    while st.running:
        with st.lock:
            cand = [ut for ut in st.tasks.values()
                    if ut.task.status == "done" and not ut.downloaded
                    and ut.task_id not in st.dl_futures]
        for ut in cand:
            fut = st.dl_pool.submit(_download_one, st, ut)
            st.dl_futures[ut.task_id] = fut
        for tid in [tid for tid, f in st.dl_futures.items() if f.done()]:
            st.dl_futures.pop(tid, None)
        time.sleep(1)


def _download_one(st, ut):
    try:
        target = mpool.download_result(ut.task, st.opts.out_dir)
        if target:
            ut.downloaded = True
            ut.out_dir = target
            log_info(f"[下载] {ut.task_id} 产物落盘 → {target}")
            # ★ 页数统计：v4 接口无 extract_progress，从 layout.json 的 pdf_info 提取
            pages = 0
            lp = os.path.join(target, "layout.json")
            if ut.task.channel == "v4" and os.path.exists(lp):
                try:
                    with open(lp, encoding="utf-8") as f:
                        lj = json.load(f)
                    pi = lj.get("pdf_info")
                    if isinstance(pi, list):
                        pages = len(pi)
                except Exception:
                    pass
            if pages:
                with st.lock:
                    st.pool.add_pages(ut.task.token, pages)   # 页数已由 pool 层记账
            st._save()
    except Exception as e:
        log_info(f"[下载异常] {ut.task_id}: {str(e)[:80]}")


def log_info(msg):
    try:
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)
    except Exception:
        pass


def record_final(st, ut):
    """任务终态记账：全局统计 + token 级回写（解析成功/失败/页数）"""
    t = ut.task
    with st.lock:
        st.stats["by_status"][t.status] += 1
        st.stats["by_channel"][t.channel] += 1
        hour = time.strftime("%m-%d %H")
        st.stats["by_hour"][hour] += 1
        if t.status == "done":
            pages = (t.progress or {}).get("total_pages", 0) or 0
            if t.channel == "v4":
                st.pool.mark_parse(t.token, True, pages)
        elif t.status == "failed":
            if t.channel == "v4":
                st.pool.mark_parse(t.token, False)
            reason = (t.error or "unknown")[:40]
            st.stats["fail_reasons"][reason] += 1


# ─────────────────────────── HTTP Handler ───────────────────────────

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    st = None

    # ── 工具 ──
    def _send_json(self, code, data, http_code=200):
        body = json.dumps({"code": code, "msg": "ok" if code == 0 else data,
                           "data": data if code == 0 else None},
                          ensure_ascii=False).encode()
        self.send_response(http_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # ★ CORS 默认关闭（API 面向服务端/CLI 调用）；--cors 显式开启
        if getattr(self.st, "opts", None) and getattr(self.st.opts, "cors", False):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data, ctype, filename=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def _auth(self, need_admin=False, count=True):
        ip = self.client_address[0] if self.client_address else "?"
        # ★ 鉴权爆破防护：封禁中的 IP 直接拒绝
        if not self.st.ip_allowed(ip):
            return None, "IP 已临时封禁（鉴权失败过多），10 分钟后恢复"
        h = self.headers.get("Authorization", "")
        if not h.startswith("Bearer "):
            self.st.note_auth_fail(ip)
            return None, "缺少 Authorization: Bearer <key>"
        key = h[7:].strip()
        ok, msg = self.st.check_key(key, need_admin, count)
        if not ok:
            self.st.note_auth_fail(ip)
            return None, msg
        return key, msg

    def _read_body(self, max_size=100 * 1024 * 1024):
        try:
            ln = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None, "Content-Length 非法"
        if ln > max_size:
            return None, f"请求体过大（>{max_size // 1024 // 1024}MB）"
        return self.rfile.read(ln), None

    def log_message(self, fmt, *args):
        # ★ 审计日志：仅记录非 2xx 响应（含客户端 IP）
        try:
            if args and str(args[0]) not in ("200", "301", "302", "304"):
                ip = self.client_address[0] if self.client_address else "?"
                log_info(f"[HTTP {args[0]}] {self.command} {self.path[:100]} from {ip}")
        except Exception:
            pass

    # ── 路由 ──
    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/v1/tasks":
                self._route_tasks_list()
            elif path.startswith("/v1/tasks/"):
                self._route_task_detail(path)
            elif path == "/v1/keys":
                self._route_keys_list()
            elif path.startswith("/v1/keys/"):
                self._route_key_delete(path)
            elif path == "/v1/stats":
                self._route_stats()
            elif path == "/v1/metrics":
                self._route_metrics()
            elif path == "/v1/stats/tokens":
                self._route_stats_tokens()
            elif path == "/v1/stats/trends":
                self._route_stats_trends()
            elif path == "/v1/history":
                self._route_history()
            elif path == "/dashboard":
                return self._page_dashboard()
            elif path == "/v1/me":
                self._route_me()
            elif path in ("/", "/health"):
                self._send_json(0, {"service": "mineru-api-server", "version": VERSION,
                                    "endpoints": ["POST /v1/tasks", "GET /v1/tasks/{id}",
                                                  "GET /v1/tasks/{id}/result",
                                                  "GET /v1/tasks/{id}/file/{name}",
                                                  "GET /v1/tasks/{id}/zip",
                                                  "POST /v1/tasks/{id}/retry",
                                                  "POST /v1/crawl", "GET /v1/me",
                                                  "POST /v1/keys", "GET /v1/stats",
                                                  "GET /v1/stats/tokens", "GET /v1/stats/trends",
                                                  "GET /v1/metrics"]})
            else:
                self._send_json(404, "not found", 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            log_info(f"[GET {path}] 异常:\n{traceback.format_exc()}")
            self._send_json(500, f"internal error: {str(e)[:120]}", 500)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/v1/tasks":
                self._route_task_create()
            elif path.startswith("/v1/tasks/"):
                self._route_task_post(path)
            elif path == "/v1/crawl":
                self._route_crawl()
            elif path == "/v1/keys":
                self._route_key_create()
            else:
                self._send_json(404, "not found", 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            log_info(f"[POST {path}] 异常:\n{traceback.format_exc()}")
            self._send_json(500, f"internal error: {str(e)[:120]}", 500)

    def do_DELETE(self):
        """DELETE /v1/tasks/{id}：删除任务记录 + 产物目录"""
        path = self.path.split("?")[0]
        try:
            if not path.startswith("/v1/tasks/"):
                return self._send_json(404, "not found", 404)
            key, msg = self._auth()
            if not key:
                return self._send_json(401, msg, 401)
            tid = path[len("/v1/tasks/"):].split("/")[0]
            ut = self.st.tasks.get(tid)
            if not ut or ut.key != key:
                return self._send_json(404, "任务不存在", 404)
            # 删除产物目录
            if ut.out_dir and os.path.isdir(ut.out_dir):
                import shutil
                shutil.rmtree(ut.out_dir, ignore_errors=True)
            with self.st.lock:
                del self.st.tasks[tid]
                self.st.user_tasks.get(key, set()).discard(tid)
            self.st._save()
            self._send_json(0, {"deleted": tid, "source": ut.task.source[:60]})
        except BrokenPipeError:
            pass
        except Exception as e:
            log_info(f"[DELETE {path}] 异常:\n{traceback.format_exc()}")
            self._send_json(500, f"internal error: {str(e)[:120]}", 500)

    # ── /v1/tasks ──
    def _route_tasks_list(self):
        key, msg = self._auth()
        if not key:
            return self._send_json(401, msg, 401)
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        try:
            limit = min(max(int(q.get("limit", ["50"])[0]), 1), 200)
            offset = max(int(q.get("offset", ["0"])[0]), 0)
        except ValueError:
            return self._send_json(400, "limit/offset 必须为数字", 400)
        status_f = q.get("status", [None])[0]
        with self.st.lock:
            ids = sorted(self.st.user_tasks.get(key, set()))
        out = []
        for tid in ids[offset:offset + limit]:
            ut = self.st.tasks.get(tid)
            if not ut:
                continue
            if status_f and ut.status != status_f:
                continue
            out.append({"task_id": tid, "status": ut.status, "source": ut.task.source,
                        "channel": ut.task.channel, "created_at": ut.task.created_at,
                        "error": ut.task.error[:80]})
        self._send_json(0, {"total": len(ids), "tasks": out})

    def _create_tasks(self, key, body):
        """核心任务创建逻辑（不读 HTTP body，供 /v1/tasks 与 /v1/crawl 复用）"""
        urls = body.get("urls") or []
        files = body.get("files") or []
        if not urls and not files:
            return None, "urls 与 files 不能同时为空"
        if len(urls) + len(files) > 50:
            return None, "单次最多提交 50 个"
        # ★ 请求参数绑定到任务（不再改共享 opts，消除并发竞态）
        task_opts = {k: body[k] for k in ("language", "pages", "extra_formats")
                     if body.get(k)}
        for flag in ("formula", "table", "ocr"):
            if flag in body:
                task_opts[flag] = bool(body[flag])
        task_model = body.get("model") or None

        ids, reused = [], []
        try:
            fresh = bool(body.get("fresh", False))
            # ★ URL flash 通道（请求体 flash=true 时 URL 走免 token agent API）
            url_flash = bool(body.get("flash", False))
            for u in urls:
                u = str(u).strip()
                if not u:
                    continue
                # ★ SSRF 防护：仅允许公网 http/https，内网/元数据/保留段拒绝
                if not is_safe_url(u):
                    return None, f"URL 不合法或被拒绝（仅允许公网 http/https）: {u[:80]}"
                if not fresh:
                    old = self.st.find_done_url(key, u)
                    if old:
                        reused.append(old)
                        continue
                ut = self.st.create_task(key, u, "url")
                ut.task.model = task_model
                ut.task.task_opts = task_opts or None
                if url_flash:
                    ut.task.channel = "flash"   # URL 走 flash 通道
                # ★ 统计：任务创建记账
                with self.st.lock:
                    self.st.stats["tasks_total"] += 1
                    self.st.stats["by_model"][task_model or self.st.opts.model] += 1
                ids.append(ut.task_id)
            for f in files:
                name = str(f.get("name", "file.bin")).strip()
                # ★ 路径穿越防护：拒绝含路径分隔符的名字 + 扩展名白名单
                if not name or "/" in name or "\\" in name or name in (".", ".."):
                    return None, f"文件名不合法（不能包含路径）: {name[:60]}"
                if not name.lower().endswith(SAFE_UPLOAD_EXTS):
                    return None, f"文件名不合法（仅允许 {SAFE_UPLOAD_EXTS}）: {name[:60]}"
                data = base64.b64decode(f.get("data", ""))
                # ★ 文件大小限制
                if len(data) > MAX_UPLOAD_SIZE:
                    return None, f"文件过大（>{MAX_UPLOAD_SIZE // 1024 // 1024}MB）: {name[:40]}"
                local = os.path.join(self.st.data_dir, "uploads", key[:8], name)
                os.makedirs(os.path.dirname(local), exist_ok=True)
                with open(local, "wb") as fp:
                    fp.write(data)
                extra = {}
                if f.get("pages"):
                    extra["page_ranges"] = str(f["pages"])
                if f.get("ocr") is not None:
                    extra["is_ocr"] = bool(f["ocr"])
                if f.get("data_id"):
                    extra["data_id"] = str(f["data_id"])
                ut = self.st.create_task(key, name, "file", local_path=local,
                                         extra=extra or None)
                ut.task.model = task_model
                ut.task.task_opts = task_opts or None
                # ★ 小文件自动走 flash 通道（免 token）
                if getattr(self.st.opts, "flash", True) and os.path.getsize(local) <= FLASH_MAX_SIZE:
                    ut.task.channel = "flash"
                with self.st.lock:
                    self.st.stats["tasks_total"] += 1
                    self.st.stats["by_model"][task_model or self.st.opts.model] += 1
                ids.append(ut.task_id)
        except Exception as e:
            return None, f"创建任务失败: {str(e)[:100]}"
        self.st._save()
        return {"task_ids": ids, "reused_ids": reused,
                "tasks": len(ids), "reused": len(reused)}, None

    def _route_task_create(self):
        key, msg = self._auth()
        if not key:
            return self._send_json(401, msg, 401)
        raw, err = self._read_body()
        if err:
            return self._send_json(400, err, 400)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._send_json(400, "请求体不是合法 JSON", 400)
        data, err = self._create_tasks(key, body)
        if err:
            return self._send_json(400, err, 400)
        self._send_json(0, data)

    def _route_task_post(self, path):
        """POST /v1/tasks/{id}/retry"""
        key, msg = self._auth()
        if not key:
            return self._send_json(401, msg, 401)
        parts = path[len("/v1/tasks/"):].split("/")
        if len(parts) != 2 or parts[1] != "retry":
            return self._send_json(404, "not found", 404)
        ut = self.st.tasks.get(parts[0])
        if not ut or ut.key != key:
            return self._send_json(404, "任务不存在", 404)
        if ut.status != "failed":
            return self._send_json(0, {"task_id": ut.task_id, "status": ut.status,
                                       "message": "仅 failed 任务可重试"})
        # 重置为 pending，重新提交
        t = ut.task
        t.status = "pending"
        t.batch_id = None
        t.token = None
        t.error = ""
        t.result_url = ""
        t.channel = "v4"
        t.attempts = 0
        t.progress = None
        t.poll_fails = 0
        ut.downloaded = False
        ut.out_dir = None
        self.st._save()
        self._send_json(0, {"task_id": ut.task_id, "status": "pending",
                            "message": "已重置，等待重新提交"})

    def _route_task_detail(self, path):
        parts = path[len("/v1/tasks/"):].split("/")
        tid, sub = parts[0], parts[1] if len(parts) > 1 else None
        # 下载类端点（result/zip/file）鉴权但不占限流额度
        key, msg = self._auth(count=(sub not in ("result", "zip", "file")))
        if not key:
            return self._send_json(401, msg, 401)
        ut = self.st.tasks.get(tid)
        if not ut or ut.key != key:
            return self._send_json(404, "任务不存在", 404)
        if sub is None:
            self._send_json(0, {"task_id": tid, "status": ut.status, "source": ut.task.source,
                                "channel": ut.task.channel, "batch_id": ut.task.batch_id,
                                "created_at": ut.task.created_at,
                                "finished_at": ut.task.finished_at,
                                "progress": ut.task.progress,
                                "error": ut.task.error[:120], "downloaded": ut.downloaded})
        elif sub == "result":
            if ut.status != "done":
                return self._send_json(0, {"status": ut.status, "message": "任务未完成"})
            if not ut.downloaded or not ut.out_dir:
                return self._send_json(0, {"status": "done", "downloaded": False,
                                           "message": "产物下载中，请稍后重试"})
            self._route_result(ut)
        elif sub == "zip":
            if ut.status != "done":
                return self._send_json(0, {"status": ut.status, "message": "任务未完成"})
            if not ut.downloaded or not ut.out_dir:
                return self._send_json(0, {"status": "done", "downloaded": False,
                                           "message": "产物下载中，请稍后重试"})
            self._route_zip(ut)
        elif sub == "file":
            self._route_file(ut, "/".join(parts[2:]))
        else:
            self._send_json(404, "unknown sub-route", 404)

    def _route_result(self, ut):
        out = {"task_id": ut.task_id, "status": "done", "source": ut.task.source,
               "channel": ut.task.channel, "files": []}
        if ut.out_dir and os.path.isdir(ut.out_dir):
            for root, _, fns in os.walk(ut.out_dir):
                for fn in sorted(fns):
                    if fn == "meta.json":
                        continue
                    fp = os.path.join(root, fn)
                    out["files"].append({"name": os.path.relpath(fp, ut.out_dir),
                                         "size": os.path.getsize(fp)})
            md = os.path.join(ut.out_dir, "full.md")
            if os.path.exists(md):
                with open(md, encoding="utf-8") as f:
                    out["markdown"] = f.read()
        self._send_json(0, out)

    def _route_zip(self, ut):
        # ★ 产物打包上限（防内存打爆）
        total = sum(os.path.getsize(os.path.join(r, fn))
                    for r, _, fns in os.walk(ut.out_dir) for fn in fns)
        if total > 1024 * 1024 * 1024:
            return self._send_json(400, "产物过大（>1GB），请按文件逐个下载", 400)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, fns in os.walk(ut.out_dir):
                for fn in fns:
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, ut.out_dir))
        self._send_bytes(buf.getvalue(), "application/zip", f"{ut.task_id}_result.zip")

    def _route_file(self, ut, name):
        # ★ 路径穿越防护：name 必须解析在产物目录内
        if not ut.out_dir or not name:
            return self._send_json(404, "文件不存在", 404)
        fp = safe_join(ut.out_dir, name)
        if not fp or not os.path.isfile(fp):
            return self._send_json(404, "文件不存在", 404)
        with open(fp, "rb") as f:
            data = f.read()
        ctype = "image/jpeg" if name.lower().endswith((".jpg", ".jpeg")) else \
                "text/markdown; charset=utf-8" if name.endswith(".md") else \
                "application/octet-stream"
        self._send_bytes(data, ctype, safe_filename(name))

    # ── /v1/crawl（网页转 md）──
    def _route_crawl(self):
        key, msg = self._auth()
        if not key:
            return self._send_json(401, msg, 401)
        raw, err = self._read_body()
        if err:
            return self._send_json(400, err, 400)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._send_json(400, "请求体不是合法 JSON", 400)
        urls = body.get("urls") or []
        if not urls:
            return self._send_json(400, "urls 不能为空", 400)
        if len(urls) > 20:
            return self._send_json(400, "单次最多爬取 20 个 URL", 400)
        # 复用任务创建逻辑（model=html 绑定到任务，不碰共享 opts）
        body["model"] = "html"
        data, err = self._create_tasks(key, body)
        if err:
            return self._send_json(400, err, 400)
        self._send_json(0, data)

    # ── /v1/me ──
    def _route_me(self):
        key, msg = self._auth()
        if not key:
            return self._send_json(401, msg, 401)
        with self.st.lock:
            info = self.st.keys.get(key, {})
            ids = self.st.user_tasks.get(key, set())
            statuses = Counter()
            channels = Counter()
            models = Counter()
            for tid in ids:
                ut = self.st.tasks.get(tid)
                if ut:
                    statuses[ut.status] += 1
                    channels[ut.task.channel] += 1
                    models[ut.task.model or "default"] += 1
            total = sum(statuses.values())
            reqs = self.st.user_requests.get(key, 0)
        self._send_json(0, {"key": key[:12] + "...", "name": info.get("name", ""),
                            "tasks_total": total,
                            "tasks_by_status": dict(statuses),
                            "tasks_by_channel": dict(channels),
                            "tasks_by_model": dict(models),
                            "api_requests": reqs,
                            "rate_limit_per_min": self.st.key_rate})

    # ── /v1/keys（admin）──
    def _route_key_create(self):
        key, msg = self._auth(need_admin=True)
        if not key:
            return self._send_json(401, msg, 401)
        raw, err = self._read_body(max_size=64 * 1024)
        if err:
            return self._send_json(400, err, 400)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._send_json(400, "请求体不是合法 JSON", 400)
        name = str(body.get("name", "user"))[:32]
        new_key = "sk-" + secrets.token_hex(16)
        with self.st.lock:
            self.st.keys[new_key] = {"name": name, "admin": False,
                                     "created_at": time.time()}
        self.st._save()
        self._send_json(0, {"key": new_key, "name": name})

    def _route_keys_list(self):
        key, msg = self._auth(need_admin=True)
        if not key:
            return self._send_json(401, msg, 401)
        with self.st.lock:
            out = [{"key": k, "name": v.get("name"), "admin": v.get("admin", False),
                    "created_at": v.get("created_at"),
                    "tasks": len(self.st.user_tasks.get(k, set()))}
                   for k, v in self.st.keys.items()]
        self._send_json(0, {"keys": out})

    def _route_key_delete(self, path):
        key, msg = self._auth(need_admin=True)
        if not key:
            return self._send_json(401, msg, 401)
        target = path[len("/v1/keys/"):]
        with self.st.lock:
            if target not in self.st.keys:
                return self._send_json(404, "key 不存在", 404)
            del self.st.keys[target]
            self.st.ratelimit.pop(target, None)
            self.st.user_tasks.pop(target, None)
        self.st._save()
        self._send_json(0, {"deleted": target})

    # ── /v1/stats（admin）──
    # ── /v1/metrics（admin，Prometheus 文本格式）──
    def _route_metrics(self):
        key, msg = self._auth(need_admin=True)
        if not key:
            return self._send_json(401, msg, 401)
        ps = self.st.pool.stats()
        lines = [
            "# HELP mineru_tokens token 池总数", "# TYPE mineru_tokens gauge",
            f"mineru_tokens {ps['tokens']}",
            "# HELP mineru_ok 提交成功数", "# TYPE mineru_ok counter",
            f"mineru_ok {ps['ok']}",
            "# HELP mineru_err 提交失败数", "# TYPE mineru_err counter",
            f"mineru_err {ps['err']}",
            "# HELP mineru_rate_limited 429 次数", "# TYPE mineru_rate_limited counter",
            f"mineru_rate_limited {ps['rate_limited']}",
            "# HELP mineru_suspended 配额暂停次数", "# TYPE mineru_suspended counter",
            f"mineru_suspended {ps['suspended']}",
            "# HELP mineru_banned_now 当前熔断数", "# TYPE mineru_banned_now gauge",
            f"mineru_banned_now {ps['banned_now']}",
            "# HELP mineru_auth_failed 无效 key 数", "# TYPE mineru_auth_failed gauge",
            f"mineru_auth_failed {ps['auth_failed']}",
            "# HELP mineru_parse_ok 解析成功数", "# TYPE mineru_parse_ok counter",
            f"mineru_parse_ok {ps['parse_ok']}",
            "# HELP mineru_parse_fail 解析失败数", "# TYPE mineru_parse_fail counter",
            f"mineru_parse_fail {ps['parse_fail']}",
            "# HELP mineru_pages_parsed 累计解析页数", "# TYPE mineru_pages_parsed counter",
            f"mineru_pages_parsed {ps['pages_parsed']}",
            "# HELP mineru_bytes_uploaded 累计上传字节", "# TYPE mineru_bytes_uploaded counter",
            f"mineru_bytes_uploaded {ps['bytes_uploaded']}",
            "# HELP mineru_avg_success_rate 池平均成功率", "# TYPE mineru_avg_success_rate gauge",
            f"mineru_avg_success_rate {ps['avg_success_rate']}",
            "# HELP mineru_latency_p99 提交延迟 p99(ms)", "# TYPE mineru_latency_p99 gauge",
            f"mineru_latency_p99 {ps.get('latency_ms', {}).get('p99') or 0}",
            "# HELP mineru_api_requests 累计 API 请求数", "# TYPE mineru_api_requests counter",
            f"mineru_api_requests {self.st.stats['api_requests']}",
            "# HELP mineru_tasks_total 累计任务数", "# TYPE mineru_tasks_total counter",
            f"mineru_tasks_total {self.st.stats['tasks_total']}",
        ]
        body = "\n".join(lines) + "\n"
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())
        except Exception:
            pass

    def _route_stats(self):
        key, msg = self._auth(need_admin=True)
        if not key:
            return self._send_json(401, msg, 401)
        st = Counter(ut.status for ut in self.st.tasks.values())
        ps = self.st.pool.stats()
        # 最近 24 小时趋势
        now = time.time()
        trends = {}
        for i in range(24):
            h = time.strftime("%m-%d %H", time.localtime(now - (23 - i) * 3600))
            trends[h] = self.st.stats["by_hour"].get(h, 0)
        self._send_json(0, {
            "uptime": int(time.time() - self.st.started_at),
            "tokens": {k: v for k, v in ps.items() if k != "detail"},
            "quota": ps.get("daily", {}),
            "quota_obs": self.st.stats.get("quota_obs", [])[-10:],
            "flash": {"rate_per_min": getattr(self.st.opts, "flash_rate", 0),
                      "tasks": self.st.stats["flash_tasks"],
                      "fallback_to_v4": self.st.stats["flash_fallback"]},
            "tasks": {"total": len(self.st.tasks), **dict(st)},
            "stats": {"tasks_total": self.st.stats["tasks_total"],
                       "by_status": dict(self.st.stats["by_status"]),
                       "by_channel": dict(self.st.stats["by_channel"]),
                       "by_model": dict(self.st.stats["by_model"]),
                       "pages_parsed": ps.get("pages_parsed", 0),
                       "uploads_bytes": self.st.stats["uploads_bytes"],
                       "api_requests": self.st.stats["api_requests"],
                       "fail_reasons": dict(self.st.stats["fail_reasons"].most_common(10))},
            "trends_24h": trends,
            "users": len([k for k, v in self.st.keys.items() if not v.get("admin")]),
        })

    def _route_stats_tokens(self):
        """GET /v1/stats/tokens：各 token 完整明细（admin）"""
        key, msg = self._auth(need_admin=True)
        if not key:
            return self._send_json(401, msg, 401)
        ps = self.st.pool.stats()
        detail = sorted(ps["detail"], key=lambda d: -d["parse_ok"])
        self._send_json(0, {"summary": {k: v for k, v in ps.items() if k != "detail"},
                            "tokens": detail})

    def _route_stats_trends(self):
        """GET /v1/stats/trends：按小时/按天趋势（admin）"""
        key, msg = self._auth(need_admin=True)
        if not key:
            return self._send_json(401, msg, 401)
        by_hour = dict(sorted(self.st.stats["by_hour"].items()))
        by_day = {}
        for h, n in by_hour.items():
            d = h[:5]
            by_day[d] = by_day.get(d, 0) + n
        self._send_json(0, {"by_hour": by_hour, "by_day": by_day})

    # ── /dashboard（内嵌监控页，无需鉴权，数据接口仍鉴权）──
    def _page_dashboard(self):
        body = dashboard_page.PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── /v1/history（云端 GitHub 归档历史，admin）──
    _hist_cache = {}  # fname -> (expire_at, text|None)

    def _raw_history(self, fname):
        hit = Handler._hist_cache.get(fname)
        if hit and hit[0] > time.time():
            return hit[1]
        text = None
        try:
            url = f"https://raw.githubusercontent.com/ZhangCurosr/paper-notes/main/{fname}"
            req = urllib.request.Request(url, headers={"User-Agent": "mineru-api-server"})
            text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        except Exception:
            text = None
        Handler._hist_cache[fname] = (time.time() + 300, text)
        if len(Handler._hist_cache) > 100:
            Handler._hist_cache.clear()
        return text

    def _route_history(self):
        """GET /v1/history?days=7：历史任务/配额/错误（从云端 GitHub 归档聚合，admin）"""
        key, msg = self._auth(need_admin=True)
        if not key:
            return self._send_json(401, msg, 401)
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        try:
            days = min(max(int(q.get("days", ["7"])[0]), 1), 90)
        except ValueError:
            return self._send_json(400, "days 必须为数字", 400)
        out = []
        today = datetime.date.today()
        for i in range(days - 1, -1, -1):
            d = today - datetime.timedelta(days=i)
            fname = f"data/history/{d.strftime('%Y-%m-%d')}.jsonl"
            text = self._raw_history(fname)
            if not text:
                continue
            agg = {"date": d.strftime("%m-%d"), "submits": 0, "pages": 0,
                   "ok": 0, "err": 0, "files_left": None, "hours": {}}
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                agg["submits"] += r.get("submits", 0)
                agg["pages"] += r.get("pages", 0)
                agg["ok"] += r.get("ok", 0)
                agg["err"] += r.get("err", 0)
                if r.get("files_left") is not None:
                    agg["files_left"] = r.get("files_left")
                agg["hours"][r.get("hour", "")] = r.get("submits", 0)
            out.append(agg)
        self._send_json(0, {"days": out})


# ─────────────────────────── 启动 ───────────────────────────

def main():
    ap = argparse.ArgumentParser(description="MinerU API 服务（token 池调度器封装）",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（0.0.0.0 对外）")
    ap.add_argument("--port", type=int, default=8900, help="监听端口")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="服务数据目录（keys/state/uploads）")
    ap.add_argument("--admin-key", default=os.environ.get("MINERU_ADMIN_KEY"),
                    help="管理员 key（缺省自动生成并保存；可设 MINERU_ADMIN_KEY 环境变量）")
    ap.add_argument("--tokens", help="逗号分隔 token（缺省读 MINERU_TOKENS 环境变量 / mineru_accounts.csv）")
    ap.add_argument("--key-rate", type=int, default=60, help="每用户 key 每分钟请求上限")
    ap.add_argument("--cors", action="store_true",
                    help="开启 CORS（默认关闭；仅浏览器直连调试时需要）")
    ap.add_argument("--out-dir", default=mpool.DEFAULT_OUT, help="产物输出目录")
    # token 池参数
    ap.add_argument("--rate", type=int, default=40, help="token 池每 token 每分钟提交数")
    ap.add_argument("--strategy", choices=["rr", "weighted", "score"],
                    default=os.environ.get("MINERU_STRATEGY", "rr"),
                    help="调度策略：rr=轮转 / weighted=平滑加权轮询 / score=成功率+延迟健康度（可设 MINERU_STRATEGY）")
    ap.add_argument("--ban-threshold", type=int,
                    default=int(os.environ.get("MINERU_BAN_THRESHOLD", "5") or 5),
                    help="连续失败熔断阈值（达阈值指数退避禁用，健康检查自动恢复）")
    ap.add_argument("--health-interval", type=int,
                    default=int(os.environ.get("MINERU_HEALTH_INTERVAL", "300") or 300),
                    help="健康检查间隔秒（0=关闭）")
    ap.add_argument("--model", choices=["pipeline", "vlm", "html"], default="pipeline")
    ap.add_argument("--formula", action="store_true")
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--language", help="默认语言（en/zh 等）")
    ap.add_argument("--download-workers", type=int, default=8)
    ap.add_argument("--submit-workers", type=int, default=8, help="并行提交线程数")
    ap.add_argument("--poll-workers", type=int, default=8, help="并发轮询线程数")
    # flash 免 token 通道
    ap.add_argument("--no-flash", action="store_true",
                    help="禁用 flash 免 token 通道（默认启用，≤10MB 本地文件自动走）")
    ap.add_argument("--flash-rate", type=int, default=20,
                    help="flash 通道每分钟提交上限（IP 级限频，保守值）")
    args = ap.parse_args()
    args.flash = not args.no_flash
    # ★ admin key 强度检查
    if args.admin_key and len(args.admin_key) < 16:
        log_info("警告: admin key 过短（<16 字符），建议使用随机长 key")
    # server 的 --tokens 是逗号分隔列表（与 pool CLI 的文件路径语义区分）
    if args.tokens:
        tl = [t.strip() for t in args.tokens.split(",") if t.strip()]
        if tl:
            os.environ["MINERU_TOKENS"] = ",".join(tl)

    os.makedirs(args.out_dir, exist_ok=True)
    faulthandler.dump_traceback_later(60, repeat=True)
    st = ServerState(args.data_dir, args.admin_key, args.key_rate, args)
    Handler.st = st
    background_worker(st)

    # 恢复未完成任务
    recovered = {"pending": 0, "submitted": 0, "done": 0}
    for ut in st.tasks.values():
        s = ut.task.status
        if s == "done":
            ut.downloaded = ut.out_dir and os.path.isdir(ut.out_dir)
            if not ut.downloaded:
                recovered["done"] += 1
        elif s == "submitted":
            recovered["submitted"] += 1
        elif s == "pending":
            recovered["pending"] += 1
    log_info(f"恢复任务: {recovered}")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    log_info(f"MinerU API 服务 v{VERSION} 启动: http://{args.host}:{args.port}")
    log_info(f"token 池: {len(st.tokens)} 个 | flash 通道: {'开' if args.flash else '关'}"
             f"（{args.flash_rate}/min）| 产物目录: {args.out_dir}")
    log_info(f"POST /v1/keys 创建用户 key → 用户持 key 调 /v1/tasks")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log_info("服务停止")
    finally:
        st.running = False
        st._save()
        httpd.server_close()


if __name__ == "__main__":
    main()
