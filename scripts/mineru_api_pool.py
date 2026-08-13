#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MinerU API Token 池轮询调度器
==============================
利用 mineru_batch.py 批量注册积累的多个 API token，突破单 token 提交限流
（实测：滑动窗口 ~50 次/分钟/token，per-token 独立 → N 个 token 吞吐 ×N）。

功能：
  - URL 任务批量提交（/api/v4/extract/task/batch）
  - 本地文件批量上传（/api/v4/file-urls/batch + PUT）
  - Token 池滑动窗口限速 + 429 自适应退避/冷却/轮换
  - 多线程并行提交（限速由 token 池窗口控制，网络延迟并行化）
  - 结果轮询（提交后延迟首查 + 每轮限量 + 连续失败暂停保护）
  - 产物 zip 下载解压落盘（full.md / layout.json / images/ 等）
  - 断点续跑：state.json 持久化任务状态，--resume 恢复（done 跳过 / submitted 续轮询）
  - 汇总报告：summary.json（任务全量状态 + token 使用统计）
  - URL 去重（重复提交命中服务端缓存，秒出）
  - 每 token 日配额耗尽（-60018/-60019）自动暂停该 token

用法：
  python scripts/mineru_api_pool.py --url-file urls.txt --count 50
  python scripts/mineru_api_pool.py --input-dir ./docs --out-dir ./mineru_out
  python scripts/mineru_api_pool.py --resume --out-dir ./mineru_out   # 续跑上次中断
  python scripts/mineru_api_pool.py --token sk-xxx --urls "https://a.pdf,https://b.pdf"

