<p align="center">
  <img src="docs/assets/readme-hero.png" alt="Scriptorium" width="100%">
</p>

<h1 align="center">Scriptorium</h1>

<p align="center">
  <strong>Convert document sources into editable, annotated, benchmarkable HTML with enough structure evidence for translation and re-rendering.</strong>
</p>

<p align="center">
  <a href="README.md"><img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-Read-blue"></a>
  <a href="README.en.md"><img alt="English" src="https://img.shields.io/badge/English-current-2f855a"></a>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2b6cb0">
  <img alt="Status" src="https://img.shields.io/badge/status-research%20prototype-2f855a">
  <img alt="Output" src="https://img.shields.io/badge/output-annotated%20HTML-6b46c1">
  <img alt="Benchmark" src="https://img.shields.io/badge/benchmark-visual%20%2B%20semantic-805ad5">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a>
  ·
  <a href="#core-workflow">Workflow</a>
  ·
  <a href="#benchmark">Benchmark</a>
  ·
  <a href="https://followcat.github.io/Scriptorium/">Live Gallery</a>
  ·
  <a href="#documentation">Docs</a>
</p>

Scriptorium is a source-neutral document-to-HTML conversion and evaluation engine. The current main path covers PNG/JPEG/TIFF/WebP images, screenshots, PDFs, web-printed PDFs, and image-only PDFs; image sources enter the IR as first-class sources instead of pretending to be PDFs first.

It merges source text, images, vector drawings, OCR output, and external structure JSON into a single `DocumentIR`, then exports coordinate-aware HTML. Each editable node keeps its source, bbox, style, role, reading stream, and edit/translation fields, so downstream tools can write `edited_text` or `translated_text` and print the result back to PDF.

## When to Use It

| Use case | What Scriptorium provides |
|---|---|
| Document editing experiments | Local text nodes that can be addressed, replaced, and written back through HTML/IR. |
| Document translation re-rendering | Source-preserving visual layers, `translated_text` replacements, browser-measured fitting, and mask/overflow/conflict diagnostics. |
| Papers, annual reports, and portal pages | Multi-column body flow, table islands, card grids, footnotes, sidebars, page artifacts, and local reading streams. |
| OCR/layout-model validation | PaddleOCR-VL, PP-Structure, Docling, and ROOR-style JSON fusion where OCR/structure JSON can drive image-source semantics, plus native-only vs native-plus-structure A/B benchmarks. |
| Conversion quality regression | Print HTML back to PDF and measure visual similarity, page/size match, semantic order, and risk metrics. |

## Why Not Just Screenshots

Many PDF-to-HTML / OCR-to-HTML tools render a whole page image and overlay a hidden text layer. That can look close, but it leaves little structure for local editing, translation, or reading-order analysis.

Scriptorium supports two output paths:

- `structured`: rebuild text, images, and shapes with HTML/SVG where possible, making structure and editability easy to inspect.
- `fidelity`: preserve an SVG/raster source visual layer while keeping recognized text and structure nodes as transparent coordinate anchors; edited or translated nodes print as browser-fitted local replacement overlays.

HTML nodes carry `data-scriptorium-*` metadata such as role, source, bbox, style id, reading order, reading stream, translation target, and replacement risk. See [Implementation notes](docs/implementation-notes.md) for the full model.

Standalone HTML also exposes `window.ScriptoriumEdits`: browser changes become validated `scriptorium-html-edits/v1` patches that `scriptorium apply-html-edits` can write back to the same `DocumentIR` before another export or print.

<table>
  <tr>
    <td width="50%">
      <img src="docs/assets/readme-webpage-score.png" alt="Web page PDF conversion preview" width="100%"><br>
      <strong>Web and portal sources</strong><br>
      Print PDFs or capture screenshots with Playwright, then convert them into HTML with native/OCR coordinate anchors.
    </td>
    <td width="50%">
      <img src="docs/assets/readme-benchmark-score.png" alt="Benchmark score preview" width="100%"><br>
      <strong>Papers, reports, and manuals</strong><br>
      Track visual fidelity, semantic order, candidate disagreement, and translation replacement risk across source types.
    </td>
  </tr>
