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
   - native PDF text spans become text elements with font size, font family, weight, color, bbox, and source metadata
   - native PDF drawings become shape elements with fill/stroke/border metadata
   - OCR fallback elements keep bbox, type, confidence, crop, and style hints
2. `annotate_document()` assigns recognized marks:
   - `role`: `heading`, `paragraph`, `table-cell-text`, `table-shape`, etc.
   - `source_kind`: `native-pdf`, `native-drawing`, `json-fallback`, etc.
   - `style_id`: stable style bucket recorded under `DocumentIR.metadata.styles`
   - `layout_group_id`: shared region id such as `table-001`
   - `editable` and `edit_target`: whether the node maps to editable text
   - `bbox_pdf` and `bbox_px`: original coordinate evidence
3. The HTML exporter exposes those marks as DOM attributes:
   - `data-scriptorium-role`
   - `data-scriptorium-source`
   - `data-scriptorium-style-id`
   - `data-scriptorium-layout-group`
   - `data-scriptorium-editable`
   - `data-scriptorium-edit-target`
   - `data-bbox-pdf`
   - `data-bbox-px`

In `structured` mode the exporter intentionally does not include the page background image. The result is made of editable text nodes plus structural shape nodes, all tied back to recognized evidence in the IR.

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

Current baseline artifacts live under `outputs/benchmark-baseline/`. Future optimizations should report delta against `benchmark_report.json` and `benchmark_summary.csv`.
