---
name: survey-workbench
description: 文献资料调研工作台（端到端）。输入一个关键词或研究方向，自动完成「多源检索 → 公开资料下载 → 规范化重命名归档 → PDF 转 Markdown 可检索（扫描件无文本层时自动走 OCR，未配 OCR 则由 agent 自身多模态能力逐页读图回写）→ 生成 Excel/SQLite 索引库 → 反复交互查询与打标 → 按基金申请书「国内外研究现状」规范产出调研报告（md + docx）」。覆盖的不只是 OA 论文，还包括 arXiv 预印本、会议论文、学位论文、技术报告、幻灯片、数据集与网页资料；下载不到的闭源文献会给出 DOI、出版方与可照做的获取通道（机构 CARSI / Zotero+sci-pdf / 图书馆文献传递 / 知网），绝不假装拿到了。凡是出现「文献调研、资料收集、把这个方向摸一遍、建文献库、下载论文和报告、做索引表、查某篇的表格数据、写开题/基金本子的国内外研究现状、整理研究现状」这类意图，都必须使用本 skill——即使用户没说「调研」二字，只丢来一个研究方向和一句「先帮我看看」，也应当走这里。注意区分：只查不建库用 literature-research；只读手上已有的 PDF 用 paper-reading；本 skill 的产出是一个可持续查询的资料库加一份可直接贴进本子的报告。
license: MIT
---

# 文献资料调研工作台（survey-workbench）

一句话：**把一个关键词，变成一间可查询的资料室，加一份能直接贴进本子的研究现状。**

产出四件东西：
1. 规范化的资料库目录（`survey_<主题>/`，重命名、按语言×来源×类型×OA×等级归位）
2. 索引库（`索引表.xlsx` 七个 sheet + `library.db`），支持反复查询、打标、抽表
3. **未获取资料清单**（`05_报告/未获取资料清单.md`）：每一条带 DOI 与可点链接、出版方、失败原因、获取通道。
   这份清单是给用户去「手动补」的操作单，不是失败报告——必须跟报告一起交付
4. 调研报告（`研究现状_<主题>.md` + `.docx`），按基金申请书「1.3 国内外研究现状 + 1.4 发展动态分析」的量化规范写

## 三条红线（先看这个，其余都可商量）

1. **不编。** 影响因子、分区、被引数、论文结论里的数字，拿不到就留空标「待核实」。一条结论必须能追到某一篇的第几页。
2. **不当搬运工。** 报告不是摘要拼接。每段要有判断（谁做到了什么、还缺什么），照抄摘要即失败。
3. **不假装读到，也不轻易放弃。** 扫描件没有文本层时按三级链走：有 OCR 用 OCR；**没配 OCR 就用你自己的多模态能力逐页读图、回写正文（这是默认路径，不是应急方案）**；连图都读不出才标「需OCR」并说明原因。闭源没权限就进「待手动获取」并给通道。把失败说成失败，是本 skill 的一部分功能。

## 与另外两个 skill 的边界

| 用户想干什么 | 走哪个 |
|---|---|
| 只是查几篇文献、下几个 PDF | `literature-research` |
| 读手上已有的 PDF，做横向对比总结 | `paper-reading` |
| 从关键词建一个能反复查的资料库，最后出调研报告 | **本 skill** |

---

## 流程总览

```
P0 环境自检 → P1 需求确认(默认值优先) → P2 多源检索 → P3 筛选分级
→ P4 获取(公开优先/闭源登记) → P5 归档+解析 → P6 建索引 → P7 交互 → P8 出报告
```

全程在一个目录里干活。命令里的 `<S>` 指本 skill 的 `scripts/` 绝对路径，
`<R>` 指调研库根目录（形如 `survey_<主题>`）。

```bash
# 所有脚本都支持 --root，且默认会向上寻找 survey_* 目录，所以进了库目录后可以省略
python <S>/check_env.py --json
```

---

## P0 环境自检（先跑，30 秒）

```bash
python <S>/check_env.py
```

