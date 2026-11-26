# 与 survey-workbench 的衔接（本文件由 survey-workbench 维护）

本目录是第三方开源 skill **CN Academic Spark** 的完整拷贝，用于把学术材料转成
可编辑的中文学术 PPTX。上游：<https://github.com/wycmochi/cn-academic-spark>，
MIT License，版权归 Yuecheng Wang（见 `LICENSE`）。

survey-workbench 不修改它的生成流程，只负责在**用户明确要 PPT 时**把已有调研成果
交接进来，并按它的 Route D（文献综述）执行。

## 交接方式

```bash
# 1) 由调研库生成交接包（报告 + 索引 + 缺口 + 可用图）
python <S>/ppt_handoff.py --root <R>

# 2) 按本目录 SKILL.md 的流程出片
#    Step 1 的 source 就是 05_报告/ppt_source/source.md
#    路线选 Route D（references/academic/route-literature-review.md）
#    模板默认 academic_defense，用户给了校模板再走 create-template
```

产物建议命名 `05_报告/<主题>_汇报.pptx`。

## 三条硬约束（与 survey-workbench 的边界）

1. **只引用已调研内容**：`source.md` 是唯一内容源，不重新检索、不新增文献、
   不补写 `source.md` 之外的数字。缺证据就写进「资料缺口」页。
2. **阶段顺序沿用报告**：报告的 `YYYY–YYYY 年：判断句` 就是 PPT 的章节顺序，
   不按主题重排。
3. **引用格式**：GB/T 7714，作者与年份必须与 `source.md` 第三节一致。

## 依赖

- 必需：`python-pptx`（SVG → DrawingML 导出）。
- 推荐：`cairosvg`（Office 兼容模式生成 PNG 后备图）；没有则自动退回 `svglib` + `reportlab`。
- 可选：`edge-tts`（旁白音频）、`.env` 中的图像后端（AI 配图）。
  不配 `.env` 时用本地模板、图标与已解析图片，不联网生图。
- PDF/DOCX 解析依赖见 `requirements.txt`，与 survey-workbench 主环境基本重合。

## 目录速览

| 路径 | 用途 |
|---|---|
| `SKILL.md` | 上游主入口（Step 1–7 全流程、质量门） |
| `references/academic/` | 学术路线、引用格式、版面映射、公式渲染 |
| `references/technicalroute/` | 技术路线图规范与素材 |
| `templates/layouts/` | 17 套版面模板（含 academic_defense） |
| `templates/icons/`、`templates/charts/` | 图标与图表库 |
| `templates/technicalroute/Custom_gallery/` | 技术路线图风格参考（体积主要来源） |
| `scripts/` | 解析、项目、SVG 质检、导出、完整性检查 |
| `conditional-workflows/` | 建模板、恢复执行、图表核对、局部改稿等按需流程 |
