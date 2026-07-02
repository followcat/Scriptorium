# Optimization Roadmap

This project optimizes two different outcomes:

- visual fidelity: the HTML prints back to a PDF that looks like the source
- semantic fidelity: editable/exported text follows human reading order and keeps document structure

## Current Implemented Path

- `column-flow-v1` detects common two-column text regions and orders text left-column first, then right-column.
- Table-like grids stay row-major so table cells are not read as document columns.
- Native PDF and OCR JSON paths share the same `scriptorium.reading_order` module.
- Benchmark reports now include `multi_column_element_count` and `column_flow_element_count`.

Current benchmark coverage:

| Sample | Multi-column elements | Visual similarity |
|---|---:|---:|
| Built-in fixtures | 9 | 0.98908544 |
| arXiv Attention paper | 163 | 0.88813653 |
| Hacker News print PDF | 0 | 0.9792518 |

## Next Optimization Options

1. Recursive XY-Cut backend

   Use page regions and whitespace cuts to recursively segment headers, columns, figures, tables, and footers. This should improve irregular academic pages where a global two-column heuristic is too weak.

2. Box-flow scoring backend

   Add a continuous score similar to pdfminer.six `boxes_flow`, where horizontal and vertical proximity jointly decide text box order. This is useful for pages that are not cleanly separable into columns.

3. Layout-model adapter

   Keep `reading_order.py` as the internal contract and add optional adapters for Docling, LayoutParser, PaddleOCR-VL, or PP-Structure when those tools provide region/order predictions.

4. Semantic-order benchmark

   Add fixtures with ground-truth text order and report:

   - normalized edit distance between expected and exported source text
   - column order accuracy
   - table row-major preservation
   - figure/table caption proximity

## Research References

- PyMuPDF text extraction and reading-order notes: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- pdfminer.six `LAParams.boxes_flow`: https://pdfminersix.readthedocs.io/en/latest/reference/composable.html
- XY-Cut++ reading-order recovery: https://arxiv.org/html/2504.10258v1
- Docling technical report: https://arxiv.org/html/2408.09869v5
- LayoutParser paper: https://arxiv.org/abs/2103.15348
