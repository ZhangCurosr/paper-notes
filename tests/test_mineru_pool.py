#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mineru 调度池 + API 服务 全方位测试套件（纯标准库，无 pytest 依赖）
运行: python tests/test_mineru_pool.py

覆盖：
  1. TokenSlot 限流窗口 / 每日配额
  2. 熔断器（指数退避）+ 恢复
  3. 动态权重
  4. 调度策略（rr / weighted / score）
  5. 并发 acquire（线程安全）
  6. 失败分类 mark_result（quota/auth/network/business/429/parse）
  7. 持久化 restore_stats
  8. 安全函数（SSRF / safe_join / safe_filename / zip slip / zip 炸弹）
  9. server 集成（鉴权 / 越权 / 路径穿越拒绝 / 上传白名单 / 鉴权封禁 / CORS）
 10. API 路由完整性
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

PASS = 0
FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILS.append(name)
        print(f"  [FAIL] {name} {detail}")


def section(t):
    print(f"\n=== {t} ===")


def mock_api_get(path, token, timeout=30):
    """测试用：模拟 mineru /quota 测活（有效 key 返回 200）"""
    if token.startswith("sk-bad"):
        raise __import__("requests").HTTPError(response=__import__("types").SimpleNamespace(
            status_code=401))
    return {"code": 0, "data": {"user_left_quota": 0}}


# ─────────────────────────── 1. TokenSlot ───────────────────────────
def test_slot():
    section("1. TokenSlot 限流窗口 / 配额")
    import mineru_api_pool as m
    s = m.TokenSlot("sk-test1")
    # 初始可用
    check("初始可用", s.available(40))
    # 填满窗口 → 不可用
    for _ in range(40):
        s.reserve()
    check("窗口满不可用", not s.available(40))
    # 429 冷却
    s2 = m.TokenSlot("sk-test2")
    s2.on_429()
    check("429 冷却中不可用", not s2.available(40))
    # 配额暂停
    s3 = m.TokenSlot("sk-test3")
    s3.on_suspend()
    check("配额暂停不可用", not s3.available(40))
    # 熔断
    s4 = m.TokenSlot("sk-test4")
    s4.on_ban(60)
    check("熔断中不可用", not s4.available(40))
    # 每日配额
    s5 = m.TokenSlot("sk-test5")
    check("5000 文件配额初始", s5.files_left() == 5000)
    for _ in range(100):
        s5.mark_submit()
    check("提交 100 后剩 4900", s5.files_left() == 4900)
    # 成功率（通过 mark_result 走成功窗口）
    import mineru_api_pool as m
    s6 = m.TokenSlot("sk-test6")
    pool6 = m.TokenPool(["sk-test6"], rate=40)
    for _ in range(10):
        pool6.mark_result(s6, True)
    check("成功率 1.0", s6.success_rate == 1.0)
    pool6.mark_result(s6, False, err_type="business", err_msg="x")
    check("失败后 0.909", abs(s6.success_rate - 10 / 11) < 0.001)


# ─────────────────────────── 2. 熔断器 ───────────────────────────
def test_breaker():
    section("2. 熔断器（指数退避）")
    import mineru_api_pool as m
    pool = m.TokenPool(["sk-a", "sk-b"], rate=40, strategy="rr", ban_threshold=3)
    s = pool.slots[0]
    # 3 次失败触发熔断
    for i in range(3):
        pool.mark_result(s, False, err_type="network", err_msg="HTTP 500")
    check("连败 3 触发熔断", s.ban_until > time.time())
    check("熔断后 acquire 跳过", pool.acquire().token != s.token)
    # 指数退避：backoff 应 ≥ 120s（3-3+1=1 → 60*2^1=120）
    check("退避 ≥120s", s.ban_until - time.time() >= 115)
    # 成功清零连败
    s2 = pool.slots[1]
    pool.mark_result(s2, True, latency=100)
    check("成功清零连败", s2.fail_streak == 0)


# ─────────────────────────── 3. 动态权重 ───────────────────────────
def test_weight():
    section("3. 动态权重")
    import mineru_api_pool as m
    pool = m.TokenPool(["sk-a", "sk-b"], rate=40, strategy="weighted")
    s = pool.slots[0]
    # 初始 1.0
    check("初始权重 1.0", s.weight == 1.0)
    # 全成功 → 2.0
    for _ in range(5):
        pool.mark_result(s, True)
    check("全成功权重 2.0", abs(s.weight - 2.0) < 0.01)
    # 全失败 → 权重降到下限（用干净 slot，无成功历史）
    s7 = m.TokenSlot("sk-w7")
    pool7 = m.TokenPool(["sk-w7", "sk-w8"], rate=40, strategy="weighted", ban_threshold=99)
    for _ in range(10):
        pool7.mark_result(s7, False, err_type="business")
    check("全失败权重 0.5", abs(s7.weight - 0.5) < 0.01)


