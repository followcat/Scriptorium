# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Scriptorium is a research prototype that converts document sources (digital PDF, image-only PDF, PNG/JPEG/TIFF/WebP, screenshots, web-print PDF) into annotated, editable, benchmarkable HTML. The package name and CLI entry point are both `scriptorium` (`src/scriptorium/`, `scriptorium = scriptorium.cli:app`).

Primary goals:

- **Visual fidelity**: HTML printed back to PDF should look like the source.
- **Semantic fidelity**: editable/exported text follows human reading order and keeps local structure (columns, tables, grids, footnotes, sidebars, captions).

This is not a desktop document editor. Optional OCR/layout model runtimes stay out of the core path; normalized JSON is the stable bridge.

## Setup and common commands

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt   # also pulls pytest
pip install -e .

# Optional Chromium for HTML→PDF print/compare (system chrome/chromium or Playwright)
# Playwright is a core dep; local Chrome/Chromium is preferred by browser_launch.py

# Optional heavy providers (install only when needed)
pip install -r requirements-ocr.txt              # PaddleOCR-VL / PP-StructureV3
pip install -r requirements-docling.txt
pip install -r requirements-opendataloader.txt   # needs Java 11+
pip install -r requirements-relation-ranker.txt
pip install -r requirements-semantic-order.txt   # torch + transformers for optional NSP feature
# Surya uses a separate venv + requirements-surya.txt and --accept-model-license
```

Core workflow:

```bash
# Built-in fixture end-to-end
scriptorium make-fixture --out-dir data/fixture
scriptorium convert data/fixture/sample.pdf --ocr-json data/fixture/sample.ocr.json --out-dir outputs/sample
scriptorium export-html outputs/sample/document.ir.json --out-dir outputs/sample/html --display-mode structured
scriptorium print-pdf outputs/sample/html/index.html --pdf outputs/sample/export.pdf
scriptorium compare-pdf data/fixture/sample.pdf outputs/sample/export.pdf --out-dir outputs/sample/pdf-quality

# Image source (first-class; not wrapped as a fake PDF)
scriptorium convert page.png --input-kind image --image-dpi 96 \
  --structure-json page.structure.json --out-dir outputs/page-image

# Built-in benchmark
scriptorium benchmark --out-dir outputs/benchmark-baseline --dpi 192

# Native-only vs structure-evidence A/B
scriptorium benchmark-structure-ab path/to/source.pdf \
  --structure-json path/to/source.structure.json \
  --out-dir outputs/structure-ab --dpi 144
```

Review-only graph path for multi-column experiments:

```bash
scriptorium export-hierarchy-input outputs/doc/document.ir.json --page-index 0 -o hierarchy.json
scriptorium predict-paragraph-graph hierarchy.json --model models/paragraph.joblib -o paragraph.proposal.json
scriptorium predict-successor-graph hierarchy.json --model models/successor.joblib -o successor.proposal.json
scriptorium benchmark-joint-graph TRAIN --paragraph-proposals-dir PARA --successor-proposals-dir SUCC -o joint.json
scriptorium propose-joint-graph document.ir.json --page-index 0 \
  --paragraph-model models/paragraph.joblib --successor-model models/successor.joblib -o joint.proposal.json
```

Graph proposals stay `runtime_reorder: false` until external-benchmark gates promote them.

Tests (no separate lint/format toolchain is configured in `pyproject.toml`; a local ruff cache may exist but is not project policy):

```bash
pytest
pytest tests/test_pipeline.py
pytest tests/test_pipeline.py::test_fixture_pipeline_exports_html
pytest -k reading_order
```

`data/` and `outputs/` are gitignored working directories. Fixture generators and CLI defaults write there; do not commit large generated artifacts.

Env knobs (see `.env.example`): `SCRIPTORIUM_RENDER_DPI`, `SCRIPTORIUM_DATA_DIR`, `SCRIPTORIUM_OCR_BACKEND`, `SCRIPTORIUM_PADDLE_MODEL_DIR`, `SCRIPTORIUM_EXPORT_SCALE`, translation mock/provider settings.

Docs (bilingual; prefer the English files unless the user is working in Chinese):

- `README.en.md` / `README.md` / `README.zh-CN.md`
- `docs/implementation-notes.md` — IR fields, fusion rules, reading-order strategies, fidelity details
- `docs/optimization-roadmap.md` — what is implemented vs next research options
- `docs/external-benchmarks.md` — frozen external numbers, checksums, gate decisions

## Architecture

### Pipeline shape

```
source (pdf|image)
  → render_source()                    # pdf_render.py / image path
  → extract_native_pdf_to_ir()         # digital PDF
    or normalize_ocr_to_ir()           # image / OCR-json path
  → apply_structure_evidence()         # optional --structure-json
  → annotate_document()                # roles, styles, reading streams
  → DocumentIR (document.ir.json)
  → export_html() / export XML / set-text / apply-html-edits
  → print_html_to_pdf() → compare / benchmark
