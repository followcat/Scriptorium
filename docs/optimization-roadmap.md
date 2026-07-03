# Optimization Roadmap

This project optimizes two different outcomes:

- visual fidelity: the HTML prints back to a PDF that looks like the source
- semantic fidelity: editable/exported text follows human reading order and keeps document structure

## Current Implemented Path

- `recursive-xy-cut-v1` recursively segments pages with horizontal and vertical whitespace cuts, so section headings can stay between independent column regions.
- `column-flow-v1` detects common two- and three-column text regions and orders text by column before moving to the next column.
- `spatial-graph-v1` handles weak irregular columns by linking vertically adjacent text boxes through horizontal overlap and center proximity, but only after stronger table and repeated-anchor paths decline the page.
- `box-flow-v1` handles weak irregular columns with a pdfminer-style column-biased candidate order, but only after stronger table, repeated-anchor, and spatial-graph paths decline the page.
- Column-biased box-flow candidate diagnostics still compare the selected semantic order against a horizontal-flow order and report pairwise disagreement for all benchmarked pages.
- Pure table-like grids use `table-row-major-v1`, so table cells stay row-major without being reported as an unknown visual-order fallback.
- Native PDF extraction now preserves image blocks, maps common paper fonts to closer browser font families, renders simple line drawings and supported non-rectangular drawing paths as SVG, and uses local raster fallback for dense vector figures.
- Native extraction now has an `image-only` OCR fallback for scanned/screenshot PDFs: textless high-image-coverage pages keep their source image layer and gain transparent `native-ocr` editable anchors.
- Native PDF extraction exposes benchmarkable font profiles: `browser-default` for stable baseline numbers and `local-urw` for explicit local Nimbus/DejaVu experiments.
- Benchmark `--font-profile auto` runs both stable and local-URW candidates, records both candidate artifacts, and selects the higher visual-similarity case per PDF.
- Benchmark `--font-size-scale auto` runs a small CSS font-size sweep, records candidate artifacts, and selects the higher visual-similarity case per PDF.
- Benchmark `--text-fit auto` compares normal editable HTML text with an SVG text-fit layer that uses PDF run bboxes and `textLength` to match line widths while retaining a transparent editable proxy.
- Benchmark `--html-mode auto` compares the structured redraw path with fidelity overlay paths and selects the higher visual-similarity case per PDF.
- Benchmark `--fidelity-background auto` compares SVG page backgrounds with raster page backgrounds for fidelity mode. SVG keeps a vector/zoom-friendly source layer; raster often wins strict pixel parity on complex pages.
- `fidelity` HTML mode keeps SVG or raster page backgrounds visible while overlaying transparent editable coordinate nodes. Print hides unchanged overlays so source-preservation measures the background layer, while edited/translated nodes print as local white-background replacement overlays.
- Benchmark printing normalizes exported page boxes to the source PDF dimensions, avoiding Chromium's 1px A4 page-size quantization from showing up as a persistent dimension mismatch.
- Structured HTML text lines use PDF bbox-width alignment (`text-align-last: justify`) to better reproduce justified PDF word spacing while keeping editable source text.
- Short superscript/subscript text runs can be positioned by source span bbox, with guards that avoid long baseline-only body lines.
- `column-flow-v1` can detect real academic two/three-column pages from repeated left-edge anchors, with coverage checks that avoid sparse author grids.
- Table-like grid protection now requires repeated anchors to look like text-flow columns before bypassing row-major order, so short financial/table cells are not read down columns.
- Mixed academic pages can now bypass the table-grid guard when repeated left-edge anchors strongly cover the body text, so formula/table noise no longer forces the whole page back to visual order.
- Mixed table/body pages can now use `mixed-table-column-flow-v1`: repeated short-cell table islands remain row-major, while surrounding non-table text still contributes to body-column detection.
- Weak-column pages can now fall back to `spatial-graph-v1` when repeated x anchors are unstable, while table-like pages and existing high-confidence column/table flows keep their current strategies.
- Weak-column pages can now also fall back to `box-flow-v1` when candidate-order disagreement, balanced x split, vertical overlap, and column separation all support a column-major order.
- Benchmark records box-flow candidate disagreement so complex samples can be prioritized for semantic labels, model evidence, or rule refinement before changing the default ordering path.
- Table-dominated pages now use `table-row-major-v1`, separating intentional table reading order from low-confidence `visual-yx` fallback.
- Formula fragments are guarded by rejecting table-candidate rows that reuse the same repeated x slot, preserving Transformer-XL page-3 semantic order while keeping real table islands active.
- Running page headers/footers near the page margins are tagged as `page-artifact` and removed from body-column inference while staying editable/visible in the IR and HTML.
- Sidebar/marginalia detection estimates the main print space from wider body lines, routes narrow grouped text outside that space as `reading_order_scope = sidebar`, and orders it after the primary body flow instead of treating it as an extra body column.
- Footnote detection routes compact bottom-zone notes as `reading_order_scope = footnote`, keeping them after body columns but before sidebars and footer artifacts.
- Reading-order assignments now expose bounded heuristic confidence plus evidence tags such as `recursive-xy-cut`, `column-flow`, `repeated-left-edge`, `spatial-graph`, `horizontal-overlap-chain`, `multi-head-flow`, `table-row-major`, `table-island-row-major`, `page-edge-artifact`, `footnote-secondary-flow`, `bottom-note-zone`, `sidebar-secondary-flow`, and `external-structure-order`.
- Dense list ordering uses a tighter row bucket so adjacent rows in web-to-PDF pages do not collapse into one reading-order row.
- PaddleOCR-VL / PP-StructureV3 style JSON can be loaded as external structure evidence and fused into native elements by bbox coverage and text similarity.
- Native PDF and OCR JSON paths share the same `scriptorium.reading_order` module.
- Structured HTML exposes reading-order strategy, region, scope, artifact, sidebar, confidence, and evidence attributes.
- Benchmark reports now include `image_count`, `multi_column_element_count`, `column_flow_element_count`, `mixed_table_column_flow_element_count`, `table_row_major_element_count`, `spatial_graph_element_count`, `box_flow_element_count`, `recursive_xy_cut_element_count`, box-flow disagreement metrics, `reading_order_strategy_counts`, font profile, and structure evidence match/reorder counts.
- Benchmark reports now include text-run, mixed-inline-style, layout-region, raster-policy, raster-fallback, OCR fallback, auto font-profile candidate, reading-order footnote/sidebar/confidence/evidence counts, and detailed reading-order risk diagnostics.
- Built-in fixtures and selected external PDFs use `.semantic-order.json` sidecars and benchmark semantic order with pairwise order accuracy and normalized sequence similarity.

