#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dashboard 页面（共享模块 v3）
=============================
被两类宿主加载：
  1. 云端 mineru_api_server.py  → GET /dashboard（同源直连 /v1/*）
  2. 本地 local_dashboard.py    → 本地代理 /api/*

功能：
  - 总览：告警（含声音提示）、KPI（数字动画）、配额进度、24h 趋势、错误分布
  - Token 池：状态环形图、搜索/排序/详情弹窗/导出 CSV
  - 任务：提交面板、状态环形图、域名 Top、筛选/搜索、批量选择（重试/删除）、
          markdown 预览、分页加载更多、详情弹窗、导出 CSV
  - 历史：7/14/30 天任务量折线、延迟 p90 折线、配额柱状、每日明细表
  - 通用：明暗主题（图表配色自适应）、刷新间隔、快捷键（r 刷新 / 1-4 切 tab）、声音告警
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
.wrap{max-width:1320px;margin:0 auto;padding:16px 20px 40px}
/* 头部 */
.topbar{position:sticky;top:0;z-index:40;display:flex;align-items:center;gap:12px;flex-wrap:wrap;
  padding:11px 0;background:linear-gradient(180deg,var(--bg) 75%,transparent);backdrop-filter:blur(6px)}
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
.iconbtn:disabled{opacity:.5;cursor:not-allowed;transform:none}
select{background:var(--bg2);border:1px solid var(--line);color:var(--fg);border-radius:9px;padding:6px 7px;font-size:12px;outline:none;cursor:pointer}
.kinput{background:var(--bg2);border:1px solid var(--line);color:var(--fg);border-radius:9px;padding:6px 10px;
  font-size:12px;width:200px;outline:none;font-family:Consolas,monospace}
.kinput:focus{border-color:var(--acc)}
/* Tab */
.tabs{display:flex;gap:6px;margin:8px 0 13px;border-bottom:1px solid var(--line)}
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
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:11px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 14px;position:relative;overflow:hidden;transition:.2s;cursor:default}
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
.kpi .trend{position:absolute;top:10px;right:12px;font-size:11px;color:var(--dim)}
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
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:13px}
@media(max-width:1020px){.grid2,.grid3{grid-template-columns:1fr}.quota{grid-template-columns:1fr}}
/* 图表 */
canvas{width:100%;display:block}
.chartwrap{position:relative}
#tip,#tip2{position:absolute;pointer-events:none;background:var(--card2);border:1px solid var(--line);
  border-radius:8px;padding:5px 9px;font-size:12px;display:none;z-index:10;box-shadow:var(--shadow)}
.legend{display:flex;gap:14px;font-size:12px;color:var(--dim);margin-top:7px;flex-wrap:wrap}
.legend i{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:5px;vertical-align:-1px}
/* 提交面板 */
.submitbox{display:flex;flex-direction:column;gap:9px}
.submitbox textarea{width:100%;height:74px;background:var(--bg2);border:1px solid var(--line);color:var(--fg);
  border-radius:10px;padding:9px 12px;font-size:12.5px;resize:vertical;outline:none;font-family:Consolas,monospace}
