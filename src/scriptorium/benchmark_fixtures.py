from __future__ import annotations

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
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 86), "Quarterly Engineering Note", fontsize=26, fontname="helv", color=(0.05, 0.12, 0.26))
    page.insert_text((72, 132), "This page is mostly flowing native PDF text.", fontsize=12, fontname="helv")
    page.insert_text((72, 156), "The benchmark checks whether text nodes, style marks, and coordinates survive.", fontsize=12, fontname="helv")
    page.insert_text((72, 204), "Key points", fontsize=16, fontname="helv", color=(0.12, 0.22, 0.38))
    for index, text in enumerate(
        [
            "1. Extract text spans without using a page screenshot.",
            "2. Emit editable HTML nodes with stable IDs.",
            "3. Preserve source text while allowing edited text.",
        ]
    ):
        page.insert_text((92, 236 + index * 24), text, fontsize=11, fontname="helv")
    _save(doc, path)
    return path


def _create_table_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 82), "Structured Table Report", fontsize=24, fontname="helv", color=(0.08, 0.18, 0.32))
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
    page.insert_text((72, 314), "Table cells should be marked as table-cell-text.", fontsize=11, fontname="helv")
    _save(doc, path)
    return path


def _create_multipage_pdf(path: Path) -> Path:
    doc = fitz.open()
    for page_index in range(2):
        page = doc.new_page(width=420, height=594)
        page.insert_text((48, 64), f"Multipage Fixture {page_index + 1}", fontsize=22, fontname="helv", color=(0.12, 0.16, 0.32))
        page.insert_text((50, 104), "Each page should keep independent dimensions and node IDs.", fontsize=10.5, fontname="helv")
        page.draw_rect(fitz.Rect(48, 146, 372, 226), color=(0.15, 0.35, 0.58), width=1.0)
        page.insert_text((62, 176), f"Page-local block {page_index + 1}", fontsize=14, fontname="helv")
        page.insert_text((62, 202), "Editable text is tracked by element id.", fontsize=9.5, fontname="helv")
    _save(doc, path)
    return path


def _create_columns_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 82), "Two Column Layout", fontsize=24, fontname="helv", color=(0.05, 0.18, 0.22))
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
