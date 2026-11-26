#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析层：PDF → Markdown / 纯文本 / 表格 CSV / 页面图像，并维护全文索引。

三级文本获取链，前一级不可用才降级：
  1. **文本层**（PyMuPDF 直接取）—— 每页均字符 ≥ 50，最省事
  2. **OCR**（pytesseract + tesseract）—— 装了就用，全自动
  3. **多模态提取**（agent 自己的视觉能力）—— 没装 OCR 时的**默认路径**，不是「放弃」

第 3 级是本 skill 的默认兜底：脚本把每页导成 PNG 并生成带页码的空骨架，
agent 用视觉能力逐页读图、把文字填进骨架，再跑 `--ingest` 回写。
这样没有 tesseract 也能处理扫描件，且每页图都留着，数值随时能回原图核对。

用法:
  python pdf_to_md.py --root survey_x                        # 解析全部
  python pdf_to_md.py --root survey_x --ids P003 --force
  python pdf_to_md.py --root survey_x --tables-only
  python pdf_to_md.py --root survey_x --ocr auto             # 有 tesseract 就自动 OCR
  python pdf_to_md.py --root survey_x --ingest P003          # 回写多模态提取的文字
  python pdf_to_md.py --root survey_x --ingest P003 --from 自定义.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    DIR_CACHE,
    DIR_TEXT,
    ensure_tree,
    find_root,
    load_json,
    print_table,
    save_json,
    setup_stdout,
)

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

DPI = 160                 # 页面图像精度：够看清正文字号，又不至于几十 MB
PAGE_MARK = re.compile(r"^##\s*第\s*(\d+)\s*页\s*$|^===\s*\[Page\s*(\d+)\]\s*===$")
FM_KEYS = ("id", "title", "authors", "year", "venue", "doi", "tier", "pages", "chars",
           "chars_per_page", "text_layer", "extract_method", "ocr", "vision", "tables", "source_file")


def have_ocr() -> bool:
    try:
        import pytesseract  # noqa: F401

        return True
    except Exception:
        return False


def ocr_page(page):
    import io

    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=200)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang="chi_sim+eng")


def export_pages(doc, root: Path, stem: str, limit: int) -> list:
    """把页面渲染成 PNG，供 agent 用视觉能力读。返回 [(页码, 相对 03_解析全文 的路径)]。"""
    out_dir = root / DIR_TEXT / "pages" / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for i in range(min(limit, doc.page_count)):
        path = out_dir / f"p{i+1:03d}.png"
        try:
            if not path.exists():
                doc[i].get_pixmap(dpi=DPI).save(str(path))
            # 相对 DIR_TEXT：md/ 与 vision/ 都在它下面，引用时统一加 ../
            saved.append((i + 1, f"pages/{stem}/{path.name}"))
        except Exception:
            continue
    return saved


def write_vision_stub(root: Path, doc_id: str, title: str, page_images: list) -> Path:
    """生成待填的多模态骨架：只有页码与图，正文留空。

    agent 只需在每页下面写文字，格式不用自己记 —— 这是降低出错率的关键。
    """
    vdir = root / DIR_TEXT / "vision"
    vdir.mkdir(parents=True, exist_ok=True)
    stub = vdir / f"{doc_id}.md"
    if stub.exists():
        return stub
    L = [
        "<!--",
        f"  多模态提取骨架：{doc_id} {title[:60]}",
        f"  共 {len(page_images)} 页。页面图像在 {DIR_TEXT}/pages/{doc_id}/",
        "  做法：逐页看 PNG → 在「## 第 N 页」下面写该页正文 → 跑",
        f"    python pdf_to_md.py --ingest {doc_id}",
        "  只写原文有的内容；表格用 markdown 表格；图注照抄；看不清写 [不清]，不要编。",
        "-->",
        "",
    ]
    for pno, img in page_images:
        L += [f"## 第 {pno} 页", "", f"![](../{img})", "", ""]
    stub.write_text("\n".join(L), encoding="utf-8")
    return stub


