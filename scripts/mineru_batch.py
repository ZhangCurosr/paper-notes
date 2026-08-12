#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MinerU 批量注册 + Token 开通（CDP 附加真实 Chrome test profile）

前置条件（一次性）：
  1. 真实 Chrome 以 test profile 启动并带调试端口（脚本自带 ensure_chrome 自愈）：
     chrome.exe --user-data-dir=D:\\tmp\\ud-test-live --profile-directory="Profile 4" --remote-debugging-port=9222
  2. 密码配置（不入库）：环境变量 MINERU_PASS 或本地文件 scripts/.mineru_secret
     （见 mineru_secret.py；mail.tm 密码默认 = 账号密码 + "!"）

每轮流程（验证码自动处理：无感评估→checkbox→滑块拖动→人工兜底，其余全自动）：
  mail.tm 建邮箱（API）→ 注册 → API 收激活邮件 → 激活 → SSO 登录（人工验证码）
  → 手机绑定（1.2.2 密码路径 + Authorization 裸 token）→ 创建 token → 验收

模式：
  register  （默认）串行全流程：注册→激活→登录→绑定→建 token→验收
  pipeline  流水线：先批量注册（邮件等待被覆盖），再逐个激活/登录/建 token
  complete  补全占位账号（CSV 中 api_key 为空的行）
  refresh   刷新已有账号 token（重新登录 + 建新 token + 更新 CSV）

用法：python scripts/mineru_batch.py --count 3 --phone-start 13900139100
输出：mineru_accounts.csv（项目根目录，gitignored）
凭据兜底：logs/mineru_batch_日期.log 与 logs/credentials_日期.csv
  —— 每账号以 [CRED] 行记录完整凭据（含 api_key），即使 CSV 保存失败也可从日志恢复
