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

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<title>MinerU API Dashboard</title>
<style>
:root{
  --bg:#0d1220; --bg2:#121a2e; --card:#161f36; --card2:#1a2440; --line:#243152;
  --fg:#e6edfb; --dim:#8b98b8; --dim2:#5c6a8f;
  --ok:#2dd4a7; --warn:#f5b94d; --bad:#f2637b; --acc:#5b9dff; --purple:#a78bfa;
  --shadow:0 8px 24px rgba(0,0,0,.35);
}
html[data-theme="light"]{
  --bg:#f2f5fb; --bg2:#e8edf7; --card:#ffffff; --card2:#f6f8fd; --line:#dbe3f2;
  --fg:#1b2740; --dim:#64748b; --dim2:#94a3b8;
  --shadow:0 8px 24px rgba(30,60,120,.08);
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:linear-gradient(160deg,var(--bg),var(--bg2) 60%,var(--bg));color:var(--fg);
  font:14px/1.55 "Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif;padding:22px;min-height:100vh}
.wrap{max-width:1280px;margin:0 auto}
/* 头部 */
.top{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.logo{display:flex;align-items:center;gap:10px}
.logo .ic{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;
  font-size:19px;background:linear-gradient(135deg,#5b9dff,#a78bfa);box-shadow:var(--shadow)}
.top h1{font-size:19px;font-weight:700;letter-spacing:.3px}
.top .sub{color:var(--dim);font-size:12px}
.spacer{flex:1}
.pill{display:inline-flex;align-items:center;gap:7px;background:var(--card);border:1px solid var(--line);
  border-radius:20px;padding:6px 14px;font-size:13px;color:var(--dim)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--warn)}
.dot.ok{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.dot.bad{background:var(--bad);box-shadow:0 0 8px var(--bad)}
.iconbtn{background:var(--card);border:1px solid var(--line);color:var(--fg);border-radius:9px;
  padding:7px 13px;font-size:13px;cursor:pointer;display:inline-flex;align-items:center;gap:7px;transition:.15s}
.iconbtn:hover{border-color:var(--acc);transform:translateY(-1px)}
.iconbtn:active{transform:translateY(0)}
.iconbtn.spin svg{animation:rot 1s linear infinite}
@keyframes rot{to{transform:rotate(360deg)}}
/* KPI */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 15px;position:relative;overflow:hidden;transition:.2s}
.kpi:hover{transform:translateY(-2px);box-shadow:var(--shadow)}
.kpi .k{color:var(--dim);font-size:12px;display:flex;align-items:center;gap:6px}
.kpi .v{font-size:23px;font-weight:700;margin-top:5px;font-variant-numeric:tabular-nums}
.kpi .u{font-size:12px;color:var(--dim);font-weight:400;margin-left:3px}
.kpi .ic{position:absolute;right:-8px;bottom:-10px;font-size:52px;opacity:.08;pointer-events:none}
.kpi.good .v{color:var(--ok)} .kpi.warn .v{color:var(--warn)} .kpi.bad .v{color:var(--bad)} .kpi.acc .v{color:var(--acc)}
/* 配额条 */
.quota{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}
.qbar{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
.qbar .row{display:flex;justify-content:space-between;font-size:12.5px;color:var(--dim);margin-bottom:7px}
.qbar .row b{color:var(--fg);font-variant-numeric:tabular-nums}
.track{height:8px;background:var(--bg2);border-radius:6px;overflow:hidden}
.fill{height:100%;border-radius:6px;background:linear-gradient(90deg,var(--acc),var(--purple));transition:width .6s}
.fill.warn{background:linear-gradient(90deg,var(--warn),#f58a4d)}
.fill.bad{background:linear-gradient(90deg,var(--bad),#d94a62)}
/* 区段标题 */
h2{font-size:13.5px;color:var(--dim);margin:22px 0 10px;text-transform:uppercase;letter-spacing:1px;
  display:flex;align-items:center;gap:8px}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-top:4px}
.grid2{display:grid;grid-template-columns:1.6fr 1fr;gap:14px}
@media(max-width:1020px){.grid2{grid-template-columns:1fr}.quota{grid-template-columns:1fr}}
/* 图表 */
canvas{width:100%;display:block}
.chartwrap{position:relative}
#tip{position:absolute;pointer-events:none;background:var(--card2);border:1px solid var(--line);
  border-radius:8px;padding:6px 10px;font-size:12px;display:none;z-index:10;box-shadow:var(--shadow)}
.legend{display:flex;gap:14px;font-size:12px;color:var(--dim);margin-top:8px}
.legend i{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:5px;vertical-align:-1px}
/* 错误条形 */
.errrow{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:12.5px}
.errrow .lbl{width:70px;color:var(--dim);text-align:right;font-family:monospace}
.errrow .trk{flex:1;height:14px;background:var(--bg2);border-radius:7px;overflow:hidden}
.errrow .fl{height:100%;border-radius:7px;background:linear-gradient(90deg,#f2637b,#f5b94d);min-width:3px}
.errrow .n{width:44px;font-variant-numeric:tabular-nums;color:var(--dim)}
/* 表格 */
.tblwrap{overflow:auto;max-height:430px;border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{position:sticky;top:0;background:var(--card2);color:var(--dim);font-weight:600;padding:9px 10px;
  border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;cursor:pointer;user-select:none;z-index:2}
th:hover{color:var(--fg)}
th .arr{font-size:10px;color:var(--acc)}
td{padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap;font-variant-numeric:tabular-nums}
tr:hover td{background:var(--card2)}
td.src{max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag{display:inline-block;padding:2px 9px;border-radius:12px;font-size:11.5px;font-weight:600;letter-spacing:.3px}
.tag.done{background:rgba(45,212,167,.14);color:var(--ok)}
.tag.failed{background:rgba(242,99,123,.14);color:var(--bad)}
.tag.pending{background:rgba(245,185,77,.14);color:var(--warn)}
.tag.submitted{background:rgba(91,157,255,.14);color:var(--acc)}
.tag.active{background:rgba(45,212,167,.14);color:var(--ok)}
.tag.banned,.tag.bad{background:rgba(242,99,123,.14);color:var(--bad)}
.tag.suspended,.tag.cooling{background:rgba(245,185,77,.14);color:var(--warn)}
.tag.skip{background:rgba(140,150,180,.14);color:var(--dim)}
.srbar{display:inline-block;width:52px;height:6px;background:var(--bg2);border-radius:4px;overflow:hidden;vertical-align:middle;margin-right:6px}
.srbar i{display:block;height:100%;border-radius:4px;background:var(--ok)}
.srbar i.warn{background:var(--warn)} .srbar i.bad{background:var(--bad)}
/* 工具栏 */
.tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.search{background:var(--bg2);border:1px solid var(--line);border-radius:9px;color:var(--fg);
  padding:7px 12px;font-size:13px;width:210px;outline:none;transition:.15s}
.search:focus{border-color:var(--acc)}
.statbar{display:flex;gap:6px;flex-wrap:wrap}
.statbar .seg{background:var(--card2);border:1px solid var(--line);border-radius:9px;padding:6px 13px;
  font-size:12.5px;color:var(--dim);cursor:pointer;transition:.15s}
.statbar .seg.on{color:var(--fg);border-color:var(--acc);background:rgba(91,157,255,.12)}
.statbar .seg b{color:var(--fg)}
/* 任务分布条 */
.dist{display:flex;height:7px;border-radius:5px;overflow:hidden;margin:8px 0 4px}
/* 弹窗 */
.modalbg{position:fixed;inset:0;background:rgba(6,10,20,.6);backdrop-filter:blur(3px);display:none;
  align-items:center;justify-content:center;z-index:50;padding:20px}
.modalbg.show{display:flex}
.modal{background:var(--card);border:1px solid var(--line);border-radius:16px;max-width:640px;width:100%;
  max-height:82vh;overflow:auto;box-shadow:var(--shadow);animation:pop .18s ease}
@keyframes pop{from{transform:scale(.96);opacity:0}}
.modal .mhead{display:flex;align-items:center;gap:10px;padding:16px 20px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--card)}
.modal .mhead h3{font-size:15px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal .mbody{padding:16px 20px}
.kv{display:grid;grid-template-columns:110px 1fr;gap:7px 12px;font-size:13px}
.kv dt{color:var(--dim)} .kv dd{word-break:break-all}
/* toast */
#toast{position:fixed;top:18px;right:18px;background:var(--card2);border:1px solid var(--line);
  color:var(--fg);padding:11px 18px;border-radius:10px;display:none;z-index:99;box-shadow:var(--shadow);font-size:13px}
#toast.err{border-color:var(--bad);color:var(--bad)}
.empty{color:var(--dim);padding:22px;text-align:center;font-size:13px}
.dim{color:var(--dim);font-size:12px}
.foot{color:var(--dim2);font-size:11.5px;text-align:center;margin:26px 0 8px}
/* 告警横幅 */
#alerts{display:flex;flex-direction:column;gap:8px;margin-bottom:14px}
.alert{display:flex;align-items:center;gap:10px;padding:11px 16px;border-radius:12px;font-size:13px;
  border:1px solid;animation:slidein .25s ease}
.alert.bad{background:rgba(242,99,123,.12);border-color:rgba(242,99,123,.5);color:var(--bad)}
.alert.warn{background:rgba(245,185,77,.12);border-color:rgba(245,185,77,.5);color:var(--warn)}
@keyframes slidein{from{transform:translateY(-6px);opacity:0}}
/* KPI 顶部分色条 + 数字动画 */
.kpi::before{content:"";position:absolute;top:0;left:14px;right:14px;height:3px;border-radius:0 0 4px 4px;
  background:linear-gradient(90deg,var(--acc),var(--purple));opacity:0;transition:.2s}
.kpi:hover::before{opacity:1}
.kpi.good::before{background:linear-gradient(90deg,var(--ok),#34d399)}
.kpi.warn::before{background:linear-gradient(90deg,var(--warn),#f58a4d)}
.kpi.bad::before{background:linear-gradient(90deg,var(--bad),#d94a62)}
.kpi .v .num{font-variant-numeric:tabular-nums}
/* 间隔选择 */
select{background:var(--bg2);border:1px solid var(--line);color:var(--fg);border-radius:9px;padding:7px 8px;font-size:12.5px;outline:none;cursor:pointer}
select:hover{border-color:var(--acc)}
/* token 行可点 */
#toktbl tbody tr{cursor:pointer}
/* 弹窗操作按钮 */
.mbody .ops{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.mbody .ops .iconbtn{padding:6px 12px;font-size:12.5px}
.iconbtn.danger:hover{border-color:var(--bad);color:var(--bad)}
.iconbtn.copied{border-color:var(--ok);color:var(--ok)}
.kv .mono{font-family:Consolas,monospace;font-size:12px;background:var(--bg2);border-radius:5px;padding:1px 6px}
</style>
</head>
<body>
<div class="wrap">
  <!-- 头部 -->
  <div class="top">
    <div class="logo"><div class="ic">⚡</div>
      <div><h1>MinerU API Dashboard</h1><div class="sub">云端任务监控 · 本地代理</div></div>
    </div>
    <span class="pill" id="svc"><span class="dot"></span>连接中…</span>
    <span class="pill" id="clock" title="下次自动刷新">—</span>
    <span class="spacer"></span>
    <select id="interval" title="刷新间隔">
      <option value="5">5s</option><option value="15" selected>15s</option>
      <option value="30">30s</option><option value="60">60s</option>
    </select>
    <button class="iconbtn" id="themeBtn" title="切换明暗主题">🌓</button>
    <button class="iconbtn" id="autoBtn" title="自动刷新开关">⏸</button>
    <button class="iconbtn" id="refBtn" onclick="refreshAll()">⟳ 刷新</button>
  </div>

  <!-- 告警横幅 -->
  <div id="alerts"></div>

  <!-- KPI -->
  <div class="kpis" id="kpis"></div>

  <!-- 每日配额 -->
  <div class="quota">
    <div class="qbar"><div class="row"><span>今日文件配额</span><span id="q1t">—</span></div>
      <div class="track"><div class="fill" id="q1f" style="width:0%"></div></div></div>
    <div class="qbar"><div class="row"><span>今日优先页配额</span><span id="q2t">—</span></div>
      <div class="track"><div class="fill" id="q2f" style="width:0%"></div></div></div>
  </div>

  <h2>24h 任务趋势</h2>
  <div class="grid2">
    <div class="panel chartwrap">
      <canvas id="chart"></canvas>
      <div id="tip"></div>
      <div class="legend"><span><i style="background:var(--acc)"></i>每小时任务数</span>
        <span id="daytotal"></span></div>
    </div>
    <div class="panel" id="errbox">
      <div class="dim" style="margin-bottom:4px">错误码分布</div>
      <div id="errdist"><div class="empty">加载中…</div></div>
      <div class="dim" style="margin:14px 0 4px">失败原因 Top10</div>
      <div id="failreasons"><div class="empty">加载中…</div></div>
    </div>
  </div>

  <h2>Token 池 <span class="dim" id="poolinfo"></span></h2>
  <div class="panel">
    <div class="tools">
      <input class="search" id="tokSearch" placeholder="🔍 搜索 token / 状态…" oninput="renderTokBody()">
      <span class="spacer"></span>
      <button class="iconbtn" onclick="exportCSV(tokData,'tokens')">⭳ 导出 CSV</button>
    </div>
    <div class="tblwrap"><table id="toktbl">
      <thead><tr>
        <th onclick="sortTok('status')">状态<span class="arr" id="s-status"></span></th>
        <th onclick="sortTok('token')">token<span class="arr" id="s-token"></span></th>
        <th onclick="sortTok('success_rate')">成功率<span class="arr" id="s-success_rate"></span></th>
        <th onclick="sortTok('ok')">成功<span class="arr" id="s-ok"></span></th>
        <th onclick="sortTok('err')">失败<span class="arr" id="s-err"></span></th>
        <th onclick="sortTok('rate_limited')">429<span class="arr" id="s-rate_limited"></span></th>
        <th onclick="sortTok('latency_ms')">延迟ms<span class="arr" id="s-latency_ms"></span></th>
        <th>preflight</th><th onclick="sortTok('files_left')">今日剩余<span class="arr" id="s-files_left"></span></th>
        <th onclick="sortTok('daily_submits')">今日提交<span class="arr" id="s-daily_submits"></span></th>
      </tr></thead>
      <tbody></tbody></table></div>
  </div>

  <h2>最近任务 <span class="dim" id="taskinfo"></span></h2>
  <div class="panel">
    <div class="tools">
      <input class="search" id="taskSearch" placeholder="🔍 搜索来源 URL…" oninput="renderTasksLocal()">
      <span class="statbar" id="taskSegs"></span>
      <span class="spacer"></span>
      <span class="dim">点击行查看详情</span>
      <button class="iconbtn" onclick="exportCSV(tasks,'tasks')">⭳ 导出 CSV</button>
    </div>
    <div class="tblwrap"><table id="tasktbl">
      <thead><tr>
        <th>task_id</th><th>状态</th><th>通道</th><th>来源</th><th>创建</th><th>耗时</th><th>错误</th>
      </tr></thead>
      <tbody></tbody></table></div>
  </div>

  <div class="foot">local_dashboard v2.0 · 数据经本地代理缓存 · 自动刷新 15s</div>
</div>

<!-- 详情弹窗 -->
<div class="modalbg" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="mhead"><h3 id="mTitle">任务详情</h3>
      <button class="iconbtn" onclick="closeModal()">✕</button></div>
    <div class="mbody" id="mBody"></div>
  </div>
</div>
<div id="toast"></div>

<script>
let tokData=[], tasks=[], tokSort={k:'success_rate',asc:false}, statusF='', taskQ='';
let curTask=null, curTokList=[];
const BASE='/api';

const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtNum=n=>n==null?'-':Number(n).toLocaleString();
function relTime(ts){ if(!ts) return '-'; const d=Date.now()/1000-ts;
  if(d<60) return Math.floor(d)+'s'; if(d<3600) return Math.floor(d/60)+'m';
  if(d<86400) return Math.floor(d/3600)+'h'; return Math.floor(d/86400)+'d'; }
function toast(m,err){ const t=$('toast'); t.textContent=m; t.className=err?'err':''; t.style.display='block';
  setTimeout(()=>t.style.display='none',3500); }
async function api(p,force){ const r=await fetch(BASE+p+(force?'&_='+Date.now():'')); return r.json(); }

/* ── 主题 / 自动刷新 ── */
const themeBtn=$('themeBtn'), autoBtn=$('autoBtn');
let auto=true, left=15;
function applyTheme(t){ document.documentElement.dataset.theme=t; themeBtn.textContent=t==='dark'?'🌓':'☀️'; }
applyTheme(localStorage.getItem('dash_theme')||'dark');
themeBtn.onclick=()=>{ const t=document.documentElement.dataset.theme==='dark'?'light':'dark';
  localStorage.setItem('dash_theme',t); applyTheme(t); };
autoBtn.onclick=()=>{ auto=!auto; autoBtn.textContent=auto?'⏸':'▶'; autoBtn.title=auto?'暂停自动刷新':'开启自动刷新'; };

/* ── 数据加载 ── */
async function refreshAll(){
  const b=$('refBtn'); b.classList.add('spin'); b.innerHTML='⟳ 刷新中…';
  try{
    const [ov,tk]=await Promise.all([api('/overview'),api('/tokens')]);
    renderOverview(ov); renderTokens(tk); renderChart(ov);
    const eb=await api('/errbox'); renderErr(eb);
    loadTasks();
  }catch(e){ toast('加载失败: '+e,true); }
  b.classList.remove('spin'); b.innerHTML='⟳ 刷新';
}

/* ── 概览 ── */
function animNum(el,to,dec,unit){
  if(el==null) return; if(to==null||isNaN(to)){ el.textContent='-'; return; }
  const from=parseFloat(el.dataset.v||'0'); if(isNaN(from))from=0; el.dataset.v=to;
  const t0=performance.now(), dur=450;
  function step(t){ const p=Math.min(1,(t-t0)/dur), v=from+(to-from)*(1-Math.pow(1-p,3));
    el.textContent=(dec!=null?v.toFixed(dec):Math.round(v).toLocaleString())+(unit||'');
    if(p<1) requestAnimationFrame(step); }
  requestAnimationFrame(step);
}
function renderOverview(ov){
  const d=ov.data||{}, tk=d.tokens||{}, q=tk.daily||{};
  const up=d.uptime;
  const s=$('svc');
  if(up==null){ s.innerHTML='<span class="dot bad"></span>云端不可达'; document.title='⚠ MinerU Dashboard 离线'; }
  else{ s.innerHTML=`<span class="dot ok"></span>在线 ${Math.floor(up/3600)}h ${Math.floor(up%3600/60)}m · ${tk.strategy} · ${tk.tokens} token · v${ov.version}`;
    document.title='MinerU Dashboard · 在线'; }
  const pct=tk.pages_parsed||0, err=tk.err||0, ok=tk.ok||0, sr=tk.avg_success_rate;
  const cards=[
    ['提交成功',ok,0,'good','✅'],['提交失败',err,0,err>0?'bad':'','❌'],
    ['成功率',sr,sr!=null?1:null,'good','🎯'],
    ['解析页数',pct,0,'','📄'],['延迟 p99',tk.latency_ms?.p99,tk.latency_ms?.p99!=null?0:null,'','⚡'],
    ['熔断中',tk.banned_now,0,tk.banned_now>0?'bad':'','🧯'],
    ['配额暂停',tk.suspended_now,0,tk.suspended_now>0?'warn':'','⏸'],
    ['429 冷却',tk.cooling,0,'','🌡'],
    ['今日提交',q.submits,0,'','📥'],
    ['剩余文件',q.files_left,0,q.files_left<500?'warn':'','🗂'],
    ['flash 任务',d.flash?.tasks,0,'','🪄'],
    ['API 请求',d.stats?.api_requests,0,'','🔁']];
  $('kpis').innerHTML=cards.map((c,i)=>`<div class="kpi ${c[3]}" id="kpi${i}"><div class="k">${c[4]} ${c[0]}</div><div class="v"><span class="num" id="knum${i}">0</span></div><div class="ic">${c[4]}</div></div>`).join('');
  cards.forEach((c,i)=>animNum($('knum'+i), c[1], c[2], c[0]==='成功率'?'%':c[0].includes('延迟')?'ms':''));
  // 配额条
  const fl=q.files_limit||5000, pl=q.pages_priority_limit||1000;
  const p1=Math.min(100,100*q.submits/fl), p2=Math.min(100,100*q.pages/pl);
  $('q1t').innerHTML=`<b>${fmtNum(q.submits)}</b> / ${fmtNum(fl)} · 剩余 <b>${fmtNum(q.files_left)}</b>`;
  $('q2t').innerHTML=`<b>${fmtNum(q.pages)}</b> / ${fmtNum(pl)} · 剩余 <b>${fmtNum(q.pages_priority_left)}</b>`;
  const f1=$('q1f'),f2=$('q2f'); f1.style.width=p1+'%'; f2.style.width=p2+'%';
  f1.className='fill'+(p1>85?' bad':p1>70?' warn':''); f2.className='fill'+(p2>85?' bad':p2>70?' warn':'');
  $('poolinfo').textContent=`preflight ${JSON.stringify(tk.preflight||{})} · err_dist ${JSON.stringify(tk.err_dist||{})}`;
  $('daytotal').textContent='今日任务 '+fmtNum(d.stats?.tasks_total);
  checkAlerts(ov);
}

/* ── 告警横幅 ── */
function checkAlerts(ov){
  const d=ov.data||{}, tk=d.tokens||{}, q=tk.daily||{}, pre=tk.preflight||{};
  const alerts=[];
  if(d.uptime==null) alerts.push(['bad','⚠ 云端服务不可达，请检查 Render 状态或网络']);
  if(tk.banned_now>0) alerts.push(['bad','🧯 '+tk.banned_now+' 个 token 熔断中（连续失败，自动恢复中）']);
  if(pre.bad>0) alerts.push(['bad','🚫 '+pre.bad+' 个 token 预热探测失败（无效 token，建议从台账替换）']);
  if(q.files_left!=null&&q.files_left<200) alerts.push(['warn','🗂 今日文件配额剩余不足 200（'+q.files_left+'），注意耗尽']);
  if(tk.suspended_now>tk.tokens*0.05) alerts.push(['warn','⏸ '+tk.suspended_now+' 个 token 配额暂停中（超过 5%，12h 自动恢复）']);
  $('alerts').innerHTML=alerts.map(a=>`<div class="alert ${a[0]}">${a[1]}</div>`).join('');
}

/* ── 趋势图（悬停提示） ── */
let chartData={};
function renderChart(ov){
  const tr=ov.data?.trends_24h||{}; chartData=tr;
  const keys=Object.keys(tr), vals=keys.map(k=>tr[k]);
  const cv=$('chart'),ctx=cv.getContext('2d');
  const W=cv.width=cv.offsetWidth*2,H=cv.height=160*2; ctx.clearRect(0,0,W,H);
  const max=Math.max(1,...vals),n=keys.length||1,pad=12;
  // 网格
  ctx.strokeStyle='rgba(90,110,150,.14)'; ctx.lineWidth=1; ctx.fillStyle='#8b98b8'; ctx.font='10px sans-serif';
  for(let g=0;g<=3;g++){ const y=pad+g*(H-2*pad)/3;
    ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(W-pad,y);ctx.stroke();
    ctx.fillText(Math.round(max*(3-g)/3),2,y+3); }
  // 面积 + 折线
  const pts=vals.map((v,i)=>{const x=pad+i*(W-2*pad)/Math.max(1,n-1);const y=H-pad-(v/max)*(H-2*pad);return[x,y];});
  ctx.beginPath();ctx.moveTo(pts[0][0],H-pad);pts.forEach(p=>ctx.lineTo(p[0],p[1]));ctx.lineTo(pts[pts.length-1][0],H-pad);ctx.closePath();
  const g=ctx.createLinearGradient(0,0,0,H);g.addColorStop(0,'rgba(91,157,255,.28)');g.addColorStop(1,'rgba(91,157,255,.02)');
  ctx.fillStyle=g;ctx.fill();
  ctx.strokeStyle='#5b9dff';ctx.lineWidth=2.5;ctx.lineJoin='round';
  ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke();
  // 点
  ctx.fillStyle='#5b9dff';
  pts.forEach(p=>{ctx.beginPath();ctx.arc(p[0],p[1],3,0,7);ctx.fill();});
  if(n>1){ctx.fillStyle='#8b98b8';ctx.fillText(keys[0],pad,H-3);ctx.fillText(keys[n-1],W-70,H-3);}
  // 悬停
  cv.onmousemove=e=>{const r=cv.getBoundingClientRect();
    const x=e.clientX-r.left,y=e.clientY-r.top, W2=W/2,H2=H/2;
    let best=-1,bd=1e9;
    pts.forEach((p,i)=>{const dx=x-p[0]/2,dy=y-p[1]/2;const d=dx*dx+dy*dy;if(d<bd){bd=d;best=i;}});
    if(best>=0&&bd<900){ const tip=$('tip'); tip.style.display='block';
      tip.style.left=(pts[best][0]/2+8)+'px'; tip.style.top=(pts[best][1]/2-6)+'px';
      tip.innerHTML=`<b>${keys[best]}</b><br>${vals[best]} 任务`; }
    else $('tip').style.display='none'; };
  cv.onmouseleave=()=>$('tip').style.display='none';
}

/* ── 错误分布 ── */
function renderErr(d){
  const dd=d.data||{}, ed=dd.err_dist||{}, fr=dd.fail_reasons||{};
  const entries=Object.entries(ed); const mx=Math.max(1,...entries.map(e=>e[1]));
  $('errdist').innerHTML=entries.length?entries.map(([k,v])=>
    `<div class="errrow"><span class="lbl">${esc(k)}</span><div class="trk"><div class="fl" style="width:${v/mx*100}%"></div></div><span class="n">${v}</span></div>`).join('')
    :'<div class="empty">暂无错误</div>';
  const frs=Object.entries(fr).slice(0,10);
  $('failreasons').innerHTML=frs.length?frs.map(([k,v])=>
    `<div class="errrow"><span class="lbl" style="width:auto;font-family:inherit">${esc(k)}</span><span class="n" style="width:auto">×${v}</span></div>`).join('')
    :'<div class="empty">暂无失败</div>';
}

/* ── Token 池 ── */
function tokStatus(t){ if(t.ban_active)return['熔断','banned'];if(t.suspend_active)return['暂停','suspended'];
  if(t.cooling)return['冷却','cooling'];return['active','active']; }
function sortTok(k){ tokSort={k,asc:!(tokSort.k===k&&!tokSort.asc)};
  document.querySelectorAll('#toktbl th .arr').forEach(a=>a.textContent='');
  $('s-'+k).textContent=tokSort.asc?'▲':'▼';
  renderTokBody(); }
function renderTokens(d){ tokData=(d.data?.tokens||[]).slice(); renderTokBody(); }
function renderTokBody(){
  const q=$('tokSearch').value.toLowerCase();
  const list=tokData.filter(t=>t.token.toLowerCase().includes(q)||tokStatus(t)[0].includes(q));
  curTokList=list;
  const map={token:0,status:0}; const {k,asc}=tokSort;
  list.sort((a,b)=>{ if(k==='status'){const x=tokStatus(a)[0].localeCompare(tokStatus(b)[0]);return asc?x:-x;}
    const av=a[k],bv=b[k]; if(typeof av==='string'){const x=av.localeCompare(bv);return asc?x:-x;}
    return asc?((av||0)-(bv||0)):((bv||0)-(av||0)); });
  const tb=$('toktbl tbody');
  if(!list.length){tb.innerHTML='<tr><td colspan="10" class="empty">无匹配 token</td></tr>';return;}
  tb.innerHTML=list.map((t,i)=>{
    const[s,cl]=tokStatus(t),sr=t.success_rate*100;
    const srb=sr>=95?'':sr>=70?'warn':'bad';
    return `<tr onclick="openTok(${i})" title="点击查看详情"><td><span class="tag ${cl}">${s}</span></td><td>${esc(t.token)}</td>
      <td><span class="srbar"><i class="${srb}" style="width:${sr}%"></i></span>${sr.toFixed(0)}%</td>
      <td>${fmtNum(t.ok)}</td><td>${fmtNum(t.err)}</td><td>${fmtNum(t.rate_limited)}</td>
      <td>${t.latency_ms??'-'}</td>
      <td>${t.preflight===true?'<span class="tag active">ok</span>':t.preflight===false?'<span class="tag bad">bad</span>':'<span class="tag skip">skip</span>'}</td>
      <td>${fmtNum(t.files_left)}</td><td>${fmtNum(t.daily_submits)}</td></tr>`;}).join('');
}

/* ── Token 详情弹窗 ── */
function openTok(i){
  const t=curTokList[i]; if(!t){toast('数据未加载',true);return;}
  const[s,cl]=tokStatus(t);
  $('mTitle').textContent='Token 详情 · '+t.token;
  $('mBody').innerHTML=`<dl class="kv">
    <dt>状态</dt><dd><span class="tag ${cl}">${s}</span> ${t.quota_warn?'<span class="tag suspended">配额预警</span>':''}</dd>
    <dt>成功率</dt><dd>${(t.success_rate*100).toFixed(1)}%</dd>
    <dt>提交</dt><dd>成功 <b>${t.ok}</b> / 失败 <b>${t.err}</b> / 总请求 ${t.total_requests}</dd>
    <dt>错误细分</dt><dd>429×${t.rate_limited} · 配额暂停×${t.suspended} · 服务端×${t.server_error} · 解析 ${t.parse_ok}✓/${t.parse_fail}✗</dd>
    <dt>延迟</dt><dd>EMA <b>${t.latency_ms??'-'}ms</b> · p50 ${t.latency_p50??'-'} · p90 ${t.latency_p90??'-'} · p99 ${t.latency_p99??'-'}</dd>
    <dt>错误码</dt><dd class="mono">${esc(JSON.stringify(t.err_codes||{}))}</dd>
    <dt>最近错误</dt><dd class="dim">${esc(t.last_err||'-')}</dd>
    <dt>连续失败</dt><dd>${t.fail_streak}（阈值熔断=${t.ban_active?'已熔断':'否'}）</dd>
    <dt>最近使用</dt><dd>${relTime(t.last_used)} 前</dd>
    <dt>今日</dt><dd>提交 ${t.daily_submits} · 页 ${t.daily_pages} · 剩余文件 ${t.files_left} / 优先页 ${t.pages_priority_left}</dd>
    <dt>预热探测</dt><dd>${t.preflight===true?'通过':t.preflight===false?'失败':'未测'}</dd>
    <dt>窗口样本</dt><dd>${t.window_len}</dd></dl>`;
  $('modal').classList.add('show');
}

/* ── 任务 ── */
async function loadTasks(){
  try{ const d=await api('/tasks?limit=100'+(statusF?'&status='+statusF:''));
    tasks=d.data?.tasks||[]; renderTasks(); }catch(e){}
}
function renderTasks(){
  const by={}; tasks.forEach(t=>by[t.status]=(by[t.status]||0)+1);
  $('taskinfo').textContent=`共 ${tasks.length} 条（limit 100）`;
  const segs=['','pending','submitted','done','failed'];
  $('taskSegs').innerHTML=segs.map(s=>`<span class="seg ${s===statusF?'on':''}" onclick="setStatus('${s}')">${s||'全部'}${s?'':`<b> ${tasks.length}</b>`}${s&&by[s]?` <b>${by[s]}</b>`:''}</span>`).join('');
  renderTasksLocal();
}
function setStatus(s){statusF=s;loadTasks();}
function renderTasksLocal(){
  const q=$('taskSearch').value.toLowerCase();
  const list=tasks.filter(t=>!q||(t.source||'').toLowerCase().includes(q));
  const tb=$('tasktbl tbody');
  if(!list.length){tb.innerHTML='<tr><td colspan="7" class="empty">暂无任务</td></tr>';return;}
  tb.innerHTML=list.map(t=>`<tr style="cursor:pointer" onclick="openTask('${t.task_id}')">
    <td>${esc(t.task_id)}</td><td><span class="tag ${t.status}">${esc(t.status)}</span></td>
    <td>${esc(t.channel||'-')}</td><td class="src" title="${esc(t.source)}">${esc(t.source)}</td>
    <td title="${new Date(t.created_at*1000).toLocaleString()}">${relTime(t.created_at)}</td>
    <td>${t.finished_at?relTime(t.finished_at-t.created_at):'-'}</td>
    <td class="dim" style="max-width:180px;overflow:hidden;text-overflow:ellipsis">${esc(t.error||'')}</td></tr>`).join('');
}
async function openTask(tid){
  try{ const d=await api('/task/'+tid);
    if(d.code!==0){ toast('任务不存在或无权访问',true); return; }
    const t=d.data||{}; curTask=t;
    $('mTitle').textContent=tid;
    const ops=`<div class="ops">
      <button class="iconbtn" onclick="copyText(curTask.task_id,'已复制 task_id')">📋 复制 task_id</button>
      <button class="iconbtn" onclick="copyText(curTask.source||'','已复制来源 URL')">📋 复制来源</button>
      ${t.status==='failed'?`<button class="iconbtn" onclick="doRetry('${tid}')">↻ 重试任务</button>`:''}
      <button class="iconbtn danger" onclick="doDelete('${tid}')">🗑 删除任务</button>
    </div>`;
    $('mBody').innerHTML=ops+`<dl class="kv">
      <dt>状态</dt><dd><span class="tag ${t.status}">${esc(t.status)}</span> <span class="dim">通道 ${esc(t.channel||'-')}</span></dd>
      <dt>来源</dt><dd>${esc(t.source||'-')}</dd>
      <dt>batch_id</dt><dd class="mono">${esc(t.batch_id||'-')}</dd>
      <dt>创建时间</dt><dd>${t.created_at?new Date(t.created_at*1000).toLocaleString():'-'}</dd>
      <dt>完成时间</dt><dd>${t.finished_at?new Date(t.finished_at*1000).toLocaleString():'-'}</dd>
      <dt>进度</dt><dd>${esc(JSON.stringify(t.progress||null))}</dd>
      <dt>产物已下载</dt><dd>${t.downloaded?'是':'否'}</dd>
      <dt>错误</dt><dd class="dim">${esc(t.error||'-')}</dd></dl>`;
    $('modal').classList.add('show');
  }catch(e){toast('详情加载失败',true);}
}
async function copyText(txt,msg){
  try{ await navigator.clipboard.writeText(txt); toast(msg||'已复制'); }
  catch(e){ toast('复制失败（浏览器限制），请手动复制',true); }
}
async function doRetry(tid){
  if(!confirm('确认重试任务 '+tid+' ？')) return;
  const r=await fetch('/api/retry/'+tid,{method:'POST'}).then(x=>x.json());
  toast(r.code===0?'已重置为 pending，等待重新提交':'重试失败: '+(r.msg||''), r.code!==0);
  closeModal(); refreshAll();
}
async function doDelete(tid){
  if(!confirm('确认删除任务 '+tid+' ？（记录与产物将不可恢复）')) return;
  const r=await fetch('/api/task/'+tid,{method:'DELETE'}).then(x=>x.json());
  toast(r.code===0?'已删除 '+tid:'删除失败: '+(r.msg||''), r.code!==0);
  closeModal(); refreshAll();
}
function closeModal(){$('modal').classList.remove('show');}

/* ── 导出 CSV ── */
function exportCSV(data,name){
  if(!data||!data.length){toast('无数据可导出',true);return;}
  const cols=name==='tokens'?['token','status','success_rate','ok','err','rate_limited','suspended','parse_ok','parse_fail','latency_ms','preflight','files_left','daily_submits','last_err']
    :['task_id','status','channel','source','created_at','error'];
  const lines=[cols.join(',')];
  data.forEach(r=>{
    if(name==='tokens'){const[s]=tokStatus(r);
      lines.push([r.token,s,r.success_rate,r.ok,r.err,r.rate_limited,r.suspended,r.parse_ok,r.parse_fail,r.latency_ms,r.preflight,r.files_left,r.daily_submits,(r.last_err||'').slice(0,60)].join(','));}
    else lines.push([r.task_id,r.status,r.channel||'',(r.source||'').replace(/,/g,' '),r.created_at,(r.error||'').replace(/,/g,' ')].join(','));
  });
  const blob=new Blob([lines.join('\n')],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download=`mineru_${name}_${new Date().toISOString().slice(0,10)}.csv`;a.click();
  URL.revokeObjectURL(a.href);toast('已导出 '+name+'.csv');
}

setInterval(()=>{ if(auto&&--left<=0){left=+$('interval').value;refreshAll();} $('clock').textContent='⏱ '+left+'s'; },1000);
$('interval').onchange=()=>{ left=+$('interval').value; $('clock').textContent='⏱ '+left+'s'; toast('刷新间隔已设为 '+$('interval').value+'s'); };
refreshAll();
</script>
</body>
</html>
"""


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
            body = PAGE.encode()
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
            if path == "/api/tasks":
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                limit = min(max(int(q.get("limit", ["100"])[0]), 1), 200)
                st = q.get("status", [""])[0]
                p = f"/v1/tasks?limit={limit}"
                if st:
                    p += f"&status={st}"
                return self._json(client.get(p))
            if path == "/api/refresh":
                client.refresh()
                return self._json({"code": 0})
            self._json({"code": 404, "msg": "not found"}, 404)

        def do_POST(self):
            path = self.path.split("?")[0]
            if path.startswith("/api/retry/"):
                tid = path[len("/api/retry/"):]
                return self._json(client.call("POST", f"/v1/tasks/{tid}/retry"))
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
