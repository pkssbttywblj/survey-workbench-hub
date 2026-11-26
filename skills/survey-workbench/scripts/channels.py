#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索渠道导航 + 机构授权引导。

为什么单独一个脚本：
  中文库（CNKI/万方/维普/百度学术/ChinaXiv/NSTL）实测是验证码、412 或 JS 渲染，
  search_all.py 抓不到。但用户在浏览器里点一下就能检索。把它们做成「一键检索链接 +
  可照做的下载步骤」，比假装自动检索诚实，也比什么都不给有用。

命令:
  python channels.py list                          # 全部渠道表
  python channels.py url 知网 "爆轰 数值模拟"       # 生成可点的检索 URL
  python channels.py guide 万方                     # 看某个渠道的获取步骤
  python channels.py ask --root survey_XX          # 生成 05_报告/文献获取指引.md
  python channels.py for P003 --root survey_XX     # 给单条记录推荐渠道

判读:
  - ask 只在有「待手动获取」记录时生成指引；没有就直接说明无需补全文
  - 授权询问是可选的：用户答「没有」就走 NSTL/文献传递，不反复追问
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DIR_REPORT, load_json, print_table, setup_stdout  # noqa: E402

# key -> (名称, 分组, 检索URL模板, 是否需机构授权, 一句话说明)
# {q} 会被 URL 编码后替换。模板均为站点公开的检索路径，2026-11-25 实测可达。
CHANNELS = {
    "cnki": ("知网CNKI", "中文库", "https://kns.cnki.net/kns8s/defaultresult/index?kw={q}",
             True, "中文期刊/硕博论文主力库；自动抓取会遇滑块验证，需浏览器操作"),
    "wanfang": ("万方", "中文库", "https://s.wanfangdata.com.cn/paper?q={q}",
                True, "中文期刊/学位论文/会议；站内检索可用，全文需机构权限"),
    "cqvip": ("维普", "中文库", "https://qikan.cqvip.com/Qikan/Search/Index?key={q}",
              True, "中文期刊；直连有 412 风控，浏览器里可正常检索"),
    "baiduxueshu": ("百度学术", "中文聚合", "https://xueshu.baidu.com/s?wd={q}",
                    False, "聚合检索中英文题录、给出 DOI 与各库入口；有反爬验证"),
    "chinaxiv": ("ChinaXiv", "中文预印本", "https://www.chinaxiv.org/home.htm",
                 False, "中科院预印本平台；进入后用站内搜索，多数可免费下全文"),
    "paper": ("中国科技论文在线", "中文预印本", "https://www.paper.edu.cn/search?keyword={q}",
              False, "首发论文平台；结果页 JS 渲染，浏览器可用，不少全文免费"),
    "nstl": ("NSTL国家科技图书文献中心", "文献传递", "https://www.nstl.gov.cn/search?searchType=all&q={q}",
             False, "外文期刊/会议/专利/标准；可全文传递，机构用户常有补贴"),
    "nstrs": ("国家科技报告服务系统", "中文报告", "https://www.nstrs.cn/",
              False, "国家级科技报告；进入后站内检索，多数报告可在线阅览"),
    "ncpssd": ("国家哲学社会科学文献中心", "中文库", "https://www.ncpssd.cn/",
               False, "社科期刊免费获取为主；理工方向只作补充"),
    "cbm": ("CBM中国生物医学文献", "中文库", "https://www.sinomed.ac.cn/",
            False, "生物医学方向补充，工程类召回低"),
    "duxiu": ("读秀", "中文图书", "https://www.duxiu.com/search?sw={q}",
              True, "中文图书/章节；需要机构权限，适合查专著与教材"),
    "zhizhen": ("超星发现", "中文聚合", "https://ss.zhizhen.com/?sw={q}",
                True, "图书+期刊统一检索；需机构权限"),
    "wos": ("Web of Science", "机构库", "https://apps.webofknowledge.com/",
            True, "引文检索权威；需要学校/院所订阅"),
    "scopus": ("Scopus", "机构库", "https://www.scopus.com/search/form.uri?display=basic",
               True, "覆盖面广、引文统计好；需要机构订阅"),
    "ei": ("EI Compendex", "机构库", "https://www.engineeringvillage.com/",
           True, "工程索引，会议论文覆盖好；需要机构订阅"),
    "sciencedirect": ("ScienceDirect", "机构库", "https://www.sciencedirect.com/search?qs={q}",
                      True, "Elsevier 全文；CARSI/校内 IP 通常可覆盖"),
    "springer": ("SpringerLink", "机构库", "https://link.springer.com/search?query={q}",
                 True, "Springer 全文与图书；CARSI 通常可覆盖"),
    "wiley": ("Wiley Online Library", "机构库", "https://onlinelibrary.wiley.com/action/doSearch?AllField={q}",
              True, "Wiley 全文；CARSI 通常可覆盖"),
    "aiaa": ("AIAA ARC", "机构库", "https://arc.aiaa.org/action/doSearch?AllField={q}",
             True, "航空航天会议/期刊主库；多数需机构权限或作者主页"),
    "asme": ("ASME Digital Collection", "机构库", "https://asmedigitalcollection.asme.org/search-results/?q={q}",
             True, "机械/能源会议论文；多数需机构权限"),
    "ieee": ("IEEE Xplore", "机构库", "https://ieeexplore.ieee.org/search/searchresult.jsp?queryText={q}",
             True, "电气/自动化；IEEE 会员或机构权限"),
    "acm": ("ACM Digital Library", "机构库", "https://dl.acm.org/action/doSearch?AllField={q}",
            True, "计算机方向；机构权限或 ACM 会员"),
    "pubmed": ("PubMed", "开放库", "https://pubmed.ncbi.nlm.nih.gov/?term={q}",
               False, "生物医学开放检索；工程方向召回低"),
    "arxiv": ("arXiv", "开放库", "https://arxiv.org/search/?query={q}&searchtype=all",
              False, "预印本，全文免费；脚本端 search_all 已自动检索"),
    "googlescholar": ("Google Scholar", "开放聚合", "https://scholar.google.com/scholar?q={q}",
                      False, "引文与作者页；国内需可用网络，不作脚本依赖"),
    "nasa": ("NASA NTRS", "开放库", "https://ntrs.nasa.gov/search?q={q}",
             False, "NASA 技术报告，全文多为公有领域"),
    "dtic": ("DTIC", "开放库", "https://discover.dtic.mil/?q={q}",
             False, "美国国防技术报告；部分受限"),
    "researchgate": ("ResearchGate", "作者通道", "https://www.researchgate.net/search/publication?q={q}",
                     False, "用 Request full-text 向作者索取，常比图书馆快"),
}

