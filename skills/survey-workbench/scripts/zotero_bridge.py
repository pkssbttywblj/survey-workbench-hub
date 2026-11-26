#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zotero 桥（可选）：把本地 Zotero 库里的条目与 PDF 接进调研库。

为什么需要它：机构权限下载（CARSI/Shibboleth）在 CLI 里做不了，但用户手动在浏览器/Zotero
里点两下就能拿到。Zotero 配合 sci-pdf（自动抓 PDF）和茉莉花 Jasminum（中文文献元数据）
是现成的「半自动」通道 —— 这个脚本负责把成果收编，不重复劳动。

两种接入方式，按可用性自动选：
  1. Zotero 7 本地 API  http://127.0.0.1:23119/api/users/0/...  （需在设置里开启）
  2. 直接读 zotero.sqlite（复制一份再读，避免被 Zotero 锁住）

用法:
  python zotero_bridge.py --status
  python zotero_bridge.py --import --root survey_x [--query "detonation"] [--limit 50]
  python zotero_bridge.py --guide
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    DIR_CACHE,
    ensure_tree,
    find_root,
    http_get,
    print_table,
    record_key,
    save_json,
    setup_stdout,
)

GUIDE = """
Zotero 通道配置（一次性，10 分钟）
──────────────────────────────────────────────
1. 装 Zotero 7：https://www.zotero.org/download/
2. 装两个插件（工具 → 插件 → 齿轮 → Install Add-on From File）：
   · sci-pdf     https://github.com/MuiseDestiny/zotero-sci-pdf   自动按 DOI 抓 PDF（走你的机构权限）
   · 茉莉花      https://github.com/lifanxi/zotero-jasminum       中文文献元数据/知网辅助
3. 配机构代理：Zotero 首选项 → 通用 → 代理 → 填学校 EZproxy/CARSI 地址（问图书馆要）
   或在浏览器里登录 CARSI，用 Zotero Connector 抓取时复用会话。
4. 批量补 PDF：选中条目 → 右键 → Manage PDF → 或 sci-pdf 的 Update；等到条目下有 PDF 图标。
5. 回到本 skill：
   python zotero_bridge.py --status                 # 确认能读到库
   python zotero_bridge.py --import --root survey_x --query "关键词"

注意：sci-pdf 拿 PDF 依赖你已登录的机构会话，skill 不接触账号密码。
拿不到就说明该刊不在学校订阅范围内 —— 走图书馆文献传递，别再试。
"""


def zotero_dir() -> Path:
    env = os.environ.get("ZOTERO_DATA_DIR")
    if env:
        return Path(env)
    home = Path.home()
    for cand in (home / "Zotero", home / "Documents" / "Zotero", home / "AppData" / "Roaming" / "Zotero"):
        if (cand / "zotero.sqlite").exists():
            return cand
    return home / "Zotero"


def api_alive() -> bool:
    st, _, body, _ = http_get("http://127.0.0.1:23119/api/users/0/items?limit=1", timeout=3, retries=0)
    return st == 200 and body[:1] in (b"[", b"{")


def status() -> dict:
    zd = zotero_dir()
    db = zd / "zotero.sqlite"
    info = {
        "data_dir": str(zd),
        "sqlite": db.exists(),
        "api": api_alive(),
        "storage": (zd / "storage").exists(),
        "plugins": sorted(p.name for p in (zd / "extensions").glob("*")) if (zd / "extensions").exists() else [],
    }
    return info


