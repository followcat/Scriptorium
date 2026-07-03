<p align="center">
  <img src="docs/assets/readme-hero.png" alt="Scriptorium PDF - structured PDF to editable annotated HTML" width="100%">
</p>

<h1 align="center">Scriptorium PDF</h1>

<p align="center">
  <strong>把 PDF、网页打印 PDF 和 OCR 结构结果转换成可编辑、可标注、可回归评测的 HTML。</strong>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2b6cb0">
  <img alt="Status" src="https://img.shields.io/badge/status-core%20prototype-2f855a">
  <img alt="Structured HTML" src="https://img.shields.io/badge/output-annotated%20HTML-6b46c1">
  <img alt="Tests" src="https://img.shields.io/badge/tests-42%20passing-2f855a">
</p>

## What It Does

Scriptorium PDF 是一个核心转换引擎，目标不是把 PDF 页面截图塞进 HTML，而是把 PDF 里的可识别结构转成可编辑节点：

- 从 PDF 提取 native text、字体、颜色、粗细、坐标、image block 和 drawing/shape。
- 从 OCR / PaddleOCR-VL / PP-Structure 输出归一化到同一个 `DocumentIR`。
- 生成 `structured` HTML：文本节点可编辑，图形节点保留结构，DOM 上带识别标记。
- 支持 XML 级局部节点编辑，再回写到 IR 并重新导出 HTML/PDF。
- 用 Playwright 打印网页或 HTML 为 PDF，再用渲染图对比生成相似度指标。
- 用 benchmark 记录优化前后的可比较分数。

它适合做 PDF 编辑、翻译、版面重建、OCR 结构验证、HTML-PDF 转换质量评测的底层实验平台。

## Core Requirements

Scriptorium 的实现围绕四个硬需求设计：

| Requirement | Meaning |
|---|---|
| Structured output | 产出的 HTML 需要有文本、shape、role、bbox、style id、source marker，而不是单张整页截图。 |
| Local editability | 每个可编辑文本节点都有稳定 element id，可通过 DOM 或 XML 精确修改局部内容。 |
| Source preservation | OCR/native 原文保存在 `source_text`，编辑写入 `edited_text`，翻译写入 `translated_text`，不覆盖原始识别结果。 |
| Measurable quality | 每次转换都能打印回 PDF 并计算 `visual_similarity`、diff 分布、页数匹配和尺寸匹配，后续优化用同一指标比较。 |

## Why It Is Different

很多 PDF-to-HTML 工具会先渲染整页图片，然后把透明文本覆盖上去。那种方式视觉上容易接近，但局部编辑能力很弱。

Scriptorium 的 `structured` 模式明确避免整页图片：

```html
<div
  data-scriptorium-role="table-cell-text"
  data-scriptorium-source="native-pdf"
  data-scriptorium-style-id="style-004"
  data-scriptorium-layout-group="table-001"
  data-scriptorium-layout-kind="table"
  data-scriptorium-layout-confidence="0.86"
  data-scriptorium-semantic-order="12"
  data-scriptorium-column-count="2"
  data-scriptorium-reading-order-strategy="recursive-xy-cut-v1"
  data-scriptorium-reading-order-region="root/h1/v0"
  data-scriptorium-edit-target="edited_text"
  data-bbox-pdf="76.99,212.49,117.83,224.22"
  contenteditable="true"
>
  PDF text
</div>
```

每个节点都能追溯到来源、坐标、样式桶、版面分组和编辑目标。普通 drawing 会保留为 SVG line/path；复杂矢量图会在局部区域触发 raster fallback，仍然是带 bbox/source metadata 的局部 image 节点，不是整页背景图。

## Real-World Scores

<p align="center">
  <img src="docs/assets/readme-webpage-score.png" alt="Live webpage conversion score" width="100%">
</p>

