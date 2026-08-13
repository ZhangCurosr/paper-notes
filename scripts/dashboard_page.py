#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dashboard 页面（共享模块）
==========================
被两类宿主加载：
  1. 云端 mineru_api_server.py  → GET /dashboard（同源直连 /v1/*，无 CORS 问题）
  2. 本地 local_dashboard.py    → 本地代理 /api/*（浏览器不直连云端）

前端自动检测：location.pathname 以 /dashboard 开头 → 云端模式（需要页面内输入 API key，存 localStorage）；
否则本地模式（key 由本地代理注入）。

页面：Tab 布局（总览 / Token 池 / 任务 / 历史）
  - 总览：告警横幅 + KPI + 配额进度 + 24h 趋势 + 错误分布
  - Token：搜索/排序/详情弹窗/导出 CSV
  - 任务：筛选/搜索/详情弹窗（重试/删除/复制）/导出 CSV
  - 历史：7/14/30 天趋势（数据来自云端 GitHub 归档，/v1/history）
"""
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
  --bg:#f2f5fb; --bg2:#e8edf7; --card:#fff; --card2:#f6f8fd; --line:#dbe3f2;
  --fg:#1b2740; --dim:#64748b; --dim2:#94a3b8; --shadow:0 8px 24px rgba(30,60,120,.08);
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:linear-gradient(160deg,var(--bg),var(--bg2) 60%,var(--bg));color:var(--fg);
  font:14px/1.55 "Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif;min-height:100vh}
.wrap{max-width:1280px;margin:0 auto;padding:18px 20px 40px}
/* 吸顶头部 */
.topbar{position:sticky;top:0;z-index:40;display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  padding:12px 0;background:linear-gradient(180deg,var(--bg) 75%,transparent);backdrop-filter:blur(6px)}
.logo{display:flex;align-items:center;gap:10px}
.logo .ic{width:34px;height:34px;border-radius:10px;display:flex;align-items:center;justify-content:center;
  font-size:18px;background:linear-gradient(135deg,#5b9dff,#a78bfa);box-shadow:var(--shadow)}
.topbar h1{font-size:17px;font-weight:700}
.topbar .sub{color:var(--dim);font-size:11.5px}
.spacer{flex:1}
.pill{display:inline-flex;align-items:center;gap:7px;background:var(--card);border:1px solid var(--line);
  border-radius:20px;padding:5px 13px;font-size:12.5px;color:var(--dim);white-space:nowrap}
.dot{width:9px;height:9px;border-radius:50%;background:var(--warn)}
.dot.ok{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.dot.bad{background:var(--bad);box-shadow:0 0 8px var(--bad)}
.iconbtn{background:var(--card);border:1px solid var(--line);color:var(--fg);border-radius:9px;
  padding:6px 12px;font-size:12.5px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;transition:.15s}
.iconbtn:hover{border-color:var(--acc);transform:translateY(-1px)}
.iconbtn.spin svg{animation:rot 1s linear infinite}
@keyframes rot{to{transform:rotate(360deg)}}
.iconbtn.danger:hover{border-color:var(--bad);color:var(--bad)}
select{background:var(--bg2);border:1px solid var(--line);color:var(--fg);border-radius:9px;padding:6px 7px;font-size:12px;outline:none;cursor:pointer}
.kinput{background:var(--bg2);border:1px solid var(--line);color:var(--fg);border-radius:9px;padding:6px 10px;
  font-size:12px;width:210px;outline:none;font-family:Consolas,monospace}
.kinput:focus{border-color:var(--acc)}
/* Tab 导航 */
.tabs{display:flex;gap:6px;margin:10px 0 14px;border-bottom:1px solid var(--line);padding-bottom:0}
.tab{background:none;border:none;color:var(--dim);font-size:13.5px;font-weight:600;padding:9px 18px;
  cursor:pointer;border-bottom:2.5px solid transparent;transition:.15s;letter-spacing:.5px}
.tab:hover{color:var(--fg)}
.tab.on{color:var(--acc);border-bottom-color:var(--acc)}
.tab b{font-size:11px;color:var(--dim);margin-left:3px}
section{display:none;animation:fade .2s ease}
section.on{display:block}
@keyframes fade{from{opacity:0;transform:translateY(4px)}}
/* 告警 */
#alerts{display:flex;flex-direction:column;gap:8px;margin-bottom:12px}
.alert{display:flex;align-items:center;gap:10px;padding:10px 15px;border-radius:12px;font-size:13px;border:1px solid;animation:slidein .25s ease}
.alert.bad{background:rgba(242,99,123,.12);border-color:rgba(242,99,123,.5);color:var(--bad)}
.alert.warn{background:rgba(245,185,77,.12);border-color:rgba(245,185,77,.5);color:var(--warn)}
@keyframes slidein{from{transform:translateY(-6px);opacity:0}}
/* KPI */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(142px,1fr));gap:11px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 14px;position:relative;overflow:hidden;transition:.2s}
.kpi:hover{transform:translateY(-2px);box-shadow:var(--shadow)}
.kpi::before{content:"";position:absolute;top:0;left:14px;right:14px;height:3px;border-radius:0 0 4px 4px;
  background:linear-gradient(90deg,var(--acc),var(--purple));opacity:0;transition:.2s}
.kpi:hover::before{opacity:1}
.kpi.good::before{background:linear-gradient(90deg,var(--ok),#34d399)}
.kpi.warn::before{background:linear-gradient(90deg,var(--warn),#f58a4d)}
.kpi.bad::before{background:linear-gradient(90deg,var(--bad),#d94a62)}
.kpi .k{color:var(--dim);font-size:11.5px;display:flex;align-items:center;gap:5px}
.kpi .v{font-size:22px;font-weight:700;margin-top:4px}
.kpi .v .num{font-variant-numeric:tabular-nums}
.kpi.good .v{color:var(--ok)} .kpi.warn .v{color:var(--warn)} .kpi.bad .v{color:var(--bad)} .kpi.acc .v{color:var(--acc)}
.kpi .ic{position:absolute;right:-8px;bottom:-10px;font-size:48px;opacity:.08}
/* 配额 */
.quota{display:grid;grid-template-columns:1fr 1fr;gap:11px;margin-top:11px}
.qbar{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 15px}
.qbar .row{display:flex;justify-content:space-between;font-size:12px;color:var(--dim);margin-bottom:6px}
.qbar .row b{color:var(--fg);font-variant-numeric:tabular-nums}
.track{height:8px;background:var(--bg2);border-radius:6px;overflow:hidden}
.fill{height:100%;border-radius:6px;background:linear-gradient(90deg,var(--acc),var(--purple));transition:width .6s}
.fill.warn{background:linear-gradient(90deg,var(--warn),#f58a4d)}
.fill.bad{background:linear-gradient(90deg,var(--bad),#d94a62)}
/* 区段 */
h2{font-size:13px;color:var(--dim);margin:20px 0 9px;text-transform:uppercase;letter-spacing:1px;display:flex;align-items:center;gap:8px}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px;margin-top:3px}
.grid2{display:grid;grid-template-columns:1.6fr 1fr;gap:13px}
@media(max-width:1020px){.grid2{grid-template-columns:1fr}.quota{grid-template-columns:1fr}}
/* 图表 */
canvas{width:100%;display:block}
.chartwrap{position:relative}
#tip{position:absolute;pointer-events:none;background:var(--card2);border:1px solid var(--line);
  border-radius:8px;padding:5px 9px;font-size:12px;display:none;z-index:10;box-shadow:var(--shadow)}
.legend{display:flex;gap:14px;font-size:12px;color:var(--dim);margin-top:7px;flex-wrap:wrap}
.legend i{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:5px;vertical-align:-1px}
/* 历史 */
.hbtn{background:var(--card2);border:1px solid var(--line);color:var(--dim);border-radius:9px;padding:5px 13px;
  font-size:12px;cursor:pointer;transition:.15s}
.hbtn.on{color:var(--acc);border-color:var(--acc);background:rgba(91,157,255,.12)}
/* 错误条 */
.errrow{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:12px}
.errrow .lbl{width:66px;color:var(--dim);text-align:right;font-family:monospace}
.errrow .trk{flex:1;height:13px;background:var(--bg2);border-radius:7px;overflow:hidden}
.errrow .fl{height:100%;border-radius:7px;background:linear-gradient(90deg,#f2637b,#f5b94d);min-width:3px}
.errrow .n{width:40px;font-variant-numeric:tabular-nums;color:var(--dim)}
/* 表格 */
.tblwrap{overflow:auto;max-height:440px;border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{position:sticky;top:0;background:var(--card2);color:var(--dim);font-weight:600;padding:8px 9px;
  border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;cursor:pointer;user-select:none;z-index:2}
th:hover{color:var(--fg)}
th .arr{font-size:10px;color:var(--acc)}
td{padding:7px 9px;border-bottom:1px solid var(--line);white-space:nowrap;font-variant-numeric:tabular-nums}
tr:hover td{background:var(--card2)}
td.src{max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#toktbl tbody tr{cursor:pointer}
.tag{display:inline-block;padding:1px 8px;border-radius:12px;font-size:11px;font-weight:600;letter-spacing:.3px}
.tag.done,.tag.active{background:rgba(45,212,167,.14);color:var(--ok)}
.tag.failed,.tag.banned,.tag.bad{background:rgba(242,99,123,.14);color:var(--bad)}
.tag.pending,.tag.suspended,.tag.cooling{background:rgba(245,185,77,.14);color:var(--warn)}
.tag.submitted{background:rgba(91,157,255,.14);color:var(--acc)}
.tag.skip{background:rgba(140,150,180,.14);color:var(--dim)}
.srbar{display:inline-block;width:48px;height:6px;background:var(--bg2);border-radius:4px;overflow:hidden;vertical-align:middle;margin-right:6px}
.srbar i{display:block;height:100%;border-radius:4px;background:var(--ok)}
.srbar i.warn{background:var(--warn)} .srbar i.bad{background:var(--bad)}
.tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:9px}
.search{background:var(--bg2);border:1px solid var(--line);border-radius:9px;color:var(--fg);
  padding:6px 11px;font-size:12.5px;width:200px;outline:none;transition:.15s}
.search:focus{border-color:var(--acc)}
.statbar{display:flex;gap:5px;flex-wrap:wrap}
.statbar .seg{background:var(--card2);border:1px solid var(--line);border-radius:9px;padding:5px 12px;
  font-size:12px;color:var(--dim);cursor:pointer;transition:.15s}
.statbar .seg.on{color:var(--fg);border-color:var(--acc);background:rgba(91,157,255,.12)}
.statbar .seg b{color:var(--fg)}
/* 弹窗 */
.modalbg{position:fixed;inset:0;background:rgba(6,10,20,.6);backdrop-filter:blur(3px);display:none;
  align-items:center;justify-content:center;z-index:50;padding:20px}
.modalbg.show{display:flex}
.modal{background:var(--card);border:1px solid var(--line);border-radius:16px;max-width:640px;width:100%;
  max-height:82vh;overflow:auto;box-shadow:var(--shadow);animation:pop .18s ease}
@keyframes pop{from{transform:scale(.96);opacity:0}}
.modal .mhead{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--card)}
.modal .mhead h3{font-size:14px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal .mbody{padding:14px 18px}
.kv{display:grid;grid-template-columns:108px 1fr;gap:6px 12px;font-size:12.5px}
.kv dt{color:var(--dim)} .kv dd{word-break:break-all}
.mono{font-family:Consolas,monospace;font-size:11.5px;background:var(--bg2);border-radius:5px;padding:1px 6px}
.mbody .ops{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:13px}
/* toast / 空态 / 登录 */
#toast{position:fixed;top:18px;right:18px;background:var(--card2);border:1px solid var(--line);
  color:var(--fg);padding:10px 17px;border-radius:10px;display:none;z-index:99;box-shadow:var(--shadow);font-size:12.5px}
#toast.err{border-color:var(--bad);color:var(--bad)}
.empty{color:var(--dim);padding:20px;text-align:center;font-size:12.5px}
.dim{color:var(--dim);font-size:12px}
.loginbox{display:inline-flex;gap:6px;align-items:center}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="logo"><div class="ic">⚡</div>
      <div><h1>MinerU API Dashboard</h1><div class="sub" id="modeTag"></div></div></div>
    <span class="pill" id="svc"><span class="dot"></span>连接中…</span>
    <span class="pill" id="clock">—</span>
    <span class="spacer"></span>
    <span class="loginbox" id="loginBox" style="display:none">
      <input class="kinput" id="keyInput" type="password" placeholder="输入 API key">
      <button class="iconbtn" id="loginBtn">登录</button>
    </span>
    <select id="interval" title="刷新间隔">
      <option value="5">5s</option><option value="15" selected>15s</option>
      <option value="30">30s</option><option value="60">60s</option>
    </select>
    <button class="iconbtn" id="themeBtn" title="明暗主题">🌓</button>
    <button class="iconbtn" id="autoBtn" title="自动刷新">⏸</button>
    <button class="iconbtn" id="refBtn">⟳ 刷新</button>
  </div>

  <nav class="tabs" id="tabs">
    <button class="tab on" data-tab="ov">总览</button>
    <button class="tab" data-tab="tok">Token 池<b id="tokN"></b></button>
    <button class="tab" data-tab="task">任务<b id="taskN"></b></button>
    <button class="tab" data-tab="hist">历史</button>
  </nav>

  <!-- 总览 -->
  <section id="tab-ov" class="on">
    <div id="alerts"></div>
    <div class="kpis" id="kpis"></div>
    <div class="quota">
      <div class="qbar"><div class="row"><span>今日文件配额</span><span id="q1t">—</span></div>
        <div class="track"><div class="fill" id="q1f" style="width:0%"></div></div></div>
      <div class="qbar"><div class="row"><span>今日优先页配额</span><span id="q2t">—</span></div>
        <div class="track"><div class="fill" id="q2f" style="width:0%"></div></div></div>
    </div>
    <h2>24h 任务趋势</h2>
    <div class="grid2">
      <div class="panel chartwrap"><canvas id="chart"></canvas><div id="tip"></div>
        <div class="legend"><span><i style="background:var(--acc)"></i>每小时任务数</span><span id="daytotal"></span></div></div>
      <div class="panel">
        <div class="dim" style="margin-bottom:4px">错误码分布</div><div id="errdist"><div class="empty">加载中…</div></div>
        <div class="dim" style="margin:13px 0 4px">失败原因 Top10</div><div id="failreasons"><div class="empty">加载中…</div></div>
      </div>
    </div>
  </section>

  <!-- Token 池 -->
  <section id="tab-tok">
    <div class="panel">
      <div class="tools">
        <input class="search" id="tokSearch" placeholder="🔍 搜索 token / 状态…" oninput="renderTokBody()">
        <span class="dim" id="poolinfo"></span><span class="spacer"></span>
        <button class="iconbtn" onclick="exportCSV(tokData,'tokens')">⭳ 导出 CSV</button>
      </div>
      <div class="tblwrap"><table id="toktbl"><thead><tr>
        <th onclick="sortTok('status')">状态<span class="arr" id="s-status"></span></th>
        <th onclick="sortTok('token')">token<span class="arr" id="s-token"></span></th>
        <th onclick="sortTok('success_rate')">成功率<span class="arr" id="s-success_rate"></span></th>
        <th onclick="sortTok('ok')">成功<span class="arr" id="s-ok"></span></th>
        <th onclick="sortTok('err')">失败<span class="arr" id="s-err"></span></th>
        <th onclick="sortTok('rate_limited')">429<span class="arr" id="s-rate_limited"></span></th>
        <th onclick="sortTok('latency_ms')">延迟ms<span class="arr" id="s-latency_ms"></span></th>
        <th>preflight</th><th onclick="sortTok('files_left')">今日剩余<span class="arr" id="s-files_left"></span></th>
        <th onclick="sortTok('daily_submits')">今日提交<span class="arr" id="s-daily_submits"></span></th>
      </tr></thead><tbody></tbody></table></div>
    </div>
  </section>

  <!-- 任务 -->
  <section id="tab-task">
    <div class="panel">
      <div class="tools">
        <input class="search" id="taskSearch" placeholder="🔍 搜索来源 URL…" oninput="renderTasksLocal()">
        <span class="statbar" id="taskSegs"></span><span class="spacer"></span>
        <span class="dim">点击行查看详情</span>
        <button class="iconbtn" onclick="exportCSV(tasks,'tasks')">⭳ 导出 CSV</button>
      </div>
      <div class="tblwrap"><table id="tasktbl"><thead><tr>
        <th>task_id</th><th>状态</th><th>通道</th><th>来源</th><th>创建</th><th>耗时</th><th>错误</th>
      </tr></thead><tbody></tbody></table></div>
    </div>
  </section>

  <!-- 历史 -->
  <section id="tab-hist">
    <div class="panel">
      <div class="tools">
        <span class="hbtn on" onclick="setHist(7,this)">7 天</span>
        <span class="hbtn" onclick="setHist(14,this)">14 天</span>
        <span class="hbtn" onclick="setHist(30,this)">30 天</span>
        <span class="spacer"></span>
        <span class="dim" id="histInfo">历史数据归档于云端 GitHub 仓库（每小时）</span>
      </div>
      <h2 style="margin-top:6px">每日任务量（折线）</h2>
      <div class="chartwrap"><canvas id="histChart1"></canvas><div id="tip2"></div></div>
      <h2>每日配额消耗（柱状 · 文件/天）</h2>
      <div class="chartwrap"><canvas id="histChart2"></canvas></div>
      <h2>每日提交与错误</h2>
      <div id="histTable"></div>
    </div>
  </section>
</div>

<div class="modalbg" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="mhead"><h3 id="mTitle"></h3><button class="iconbtn" onclick="closeModal()">✕</button></div>
    <div class="mbody" id="mBody"></div>
  </div>
</div>
<div id="toast"></div>

<script>
/* ── 模式：云端同源 / 本地代理 ── */
const IS_CLOUD = location.pathname.startsWith('/dashboard');
let savedKey = localStorage.getItem('dash_key') || '';
document.getElementById('modeTag').textContent = IS_CLOUD ? '云端直连模式' : '本地代理模式';
if(IS_CLOUD){ const lb=document.getElementById('loginBox'); lb.style.display='inline-flex';
  if(savedKey) lb.style.display='none'; }
function ep(name,arg){
  if(IS_CLOUD){
    if(name==='overview'||name==='errbox') return '/v1/stats';
    if(name==='tokens') return '/v1/stats/tokens';
    if(name==='tasks') return '/v1/tasks?limit=100'+(arg?'&status='+arg:'');
    if(name==='task') return '/v1/tasks/'+arg;
    if(name==='retry') return '/v1/tasks/'+arg+'/retry';
    if(name==='delete') return '/v1/tasks/'+arg;
    if(name==='history') return '/v1/history?days='+arg;
  }else{
    if(name==='overview') return '/api/overview';
    if(name==='errbox') return '/api/errbox';
    if(name==='tokens') return '/api/tokens';
    if(name==='tasks') return '/api/tasks?limit=100'+(arg?'&status='+arg:'');
    if(name==='task') return '/api/task/'+arg;
    if(name==='retry') return '/api/retry/'+arg;
    if(name==='delete') return '/api/task/'+arg;
    if(name==='history') return '/api/history?days='+arg;
  }
}
function authH(){ return savedKey?{'Authorization':'Bearer '+savedKey}:{}; }
async function apiGet(name,arg){
  const r = await fetch(ep(name,arg),{headers:authH()});
  if(r.status===401){ showLogin(); throw new Error('unauthorized'); }
  return r.json();
}
async function apiAct(name,arg,method){
  const r = await fetch(ep(name,arg),{method,headers:authH()});
  return r.json();
}
function showLogin(){ if(IS_CLOUD) document.getElementById('loginBox').style.display='inline-flex'; }
document.getElementById('loginBtn').onclick=()=>{
  savedKey = document.getElementById('keyInput').value.trim();
  localStorage.setItem('dash_key', savedKey);
  document.getElementById('loginBox').style.display='none';
  toast('已保存，加载数据…'); refreshAll();
};

/* ── 基础工具 ── */
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtNum=n=>n==null?'-':Number(n).toLocaleString();
function relTime(ts){ if(!ts) return '-'; const d=Date.now()/1000-ts;
  if(d<60) return Math.floor(d)+'s'; if(d<3600) return Math.floor(d/60)+'m';
  if(d<86400) return Math.floor(d/3600)+'h'; return Math.floor(d/86400)+'d'; }
function toast(m,err){ const t=$('toast'); t.textContent=m; t.className=err?'err':''; t.style.display='block';
  setTimeout(()=>t.style.display='none',3500); }

/* ── Tab 切换（懒加载） ── */
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  document.querySelectorAll('section').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); $('tab-'+b.dataset.tab).classList.add('on');
  if(b.dataset.tab==='task') loadTasks();
  if(b.dataset.tab==='hist') loadHistory(histDays);
});

/* ── 主题 / 刷新 / 间隔 ── */
let auto=true, left=15, histDays=7;
function applyTheme(t){ document.documentElement.dataset.theme=t; $('themeBtn').textContent=t==='dark'?'🌓':'☀️'; }
applyTheme(localStorage.getItem('dash_theme')||'dark');
$('themeBtn').onclick=()=>{ const t=document.documentElement.dataset.theme==='dark'?'light':'dark';
  localStorage.setItem('dash_theme',t); applyTheme(t); };
$('autoBtn').onclick=()=>{ auto=!auto; $('autoBtn').textContent=auto?'⏸':'▶'; };
$('interval').onchange=()=>{ left=+$('interval').value; $('clock').textContent='⏱ '+left+'s'; };
$('refBtn').onclick=()=>refreshAll();

/* ── 数据加载 ── */
async function refreshAll(){
  const b=$('refBtn'); b.classList.add('spin'); b.innerHTML='⟳ 刷新中…';
  try{
    const [ov,tk]=await Promise.all([apiGet('overview'),apiGet('tokens')]);
    renderOverview(ov); renderTokens(tk); renderChart(ov); renderErr(ov);
    if($('tab-task').classList.contains('on')) loadTasks();
    if($('tab-hist').classList.contains('on')) loadHistory(histDays);
  }catch(e){ if(!IS_CLOUD) toast('加载失败: '+e,true); }
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
  const s=$('svc');
  if(d.uptime==null){ s.innerHTML='<span class="dot bad"></span>云端不可达'; document.title='⚠ MinerU Dashboard 离线'; }
  else{ s.innerHTML=`<span class="dot ok"></span>在线 ${Math.floor(d.uptime/3600)}h ${Math.floor(d.uptime%3600/60)}m · ${tk.strategy} · ${tk.tokens} token`;
    document.title='MinerU Dashboard · 在线'; }
  $('tokN').textContent=tk.tokens?(' '+tk.tokens):'';
  const ok=tk.ok||0, err=tk.err||0, sr=tk.avg_success_rate;
  const cards=[
    ['提交成功',ok,0,'good','✅'],['提交失败',err,0,err>0?'bad':'','❌'],
    ['成功率',sr,sr!=null?1:null,'good','🎯'],
    ['解析页数',tk.pages_parsed||0,0,'','📄'],
    ['延迟 p99',tk.latency_ms?.p99,tk.latency_ms?.p99!=null?0:null,'','⚡'],
    ['熔断中',tk.banned_now,0,tk.banned_now>0?'bad':'','🧯'],
    ['配额暂停',tk.suspended_now,0,tk.suspended_now>0?'warn':'','⏸'],
    ['429 冷却',tk.cooling,0,'','🌡'],
    ['今日提交',q.submits,0,'','📥'],
    ['剩余文件',q.files_left,0,q.files_left<500?'warn':'','🗂'],
    ['flash 任务',d.flash?.tasks,0,'','🪄'],
    ['API 请求',d.stats?.api_requests,0,'','🔁']];
  $('kpis').innerHTML=cards.map((c,i)=>`<div class="kpi ${c[3]}" id="kpi${i}"><div class="k">${c[4]} ${c[0]}</div><div class="v"><span class="num" id="knum${i}">0</span></div><div class="ic">${c[4]}</div></div>`).join('');
  cards.forEach((c,i)=>animNum($('knum'+i),c[1],c[2],c[0]==='成功率'?'%':c[0].includes('延迟')?'ms':''));
  const fl=q.files_limit||5000, pl=q.pages_priority_limit||1000;
  const p1=Math.min(100,100*q.submits/fl), p2=Math.min(100,100*q.pages/pl);
  $('q1t').innerHTML=`<b>${fmtNum(q.submits)}</b> / ${fmtNum(fl)} · 剩余 <b>${fmtNum(q.files_left)}</b>`;
  $('q2t').innerHTML=`<b>${fmtNum(q.pages)}</b> / ${fmtNum(pl)} · 剩余 <b>${fmtNum(q.pages_priority_left)}</b>`;
  const f1=$('q1f'),f2=$('q2f'); f1.style.width=p1+'%'; f2.style.width=p2+'%';
  f1.className='fill'+(p1>85?' bad':p1>70?' warn':''); f2.className='fill'+(p2>85?' bad':p2>70?' warn':'');
  $('poolinfo').textContent=`preflight ${JSON.stringify(tk.preflight||{})} · err_dist ${JSON.stringify(tk.err_dist||{})}`;
  $('daytotal').textContent='今日任务 '+fmtNum(d.stats?.tasks_total);
  checkAlerts(d, tk, q);
}
function checkAlerts(d, tk, q){
  const pre=tk.preflight||{}, alerts=[];
  if(d.uptime==null) alerts.push(['bad','⚠ 云端服务不可达，请检查 Render 状态或网络']);
  if(tk.banned_now>0) alerts.push(['bad','🧯 '+tk.banned_now+' 个 token 熔断中（连续失败，自动恢复中）']);
  if(pre.bad>0) alerts.push(['bad','🚫 '+pre.bad+' 个 token 预热探测失败（无效 token，建议从台账替换）']);
  if(q.files_left!=null&&q.files_left<200) alerts.push(['warn','🗂 今日文件配额剩余不足 200（'+q.files_left+'），注意耗尽']);
  if(tk.suspended_now>tk.tokens*0.05) alerts.push(['warn','⏸ '+tk.suspended_now+' 个 token 配额暂停中（超过 5%，12h 自动恢复）']);
  $('alerts').innerHTML=alerts.map(a=>`<div class="alert ${a[0]}">${a[1]}</div>`).join('');
}

/* ── 24h 趋势 ── */
function renderChart(ov){
  const tr=ov.data?.trends_24h||{};
  const keys=Object.keys(tr), vals=keys.map(k=>tr[k]);
  const cv=$('chart'),ctx=cv.getContext('2d');
  const W=cv.width=cv.offsetWidth*2,H=cv.height=150*2; ctx.clearRect(0,0,W,H);
  const max=Math.max(1,...vals),n=keys.length||1,pad=12;
  ctx.strokeStyle='rgba(90,110,150,.14)'; ctx.lineWidth=1; ctx.fillStyle='#8b98b8'; ctx.font='10px sans-serif';
  for(let g=0;g<=3;g++){ const y=pad+g*(H-2*pad)/3;
    ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(W-pad,y);ctx.stroke();
    ctx.fillText(Math.round(max*(3-g)/3),2,y+3); }
  const pts=vals.map((v,i)=>{const x=pad+i*(W-2*pad)/Math.max(1,n-1);const y=H-pad-(v/max)*(H-2*pad);return[x,y];});
  ctx.beginPath();ctx.moveTo(pts[0][0],H-pad);pts.forEach(p=>ctx.lineTo(p[0],p[1]));ctx.lineTo(pts[pts.length-1][0],H-pad);ctx.closePath();
  const g=ctx.createLinearGradient(0,0,0,H);g.addColorStop(0,'rgba(91,157,255,.28)');g.addColorStop(1,'rgba(91,157,255,.02)');
  ctx.fillStyle=g;ctx.fill();
  ctx.strokeStyle='#5b9dff';ctx.lineWidth=2.5;ctx.lineJoin='round';
  ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke();
  ctx.fillStyle='#5b9dff'; pts.forEach(p=>{ctx.beginPath();ctx.arc(p[0],p[1],3,0,7);ctx.fill();});
  if(n>1){ctx.fillStyle='#8b98b8';ctx.fillText(keys[0],pad,H-3);ctx.fillText(keys[n-1],W-70,H-3);}
  cv.onmousemove=e=>{const r=cv.getBoundingClientRect(); const x=e.clientX-r.left,y=e.clientY-r.top;
    let best=-1,bd=1e9; pts.forEach((p,i)=>{const dx=x-p[0]/2,dy=y-p[1]/2,d=dx*dx+dy*dy;if(d<bd){bd=d;best=i;}});
    if(best>=0&&bd<900){ const tip=$('tip'); tip.style.display='block';
      tip.style.left=(pts[best][0]/2+8)+'px'; tip.style.top=(pts[best][1]/2-6)+'px';
      tip.innerHTML=`<b>${keys[best]}</b><br>${vals[best]} 任务`; }
    else $('tip').style.display='none'; };
  cv.onmouseleave=()=>$('tip').style.display='none';
}

/* ── 错误分布（从 overview 提取） ── */
function renderErr(ov){
  const d=ov.data||{}, tk=d.tokens||{};
  const ed=tk.err_dist||{}, fr=(d.stats||{}).fail_reasons||{};
  const entries=Object.entries(ed), mx=Math.max(1,...entries.map(e=>e[1]));
  $('errdist').innerHTML=entries.length?entries.map(([k,v])=>
    `<div class="errrow"><span class="lbl">${esc(k)}</span><div class="trk"><div class="fl" style="width:${v/mx*100}%"></div></div><span class="n">${v}</span></div>`).join('')
    :'<div class="empty">暂无错误</div>';
  const frs=Object.entries(fr).slice(0,10);
  $('failreasons').innerHTML=frs.length?frs.map(([k,v])=>
    `<div class="errrow"><span class="lbl" style="width:auto;font-family:inherit">${esc(k)}</span><span class="n" style="width:auto">×${v}</span></div>`).join('')
    :'<div class="empty">暂无失败</div>';
}

/* ── Token 池 ── */
let tokData=[], curTokList=[], tokSort={k:'success_rate',asc:false};
function tokStatus(t){ if(t.ban_active)return['熔断','banned'];if(t.suspend_active)return['暂停','suspended'];
  if(t.cooling)return['冷却','cooling'];return['active','active']; }
function sortTok(k){ tokSort={k,asc:!(tokSort.k===k&&!tokSort.asc)};
  document.querySelectorAll('#toktbl th .arr').forEach(a=>a.textContent='');
  $('s-'+k).textContent=tokSort.asc?'▲':'▼'; renderTokBody(); }
function renderTokens(d){ tokData=(d.data?.tokens||[]).slice(); renderTokBody(); }
function renderTokBody(){
  const q=$('tokSearch').value.toLowerCase();
  const list=tokData.filter(t=>t.token.toLowerCase().includes(q)||tokStatus(t)[0].includes(q));
  curTokList=list; const {k,asc}=tokSort;
  list.sort((a,b)=>{ if(k==='status'){const x=tokStatus(a)[0].localeCompare(tokStatus(b)[0]);return asc?x:-x;}
    const av=a[k],bv=b[k]; if(typeof av==='string'){const x=av.localeCompare(bv);return asc?x:-x;}
    return asc?((av||0)-(bv||0)):((bv||0)-(av||0)); });
  const tb=$('toktbl tbody');
  if(!list.length){tb.innerHTML='<tr><td colspan="10" class="empty">无匹配 token</td></tr>';return;}
  tb.innerHTML=list.map((t,i)=>{ const[s,cl]=tokStatus(t),sr=t.success_rate*100;
    const srb=sr>=95?'':sr>=70?'warn':'bad';
    return `<tr onclick="openTok(${i})" title="点击查看详情"><td><span class="tag ${cl}">${s}</span></td><td>${esc(t.token)}</td>
      <td><span class="srbar"><i class="${srb}" style="width:${sr}%"></i></span>${sr.toFixed(0)}%</td>
      <td>${fmtNum(t.ok)}</td><td>${fmtNum(t.err)}</td><td>${fmtNum(t.rate_limited)}</td>
      <td>${t.latency_ms??'-'}</td>
      <td>${t.preflight===true?'<span class="tag active">ok</span>':t.preflight===false?'<span class="tag bad">bad</span>':'<span class="tag skip">skip</span>'}</td>
      <td>${fmtNum(t.files_left)}</td><td>${fmtNum(t.daily_submits)}</td></tr>`;}).join('');
}
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
    <dt>连续失败</dt><dd>${t.fail_streak}（熔断=${t.ban_active?'是':'否'}）</dd>
    <dt>最近使用</dt><dd>${relTime(t.last_used)} 前</dd>
    <dt>今日</dt><dd>提交 ${t.daily_submits} · 页 ${t.daily_pages} · 剩余文件 ${t.files_left} / 优先页 ${t.pages_priority_left}</dd>
    <dt>预热探测</dt><dd>${t.preflight===true?'通过':t.preflight===false?'失败':'未测'}</dd>
    <dt>窗口样本</dt><dd>${t.window_len}</dd></dl>`;
  $('modal').classList.add('show');
}

/* ── 任务 ── */
let tasks=[], curTask=null, statusF='';
async function loadTasks(){
  try{ const d=await apiGet('tasks',statusF); tasks=d.data?.tasks||[]; renderTasks(); }
  catch(e){ if(!IS_CLOUD) toast('任务加载失败',true); }
}
function renderTasks(){
  const by={}; tasks.forEach(t=>by[t.status]=(by[t.status]||0)+1);
  $('taskN').textContent=tasks.length?(' '+tasks.length):'';
  $('taskSegs').innerHTML=['','pending','submitted','done','failed'].map(s=>
    `<span class="seg ${s===statusF?'on':''}" onclick="setStatus('${s}')">${s||'全部'}${s&&by[s]?` <b>${by[s]}</b>`:s===''?` <b>${tasks.length}</b>`:''}</span>`).join('');
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
    <td class="dim" style="max-width:170px;overflow:hidden;text-overflow:ellipsis">${esc(t.error||'')}</td></tr>`).join('');
}
async function openTask(tid){
  try{ const d=await apiGet('task',tid);
    if(d.code!==0){ toast('任务不存在或无权访问',true); return; }
    const t=d.data||{}; curTask=t;
    $('mTitle').textContent=tid;
    $('mBody').innerHTML=`<div class="ops">
      <button class="iconbtn" onclick="copyText(curTask.task_id,'已复制 task_id')">📋 复制 task_id</button>
      <button class="iconbtn" onclick="copyText(curTask.source||'','已复制来源 URL')">📋 复制来源</button>
      ${t.status==='failed'?`<button class="iconbtn" onclick="doRetry('${tid}')">↻ 重试任务</button>`:''}
      <button class="iconbtn danger" onclick="doDelete('${tid}')">🗑 删除任务</button></div>
      <dl class="kv">
      <dt>状态</dt><dd><span class="tag ${t.status}">${esc(t.status)}</span> <span class="dim">通道 ${esc(t.channel||'-')}</span></dd>
      <dt>来源</dt><dd>${esc(t.source||'-')}</dd>
      <dt>batch_id</dt><dd class="mono">${esc(t.batch_id||'-')}</dd>
      <dt>创建时间</dt><dd>${t.created_at?new Date(t.created_at*1000).toLocaleString():'-'}</dd>
      <dt>完成时间</dt><dd>${t.finished_at?new Date(t.finished_at*1000).toLocaleString():'-'}</dd>
      <dt>进度</dt><dd>${esc(JSON.stringify(t.progress||null))}</dd>
      <dt>产物已下载</dt><dd>${t.downloaded?'是':'否'}</dd>
      <dt>错误</dt><dd class="dim">${esc(t.error||'-')}</dd></dl>`;
    $('modal').classList.add('show');
  }catch(e){ toast('详情加载失败',true); }
}
async function copyText(txt,msg){
  try{ await navigator.clipboard.writeText(txt); toast(msg||'已复制'); }
  catch(e){ toast('复制失败（浏览器限制），请手动复制',true); }
}
async function doRetry(tid){
  if(!confirm('确认重试任务 '+tid+' ？')) return;
  const r=await apiAct('retry',tid,'POST');
  toast(r.code===0?'已重置为 pending，等待重新提交':'重试失败: '+(r.msg||''), r.code!==0);
  closeModal(); refreshAll();
}
async function doDelete(tid){
  if(!confirm('确认删除任务 '+tid+' ？（记录与产物将不可恢复）')) return;
  const r=await apiAct('delete',tid,'DELETE');
  toast(r.code===0?'已删除 '+tid:'删除失败: '+(r.msg||''), r.code!==0);
  closeModal(); refreshAll();
}
function closeModal(){ $('modal').classList.remove('show'); }

/* ── 历史（云端 GitHub 归档） ── */
let histData=[];
async function loadHistory(days){
  histDays=days;
  try{ const d=await apiGet('history',days);
    if(d.code!==0){ $('histInfo').textContent='历史数据暂不可用（'+d.msg+')'; return; }
    histData=d.data?.days||[];
    $('histInfo').textContent='历史数据归档于云端 GitHub 仓库（每小时）· '+histData.length+' 天';
    drawHist1(); drawHist2(); renderHistTable();
  }catch(e){ if(!IS_CLOUD) toast('历史加载失败',true); }
}
function setHist(days,el){ document.querySelectorAll('.hbtn').forEach(x=>x.classList.remove('on'));
  el.classList.add('on'); loadHistory(days); }
function drawHist1(){
  const cv=$('histChart1'),ctx=cv.getContext('2d');
  const W=cv.width=cv.offsetWidth*2,H=cv.height=140*2; ctx.clearRect(0,0,W,H);
  const ds=histData, vals=ds.map(d=>d.submits||0);
  if(!ds.length){ctx.fillStyle='#8b98b8';ctx.font='12px sans-serif';ctx.fillText('暂无历史数据',W/2-40,H/2);return;}
  const max=Math.max(1,...vals),pad=12;
  ctx.strokeStyle='rgba(90,110,150,.14)';ctx.lineWidth=1;ctx.fillStyle='#8b98b8';ctx.font='10px sans-serif';
  for(let g=0;g<=3;g++){const y=pad+g*(H-2*pad)/3;ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(W-pad,y);ctx.stroke();
    ctx.fillText(Math.round(max*(3-g)/3),2,y+3);}
  const pts=vals.map((v,i)=>{const x=pad+i*(W-2*pad)/Math.max(1,ds.length-1);const y=H-pad-(v/max)*(H-2*pad);return[x,y];});
  ctx.beginPath();ctx.moveTo(pts[0][0],H-pad);pts.forEach(p=>ctx.lineTo(p[0],p[1]));ctx.lineTo(pts[pts.length-1][0],H-pad);ctx.closePath();
  const g=ctx.createLinearGradient(0,0,0,H);g.addColorStop(0,'rgba(167,139,250,.3)');g.addColorStop(1,'rgba(167,139,250,.03)');
  ctx.fillStyle=g;ctx.fill();
  ctx.strokeStyle='#a78bfa';ctx.lineWidth=2.5;ctx.lineJoin='round';
  ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke();
  ctx.fillStyle='#a78bfa';pts.forEach(p=>{ctx.beginPath();ctx.arc(p[0],p[1],3,0,7);ctx.fill();});
  if(ds.length>1){ctx.fillStyle='#8b98b8';ctx.fillText(ds[0].date,pad,H-3);ctx.fillText(ds[ds.length-1].date,W-60,H-3);}
  cv.onmousemove=e=>{const r=cv.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
    let best=-1,bd=1e9;pts.forEach((p,i)=>{const dx=x-p[0]/2,dy=y-p[1]/2,d=dx*dx+dy*dy;if(d<bd){bd=d;best=i;}});
    if(best>=0&&bd<900){const tip=$('tip2');tip.style.display='block';
      tip.style.left=(pts[best][0]/2+8)+'px';tip.style.top=(pts[best][1]/2-6)+'px';
      tip.innerHTML=`<b>${ds[best].date}</b><br>${vals[best]} 提交`;}else $('tip2').style.display='none';};
  cv.onmouseleave=()=>$('tip2').style.display='none';
}
function drawHist2(){
  const cv=$('histChart2'),ctx=cv.getContext('2d');
  const W=cv.width=cv.offsetWidth*2,H=cv.height=110*2; ctx.clearRect(0,0,W,H);
  const ds=histData; if(!ds.length)return;
  const cap=5000, vals=ds.map(d=>Math.min(1,(d.submits||0)/cap)),pad=12;
  const bw=(W-2*pad)/Math.max(1,ds.length)*0.62;
  ds.forEach((d,i)=>{const x=pad+i*(W-2*pad)/Math.max(1,ds.length-1)+ (W-2*pad)/Math.max(1,ds.length)/2-bw/2;
    const h=(vals[i])*(H-2*pad);
    const g=ctx.createLinearGradient(0,H-h,0,H);g.addColorStop(0,'#5b9dff');g.addColorStop(1,'rgba(91,157,255,.25)');
    ctx.fillStyle=g;ctx.fillRect(x,H-pad-h,bw,h);
    ctx.fillStyle='#8b98b8';ctx.font='9px sans-serif';
    if(ds.length<=14) ctx.fillText(d.submits||'',x+2,H-pad-h-2);});
  ctx.fillStyle='#8b98b8';ctx.font='10px sans-serif';
  ctx.fillText('上限 5000/天',W-90,H-3);
}
function renderHistTable(){
  $('histTable').innerHTML=`<div class="tblwrap"><table><thead><tr>
    <th>日期</th><th>提交</th><th>页数</th><th>成功</th><th>失败</th><th>文件配额剩余</th></tr></thead><tbody>`+
    histData.slice().reverse().map(d=>`<tr><td>${esc(d.date)}</td><td><b>${fmtNum(d.submits)}</b></td>
      <td>${fmtNum(d.pages)}</td><td class="tag done">${fmtNum(d.ok)}</td>
      <td class="${d.err?'tag failed':''}">${d.err?fmtNum(d.err):0}</td>
      <td>${d.files_left!=null?fmtNum(d.files_left):'-'}</td></tr>`).join('')+`</tbody></table></div>`;
}

/* ── 导出 ── */
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
refreshAll();
</script>
</body>
</html>
"""