判读三件事：
- `OCR 通道` 不可用 → **不用另装**，扫描件走多模态提取（P5 有明确步骤）；装了则更省事
- 数据源里有 `FAIL` → P2 检索时从 `--sources` 剔除，别反复重试（Semantic 常被限流，属正常）
- `Zotero本地` 已连接 → P4 之后可以用 `zotero_bridge.py` 收编用户手动下载的 PDF

缺包就装：`pip install openpyxl pymupdf pdfplumber python-docx`（可选 `curl_cffi`，能显著提高出版社站点的下载成功率）。

## P1 需求确认（能默认就默认，一次问完）

| 项 | 默认 | 什么时候问 |
|---|---|---|
| 检索词 | 中文 + 英文对照（含同义词，如 爆轰/爆震 detonation） | 主题有歧义时 |
| 范围 | 近 5 年为主，经典高被引不排时间 | 用户说「只要最新」→ `--sort date` |
| 数量 | 论文 30~60 篇 + 非论文资料 10~20 份 | 用户给了明确篇数就照办 |
| 资料类型 | 论文 + 预印本 + 会议 + 报告 + 幻灯片 + 数据集 + 网页 | 用户只要论文时说清 |
| 库目录 | `./survey_<主题简写>` | 用户指定路径时 |
| 报告规格 | 3500 字 / 4 节 / 1750 字动态分析 | 用户给了模板或字数时 |
| 是否出报告 | 出 | 用户只要库时问一句 |

用一次提问把「主题范围、篇数、要不要报告」问掉，不要连环追问。

## P2 多源检索

**论文（7 源，一次调用内部聚合去重）**

```bash
python <S>/search_all.py "rotating detonation engine simulation" \
    --query-zh "旋转爆轰 数值模拟" --kind paper --limit 40 --per-source 25 \
    --from-date 2019-01-01 --root <R>
```

**非论文资料（幻灯片/报告/数据集/软件）**

```bash
python <S>/search_all.py "detonation engine" --kind material --limit 25 --root <R>
```

`--kind material` 走 Zenodo（presentation/poster/dataset/software）、Figshare、DataCite、
OpenAlex 的 report/thesis 类型。DataCite 是国内直连可用的 DOI 注册库，专门补数据集/软件/报告；
它相关性排序很松，脚本已在源函数里按标题/摘要做命中过滤，噪声不会进证据链。

**国内直连优先**：crossref / arxiv / europepmc / doaj / plos / datacite 实测 `--noproxy` 全部 200。
OpenAlex 本机也通，但部分国内网络要代理——`check_env.py` 报它 FAIL 时，
用 `--sources crossref,arxiv,europepmc,doaj,plos,datacite` 顶上，别反复重试 OpenAlex。
Semantic 429、Zenodo 直连失败、Figshare 403 都是已知情况，不奇怪、不重试。

**中文库扩展检索**（CNKI/万方/维普等脚本抓不到，一键链接交给你点）：

```bash
python <S>/channels.py list                       # 28 个渠道总表
python <S>/channels.py url 知网 "旋转爆轰 数值模拟"
python <S>/channels.py ask --root <R>             # 生成文献获取指引 + 授权询问
```

**网页与机构报告**（脚本抓不到的部分，用你所在平台的网页搜索工具）

搜索结果里的 URL 直接交给下载器，标题一并给进去，后面归档才认得：

```bash
python <S>/acquire.py --urls "https://xxx/report.pdf" "https://yyy/slides.pptx" \
    --titles "某实验室年度报告" "某会议报告PPT" --kind material --root <R>
```

判断要点：
- 中文文献用中文检索词再跑一遍，两批合并；CNKI 类基本下不到全文，直接进待手动获取
- 英文源覆盖不够时，用 `channels.py` 把同一检索式补到 WoS/Scopus/EI/AIAA 等机构库入口，
  这些库给用户点开检索即可，脚本不去碰登录墙
- `--limit` 是**去重后保留数**，不是每源条数；`--per-source` 才是每源抓取量
- 各源状态会打印在末尾，出现的 `FAIL` 要如实告诉用户，不要说「已全面检索」

## P3 筛选与分级（这一步由你判断，脚本只给原料）