def extract_tables(pdf_path: Path, out_dir: Path, stem: str, max_pages: int = 40):
    """pdfplumber 抽表，落了 CSV 才有办法横向对比。"""
    try:
        import pdfplumber
    except Exception:
        return []
    found = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for pno, page in enumerate(pdf.pages[:max_pages], 1):
                try:
                    tables = page.extract_tables()
                except Exception:
                    continue
                for ti, table in enumerate(tables, 1):
                    rows = [r for r in table if any((c or "").strip() for c in r)]
                    if len(rows) < 2 or len(rows[0]) < 2:
                        continue  # 单行/单列多半是排版残留，不是真表
                    longest = max((max(len((c or "").strip()) for c in r) for r in rows), default=0)
                    if longest < 4:
                        continue  # 全格子极短：图内碎片或公式块
                    out = out_dir / f"{stem}_p{pno}t{ti}.csv"
                    with out.open("w", encoding="utf-8-sig", newline="") as f:
                        for r in rows:
                            cells = [(c or "").replace("\n", " ").replace(",", "，").strip() for c in r]
                            f.write(",".join(f'"{c}"' for c in cells) + "\n")
                    found.append(
                        {
                            "page": pno,
                            "index": ti,
                            "rows": len(rows),
                            "cols": max(len(r) for r in rows),
                            "csv": out.name,
                            "head": " | ".join((c or "")[:18] for c in rows[0][:4]),
                        }
                    )
    except Exception as exc:
        return found + [{"error": str(exc)[:80]}]
    return found


def build_md(rec: dict, fm: dict, pages: list, tables: list, page_images: list) -> str:
    """统一由 frontmatter + 页列表生成 md。convert 与 ingest 共用，避免两处格式漂移。"""
    L = ["---"]
    for k in FM_KEYS:
        if k not in fm:
            continue
        val = fm[k]
        if k in ("title", "authors", "venue"):
            L.append(f"{k}: {json.dumps(val, ensure_ascii=False)}")
        else:
            L.append(f"{k}: {val}")
    L += ["---", ""]
    L.append(f"# {rec.get('title') or fm.get('title') or rec.get('id')}")
    L.append("")
    head = " · ".join(
        x for x in [rec.get("venue") or fm.get("venue"), str(rec.get("year") or ""), rec.get("doi") or ""] if x
    )
    if head:
        L += [f"> {head}", ""]

    img_map = {p: path for p, path in page_images}
    for pno, text in pages:
        L += [f"## 第 {pno} 页", ""]
        for t in tables or []:
            if t.get("page") == pno and "csv" in t:
                L += [f"**[表 {t['index']}] → tables/{t['csv']}（{t['rows']}×{t['cols']}）**", ""]
        body = (text or "").strip()
        if body:
            L.append(body)
        elif pno in img_map:
            L += [f"![](../{img_map[pno]})", "", "_（本页无文本层，图像已导出，待多模态提取）_"]
        else:
            L.append("_（本页无内容）_")
        L.append("")
    return "\n".join(L)


# ------------------------------------------------------------------ HTML 资料
def convert_html(path: Path, rec: dict, root: Path):
    """公开网页原件 → md/txt。网页本身就是资料，不是"落地页失败"。"""
    from bs4 import BeautifulSoup

    raw = path.read_bytes()
    soup = BeautifulSoup(raw.decode("utf-8", "replace"), "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer"]):
        tag.decompose()
    title = (soup.title.get_text(" ", strip=True) if soup.title else "") or rec.get("title") or path.stem
    lines = [ln.strip() for ln in soup.get_text("\n", strip=True).splitlines() if ln.strip()]
    body = "\n".join(lines)
    stem = rec.get("id") or path.stem
    fm = {
        "id": stem,
        "title": title,
        "authors": rec.get("authors") or [],
        "year": rec.get("year") or "",
        "venue": rec.get("venue") or "网页存档",
        "doi": rec.get("doi") or "",
        "tier": "NA",
        "pages": 1,
        "chars": len(body),
        "chars_per_page": len(body),
        "text_layer": "HTML",
        "extract_method": "网页正文",
        "ocr": "false",
        "vision": "none",
        "tables": 0,
        "source_file": rec.get("stored") or path.name,
    }
    md_path = root / DIR_TEXT / "md" / f"{stem}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(build_md({**rec, "title": title}, fm, [(1, body)], [], []), encoding="utf-8")
    (root / DIR_TEXT / "txt" / f"{stem}.txt").write_text(
        f"=== [Page 1] ===\n{body}", encoding="utf-8"
    )
    return {
        "ok": True,
        "pages": 1,
        "chars": len(body),
        "text_layer": "HTML",
        "extract_method": "网页正文",
        "ocr": False,
        "vision_pending": False,
        "tables": [],
        "seconds": 0,
    }