Current benchmark coverage:

| Sample | Multi-column elements | Mixed table-flow elements | Table row-major | Spatial graph | Box-flow elements | Box-flow disagreement | Page artifacts | Footnotes | Sidebars | OCR text | Semantic GT | Order accuracy | Visual similarity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Built-in fixtures | 20 | 0 | 18 | 0 | 0 | 0.19494585 | 0 | 0 | 0 | 0 | yes | 1.0 | 0.9906702 |
| arXiv Attention paper | 163 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 | partial | 1.0 | 0.96840246 |
| ACL Transformer-XL paper | 1213 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 | partial | 1.0 | 0.95679576 |
| ACL Transformer-XL first 3 pages, box-flow fallback | 321 | 0 | 0 | 0 | 0 | 0.0825672 | 1 | 7 | 0 | 0 | partial | 1.0 | 0.98160664 |
| Hacker News print PDF | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 | partial | 1.0 | 0.9800288 |
| PUMA 2024 Annual Report, first 12 pages | 217 | 238 | 0 | 0 | 0 | 0.17460108 | 20 | 2 | 36 | 0 | no | n/a | 0.9795117 |
| JD homepage screenshot PDF | 0 | 0 | 0 | 0 | 0 | 0.42778588 | 0 | 0 | 0 | 134 | no | n/a | 0.99576887 |

