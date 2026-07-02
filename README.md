# Scriptorium PDF

Scriptorium is a core-first prototype for turning scanned PDFs into visually faithful HTML, while preserving enough structure for later editing, translation, and PDF export.

The first implementation focuses on conversion quality rather than a full application UI:

- render PDF pages into stable background images
- normalize OCR/structure output into a single `DocumentIR`
- export standalone HTML with recognized roles, styles, source markers, and page-accurate coordinates
- keep `source_text`, `edited_text`, and `translated_text` separate
- compare HTML screenshots against the original PDF render

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Optional local PaddleOCR experiments:

```bash
pip install -r requirements-ocr.txt
```

Copy `.env.example` to `.env` for local overrides. Do not commit `.env` or `.venv/`.

## CLI Quick Start

Generate a deterministic sample PDF and fallback OCR JSON:

```bash
scriptorium make-fixture --out-dir data/fixture
```

Convert the sample PDF into IR and HTML:

```bash
scriptorium convert data/fixture/sample.pdf --ocr-json data/fixture/sample.ocr.json --out-dir outputs/sample
scriptorium export-html outputs/sample/document.ir.json --out-dir outputs/sample/html --display-mode background
```

Compare the exported HTML against the rendered PDF background:

```bash
scriptorium quality-check outputs/sample/document.ir.json outputs/sample/html/index.html --out-dir outputs/sample/quality
```

Print the HTML back to PDF and compare it with the original PDF rendering:

```bash
scriptorium print-pdf outputs/sample/html/index.html --pdf outputs/sample/export.pdf
scriptorium compare-pdf data/fixture/sample.pdf outputs/sample/export.pdf --out-dir outputs/sample/pdf-quality
```

Capture a page with Playwright, extract native PDF structure, and export annotated editable HTML:

```bash
scriptorium make-web-fixture --out-dir data/playwright-fixture
scriptorium capture-pdf data/playwright-fixture/structured-page.html --pdf outputs/playwright/input.pdf --mode print
scriptorium convert outputs/playwright/input.pdf --out-dir outputs/playwright/annotated --extract-mode native
scriptorium export-html outputs/playwright/annotated/document.ir.json --out-dir outputs/playwright/annotated/html --display-mode structured
```

`structured` HTML does not include the page image. It emits editable text nodes and structural shape nodes with `data-scriptorium-role`, `data-scriptorium-source`, `data-scriptorium-style-id`, `data-scriptorium-layout-group`, and bbox attributes.

Run the multi-PDF benchmark baseline:

```bash
scriptorium benchmark --out-dir outputs/benchmark-baseline --dpi 192
```

The benchmark creates several PDF fixtures when no input PDFs are provided, runs the structured conversion pipeline, prints the output HTML back to PDF, and compares the rendered PDFs. It writes:

- `benchmark_report.json`: full per-case metrics and timings
- `benchmark_summary.csv`: compact table for tracking optimization progress

Primary metric: `visual_similarity = 1 - max_diff_ratio`. Higher is better.

Run tests:

```bash
pytest
```

## Data Model

`DocumentIR` is the stable source of truth. It stores:

- PDF point geometry and rendered pixel geometry
- page background images
- OCR/structure elements with `bbox_pdf` and `bbox_px`
- original OCR text in `source_text`
- user edits in `edited_text`
- translation output in `translated_text`
- style hints, confidence, crop paths, and revision records

The original OCR text is never overwritten by editing or translation.

## Current OCR Strategy

The default implementation supports JSON fallback so the rendering, geometry, HTML export, and quality comparison can be developed before a local PaddleOCR-VL environment is ready.

The Paddle adapter is intentionally isolated. Once the model environment is installed, it can populate the same `DocumentIR` without changing export or quality logic.
