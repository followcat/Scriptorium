# Implementation Notes

## OCR Backend Boundary

The core pipeline consumes normalized JSON and turns it into `DocumentIR`. This is intentional:

- PaddleOCR-VL 1.6 official examples use `from paddleocr import PaddleOCRVL`, create `PaddleOCRVL(pipeline_version="v1.6")`, run `pipeline.predict(...)`, and persist results with `save_to_json(...)`.
- PP-StructureV3 is documented and described as producing structured JSON/Markdown for document parsing, with finer coordinate-oriented output than a pure VLM result.
- Scriptorium should therefore treat Paddle outputs as an OCR adapter concern. The renderer, geometry, IR, HTML export, editing, translation, and quality comparison should stay independent from model runtime details.

Current implementation status:

- `--ocr-json` is the stable tested input for conversion quality work.
- `PaddleOcrAdapter` is isolated in `scriptorium.ocr` and intentionally lazy-imports `paddleocr`.
- The next Paddle-specific step is to map real `save_to_json` output from PaddleOCR-VL/PP-StructureV3 into the fallback JSON shape consumed by `normalize_ocr_to_ir`.

## Annotation Layer

The structured HTML export must not rely on a hand-authored stylesheet to make a demo look right. The pipeline now has an explicit annotation pass:

1. Extraction writes raw evidence into `DocumentIR`:
   - native PDF lines become editable text elements with font size, font family, weight, color, bbox, and source metadata
   - native PDF spans are preserved under `element.metadata.text_runs` with run text, bbox, font, weight, style, color, script, and source coordinates
   - native PDF drawings become shape elements with fill/stroke/border metadata and `shape_geometry`
   - OCR fallback elements keep bbox, type, confidence, crop, and style hints
2. `annotate_document()` assigns recognized marks:
   - `role`: `heading`, `paragraph`, `table-cell-text`, `table-shape`, `figure-shape`, `separator-shape`, etc.
   - `source_kind`: `native-pdf`, `native-drawing`, `json-fallback`, etc.
   - `style_id`: stable style bucket recorded under `DocumentIR.metadata.styles`
   - `text_run_count` and `mixed_inline_style`: whether the text element contains multiple native PDF style runs
   - `layout_group_id`: shared region id such as `table-001`, `figure-001`, or `separator-001`
   - `layout_group_kind`: inferred region kind for downstream editing and translation tools
   - `semantic_order`, `visual_order`, `column_index`, `column_count`, `column_span`, and `flow_segment_index`
   - `editable` and `edit_target`: whether the node maps to editable text
   - `bbox_pdf` and `bbox_px`: original coordinate evidence
3. `DocumentIR.metadata.layout_regions` records each inferred region with its page index, bbox, kind, confidence, and contributing shape ids.
4. The HTML exporter exposes those marks as DOM attributes:
   - `data-scriptorium-role`
   - `data-scriptorium-source`
   - `data-scriptorium-style-id`
   - `data-scriptorium-layout-group`
   - `data-scriptorium-layout-kind`
   - `data-scriptorium-layout-confidence`
   - `data-scriptorium-run-index`
   - `data-scriptorium-run-style-id`
   - `data-scriptorium-run-script`
   - `data-scriptorium-semantic-order`
   - `data-scriptorium-visual-order`
   - `data-scriptorium-column-index`
   - `data-scriptorium-column-count`
   - `data-scriptorium-flow-segment`
   - `data-scriptorium-editable`
   - `data-scriptorium-edit-target`
   - `data-bbox-pdf`
   - `data-bbox-px`

In `structured` mode the exporter intentionally does not include the page background image. The result is made of editable text nodes plus structural shape nodes, all tied back to recognized evidence in the IR.

Text runs are a source-fidelity layer, not the edit storage model. When `edited_text` or `translated_text` is present, the exporter renders the replacement text as a plain editable node so stale source spans do not distort new content.

## Reading Order Layer

PDF text is positioned drawing evidence, not guaranteed semantic text order. The current implementation keeps visual element IDs stable, then writes semantic ordering metadata:

- `visual_order`: top-left visual order from bbox sorting.
- `semantic_order`: reading order used by XML/DOM/export consumers.
- `column-flow-v1`: a lightweight multi-column heuristic that detects repeated left/right text columns, keeps tables row-major, and orders each flow segment by column then vertical position.
- `column_index` and `column_count`: column assignment for downstream translation/editing surfaces.

The current heuristic is intentionally modular in `src/scriptorium/reading_order.py`. It can be replaced or augmented by:

- Recursive XY-Cut / XY-Cut++ for hierarchical page segmentation.
- A pdfminer.six-style box-flow scorer for pages that need a continuous horizontal-vs-vertical ordering tradeoff.
- Optional model/layout backends such as Docling, LayoutParser, PaddleOCR-VL, or PP-Structure outputs when available.

Research references used for this pass:

- PyMuPDF documents that PDF text may not appear in natural reading order and exposes sorting helpers: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- pdfminer.six exposes `LAParams.boxes_flow` for horizontal-vs-vertical text box ordering: https://pdfminersix.readthedocs.io/en/latest/reference/composable.html
- XY-Cut / XY-Cut++ is a common document reading-order recovery family: https://arxiv.org/html/2504.10258v1
- Docling targets detailed PDF layout and reading-order reconstruction: https://arxiv.org/html/2408.09869v5
- LayoutParser provides model-oriented document layout structures and tooling: https://arxiv.org/abs/2103.15348

## Useful References

- PaddleOCR GitHub: https://github.com/PaddlePaddle/PaddleOCR
- PaddleOCR-VL-1.6 model usage: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6
- PyMuPDF image rendering: https://pymupdf.readthedocs.io/en/latest/recipes-images.html
- Playwright PDF output: https://playwright.dev/python/docs/api/class-page#page-pdf

## Benchmark Metrics

The benchmark command is the baseline for future optimization:

```bash
scriptorium benchmark --out-dir outputs/benchmark-baseline --dpi 192
```

Metrics:

- `max_diff_ratio`: maximum normalized page difference between original PDF render and structured HTML-to-PDF render. Missing/extra pages are scored as `1.0`; page dimension mismatches add a size penalty instead of silently resizing away the mismatch.
- `mean_diff_ratio`: average page difference across all matched and unmatched pages.
- `p95_diff_ratio`: 95th percentile diff ratio for the compared page set.
- `worst_page`: 1-based page number with the largest effective diff ratio.
- `visual_similarity`: `1 - max_diff_ratio`; higher is better.
- `page_count_match`: whether expected and actual PDFs have the same page count.
- `dimension_match`: whether every reported page has matching render dimensions.
- `total_seconds`: wall-clock time for render, extraction, annotation, HTML export, PDF print, and comparison.
- `timings`: per-stage timing split.
- `element_count`: total generated IR elements.
- `editable_element_count`: elements that map to editable text.
- `shape_count`: structural drawing nodes.
- `style_count`: inferred style buckets.
- `annotation_count`: elements with annotation metadata.
- `multi_column_element_count`: editable text nodes assigned to a multi-column flow.
- `column_flow_element_count`: editable text nodes ordered by `column-flow-v1`.

Current baseline artifacts live under `outputs/benchmark-baseline/`. Future optimizations should report delta against `benchmark_report.json` and `benchmark_summary.csv`.
