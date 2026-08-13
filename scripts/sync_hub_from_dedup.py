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
# 只保留完整产物条目（防 hub_update 混入不完整）
rows = [r for r in rows if r.get("path")]
put_file("index.json", json.dumps(rows, ensure_ascii=False, indent=1),
         f"rebuild index after dedup ({len(rows)})")

by_venue = collections.Counter(r.get("venue", "?") for r in rows)
by_repo = collections.Counter(r["repo"] for r in rows)
lines = ["# 论文笔记总厂库（Hub）",
         "",
         "统一索引全部论文笔记产物仓库（每篇论文一套完整产物：`paper.pdf` + `full.md` + `images/` + `meta.json`）。",
         "",
         "## 统计",
         f"- 论文总数：**{len(rows)}**", ""]
for v, n in sorted(by_venue.items(), key=lambda x: -x[1]):
    lines.append(f"- **{v}**: {n} 篇")
lines += ["", "## 产物仓库", ""]
for rp, n in sorted(by_repo.items()):
    lines.append(f"- `{rp}` — {n} 篇")
lines += ["", "## 检索", "", "全量映射见 `index.json`（url → 仓库/路径）。", ""]
put_file("README.md", "\n".join(lines), "update readme after dedup")
print(f"hub 已同步：{len(rows)} 条")
