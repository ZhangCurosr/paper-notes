# -*- coding: utf-8 -*-
"""去重后自动同步 hub 总厂库（index.json + README.md 覆盖式更新）。
用法: python scripts/sync_hub_from_dedup.py work/hub_index.json
环境: GH_TOKEN
"""
import base64, collections, json, os, subprocess, sys, time, urllib.request

GH_TOKEN = os.environ.get("GH_TOKEN", "")
INDEX = sys.argv[1] if len(sys.argv) > 1 else "work/hub_index.json"
HUB = "ZhangCurosr/zhangcursor-hub"


def api(url, method="GET", body=None, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, method=method,
                data=json.dumps(body).encode() if body else None,
                headers={"Authorization": f"Bearer {GH_TOKEN}", "User-Agent": "x",
                         "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=120).read())
        except Exception as e:
            print(f"  retry {i + 1}: {type(e).__name__} {e}")
            time.sleep(4)
    raise RuntimeError(url)


def put_file(path, content, msg):
    d = api(f"https://api.github.com/repos/{HUB}/contents/{path}")
    api(f"https://api.github.com/repos/{HUB}/contents/{path}", "PUT",
        {"message": msg,
         "content": base64.b64encode(content.encode()).decode(),
         "branch": "main", "sha": d["sha"]})


rows = json.load(open(INDEX, encoding="utf-8"))
rows.sort(key=lambda x: x["source"])
rows = [r for r in rows if r.get("path")]
put_file("index.json", json.dumps(rows, ensure_ascii=False, indent=1),
         f"rebuild index after dedup ({len(rows)})")

by_venue = collections.Counter(r.get("venue", "?") for r in rows)
by_repo = collections.Counter(r["repo"] for r in rows)
total = len(rows)
repo_table = "\n".join(
    f"| [`{rp}`](https://github.com/{rp}) | {n} |" for rp, n in sorted(by_repo.items()))
venue_lines = "\n".join(f"- **{v}**：{n} 篇" for v, n in sorted(by_venue.items(), key=lambda x: -x[1]))
NL = "\n"
lines = [
    "# 🗂️ zhangcursor-hub — 论文笔记总厂库",
    "",
    "**全部论文笔记的统一索引**：收录 arXiv 与四大顶会（ACL / EMNLP / NAACL / CVPR）论文的完整解析产物，一份索引查遍所有论文仓库。",
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
put_file("README.md", NL.join(lines), "update readme after dedup")
print(f"hub 已同步：{len(rows)} 条")
