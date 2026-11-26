# PDF 解析排障

## 1. 三级取文链

`pdf_to_md.py` 按「每页平均字符数」决定走哪一级，**前一级不可用才降级**：

| 均字符/页 | 文本层 | 走哪一级 | 能不能写进报告 |
|---|---|---|---|
| ≥ 200 | 正常 | ① 直接取文本 | 能 |
| 50–200 | 稀疏（双栏、图多、栏间空白多） | ① 直接取文本 | 能，但小心栏间串行，引用前回原文核对 |
| < 50 | 无（扫描件） | ② 有 OCR 就 OCR；③ 没 OCR 就**多模态提取** | 取文完成后能（`extract_method` 为 `OCR` 或 `多模态`） |

判定结果写进 md 的 frontmatter（`text_layer` / `extract_method` / `ocr` / `vision`）和索引表「解析状态」sheet 的「取文方式」列。

**引用规则**：只要 `extract_method` 是 `文本层`/`OCR`/`多模态` 就能引用；但 OCR 与多模态都可能误识别数字，
凡是要写进报告的**数值**，回 `03_解析全文/pages/<ID>/` 的页面图核对一遍。

## 2. OCR（可选，装了更省事）

需要两件东西：`pytesseract`（Python 包）+ `tesseract`（系统程序）+ 中文语言包。

```bash
pip install pytesseract
# Windows: 装 https://github.com/UB-Mannheim/tesseract/wiki ，勾选 Chinese (Simplified)
# 装完把 tesseract.exe 所在目录加进 PATH，再验证：
tesseract --list-langs     # 应能看到 chi_sim
```

然后 `python <S>/pdf_to_md.py --root . --ocr auto`。全自动，适合批量处理几十份扫描件。

## 2.5 多模态提取（没装 OCR 时的默认路径）

这条路径不需要任何额外依赖——用 agent 自己的视觉能力读页面图。

脚本在 `--ocr auto` 且没有 tesseract 时**自动**做三件事：

1. 每页渲染成 `03_解析全文/pages/<ID>/pNNN.png`（160 dpi，够看清正文字号）
2. 生成骨架 `03_解析全文/vision/<ID>.md`，里面已经按 `## 第 N 页` 分好段、放好图片链接
3. 在终端打印待处理清单与具体命令

agent 要做的只有填和回写：

```
1. 逐页 Read/ViewImage 看 pages/<ID>/pNNN.png
2. 在 vision/<ID>.md 的「## 第 N 页」下面写该页文字
   · 只写原文有的；表格用 markdown 表格；图注照抄
   · 看不清写 [不清（原因）]，不要猜数字
3. python <S>/pdf_to_md.py --root . --ingest <ID>
4. python <S>/build_index.py --root .
```

`--ingest` 会重写 md/txt、更新 `fulltext.jsonl` 与 manifest，并把 `text_layer` 置为 `多模态`、
`extract_method` 置为 `多模态`、状态回到 `已入库`。之后再 `query.py grep` 就能按页检索到这些文字。

骨架格式（`--ingest` 只认这两种页标记）：

```markdown
## 第 1 页

这里写第 1 页原文……

## 第 2 页

=== [Page 2] ===   ← 也接受这种写法
```

注意事项：
- 一页图模糊到读不出 → 该页写 `[不清（扫描模糊）]`，并在笔记里注明，**不要凭上下文补数字**
- 页数很多（>60）时用 `--vision-max-pages` 限制，只提取方法/结果相关的页
- 提取完 `vision/<ID>.md` 保留不动，作为「这页的机器转写」留痕，方便复核

## 3. 表格抽不出来 / 抽得很碎

pdfplumber 靠线条和空白判表，遇到三线表、跨页表、图内嵌表会失手。

处置顺序：
1. 先看 `03_解析全文/tables/*.csv` 是不是抽到了但被过滤（脚本会丢掉「全格子都短于 4 字符」的表）
2. 调 `pdf_to_md.py --tables on --force` 重抽一次
3. 还是没有 → 用 md 里的页码定位，让 agent 直接看原文页面（`Read` 图片或人工核对），
   手工把数据录入 CSV，放到 `03_解析全文/tables/<ID>_manual_t1.csv`
4. 重跑 `build_index.py`，手工表会出现在「表格清单」里

过滤阈值是有意的：图注碎片、公式块会被当成 2×2 的假表，不滤掉会让表格清单全是噪声。

## 4. 常见异常

| 现象 | 原因 | 处置 |
|---|---|---|
| `加密PDF，无法读取` | 有打开密码 | 让用户先解密，或标 解析失败 |
| `type 'NoneType' has no attribute` | PDF 结构损坏 | 换 `pypdf` 重试，仍失败则记失败 |
| md 里中文变成方框 | 缺中文字体 | 抽取不受影响（文本层没问题），只影响后续渲染/PDF 导出时换 Microsoft YaHei |
| 双栏论文读起来串行 | PyMuPDF 按内容流顺序取文本 | 关键结论回原文核对；或用 `page.get_text("blocks")` 按块重组（需要时再改脚本） |
| 页数特别大（>100）很慢 | 全页抽表是 O(页数) | 加 `--max-pages 40`，只抽前面的方法/结果部分 |
| 多模态提取后 md 仍是空的 | `--ingest` 时页标记不匹配 | 页头必须写成 `## 第 N 页` 或 `=== [Page N] ===`，且 N 是数字 |
| `--ingest` 提示某几页内容过少 | 那些页没填或只填了图片链接 | 补齐正文；确实空白就写「（本页为空白页）」 |
| 页面图看不清 | 扫描分辨率太低 | 提高 `DPI`（脚本顶部常量）或 `--force` 重新导出后复查 |

## 5. 为什么一定要落 md/txt

PDF 是二进制，`Read` 读不了，`Grep` 搜不到。落成带 `## 第 N 页` 锚点的 md 之后：
- agent 能按页码引用（report-spec 要求每条结论带页）
- `query.py grep` 能跨全部文献检索，返回「哪篇第几页出现」
- 语料可以整体重切、重排、喂给别的工具

`txt/` 保留 `=== [Page N] ===` 标记，是给 grep 和外部脚本用的最小公约格式。
**多模态提取的文字同样走这条通道**，所以扫描件进了库之后，检索体验和有文本层的论文完全一致。

## 6. 清理与重跑

```bash
python <S>/pdf_to_md.py --root . --force                 # 全部重解
python <S>/pdf_to_md.py --root . --ids P003 --force      # 只重解一篇
python <S>/pdf_to_md.py --root . --tables-only           # 只重抽表
python <S>/pdf_to_md.py --root . --ingest P003           # 回写多模态提取的文字
```

重跑是幂等的：`fulltext.jsonl` 按 ID 覆盖，不会重复追加。
`--force` 会重写 md，但**不会**清掉 `vision/<ID>.md`——那是提取留痕，删了要重读一遍图。
