#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 产物同步器：把 MinerU 解析产物自动 push 到 GitHub 仓库（多仓库轮换）
============================================================================
自动完成：
  1. 扫描产物目录（pool 输出的 {safe}_{batch8}/full.md 等）
  2. 按容量阈值分批，自动创建新仓库 mineru-{prefix}-{n}（gh repo create）
  3. git push 产物（markdown + layout + meta，可选压缩大文件）
  4. 维护索引仓库：{url → repo/path} 映射，便于检索

依赖：gh CLI（GitHub Actions ubuntu-latest 自带）或 git + token
用法：
  python scripts/github_sync.py --dir output/cs.CL --prefix mineru-cs.CL \
        --gh-token ghp_xxx --owner myuser --index-repo mineru-index
  python scripts/github_sync.py --dir output/cs.CL --prefix mineru-cs.CL \
        --gh-token ghp_xxx --dry-run        # 演练：只打印计划不推送
"""

import argparse
import json
import os
import subprocess
import sys
import time

# 产物中保留的文件（丢弃 images/ 大图与大 json，控制仓库体积）
KEEP_SUFFIXES = (".md", "layout.json", "content_list.json", "content_list_v2.json",
                 "meta.json")
MAX_FILES_PER_PUSH = 200      # 单次 push 文件数上限（防 commit 过大）
MAX_BYTES_DEFAULT = 500 * 1024 * 1024   # 单仓库容量阈值


def run(cmd, env=None, check=True):
    """执行命令并返回输出"""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败: {cmd[:120]}\n{r.stderr[-500:]}")
    return r.stdout.strip()


def collect(dir_path):
    """扫描产物目录 → [(source, batch_id, pages, files[])]"""
    items = []
    for entry in sorted(os.listdir(dir_path)):
        p = os.path.join(dir_path, entry)
        if not os.path.isdir(p):
            continue
        meta_f = os.path.join(p, "meta.json")
        meta = {}
        if os.path.exists(meta_f):
            try:
                with open(meta_f, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                pass
        files = []
        total = 0
        for root, _, fns in os.walk(p):
            for fn in sorted(fns):
                if fn.endswith(KEEP_SUFFIXES):
                    fp = os.path.join(root, fn)
                    rel = os.path.relpath(fp, dir_path)
                    sz = os.path.getsize(fp)
                    files.append((rel, fp))
                    total += sz
        if files:
            items.append({"source": meta.get("source", entry),
                          "batch_id": meta.get("batch_id", entry[:8]),
                          "total_bytes": total,
                          "files": files})
    return items


def get_owner(token):
    """从 token 推断 GitHub 用户名"""
    out = run(f'gh api user --jq .login', env={"GH_TOKEN": token, "PATH": os.environ["PATH"]})
    return out


def repo_exists(name, token):
    out = run(f"gh repo view {name} --json nameWithOwner",
              env={"GH_TOKEN": token, "PATH": os.environ["PATH"]}, check=False)
    return bool(out)


def push_batch(repo, owner, token, files, subdir, dry_run=False):
    """把一批文件 push 到仓库 {owner}/{repo} 的 {subdir}/ 下"""
    if dry_run:
        print(f"  [dry-run] 将 push {len(files)} 个文件 → {owner}/{repo}:{subdir}/")
        return True
    tmp = os.path.join(os.environ.get("TEMP", "/tmp"), f"sync_{repo}_{int(time.time())}")
    os.makedirs(tmp, exist_ok=True)
    env = {"PATH": os.environ["PATH"], "GH_TOKEN": token,
           "GIT_TERMINAL_PROMPT": "0"}
    try:
        if repo_exists(repo, token):
            run(f"git clone --depth 1 https://x-access-token:{token}@github.com/{owner}/{repo}.git {tmp}",
                env=env, check=False)
            if not os.path.exists(os.path.join(tmp, ".git")):
                os.makedirs(tmp, exist_ok=True)
                run(f"git init -b main {tmp}", env=env)
        else:
            os.makedirs(tmp, exist_ok=True)
            run(f"git init -b main {tmp}", env=env)
        # 复制文件到子目录
        for rel, fp in files:
            dest = os.path.join(tmp, subdir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(fp, "rb") as src, open(dest, "wb") as dst:
                dst.write(src.read())
        run(f"git -C {tmp} config user.email 'mineru-bot@users.noreply.github.com'", env=env)
        run(f"git -C {tmp} config user.name 'mineru-bot'", env=env)
        run(f"git -C {tmp} add -A", env=env)
        run(f"git -C {tmp} commit -m 'sync {subdir}: {len(files)} files' --allow-empty", env=env)
        if repo_exists(repo, token):
            run(f"git -C {tmp} push origin main", env=env, check=False)
        else:
            run(f"gh repo create {owner}/{repo} --public --source={tmp} --push",
                env=env)
        return True
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="GitHub 产物同步器（多仓库轮换）")
    ap.add_argument("--dir", required=True, help="产物目录（pool 输出根）")
    ap.add_argument("--prefix", default="mineru", help="仓库名前缀，如 mineru-cs.CL")
    ap.add_argument("--gh-token", required=True, help="GitHub PAT（repo 权限）")
    ap.add_argument("--owner", help="GitHub 用户名（缺省从 token 推断）")
    ap.add_argument("--index-repo", default="", help="索引仓库名（记录 url→路径 映射，可空=不建）")
    ap.add_argument("--max-bytes", type=int, default=MAX_BYTES_DEFAULT, help="单仓容量阈值")
    ap.add_argument("--index-out", help="把本次同步索引同时写入本地文件（供总厂库 hub 汇总）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    args = ap.parse_args()

    items = collect(args.dir)
    if not items:
        sys.exit(f"错误: {args.dir} 下没有产物")
    owner = args.owner or ("dry-run-owner" if args.dry_run else get_owner(args.gh_token))
    print(f"共 {len(items)} 篇产物，总计 {sum(i['total_bytes'] for i in items)//1024//1024} MB")

    # 按容量分批 → 仓库序号
    batches = []          # [(repo_name, subdir, files[])]
    cur, cur_bytes, cur_files, n = [], 0, 0, 1
    for it in items:
        if cur and (cur_bytes + it["total_bytes"] > args.max_bytes
                    or cur_files + len(it["files"]) > MAX_FILES_PER_PUSH):
            repo = f"{args.prefix}-{n:03d}"
            subdir = time.strftime("%Y-%m-%d")
            batches.append((repo, subdir, cur))
            n += 1
            cur, cur_bytes, cur_files = [], 0, 0
        cur.append(it)
        cur_bytes += it["total_bytes"]
        cur_files += len(it["files"])
    if cur:
        batches.append((f"{args.prefix}-{n:03d}", time.strftime("%Y-%m-%d"), cur))

    print(f"计划推送 {len(batches)} 个仓库:")
    for repo, subdir, b in batches:
        mb = sum(x["total_bytes"] for x in b) // 1024 // 1024
        print(f"  {owner}/{repo}:{subdir}  {len(b)} 篇  {mb} MB")
        if args.dry_run:
            for x in b[:3]:
                print(f"    - {x['source'][:70]}")

    if args.dry_run:
        return

    # 执行推送 + 索引
    index_rows = []
    for repo, subdir, b in batches:
        files = [f for it in b for f in it["files"]]
        ok = push_batch(repo, owner, args.gh_token, files, subdir, dry_run=False)
        if ok:
            print(f"✅ {owner}/{repo} 推送完成")
            for it in b:
                md = next((f for f in it["files"] if f[0].endswith(".md")), None)
                index_rows.append({"source": it["source"], "repo": f"{owner}/{repo}",
                                   "path": f"{subdir}/{md[0] if md else ''}",
                                   "batch_id": it["batch_id"],
                                   "synced_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        else:
            print(f"❌ {owner}/{repo} 推送失败")
        time.sleep(2)

    # 索引仓库
    if args.index_repo and index_rows:
        tmp = os.path.join(os.environ.get("TEMP", "/tmp"), "idx_" + str(int(time.time())))
        os.makedirs(tmp, exist_ok=True)
        env = {"PATH": os.environ["PATH"], "GH_TOKEN": args.gh_token,
               "GIT_TERMINAL_PROMPT": "0"}
        idx_path = os.path.join(tmp, "index.json")
        old = []
        if repo_exists(args.index_repo, args.gh_token):
            run(f"git clone --depth 1 https://oauth2:{args.gh_token}@github.com/{owner}/{args.index_repo}.git {tmp}", env=env, check=False)
            if os.path.exists(os.path.join(tmp, "index.json")):
                try:
                    with open(os.path.join(tmp, "index.json"), encoding="utf-8") as f:
                        old = json.load(f)
                except Exception:
                    pass
        old.extend(index_rows)
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False, indent=1)
        run(f"git -C {tmp} config user.email 'mineru-bot@users.noreply.github.com'", env=env)
        run(f"git -C {tmp} config user.name 'mineru-bot'", env=env)
        run(f"git -C {tmp} add -A", env=env)
        run(f"git -C {tmp} commit -m 'index +{len(index_rows)}' --allow-empty", env=env)
        if repo_exists(args.index_repo, args.gh_token):
            run(f"git -C {tmp} push origin main", env=env, check=False)
        else:
            run(f"gh repo create {owner}/{args.index_repo} --public --source={tmp} --push", env=env)
        print(f"✅ 索引更新 {len(index_rows)} 条 → {owner}/{args.index_repo}")

    # 本地索引输出（供总厂库汇总）
    if args.index_out and index_rows:
        os.makedirs(os.path.dirname(args.index_out) or ".", exist_ok=True)
        with open(args.index_out, "w", encoding="utf-8") as f:
            json.dump(index_rows, f, ensure_ascii=False, indent=1)
        print(f"✅ 本地索引 {len(index_rows)} 条 → {args.index_out}")


if __name__ == "__main__":
    main()
