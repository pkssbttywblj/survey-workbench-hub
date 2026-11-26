#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期刊等级判定：影响因子 / JCR 分区 / 中科院分区。

三条规则，按优先级：
  1. 用户缓存（~/.survey-workbench/journal_cache.json，verified=true）→ 直接用
  2. 种子表（references/journals.json，覆盖高频刊，标注 seed）→ 用，但 Excel 里标「种子未核实」
  3. 都不命中 → tier=待核实，if_=None。**绝不猜数字**，交给 agent 用 WebSearch 查证后写入

用法:
  python journal_meta.py --lookup "Combustion and Flame"
  python journal_meta.py --set "Combustion and Flame" --if 4.4 --zone "1区" --year 2025
  python journal_meta.py --pending --records .cache/acquired.json    # 列出待核实的刊
  python journal_meta.py --apply .cache/acquired.json                # 原地补齐 if_/tier 字段
  python journal_meta.py --list
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import HOME_STATE, SKILL_DIR, load_json, print_table, save_json, setup_stdout  # noqa: E402

CACHE_PATH = HOME_STATE / "journal_cache.json"
SEED_PATH = SKILL_DIR / "references" / "journals.json"


def norm(name: str) -> str:
    n = unicodedata.normalize("NFKC", name or "").lower()
    n = re.sub(r"^the\s+", "", n)
    n = re.sub(r"&", "and", n)
    n = re.sub(r"[^\w\u4e00-\u9fff]+", "", n)
    return n


def load_cache() -> dict:
    return load_json(CACHE_PATH, default={}) or {}


def load_seed() -> dict:
    return load_json(SEED_PATH, default={}) or {}


def lookup(name: str) -> dict:
    """返回 {if_, zone, jcr, source, year} 或 None。zone 优先中科院分区。"""
    n = norm(name)
    if not n:
        return None
    key = next((k for k in load_cache() if norm(k) == n), None)
    if key:
        e = dict(load_cache()[key])
        e["source"] = "缓存(已核实)"
        e.setdefault("zone", e.get("cas") or e.get("jcr") or "")
        return e
    seed = load_seed().get("journals") or {}
    key = next((k for k in seed if norm(k) == n), None)
    if key:
        e = dict(seed[key])
        e["source"] = "种子(未核实)"
        e["zone"] = e.get("cas") or e.get("jcr") or ""
        return e
    return None


def apply_to_records(records: list) -> list:
    for r in records:
        venue = r.get("venue") or ""
        if not venue or venue.startswith(("Zenodo", "Figshare", "arXiv", "OpenAlex", "Crossref", "Web")):
            r.setdefault("tier", "NA")
            r.setdefault("if_", None)
            continue
        hit = lookup(venue)
        if hit:
            r["if_"] = hit.get("if_")
            r["tier"] = hit.get("zone") or r.get("tier") or "待核实"
            r["tier_source"] = hit.get("source")
            r["jcr_q"] = hit.get("jcr") or ""
        else:
            r["if_"] = None
            r["tier"] = "待核实"
            r["tier_source"] = "未收录"
    return records


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="期刊等级判定")
    ap.add_argument("--lookup", default="")
    ap.add_argument("--set", default="", help="期刊名")
    ap.add_argument("--if", dest="if_", type=float, default=None)
    ap.add_argument("--zone", default="", help="1区/2区/3区/4区/Q1..Q4/核心/非SCI")
    ap.add_argument("--jcr", default="")
    ap.add_argument("--year", type=int, default=0)
    ap.add_argument("--apply", default="", help="对 records JSON 原地补齐")
    ap.add_argument("--pending", action="store_true", help="列出未收录的期刊")
    ap.add_argument("--records", default="")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.lookup:
        hit = lookup(args.lookup)
        if not hit:
            print(f"未收录: {args.lookup}")
            print("下一步：WebSearch '<刊名> 影响因子 中科院分区'，再 --set 写回。不要猜。")
            return 2
        print(json.dumps(hit, ensure_ascii=False, indent=2))
        return 0

    if args.set:
        cache = load_cache()
        entry = {"if_": args.if_, "year": args.year or 2025, "verified": True}
        if args.zone:
            entry["zone"] = args.zone
            if re.match(r"^[1-4]区$", args.zone):
                entry["cas"] = args.zone
            elif re.match(r"^Q[1-4]$", args.zone, re.I):
                entry["jcr"] = args.zone.upper()
        if args.jcr:
            entry["jcr"] = args.jcr
        cache[args.set] = entry
        save_json(CACHE_PATH, cache)
        print(f"已写入缓存: {args.set} → {json.dumps(entry, ensure_ascii=False)}")
        return 0

    if args.list:
        cache, seed = load_cache(), (load_seed().get("journals") or {})
        rows = [(k, (v.get("if_") if v.get("if_") is not None else "-"), v.get("zone") or v.get("cas") or v.get("jcr") or "-", "缓存") for k, v in cache.items()]
        rows += [(k, v.get("if_") if v.get("if_") is not None else "-", v.get("cas") or v.get("jcr") or "-", "种子") for k, v in seed.items()]
        print_table(["期刊", "IF", "分区", "来源"], rows)
        return 0

    if args.apply:
        records = load_json(args.apply, default=[]) or []
        apply_to_records(records)
        save_json(args.apply, records)
        unknown = sorted({r.get("venue") for r in records if r.get("tier") == "待核实"})
        print(f"已补齐 {len(records)} 条；待核实期刊 {len(unknown)} 个")
        for u in unknown[:40]:
            print("  -", u)
        return 0

    if args.pending:
        records = load_json(args.records, default=[]) or []
        unknown = sorted({r.get("venue") for r in records if r.get("venue") and not lookup(r.get("venue"))})
        print(f"未收录期刊 {len(unknown)} 个：")
        for u in unknown:
            print("  -", u)
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
