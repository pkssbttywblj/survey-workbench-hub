#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仓库一致性检查：中英 README 对齐、链接与图片存在、脚本可编译。"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAIL = []


def headers(p: Path):
    return [m.group(1).strip() for m in re.finditer(r"^##\s+(.+)$", p.read_text(encoding="utf-8"), re.M)]


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def main():
    zh, en = ROOT / "README.md", ROOT / "README_EN.md"
    for p in (zh, en):
        check(p.exists(), f"{p.name} 存在")
    if zh.exists() and en.exists():
        hz, he = headers(zh), headers(en)
        check(len(hz) == len(he), f"README 章节数一致（zh={len(hz)} en={len(he)}）")
        def numbered(items):
            return [int(m.group(1)) for x in items if (m := re.match(r"(\d+)\.", x))]

        check(numbered(hz) == numbered(he), "README 编号章节顺序一致（标题本身允许中英翻译）")

    skill = ROOT / "skills" / "survey-workbench"
    for f in ("SKILL.md", "README.md", "README_EN.md", "references/report-spec.md",
              "ppt_skill/SKILL.md", "ppt_skill/SURVEY_INTEGRATION.md", "scripts/ppt_handoff.py",
              "scripts/table_ocr.py"):
        check((skill / f).exists(), f"skill 文件 {f}")

    text = skill.joinpath("SKILL.md").read_text(encoding="utf-8")
    check(bool(re.search(r"^name:\s*\S+", text, re.M)), "SKILL.md 有 name")
    check(bool(re.search(r"^description:\s*\S+", text, re.M)), "SKILL.md 有 description")

    for img in re.findall(r"!\[[^\]]*\]\((assets/[^)]+)\)", zh.read_text(encoding="utf-8")):
        check((ROOT / img).exists(), f"效果图 {img}")

    scripts = list((skill / "scripts").glob("*.py")) + list((skill / "ppt_skill" / "scripts").glob("*.py"))
    r = subprocess.run([sys.executable, "-m", "py_compile", *map(str, scripts)], capture_output=True)
    check(r.returncode == 0, f"{len(scripts)} 个脚本语法通过")

    print(f"\n{'PASS' if not FAIL else 'FAIL'}：{len(FAIL)} 项失败")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
