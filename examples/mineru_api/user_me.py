#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 07：我的用量（/v1/me）——任务统计 + 限流额度
用法：
  set MINERU_API_KEY=sk-user-xxx
  python examples/mineru_api/user_me.py
"""
import json
from common import call

d = call("GET", "/v1/me")
me = d["data"]
print(f"key        : {me['key']}")
print(f"name       : {me['name']}")
print(f"任务总数   : {me['tasks_total']}")
print(f"按状态     : {json.dumps(me['tasks_by_status'], ensure_ascii=False)}")
print(f"按通道     : {me['tasks_by_channel']}   (v4=token 池 / flash=免token)")
print(f"按模型     : {me['tasks_by_model']}")
print(f"API 请求数 : {me['api_requests']}")
print(f"限流       : {me['rate_limit_per_min']} 次/分钟（超出返回 401 请求过于频繁）")
