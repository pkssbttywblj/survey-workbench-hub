#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多源检索：论文 + 开放资料，统一记录、去重、排序。

论文源: openalex / crossref / arxiv / europepmc / doaj / semantic / plos
资料源: zenodo（幻灯片·海报·数据集·软件·报告） / figshare / openalex(type=report) / datacite

国内直连可用性（2026-11-25 实测 --noproxy '*'）：
  crossref / arxiv / europepmc / doaj / plos / datacite 全部 200
  openalex 本机亦通，但部分国内网络需代理——被拦时从默认源剔除，改用 crossref+plos+doaj
  zenodo 直连 000、semantic 429、figshare 403，均属已知降级，不要反复重试
中文库（CNKI/万方/维普/百度学术/ChinaXiv/NSTL）为验证码或 JS 渲染，不能自动抓取，
改由 channels.py 生成一键检索链接 + 机构授权引导，见 references/sources.md。

用法:
  python search_all.py "rotating detonation engine" --limit 40
  python search_all.py "复合材料固化" --query-en "composite curing" --sources all --kind all
  python search_all.py "RDE simulation" --kind material --per-source 20
  python search_all.py "..." --root survey_旋转爆轰       # 写到库内 .cache/search_raw/

输出: merged.json（数组），每条含 key/title/authors/year/venue/type/doi/url/pdf_url/
      oa_status/citations/abstract/language/relevance/score/source[]。
      同时把本次检索登记进 <root>/00_索引/检索记录.json。

排序策略:
  balanced  默认。近 5 年加权 + 被引对数加权，兼顾「新」和「有分量」
  date      纯按年份倒序
  citations 纯按被引倒序
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    DIR_INDEX,
    http_get,
    http_json,
    http_post_json,
    load_json,
    norm_doi,
    norm_title,
    now_str,
    print_table,
    record_key,
    save_json,
    setup_stdout,
)

CUR_YEAR = date.today().year
ATOM = "{http://www.w3.org/2005/Atom}"
PAPER_SOURCES = ["openalex", "crossref", "arxiv", "europepmc", "doaj", "semantic", "plos"]
MATERIAL_SOURCES = ["zenodo", "figshare", "openalex", "datacite"]


def _authors_str(names) -> list:
    out = []
    for n in names or []:
        if isinstance(n, dict):
            n = n.get("name") or n.get("display_name") or ""
        n = re.sub(r"\s+", " ", str(n)).strip()
        if n:
            out.append(n)
    return out[:10]


def _pick(*vals):
    for v in vals:
        if v:
            return v
    return ""


# ------------------------------------------------------------------ 各源实现
def search_openalex(query, limit, from_date, mailto="", material=False):
    filt = [f"from_publication_date:{from_date}"] if from_date else []
    if material:
        filt.append("type:report|dissertation|book|dataset")
    params = {"search": query, "per-page": str(min(limit, 200))}
    if filt:
        params["filter"] = ",".join(filt)
    if mailto:
        params["mailto"] = mailto
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = http_json(url)
    if not data:
        raise RuntimeError("openalex 无返回")
    out = []
    for w in data.get("results", []):
        inv = w.get("abstract_inverted_index")
        abstract = ""
        if inv:
            pos = {}
            for word, idxs in inv.items():
                for i in idxs or []:
                    pos[i] = word
            abstract = " ".join(pos[k] for k in sorted(pos))
        best = w.get("best_oa_location") or {}
        oa = w.get("open_access") or {}
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        out.append(
            {
                "title": _pick(w.get("title"), w.get("display_name")),
                "authors": _authors_str(
                    [(a.get("author") or {}).get("display_name") for a in w.get("authorships", [])]
                ),
                "year": w.get("publication_year"),
                "venue": _pick(src.get("display_name"), "OpenAlex"),
                "type": w.get("type") or "article",
                "doi": w.get("doi") or "",
                "url": _pick(loc.get("landing_page_url"), w.get("doi"), w.get("id"), ""),
                "pdf_url": _pick(best.get("pdf_url"), oa.get("oa_url"), ""),
                "is_oa": bool(oa.get("is_oa")),
                "citations": w.get("cited_by_count") or 0,
                "abstract": abstract[:2000],
                "language": "中文" if (w.get("language") or "").startswith("zh") else "",
                "source": ["OpenAlex"],
            }
        )
    return out


