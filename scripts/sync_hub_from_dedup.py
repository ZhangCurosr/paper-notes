# -*- coding: utf-8 -*-
"""去重后自动同步 hub 总厂库（index.json + README.md 覆盖式更新，git push 方式）。
用法: python scripts/sync_hub_from_dedup.py work/hub_index.json
环境: GH_TOKEN
"""
import json, os, subprocess, sys, time
from collections import Counter

GH_TOKEN = os.environ.get("GH_TOKEN", "")
INDEX = sys.argv[1] if len(sys.argv) > 1 else "work/hub_index.json"
HUB = "ZhangCurosr/zhangcursor-hub"


def run(cmd, check=True):
    env = {"PATH": os.environ["PATH"], "GH_TOKEN": GH_TOKEN, "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败: {cmd[:140]}\n{r.stderr[-400:]}")
    return r.stdout.strip()


def main():
    rows = json.load(open(INDEX, encoding="utf-8"))
    rows.sort(key=lambda x: x["source"])
    rows = [r for r in rows if r.get("path")]
    total = len(rows)
    print(f"hub 同步: {total} 条")

    tmp = os.path.join(os.environ.get("TEMP", "/tmp"), f"hub_sync_{int(time.time())}")
    os.makedirs(tmp, exist_ok=True)
    run(f"git clone --depth 1 https://oauth2:{GH_TOKEN}@github.com/{HUB}.git {tmp}")

    with open(os.path.join(tmp, "index.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    by_venue = Counter(r.get("venue", "?") for r in rows)
    by_repo = Counter(r["repo"] for r in rows)
    repo_table = "\n".join(
        f"| [`{rp}`](https://github.com/{rp}) | {n} |" for rp, n in sorted(by_repo.items()))
    venue_lines = "\n".join(f"- **{v}**：{n} 篇" for v, n in sorted(by_venue.items(), key=lambda x: -x[1]))
    NL = "\n"
    lines = [
        "# 🗂️ zhangcursor-hub — 论文笔记总厂库",
        "",
        "**全部论文笔记的统一索引**：收录 arXiv 与各大学术会议（ACL / EMNLP / NAACL / COLING / CVPR / ICCV）论文的完整解析产物，一份索引查遍所有论文仓库。",
        "",
        "<!-- 胶囊徽章带 -->",
        f"![Papers](https://img.shields.io/badge/Papers-{total}-brightgreen?style=flat-square)",
        f"![Repos](https://img.shields.io/badge/Repos-{len(by_repo)}-blue?style=flat-square)",
        "![Last commit](https://img.shields.io/github/last-commit/ZhangCurosr/zhangcursor-hub?style=flat-square)",
        "![Pipeline](https://img.shields.io/github/actions/workflow/status/ZhangCurosr/paper-notes/mineru_batch.yml?label=daily%20pipeline&style=flat-square)",
        "![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)",
        "",
        "## 这是什么",
        "",
        "- **统一索引**：全部论文产物仓库的 URL → 仓库/路径 映射，一份 JSON 查遍所有论文",
        "- **全自动**：每天 02:00 / 14:00 UTC 由云端流水线抓取新论文并更新本库",
        "- **去重保障**：每篇论文只保留一套完整产物（`paper.pdf` + `full.md` + `images/` + `meta.json`），冗余目录定期自动清理",
        "",
        "## 统计",
        "",
        f"- 论文总数：**{total}**",
        "",
        venue_lines,
        "",
        "## 产物仓库",
        "",
        "| 仓库 | 论文数 |",
        "|---|---|",
        repo_table,
        "",
        "## 索引格式（index.json）",
        "",
        "```json",
        "{",
        '  "source": "https://arxiv.org/pdf/xxxx.pdf",',
        '  "repo": "ZhangCurosr/zhangcursor-papers-arxiv-cl-001",',
        '  "path": "2026-08-13/Paper-Title_abc12345",',
        '  "title": "Paper Title",',
        '  "venue": "arXiv",',
        '  "date": "2026-08-13"',
        "}",
        "```",
        "",
        "## 怎么用",
        "",
        "```python",
        "# 读 index.json 按标题 / venue / 来源检索",
        'idx = json.load(open("index.json"))',
        'hits = [r for r in idx if "attention" in r["title"].lower()]',
        "```",
        "",
        "## 更新机制",
        "",
        "- 每日流水线（`paper-notes` 仓库 Actions）自动追加新论文",
        "- “Dedup Repos” workflow 一键去重并重建本索引（覆盖式，保证索引与仓库实际一致）",
        "",
        "## Limits",
        "",
        "- 索引仅收录**完整产物**（有 PDF 且已解析）的论文",
        "- 论文全文请到对应产物仓库取用，本库不存原文",
        "- 各仓库容量上限 500MB，超限自动开新仓（`-002`），索引会跟随归一",
        "",
        "## License",
        "",
        "MIT",
        "",
    ]
    with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
        f.write(NL.join(lines))

    run(f"git -C {tmp} config user.email 'ctf@4router.net'")
    run(f"git -C {tmp} config user.name 'paper-notes-bot'")
    run(f"git -C {tmp} add -A")
    run(f"git -C {tmp} commit -m 'rebuild index after dedup ({total})' --allow-empty")
    run(f"git -C {tmp} push origin main")
    print(f"hub 已同步：{total} 条（git push）")


if __name__ == "__main__":
    main()
