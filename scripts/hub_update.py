#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
总厂库（Hub）更新器
====================
汇总各 job 产出的本地索引（index_*.json），更新"总仓库"：
  - index.json   全量 url → repo/path 映射（合并历史 + 本次）
  - catalog.json 来源分组统计（venue → 仓库 → 篇数）
  - README.md    自动生成导航表格（按来源/日期/仓库）

用法：
  python scripts/hub_update.py --index-dir output/ --hub-repo paper-hub \
        --gh-token ghp_xxx --owner myuser
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter


def run(cmd, env, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败: {cmd[:120]}\n{r.stderr[-400:]}")
    return r.stdout.strip()


def repo_exists(name, token):
    out = run(f"gh repo view {name} --json nameWithOwner",
              env={"GH_TOKEN": token, "PATH": os.environ["PATH"]}, check=False)
    return bool(out)


def collect_indexes(index_dir):
    """收集目录下所有 index_*.json → 合并行"""
    rows = []
    if not os.path.isdir(index_dir):
        return rows
    for fn in sorted(os.listdir(index_dir)):
        if fn.startswith("index_") and fn.endswith(".json"):
            try:
                with open(os.path.join(index_dir, fn), encoding="utf-8") as f:
                    rows.extend(json.load(f))
            except Exception as e:
                print(f"  跳过 {fn}: {e}")
    return rows


def make_readme(rows):
    """生成导航 README"""
    lines = ["# 论文库总目录（Hub）",
             "",
             f"> 自动生成于 {time.strftime('%Y-%m-%d %H:%M:%S')} · 共 {len(rows)} 篇论文已入库",
             "",
             "## 来源分布", ""]
    by_venue = Counter(r.get("venue", r["source"].split("/")[-1][:20]) for r in rows)
    for venue, n in sorted(by_venue.items(), key=lambda x: -x[1]):
        lines.append(f"- **{venue}**: {n} 篇")
    lines += ["", "## 产物仓库", ""]
    by_repo = Counter(r["repo"] for r in rows)
    for repo, n in sorted(by_repo.items()):
        lines.append(f"- `{repo}` — {n} 篇")
    lines += ["", "## 检索", "",
              "全量映射见 `index.json`（url → 仓库/路径）。", ""]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="总厂库 Hub 更新器")
    ap.add_argument("--index-dir", required=True, help="本地索引目录（含 index_*.json）")
    ap.add_argument("--hub-repo", required=True, help="总仓库名（不存在则自动创建）")
    ap.add_argument("--gh-token", required=True)
    ap.add_argument("--owner", help="GitHub 用户名（缺省从 token 推断）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = collect_indexes(args.index_dir)
    if not rows:
        print("无新索引（index_*.json 不存在）→ 跳过 Hub 更新")
        return
    owner = args.owner or "dry-run-owner" if args.dry_run else None
    if owner is None:
        owner = run("gh api user --jq .login", env={"GH_TOKEN": args.gh_token, "PATH": os.environ["PATH"]})
    print(f"本次 {len(rows)} 条索引，Hub: {owner}/{args.hub_repo}")
    if args.dry_run:
        for v, n in Counter(r.get("venue", "?") for r in rows).most_common():
            print(f"  {v}: {n} 篇")
        return

    env = {"PATH": os.environ["PATH"], "GH_TOKEN": args.gh_token,
           "GIT_TERMINAL_PROMPT": "0"}
    tmp = os.path.join(os.environ.get("TEMP", "/tmp"), f"hub_{int(time.time())}")
    os.makedirs(tmp, exist_ok=True)

    # 拉取/初始化总仓库
    if repo_exists(args.hub_repo, args.gh_token):
        run(f"git clone --depth 1 https://oauth2:{args.gh_token}@github.com/{owner}/{args.hub_repo}.git {tmp}", env=env, check=False)
        if not os.path.exists(os.path.join(tmp, ".git")):
            os.makedirs(tmp, exist_ok=True)
            run(f"git init -b main {tmp}", env=env)
    else:
        run(f"git init -b main {tmp}", env=env)

    # 合并历史索引
    old = []
    if os.path.exists(os.path.join(tmp, "index.json")):
        try:
            with open(os.path.join(tmp, "index.json"), encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            pass
    # 同 source 的新条目覆盖旧条目（仓库改名后索引跟随新仓库名）
    by_source = {r["source"]: r for r in old}
    replaced = 0
    for r in rows:
        if r["source"] in by_source:
            replaced += 1
        by_source[r["source"]] = r
    merged = list(by_source.values())
    print(f"合并: 历史 {len(old)} + 本次 {len(rows)}（覆盖 {replaced}）= {len(merged)}")

    with open(os.path.join(tmp, "index.json"), "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)

    # catalog.json（来源分组）
    catalog = {}
    for r in merged:
        venue = r.get("venue", "other")
        catalog.setdefault(venue, {})
        catalog[venue].setdefault(r["repo"], 0)
        catalog[venue][r["repo"]] += 1
    with open(os.path.join(tmp, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=1)

    # README
    with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
        f.write(make_readme(merged))

    run(f"git -C {tmp} config user.email 'mineru-bot@users.noreply.github.com'", env=env)
    run(f"git -C {tmp} config user.name 'mineru-bot'", env=env)
    run(f"git -C {tmp} add -A", env=env)
    run(f"git -C {tmp} commit -m 'hub +{len(new_rows)}' --allow-empty", env=env)
    if repo_exists(args.hub_repo, args.gh_token):
        run(f"git -C {tmp} push origin main", env=env)
    else:
        run(f"gh repo create {owner}/{args.hub_repo} --public --source={tmp} --push", env=env)
    print(f"✅ Hub 更新完成: {owner}/{args.hub_repo}（+{len(new_rows)} 篇）")


if __name__ == "__main__":
    main()