def search_crossref(query, limit, from_date, mailto=""):
    params = {
        "query": query,
        "rows": str(min(limit, 100)),
        "select": "DOI,title,author,issued,container-title,is-referenced-by-count,type,URL,abstract,link",
    }
    if from_date:
        params["filter"] = f"from-pub-date:{from_date}"
    if mailto:
        params["mailto"] = mailto
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = http_json(url)
    if not data:
        raise RuntimeError("crossref 无返回")
    items = ((data.get("message") or {}).get("items")) or []
    out = []
    for it in items:
        authors = []
        for a in it.get("author", [])[:10]:
            nm = " ".join(x for x in [a.get("given"), a.get("family")] if x) or a.get("name")
            if nm:
                authors.append(nm)
        issued = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        pdf = ""
        for lk in it.get("link", []) or []:
            if (lk.get("content-type") or "").lower() == "application/pdf":
                pdf = lk.get("URL") or ""
                break
        abstract = re.sub(r"<[^>]+>", " ", it.get("abstract") or "").strip()
        out.append(
            {
                "title": _pick((it.get("title") or [""])[0]),
                "authors": authors,
                "year": issued[0] if issued else None,
                "venue": _pick((it.get("container-title") or [""])[0], "Crossref"),
                "type": it.get("type") or "journal-article",
                "doi": it.get("DOI") or "",
                "url": it.get("URL") or "",
                "pdf_url": pdf,
                "is_oa": bool(pdf),
                "citations": it.get("is-referenced-by-count") or 0,
                "abstract": re.sub(r"\s+", " ", abstract)[:2000],
                "language": "",
                "source": ["Crossref"],
            }
        )
    return out


def search_arxiv(query, limit, from_date, **_):
    # 引号短语在 arXiv 上极易 0 命中，改成 AND 逐词更稳
    tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", query)][:6]
    q = " AND ".join(f"all:{t}" for t in tokens) if len(tokens) > 1 else f"all:{query}"
    url = (
        "https://export.arxiv.org/api/query?"
        + urllib.parse.urlencode({"search_query": q, "max_results": min(limit, 100), "sortBy": "relevance"})
    )
    status, _, data, err = http_get(url, timeout=50)
    if status != 200 or not data:
        raise RuntimeError(f"arxiv HTTP {status} {err[:60]}")
    try:
        root = ET.fromstring(data.decode("utf-8", "replace"))
    except Exception as exc:
        raise RuntimeError(f"arxiv XML 解析失败: {exc}")
    out = []
    for e in root.findall(f"{ATOM}entry"):
        title = re.sub(r"\s+", " ", (e.findtext(f"{ATOM}title") or "")).strip()
        if not title or title.lower().startswith("error"):
            continue
        pub = (e.findtext(f"{ATOM}published") or "")[:4]
        pdf = ""
        for lk in e.findall(f"{ATOM}link"):
            if lk.get("title") == "pdf" or (lk.get("type") or "") == "application/pdf":
                pdf = lk.get("href") or ""
        entry_id = e.findtext(f"{ATOM}id") or ""
        out.append(
            {
                "title": title,
                "authors": _authors_str([a.findtext(f"{ATOM}name") for a in e.findall(f"{ATOM}author")]),
                "year": int(pub) if pub.isdigit() else None,
                "venue": "arXiv",
                "type": "preprint",
                "doi": "",
                "url": entry_id,
                "pdf_url": pdf or entry_id.replace("/abs/", "/pdf/"),
                "is_oa": True,
                "citations": 0,
                "abstract": re.sub(r"\s+", " ", (e.findtext(f"{ATOM}summary") or "")).strip()[:2000],
                "language": "",
                "source": ["arXiv"],
            }
        )
    return out


def search_europepmc(query, limit, from_date, **_):
    params = {"query": query, "format": "json", "pageSize": str(min(limit, 100)), "resultType": "core"}
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params)
    data = http_json(url)
    if not data:
        raise RuntimeError("europepmc 无返回")
    out = []
    for r in ((data.get("resultList") or {}).get("result")) or []:
        pdf = ""
        for u in ((r.get("fullTextUrlList") or {}).get("fullTextUrl")) or []:
            if (u.get("documentStyle") or "").upper() == "PDF":
                pdf = u.get("url") or ""
                break
        out.append(
            {
                "title": _pick(r.get("title")),
                "authors": _authors_str([r.get("authorString")]),
                "year": int(r["pubYear"]) if str(r.get("pubYear", "")).isdigit() else None,
                "venue": _pick(r.get("journalTitle"), "Europe PMC"),
                "type": "journal-article",
                "doi": r.get("doi") or "",
                "url": f"https://europepmc.org/article/{r.get('source', 'MED')}/{r.get('id', '')}",
                "pdf_url": pdf,
                "is_oa": (r.get("isOpenAccess") or "").upper() == "Y",
                "citations": r.get("citedByCount") or 0,
                "abstract": re.sub(r"<[^>]+>", " ", r.get("abstractText") or "")[:2000],
                "language": "",
                "source": ["EuropePMC"],
            }
        )
    return out


