#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Render token 自动同步（GitHub Actions 调用）
=============================================
本地 mineru_accounts.csv 增加 token → 重加密上传 → 本 workflow 解密对比 →
内容变化则通过 Render API 更新 MINERU_TOKENS env → 自动触发重新部署。

机制说明（Render API 实测）：
  - GET /services/{id} 不返回 env var 值（安全设计，serviceDetails.env 为 str）
  - 正确端点: PUT /v1/services/{id}/env-vars/{KEY}  body {"value": "..."}
    （不存在则创建，存在则更新，均触发 redeploy）
  - 为避免每日无谓部署：以 repo variable MINERU_TOKENS_SHA 存上次同步指纹，
    内容无变化直接跳过 PUT（不触发部署）。

依赖（workflow 注入）：
  env RENDER_API_KEY     Render API key（secrets）
  env GITHUB_TOKEN       Actions token（写指纹 variable 用；无则每次全量 PUT）
  env RENDER_SERVICE_ID  服务 ID（vars，可选；缺省按服务名自动查找）
  env MINERU_SERVICE_NAME 服务名（vars，默认 mineru-api）

用法：python .github/scripts/sync_render_tokens.py
"""
import csv
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

RENDER_API = "https://api.render.com/v1"
GH_API = "https://api.github.com"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
VAR_NAME = "MINERU_TOKENS_SHA"


def api(base, method, path, token, body=None):
    req = urllib.request.Request(
        base + path, method=method,
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
            return r.status, raw   # 非 JSON 响应体容错
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:200]}   # 非 JSON 错误体容错
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


def get_fingerprint(gh_token):
    """读 repo variable 指纹；不存在/无权限返回 None"""
    if not gh_token or not REPO:
        return None
    st, body = api(GH_API, "GET", f"/repos/{REPO}/actions/variables/{VAR_NAME}", gh_token)
    if st == 200 and isinstance(body, dict):
        return body.get("value")
    return None


def set_fingerprint(gh_token, value):
    """写/更新 repo variable 指纹"""
    if not gh_token or not REPO:
        print("  [warn] 无 GITHUB_TOKEN/REPO，指纹未写入")
        return
    st, body = api(GH_API, "GET", f"/repos/{REPO}/actions/variables/{VAR_NAME}", gh_token)
    if st == 200 and isinstance(body, dict):
        st2, body2 = api(GH_API, "PATCH", f"/repos/{REPO}/actions/variables/{VAR_NAME}",
                         gh_token, {"value": value})
        print(f"  指纹 PATCH: HTTP {st2}")
    else:
        st2, body2 = api(GH_API, "POST", f"/repos/{REPO}/actions/variables",
                         gh_token, {"name": VAR_NAME, "value": value})
        print(f"  指纹 POST: HTTP {st2} {str(body2)[:100]}")


def main():
    api_key = os.environ.get("RENDER_API_KEY", "")
    if not api_key:
        print("缺少 RENDER_API_KEY"); sys.exit(1)
    gh_token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    svc_id = os.environ.get("RENDER_SERVICE_ID", "")
    svc_name = os.environ.get("MINERU_SERVICE_NAME", "mineru-api")

    toks = load_tokens()
    if not toks:
        print("CSV 中无有效 token"); sys.exit(1)
    new_val = ",".join(toks)
    print(f"本地 token 数: {len(toks)}（{len(new_val)} 字符）")

    # 定位服务
    if not svc_id:
        st, body = api(RENDER_API, "GET", "/services", api_key)
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

    # 指纹对比：无变化则不 PUT（不触发 redeploy）
    new_sha = hashlib.sha256(new_val.encode()).hexdigest()
    prev = get_fingerprint(gh_token)
    if prev == new_sha:
        print("内容无变化，跳过（不触发部署）")
        return
    if prev:
        print(f"检测到变化: 指纹 {prev[:12]} → {new_sha[:12]}")
    else:
        print(f"首次同步（指纹 {new_sha[:12]}）")

    # 幂等写入（不存在则创建，存在则更新）
    st, body = api(RENDER_API, "PUT", f"/services/{svc_id}/env-vars/MINERU_TOKENS",
                   api_key, {"value": new_val})
    if st not in (200, 201):
        print(f"更新 MINERU_TOKENS 失败: HTTP {st} {str(body)[:150]}")
        sys.exit(1)
    print(f"已更新 MINERU_TOKENS（{len(new_val)} 字符），Render 自动重新部署")
    set_fingerprint(gh_token, new_sha)
    print(f"指纹已记录 {new_sha[:12]}（下次内容无变化将跳过）")


if __name__ == "__main__":
    main()
