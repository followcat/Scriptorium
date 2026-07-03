from pathlib import Path

import fitz

from scriptorium.pdf_export import normalize_pdf_page_boxes


def test_normalize_pdf_page_boxes_sets_source_dimensions(tmp_path: Path) -> None:
    pdf_path = tmp_path / "page.pdf"
    doc = fitz.open()
    doc.new_page(width=100, height=120)
    doc.save(pdf_path)

    changed = normalize_pdf_page_boxes(pdf_path, [(123.5, 98.25)])

    assert changed is True
    with fitz.open(pdf_path) as normalized:
        rect = normalized[0].rect
        assert round(rect.width, 2) == 123.5
        assert round(rect.height, 2) == 98.25

    unchanged = normalize_pdf_page_boxes(pdf_path, [(123.5, 98.25)])

    assert unchanged is False
