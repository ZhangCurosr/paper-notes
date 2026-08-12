#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arXiv 论文批量采集器
====================
通过 arXiv API（免费、无 key）拉取论文元数据，生成 MinerU 解析任务 URL 清单。
支持按分类/日期范围/关键词筛选，输出：
  1) URL 清单文件（喂给 mineru_api_client / mineru_api_pool）
  2) JSON 元数据索引（标题/作者/摘要 → 便于后续建知识库）

用法：
  # 最近 3 天的 cs.CL 论文
  python scripts/arxiv_fetcher.py --category cs.CL --days 3 --out logs/arxiv_batch.json

  # 指定时间范围 + 关键词
  python scripts/arxiv_fetcher.py --category cs.AI --from 2026-08-01 --to 2026-08-12 \
         --query "LLM OR agent" --max 2000 --out logs/arxiv_batch.json

  # 与调度池联动
  python scripts/arxiv_fetcher.py --category cs.CL --days 1 --out logs/arxiv_batch.json
  python scripts/arxiv_fetcher.py --to-urls logs/arxiv_urls.txt   # 提取 URL 清单
  python scripts/mineru_api_client.py --key sk-xxx --url-file logs/arxiv_urls.txt --out ./papers
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

ARXIV_API = "http://export.arxiv.org/api/query"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def fetch_arxiv(category, date_from, date_to, query, max_results, batch=100):
    """拉取 arXiv 论文元数据（分页）。返回 list[dict]"""
    results = []
    start = 0
    while start < max_results:
        q_parts = []
        if category:
            q_parts.append(f"cat:{category}")
        if query:
            q_parts.append(f"({query})")
        # 按提交日期过滤
        date_q = f"submittedDate:[{date_from}0000 TO {date_to}2359]"
        q_parts.append(date_q)
        params = {
            "search_query": " AND ".join(q_parts),
            "start": start,
            "max_results": min(batch, max_results - start),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        url = ARXIV_API + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            xml = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
        except Exception as e:
            print(f"  [错误] 拉取失败: {str(e)[:80]}，重试中...", flush=True)
            time.sleep(5)
            continue
        entries = _parse_atom(xml)
        if not entries:
            break
        results.extend(entries)
        print(f"  已获取 {len(results)}/{max_results}（本次 {len(entries)}）", flush=True)
        start += len(entries)
        if len(entries) < batch:
            break
        time.sleep(2)   # arXiv API 礼貌限速（约 1 req/3s 上限）
    return results


def _parse_atom(xml):
    """极简 Atom 解析（无第三方依赖）"""
    import re
    entries = []
    for m in re.finditer(r"<entry>(.*?)</entry>", xml, re.S):
        e = m.group(1)
        def g(tag):
            mm = re.search(rf"<{tag}>(.*?)</{tag}>", e, re.S)
            return mm.group(1).strip() if mm else ""
        title = re.sub(r"\s+", " ", g("title"))
        summary = re.sub(r"\s+", " ", g("summary"))
        link = re.search(r'<link[^>]*href="(http[^"]+)"', e)
        url = link.group(1) if link else ""
        # 作者
        authors = re.findall(r"<name>(.*?)</name>", e)
        # PDF 直链
        pdf_url = re.sub(r"/abs/", "/pdf/", url) + ".pdf" if url else ""
        published = g("published")
        entries.append({
            "title": title, "authors": authors[:10], "summary": summary[:500],
            "abs_url": url, "pdf_url": pdf_url, "published": published[:10],
        })
    return entries


def main():
    ap = argparse.ArgumentParser(description="arXiv 论文批量采集器")
    ap.add_argument("--category", help="arXiv 分类（cs.CL/cs.AI/cs.LG 等，可多次）", action="append")
    ap.add_argument("--days", type=int, help="最近 N 天（默认 1）")
    ap.add_argument("--from", dest="date_from", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--query", help="关键词过滤（arXiv 语法，如 'LLM AND agent'）")
    ap.add_argument("--max", type=int, default=500, help="最多拉取数（默认 500）")
    ap.add_argument("--out", default="logs/arxiv_batch.json", help="输出 JSON 文件")
    ap.add_argument("--to-urls", help="从已有 JSON 提取 PDF URL 清单到该文件")
    ap.add_argument("--only-urls", action="store_true", help="仅输出 URL 清单（不写 JSON）")
    args = ap.parse_args()

    # 从 JSON 提取 URL 清单模式
    if args.to_urls:
        with open(args.to_urls if args.to_urls != "x" else args.out, encoding="utf-8") as f:
            data = json.load(f)
        urls = [d["pdf_url"] for d in data if d.get("pdf_url")]
        target = args.to_urls if args.to_urls else args.out.replace(".json", "_urls.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("\n".join(urls) + "\n")
        print(f"已提取 {len(urls)} 个 PDF URL → {target}")
        return

    import datetime
    today = datetime.date.today()
    if args.date_to:
        date_to = args.date_to.replace("-", "")
    else:
        date_to = today.strftime("%Y%m%d")
    if args.date_from:
        date_from = args.date_from.replace("-", "")
    else:
        date_from = (today - datetime.timedelta(days=args.days or 1)).strftime("%Y%m%d")

    cats = args.category or ["cs.CL"]
    all_papers = []
    for cat in cats:
        print(f"=== 拉取 {cat}（{date_from} ~ {date_to}）===")
        papers = fetch_arxiv(cat, date_from, date_to, args.query, args.max)
        print(f"{cat}: {len(papers)} 篇")
        all_papers.extend(papers)

    # 去重（按 pdf_url）
    seen, dedup = set(), []
    for p in all_papers:
        if p["pdf_url"] and p["pdf_url"] not in seen:
            seen.add(p["pdf_url"])
            dedup.append(p)

    print(f"总计: {len(dedup)} 篇（去重后）")
    if args.only_urls:
        for p in dedup:
            print(p["pdf_url"])
        return
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(dedup, f, ensure_ascii=False, indent=1)
    print(f"元数据 → {args.out}")
    # 同时输出 URL 清单
    urls_path = args.out.replace(".json", "_urls.txt")
    with open(urls_path, "w", encoding="utf-8") as f:
        f.write("\n".join(p["pdf_url"] for p in dedup) + "\n")
    print(f"URL 清单 → {urls_path}")


if __name__ == "__main__":
    main()
