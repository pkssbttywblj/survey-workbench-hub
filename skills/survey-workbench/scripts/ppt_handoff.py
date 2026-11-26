#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已完成的调研成果打包成 PPT 交接包（供内置 CN Academic Spark 引擎使用）。

只做一件事：把「报告 + 索引库 + 缺口清单」汇成一份 `source.md`，
让 PPT 引擎在生成内容时**自动引用之前的调研信息**，不重新检索、不新增文献。

用法:
  python ppt_handoff.py --root <R>              # 生成 05_报告/ppt_source/
  python ppt_handoff.py --root <R> --max-refs 80
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    DIR_INDEX,
    DIR_REPORT,
    DIR_TEXT,
    find_root,
    print_table,
    save_json,
    setup_stdout,
)

SKILL = Path(__file__).resolve().parent.parent
TYPE_ZH = {"J": "J", "C": "C", "P": "P", "T": "D", "R": "R", "S": "Z",
           "D": "DS", "W": "EB/OL", "B": "M", "N": "N"}


def report_path(root: Path) -> Path | None:
    cands = [p for p in (root / DIR_REPORT).glob("研究现状_*.md") if "速览" not in p.name]
    return sorted(cands, key=lambda p: -p.stat().st_mtime)[0] if cands else None


def load_docs(root: Path) -> list[dict]:
    db = root / DIR_INDEX / "library.db"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "select id,title,title_zh,authors,year,venue,type,language,access,doi,status,"
        "citations,tier,stored from docs order by year, id")]
    con.close()
    for r in rows:
        try:
            r["authors"] = json.loads(r.get("authors") or "[]")
        except Exception:
            r["authors"] = [str(r.get("authors") or "")]
    return rows


def author_str(authors: list[str]) -> str:
    names = [a for a in authors if a]
    if not names:
        return "佚名"
    if len(names) > 3:
        return ", ".join(names[:3]) + ", 等"
    return ", ".join(names)


def gbt_ref(i: int, d: dict) -> str:
    title = d.get("title_zh") or d.get("title") or "（无题名）"
    code = TYPE_ZH.get(d.get("type") or "", "Z")
    venue = d.get("venue") or ""
    year = d.get("year") or ""
    doi = d.get("doi") or ""
    line = f"[{i}] {author_str(d['authors'])}. {title}[{code}]"
    if venue:
        line += f". {venue}"
    if year:
        line += f", {year}"
    line += "."
    if doi:
        line += f" DOI: {doi}."
    if d.get("status") == "待手动获取":
        line += "（未获取全文）"
    return line


def key_sentences(text: str, limit: int = 12) -> list[str]:
    """只从正文段落抽量化句；标题/表格/代码块不参与，避免把标题粘进句子。"""
    body_lines = [ln for ln in text.splitlines()
                  if not ln.startswith(("#", "|", "```", ">"))]
    body = " ".join(body_lines).replace("**", "")
    out = []
    for sent in re.split(r"(?<=[。！？])", body):
        s = re.sub(r"\s+", " ", sent).strip()
        if len(s) < 12 or len(s) > 110:
            continue
        if re.search(r"\d", s) and any(k in s for k in ("%", "倍", "min", "h", "MPa",
                                                        "℃", "°C", "m/s", "CPU", "误差")):
            out.append(s)
        if len(out) >= limit:
            break
    return out