Token 来源（按优先级）：
  1. --token / --tokens 文件
  2. 环境变量 MINERU_TOKENS（逗号分隔）
  3. 项目根目录 mineru_accounts.csv 中全部 api_key
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import threading
import time
import traceback
import zipfile
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE = "https://mineru.net/api/v4"
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mineru_accounts.csv")
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mineru_output")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "*/*"}

# ★ 模型名映射（SDK 实测：html → MinerU-HTML，直接传 html 服务端报错）
_MODEL_MAP = {"pipeline": "pipeline", "vlm": "vlm", "html": "MinerU-HTML"}

WINDOW_SEC = 60          # 限流滑动窗口（服务端 ~50/min，客户端按 rate 保守执行）
MAX_ATTEMPTS = 6         # 单任务提交最大尝试次数（429 换 token 重试）
COOLDOWN_429 = 30        # token 触发 429 后的冷却秒数
SUSPEND_60018 = 12 * 3600   # 日配额耗尽暂停秒数（12h，跨日自动恢复；1h 恢复仍会耗尽）
POLL_MIN_DELAY = 8       # 提交后至少等 N 秒再首查（避免无谓轮询）
POLL_FAIL_SKIP = 30      # 轮询连续失败 3 次后暂停 N 秒
POLL_INTERVAL = 8        # 无候选任务时的轮询休眠间隔（秒）
QUOTA_CHECK_SEC = 60     # quota 检查间隔
STATE_SAVE_SEC = 20      # state.json 定期保存间隔

# ★ 官方每日配额（mineru.net/apiManage/limit）
DAILY_FILE_LIMIT = 5000   # 每天最多上传 5000 个文件（超限官方拒绝）
DAILY_PAGE_LIMIT = 1000   # 每天 1000 页最高优先级解析（超出降级排队）
QUOTA_FILE_BUFFER = 20    # 文件配额缓冲（留余量，防计数偏差提前停用）
QUOTA_PAGE_WARN = 0.9     # 页数配额告警阈值（用掉 90% 时标记）

LOGF = None
EVENT_LOGF = None   # 结构化事件日志（JSON lines）
LOCK = threading.Lock()


def set_event_log(path):
    """启用结构化事件日志（JSON lines，供分析/监控）"""
    global EVENT_LOGF
    EVENT_LOGF = path


def log_event(event, **fields):
    """写结构化事件：{"ts": "2026-08-13 17:00:00", "event": "circuit_break", ...}"""
    if not EVENT_LOGF:
        return
    try:
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event}
        rec.update(fields)
        with open(EVENT_LOGF, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def log(*args):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {' '.join(str(a) for a in args)}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        try:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
    if LOGF:
        try:
            with open(LOGF, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ─────────────────────────── Token 池（线程安全） ───────────────────────────

class TokenSlot:
    """单个 token 的限流状态 + 健康度（new-api 风格：熔断/成功率/延迟）+ 细粒度统计"""

    def __init__(self, token):
        self.token = token
        self.window = deque()          # 最近 WINDOW_SEC 内提交时间戳
        self.cooldown_until = 0.0      # 429 冷却截止
        self.suspend_until = 0.0       # 日配额耗尽暂停截止
        self.ok_count = 0              # 提交成功
        self.err_count = 0             # 提交失败
        self.last_429_at = 0.0
        # ★ new-api 风格健康度
        self.weight = 1.0              # 权重（加权策略用，默认均等；动态调整 0.5~2.0）
        self.current_weight = 0.0      # 平滑加权轮询（SWRR）累计值
        self.fail_streak = 0           # 连续失败次数（熔断依据）
        self.ban_until = 0.0           # 熔断截止（指数退避）
        self.success_win = deque(maxlen=20)   # 最近 20 次成败窗口（成功率）
        self.latency_ema = None        # 提交延迟 EMA（毫秒，指数平滑）
        self.latencies = deque(maxlen=200)    # ★ 延迟样本（p50/p90/p99 分位数）
        self.err_codes = Counter()     # ★ 错误码分布（429/-60018/401/5xx/timeout/business）
        self.last_err = ""             # 最近一次失败原因
        self.auth_fail = 0             # 鉴权失败次数（401/403 → 永久禁用）
        self.preflight = None          # ★ 启动预热探测：None=未测 / True=通过 / False=失败
        # ★ 细粒度统计
        self.last_used = 0.0           # 最近一次提交时间
        self.total_requests = 0        # 总提交请求数（含失败重试）
        self.rate_limited = 0          # 触发 429 次数
        self.suspended = 0             # 配额耗尽暂停次数
        self.server_error = 0          # 5xx 次数
        self.parse_ok = 0              # 任务解析成功数
        self.parse_fail = 0            # 任务解析失败数
        self.pages_parsed = 0          # 累计解析页数
        self.bytes_uploaded = 0        # 累计上传字节
        # ★ 每日配额追踪（官方：5000 文件/天、1000 页优先/天）
        self.daily_date = time.strftime("%Y-%m-%d")
        self.daily_submits = 0         # 今日提交（文件）数
        self.daily_pages = 0           # 今日解析页数

    def available(self, rate):
        if time.time() < self.cooldown_until:
            return False
        if time.time() < self.suspend_until:
            return False
        if time.time() < self.ban_until:      # ★ 熔断中不可用
            return False
        # ★ 每日文件配额：接近上限（留 BUFFER 余量）即停用该 token
        if self.files_left() <= QUOTA_FILE_BUFFER:
            return False
        return len(self.window) < rate

    def reserve(self):
        self.window.append(time.time())
        while self.window and time.time() - self.window[0] > WINDOW_SEC:
            self.window.popleft()

    def on_429(self):
        self.cooldown_until = time.time() + COOLDOWN_429
        self.last_429_at = time.time()
        self.err_count += 1
        self.rate_limited += 1

    def on_suspend(self):
        self.suspend_until = time.time() + SUSPEND_60018
        self.err_count += 1
        self.suspended += 1

    # ── new-api 风格健康度 ──
    def on_success(self, latency=None):
        """提交成功：清零连败、更新延迟 EMA"""
        self.fail_streak = 0
        if latency is not None:
            self.latency_ema = latency if self.latency_ema is None \
                else self.latency_ema * 0.8 + latency * 0.2

    def on_fail(self, err=""):
        """普通失败：连败 +1（达阈值由池触发熔断）"""
        self.fail_streak += 1
        self.err_count += 1
        self.last_err = err[:80]

    def on_ban(self, backoff):
        """熔断：指数退避禁用"""
        self.ban_until = time.time() + backoff

    def on_auth_fail(self):
        """鉴权失败：无效 key，长期禁用（30 天，人工换 key 后重启生效）"""
        self.auth_fail += 1
        self.ban_until = time.time() + 30 * 24 * 3600
        self.err_count += 1

    @property
    def success_rate(self):
        if not self.success_win:
            return 1.0
        return sum(self.success_win) / len(self.success_win)

    def latency_pct(self, p):
        """延迟分位数（毫秒）：p 为 50/90/99"""
        if not self.latencies:
            return None
        arr = sorted(self.latencies)
        idx = min(len(arr) - 1, int(len(arr) * p / 100.0))
        return int(arr[idx])

    # ── 每日配额 ──
    def _roll_daily(self):
        """跨天自动重置每日计数"""
        today = time.strftime("%Y-%m-%d")
        if today != self.daily_date:
            self.daily_date = today
            self.daily_submits = 0
            self.daily_pages = 0
            return True
        return False

    def mark_submit(self):
        """提交成功（=消耗 1 个文件配额）"""
        self._roll_daily()
        self.daily_submits += 1

    def mark_pages(self, n):
        """解析页数入账（优先页数额度）"""
        self._roll_daily()
        self.daily_pages += n

    def files_left(self):
        self._roll_daily()
        return max(0, DAILY_FILE_LIMIT - self.daily_submits)

    def pages_priority_left(self):
        self._roll_daily()
        return max(0, DAILY_PAGE_LIMIT - self.daily_pages)

    def quota_warn(self):
        """页数优先额度使用超 90%（降级提示）"""
        self._roll_daily()
        return self.daily_pages >= DAILY_PAGE_LIMIT * QUOTA_PAGE_WARN

    def to_dict(self):
        """统计明细（token 脱敏，只露后 6 位）"""
        return {"token": "..." + self.token[-6:],
                "ok": self.ok_count, "err": self.err_count,
                "total_requests": self.total_requests,
                "rate_limited": self.rate_limited,
                "suspended": self.suspended,
                "server_error": self.server_error,
                "parse_ok": self.parse_ok, "parse_fail": self.parse_fail,
                "pages_parsed": self.pages_parsed,
                "bytes_uploaded": self.bytes_uploaded,
                "last_used": self.last_used,
                "cooling": time.time() < self.cooldown_until,
                "suspend_active": time.time() < self.suspend_until,
                "ban_active": time.time() < self.ban_until,
                "ban_until": self.ban_until,   # ★ 持久化恢复用
                "fail_streak": self.fail_streak,
                "success_rate": round(self.success_rate, 3),
                "latency_ms": int(self.latency_ema) if self.latency_ema else None,
                "latency_p50": self.latency_pct(50),
                "latency_p90": self.latency_pct(90),
                "latency_p99": self.latency_pct(99),
                "err_codes": dict(self.err_codes),
                "preflight": self.preflight,
                "last_err": self.last_err,
                "window_len": len(self.window),
                # ★ 每日配额
                "daily_date": self.daily_date,
                "daily_submits": self.daily_submits,
                "daily_pages": self.daily_pages,
                "files_left": self.files_left(),
                "pages_priority_left": self.pages_priority_left(),
                "quota_warn": self.quota_warn()}


class TokenPool:
    """new-api 风格 token 调度池：策略选择（rr/weighted/score）+ 熔断 + 健康检查

    - rr：轮转（默认，兼容旧行为）
    - weighted：平滑加权轮询（SWRR，均匀分配且天然防热点）
    - score：成功率 × 权重 + 低延迟 健康度感知
    """

    def __init__(self, tokens, rate, strategy="rr", ban_threshold=5, health_interval=300):
        self.slots = [TokenSlot(t) for t in tokens]
        self.slots_by_token = {s.token: s for s in self.slots}   # token → slot
        self.rate = rate
        self.rate_scale = 1.0           # 自适应降速系数
        self.lock = threading.RLock()
        self._rr = 0   # ★ round-robin 指针（实例级：每次 acquire 从上次位置继续，避免永远选中 slots[0]）
        self.strategy = strategy        # rr | weighted | score
        self.ban_threshold = ban_threshold   # 连续失败熔断阈值
        self.health_interval = health_interval   # 健康检查间隔（秒，0=关闭）
        self.last_429_total = 0
        self.total_pages_parsed = 0     # 全池累计解析页数

    # ── new-api 风格：统一成败上报（成败窗口 + 分类处置 + 统计）──
    def mark_result(self, slot, ok, latency=None, err_type=None, err_msg=""):
        """统一成败上报。err_type：quota=配额耗尽 / auth=鉴权失败 / network=网络或5xx / business=业务错误
        429 走 mark_429（冷却不熔断）；解析失败走 mark_parse（内容问题不熔断）"""
        with self.lock:
            slot.total_requests += 1
            slot.last_used = time.time()
            slot.success_win.append(1 if ok else 0)
            if ok:
                slot.ok_count += 1
                slot.on_success(latency)
                if latency is not None:
                    slot.latencies.append(latency)   # ★ 延迟样本（分位数）
                slot.mark_submit()   # ★ 每日文件配额入账
            else:
                code = err_msg.split(" ")[0][:12] if err_msg else (err_type or "fail")
                slot.err_codes[code] += 1   # ★ 错误码分布
                slot.on_fail(err_msg)
                if err_type == "quota":
                    slot.on_suspend()
                    log(f"  [配额] token {slot.token[:10]}... {err_msg[:50]} → 暂停 {SUSPEND_60018 // 3600}h")
                    log_event("quota_suspend", token=slot.token[-8:], msg=err_msg[:80])
                elif err_type == "auth":
                    slot.on_auth_fail()
                    log(f"  [鉴权] token {slot.token[:10]}... {err_msg[:50]} → 长期禁用（无效 key）")
                    log_event("auth_ban", token=slot.token[-8:])
                elif err_type == "network":
                    slot.server_error += 1
                    self._maybe_ban(slot)
                elif err_type == "business":
                    self._maybe_ban(slot)
            # ★ 动态权重（weighted 策略）：成功率越高权重越大（0.5 ~ 2.0）
            slot.weight = round(max(0.5, min(2.0, 0.5 + slot.success_rate * 1.5)), 3)

    def _maybe_ban(self, slot):
        """连续失败达阈值 → 指数退避熔断（60s→120s→240s→…→1h 封顶）"""
        if slot.fail_streak >= self.ban_threshold:
            backoff = min(3600, 60 * (2 ** min(slot.fail_streak - self.ban_threshold + 1, 6)))
            slot.on_ban(backoff)
            log(f"  [熔断] token {slot.token[:10]}... 连续失败 {slot.fail_streak} 次，禁用 {backoff}s")
            log_event("circuit_break", token=slot.token[-8:], streak=slot.fail_streak, backoff_s=backoff)

    # ── 启动预热探测：并发对全部 token 测活（GET /quota），无效 key 启动即禁用 ──
    def start_preflight(self):
        threading.Thread(target=self._preflight, daemon=True).start()

    def _preflight(self):
        from concurrent.futures import ThreadPoolExecutor

        def probe(s):
            try:
                api_get("/quota", s.token, timeout=10)
                with self.lock:
                    s.preflight = True
            except RateLimited:
                with self.lock:
                    s.preflight = None
            except requests.HTTPError as e:
                if e.response.status_code in (401, 403):
                    with self.lock:
                        s.on_auth_fail()
                        s.preflight = False
                else:
                    with self.lock:
                        s.preflight = None
            except Exception:
                with self.lock:
                    s.preflight = None

        with ThreadPoolExecutor(max_workers=12) as ex:
            list(ex.map(probe, self.slots))
        ok_n = sum(1 for s in self.slots if s.preflight is True)
        bad_n = sum(1 for s in self.slots if s.preflight is False)
        skip_n = sum(1 for s in self.slots if s.preflight is None)
        log(f"  [预热探测] 完成: 有效 {ok_n} | 无效禁用 {bad_n} | 网络抖动跳过 {skip_n}（共 {len(self.slots)}）")
        log_event("preflight_done", ok=ok_n, bad=bad_n, skip=skip_n, total=len(self.slots))

    # ── 健康检查：定期对熔断/暂停中的 token 测活（GET /quota 轻量，不耗配额）──
    def start_health_check(self, stop):
        if self.health_interval <= 0:
            return
        threading.Thread(target=self._health_loop, args=(stop,), daemon=True).start()

    def _health_loop(self, stop):
        while not stop.wait(self.health_interval):
            now = time.time()
            with self.lock:
                targets = [s for s in self.slots if s.ban_until > now or s.suspend_until > now]
            for s in targets:
                try:
                    api_get("/quota", s.token, timeout=10)
                    with self.lock:
                        # quota 接口可过鉴权 → key 有效，解除熔断（配额类靠 12h 到期自动恢复）
                        if s.ban_until > now and s.auth_fail == 0:
                            s.ban_until = 0.0
                            s.fail_streak = 0
                            log(f"  [健康检查] token {s.token[:10]}... 恢复可用")
                            log_event("health_recovered", token=s.token[-8:])
                except RateLimited:
                    pass
                except requests.HTTPError as e:
                    if e.response.status_code in (401, 403):
                        with self.lock:
                            s.on_auth_fail()
                        log(f"  [健康检查] token {s.token[:10]}... 鉴权失败，长期禁用")
                        log_event("health_auth_ban", token=s.token[-8:])
                except Exception:
                    pass   # 网络抖动保持熔断，下轮再试

    def mark_parse(self, token, ok, pages=0):
        """任务完成时回写 token 统计（解析成功/失败 + 页数；解析失败不触发熔断）"""
        s = self.slots_by_token.get(token)
        if s:
            with self.lock:
                if ok:
                    s.parse_ok += 1
                    s.pages_parsed += pages
                    self.total_pages_parsed += pages
                else:
                    s.parse_fail += 1

    def add_pages(self, token, n):
        """产物落盘后补充页数（v4 接口无 extract_progress，从 layout.json 提取）"""
        s = self.slots_by_token.get(token)
        if s and n:
            with self.lock:
                s.pages_parsed += n
                s.mark_pages(n)   # ★ 每日页数配额入账
                self.total_pages_parsed += n

    def restore_stats(self, stats_list):
        """从持久化恢复统计（重启不丢）。token 统计按顺序恢复（池顺序稳定）"""
        if not stats_list:
            return
        with self.lock:
            for i, d in enumerate(stats_list):
                if i < len(self.slots):
                    s = self.slots[i]
                    s.ok_count = d.get("ok", 0)
                    s.err_count = d.get("err", 0)
                    s.total_requests = d.get("total_requests", 0)
                    s.rate_limited = d.get("rate_limited", 0)
                    s.suspended = d.get("suspended", 0)
                    s.server_error = d.get("server_error", 0)
                    s.parse_ok = d.get("parse_ok", 0)
                    s.parse_fail = d.get("parse_fail", 0)
                    s.pages_parsed = d.get("pages_parsed", 0)
                    s.bytes_uploaded = d.get("bytes_uploaded", 0)
                    s.last_used = d.get("last_used", 0)
                    s.fail_streak = d.get("fail_streak", 0)
                    s.ban_until = d.get("ban_until", 0)   # ★ 熔断状态恢复（重启不丢）
                    s.latency_ema = d.get("latency_ms")   # ★ 延迟 EMA 恢复
                    # ★ 每日配额恢复（跨天自动重置）
                    s.daily_date = d.get("daily_date", time.strftime("%Y-%m-%d"))
                    s.daily_submits = d.get("daily_submits", 0)
                    s.daily_pages = d.get("daily_pages", 0)
                    self.total_pages_parsed += s.pages_parsed

    @property
    def effective_rate(self):
        with self.lock:
            return max(1, int(self.rate * self.rate_scale))

    def _adapt(self):
        """基于全局 429 率自适应降速/恢复"""
        with self.lock:
            total_ok = sum(s.ok_count for s in self.slots)
            recent_429 = sum(1 for s in self.slots if time.time() - s.last_429_at < 300)
            if recent_429 >= 2:
                self.rate_scale = max(0.33, self.rate_scale * 0.75)
                log(f"  [限流] 检测到 {recent_429} 个 token 近期 429，降速系数 → {self.rate_scale:.2f}")
            elif total_ok - self.last_429_total > 200 and self.rate_scale < 1.0:
                self.rate_scale = min(1.0, self.rate_scale * 1.25)
                log(f"  [限流] 稳定运行，提速系数 → {self.rate_scale:.2f}")
            self.last_429_total = total_ok

    def acquire(self, timeout=600):
        """获取一个可用 token（预留窗口槽位）；无可用则等待。返回 TokenSlot"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            rate = self.effective_rate
            with self.lock:
                cands = [s for s in self.slots if s.available(rate)]
                if cands:
                    s = self._pick(cands)
                    s.reserve()
                    return s
            if time.time() - t0 > 30:
                self._adapt()
            time.sleep(0.5)
        raise TimeoutError("所有 token 均不可用（限流/冷却/暂停/熔断/配额耗尽）")

    def _pick(self, cands):
        """按策略从候选集中选 token（锁内调用）"""
        if self.strategy == "weighted":
            # ★ 平滑加权轮询（SWRR）：权重越大分到越多流量，天然防热点
            total = sum(c.weight for c in cands) or 1.0
            best = cands[0]
            for c in cands:
                c.current_weight += c.weight
                if c.current_weight > best.current_weight:
                    best = c
            best.current_weight -= total
            return best
        if self.strategy == "score":
            # ★ 健康度感知：成功率 ×100 + 权重 + 低延迟，连败中的 token 降权
            def sc(c):
                lat = c.latency_ema or 0.0
                return (c.success_rate * 100.0
                        + min(c.weight, 5.0) * 8.0
                        - min(lat / 100.0, 40.0)
                        - (20.0 if c.fail_streak > 0 else 0.0))
            return max(cands, key=sc)
        # rr：从指针处轮转（跳过不可用）
        for _ in range(len(self.slots)):
            s = self.slots[self._rr % len(self.slots)]
            self._rr += 1
            if s in cands:
                return s
        return cands[0]

    def mark_429(self, slot):
        with self.lock:
            slot.on_429()

    def mark_suspend(self, slot):
        with self.lock:
            slot.on_suspend()

    def mark_ok(self, slot):
        with self.lock:
            slot.ok_count += 1
            slot.total_requests += 1
            slot.last_used = time.time()
            slot.mark_submit()   # ★ 每日文件配额入账

    def mark_err(self, slot):
        with self.lock:
            slot.err_count += 1
            slot.total_requests += 1
            slot.last_used = time.time()

    def mark_5xx(self, slot):
        with self.lock:
            slot.server_error += 1
            slot.total_requests += 1
            slot.last_used = time.time()

    def mark_upload(self, slot, nbytes):
        with self.lock:
            slot.bytes_uploaded += nbytes

    def _pool_pct(self, p):
        """池级延迟分位数（合并所有 token 样本）"""
        arr = []
        for s in self.slots:
            arr.extend(s.latencies)
        if not arr:
            return None
        arr.sort()
        return int(arr[min(len(arr) - 1, int(len(arr) * p / 100.0))])

    def stats(self):
        with self.lock:
            detail = [s.to_dict() for s in self.slots]
            today = time.strftime("%Y-%m-%d")
            d_submits = sum(s.daily_submits for s in self.slots if s.daily_date == today)
            d_pages = sum(s.daily_pages for s in self.slots if s.daily_date == today)
            warn_n = sum(1 for s in self.slots if s.quota_warn())
            return {
                "tokens": len(self.slots),
                "rate": self.effective_rate,
                "strategy": self.strategy,
                "ok": sum(s.ok_count for s in self.slots),
                "err": sum(s.err_count for s in self.slots),
                "rate_limited": sum(s.rate_limited for s in self.slots),
                "suspended": sum(s.suspended for s in self.slots),
                "server_error": sum(s.server_error for s in self.slots),
                "parse_ok": sum(s.parse_ok for s in self.slots),
                "parse_fail": sum(s.parse_fail for s in self.slots),
                "pages_parsed": self.total_pages_parsed,
                "bytes_uploaded": sum(s.bytes_uploaded for s in self.slots),
                "cooling": sum(1 for s in self.slots if time.time() < s.cooldown_until),
                "suspended_now": sum(1 for s in self.slots if time.time() < s.suspend_until),
                "banned_now": sum(1 for s in self.slots if time.time() < s.ban_until),
                "auth_failed": sum(1 for s in self.slots if s.auth_fail > 0),
                "avg_success_rate": round(sum(s.success_rate for s in self.slots) / len(self.slots), 3) if self.slots else 1.0,
                "avg_weight": round(sum(s.weight for s in self.slots) / len(self.slots), 3) if self.slots else 1.0,
                # ★ 错误码分布汇总（最近样本）
                "err_dist": dict(sum((Counter(s.err_codes) for s in self.slots), Counter())),
                # ★ 池级延迟分位数（毫秒）
                "latency_ms": {"p50": self._pool_pct(50), "p90": self._pool_pct(90), "p99": self._pool_pct(99)},
                # ★ 预热探测结果
                "preflight": {"ok": sum(1 for s in self.slots if s.preflight is True),
                               "bad": sum(1 for s in self.slots if s.preflight is False),
                               "skip": sum(1 for s in self.slots if s.preflight is None)},
                # ★ 每日配额汇总（官方：5000 文件/天、1000 页优先/天）
                "daily": {"date": today,
                          "submits": d_submits,
                          "files_left": max(0, DAILY_FILE_LIMIT - d_submits),
                          "files_limit": DAILY_FILE_LIMIT,
                          "pages": d_pages,
                          "pages_priority_left": max(0, DAILY_PAGE_LIMIT - d_pages),
                          "pages_priority_limit": DAILY_PAGE_LIMIT,
                          "quota_warn_tokens": warn_n},
                "detail": detail,
            }


# ─────────────────────────── API 调用 ───────────────────────────

class RateLimited(Exception):
    pass


def api_post(path, token, payload, timeout=30):
    resp = requests.post(BASE + path, json=payload, headers={**HEADERS,
                         "Authorization": "Bearer " + token, "Content-Type": "application/json"},
                         timeout=timeout)
    if resp.status_code == 429:
        raise RateLimited(resp.text[:120])
    resp.raise_for_status()
    return resp.json()


def api_get(path, token, timeout=30):
    resp = requests.get(BASE + path, headers={**HEADERS, "Authorization": "Bearer " + token},
                        timeout=timeout)
    if resp.status_code == 429:
        raise RateLimited(resp.text[:120])
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────── 任务管理（含持久化） ───────────────────────────

class Task:
    __slots__ = ("source", "kind", "local_path", "batch_id", "token", "status",
                 "attempts", "created_at", "finished_at", "error", "result_url",
                 "last_poll", "poll_fails", "channel", "progress", "extra", "model",
                 "task_opts", "out_dir")

    def __init__(self, source, kind="url", local_path=None):
        self.source = source          # URL 或文件路径
        self.kind = kind              # url | file
        self.local_path = local_path
        self.batch_id = None
        self.token = None
        self.status = "pending"       # pending|submitted|done|failed
        self.attempts = 0
        self.created_at = time.time()
        self.finished_at = None
        self.error = ""
        self.result_url = ""
        self.last_poll = 0.0
        self.poll_fails = 0
        self.channel = "v4"           # v4（token 池）| flash（免 token agent 通道）
        self.progress = None          # {extracted_pages, total_pages}
        self.extra = None             # 单文件参数（page_ranges/is_ocr/data_id）
        self.model = None             # 任务级模型（None=用服务端默认）
        self.task_opts = None         # 任务级参数（language/pages/extra_formats/formula/table/ocr）
        self.out_dir = None           # 产物落盘目录（相对 out_dir）

    def to_dict(self):
        return {"source": self.source, "kind": self.kind, "local_path": self.local_path,
                "batch_id": self.batch_id, "token": self.token, "status": self.status,
                "attempts": self.attempts, "created_at": self.created_at,
                "finished_at": self.finished_at, "error": self.error,
                "result_url": self.result_url, "channel": self.channel,
                "progress": self.progress, "extra": self.extra, "model": self.model,
                "task_opts": self.task_opts, "out_dir": self.out_dir}

    @staticmethod
    def from_dict(d):
        t = Task(d.get("source", ""), d.get("kind", "url"), d.get("local_path"))
        t.batch_id = d.get("batch_id")
        t.token = d.get("token")
        t.status = d.get("status", "pending")
        t.attempts = d.get("attempts", 0)
        t.created_at = d.get("created_at", time.time())
        t.finished_at = d.get("finished_at")
        t.error = d.get("error", "")
        t.result_url = d.get("result_url", "")
        t.channel = d.get("channel", "v4")
        t.progress = d.get("progress")
        t.extra = d.get("extra")
        t.out_dir = d.get("out_dir")
        t.model = d.get("model")
        t.task_opts = d.get("task_opts")
        return t


def save_state(out_dir, tasks):
    try:
        with open(os.path.join(out_dir, "state.json"), "w", encoding="utf-8") as f:
            json.dump({"updated_at": time.time(),
                       "tasks": [t.to_dict() for t in tasks]}, f, ensure_ascii=False)
    except Exception:
        pass


def load_state(out_dir):
    p = os.path.join(out_dir, "state.json")
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return [Task.from_dict(d) for d in json.load(f).get("tasks", [])]
    except Exception:
        return []


def load_tokens(args):
    tokens = []
    if getattr(args, "token", None):
        tokens = [args.token]
    elif getattr(args, "tokens", None) and os.path.exists(args.tokens):
        with open(args.tokens, encoding="utf-8") as f:
            tokens = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    elif os.environ.get("MINERU_TOKENS"):
        tokens = [t.strip() for t in os.environ["MINERU_TOKENS"].split(",") if t.strip()]
    elif os.path.exists(CSV_PATH):
        with open(CSV_PATH, encoding="utf-8-sig") as f:
            tokens = [str(r.get("api_key", "")).strip()
                      for r in csv.DictReader(f)
                      if r.get("api_key") and str(r.get("api_key")).strip()]
    tokens = list(dict.fromkeys(tokens))  # 去重保序
    if not tokens:
        sys.exit("错误: 未找到任何 token（--token / --tokens / MINERU_TOKENS / mineru_accounts.csv）")
    return tokens


def load_tasks(args):
    """收集任务：--url-file / --urls / --input-dir"""
    tasks, seen = [], set()
    def add_url(u):
        u = u.strip()
        if u and u not in seen:
            seen.add(u)
            tasks.append(Task(u, "url"))
    def add_file(p):
        tasks.append(Task(os.path.basename(p), "file", local_path=p))

    if args.url_file:
        with open(args.url_file, encoding="utf-8") as f:
            for line in f:
                add_url(line.split("#")[0])
    if args.urls:
        for u in args.urls.split(","):
            add_url(u)
    if args.input_dir:
        exts = (".pdf", ".docx", ".doc", ".ppt", ".pptx", ".xls", ".xlsx", ".png", ".jpg", ".jpeg")
        for root, _, files in os.walk(args.input_dir):
            for fn in sorted(files):
                if fn.lower().endswith(exts):
                    add_file(os.path.join(root, fn))
    if args.count:
        tasks = tasks[: args.count]
    if not tasks:
        sys.exit("错误: 任务为空（--url-file / --urls / --input-dir 至少一项）")
    return tasks


# ─────────────────────────── 提交 / 轮询 / 下载 ───────────────────────────

def submit_task(pool, task, args):
    """提交单个任务（自动换 token 重试 429）。返回 True/False"""
    to = task.task_opts or {}   # ★ 任务级参数优先，缺省回退服务端默认
    # ★ 模型名映射（SDK 实测：html → MinerU-HTML，直接传 html 会报错）
    model_name = task.model or args.model
    payload = {"files": [{"url": task.source} if task.kind == "url"
                         else {"name": os.path.basename(task.local_path)}]}
    # ★ 默认 mineru 模型不传 model_version（新注册账号不支持 version 字段 -10002；官方默认即最新模型）
    if model_name != "mineru":
        payload["model_version"] = _MODEL_MAP.get(model_name, model_name)
    # ★ 单文件参数（page_ranges/is_ocr/data_id）
    if task.extra:
        payload["files"][0].update(task.extra)
    # ★ 任务级/全局可选参数
    extra_formats = to.get("extra_formats") or getattr(args, "extra_formats", None)
    if extra_formats:
        payload["extra_formats"] = extra_formats
    pages = to.get("pages") or getattr(args, "pages", None)
    if pages:
        payload["files"][0]["page_ranges"] = pages
    formula = to.get("formula", args.formula)
    if formula:
        payload["enable_formula"] = True
    table = to.get("table", args.table)
    if table:
        payload["enable_table"] = True
    ocr = to.get("ocr", args.ocr)
    if ocr and "is_ocr" not in (task.extra or {}):
        payload["files"][0]["is_ocr"] = True
    language = to.get("language") or getattr(args, "language", None)
    if language:
        payload["language"] = language

    while task.attempts < MAX_ATTEMPTS:
        slot = pool.acquire()
        task.attempts += 1
        task.token = slot.token
        t_submit = time.time()
        try:
            if task.kind == "url":
                body = api_post("/extract/task/batch", slot.token, payload)
            else:
                body = api_post("/file-urls/batch", slot.token, payload)
                upload_url = body["data"]["file_urls"][0]
                with open(task.local_path, "rb") as f:
                    r = requests.put(upload_url, data=f, headers={"Content-Type": "application/octet-stream"},
                                     timeout=(30, 600))
                    if r.status_code >= 400:
                        raise RuntimeError(f"上传失败 HTTP {r.status_code}")
                pool.mark_upload(slot, os.path.getsize(task.local_path))
            # ★ 业务错误（HTTP 200 但 code!=0，body 无 data）→ 保留真实 msg
            if not isinstance(body, dict) or body.get("code") != 0:
                msg = body.get("msg", "") if isinstance(body, dict) else ""
                raise RuntimeError(f"MinerU 业务错误: {msg or str(body)[:80]}")
            pool.mark_result(slot, True, latency=(time.time() - t_submit) * 1000)
            task.batch_id = body["data"]["batch_id"]
            task.status = "submitted"
            task.last_poll = 0.0
            return True
        except RateLimited:
            pool.mark_429(slot)
            log(f"  [429] token {slot.token[:10]}... 冷却中，重试 {task.source[:60]}")
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                pool.mark_429(slot)
                continue
            detail = ""
            try:
                detail = e.response.json().get("msg", "")[:80]
            except Exception:
                pass
            code = e.response.status_code
            if code in (401, 403):
                # ★ 鉴权失败：无效 key → 长期禁用，换下一个 token
                pool.mark_result(slot, False, err_type="auth", err_msg=f"HTTP {code} {detail}")
                log(f"  [鉴权] token {slot.token[:10]}... HTTP {code} → 禁用，换 token 重试")
                continue
            if "-60018" in str(e.response.text) or "-60019" in str(e.response.text):
                pool.mark_result(slot, False, err_type="quota", err_msg=detail or f"HTTP {code}")
                continue
            pool.mark_result(slot, False, err_type="network" if code >= 500 else "business",
                             err_msg=f"HTTP {code} {detail}")
            log(f"  [错误] {task.source[:60]}: HTTP {code} {detail}")
            if code >= 500:
                time.sleep(2)   # 服务端错误短暂重试
                continue
            task.status = "failed"
            task.error = f"HTTP {code} {detail}"
            task.finished_at = time.time()
            return False
        except (KeyError, ValueError, RuntimeError) as e:
            em = str(e)
            # ★ 日配额耗尽类业务错误（HTTP 200 + code -60018 等）→ 暂停该 token 并换下一个重试
            if any(k in em for k in ("daily limit", "limit reached", "quota")):
                pool.mark_result(slot, False, err_type="quota", err_msg=em[:80])
                log(f"  [配额] token {slot.token[:10]}... {em[:60]} → 暂停 {SUSPEND_60018 // 3600}h，换 token 重试")
                continue
            pool.mark_result(slot, False, err_type="business", err_msg=em[:80])
            log(f"  [错误] {task.source[:60]}: {str(e)[:80]}")
            task.status = "failed"
            task.error = str(e)[:120]
            task.finished_at = time.time()
            return False
        except Exception as e:
            pool.mark_result(slot, False, err_type="network", err_msg=str(e)[:80])
            log(f"  [异常] {task.source[:60]}: {str(e)[:80]}")
            time.sleep(1)
    task.status = "failed"
    task.error = f"提交重试 {MAX_ATTEMPTS} 次仍失败"
    task.finished_at = time.time()
    return False


def poll_batch(task):
    """轮询单个任务结果。返回 True=查到结果(含失败)，False=异常/未就绪"""
    try:
        d = api_get(f"/extract-results/batch/{task.batch_id}", task.token)
    except Exception:
        task.poll_fails += 1
        return False
    task.poll_fails = 0
    for item in d.get("data", {}).get("extract_result", []):
        state = item.get("state")
        # ★ 进度字段（extract_progress）
        ep = item.get("extract_progress") or {}
        if ep:
            task.progress = {"extracted_pages": ep.get("extracted_pages", 0),
                             "total_pages": ep.get("total_pages", 0)}
        if state == "done":
            task.status = "done"
            task.result_url = item.get("full_zip_url", "")
            task.finished_at = time.time()
            return True
        if state == "failed":
            task.status = "failed"
            task.error = item.get("err_msg", "")[:120]
            task.finished_at = time.time()
            log(f"  [解析失败] {task.source[:60]}: {task.error}")
            return True
    return False


def _slug(s):
    s = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", s).strip("-")
    return s[:60].rstrip("-") or "untitled"


def _title_slug(md_path):
    """从 full.md 提取标题并 slug 化：优先 Markdown # 标题行，fallback 首行长文本"""
    try:
        with open(md_path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
        # 优先：markdown 一级标题（# 开头）
        for line in lines:
            if line.startswith("#"):
                s = line.lstrip("#").strip()
                if 6 <= len(s) <= 200 and "://" not in s:
                    return _slug(s)
        # fallback：第一行非空长文本（≥15 字符，排除 arXiv 版权声明等短行）
        for line in lines:
            if line and len(line) >= 15 and len(line) <= 200 and "://" not in line:
                return _slug(line)
    except Exception:
        pass
    return "untitled"


def _safe_extract(zf, target_dir):
    """安全解压：防 zip slip（路径穿越）+ zip 炸弹（大小限制）"""
    MAX_ZIP_TOTAL = 2 * 1024 * 1024 * 1024   # 解压总大小上限 2GB
    MAX_ZIP_FILE = 512 * 1024 * 1024         # 单文件上限 512MB
    target_r = os.path.realpath(target_dir)
    total = 0
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        # 绝对路径 / 上级目录（含编码变体 %2e%2e）→ 拒绝
        if name.startswith("/") or any(part == ".." for part in name.split("/")):
            raise RuntimeError(f"非法 zip 条目: {name[:60]}")
        fp = os.path.realpath(os.path.join(target_r, name))
        if fp != target_r and not fp.startswith(target_r + os.sep):
            raise RuntimeError(f"zip 条目越界: {name[:60]}")
        total += info.file_size
        if info.file_size > MAX_ZIP_FILE or total > MAX_ZIP_TOTAL:
            raise RuntimeError("zip 内容超限（防 zip 炸弹）")
    zf.extractall(target_r)


def download_result(task, out_dir):
    """下载产物 zip 并落盘 out_dir/{标题}_{batch8}/：仅保留 paper.pdf + full.md + images/ + meta.json"""
    if not task.result_url:
        # ★ 空 result_url（历史脏数据/异常）不重试，直接标记失败
        task.error = "result_url 为空（任务数据异常）"
        return None
    if task.out_dir and os.path.exists(os.path.join(task.out_dir, "full.md")):
        return task.out_dir
    for attempt in range(2):
        try:
            r = requests.get(task.result_url, headers=HEADERS, timeout=(10, 120), stream=True)
            r.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            tmp = os.path.join(out_dir, f"_tmp_{task.batch_id[:8]}")
            os.makedirs(tmp, exist_ok=True)
            _safe_extract(zf, tmp)   # ★ zip slip / zip 炸弹防护
            # 原 PDF：zip 内 {uuid}_origin.pdf → paper.pdf
            pdf_src = next((n for n in zf.namelist() if n.endswith("_origin.pdf")), None)
            if pdf_src:
                os.replace(os.path.join(tmp, pdf_src), os.path.join(tmp, "paper.pdf"))
            # 清理多余产物：layout/content_list/model json
            for fn in os.listdir(tmp):
                if fn.endswith(("_content_list.json", "_content_list_v2.json",
                                "_model.json", "layout.json")):
                    try:
                        os.remove(os.path.join(tmp, fn))
                    except OSError:
                        pass
            title = _title_slug(os.path.join(tmp, "full.md"))
            target = os.path.join(out_dir, f"{title}_{task.batch_id[:8]}")
            if os.path.exists(target):   # 同名标题 → 加后缀
                target = os.path.join(out_dir, f"{title}_{task.batch_id[:8]}_{int(time.time()) % 10000}")
            os.replace(tmp, target)
            with open(os.path.join(target, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"source": task.source, "batch_id": task.batch_id,
                           "title": title,
                           "token": (task.token or "")[:12] + "...",
                           "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "result_url": task.result_url}, f, ensure_ascii=False, indent=1)
            task.out_dir = target
            return target
        except Exception as e:
            if attempt == 0:
                log(f"  [下载重试] {task.source[:50]}: {str(e)[:60]}")
                time.sleep(2)
            else:
                task.error = f"下载失败: {str(e)[:80]}"
                log(f"  [下载失败] {task.source[:60]}: {str(e)[:80]}")
    return None


def quota_loop(pool, stop):
    """定时打印配额（仅观察，quota=0 不阻止任务）"""
    while not stop.is_set():
        try:
            d = api_get("/quota", pool.slots[0].token)
            q = d.get("data", {})
            log(f"  [配额] user_left={q.get('user_left_quota')} total_left={q.get('total_left_quota')}")
        except Exception:
            pass
        stop.wait(QUOTA_CHECK_SEC)


# ─────────────────────────── 主流程 ───────────────────────────

def run(args):
    global LOGF
    os.makedirs(args.out_dir, exist_ok=True)
    logf = os.path.join(args.out_dir, f"pool_{time.strftime('%Y%m%d_%H%M%S')}.log")
    LOGF = logf

    tokens = load_tokens(args)
    pool = TokenPool(tokens, args.rate)
    stop = threading.Event()

    # ── 任务加载 + 断点续跑 ──
    tasks = load_tasks(args)
    resumed = 0
    if args.resume:
        old = {t.source: t for t in load_state(args.out_dir)}
        for t in tasks:
            o = old.get(t.source)
            if o is None:
                continue
            if o.status == "done":
                # 产物已落盘则直接完成；产物缺失则重下
                t.status = "done"
                t.batch_id = o.batch_id
                t.result_url = o.result_url
                t.token = o.token
                t.finished_at = o.finished_at
                t.out_dir = o.out_dir
                resumed += 1
            elif o.batch_id and o.status == "submitted":
                t.status = "submitted"
                t.batch_id = o.batch_id
                t.token = o.token
                t.attempts = o.attempts
                resumed += 1
            # failed/pending 恢复后重新提交

    log("=" * 60)
    log(f"MinerU Token 池调度器启动 | tokens={len(tokens)} rate={args.rate}/min "
        f"tasks={len(tasks)}（续跑 {resumed}）| 提交线程={args.submit_workers} "
        f"下载线程={args.download_workers}")
    log(f"模型: {args.model} | formula={args.formula} table={args.table} "
        f"ocr={args.ocr} lang={args.language}")
    log(f"调度策略: {pool.strategy} | 熔断阈值: {pool.ban_threshold} | 健康检查: {pool.health_interval}s")
    log(f"输出: {args.out_dir} | 日志: {logf}")

    if not args.no_quota:
        threading.Thread(target=quota_loop, args=(pool, stop), daemon=True).start()
    pool.start_health_check(stop)   # ★ new-api 风格：熔断 token 定期测活自动恢复
    pool.start_preflight()          # ★ 启动预热探测：无效 key 立即禁用
    set_event_log(os.path.join(args.out_dir, "events.jsonl"))   # ★ 结构化事件日志

    to_submit = [t for t in tasks if t.status == "pending"]
    # 已提交的（含续跑的）直接进轮询
    submitted = [t for t in tasks if t.status == "submitted"]
    # 续跑 done 但产物缺失的 → 重新下载
    re_dl = [t for t in tasks if t.status == "done"
             and not os.path.exists(os.path.join(args.out_dir, t.out_dir or f"{t.source.split('/')[-1][:60]}_{(t.batch_id or 'x')[:8]}", "full.md"))]

    # ── 提交阶段（多线程并行，token 池控速；全 429 限流时集体等待重试）──
    if to_submit:
        t0 = time.time()
        SUBMIT_DEADLINE = 900   # 提交窗口最长 15 分钟（并行 job 共享 token 时防 429 雪崩）
        def _submit_wrapper(pool, task, args):
            ok = submit_task(pool, task, args)
            return task, ok
        while to_submit and time.time() - t0 < SUBMIT_DEADLINE:
            round_ok = []
            with ThreadPoolExecutor(max_workers=args.submit_workers) as ex:
                futs = [ex.submit(_submit_wrapper, pool, t, args) for t in to_submit]
                for i, f in enumerate(as_completed(futs), 1):
                    task, ok = f.result()
                    if ok:
                        submitted.append(task)
                        round_ok.append(task)
                    if i % 10 == 0 or i == len(futs):
                        s = pool.stats()
                        log(f"  提交进度 {len(submitted)}/{len(to_submit) + len(submitted)} | 窗口速率 {s['rate']}/min | "
                            f"冷却 {s['cooling']} 暂停 {s['suspended']} | ok={s['ok']} err={s['err']}")
                    if i % 50 == 0:
                        save_state(args.out_dir, tasks)
            to_submit = [t for t in to_submit if t not in round_ok]
            if to_submit:
                s = pool.stats()
                log(f"  [限流等待] 剩余 {len(to_submit)} 任务未提交（冷却 {s['cooling']}）等 30s 重试")
                save_state(args.out_dir, tasks)
                time.sleep(30)
        log(f"提交阶段完成: 成功 {len(submitted)}/{len(tasks)} 耗时 {time.time()-t0:.0f}s")

    # ── 轮询 + 下载阶段 ──
    pending = [t for t in submitted if t.status == "submitted"]
    done_count = sum(1 for t in tasks if t.status == "done")
    failed_count = sum(1 for t in tasks if t.status == "failed")
    dl_pool = ThreadPoolExecutor(max_workers=args.download_workers)
    futures = []
    last_state_save = time.time()
    t_poll0 = time.time()

    def _finish(task):
        nonlocal done_count, failed_count
        if task.status == "done":
            target = download_result(task, args.out_dir)
            if target:
                done_count += 1
                log(f"  ✅ {task.source[:50]} → {os.path.relpath(target, args.out_dir)}")
            else:
                failed_count += 1
        elif task.status == "failed":
            failed_count += 1

    # 续跑补下载
    for t in re_dl:
        futures.append(dl_pool.submit(_finish, t))

    while pending:
        now = time.time()
        # 提交后延迟首查 + 连续失败暂停保护
        candidates = [t for t in pending
                      if now - t.last_poll >= POLL_MIN_DELAY
                      and (t.poll_fails < 3 or now - t.last_poll >= POLL_FAIL_SKIP)]
        for t in candidates[: args.poll_batch]:
            t.last_poll = now
            if poll_batch(t) and t.status in ("done", "failed"):
                pool.mark_parse(t.token, t.status == "done")   # ★ 成败回写 token（解析失败不熔断）
                futures.append(dl_pool.submit(_finish, t))
        pending = [t for t in pending if t.status == "submitted"]
        if time.time() - last_state_save > STATE_SAVE_SEC:
            save_state(args.out_dir, tasks)
            last_state_save = time.time()
        elapsed = time.time() - t_poll0
        s = pool.stats()
        log(f"  轮询中 {elapsed:.0f}s | 待完成 {len(pending)} | 本轮查 {min(len(candidates), args.poll_batch)} | "
            f"done={done_count} failed={failed_count} | token ok={s['ok']} err={s['err']}")
        if elapsed > args.timeout:
            log(f"⚠️ 轮询超时 {args.timeout}s，剩余 {len(pending)} 个未完成（--resume 可续）")
            break
        time.sleep(POLL_INTERVAL if not candidates else 1)

    # ── 下载阶段：主动等待全部产物落盘（带进度）──
    if futures:
        last_log = 0.0
        while True:
            dl_done = sum(1 for f in futures if f.done())
            if dl_done >= len(futures):
                break
            if time.time() - last_log > 15:
                log(f"  下载进度 {dl_done}/{len(futures)}（{args.download_workers} 线程）")
                last_log = time.time()
            time.sleep(2)
    dl_pool.shutdown(wait=True)
    stop.set()

    # ── 汇总报告 ──
    save_state(args.out_dir, tasks)
    ps = pool.stats()
    # ★ 健康排名（TOP5 / BOTTOM5）
    detail = sorted(ps["detail"], key=lambda d: (-d["success_rate"], -d["ok"]))
    top5 = [{k: d[k] for k in ("token", "ok", "err", "success_rate", "latency_ms", "parse_ok", "parse_fail")} for d in detail[:5]]
    bot5 = [{k: d[k] for k in ("token", "ok", "err", "success_rate", "latency_ms", "parse_ok", "parse_fail")} for d in detail[-5:]]
    log("─ 健康 TOP5 ─")
    for d in top5:
        log(f"  {d['token']} sr={d['success_rate']} ok={d['ok']} err={d['err']} lat={d['latency_ms']}ms parse={d['parse_ok']}/{d['parse_fail']}")
    log("─ 健康 BOTTOM5 ─")
    for d in bot5:
        log(f"  {d['token']} sr={d['success_rate']} ok={d['ok']} err={d['err']} lat={d['latency_ms']}ms parse={d['parse_ok']}/{d['parse_fail']}")
    summary = {
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(tasks),
        "done": done_count,
        "failed": failed_count,
        "pending": sum(1 for t in tasks if t.status == "submitted"),
        "submit_ok": len(submitted),
        "pool": {k: v for k, v in ps.items() if k != "detail"},   # ★ 池级指标（含 err_dist/延迟分位/预检）
        "health_top5": top5,
        "health_bottom5": bot5,
        "tokens": pool.stats()["detail"],
        "tasks": [t.to_dict() for t in tasks],
    }
    with open(os.path.join(args.out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    log("=" * 60)
    log(f"完成: done={done_count} failed={failed_count} 提交成功={len(submitted)} 总任务={len(tasks)}")
    log(f"输出目录: {args.out_dir} | 日志: {logf} | 状态: state.json | 汇总: summary.json")


def main():
    ap = argparse.ArgumentParser(description="MinerU API Token 池调度器",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--url-file", help="URL 列表文件（每行一个，# 注释）")
    ap.add_argument("--urls", help="逗号分隔的 URL 列表")
    ap.add_argument("--input-dir", help="本地文件目录（pdf/docx/ppt/xlsx/图片 递归）")
    ap.add_argument("--out-dir", default=DEFAULT_OUT, help="产物输出根目录")
    ap.add_argument("--count", type=int, help="最多处理任务数")
    ap.add_argument("--resume", action="store_true",
                    help="续跑：读取 out-dir/state.json（done 跳过，submitted 续轮询）")
    ap.add_argument("--token", help="单个 token")
    ap.add_argument("--tokens", help="token 列表文件（每行一个）")
    ap.add_argument("--rate", type=int, default=40, help="每 token 每分钟提交数（服务端限 ~50）")
    ap.add_argument("--strategy", choices=["rr", "weighted", "score"], default="rr",
                    help="调度策略：rr=轮转 / weighted=平滑加权轮询 / score=成功率+延迟健康度")
    ap.add_argument("--ban-threshold", type=int, default=5,
                    help="连续失败熔断阈值（达阈值指数退避禁用，健康检查自动恢复）")
    ap.add_argument("--health-interval", type=int, default=300,
                    help="健康检查间隔秒（0=关闭；对熔断/暂停 token 测活自动恢复）")
    ap.add_argument("--submit-workers", type=int, default=8, help="并行提交线程数")
    ap.add_argument("--download-workers", type=int, default=8, help="zip 下载线程数")
    ap.add_argument("--poll-batch", type=int, default=100, help="每轮最多轮询任务数")
    ap.add_argument("--timeout", type=int, default=3600, help="结果轮询总超时（秒）")
    ap.add_argument("--model", choices=["pipeline", "vlm", "html"], default="pipeline")
    ap.add_argument("--formula", action="store_true", help="启用公式识别")
    ap.add_argument("--table", action="store_true", help="启用表格识别")
    ap.add_argument("--ocr", action="store_true", help="强制 OCR（扫描件）")
    ap.add_argument("--language", help="文档语言（en/zh 等）")
    ap.add_argument("--no-quota", action="store_true", help="禁用 quota 监控打印")
    args = ap.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        log("已中断（Ctrl+C）——任务状态已保存于 state.json，可 --resume 续跑")
    except Exception:
        log(traceback.format_exc())


if __name__ == "__main__":
    main()