def search_doaj(query, limit, from_date, **_):
    url = "https://doaj.org/api/search/articles/" + urllib.parse.quote(query) + f"?pageSize={min(limit, 100)}"
    data = http_json(url)
    if not data:
        raise RuntimeError("doaj 无返回")
    out = []
    for r in data.get("results", []):
        b = r.get("bibjson") or {}
        links = b.get("link") or []
        full = ""
        for lk in links:
            if (lk.get("type") or "").lower() == "fulltext":
                full = lk.get("url") or ""
                break
        out.append(
            {
                "title": _pick(b.get("title")),
                "authors": _authors_str([a.get("name") for a in b.get("author", [])]),
                "year": int(b["year"]) if str(b.get("year", "")).isdigit() else None,
                "venue": _pick((b.get("journal") or {}).get("title"), "DOAJ"),
                "type": "journal-article",
                "doi": _pick((b.get("identifier") or [{}])[0].get("id") if b.get("identifier") else ""),
                "url": full or f"https://doaj.org/article/{r.get('id', '')}",
                "pdf_url": full if full.lower().endswith(".pdf") else "",
                "is_oa": True,
                "citations": 0,
                "abstract": re.sub(r"<[^>]+>", " ", b.get("abstract") or "")[:2000],
                "language": "",
                "source": ["DOAJ"],
            }
        )
    return out


def search_plos(query, limit, from_date, **_):
    """PLOS：国内直连可用（实测 --noproxy 200）。纯 OA，PDF 直链可拼。"""
    params = {"q": query, "rows": str(min(limit, 100)), "wt": "json", "fl": "*,score"}
    if from_date:
        params["fq"] = f"publication_date:[{from_date}T00:00:00Z TO *]"
    url = "https://api.plos.org/search?" + urllib.parse.urlencode(params)
    data = http_json(url, timeout=40)
    if not data:
        raise RuntimeError("plos 无返回")
    slug_of = {
        "plos one": "plosone",
        "plos genetics": "plosgenetics",
        "plos computational biology": "ploscompbiol",
        "plos medicine": "plosmedicine",
        "plos biology": "plosbiology",
        "plos pathogens": "plospathogens",
        "plos neglected tropical diseases": "plosntds",
        "plos climate": "plosclimate",
        "plos digital health": "plosdigitalhealth",
        "plos water": "ploswater",
        "plos global public health": "plosgph",
    }
    out = []
    for doc in (data.get("response") or {}).get("docs", []):
        doi = str(doc.get("id") or "").strip()
        title = _pick(doc.get("title_display"), (doc.get("title") or [""])[0] if isinstance(doc.get("title"), list) else "")
        journal = str(doc.get("journal") or "PLOS")
        slug = slug_of.get(journal.lower()) or re.sub(r"[^a-z]", "", journal.lower().replace("plos ", "plos"))
        pdf = f"https://journals.plos.org/{slug}/article/file?id={doi}&type=printable" if doi else ""
        year = None
        m = re.match(r"(\d{4})", str(doc.get("publication_date") or ""))
        if m:
            year = int(m.group(1))
        abstract = re.sub(r"<[^>]+>", " ", str(doc.get("abstract") or ""))[:2000]
        out.append(
            {
                "title": title,
                "authors": _authors_str(doc.get("author_display") or []),
                "year": year,
                "venue": journal,
                "type": "journal-article",
                "doi": doi,
                "url": doc.get("article_url") or (f"https://doi.org/{doi}" if doi else ""),
                "pdf_url": pdf,
                "is_oa": True,
                "citations": 0,
                "abstract": abstract,
                "language": "",
                "source": ["PLOS"],
            }
        )
    return out


