from __future__ import annotations

import json
from pathlib import Path

import fitz


def create_benchmark_fixtures(out_dir: str | Path) -> list[Path]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    return [
        _create_text_pdf(target / "text_letter.pdf"),
        _create_table_pdf(target / "table_report.pdf"),
        _create_multipage_pdf(target / "multipage_mixed.pdf"),
        _create_columns_pdf(target / "two_column_notes.pdf"),
    ]


def _create_text_pdf(path: Path) -> Path:
    expected_text = [
        "Quarterly Engineering Note",
        "This page is mostly flowing native PDF text.",
        "The benchmark checks whether text nodes, style marks, and coordinates survive.",
        "Key points",
        "1. Extract text spans without using a page screenshot.",
        "2. Emit editable HTML nodes with stable IDs.",
        "3. Preserve source text while allowing edited text.",
    ]
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 86), expected_text[0], fontsize=26, fontname="helv", color=(0.05, 0.12, 0.26))
    page.insert_text((72, 132), expected_text[1], fontsize=12, fontname="helv")
    page.insert_text((72, 156), expected_text[2], fontsize=12, fontname="helv")
    page.insert_text((72, 204), expected_text[3], fontsize=16, fontname="helv", color=(0.12, 0.22, 0.38))
    for index, text in enumerate(expected_text[4:]):
        page.insert_text((92, 236 + index * 24), text, fontsize=11, fontname="helv")
    _save(doc, path)
    _write_semantic_ground_truth(path, [expected_text])
    return path


def _create_table_pdf(path: Path) -> Path:
    expected_text = [
        "Structured Table Report",
        "Layer",
        "Signal",
        "Metric",
        "Value",
        "Text",
        "Native spans",
        "nodes",
        "18",
        "Drawing",
        "Vector lines",
        "shapes",
        "24",
        "HTML",
        "Editable DOM",
        "mode",
        "structured",
        "Table cells should be marked as table-cell-text.",
    ]
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 82), expected_text[0], fontsize=24, fontname="helv", color=(0.08, 0.18, 0.32))
    x0, y0 = 72, 132
    widths = [138, 180, 120, 80]
    heights = [34, 34, 34, 34]
    headers = ["Layer", "Signal", "Metric", "Value"]
    rows = [
        ["Text", "Native spans", "nodes", "18"],
        ["Drawing", "Vector lines", "shapes", "24"],
        ["HTML", "Editable DOM", "mode", "structured"],
    ]
    _draw_table(page, x0, y0, widths, heights, headers, rows)
    page.insert_text((72, 314), expected_text[-1], fontsize=11, fontname="helv")
    _save(doc, path)
    _write_semantic_ground_truth(path, [expected_text])
    return path


def _create_multipage_pdf(path: Path) -> Path:
    doc = fitz.open()
    expected_pages: list[list[str]] = []
    for page_index in range(2):
        expected_text = [
            f"Multipage Fixture {page_index + 1}",
            "Each page should keep independent dimensions and node IDs.",
            f"Page-local block {page_index + 1}",
            "Editable text is tracked by element id.",
        ]
        expected_pages.append(expected_text)
        page = doc.new_page(width=420, height=594)
        page.insert_text((48, 64), expected_text[0], fontsize=22, fontname="helv", color=(0.12, 0.16, 0.32))
        page.insert_text((50, 104), expected_text[1], fontsize=10.5, fontname="helv")
        page.draw_rect(fitz.Rect(48, 146, 372, 226), color=(0.15, 0.35, 0.58), width=1.0)
        page.insert_text((62, 176), expected_text[2], fontsize=14, fontname="helv")
        page.insert_text((62, 202), expected_text[3], fontsize=9.5, fontname="helv")
    _save(doc, path)
    _write_semantic_ground_truth(path, expected_pages)
    return path


def _create_columns_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    title = "Two Column Layout"
    page.insert_text((72, 82), title, fontsize=24, fontname="helv", color=(0.05, 0.18, 0.22))
    left_lines = [
        "Left column paragraph one.",
        "Native extraction keeps text spans.",
        "The annotation layer records role.",
        "Coordinates remain in PDF points.",
    ]
    right_lines = [
        "Right column paragraph one.",
        "This stresses reading order.",
        "The HTML should avoid page images.",
        "Benchmarks track similarity.",
    ]
    for index, line in enumerate(left_lines):
        page.insert_text((72, 136 + index * 28), line, fontsize=11, fontname="helv")
    for index, line in enumerate(right_lines):
        page.insert_text((330, 136 + index * 28), line, fontsize=11, fontname="helv")
    page.draw_line(fitz.Point(306, 128), fitz.Point(306, 260), color=(0.65, 0.65, 0.65), width=0.8)
    _save(doc, path)
    _write_semantic_ground_truth(path, [[title, *left_lines, *right_lines]])
    return path


def _draw_table(
    page: fitz.Page,
    x0: float,
    y0: float,
    widths: list[float],
    heights: list[float],
    headers: list[str],
    rows: list[list[str]],
) -> None:
    y = y0
    all_rows = [headers, *rows]
    for row_index, row in enumerate(all_rows):
        x = x0
        height = heights[min(row_index, len(heights) - 1)]
        for col_index, cell in enumerate(row):
            width = widths[col_index]
            rect = fitz.Rect(x, y, x + width, y + height)
            if row_index == 0:
                page.draw_rect(rect, color=(0.37, 0.46, 0.58), fill=(0.87, 0.91, 0.96), width=0.9)
            else:
                page.draw_rect(rect, color=(0.37, 0.46, 0.58), width=0.8)
            page.insert_text((x + 8, y + 21), cell, fontsize=10.5, fontname="helv")
            x += width
        y += height


def _save(doc: fitz.Document, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def _write_semantic_ground_truth(path: Path, page_sequences: list[list[str]]) -> None:
    payload = {
        "version": 1,
        "source_pdf": path.name,
        "pages": [
            {"page_index": page_index, "text_sequence": sequence}
            for page_index, sequence in enumerate(page_sequences)
        ],
    }
    path.with_suffix(".semantic-order.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