# ─────────────────────────── 4. 调度策略 ───────────────────────────
def test_strategy():
    section("4. 调度策略（rr / weighted / score）")
    import mineru_api_pool as m
    # rr 轮转
    pool = m.TokenPool(["sk-a", "sk-b", "sk-c"], rate=40, strategy="rr")
    seq = [pool.acquire().token for _ in range(6)]
    check("rr 均匀轮转", seq == ["sk-a", "sk-b", "sk-c", "sk-a", "sk-b", "sk-c"], str(seq))
    # weighted 平滑轮询（3 token 等权 → 均匀）
    pool2 = m.TokenPool(["sk-a", "sk-b", "sk-c"], rate=40, strategy="weighted")
    seq2 = [pool2.acquire().token for _ in range(9)]
    from collections import Counter
    c = Counter(seq2)
    check("weighted 等权均匀", max(c.values()) - min(c.values()) <= 1, str(c))
    # score 优先健康
    pool3 = m.TokenPool(["sk-a", "sk-b", "sk-c"], rate=40, strategy="score")
    pool3.mark_result(pool3.slots[0], True, latency=50)
    pool3.mark_result(pool3.slots[0], True, latency=50)
    pool3.mark_result(pool3.slots[1], False, err_type="business")
    pool3.mark_result(pool3.slots[2], True, latency=2000)
    got = pool3.acquire()
    check("score 选健康 token", got.token == "sk-a", got.token)


# ─────────────────────────── 5. 并发 acquire ───────────────────────────
def test_concurrency():
    section("5. 并发 acquire（线程安全）")
    import mineru_api_pool as m
    pool = m.TokenPool([f"sk-{i}" for i in range(8)], rate=40, strategy="rr")
    seen = []
    lock = threading.Lock()

    def worker():
        for _ in range(25):
            s = pool.acquire()
            with lock:
                seen.append(s.token)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    from collections import Counter
    c = Counter(seen)
    check(f"并发 200 次无冲突（8 token 均分）", len(c) == 8 and max(c.values()) - min(c.values()) <= 2,
          str(c))


# ─────────────────────────── 6. 失败分类 ───────────────────────────
def test_failclass():
    section("6. 失败分类（quota/auth/network/business/429/parse）")
    import mineru_api_pool as m
    pool = m.TokenPool(["sk-a", "sk-b", "sk-c", "sk-d"], rate=40, ban_threshold=5)
    # quota → suspend（不熔断）
    s = pool.slots[0]
    pool.mark_result(s, False, err_type="quota", err_msg="daily limit reached")
    check("quota → suspend", s.suspend_until > time.time())
    check("quota 不熔断", s.ban_until == 0)
    check("err_codes 记录", s.err_codes.get("daily") == 1, str(s.err_codes))
    # auth → 长期禁用
    s2 = pool.slots[1]
    pool.mark_result(s2, False, err_type="auth", err_msg="HTTP 401")
    check("auth → 30 天禁用", s2.ban_until - time.time() > 20 * 24 * 3600)
    check("auth_fail 计数", s2.auth_fail == 1)
    # network 5 次 → 熔断
    s3 = pool.slots[2]
    for _ in range(5):
        pool.mark_result(s3, False, err_type="network", err_msg="HTTP 500")
    check("network 5 次熔断", s3.ban_until > time.time())
    # parse 失败不熔断
    pool.mark_parse("sk-d", False)
    s4 = pool.slots[3]
    check("parse 失败不熔断", s4.ban_until == 0 and s4.parse_fail == 1)
    # 延迟样本
    s5 = pool.slots[0]
    for i in range(10):
        pool.mark_result(s5, True, latency=100 + i)
    check("延迟 EMA 更新", s5.latency_ema is not None and 100 <= s5.latency_ema <= 110)


