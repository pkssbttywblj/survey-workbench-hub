#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""survey-workbench 公共工具库（仅标准库，跨 agent 可移植）。

不做业务判断，只做：路径、编码、命名清洗、HTTP、去重键、类型/语言粗判、表格打印。
判定类的逻辑（相关度、分区、结论提炼）交给调用方或 agent，避免两处实现漂移。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 survey-workbench/1.0"
)
HOME_STATE = Path(os.environ.get("SURVEY_HOME", Path.home() / ".survey-workbench"))

# 目录常量（与 references/naming-and-layout.md 一一对应）
DIR_INDEX = "00_索引"
DIR_PAPERS = "01_文献"
DIR_MATERIAL = "02_开放资料"
DIR_TEXT = "03_解析全文"
DIR_NOTES = "04_精读笔记"
DIR_REPORT = "05_报告"
DIR_CACHE = ".cache"

PAPER_SUBDIRS = [
    "中文_核心",
    "中文_非核心",
    "英文_SCI1区",
    "英文_SCI2区",
    "英文_SCI3区",
    "英文_SCI4区",
    "英文_非SCI",
    "等级待核实",
]
MATERIAL_SUBDIRS = [
    "幻灯片",
    "技术报告与白皮书",
    "网页存档",
    "数据集",
    "软件与代码",
    "其他",
]

TYPE_CODE = {
    "journal-article": "J",
    "article": "J",
    "posted-content": "P",
    "preprint": "P",
    "proceedings-article": "C",
    "proceedings": "C",
    "conference": "C",
    "dissertation": "D",
    "thesis": "D",
    "report": "R",
    "peer-review": "O",
    "book": "M",
    "book-chapter": "M",
    "monograph": "M",
    "standard": "M",
    "dataset": "Dset",
    "software": "SW",
    "presentation": "S",
    "slide": "S",
    "poster": "S",
    "webpage": "W",
    "html": "W",
    "other": "O",
}

MATERIAL_TYPES = {"S", "R", "W", "Dset", "SW", "M", "O"}


