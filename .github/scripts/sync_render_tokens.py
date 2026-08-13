#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Render token 自动同步（GitHub Actions 调用）
=============================================
本地 mineru_accounts.csv 增加 token → 重加密上传 → 本 workflow 解密对比 →
内容变化则通过 Render API 更新 MINERU_TOKENS env → 自动触发重新部署。

机制说明（Render API 实测验证）：
  - GET  /services/{id}/env-vars/{KEY}  → 200 读回完整值（404 = 不存在）
  - PUT  /services/{id}/env-vars/{KEY}  body {"value": "..."}
        → 不存在则创建、存在则更新；**相同值 PUT 不触发部署**（Render 内部 diff）
  - 因此：GET 对比 → 变化才 PUT（精准触发部署），无需外部指纹存储

依赖（workflow 注入）：
  env RENDER_API_KEY     Render API key（secrets）
  env RENDER_SERVICE_ID  服务 ID（vars，可选；缺省按服务名自动查找）
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
                 "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        raw = r.read().decode(errors="replace")
        try:
            return r.status, json.loads(raw)
        except Exception:
            return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:200]}
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

    # 定位服务（未指定 ID 时按服务名查找）
    if not svc_id:
        st, body = api("GET", "/services", api_key)
        if st != 200:
            print(f"列出服务失败: HTTP {st} {str(body)[:100]}"); sys.exit(1)
        for s in body:
            svc = s.get("service", {})
            if svc.get("name") == svc_name:
                svc_id = svc.get("id")
                break
        if not svc_id:
            print(f"未找到服务 {svc_name}"); sys.exit(1)
    print(f"服务: {svc_name} ({svc_id})")

    # 读取云端当前值（404 = 尚未创建）
    st, body = api("GET", f"/services/{svc_id}/env-vars/MINERU_TOKENS", api_key)
    if st == 200 and isinstance(body, dict):
        old = body.get("value") or ""
        if old == new_val:
            print("内容无变化，跳过（不触发部署）")
            return
        print(f"检测到变化: 云端 {len(old)} 字符 → 本地 {len(new_val)} 字符")
    elif st == 404:
        print("MINERU_TOKENS 不存在，首次创建")
    else:
        print(f"读取失败: HTTP {st} {str(body)[:100]}"); sys.exit(1)

    # 幂等写入（相同值 Render 不会触发部署，变化值自动 redeploy）
    st, body = api("PUT", f"/services/{svc_id}/env-vars/MINERU_TOKENS",
                   api_key, {"value": new_val})
    if st not in (200, 201):
        print(f"更新失败: HTTP {st} {str(body)[:150]}"); sys.exit(1)
    print(f"已更新 MINERU_TOKENS（{len(new_val)} 字符），Render 自动重新部署")


if __name__ == "__main__":
    main()