# 出版方/DOI 前缀 -> 推荐渠道 key（按优先级）
PUBLISHER_MAP = [
    ("elsevier", ["sciencedirect", "nstl"]),
    ("sciencedirect", ["sciencedirect", "nstl"]),
    ("springer", ["springer", "nstl"]),
    ("wiley", ["wiley", "nstl"]),
    ("aiaa", ["aiaa", "researchgate", "nstl"]),
    ("asme", ["asme", "researchgate", "nstl"]),
    ("ieee", ["ieee", "researchgate"]),
    ("acm", ["acm", "researchgate"]),
    ("taylor", ["nstl", "researchgate"]),
    ("sage", ["nstl", "researchgate"]),
    ("nature", ["nstl", "researchgate"]),
    ("cnki", ["cnki", "wanfang"]),
    ("万方", ["wanfang", "cnki"]),
    ("维普", ["cqvip", "cnki"]),
]

GUIDE = {
    "cnki": "① 点检索链接 ② 校内 IP 或 CARSI 登录（机构入口 → 搜学校名 → Shibboleth）"
            "③ 下载 PDF/CAJ ④ 放进 `.cache/staging/` 再跑 `organize.py`",
    "wanfang": "① 点检索链接 ② 机构登录或校内 IP ③ 下载 PDF "
               "④ 放进 `.cache/staging/` 再跑 `organize.py`",
    "cqvip": "① 浏览器打开检索链接（脚本直连会遇 412）② 机构登录 ③ 下载 ④ 走 organize.py",
    "baiduxueshu": "① 点检索链接 ② 记录标题与 DOI ③ 点右侧「全文链接」看哪个库有权限 "
                   "④ 下载后走 organize.py",
    "chinaxiv": "① 进入首页后在站内搜标题 ② 详情页找 PDF 直链 ③ 下载后走 organize.py",
    "paper": "① 点检索链接 ② 详情页下载全文（多篇免费）③ 走 organize.py",
    "nstl": "① 点检索链接 ② 检索到条目后点「原文传递」③ 填机构与邮箱，通常 1–2 个工作日 "
            "④ 收到 PDF 后走 organize.py",
    "nstrs": "① 进入系统后站内检索报告名 ② 点「在线阅览/下载」③ 走 organize.py",
    "ncpssd": "① 进入后按篇名检索 ② 直接下载（多为免费）③ 走 organize.py",
    "cbm": "① 进入后按篇名检索 ② 按机构权限获取 ③ 走 organize.py",
    "duxiu": "① 点检索链接 ② 机构登录 ③ 图书按章节下载 ④ 走 organize.py",
    "zhizhen": "① 点检索链接 ② 机构登录 ③ 下载后走 organize.py",
    "wos": "① 校内/机构入口进 Web of Science ② 按 DOI 或标题检索 ③ 点出版方链接转全文 "
           "④ 下载后走 organize.py",
    "scopus": "① 机构入口进 Scopus ② 按 DOI 检索 ③ 经 Full text links 转全文 ④ 走 organize.py",
    "ei": "① 机构入口进 Engineering Village ② 按标题检索 ③ 点 DOI 转全文 ④ 走 organize.py",
    "sciencedirect": "① 打开 https://doi.org/<DOI> ② Sign in → 机构登录 → 搜学校名走 CARSI "
                     "③ Download PDF ④ 走 organize.py；无权限时转 NSTL 文献传递",
    "springer": "① 打开 DOI ② 机构登录/CARSI ③ 下载 PDF ④ 走 organize.py",
    "wiley": "① 打开 DOI ② 机构登录/CARSI ③ 下载 PDF ④ 走 organize.py",
    "aiaa": "① 打开 DOI（arc.aiaa.org）② 机构登录 ③ 无权限先去作者主页/课题组页面找 preprint "
            "④ 仍无则 ResearchGate 请求或 NSTL 传递",
    "asme": "① 打开 DOI ② 机构登录 ③ 无权限走作者主页/ResearchGate/NSTL",
    "ieee": "① 机构或会员登录 ② 下载 PDF ③ 走 organize.py",
    "acm": "① 机构或会员登录 ② 下载 PDF ③ 走 organize.py",
    "pubmed": "① 检索题名 ② 有 PMC 全文就直接下 ③ 只有摘要时记下 DOI 转开放库/机构库",
    "arxiv": "① 检索题名 ② 详情页 Download PDF ③ 走 organize.py（脚本通常已自动下好）",
    "googlescholar": "① 检索题名 ② 右侧找 [PDF] 链接 ③ 优先点机构或作者主页的链接 ④ 走 organize.py",
    "nasa": "① 站内检索 ② 详情页 Download ③ 走 organize.py",
    "dtic": "① 站内检索 ② 有公开的报告直接下载 ③ 走 organize.py",
    "researchgate": "① 搜标题 ② 有 PDF 直接下；没有就点 Request full-text "
                    "③ 收到后走 organize.py",
}
DEFAULT_GUIDE = "① 打开检索链接 ② 机构登录或找作者主页 ③ 下载后放进 `.cache/staging/` 再跑 `organize.py`"