</table>

<p align="center">
  <a href="https://followcat.github.io/Scriptorium/"><strong>Open the live source-to-HTML gallery</strong></a>
</p>

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Images, screenshots, and scanned pages can be converted directly. When OCR/structure JSON is available, it seeds text anchors first, then structure evidence adds roles, reading order, and reading streams:

```bash
scriptorium convert \
  path/to/page.png \
  --input-kind image \
  --structure-json path/to/page.structure.json \
  --out-dir outputs/image-source

scriptorium export-html \
  outputs/image-source/document.ir.json \
  --out-dir outputs/image-source/html \
  --display-mode fidelity
```

Saved PaddleOCR-VL JSON retains the model input canvas. Scriptorium maps its
pixel boxes through that saved `width`/`height`, so one model run can be
replayed safely at a different conversion or benchmark DPI.

When a model supplies explicit `block_order` for a body/paragraph block and
all matched native lines stay in one selected flow segment and column,
Scriptorium promotes them to an `external-block-body-*` local translation
stream. This does not reorder the page or bridge tables, grids, captions,
page artifacts, footnotes, or sidebars; it is a paragraph-level batching
boundary for translation and editing, not a whole-page reading-order claim.

The built-in PDF fixture is still useful for a fully runnable smoke test:

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

External OCR/layout models are optional. Without OCR or structure JSON, an image source still keeps the full-page visual layer; with OCR or Paddle/PP-Structure/Docling/ROOR-style structure JSON it gains transparent text anchors and reading-stream evidence.

