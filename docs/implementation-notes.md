# Implementation Notes

## OCR Backend Boundary

The core pipeline consumes normalized JSON and turns it into `DocumentIR`. This is intentional:

- PaddleOCR-VL 1.6 official examples use `from paddleocr import PaddleOCRVL`, create `PaddleOCRVL(pipeline_version="v1.6")`, run `pipeline.predict(...)`, and persist results with `save_to_json(...)`.
- PP-StructureV3 is documented and described as producing structured JSON/Markdown for document parsing, with finer coordinate-oriented output than a pure VLM result.
- Scriptorium should therefore treat Paddle outputs as an OCR adapter concern. The renderer, geometry, IR, HTML export, editing, translation, and quality comparison should stay independent from model runtime details.

Current implementation status:

- `--ocr-json` is the stable tested input for conversion quality work.
- `PaddleOcrAdapter` is isolated in `scriptorium.ocr` and intentionally lazy-imports `paddleocr`.
- `--structure-json` is the stable lightweight bridge for real model output. It accepts PaddleOCR-VL / PP-StructureV3 style JSON and fuses region bbox, label, content, confidence, and block order back into `DocumentIR`.
- `structure_evidence.py` parses nested `res`, `raw_results`, `pages`, `parsing_res_list`, and `layout_det_res.boxes` shapes. The next Paddle-specific step is running real `save_to_json` payloads through this bridge and tracking native-only versus native-plus-structure deltas.

## Annotation Layer

The structured HTML export must not rely on a hand-authored stylesheet to make a demo look right. The pipeline now has an explicit annotation pass:

1. Extraction writes raw evidence into `DocumentIR`:
   - native PDF lines become editable text elements with font size, font family, weight, color, bbox, and source metadata
   - native PDF spans are preserved under `element.metadata.text_runs` with run text, bbox, font, weight, style, color, script, and source coordinates
   - native PDF image blocks become local `image` elements with `source_crop`, bbox, dimensions, and `native-image` source metadata
   - native PDF drawings become shape elements with fill/stroke/border metadata and `shape_geometry`; simple lines keep `line_points_pdf`, and supported multi-item drawing paths keep `svg_path_pdf`
   - dense vector regions can become local raster fallback image elements with `native-raster-region` source metadata
   - OCR fallback elements keep bbox, type, confidence, crop, and style hints
2. `annotate_document()` assigns recognized marks:
   - `role`: `heading`, `paragraph`, `table-cell-text`, `table-shape`, `figure-shape`, `separator-shape`, etc.
   - `source_kind`: `native-pdf`, `native-drawing`, `json-fallback`, etc.
   - `style_id`: stable style bucket recorded under `DocumentIR.metadata.styles`
   - `text_run_count` and `mixed_inline_style`: whether the text element contains multiple native PDF style runs
   - `layout_group_id`: shared region id such as `table-001`, `figure-001`, or `separator-001`
   - `layout_group_kind`: inferred region kind for downstream editing and translation tools
   - `semantic_order`, `visual_order`, `column_index`, `column_count`, `column_span`, and `flow_segment_index`
   - `reading_order_strategy` and `reading_order_region_path`
   - `editable` and `edit_target`: whether the node maps to editable text
   - `bbox_pdf` and `bbox_px`: original coordinate evidence
   - external structure labels from Paddle/PP-Structure evidence, mapped to roles such as `formula`, `running-header`, `footer`, `caption`, and `table-cell-text`
3. `DocumentIR.metadata.layout_regions` records each inferred region with its page index, bbox, kind, confidence, and contributing shape ids.
4. The HTML exporter exposes those marks as DOM attributes:
   - `data-scriptorium-role`
   - `data-scriptorium-source`
   - `data-scriptorium-style-id`
   - `data-scriptorium-layout-group`
   - `data-scriptorium-layout-kind`
   - `data-scriptorium-layout-confidence`
   - `data-scriptorium-shape-geometry`
   - `data-scriptorium-shape-line`
   - `data-scriptorium-run-index`
   - `data-scriptorium-run-style-id`
   - `data-scriptorium-run-script`
   - `data-scriptorium-semantic-order`
   - `data-scriptorium-visual-order`
   - `data-scriptorium-column-index`
   - `data-scriptorium-column-count`
   - `data-scriptorium-column-span`
   - `data-scriptorium-flow-segment`
   - `data-scriptorium-reading-order-strategy`
   - `data-scriptorium-reading-order-region`
   - `data-scriptorium-editable`
   - `data-scriptorium-edit-target`
   - `data-bbox-pdf`
   - `data-bbox-px`

