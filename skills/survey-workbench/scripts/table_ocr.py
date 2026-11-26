#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""表格高精度提取的 OCR 分支（按需启用，默认不打扰用户）。

关于「MiniOCR-0.7B / 1.5B」：截至 2026-11，未查到以此为名的官方发布版本。
实际可选的小参数量 OCR-VLM 主要是：
  - HunyuanOCR-1.5（1B，腾讯；表格解析工作 StrucTab 被 ECCV 2026 主会接收）
    https://github.com/Tencent-Hunyuan/HunyuanOCR
  - PaddleOCR-VL 系列（约 0.9B，Apache-2.0）
    https://github.com/PaddlePaddle/PaddleOCR
本脚本把 miniocr 留作「本机已装 CLI」的兼容入口，并优先探测它；没装时给出上面两个真实可装选项。

子命令:
  check                          探测本机 OCR，输出 JSON/表格
  hint                           给出安装链接、目标目录与命令（不执行）
  install --backend X --confirm  确认后才下载安装
  extract --root R --id P003     用探测到的 OCR 重提表格（写到 tables/ 并刷新索引）

默认行为：不主动提示。用户明确要求高精度提表格时才运行 check/hint/install。
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    DIR_TEXT,
    HOME_STATE,
    find_root,
    load_json,
    print_table,
    save_json,
    setup_stdout,
)

OCR_HOME = HOME_STATE / "ocr"
LINKS = {
    "hunyuanocr": {
        "name": "HunyuanOCR-1.5（1B，表格解析强）",
        "repo": "https://github.com/Tencent-Hunyuan/HunyuanOCR",
        "dir": OCR_HOME / "hunyuanocr",
        "license": "Tencent Hunyuan Community License Agreement",
        "note": "仓库内注明表格解析工作 StrucTab 被 ECCV 2026 主会接收；支持 doc_parse 等 12 种任务类型。",
    },
    "paddleocr_vl": {
        "name": "PaddleOCR-VL（约 0.9B）",
        "repo": "https://github.com/PaddlePaddle/PaddleOCR",
        "dir": OCR_HOME / "paddleocr_vl",
        "license": "Apache-2.0",
        "note": "按仓库 README 的 PaddleOCR-VL 章节安装；不要照抄本脚本臆测的命令。",
    },
    "miniocr": {
        "name": "MiniOCR（本机 CLI，若已装）",
        "repo": "",
        "dir": OCR_HOME / "miniocr",
        "license": "视具体实现",
        "note": "未查到官方 0.7B/1.5B 版本；若你本机已有 miniocr/mini_ocr 命令，脚本会优先用它。",
    },
}


def detect() -> dict:
    found = {}
    for key in ("miniocr", "mini_ocr"):
        p = shutil.which(key)
        if p:
            found["miniocr"] = {"ok": True, "kind": "cli", "path": p, "path_hint": key}
            break
    if os.environ.get("HUNYUAN_OCR_URL"):
        found["hunyuanocr"] = {
            "ok": True,
            "kind": "endpoint",
            "path": os.environ["HUNYUAN_OCR_URL"],
            "path_hint": "HUNYUAN_OCR_URL",
        }
    elif (OCR_HOME / "hunyuanocr").exists():
        found["hunyuanocr"] = {
            "ok": True,
            "kind": "local_dir",
            "path": str(OCR_HOME / "hunyuanocr"),
            "path_hint": "local_dir",
        }
    try:
        importlib.import_module("paddleocr")
        found["paddleocr_vl"] = {"ok": True, "kind": "python", "path": "paddleocr", "path_hint": "import paddleocr"}
    except Exception:
        pass
    try:
        importlib.import_module("rapidocr_onnxruntime")
        found["rapidocr"] = {"ok": True, "kind": "python", "path": "rapidocr_onnxruntime", "path_hint": "import rapidocr_onnxruntime"}
    except Exception:
        pass
    tesseract = shutil.which("tesseract")
    if tesseract:
        found["tesseract"] = {"ok": True, "kind": "cli", "path": tesseract, "path_hint": "tesseract"}
    return found


def cmd_check(as_json=False):
    found = detect()
    preferred = next((b for b in ("miniocr", "hunyuanocr", "paddleocr_vl", "rapidocr", "tesseract") if b in found), None)
    if as_json:
        print(json.dumps({"found": found, "preferred": preferred}, ensure_ascii=False, indent=2))
        return 0
    rows = [(b, v["kind"], v["path"][:70]) for b, v in found.items()]
    print_table(["后端", "形态", "位置"], rows or [("无", "-", "未探测到任何 OCR")])
    print(f"\n优先使用: {preferred or '无（需安装）'}")
    if not preferred:
        print("用户明确要求高精度提表格时，跑 `table_ocr.py hint` 看安装建议。")
    return 0