_ASK_HEAD = """# 文献获取指引

> 本文件只在有「待手动获取」条目时生成。授权询问是**可选**的：
> 回答「没有」就直接按第 3 节走文献传递，不需要额外确认。

## 1. 需要补全文的条目

共 **{n}** 条。
"""


def norm_key(name: str) -> str:
    key = str(name or "").strip().lower()
    if key in CHANNELS:
        return key
    for k, v in CHANNELS.items():
        if key == v[0].lower() or key in v[0].lower():
            return k
    return ""


def search_url(key: str, query: str) -> str:
    key = norm_key(key)
    if not key:
        return ""
    tmpl = CHANNELS[key][2]
    return tmpl.replace("{q}", urllib.parse.quote(str(query), safe=""))


def recommend(rec: dict) -> list:
    """按 DOI/出版方/语言推荐最多 4 个渠道，优先开放与作者通道。"""
    hay = " ".join(
        str(rec.get(k) or "") for k in ("doi", "venue", "publisher", "url", "title", "language")
    ).lower()
    keys = []
    lang = str(rec.get("language") or "")
    if "中文" in lang:
        keys += ["cnki", "wanfang", "baiduxueshu"]
    if "报告" in str(rec.get("title") or "") or "report" in hay:
        keys += ["nstrs", "nstl"]
    for needle, candidates in PUBLISHER_MAP:
        if needle in hay:
            keys += candidates
            break
    if not keys:
        keys = ["baiduxueshu", "researchgate", "nstl"]
    keys += ["nstl", "researchgate"]
    out, seen = [], set()
    for k in keys:
        if k in seen or k not in CHANNELS:
            continue
        seen.add(k)
        out.append(k)
        if len(out) == 4:
            break
    return out