.submitbox textarea:focus{border-color:var(--acc)}
.sopt{display:flex;gap:14px;flex-wrap:wrap;align-items:center;font-size:12.5px;color:var(--dim)}
.sopt label{display:inline-flex;gap:5px;align-items:center;cursor:pointer}
.sopt label:hover{color:var(--fg)}
.sopt input[type=checkbox]{accent-color:var(--acc)}
.sopt select{font-size:12px}
/* 环形图 */
.donutwrap{display:flex;align-items:center;gap:16px}
.donutwrap canvas{width:130px;height:130px}
.dl{display:flex;flex-direction:column;gap:5px;font-size:12.5px}
.dl .it{display:flex;align-items:center;gap:7px}
.dl i{width:10px;height:10px;border-radius:3px;display:inline-block}
.dl b{margin-left:auto;font-variant-numeric:tabular-nums}
/* 错误条 */
.errrow{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:12px}
.errrow .lbl{width:66px;color:var(--dim);text-align:right;font-family:monospace}
.errrow .trk{flex:1;height:13px;background:var(--bg2);border-radius:7px;overflow:hidden}
.errrow .fl{height:100%;border-radius:7px;background:linear-gradient(90deg,#f2637b,#f5b94d);min-width:3px}
.errrow .n{width:40px;font-variant-numeric:tabular-nums;color:var(--dim)}
/* 表格 */
.tblwrap{overflow:auto;max-height:460px;border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{position:sticky;top:0;background:var(--card2);color:var(--dim);font-weight:600;padding:8px 9px;
  border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;cursor:pointer;user-select:none;z-index:2}
th:hover{color:var(--fg)}
th .arr{font-size:10px;color:var(--acc)}
td{padding:7px 9px;border-bottom:1px solid var(--line);white-space:nowrap;font-variant-numeric:tabular-nums}
tr:hover td{background:var(--card2)}
td.src{max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#toktbl tbody tr{cursor:pointer}
#tasktbl td.cb{width:30px;padding-right:0}
#tasktbl td.cb input{accent-color:var(--acc);cursor:pointer}
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
.hbtn{background:var(--card2);border:1px solid var(--line);color:var(--dim);border-radius:9px;padding:5px 13px;
  font-size:12px;cursor:pointer;transition:.15s}
.hbtn.on{color:var(--acc);border-color:var(--acc);background:rgba(91,157,255,.12)}
/* 弹窗 */
.modalbg{position:fixed;inset:0;background:rgba(6,10,20,.6);backdrop-filter:blur(3px);display:none;
  align-items:center;justify-content:center;z-index:50;padding:20px}
.modalbg.show{display:flex}
.modal{background:var(--card);border:1px solid var(--line);border-radius:16px;max-width:680px;width:100%;
  max-height:84vh;overflow:auto;box-shadow:var(--shadow);animation:pop .18s ease}
@keyframes pop{from{transform:scale(.96);opacity:0}}
.modal .mhead{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--card)}
.modal .mhead h3{font-size:14px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal .mbody{padding:14px 18px}
.kv{display:grid;grid-template-columns:108px 1fr;gap:6px 12px;font-size:12.5px}
.kv dt{color:var(--dim)} .kv dd{word-break:break-all}
.mono{font-family:Consolas,monospace;font-size:11.5px;background:var(--bg2);border-radius:5px;padding:1px 6px}
.mbody .ops{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:13px}
.preview{background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:12px;
  max-height:380px;overflow:auto;font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-all}
/* toast / 空态 */
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
    <button class="iconbtn" id="soundBtn" title="异常声音提醒">🔔</button>
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
    <div class="panel" style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
      <div class="donutwrap"><canvas id="tokDonut" width="260" height="260"></canvas>
        <div class="dl" id="tokDl"></div></div>
      <div style="flex:1;min-width:200px"><div class="dim" style="margin-bottom:6px">Token 池速览</div>
        <div id="tokQuick" class="dim">加载中…</div></div>
    </div>
    <div class="panel" style="margin-top:13px">
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
      <div class="tools" style="margin-bottom:8px">
        <b style="font-size:13px">📤 快速提交任务</b><span class="dim">（每行一个 URL，≤50）</span>
        <span class="spacer"></span>
        <button class="iconbtn" id="submitBtn">提交</button>
      </div>
      <div class="submitbox">
        <textarea id="urlInput" placeholder="https://arxiv.org/pdf/xxxx.pdf&#10;https://example.com/paper.pdf"></textarea>
        <div class="sopt">
          <label><input type="checkbox" id="optFormula"> 公式</label>
          <label><input type="checkbox" id="optTable"> 表格</label>
          <label><input type="checkbox" id="optOcr"> OCR</label>
          <label><input type="checkbox" id="optFresh"> 强制重解析</label>
          <label><input type="checkbox" id="optFlash"> flash 通道</label>
          <span>语言 <select id="optLang"><option value="">默认</option><option>zh</option><option>en</option></select></span>
        </div>
      </div>
      <div id="submitResult" class="dim" style="margin-top:8px"></div>
    </div>
    <div class="grid3" style="margin-top:13px">
      <div class="panel" style="padding:10px"><div class="dim" style="margin-bottom:4px">任务状态分布</div>
        <div class="donutwrap" style="gap:10px"><canvas id="taskDonut" width="240" height="240"></canvas>
          <div class="dl" id="taskDl"></div></div></div>
      <div class="panel" style="padding:10px"><div class="dim" style="margin-bottom:4px">来源域名 Top</div>
        <div id="domainTop" class="dim">加载中…</div></div>
      <div class="panel" style="padding:10px"><div class="dim" style="margin-bottom:4px">失败原因 Top</div>
        <div id="taskFailTop" class="dim">加载中…</div></div>
    </div>
    <div class="panel" style="margin-top:13px">
      <div class="tools">
        <input class="search" id="taskSearch" placeholder="🔍 搜索来源 URL / task_id…" oninput="renderTasksLocal()">
        <span class="statbar" id="taskSegs"></span>
        <span class="dim" id="selInfo"></span>
        <span class="spacer"></span>
        <button class="iconbtn" id="selAllBtn" title="全选/取消">☑ 全选</button>
        <button class="iconbtn" id="bulkRetryBtn" title="批量重试（仅 failed）">↻ 重试所选</button>
        <button class="iconbtn danger" id="bulkDelBtn" title="批量删除">🗑 删除所选</button>
        <button class="iconbtn" onclick="exportCSV(tasks,'tasks')">⭳ 导出 CSV</button>
      </div>
      <div class="tblwrap" style="max-height:400px"><table id="tasktbl"><thead><tr>
        <th class="cb"><input type="checkbox" id="selAll" onchange="toggleAll()"></th>
        <th>task_id</th><th>状态</th><th>通道</th><th>来源</th><th>创建</th><th>耗时</th><th>错误</th><th>操作</th>
      </tr></thead><tbody></tbody></table></div>
      <div class="tools" style="margin-top:8px">
        <span class="dim" id="taskTotal"></span><span class="spacer"></span>
        <button class="iconbtn" id="moreBtn">加载更多</button>
      </div>
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
      <h2 style="margin-top:6px">每日任务量</h2>
      <div class="chartwrap"><canvas id="histChart1"></canvas><div id="tip2"></div></div>
      <h2>提交延迟 p90（ms）</h2>
      <div class="chartwrap"><canvas id="histChart3"></canvas></div>
      <h2>每日配额消耗（文件/天，上限 5000）</h2>
      <div class="chartwrap"><canvas id="histChart2"></canvas></div>
      <h2>每日明细</h2>
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
/* ── 模式 ── */
const IS_CLOUD = location.pathname.startsWith('/dashboard');
let savedKey = localStorage.getItem('dash_key') || '';
document.getElementById('modeTag').textContent = IS_CLOUD ? '云端直连模式' : '本地代理模式';
if(IS_CLOUD){ document.getElementById('loginBox').style.display = savedKey ? 'none' : 'inline-flex'; }
function ep(name,arg){
  if(IS_CLOUD){
    if(name==='overview'||name==='errbox') return '/v1/stats';
    if(name==='tokens') return '/v1/stats/tokens';
    if(name==='tasks') return '/v1/tasks?limit=100&offset='+(arg?.offset||0)+(arg?.status?'&status='+arg.status:'');
    if(name==='task') return '/v1/tasks/'+arg;
    if(name==='result') return '/v1/tasks/'+arg+'/result';
    if(name==='retry') return '/v1/tasks/'+arg+'/retry';
    if(name==='delete') return '/v1/tasks/'+arg;
    if(name==='history') return '/v1/history?days='+arg;
    if(name==='submit') return '/v1/tasks';
  }else{
    if(name==='overview') return '/api/overview';
    if(name==='errbox') return '/api/errbox';
    if(name==='tokens') return '/api/tokens';
    if(name==='tasks') return '/api/tasks?limit=100&offset='+(arg?.offset||0)+(arg?.status?'&status='+arg.status:'');
    if(name==='task') return '/api/task/'+arg;
    if(name==='result') return '/api/task-result/'+arg;
    if(name==='retry') return '/api/retry/'+arg;
    if(name==='delete') return '/api/task/'+arg;
    if(name==='history') return '/api/history?days='+arg;
    if(name==='submit') return '/api/submit';
  }
}
function authH(){ return savedKey?{'Authorization':'Bearer '+savedKey}:{}; }
async function apiGet(name,arg){
  const r = await fetch(ep(name,arg),{headers:authH()});
  if(r.status===401){ showLogin(); throw new Error('unauthorized'); }
  return r.json();
}
async function apiSend(name,arg,method,body){
  const r = await fetch(ep(name,arg),{method,headers:{...(body?{'Content-Type':'application/json'}:{}),...authH()},
    body:body?JSON.stringify(body):undefined});
  return r.json();
}
function showLogin(){ if(IS_CLOUD) document.getElementById('loginBox').style.display='inline-flex'; }
document.getElementById('loginBtn').onclick=()=>{
  savedKey = document.getElementById('keyInput').value.trim();
  localStorage.setItem('dash_key', savedKey);
  document.getElementById('loginBox').style.display='none';
  toast('已保存，加载数据…'); refreshAll();
};

/* ── 工具 ── */
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtNum=n=>n==null?'-':Number(n).toLocaleString();
function relTime(ts){ if(!ts) return '-'; const d=Date.now()/1000-ts;
  if(d<60) return Math.floor(d)+'s'; if(d<3600) return Math.floor(d/60)+'m';
  if(d<86400) return Math.floor(d/3600)+'h'; return Math.floor(d/86400)+'d'; }
function toast(m,err){ const t=$('toast'); t.textContent=m; t.className=err?'err':''; t.style.display='block';
  setTimeout(()=>t.style.display='none',3500); }
function cssVar(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function beep(){
  try{ const ctx=new (window.AudioContext||window.webkitAudioContext)();
    [0,0.15].forEach(d=>{const o=ctx.createOscillator(),g=ctx.createGain();
      o.frequency.value=880;o.connect(g);g.connect(ctx.destination);
      g.gain.setValueAtTime(.08,ctx.currentTime+d);g.gain.exponentialRampToValueAtTime(.001,ctx.currentTime+d+.25);
      o.start(ctx.currentTime+d);o.stop(ctx.currentTime+d+.3);}); }catch(e){}
}
const SOUND = localStorage.getItem('dash_sound')==='1';

/* ── Tab ── */
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
function switchTab(t){
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.dataset.tab===t));
  document.querySelectorAll('section').forEach(x=>x.classList.toggle('on',x.id==='tab-'+t));
  if(t==='task'){ loadTasks(true); }
  if(t==='hist') loadHistory(histDays);
}
/* 快捷键 */
document.addEventListener('keydown',e=>{
  if(e.target.matches('input,textarea,select')) return;
  if(e.key==='r'||e.key==='R') refreshAll();
  if(['1','2','3','4'].includes(e.key)) switchTab(['ov','tok','task','hist'][+e.key-1]);
});

/* ── 主题 / 声音 / 刷新 ── */
let auto=true, left=15, histDays=7;
function applyTheme(t){ document.documentElement.dataset.theme=t; $('themeBtn').textContent=t==='dark'?'🌓':'☀️'; }
applyTheme(localStorage.getItem('dash_theme')||'dark');
$('themeBtn').onclick=()=>{ const t=document.documentElement.dataset.theme==='dark'?'light':'dark';
  localStorage.setItem('dash_theme',t); applyTheme(t); refreshAll(); };
$('soundBtn').onclick=()=>{ localStorage.setItem('dash_sound', SOUND?'0':'1');
  location.reload(); };
$('soundBtn').textContent = SOUND ? '🔔 开' : '🔕 关';
$('autoBtn').onclick=()=>{ auto=!auto; $('autoBtn').textContent=auto?'⏸':'▶'; };
$('interval').onchange=()=>{ left=+$('interval').value; $('clock').textContent='⏱ '+left+'s'; };
$('refBtn').onclick=()=>refreshAll();

/* ── 加载 ── */
async function refreshAll(){
  const b=$('refBtn'); b.classList.add('spin'); b.innerHTML='⟳ 刷新中…';
  try{
    const [ov,tk]=await Promise.all([apiGet('overview'),apiGet('tokens')]);
    renderOverview(ov); renderTokens(tk); renderChart(ov); renderErr(ov);
    if($('tab-task').classList.contains('on')) loadTasks(true);
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
let prevAlerts='';
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
  $('poolinfo').textContent=`preflight ${JSON.stringify(tk.preflight||{})}`;
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
  const html=alerts.map(a=>`<div class="alert ${a[0]}">${a[1]}</div>`).join('');
  $('alerts').innerHTML=html;
  if(SOUND && html && html!==prevAlerts){ beep(); }
  prevAlerts=html;
}

/* ── 环形图 ── */
function drawDonut(cv, items, center){
  const ctx=cv.getContext('2d'); const W=cv.width,H=cv.height;
  ctx.clearRect(0,0,W,H);
  const total=items.reduce((s,i)=>s+i.value,0); if(!total) return;
  const r=Math.min(W,H)/2-6, cx=W/2, cy=H/2, lw=Math.max(10,r*0.28);
  let a=-Math.PI/2;
  items.forEach(it=>{
    const ang=it.value/total*Math.PI*2;
    ctx.beginPath(); ctx.arc(cx,cy,r,a,a+ang);
    ctx.strokeStyle=it.color; ctx.lineWidth=lw; ctx.lineCap='butt'; ctx.stroke();
    a+=ang;
  });
  ctx.fillStyle=cssVar('--fg'); ctx.font='bold 15px sans-serif'; ctx.textAlign='center';
  ctx.fillText(center||String(total), cx, cy-2);
  ctx.font='10px sans-serif'; ctx.fillStyle=cssVar('--dim');
  ctx.fillText('总数', cx, cy+14);
}
function donutData(items){
  return items.filter(i=>i.value>0);
}

/* ── 24h 趋势 ── */
function renderChart(ov){
  const tr=ov.data?.trends_24h||{};
  const keys=Object.keys(tr), vals=keys.map(k=>tr[k]);
  const cv=$('chart'),ctx=cv.getContext('2d');
  const W=cv.width=cv.offsetWidth*2,H=cv.height=150*2; ctx.clearRect(0,0,W,H);
  const max=Math.max(1,...vals),n=keys.length||1,pad=12;
  const line=cssVar('--acc'), dim=cssVar('--dim'), grid='rgba(90,110,150,.14)';
  ctx.strokeStyle=grid; ctx.lineWidth=1; ctx.fillStyle=dim; ctx.font='10px sans-serif';
  for(let g=0;g<=3;g++){ const y=pad+g*(H-2*pad)/3;
    ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(W-pad,y);ctx.stroke();
    ctx.fillText(Math.round(max*(3-g)/3),2,y+3); }
  const pts=vals.map((v,i)=>{const x=pad+i*(W-2*pad)/Math.max(1,n-1);const y=H-pad-(v/max)*(H-2*pad);return[x,y];});
  ctx.beginPath();ctx.moveTo(pts[0][0],H-pad);pts.forEach(p=>ctx.lineTo(p[0],p[1]));ctx.lineTo(pts[pts.length-1][0],H-pad);ctx.closePath();
  const g=ctx.createLinearGradient(0,0,0,H);g.addColorStop(0,line+'46');g.addColorStop(1,line+'05');
  ctx.fillStyle=g;ctx.fill();
  ctx.strokeStyle=line;ctx.lineWidth=2.5;ctx.lineJoin='round';
  ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke();
  ctx.fillStyle=line; pts.forEach(p=>{ctx.beginPath();ctx.arc(p[0],p[1],3,0,7);ctx.fill();});
  if(n>1){ctx.fillStyle=dim;ctx.fillText(keys[0],pad,H-3);ctx.fillText(keys[n-1],W-70,H-3);}
  cv.onmousemove=e=>{const r=cv.getBoundingClientRect(); const x=e.clientX-r.left,y=e.clientY-r.top;
    let best=-1,bd=1e9; pts.forEach((p,i)=>{const dx=x-p[0]/2,dy=y-p[1]/2,d=dx*dx+dy*dy;if(d<bd){bd=d;best=i;}});
    if(best>=0&&bd<900){ const tip=$('tip'); tip.style.display='block';
      tip.style.left=(pts[best][0]/2+8)+'px'; tip.style.top=(pts[best][1]/2-6)+'px';
      tip.innerHTML=`<b>${keys[best]}</b><br>${vals[best]} 任务`; }
    else $('tip').style.display='none'; };
  cv.onmouseleave=()=>$('tip').style.display='none';
}

/* ── 错误分布 ── */
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
function renderTokens(d){
  tokData=(d.data?.tokens||[]).slice(); renderTokBody();
  const st={active:0,cooling:0,suspended:0,banned:0};
  tokData.forEach(t=>{const[s]=tokStatus(t);st[s]++;});
  const colors={'active':cssVar('--ok'),'cooling':cssVar('--warn'),'suspended':cssVar('--warn'),'banned':cssVar('--bad')};
  const items=donutData([{label:'active',value:st.active,color:colors.active},
    {label:'冷却',value:st.cooling,color:colors.cooling},
    {label:'暂停',value:st.suspended,color:colors.suspended},
    {label:'熔断',value:st.banned,color:colors.banned}]);
  drawDonut($('tokDonut'), items, String(st.active));
  $('tokDl').innerHTML=items.map(i=>`<div class="it"><i style="background:${i.color}"></i>${i.label}<b>${i.value}</b></div>`).join('');
  $('tokQuick').innerHTML=`共 ${tokData.length} 个 token · 活跃 <b class="tag active">${st.active}</b> · `+
    `冷却 <b class="tag cooling">${st.cooling}</b> · 配额暂停 <b class="tag suspended">${st.suspended}</b> · 熔断 <b class="tag banned">${st.banned}</b>`;
}
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

/* ── 提交任务 ── */
async function submitTasks(){
  const text=$('urlInput').value.trim();
  const urls=text.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
  if(!urls.length){ toast('请输入至少一个 URL',true); return; }
  if(urls.length>50){ toast('单次最多 50 个',true); return; }
  const body={urls};
  if($('optFormula').checked) body.formula=true;
  if($('optTable').checked) body.table=true;
  if($('optOcr').checked) body.ocr=true;
  if($('optFresh').checked) body.fresh=true;
  if($('optFlash').checked) body.flash=true;
  const lang=$('optLang').value; if(lang) body.language=lang;
  const b=$('submitBtn'); b.disabled=true; b.textContent='提交中…';
  try{
    const r=await apiSend('submit',null,'POST',body);
    if(r.code!==0){ toast('提交失败: '+(r.msg||''),true); $('submitResult').innerHTML=`<span class="errline">${esc(r.msg||'')}</span>`; }
    else{
      const ids=r.data.task_ids||[];
      $('submitResult').innerHTML=`✅ 已提交 <b>${ids.length}</b> 个任务：<span class="mono">${ids.join(', ')}</span>`+
        (r.data.reused?`（复用 ${r.data.reused} 个已完成）`:'');
      toast('已提交 '+ids.length+' 个任务');
      $('urlInput').value='';
      loadTasks(true);
    }
  }catch(e){ toast('提交失败: '+e,true); }
  b.disabled=false; b.textContent='提交';
}
$('submitBtn').onclick=submitTasks;
$('urlInput').addEventListener('keydown',e=>{ if(e.key==='Enter'&&e.ctrlKey) submitTasks(); });

/* ── 任务 ── */
let tasks=[], allTasks=[], curTask=null, statusF='', sel=new Set(), taskOffset=0, taskTotal=0;
async function loadTasks(reset){
  if(reset) taskOffset=0;
  try{
    const d=await apiGet('tasks',{offset:taskOffset,status:statusF});
    const list=d.data?.tasks||[]; taskTotal=d.data?.total||0;
    allTasks=reset?list:allTasks.concat(list);
    tasks=allTasks; taskOffset+=list.length;
    renderTasks(); renderTaskStats();
  }catch(e){ if(!IS_CLOUD) toast('任务加载失败',true); }
}
function renderTasks(){
  $('taskN').textContent=allTasks.length?(' '+allTasks.length):'';
  const by={}; allTasks.forEach(t=>by[t.status]=(by[t.status]||0)+1);
  $('taskTotal').textContent=`已加载 ${allTasks.length} / ${taskTotal} 条`;
  $('moreBtn').style.display = allTasks.length>=taskTotal||taskTotal===0 ? 'none':'';
  $('taskSegs').innerHTML=['','pending','submitted','done','failed'].map(s=>
    `<span class="seg ${s===statusF?'on':''}" onclick="setStatus('${s}')">${s||'全部'}${s&&by[s]?` <b>${by[s]}</b>`:s===''?` <b>${allTasks.length}</b>`:''}</span>`).join('');
  renderTasksLocal();
}
function setStatus(s){ statusF=s; sel.clear(); loadTasks(true); }
$('moreBtn').onclick=()=>loadTasks(false);
function renderTasksLocal(){
  const q=$('taskSearch').value.toLowerCase();
  const list=tasks.filter(t=>!q||(t.source||'').toLowerCase().includes(q)||(t.task_id||'').toLowerCase().includes(q));
  const tb=$('tasktbl tbody');
  if(!list.length){tb.innerHTML='<tr><td colspan="9" class="empty">暂无任务</td></tr>';return;}
  tb.innerHTML=list.map(t=>{
    const checked=sel.has(t.task_id)?'checked':'';
    return `<tr class="${checked?'selrow':''}" style="cursor:pointer" onclick="rowClick(event,'${t.task_id}')">
    <td class="cb" onclick="event.stopPropagation()"><input type="checkbox" ${checked} onchange="toggleSel('${t.task_id}')"></td>
    <td>${esc(t.task_id)}</td><td><span class="tag ${t.status}">${esc(t.status)}</span></td>
    <td>${esc(t.channel||'-')}</td><td class="src" title="${esc(t.source)}">${esc(t.source)}</td>
    <td title="${new Date(t.created_at*1000).toLocaleString()}">${relTime(t.created_at)}</td>
    <td>${t.finished_at?relTime(t.finished_at-t.created_at):'-'}</td>
    <td class="dim" style="max-width:150px;overflow:hidden;text-overflow:ellipsis" title="${esc(t.error||'')}">${esc(t.error||'')}</td>
    <td><span class="dim" style="cursor:pointer" onclick="event.stopPropagation();openTask('${t.task_id}')">详情 ›</span></td></tr>`;}).join('');
  $('selInfo').textContent=sel.size?`已选 ${sel.size}`:'';
}
function rowClick(e,tid){ if(e.target.closest('td.cb'))return; openTask(tid); }
function toggleSel(tid){ sel.has(tid)?sel.delete(tid):sel.add(tid); renderTasksLocal(); updateBulk(); }
function toggleAll(){ const cb=$('selAll');
  if(cb.checked){ tasks.forEach(t=>sel.add(t.task_id)); } else sel.clear();
  renderTasksLocal(); updateBulk(); }
function updateBulk(){
  $('bulkRetryBtn').disabled = ![...sel].some(id=>{const t=allTasks.find(x=>x.task_id===id);return t&&t.status==='failed';});
  $('bulkDelBtn').disabled = sel.size===0;
  $('selInfo').textContent=sel.size?`已选 ${sel.size}`:'';
}
$('bulkRetryBtn').onclick=async()=>{
  const selFailed=[...sel].filter(id=>{const t=allTasks.find(x=>x.task_id===id);return t&&t.status==='failed';});
  if(!selFailed.length){toast('所选任务中没有 failed 的',true);return;}
  if(!confirm('批量重试 '+selFailed.length+' 个 failed 任务？')) return;
  let okN=0;
  for(const id of selFailed){ const r=await apiSend('retry',id,'POST'); if(r.code===0) okN++; }
  toast('重试完成：成功 '+okN+'/'+selFailed.length, okN<selFailed.length);
  sel.clear(); loadTasks(true);
};
$('bulkDelBtn').onclick=async()=>{
  if(!sel.size) return;
  if(!confirm('确认删除 '+sel.size+' 个任务？（记录与产物不可恢复）')) return;
  let okN=0;
  for(const id of [...sel]){ const r=await apiSend('delete',id,'DELETE'); if(r.code===0) okN++; }
  toast('删除完成：成功 '+okN+'/'+sel.size, okN<sel.size);
  sel.clear(); loadTasks(true);
};
function renderTaskStats(){
  // 状态环形图
  const st={done:0,failed:0,pending:0,submitted:0};
  allTasks.forEach(t=>{ if(st[t.status]!=null) st[t.status]++; });
  const C={done:cssVar('--ok'),failed:cssVar('--bad'),pending:cssVar('--warn'),submitted:cssVar('--acc')};
  const items=donutData([{label:'done',value:st.done,color:C.done},{label:'failed',value:st.failed,color:C.failed},
    {label:'pending',value:st.pending,color:C.pending},{label:'submitted',value:st.submitted,color:C.submitted}]);
  drawDonut($('taskDonut'), items, String(st.done));
  $('taskDl').innerHTML=items.map(i=>`<div class="it"><i style="background:${i.color}"></i>${i.label}<b>${i.value}</b></div>`).join('');
  // 域名 Top
  const doms={};
  allTasks.forEach(t=>{ try{ const h=new URL(t.source||'x').hostname; doms[h]=(doms[h]||0)+1; }catch(e){} });
  const top=Object.entries(doms).sort((a,b)=>b[1]-a[1]).slice(0,8);
  const mx=Math.max(1,...top.map(x=>x[1]));
  $('domainTop').innerHTML=top.length?top.map(([h,n])=>
    `<div class="errrow"><span class="lbl" style="width:auto;max-width:150px;overflow:hidden;text-overflow:ellipsis">${esc(h)}</span><div class="trk"><div class="fl" style="width:${n/mx*100}%;background:linear-gradient(90deg,var(--acc),var(--purple))"></div></div><span class="n">${n}</span></div>`).join('')
    :'<div class="empty">暂无数据</div>';
  // 失败原因（从 allTasks error 统计）
  const fr={};
  allTasks.filter(t=>t.status==='failed'&&t.error).forEach(t=>{
    const e=(t.error||'').slice(0,60); fr[e]=(fr[e]||0)+1; });
  const frs=Object.entries(fr).sort((a,b)=>b[1]-a[1]).slice(0,8);
  $('taskFailTop').innerHTML=frs.length?frs.map(([k,v])=>
    `<div class="errrow"><span class="lbl" style="width:auto;font-family:inherit">×${v}</span><span class="dim" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px">${esc(k)}</span></div>`).join('')
    :'<div class="empty">暂无失败</div>';
}
/* 任务详情 */
async function openTask(tid){
  try{ const d=await apiGet('task',tid);
    if(d.code!==0){ toast('任务不存在或无权访问',true); return; }
    const t=d.data||{}; curTask=t;
    $('mTitle').textContent=tid;
    $('mBody').innerHTML=`<div class="ops">
      <button class="iconbtn" onclick="copyText(curTask.task_id,'已复制 task_id')">📋 复制 task_id</button>
      <button class="iconbtn" onclick="copyText(curTask.source||'','已复制来源 URL')">📋 复制来源</button>
      ${t.status==='done'?`<button class="iconbtn" onclick="previewMd('${tid}')">👁 预览 markdown</button>`:''}
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
async function previewMd(tid){
  try{
    const d=await apiGet('result',tid);
    const data=d.data||{};
    if(data.status!=='done'||!data.markdown){ toast('暂无 markdown（任务未完成或未下载）',true); return; }
    const md=data.markdown.length>300000 ? data.markdown.slice(0,300000)+'\n…（截断）' : data.markdown;
    const blk=document.createElement('div'); blk.className='preview'; blk.textContent=md;
    const old=$('mPreview'); if(old) old.remove();
    blk.id='mPreview';
    $('mBody').appendChild(blk);
    toast('已加载 '+fmtNum(data.markdown.length)+' 字符');
  }catch(e){ toast('预览加载失败: '+e,true); }
}
async function copyText(txt,msg){
  try{ await navigator.clipboard.writeText(txt); toast(msg||'已复制'); }
  catch(e){ toast('复制失败（浏览器限制），请手动复制',true); }
}
async function doRetry(tid){
  if(!confirm('确认重试任务 '+tid+' ？')) return;
  const r=await apiSend('retry',tid,'POST');
  toast(r.code===0?'已重置为 pending，等待重新提交':'重试失败: '+(r.msg||''), r.code!==0);
  closeModal(); loadTasks(true);
}
async function doDelete(tid){
  if(!confirm('确认删除任务 '+tid+' ？（记录与产物将不可恢复）')) return;
  const r=await apiSend('delete',tid,'DELETE');
  toast(r.code===0?'已删除 '+tid:'删除失败: '+(r.msg||''), r.code!==0);
  closeModal(); loadTasks(true);
}
function closeModal(){ $('modal').classList.remove('show'); }

/* ── 历史 ── */
let histData=[];
async function loadHistory(days){
  histDays=days;
  try{ const d=await apiGet('history',days);
    if(d.code!==0){ $('histInfo').textContent='历史数据暂不可用（'+d.msg+')'; return; }
    histData=d.data?.days||[];
    $('histInfo').textContent='历史数据归档于云端 GitHub 仓库（每小时）· '+histData.length+' 天';
    drawHist1(); drawHist3(); drawHist2(); renderHistTable();
  }catch(e){ if(!IS_CLOUD) toast('历史加载失败',true); }
}
function setHist(days,el){ document.querySelectorAll('.hbtn').forEach(x=>x.classList.remove('on'));
  el.classList.add('on'); loadHistory(days); }
function lineChart(cvId,tipId,labels,vals,color,unit,cap){
  const cv=$(cvId),ctx=cv.getContext('2d');
  const W=cv.width=cv.offsetWidth*2,H=cv.height=130*2; ctx.clearRect(0,0,W,H);
  if(!labels.length){ctx.fillStyle=cssVar('--dim');ctx.font='12px sans-serif';ctx.textAlign='center';
    ctx.fillText('暂无历史数据',W/2-40,H/2);return;}
  const mx=Math.max(1,...vals),pad=12;
  ctx.strokeStyle='rgba(90,110,150,.14)';ctx.lineWidth=1;ctx.fillStyle=cssVar('--dim');ctx.font='10px sans-serif';
  for(let g=0;g<=3;g++){const y=pad+g*(H-2*pad)/3;ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(W-pad,y);ctx.stroke();
    ctx.fillText(Math.round(mx*(3-g)/3),2,y+3);}
  const pts=vals.map((v,i)=>{const x=pad+i*(W-2*pad)/Math.max(1,labels.length-1);const y=H-pad-(v/mx)*(H-2*pad);return[x,y];});
  ctx.beginPath();ctx.moveTo(pts[0][0],H-pad);pts.forEach(p=>ctx.lineTo(p[0],p[1]));ctx.lineTo(pts[pts.length-1][0],H-pad);ctx.closePath();
  const g=ctx.createLinearGradient(0,0,0,H);g.addColorStop(0,color+'46');g.addColorStop(1,color+'05');
  ctx.fillStyle=g;ctx.fill();
  ctx.strokeStyle=color;ctx.lineWidth=2.5;ctx.lineJoin='round';
  ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke();
  ctx.fillStyle=color;pts.forEach(p=>{ctx.beginPath();ctx.arc(p[0],p[1],3,0,7);ctx.fill();});
  if(labels.length>1){ctx.fillStyle=cssVar('--dim');ctx.fillText(labels[0],pad,H-3);ctx.fillText(labels[labels.length-1],W-60,H-3);}
  cv.onmousemove=e=>{const r=cv.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
    let best=-1,bd=1e9;pts.forEach((p,i)=>{const dx=x-p[0]/2,dy=y-p[1]/2,d=dx*dx+dy*dy;if(d<bd){bd=d;best=i;}});
    if(best>=0&&bd<900){const tip=$(tipId);tip.style.display='block';
      tip.style.left=(pts[best][0]/2+8)+'px';tip.style.top=(pts[best][1]/2-6)+'px';
      tip.innerHTML=`<b>${labels[best]}</b><br>${vals[best]}${unit||''}`;}else $(tipId).style.display='none';};
  cv.onmouseleave=()=>$(tipId).style.display='none';
}
function drawHist1(){ lineChart('histChart1','tip2',histData.map(d=>d.date),histData.map(d=>d.submits||0),cssVar('--purple'),' 提交'); }
function drawHist3(){ lineChart('histChart3','tip2',histData.map(d=>d.date),histData.map(d=>d.latency?.p90||null),cssVar('--warn'),' ms'); }
function drawHist2(){
  const cv=$('histChart2'),ctx=cv.getContext('2d');
  const W=cv.width=cv.offsetWidth*2,H=cv.height=110*2; ctx.clearRect(0,0,W,H);
  const ds=histData; if(!ds.length)return;
  const cap=5000, vals=ds.map(d=>Math.min(1,(d.submits||0)/cap)),pad=12;
  const bw=(W-2*pad)/Math.max(1,ds.length)*0.62;
  ds.forEach((d,i)=>{const x=pad+i*(W-2*pad)/Math.max(1,ds.length-1)+ (W-2*pad)/Math.max(1,ds.length)/2-bw/2;
    const h=(vals[i])*(H-2*pad);
    const g=ctx.createLinearGradient(0,H-h,0,H);g.addColorStop(0,cssVar('--acc'));g.addColorStop(1,cssVar('--acc')+'40');
    ctx.fillStyle=g;ctx.fillRect(x,H-pad-h,bw,h);
    ctx.fillStyle=cssVar('--dim');ctx.font='9px sans-serif';
    if(ds.length<=14) ctx.fillText(d.submits||'',x+2,H-pad-h-2);});
  ctx.fillStyle=cssVar('--dim');ctx.font='10px sans-serif';
  ctx.fillText('上限 5000/天',W-90,H-3);
}
function renderHistTable(){
  $('histTable').innerHTML=`<div class="tblwrap" style="max-height:300px"><table><thead><tr>
    <th>日期</th><th>提交</th><th>页数</th><th>成功</th><th>失败</th><th>延迟 p90</th><th>文件配额剩余</th></tr></thead><tbody>`+
    histData.slice().reverse().map(d=>`<tr><td>${esc(d.date)}</td><td><b>${fmtNum(d.submits)}</b></td>
      <td>${fmtNum(d.pages)}</td><td><span class="tag done">${fmtNum(d.ok)}</span></td>
      <td>${d.err?`<span class="tag failed">${fmtNum(d.err)}</span>`:0}</td>
      <td>${d.latency?.p90!=null?d.latency.p90+'ms':'-'}</td>
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