def convert(pdf_path: Path, rec: dict, root: Path, do_tables: bool, ocr_mode: str,
            max_pages: int, vision_pages_max: int):
    t0 = time.time()
    doc = fitz.open(str(pdf_path))
    if doc.is_encrypted and not doc.authenticate(""):
        return {"ok": False, "reason": "加密PDF，无法读取"}

    stem = rec.get("id") or re.sub(r"[^\w\-]", "_", pdf_path.stem)[:40]
    pages, total_chars = [], 0
    for i, page in enumerate(doc):
        if max_pages and i >= max_pages:
            break
        text = page.get_text("text") or ""
        total_chars += len(text.strip())
        pages.append((i + 1, text))
    n_pages = len(pages)
    per_page = total_chars / max(1, n_pages)
    # text_layer 描述的是 PDF 自身有没有文字层，不能被后续 OCR/多模态的文字密度覆盖
    pdf_per_page = per_page

    ocr_used = False
    if per_page < 50 and ocr_mode != "off" and have_ocr():
        try:
            pages = [(p, ocr_page(doc[p - 1]) or t) for p, t in pages]
            ocr_used = True
            total_chars = sum(len(t.strip()) for _, t in pages)
            per_page = total_chars / max(1, n_pages)
        except Exception:
            ocr_used = False

    # 没有文本层、也没 OCR → 导出页面图像交给 agent 的多模态。这是默认路径，不是失败。
    page_images, stub, reused = [], "", False
    if per_page < 50 and not ocr_used:
        page_images = export_pages(doc, root, stem, min(n_pages, vision_pages_max))
        if page_images:
            stub = str(write_vision_stub(root, stem, rec.get("title") or pdf_path.stem, page_images))
            # 之前已经提取过 → 直接复用。否则 --force 重跑会把多模态成果冲掉、退回「待提取」。
            prior = parse_pages(Path(stub).read_text(encoding="utf-8", errors="replace"))
            filled = {p: t for p, t in prior.items() if len(t.strip()) >= 10}
            if len(filled) >= max(1, int(0.8 * len(page_images))):
                pages = [(p, filled.get(p, t)) for p, t in pages]
                total_chars = sum(len(t.strip()) for _, t in pages)
                per_page = total_chars / max(1, len(pages))
                reused = True
                page_images = []
    doc.close()

    layer = "正常" if pdf_per_page >= 200 else ("稀疏" if pdf_per_page >= 50 else "无")
    if ocr_used:
        method = "OCR"
    elif reused:
        method = "多模态"
    else:
        method = "多模态待提取" if page_images else "文本层"

    tables = []
    if do_tables and per_page >= 50:
        tables = extract_tables(pdf_path, root / DIR_TEXT / "tables", stem, max_pages=min(max_pages or 40, 40))

    fm = {
        "id": stem,
        "title": rec.get("title") or pdf_path.stem,
        "authors": rec.get("authors") or [],
        "year": rec.get("year") or "",
        "venue": rec.get("venue") or "",
        "doi": rec.get("doi") or "",
        "tier": rec.get("tier") or "",
        "pages": n_pages,
        "chars": total_chars,
        "chars_per_page": round(per_page),
        "text_layer": layer,
        "extract_method": method,
        "ocr": str(ocr_used).lower(),
        "vision": "done" if reused else ("pending" if page_images else "none"),
        "tables": len([t for t in tables if "csv" in t]),
        "source_file": rec.get("stored") or pdf_path.name,
    }
    md_path = root / DIR_TEXT / "md" / f"{stem}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(build_md(rec, fm, pages, tables, page_images), encoding="utf-8")
    (root / DIR_TEXT / "txt" / f"{stem}.txt").write_text(
        "\n".join(f"=== [Page {p}] ===\n{t}" for p, t in pages), encoding="utf-8"
    )

    return {
        "ok": True,
        "md": str(md_path),
        "txt": str(root / DIR_TEXT / "txt" / f"{stem}.txt"),
        "pages": n_pages,
        "chars": total_chars,
        "chars_per_page": round(per_page),
        "text_layer": layer,
        "extract_method": method,
        "ocr": ocr_used,
        "vision_pending": bool(page_images),
        "stub": stub,
        "tables": [t for t in tables if "csv" in t],
        "seconds": round(time.time() - t0, 1),
    }


# ------------------------------------------------------------------ 多模态回写
def split_frontmatter(text: str):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("["):
            try:
                fm[k] = json.loads(v)
                continue
            except Exception:
                pass
        fm[k] = v.strip('"')
    return fm, text[m.end():]


def parse_pages(text: str) -> dict:
    """按 `## 第 N 页` 或 `=== [Page N] ===` 切页，返回 {页号: 正文}。"""
    pages, cur, buf = {}, None, []
    for line in text.splitlines():
        m = PAGE_MARK.match(line.strip())
        if m:
            if cur is not None:
                pages[cur] = "\n".join(buf).strip()
            cur = int(m.group(1) or m.group(2))
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        pages[cur] = "\n".join(buf).strip()
    # 去掉骨架自带的图片引用与占位提示，避免回写成正文
    for k in list(pages):
        v = re.sub(r"^!\[\]\([^)]*\)\s*$", "", pages[k], flags=re.M)
        v = re.sub(r"^_（本页无文本层[^）]*）_$", "", v, flags=re.M)
        pages[k] = v.strip()
    return pages


