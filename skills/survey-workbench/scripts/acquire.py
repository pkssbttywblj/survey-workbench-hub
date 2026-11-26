#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""获取层：把检索到的记录变成磁盘上真实的文件。

策略（每一步都留证据，不做「假装拿到了」）：
  1. 记录里自带 pdf_url 且像 PDF           → 直接取
  2. DOI → 落地页，解析 citation_pdf_url    → 取
  3. 已知 OA 出版社的 PDF 直链规律            → 拼 URL 取
  4. Unpaywall（需 --email）                 → 取
  5. 全失败                                  → 记 闭源 + 原因 + 推荐通道

校验：%PDF 魔数（或 pptx/zip 的 PK 头）+ 体积下限 + 内容 sha1 去重。
拿到的是 HTML 落地页而不是 PDF 时，会从页面里再挖一次 PDF 链接，只重试一次。

用法:
  python acquire.py --records .cache/search_raw/merged.json --root survey_x --top 30
  python acquire.py --records merged.json --root survey_x --ids P001,P002
  python acquire.py --urls https://x/y.pdf --root survey_x --kind material --titles "某报告"
  python acquire.py ... --dry-run          # 只出计划，不下载

输出: <root>/.cache/acquired.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    ensure_tree,
    fetch_bytes,
    find_root,
    http_get,
    load_json,
    now_str,
    print_table,
    record_key,
    save_json,
    setup_stdout,
)

MIN_BYTES = 12 * 1024
ASSET_EXT = (".pdf", ".pptx", ".ppt", ".docx", ".zip", ".csv", ".xlsx", ".txt", ".md", ".html", ".htm")

# 这些出版社基本靠机构权限，抓到 HTML 也拿不到正文 —— 直接给出通道，省得反复试
PUBLISHER_CHANNEL = [
    (("sciencedirect.com", "linkinghub.elsevier.com"), "机构CARSI", "Elsevier ScienceDirect"),
    (("ieeexplore.ieee.org",), "机构CARSI", "IEEE Xplore"),
    (("link.springer.com", "springer.com"), "机构CARSI", "Springer"),
    (("onlinelibrary.wiley.com", "wiley.com"), "机构CARSI", "Wiley"),
    (("tandfonline.com",), "机构CARSI", "Taylor & Francis"),
    (("pubs.acs.org",), "机构CARSI", "ACS"),
    (("pubs.rsc.org",), "机构CARSI", "RSC"),
    (("cambridge.org",), "机构CARSI", "Cambridge"),
    (("journals.sagepub.com",), "机构CARSI", "Sage"),
    (("ascelibrary.org",), "机构CARSI", "ASCE"),
    (("asmedigitalcollection.asme.org",), "机构CARSI", "ASME"),
    (("dl.acm.org",), "机构CARSI", "ACM"),
    (("arc.aiaa.org", "aiaa.org"), "机构CARSI", "AIAA（会议论文多为闭源）"),
    (("iopscience.iop.org",), "机构CARSI", "IOP"),
    (("journals.aps.org",), "机构CARSI", "APS"),
    (("begellhouse.com",), "机构CARSI", "Begell House"),
    (("ssrn.com", "papers.ssrn.com"), "Zotero+sci-pdf", "SSRN 预印本（需登录/人机校验）"),
    (("researchgate.net",), "作者主页", "ResearchGate"),
    (("cnki.net", "cnki.com.cn", "wanfangdata"), "知网", "CNKI/万方"),
    (("jstor.org",), "图书馆文献传递", "JSTOR"),
]

OA_PATTERNS = [
    (re.compile(r"^https?://(www\.)?mdpi\.com/.*?/(\d+)/(\d+)/(\d+)$"), r"\g<0>/pdf"),
    (re.compile(r"^https?://(www\.)?frontiersin\.org/articles/(10\.\S+)/full$"), r"https://www.frontiersin.org/articles/\1/pdf"),
    (re.compile(r"/article/(\d+)/(\d+)/(\d+)/?$"), r"\g<0>pdf"),
]


def looks_binary(data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:2] == b"PK":
        return "zip"
    head = data[:400].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html") or b"<html" in head:
        return "html"
    return "other"