In `structured` mode the exporter intentionally does not include the page background image. The result is made of editable text nodes, structural shape nodes, native image nodes, and local raster fallback nodes, all tied back to recognized evidence in the IR.

Text runs are a source-fidelity layer, not the edit storage model. When `edited_text` or `translated_text` is present, the exporter renders the replacement text as a plain editable node so stale source spans do not distort new content.

## Native Visual Fidelity Layer

Complex scientific PDFs often lose visual score for reasons unrelated to reading order: embedded figures are image blocks, LaTeX fonts are not named like browser fonts, and dense vector graphics may depend on transparency, clipping, and draw ordering that a simple rectangle exporter cannot reproduce.

The native PDF path now handles these cases:

- `native-image`: PyMuPDF `get_text("dict")` image blocks are written as local image assets and exported as positioned image elements. These are source PDF image blocks, not whole-page screenshots.
- Font family normalization maps common PDF names such as `NimbusRomNo9L`, `CMR`, `CMMI`, `CMSY`, `SFTT`, `LiberationSans`, and Nimbus/Courier variants to closer browser families.
- Native extraction records a `font_profile`. `browser-default` is the stable default used for public benchmark numbers; `local-urw` is an explicit A/B profile that prefers locally installed Nimbus/DejaVu families for papers whose PDF fonts match those metrics better.
- Current A/B evidence is mixed: `local-urw` improved Attention from `0.93202666` to `0.93871982`, but reduced Transformer-XL from `0.93358709` to `0.90096092`. Keep `browser-default` as the default until profile selection can be driven by page/font evidence rather than a global switch.
- Benchmark-time `--font-profile auto` now runs both `browser-default` and `local-urw`, records both candidate artifacts, and selects the higher visual-similarity result per PDF. On the current real sample set it keeps Transformer-XL and Hacker News on `browser-default`, selects `local-urw` for Attention, and raises the three-sample mean visual similarity from about `0.94855` to about `0.95078`.
- Structured text lines keep `white-space: pre` and add `text-align-last: justify` in `structured` mode. Each line still uses its extracted PDF bbox, but the browser can expand word spacing to match justified PDF lines more closely.
- Short mixed text runs with script positioning, such as author footnote marks and compact superscripts, can be rendered as positioned child spans. The gate intentionally excludes long baseline-only citation/body lines because fully absolute run placement caused Transformer-XL page scaling and major visual regressions.
- `native-drawing`: simple lines render as SVG `<line>`. Supported non-rectangular drawing items (`l`, `c`, `re`, `qu`) render as positioned SVG `<path>` with fill/stroke opacity, avoiding the previous rectangular approximation for polygons and rounded paths.
- `native-raster-region`: when a page has a dense vector cluster with many line drawings, Scriptorium clips just that local region from the source PDF and exports it as one image node. Text and shape nodes whose centers fall inside that region are hidden to avoid duplicate rendering. Captions and surrounding body text remain editable.
- `--raster-policy tables` is available for explicit experiments with complex table vector regions, but it is not the default. On the current Attention, Transformer-XL, and Hacker News set it reduced visual similarity because Chrome's reprinted bitmap regions introduced more anti-aliasing/compression difference than the structured table renderer did.

This is an explicit fidelity/editability tradeoff: ordinary text, tables, separators, simple drawings, and supported SVG paths stay structured; very dense diagrams become local raster nodes until the vector renderer supports the required clipping, grouping, and blend-mode semantics.

## External Structure Evidence Fusion

PaddleOCR-VL and PP-StructureV3 are best treated as optional evidence providers rather than replacements for native PDF extraction. Native extraction usually gives better font/style/bbox fidelity for digital PDFs, while document models can add missing OCR, layout labels, table/formula/chart regions, and reading-order block predictions.

`src/scriptorium/structure_evidence.py` implements the current bridge:

