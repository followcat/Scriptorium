# Optimization Roadmap

This project optimizes two different outcomes:

- visual fidelity: the HTML prints back to a PDF that looks like the source
- semantic fidelity: editable/exported text follows human reading order and keeps document structure

## Current Implemented Path

- `recursive-xy-cut-v1` recursively segments pages with horizontal and vertical whitespace cuts, so section headings can stay between independent column regions.
- `column-flow-v1` detects common two-column text regions and orders text left-column first, then right-column.
- Table-like grids stay row-major so table cells are not read as document columns.
- Native PDF extraction now preserves image blocks, maps common paper fonts to closer browser font families, renders simple line drawings and supported non-rectangular drawing paths as SVG, and uses local raster fallback for dense vector figures.
- Native PDF extraction exposes benchmarkable font profiles: `browser-default` for stable baseline numbers and `local-urw` for explicit local Nimbus/DejaVu experiments.
- Benchmark `--font-profile auto` runs both stable and local-URW candidates, records both candidate artifacts, and selects the higher visual-similarity case per PDF.
- Benchmark `--font-size-scale auto` runs a small CSS font-size sweep, records candidate artifacts, and selects the higher visual-similarity case per PDF.
- Benchmark `--text-fit auto` compares normal editable HTML text with an SVG text-fit layer that uses PDF run bboxes and `textLength` to match line widths while retaining a transparent editable proxy.
- Benchmark `--html-mode auto` compares the structured redraw path with the SVG-background fidelity overlay path and selects the higher visual-similarity case per PDF.
- `fidelity` HTML mode keeps SVG page backgrounds visible while overlaying transparent editable coordinate nodes. Print hides unchanged overlays so source-preservation measures the vector background layer, while edited/translated nodes print as local white-background replacement overlays.
- Benchmark printing normalizes exported page boxes to the source PDF dimensions, avoiding Chromium's 1px A4 page-size quantization from showing up as a persistent dimension mismatch.
- Structured HTML text lines use PDF bbox-width alignment (`text-align-last: justify`) to better reproduce justified PDF word spacing while keeping editable source text.
- Short superscript/subscript text runs can be positioned by source span bbox, with guards that avoid long baseline-only body lines.
- `column-flow-v1` can detect real academic two-column pages from repeated left-edge anchors, with coverage checks that avoid sparse author grids.
- Mixed academic pages can now bypass the table-grid guard when repeated left-edge anchors strongly cover the body text, so formula/table noise no longer forces the whole page back to visual order.
- Dense list ordering uses a tighter row bucket so adjacent rows in web-to-PDF pages do not collapse into one reading-order row.
- PaddleOCR-VL / PP-StructureV3 style JSON can be loaded as external structure evidence and fused into native elements by bbox coverage and text similarity.
- Native PDF and OCR JSON paths share the same `scriptorium.reading_order` module.
- Structured HTML exposes both `data-scriptorium-reading-order-strategy` and `data-scriptorium-reading-order-region`.
- Benchmark reports now include `image_count`, `multi_column_element_count`, `column_flow_element_count`, `recursive_xy_cut_element_count`, `reading_order_strategy_counts`, font profile, and structure evidence match/reorder counts.
- Benchmark reports now include text-run, mixed-inline-style, layout-region, raster-policy, raster-fallback, auto font-profile candidate, and reading-order risk diagnostics.
- Built-in fixtures and selected external PDFs use `.semantic-order.json` sidecars and benchmark semantic order with pairwise order accuracy and normalized sequence similarity.

Current benchmark coverage:

| Sample | Multi-column elements | Semantic GT | Order accuracy | Visual similarity |
|---|---:|---:|---:|---:|
| Built-in fixtures | 20 | yes | 1.0 | 0.99036719 |
| arXiv Attention paper | 163 | partial | 1.0 | 0.96840246 |
| ACL Transformer-XL paper | 1213 | partial | 1.0 | 0.95679576 |
| Hacker News print PDF | 0 | partial | 1.0 | 0.9800288 |

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

Current `--html-mode auto --font-size-scale auto --text-fit auto` sweep:

| Sample | Best structured | Auto visual | Delta | Selected mode | Page/size match |
|---|---:|---:|---:|---|---|
| arXiv Attention paper | 0.96840246 | 0.98809524 | +0.01969278 | `fidelity` | yes / yes |
| ACL Transformer-XL paper | 0.95679576 | 0.97636829 | +0.01957253 | `fidelity` | yes / yes |
| Hacker News print PDF | 0.9800288 | 0.99490923 | +0.01488043 | `fidelity` | yes / yes |
| Three-sample mean | 0.96840901 | 0.98645759 | +0.01804858 | mixed | yes / yes |

The fidelity path now has a minimal edit-print path: edited or translated nodes print as local white-background replacement overlays. It still needs smarter masks, adaptive text fitting, and conflict handling for long translations, but it proves that the HTML can carry recognized coordinate nodes while preserving the original visual page much more closely than a full structured redraw. `--html-mode auto` makes this tradeoff explicit by measuring both paths and recording the selected mode in the report.

Current reading-order risk diagnostics example:

| Sample | Risk score | Risk level | Column-geometry pages | Visual-yx column pages | Unlabeled risk text |
|---|---:|---|---:|---:|---:|
| arXiv Attention paper with partial sidecar | 0.07829172 | low | 3 | 1 | 147 |
| ACL Transformer-XL before mixed-layout guard refinement | 0.17061801 | medium | 10 | 3 | 277 |
| ACL Transformer-XL after mixed-layout guard refinement | 0.08879982 | low | 10 | 1 | 277 |

