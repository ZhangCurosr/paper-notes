#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 Web Dashboard —— 查看云端 MinerU API 任务情况
====================================================
零第三方依赖（纯标准库）。启动后在浏览器打开 http://127.0.0.1:8901 即可。

功能：
  - 服务概览（在线状态 / 运行时长 / 调度策略 / token 数 / flash 通道）
  - KPI 卡片（提交 ok/err / 成功率 / 延迟分位 / 解析页数 / 熔断 / 配额暂停 / 429 / 每日配额剩余）
  - 24h 任务趋势折线图（canvas 手绘，无外部资源）
  - token 池明细表（状态 / 成功率 / 计数 / 延迟 / preflight / 每日剩余，可排序）
  - 最近任务列表（状态徽章 / 通道 / 来源 / 错误，可筛选）
  - 错误分布与失败原因 Top10
  - 自动刷新（默认 15s）+ 手动刷新；数据本地代理缓存 15s，不直连浏览器→云端（云端 CORS 默认关）

用法：
  set MINERU_API_KEY=sk-admin-xxx     # admin 或 user key
  python scripts/local_dashboard.py
  python scripts/local_dashboard.py --port 8901 --refresh 15 --key sk-xxx

说明：
  - 仅监听 127.0.0.1（不回暴露内网）
  - key 只从命令行/环境变量读取，不写入任何文件，不出现在页面
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

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VERSION = "1.0"
DEFAULT_BASE = "https://mineru-api-sdwh.onrender.com"
UA = "python-requests/2.32.3 (mineru-api-client)"
RETRY_CODES = (400, 404, 429, 500, 502, 503, 504)


class CloudClient:
    """云端 API 代理客户端（带缓存）"""

    def __init__(self, base, key, cache_ttl=15):
        self.base = base.rstrip("/")
        self.key = key
        self.ttl = cache_ttl
        self.cache = {}          # path -> (expire_at, data)
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
                return {"code": e.code, "msg": e.read().decode(errors="replace")[:200],
                        "http": e.code}
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
        return True