| Sample | Pages | Elements | Editable | Images | Shapes | Multi-Col | Visual Similarity | Max Diff | Mean Diff | Page/Size Match |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Hacker News live page printed by Playwright | 2 | 162 | 95 | 30 | 37 | 0 | 0.9800288 | 0.0199712 | 0.01032101 | yes / yes |
| arXiv paper: Attention Is All You Need | 15 | 876 | 761 | 6 | 109 | 163 | 0.96840246 | 0.03159754 | 0.02179977 | yes / yes |
| ACL paper: Transformer-XL | 11 | 1558 | 1446 | 2 | 110 | 880 | 0.95658128 | 0.04341872 | 0.03665926 | yes / no |
| Built-in benchmark fixtures, mean | 6 pages total | 72 | 53 | 0 | 19 | 20 | 0.99036719 | 0.01183004 | 0.00960058 | yes / yes |

`visual_similarity = 1 - max_diff_ratio`。`max_diff_ratio` 现在包含页数缺失和页面尺寸不匹配惩罚；报告会同时输出 `mean_diff_ratio`、`p95_diff_ratio`、`worst_page`、`page_count_match` 和 `dimension_match`，避免错误页面被 resize 后看起来“相似”。

Transformer-XL 的 `dimension_match = false` 来自 Chromium 打印 A4 页面时产生的 1px 宽度量化差异；页数匹配，diff 仍按未拉伸画布严格比较。

内置 fixtures 同时带 `.semantic-order.json` ground truth。当前 `semantic_order_pair_accuracy = 1.0`，`semantic_sequence_similarity = 1.0`，覆盖 53 个期望文本节点；其中 20 个多栏文本节点由 `recursive-xy-cut-v1` 负责排序。arXiv Attention 论文有 repo 内部分人工 sidecar，覆盖 5 页、38 个关键文本点，`semantic_order_pair_accuracy = 1.0`。Transformer-XL 论文新增真实双栏 sidecar，覆盖 3 页、44 个关键文本点，`semantic_order_pair_accuracy = 1.0`。Hacker News 网页打印 PDF 覆盖 2 页、26 个关键文本点，`semantic_order_pair_accuracy = 1.0`。

最新 semantic benchmark 改进为网页打印 PDF 增加 parent-scoped sidecar，并把密集列表行桶从 12pt 收紧到 6pt，避免下一条列表编号插到上一条 metadata 前面。报告还输出 partial labels 忽略文本的 zone/role/source 分布：Attention 当前忽略 147 个未标注节点，Transformer-XL 忽略 277 个，web-HN 忽略 69 个 table-cell 节点，用于决定下一批人工 ground truth。视觉侧的主要瓶颈已经转向字体/浏览器重绘差异和正文行宽度拟合：`--text-fit auto` 会比较普通 HTML 文本和 bbox 内 SVG `textLength` 拟合层。它在论文类样本上选择 `0.99 + svg`，把 Attention 从 `0.93670278` 提升到 `0.96840246`，把 Transformer-XL 从 `0.93358709` 提升到 `0.95658128`；网页打印 PDF 自动保留 `none`，维持 `0.9800288`。

`--html-mode fidelity` 是新的高保真 overlay 路径：HTML 可见层使用每页 SVG 背景，识别出的文本/结构节点仍以透明 `contenteditable` 坐标锚点存在；未编辑时打印只输出背景层，已编辑或已翻译节点会作为局部白底 replacement layer 打印。它适合“未编辑状态接近原 PDF + 保留后续编辑定位能力”的架构验证。当前 fidelity/SVG 未编辑分数：Attention `0.98809524`，Transformer-XL `0.9750043`，web-HN `0.99490923`。

<p align="center">
  <img src="docs/assets/readme-benchmark-score.png" alt="Paper and benchmark score overview" width="100%">
</p>

## Requirements

Required:

- Python `3.10+`
- Google Chrome / Chromium
- Playwright Python package
- PyMuPDF
- Pillow
- Pydantic
- Jinja2
- Typer

Optional:

- PaddleOCR / PaddleOCR-VL for local OCR and document structure experiments

Notes:

- `.env.example` is committed as a template.
- `.env`, `.venv/`, `data/`, and `outputs/` are intentionally ignored.
- Playwright is launched with `--no-proxy-server` by default in this repo because some environments inject proxy credentials into Chrome.

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Optional OCR stack:

```bash
pip install -r requirements-ocr.txt
```

## Quick Start