打开 `<R>/.cache/search_raw/merged.json`，按下面顺序处理，**用 Edit 直接改这个 JSON**（字段加在记录上即可）：

1. **相关性 ★1–5**：脚本给的是关键词粗筛。你要读摘要重判——研究的是同一物理对象才给 4–5 分；
   只是方法可迁移给 3 分；只在引言里提一句给 1–2 分。低于 3 分的不要进库。
2. **中文短标题 `short_zh`**（≤14 字，如「旋转爆轰起爆机制」）。不填就会落到英文拼接的丑陋文件名。
3. **主题分节归属 `section`**（可选，取 `1`~`4`）：决定它在报告里进哪一小节。这一列是写报告的骨架。
4. **期刊等级**：`acquire.py`/`organize.py` 会自动查种子表。之后统一核实：

```bash
python <S>/journal_meta.py --pending --records <R>/.cache/acquired.json   # 列出没收录的刊
# 对每个刊用网页搜索查「刊名 影响因子 中科院分区」，查到才写回：
python <S>/journal_meta.py --set "Combustion and Flame" --if 4.4 --zone "1区" --year 2025
```

查不到就让它留在「待核实」，**不要凭印象填数字**。

5. 用表格向用户过一遍清单（标题/年/期刊/分区/被引/相关性/OA），让他挑。挑完再下载，别先下 60 篇再说。

## P4 获取

```bash
python <S>/acquire.py --records <R>/.cache/search_raw/merged.json --root <R> \
    --top 40 --min-relevance 3 --email "你的邮箱" --timeout 30
```

三级通道自动降级（环境代理 → 直连 → Chrome TLS 指纹），结果分两类：

- **成功**：落到 `.cache/staging/`，校验 `%PDF` 魔数与体积，内容 sha1 去重
- **失败**：写 `待手动获取`，带 `reason`（403/只拿到落地页/网络不可达）、`channel`（机构CARSI / Zotero+sci-pdf / 知网 / 图书馆文献传递）与 `publisher`

失败不是终点。四条补救路径，按用户条件选：

| 条件 | 做法 |
|---|---|
| 可能学校/机构有授权 | 先问一句「这几篇涉及 AIAA / ScienceDirect / 知网，你所在机构开通了哪个？没有也没关系，我直接走文献传递」——**只问一次，答「没有」就不再追问** |
| 有学校/机构权限 | 按 `channels.py guide <渠道>` 给的步骤下载（含 CARSI/校内 IP），存进 `.cache/staging/`，再跑 `organize.py` |
| 装了 Zotero + sci-pdf | `python <S>/zotero_bridge.py --status` 确认连上，再 `--import --root <R>` |
| 什么都没有 | 走 NSTL 文献传递（1–2 个工作日）或作者主页/ResearchGate 索取，如实说明「公开渠道拿不到」 |

`python <S>/channels.py ask --root <R>` 会生成 `05_报告/文献获取指引.md`：逐条 DOI 直链 + 按标题的
一键检索链接 + 可照做的下载步骤 + 授权询问话术。用户不想回答时直接跳过，不影响交付。

`sci-pdf` / 茉莉花 的配置步骤见 `python <S>/zotero_bridge.py --guide`。
**不要尝试绕过登录墙**——没有权限就是没有权限。

### Zotero MCP 分支（可选，检测到才走）

先做一次能力探测，不要假设它存在：

1. 用你所在平台的工具发现能力查有没有 **Zotero MCP**（常见名 `zotero` / `zotero-mcp`）。
2. **有 MCP**：把闭源条目按 DOI 交给 MCP 建条目，再把已下载 PDF 通过附件接口挂到对应条目上；
   把返回的 itemKey 记进 `04_精读笔记/<ID>.md` 的元数据行，便于回查。
   这样用户后续在 Zotero 里就能直接看到，无需手动搬文件。
3. **没有 MCP**：走本地桥 `zotero_bridge.py --status` / `--import`（读 Zotero 7 本地 API 或 zotero.sqlite）。
4. 两条都没有：如实记录到「待手动获取」，不要为了这条分支临时安装来路不明的 MCP。

