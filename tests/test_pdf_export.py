from pathlib import Path

import fitz

from scriptorium.pdf_export import normalize_pdf_page_boxes, trim_trailing_blank_pages


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


def test_trim_trailing_blank_pages_removes_browser_blank_tail(tmp_path: Path) -> None:
    pdf_path = tmp_path / "extra_blank.pdf"
    doc = fitz.open()
    doc.new_page(width=100, height=120).insert_text((12, 24), "Real page", fontsize=10)
    blank = doc.new_page(width=100, height=120)
    blank.draw_rect(blank.rect, color=None, fill=(1, 1, 1), width=0)
    doc.save(pdf_path)
    doc.close()

    changed = trim_trailing_blank_pages(pdf_path, expected_page_count=1)

    assert changed is True
    with fitz.open(pdf_path) as trimmed:
        assert trimmed.page_count == 1
        assert "Real page" in trimmed[0].get_text()


def test_trim_trailing_blank_pages_keeps_nonblank_tail(tmp_path: Path) -> None:
    pdf_path = tmp_path / "extra_nonblank.pdf"
    doc = fitz.open()
    doc.new_page(width=100, height=120).insert_text((12, 24), "Real page", fontsize=10)
    doc.new_page(width=100, height=120).insert_text((12, 24), "Overflow page", fontsize=10)
    doc.save(pdf_path)
    doc.close()

    changed = trim_trailing_blank_pages(pdf_path, expected_page_count=1)

    assert changed is False
    with fitz.open(pdf_path) as unchanged:
        assert unchanged.page_count == 2