Generate a deterministic PDF fixture:

```bash
scriptorium make-fixture --out-dir data/fixture
```

Convert it to IR:

```bash
scriptorium convert \
  data/fixture/sample.pdf \
  --ocr-json data/fixture/sample.ocr.json \
  --out-dir outputs/sample
```

External structure evidence from PaddleOCR-VL / PP-StructureV3 style JSON can be fused without making the model runtime a core dependency:

```bash
scriptorium convert \
  path/to/input.pdf \
  --structure-json path/to/paddle-or-ppstructure.json \
  --out-dir outputs/with-structure
```

Export HTML:

```bash
scriptorium export-html \
  outputs/sample/document.ir.json \
  --out-dir outputs/sample/html \
  --display-mode structured
```

Print HTML back to PDF and compare:

```bash
scriptorium print-pdf \
  outputs/sample/html/index.html \
  --pdf outputs/sample/export.pdf

scriptorium compare-pdf \
  data/fixture/sample.pdf \
  outputs/sample/export.pdf \
  --out-dir outputs/sample/pdf-quality
```

## Real Web Page Workflow

Capture a live page with Playwright:

```bash
scriptorium capture-pdf \
  https://news.ycombinator.com/ \
  --pdf outputs/external/web-hn/input.pdf \
  --mode print
```

Convert the captured PDF into annotated structured HTML:

```bash
scriptorium convert \
  outputs/external/web-hn/input.pdf \
  --out-dir outputs/external/web-hn/structured \
  --extract-mode native \
  --dpi 144

scriptorium export-html \
  outputs/external/web-hn/structured/document.ir.json \
  --out-dir outputs/external/web-hn/structured/html \
  --display-mode structured
```

Score the result:

```bash
scriptorium print-pdf \
  outputs/external/web-hn/structured/html/index.html \
  --pdf outputs/external/web-hn/structured/structured-export.pdf

scriptorium compare-pdf \
  outputs/external/web-hn/input.pdf \
  outputs/external/web-hn/structured/structured-export.pdf \
  --out-dir outputs/external/web-hn/structured/pdf-quality \
  --dpi 144
```

## Benchmark

Run the built-in multi-PDF benchmark:

```bash
scriptorium benchmark --out-dir outputs/benchmark-baseline --dpi 192
```

Run benchmark on your own PDFs:

```bash
scriptorium benchmark path/to/file1.pdf path/to/file2.pdf --out-dir outputs/my-benchmark --dpi 144
```

Try the local URW/DejaVu font fallback profile as an A/B experiment:

```bash
scriptorium benchmark path/to/paper.pdf \
  --font-profile local-urw \
  --out-dir outputs/font-profile-local-urw \
  --dpi 144
```

Run a benchmark-time font calibration sweep and keep the better per-PDF result:

```bash
scriptorium benchmark path/to/paper.pdf \
  --font-profile auto \
  --out-dir outputs/font-profile-auto \
  --dpi 144
```

`auto` runs both `browser-default` and `local-urw` candidates, writes both candidate artifacts under the case directory, and selects the higher `visual_similarity` result for the report. In the current real-sample sweep, it selected `local-urw` for Attention (`0.93202666 -> 0.93871982`) and kept `browser-default` for Transformer-XL (`0.93358709`) and Hacker News (`0.9800288`).

Run a lightweight font-size calibration sweep:

```bash
scriptorium benchmark path/to/paper.pdf \
  --font-size-scale auto \
  --out-dir outputs/font-size-scale-auto \
  --dpi 144
```

`--font-size-scale auto` evaluates `0.99` and `1.0`, records candidate artifacts, and selects the higher score. It improved the Attention sample with `browser-default` from `0.93202666` to `0.93670278`, while keeping Transformer-XL and Hacker News at `1.0`.

Run a structured text-fit sweep:

```bash
scriptorium benchmark path/to/paper.pdf \
  --text-fit auto \
  --out-dir outputs/text-fit-auto \
  --dpi 144
```