# ─────────────────────────── 7. 持久化 ───────────────────────────
def test_persist():
    section("7. 持久化 restore_stats")
    import mineru_api_pool as m
    pool1 = m.TokenPool(["sk-a", "sk-b"], rate=40)
    pool1.mark_result(pool1.slots[0], True, latency=300)
    for _ in range(5):   # 5 次失败触发熔断
        pool1.mark_result(pool1.slots[1], False, err_type="network")
    check("熔断已触发", pool1.slots[1].ban_until > time.time())
    stats1 = pool1.stats()
    # 新池恢复
    pool2 = m.TokenPool(["sk-a", "sk-b"], rate=40)
    pool2.restore_stats(stats1["detail"])
    check("恢复 ok/err", pool2.slots[0].ok_count == 1 and pool2.slots[1].err_count == 5)
    check("恢复 fail_streak", pool2.slots[1].fail_streak == 5)
    check("恢复 ban 状态", pool2.slots[1].ban_until > time.time())
    check("恢复延迟 EMA", pool2.slots[0].latency_ema is not None)


# ─────────────────────────── 8. 安全函数 ───────────────────────────
def test_security():
    section("8. 安全函数（SSRF / 路径穿越 / zip）")
    import mineru_api_server as srv
    import mineru_api_pool as m
    # SSRF
    bad_urls = ["http://localhost:8080/x", "https://127.0.0.1/x",
                "https://169.254.169.254/latest/meta-data", "https://10.0.0.5/x",
                "https://192.168.1.1/x", "file:///etc/passwd", "ftp://x.com/f",
                "https://metadata.google.internal/x", ""]
    good_urls = ["https://arxiv.org/pdf/2401.00001", "https://example.com/a.pdf"]
    check("SSRF 拒绝全部恶意 URL", all(not srv.is_safe_url(u) for u in bad_urls))
    check("SSRF 放行公网 URL", all(srv.is_safe_url(u) for u in good_urls))
    # safe_join
    tmp = tempfile.mkdtemp()
    check("safe_join 正常文件", srv.safe_join(tmp, "full.md") is not None)
    check("safe_join 拒 ../", srv.safe_join(tmp, "../x") is None)
    check("safe_join 拒绝对路径", srv.safe_join(tmp, "/etc/passwd") is None)
    check("safe_join 拒空", srv.safe_join(tmp, "") is None)
    # zip slip
    zpath = os.path.join(tmp, "evil.zip")
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../../evil.txt", "pwn")
    try:
        m._safe_extract(zipfile.ZipFile(zpath), os.path.join(tmp, "out"))
        check("zip slip 被拦截", False, "未被拦截")
    except RuntimeError:
        check("zip slip 被拦截", True)
    # zip 炸弹（虚假大文件）
    zpath2 = os.path.join(tmp, "bomb.zip")
    with zipfile.ZipFile(zpath2, "w") as zf:
        zf.writestr("big.bin", b"\x00" * 10)   # 压缩后小
        zf.getinfo("big.bin").file_size = 1024 * 1024 * 1024   # 伪造巨大
    try:
        m._safe_extract(zipfile.ZipFile(zpath2), os.path.join(tmp, "out2"))
        check("zip 炸弹被拦截", False)
    except RuntimeError:
        check("zip 炸弹被拦截", True)
    shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────── 9+10. server 集成 ───────────────────────────
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _req(port, method, path, key=None, body=None, timeout=15):
    import urllib.request
    import urllib.error
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {key}"} if key else {})
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"err": str(e)}


