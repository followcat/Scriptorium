<p align="center">
  <img src="docs/assets/readme-hero.png" alt="Scriptorium PDF - structured PDF to editable annotated HTML" width="100%">
</p>

<h1 align="center">Scriptorium PDF</h1>

<p align="center">
  <strong>Convert PDFs, web-print PDFs, and OCR structure output into editable, annotated, benchmarkable HTML.</strong>
</p>

<p align="center">
  <a href="README.md">简体中文</a>
  |
  <a href="README.en.md"><strong>English</strong></a>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2b6cb0">
  <img alt="Status" src="https://img.shields.io/badge/status-core%20prototype-2f855a">
  <img alt="Structured HTML" src="https://img.shields.io/badge/output-annotated%20HTML-6b46c1">
  <img alt="Benchmark" src="https://img.shields.io/badge/benchmark-visual%20%2B%20semantic-805ad5">
  <img alt="OCR" src="https://img.shields.io/badge/OCR-optional%20Paddle%2FDocling-0f766e">
  <img alt="Tests" src="https://img.shields.io/badge/tests-80%20passing-2f855a">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a>
  ·
  <a href="#real-world-scores">Scores</a>
  ·
  <a href="#architecture">Architecture</a>
  ·
  <a href="#benchmark">Benchmark</a>
  ·
  <a href="docs/optimization-roadmap.md">Roadmap</a>
</p>

<p align="center">
  <strong>Scriptorium PDF is built for PDF editing, translation, layout reconstruction, OCR validation, and HTML-to-PDF quality research.</strong><br>
  It keeps source evidence intact, maps text/coordinates/styles/layout roles/reading order into one IR, and uses reproducible benchmarks to track whether each optimization actually improves output quality.
</p>

<table>
  <tr>
    <td width="50%">
      <strong>Chinese documentation</strong><br>
      <a href="README.md">中文 README</a> ·
      <a href="docs/implementation-notes.zh-CN.md">实现说明</a> ·
      <a href="docs/optimization-roadmap.zh-CN.md">优化路线</a> ·
      <a href="docs/external-benchmarks.zh-CN.md">外部基准</a>
    </td>
    <td width="50%">
      <strong>English documentation</strong><br>
      <a href="README.en.md">English README</a> ·
      <a href="docs/implementation-notes.md">Implementation notes</a> ·
      <a href="docs/optimization-roadmap.md">Optimization roadmap</a> ·
      <a href="docs/external-benchmarks.md">External benchmarks</a>
    </td>
  </tr>
</table>

| Capability | Current Status |
|---|---|
| Structured HTML | Text, images, shapes, layout groups, roles, bboxes, style ids, and source markers are exported to DOM metadata. |
| Editing and translation | `source_text` is preserved; edits go to `edited_text`, translations go to `translated_text`, with XML/IR round trips. |
| OCR and structure evidence | Supports image-only OCR fallback and PaddleOCR-VL / PP-Structure / Docling JSON fusion. |
| Visual fidelity | Supports structured redraw, SVG/raster fidelity overlay, and benchmark-time font/scale/text-fit selection. |
| Semantic reading order | Supports XY-Cut, multi-column flow, table islands, headers/footers, footnotes, sidebars, captions, relation graph, successor-consensus diagnostics, and conservative runtime arbitration. |
| Quality metrics | Reports visual similarity, page diff distribution, semantic order, successor accuracy, candidate arbitration, and risk diagnostics. |

## What It Does

Scriptorium PDF is a core conversion engine. It does not treat PDF-to-HTML as a single screenshot problem; instead, it rebuilds recognized PDF structure into editable nodes:

- Extract native PDF text, fonts, colors, weights, coordinates, image blocks, and drawing/shape evidence.
- Add transparent `native-ocr` edit anchors for image-only scanned or screenshot PDFs.
- Normalize OCR / PaddleOCR-VL / PP-Structure / Docling output into the same `DocumentIR`.
- Export structured HTML with editable text nodes and source/coordinate/style/role metadata.
- Support XML-level local node edits, then write them back into IR and exported HTML/PDF.
- Print HTML back to PDF with Playwright and compare rendered pages for measurable visual quality.
- Track optimization progress with repeatable benchmark reports.

## Why It Is Different

Many PDF-to-HTML tools use a full-page image plus a hidden text layer. That can look close, but local editing and semantic structure are weak.

Scriptorium's structured mode keeps page content addressable:

```html
<div
  data-scriptorium-role="table-cell-text"
  data-scriptorium-source="native-pdf"
  data-scriptorium-style-id="style-004"
  data-scriptorium-semantic-order="12"
  data-scriptorium-reading-order-strategy="recursive-xy-cut-v1"
  data-scriptorium-reading-order-confidence="0.83"
  data-scriptorium-edit-target="edited_text"
  data-bbox-pdf="76.99,212.49,117.83,224.22"
  contenteditable="true"
>
  PDF text
</div>
```

Each node can be traced back to source evidence, coordinates, style buckets, layout grouping, reading-order evidence, and edit targets. Complex vector regions can still fall back to local raster crops, but those are local elements with bbox/source metadata rather than a full-page background.

## Reading Order

