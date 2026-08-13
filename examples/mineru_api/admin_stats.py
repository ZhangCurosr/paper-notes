#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 09：管理与监控（admin）——全局统计 / token 明细 / 趋势 / Prometheus
用法（需 admin key）：
  set MINERU_API_KEY=sk-admin-xxxx
  python examples/mineru_api/admin_stats.py             # 全局统计摘要
  python examples/mineru_api/admin_stats.py --tokens    # 每个 token 明细
  python examples/mineru_api/admin_stats.py --trends    # 小时/天趋势
  python examples/mineru_api/admin_stats.py --metrics   # Prometheus 文本
"""
import argparse
import json
import sys
from common import BASE, KEY, UA, call

p = argparse.ArgumentParser(description="监控（admin）")
p.add_argument("--tokens", action="store_true", help="token 明细")
p.add_argument("--trends", action="store_true", help="趋势")
p.add_argument("--metrics", action="store_true", help="Prometheus 指标")
args = p.parse_args()

if args.metrics:
    # Prometheus 文本格式（非 JSON），直接原始输出
    import urllib.request as _ur
    req = _ur.Request(f"{BASE}/v1/metrics")
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("User-Agent", UA)
    print(_ur.urlopen(req, timeout=60).read().decode())
    sys.exit(0)
elif args.tokens:
    d = call("GET", "/v1/stats/tokens")
    print(f"池: {d['data']['summary']['tokens']} token | "
          f"preflight {d['data']['summary']['preflight']}")
    for t in d["data"]["tokens"][:15]:
        st = ("熔断" if t.get("ban_active") else "配额暂停" if t.get("suspend_active")
              else "冷却" if t.get("cooling") else "active")
        print(f"  {t['token']:10s} {st:6s} 成功率={t.get('success_rate')} "
              f"成功={t.get('ok')} 失败={t.get('err')} 暂停累计={t.get('suspended')} "
              f"429={t.get('rate_limited')} 延迟={t.get('latency_ms')}ms preflight={t.get('preflight')}")
    if len(d["data"]["tokens"]) > 15:
        print(f"  ... 共 {len(d['data']['tokens'])} 个")
elif args.trends:
    d = call("GET", "/v1/stats/trends")
    print("按小时:", json.dumps(d["data"]["by_hour"], ensure_ascii=False))
    print("按天  :", d["data"]["by_day"])
else:
    d = call("GET", "/v1/stats")
    data = d["data"]
    tk = data["tokens"]
    print(f"运行时长 : {data['uptime'] // 3600}h | 策略 {tk['strategy']} | {tk['tokens']} token")
    print(f"提交     : ok={tk['ok']} err={tk['err']} 解析页数={tk['pages_parsed']}")
    print(f"健康     : 熔断={tk['banned_now']} 冷却={tk['cooling']} 配额暂停={tk['suspended_now']}")
    print(f"错误分布 : {tk['err_dist']}")
    print(f"延迟     : {tk['latency_ms']}")
    print(f"预热     : {tk['preflight']}")
    print(f"每日配额 : {tk['daily']}")
    print(f"flash    : {data['flash']}")
    print(f"任务统计 : {data['tasks']}")
    print(f"失败原因 : {data['stats']['fail_reasons']}")
