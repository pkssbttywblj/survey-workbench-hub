#!/usr/bin/env bash
# 把仓库里的 skill 同步到 WorkBuddy 全局目录（Windows 下用 Python 复制，避免路径差异）
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
python - "$HERE" <<'PY'
import shutil, sys
from pathlib import Path
repo = Path(sys.argv[1])
src = repo / "skills" / "survey-workbench"
dst = Path.home() / ".workbuddy" / "skills" / "survey-workbench"
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
print(f"installed: {dst}")
PY
