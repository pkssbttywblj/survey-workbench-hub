#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报告层：按基金「国内外研究现状」规范生成骨架，并对成稿做机械体检。

为什么骨架要有「候选证据」而不是空白：写研究现状最费时的不是措辞，是把 80 条文献按小节归位
并找出可引用的量化结论。脚本把带 ID/年份/分区/结论的候选表摊到每一节下面，agent 只需要判断进哪节、
怎么连成句。

`--check` 才是这个脚本的价值所在：字数、引文编号连续性、参考文献条数、禁用词、超长段落，
全部按 references/report-spec.md 量化判定，不靠感觉。

用法:
  python make_report.py --root survey_x --topic "旋转爆轰发动机数值模拟"
  python make_report.py --root survey_x --check "05_报告/研究现状_xxx.md"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    DIR_INDEX,
    DIR_NOTES,
    DIR_REPORT,
    ensure_tree,
    find_root,
    load_json,
    print_table,
    save_json,
    setup_stdout,
)

DEFAULTS = {
    "topic": "",
    "survey_chars": 3500,
    "trends_chars": 1750,
    # time：按研究阶段（早期→近期）展开，默认；topic：按技术主题分节
    "organize_by": "time",
    "sections": 3,
    "section_titles": [
        "〈主题一：对象/现象层面的关键问题〉",
        "〈主题二：理论/机理层面〉",
        "〈主题三：方法/算法层面〉",
        "〈主题四：交叉技术/新兴方向〉",
    ],
    "include_timeline": True,
    "trends": [
        "〈判断句：已成为……的关键驱动力〉",
        "〈判断句：急需……定制化发展〉",
        "〈判断句：……有望大幅提升〉",
    ],
    "citation_style": "numeric",
    "ref_start": 1,
    "ref_range": [80, 120],
    "zh_ref_ratio": 0.25,
    "include_figures": True,
    "banned": ["本文", "我们认为", "笔者", "众所周知", "不言而喻", "综述", "文献计量", "研究热度",
               "诚实说明", "仅供参考", "受限于时间"],
}

SECTION_RE = re.compile(r"^(#{1,3})\s+(.*)$")
CITE_RE = re.compile(r"\[(\d{1,3})\]")


def ensure_config(root: Path, topic: str) -> dict:
    cfg_path = root / DIR_REPORT / "report_config.json"
    cfg = load_json(cfg_path, default=None)
    if cfg is None:
        cfg = dict(DEFAULTS)
        cfg["topic"] = topic
        save_json(cfg_path, cfg)
        print(f"已生成报告参数: {cfg_path}（要改字数/节数/引文风格就改这里，重跑不丢）")
    elif topic and not cfg.get("topic"):
        cfg["topic"] = topic
        save_json(cfg_path, cfg)
    return cfg


