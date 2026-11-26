#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出层：Markdown → 基金申请书版式的 docx。

版式对齐本子正文习惯：A4、正文宋体小四、1.5 倍行距、首行缩进 2 字符、
标题黑体加粗、参考文献五号悬挂缩进、图注居中五号。目的是直接贴进申请书不用再排版。

用法:
  python export_docx.py --root survey_x "05_报告/研究现状_xxx.md"
  python export_docx.py --root survey_x a.md --out b.docx
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DIR_REPORT, ensure_tree, find_root, setup_stdout  # noqa: E402

try:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
except Exception:
    Document = None

BODY_PT = 12          # 小四
SMALL_PT = 10.5       # 五号
LINE_SPACING = 1.5


def set_font(run, cn="宋体", en="Times New Roman", size=BODY_PT, bold=None, color=None):
    run.font.name = en
    run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), cn)


def add_para(doc, text="", *, cn="宋体", en="Times New Roman", size=BODY_PT, bold=False,
             align=None, indent=True, spacing=LINE_SPACING, first_line_cm=0.74, space_after=6,
             hanging=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing = spacing
    pf.space_after = Pt(space_after)
    pf.space_before = Pt(0)
    if align is not None:
        p.alignment = align
    if first_line_cm is not None:
        if hanging:
            pf.left_indent = Cm(first_line_cm)
            pf.first_line_indent = Cm(-first_line_cm)
        elif indent:
            pf.first_line_indent = Cm(first_line_cm)
    # 行内 **加粗**
    for i, seg in enumerate(re.split(r"\*\*", text)):
        if not seg:
            continue
        set_font(p.add_run(seg), cn=cn, en=en, size=size, bold=(bold or i % 2 == 1))
    return p


def _vlen(s: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(s))


def add_table(doc, rows, total_cm=14.6):
    if not rows:
        return
    cols = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=cols)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False

    # 均分列宽会把「年份」「阶段」这种短列撑得很空、把长文本列挤到换行。
    # 按实际厘米估需求宽度：五号字下 ASCII ≈0.185cm/字，中文按 2 倍算，再留 0.45cm 内边距。
    need = []
    for c in range(cols):
        longest = max((_vlen(r[c]) if c < len(r) else 0) for r in rows[:21])
        need.append(max(1.5, longest * 0.185 + 0.45))

    # 超宽时不能等比缩：等比缩会把「年份」这类短列压到折行，而长文本列本来就该折。
    # 策略是短列（≤2.9cm）保足需求，剩余宽度由长列按比例分担。
    short_i = [i for i, n in enumerate(need) if n <= 2.9]
    long_i = [i for i, n in enumerate(need) if n > 2.9]
    if sum(need) <= total_cm:
        widths = [n * total_cm / sum(need) for n in need]
    elif sum(need[i] for i in short_i) >= total_cm:
        widths = [max(1.3, n * total_cm / sum(need)) for n in need]
    else:
        rest = total_cm - sum(need[i] for i in short_i)
        pool = sum(need[i] for i in long_i) or 1
        widths = [0.0] * cols
        for i in short_i:
            widths[i] = need[i]
        for i in long_i:
            widths[i] = max(1.8, need[i] * rest / pool)

    for c, w in enumerate(widths):
        width = Cm(w)
        # 只设 cell.width 不够：Word/LibreOffice 以 tblGrid 的 gridCol 为排版依据，
        # 两者都要写，否则列宽还是均分（实测年份列照样折行）。
        t.columns[c].width = width
        for cell in t.columns[c].cells:
            cell.width = width

    for r, row in enumerate(rows):
        for c in range(cols):
            cell = t.cell(r, c)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.line_spacing = 1.0
            p.paragraph_format.space_after = Pt(2)
            val = row[c] if c < len(row) else ""
            run = p.add_run(val)
            set_font(run, size=SMALL_PT, bold=(r == 0))


def add_image(doc, path: Path, caption=""):
    if not path.exists():
        print(f"  [warn] 图片不存在，跳过: {path}")
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run()
    width = Cm(14.5)
    run.add_picture(str(path), width=width)
    if caption:
        add_para(doc, caption, size=SMALL_PT, align=WD_ALIGN_PARAGRAPH.CENTER,
                 indent=False, first_line_cm=None, space_after=10)


def parse_md(md_text: str):
    """把 md 拆成块，交给 docx 渲染。表格按连续 | 行聚合。"""
    blocks, buf = [], []
    for raw in md_text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("|"):
            buf.append(line)
            continue
        if buf:
            blocks.append(("table", buf))
            buf = []
        blocks.append(("line", line))
    if buf:
        blocks.append(("table", buf))
    return blocks


def convert(md_path: Path, out_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(2.54)
    sec.left_margin = sec.right_margin = Cm(3.17)
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(BODY_PT)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    pending_img = None
    for kind, payload in parse_md(text):
        if kind == "table":
            rows = []
            for line in payload:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    continue
                rows.append(cells)
            add_table(doc, rows)
            doc.add_paragraph()
            continue

        line = payload.strip()
        if not line:
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            size = 16 if level == 1 else 14 if level == 2 else BODY_PT
            # 本子的 1.3/1.4/参考文献 都是左对齐的编号标题，不要居中
            add_para(doc, title, cn="黑体", size=size, bold=True, indent=False,
                     first_line_cm=None, space_after=8)
            continue
        if line.startswith("!["):
            im = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line)
            if im:
                pending_img = md_path.parent / im.group(2)
            continue
        if re.match(r"^图\s*\d", line):
            if pending_img:
                add_image(doc, pending_img, line)
                pending_img = None
            else:
                add_para(doc, line, size=SMALL_PT, align=WD_ALIGN_PARAGRAPH.CENTER,
                         indent=False, first_line_cm=None)
            continue
        if line.startswith(">"):
            add_para(doc, line.lstrip("> ").strip(), size=BODY_PT, indent=True)
            continue
        if re.match(r"^\[\d+\]\s", line):
            add_para(doc, line, size=SMALL_PT, indent=False, first_line_cm=0.74, hanging=True, space_after=2)
            continue
        if line.startswith(("---", "***")):
            continue
        add_para(doc, line)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="md → 基金版式 docx")
    ap.add_argument("md")
    ap.add_argument("--root", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if Document is None:
        print("缺少 python-docx：pip install python-docx")
        return 1

    root = ensure_tree(find_root(args.root) if args.root else Path.cwd())
    md_path = Path(args.md)
    if not md_path.is_absolute():
        md_path = (root / args.md) if (root / args.md).exists() else Path(args.md)
    if not md_path.exists():
        print(f"找不到 {md_path}")
        return 1

    out = Path(args.out) if args.out else md_path.with_suffix(".docx")
    convert(md_path, out)
    size = out.stat().st_size
    print(f"已导出: {out}  ({size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
