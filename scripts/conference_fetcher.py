#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学术会议论文批量抓取器（CVPR/ICML/NeurIPS/AAAI/ACL）
=====================================================
从各会议官方免费 PDF 直链站点抓取论文清单，输出与 arxiv_fetcher 相同格式：
  JSON（title/authors/year/venue/pdf_url/abs_url）+ _urls.txt

站点（全部官方免费 PDF）：
  - icml{YYYY}    proceedings.mlr.press/v{卷号}/
  - cvpr{YYYY}    openaccess.thecvf.com/CVPR{YYYY}
  - neurips{YYYY} proceedings.neurips.cc/paper_files/paper/{YYYY}
  - aaai{YYYY}    ojs.aaai.org（OJS 两层：archive → issue TOC）
  - acl{YYYY}     aclanthology.org/events/acl-{YYYY}/（acl/emnlp/naacl/coling 同族）

用法：
  python scripts/conference_fetcher.py --venue icml2024 --max 200 --out logs/conf_icml2024.json
  python scripts/conference_fetcher.py --venue cvpr2024 --venue acl2024 --max 300
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


# ─────────────────────────── 各站点适配器 ───────────────────────────

def fetch_icml(year, max_n):
    vol = ICML_VOL[year]
    base = f"https://proceedings.mlr.press/v{vol}/"
    page = http_get(base)
    # 论文 id：v235/smith24a.html 或 smith24a.html
    ids = list(dict.fromkeys(links(page, r'v\d+/([a-z0-9]+)\.html')))
    papers = []
    for pid in ids[:max_n]:
        papers.append({
            "title": pid, "authors": [], "year": year, "venue": f"ICML {year}",
            "abs_url": f"{base}{pid}.html",
            "pdf_url": f"{base}{pid}/{pid}.pdf",   # mlr 规则路径
            "published": str(year),
        })
    return papers


def fetch_cvpr(year, max_n):
    base = f"https://openaccess.thecvf.com/CVPR{year}"
    page = http_get(base)
    # 2024 起：/content/CVPR2024/papers/xxx_CVPR_2024_paper.pdf；2023 前小写
    pat = r'(content/CVPR\d+/papers/[^"\']+_paper\.pdf|content_cvpr_\d+/papers/[^"\']+\.pdf)'
    pairs = re.findall(r'<dt class="ptitle"><br><a href="([^"]+)">(.*?)</a>', page)
    if not pairs:
        days = list(dict.fromkeys(links(page, r'(\?day=[^"\']+)'))) or ["?day=all"]
        for d in days:
            sub = http_get(f"{base}{d}")
            pairs += re.findall(r'<dt class="ptitle"><br><a href="([^"]+)">(.*?)</a>', sub)
    if not pairs:
        pdfs = links(page, pat)
        return [{"title": p.split("/")[-1][:60], "authors": [], "year": year,
                 "venue": f"CVPR {year}", "abs_url": f"https://openaccess.thecvf.com/{p}",
                 "pdf_url": f"https://openaccess.thecvf.com/{p}", "published": str(year)}
                for p in pdfs[:max_n]]
    papers = []
    for href, title in pairs:
        # html 详情页 → pdf 规则转换（/html/xxx.html → /papers/xxx.pdf）
        if href.endswith(".html"):
            pdf = href.replace("/html/", "/papers/").replace(".html", ".pdf")
        elif href.endswith(".pdf"):
            pdf = href
        else:
            continue
        papers.append({
            "title": html.unescape(title).strip(), "authors": [], "year": year,
            "venue": f"CVPR {year}",
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
    # hash/Title-Abstract-Conference.html → pdf: hash/Title-Paper-Conference.pdf
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
    # 从新到旧找匹配卷号的 issue（AAAI 2024 = Vol. 38 / AAAI-38）
    target = None
    for iss in issues[:20]:
        page = http_get(abs_url(base, iss), timeout=60)
        if f"AAAI-{vol}" in page or f"Vol. {vol}" in page:
            target = iss
            break
        time.sleep(3)     # 反爬友好（AAAI OJS WAF 敏感）
    if not target:
        return []
    toc = http_get(abs_url(base, target), timeout=60)
    # OJS galley：article/view/{aid}/{gid}（绝对 URL，直接返回 PDF）
    galleys = list(dict.fromkeys(links(toc, r'(https://ojs\.aaai\.org[^"\']*article/view/\d+/\d+)')))
    if not galleys:
        galleys = list(dict.fromkeys(links(toc, r'(article/view/\d+/\d+)')))
        galleys = [abs_url(base, g) for g in galleys]
    # 标题按 article id 配对
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
def fetch_acl(venue, year, max_n):
    """aclanthology 全家族：acl/emnlp/naacl/coling（pdf 直链规则统一）"""
    base = f"https://aclanthology.org/events/{venue}-{year}/"
    page = http_get(base)
    # id 模式：2024.acl-long.5 / 2024.emnlp-main.5 / 2024.findings-acl.5 ...
    pat = rf'(\d{{4}}\.(?:{venue}-(?:main|long|short)|findings-{venue})\.\d+)'
    pids = list(dict.fromkeys(links(page, pat)))
    papers = []
    for pid in pids[:max_n]:
        papers.append({
            "title": pid, "authors": [], "year": year,
            "venue": f"{venue.upper()} {year}",
            "abs_url": f"https://aclanthology.org/{pid}/",
            "pdf_url": f"https://aclanthology.org/{pid}.pdf",
            "published": str(year),
        })
    return papers


FETCHERS = {"icml": fetch_icml, "cvpr": fetch_cvpr,
            "neurips": fetch_neurips, "aaai": fetch_aaai,
            "acl": lambda y, n: fetch_acl("acl", y, n),
            "emnlp": lambda y, n: fetch_acl("emnlp", y, n),
            "naacl": lambda y, n: fetch_acl("naacl", y, n),
            "coling": lambda y, n: fetch_acl("coling", y, n)}


def main():
    ap = argparse.ArgumentParser(description="学术会议论文抓取器")
    ap.add_argument("--venue", action="append", required=True,
                    help="会议+年份，如 icml2024 / cvpr2024 / neurips2024 / aaai2024 / acl2024（可多次）")
    ap.add_argument("--max", type=int, default=300, help="每 venue 最多论文数")
    ap.add_argument("--out", default="logs/conf_batch.json", help="输出 JSON")
    ap.add_argument("--only-urls", action="store_true", help="仅输出 URL 清单")
    args = ap.parse_args()

    all_papers = []
    for v in args.venue:
        m = re.match(r"([a-z]+)(\d{4})", v.lower())
        if not m or m.group(1) not in FETCHERS:
            print(f"跳过未知 venue: {v}（支持 icml/cvpr/neurips/aaai/acl）")
            continue
        name, year = m.group(1), int(m.group(2))
        try:
            print(f"=== 抓取 {v} ...", flush=True)
            papers = FETCHERS[name](year, args.max)
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