# ─────────────────────────── 前端页面 ───────────────────────────

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>MinerU API Dashboard</title>
<style>
  :root { --bg:#0f1420; --card:#171e2e; --line:#26304a; --fg:#dbe4f5; --dim:#7d8aa5;
          --ok:#34d399; --warn:#fbbf24; --bad:#f87171; --acc:#60a5fa; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--fg); font:14px/1.5 "Segoe UI",system-ui,sans-serif; padding:20px; }
  h1 { font-size:18px; } h2 { font-size:14px; color:var(--dim); margin:18px 0 8px; text-transform:uppercase; letter-spacing:.5px;}
  .bar { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  .bar .spacer { flex:1; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
  .ok { background:var(--ok); } .bad { background:var(--bad); } .warn { background:var(--warn); }
  button { background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:6px;
           padding:6px 14px; cursor:pointer; font-size:13px; }
  button:hover { border-color:var(--acc); }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  .card .v { font-size:22px; font-weight:600; margin-top:2px; }
  .card .k { color:var(--dim); font-size:12px; }
  .card.ok .v { color:var(--ok); } .card.warn .v { color:var(--warn); } .card.bad .v { color:var(--bad); }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; margin-top:10px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:var(--dim); font-weight:500; padding:6px 8px; border-bottom:1px solid var(--line); cursor:pointer; white-space:nowrap;}
  td { padding:6px 8px; border-bottom:1px solid #1e2740; white-space:nowrap; }
  td.src { max-width:340px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  tr:hover td { background:#1b2438; }
  .tag { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; }
  .tag.done{background:#0f3d2e;color:var(--ok);} .tag.failed{background:#45201f;color:var(--bad);}
  .tag.pending{background:#3d3310;color:var(--warn);} .tag.submitted{background:#17324f;color:var(--acc);}
  .tag.bad{background:#45201f;color:var(--bad);} .tag.suspended{background:#3d3310;color:var(--warn);}
  .tag.active{background:#0f3d2e;color:var(--ok);}
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  @media(max-width:1000px){ .grid2{grid-template-columns:1fr;} }
  #toast { position:fixed; top:16px; right:16px; background:#45201f; color:var(--bad);
           border:1px solid var(--bad); padding:10px 16px; border-radius:8px; display:none; }
  .dim { color:var(--dim); font-size:12px; }
  canvas { width:100%; height:150px; }
  .errline { color:var(--bad); font-size:12px; font-family:monospace; }
</style>
</head>
<body>
<div class="bar">
  <h1>MinerU API Dashboard <span class="dim">本地代理 v1.0</span></h1>
  <span id="svc"><span class="dot warn"></span>连接中…</span>
  <span class="spacer"></span>
  <span class="dim" id="cache">缓存 15s</span>
  <button onclick="refreshAll()">手动刷新</button>
</div>
<div id="toast"></div>

<div class="cards" id="kpis"></div>

<h2>24h 任务趋势</h2>
<div class="panel"><canvas id="chart"></canvas></div>

<div class="grid2">
  <div>
    <h2>Token 池 <span class="dim" id="poolinfo"></span></h2>
    <div class="panel" style="max-height:420px;overflow:auto"><table id="toktbl">
      <thead><tr><th onclick="sortTok('status')">状态</th><th onclick="sortTok('token')">token</th>
      <th onclick="sortTok('success_rate')">成功率</th><th onclick="sortTok('ok')">成功</th>
      <th onclick="sortTok('err')">失败</th><th onclick="sortTok('rate_limited')">429</th>
      <th onclick="sortTok('latency_ms')">延迟ms</th><th>preflight</th><th onclick="sortTok('files_left')">今日剩余</th></tr></thead>
      <tbody></tbody></table></div>
  </div>
  <div>
    <h2>错误分布与失败原因</h2>
    <div class="panel" id="errbox"></div>
  </div>
</div>

<h2>最近任务 <span class="dim" id="taskinfo"></span></h2>
<div class="panel">
  <div class="bar" style="margin-bottom:8px">
    <button onclick="setStatus('')">全部</button>
    <button onclick="setStatus('pending')">pending</button>
    <button onclick="setStatus('submitted')">submitted</button>
    <button onclick="setStatus('done')">done</button>
    <button onclick="setStatus('failed')">failed</button>
    <span class="spacer"></span>
    <span class="dim">limit 50</span>
  </div>
  <div style="max-height:380px;overflow:auto"><table id="tasktbl">
    <thead><tr><th>task_id</th><th>状态</th><th>通道</th><th>来源</th><th>创建时间</th><th>错误</th></tr></thead>
    <tbody></tbody></table></div>
</div>

<script>
let tokenSort = 'success_rate', statusF = '';
const BASE = '/api';

function esc(s){ return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function toast(m){ const t=document.getElementById('toast'); t.textContent=m; t.style.display='block';
  setTimeout(()=>t.style.display='none', 4000); }
async function api(p){ const r = await fetch(BASE + p); return r.json(); }

async function refreshAll(){
  try {
    const [ov, tk, errb] = await Promise.all([
      api('/overview'), api('/tokens'), api('/errbox') ]);
    renderOverview(ov); renderTokens(tk); renderErr(errb); renderChart(ov);
  } catch(e){ toast('加载失败: '+e); }
  loadTasks();
}
async function loadTasks(){
  try {
    const d = await api('/tasks?limit=50&status=' + encodeURIComponent(statusF));
    renderTasks(d);
  } catch(e){ /* 忽略 */ }
}

function renderOverview(ov){
  const d = ov.data || {}, tk = d.tokens || {};
  document.getElementById('svc').innerHTML =
    `<span class="dot ${d.uptime==null?'bad':'ok'}"></span>` +
    (d.uptime==null ? '云端不可达' :
     `在线 ${Math.floor(d.uptime/3600)}h ${Math.floor(d.uptime%3600/60)}m | 策略 ${tk.strategy} | `+
     `${tk.tokens} token | v${ov.version||'?'}`);
  const q = tk.daily || {};
  const cards = [
    ['提交成功', tk.ok ?? '-', 'ok'], ['提交失败', tk.err ?? '-', tk.err>0?'warn':''],
    ['成功率', (tk.avg_success_rate*100).toFixed(1)+'%', 'ok'],
    ['解析页数', tk.pages_parsed ?? '-', ''],
    ['延迟 p99', (tk.latency_ms?.p99 ?? '-') + (tk.latency_ms?.p99?'ms':''), ''],
    ['熔断中', tk.banned_now ?? '-', tk.banned_now>0?'bad':''],
    ['配额暂停', tk.suspended_now ?? '-', tk.suspended_now>0?'warn':''],
    ['429 冷却', tk.cooling ?? '-', ''],
    ['今日剩余文件', q.files_left ?? '-', q.files_left<500?'warn':''],
    ['今日剩余优先页', q.pages_priority_left ?? '-', ''],
    ['flash 任务', d.flash?.tasks ?? 0, ''], ['API 请求', d.stats?.api_requests ?? 0, ''] ];
  document.getElementById('kpis').innerHTML = cards.map(c =>
    `<div class="card ${c[2]}"><div class="k">${c[0]}</div><div class="v">${esc(c[1])}</div></div>`).join('');
  document.getElementById('poolinfo').textContent =
    `preflight ${JSON.stringify(tk.preflight||{})} | err_dist ${JSON.stringify(tk.err_dist||{})}`;
}

function renderChart(ov){
  const tr = ov.data?.trends_24h || {};
  const keys = Object.keys(tr), vals = keys.map(k=>tr[k]);
  const cv = document.getElementById('chart'), ctx = cv.getContext('2d');
  const W = cv.width = cv.offsetWidth*2, H = cv.height = 150*2;
  ctx.clearRect(0,0,W,H);
  const max = Math.max(1, ...vals), n = keys.length || 1;
  const pad = 8;
  ctx.strokeStyle = '#60a5fa'; ctx.lineWidth = 2.5; ctx.beginPath();
  vals.forEach((v,i)=>{ const x = pad + i*(W-2*pad)/Math.max(1,n-1);
    const y = H - pad - (v/max)*(H-2*pad);
    i===0?ctx.moveTo(x,y):ctx.lineTo(x,y); });
  ctx.stroke();
  ctx.fillStyle = 'rgba(96,165,250,.12)'; ctx.lineTo(W-pad, H-pad); ctx.lineTo(pad, H-pad); ctx.closePath(); ctx.fill();
  ctx.fillStyle = '#7d8aa5'; ctx.font = '10px sans-serif';
  if(keys.length){ ctx.fillText(keys[0], pad, H-2); ctx.fillText(keys[keys.length-1], W-64, H-2); }
}

let tokData = [];
function renderTokens(d){
  tokData = (d.data?.tokens || []).slice();
  sortTok(tokenSort, true);
}
function tokStatus(t){
  if (t.ban_active) return ['熔断','bad']; if (t.suspend_active) return ['暂停','suspended'];
  if (t.cooling) return ['冷却','warn']; return ['active','active'];
}
function sortTok(k, again){
  tokenSort = k; 
  const map = {success_rate:1, ok:1, err:1, rate_limited:1, latency_ms:1, files_left:1, token:0};
  tokData.sort((a,b)=>{
    const av=a[k], bv=b[k]; if (typeof av==='string') return av.localeCompare(bv);
    return (av||0)-(bv||0); });
  renderTokBody();
}
function renderTokBody(){
  const tb = document.querySelector('#toktbl tbody');
  tb.innerHTML = tokData.map(t => {
    const [s,cl] = tokStatus(t);
    return `<tr><td><span class="tag ${cl}">${s}</span></td><td>${esc(t.token)}</td>
      <td>${(t.success_rate*100).toFixed(0)}%</td><td>${t.ok}</td><td>${t.err}</td>
      <td>${t.rate_limited}</td><td>${t.latency_ms ?? '-'}</td>
      <td>${t.preflight===true?'<span class="tag active">ok</span>':t.preflight===false?'<span class="tag bad">bad</span>':'<span class="dim">skip</span>'}</td>
      <td>${t.files_left ?? '-'}</td></tr>`;
  }).join('');
}

function renderErr(d){
  const dd = d.data || {};
  const ed = dd.err_dist || {}, fr = dd.fail_reasons || {};
  const edHtml = Object.keys(ed).length ? Object.entries(ed)
    .map(([k,v])=>`<div>${esc(k)}: <b>${v}</b></div>`).join('') : '<div class="dim">无</div>';
  const frHtml = Object.keys(fr).length ? Object.entries(fr)
    .map(([k,v])=>`<div class="errline">${esc(k)}: ${v}</div>`).join('') : '<div class="dim">无</div>';
  document.getElementById('errbox').innerHTML =
    `<div class="dim" style="margin-bottom:6px">错误码分布（err_dist）</div>${edHtml}` +
    `<div class="dim" style="margin:10px 0 6px">失败原因 Top10（fail_reasons）</div>${frHtml}`;
}

function setStatus(s){ statusF = s; loadTasks(); }
function renderTasks(d){
  const dd = d.data || {};
  document.getElementById('taskinfo').textContent = `共 ${dd.total ?? 0} 条（我的 key）`;
  const tb = document.querySelector('#tasktbl tbody');
  if (!(dd.tasks||[]).length){ tb.innerHTML = '<tr><td colspan="6" class="dim">暂无任务</td></tr>'; return; }
  tb.innerHTML = dd.tasks.map(t => `<tr>
    <td>${esc(t.task_id)}</td><td><span class="tag ${t.status}">${esc(t.status)}</span></td>
    <td>${esc(t.channel)}</td><td class="src" title="${esc(t.source)}">${esc(t.source)}</td>
    <td>${new Date(t.created_at*1000).toLocaleString()}</td><td class="errline">${esc(t.error||'')}</td></tr>`).join('');
}

setInterval(refreshAll, 15000);
refreshAll();
</script>
</body></html>
"""


# ─────────────────────────── 本地 HTTP 服务 ───────────────────────────

def make_handler(client):
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
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass  # 静默访问日志

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                return self._page()
            if path == "/api/overview":
                d = client.get("/v1/stats")
                v = client.get("/health")
                ver = v.get("data", {}).get("version", "?")
                return self._json({"data": d.get("data"), "code": d.get("code"),
                                   "msg": d.get("msg"), "version": ver,
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
            if path == "/api/tasks":
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                limit = min(max(int(q.get("limit", ["50"])[0]), 1), 200)
                st = q.get("status", [""])[0]
                p = f"/v1/tasks?limit={limit}"
                if st:
                    p += f"&status={st}"
                return self._json(client.get(p))
            if path == "/api/refresh":
                client.refresh()
                return self._json({"code": 0})
            self._json({"code": 404, "msg": "not found"}, 404)
    return Handler


def main():
    ap = argparse.ArgumentParser(description="本地 MinerU API Dashboard")
    ap.add_argument("--base", default=os.environ.get("MINERU_API_BASE", DEFAULT_BASE),
                    help="云端服务地址")
    ap.add_argument("--key", default=os.environ.get("MINERU_API_KEY", "") or
                    os.environ.get("MINERU_ADMIN_KEY", ""), help="API key（admin 或 user）")
    ap.add_argument("--port", type=int, default=8901, help="本地监听端口")
    ap.add_argument("--refresh", type=int, default=15, help="云端数据缓存秒数")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    args = ap.parse_args()

    if not args.key:
        sys.exit("缺少 API key：--key 或环境变量 MINERU_API_KEY / MINERU_ADMIN_KEY")

    client = CloudClient(args.base, args.key, cache_ttl=args.refresh)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(client))
    print(f"MinerU API Dashboard v{VERSION}")
    print(f"  云端: {args.base}")
    print(f"  本地: http://{args.host}:{args.port}  （Ctrl+C 退出）")
    print(f"  缓存: {args.refresh}s | key: {args.key[:10]}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