def find_pdf_in_html(text: str, base_url: str) -> str:
    """从落地页里挖 PDF 链接，优先级：citation_pdf_url > application/pdf 链接 > .pdf 链接。"""
    patterns = [
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
        r'<a[^>]+href=["\']([^"\']+\.pdf[^"\']*)["\']',
        r'<iframe[^>]+src=["\']([^"\']+\.pdf[^"\']*)["\']',
        r'"(https?://[^"]+/[^"]*\.pdf)"',
        r'"(/[\w\-./]+\.pdf)"',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return urllib.parse.urljoin(base_url, m.group(1).replace("&amp;", "&"))
    return ""


def channel_for(url: str) -> tuple:
    host = urllib.parse.urlparse(url or "").netloc.lower()
    for keys, channel, label in PUBLISHER_CHANNEL:
        if any(k in host for k in keys):
            return channel, label
    return "", ""


def candidate_urls(rec: dict, email: str = "") -> list:
    urls = []
    pdf = (rec.get("pdf_url") or "").strip()
    if pdf:
        urls.append(pdf)
    page = (rec.get("url") or "").strip()
    if page.lower().endswith(ASSET_EXT):
        urls.append(page)
    elif page:
        urls.append(page)
        for pat, repl in OA_PATTERNS:
            if pat.search(page):
                try:
                    cand = re.sub(pat, repl, page)
                except re.error:
                    cand = ""
                if cand and cand not in urls:
                    urls.append(cand)
    doi = (rec.get("doi") or "").strip()
    if doi:
        urls.append("https://doi.org/" + doi)
        if email:
            up = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(email)}"
            data = load_json_from_url(up)
            if data:
                best = data.get("best_oa_location") or {}
                if best.get("url_for_pdf"):
                    urls.insert(0, best["url_for_pdf"])
                elif (data.get("first_oa_location") or {}).get("url_for_pdf"):
                    urls.insert(0, data["first_oa_location"]["url_for_pdf"])
    out = []
    for u in urls:
        if u and u not in out:
            out.append(u)
    return out


def load_json_from_url(url: str):
    status, _, data, _ = http_get(url, timeout=20, retries=1, headers={"Accept": "application/json"})
    if status != 200 or not data:
        return None
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except Exception:
        return None


def is_web_resource(rec: dict) -> bool:
    """公开网页/报告类资料：HTML 原件本身就是可交付资料，不是失败。"""
    if rec.get("kind") == "material":
        return True
    t = (rec.get("type") or "").lower()
    return t in ("webpage", "html", "report", "presentation", "poster", "dataset", "software")