这条分支始终可选；用户没提 Zotero 时不必主动做第 2 步。

## P5 归档 + 解析

```bash
python <S>/organize.py --root <R>          # 重命名、按 语言×类型×等级 归位、分配 ID
python <S>/pdf_to_md.py --root <R>         # PDF → md/txt/表格 CSV
```

归档结果长这样：

```
01_文献/英文_SCI1区/2023_Raman_旋转爆轰非理想性__OpenAlex_J_公开_1区.pdf
02_开放资料/幻灯片/2024_某实验室_年度进展__Zenodo_S_公开_NA.pptx
```

ID 规则：论文 `P001…`，非论文 `O001…`，**一次分配终身不变**，重跑不会漂。
目录与文件名里的等级必须一致，不一致 `build_index.py` 会标出来。

解析要点：
- 三级取文链：**文本层 → OCR（装了才用）→ 多模态（默认兜底）**。没有 tesseract 不影响交付
- `03_解析全文/md/<ID>.md` 带页码锚（`## 第 3 页`），`txt/` 是给 grep 用的
- 表格抽出到 `03_解析全文/tables/<ID>_p3t1.csv`，会在索引表「表格清单」里登记
- 解析结果里 `extract_method` 说明这份文字是怎么来的：`文本层` / `OCR` / `多模态`。写报告引用多模态提取的数值时，**必须回页面图核对一遍**

**扫描件（无文本层）怎么处理——默认走多模态，不要标了「需OCR」就放着**

脚本会自动把每页导出成 PNG、生成一张带页码的空骨架，并打印待处理清单。你只需：

1. 逐页看 `03_解析全文/pages/<ID>/pNNN.png`（用你的视觉能力读，不是让用户去读）
2. 把该页文字写进 `03_解析全文/vision/<ID>.md` 对应的「## 第 N 页」下面
   —— 只写原文有的；表格用 markdown 表格；图注照抄；看不清写 `[不清（原因）]`，**不要猜数字**
3. `python <S>/pdf_to_md.py --root <R> --ingest <ID>` 回写 md/txt 与索引
4. `python <S>/build_index.py --root <R>` 刷新索引表

一页图读不出来（模糊、旋转、手写）才标 `需OCR`，并在报告里说明该篇未纳入。

## P6 建索引

```bash
python <S>/build_index.py --root <R>
```

产出 `00_索引/索引表.xlsx`（总索引 / 待手动获取 / 解析状态 / 表格清单 / 标签统计 / 检索记录）
和 `library.db`，以及两份可直接转发的 md：

- **`05_报告/未获取资料清单.md`**——按获取通道分组，逐条 DOI、可点链接、一键检索、失败原因
- **`05_报告/文献获取指引.md`**——28 个检索渠道、逐条下载步骤、机构授权询问话术

`索引表.xlsx → 待手动获取` 与清单同源（链接都由 `link_of` 算）：标题和链接两列可点，
新增「一键检索」列指向渠道检索页；DOI 是纯文本、方便复制进文献传递系统。

之后**所有写操作只改 db，改完重跑 build_index 重画 Excel**——不要手改 Excel，
否则 db 和表会对不上。

## P7 交互（反复用的就是这一层）

```bash
python <S>/query.py stats --root <R>                          # 库健康度
python <S>/query.py list --tier 1区 --min-rel 4 -n 20 --root <R>
python <S>/query.py show P003 --root <R>                      # 单条全貌 + 它的表格
python <S>/query.py grep "peak pressure" -n 10 --root <R>     # 跨全部全文检索，带页码
python <S>/query.py tables --id P003 --print 2 --root <R>     # 打印抽出的表
python <S>/query.py note P003 --root <R>                      # 生成精读卡片骨架
python <S>/query.py tag P003 --add "方法;数据驱动" --root <R>
python <S>/query.py set P003 --field summary --value "…" --root <R>
python <S>/query.py export --out /tmp/list.md --root <R>
```

典型诉求与对应动作：