"""

import argparse
import asyncio
import base64
import csv
import os
import random
import re
import string
import sys
import time
import traceback

import requests
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization
from playwright.async_api import async_playwright

from mineru_secret import get_mailtm_password, get_password

CDP = "http://127.0.0.1:9222"
SSO = "https://sso.openxlab.org.cn/gw/uaa-be/api/v1"
MINERU = "https://mineru.net"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/151.0.0.0 Safari/537.36")

PWD = None  # 惰性加载（见 _pwd()）


def _pwd():
    """惰性读取统一密码：MINERU_PASS → scripts/.mineru_secret → 明确报错"""
    global PWD
    if PWD is None:
        PWD = get_password()
    return PWD


LOGF = None  # 日志文件路径（main 初始化）


def log(*args, **kwargs):
    """带时间戳的日志：控制台实时输出 + 追加写日志文件"""
    ts = time.strftime("%H:%M:%S")
    msg = " ".join(str(a) for a in args)
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # ★ Windows GBK 控制台遇 emoji 会抛 UnicodeEncodeError——降级 ASCII 打印，
        #   不影响文件日志（此前该异常会让 CSV 保存函数被误判失败！）
        try:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
    if LOGF:
        try:
            with open(LOGF, "a", encoding="utf-8") as f:
                f.write(line + chr(10))
        except Exception:
            pass


def set_logfile(path):
    global LOGF
    LOGF = path

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mineru_accounts.csv")  # ★ 保存到项目根目录（gitignored）
FIELDS = ["username", "email", "phone", "api_key", "password", "created_at", "expires_at"]
TS_FMT = "%Y-%m-%d %H:%M:%S"
TOKEN_LIFETIME_DAYS = 90  # ★ token 有效期 3 个月

CRED_CSV = None  # 凭据兜底 CSV（logs/credentials_日期.csv，main 初始化）


def set_cred_file(path):
    global CRED_CSV
    CRED_CSV = path


def log_cred(stage, row):
    """★ 凭据兜底日志：账号完整信息（含 api_key）以 [CRED] 行写入主日志，
    并追加到独立凭据 CSV（双保险）。即使 CSV 保存失败，也能从日志恢复全部凭据。
    stage: placeholder(占位)/final(完整)/refresh/complete/register_failed/csv_save_failed"""
    def _safe(v):
        return "" if v is None else str(v).replace("\n", " ").replace("\r", " ")
    fields = {k: _safe(row.get(k)) for k in FIELDS}
    line = ("[CRED] stage=" + str(stage) +
            "|username=" + fields["username"] +
            "|email=" + fields["email"] +
            "|phone=" + fields["phone"] +
            "|password=" + fields["password"] +
            "|api_key=" + fields["api_key"] +
            "|created_at=" + fields["created_at"] +
            "|expires_at=" + fields["expires_at"])
    log(line)
    # ★ 独立凭据 CSV（主日志被轮转/截断也能恢复）
    if CRED_CSV:
        try:
            new = not os.path.exists(CRED_CSV)
            with open(CRED_CSV, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=["stage"] + FIELDS)
                if new:
                    w.writeheader()
                w.writerow({"stage": str(stage), **fields})
        except Exception as e:
            try:
                print(f"[CRED] 凭据 CSV 写入失败: {str(e)[:60]}", flush=True)
            except Exception:
                pass


def _csv_rows():
    """读取 CSV 全部行（utf-8-sig 兼容 Excel BOM；容错多余列）。
    文件不存在 → []; 读取失败 → None（调用方不得覆盖原文件）"""
    if not os.path.exists(OUT):
        return []
    try:
        with open(OUT, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            for k in [k for k in r if k is None]:
                del r[k]
        return rows
    except Exception as e:
        log(f"  ⚠️ CSV 读取失败: {str(e)[:80]}（本次不覆盖原文件，凭据由 CRED 日志兜底）")
        return None


def _csv_lock():
    """获取 CSV 写入锁（Windows msvcrt；防多实例并发写）。其他平台退化为无锁。"""
    try:
        import msvcrt
        lf = open(OUT + ".lock", "a+b")
        if lf.seek(0, os.SEEK_END) == 0:
            lf.write(b"x")
            lf.flush()
        lf.seek(0)
        msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
        return lf
    except (ImportError, OSError):
        try:
            lf.close()
        except Exception:
            pass
        return None


def _csv_unlock(lf):
    """释放 CSV 写入锁"""
    if lf is None:
        return
    try:
        import msvcrt
        lf.seek(0)
        msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
        lf.close()
    except (ImportError, OSError):
        try:
            lf.close()
        except Exception:
            pass


def _backoff(base, attempt, cap=30):
    """轻量指数退避 + jitter（保持总耗时量级，防固定节奏被限流）"""
    return min(base * (2 ** attempt), cap) + random.uniform(0, base * 0.5)


def save_row(row):
    """每完成一个账号立即追加保存（防中途失败丢失）。
    文件不存在或为空时写 header；utf-8-sig（Excel 兼容）；写入后读回验证；
    失败时凭据由 CRED 日志兜底。"""
    lf = _csv_lock()
    try:
        new = not os.path.exists(OUT) or os.path.getsize(OUT) == 0
        with open(OUT, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
            f.flush()
            os.fsync(f.fileno())
        # ★ 读回验证（确认真的落盘）
        try:
            with open(OUT, encoding="utf-8-sig", newline="") as f:
                last = list(csv.DictReader(f))[-1]
            if last.get("username") != row.get("username"):
                raise IOError("末行 username 读回不一致")
        except Exception as e:
            log(f"  ⚠️ CSV 读回校验异常: {str(e)[:80]}")
        log(f"  💾 已保存到 {OUT}")
        return True
    except Exception as e:
        log(f"  ❌ CSV 保存失败: {str(e)[:120]}")
        log_cred("csv_save_failed", row)  # ★ CSV 失败也必须有凭据留档
        return False
    finally:
        _csv_unlock(lf)


def update_csv_row(username, new_fields):
    """更新 CSV 中对应行（保留历史字段）。BOM 容错 + 原子写（tmp+replace 防中断损坏）。
    匹配不到目标行时明确告警（不再静默）——凭据由 CRED 日志兜底。"""
    lf = _csv_lock()
    try:
        rows = _csv_rows()
        if rows is None:
            return False
        if not rows:
            log(f"  ⚠️ CSV 为空，跳过更新（凭据已由 CRED 日志兜底）")
            return False
        hit = False
        for i, r in enumerate(rows):
            if r.get("username") == username:
                r.update(new_fields)
                rows[i] = r
                hit = True
        if not hit:
            # ★ 原来静默失败——现在明确告警（行可能被 Excel/BOM/乱码破坏）
            log(f"  ⚠️ CSV 中未找到 {username} 行——更新失败！凭据已由 CRED 日志兜底")
            return False
        # ★ 原子写：临时文件 + os.replace，防写入中断损坏 CSV
        tmp = OUT + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, OUT)
        log(f"  ♻️  {username} 行已更新 → {OUT}")
        return True
    except Exception as e:
        log(f"  ⚠️ CSV 更新失败: {str(e)[:120]}（凭据已由 CRED 日志兜底）")
        return False
    finally:
        _csv_unlock(lf)


def get_page(ctx):
    pages = [p for p in ctx.pages if not p.is_closed()]
    return pages[0] if pages else None


async def safe_goto(page, url, timeout=45000, wait_until="domcontentloaded"):
    """goto 封装：ERR_ABORTED/导航中断自动重试"""
    for attempt in range(3):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            return True
        except Exception as e:
            err = str(e)[:50]
            if "ERR_ABORTED" in err or "interrupted" in err or "closed" in err:
                log(f"  [goto] {err}（重试 {attempt+1}/3）")
                await page.wait_for_timeout(2500)
                continue
            raise
    return False


def ensure_chrome():
    """★ 自愈：确保 CDP Chrome 活着——挂了则精确终止本 profile 进程后重启。
    只按 CommandLine 包含本次 --user-data-dir 的 chrome 进程终止，避免误杀用户其他窗口。"""
    import subprocess
    CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    USER_DATA = "D:/tmp/ud-test-live"
    ARGS = ["--user-data-dir=" + USER_DATA,
            "--profile-directory=Profile 4",
            "--remote-debugging-port=9222",
            "--no-first-run", "--no-default-browser-check",
            "--no-restore-session-state", "--disable-session-crashed-bubble",
            "about:blank"]
    for attempt in range(3):
        try:
            r = requests.get("http://127.0.0.1:9222/json/version", timeout=3)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        log(f"[Chrome] 不可用（第{attempt+1}次），精确清理本 profile 残留进程后重启...")
        # ★ 只终止 CommandLine 含本次 user-data-dir 的 chrome 进程（不再 taskkill 全部）
        try:
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                  "Where-Object { $_.CommandLine -like '*" + USER_DATA + "*' } | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=30)
        except Exception:
            pass
        time.sleep(3)
        # ★ Start-Process 用参数数组传参（避免含空格路径 'Profile 4' 被当单参数）
        try:
            arg_ps = ",".join("'" + a.replace("'", "''") + "'" for a in ARGS)
            cmd = f"Start-Process -FilePath '{CHROME}' -ArgumentList @({arg_ps})"
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=30)
        except Exception:
            pass
        time.sleep(10)
    return False


async def connect(p):
    if not ensure_chrome():
        raise RuntimeError("Chrome 启动失败")
    for _ in range(30):
        try:
            b = await p.chromium.connect_over_cdp(CDP)
            return b, b.contexts[0]
        except Exception:
            await asyncio.sleep(1)
    raise RuntimeError("CDP 连接失败")


async def wait_url(page, pred, timeout=300):
    """等待跳转（timeout 单位为秒）：同时检查该页面和 context 内其他标签页（SSO 可能新标签跳主站）。
    内部 2s 轮询；返回前打印真实等待时长。"""
    ctx = page.context
    start = time.monotonic()
    waited = 0
    while time.monotonic() - start < timeout:
        await asyncio.sleep(2)
        waited = int(time.monotonic() - start)
        try:
            # 主页面检查
            u = page.url
            if pred(u):
                log(f"  [wait_url] 命中于 {waited}s")
                return True, u
            # ★ 其他标签页检查（SSO 自动跳转可能开新标签）
            for pg in ctx.pages:
                if pg.is_closed() or pg == page:
                    continue
                try:
                    u2 = pg.url
                    if pred(u2):
                        log(f"  [wait_url] 命中于 {waited}s（其他标签页）")
                        return True, u2
                except Exception:
                    pass
        except Exception:
            pass
        if waited % 60 == 0 and waited > 0:
            log(f"  [{waited}s] 当前: {page.url[:80]}")
            sys.stdout.flush()
    log(f"  [wait_url] 超时（实际等待 {waited}s）")
    return False, page.url


# ---------- 1. 临时邮箱（mail.tm：无限创建 + API 收信，无需 DuckDuckGo/Outlook） ----------
def mailtm_create():
    """创建 mail.tm 邮箱，返回 (address, password, api_token)，失败重试 3 次。
    加固：校验 domains/accounts/token 响应；候选域名逐个跑 register/check oracle
    （exist:false 才可用，参照原 prefetch_aliases 逻辑），失败自动轮换下一域名。"""
    for attempt in range(3):
        try:
            S = requests.Session()
            r = S.get("https://api.mail.tm/domains", timeout=15)
            r.raise_for_status()
            members = r.json().get("hydra:member", [])
            if not members:
                raise RuntimeError("mail.tm domains 返回为空")
            user = "ctf" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
            pwd = get_mailtm_password()
            # ★ oracle 校验：候选域名逐个尝试，exist:false 才可用，失败轮换下一域名
            addr = None
            for m in members:
                dom = m.get("domain") if isinstance(m, dict) else m
                cand = f"{user}@{dom}"
                try:
                    rr = S.post(f"{SSO}/register/check", json={"item": cand, "type": "email"}, timeout=20)
                    exist = rr.json().get("data", {}).get("exist")
                    if exist is False:
                        addr = cand
                        break
                    log(f"  [mail.tm] 域名 {dom} 候选邮箱已存在，换下一个...")
                except Exception as e:
                    log(f"  [mail.tm] oracle 检查异常 {dom}: {str(e)[:40]}，换下一个...")
            if not addr:
                raise RuntimeError("候选域名 oracle 全部不可用")
            ra = S.post("https://api.mail.tm/accounts", json={"address": addr, "password": pwd}, timeout=15)
            if ra.status_code not in (200, 201):
                raise RuntimeError(f"accounts 响应 {ra.status_code}: {ra.text[:60]}")
            rt = S.post("https://api.mail.tm/token", json={"address": addr, "password": pwd}, timeout=15)
            rt.raise_for_status()
            tok = rt.json().get("token")
            if not tok:
                raise RuntimeError("token 响应缺 token 字段")
            log(f"  [邮箱] {addr}")
            return addr, pwd, tok
        except Exception as e:
            log(f"  [mail.tm] 创建失败，重试 {attempt+1}/3: {str(e)[:60]}")
            time.sleep(_backoff(2, attempt))
    raise RuntimeError("mail.tm 邮箱创建失败")


def mailtm_wait_activate(tok, timeout_s=240):
    """轮询 mail.tm 收 OpenXLab 激活邮件，返回激活链接（每 15s 输出进度）"""
    H = {"Authorization": f"Bearer {tok}"}
    deadline = time.time() + timeout_s
    last_report = time.time()
    while time.time() < deadline:
        try:
            msgs = requests.get("https://api.mail.tm/messages", headers=H, timeout=15).json()
            for m in msgs.get("hydra:member", []):
                full = requests.get(f"https://api.mail.tm/messages/{m['id']}", headers=H, timeout=15).json()
                html = full.get("html", [""])[0] if full.get("html") else ""
                text = full.get("text", "") or ""
                m2 = re.search(r'https://sso\.openxlab\.org\.cn/active\?[^\s"\x27<>]+', html + text)
                if m2:
                    link = m2.group(0).replace("&amp;", "&")
                    log(f"  [激活] 链接已获取")
                    return link
        except Exception:
            pass
        # ★ 每 15s 报告等待进度
        now = time.time()
        if now - last_report >= 15:
            log(f"  [激活] 等待邮件中... {int(now - (deadline - timeout_s))}s / {timeout_s}s")
            last_report = now
        time.sleep(3)
    raise RuntimeError("激活邮件超时未收到")


# ---------- 2. 注册 ----------
async def register(page, addr):
    username = "ctf" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    await safe_goto(page, "https://sso.openxlab.org.cn/mineru-register?redirect=https://mineru.net/?clientId=lkzdx57nvy22jkpq9x2w&source=minerU",
                    wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(5000)
    try:
        await page.click("text=邮箱注册", timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(2000)
    await page.fill("#normal_login_username:visible", username, timeout=8000)
    await page.fill("#normal_login_email:visible", addr, timeout=8000)
    await page.fill("#normal_login_password:visible", _pwd(), timeout=8000)
    await page.fill("#normal_login_confirm:visible", _pwd(), timeout=8000)
    try:
        await page.check("input[type=checkbox]:visible", timeout=3000)
    except Exception:
        pass
    await page.click("button:has-text('注册'):visible", timeout=8000)
    await page.wait_for_timeout(6000)
    txt = await page.evaluate("document.body.innerText")
    if "邮件发送成功" not in txt:
        raise RuntimeError(f"注册失败: {txt[:120]}")
    log(f"  [注册] {username} + {addr} → 邮件已发送")
    return username


# ---------- 4. SSO 登录（验证码自动处理） ----------
_SLIDER_SELECTORS = [
    "#aliyunCaptcha-sliding-slider",   # 阿里云 2.0 滑动验证滑块按钮
    "#aliyunCaptcha-slide-btn",        # 变体
    ".aliyunCaptcha-move-bg .btn_slide",
    "#nc_1_n1z",                       # 旧版 nc 滑块按钮
]
_TRACK_SELECTORS = [
    "#aliyunCaptcha-sliding-track",
    "#aliyunCaptcha-slide-bg",
    "#aliyunCaptcha-window-slide",
    ".aliyunCaptcha-move-bg",
]


async def _find_slider(page):
    """查找当前可见的滑块按钮（点击 checkbox 后阿里云可能弹出滑块窗）"""
    for sel in _SLIDER_SELECTORS:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                return loc.first
        except Exception:
            pass
    return None


def _human_track(distance, y, steps=45):
    """生成人类拖动轨迹：easeOutCubic 加速→减速 + y 轴抖动 + 末端回拉"""
    pts = []
    x = 0.0
    for i in range(steps):
        t = (i + 1) / steps
        target = distance * (1 - (1 - t) ** 3)
        x = target
        pts.append((x + random.uniform(-0.8, 0.8), y + random.uniform(-2.5, 2.5)))
    # 人类习惯：快到位时回拉一点再落到终点
    pts.append((x - random.uniform(2, 6), y + random.uniform(-1.5, 1.5)))
    pts.append((x, y))
    return pts


async def _drag_slider(page):
    """拖动滑块到最右端（阿里云滑动验证多为'拖到最右'）。返回 True=已执行拖动"""
    btn = await _find_slider(page)
    if btn is None:
        return False
    try:
        bb = await btn.bounding_box()
        if not bb:
            return False
        # 轨道宽度：优先取轨道元素，缺省用官方默认滑块窗宽 360px
        tw = None
        for sel in _TRACK_SELECTORS:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    tb = await loc.first.bounding_box()
                    if tb and tb["width"] > 0:
                        tw = tb["width"]
                        break
            except Exception:
                pass
        if not tw:
            tw = 360.0
        distance = max(tw - bb["width"] - 4, 10)
        cx = bb["x"] + bb["width"] / 2
        cy = bb["y"] + bb["height"] / 2
        # 人类轨迹：远处悬停 → 接近 → 按下 → 变速拖动（每步 8-22ms 随机）→ 释放
        await page.mouse.move(cx - random.uniform(30, 80), cy + random.uniform(-10, 10), steps=12)
        await page.wait_for_timeout(random.randint(250, 600))
        await page.mouse.move(cx, cy, steps=6)
        await page.wait_for_timeout(random.randint(120, 300))
        await page.mouse.down()
        await page.wait_for_timeout(random.randint(80, 200))
        for px, py in _human_track(distance, cy):
            await page.mouse.move(px, py)
            await page.wait_for_timeout(random.randint(8, 22))
        await page.wait_for_timeout(random.randint(150, 350))
        await page.mouse.up()
        log(f"  [验证码] 已拖动滑块（距离 {int(distance)}px），等评估...")
        return True
    except Exception as e:
        log(f"  [验证码] 滑块拖动异常: {str(e)[:60]}")
        return False


async def auto_captcha(page):
    """阿里云验证码自动处理：无感评估 → checkbox 点击 → 滑块拖动 → 人工兜底。
    评估耗时不可预测（8s~90s+），全程轮询 URL 跳转 + cookie 写入双判定，每 30s 报告进度。
    自动全部失败后提示人工在浏览器里手动完成，脚本继续轮询等待（5 分钟）。"""
    def is_jumped():
        try:
            return "login" not in page.url.lower()
        except Exception:
            return False

    async def _passed(tag):
        log(f"  [验证码] ✅ {tag}")
        return True

    # ── 阶段1：无感评估（16s，期间轻微移动鼠标降低机器人行为分）──
    for i in range(8):
        await asyncio.sleep(2)
        try:
            await page.mouse.move(random.randint(200, 900), random.randint(200, 600), steps=4)
        except Exception:
            pass
        if is_jumped():
            return await _passed(f"无感验证自动通过 ({i*2}s)")
    log("  [验证码] 无感未过，进入 checkbox 点击...")

    # ── 阶段2：checkbox 点击（最多 2 次）→ 每次点击后最多拖 3 次滑块 ──
    for attempt in range(2):
        try:
            box = page.locator("#aliyunCaptcha-checkbox-body")
            if await box.is_visible():
                bb = await box.bounding_box()
                if bb:
                    cx, cy = bb["x"] + 30, bb["y"] + bb["height"] / 2
                    # 人类轨迹：先远处移动 → 接近 → 点击
                    await page.mouse.move(cx - 50, cy + 10, steps=18)
                    await page.wait_for_timeout(350)
                    await page.mouse.move(cx - 12, cy - 4, steps=12)
                    await page.wait_for_timeout(220)
                    await page.mouse.move(cx, cy, steps=6)
                    await page.wait_for_timeout(150)
                    await page.mouse.down()
                    await page.wait_for_timeout(110)
                    await page.mouse.up()
                    log(f"  [验证码] 已点击 checkbox（第{attempt+1}次）")
        except Exception:
            pass
        for drag in range(3):
            await _drag_slider(page)
            # ★ 点击/拖动后持续轮询：URL 跳转 / cookie 写入都算通过（评估 8-90s 不等）
            for i in range(45):
                await asyncio.sleep(2)
                if is_jumped():
                    return await _passed(f"跳转成功（点击后 {i*2}s）")
                # cookie 已写入也算通过（SSO 可能先写 cookie 后跳转）
                try:
                    ck = await page.context.cookies(MINERU)
                    for c in ck:
                        if c["name"] in ("opendatalab_session", "uaa-token") and len(c["value"]) > 50:
                            return await _passed(f"cookie 已写入（点击后 {i*2}s）")
                except Exception:
                    pass
                if i % 15 == 14:
                    log(f"  [验证码] 评估中... {i*2}s（无感评估耗时不可预测，请耐心）")
            # 90s 未通过：滑块还在就再拖一次，否则回外层重新点 checkbox
            if await _find_slider(page) is None:
                break
        await asyncio.sleep(random.uniform(2, 4))

    # ── 阶段3：人工兜底（自动全部失败——提示人工完成，脚本持续轮询 5 分钟）──
    log("  ⚠️⚠️ 自动验证未通过——请在浏览器窗口手动完成验证码（脚本会等待 5 分钟）")
    for i in range(150):
        await asyncio.sleep(2)
        if is_jumped():
            return await _passed(f"人工完成后跳转成功（{i*2}s）")
        try:
            ck = await page.context.cookies(MINERU)
            for c in ck:
                if c["name"] in ("opendatalab_session", "uaa-token") and len(c["value"]) > 50:
                    return await _passed(f"人工完成后 cookie 已写入（{i*2}s）")
        except Exception:
            pass
        if i % 30 == 29:
            log(f"  [验证码] 等待人工验证中... {i*2}s")
    raise RuntimeError("验证码未通过（自动 + 人工 5 分钟均超时）")


async def sso_login(page, username, password=None):
    """mineru-login 入口登录：登录后自动跳 mineru 主站，主站会话一并建立
    返回 (sso_token, 主站 localStorage token)。password 为空时用统一密码 _pwd()。"""
    pwd = password or _pwd()
    # ★★ 防串号：登录前全清 cookie + 主站 localStorage
    #    （clear_cookies(domain=) 对 CDP 附加 context 的 .mineru.net 带点域匹配不可靠，改全清）
    try:
        await page.context.clear_cookies()
    except Exception:
        pass
    try:
        pg_tmp = await page.context.new_page()
        await safe_goto(pg_tmp, MINERU, wait_until="domcontentloaded", timeout=60000)
        await pg_tmp.evaluate("localStorage.clear(); sessionStorage.clear();")
        await pg_tmp.close()  # ★ 立即关闭，不留干扰页（否则 wait_url 会误匹配）
    except Exception:
        pass
    await safe_goto(page, "https://sso.openxlab.org.cn/mineru-login?redirect=https://mineru.net/apiManage/token?clientId=lkzdx57nvy22jkpq9x2w&source=minerU",
                    wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(5000)
    # ★ 页面域检查（防残留页面/导航失败后继续操作——example.com 污染教训）
    if "sso.openxlab.org.cn" not in page.url:
        raise RuntimeError(f"登录页异常（页面不在 SSO 域）: {page.url[:60]}")
    # 可能已在登录页或已登录自动跳转
    try:
        acc = await page.input_value("#normal_login_account:visible", timeout=2000)
    except Exception:
        acc = ""
    if not acc:
        try:
            await page.fill("#normal_login_account:visible", username, timeout=8000)
            await page.fill("#normal_login_password:visible", pwd, timeout=8000)
            await page.click("button:has-text('登录'):visible", timeout=8000)
            log("  [登录] 已提交，等待验证（无感优先）...")
            sys.stdout.flush()
            await auto_captcha(page)
        except Exception as e:
            log(f"  [登录] 验证码异常: {str(e)[:60]}，轮询 cookie 30s...")
            # ★★ 验证码报错但登录可能已成功（SSO 异步写 cookie）——轮询 30s 直读
            for _ in range(15):
                await asyncio.sleep(2)
                try:
                    ck = await page.context.cookies(MINERU)
                    for c in ck:
                        if c["name"] in ("opendatalab_session", "uaa-token") and len(c["value"]) > 50:
                            log(f"  [登录] ✅ cookie 直读（验证码报错后轮询命中）")
                            return c["value"]
                except Exception:
                    pass
                try:
                    if "mineru.net" in page.url and "sso" not in page.url:
                        break
                except Exception:
                    pass
    # 等跳转 mineru 主站（★ 匹配 apiManage 或带 code——干扰页已清除，无需再区分）
    ok, u = await wait_url(page,
                           lambda u: ("mineru.net" in u and "sso" not in u
                                      and ("apiManage" in u or "code=" in u)),
                           timeout=240)
    if not ok:
        # 兜底 1：cookie 直读主站 token（SSO cookie 模式登录，无 code 时最可靠）
        try:
            ck = await page.context.cookies(MINERU)
            for c in ck:
                if c["name"] in ("opendatalab_session", "uaa-token") and len(c["value"]) > 50:
                    log(f"  [登录] ✅ 兜底：cookie 直读主站 token（{c['name']}）")
                    return c["value"]
        except Exception:
            pass
        # 兜底 2：主站页面 localStorage 已有 token（SPA 已自动兑换且 code 被消费）
        for pg in page.context.pages:
            if pg.is_closed():
                continue
            try:
                if "mineru.net" in pg.url:
                    t = await pg.evaluate("localStorage.getItem('uaa-token') || ''")
                    if t:
                        log(f"  [登录] ✅ 兜底：localStorage 已含 token（SPA 自动兑换）")
                        return t
            except Exception:
                pass
        raise RuntimeError("未跳转 mineru 主站（登录失败）")
    # ★ 主站页面可能开在新标签（SSO 自动跳转），切换到主站域页面再执行兑换
    for pg in page.context.pages:
        if pg.is_closed():
            continue
        try:
            if "mineru.net" in pg.url:
                page = pg
                break
        except Exception:
            pass
    # ★ 必须落在 apiManage/token 控制台页（只有它执行 ssoCode 兑换）
    m = re.search(r"code=([^&]+)", u)
    if m:
        if "apiManage" not in u:
            await safe_goto(page, f"https://mineru.net/apiManage/token?code={m.group(1)}",
                            wait_until="domcontentloaded", timeout=60000)
        # ★ 手动执行 ssoCode 兑换（主站 JS 可能不自动跑）
        r = await page.evaluate("""async (c) => {
          const resp = await fetch('/datasets/api/v2/users/auth', {method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({code: c, redirect: 'https://mineru.net'})});
          const j = await resp.json();
          if (j.code === 0 && j.data && j.data.token) {
            localStorage.setItem('uaa-token', j.data.token);
            return j.data.token;
          }
          return null;
        }""", m.group(1))
        if r:
            log(f"  [登录] ✅ 手动兑换成功（{len(r)} 字符 token）")
            return r
        log("  [登录] 手动兑换失败，尝试等 JS 自动...")
    else:
        await safe_goto(page, "https://mineru.net/apiManage/token", wait_until="domcontentloaded", timeout=60000)
        log("  [登录] 已强制到控制台页（无 code）")
    # 等主站完成 ssoCode 兑换（localStorage 或 cookie 有 token）——60s 上限
    token = ""
    for i in range(30):
        await asyncio.sleep(2)
        try:
            t = await page.evaluate("localStorage.getItem('uaa-token') || sessionStorage.getItem('uaa-token') || ''")
            if t:
                token = t
                log(f"  [登录] ✅ 主站 token 已获取 ({i*2}s)")
                return token
        except Exception:
            pass
        # ★ cookie 直读兜底（SPA 把 token 写进 cookie 而不是 localStorage）
        try:
            ck = await page.context.cookies(MINERU)
            for c in ck:
                if c["name"] in ("opendatalab_session", "uaa-token") and len(c["value"]) > 50:
                    log(f"  [登录] ✅ cookie 直读 token（{c['name']}，{i*2}s）")
                    return c["value"]
        except Exception:
            pass
        if i % 30 == 29:
            log(f"  [登录] 等待主站兑换 {i*2}s...")
    # 兜底：刷新让页面重新处理 code
    log("  [登录] 兜底刷新页面...")
    await safe_goto(page, "https://mineru.net/apiManage/token", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(10000)
    token = await page.evaluate("localStorage.getItem('uaa-token') || sessionStorage.getItem('uaa-token') || ''")
    if not token:
        raise RuntimeError("未拿到主站 token")
    log("  [登录] ✅ 主站 token 已获取（兜底）")
    return token


# ---------- 5. 手机绑定（API 直调） ----------
def rsa_encrypt(pub_b64, plain):
    b64 = pub_b64.replace("\n", "").replace(" ", "")
    b64 = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    pem = f"-----BEGIN PUBLIC KEY-----\n{b64}\n-----END PUBLIC KEY-----"
    key = serialization.load_pem_public_key(pem.encode())
    return base64.b64encode(key.encrypt(plain, padding.PKCS1v15())).decode()


async def bind_phone_via_browser(page, phone):
    """绑定手机：从浏览器抓 SSO cookie，requests 直调 bind（Authorization 裸 token）"""
    cookies = await page.context.cookies("https://sso.openxlab.org.cn")
    ck = {c["name"]: c["value"] for c in cookies}
    token = ck.get("uaa-token", "")
    if not token:
        raise RuntimeError("SSO uaa-token cookie 缺失")
    S = requests.Session()
    S.cookies.update(ck)
    S.headers.update({"Content-Type": "application/json",
                      "Authorization": token,  # ★ 裸 token
                      "User-Agent": UA})
    r = S.post(f"{SSO}/cipher/getPubKey", json={"type": "resetPassword", "from": "browser"}, timeout=20)
    pub = r.json()["data"]["pubKey"]
    ts = int(time.time())
    cipher = rsa_encrypt(pub, f"{phone}||{_pwd()}{ts}".encode())
    r2 = S.post(f"{SSO}/personal/mobile/bind", json={
        "function": "1.2.2", "phone": phone, "password": cipher}, timeout=20)
    j = r2.json()
    if j.get("msgCode") != "10000":
        raise RuntimeError(f"绑定失败: {j.get('msgCode')} {j.get('msg','')}")
    log(f"  [绑定] {phone} ✅")


# ---------- 6. 创建 token ----------
async def create_token(page, token):
    # 主站 WAF cookie
    await safe_goto(page, MINERU, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(5000)
    cookies = await page.context.cookies(MINERU)
    ck = {c["name"]: c["value"] for c in cookies}
    S = requests.Session()
    S.cookies.update(ck)
    S.headers.update({"Content-Type": "application/json",
                      "Authorization": f"Bearer {token}",
                      "User-Agent": UA})
    r = S.post(f"{MINERU}/api/v4/tokens", json={"token_name": f"ctf_{int(time.time())}"}, timeout=30)
    j = r.json()
    if j.get("code") != 0:
        msg = str(j.get("msg", ""))
        if "maximum" in msg.lower() or "上限" in msg:
            raise RuntimeError("建 token 失败：已达单账号 token 数量上限。请先删除旧 token（刷新模式会自动尝试删旧）或检查账号 token 列表")
        raise RuntimeError(f"建 token 失败: {j.get('msg')}")
    api_key = j["data"]["token"]
    log(f"  [Token] {api_key[:30]}...")
    return api_key


# ---------- 6.8 流水线拆分（注册与邮件等待并行覆盖） ----------
async def register_only(page, phone):
    """阶段 1：仅创建邮箱 + 注册（~20s），邮件等待留到阶段 2 覆盖"""
    addr, mailpwd, mailtok = mailtm_create()
    try:
        username = await register(page, addr)
        return username, addr, mailtok
    except Exception as e:
        # ★ 注册失败也把邮箱凭据留档（邮件可能已发出，可手动激活/登录）
        log_cred("register_failed", {"username": "?", "email": addr, "phone": phone,
                                     "password": mailpwd, "api_key": "",
                                     "created_at": time.strftime(TS_FMT), "expires_at": ""})
        raise


async def finalize_account(page, username, addr, mailtok, phone):
    """阶段 2：等激活邮件（大多已到）→ 激活 → 登录 → 绑定 → token → 验收"""
    link = mailtm_wait_activate(mailtok, timeout_s=240)
    await safe_goto(page, link, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(5000)
    token = await sso_login(page, username)
    await bind_phone_via_browser(page, phone)
    api_key = await create_token(page, token)
    try:
        await verify(page, token)
    except Exception as e:
        # ★ 验收失败不阻止保存（token 已创建，可能仍可用）
        log(f"  ⚠️ 验收失败（token 仍保存）: {str(e)[:80]}")
    created = time.strftime(TS_FMT)
    expires = time.strftime(TS_FMT, time.localtime(time.time() + TOKEN_LIFETIME_DAYS * 86400))
    row = {"username": username, "email": addr, "phone": phone,
           "api_key": api_key, "password": _pwd(),
           "created_at": created, "expires_at": expires}
    log_cred("final", row)  # ★ 完整凭据（含 token）入日志兜底
    return row


# ---------- 6.5 刷新 token（账号已存在，3 个月过期后复用登录） ----------
async def _try_delete_old_tokens(page, token):
    """建新 token 前尝试删旧 token（释放'每账号限 1 个'配额）。
    GET /api/v4/tokens 列表；DELETE 接口存在性未确认——任何异常仅告警，不阻断流程。"""
    try:
        ck = {c["name"]: c["value"] for c in await page.context.cookies(MINERU)}
    except Exception:
        ck = {}
    S = requests.Session()
    S.cookies.update(ck)
    S.headers.update({"Content-Type": "application/json",
                      "Authorization": f"Bearer {token}",
                      "User-Agent": UA})
    try:
        r = S.get(f"{MINERU}/api/v4/tokens", timeout=20)
        if r.status_code != 200:
            log(f"  [删旧token] 列表接口不可用（HTTP {r.status_code}），跳过")
            return
        items = r.json().get("data") or []
        if not isinstance(items, list):
            log(f"  [删旧token] 列表响应结构未知（data={type(items).__name__}），跳过")
            return
        for it in items:
            tid = it.get("id") or it.get("token_id")
            if not tid:
                continue
            try:
                dr = S.delete(f"{MINERU}/api/v4/tokens/{tid}", timeout=20)
                log(f"  [删旧token] DELETE {tid} → HTTP {dr.status_code}")
            except Exception as e:
                log(f"  [删旧token] DELETE {tid} 异常: {str(e)[:40]}")
    except Exception as e:
        log(f"  [删旧token] 接口不可用，跳过: {str(e)[:50]}")


async def refresh_account(page, username, password=None):
    """重新登录已有账号并创建新 token（手机已绑定，无需重复绑定）。
    password：CSV 行内密码；为空则用统一密码 _pwd()。"""
    log(f"\n===== 刷新 {username} =====")
    token = await sso_login(page, username, password)
    # ★ 建新 token 前尝试删旧 token（避免撞'已达上限'；接口未确认则跳过）
    await _try_delete_old_tokens(page, token)
    api_key = await create_token(page, token)
    try:
        await verify(page, token)
    except Exception as e:
        # ★ 验收失败不阻止保存（token 已创建，可能仍可用）
        log(f"  ⚠️ 验收失败（token 仍保存）: {str(e)[:80]}")
    created = time.strftime(TS_FMT)
    expires = time.strftime(TS_FMT, time.localtime(time.time() + TOKEN_LIFETIME_DAYS * 86400))
    log(f"  ✅ 新 token: {api_key[:20]}...（{expires} 过期）")
    log_cred("refresh", {"username": username, "email": "", "phone": "",
                         "password": password or _pwd(), "api_key": api_key,
                         "created_at": created, "expires_at": expires})
    return api_key, created, expires


# ---------- 7. 验收（users/auth 兑换 + opendatalab_session） ----------
async def verify(page, token):
    # 主站会话：token 已是主站 localStorage 的（sso_login 返回）
    # 确保 cookie 设置（主站域）
    await page.evaluate("""(t) => {
      document.cookie = 'opendatalab_session=' + t + '; domain=.mineru.net; path=/';
      document.cookie = 'uaa-token=' + t + '; domain=.mineru.net; path=/';
    }""", token)
    cookies = await page.context.cookies(MINERU)
    ck = {c["name"]: c["value"] for c in cookies}
    S = requests.Session()
    S.cookies.update(ck)
    S.headers.update({"Content-Type": "application/json",
                      "Authorization": f"Bearer {token}",
                      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"})
    r2 = S.post(f"{MINERU}/api/v4/file-urls/batch", json={
        "files": [{"name": "test.pdf", "url": "https://example.com/test.pdf"}], "language": "en"}, timeout=30)
    j = r2.json()
    if j.get("code") != 0:
        raise RuntimeError(f"验收失败: {j.get('code')} {j.get('msg')}")
    log(f"  [验收] batch_id={j['data']['batch_id'][:12]}... ✅")
    return True


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--phone-start", type=int, default=13100139000)
    ap.add_argument("--mode", choices=["register", "refresh", "complete"], default="register",
                    help="register=注册新账号；refresh=刷新已有 token；complete=补全占位账号（空 token）")
    ap.add_argument("--pipeline", action="store_true",
                    help="流水线模式：先批量注册（邮件等待被覆盖），再逐个激活/登录/建token（更快）")
    ap.add_argument("--debug", action="store_true",
                    help="详细日志：异常时打印完整 traceback 到日志（默认只打摘要）")
    args = ap.parse_args()
    # ★ 日志文件初始化（logs/mineru_batch_日期.log，按天轮转）
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs"), exist_ok=True)
    logf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                        f"mineru_batch_{time.strftime('%Y%m%d')}.log")
    credf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                         f"credentials_{time.strftime('%Y%m%d')}.csv")
    set_logfile(logf)
    set_cred_file(credf)
    log(f"════ 启动 mode={args.mode} count={args.count} phone_start={args.phone_start} pipeline={args.pipeline}")
    log(f"日志文件: {logf}")
    log(f"凭据兜底: {credf}（[CRED] 行 + 独立 CSV，CSV 保存失败也能恢复 token/账号）")
    log(f"账号 CSV: {OUT}")
    # ★ 提前校验密码配置（避免跑到一半才报错）；refresh 模式用 CSV 行内密码，可跳过
    if args.mode != "refresh":
        try:
            _pwd()
        except RuntimeError as e:
            log(f"❌ {e}")
            sys.exit(1)
    async with async_playwright() as p:
        b, ctx = await connect(p)
        page = get_page(ctx)
        if page is None:
            page = await ctx.new_page()
        # ★ 全局处理弹窗（Outlook/表单 beforeunload 会阻塞 goto）
        async def on_dialog(dialog):
            try:
                await dialog.dismiss()
            except Exception:
                pass
        page.on("dialog", on_dialog)
        rows = []
        if args.pipeline:
            # ★ 流水线模式（可选）：阶段 1 批量注册 → 阶段 2 逐个激活+登录+建token
            #   注册成功立即写占位行（api_key 空）——防中断丢失账号
            accs = []
            for i in range(args.count):
                phone = str(args.phone_start + i)
                log(f"\n[{i+1}/{args.count}] ── 注册 {phone} ──")
                try:
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = await ctx.new_page()
                    page.on("dialog", on_dialog)
                    username, addr, mailtok = await register_only(page, phone)
                    prow = {"username": username, "email": addr, "phone": phone,
                            "api_key": "", "password": _pwd(),
                            "created_at": time.strftime(TS_FMT), "expires_at": ""}
                    log_cred("placeholder", prow)  # ★ 占位凭据入日志兜底
                    save_row(prow)
                    accs.append((phone, username, addr, mailtok))
                    log(f"  [已注册占位] {username} / {phone}")
                except Exception as e:
                    log(f"  ❌ 注册 {phone}: {str(e)[:100]}")
                    if args.debug:
                        log(traceback.format_exc())
            log(f"[流水线] 阶段 1 完成（{len(accs)} 个待激活），开始阶段 2...")
            ok = 0
            for i, (phone, username, addr, mailtok) in enumerate(accs):
                log(f"\n[{i+1}/{len(accs)}] ── 激活+登录+建token {phone}（{username}）──")
                try:
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = await ctx.new_page()
                    page.on("dialog", on_dialog)
                    row = await finalize_account(page, username, addr, mailtok, phone)
                    update_csv_row(username, {"api_key": row["api_key"],
                                              "created_at": row["created_at"],
                                              "expires_at": row["expires_at"]})
                    ok += 1
                    log(f"  ✅ {row['username']} / {row['api_key'][:20]}...")
                except Exception as e:
                    log(f"  ❌ {phone}: {str(e)[:100]}")
            log(f"\n流水线完成: 成功 {ok}/{len(accs)}")
        elif args.mode == "complete":
            # ★ 补全模式：占位行（空 token）→ 登录 + 绑定 + 建 token
            accounts = []
            with open(OUT, encoding="utf-8") as f:
                accounts = list(csv.DictReader(f))
            targets = [a for a in accounts if not a.get("api_key")][:args.count]
            ok = 0
            for acc in targets:
                try:
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = await ctx.new_page()
                    page.on("dialog", on_dialog)
                    log(f"\n===== 补全 {acc['username']}（{acc['phone']}） =====")
                    token = await sso_login(page, acc["username"])
                    try:
                        await bind_phone_via_browser(page, acc["phone"])
                    except Exception as e:
                        log(f"  ⚠️ 绑定: {str(e)[:60]}（可能已绑定，继续）")
                    api_key = await create_token(page, token)
                    try:
                        await verify(page, token)
                    except Exception as e:
                        log(f"  ⚠️ 验收失败（token 仍保存）: {str(e)[:60]}")
                    created = time.strftime(TS_FMT)
                    expires = time.strftime(TS_FMT, time.localtime(time.time() + TOKEN_LIFETIME_DAYS * 86400))
                    log_cred("complete", {"username": acc["username"], "email": acc.get("email", ""),
                                          "phone": acc.get("phone", ""), "password": _pwd(),
                                          "api_key": api_key, "created_at": created, "expires_at": expires})
                    update_csv_row(acc["username"], {"api_key": api_key,
                                                      "created_at": created, "expires_at": expires})
                    ok += 1
                    log(f"  ✅ {acc['username']} / {api_key[:20]}...")
                except Exception as e:
                    log(f"  ❌ {acc['username']}: {str(e)[:100]}")
                    if args.debug:
                        log(traceback.format_exc())
            log(f"\n补全完成: 成功 {ok}/{len(targets)}")
        elif args.mode == "refresh":
            # ★ 刷新模式：读 CSV 已有账号 → 重新登录建新 token
            accounts = []
            with open(OUT, encoding="utf-8") as f:
                accounts = list(csv.DictReader(f))
            ok = 0
            for acc in accounts[:args.count]:
                try:
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = await ctx.new_page()
                    page.on("dialog", on_dialog)
                    api_key, created, expires = await refresh_account(page, acc["username"], acc["password"])
                    update_csv_row(acc["username"], {"api_key": api_key, "created_at": created, "expires_at": expires})
                    ok += 1
                except Exception as e:
                    log(f"  ❌ {acc['username']}: {str(e)[:120]}")
                    if args.debug:
                        log(traceback.format_exc())
            log(f"\n刷新完成: 成功 {ok}/{len(accounts[:args.count])}")
        else:
            # ★ 串行全流程（默认）：注册→占位保存→等激活→登录→绑定→建token→验收→更新
            #   一个账号完整跑完再下一个（用户要求：不批量囤账号）
            for i in range(args.count):
                phone = str(args.phone_start + i)
                log(f"\n[{i+1}/{args.count}] ════ 账号 {phone} ════")
                try:
                    # ★ 每账号独立新标签页（防导航状态污染）
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = await ctx.new_page()
                    page.on("dialog", on_dialog)
                    # 1) 注册（成功后立即占位落盘，防中断丢账号）
                    username, addr, mailtok = await register_only(page, phone)
                    prow = {"username": username, "email": addr, "phone": phone,
                            "api_key": "", "password": _pwd(),
                            "created_at": time.strftime(TS_FMT), "expires_at": ""}
                    log_cred("placeholder", prow)  # ★ 占位凭据入日志兜底
                    save_row(prow)
                    log(f"  [已注册占位] {username} / {phone}")
                    # 2) 激活 + 登录 + 绑定 + 建 token + 验收
                    row = await finalize_account(page, username, addr, mailtok, phone)
                    # 3) 更新占位行：补 token + 时间戳
                    update_csv_row(username, {"api_key": row["api_key"],
                                              "created_at": row["created_at"],
                                              "expires_at": row["expires_at"]})
                    rows.append(row)
                    log(f"  ✅ {row['username']} / {row['api_key'][:20]}...")
                except Exception as e:
                    log(f"  ❌ {phone}: {str(e)[:120]}")
                    if args.debug:
                        log(traceback.format_exc())
                    # 失败不中断，继续下一个
                # ★ 每账号间随机停顿 3-8s（降低批量触发风控的概率）
                await asyncio.sleep(random.uniform(3, 8))
            log(f"\n本批完成: 成功 {len(rows)}/{args.count}")
            log(f"账号已保存: {OUT}")
            log(f"★ 若 CSV 有缺漏，凭据可从日志恢复: grep '[CRED]' {logf} 或查看 {credf}")
    # with 块结束自动断开（不关闭真实浏览器）


if __name__ == "__main__":
    asyncio.run(main())