def fetch_one(rec: dict, staging: Path, email: str, timeout: int, dry_run: bool) -> dict:
    key = rec.get("key") or record_key(rec)
    stem = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    cands = candidate_urls(rec, email)
    tried, got = [], None
    if dry_run:
        return {**rec, "file": "", "status": "计划", "bytes": 0, "tried": cands, "reason": ""}

    for url in cands:
        status, ctype, data, err, backend = fetch_bytes(url, timeout=timeout, max_bytes=120 * 1024 * 1024)
        tried.append(
            {
                "url": url,
                "http": status,
                "ctype": (ctype or "")[:40],
                "bytes": len(data or b""),
                "via": backend,
                "err": (err or "")[:60],
            }
        )
        if status != 200 or not data:
            continue
        kind = looks_binary(data)
        if kind == "html":
            # 落地页：先挖一次 PDF 链接
            found = find_pdf_in_html(data.decode("utf-8", "replace")[:400000], url)
            if found and found != url:
                st2, _, data2, _, via2 = fetch_bytes(found, timeout=timeout, max_bytes=120 * 1024 * 1024)
                tried.append(
                    {"url": found, "http": st2, "ctype": "from-html", "bytes": len(data2 or b""), "via": via2}
                )
                if st2 == 200 and looks_binary(data2) in ("pdf", "zip"):
                    got = (found, data2, looks_binary(data2))
                    break
            # 公开网页本身就是资料，不该因为「没挖到 PDF」就判失败。
            # 论文落地页不算——那只是摘要页，存了会让人误以为拿到全文。
            if is_web_resource(rec):
                got = (url, data, "html")
                break
            continue
        if len(data) < MIN_BYTES:
            tried[-1]["note"] = f"体积过小 {len(data)}B"
            continue
        got = (url, data, kind)
        break

    if not got:
        codes = [t.get("http") for t in tried if t.get("http")]
        html_only = any(str(t.get("ctype", "")).startswith("text/html") for t in tried)
        if not cands:
            reason = "记录里没有可用链接"
        elif 403 in codes:
            reason = "出版社 403（反爬/登录墙）"
        elif html_only:
            reason = "只取到落地页，页内未找到 PDF 直链"
        elif not codes:
            first_err = (tried[0].get("err") if tried else "") or "连接失败"
            reason = f"网络不可达：{first_err[:40]}"
        else:
            reason = f"候选地址全部失败（HTTP {sorted(set(codes))}）"
        probe_url = (tried[0].get("url") if tried else "") or rec.get("url") or rec.get("doi") or ""
        ch, label = channel_for(probe_url)
        return {
            **rec,
            "file": "",
            "status": "待手动获取",
            "reason": reason,
            "channel": ch or "机构CARSI",
            "publisher": label,
            "tried": tried,
            "bytes": 0,
        }

    url, data, res_kind = got
    ext = ".html" if res_kind == "html" else (
        ".pdf" if res_kind == "pdf" else ".zip" if res_kind == "zip"
        else (Path(urllib.parse.urlparse(url).path).suffix or ".bin")
    )
    path = staging / f"{stem}{ext}"
    if res_kind == "html":
        # 网页原件保留原始字节，同时在旁边落一份 source URL，便于以后追溯页面来源
        (staging / f"{stem}.source.txt").write_text(f"{url}\n", encoding="utf-8")
    path.write_bytes(data)
    oa_tag = "OA" if rec.get("is_oa") else "公开"
    return {
        **rec,
        "file": str(path),
        "status": oa_tag,
        "resource_kind": res_kind,
        "reason": "",
        "bytes": len(data),
        "sha1": hashlib.sha1(data).hexdigest(),
        "final_url": url,
        "tried": tried,
    }


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="下载公开可获取的资料")
    ap.add_argument("--records", default="", help="search_all.py 产出的 merged.json")
    ap.add_argument("--urls", nargs="*", default=[], help="直接给 URL（网页/PPT/报告）")
    ap.add_argument("--titles", nargs="*", default=[], help="配合 --urls 的标题")
    ap.add_argument("--kind", default="paper", choices=["paper", "material"])
    ap.add_argument("--root", default="")
    ap.add_argument("--top", type=int, default=0, help="只取前 N 条")
    ap.add_argument("--ids", default="", help="只取指定记录序号(rank)，逗号分隔")
    ap.add_argument("--min-relevance", type=int, default=1)
    ap.add_argument("--email", default="", help="Unpaywall 需要邮箱，留空则跳过该通道")
    ap.add_argument("--timeout", type=int, default=40)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = ensure_tree(find_root(args.root) if args.root else Path.cwd())
    staging = root / ".cache" / "staging"

    records = []
    if args.records:
        records = load_json(args.records, default=[]) or []
    if args.urls:
        for i, u in enumerate(args.urls):
            title = args.titles[i] if i < len(args.titles) else Path(urllib.parse.urlparse(u).path).name or u
            records.append(
                {
                    "title": title,
                    "authors": [],
                    "year": "",
                    "venue": "Web",
                    "type": "webpage" if args.kind == "material" else "article",
                    "doi": "",
                    "url": u,
                    "pdf_url": u if u.lower().endswith(".pdf") else "",
                    "is_oa": True,
                    "source": ["Web"],
                }
            )
        for r in records:
            r.setdefault("key", record_key(r))

    if not records:
        print("没有待获取记录。先跑 search_all.py，或用 --urls 指定。")
        return 1

    ids = {s.strip() for s in args.ids.split(",") if s.strip()}
    if ids:
        records = [r for r in records if str(r.get("rank")) in ids or r.get("key") in ids]
    records = [r for r in records if (r.get("relevance") or 5) >= args.min_relevance]
    if args.top:
        records = records[: args.top]

    for r in records:
        r["kind"] = args.kind

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(fetch_one, r, staging, args.email, args.timeout, args.dry_run) for r in records]
        for f in futures:
            results.append(f.result())

    # 内容级去重：同一份 PDF 被两个源下载两次时，只留第一条
    seen = {}
    for r in results:
        s = r.get("sha1")
        if s and s in seen:
            r["status"] = "重复"
            r["reason"] = f"内容与 {seen[s].get('title','')[:30]} 相同"
            try:
                Path(r["file"]).unlink(missing_ok=True)
            except Exception:
                pass
            r["file"] = ""
        elif s:
            seen[s] = r

    out = root / ".cache" / "acquired.json"
    save_json(out, results)

    ok = [r for r in results if r.get("file")]
    fail = [r for r in results if not r.get("file")]
    print_table(
        ["结果", "年", "标题", "字节", "说明"],
        [("OK", r.get("year") or "-", (r.get("title") or "")[:50], r.get("bytes"), r.get("status")) for r in ok]
        + [("FAIL", r.get("year") or "-", (r.get("title") or "")[:50], 0, r.get("reason", "")[:40]) for r in fail],
    )
    total = sum(r.get("bytes") or 0 for r in ok)
    print(f"\n成功 {len(ok)} / 失败 {len(fail)}，合计 {total/1048576:.1f} MB → {out}")
    if fail:
        print("失败项已登记待手动获取通道；跑 build_index.py 后会进 索引表.xlsx 的『待手动获取』sheet。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