| 用户说 | 你做 |
|---|---|
| 「把这几篇的实验工况列个表」 | `query.py tables` 找表 → 读 CSV → 拼成对比表 → 需要时写回 `report` |
| 「哪几篇支持『起爆困难』这个说法」 | `query.py grep "initiation"` → 按 ID+页码组织证据 |
| 「这篇讲了什么」 | 读 `03_解析全文/md/<ID>.md` 的摘要/结论页，不要读全文 |
| 「把 A 组标成核心文献」 | `query.py tag ... --add 核心`，然后重跑 `build_index.py` |

## P8 出报告

```bash
python <S>/make_report.py --root <R> --topic "旋转爆轰发动机数值模拟"
```

脚本产出两样东西：`05_报告/report_config.json`（组织方式/字数/阶段数/引文风格，反复交互只改这里）
和带「候选证据」表的骨架 md。**骨架必须由你重写**，流程：

1. 先 `query.py list` 看候选，注意骨架已经**按年份把文献分进各研究阶段**（`organize_by: time` 默认），
   每段下面就是该阶段的候选证据，已按年份升序排好
2. 读 `03_解析全文/md/<ID>.md` 的方法与结论页，抠出可引用的量化结论（压力、效率倍数、误差）
3. 按 `references/report-spec.md` 重写：导语 150–200 字并声明按阶段展开；3 个阶段各 700–1000 字
   （段A 按年份由早到晚讲清「谁在先、谁改进了什么」，段B 收口到「当时卡在哪 + 为什么转向下一阶段」）；
   1.4 三条趋势各 250–400 字，且必须从阶段演进中长出来，末尾压成两个关键问题
4. 先把「研究脉络时间轴」表填满（阶段/年份/篇数已自动填好，你补「标志性进展」「遗留问题」两列），
   写完再**删掉所有候选证据表、时间轴表和占位符**——留着就是没写完
5. 自检，PASS 才导出：

```bash
python <S>/make_report.py --root <R> --check "05_报告/研究现状_主题.md"
python <S>/export_docx.py --root <R> "05_报告/研究现状_主题.md"
```

`--check` 会按量化基准打分：字数、引文编号连续性、参考文献条数、禁用词、超长段落、
未填占位符、量化结论处数、图片是否落地。FAIL 就改到 PASS，不要跳过。

写作规范的完整版在 `references/report-spec.md`，动笔前必读——里面是样张逐段量化出来的句式和禁忌。

## P9 出汇报 PPT（按需，用户明确要才做）

本 skill 不自己画 PPT，直接调用内置的 **CN Academic Spark** 引擎（`ppt_skill/`，MIT）。
幻灯片是**报告的极度精简**，内容只来自既有调研成果，不重新检索。

```bash
python <S>/ppt_handoff.py --root <R>     # 汇总报告+索引+缺口 → 05_报告/ppt_source/source.md
```

1. 交接包已自动带上：报告正文、GB/T 7714 文献清单、可引用数据、资料缺口、已解析图片。
   先扫一眼 `source.md`，确认主题、年份跨度、文献量没跑偏
2. 读 `ppt_skill/SKILL.md`，按它的 Step 1–7 执行；Step 1 的 source 就是 `05_报告/ppt_source/source.md`：
   **不要重新检索、不要新增文献、不要补写包外数字**，缺证据就落到「资料缺口」页
3. 路线固定 **Route D（文献综述）**，读 `ppt_skill/references/academic/route-literature-review.md`；
   模板默认 `academic_defense`，用户给了校模板再走 `conditional-workflows/create-template.md`
4. 产出 `05_报告/<主题>_汇报.pptx`，导出前按引擎的 QA gate 自检；不通过不算交付
5. 依赖：`python-pptx` 必需；`cairosvg` 或 `svglib` 用于 Office 兼容模式；
   AI 配图要 `.env` 配后端，不配就用本地模板/图标/已解析图片，不联网生图
6. 衔接约定见 `ppt_skill/SURVEY_INTEGRATION.md`

## 表格高精度 OCR（按需，不主动提）

用户没要求时，只在索引表 `说明` sheet 和统计输出里留一句提示。
用户明确要提准表格（合并单元格/三线表）时才跑：

