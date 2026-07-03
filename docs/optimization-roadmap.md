# Optimization Roadmap

This project optimizes two different outcomes:

- visual fidelity: the HTML prints back to a PDF that looks like the source
- semantic fidelity: editable/exported text follows human reading order and keeps document structure

## Current Implemented Path

- `recursive-xy-cut-v1` recursively segments pages with horizontal and vertical whitespace cuts, so section headings can stay between independent column regions.
- `column-flow-v1` detects common two-column text regions and orders text left-column first, then right-column.
- Table-like grids stay row-major so table cells are not read as document columns.
- Native PDF extraction now preserves image blocks, maps common paper fonts to closer browser font families, renders simple line drawings and supported non-rectangular drawing paths as SVG, and uses local raster fallback for dense vector figures.
- Structured HTML text lines use PDF bbox-width alignment (`text-align-last: justify`) to better reproduce justified PDF word spacing while keeping editable source text.
- `column-flow-v1` can detect real academic two-column pages from repeated left-edge anchors, with coverage checks that avoid sparse author grids.
- Dense list ordering uses a tighter row bucket so adjacent rows in web-to-PDF pages do not collapse into one reading-order row.
- PaddleOCR-VL / PP-StructureV3 style JSON can be loaded as external structure evidence and fused into native elements by bbox coverage and text similarity.
- Native PDF and OCR JSON paths share the same `scriptorium.reading_order` module.
- Structured HTML exposes both `data-scriptorium-reading-order-strategy` and `data-scriptorium-reading-order-region`.
- Benchmark reports now include `image_count`, `multi_column_element_count`, `column_flow_element_count`, `recursive_xy_cut_element_count`, and `reading_order_strategy_counts`.
- Built-in fixtures and selected external PDFs use `.semantic-order.json` sidecars and benchmark semantic order with pairwise order accuracy and normalized sequence similarity.

Current benchmark coverage:

| Sample | Multi-column elements | Semantic GT | Order accuracy | Visual similarity |
|---|---:|---:|---:|---:|
| Built-in fixtures | 20 | yes | 1.0 | 0.99036719 |
| arXiv Attention paper | 163 | partial | 1.0 | 0.93202666 |
| ACL Transformer-XL paper | 880 | partial | 1.0 | 0.93358709 |
| Hacker News print PDF | 0 | partial | 1.0 | 0.9800288 |

## Next Optimization Options

1. Expand real semantic ground truth for complex PDFs

   The arXiv Attention sidecar covers 5 representative pages and 38 labeled text nodes. The Transformer-XL sidecar covers 3 real ACL two-column pages and 44 labeled text nodes. The Hacker News web-to-PDF sidecar covers 2 pages and 26 dense-list/footer labels. Current ignored-text diagnostics show 147 unlabeled Attention nodes, 277 Transformer-XL nodes, and 69 web-HN table-cell nodes. Expand this to more pages and more document families, especially equations, tables, footnotes, appendices, manuals, and additional web-to-PDF pages.

2. Recursive XY-Cut refinement

   The first backend is implemented. Next refinements should add table-aware two-column table handling, footer/header suppression, figure/caption proximity, and confidence scoring so `auto` can choose between recursive cuts and fallback order more transparently.

3. Vector renderer refinement

   SVG path output now handles supported PyMuPDF drawing items (`l`, `c`, `re`, `qu`) without using rectangular approximations. Dense local raster fallback still sacrifices editability inside diagrams. The next step is preserving PDF clipping, blend modes, masks, and grouped draw ordering so more complex drawings can remain structured.

4. Box-flow scoring backend

   Add a continuous score similar to pdfminer.six `boxes_flow`, where horizontal and vertical proximity jointly decide text box order. This is useful for pages that are not cleanly separable into columns.

5. Real model evidence A/B

   The `structure_evidence.py` bridge is now implemented. Run real PaddleOCR-VL 1.6 and PP-StructureV3 `save_to_json` outputs against the same PDFs and compare `native` versus `native-plus-structure`. For digital PDFs, use model output to improve role/order/table/formula metadata while preserving native text/style. For scanned PDFs, use model output as the primary text source.

6. Semantic-order benchmark expansion

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