def cmd_hint():
    print("高精度表格提取需要 OCR。按优先级给你三个真实可选方案：\n")
    for key in ("miniocr", "hunyuanocr", "paddleocr_vl"):
        info = LINKS[key]
        print(f"[{key}] {info['name']}")
        if info["repo"]:
            print(f"  仓库/说明: {info['repo']}")
        print(f"  建议安装目录: {info['dir']}")
        print(f"  许可: {info['license']}")
        print(f"  备注: {info['note']}")
        print()
    print("MiniOCR-0.7B/1.5B：截至 2026-11 未查到官方发布版本，请勿按这个名字下载来路不明的权重。")
    print("确认要装某一个后，让 agent 执行：python table_ocr.py install --backend <名字> --confirm")
    return 0


def cmd_install(backend: str, confirm: bool):
    if backend not in LINKS:
        print(f"未知后端 {backend}；可选: {', '.join(LINKS)}")
        return 1
    if not confirm:
        print("未确认，不执行安装。加上 --confirm 才会下载。")
        return 1
    info = LINKS[backend]
    if backend == "miniocr":
        print("本机 miniocr 未探测到。若你已有安装包，按提供方的说明装到本地后重新 check。")
        return 1
    if backend == "hunyuanocr":
        dest = info["dir"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        repo = dest / "HunyuanOCR"
        if not repo.exists():
            subprocess.run(["git", "clone", "https://github.com/Tencent-Hunyuan/HunyuanOCR.git", str(repo)], check=False)
        print(f"仓库已就位/已存在: {repo}")
        print("权重下载按其 README 的 `huggingface-cli download tencent/HunyuanOCR --local-dir <dir>` 执行。")
        print(f"建议本地目录: {dest / 'weights'}")
        print(f"运行方式: 设 HUNYUAN_OCR_URL 指向本地 OpenAI 兼容服务，再跑 extract。")
        return 0
    if backend == "paddleocr_vl":
        print("安装命令请严格按仓库 README 的 PaddleOCR-VL 章节执行，不要用猜测的命令。")
        print(f"仓库: {info['repo']}    建议目录: {info['dir']}")
        print("装完后重新跑 check，能用 import paddleocr 探测到即可。")
        return 0
    return 1


def cmd_extract(root: Path, doc_id: str, backend: str = ""):
    found = detect()
    backend = backend or next((b for b in ("miniocr", "hunyuanocr", "paddleocr_vl", "rapidocr", "tesseract") if b in found), "")
    if not backend:
        print("本机没有可用 OCR。跑 `table_ocr.py hint` 看安装建议；未安装前不要假装提了表格。")
        return 2
    pages_dir = root / DIR_TEXT / "pages" / doc_id
    if not pages_dir.exists():
        print(f"{pages_dir} 不存在。若 PDF 有文本层，用 pdf_to_md.py --tables on --force 走 pdfplumber；")
        print("只有扫描件才需要先把页面图导出（pdf_to_md.py --force）。")
        return 1
    out_dir = root / DIR_TEXT / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    if backend == "tesseract":
        import pytesseract
        from PIL import Image

        for img in sorted(pages_dir.glob("p*.png")):
            txt = pytesseract.image_to_string(Image.open(img), lang="chi_sim+eng", config="--psm 6")
            if not txt.strip():
                continue
            out = out_dir / f"{img.stem}_ocr.csv"
            out.write_text("\n".join(f'"{line.strip()}"' for line in txt.splitlines() if line.strip()), encoding="utf-8-sig")
            written.append(out)
    elif backend == "hunyuanocr":
        url = os.environ.get("HUNYUAN_OCR_URL")
        if not url:
            print("设 HUNYUAN_OCR_URL 指向本地服务后再跑（OpenAI 兼容，任务类型建议 doc_parse）。")
            return 2
        import urllib.request

        for img in sorted(pages_dir.glob("p*.png")):
            payload = json.dumps({"model": "tencent/HunyuanOCR", "image": str(img), "task_type": "doc_parse"}).encode()
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
            text = json.dumps(res, ensure_ascii=False)
            out = out_dir / f"{img.stem}_ocr.json"
            out.write_text(text, encoding="utf-8")
            written.append(out)
    else:
        print(f"后端 {backend} 已探测到，但自动提表接口尚未接线。请先按该后端的 CLI 对 {pages_dir} 批量识别，" )
        print(f"结果落到 {out_dir}/<页号>_ocr.csv，然后重跑 build_index.py。")
        return 2

    print(f"OCR 结果 {len(written)} 份 → {out_dir}")
    print("下一步：python build_index.py --root .   # 表格清单会吸收新文件")
    return 0


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="按需启用的表格 OCR")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check"); p.add_argument("--json", action="store_true")
    sub.add_parser("hint")
    p = sub.add_parser("install"); p.add_argument("--backend", required=True); p.add_argument("--confirm", action="store_true")
    p = sub.add_parser("extract"); p.add_argument("--root", default=""); p.add_argument("--id", required=True); p.add_argument("--backend", default="")
    args = ap.parse_args()
    if args.cmd == "check":
        return cmd_check(args.json)
    if args.cmd == "hint":
        return cmd_hint()
    if args.cmd == "install":
        return cmd_install(args.backend, args.confirm)
    return cmd_extract(find_root(args.root) if args.root else find_root(), args.id, args.backend)


if __name__ == "__main__":
    raise SystemExit(main())
