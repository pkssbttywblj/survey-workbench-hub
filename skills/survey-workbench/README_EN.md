# `survey-workbench` Skill

[中文说明](README.md)

Turn a keyword into a searchable literature library and a grant-style research-status report, then condense that report into a scientific presentation.

## What To Use It For

- Survey a research direction before a proposal or thesis opening
- Build a reusable library (Excel index + SQLite) instead of a one-off folder
- Write "research status and development trends" organized by research stage, earliest to latest
- Produce a presentation whose logic exactly matches the report
- Extend coverage to Chinese and institutional databases via 28 one-click search links and optional access guidance

## Typical Requests

```text
Survey the research status of composite curing optimization by stage.
Download whatever is publicly available, give DOI and access routes for closed papers,
then produce the report and a presentation.
```

## What You Need To Provide

- A research keyword (Chinese or English)
- Optional: number of papers, date range, whether a PPT is needed, institutional access

## Outputs

| Output | Location |
|---|---|
| Normalized library | `01_文献/`, `02_开放资料/` |
| Index | `00_索引/索引表.xlsx`, `library.db` |
| Manual-download list | `05_报告/未获取资料清单.md` (DOI + one-click search) |
| Access guide | `05_报告/文献获取指引.md` (28 channels + optional access question) |
| Report | `05_报告/研究现状_<topic>.md` / `.docx` |
| Presentation | `05_报告/研究现状_<topic>.pptx` |

## Boundaries

- Institutional login and CAPTCHA are handled by the user; no credentials are stored
- CNKI/Wanfang/CQVIP use CAPTCHA or JS rendering; the skill gives one-click links, asks about institutional access only once
- Scanned pages go through multimodal reading; OCR is opt-in for high-precision tables
- PPT engine is bundled (CN Academic Spark with templates, icons and route-diagram library, ~102 MB); invoked only when a deck is requested, and it reads from the existing survey results

## Workflow

P0 environment check → P1 requirements → P2 multi-source search → P3 screening → P4 acquisition → P5 archive/parse → P6 index → P7 interaction → P8 report → P9 PPT.

Full rules and script flags are in [SKILL.md](SKILL.md).

## Runtime and Dependencies

Python 3.9+; `openpyxl`, `pymupdf`, `pdfplumber`, `python-docx`, `beautifulsoup4`.
Optional: `curl_cffi` for better download success, tesseract / an OCR VLM for precise tables.

## Example Preview

See `assets/` at the repository root and the real run in `examples/demo_library/`.

## Related Skills

- `literature-research`: search and download only, no library
- `paper-reading`: read local PDFs and compare them
- `CN Academic Spark` (bundled at `ppt_skill/`): Chinese academic deck engine, Route D literature review; MIT