`--text-fit auto` evaluates the normal editable HTML text layer and an SVG fitted text layer. The SVG candidate uses each PDF text bbox/run bbox plus `textLength` / `lengthAdjust="spacingAndGlyphs"` to match line width while keeping a transparent editable proxy in the DOM. Combined with `--font-size-scale auto`, it raised the current structured paper scores to Attention `0.96840246` and Transformer-XL `0.95658128`; Hacker News selected `none` because its normal HTML text remains closer.

You can combine both calibration axes:

```bash
scriptorium benchmark path/to/paper.pdf \
  --font-profile auto \
  --font-size-scale auto \
  --text-fit auto \
  --out-dir outputs/visual-calibration-auto \
  --dpi 144
```

Run the high-fidelity SVG overlay benchmark:

```bash
scriptorium benchmark path/to/paper.pdf \
  --html-mode fidelity \
  --out-dir outputs/fidelity-overlay \
  --dpi 144
```

This mode keeps editable coordinate nodes in the HTML but hides them during print, so the score measures source visual preservation. Use it to track the future edit-mask/replacement architecture separately from the fully structured redraw path.

Run the same benchmark with external PaddleOCR-VL / PP-StructureV3 style evidence:

```bash
scriptorium benchmark \
  path/to/file1.pdf path/to/file2.pdf \
  --structure-json path/to/file1.structure.json \
  --structure-json path/to/file2.structure.json \
  --out-dir outputs/native-plus-structure \
  --dpi 144
```

For a single PDF, pass one `--structure-json`. For multiple PDFs, pass JSON files in PDF order or name them like `<pdf-stem>.structure.json` / `<parent-dir>.<pdf-stem>.structure.json` so the benchmark can match them.

Outputs:

- `benchmark_report.json`: full metrics, per-stage timings, artifact paths
- `benchmark_summary.csv`: compact table for tracking optimization progress
- per-case `document.ir.json`, `html/index.html`, `structured-export.pdf`, and visual diff images

Tracked metrics:

- `visual_similarity`
- `max_diff_ratio`
- `mean_diff_ratio`
- `p95_diff_ratio`
- `worst_page`
- page count match
- page dimension match
- image count
- multi-column element count
- column-flow element count
- recursive XY-Cut element count
- reading-order strategy counts
- reading-order risk score, risk level, column-geometry page count, and unlabeled-text risk count
- font profile
- font profile candidate scores when `--font-profile auto` is used
- font size scale and candidate scores when `--font-size-scale auto` is used
- text fit strategy and candidate scores when `--text-fit auto` is used
- HTML mode and vector background page count
- raster policy
- text run count
- mixed inline style element count
- layout region counts
- raster fallback count and rasterized text/image/shape counts
- structure evidence source, region count, matched element count, and reordered page count
- semantic ground-truth case count
- semantic order pair accuracy
- semantic sequence similarity
- semantic ignored text count for partial labels
- semantic ignored text zone/role/source counts for partial labels
- semantic missing/extra text count
- `total_seconds`
- stage timings: render, extraction/annotation, HTML export, PDF print, visual comparison, semantic comparison
- element count
- editable element count
- shape count
- style count
- annotation count

## Architecture

```mermaid
flowchart LR
  A[PDF or Web Page] --> B[PyMuPDF Render]
  A --> C[Native PDF Extractor]
  A --> D[OCR JSON / Paddle Adapter]
  D --> K[Structure Evidence Fusion]
  C --> E[DocumentIR]
  D --> E
  K --> E
  E --> F[Annotation Pass]
  F --> G[Structured HTML]
  G --> H[XML Node Edit]
  H --> E
  G --> I[Playwright Print PDF]
  I --> J[Visual Regression Score]
```

Core files:

- `src/scriptorium/models.py`: `DocumentIR`, page and element models
- `src/scriptorium/native_pdf.py`: native text and drawing extraction
- `src/scriptorium/annotations.py`: role/style/source/bbox annotation pass
- `src/scriptorium/reading_order.py`: visual order, column-flow fallback, and recursive XY-Cut semantic order
- `src/scriptorium/structure_evidence.py`: PaddleOCR-VL/PP-Structure style external region/order evidence fusion
- `src/scriptorium/html_export.py`: standalone HTML export
- `src/scriptorium/xml_edit.py`: XML node edit round trip
- `src/scriptorium/benchmark.py`: reproducible quality benchmark
- `docs/optimization-roadmap.md`: reading-order and complex-page optimization plan

