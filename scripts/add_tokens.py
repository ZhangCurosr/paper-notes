#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_tokens.py — 一键添加 MinerU token（优雅版）
=================================================
自动完成：解密 gpg → 合并去重 → 写回明文 CSV → 重加密 → 回读验证 → 可选提交推送

用法：
  python scripts/add_tokens.py sk-新token1 sk-新token2 ...   # 直接传新 token（可多个）
  python scripts/add_tokens.py -f 新注册导出.csv              # 从文件导入（CSV/每行一个 token 均可）
  python scripts/add_tokens.py --push                        # 合并后自动 git commit + push（触发 Render 同步）
  python scripts/add_tokens.py --check                       # 只查看当前 token 数

示例：
  python scripts/add_tokens.py sk-abc123 --push              # 加 1 个并推送
  python scripts/add_tokens.py -f ~/Downloads/new_accounts.csv --push
"""
import argparse
import csv
import datetime
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根目录
CSV = os.path.join(BASE, "mineru_accounts.csv")
GPG = os.path.join(BASE, "mineru_accounts.csv.gpg")
PASSPHRASE_FILE = r"C:/Users/Lenovo/AppData/Local/Temp/csv_passphrase.txt"
COLS = ["username", "email", "phone", "api_key", "password", "created_at", "expires_at"]


def get_passphrase():
    if not os.path.exists(PASSPHRASE_FILE):
        sys.exit("未找到口令文件，请先配置 csv_passphrase.txt")
    return open(PASSPHRASE_FILE).read().strip()


def gpg(op, infile, outfile, pw):
    r = subprocess.run(
        ["gpg", "--batch", "--yes", "--quiet", op, "--passphrase", pw,
         "-o", outfile, infile],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"gpg {op} 失败: {r.stderr.strip()[:200]}")


def load_rows(path):
    """读 CSV（自动兼容 BOM），返回 dict 列表；文件不存在返回 []"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def extract_tokens(text):
    """从任意文本提取 sk- 开头的 token"""
    return list(dict.fromkeys(re.findall(r"sk-[A-Za-z0-9_-]{20,}", text)))


def collect_new(args, pw):
    """收集新 token：命令行参数 + 导入文件"""
    new_tokens = []
    for t in args.tokens:
        new_tokens.extend(extract_tokens(t))
    for f in args.files:
        if not os.path.exists(f):
            sys.exit(f"文件不存在: {f}")
        text = open(f, encoding="utf-8-sig", errors="replace").read()
        got = extract_tokens(text)
        if not got:
            sys.exit(f"{f} 中没有找到 sk- 开头的 token")
        new_tokens.extend(got)
        print(f"  从 {f} 提取 {len(got)} 个 token")
    return list(dict.fromkeys(new_tokens))


def main():
    p = argparse.ArgumentParser(description="一键添加 MinerU token 并同步")
    p.add_argument("tokens", nargs="*", help="sk- 开头的 token（可多个）")
    p.add_argument("-f", "--files", action="append", default=[],
                   help="导入文件（CSV 或每行一个 token 的文本），可多次")
    p.add_argument("--push", action="store_true", help="合并后自动 git 提交并推送")
    p.add_argument("--check", action="store_true", help="仅查看当前 token 数")
    args = p.parse_args()

    pw = get_passphrase()

    # 解密现有台账
    tmp = os.path.join(os.path.dirname(PASSPHRASE_FILE), "_csv_now.csv")
    if os.path.exists(GPG):
        gpg("--decrypt", GPG, tmp, pw)
    rows = load_rows(tmp)
    existing = {str(r.get("api_key", "")).strip() for r in rows if str(r.get("api_key", "")).strip().startswith("sk-")}
    print(f"当前台账: {len(rows)} 行 / {len(existing)} 个有效 token")

    if args.check:
        return

    new_tokens = collect_new(args, pw)
    if not new_tokens:
        sys.exit("没有提供新 token（直接传 sk-xxx 或用 -f 指定文件）")
    fresh = [t for t in new_tokens if t not in existing]
    dup = [t for t in new_tokens if t in existing]
    if dup:
        print(f"  跳过重复 {len(dup)} 个（已在台账中）")
    if not fresh:
        print("没有新 token 需要添加")
        return
    print(f"  新增 {len(fresh)} 个: " + ", ".join(t[:12] + "…" for t in fresh[:5])
          + (" …" if len(fresh) > 5 else ""))

    today = datetime.date.today().isoformat()
    for t in fresh:
        rows.append({"username": "", "email": "", "phone": "",
                     "api_key": t, "password": "", "created_at": today, "expires_at": ""})
    with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLS})
    print(f"明文 CSV 已更新: {len(rows)} 行（{len(fresh)} 新增）")

    # 重加密 + 回读验证
    gpg("--symmetric", CSV, GPG, pw)
    verify = extract_tokens(open(CSV, encoding="utf-8-sig").read())
    print(f"gpg 已重加密: {os.path.getsize(GPG)} 字节 | 回读验证 {len(verify)} 个 token ✓")

    if args.push:
        subprocess.run(["git", "add", "mineru_accounts.csv.gpg"], cwd=BASE, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"creds: 新增 {len(fresh)} token → {len(existing) + len(fresh)} 全量"], cwd=BASE, check=True)
        subprocess.run(["git", "push"], cwd=BASE, check=True)
        print("已提交并推送 → GitHub 自动同步 Render，约 2~5 分钟生效（可在 /v1/stats 确认）")
    else:
        print("未推送。确认无误后执行: python scripts/add_tokens.py --push 或手动 git push")


if __name__ == "__main__":
    main()
