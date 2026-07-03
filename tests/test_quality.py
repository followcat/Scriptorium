from pathlib import Path

import fitz
from PIL import Image

from scriptorium.pdf_render import render_pdf
from scriptorium.quality import compare_images, compare_pdf_renderings


def test_compare_images_penalizes_dimension_mismatch(tmp_path: Path) -> None:
    expected = tmp_path / "expected.png"
    actual = tmp_path / "actual.png"
    diff = tmp_path / "diff.png"
    Image.new("RGB", (100, 100), "white").save(expected)
    Image.new("RGB", (120, 100), "white").save(actual)

    report = compare_images(expected, actual, diff, page_number=1)

    assert report["page_match"] is True
    assert report["dimension_match"] is False
    assert report["mismatch_type"] == "dimension_mismatch"
    assert report["pixel_diff_ratio"] == 0
    assert report["size_mismatch_penalty"] > 0
    assert report["diff_ratio"] > 0
    assert report["actual_width"] == 120
    assert diff.exists()


def test_compare_pdf_renderings_penalizes_page_count_mismatch(tmp_path: Path) -> None:
    expected_pdf = tmp_path / "one-page.pdf"
    actual_pdf = tmp_path / "two-page.pdf"
    _make_blank_pdf(expected_pdf, page_count=1)
    _make_blank_pdf(actual_pdf, page_count=2)

    report = compare_pdf_renderings(expected_pdf, actual_pdf, tmp_path / "quality", dpi=72)

    assert report["expected_page_count"] == 1
    assert report["actual_page_count"] == 2
    assert report["compared_page_count"] == 1
    assert report["page_count_match"] is False
    assert report["dimension_match"] is False
    assert report["unmatched_page_count"] == 1
    assert report["mismatched_page_count"] == 1
    assert report["max_diff_ratio"] == 1
    assert report["pages"][1]["page_match"] is False
    assert report["pages"][1]["mismatch_type"] == "extra_actual_page"


def test_pdf_rendering_can_limit_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "two-page.pdf"
    _make_blank_pdf(pdf, page_count=2)

    rendered = render_pdf(pdf, tmp_path / "pages", dpi=72, max_pages=1)
    report = compare_pdf_renderings(pdf, pdf, tmp_path / "quality", dpi=72, max_pages=1)

    assert len(rendered.pages) == 1
    assert rendered.pages[0].page_index == 0
    assert report["max_pages"] == 1
    assert report["expected_page_count"] == 1
    assert report["actual_page_count"] == 1
    assert report["page_count_match"] is True


def _make_blank_pdf(path: Path, page_count: int) -> None:
    doc = fitz.open()
    try:
        for _ in range(page_count):
            doc.new_page(width=200, height=200)
        doc.save(path)
    finally:
        doc.close()
