# 表格高精度 OCR（按需）

## 什么时候才提这件事

**用户没要求就不提。** 只在两个地方留痕：

1. 索引表.xlsx → `说明` sheet 的一行；
2. `build_index.py` 打印的统计里一行「高精度表格提取：未启用（需要时跑 table_ocr.py hint）」。

用户明确说「表格要提准」「合并单元格要还原」时，才走下面的探测与安装流程。

## 关于「MiniOCR-0.7B / 1.5B」

截至 **2026-11**，未检索到以 `MiniOCR-0.7B` 或 `MiniOCR-1.5B` 为名的官方发布版本。
搜索结果里同名项目有多个互不相关的实现（PaddleOCR 封装、DeepSeek-OCR LoRA 微调、独立 CLI），
不能按这个名字随便下载权重。

真实可选的小参数量方案（按优先级）：

| 后端 | 参数量 | 依据 | 许可 | 地址 |
|---|---|---|---|---|
| 本机 MiniOCR CLI | 视实现 | 若已安装则优先探测 `miniocr` / `mini_ocr` | 视实现 | 本机 |
| HunyuanOCR-1.5 | 1B | 腾讯开源；仓库注明表格解析工作 StrucTab 被 ECCV 2026 主会接收；支持 `doc_parse` 等 12 类任务 | Tencent Hunyuan Community License | https://github.com/Tencent-Hunyuan/HunyuanOCR |
| PaddleOCR-VL | 约 0.9B | PaddleOCR 仓库的 VL 系列 | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR |

## 流程

```bash
# 用户要求高精度提表格时
python <S>/table_ocr.py check              # 探测本机有没有 OCR
python <S>/table_ocr.py hint               # 没有就给链接 + 目标目录 + 许可说明
# 用户确认后才装：
python <S>/table_ocr.py install --backend hunyuanocr --confirm
# 提取（对扫描件页面图）：
python <S>/table_ocr.py extract --root <R> --id P003
python <S>/build_index.py --root <R>
```

建议安装目录：`~/.survey-workbench/ocr/<后端>/`。

**确认之前不要下载。** `install` 不带 `--confirm` 什么都做不了，这是故意的。

## 与 pdfplumber 的分工

| 情况 | 走哪条 |
|---|---|
| PDF 有文本层、表格规整 | `pdf_to_md.py --tables on`，pdfplumber 直接抽 CSV，够用 |
| 扫描件 / 无文本层 | 先多模态读图（默认），用户要求高精度时再 OCR |
| 合并单元格、三线表、跨页表 | OCR-VLM 走 `table_ocr.py extract`，结果落 `tables/<ID>_pNN_ocr.csv/json` |

OCR 输出不覆盖 pdfplumber 的 CSV，用 `_ocr` 后缀并存，方便对照。
