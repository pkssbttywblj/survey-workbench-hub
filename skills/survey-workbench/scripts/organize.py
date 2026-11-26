#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归档层：重命名 + 分类迁移 + 去重 + 分配 ID。

ID 一旦分配终身不变：重跑时先读 .cache/manifest_final.json，复用 key→ID 映射，
只有新记录才拿新号。这样反复交互（换分区、补笔记）不会把引文编号搅乱。

用法:
  python organize.py --root survey_x                       # 用 .cache/acquired.json
  python organize.py --root survey_x --input .cache/acquired.json
  python organize.py --root survey_x --reclassify          # 期刊等级更新后重新归位
  python organize.py --root survey_x --dry-run

输出: <root>/.cache/manifest_final.json、00_索引/tree.txt
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    DIR_CACHE,
    DIR_INDEX,
    DIR_MATERIAL,
    DIR_PAPERS,
    ensure_tree,
    field,
    find_root,
    first_author_family,
    guess_language,
    guess_type_code,
    is_material,
    load_json,
    material_dir,
    now_str,
    print_table,
    record_key,
    rel,
    save_json,
    setup_stdout,
    sha1_file,
    short_title_zh,
    slug,
    tier_dir,
    year_of,
)
from journal_meta import apply_to_records  # noqa: E402


def norm_tier(tier: str) -> str:
    """Q1/Q2 → 1区/2区，保证文件名与目录用的是同一套说法。"""
    import re

    t = (tier or "").strip()
    m = re.match(r"^Q([1-4])$", t, re.I)
    if m:
        return f"{m.group(1)}区"
    return t or "待核实"


def build_name(rec: dict, ext: str) -> str:
    year = year_of(rec)
    author = first_author_family(rec)
    short = short_title_zh(rec, 14)
    src = field((rec.get("source") or ["Manual"])[0], 12, "Manual")
    tcode = field(rec.get("_type_code") or "J", 4, "O")
    access = field(rec.get("_access") or "公开", 4, "公开")
    tier = field(norm_tier(rec.get("tier")), 6, "NA")
    return f"{year}_{author}_{short}__{src}_{tcode}_{access}_{tier}{ext}"