def search_datacite(query, limit, from_date, **_):
    """DataCite：国内直连可用（实测 --noproxy 200）。DOI 注册库，补数据集/软件/报告/学位论文。

    它不是全文库，pdf_url 一般为空；价值在于把非论文资料纳入同一张索引表。
    """
    params = {"query": query, "page[size]": str(min(limit, 100))}
    if from_date:
        params["query"] += f" AND publicationYear:>={from_date[:4]}"
    url = "https://api.datacite.org/dois?" + urllib.parse.urlencode(params)
    data = http_json(url, timeout=40)
    if not data:
        raise RuntimeError("datacite 无返回")
    allow = {
        "text", "dataset", "software", "report", "conferencepaper", "dissertation",
        "preprint", "technicalreport", "collection", "image", "audiovisual",
    }
    # DataCite 的相关性排序很松（查 detonation 会返回小说），必须逐条验命中。
    probes = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z-]{3,}", query)]
    probes += re.findall(r"[\u4e00-\u9fff]{2,}", query)
    out = []
    for item in data.get("data", []):
        a = item.get("attributes") or {}
        kind = str(((a.get("types") or {}).get("resourceTypeGeneral") or "")).lower()
        if kind and kind not in allow:
            continue
        titles = a.get("titles") or []
        title = _pick(titles[0].get("title") if titles else "")
        if not title:
            continue
        doi = str(a.get("doi") or "").strip()
        names = []
        for c in a.get("creators") or []:
            names.append(c.get("name") or " ".join(x for x in (c.get("givenName"), c.get("familyName")) if x))
        year = a.get("publicationYear")
        rights = " ".join(str(r.get("uri") or r.get("rights") or "") for r in a.get("rightsList") or [])
        abstract = ""
        for d in a.get("descriptions") or []:
            if d.get("descriptionType") == "Abstract":
                abstract = re.sub(r"<[^>]+>", " ", str(d.get("description") or ""))[:2000]
                break
        # 论文类必须标题命中；数据集/软件/报告允许摘要命中（它们的标题常只写 "Data for ..."）。
        # 只靠 publisher 命中一律不算——DataCite 里书商/机构名会把噪声顶到前列。
        title_hit = any(p in title.lower() for p in probes) if probes else True
        abstract_hit = any(p in abstract.lower() for p in probes) if probes else True
        material_kind = kind in ("dataset", "software", "report", "technicalreport", "collection")
        if probes and not (title_hit or (material_kind and abstract_hit)):
            continue
        out.append(
            {
                "title": title,
                "authors": _authors_str(names),
                "year": int(year) if str(year).isdigit() else None,
                "venue": _pick(a.get("publisher"), kind or "DataCite"),
                "type": kind or "text",
                "doi": doi,
                "url": a.get("url") or (f"https://doi.org/{doi}" if doi else ""),
                "pdf_url": "",
                "is_oa": "creativecommons.org" in rights.lower(),
                "citations": 0,
                "abstract": abstract,
                "language": a.get("language") or "",
                "source": ["DataCite"],
            }
        )
    return out


def search_zenodo(query, limit, from_date, **_):
    params = {"q": query, "size": str(min(limit, 100)), "sort": "mostrecent", "all_versions": "false"}
    url = "https://zenodo.org/api/records?" + urllib.parse.urlencode(params)
    data = http_json(url)
    if not data:
        raise RuntimeError("zenodo 无返回")
    out = []
    for r in ((data.get("hits") or {}).get("hits")) or []:
        md = r.get("metadata") or {}
        rt = md.get("resource_type") or {}
        subtype = (rt.get("subtype") or rt.get("type") or "").lower()
        files = r.get("files") or []
        dl = ""
        for f in files:
            key = (f.get("key") or "").lower()
            if key.endswith((".pdf", ".pptx", ".ppt", ".zip", ".docx")):
                dl = (f.get("links") or {}).get("self") or ""
                break
        if not dl and files:
            dl = (files[0].get("links") or {}).get("self") or ""
        creators = md.get("creators") or []
        names = [c.get("name") for c in creators if isinstance(c, dict)]
        out.append(
            {
                "title": _pick(md.get("title")),
                "authors": _authors_str(names),
                "year": int((md.get("publication_date") or "0000")[:4] or 0) or None,
                "venue": f"Zenodo/{subtype or 'record'}",
                "type": subtype or "other",
                "doi": md.get("doi") or r.get("doi") or "",
                "url": r.get("links", {}).get("self_html") or r.get("links", {}).get("self") or "",
                "pdf_url": dl,
                "is_oa": True,
                "citations": 0,
                "abstract": re.sub(r"<[^>]+>", " ", md.get("description") or "")[:2000],
                "language": "",
                "source": ["Zenodo"],
            }
        )
    return out


