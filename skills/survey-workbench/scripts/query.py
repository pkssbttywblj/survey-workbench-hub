#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交互层：按库查询、打标、改字段、全文检索、抽表、生成笔记卡片。

设计原则：所有写操作只改 SQLite，Excel 由 build_index.py 重画。
这样「反复交互」不会出现 Excel 被手改、db 没跟上，两边数字打架的情况。

子命令:
  stats                              库健康度
  list [--year 2023] [--venue 关键词] [--tag 标签] [--tier 1区] [--status 已入库] [-n 30]
  show P003                          完整记录
  note P003                          生成/补全精读笔记卡片
  tag P003 --add "方法;数据驱动" [--remove "旧标签"]
  set P003 --field summary --value "一句话"
  grep "关键词" [-n 20]              跨全部解析全文检索，返回 文献+页码+上下文
  tables [--id P003] [--print 3]     列出/打印抽出的表格
  export --out x.md|csv             导出清单

用法:
  python query.py stats --root survey_x
  python query.py grep "peak pressure" --root survey_x -n 5
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    DIR_INDEX,
    DIR_NOTES,
    DIR_TEXT,
    find_root,
    print_table,
    setup_stdout,
    slug,
)

NOTE_TEMPLATE = """---
id: {id}
tags: 
summary: 
findings: 
---

# {id} {title}

- 元数据：{authors} | {year} | {venue} | {doi}
- 分区：{tier}（{tier_source}） 影响因子：{if_} 被引：{citations}
- 获取：{access} | 本地：{stored}
- 全文：{md}

## 一句话贡献

## 问题与方法

## 关键结果（带数值）

## 局限

## 与本课题的关系

## 可用表格

## 证据
- [p. ] ""

## 标签
"""


def conn_of(root: Path) -> sqlite3.Connection:
    db = root / DIR_INDEX / "library.db"
    if not db.exists():
        print(f"找不到 {db}，先跑 build_index.py。")
        raise SystemExit(1)
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    return c


def cmd_stats(c, root, args):
    rows = c.execute("SELECT status, COUNT(*) n FROM docs GROUP BY status").fetchall()
    tiers = c.execute("SELECT tier, COUNT(*) n FROM docs GROUP BY tier ORDER BY n DESC").fetchall()
    venues = c.execute("SELECT venue, COUNT(*) n FROM docs GROUP BY venue ORDER BY n DESC LIMIT 8").fetchall()
    print_table(["状态", "数量"], [(r["status"], r["n"]) for r in rows])
    print()
    print_table(["分区", "数量"], [(r["tier"] or "-", r["n"]) for r in tiers])
    print()
    print_table(["主要期刊/来源", "数量"], [(r["venue"] or "-", r["n"]) for r in venues])


def build_where(args):
    where, params = [], []
    if args.year:
        where.append("year = ?")
        params.append(int(args.year))
    if args.venue:
        where.append("venue LIKE ?")
        params.append(f"%{args.venue}%")
    if args.tag:
        where.append("tags LIKE ?")
        params.append(f"%{args.tag}%")
    if args.tier:
        where.append("tier LIKE ?")
        params.append(f"%{args.tier}%")
    if args.status:
        where.append("status = ?")
        params.append(args.status)
    if args.type:
        where.append("type = ?")
        params.append(args.type)
    if args.min_rel:
        where.append("relevance >= ?")
        params.append(args.min_rel)
    return (" WHERE " + " AND ".join(where)) if where else "", params


def cmd_list(c, root, args):
    sql, params = build_where(args)
    sql = f"SELECT * FROM docs{sql} ORDER BY year DESC, relevance DESC LIMIT ?"
    rows = c.execute(sql, params + [args.n]).fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return
    print_table(
        ["ID", "年", "分区", "状态", "标题"],
        [(r["id"], r["year"], r["tier"], r["status"], (r["title_zh"] or r["title"] or "")[:56]) for r in rows],
    )
    print(f"\n{len(rows)} 条")