Optional OCR dependencies live in `requirements-ocr.txt`. Image-only OCR fallback also requires the system `tesseract` binary and language data; local PP-StructureV3 runs need the version-specific Paddle CPU compatibility settings in the [implementation notes](docs/implementation-notes.md#external-structure-evidence-fusion).

## Core Workflow

```mermaid
flowchart LR
  S[Document / image source] --> A{Source kind}
  A -->|Digital PDF| B[Native PDF Extractor]
  A -->|Image / screenshot| C[Image Renderer]
  A --> C2[Render Pages]
  C --> E[OCR / Structure JSON Adapter]
  C2 --> E
  B --> D[Image-only OCR Fallback]
  B --> E[Structure JSON Adapter]
  E --> F[Structure Evidence Fusion]
  B --> G[DocumentIR]
  C --> G
  C2 --> G
  D --> G
  F --> G
  G --> H[Annotation + Reading Streams]
  H --> I[Structured / Fidelity HTML]
  I --> J[Edit or Translate]
  J --> I
  I --> K[Print PDF]
  K --> L[Visual + Semantic Benchmark]
```

Main modules:

| Module | Role |
|---|---|
| `native_pdf.py` | Extract native text, images, drawings, and page geometry. |
| `structure_evidence.py` | Normalize PaddleOCR-VL / PP-Structure / Docling / ROOR-style structure evidence. |
| `ocr.py` | Normalize OCR/structure JSON into image/source text anchors and record the semantic-layer source; for image sources, structure JSON can be the semantic driver. |
| `reading_order.py` | Build multi-column flow, table islands, card grids, footnotes, sidebars, captions, and reading streams. |
| `html_export.py` | Export structured/fidelity HTML with edit and translation anchors. |
| `benchmark.py` | Run visual, semantic-order, structure A/B, and translation re-rendering benchmarks. |

## Benchmark

Run the built-in benchmark:

```bash
scriptorium benchmark --out-dir outputs/benchmark-baseline --dpi 192
```

Run external documents with automatic fidelity path selection:

```bash
scriptorium benchmark path/to/file.pdf \
  --out-dir outputs/my-benchmark \
  --dpi 144 \
  --html-mode auto \
  --fidelity-background auto
```

Compare native-only against native-plus-structure:

```bash
scriptorium benchmark-structure-ab \
  path/to/source.pdf \
  --structure-json path/to/source.structure.json \
  --out-dir outputs/structure-ab \
  --dpi 144
```

Stress translated re-rendering:

```bash
scriptorium benchmark path/to/file.pdf \
  --html-mode fidelity \
  --fidelity-background auto \
  --translation-stress pseudo-expand \
  --out-dir outputs/translation-stress \
  --dpi 144
```

Image sources can be benchmarked directly. Visual scoring compares the source image visual layer against the rendered HTML-to-PDF output; structure JSON first seeds OCR/text anchors, then participates in reading-stream and structure-evidence fusion. Reports also expose `semantic_layer_driver`:

```bash
scriptorium benchmark path/to/page.png \
  --input-kind image \
  --image-dpi 96 \
  --structure-json path/to/page.structure.json \
  --html-mode structured \
  --out-dir outputs/image-benchmark
```

Representative current scores are shown below. Exact commands, sources, checksums, and full metrics are in [External benchmarks](docs/external-benchmarks.md).

| Sample | Pages | Main stress | Visual similarity | Notes |
|---|---:|---|---:|---|
| Hacker News print PDF | 2 | Real portal/list page | 0.9800288 | Has a semantic sidecar. |
| Attention Is All You Need | 15 | Paper columns, formulas, figures | 0.96840246 | Used for paper reading-order regression. |
| Transformer-XL | 11 | Two-column paper and page-size variance | 0.95679576 | Used for multi-column successor-edge checks. |
| BYD 2024 annual report | 40 | Chinese annual report, tables, dense vector rules | 0.89780001 | Current complex Chinese PDF stress sample. |
| JD homepage screenshot PDF | 1 | Image-only ecommerce homepage | 0.99576887 | OCR adds transparent editable anchors. |
| JD homepage screenshot PNG | 1 | First-class image source path | 0.99236799 | Matches the image-only PDF compatibility path's OCR/structure anchor inventory. |

`visual_similarity = 1 - max_diff_ratio`. Reports also include page/size match, diff distribution, reading-order risk, candidate disagreement, grid/table/stream statistics, and replacement risk.

## Editing and Translation

`source_text` always preserves the original recognized text. Local edits go to `edited_text`; translations go to `translated_text`. In fidelity mode, unchanged nodes stay hidden during print, while edited or translated nodes become local source-aware mask replacement layers.

Translation re-rendering currently focuses on three hard problems:

- Fitting longer translations back into the source bbox. Chromium measures the real glyph layout after fonts load, searches a bounded scale, and can compact line height before reporting actual clipping; the static estimate remains separately available for fallback and triage.
- Masking source text without damaging neighboring elements. Each mask side stops at adjacent visible boxes; light source text on a dark raster edge can use a sampled dark mask instead of the default white. Print conversion also maps source render pixels to 96-DPI CSS coordinates, so replacement boxes, padding, and font sizes retain their position in the exported PDF.
- Translating multi-column body flows, table islands, card grids, and sidebars as separate reading streams.

This is not a full end-user document editor yet. It is a measurable conversion core that exposes the right risks for a future UI, review workflow, or stronger model-based structure evidence.

## Current Boundaries

- Complex-page visual fidelity can be high with fidelity backgrounds; semantic order, local flow structure, and translated replacement conflicts are the harder parts.
- Without structure priors, portal pages, product grids, report tables, and OCR-heavy pages can have genuinely ambiguous reading order.
- PaddleOCR-VL / PP-Structure / Docling JSON can already be fused as evidence, but model runtimes remain optional.
- The project is a research prototype for conversion, evaluation, and architecture work, not a desktop document editor for end users.

## Documentation

- [简体中文 README](README.zh-CN.md)
- [默认中文 README](README.md)
- [Implementation notes](docs/implementation-notes.md)
- [Optimization roadmap](docs/optimization-roadmap.md)
- [External benchmarks](docs/external-benchmarks.md)
- [中文实现说明](docs/implementation-notes.zh-CN.md)
- [中文优化路线](docs/optimization-roadmap.zh-CN.md)
- [中文外部基准](docs/external-benchmarks.zh-CN.md)

## Development

```bash
pytest
```
