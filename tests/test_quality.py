from pathlib import Path

import fitz
from PIL import Image

from scriptorium.html_export import export_html
from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.pdf_render import render_pdf
from scriptorium.quality import (
    _fidelity_replacement_layout_report,
    compare_images,
    compare_pdf_renderings,
    inspect_fidelity_replacement_layout,
)


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


def test_fidelity_replacement_layout_report_counts_browser_measurements() -> None:
    report = _fidelity_replacement_layout_report(
        [
            {
                "element_id": "fit",
                "overflow": False,
                "horizontal_overflow": False,
                "vertical_overflow": False,
                "fit_policy": "browser-layout-v1",
                "declared_line_height": 1.12,
                "rendered_line_height": 1.0,
            },
            {
                "element_id": "clipped",
                "overflow": True,
                "horizontal_overflow": False,
                "vertical_overflow": True,
                "fit_policy": None,
                "declared_line_height": 1.12,
                "rendered_line_height": None,
            },
        ]
    )

    assert report["available"] is True
    assert report["element_count"] == 2
    assert report["overflow_count"] == 1
    assert report["vertical_overflow_count"] == 1
    assert report["fitted_element_count"] == 1
    assert report["line_height_compacted_count"] == 1


def test_inspect_fidelity_replacement_layout_measures_exported_html(tmp_path: Path) -> None:
    background = tmp_path / "page.png"
    Image.new("RGB", (240, 160), "white").save(background)
    replacement = ElementIR(
        id="replace",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=15, y0=12, x1=75, y1=24),
        bbox_px=BBox(x0=30, y0=24, x1=150, y1=48),
        source_text="Source",
        translated_text="A translated replacement that needs browser fitting.",
        style_hint={"font_size_px": 18, "line_height": 1.1, "text_color": "rgb(0, 0, 0)"},
    )
    document = DocumentIR(
        source_pdf="synthetic.pdf",
        render_dpi=144,
        page_count=1,
        pages=[
            PageIR(
                page_index=0,
                width_pt=120,
                height_pt=80,
                width_px=240,
                height_px=160,
                render_dpi=144,
                scale_x=2,
                scale_y=2,
                background_image=str(background),
                elements=[replacement],
            )
        ],
    )
    html_path = export_html(document, tmp_path / "fidelity", display_mode="fidelity")

    report = inspect_fidelity_replacement_layout(html_path, tmp_path / "layout.json")

    assert report["available"] is True
    assert report["element_count"] == 1
    assert report["fitted_element_count"] == 1
    assert report["elements"][0]["element_id"] == "replace"
    assert report["elements"][0]["fit_policy"] == "browser-layout-v1"
    assert (tmp_path / "layout.json").is_file()


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


def test_pdf_rendering_can_select_source_page_indices(tmp_path: Path) -> None:
    pdf = tmp_path / "three-page.pdf"
    _make_blank_pdf(pdf, page_count=3)

    rendered = render_pdf(pdf, tmp_path / "pages", dpi=72, page_indices=(1,))
    report = compare_pdf_renderings(
        pdf,
        pdf,
        tmp_path / "quality",
        dpi=72,
        expected_page_indices=(1,),
        actual_page_indices=(1,),
    )

    assert len(rendered.pages) == 1
    assert rendered.pages[0].page_index == 1
    assert rendered.pages[0].background_image.name == "page_0002.png"
    assert report["max_pages"] is None
    assert report["expected_page_indices"] == [1]
    assert report["actual_page_indices"] == [1]
    assert report["expected_page_count"] == 1
    assert report["actual_page_count"] == 1
    assert report["pages"][0]["expected_source_page_index"] == 1
    assert report["pages"][0]["expected_source_page_number"] == 2
    assert report["pages"][0]["actual_source_page_index"] == 1
    assert report["pages"][0]["actual_source_page_number"] == 2


def _make_blank_pdf(path: Path, page_count: int) -> None:
    doc = fitz.open()
    try:
        for _ in range(page_count):
            doc.new_page(width=200, height=200)
        doc.save(path)
    finally:
        doc.close()
