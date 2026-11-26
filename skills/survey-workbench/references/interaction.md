# 交互手册：把「帮我看看」翻译成命令

用户的话往往不是命令。下面是真实会遇到的表达与对应动作，照着做，不要重新发明流程。

## 1. 查询类

| 用户说 | 动作 |
|---|---|
| 「库里现在有什么」 | `query.py stats` + `query.py list -n 20` |
| 「有哪些一区的」 | `query.py list --tier 1区` |
| 「2023 年之后的方法类文献」 | `query.py list --year 2023 --tag 方法`（`--year` 是等值，跨年用 grep + 自筛） |
| 「Xxx 那篇讲了什么」 | 先 `query.py list --venue`/`--tag` 找 ID，再读 `03_解析全文/md/<ID>.md` 的摘要与结论 |
| 「谁支持『起爆困难』这个说法」 | `query.py grep "initiation"`，按 ID+页码组织证据 |
| 「这几篇的实验工况对比一下」 | `query.py tables --print N` 取表 → 拼对比表；缺表就 `pdf_to_md.py --tables on --force` 重抽 |
| 「中文文献有几篇」 | `query.py list --json` 后按 `language` 过滤，或在 build 时看统计 |
| 「哪些没下下来？链接给我」 | 打开 `05_报告/未获取资料清单.md`（按通道分组、带 DOI 与可点链接）；Excel 的 `待手动获取` sheet 是同源的另一份 |
| 「中文库里的文献怎么找」 | `channels.py list` 看渠道；`channels.py url 知网 "<检索式>"` 给一键链接；说明脚本不抓验证码站点，需用户点开 |
| 「openalex 我这里打不开」 | 用 `--sources crossref,arxiv,europepmc,doaj,plos,datacite`，这几源实测国内直连 200，覆盖足够起步 |
| 「我有学校图书馆账号」 | `channels.py ask --root <R>` 生成指引 → 按涉及的库给 `channels.py guide <渠道>` 的 CARSI/校内 IP 步骤；只问一次授权，不反复确认 |
| 「没有图书馆权限」 | 不再追问授权，直接给 NSTL 文献传递与作者主页/ResearchGate 索取路径 |

`query.py list --json` 给的是完整记录，适合你要做进一步判断时用；纯表格输出是给人看的。

## 2. 改库类

| 用户说 | 动作 |
|---|---|
| 「把这几篇标成核心」 | `query.py tag P001 --add 核心`（多个用分号） |
| 「这篇的分区不对，是 2 区」 | `journal_meta.py --set "刊名" --zone 2区` → `organize.py --reclassify` → `build_index.py` |
| 「这篇要加一句话说明」 | `query.py set P003 --field summary --value "…"` |
| 「帮我建精读卡片」 | `query.py note P003`，然后按 `references/report-spec.md` 的「必答问题」填 |
| 「删掉这条」 | 库里不提供删除命令。把 `status` 改成 `弃用`，重跑 build_index；物理删除由用户手动做 |

**顺序铁律**：改 db（query/journal_meta）→ 重跑 `build_index.py`。
改了 Excel 不重跑 db，下一次 build 会把手改的内容覆盖掉，用户会以为数据丢了。

## 3. 批量操作

要批量改字段时，不要一条条敲命令，写个一次性脚本读 `library.db` 直接 UPDATE，再重新 build。
例：把所有 `tier=待核实` 且语种为中文的记录标为「核心待定」——

```bash
python -c "
import sqlite3; c=sqlite3.connect('00_索引/library.db')
c.execute(\"UPDATE docs SET tier='待核实' WHERE language='中文'\"); c.commit()"
python <S>/build_index.py --root .
```

## 4. 精读与提炼

读 md 的正确姿势（几万字不要整篇吞）：

1. 先看 frontmatter 的 `pages / chars / text_layer`，判断值不值得精读
2. `Grep` 定位 `abstract|conclusion|results|Table [0-9]` 的行号
3. 按行号区间读摘要、方法首段、实验主表、结论
4. 需要数值时用 `query.py grep "MPa|%|times"` 直接捞带单位的句子

每篇的笔记写到 `04_精读笔记/<ID>_<短标题>.md`，frontmatter 至少填 `tags/summary/findings` 三项，
`build_index.py` 会把它们吸进索引表的「标签」「一句话说明」「核心结论」三列。

## 5. 报告迭代

| 用户说 | 动作 |
|---|---|
| 「字数少一点」 | 改 `report_config.json` 的 `survey_chars`，重跑 `make_report.py`，按新预算重写 |
| 「改成按主题写，别按时间」 | `organize_by` 改 `topic`，`sections` 改 4，`section_titles` 填主题名，重跑 |
| 「阶段分得再细一点」 | `sections` 改 4–5，time 模式会按年跨度重新等分，候选证据会重新归组 |
| 「不要时间轴表」 | `include_timeline` 改 false |
| 「引文编号要接着正文往下排」 | 改 `ref_start`，正文引文与参考文献表一起偏移 |
| 「加一张方法对比表」 | 在对应小节插 md 表格，`export_docx.py` 会自动转成带边框的 Word 表 |
| 「这段太像综述」 | 加判断句、去掉「综上所述」类连接词，见 report-spec 第 6 节禁忌 |

## 6. 常见失败与处置

| 现象 | 根因 | 处置 |
|---|---|---|
| `Path("")` 相关报错 / `Cannot move '.' into itself` | 记录没有文件却走了移动分支 | 已修（`organize.py` 用 `is_file()` 判）。若还出现，检查 `acquired.json` 里 `file` 字段 |
| Excel 里状态全是「归档失败」 | 同上的历史脏数据 | 重跑 `organize.py` → `build_index.py`，状态由 `derive_status` 从事实重算 |
| 重跑后 ID 变了 | manifest 被删或换库 | ID 存在 `.cache/manifest_final.json`，不要删它 |
| 表格 doc_id 显示成 `P` | 正则捕获组取错 | 已修，正则为 `((?:P|O)\d{3})` |
| 分区全是「待核实」 | 种子表没收录该刊 | `journal_meta.py --pending` 看清单，查证后 `--set` |
| `--check` 报「未填占位符」 | 骨架里的 `〈TODO…〉` 没删 | 那是提示，正文写完后必须删 |
| 状态一直是 `待多模态提取` | 扫描件还没被读 | 按 `pdf-pipeline.md` §2.5 逐页读图 → `--ingest` → 重跑 `build_index.py` |
| `--ingest` 说找不到文件 | 骨架路径不对或还没生成 | 默认读 `03_解析全文/vision/<ID>.md`，也可 `--from` 指定 |
| 回写后 grep 还是搜不到 | 忘了重跑 build_index / 页标记格式错 | 页头必须是 `## 第 N 页`；先 `pdf_to_md.py --ingest` 再 `build_index.py` |
