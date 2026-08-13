#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学术会议论文批量抓取器（CVPR/ICCV/ACL/EMNLP/NAACL/COLING + icml/neurips/aaai）
=====================================================
从各会议官方免费 PDF 直链站点抓取论文清单，输出与 arxiv_fetcher 相同格式：
  JSON（title/authors/year/venue/pdf_url/abs_url）+ _urls.txt

站点（全部官方免费 PDF）：
  - cvpr{YYYY}/iccv{YYYY}  openaccess.thecvf.com/{ACRO}{YYYY}
  - acl{YYYY}     aclanthology.org/events/{venue}-{YYYY}/（acl/emnlp/naacl/coling 同族）
  - icml/neurips/aaai：仅保留代码，实际部署源以 MinerU 可达性为准

用法：
  python scripts/conference_fetcher.py --venue acl2025 --max 200 --out logs/conf_acl2025.json
  python scripts/conference_fetcher.py --venue cvpr2025 --venue iccv2023 --max 300
  python scripts/conference_fetcher.py --venue acl2025 --skip-existing   # 跳过 hub 已收录
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

HUB_INDEX = "https://raw.githubusercontent.com/ZhangCurosr/zhangcursor-hub/main/index.json"

# ICML 年份 → mlr.press 卷号
ICML_VOL = {2020: 119, 2021: 139, 2022: 162, 2023: 202, 2024: 235, 2025: 267}
# AAAI 年份 → 卷号（AAAI-34=2020 ...）
AAAI_VOL = {2020: 34, 2021: 35, 2022: 36, 2023: 37, 2024: 38, 2025: 39}


def http_get(url, timeout=30, retries=4):
    """GET + 重试（自动处理 gzip 压缩响应；404/429 长退避）"""
    import gzip
    import io
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                        "Accept-Encoding": "gzip"})
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if i == retries - 1:
                raise
            time.sleep(5 * (i + 2))     # 反爬/WAF 冷却
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(3 * (i + 1))
    return ""


def links(html_text, pattern):
    """提取 html 中匹配 pattern 的 href 链接（绝对化由调用方处理）"""
    return re.findall(pattern, html_text)


def abs_url(base, href):
    return urllib.parse.urljoin(base, href)


def load_existing_sources():
    """拉取 hub index.json，返回已收录 source（pdf_url）集合。失败返回空集（全量抓取）。"""
    try:
        req = urllib.request.Request(HUB_INDEX, headers={"User-Agent": UA})
        rows = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return {r.get("source") for r in rows if r.get("source")}
    except Exception:
        print("警告: hub index 拉取失败，本次全量抓取", flush=True)
        return set()


# ─────────────────────────── 各站点适配器 ───────────────────────────

def fetch_icml(year, max_n):
    vol = ICML_VOL[year]
    base = f"https://proceedings.mlr.press/v{vol}/"
    page = http_get(base)
    ids = list(dict.fromkeys(links(page, r'v\d+/([a-z0-9]+)\.html')))
    papers = []
    for pid in ids[:max_n]:
        papers.append({
            "title": pid, "authors": [], "year": year, "venue": f"ICML {year}",
            "abs_url": f"{base}{pid}.html",
            "pdf_url": f"{base}{pid}/{pid}.pdf",
            "published": str(year),
        })
    return papers


