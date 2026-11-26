#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""索引层：manifest + 解析结果 + 精读笔记 → SQLite + 索引表.xlsx。

为什么要 SQLite：Excel 只能看，不能算。query.py 的按年/按刊/按标签/按表格查询都走 db，
改标签、改状态也只改 db，最后 reindex 一次把 Excel 重画出来，两边不会各说各话。

Excel 七个 sheet（列定义见 references/naming-and-layout.md §4）：
  总索引 / 待手动获取 / 解析状态 / 表格清单 / 标签统计 / 检索记录 / 说明
另出两份交付 md：未获取资料清单.md（DOI + 一键检索）与 文献获取指引.md（渠道 + 授权引导）

用法:
  python build_index.py --root survey_x
  python build_index.py --root survey_x --open      # 生成后直接打开
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from channels import CHANNELS, build_guide as build_channel_guide, recommend, search_url  # noqa: E402
from _common import (  # noqa: E402
    DIR_CACHE,
    DIR_INDEX,
    DIR_NOTES,
    DIR_REPORT,
    DIR_TEXT,
    ensure_tree,
    find_root,
    load_json,
    now_str,
    print_table,
    rel,
    setup_stdout,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  id TEXT PRIMARY KEY, title TEXT, title_zh TEXT, authors TEXT, year INTEGER,
  venue TEXT, type TEXT, language TEXT, access TEXT, if_ REAL, tier TEXT, tier_source TEXT,
  citations INTEGER, doi TEXT, url TEXT, stored TEXT, md TEXT, note TEXT,
  relevance INTEGER, tags TEXT, summary TEXT, findings TEXT, status TEXT,
  publisher TEXT, channel TEXT, reason TEXT, created_at TEXT,
  bytes INTEGER, pages INTEGER, chars INTEGER, text_layer TEXT, extract_method TEXT, ocr INTEGER, n_tables INTEGER
);
CREATE TABLE IF NOT EXISTS tables_ (
  doc_id TEXT, page INTEGER, idx INTEGER, rows INTEGER, cols INTEGER, csv TEXT, head TEXT
);
CREATE TABLE IF NOT EXISTS search_log (
  ts TEXT, query TEXT, kind TEXT, sources TEXT, raw INTEGER, merged INTEGER
);
"""

COLS = [
    ("ID", "id", 8),
    ("标题", "title", 46),
    ("中文题名", "title_zh", 26),
    ("第一作者", "first_author", 14),
    ("年份", "year", 7),
    ("期刊/来源", "venue", 26),
    ("类型", "type", 7),
    ("语言", "language", 7),
    ("获取状态", "access", 10),
    ("影响因子", "if_", 9),
    ("分区", "tier", 9),
    ("分区来源", "tier_source", 14),
    ("被引", "citations", 7),
    ("DOI", "doi", 30),
    ("链接", "url", 34),
    ("本地文件", "stored", 40),
    ("解析全文", "md", 26),
    ("笔记", "note", 26),
    ("相关度", "relevance", 8),
    ("标签", "tags", 22),
    ("一句话说明", "summary", 40),
    ("核心结论", "findings", 40),
    ("状态", "status", 12),
    ("入库时间", "created_at", 18),
]


def first_author(authors) -> str:
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except Exception:
            authors = [authors]
    if not authors:
        return ""
    a = str(authors[0])
    if re.search(r"[\u4e00-\u9fff]", a):
        return a
    parts = re.split(r"[\s,]+", a)
    return parts[-1] if parts else a


def derive_status(r: dict) -> str:
    """状态只从「有没有文件」和「解析结果」两个事实推出来，不信 manifest 里可能过期的字段。

    历史教训：organize 的异常分支把 9 条记录写成「归档失败」，Excel 直接把它当状态显示，
    看起来像 9 篇下载失败，实际是脚本自己把 Path("") 当成了当前目录。
    """
    has_file = bool(r.get("stored"))
    raw = (r.get("status") or "").strip()
    if not has_file:
        return "闭源" if raw == "闭源" else "待手动获取"
    if raw in ("解析失败", "需OCR", "归档失败", "待多模态提取"):
        return raw
    return "已入库"


# 各通道的标准动作，Excel 与 md 清单共用一份，避免两处话说得不一样
CHANNEL_STEPS = {
    "机构CARSI": "打开链接 → 出版方页面点 Sign in → 选择机构登录（CARSI/Shibboleth）→ 搜学校名 → 回到文章页下载 PDF",
    "Zotero+sci-pdf": "Zotero 装 sci-pdf 插件 → 右键条目 Update Sci-PDF → 首选项配好学校代理 → 下载到的 PDF 直接可用",
    "知网": "校内/机构入口进知网 → 按篇名检索 → 下载 CAJ/PDF",
    "图书馆文献传递": "图书馆文献传递系统提交 DOI → 等邮件（通常 1–2 个工作日）",
    "作者主页": "搜「作者名 + 论文标题 + pdf」→ 优先看课题组主页 / ResearchGate",
    "ResearchGate": "站内搜标题 → Request full-text → 收到后下载",
}
DEFAULT_STEP = "按链接打开 → 通过机构权限下载"


def link_of(d: dict) -> tuple:
    """未下载资料的可点地址，返回 (url, 标签)。

    优先级：DOI 解析 > 抓取时记下的落地页 > 按标题检索。
    DOI 比落地页耐用（落地页可能是临时的 am/pii 链接）；连 DOI 都没有时才退到检索，
    且标签写「检索」而不是「打开」，免得用户以为点进去就是全文。
    """
    doi = (d.get("doi") or "").strip()
    if doi:
        clean = doi.replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
        return "https://doi.org/" + clean, "打开"
    url = (d.get("url") or "").strip()
    if url:
        return url, "打开"
    title = (d.get("title") or "").strip()
    if title:
        return "https://www.bing.com/search?q=" + quote(f'"{title}" pdf'), "检索"
    return "", ""


def channel_of(d: dict) -> str:
    return (d.get("channel") or "机构CARSI").strip()


def parse_note(path: Path) -> dict:
    """笔记 frontmatter 只有三种字段会被读进来，其余当正文。"""
    out = {"tags": "", "summary": "", "findings": ""}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return out
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip().lower(), v.strip().strip('"').strip("'")
            if k in ("tags", "keywords", "标签"):
                out["tags"] = v.replace("[", "").replace("]", "").replace("'", "")
            elif k in ("summary", "一句话", "说明"):
                out["summary"] = v
            elif k in ("findings", "conclusion", "核心结论"):
                out["findings"] = v
    return out


def build_db(root: Path, docs: list, tables: list, logs: list) -> sqlite3.Connection:
    db_path = root / DIR_INDEX / "library.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    for d in docs:
        conn.execute(
            """INSERT OR REPLACE INTO docs VALUES (
                :id,:title,:title_zh,:authors,:year,:venue,:type,:language,:access,:if_,:tier,:tier_source,
                :citations,:doi,:url,:stored,:md,:note,:relevance,:tags,:summary,:findings,:status,
                :publisher,:channel,:reason,:created_at,:bytes,:pages,:chars,:text_layer,:extract_method,:ocr,:n_tables)""",
            d,
        )
    for t in tables:
        conn.execute("INSERT INTO tables_ VALUES (?,?,?,?,?,?,?)",
                     (t["doc_id"], t["page"], t["idx"], t["rows"], t["cols"], t["csv"], t["head"]))
    for lg in logs:
        conn.execute("INSERT INTO search_log VALUES (?,?,?,?,?,?)",
                     (lg.get("time", ""), lg.get("query", ""), lg.get("kind", ""),
                      json.dumps(lg.get("sources", {}), ensure_ascii=False),
                      lg.get("raw", 0), lg.get("merged", 0)))
    conn.commit()
    return conn


def style_sheet(ws, headers, widths):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    head_fill = PatternFill("solid", fgColor="1F4E79")
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = widths[c - 1]
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"
    # 24 列的宽表必须横向 + 按宽缩放，否则打印/导 PDF 会被切成十几页
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:1"


def fit_row_heights(ws, headers, widths, wrap_keys):
    """openpyxl 不会自动算行高，wrap_text 的单元格会溢到下一行叠字。

    按「字符视觉宽度 / 列宽」估行数并写死行高。中文按 2 个字宽计。
    """

    def vlen(s: str) -> int:
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(s))

    for r in range(2, ws.max_row + 1):
        lines = 1
        for c, h in enumerate(headers, 1):
            if h not in wrap_keys:
                continue
            val = ws.cell(row=r, column=c).value
            if not val:
                continue
            per = max(6, widths[c - 1] * 1.0)
            lines = max(lines, -(-vlen(val) // per))
        ws.row_dimensions[r].height = min(15 * lines + 4, 120)


def write_xlsx(root: Path, docs: list, tables: list, logs: list) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    index_dir = root / DIR_INDEX

    def href(p: str) -> str:
        """Excel 超链接按工作簿所在目录解析，统一转成 00_索引 的相对路径。"""
        return "../" + p.replace("\\", "/")

    # ---------- 总索引
    ws = wb.active
    ws.title = "总索引"
    headers = [c[0] for c in COLS]
    style_sheet(ws, headers, [c[2] for c in COLS])
    warn_fill = PatternFill("solid", fgColor="FFF2CC")   # 闭源/待手动获取
    bad_fill = PatternFill("solid", fgColor="FCE4E4")    # 解析失败/需OCR
    todo_fill = PatternFill("solid", fgColor="DDEBF7")   # 待多模态提取（中间态，不是错误）
    for r, d in enumerate(docs, 2):
        for c, (_, key, _w) in enumerate(COLS, 1):
            val = d.get(key, "")
            if key == "first_author":
                val = first_author(d.get("authors") or [])
            elif key == "url":
                # 只有 DOI 没有落地页的记录，也用 DOI 解析地址填上，别留空
                val = val or link_of(d)[0]
            cell = ws.cell(row=r, column=c, value=val)
            cell.alignment = Alignment(vertical="top", wrap_text=key in ("title", "title_zh", "summary", "findings", "tags"))
            if key in ("stored", "md", "note"):
                if val:
                    cell.hyperlink = href(str(val))
                    cell.font = Font(color="0563C1", underline="single", size=9)
                else:
                    cell.value = ""
            elif key == "url" and val:
                cell.hyperlink = val
                cell.font = Font(color="0563C1", underline="single", size=9)
            elif key == "id":
                cell.font = Font(bold=(d.get("relevance") or 0) >= 4)
        status = (d.get("status") or "")
        if status in ("待手动获取", "闭源"):
            for c in range(1, len(COLS) + 1):
                ws.cell(row=r, column=c).fill = warn_fill
        elif status in ("解析失败", "需OCR"):
            for c in range(1, len(COLS) + 1):
                ws.cell(row=r, column=c).fill = bad_fill
        elif status == "待多模态提取":
            for c in range(1, len(COLS) + 1):
                ws.cell(row=r, column=c).fill = todo_fill

    # ---------- 待手动获取
    ws2 = wb.create_sheet("待手动获取")
    h2 = ["ID", "标题", "年份", "DOI", "链接", "出版方", "推荐通道", "一键检索", "失败原因", "操作说明", "预计耗时"]
    style_sheet(ws2, h2, [8, 44, 7, 24, 28, 18, 14, 18, 20, 46, 12])
    pending = [x for x in docs if not x.get("stored")]
    for r, d in enumerate(pending, 2):
        url, label = link_of(d)
        step = CHANNEL_STEPS.get(channel_of(d), DEFAULT_STEP)
        # 检索兜底链接的原始 URL 又长又没信息量，单元格显示「按标题检索」，超链接指到该 URL
        shown_link = url if label == "打开" else label
        # 中文库/机构库一键检索：按标题搜，不用 DOI（多数库对 DOI 字符串召回极差）
        ch_keys = recommend(d)
        first_key = ch_keys[0] if ch_keys else "baiduxueshu"
        search_target = search_url(first_key, d.get("title") or d.get("doi") or "")
        search_label = CHANNELS[first_key][0] if first_key in CHANNELS else "按标题检索"
        vals = [d.get("id"), d.get("title"), d.get("year"), d.get("doi"), shown_link,
                d.get("publisher") or d.get("venue"), channel_of(d), search_label,
                d.get("reason") or "", step, "3–10 分钟"]
        for c, val in enumerate(vals, 1):
            cell = ws2.cell(row=r, column=c, value=val)
            cell.alignment = Alignment(vertical="top", wrap_text=c in (2, 4, 5, 6, 9, 10))
        if url:
            for c in (2, 5):
                ws2.cell(row=r, column=c).hyperlink = url
                ws2.cell(row=r, column=c).font = Font(color="0563C1", underline="single", size=9)
        if search_target:
            ws2.cell(row=r, column=8).hyperlink = search_target
            ws2.cell(row=r, column=8).font = Font(color="0563C1", underline="single", size=9)
    if not pending:
        ws2.cell(row=2, column=1, value="（本次全部获取成功，无待手动项）")

    # ---------- 解析状态
    ws3 = wb.create_sheet("解析状态")
    h3 = ["ID", "文件名", "页数", "字符数", "每页均字符", "文本层", "取文方式", "是否OCR", "表格数", "耗时秒"]
    style_sheet(ws3, h3, [8, 44, 7, 10, 12, 9, 13, 9, 8, 9])
    for r, d in enumerate([x for x in docs if x.get("pages")], 2):
        per = round((d.get("chars") or 0) / max(1, d.get("pages") or 1))
        for c, val in enumerate(
            [d.get("id"), Path(d.get("stored") or "").name, d.get("pages"), d.get("chars"),
             per, d.get("text_layer"), d.get("extract_method") or "文本层",
             "是" if d.get("ocr") else "否", d.get("n_tables") or 0,
             d.get("parse_seconds") or ""], 1
        ):
            ws3.cell(row=r, column=c, value=val)

    # ---------- 表格清单
    ws4 = wb.create_sheet("表格清单")
    h4 = ["ID", "文献", "页码", "表号", "表名/首行", "行×列", "CSV路径", "备注"]
    style_sheet(ws4, h4, [8, 40, 7, 7, 50, 10, 34, 18])
    title_of = {d.get("id"): (d.get("title_zh") or d.get("title") or "")[:38] for d in docs}
    for r, t in enumerate(tables, 2):
        for c, val in enumerate(
            [t["doc_id"], title_of.get(t["doc_id"], ""), t["page"], t["idx"], t["head"],
             f'{t["rows"]}×{t["cols"]}', t["csv"], ""], 1
        ):
            cell = ws4.cell(row=r, column=c, value=val)
            if c == 7 and val:
                cell.hyperlink = href(f"{DIR_TEXT}/{val}")
                cell.font = Font(color="0563C1", underline="single", size=9)

    # ---------- 标签统计
    ws5 = wb.create_sheet("标签统计")
    h5 = ["标签", "数量", "代表ID（前3）"]
    style_sheet(ws5, h5, [24, 8, 30])
    counter = Counter()
    reps = {}
    for d in docs:
        for tag in re.split(r"[;,；、\s]+", str(d.get("tags") or "")):
            tag = tag.strip()
            if not tag:
                continue
            counter[tag] += 1
            reps.setdefault(tag, [])
            if len(reps[tag]) < 3:
                reps[tag].append(d.get("id"))
    for r, (tag, n) in enumerate(counter.most_common(), 2):
        for c, val in enumerate([tag, n, ", ".join(reps[tag])], 1):
            ws5.cell(row=r, column=c, value=val)

    # ---------- 检索记录
    ws6 = wb.create_sheet("检索记录")
    h6 = ["时间", "关键词", "类型", "数据源状态", "原始条数", "去重后"]
    style_sheet(ws6, h6, [20, 44, 9, 60, 10, 9])
    for r, lg in enumerate(logs, 2):
        for c, val in enumerate(
            [lg.get("time"), lg.get("query"), lg.get("kind"),
             json.dumps(lg.get("sources", {}), ensure_ascii=False), lg.get("raw"), lg.get("merged")], 1
        ):
            ws5c = ws6.cell(row=r, column=c, value=val)
            ws5c.alignment = Alignment(vertical="top", wrap_text=c in (2, 4))

    # ---------- 说明（不打扰用户，只在索引里留一句"需要时怎么做"）
    ws7 = wb.create_sheet("说明")
    style_sheet(ws7, ["项目", "说明"], [22, 110])
    notes = [
        ("表格提取", "当前表格由 pdfplumber 从 PDF 文本层抽取。合并单元格、三线表等高精度需求：安装 OCR 后让 agent 运行 table_ocr.py extract 进一步提取，结果落 03_解析全文/tables/。"),
        ("OCR 选型", "优先探测本机 MiniOCR；截至 2026-11 未查到官方 MiniOCR-0.7B/1.5B 版本。可选 HunyuanOCR-1.5（1B）或 PaddleOCR-VL（约 0.9B），链接见 references/table-ocr.md。"),
        ("网页资料", "公开网页原件归入 02_开放资料/网页存档，并已转成 03_解析全文/md/ 可全文检索。"),
    ]
    for r, (k, v) in enumerate(notes, 2):
        ws7.cell(row=r, column=1, value=k)
        c = ws7.cell(row=r, column=2, value=v)
        c.alignment = Alignment(vertical="top", wrap_text=True)
        ws7.row_dimensions[r].height = 42

    # 行高按内容估算（openpyxl 不会自动 fit），否则换行的标题会叠到下一行
    fit_row_heights(ws, headers, [c[2] for c in COLS], {"标题", "中文题名", "一句话说明", "核心结论", "标签"})
    fit_row_heights(ws2, h2, [8, 44, 7, 24, 28, 18, 14, 18, 20, 46, 12],
                    {"标题", "DOI", "链接", "出版方", "失败原因", "操作说明"})
    fit_row_heights(ws4, h4, [8, 40, 7, 7, 50, 10, 34, 18], {"表名/首行"})
    fit_row_heights(ws6, h6, [20, 44, 9, 60, 10, 9], {"关键词", "数据源状态"})

    out = index_dir / "索引表.xlsx"
    wb.save(out)
    # CSV 备份：给不会用 Excel 的 agent/脚本留一条路
    with (index_dir / "索引表.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for d in docs:
            row = []
            for _, key, _w in COLS:
                row.append(first_author(d.get("authors") or []) if key == "first_author" else d.get(key, ""))
            w.writerow(row)
    return out


def write_pending_list(root: Path, docs: list) -> Path | None:
    """未获取资料单独出一份可点清单。

    为什么不能只放在 Excel 里：用户多半是在浏览器里点着下载，Excel 的 DOI 是纯文本、复制粘贴多一步；
    而且这份清单要能单独发给别人（师兄/文献传递员），得是自解释的。
    """
    pending = [d for d in docs if not d.get("stored")]
    out = root / "05_报告" / "未获取资料清单.md"
    if not pending:
        # 上一轮生成的清单如果还在，会被当成"当前仍未获取"，必须改写掉而不是留着
        if out.exists():
            out.write_text(
                "# 未获取资料清单\n\n> 本次全部获取成功，无待手动项。\n", encoding="utf-8"
            )
            return out
        return None

    groups = {}
    for d in pending:
        groups.setdefault(channel_of(d), []).append(d)

    L = [
        "# 未获取资料清单",
        "",
        f"> 共 {len(pending)} 条 ｜ 生成于 {now_str()}",
        "> 这些资料公开渠道拿不到全文（闭源或网络不可达），按获取通道分组，逐条给出 DOI 与可点链接。",
        "> 拿到 PDF 后放进库根目录的 `.cache/staging/`，跑 `organize.py` 即自动归位入库。",
        "",
    ]

    for ch in sorted(groups, key=lambda c: -len(groups[c])):
        items = sorted(groups[ch], key=lambda d: str(d.get("id")))
        L += [f"## {ch}（{len(items)} 条）", ""]
        for d in items:
            url, label = link_of(d)
            doi = (d.get("doi") or "").strip()
            year = d.get("year") or ""
            title = (d.get("title") or "").strip() or "（无标题）"
            pub = (d.get("publisher") or d.get("venue") or "").strip()
            reason = (d.get("reason") or "").strip()
            bits = [f"**{d.get('id')}**", str(year)] if year else [f"**{d.get('id')}**"]
            head = " · ".join(bits) + f"  {title}"
            tail = []
            tail.append(f"DOI: `{doi}`" if doi else "DOI: —（无）")
            if url:
                tail.append(f"[{label}]({url})")
            if pub:
                tail.append(pub)
            if reason:
                tail.append(f"失败原因：{reason}")
            # 一键检索：站内按标题搜，比拿 DOI 去搜命中率高得多
            for k in recommend(d)[:2]:
                if k in CHANNELS:
                    tail.append(f"[{CHANNELS[k][0]}检索]({search_url(k, title)})")
            L.append(f"- {head} ｜ " + " ｜ ".join(tail))
        L.append("")

    L += ["## 各通道怎么操作", ""]
    for ch in sorted(groups, key=lambda c: -len(groups[c])):
        L += [f"**{ch}**：{CHANNEL_STEPS.get(ch, DEFAULT_STEP)}", ""]

    L += [
        "## 拿到之后",
        "",
        "```bash",
        "# 1) 下载的 PDF 放进暂存目录（文件名随意，organize 会重命名）",
        "cp <下载的文件> .cache/staging/",
        "python <S>/organize.py --root .",
        "python <S>/pdf_to_md.py --root .",
        "python <S>/build_index.py --root .",
        "```",
        "",
        "已是用户自己 Zotero 里条目的，直接 `python <S>/zotero_bridge.py --import --root .`，不用手动搬文件。",
        "",
        "## 更多渠道与机构授权",
        "",
        f"另有 **{len(CHANNELS)}** 个中文/机构/开放检索渠道（知网、万方、维普、百度学术、NSTL、"
        "Web of Science、Scopus、EI、AIAA、ASME 等），含一键检索链接和逐渠道下载步骤，"
        "见 `文献获取指引.md`。",
        "若你有学校/院所的图书馆数据库授权，可以在那里按渠道获得具体下载指导；"
        "没有授权就直接走 NSTL 文献传递，不影响交付。",
        "",
    ]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="生成 SQLite + Excel 索引")
    ap.add_argument("--root", default="")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    root = ensure_tree(find_root(args.root) if args.root else Path.cwd())

    manifest = load_json(root / DIR_CACHE / "manifest_final.json", default=[]) or []
    if not manifest:
        print(f"{root}/.cache/manifest_final.json 为空，先跑 organize.py。")
        return 1

    notes = {}
    note_dir = root / DIR_NOTES
    if note_dir.exists():
        for p in sorted(note_dir.glob("*.md")):
            m = re.match(r"^(P|O)\d{3}", p.name)
            if m:
                notes[m.group(0)] = p

    docs = []
    for r in manifest:
        nid = r.get("id")
        note_path = notes.get(nid)
        note_meta = parse_note(note_path) if note_path else {"tags": "", "summary": "", "findings": ""}
        parse = r.get("parse") or {}
        docs.append(
            {
                "id": nid,
                "title": r.get("title") or "",
                "title_zh": r.get("title_zh") or "",
                "authors": json.dumps(r.get("authors") or [], ensure_ascii=False),
                "year": r.get("year") or "",
                "venue": r.get("venue") or "",
                "type": r.get("_type_code") or "",
                "language": r.get("language") or "",
                "access": r.get("_access") or ("闭源" if not r.get("stored") else "公开"),
                "if_": r.get("if_"),
                "tier": r.get("tier") or "",
                "tier_source": r.get("tier_source") or "",
                "citations": r.get("citations") or 0,
                "doi": r.get("doi") or "",
                "url": r.get("url") or "",
                "stored": r.get("stored") or "",
                "md": rel(root / DIR_TEXT / "md" / f"{nid}.md", root) if (root / DIR_TEXT / "md" / f"{nid}.md").exists() else "",
                "note": rel(note_path, root) if note_path else "",
                "relevance": r.get("relevance") or 0,
                "tags": note_meta["tags"] or r.get("tags") or "",
                "summary": note_meta["summary"] or r.get("summary") or "",
                "findings": note_meta["findings"] or r.get("findings") or "",
                "status": derive_status(r),
                "publisher": r.get("publisher") or "",
                "channel": r.get("channel") or "",
                "reason": r.get("reason") or "",
                "created_at": r.get("created_at") or "",
                "bytes": r.get("bytes") or 0,
                "pages": parse.get("pages") or 0,
                "chars": parse.get("chars") or 0,
                "text_layer": parse.get("text_layer") or "",
                "extract_method": parse.get("extract_method") or "",
                "ocr": 1 if parse.get("ocr") else 0,
                "n_tables": parse.get("tables") or 0,
                "parse_seconds": parse.get("seconds") or "",
            }
        )

    tables = []
    tables_dir = root / DIR_TEXT / "tables"
    if tables_dir.exists():
        for p in sorted(tables_dir.glob("*.csv")):
            m = re.match(r"^((?:P|O)\d{3})_p(\d+)t(\d+)\.csv$", p.name)
            if not m:
                continue
            rows = 0
            head = ""
            try:
                with p.open(encoding="utf-8-sig") as f:
                    rd = list(csv.reader(f))
                rows = len(rd)
                head = " | ".join((rd[0][0][:22], rd[0][1][:22])) if rd and len(rd[0]) > 1 else (rd[0][0][:44] if rd else "")
                cols = max((len(r) for r in rd), default=0)
            except Exception:
                cols = 0
            tables.append(
                {"doc_id": m.group(1), "page": int(m.group(2)), "idx": int(m.group(3)),
                 "rows": rows, "cols": cols, "csv": f"tables/{p.name}", "head": head}
            )

    logs = load_json(root / DIR_INDEX / "检索记录.json", default=[]) or []
    build_db(root, docs, tables, logs)
    xlsx = write_xlsx(root, docs, tables, logs)
    pending_md = write_pending_list(root, docs)
    channel_md = build_channel_guide(root)

    ok = sum(1 for d in docs if d["stored"])
    print_table(
        ["指标", "数量"],
        [
            ("总记录", len(docs)),
            ("已入库文件", ok),
            ("待手动获取", len(docs) - ok),
            ("已解析", sum(1 for d in docs if d["pages"])),
            ("其中多模态", sum(1 for d in docs if d.get("extract_method") == "多模态")),
            ("待多模态提取", sum(1 for d in docs if d["status"] == "待多模态提取")),
            ("抽出表格", len(tables)),
            ("高精度表格提取", "未启用（需要时跑 table_ocr.py hint）"),
            ("有笔记", sum(1 for d in docs if d["note"])),
            ("待核实分区", sum(1 for d in docs if d["tier"] == "待核实")),
            ("检索批次", len(logs)),
        ],
    )
    print(f"\n索引表: {xlsx}")
    print(f"数据库: {root / DIR_INDEX / 'library.db'}")
    if pending_md:
        print(f"未获取清单: {pending_md}   （带 DOI 与可点链接，可直接转发/打印）")
    else:
        print("未获取清单: 无待手动项（本次全部获取成功）")
    print(f"渠道与授权指引: {channel_md}   （{len(CHANNELS)} 个渠道，含机构授权询问话术）")
    if args.open:
        import os

        os.startfile(str(xlsx))  # noqa: S606
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