Current reading-order evidence coverage:

| Sample | RO confidence | Table row-major | Footnote elements | Sidebar elements | Low-confidence RO elements | Evidence highlights |
|---|---:|---:|---:|---:|---:|---|
| Built-in fixtures | 0.80113208 | 18 | 0 | 0 | 0 | 18 `table-row-major`, 20 `recursive-xy-cut`, 20 horizontal/vertical whitespace cuts |
| ACL Transformer-XL first 3 pages | 0.9552648 | 0 | 7 | 0 | 0 | 321 `column-flow`, 321 `repeated-left-edge`, 7 `footnote-secondary-flow` |
| PUMA 2024 Annual Report, first 12 pages | 0.82476488 | 0 | 2 | 36 right | 0 | 36 `sidebar-secondary-flow`, 2 `footnote-secondary-flow`, 20 `page-edge-artifact`, 46 `table-island-row-major` |
| JD homepage screenshot PDF | 0.83 | 0 | 0 | 0 | 0 | 134 `recursive-xy-cut`, 134 horizontal/vertical whitespace cuts |

The current built-in and external benchmark set reports 0 `spatial-graph-v1` and 0 `box-flow-v1` elements. That is intentional for this capability pass: both fallbacks are covered by weak-column unit tests and are guarded so they do not replace stronger repeated-anchor, table, sidebar, footnote, or XY-Cut evidence on existing samples.

Box-flow disagreement is a triage metric, not a semantic score. Transformer-XL's low ratio (`0.0825672`) is consistent with its labeled semantic order staying at `1.0`; JD's high ratio (`0.42778588`) flags dense OCR/web layout where more semantic labels or model structure evidence are needed before changing ordering rules.

Current `--font-profile auto` sweep:

| Sample | Selected profile | Default visual | Auto visual | Delta |
|---|---|---:|---:|---:|
| arXiv Attention paper | `local-urw` | 0.93202666 | 0.93871982 | +0.00669316 |
| ACL Transformer-XL paper | `browser-default` | 0.93358709 | 0.93358709 | +0.00000000 |
| Hacker News print PDF | `browser-default` | 0.9800288 | 0.9800288 | +0.00000000 |
| Three-sample mean | mixed | 0.94854752 | 0.95077857 | +0.00223105 |

Current `--font-size-scale auto` sweep with `browser-default`:

| Sample | Selected scale | Default visual | Auto-scale visual | Delta |
|---|---:|---:|---:|---:|
| arXiv Attention paper | `0.99` | 0.93202666 | 0.93670278 | +0.00467612 |
| ACL Transformer-XL paper | `1.0` | 0.93358709 | 0.93358709 | +0.00000000 |
| Hacker News print PDF | `1.0` | 0.9800288 | 0.9800288 | +0.00000000 |
| Three-sample mean | mixed | 0.94854752 | 0.95010622 | +0.00155870 |

Combined `--font-profile auto --font-size-scale auto` on Attention selected `local-urw` + `1.0`, matching the current best Attention score of `0.93871982`.

Current `--font-size-scale auto --text-fit auto` sweep with `browser-default`:

| Sample | Selected text fit | Previous best structured | Auto text-fit visual | Delta |
|---|---|---:|---:|---:|
| arXiv Attention paper | `0.99 + svg` | 0.93670278 | 0.96840246 | +0.03169968 |
| ACL Transformer-XL paper | `0.99 + svg` | 0.93358709 | 0.95679576 | +0.02320867 |
| Hacker News print PDF | `none` | 0.9800288 | 0.9800288 | +0.00000000 |
| Three-sample mean | mixed | 0.95010622 | 0.96840901 | +0.01830279 |

Current `--html-mode fidelity` SVG overlay sweep:

| Sample | Structured visual | Fidelity visual | Delta | Vector background pages |
|---|---:|---:|---:|---:|
| arXiv Attention paper | 0.93202666 | 0.98809524 | +0.05606858 | 15 |
| ACL Transformer-XL paper | 0.93358709 | 0.97636829 | +0.04278120 | 11 |
| Hacker News print PDF | 0.9800288 | 0.99490923 | +0.01488043 | 2 |
| Three-sample mean | 0.94854752 | 0.98645759 | +0.03791007 | 28 |

Current `--html-mode auto --fidelity-background auto` sweep:

| Sample | Best structured | SVG fidelity | Raster fidelity | Auto visual | Selected path |
|---|---:|---:|---:|---:|---|
| arXiv Attention paper | 0.96840246 | 0.98809524 | 1.0 | 1.0 | `fidelity/raster` |
| ACL Transformer-XL paper | 0.95679576 | 0.97636829 | 0.98096887 | 0.98096887 | `fidelity/raster` |
| Hacker News print PDF | 0.9800288 | 0.99490923 | 1.0 | 1.0 | `fidelity/raster` |
| Three-sample mean | 0.96840901 | 0.98645759 | 0.99365629 | 0.99365629 | mixed |

The fidelity path now has a minimal edit-print path: edited or translated nodes print as local white-background replacement overlays. Raster backgrounds pushed two current real samples to perfect visual parity and raised Transformer-XL from `0.97636829` to `0.98096887`, but SVG backgrounds remain important for vector inspection and future non-raster editing strategies. The remaining Transformer-XL difference is no longer reading-order driven: semantic order is `1.0`, risk is `0.08879982 / low`, and the worst raster diff is page 7 with `0.01903113`.

Current additional complex-source baselines:

| Sample | Scope | Structured visual | SVG fidelity | Raster fidelity | Selected path | Notes |
|---|---:|---:|---:|---:|---|---|
| PUMA 2024 Annual Report | first 12 / 345 pages | 0.73733248 | 0.97885835 | 0.9795117 | `fidelity/raster` | 815 elements, 521 editable, 99 direct column-flow elements, 238 mixed-table-flow elements, 20 header artifacts, 2 footnote elements, 36 right sidebar elements, high semantic-risk without sidecar |
| JD homepage screenshot PDF | 1 / 1 page | 0.99536129 | 0.99536129 | 0.99576887 | `fidelity/raster` | image-only screenshot PDF, 134 transparent OCR edit anchors, now handled by recursive XY-Cut rather than false table-flow |

The JD gain is not a visual-score gain; it is a structural/editability gain. The image-only PDF previously reported 1 image element and 0 editable text nodes. With generic OCR fallback it reports 135 elements, 134 editable `native-ocr` nodes, and keeps the same selected visual score. PUMA remains unchanged on OCR counts because its sampled pages expose native PDF text. Its latest mixed-table/artifact/sidebar/footnote pass keeps the pixel score unchanged, marks 20 repeated header candidates, routes 36 right-side marginal/sidebar text nodes and 2 bottom-zone footnote nodes as secondary flow, and reduces table-like visual-yx pages to 0, with reading-order risk `0.35 / high`.

Current reading-order risk diagnostics example:

| Sample | Risk score | Risk level | Text-flow column pages | Visual-yx column pages | Repeated-anchor pages | Max anchors | Table-like pages | Table-like visual-yx | Unlabeled risk text |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| Built-in fixtures | 0 | low | 3 | 0 | 3 | 3 | 1 | 0 | 0 |
| ACL Transformer-XL after mixed-layout guard refinement | 0.08879982 | low | 10 | 1 | n/a | n/a | n/a | n/a | 277 |
| ACL Transformer-XL first 3 pages, formula-slot guard | 0.21573209 | medium | 3 | 0 | 3 | 2 | 1 | 0 | 277 |
| PUMA Annual Report, first 12 pages | 0.35 | high | 5 | 0 | 5 | 3 | 4 | 0 | 521 |

