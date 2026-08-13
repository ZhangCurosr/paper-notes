# -*- coding: utf-8 -*-
"""论文仓库去重：同一 source 论文出现多套目录时，保留最完整版本（paper.pdf > images > 最新），删除冗余目录。
同时重建 hub 总厂库索引（覆盖式，保证索引与仓库实际一致）。
用法: python scripts/dedup_repos.py
环境: GH_TOKEN
"""
import json, os, re, shutil, subprocess, sys, time, glob

GH_TOKEN = os.environ.get("GH_TOKEN", "")
REPOS = [
    ("ZhangCurosr/zhangcursor-papers-arxiv-cl-001", "arXiv"),
    ("ZhangCurosr/zhangcursor-papers-arxiv-ai-001", "arXiv"),
    ("ZhangCurosr/zhangcursor-papers-arxiv-cv-001", "arXiv"),
    ("ZhangCurosr/zhangcursor-papers-arxiv-lg-001", "arXiv"),
    ("ZhangCurosr/zhangcursor-papers-acl-2023-001", "ACL 2023"),
    ("ZhangCurosr/zhangcursor-papers-acl-2024-001", "ACL 2024"),
    ("ZhangCurosr/zhangcursor-papers-acl-2025-001", "ACL 2025"),
    ("ZhangCurosr/zhangcursor-papers-emnlp-2023-001", "EMNLP 2023"),
    ("ZhangCurosr/zhangcursor-papers-emnlp-2024-001", "EMNLP 2024"),
    ("ZhangCurosr/zhangcursor-papers-emnlp-2025-001", "EMNLP 2025"),
    ("ZhangCurosr/zhangcursor-papers-naacl-2022-001", "NAACL 2022"),
    ("ZhangCurosr/zhangcursor-papers-naacl-2024-001", "NAACL 2024"),
    ("ZhangCurosr/zhangcursor-papers-naacl-2025-001", "NAACL 2025"),
    ("ZhangCurosr/zhangcursor-papers-coling-2025-001", "COLING 2025"),
    ("ZhangCurosr/zhangcursor-papers-cvpr-2023-001", "CVPR 2023"),
    ("ZhangCurosr/zhangcursor-papers-cvpr-2024-001", "CVPR 2024"),
    ("ZhangCurosr/zhangcursor-papers-cvpr-2025-001", "CVPR 2025"),
    ("ZhangCurosr/zhangcursor-papers-iccv-2023-001", "ICCV 2023"),
]

def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print(f"!! {cmd[0]} 失败: {r.stderr[-400:]}")
        raise SystemExit(1)
    return r.stdout

def guess_venue(url):
    if "arxiv.org" in url:
        return "arXiv"
    m = re.search(r"/(\d{4})\.(?:findings-)?(acl|emnlp|naacl|coling)[-.]", url)
    if m:
        year, venue = m.group(1), m.group(2).upper()
        return f"{venue} {year}"
    if "thecvf.com" in url:
        m = re.search(r"(\d{4})", url)
        return f"CVPR {m.group(1)}" if m else "CVPR"
    if "mlr.press" in url:
        m = re.search(r"v(\d+)", url)
        return f"ICML {m.group(1)}" if m else "ICML"
    if "neurips.cc" in url:
        return "NeurIPS"
    if "ojs.aaai.org" in url:
        return "AAAI"
    return "Unknown"