def cmd_show(c, root, args):
    row = c.execute("SELECT * FROM docs WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"没有 {args.id}")
        return
    d = dict(row)
    for k in ("title", "title_zh", "authors", "year", "venue", "type", "tier", "if_", "citations",
              "doi", "url", "access", "stored", "md", "note", "tags", "summary", "findings", "status"):
        if d.get(k) not in ("", None):
            print(f"{k:12s} {d[k]}")
    tabs = c.execute("SELECT page, idx, rows, cols, csv, head FROM tables_ WHERE doc_id = ? ORDER BY page, idx", (args.id,)).fetchall()
    if tabs:
        print()
        print_table(["页", "表", "行×列", "首行", "CSV"], [(t["page"], t["idx"], f'{t["rows"]}×{t["cols"]}', t["head"][:40], t["csv"]) for t in tabs])


def cmd_note(c, root, args):
    row = c.execute("SELECT * FROM docs WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"没有 {args.id}")
        return
    d = dict(row)
    notes = root / DIR_NOTES
    notes.mkdir(parents=True, exist_ok=True)
    existing = sorted(notes.glob(f"{d['id']}_*.md"))
    if existing:
        print(f"笔记已存在: {existing[0]}")
        return
    name = f"{d['id']}_{slug(d.get('title_zh') or d.get('title') or 'note', 24)}.md"
    path = notes / name
    path.write_text(
        NOTE_TEMPLATE.format(
            id=d["id"], title=d.get("title") or "", authors=d.get("authors") or "",
            year=d.get("year") or "", venue=d.get("venue") or "", doi=d.get("doi") or "",
            tier=d.get("tier") or "", tier_source=d.get("tier_source") or "",
            if_=d.get("if_") if d.get("if_") is not None else "待核实",
            citations=d.get("citations") or 0, access=d.get("access") or "",
            stored=d.get("stored") or "", md=d.get("md") or "",
        ),
        encoding="utf-8",
    )
    c.execute("UPDATE docs SET note = ? WHERE id = ?", (path.relative_to(root).as_posix(), d["id"]))
    c.commit()
    print(f"已生成 {path}")


def cmd_tag(c, root, args):
    row = c.execute("SELECT tags FROM docs WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"没有 {args.id}")
        return
    tags = [t.strip() for t in re.split(r"[;,；、]", row["tags"] or "") if t.strip()]
    for t in [x.strip() for x in re.split(r"[;,；、]", args.add or "") if x.strip()]:
        if t not in tags:
            tags.append(t)
    for t in [x.strip() for x in re.split(r"[;,；、]", args.remove or "") if x.strip()]:
        if t in tags:
            tags.remove(t)
    c.execute("UPDATE docs SET tags = ? WHERE id = ?", (";".join(tags), args.id))
    c.commit()
    print(f"{args.id} 标签 → {';'.join(tags) or '(空)'}")


def cmd_set(c, root, args):
    allowed = {"title_zh", "summary", "findings", "tags", "status", "tier", "tier_source", "if_", "relevance", "access"}
    if args.field not in allowed:
        print(f"只能改: {', '.join(sorted(allowed))}")
        return
    val = args.value
    if args.field in ("if_",):
        val = float(val) if val else None
    elif args.field == "relevance":
        val = int(val)
    c.execute(f"UPDATE docs SET {args.field} = ? WHERE id = ?", (val, args.id))
    c.commit()
    print(f"{args.id}.{args.field} ← {val}")


def iter_pages(txt_path: Path):
    try:
        text = txt_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    cur, buf = None, []
    for line in text.splitlines():
        m = re.match(r"^=== \[Page (\d+)\] ===$", line.strip())
        if m:
            if cur is not None:
                yield cur, "\n".join(buf)
            cur, buf = int(m.group(1)), []
        else:
            buf.append(line)
    if cur is not None:
        yield cur, "\n".join(buf)


def cmd_grep(c, root, args):
    """全文检索：命中给 文献/页/上下文三行，直接能用来写引文。"""
    pat = re.compile(args.pattern, re.I)
    hits = []
    for txt in sorted((root / DIR_TEXT / "txt").glob("*.txt")):
        doc_id = txt.stem
        for pno, page_text in iter_pages(txt):
            for m in pat.finditer(page_text):
                s = max(0, m.start() - 90)
                ctx = re.sub(r"\s+", " ", page_text[s : m.end() + 110]).strip()
                hits.append({"id": doc_id, "page": pno, "ctx": ctx})
                if len(hits) >= args.n:
                    break
            if len(hits) >= args.n:
                break
        if len(hits) >= args.n:
            break
    if args.json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
        return
    for h in hits:
        print(f"[{h['id']} p.{h['page']}] …{h['ctx']}…")
    print(f"\n命中 {len(hits)} 处")


def cmd_tables(c, root, args):
    sql = "SELECT * FROM tables_"
    params = []
    if args.id:
        sql += " WHERE doc_id = ?"
        params.append(args.id)
    sql += " ORDER BY doc_id, page, idx"
    rows = c.execute(sql, params).fetchall()
    if args.print_rows:
        for r in rows[: args.print_rows]:
            print(f"\n=== {r['doc_id']} p.{r['page']} 表{r['idx']} ({r['csv']}) ===")
            p = root / DIR_TEXT / r["csv"].replace("tables/", "tables/")
            if p.exists():
                print(p.read_text(encoding="utf-8-sig")[:1500])
        return
    print_table(
        ["ID", "页", "表", "行×列", "首行", "CSV"],
        [(r["doc_id"], r["page"], r["idx"], f'{r["rows"]}×{r["cols"]}', r["head"][:38], r["csv"]) for r in rows],
    )
    print(f"\n{len(rows)} 张表")


def cmd_export(c, root, args):
    rows = c.execute("SELECT * FROM docs ORDER BY id").fetchall()
    out = Path(args.out)
    if out.suffix.lower() == ".csv":
        import csv

        with out.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            w.writeheader()
            for r in rows:
                w.writerow(dict(r))
    else:
        lines = ["| ID | 年 | 标题 | 期刊 | 分区 | 状态 |", "|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['id']} | {r['year']} | {(r['title_zh'] or r['title'] or '')[:40]} | {r['venue'] or ''} | {r['tier']} | {r['status']} |")
        out.write_text("\n".join(lines), encoding="utf-8")
    print(f"导出 {len(rows)} 条 → {out}")


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="资料库交互查询")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default="", help="调研库根目录，默认自动向上查找")
    ap.add_argument("--root", default="")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("stats", parents=[common]); p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("list", parents=[common])
    for name in ("year", "venue", "tag", "tier", "status", "type"):
        p.add_argument(f"--{name}", default="")
    p.add_argument("--min-rel", type=int, default=0)
    p.add_argument("-n", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", parents=[common]); p.add_argument("id"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("note", parents=[common]); p.add_argument("id"); p.set_defaults(fn=cmd_note)

    p = sub.add_parser("tag", parents=[common])
    p.add_argument("id"); p.add_argument("--add", default=""); p.add_argument("--remove", default="")
    p.set_defaults(fn=cmd_tag)

    p = sub.add_parser("set", parents=[common])
    p.add_argument("id"); p.add_argument("--field", required=True); p.add_argument("--value", required=True)
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("grep", parents=[common])
    p.add_argument("pattern"); p.add_argument("-n", type=int, default=20); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_grep)

    p = sub.add_parser("tables", parents=[common])
    p.add_argument("--id", default=""); p.add_argument("--print", dest="print_rows", type=int, default=0)
    p.set_defaults(fn=cmd_tables)

    p = sub.add_parser("export", parents=[common]); p.add_argument("--out", required=True); p.set_defaults(fn=cmd_export)

    args = ap.parse_args()
    root = find_root(args.root) if args.root else find_root()
    c = conn_of(root)
    args.fn(c, root, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