- `normalize_structure_evidence(payload, document)` accepts common Paddle JSON shapes, including `parsing_res_list` blocks with `block_bbox`, `block_label`, `block_content`, and `block_order`.
- Pixel bboxes are converted to PDF-point bboxes using the page render scale already stored in `DocumentIR`.
- `apply_structure_evidence(document, payload)` aligns model regions to native elements by element bbox coverage and text similarity.
- Matched text elements receive `structure_evidence`, `external_structure_label`, and `external_structure_order` metadata.
- When at least two external block orders are matched on a page, the text reading order can be reassigned with `reading_order_strategy = external-structure-fusion-v1`.
- The annotation pass maps external labels into roles, so labels such as `formula`, `header`, `footer`, `table_caption`, and `table` can affect the structured HTML metadata.

This gives the project an A/B path:

```bash
scriptorium convert input.pdf --out-dir outputs/native
scriptorium convert input.pdf --structure-json paddle.json --out-dir outputs/native-plus-structure
scriptorium benchmark input.pdf --structure-json paddle.json --out-dir outputs/benchmark-native-plus-structure
scriptorium benchmark input.pdf --font-profile local-urw --out-dir outputs/benchmark-local-urw
```

The benchmark command accepts one or more `--structure-json` files, matched by argument order or by names such as `<pdf-stem>.structure.json` and `<parent-dir>.<pdf-stem>.structure.json`. The next quality step is to run real PaddleOCR-VL 1.6 or PP-StructureV3 payloads and compare `native` versus `native-plus-structure` with the same benchmark reports. For scanned PDFs, the model evidence can become the primary text source; for digital papers, it should first be used as role/order/table/formula evidence while preserving native text and style.

## Reading Order Layer

PDF text is positioned drawing evidence, not guaranteed semantic text order. The current implementation keeps visual element IDs stable, then writes semantic ordering metadata:

- `visual_order`: top-left visual order from bbox sorting.
- `semantic_order`: reading order used by XML/DOM/export consumers.
- `recursive-xy-cut-v1`: a hierarchical backend that recursively cuts whitespace into top/bottom and left/right regions, then records the region path for downstream HTML/editing inspection.
- `column-flow-v1`: a lightweight multi-column fallback that detects repeated left/right text columns, keeps tables row-major, and orders each flow segment by column then vertical position.
- Repeated-left-edge detection catches real academic columns whose long text boxes have overlapping center-x clusters. It requires enough repeated anchors per column and at least 55% coverage of candidate body lines, so sparse author grids do not become false two-column pages.
- Visual row ordering uses a small row bucket to absorb tiny PDF extraction offsets while keeping dense list rows separate, which matters for web-to-PDF ranked lists.
- `auto`: uses recursive XY-Cut only when the page has both horizontal and vertical structure; otherwise it falls back to `column-flow-v1` or visual order.
- `column_index` and `column_count`: column assignment for downstream translation/editing surfaces.

The table guard intentionally preserves obvious three-or-more-column grids as row-major visual order, preventing spreadsheet-like rows from being read down columns. The current heuristic is intentionally modular in `src/scriptorium/reading_order.py`. It can be replaced or augmented by:

- A pdfminer.six-style box-flow scorer for pages that need a continuous horizontal-vs-vertical ordering tradeoff.
- Optional model/layout backends such as Docling, LayoutParser, PaddleOCR-VL, or PP-Structure outputs when available.

Research references used for this pass:

- PyMuPDF documents that PDF text may not appear in natural reading order and exposes sorting helpers: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- pdfminer.six exposes `LAParams.boxes_flow` for horizontal-vs-vertical text box ordering: https://pdfminersix.readthedocs.io/en/latest/reference/composable.html
- Reading-order evaluation can use pairwise ordering measures such as Kendall tau: https://aclanthology.org/J06-4002.pdf
- ReadingBank is a reading-order benchmark built for document images: https://aclanthology.org/2021.emnlp-main.389/
- XY-Cut / XY-Cut++ is a common document reading-order recovery family: https://arxiv.org/html/2504.10258v1
- Docling targets detailed PDF layout and reading-order reconstruction: https://arxiv.org/html/2408.09869v5
- LayoutParser provides model-oriented document layout structures and tooling: https://arxiv.org/abs/2103.15348
- PP-StructureV3 documents multi-column reading-order recovery and outputs layout blocks with coordinates/order/content: https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html
- PaddleOCR-VL 1.6 documents `PaddleOCRVL(pipeline_version="v1.6")`, prediction, and JSON saving: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6

## Semantic-Order Benchmark

The built-in benchmark fixtures now write a sidecar file next to each generated PDF:

```text
example.semantic-order.json
```

The sidecar stores a per-page `text_sequence` ground truth. During `scriptorium benchmark`, `semantic_quality.py` first looks next to the source PDF and then under `benchmarks/semantic-ground-truth/` for matching repo sidecars. Repo-level lookup supports both `<pdf-stem>.semantic-order.json` and `<parent-dir>.<pdf-stem>.semantic-order.json`, so generic files like `web-hn/input.pdf` can have stable tracked labels without colliding with other `input.pdf` samples. It compares the extracted semantic order against that sequence and writes `semantic/semantic_quality_report.json` per case.

Supported page match modes:

- `full-sequence`: the default mode for generated fixtures; expected and actual page text should match exactly except for reported missing/extra nodes.
- `ordered-subsequence`: intended for real PDFs with partial human labels; only the listed text nodes are scored, unlisted actual text is counted as ignored, and pairwise order is still evaluated across the labeled nodes.

Metrics:

- `semantic_order_pair_accuracy`: pairwise order correctness across expected text nodes; this is Kendall-tau-like and catches left/right column swaps.
- `semantic_sequence_similarity`: normalized Levenshtein similarity between expected and actual text sequences.
- `semantic_exact_page_match_rate`: page-level exact sequence match rate.
- `ignored_text_count`: unlabelled actual text ignored by `ordered-subsequence` pages.
- `ignored_text_zone_counts`, `ignored_text_role_counts`, and `ignored_text_source_counts`: where ignored text lives and what the annotation layer thinks it is, useful for deciding which unlabeled regions should become future ground truth.
- `semantic_missing_text_count`: expected text nodes not found in extraction.
- `semantic_extra_text_count`: extracted text nodes not present in the ground truth.

For external PDFs without a sidecar in either location, semantic metrics are reported as unavailable while visual metrics still run normally. The tracked arXiv Attention sidecar currently covers 5 representative pages and 38 labeled text nodes. The tracked Transformer-XL sidecar covers 3 real ACL two-column pages and 44 labeled text nodes. The tracked Hacker News web-to-PDF sidecar covers 2 pages and 26 dense-list/footer labels.

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
- `image_count`: native image and local raster fallback elements.
- `shape_count`: structural drawing nodes.
- `style_count`: inferred style buckets.
- `annotation_count`: elements with annotation metadata.
- `text_run_count`: source span/run count preserved for structured rendering.
- `mixed_inline_style_element_count`: text elements containing multiple rendered run styles.
- `multi_column_element_count`: editable text nodes assigned to a multi-column flow.
- `column_flow_element_count`: editable text nodes ordered by `column-flow-v1`.
- `recursive_xy_cut_element_count`: editable text nodes ordered by `recursive-xy-cut-v1`.
- `reading_order_strategy_counts`: per-strategy count of editable text nodes in the JSON report summary and per case.
- `font_profile`: CSS font fallback profile used by native extraction, useful for comparing default browser fallback with local URW/DejaVu paper-font experiments. With benchmark `--font-profile auto`, each case also records `font_profile_candidates`, `font_profile_request`, and `font_profile_selected`.
- `raster_policy`: native local-raster fallback policy.
- `layout_region_counts`: inferred table/figure/separator region counts.
- `raster_fallback_count`, `rasterized_text_count`, `rasterized_image_count`, and `rasterized_shape_count`: editability cost of local raster fallback regions.
- `structure_evidence_source`: optional JSON evidence source used by the case.
- `structure_evidence_region_count`: normalized external regions loaded from Paddle/PP-Structure style JSON.
- `structure_evidence_matched_element_count`: native elements matched to those regions by bbox/text evidence.
- `structure_evidence_reordered_page_count`: pages whose text order was reassigned from external block order.
- `semantic_order_pair_accuracy`: pairwise semantic order score when ground truth is available.
- `semantic_sequence_similarity`: normalized sequence similarity against the sidecar sequence.
- `semantic_ignored_text_count`: actual text nodes ignored by partial `ordered-subsequence` labels.
- `semantic_ignored_text_zone_counts`, `semantic_ignored_text_role_counts`, `semantic_ignored_text_source_counts`: ignored-text diagnostics aggregated across semantic cases.

Current baseline artifacts live under `outputs/benchmark-baseline/`. Future optimizations should report delta against `benchmark_report.json` and `benchmark_summary.csv`.
