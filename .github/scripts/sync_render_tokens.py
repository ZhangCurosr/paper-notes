#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Render token 自动同步（GitHub Actions 调用）
=============================================
本地 mineru_accounts.csv 增加 token → 重加密上传 → 本 workflow 解密对比 →
内容变化则通过 Render API 更新 MINERU_TOKENS env → 自动触发重新部署。

依赖（均已在 workflow 注入）：
  env RENDER_API_KEY    Render API key（secrets）
  env RENDER_SERVICE_ID 服务 ID（vars，可选；缺省按服务名 mineru-api 自动查找）
  env MINERU_SERVICE_NAME 服务名（vars，默认 mineru-api）

用法：python .github/scripts/sync_render_tokens.py
"""
import csv
import json
import os
import sys
import urllib.error
import urllib.request

RENDER_API = "https://api.render.com/v1"


def api(method, path, token, body=None):
    req = urllib.request.Request(
        RENDER_API + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
    except Exception as e:
        return 0, {"err": str(e)}


def load_tokens():
    toks = []
    with open("mineru_accounts.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            t = str(r.get("api_key", "")).strip()
            if t.startswith("sk-"):
                toks.append(t)
    return list(dict.fromkeys(toks))


def main():
    api_key = os.environ.get("RENDER_API_KEY", "")
    if not api_key:
        print("缺少 RENDER_API_KEY"); sys.exit(1)
    svc_id = os.environ.get("RENDER_SERVICE_ID", "")
    svc_name = os.environ.get("MINERU_SERVICE_NAME", "mineru-api")

    toks = load_tokens()
    if not toks:
        print("CSV 中无有效 token"); sys.exit(1)
    new_val = ",".join(toks)
    print(f"本地 token 数: {len(toks)}（{len(new_val)} 字符）")

    # 定位服务
    if not svc_id:
        st, svcs = api("GET", "/services", api_key)
        if st != 200:
            print(f"列出服务失败: HTTP {st}"); sys.exit(1)
        for s in svcs:
            svc = s.get("service", {})
            if svc.get("name") == svc_name:
                svc_id = svc.get("id")
                break
        if not svc_id:
            print(f"未找到服务 {svc_name}"); sys.exit(1)
    print(f"服务: {svc_name} ({svc_id})")

    # 获取现有 MINERU_TOKENS env
    st, svc = api("GET", f"/services/{svc_id}", api_key)
    if st != 200:
        print(f"获取服务失败: HTTP {st}"); sys.exit(1)
    target = None
    for e in svc.get("envVars", []):
        v = e.get("envVar", {})
        if v.get("key") == "MINERU_TOKENS":
            target = e
            break

    if target is None:
        st, r = api("POST", f"/services/{svc_id}/envvars", api_key,
                    {"envVar": {"key": "MINERU_TOKENS", "value": new_val}})
        print(f"创建 MINERU_TOKENS: HTTP {st}（触发部署）" if st == 201 else
              f"创建失败: HTTP {st} {str(r)[:120]}")
        sys.exit(0 if st == 201 else 1)

    env_id = target.get("id")
    old = target.get("envVar", {}).get("value") or ""
    if old == new_val:
        print("内容无变化，跳过（不触发部署）")
        return
    print(f"检测到变化: 云端 {len(old)} 字符 → 本地 {len(new_val)} 字符")
    st, r = api("PUT", f"/services/{svc_id}/envvars/{env_id}", api_key,
                {"envVar": {"key": "MINERU_TOKENS", "value": new_val}})
    if st == 200:
        print(f"已更新 MINERU_TOKENS（{len(new_val)} 字符），Render 自动重新部署")
    else:
        print(f"更新失败: HTTP {st} {str(r)[:120]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