def search_figshare(query, limit, from_date, **_):
    data = http_post_json(
        "https://api.figshare.com/v2/articles/search",
        {"search_for": query, "page_size": min(limit, 100)},
        timeout=40,
    )
    if not data:
        raise RuntimeError("figshare 无返回")
    out = []
    for r in data:
        out.append(
            {
                "title": _pick(r.get("title")),
                "authors": [],
                "year": int((r.get("published_date") or "0000")[:4] or 0) or None,
                "venue": f"Figshare/{r.get('defined_type_name') or 'item'}",
                "type": r.get("defined_type_name") or "other",
                "doi": r.get("doi") or "",
                "url": r.get("url_public_html") or "",
                "pdf_url": "",
                "is_oa": True,
                "citations": 0,
                "abstract": re.sub(r"<[^>]+>", " ", r.get("description") or "")[:2000],
                "language": "",
                "source": ["Figshare"],
            }
        )
    return out


def search_semantic(query, limit, from_date, **_):
    fields = "title,authors,year,venue,citationCount,externalIds,abstract,openAccessPdf,publicationTypes"
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/search?"
        + urllib.parse.urlencode({"query": query, "limit": min(limit, 100), "fields": fields})
    )
    data = http_json(url, timeout=40, retries=1)
    if not data:
        raise RuntimeError("semantic 无返回或被限流(429)")
    out = []
    for p in data.get("data", []):
        ext = p.get("externalIds") or {}
        oa = p.get("openAccessPdf") or {}
        out.append(
            {
                "title": _pick(p.get("title")),
                "authors": _authors_str([a.get("name") for a in p.get("authors", [])]),
                "year": p.get("year"),
                "venue": _pick(p.get("venue"), "Semantic Scholar"),
                "type": ((p.get("publicationTypes") or ["JournalArticle"])[0] or "JournalArticle").lower(),
                "doi": ext.get("DOI") or "",
                "url": f"https://www.semanticscholar.org/paper/{p.get('paperId', '')}",
                "pdf_url": oa.get("url") or "",
                "is_oa": bool(oa.get("url")),
                "citations": p.get("citationCount") or 0,
                "abstract": (p.get("abstract") or "")[:2000],
                "language": "",
                "source": ["Semantic"],
            }
        )
    return out


DISPATCH = {
    "openalex": search_openalex,
    "crossref": search_crossref,
    "arxiv": search_arxiv,
    "europepmc": search_europepmc,
    "doaj": search_doaj,
    "plos": search_plos,
    "datacite": search_datacite,
    "zenodo": search_zenodo,
    "figshare": search_figshare,
    "semantic": search_semantic,
}


# ------------------------------------------------------------------ 合并与打分
def merge(records):
    merged = {}
    for r in records:
        if not (r.get("title") or "").strip():
            continue
        k = record_key(r)
        if k in merged:
            base = merged[k]
            base["citations"] = max(base.get("citations") or 0, r.get("citations") or 0)
            for src in r.get("source", []):
                if src not in base["source"]:
                    base["source"].append(src)
            for f in ("pdf_url", "abstract", "doi", "url"):
                if not base.get(f) and r.get(f):
                    base[f] = r[f]
            base["is_oa"] = base.get("is_oa") or r.get("is_oa")
            if not base.get("language") and r.get("language"):
                base["language"] = r["language"]
        else:
            merged[k] = dict(r)
            merged[k]["source"] = list(r.get("source") or [])
    return list(merged.values())