## Next Optimization Options

1. Expand real semantic ground truth for complex PDFs

   The arXiv Attention sidecar covers 5 representative pages and 38 labeled text nodes. The Transformer-XL sidecar covers 3 real ACL two-column pages and 44 labeled text nodes. The Hacker News web-to-PDF sidecar covers 2 pages and 26 dense-list/footer labels. Current ignored-text diagnostics show 147 unlabeled Attention nodes, 277 Transformer-XL nodes, and 69 web-HN table-cell nodes. Expand this to more pages and more document families, especially equations, tables, footnotes, appendices, manuals, and additional web-to-PDF pages.

2. Recursive XY-Cut refinement

   The first backend is implemented. Column-flow now tolerates formula noise between repeated two-column anchors and can split mixed table/body pages when anchors cover enough body text. Next refinements should add table-aware two-column table handling, footer/header suppression, figure/caption proximity, and confidence scoring so `auto` can choose between recursive cuts and fallback order more transparently.

3. Vector renderer refinement

   SVG path output now handles supported PyMuPDF drawing items (`l`, `c`, `re`, `qu`) without using rectangular approximations. Dense local raster fallback still sacrifices editability inside diagrams. A `tables` raster policy was tested but is not the default because current real-paper/web scores dropped. The next step is preserving PDF clipping, blend modes, masks, and grouped draw ordering so more complex drawings can remain structured.

4. Refine edit masks and replacement fitting for fidelity mode

   `fidelity` mode now preserves source visuals and prints edited/translated nodes as local white-background replacement overlays. `--html-mode auto` makes this the benchmark-selected path for the current complex samples, but editing still needs an edit-aware compositor with better masks, padding derived from glyph extents, automatic font-size fitting for translated text, and overlap/conflict detection when replacements are longer than the source bbox.

5. Finer evidence-driven font, scale, and text-fit selection

   Benchmark-time `--font-profile auto`, `--font-size-scale auto`, and `--text-fit auto` now select between global candidates per PDF. The next step is to move from whole-document selection to per-page or per-font-cluster selection, without requiring a full multi-candidate print/compare pass for normal conversion. Current research found that most paper fonts are embedded Type1/PFA, which browsers cannot directly consume; usable TTF extraction is therefore only a partial solution. SVG text-fit also needs edit-state switching and line-height/baseline refinements so long translated replacements can reuse the same fitted layer safely.

6. Box-flow scoring backend

   Add a continuous score similar to pdfminer.six `boxes_flow`, where horizontal and vertical proximity jointly decide text box order. This is useful for pages that are not cleanly separable into columns.

7. Real model evidence A/B

   The `structure_evidence.py` bridge and benchmark `--structure-json` input are now implemented. Run real PaddleOCR-VL 1.6 and PP-StructureV3 `save_to_json` outputs against the same PDFs and compare `native` versus `native-plus-structure`. For digital PDFs, use model output to improve role/order/table/formula metadata while preserving native text/style. For scanned PDFs, use model output as the primary text source.

8. Semantic-order benchmark expansion

   The first sidecar-based benchmark is implemented. Expand it with real/hand-labeled documents and report:

   - normalized edit distance between expected and exported source text
   - column order accuracy
   - table row-major preservation
   - figure/table caption proximity
   - footnote/header/footer order behavior

## Research References

- PyMuPDF text extraction and reading-order notes: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- PyMuPDF image extraction notes: https://pymupdf.readthedocs.io/en/latest/recipes-images.html
- PyMuPDF page API for vector drawings and table detection: https://pymupdf.readthedocs.io/en/latest/page.html
- pdf2htmlEX feature list for native text, font/position preservation, clipping, and image+hidden-text fallback: https://github.com/pdf2htmlEX/pdf2htmlEX/wiki/Feature-List
- PDF Association "Deriving HTML from PDF" specification: https://pdfa.org/download-area/specifications/Deriving_HTML_from_PDF.pdf
- W3C PDF reading-order technique PDF3: https://www.w3.org/TR/WCAG-TECHS/PDF3.html
- pdfminer.six `LAParams.boxes_flow`: https://pdfminersix.readthedocs.io/en/latest/reference/composable.html
- Kendall tau for information ordering evaluation: https://aclanthology.org/J06-4002.pdf
- LayoutReader / ReadingBank reading-order benchmark: https://aclanthology.org/2021.emnlp-main.389/
- XY-Cut++ reading-order recovery: https://arxiv.org/html/2504.10258v1
- Docling technical report: https://arxiv.org/html/2408.09869v5
- LayoutParser paper: https://arxiv.org/abs/2103.15348
- PP-StructureV3 pipeline usage and multi-column reading-order recovery: https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html
- PaddleOCR-VL 1.6 model usage: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6
- MuPDF/SVG plus transparent text-layer PDF-to-HTML pattern: https://github.com/OskarLebuda/rs-pdf
- BuildVu discussion of SVG/HTML5 hybrid PDF-to-HTML layout preservation and text modes: https://blog.idrsolutions.com/convert-pdf-to-html5-preserving-layout/
- Render-and-compare visual evaluation dataset pattern for OCR/HTML reconstruction: https://huggingface.co/datasets/gt-free-ocr-metrics/omnidocbench-render-compare