def fetch_cvpr(year, max_n, acro="CVPR"):
    """thecvf 全家族：cvpr/iccv（页面结构统一）"""
    base = f"https://openaccess.thecvf.com/{acro}{year}"
    page = http_get(base)
    pat = rf'(content/{acro}\d+/papers/[^"\']+_paper\.pdf|content_{acro.lower()}_\d+/papers/[^"\']+\.pdf)'
    pairs = re.findall(r'<dt class="ptitle"><br><a href="([^"]+)">(.*?)</a>', page)
    if not pairs:
        days = list(dict.fromkeys(links(page, r'(\?day=[^"\']+)'))) or ["?day=all"]
        for d in days:
            sub = http_get(f"{base}{d}")
            pairs += re.findall(r'<dt class="ptitle"><br><a href="([^"]+)">(.*?)</a>', sub)
    if not pairs:
        pdfs = links(page, pat)
        return [{"title": p.split("/")[-1][:60], "authors": [], "year": year,
                 "venue": f"{acro.upper()} {year}", "abs_url": f"https://openaccess.thecvf.com/{p}",
                 "pdf_url": f"https://openaccess.thecvf.com/{p}", "published": str(year)}
                for p in pdfs[:max_n]]
    papers = []
    for href, title in pairs:
        if href.endswith(".html"):
            pdf = href.replace("/html/", "/papers/").replace(".html", ".pdf")
        elif href.endswith(".pdf"):
            pdf = href
        else:
            continue
        pdf = pdf.lstrip("/")
        papers.append({
            "title": html.unescape(title).strip(), "authors": [], "year": year,
            "venue": f"{acro.upper()} {year}",
            "abs_url": f"https://openaccess.thecvf.com/{pdf}",
            "pdf_url": f"https://openaccess.thecvf.com/{pdf}",
            "published": str(year),
        })
        if len(papers) >= max_n:
            break
    return papers


def fetch_neurips(year, max_n):
    base = f"https://proceedings.neurips.cc/paper_files/paper/{year}"
    page = http_get(base)
    abs_pages = list(dict.fromkeys(links(page, r'(hash/[^"\']+-Abstract-Conference\.html)')))
    papers = []
    for a in abs_pages[:max_n]:
        pdf = a.replace("-Abstract-Conference.html", "-Paper-Conference.pdf")
        papers.append({
            "title": a.split("/")[-1].replace("-Abstract-Conference.html", "").replace("-", " "),
            "authors": [], "year": year, "venue": f"NeurIPS {year}",
            "abs_url": f"{base}/{a}", "pdf_url": f"{base}/{pdf}",
            "published": str(year),
        })
    return papers


def fetch_aaai(year, max_n):
    vol = AAAI_VOL[year]
    base = "https://ojs.aaai.org/index.php/AAAI"
    archive = http_get(f"{base}/issue/archive", timeout=60)
    issues = list(dict.fromkeys(links(archive, r'(issue/view/\d+)')))
    if not issues:
        return []
    target = None
    for iss in issues[:20]:
        page = http_get(abs_url(base, iss), timeout=60)
        if f"AAAI-{vol}" in page or f"Vol. {vol}" in page:
            target = iss
            break
        time.sleep(3)
    if not target:
        return []
    toc = http_get(abs_url(base, target), timeout=60)
    galleys = list(dict.fromkeys(links(toc, r'(https://ojs\.aaai\.org[^"\']*article/view/\d+/\d+)')))
    if not galleys:
        galleys = list(dict.fromkeys(links(toc, r'(article/view/\d+/\d+)')))
        galleys = [abs_url(base, g) for g in galleys]
    title_map = {}
    for aid, t in re.findall(r'article/view/(\d+)"[^>]*>(.*?)</a>', toc, re.S):
        title_map[aid] = html.unescape(re.sub(r"\s+", " ", t)).strip()
    papers = []
    for g in galleys[:max_n]:
        aid = re.search(r'article/view/(\d+)/', g)
        title = title_map.get(aid.group(1), f"aaai{year}-{len(papers)}") if aid else f"aaai{year}-{len(papers)}"
        papers.append({
            "title": title, "authors": [], "year": year, "venue": f"AAAI {year}",
            "abs_url": abs_url(base, target), "pdf_url": g,
            "published": str(year),
        })
    return papers


def _clean_acl_title(t):
    """清洗 aclanthology 标题：去 HTML 标签（官方标题为 sentence case 存储）"""
    t = re.sub(r'<[^>]+>', '', t)
    return html.unescape(re.sub(r"\s+", " ", t)).strip()


