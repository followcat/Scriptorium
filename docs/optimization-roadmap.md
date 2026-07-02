# Optimization Roadmap

This project optimizes two different outcomes:

- visual fidelity: the HTML prints back to a PDF that looks like the source
- semantic fidelity: editable/exported text follows human reading order and keeps document structure

## Current Implemented Path

- `recursive-xy-cut-v1` recursively segments pages with horizontal and vertical whitespace cuts, so section headings can stay between independent column regions.
- `column-flow-v1` detects common two-column text regions and orders text left-column first, then right-column.
- Table-like grids stay row-major so table cells are not read as document columns.
- Native PDF and OCR JSON paths share the same `scriptorium.reading_order` module.
- Structured HTML exposes both `data-scriptorium-reading-order-strategy` and `data-scriptorium-reading-order-region`.
- Benchmark reports now include `multi_column_element_count`, `column_flow_element_count`, `recursive_xy_cut_element_count`, and `reading_order_strategy_counts`.
- Built-in fixtures write `.semantic-order.json` sidecars and benchmark semantic order with pairwise order accuracy and normalized sequence similarity.

Current benchmark coverage:

| Sample | Multi-column elements | Semantic GT | Order accuracy | Visual similarity |
|---|---:|---:|---:|---:|
| Built-in fixtures | 20 | yes | 1.0 | 0.98939052 |
| arXiv Attention paper | 163 | no | n/a | 0.88813653 |
| Hacker News print PDF | 0 | no | n/a | 0.9792518 |

## Next Optimization Options

1. Recursive XY-Cut refinement

   The first backend is implemented. Next refinements should add table-aware two-column table handling, footer/header suppression, figure/caption proximity, and confidence scoring so `auto` can choose between recursive cuts and fallback order more transparently.

2. Box-flow scoring backend

   Add a continuous score similar to pdfminer.six `boxes_flow`, where horizontal and vertical proximity jointly decide text box order. This is useful for pages that are not cleanly separable into columns.

3. Layout-model adapter

   Keep `reading_order.py` as the internal contract and add optional adapters for Docling, LayoutParser, PaddleOCR-VL, or PP-Structure when those tools provide region/order predictions.

4. Semantic-order benchmark expansion

   The first sidecar-based benchmark is implemented. Expand it with real/hand-labeled documents and report:

   - normalized edit distance between expected and exported source text
   - column order accuracy
   - table row-major preservation
   - figure/table caption proximity
   - footnote/header/footer order behavior

## Research References

- PyMuPDF text extraction and reading-order notes: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- pdfminer.six `LAParams.boxes_flow`: https://pdfminersix.readthedocs.io/en/latest/reference/composable.html
- Kendall tau for information ordering evaluation: https://aclanthology.org/J06-4002.pdf
- LayoutReader / ReadingBank reading-order benchmark: https://aclanthology.org/2021.emnlp-main.389/
- XY-Cut++ reading-order recovery: https://arxiv.org/html/2504.10258v1
- Docling technical report: https://arxiv.org/html/2408.09869v5
- LayoutParser paper: https://arxiv.org/abs/2103.15348