```bash
python <S>/table_ocr.py check
python <S>/table_ocr.py hint
python <S>/table_ocr.py install --backend <名字> --confirm   # 确认后才下载
python <S>/table_ocr.py extract --root <R> --id P003
```

截至 2026-11 未查到官方 `MiniOCR-0.7B/1.5B` 发布；可选 HunyuanOCR-1.5（1B）或 PaddleOCR-VL（约 0.9B）。
详见 `references/table-ocr.md`。

---

## 脚本清单

| 脚本 | 干什么 | 关键参数 |
|---|---|---|
| `check_env.py` | 依赖/OCR/数据源/Zotero 自检 | `--json` |
| `search_all.py` | 10 源聚合检索（含 PLOS/DataCite）、去重、排序 | `--kind paper\|material\|all` `--sources` `--limit` |
| `channels.py` | 28 个中文/机构/开放渠道导航、一键检索、授权引导 | `list` `url` `guide` `ask` `for` |
| `journal_meta.py` | 影响因子/分区查询与缓存 | `--lookup` `--set` `--pending` |
| `acquire.py` | 三级通道下载 + 校验 + 失败登记；HTML 原件直接存 | `--top` `--ids` `--urls` `--email` |
| `organize.py` | 重命名、分类归位、ID 分配 | `--reclassify` `--dry-run` |
| `pdf_to_md.py` | PDF/HTML→md/txt/表格；无 OCR 时导出页面图走多模态 | `--force` `--ocr` `--ingest <ID>` |
| `zotero_bridge.py` | 收编 Zotero 条目与 PDF（MCP 优先，本地桥兜底） | `--status` `--import` `--guide` |
| `build_index.py` | SQLite + 七表 Excel（含「说明」sheet） | `--open` |
| `query.py` | 查询/打标/改字段/grep/导表 | 见 P7 |
| `make_report.py` | 报告骨架 + 规范体检（含时间顺序检查） | `--topic` `--check` |
| `export_docx.py` | md→本子版式 docx | `--out` |
| `ppt_handoff.py` | 汇总调研成果为 PPT 交接包（报告+文献+缺口） | `--root` `--max-refs` |
| `table_ocr.py` | 按需 OCR 探测/安装/提表 | `check` `hint` `install` `extract` |

## 边界

- 机构权限、验证码、二次验证一律由用户手动完成，skill 不接触账号密码，也不绕登录墙
- CNKI/万方/维普/百度学术是验证码或 JS 渲染，脚本不自动抓；给一键检索链接让用户点，
  机构通道也覆盖不了的原样标「待手动获取」，不要因此少算覆盖率
- 被引数跨源口径不同，取最大值并在说明里注明，不要当成精确值
- 加密 PDF、损坏文件 → 记为解析失败并给出原因；无文本层不算失败，走多模态提取
- 多模态提取的数值有误读风险：写进报告的每个数字都要回 `03_解析全文/pages/<ID>/` 的图核对
- 单个调研库 >300 份文件时，`build_index.py` 会明显变慢，建议按子主题拆库

## 参考文档（按需读取，不要一次全读）

| 文件 | 什么时候读 |
|---|---|
| `references/report-spec.md` | 写报告前必读——基金研究现状的量化规范与句式 |
| `references/naming-and-layout.md` | 目录结构、命名规则、Excel 列定义、状态机 |
| `references/sources.md` | 某个源查不到东西、用户点名要某个库、或要查哪些源国内直连时 |
| `references/interaction.md` | 用户提出复杂查询/改库/批量操作时 |
| `references/pdf-pipeline.md` | 解析失败、表格抽不出来、扫描件无文本层时 |
| `references/table-ocr.md` | 用户明确要求高精度提表格时 |
| `references/zotero.md` | 用户问机构权限怎么拿、Zotero MCP/本地桥怎么配 |
| `references/portability.md` | 装到 GPT / DeepSeek / Claude 等其他平台时 |
| `ppt_skill/SKILL.md` | 用户要 PPT 时读：CN Academic Spark 全流程（Route D） |
| `ppt_skill/SURVEY_INTEGRATION.md` | 与调研库的交接约定、依赖与边界 |