def fetch_acl(venue, year, max_n):
    """aclanthology 全家族：acl/emnlp/naacl/coling（pdf 直链规则统一）。
    事件页含完整论文列表（标题 + pdf/bib 链接），一次拉取全部。"""
    base = f"https://aclanthology.org/events/{venue}-{year}/"
    page = http_get(base, timeout=90)
    # id 模式：2025.acl-long.5 / 2025.coling-main.5 / 2025.findings-acl.5 ...
    pat = rf'(\d{{4}}\.(?:{venue}-(?:main|long|short|industry|demo|system|student)|findings-{venue}|{venue}-findings)\.\d+)'
    pids = [p for p in dict.fromkeys(links(page, pat)) if not p.endswith(".0")]
    # 标题链接定位：href=/2025.acl-long.5/> 标题文本</a>（pid 后直接 / 的链接唯一是标题链接）
    title_map = {}
    for m in re.finditer(r'href=/(\d{4}\.[a-z-]+\.\d+)/>', page):
        pid = m.group(1)
        if pid.endswith(".0"):
            continue
        end = page.find("</a>", m.end())
        if end < 0:
            continue
        t = _clean_acl_title(page[m.end():end])
        if t:
            title_map[pid] = t
    papers = []
    for pid in pids[:max_n]:
        papers.append({
            "title": title_map.get(pid, pid), "authors": [], "year": year,
            "venue": f"{venue.upper()} {year}",
            "abs_url": f"https://aclanthology.org/{pid}/",
            "pdf_url": f"https://aclanthology.org/{pid}.pdf",
            "published": str(year),
        })
    return papers


FETCHERS = {"icml": fetch_icml,
            "cvpr": lambda y, n: fetch_cvpr(y, n, "CVPR"),
            "iccv": lambda y, n: fetch_cvpr(y, n, "ICCV"),
            "neurips": fetch_neurips, "aaai": fetch_aaai,
            "acl": lambda y, n: fetch_acl("acl", y, n),
            "emnlp": lambda y, n: fetch_acl("emnlp", y, n),
            "naacl": lambda y, n: fetch_acl("naacl", y, n),
            "coling": lambda y, n: fetch_acl("coling", y, n)}


def main():
    ap = argparse.ArgumentParser(description="学术会议论文抓取器")
    ap.add_argument("--venue", action="append", required=True,
                    help="会议+年份，如 acl2025 / emnlp2024 / naacl2022 / coling2025 / cvpr2025 / iccv2023（可多次）")
    ap.add_argument("--max", type=int, default=300, help="每 venue 最多论文数")
    ap.add_argument("--out", default="logs/conf_batch.json", help="输出 JSON")
    ap.add_argument("--only-urls", action="store_true", help="仅输出 URL 清单")
    ap.add_argument("--skip-existing", action="store_true",
                    help="跳过 hub 总厂库已收录的论文（增量模式）")
    args = ap.parse_args()

    existing = load_existing_sources() if args.skip_existing else set()
    all_papers = []
    for v in args.venue:
        m = re.match(r"([a-z]+)(\d{4})", v.lower())
        if not m or m.group(1) not in FETCHERS:
            print(f"跳过未知 venue: {v}（支持 acl/emnlp/naacl/coling/cvpr/iccv/icml/neurips/aaai）")
            continue
        name, year = m.group(1), int(m.group(2))
        try:
            print(f"=== 抓取 {v} ...", flush=True)
            papers = FETCHERS[name](year, args.max)
            if existing:
                before = len(papers)
                papers = [p for p in papers if p["pdf_url"] not in existing]
                print(f"{v}: 抓取 {before} 篇，增量 {len(papers)} 篇（已收录 {before - len(papers)}）", flush=True)
            else:
                print(f"{v}: {len(papers)} 篇", flush=True)
            all_papers.extend(papers)
        except Exception as e:
            print(f"{v} 抓取失败: {str(e)[:100]}", flush=True)

    # 去重
    seen, dedup = set(), []
    for p in all_papers:
        if p["pdf_url"] and p["pdf_url"] not in seen:
            seen.add(p["pdf_url"])
            dedup.append(p)
    print(f"总计: {len(dedup)} 篇")
    if args.only_urls:
        for p in dedup:
            print(p["pdf_url"])
        return
    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(dedup, f, ensure_ascii=False, indent=1)
    print(f"元数据 → {args.out}")
    urls_path = args.out.replace(".json", "_urls.txt")
    with open(urls_path, "w", encoding="utf-8") as f:
        f.write("\n".join(p["pdf_url"] for p in dedup) + "\n")
    print(f"URL 清单 → {urls_path}")


if __name__ == "__main__":
    main()