def main():
    os.makedirs("work", exist_ok=True)
    hub_rows = []
    total_removed = 0
    for repo, default_venue in REPOS:
        name = repo.split("/")[-1]
        dst = os.path.join("work", name)
        if not os.path.exists(os.path.join(dst, ".git")):
            run(["git", "clone", f"https://x-access-token:{GH_TOKEN}@github.com/{repo}.git", dst])
        else:
            run(["git", "-C", dst, "fetch", "origin"])
            run(["git", "-C", dst, "reset", "--hard", "origin/main"])
        run(["git", "-C", dst, "config", "user.email", "ctf@4router.net"])
        run(["git", "-C", dst, "config", "user.name", "paper-notes-bot"])
        # 扫描所有论文目录（{date}/{title}_{batch8}）
        by_source = {}
        for date_dir in sorted(os.listdir(dst)):
            dpath = os.path.join(dst, date_dir)
            if not os.path.isdir(dpath) or date_dir.startswith("."):
                continue
            for d in sorted(os.listdir(dpath)):
                dp = os.path.join(dpath, d)
                if not os.path.isdir(dp):
                    continue
                meta_path = os.path.join(dp, "meta.json")
                meta = {}
                if os.path.exists(meta_path):
                    try:
                        meta = json.load(open(meta_path, encoding="utf-8"))
                    except Exception:
                        meta = {}
                src = meta.get("source") or meta.get("url") or ""
                if not src:
                    continue
                score = 0
                if os.path.exists(os.path.join(dp, "paper.pdf")):
                    score += 3
                if os.path.isdir(os.path.join(dp, "images")) and os.listdir(os.path.join(dp, "images")):
                    score += 2
                created = meta.get("created_at", "")
                by_source.setdefault(src, []).append((dp, score, created))
        # 每组保留最高分（同分取最新 created_at）
        removed_here = 0
        for src, lst in by_source.items():
            if len(lst) < 2:
                continue
            lst.sort(key=lambda x: (x[1], x[2]), reverse=True)
            keep, drops = lst[0], lst[1:]
            if keep[1] < 3:  # 没有完整版也保留最高分（不删）
                continue
            for dp, score, created in drops:
                rel = os.path.relpath(dp, dst).replace("\\", "/")
                print(f"  [{name}] 删 {rel} (score={score}) 保留 {os.path.relpath(keep[0], dst).replace(chr(92), '/')}")
                shutil.rmtree(dp)
                removed_here += 1
        if removed_here:
            run(["git", "-C", dst, "add", "-A"])
            run(["git", "-C", dst, "commit", "-m", f"dedup: remove {removed_here} redundant copies"])
            run(["git", "-C", dst, "push", "origin", "main"])
            print(f"[{name}] 已推送，删除 {removed_here} 个冗余目录")
        total_removed += removed_here
        # 收集（去重后）的 meta → hub 索引
        for date_dir in sorted(os.listdir(dst)):
            dpath = os.path.join(dst, date_dir)
            if not os.path.isdir(dpath) or date_dir.startswith("."):
                continue
            for d in sorted(os.listdir(dpath)):
                dp = os.path.join(dpath, d)
                meta_path = os.path.join(dp, "meta.json")
                if not os.path.exists(meta_path):
                    continue
                try:
                    meta = json.load(open(meta_path, encoding="utf-8"))
                except Exception:
                    continue
                src = meta.get("source") or meta.get("url") or ""
                if not src:
                    continue
                if not os.path.exists(os.path.join(dp, "paper.pdf")) and not os.path.isdir(os.path.join(dp, "images")):
                    continue  # 只收完整产物
                hub_rows.append({
                    "source": src,
                    "repo": repo,
                    "path": f"{date_dir}/{d}",
                    "title": meta.get("title", ""),
                    "venue": guess_venue(src) if meta.get("venue") in (None, "", "Unknown") else meta.get("venue"),
                    "date": date_dir,
                })
    print(f"\n去重完成：共删除 {total_removed} 个冗余目录；hub 索引 {len(hub_rows)} 条")
    # 重建 hub 索引（覆盖式）
    hub_rows.sort(key=lambda x: x["source"])
    with open(os.path.join("work", "hub_index.json"), "w", encoding="utf-8") as f:
        json.dump(hub_rows, f, ensure_ascii=False, indent=1)
    print("hub_index.json 已生成 → 由 hub_update 或手动上传")
    with open(os.path.join("work", "hub_index_count.txt"), "w") as f:
        f.write(str(len(hub_rows)))

if __name__ == "__main__":
    main()
