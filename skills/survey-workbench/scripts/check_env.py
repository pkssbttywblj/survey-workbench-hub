#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境自检：依赖、可选组件、数据源连通性、Zotero 本地通道。

用法:
  python check_env.py            # 人类可读表格
  python check_env.py --json     # 机器可读，供 agent 决策

判读:
  - 任一必需包 MISS → 先按提示安装，否则后续脚本会在中途报错
  - sources 里 FAIL 的源 → 检索时从 --sources 里剔除，不要反复重试
  - gateways 只测国内/机构库入口能不能打开，它们有验证码或 JS 渲染，脚本不自动抓取；
    要给一键检索链接时用 channels.py
  - ocr.ready=false → 扫描件走多模态提取（pdf_to_md 会自动导出页面图 + 生成骨架），不影响交付
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, print_table, setup_stdout  # noqa: E402

REQUIRED = {
    "openpyxl": "索引表.xlsx 生成",
    "fitz": "PDF 文本/图片抽取（PyMuPDF）",
    "pdfplumber": "PDF 表格抽取",
    "docx": "报告导出 docx（python-docx）",
}
OPTIONAL = {
    "pytesseract": "OCR（需同时装 tesseract 二进制）",
    "bs4": "网页正文提取",
    "lxml": "网页/HTML 解析加速",
    "PIL": "图片处理",
}

SOURCES = {
    "openalex": "https://api.openalex.org/works?search=detonation&per-page=1",
    "crossref": "https://api.crossref.org/works?query=detonation&rows=1",
    "arxiv": "https://export.arxiv.org/api/query?search_query=all:detonation&max_results=1",
    "europepmc": "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=detonation&format=json&pageSize=1",
    "doaj": "https://doaj.org/api/search/articles/detonation?pageSize=1",
    "plos": "https://api.plos.org/search?q=detonation&rows=1&wt=json",
    "datacite": "https://api.datacite.org/dois?query=detonation&page[size]=1",
    "zenodo": "https://zenodo.org/api/records?q=detonation&size=1",
    "figshare": "https://api.figshare.com/v2/articles?page_size=1",
    "semantic": "https://api.semanticscholar.org/graph/v1/paper/search?query=detonation&limit=1",
}

# 国内/机构数据库入口：只探测「网页能不能打开」，绝不假装能自动检索。
# 实测 2026-11-25：CNKI→滑块验证码、维普→412、百度学术→安全验证、
# ChinaXiv POST→403、paper.edu.cn/NSTL→结果页 JS 渲染。
# 它们由 channels.py 生成一键检索 URL，供 agent 引导用户手动/走机构权限下载。
GATEWAYS = {
    "知网CNKI": "https://kns.cnki.net/",
    "万方": "https://www.wanfangdata.com.cn/",
    "维普": "https://www.cqvip.com/",
    "百度学术": "https://xueshu.baidu.com/",
    "ChinaXiv": "https://www.chinaxiv.org/home.htm",
    "中国科技论文在线": "https://www.paper.edu.cn/",
    "NSTL": "https://www.nstl.gov.cn/",
    "国家科技报告": "https://www.nstrs.cn/",
    "国家哲社文献": "https://www.ncpssd.cn/",
    "CBM中国生物医学": "https://www.sinomed.ac.cn/",
}


def check_pkg(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def check_bin(names) -> str:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return ""


def main() -> int:
    setup_stdout()
    as_json = "--json" in sys.argv

    pkgs = {}
    for name, why in {**REQUIRED, **OPTIONAL}.items():
        pkgs[name] = {"ok": check_pkg(name), "why": why, "required": name in REQUIRED}

    tesseract = check_bin(["tesseract"])
    pandoc = check_bin(["pandoc"])
    soffice = check_bin(["soffice", "libreoffice"])
    browser = check_bin(["msedge", "chrome", "chromium"])
    if not browser:
        for cand in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ):
            if Path(cand).exists():
                browser = cand
                break

    sources = {}
    for name, url in SOURCES.items():
        status, _, data, err = http_get(url, timeout=25, retries=1)
        sources[name] = {
            "status": status,
            "ok": status == 200 and len(data) > 20,
            "note": "" if status == 200 else (f"HTTP {status}" if status else f"连接失败 {err[:60]}"),
        }

    gateways = {}
    for name, url in GATEWAYS.items():
        st, _, data, _err = http_get(url, timeout=20, retries=0)
        gateways[name] = {
            "status": st,
            "ok": st in (200, 301, 302),
            "note": ("入口可达；需人工检索或机构授权，不作自动抓取" if st in (200, 301, 302)
                     else f"HTTP {st}；浏览器里可能仍可访问，脚本不自动抓取"),
        }

    zotero = {"status": 0, "ok": False}
    try:
        st, _, body, _ = http_get("http://127.0.0.1:23119/api/users/0/items?limit=1", timeout=4, retries=0)
        zotero = {"status": st, "ok": st == 200 and body[:1] in (b"[", b"{")}
    except Exception:
        pass

    report = {
        "python": sys.version.split()[0],
        "python_ok": sys.version_info >= (3, 9),
        "packages": pkgs,
        "binaries": {
            "tesseract": tesseract,
            "pandoc": pandoc,
            "soffice": soffice,
            "browser": browser,
        },
        "ocr_ready": bool(tesseract and pkgs["pytesseract"]["ok"]),
        "sources": sources,
        "gateways": gateways,
        "zotero": zotero,
    }
    report["ready"] = all(v["ok"] for v in pkgs.values() if v["required"])
    ok_sources = [k for k, v in sources.items() if v["ok"]]
    report["usable_sources"] = ok_sources

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"Python {report['python']}  {'OK' if report['python_ok'] else '版本过低，需 >=3.9'}")
    print()
    rows = []
    for name, info in pkgs.items():
        tag = "必需" if info["required"] else "可选"
        rows.append((name, tag, "OK" if info["ok"] else "MISS", info["why"]))
    print_table(["包", "级别", "状态", "用途"], rows)
    print()
    print("二进制:")
    for k, v in report["binaries"].items():
        print(f"  {k:10s} {'OK  ' + v if v else 'MISS（相关功能降级）'}")
    print(f"  OCR 通道  {'可用' if report['ocr_ready'] else '不可用（扫描件将走多模态提取：导出页面图由 agent 读）'}")
    print(f"  Zotero本地 {'已连接' if zotero['ok'] else '未连接（可选，走不到就手动下载）'}")
    print()
    print_table(
        ["数据源", "状态", "备注"],
        [(k, "OK" if v["ok"] else "FAIL", v["note"]) for k, v in sources.items()],
    )
    print()
    print("国内/机构检索入口（只测可达，不自动抓取）:")
    print_table(
        ["入口", "状态", "说明"],
        [(k, "OK" if v["ok"] else "FAIL", v["note"]) for k, v in gateways.items()],
    )
    print()
    if report["ready"]:
        print(f"可用数据源: {', '.join(ok_sources) if ok_sources else '无'}")
        miss = [k for k, v in pkgs.items() if v["required"] and not v["ok"]]
        if miss:
            print("需要安装: pip install " + " ".join(miss))
    else:
        miss = [k for k, v in pkgs.items() if v["required"] and not v["ok"]]
        print("缺少必需依赖，先装: pip install " + " ".join(miss))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