def ingest(root: Path, doc_id: str, src: Path | None = None) -> int:
    md_path = root / DIR_TEXT / "md" / f"{doc_id}.md"
    src = src or (root / DIR_TEXT / "vision" / f"{doc_id}.md")
    if not src.exists():
        print(f"找不到待回写文件 {src}")
        print(f"先把 {doc_id} 的页面文字按「## 第 N 页」格式写进这个文件，再重跑。")
        return 1

    pages = parse_pages(src.read_text(encoding="utf-8", errors="replace"))
    if not pages:
        print(f"{src} 里没有解析到 `## 第 N 页` 标记，无法回写。")
        return 1

    fm = {}
    if md_path.exists():
        fm, _ = split_frontmatter(md_path.read_text(encoding="utf-8", errors="replace"))
    empty = sorted(p for p, t in pages.items() if len(t.strip()) < 10)
    total = sum(len(t.strip()) for t in pages.values())
    n = len(pages)

    rec = {
        "id": doc_id,
        "title": fm.get("title", ""),
        "venue": fm.get("venue", ""),
        "year": fm.get("year", ""),
        "doi": fm.get("doi", ""),
        "stored": fm.get("source_file", ""),
    }
    fm.update(
        {
            "pages": n,
            "chars": total,
            "chars_per_page": round(total / max(1, n)),
            # text_layer 保持 PDF 自身的判定（通常是「无」），不因为提取成功了就改口
            "extract_method": "多模态",
            "vision": "done",
        }
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(build_md(rec, fm, sorted(pages.items()), [], []), encoding="utf-8")
    (root / DIR_TEXT / "txt" / f"{doc_id}.txt").write_text(
        "\n".join(f"=== [Page {p}] ===\n{t}" for p, t in sorted(pages.items())), encoding="utf-8"
    )

    jl = root / DIR_TEXT / "fulltext.jsonl"
    existing = {}
    if jl.exists():
        for line in jl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    o = json.loads(line)
                    existing[o["id"]] = o
                except Exception:
                    pass
    existing[doc_id] = {
        "id": doc_id,
        "title": fm.get("title"),
        "md": f"{DIR_TEXT}/md/{doc_id}.md",
        "txt": f"{DIR_TEXT}/txt/{doc_id}.txt",
        "pages": n,
        "chars": total,
        "text_layer": fm.get("text_layer") or "无",
        "extract_method": "多模态",
        "ocr": False,
        "tables": 0,
    }
    jl.write_text("\n".join(json.dumps(v, ensure_ascii=False) for v in existing.values()), encoding="utf-8")

    mp = root / DIR_CACHE / "manifest_final.json"
    manifest = load_json(mp, default=[]) or []
    for r in manifest:
        if r.get("id") == doc_id:
            r["parse"] = {
                "pages": n,
                "chars": total,
                "text_layer": fm.get("text_layer") or "无",
                "extract_method": "多模态",
                "ocr": False,
                "tables": 0,
                "seconds": 0,
            }
            if r.get("stored"):
                r["status"] = "已入库"
            break
    save_json(mp, manifest)

    print(f"回写完成: {md_path}")
    print(f"  {n} 页 / {total} 字符（每页均 {round(total / max(1, n))}），取文方式=多模态")
    if empty:
        print(f"  [注意] {len(empty)} 页内容过少，确认是否漏读: {empty[:10]}")
    print("  下一步: python build_index.py --root .   # 让索引表与 db 吃到新正文")
    return 0


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="PDF 解析；无 OCR 时导出页面图像走多模态提取")
    ap.add_argument("--root", default="")
    ap.add_argument("--ids", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--tables-only", action="store_true")
    ap.add_argument("--tables", default="auto", choices=["auto", "on", "off"])
    ap.add_argument("--ocr", default="auto", choices=["auto", "off"])
    ap.add_argument("--max-pages", type=int, default=0)
    ap.add_argument("--vision-max-pages", type=int, default=60, help="导出页面图像的上限页数")
    ap.add_argument("--ingest", default="", help="把多模态提取的文字回写到指定 ID")
    ap.add_argument("--from", dest="src", default="", help="配合 --ingest 的自定义文件路径")
    args = ap.parse_args()

    if fitz is None:
        print("缺少 PyMuPDF：pip install pymupdf")
        return 1

    root = ensure_tree(find_root(args.root) if args.root else Path.cwd())

    if args.ingest:
        return ingest(root, args.ingest, Path(args.src) if args.src else None)

    manifest_path = root / DIR_CACHE / "manifest_final.json"
    manifest = load_json(manifest_path, default=[]) or []
    if not manifest:
        print(f"{manifest_path} 为空，先跑 organize.py。")
        return 1

    ids = {s.strip() for s in args.ids.split(",") if s.strip()}
    targets = [r for r in manifest if r.get("stored") and (not ids or r.get("id") in ids)]
    if not targets:
        print("没有可解析的本地文件（全是闭源/待手动获取项）。")
        return 0

    results = []
    for rec in targets:
        pdf_path = root / rec["stored"]
        is_html = pdf_path.suffix.lower() in (".html", ".htm")
        if not pdf_path.exists() or (not is_html and pdf_path.suffix.lower() != ".pdf"):
            results.append({"id": rec.get("id"), "ok": False, "reason": "文件缺失或类型不支持"})
            continue
        stem = rec.get("id") or pdf_path.stem
        md_path = root / DIR_TEXT / "md" / f"{stem}.md"
        if md_path.exists() and not args.force and not args.tables_only:
            results.append({"id": rec.get("id"), "ok": True, "reason": "已解析(跳过)"})
            continue
        try:
            if is_html:
                out = convert_html(pdf_path, rec, root)
            else:
                do_tables = args.tables == "on" or (args.tables == "auto" and not args.tables_only)
                out = convert(pdf_path, rec, root, do_tables, args.ocr, args.max_pages, args.vision_max_pages)
        except Exception as exc:
            results.append({"id": rec.get("id"), "ok": False, "reason": f"{type(exc).__name__}: {exc}"[:100]})
            continue
        out["id"] = rec.get("id")
        results.append(out)

    by_id = {r.get("id"): r for r in results}
    for rec in manifest:
        r = by_id.get(rec.get("id"))
        if not r:
            continue
        if r.get("ok") and r.get("pages"):
            rec["parse"] = {
                "pages": r.get("pages", 0),
                "chars": r.get("chars", 0),
                "text_layer": r.get("text_layer", ""),
                "extract_method": r.get("extract_method", ""),
                "ocr": r.get("ocr", False),
                "vision_pending": r.get("vision_pending", False),
                "tables": len(r.get("tables") or []),
                "seconds": r.get("seconds", 0),
            }
            if r.get("vision_pending"):
                rec["status"] = "待多模态提取"
            elif r.get("text_layer") == "无" and r.get("extract_method") not in ("多模态", "OCR"):
                rec["status"] = "需OCR"
        elif r.get("ok") is False:
            rec["status"] = "解析失败"
            rec["parse_error"] = r.get("reason")
    save_json(manifest_path, manifest)

    print_table(
        ["ID", "页", "字符", "文本层", "取文方式", "表", "说明"],
        [
            (
                r.get("id"),
                r.get("pages", 0),
                r.get("chars", 0),
                r.get("text_layer", ""),
                r.get("extract_method", ""),
                len(r.get("tables") or []),
                r.get("reason", "")[:24],
            )
            for r in results
        ],
    )
    ok = sum(1 for r in results if r.get("ok"))
    print(f"\n解析 {ok}/{len(results)} 篇；全文索引 {root / DIR_TEXT / 'fulltext.jsonl'}")

    pending = [r for r in results if r.get("vision_pending")]
    if pending:
        print("\n" + "=" * 68)
        print(f"有 {len(pending)} 篇无文本层且未配 OCR —— 用多模态提取，不要跳过：")
        for r in pending:
            print(f"  {r['id']}: {r['pages']} 页，图像在 {DIR_TEXT}/pages/{r['id']}/")
            print(f"          骨架已生成 → {r['stub']}")
        print("\n每篇三步：")
        print("  1. 用视觉能力逐页看 pages/<ID>/pNNN.png，把文字写进骨架的「## 第 N 页」下面")
        print("     · 只写原文有的；表格用 markdown 表格；图注照抄；看不清写 [不清]，不要猜")
        print("  2. python <S>/pdf_to_md.py --ingest <ID>    # 回写 md/txt 与索引")
        print("  3. python <S>/build_index.py --root .       # 刷新索引表")
        print("=" * 68)
    stuck = [r.get("id") for r in results if r.get("text_layer") == "无" and not r.get("vision_pending")]
    if stuck:
        print(f"无文本层且未导出图像：{', '.join(stuck)}（检查 --vision-max-pages 或文件是否损坏）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
