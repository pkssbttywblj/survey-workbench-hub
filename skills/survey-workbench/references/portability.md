# 跨平台安装（WorkBuddy / Claude / GPT / DeepSeek）

本 skill 刻意做成**纯 Markdown + 纯 Python 标准库优先**，不依赖任何特定平台的 MCP 工具。
唯一的硬依赖是 Python 3.9+ 和四个 pip 包。

## 1. 目录约定

```
survey-workbench/
├── SKILL.md              # 唯一必读入口
├── references/           # 按需读的分册
├── scripts/              # 11 个脚本，可独立命令行运行
└── templates/            # 笔记/报告模板
```

## 2. WorkBuddy / Claude Code / Codex（有 skills 目录的平台）

```bash
# WorkBuddy
cp -r survey-workbench ~/.workbuddy/skills/
# Claude Code
cp -r survey-workbench ~/.claude/skills/
```

放在 skills 目录下即被识别，`name` + `description` 会自动进入可用列表。
脚本用绝对路径调用：`python ~/.workbuddy/skills/survey-workbench/scripts/search_all.py ...`。

Windows 上想同时给两套 agent 用，做目录联接即可（一份文件两处可见）：

```cmd
mklink /J "%USERPROFILE%\.agents\skills\survey-workbench" "%USERPROFILE%\.workbuddy\skills\survey-workbench"
```

## 3. ChatGPT

ChatGPT 没有 skills 目录，按下面的方式落：

| 形态 | 做法 | 限制 |
|---|---|---|
| **GPTs（自定义 GPT）** | Instructions 粘贴 SKILL.md 全文；Knowledge 上传 references/*.md；Actions 留空 | 无法执行 Python，只能出计划与文本 |
| **Code Interpreter / Advanced Data Analysis** | 上传整个 skill 压缩包，让它解压后按 SKILL.md 跑脚本 | 沙箱无外网时检索不可用，需平台允许联网 |

装到 GPT 时补一句开场约定，避免它跳过流程：

> 你有 survey-workbench skill。用户给研究方向时，先跑 `check_env.py`，再按 SKILL.md 的 P0–P8 执行；
> 报告的格式规范以 references/report-spec.md 为准，不要自创结构。

## 4. DeepSeek / 其他对话式模型

| 形态 | 做法 | 限制 |
|---|---|---|
| 网页版 | 把 SKILL.md 与 report-spec.md 作为附件上传，让它「按这份手册办事」 | 无代码执行，脚本步骤会退化为手工清单 |
| API / 本地部署 | 自行实现工具调用（shell + 文件读写），把 SKILL.md 作为 system prompt 的一部分 | 需要自己提供执行环境 |

对话式模型下能保住的：检索策略、筛选分级、写作规范、报告结构、检查清单。
保不住的：真实下载、PDF 解析、Excel 生成 —— 这些必须落到脚本执行环境里。

## 5. 依赖

```bash
pip install openpyxl pymupdf pdfplumber python-docx
pip install curl_cffi            # 可选，提高出版社站点下载成功率
pip install pytesseract          # 可选，OCR（还需系统装 tesseract + chi_sim 语言包）；不装则扫描件走多模态
```

`pip install` 后跑 `python <S>/check_env.py` 确认：

- `ready: true` 表示必需依赖齐了
- `usable_sources` 列出当前网络下能通的源
- `ocr_ready` / `zotero` 决定要不要降级

## 6. 能力降级表

| 缺什么 | 会怎样 | 怎么补 |
|---|---|---|
| 无外网 | 检索全 FAIL | 换网络；或只用 `acquire.py --urls` 处理离线拿到的链接 |
| 无 `fitz` | 不能解析 PDF | `pip install pymupdf` |
| 无 `openpyxl` | 不能出 Excel（仍能出 CSV + SQLite） | `pip install openpyxl` |
| 无 tesseract | 扫描件走多模态提取（导出页面图 → agent 读图回写），不影响交付 | 见 `references/pdf-pipeline.md` |
| 平台不支持读图 | 多模态这条腿断了，扫描件只能标「需OCR」 | 装 tesseract，或换一个支持视觉的模型 |
| 无 `python-docx` | 只能出 md，不能出 docx | `pip install python-docx`，或用 pandoc |
| 平台无网页搜索 | 网页/PPT 类资料搜不到 | 仍可处理论文与 Zenodo/Figshare；如实告知用户缺口 |

## 7. 设计约束（改这个 skill 时别破坏）

1. **脚本不依赖平台工具**：所有网络、文件、Excel 操作都在 Python 里完成，agent 只负责判断与写作。
2. **判定逻辑只有一处**：类型映射、语言判定、等级归类、状态推导各只有一份实现（`_common.py` / `build_index.derive_status`）。
3. **过程文件不进交付目录**：`.cache/` 之外的目录只放成品。
4. **不写死路径**：所有脚本用 `--root`，默认向上找 `survey_*`。