## Data Model

`DocumentIR` is the source of truth. It keeps:

- page size in PDF points and rendered pixels
- element bbox in PDF points and pixels
- `source_text`, `edited_text`, `translated_text`
- `text_runs` for native PDF inline spans: text, bbox, font, weight, style, color, script, and run style id
- `font_profile`: the CSS font fallback profile used during native PDF extraction
- native drawing SVG evidence: simple line points and non-rectangular path data
- native image/raster crops via `source_crop`
- `semantic_order`, `visual_order`, `column_index`, `column_count`, `flow_segment_index`, and `reading_order_region_path`
- optional external structure evidence such as `external_structure_label`, `external_structure_order`, and fused region metadata
- source kind: `native-pdf`, `native-image`, `native-raster-region`, `native-drawing`, OCR fallback, etc.
- role: `heading`, `paragraph`, `table-cell-text`, `table-shape`, `figure-shape`, `separator-shape`, etc.
- style bucket: `style-001`, `style-002`, ...
- layout group: for example `table-001`, `figure-001`, `separator-001`
- layout region metadata: region kind, bbox, confidence, and contributing shape ids
- revision history for edits and translation

The original `source_text` is never overwritten. Inline runs are used when rendering source text; once an element has `edited_text` or `translated_text`, Scriptorium renders the replacement as plain editable text instead of forcing old source runs onto new content.

## OCR And Structure Strategy

The default tested path uses native PDF extraction or JSON fallback. Heavy model runtimes remain optional, but their structured output can now assist the core pipeline:

- conversion, annotation, HTML export, XML edit, and benchmark do not depend on the model runtime
- `--font-profile browser-default` is the stable default; `--font-profile local-urw` can be benchmarked when local Nimbus/DejaVu fonts are available
- `scriptorium benchmark --font-profile auto` performs a reproducible two-profile sweep and records the selected profile plus both candidate scores
- `scriptorium benchmark --font-size-scale auto` performs a small font-size sweep and records the selected scale plus candidate scores
- `scriptorium benchmark --text-fit auto` compares normal editable HTML text against an SVG fitted text layer plus editable proxy, then records the selected candidate
- `scriptorium benchmark --html-mode fidelity` benchmarks the SVG-background editable overlay path for source-preservation quality and supports edited/translated replacement overlays
- `--raster-policy dense` is the stable native fallback; `--raster-policy tables` is available as an explicit experiment, but current real-paper/web A/B results did not justify making table-region rasterization the default
- `--structure-json` accepts PaddleOCR-VL / PP-StructureV3 style JSON with region bbox, label, content, and block order
- `structure_evidence.py` aligns those regions back to native elements by bbox coverage/text similarity
- matched elements can receive external role/order metadata and `external-structure-fusion-v1` reading-order strategy
- `scriptorium benchmark --structure-json ...` reports whether those regions matched elements or changed page order, enabling native-only versus native-plus-structure A/B runs
- `requirements-ocr.txt` keeps heavyweight OCR dependencies optional

## Development

Run tests:

```bash
pytest
```

Current local test baseline:

```text
42 passed
```

## Project Status

This is a core-first prototype. It already has real PDF and real webpage benchmarks, stricter visual metrics, v2 layout grouping, native PDF span-level inline style preservation, PDF line-width alignment for structured text, SVG text-fit calibration with editable proxies, gated script-run positioning, native drawing SVG path output, fidelity SVG overlay with edited/translated replacement printing, native image extraction, local raster fallback for dense vector regions, benchmark-time font profile/font-size/text-fit calibration, recursive XY-Cut semantic order for sectioned multi-column pages, reading-order risk diagnostics, Paddle/PP-Structure style external evidence fusion, real-paper partial semantic ground truth, and strategy coverage metrics. The next useful work is running real model outputs through the fusion path, broader real-document semantic ground truth, richer OCR adapter mapping, and more precise edit-aware masks/reflow while keeping benchmark scores comparable.