The extra repeated-anchor/table-like/sidebar/footnote/spatial-graph/box-flow counters and evidence counts make the risk score actionable: built-in pure tables no longer produce a high-risk visual-yx false positive, PUMA identifies 36 right-side secondary-flow nodes plus 2 footnote-flow nodes, current samples show the weak-column fallbacks have not taken over unrelated pages, and JD's high box-flow disagreement identifies it as a priority for semantic labeling or external structure evidence. The next work should focus on complex-document semantic labels, confidence calibration, candidate-order selection, and external structure evidence rather than only stronger global column detection.

## Next Optimization Options

1. Expand real semantic ground truth for complex PDFs

   The arXiv Attention sidecar covers 5 representative pages and 38 labeled text nodes. The Transformer-XL sidecar covers 3 real ACL two-column pages and 44 labeled text nodes. The Hacker News web-to-PDF sidecar covers 2 pages and 26 dense-list/footer labels. Current ignored-text diagnostics show 147 unlabeled Attention nodes, 277 Transformer-XL nodes, and 69 web-HN table-cell nodes. The PUMA annual report first-12-pages benchmark now adds a high-risk non-paper sample with 99 direct column-flow elements, 238 mixed-table-flow elements, 2 footnote elements, 36 sidebar elements, and no semantic sidecar. Built-in fixtures now explicitly score 18 pure-table nodes as `table-row-major-v1`. Expand this to more pages and more document families, especially annual reports, equations, tables, footnotes, appendices, manuals, and additional web-to-PDF pages.

2. Recursive XY-Cut refinement

   The first backend is implemented. Column-flow now tolerates formula noise between repeated anchors, supports up to three repeated text-flow columns, can split mixed table/body pages with local table islands, routes print-space-external sidebars and bottom-zone footnotes as secondary flow, and emits confidence/evidence metadata. Spatial graph and guarded box-flow now cover weak-column fallback paths when repeated anchors are unstable, and box-flow diagnostics expose candidate-order disagreement on all benchmarked pages. Next refinements should add figure/caption proximity, confidence calibration against real semantic sidecars, and explicit candidate-order arbitration between native heuristics, model structure evidence, and relation-graph predictions.

3. Vector renderer refinement

   SVG path output now handles supported PyMuPDF drawing items (`l`, `c`, `re`, `qu`) without using rectangular approximations. Dense local raster fallback still sacrifices editability inside diagrams. A `tables` raster policy was tested but is not the default because current real-paper/web scores dropped. The next step is preserving PDF clipping, blend modes, masks, and grouped draw ordering so more complex drawings can remain structured.

4. Refine edit masks and replacement fitting for fidelity mode

   `fidelity` mode now preserves source visuals and prints edited/translated nodes as local white-background replacement overlays. `--html-mode auto --fidelity-background auto` makes this the benchmark-selected path for the current complex samples, but editing still needs an edit-aware compositor with better masks, padding derived from glyph extents, automatic font-size fitting for translated text, and overlap/conflict detection when replacements are longer than the source bbox. The compositor should work with both SVG and raster backgrounds so the benchmark can choose visual fidelity without losing edit architecture.

5. Finer evidence-driven font, scale, and text-fit selection

   Benchmark-time `--font-profile auto`, `--font-size-scale auto`, and `--text-fit auto` now select between global candidates per PDF. The next step is to move from whole-document selection to per-page or per-font-cluster selection, without requiring a full multi-candidate print/compare pass for normal conversion. Current research found that most paper fonts are embedded Type1/PFA, which browsers cannot directly consume; usable TTF extraction is therefore only a partial solution. SVG text-fit also needs edit-state switching and line-height/baseline refinements so long translated replacements can reuse the same fitted layer safely.

6. Box-flow scoring backend

   Add a continuous score similar to pdfminer.six `boxes_flow`, where horizontal and vertical proximity jointly decide text box order. This is useful for pages that are not cleanly separable into columns.