def _authored_by_author(rec: dict) -> bool:
    return str(rec.get("channel") or "") in ("作者主页", "ResearchGate")


def build_guide(root: Path) -> Path | None:
    manifest = load_json(root / ".cache" / "manifest_final.json", default=[]) or []
    pending = [r for r in manifest if not r.get("stored")]
    out = root / DIR_REPORT / "文献获取指引.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not pending:
        out.write_text("# 文献获取指引\n\n> 本次全部获取成功，无需补全文。\n", encoding="utf-8")
        return out

    L = [_ASK_HEAD.format(n=len(pending))]
    for d in pending:
        doi = (d.get("doi") or "").strip()
        title = (d.get("title") or "").strip() or "（无标题）"
        year = d.get("year") or ""
        pub = (d.get("publisher") or d.get("venue") or "").strip()
        reason = (d.get("reason") or "").strip()
        L.append(f"- **{d.get('id')}** · {year} {title}"
                 + (f"  \n  DOI: `{doi}`" if doi else "  \n  DOI: —（按标题检索）")
                 + (f"  \n  出版方: {pub}" if pub else "")
                 + (f"  \n  未获取原因: {reason}" if reason else ""))
    L.append("")

    involved = sorted({CHANNELS[k][0] for d in pending for k in recommend(d) if CHANNELS[k][3]})
    L += ["## 2. 机构授权询问（可选）", ""]
    if involved:
        L.append(f"本轮涉及：**{' / '.join(involved)}**。")
        L.append("")
        L.append("你所在机构是否已开通其中任何一个数据库？可只回答其中一部分：")
        L.append("")
        L.append("- **有** —— 我可以按第 3 节给出具体下载步骤（含 CARSI 登录、sci-pdf 配置）。"
                 "下载后放进 `.cache/staging/`，再跑 `organize.py` 即可自动入库。")
        L.append("- **没有** —— 跳过本步，直接走第 3 节的 NSTL / 图书馆文献传递"
                 "（通常 1–2 个工作日）。")
        L.append("- **不方便透露** —— 不需要回答，按开放渠道和文献传递处理即可。")
    else:
        L.append("本轮条目都可走开放渠道，无需机构授权。")
    L.append("")

    L += ["## 3. 逐条获取路径", ""]
    for d in pending:
        title = (d.get("title") or "").strip() or "（无标题）"
        doi = (d.get("doi") or "").strip()
        # 站内检索用标题：多数库对 DOI 字符串召回很差。DOI 另给一条直链。
        query = title
        L.append(f"### {d.get('id')}　{title[:80]}")
        L.append("")
        if doi:
            L.append(f"- **DOI 直链** [打开](https://doi.org/{doi})　"
                     "跳到当前出版方页面，通常比抓到的落地页耐用")
        for k in recommend(d):
            name, group, _tmpl, auth, note = CHANNELS[k]
            url = search_url(k, query)
            link = f"[检索]({url})" if url else "[入口]()"
            L.append(f"- **{name}**（{group}{'，需机构授权' if auth else ''}）{link}　{note}")
            L.append(f"  - {GUIDE.get(k, DEFAULT_GUIDE)}")
        L.append("")

    L += ["## 4. 补充检索渠道总表", "",
          "初步检索没覆盖到时，可把同一检索式粘到下列渠道再查一轮。", "",
          "| 渠道 | 分组 | 检索入口 | 机构授权 | 说明 |",
          "|---|---|---|---|---|"]
    for name, group, tmpl, auth, note in CHANNELS.values():
        url = tmpl.replace("{q}", urllib.parse.quote("关键词", safe=""))
        link = f"[打开]({url})"
        L.append(f"| {name} | {group} | {link} | {'需要' if auth else '不需要'} | {note} |")
    L += ["", "---", "",
          "脚本端 `search_all.py` 只自动检索有开放 API 的源；本表的中文库需人工检索，"
          "这是站点验证码/JS 渲染决定的，不代表资料不存在。"]
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description="检索渠道导航与机构授权引导")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    p = sub.add_parser("url")
    p.add_argument("channel")
    p.add_argument("query")

    p = sub.add_parser("guide")
    p.add_argument("channel")

    p = sub.add_parser("ask")
    p.add_argument("--root", required=True)

    p = sub.add_parser("for")
    p.add_argument("id")
    p.add_argument("--root", required=True)

    args = ap.parse_args()

    if args.cmd == "list":
        rows = [(v[0], v[1], "需要" if v[3] else "不需要", v[4]) for v in CHANNELS.values()]
        print_table(["渠道", "分组", "机构授权", "说明"], rows)
        print(f"\n共 {len(CHANNELS)} 个渠道。")
        return 0

    if args.cmd == "url":
        key = norm_key(args.channel)
        if not key:
            print(f"未知渠道：{args.channel}")
            return 2
        print(search_url(key, args.query))
        return 0

    if args.cmd == "guide":
        key = norm_key(args.channel)
        if not key:
            print(f"未知渠道：{args.channel}")
            return 2
        name, group, tmpl, auth, note = CHANNELS[key]
        print(f"{name}（{group}｜{'需机构授权' if auth else '开放'}）")
        print(note)
        print(GUIDE.get(key, DEFAULT_GUIDE))
        return 0

    root = Path(args.root).resolve()
    if args.cmd == "ask":
        out = build_guide(root)
        print(f"文献获取指引：{out}")
        txt = out.read_text(encoding="utf-8")
        print("\n".join(txt.splitlines()[:12]))
        return 0

    if args.cmd == "for":
        manifest = load_json(root / ".cache" / "manifest_final.json", default=[]) or []
        rec = next((r for r in manifest if r.get("id") == args.id), None)
        if not rec:
            print(f"未找到 {args.id}")
            return 2
        print(f"{args.id}　{rec.get('title') or ''}")
        for k in recommend(rec):
            name, group, _tmpl, auth, note = CHANNELS[k]
            print(f"  - {name}（{group}{'，需授权' if auth else ''}）"
                  f" {search_url(k, rec.get('title') or rec.get('doi') or '')}")
            print(f"    {GUIDE.get(k, DEFAULT_GUIDE)}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