Scriptorium treats reading order as evidence, not a single y/x sort. The runtime path includes recursive XY-Cut, repeated-anchor column flow, spatial graph fallback, guarded box-flow fallback, table islands, footnotes, sidebars, captions, and optional external PaddleOCR-VL / PP-Structure / Docling order evidence.

`successor-consensus-arbitration-v1` is intentionally narrow. It only takes over when a page would otherwise fall back to weak `single-column-visual-order`, non-visual candidates such as box-flow and relation graph strongly agree, the consensus disagrees with visual-yx on adjacent successor edges, and the consensus order contains clear column handoffs. It now preserves `column_count` / `column_index` metadata across sparse multi-column pages. Benchmark reports expose `successor_consensus_arbitration_element_count` so external PDFs show when this path is actually active.

## Real-World Scores

<p align="center">
  <img src="docs/assets/readme-webpage-score.png" alt="Live webpage conversion score" width="100%">
</p>

| Sample | Pages | Elements | Editable | Visual Similarity | Page/Size Match |
|---|---:|---:|---:|---:|---|
| Hacker News live page printed by Playwright | 2 | 162 | 95 | 0.9800288 | yes / yes |
| arXiv paper: Attention Is All You Need | 15 | 876 | 761 | 0.96840246 | yes / yes |
| ACL paper: Transformer-XL | 11 | 1558 | 1446 | 0.95679576 | yes / yes |
| Built-in benchmark fixtures, mean | 6 pages total | 72 | 53 | 0.9906702 | yes / yes |

`visual_similarity = 1 - max_diff_ratio`. Reports also include `mean_diff_ratio`, `p95_diff_ratio`, `worst_page`, `page_count_match`, and `dimension_match`.

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

Image-only OCR fallback uses PyMuPDF's Tesseract bridge, so the system `tesseract` command and language data are also required when OCR fallback is enabled.

## Quick Start

Generate a deterministic fixture and convert it:

```bash
scriptorium make-fixture --out-dir data/fixture

scriptorium convert \
  data/fixture/sample.pdf \
  --ocr-json data/fixture/sample.ocr.json \
  --out-dir outputs/sample

scriptorium export-html \
  outputs/sample/document.ir.json \
  --out-dir outputs/sample/html \
  --display-mode structured
```

Print the HTML back to PDF and compare:

```bash
scriptorium print-pdf \
  outputs/sample/html/index.html \
  --pdf outputs/sample/export.pdf

scriptorium compare-pdf \
  data/fixture/sample.pdf \
  outputs/sample/export.pdf \
  --out-dir outputs/sample/pdf-quality
```

## Benchmark

Run the built-in benchmark:

```bash
scriptorium benchmark --out-dir outputs/benchmark-baseline --dpi 192
```

Run benchmark on external PDFs:

```bash
scriptorium benchmark path/to/file1.pdf path/to/file2.pdf --out-dir outputs/my-benchmark --dpi 144
```

For complex documents, use the automatic visual path selection:

```bash
scriptorium benchmark path/to/paper.pdf \
  --html-mode auto \
  --fidelity-background auto \
  --font-size-scale auto \
  --text-fit auto \
  --out-dir outputs/html-mode-auto \
  --dpi 144
```

External PaddleOCR-VL / PP-Structure / Docling structure evidence can be fused with:

```bash
scriptorium benchmark \
  path/to/input.pdf \
  --structure-json path/to/input.structure.json \
  --out-dir outputs/native-plus-structure \
  --dpi 144
```

## Architecture

```mermaid
flowchart LR
  A[PDF or Web Page] --> B[PyMuPDF Render]
  A --> C[Native PDF Extractor]
  C --> L[Image-only OCR Fallback]
  A --> D[OCR JSON / Paddle Adapter]
  D --> K[Structure Evidence Fusion]
  C --> E[DocumentIR]
  L --> E
  D --> E
  K --> E
  E --> F[Annotation Pass]
  F --> G[Structured HTML]
  G --> H[XML Node Edit]
  H --> E
  G --> I[Playwright Print PDF]
  I --> J[Visual Regression Score]
```

Core modules:

- `src/scriptorium/models.py`: `DocumentIR`, page, and element models
- `src/scriptorium/native_pdf.py`: native PDF text/drawing/image extraction
- `src/scriptorium/annotations.py`: role/style/source/bbox annotation pass
- `src/scriptorium/reading_order.py`: visual order, XY-Cut, column flow, graph candidates, table/footnote/sidebar/caption ordering
- `src/scriptorium/structure_evidence.py`: PaddleOCR-VL / PP-Structure / Docling evidence fusion
- `src/scriptorium/html_export.py`: standalone HTML export
- `src/scriptorium/xml_edit.py`: XML node edit round trip
- `src/scriptorium/benchmark.py`: reproducible quality benchmark

## Documentation

- [简体中文 README](README.md)
- [中文实现说明](docs/implementation-notes.zh-CN.md)
- [中文优化路线](docs/optimization-roadmap.zh-CN.md)
- [中文外部基准](docs/external-benchmarks.zh-CN.md)
- [Implementation notes](docs/implementation-notes.md)
- [Optimization roadmap](docs/optimization-roadmap.md)
- [External benchmark samples](docs/external-benchmarks.md)

## Development

```bash
pytest
```

Current local test baseline:

```text
80 passed
```