```

`convert` in `cli.py` is the reference orchestration of the above. Keep renderer, geometry, IR, HTML export, editing, translation, and quality comparison independent of any specific model runtime.

### Central IR (`models.py`)

- `DocumentIR` → `PageIR` → `ElementIR`
- Source-neutral: `source` / `source_type` (`pdf`|`image`); `source_path` and `source_pdf` are compatibility aliases (image IR does not invent `source_pdf`).
- Text fields: `source_text` (immutable original), `edited_text`, `translated_text`.
- Coordinates: `bbox_pdf` + `bbox_px` with page `scale_x`/`scale_y` / `render_dpi`.
- Annotation/export metadata lives mainly on `element.metadata` and `document.metadata` (roles, streams, structure evidence, styles, layout regions, `semantic_layer`).

### Module map (by concern)

| Concern | Primary modules |
|---|---|
| Render / native extract | `pdf_render.py`, `native_pdf.py`, `geometry.py` |
| OCR / image anchors | `ocr.py` (lazy Paddle imports; JSON is the stable API) |
| Structure fusion | `structure_evidence.py` |
| Roles / layout groups / caption links | `annotations.py` |
| Reading order & streams | `reading_order.py`, `reading_streams.py`, `reading_order_sidecar.py` |
| HTML / browser fitting / edits | `html_export.py`, `templates/document.html.j2`, `html_edits.py`, `browser_launch.py`, `pdf_export.py` |
| Quality & benchmarks | `quality.py`, `semantic_quality.py`, `benchmark.py`, `benchmark_fixtures.py` |
| Optional providers | `paddle_layout_provider.py`, `docling_provider.py`, `opendataloader_provider.py`, `provider_degradation.py` |
| Research rankers / graphs | `relation_ranker.py`, `floating_ranker.py`, `hierarchical_order*.py`, `graph_model.py`, `paragraph_graph_benchmark.py`, `successor_graph_benchmark.py`, `joint_graph_benchmark.py`, `semantic_successor.py`, ROOR/Comp-HRDoc helpers |

CLI surface is large (`cli.py`): conversion/export, many `run-*` provider runners, `train-*` rankers, `fetch-*` corpora, and specialized `benchmark-*` suites. Prefer existing subcommands over inventing parallel scripts.

### HTML export modes

- **`structured`**: rebuild text/images/shapes as HTML/SVG for inspectability and editability; no full-page background image (image-only pages keep the native image as the visual layer; OCR anchors stay transparent until hover/focus).
- **`fidelity`**: preserve SVG/raster source visual layer; recognized nodes are transparent coordinate anchors. Only `edited_text` / `translated_text` print as local replacement overlays after Chromium-measured fitting (`window.ScriptoriumFitting`). Browser edits go through `window.ScriptoriumEdits` → `scriptorium-html-edits/v1` → `scriptorium apply-html-edits`.

### Reading order (important invariants)

PDF text order is drawing order, not semantic order. The pipeline keeps stable element IDs and writes:

- `visual_order` — geometric top-left sort
- `semantic_order` — consumer-facing order
- page-local **reading streams** (`body-main`, table/grid islands, footnotes, sidebars, captions, page artifacts, …) plus strategy/evidence metadata

Strategy stack (see implementation notes / roadmap): recursive XY-cut, column-flow, spatial-graph, box-flow, narrow successor-consensus arbitration, caption-flow, protected table/grid islands. Relation-graph path covers and multi-candidate consensus are used heavily as **diagnostics** and review evidence.

**Review-only vs runtime (do not blur this):**

- External providers (Paddle layout, Docling, OpenDataLoader, Surya, trained relation/floating rankers, hierarchical proposals, multi-provider consensus sidecars) default to **review-only**.
- `reading-order.sidecar.proposal.json` has `sidecar_status: proposal` and **must not** reorder runtime IR until explicitly accepted; `apply_structure_evidence()` skips unaccepted proposals.
- Consensus of providers reduces review noise; it is not acceptance and must keep `runtime_reorder: false`.
- Strict island local successors stay separate from page-wide candidate consensus. Do not pretend unresolved body/cross-region handoffs are solved.
- Promoting a provider order into default runtime requires frozen gate evidence in `docs/external-benchmarks.md` (many current gates are explicitly `reject-runtime-promotion`).

### Structure evidence fusion

`structure_evidence.py` normalizes PaddleOCR-VL / PP-StructureV3 / Docling / ROOR-style payloads (regions, weak/explicit block order, relations, streams). For **image** sources, structure JSON can seed the semantic/text-anchor layer when OCR JSON is absent; for **digital PDF**, native extract usually owns fonts/style/bbox and structure is augmenting evidence unless relations/streams force a stronger semantic driver. `DocumentIR.metadata.semantic_layer` records the driver (`native-pdf`, `structure-json`, `ocr-json`, `ocr-fallback`, `visual-only`, plus augmenting vs driver nuance).

### Benchmarking

`scriptorium benchmark` measures visual similarity (`1 - max_diff_ratio` after HTML print-back), page/size match, reading-order risk, candidate disagreement, stream stats, and translation replacement risk. Semantic ground truth lives as sidecars next to sources or under `benchmarks/semantic-ground-truth/` (`semantic_quality.py`). Structure A/B must share the same OCR/text anchors so inventory differences are not scored as structure wins.

## Working conventions for this repo

- Prefer answer-free geometry and local stream protection over global model permutations.
- Keep optional model code lazy-imported and behind CLI runners; core tests should stay runnable without GPU/Paddle/Docling.
- When changing reading order or fusion, extend unit tests under `tests/` and, for behavioral claims, benchmark diagnostics—not README narrative alone.
- Frozen external benchmark numbers and promotion gates live in `docs/external-benchmarks.md`; update those docs when gates or methodology change.
- Large corpora and model outputs belong under `data/` / `outputs/`, not the git tree.