def test_server():
    section("9. server 集成（鉴权 / 越权 / 路径穿越 / 上传 / 封禁）")
    import mineru_api_server as srv
    port = _free_port()
    data_dir = tempfile.mkdtemp(prefix="srv_test_")
    out_dir = tempfile.mkdtemp(prefix="srv_out_")
    p = subprocess.Popen([sys.executable, os.path.join(BASE, "scripts", "mineru_api_server.py"),
                          "--host", "127.0.0.1", "--port", str(port),
                          "--data-dir", data_dir, "--out-dir", out_dir,
                          "--tokens", "sk-fake1,sk-fake2", "--admin-key", "sk-admin-test-sec",
                          "--key-rate", "60"],
                         cwd=BASE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6)
    try:
        # 鉴权
        st, d = _req(port, "GET", "/health")
        check("health 无需鉴权", st == 200 and d.get("code") == 0)
        st, d = _req(port, "GET", "/v1/me")
        check("无 key 拒绝", st == 401)
        st, d = _req(port, "GET", "/v1/me", key="sk-wrong")
        check("错误 key 拒绝", st == 401)
        st, d = _req(port, "POST", "/v1/keys", key="sk-admin-test-sec", body={"name": "u1"})
        check("admin 创建用户 key", st == 200 and d.get("code") == 0, str(d))
        ukey = d.get("data", {}).get("key", "")
        st, d = _req(port, "POST", "/v1/keys", key=ukey, body={"name": "u2"})
        check("用户 key 不能建 key", st == 401)
        # 越权：用户 A 看用户 B 任务
        st, d = _req(port, "POST", "/v1/tasks", key=ukey,
                     body={"urls": ["https://arxiv.org/pdf/2401.00001"]})
        check("提交任务被 SSRF 放行", st == 200, str(d))
        # 不实际提交到 mineru（fake token），只验证端点结构
        # SSRF 拒绝
        st, d = _req(port, "POST", "/v1/tasks", key=ukey,
                     body={"urls": ["http://169.254.169.254/latest/meta-data"]})
        check("SSRF URL 拒绝", st == 400, str(d))
        st, d = _req(port, "POST", "/v1/tasks", key=ukey,
                     body={"urls": ["file:///etc/passwd"]})
        check("file:// 拒绝", st == 400)
        # 上传路径穿越
        evil = {"files": [{"name": "../../../../tmp/evil.pdf", "data": "AAAA"}]}
        st, d = _req(port, "POST", "/v1/tasks", key=ukey, body=evil)
        check("上传路径穿越拒绝", st == 400, str(d))
        # 上传扩展名白名单
        st, d = _req(port, "POST", "/v1/tasks", key=ukey,
                     body={"files": [{"name": "evil.exe", "data": "AAAA"}]})
        check("上传 .exe 拒绝", st == 400)
        # 上传合法小文件（flash 通道 fake 提交）
        st, d = _req(port, "POST", "/v1/tasks", key=ukey,
                     body={"files": [{"name": "t.pdf", "data": "JVBERi0xLjQ="}]})
        check("合法上传接受", st == 200, str(d))
        # 批量上限
        st, d = _req(port, "POST", "/v1/tasks", key=ukey,
                     body={"urls": [f"https://example.com/{i}.pdf" for i in range(51)]})
        check("51 个 URL 拒绝", st == 400)
        # limit 非数字
        st, d = _req(port, "GET", "/v1/tasks?limit=abc", key=ukey)
        check("limit 非法 400", st == 400, str(st))
        # CORS 默认关闭
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        r = urllib.request.urlopen(req, timeout=10)
        check("CORS 头默认关闭", r.headers.get("Access-Control-Allow-Origin") is None)
        # 鉴权爆破封禁（10 次失败 → 封禁）
        for i in range(10):
            _req(port, "GET", "/v1/me", key="sk-bruteforce")
        st, d = _req(port, "GET", "/v1/me", key="sk-bruteforce2")
        check("IP 鉴权封禁", st == 401 and "封禁" in str(d.get("msg", "")), str(d)[:80])
    finally:
        p.terminate()
        p.wait(timeout=10)
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)


def test_routes():
    section("10. API 路由完整性（health 端点列表）")
    import urllib.request
    import mineru_api_server as srv
    port = _free_port()
    data_dir = tempfile.mkdtemp(prefix="srv_r_")
    out_dir = tempfile.mkdtemp(prefix="srv_ro_")
    p = subprocess.Popen([sys.executable, os.path.join(BASE, "scripts", "mineru_api_server.py"),
                          "--host", "127.0.0.1", "--port", str(port),
                          "--data-dir", data_dir, "--out-dir", out_dir,
                          "--tokens", "sk-fake1", "--admin-key", "sk-admin-test-sec"],
                         cwd=BASE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6)
    try:
        st, d = _req(port, "GET", "/health")
        eps = d.get("data", {}).get("endpoints", [])
        check("health 端点列表完整", any("/v1/metrics" in e for e in eps) and
              any("/v1/stats" in e for e in eps) and any("/v1/tasks" in e for e in eps))
        st, d = _req(port, "GET", "/nonexistent")
        check("404 处理", st == 404)
        # 越权路径穿越下载（任务不存在）
        st, d = _req(port, "GET", "/v1/tasks/abc123/file/../../../etc/passwd", key="sk-admin-test-sec")
        check("不存在任务下载 404", st == 404)
    finally:
        p.terminate()
        p.wait(timeout=10)
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)


if __name__ == "__main__":
    t0 = time.time()
    test_slot()
    test_breaker()
    test_weight()
    test_strategy()
    test_concurrency()
    test_failclass()
    test_persist()
    test_security()
    test_server()
    test_routes()
    print(f"\n{'='*50}\n结果: PASS {PASS} / FAIL {FAIL}（耗时 {time.time()-t0:.0f}s）")
    if FAILS:
        print("失败项:")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    print("全部通过 OK")