# ---------------------------------------------------------------- 编码与打印
def setup_stdout() -> None:
    """Windows 控制台默认 GBK，中文输出会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def eprint(*args, **kwargs) -> None:
    kwargs.setdefault("file", sys.stderr)
    print(*args, **kwargs)


def print_table(headers, rows, max_col: int = 48) -> None:
    """极简等宽表格，中文按 2 字宽对齐。"""

    def width(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)

    def clip(s: str) -> str:
        s = str(s)
        return s if width(s) <= max_col else s[: max_col // 2 - 1] + "…"

    cells = [[clip(h) for h in headers]] + [[clip(c) for c in r] for r in rows]
    widths = [max(width(row[i]) for row in cells) for i in range(len(headers))]
    for i, row in enumerate(cells):
        line = "  ".join(c + " " * (widths[j] - width(c)) for j, c in enumerate(row))
        print(line.rstrip())
        if i == 0:
            print("  ".join("-" * widths[j] for j in range(len(headers))))


# ---------------------------------------------------------------- 文件与 JSON
def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # 损坏时不要静默吞掉
        eprint(f"[warn] 读取 JSON 失败 {p}: {exc}")
        return default


def save_json(path, data) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def slug(text: str, maxlen: int = 40, fallback: str = "survey") -> str:
    """目录名用。保留中文，去掉文件系统禁忌字符。"""
    text = (text or "").strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    text = re.sub(r"[\s\u3000]+", "_", text)
    text = re.sub(r"[，。、；：！？（）【】《》‘’“”]", "", text)
    text = text.strip("._-")
    text = re.sub(r"_{2,}", "_", text)
    return (text[:maxlen] or fallback)


def field(text: str, maxlen: int = 40, fallback: str = "NA") -> str:
    """文件名里的单个字段：更严格，空格/标点全部清掉。"""
    text = (text or "").strip()
    if not text:
        return fallback
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff\-]", "", text)
    text = text.strip("-._")
    return (text[:maxlen] or fallback)


def sha1_file(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def ensure_tree(root) -> Path:
    root = Path(root)
    for d in (DIR_INDEX, DIR_PAPERS, DIR_MATERIAL, DIR_TEXT, DIR_NOTES, DIR_REPORT, DIR_CACHE):
        (root / d).mkdir(parents=True, exist_ok=True)
    for d in PAPER_SUBDIRS:
        (root / DIR_PAPERS / d).mkdir(parents=True, exist_ok=True)
    for d in MATERIAL_SUBDIRS:
        (root / DIR_MATERIAL / d).mkdir(parents=True, exist_ok=True)
    for d in ("md", "txt", "tables", "pages", "vision"):
        (root / DIR_TEXT / d).mkdir(parents=True, exist_ok=True)
    (root / DIR_REPORT / "figures").mkdir(parents=True, exist_ok=True)
    (root / DIR_CACHE / "staging").mkdir(parents=True, exist_ok=True)
    (root / DIR_CACHE / "search_raw").mkdir(parents=True, exist_ok=True)
    (root / DIR_CACHE / "logs").mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------- HTTP
def http_get(url, timeout=45, retries=2, headers=None, max_bytes=None, allow_redirects=True):
    """返回定长 4 元组 (status, content_type, payload_bytes, error)。

    不抛异常：失败时 status=0（网络层）或 HTTP 码，error 给出可读原因。
    定长是有意的——三个值/四个值混用会让每个调用方的解包随时炸。
    """
    hdrs = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    last = (0, "", b"", "unknown")
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ctype = r.headers.get("Content-Type", "")
                data = r.read(max_bytes) if max_bytes else r.read()
                return r.status, ctype, data, ""
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()[:2000]
            except Exception:
                pass
            last = (exc.code, "", body, f"HTTP {exc.code}")
            if exc.code in (429, 503) and attempt < retries:
                time.sleep(4 * (attempt + 1))
                continue
            return last
        except Exception as exc:
            last = (0, "", b"", f"{type(exc).__name__}: {exc}")
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
    return last


def http_json(url, timeout=45, retries=2, headers=None):
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    status, _, data, _err = http_get(url, timeout=timeout, retries=retries, headers=hdrs)
    if status != 200 or not data:
        return None
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except Exception:
        return None


def http_post_json(url, payload, timeout=45, retries=1, headers=None):
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers={**hdrs, "User-Agent": UA}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            if attempt < retries:
                time.sleep(3)
    return None


def _urllib_fetch(url, timeout, headers, max_bytes, direct):
    hdrs = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    handlers = [urllib.request.ProxyHandler({})] if direct else []
    opener = urllib.request.build_opener(*handlers)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with opener.open(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            data = r.read(max_bytes) if max_bytes else r.read()
            return r.status, ctype, data, ""
    except urllib.error.HTTPError as exc:
        return exc.code, "", b"", f"HTTP {exc.code}"
    except Exception as exc:
        return 0, "", b"", f"{type(exc).__name__}: {exc}"


def fetch_bytes(url, timeout=40, headers=None, max_bytes=None, retries=1):
    """下载专用三级通道，返回 (status, content_type, data, error, backend)。

    1) 走环境代理（企业/校园网常需要）
    2) 直连（代理把出版社域名 CONNECT 掉时救回来——实测本机 7897/62487 代理会这样）
    3) curl_cffi 伪装 Chrome TLS 指纹（Cloudflare/Akamai 挡 UA 的站点）
    只有前一级「连不上」或「被代理拦」才降级，403 这类明确拒绝不再浪费时间重试。
    """
    status, ctype, data, err = _urllib_fetch(url, timeout, headers, max_bytes, direct=False)
    if status == 200 and data:
        return status, ctype, data, "", "urllib"
    if status == 0 or status in (407, 502, 503):
        status2, ctype2, data2, err2 = _urllib_fetch(url, timeout, headers, max_bytes, direct=True)
        if status2 == 200 and data2:
            return status2, ctype2, data2, "", "urllib-direct"
        if status2:
            status, ctype, data, err = status2, ctype2, data2, err2
    if status in (0, 403, 429, 502, 503):
        try:
            from curl_cffi import requests as cffi  # 可选依赖
        except Exception:
            return status, ctype, data, err, "urllib"
        for attempt in range(retries + 1):
            try:
                r = cffi.get(
                    url,
                    impersonate="chrome",
                    timeout=timeout,
                    headers=headers or {},
                    allow_redirects=True,
                    proxies={"http": None, "https": None},
                )
                if r.status_code == 200 and r.content:
                    return 200, r.headers.get("content-type", ""), r.content, "", "curl_cffi"
                status, err = r.status_code, f"HTTP {r.status_code}"
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(2)
    return status, ctype, data, err, "urllib"


# ---------------------------------------------------------------- 去重与粗判
def norm_doi(doi: str) -> str:
    if not doi:
        return ""
    d = doi.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.rstrip(". ")


def norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title or "").lower()
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)
    return t


def record_key(rec: dict) -> str:
    doi = norm_doi(rec.get("doi") or "")
    if doi:
        return "doi:" + doi
    t = norm_title(rec.get("title") or "")
    if t:
        return "tit:" + hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]
    return "raw:" + hashlib.sha1(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def guess_language(*texts) -> str:
    joined = " ".join(t for t in texts if t)
    han = len(re.findall(r"[\u4e00-\u9fff]", joined))
    return "中文" if han >= 3 else "英文"


def guess_type_code(rec: dict) -> str:
    raw = (rec.get("type") or "").strip().lower()
    if raw in TYPE_CODE:
        return TYPE_CODE[raw]
    for k, v in TYPE_CODE.items():
        if k in raw:
            return v
    venue = (rec.get("venue") or "").lower()
    if "arxiv" in venue or (rec.get("source") or [""])[0] == "arXiv":
        return "P"
    return "J"


def is_material(code: str) -> bool:
    return code in MATERIAL_TYPES


def year_of(rec: dict) -> str:
    y = str(rec.get("year") or "").strip()
    m = re.search(r"(19|20)\d{2}", y)
    return m.group(0) if m else "nd"


def first_author_family(rec: dict) -> str:
    authors = rec.get("authors") or []
    if not authors:
        return "Anon"
    a = str(authors[0]).strip()
    if re.search(r"[\u4e00-\u9fff]", a):
        return field(a, 8, "Anon")
    parts = re.split(r"[\s,]+", a)
    return field(parts[-1] if parts else a, 16, "Anon")


def short_title_zh(rec: dict, maxlen: int = 12) -> str:
    """没有 AI 译名时的降级：取已有中文标题的前若干字，或英文标题实词拼接。"""
    for key in ("title_zh", "short_zh"):
        v = (rec.get(key) or "").strip()
        if v:
            return field(v, maxlen, "未命名")
    title = rec.get("title") or ""
    han = re.findall(r"[\u4e00-\u9fff]", title)
    if len(han) >= 4:
        return field(re.sub(r"[^\w\u4e00-\u9fff]", "", title), maxlen, "未命名")
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", title)
    stop = {"the", "and", "for", "with", "from", "using", "based", "study", "analysis", "new"}
    words = [w for w in words if w.lower() not in stop][:3]
    return field("".join(w.capitalize() for w in words), maxlen, "Untitled")


def tier_dir(language: str, tier: str) -> str:
    """把 (语言, 分区) 映射到 01_文献 的子目录。"""
    t = (tier or "").strip()
    if language == "中文":
        return "中文_核心" if t in ("核心", "CSSCI", "北大核心", "CSCD") else "中文_非核心"
    m = re.search(r"([1-4])\s*区", t)
    if m:
        return f"英文_SCI{m.group(1)}区"
    m = re.search(r"Q([1-4])", t, re.I)
    if m:
        return f"英文_SCI{m.group(1)}区"
    if t in ("非SCI", "EI", "非核心"):
        return "英文_非SCI"
    return "等级待核实"


def material_dir(code: str) -> str:
    return {
        "S": "幻灯片",
        "R": "技术报告与白皮书",
        "W": "网页存档",
        "Dset": "数据集",
        "SW": "软件与代码",
    }.get(code, "其他")


def rel(path: Path, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except Exception:
        return Path(path).as_posix()


def find_root(arg_root=None, start=None) -> Path:
    """定位当前调研库根目录：显式给 > 向上的 survey_* > 当前目录。"""
    if arg_root:
        return Path(arg_root).resolve()
    cur = Path(start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if p.name.startswith("survey_") and (p / DIR_INDEX).exists():
            return p
        if (p / DIR_INDEX / "library.db").exists():
            return p
    return cur


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