def score_records(records, query, mode="balanced"):
    """相关度只做粗筛（1-5 星），真正的判断留给 agent 读摘要后重打分。

    打分把「标题命中」权重加倍：标题命中比摘要里顺带提一句更能代表主题一致。
    """
    tokens = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", query)]
    han = re.findall(r"[\u4e00-\u9fff]{2,}", query)
    probes = tokens + han
    for r in records:
        title = (r.get("title") or "").lower()
        abstract = (r.get("abstract") or "").lower()
        hit_title = sum(1 for t in probes if t in title)
        hit_abs = sum(1 for t in probes if t in abstract)
        ratio = (hit_title * 2 + hit_abs) / max(1, len(probes) * 2)
        r["relevance"] = max(1, min(5, int(round(1 + 4 * ratio))))
        year = r.get("year") or 0
        cites = r.get("citations") or 0
        try:
            gap = CUR_YEAR - int(year)
        except Exception:
            gap = 10
        recency = max(0, 450 - 100 * max(0, gap))
        cite_bonus = min(900, 90 * math.log1p(cites))
        if mode == "date":
            r["score"] = int(year or 0) * 1000 + cites
        elif mode == "citations":
            r["score"] = cites * 1000 + int(year or 0)
        else:
            r["score"] = r["relevance"] * 150 + recency + cite_bonus
    records.sort(key=lambda x: x.get("score", 0), reverse=True)
    return records


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="多源文献/资料检索")
    ap.add_argument("query", help="英文检索式（中英文都要时用 --query-zh 追加一次）")
    ap.add_argument("--query-zh", default="", help="中文检索式，会合并进同一次检索")
    ap.add_argument("--sources", default="all", help="all 或逗号分隔的源名")
    ap.add_argument("--kind", default="paper", choices=["paper", "material", "all"])
    ap.add_argument("--limit", type=int, default=40, help="合并去重后保留条数")
    ap.add_argument("--per-source", type=int, default=25, help="每个源取多少条")
    ap.add_argument("--from-date", default="", help="YYYY-MM-DD，限定起始年")
    ap.add_argument("--sort", default="balanced", choices=["balanced", "date", "citations"])
    ap.add_argument("--mailto", default="", help="OpenAlex/Crossref 礼貌池邮箱，可留空")
    ap.add_argument("--root", default="", help="调研库根目录，给了就写入库内 .cache")
    ap.add_argument("--out", default="", help="自定义输出路径")
    args = ap.parse_args()

    if args.sources == "all":
        pool = []
        if args.kind in ("paper", "all"):
            pool += PAPER_SOURCES
        if args.kind in ("material", "all"):
            pool += MATERIAL_SOURCES
        sources = list(dict.fromkeys(pool))
    else:
        sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    queries = [q for q in [args.query, args.query_zh] if q]
    raw, status = [], {}
    for src in sources:
        fn = DISPATCH.get(src)
        if not fn:
            status[src] = "未知源"
            continue
        got = 0
        for q in queries:
            try:
                items = fn(
                    q,
                    args.per_source,
                    args.from_date,
                    mailto=args.mailto if src in ("openalex", "crossref") else "",
                    material=(src == "openalex" and args.kind != "paper"),
                )
                raw.extend(items)
                got += len(items)
                status[src] = f"OK {got}"
            except TypeError:
                items = fn(q, args.per_source, args.from_date)
                raw.extend(items)
                got += len(items)
                status[src] = f"OK {got}"
            except Exception as exc:
                status[src] = f"FAIL {exc}"
    records = merge(raw)
    merged_total = len(records)
    records = score_records(records, " ".join(queries), args.sort)[: args.limit]

    for i, r in enumerate(records, 1):
        r["rank"] = i
        r["key"] = record_key(r)
        r["doi"] = norm_doi(r.get("doi") or "")
    out_path = Path(args.out) if args.out else (
        Path(args.root) / ".cache" / "search_raw" / "merged.json" if args.root else Path("merged.json")
    )
    save_json(out_path, records)

    # 检索台账：每次检索都留痕，避免重复劳动
    if args.root:
        log_path = Path(args.root) / DIR_INDEX / "检索记录.json"
        log = load_json(log_path, default=[]) or []
        log.append(
            {
                "time": now_str(),
                "query": " | ".join(queries),
                "kind": args.kind,
                "sources": status,
                "raw": len(raw),
                "merged": len(records),
            }
        )
        save_json(log_path, log)

    print_table(
        ["#", "年", "类型", "标题", "来源", "被引", "OA", "★"],
        [
            (
                r.get("rank"),
                r.get("year") or "-",
                (r.get("type") or "")[:10],
                (r.get("title") or "")[:60],
                "/".join(r.get("source") or []),
                r.get("citations") or 0,
                "Y" if r.get("is_oa") else "N",
                r.get("relevance"),
            )
            for r in records
        ],
    )
    print(f"\n各源状态: {json.dumps(status, ensure_ascii=False)}")
    print(f"原始 {len(raw)} 条 → 去重 {merged_total} 条 → 保留 {len(records)} 条 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