def read_sqlite(limit: int, query: str):
    zd = zotero_dir()
    db = zd / "zotero.sqlite"
    if not db.exists():
        return []
    tmp = Path(tempfile.gettempdir()) / "zotero_ro.sqlite"
    try:
        shutil.copy2(db, tmp)  # Zotero 运行时库被锁，复制一份再读
    except Exception:
        tmp = db
    conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    def field_map():
        rows = conn.execute(
            "SELECT f.fieldName, idv.value, id.itemID FROM itemData id "
            "JOIN fields f ON f.fieldID = id.fieldID "
            "JOIN itemDataValues idv ON idv.valueID = id.valueID"
        ).fetchall()
        m = {}
        for r in rows:
            m.setdefault(r["itemID"], {})[r["fieldName"]] = r["value"]
        return m

    fm = field_map()
    creators = {}
    for r in conn.execute(
        "SELECT ic.itemID, c.firstName, c.lastName, c.fieldMode FROM itemCreators ic "
        "JOIN creators c ON c.creatorID = ic.creatorID ORDER BY ic.orderIndex"
    ):
        name = (r["lastName"] or "") + (" " + r["firstName"] if r["firstName"] and r["fieldMode"] == 0 else "")
        creators.setdefault(r["itemID"], []).append(name.strip())

    atts = {}
    for r in conn.execute(
        "SELECT ia.parentItemID, ia.path, i.key FROM itemAttachments ia "
        "JOIN items i ON i.itemID = ia.itemID "
        "WHERE ia.contentType = 'application/pdf' AND ia.parentItemID IS NOT NULL"
    ):
        atts.setdefault(r["parentItemID"], []).append((r["path"] or "", r["key"]))

    rows = conn.execute(
        "SELECT i.itemID, i.key, it.typeName FROM items i JOIN itemTypes it ON it.itemTypeID = i.itemTypeID "
        "WHERE it.typeName NOT IN ('attachment','note','annotation') ORDER BY i.dateAdded DESC LIMIT ?",
        (limit,),
    ).fetchall()

    out = []
    for r in rows:
        meta = fm.get(r["itemID"], {})
        title = meta.get("title") or ""
        if query and query.lower() not in (title + " " + (meta.get("abstractNote") or "")).lower():
            continue
        pdf_path = ""
        for path, akey in atts.get(r["itemID"], []):
            if path.startswith("storage:"):
                cand = zd / "storage" / akey / path.split(":", 1)[1]
                if cand.exists():
                    pdf_path = str(cand)
                    break
            elif path:
                p = Path(path)
                if p.exists():
                    pdf_path = str(p)
                    break
        out.append(
            {
                "title": title,
                "authors": creators.get(r["itemID"], []),
                "year": (meta.get("date") or "")[:4],
                "venue": meta.get("publicationTitle") or meta.get("university") or "Zotero",
                "type": "journal-article",
                "doi": meta.get("DOI") or "",
                "url": meta.get("url") or "",
                "abstract": (meta.get("abstractNote") or "")[:2000],
                "pdf_url": pdf_path,
                "is_oa": bool(pdf_path),
                "citations": 0,
                "source": ["Zotero"],
                "language": "中文" if re.search(r"[\u4e00-\u9fff]{2,}", title) else "",
            }
        )
    conn.close()
    for rec in out:
        rec["key"] = record_key(rec)
    return out


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="Zotero 接入")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--guide", action="store_true")
    ap.add_argument("--import", dest="do_import", action="store_true")
    ap.add_argument("--root", default="")
    ap.add_argument("--query", default="")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    if args.guide or not (args.status or args.do_import):
        print(GUIDE)
        return 0

    if args.status:
        info = status()
        print_table(["项", "值"], [(k, str(v)) for k, v in info.items()])
        if not info["sqlite"] and not info["api"]:
            print("\n没找到 Zotero 数据。跑 --guide 看配置步骤。")
        else:
            print(f"\n可用接入方式: {'本地API' if info['api'] else ''}{' / ' if info['api'] and info['sqlite'] else ''}{'直接读库' if info['sqlite'] else ''}")
        return 0

    if args.do_import:
        root = ensure_tree(find_root(args.root) if args.root else Path.cwd())
        recs = read_sqlite(args.limit, args.query)
        if not recs:
            print("Zotero 里没读到条目。检查库路径，或先跑 --guide 配置。")
            return 1
        # 有 PDF 的复制进暂存区，后续 organize 当普通下载处理
        staging = root / DIR_CACHE / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        for i, r in enumerate(recs, 1):
            src = r.get("pdf_url") or ""
            if src and Path(src).exists():
                dest = staging / f"zotero_{i:03d}.pdf"
                try:
                    shutil.copy2(src, dest)
                    r["file"] = str(dest)
                    r["status"] = "公开"
                except Exception:
                    r["file"] = ""
                    r["status"] = "待手动获取"
            else:
                r["file"] = ""
                r["status"] = "待手动获取"
                r["reason"] = "Zotero 条目没有 PDF 附件（用 sci-pdf 补全后再导）"
        out = root / DIR_CACHE / "acquired.json"
        save_json(out, recs)
        print_table(
            ["年", "标题", "PDF"],
            [(r.get("year"), (r.get("title") or "")[:52], "Y" if r.get("file") else "-") for r in recs],
        )
        print(f"\n导入 {len(recs)} 条，其中有 PDF {sum(1 for r in recs if r.get('file'))} 条 → {out}")
        print("下一步：python organize.py --root .")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