def build(root: Path, max_refs: int) -> tuple[Path, Path]:
    report = report_path(root)
    if report is None:
        raise SystemExit(f"{root / DIR_REPORT} 里没有 研究现状_*.md，先跑 make_report.py。")
    text = report.read_text(encoding="utf-8", errors="replace")
    topic = report.stem.replace("研究现状_", "")
    cfg = {}
    cfg_path = root / DIR_REPORT / "report_config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    docs = load_docs(root)
    years = [d["year"] for d in docs if d.get("year")]
    stage_years = re.findall(r"(\d{4})[–\-—](\d{4})\s*年", text)
    if stage_years:
        span = f"{min(y[0] for y in stage_years)}–{max(y[1] for y in stage_years)}"
    elif years:
        span = f"{min(years)}–{max(years)}"
    else:
        span = "未标注"
    done = sum(1 for d in docs if d.get("status") == "已入库")
    pending = [d for d in docs if d.get("status") == "待手动获取"]
    by_type = {}
    for d in docs:
        by_type[d.get("type") or "?"] = by_type.get(d.get("type") or "?", 0) + 1

    # 缺口清单原文（如果有）
    gap_text = ""
    gap_file = root / DIR_REPORT / "未获取资料清单.md"
    if gap_file.exists():
        gap_text = gap_file.read_text(encoding="utf-8", errors="replace")

    # 已解析的图表（有就列出来，供 PPT 配图）
    figs = sorted((root / DIR_TEXT).rglob("*.png"))[:20]

    refs = docs[:max_refs]
    L = [
        f"# {topic} · 调研成果交接包",
        "",
        "> 本文件由 survey-workbench 自动生成。它是 PPT 的唯一内容源：",
        "> **不要重新检索、不要新增文献、不要补写本文件之外的数字**；",
        "> 缺失之处按「资料缺口」如实标注，不得编造。",
        "",
        "## 一、任务信息",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 汇报主题 | {topic} |",
        f"| 汇报类型 | 文献综述 / 研究现状（Route D） |",
        f"| 语言 | 中文（术语可保留英文原词） |",
        f"| 时间跨度 | {span} |",
        f"| 文献量 | {len(docs)} 条（已入库 {done}，待获取 {len(pending)}） |",
        f"| 类型分布 | " + "、".join(f"{k} {v}" for k, v in sorted(by_type.items())) + " |",
        f"| 引用格式 | GB/T 7714 |",
        f"| 生成日期 | {date.today().isoformat()} |",
        f"| 源报告 | `{report.relative_to(root).as_posix()}` |",
        "",
        "## 二、调研范围与口径",
        "",
    ]
    if cfg:
        L += [f"- 报告配置：{json.dumps(cfg, ensure_ascii=False)}", ""]
    L += [
        f"- 资料库：`{root.name}/`（{DIR_INDEX} 索引 + 01/02 原文 + 03 解析全文）",
        "- 覆盖类型：期刊、会议、预印本、学位论文、报告、幻灯片、数据集、网页资料",
        "- 筛选原则：以报告采用的阶段划分为准，不按主题重排",
        "",
        "## 三、参考文献（按年份）",
        "",
    ]
    for i, d in enumerate(refs, 1):
        L.append(gbt_ref(i, d))
    if len(docs) > len(refs):
        L.append(f"（另有 {len(docs) - len(refs)} 条未列出，完整清单见 `{DIR_INDEX}/索引表.xlsx`）")
    L += ["", "## 四、研究现状正文（报告原文，PPT 内容以此为准）", "", "```markdown", text.strip(), "```", ""]

    keys = key_sentences(text)
    L += ["## 五、可引用的关键数据", ""]
    L += [f"- {s}" for s in keys] or ["- 报告中未提取到带单位/百分比的量化句，正文不要补数。"]
    L += ["", "## 六、资料缺口（必须如实呈现）", ""]
    if pending:
        L += [f"- {d['id']} · {d.get('year') or '—'} · {d.get('title') or ''}"
              f"（{d.get('venue') or '来源未知'}，{d.get('access') or ''}）"
              for d in pending]
    else:
        L.append("- 无待获取全文，覆盖完整。")
    if gap_text:
        L += ["", "<details><summary>未获取资料清单原文</summary>", "", gap_text.strip(), "", "</details>"]
    if figs:
        L += ["", "## 七、可用图片（已从原文解析，可直接引用）", ""]
        L += [f"- `{p.relative_to(root).as_posix()}`" for p in figs]
    L += [
        "",
        "## 八、内容红线",
        "",
        "1. 只用本文件与库内图片；不检索、不补文献、不造数据。",
        "2. 阶段顺序与判断句与报告一致；一页一观点。",
        "3. 引用页码/脚注用 GB/T 7714，作者与年份必须与第三节一致。",
        "4. 缺口页放在结论前，明确写「未获取全文」，不写成「未见研究」。",
        "",
    ]

    out_dir = root / DIR_REPORT / "ppt_source"
    out_dir.mkdir(parents=True, exist_ok=True)
    src_md = out_dir / "source.md"
    src_md.write_text("\n".join(L), encoding="utf-8")

    meta = {
        "topic": topic,
        "route": "D-literature-review",
        "language": "zh",
        "citation": "GB/T 7714",
        "created_at": date.today().isoformat(),
        "report": report.relative_to(root).as_posix(),
        "source_md": src_md.relative_to(root).as_posix(),
        "docs_total": len(docs),
        "docs_stored": done,
        "docs_pending": len(pending),
        "year_span": span,
        "type_counts": by_type,
        "figures": [p.relative_to(root).as_posix() for p in figs],
        "ppt_engine": "ppt_skill (CN Academic Spark)",
        "output_dir": DIR_REPORT,
    }
    meta_path = out_dir / "meta.json"
    save_json(meta_path, meta)
    return src_md, meta_path


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="调研成果 → PPT 交接包")
    ap.add_argument("--root", default="")
    ap.add_argument("--max-refs", type=int, default=120)
    args = ap.parse_args()
    root = find_root(args.root) if args.root else find_root()

    src_md, meta_path = build(root, args.max_refs)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    print_table(["项", "值"], [
        ("交接包", str(src_md)),
        ("元信息", str(meta_path)),
        ("主题", meta["topic"]),
        ("文献", f'{meta["docs_total"]} 条（已入库 {meta["docs_stored"]}，待获取 {meta["docs_pending"]}）'),
        ("年份", meta["year_span"]),
        ("路线", meta["route"]),
    ])
    print("\n下一步：读 ppt_skill/SKILL.md，按 Route D 用这个 source.md 出片（不要重新检索）。")
    print(f"产出建议放在: {root / DIR_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