7. Real model evidence A/B

   The `structure_evidence.py` bridge and benchmark `--structure-json` input are now implemented. Run real PaddleOCR-VL 1.6 and PP-StructureV3 `save_to_json` outputs against the same PDFs and compare `native` versus `native-plus-structure`. For digital PDFs, use model output to improve role/order/table/formula metadata while preserving native text/style. For scanned PDFs, use model output as the primary text source.

8. OCR fallback refinement

   The first image-only fallback uses page-level native-text absence plus image coverage as its trigger. Next refinements should add OCR confidence aggregation, per-region OCR for mixed native/scanned pages, language auto-detection, duplicated-text suppression when PDFs contain invisible OCR text, and optional Paddle/PP-Structure OCR evidence as a stronger replacement for the local Tesseract fallback.

9. Semantic-order benchmark expansion

   The first sidecar-based benchmark is implemented. Expand it with real/hand-labeled documents and report:

   - normalized edit distance between expected and exported source text
   - column order accuracy
   - table row-major preservation
   - figure/table caption proximity
   - footnote/header/footer order calibration and edge-case coverage

## Research References

- PyMuPDF text extraction and reading-order notes: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- PyMuPDF image extraction notes: https://pymupdf.readthedocs.io/en/latest/recipes-images.html
- PyMuPDF page API for vector drawings and table detection: https://pymupdf.readthedocs.io/en/latest/page.html
- pdf2htmlEX feature list for native text, font/position preservation, clipping, and image+hidden-text fallback: https://github.com/pdf2htmlEX/pdf2htmlEX/wiki/Feature-List
- PDF Association "Deriving HTML from PDF" specification: https://pdfa.org/download-area/specifications/Deriving_HTML_from_PDF.pdf
- W3C PDF reading-order technique PDF3: https://www.w3.org/TR/WCAG-TECHS/PDF3.html
- W3C PDF14 running headers and footers as pagination artifacts: https://www.w3.org/WAI/WCAG22/Techniques/pdf/PDF14
- W3C PDF4 artifact examples including page headers/footers: https://www.w3.org/TR/WCAG20-TECHS/PDF4.html
- OCR-D PAGE reading-order guidelines for print-space-external marginalia: https://ocr-d.de/en/gt-guidelines/trans/lyLeserichtung.html
- EPUB accessibility logical reading order and `aside` semantics for secondary content: https://idpf.github.io/a11y-guidelines/content/semantics/order.html
- PRImA reading-order representation/evaluation for complex layouts: https://www.primaresearch.org/www/assets/papers/ICDAR2013_Clausner_ReadingOrder.pdf
- pdfminer.six `LAParams.boxes_flow`: https://pdfminersix.readthedocs.io/en/latest/reference/composable.html
- Kendall tau for information ordering evaluation: https://aclanthology.org/J06-4002.pdf
- Modeling reading order as relations for visually-rich documents: https://aclanthology.org/2024.emnlp-main.540/
- LayoutReader / ReadingBank reading-order benchmark: https://aclanthology.org/2021.emnlp-main.389/
- XY-Cut++ reading-order recovery: https://arxiv.org/html/2504.10258v1
- Docling technical report: https://arxiv.org/html/2408.09869v5
- LayoutParser paper: https://arxiv.org/abs/2103.15348
- PP-StructureV3 pipeline usage and multi-column reading-order recovery: https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html
- PaddleOCR-VL 1.6 model usage: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6
- MuPDF/SVG plus transparent text-layer PDF-to-HTML pattern: https://github.com/OskarLebuda/rs-pdf
- BuildVu discussion of SVG/HTML5 hybrid PDF-to-HTML layout preservation and text modes: https://blog.idrsolutions.com/convert-pdf-to-html5-preserving-layout/
- Render-and-compare visual evaluation dataset pattern for OCR/HTML reconstruction: https://huggingface.co/datasets/gt-free-ocr-metrics/omnidocbench-render-compare
- External benchmark sample manifest: docs/external-benchmarks.md