def unique_path(directory: Path, name: str) -> Path:
    p = directory / name
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    i = 2
    while True:
        cand = directory / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="重命名与分类归档")
    ap.add_argument("--root", default="")
    ap.add_argument("--input", default="", help="默认 .cache/acquired.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = ensure_tree(find_root(args.root) if args.root else Path.cwd())
    src = Path(args.input) if args.input else root / DIR_CACHE / "acquired.json"
    if not src.exists():
        print(f"找不到 {src}，先跑 acquire.py 拿到文件。")
        return 1

    incoming = load_json(src, default=[]) or []
    state_path = root / DIR_CACHE / "manifest_final.json"
    state = load_json(state_path, default=[]) or []
    by_key = {r.get("key"): r for r in state if r.get("key")}

    counters = {"P": 0, "O": 0}
    for r in state:
        i = str(r.get("id") or "")
        if i[:1] in counters and i[1:].isdigit():
            counters[i[0]] = max(counters[i[0]], int(i[1:]))

    # 先按"值得优先保留"排序，ID 顺序才有意义：相关度高、有引用、年份新
    incoming.sort(key=lambda r: (r.get("relevance") or 0, r.get("citations") or 0, r.get("year") or 0), reverse=True)

    added, moved, failed = [], [], []
    for rec in incoming:
        key = rec.get("key") or record_key(rec)
        rec["key"] = key
        existing = by_key.get(key)
        if existing:
            # 保留历史字段（parse/status/笔记路径等），只让本次采集到的值覆盖同名键
            rec = {**existing, **rec}

        code = guess_type_code(rec)
        rec["_type_code"] = code
        material = is_material(code)
        lang = rec.get("language") or guess_language(rec.get("title"), rec.get("title_zh"))

        if not rec.get("tier"):
            apply_to_records([rec])
        # 预印本/会议/学位论文/报告不是 SCI 期刊论文，落到 非SCI，不要挂在「等级待核实」下
        if not material and code in ("P", "C", "D", "R") and norm_tier(rec.get("tier")) in ("待核实", "NA", ""):
            rec["tier"] = "非SCI"
            rec["tier_source"] = "类型判定"
        rec["tier"] = norm_tier(rec.get("tier"))
        rec["language"] = lang

        if material:
            sub = material_dir(code)
            dest_dir = root / DIR_MATERIAL / sub
        else:
            dest_dir = root / DIR_PAPERS / tier_dir(lang, rec["tier"])

        if existing and existing.get("id"):
            rec["id"] = existing["id"]
            rec["created_at"] = existing.get("created_at") or now_str()
        else:
            counters["O" if material else "P"] += 1
            rec["id"] = f"{'O' if material else 'P'}{counters['O' if material else 'P']:03d}"
            rec["created_at"] = now_str()

        # 幂等关键：先认「已经归档过的文件」，别拿已经被移走的暂存路径当唯一真相。
        # 曾经因为顺序写反，重跑一次就把全部记录判成"无文件"并覆盖了状态。
        archived = None
        if existing and existing.get("stored"):
            cand = root / existing["stored"]
            if cand.exists():
                archived = cand

        # 注意 Path("") == Path(".")，而 "." 永远 exists —— 必须用 is_file() 判，否则
        # 「没有文件」的记录会被当成有文件去 move，报 Cannot move a directory '.' into itself。
        raw_src = rec.get("file") or ""
        src_file = Path(raw_src) if raw_src else None
        current = archived or (src_file if (src_file and src_file.is_file()) else None)

        if current is None:
            rec["stored"] = ""
            rec["file"] = ""
            if rec.get("status") in (None, "", "归档失败", "已入库", "OA", "公开"):
                rec["status"] = "待手动获取"
            failed.append(rec)
            by_key[key] = rec
            continue

        if current.parent.resolve() == dest_dir.resolve():
            rec["stored"] = rel(current, root)
            rec["file"] = str(current)
            rec["status"] = rec.get("status") if rec.get("status") not in ("待手动获取", "", None) else "已入库"
            by_key[key] = rec
            continue

        ext = current.suffix.lower() or ".bin"
        rec["_access"] = "OA" if rec.get("status") == "OA" else ("直链" if rec.get("status") == "直链" else "公开")
        name = build_name(rec, ext)
        target = unique_path(dest_dir, name)

        if args.dry_run:
            rec["stored"] = rel(target, root)
            rec["file"] = str(current)
            added.append(rec)
            by_key[key] = rec
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(current), str(target))
        except Exception as exc:
            rec["status"] = "归档失败"
            rec["reason"] = f"移动失败: {exc}"
            failed.append(rec)
            by_key[key] = rec
            continue

        if archived:
            moved.append((existing.get("stored"), rel(target, root)))

        rec["stored"] = rel(target, root)
        rec["file"] = str(target)
        rec["bytes"] = target.stat().st_size
        try:
            rec["sha1"] = sha1_file(target)
        except Exception:
            pass
        rec["status"] = "已入库"
        by_key[key] = rec
        added.append(rec)

    final = sorted(by_key.values(), key=lambda r: str(r.get("id")))
    save_json(state_path, final)

    # 目录树快照
    lines = [f"{root.name}/"]
    for sub in (DIR_PAPERS, DIR_MATERIAL):
        base = root / sub
        for d in sorted([p for p in base.iterdir() if p.is_dir()]):
            files = sorted(p.name for p in d.iterdir() if p.is_file())
            lines.append(f"  {sub}/{d.name}/  ({len(files)})")
            for f in files[:12]:
                lines.append(f"      {f}")
            if len(files) > 12:
                lines.append(f"      … 另有 {len(files)-12} 个")
    (root / DIR_INDEX).mkdir(parents=True, exist_ok=True)
    (root / DIR_INDEX / "tree.txt").write_text("\n".join(lines), encoding="utf-8")

    print_table(
        ["ID", "目录", "文件名"],
        [(r["id"], Path(r["stored"]).parent.as_posix() if r.get("stored") else "-", Path(r["stored"]).name if r.get("stored") else "(无文件)") for r in (added or final)],
    )
    print(f"\n入库 {len(added)} 条，无文件登记 {len(failed)} 条，搬移 {len(moved)} 条 → {state_path}")
    print(f"目录树: {root / DIR_INDEX / 'tree.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