def strip_code(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def count_chars(text: str) -> int:
    return len(re.sub(r"\s+", "", strip_code(text)))


def load_notes(root: Path) -> dict:
    out = {}
    nd = root / DIR_NOTES
    if not nd.exists():
        return out
    for p in sorted(nd.glob("*.md")):
        m = re.match(r"^((?:P|O)\d{3})", p.name)
        if not m:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        out[m.group(1)] = t
    return out


def evidence_of(doc: dict, note: str) -> str:
    """从笔记里捞可引用结论：优先『关键结果』段，其次 frontmatter 的 findings。"""
    if note:
        m = re.search(r"##\s*关键结果[^\n]*\n(.*?)(?=\n##\s|\Z)", note, re.S)
        if m:
            body = re.sub(r"\s+", " ", m.group(1)).strip()
            body = re.sub(r"^[-*•]\s*", "", body)
            if body:
                return body[:120]
    if doc.get("findings"):
        return str(doc["findings"])[:120]
    if doc.get("summary"):
        return str(doc["summary"])[:120]
    return "〈待读全文后填〉"


def _year_of(d):
    m = re.fullmatch(r"\s*(\d{4})\s*", str(d.get("year") or ""))
    return int(m.group(1)) if m else None


def year_buckets(docs, n):
    """按年份把文献切成 n 个连续阶段。

    不按分位数切：分位数会把文献密集的年份挤成一堆，看不出真正的阶段演进。
    按年跨度等分，边界好解释，报告里直接写成「2010–2016 年」。
    """
    years = [y for y in (_year_of(d) for d in docs) if y]
    if not years:
        return []
    lo, hi = min(years), max(years)
    n = max(1, min(n, hi - lo + 1))
    if n == 1:
        return [(lo, hi)]
    step = (hi - lo + 1) / n
    edges = sorted({lo + int(round(step * i)) for i in range(n)} | {lo})
    buckets = []
    for i, a in enumerate(edges):
        b = edges[i + 1] - 1 if i + 1 < len(edges) else hi
        buckets.append((a, max(a, b)))
    return buckets


def split_by_year(docs, buckets):
    out = [[] for _ in buckets]
    rest = []
    for d in docs:
        y = _year_of(d)
        placed = False
        if y:
            for i, (a, b) in enumerate(buckets):
                if a <= y <= b:
                    out[i].append(d)
                    placed = True
                    break
        if not placed:
            rest.append(d)
    for g in out:
        g.sort(key=lambda x: (_year_of(x) or 0))
    if rest and out:
        out[-1].extend(rest)  # 无年份的记录挂到最后一段，别丢
    return out


def _evidence_table(L, chunk, notes):
    if not chunk:
        return
    L.append("**候选证据（按年份升序；写进正文后删掉本表）**")
    L.append("")
    L.append("| ID | 年 | 作者/来源 | 分区 | 可引用结论 |")
    L.append("|---|---|---|---|---|")
    for d in chunk:
        au = str(d.get("authors") or "")
        try:
            au = json.loads(au)
            au = au[0] if au else ""
        except Exception:
            pass
        au = re.sub(r"\s+", " ", str(au))[:22]
        L.append(
            f"| {d['id']} | {d.get('year') or ''} | {au} / {str(d.get('venue') or '')[:22]} "
            f"| {d.get('tier') or ''} | {evidence_of(d, notes.get(d['id'], '')).replace('|', '/')} |"
        )
    L.append("")


def build_skeleton(root: Path, cfg: dict) -> Path:
    import sqlite3

    docs = []
    db = root / DIR_INDEX / "library.db"
    if db.exists():
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        docs = [dict(r) for r in conn.execute("SELECT * FROM docs ORDER BY relevance DESC, year DESC")]
        conn.close()
    notes = load_notes(root)

    mode = (cfg.get("organize_by") or "time").lower()
    n_sec = max(1, int(cfg.get("sections") or (3 if mode == "time" else 4)))
    buckets = year_buckets(docs, n_sec) if mode == "time" else []
    if mode == "time" and not buckets:
        print("[warn] 库里没有可用年份，退回按主题分节")
        mode = "topic"

    L = []
    L.append("<!--")
    L.append("  本文件由 make_report.py 生成骨架，正文必须由 agent 按 references/report-spec.md 重写。")
    L.append(f"  组织方式：{mode}（time=按研究阶段由早到晚；topic=按技术主题）")
    L.append(f"  目标：研究现状 {cfg['survey_chars']} 字 ±15%，发展动态 {cfg['trends_chars']} 字 ±15%")
    L.append("  写完后跑：python make_report.py --root . --check 本文件")
    L.append("-->")
    L.append("")
    L.append("# 1.3 国内外研究现状")
    L.append("")
    unit = "个研究阶段" if mode == "time" else "个主题小节"
    L.append(f"> 目标 {cfg['survey_chars']} 字。导语 150–200 字；{n_sec} {unit}，每节 700–1000 字（2 段）。")
    L.append("")
    L.append("<!-- 导语直接写成正文首段，不要加小标题（本子里导语就是第一段） -->")
    L.append("")
    if mode == "time":
        L.append("〈TODO 150–200 字：交代调研边界，并说明本节按研究阶段由早到晚展开（不是按主题罗列）；")
        L.append("点出整个方向的推进主线是什么，例如「从可行性验证走向工程化，再走向精细化调控」。〉")
    else:
        L.append("〈TODO 150–200 字：交代调研边界（为什么聚焦这个子方向）、调研主线（按什么顺序展开）、")
        L.append("每节要回答的问题。句式参考：「考虑到……涉及的类型众多，本项目聚焦……。调研……，")
        L.append("剖析……，对比分析……，为后续……提供理论基础。」〉")
    L.append("")
    if cfg.get("include_figures"):
        L.append("![图1 文献调研总体逻辑图](figures/fig1_logic.png)")
        L.append("")
        L.append("图1 文献调研总体逻辑图")
        L.append("")

    if mode == "time":
        groups = split_by_year(docs, buckets)
        if cfg.get("include_timeline", True):
            L.append("**研究脉络时间轴（写进正文后删掉本表）**")
            L.append("")
            L.append("| 阶段 | 年份 | 篇数 | 代表工作 | 标志性进展（一句话） | 该阶段遗留问题 |")
            L.append("|---|---|---|---|---|---|")
            for i, ((a, b), g) in enumerate(zip(buckets, groups), 1):
                reps = "、".join(d.get("id") or "" for d in g[:3]) or "—"
                rng = f"{a} 年" if a == b else f"{a}–{b} 年"
                L.append(f"| {i} | {rng} | {len(g)} | {reps} | 〈〉 | 〈〉 |")
            L.append("")
        for i, ((a, b), g) in enumerate(zip(buckets, groups), 1):
            rng = f"{a} 年" if a == b else f"{a}–{b} 年"
            L.append(f"## （{i}）{rng}：〈该阶段解决了什么核心问题（写成一句判断句）〉")
            L.append("")
            ids = "、".join(d.get("id") or "" for d in g[:6])
            L.append(f"<!-- 本阶段 {len(g)} 篇（{ids}…）。段A 按年份由早到晚铺陈，段B 收口到「当时卡在哪 + 为什么转向下一阶段」 -->")
            L.append("")
            L.append("**段A｜该阶段做了什么（按年份由早到晚）**")
            L.append("")
            L.append(f"〈TODO：Xxx等[n]（{a} 年）提出……；Yyy等[n]（{b} 年）将其推广到……。")
            L.append("关键是把「谁在先、谁改进了什么」写清楚，不要只堆结论。〉")
            L.append("")
            L.append("**段B｜阶段收口**")
            L.append("")
            L.append("〈TODO：该阶段把问题推进到了哪一步、留下什么没解决，从而引出下一阶段的做法。")
            L.append("句式：「这一时期虽已……，但受限于……，尚无法……」〉")
            L.append("")
            _evidence_table(L, g, notes)
    else:
        per_sec = max(1, len(docs) // n_sec) if docs else 0
        for i in range(n_sec):
            titles = cfg.get("section_titles") or []
            title = titles[i] if i < len(titles) else f"〈主题{i+1}〉"
            L.append(f"## （{i+1}）{title}")
            L.append("")
            L.append("<!-- 目标 700–1000 字：段A 铺事实（谁做了什么、得到什么量化结论）；段B 收口（还缺什么） -->")
            L.append("")
            L.append("**段A｜事实铺陈**")
            L.append("")
            L.append("〈TODO：按「Xxx等[n] 通过……发现……，<量化结果>」写 3–6 句，引文按出现顺序编号〉")
            L.append("")
            L.append("**段B｜判定收口**")
            L.append("")
            L.append("〈TODO：以「虽然……已取得……，但在……仍有巨大挑战/提升空间」收尾，缺口要能被本项目攻克〉")
            L.append("")
            chunk = docs[i * per_sec : (i + 1) * per_sec] if per_sec else []
            _evidence_table(L, chunk, notes)

    L.append(f"# 1.4 发展动态分析及问题分析")
    L.append("")
    L.append(f"> 目标 {cfg['trends_chars']} 字。三条趋势各 250–400 字；末尾压成 2 个关键问题。")
    L.append("")
    if mode == "time":
        L.append("<!-- 三条趋势必须从 1.3 的阶段演进中长出来（阶段一到阶段二到阶段三各自往哪走），不要另起炉灶 -->")
        L.append("")
    for i, t in enumerate((cfg.get("trends") or [])[:3], 1):
        L.append(f"## （{i}）发展趋势{i}：{t}")
        L.append("")
        L.append("〈TODO：历史脉络/现状事实 → 现存局限（点名具体软件或方法）→ 由此导出的必然方向〉")
        L.append("")
    if cfg.get("include_figures"):
        L.append("![图2 核心问题提炼逻辑图](figures/fig2_gap.png)")
        L.append("")
        L.append("图2 核心问题提炼逻辑图")
        L.append("")
    L.append("## 收口：拟解决的关键问题")
    L.append("")
    L.append("因此，本项目拟发展〈一句话定位〉，以解决以下两大难题：")
    L.append("")
    L.append("一是如何〈问题1〉？〈2–3 句展开，含技术难点与理论依据〉")
    L.append("")
    L.append("二是如何〈问题2〉？〈2–3 句展开〉")
    L.append("")
    L.append("# 参考文献")
    L.append("")
    L.append(f"> 按正文出现顺序编号，共 {cfg['ref_range'][0]}–{cfg['ref_range'][1]} 条，中文占 {int(cfg['zh_ref_ratio']*100)}% 左右。")
    L.append("")
    L.append("[1] 〈作者. 题名[J]. 刊名, 年, 卷(期): 起页-止页.〉")
    L.append("")

    out = root / DIR_REPORT / f"研究现状_{cfg.get('topic') or 'topic'}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def audit(text: str, cfg: dict, root: Path) -> tuple:
    """返回 (rows, ok)。rows 给 print_table。"""
    rows = []
    ok = True
    # 注释是给 agent 看的提示，不能当作正文被判（曾把「本文件由…」判成禁用词「本文」）
    text = strip_code(text)

    # 1) 字数
    parts = re.split(r"^#\s+", text, flags=re.M)
    sizes = {}
    for part in parts[1:]:
        head = part.splitlines()[0].strip() if part.strip() else ""
        sizes[head] = count_chars(part)

    def near(val, target, tol=0.15):
        return target * (1 - tol) <= val <= target * (1 + tol)

    survey = sum(v for k, v in sizes.items() if "1.3" in k or "研究现状" in k)
    trends = sum(v for k, v in sizes.items() if "1.4" in k or "动态" in k)

    for name, val, target in (
        ("研究现状字数", survey, cfg["survey_chars"]),
        ("发展动态字数", trends, cfg["trends_chars"]),
    ):
        good = near(val, target)
        ok &= good
        rows.append((name, val, f"{target}±15%", "OK" if good else "偏差过大"))

    # 2) 引文编号
    body = re.sub(r"^#\s+参考文献[\s\S]*$", "", text, flags=re.M)
    cites = [int(x) for x in CITE_RE.findall(body)]
    if cites:
        mx = max(cites)
        miss = [i for i in range(1, mx + 1) if i not in set(cites)]
        rows.append(("引文编号范围", f"1–{mx}", "连续不跳号", "OK" if not miss else f"缺 {miss[:8]}"))
        ok &= not miss
        rows.append(("引文使用次数", len(cites), "—", "—"))
    else:
        rows.append(("引文", 0, ">0", "正文没有任何 [n] 引文"))
        ok = False

    # 3) 参考文献条目
    ref_lines = [l for l in text.splitlines() if re.match(r"^\[(\d+)\]\s+\S", l.strip())]
    lo, hi = cfg["ref_range"]
    good = lo <= len(ref_lines) <= hi
    ok &= good
    rows.append(("参考文献条数", len(ref_lines), f"{lo}–{hi}", "OK" if good else "超出范围"))

    # 4) 禁用词
    hits = [w for w in cfg["banned"] if w in text]
    ok &= not hits
    rows.append(("禁用词", "、".join(hits) if hits else "无", "0", "OK" if not hits else "需删除"))

    # 5) 超长段落
    long_paras = [
        (i + 1, len(re.sub(r"\s+", "", p)))
        for i, p in enumerate(text.split("\n\n"))
        if not p.strip().startswith(("#", "|", ">", "![", "<!--", "[")) and count_chars(p) > 700
    ]
    ok &= not long_paras
    rows.append(("超长段落", len(long_paras), "0", "OK" if not long_paras else f"第 {[p[0] for p in long_paras][:5]} 块超 700 字"))

    # 6) 占位符残留
    leftovers = len(re.findall(r"〈TODO|〈待|〈主题", text))
    ok &= leftovers == 0
    rows.append(("未填占位符", leftovers, "0", "OK" if leftovers == 0 else "还有没写的段落"))

    # 7) 数字与单位（量化结论）
    nums = len(re.findall(r"\d+(?:\.\d+)?\s*(?:%|m/s|MPa|K|ms|倍|个数量级|分钟|小时|μs|km/s)", text))
    good = nums >= 8
    rows.append(("量化结论处数", nums, "≥8", "OK" if good else "偏少，判断缺数据支撑"))

    # 7.5) 时间顺序（time 模式）：正文里的年份应当整体由早到晚推进
    if (cfg.get("organize_by") or "time") == "time":
        years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", body)]
        if len(years) >= 8:
            pairs = list(zip(years, years[1:]))
            # 任何倒退都算（原来用 b < a-1 会漏掉「2023→2022」这类相邻倒退，逆序全文只数出 4/9）
            back = sum(1 for a, b in pairs if b < a)
            ratio = back / max(1, len(pairs))
            good = ratio <= 0.5
            rows.append(("时间顺序", f"回退 {back}/{len(pairs)}", "≤50%",
                         "OK" if good else "逆序偏多，改成由早到晚"))
            ok &= good
        else:
            rows.append(("时间顺序", f"仅 {len(years)} 个年份", "≥8", "偏少，看不出时间脉络"))

    # 8) 图
    figs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    missing = [f for f in figs if not (root / DIR_REPORT / f).exists()]
    if cfg.get("include_figures"):
        rows.append(("图片引用", len(figs), "2", "OK" if not missing else f"缺文件 {missing[:2]}"))
    else:
        rows.append(("图片引用", len(figs), "不需要", "OK" if not missing else f"缺文件 {missing[:2]}"))

    return rows, ok


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="研究报告骨架与规范校验")
    ap.add_argument("--root", default="")
    ap.add_argument("--topic", default="")
    ap.add_argument("--check", default="", help="对现有 md 做规范体检")
    args = ap.parse_args()

    root = ensure_tree(find_root(args.root) if args.root else Path.cwd())
    cfg = ensure_config(root, args.topic)

    if args.check:
        p = Path(args.check)
        if not p.is_absolute():
            p = root / args.check if (root / args.check).exists() else Path(args.check)
        if not p.exists():
            print(f"找不到 {p}")
            return 1
        text = p.read_text(encoding="utf-8", errors="replace")
        rows, ok = audit(text, cfg, root)
        print_table(["检查项", "实测", "基准", "判定"], rows)
        print()
        print(("PASS" if ok else "FAIL") + f" → {p}")
        return 0 if ok else 2

    out = build_skeleton(root, cfg)
    print(f"骨架已生成: {out}")
    print(f"参数文件: {root / DIR_REPORT / 'report_config.json'}")
    print("\n下一步（由 agent 完成，不要跳过）：")
    print("  1. 先用 query.py list / grep 把每节的候选证据看一遍，判断进哪节")
    print("  2. 读 03_解析全文/md/<ID>.md 的方法与结论段，取量化数值")
    print("  3. 按 references/report-spec.md 的句式重写骨架，删掉『候选证据』表与占位符")
    print("  4. python make_report.py --check <成稿> 自检，PASS 后再 export_docx.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
