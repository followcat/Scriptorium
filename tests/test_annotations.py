from pathlib import Path

from scriptorium.annotations import annotate_document
from scriptorium.benchmark_fixtures import create_benchmark_fixtures
from scriptorium.fixture import create_fixture
from scriptorium.native_pdf import extract_native_pdf_to_ir
from scriptorium.pdf_render import render_pdf


def test_layout_regions_split_figure_and_table(tmp_path: Path) -> None:
    pdf_path, _ = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))

    regions = document.metadata["layout_regions"][0]["regions"]
    assert [region["kind"] for region in regions] == ["figure", "table"]

    text_roles = {element.source_text: element.metadata["role"] for element in document.pages[0].elements if element.source_text}
    assert text_roles["Structured block"] == "paragraph"
    assert text_roles["Table"] == "table-cell-text"
    assert text_roles["OCR"] == "table-cell-text"
    assert text_roles["Coords"] == "table-cell-text"

    shape_roles = [element.metadata["role"] for element in document.pages[0].elements if element.type == "shape"]
    assert "figure-shape" in shape_roles
    assert "table-shape" in shape_roles


def test_layout_regions_detect_separator_lines(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")
    columns_pdf = next(path for path in pdfs if path.name == "two_column_notes.pdf")
    rendered = render_pdf(columns_pdf, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))

    regions = document.metadata["layout_regions"][0]["regions"]
    assert [region["kind"] for region in regions] == ["separator"]

    separator_shapes = [
        element
        for element in document.pages[0].elements
        if element.metadata["annotation"]["layout_group_kind"] == "separator"
    ]
    assert len(separator_shapes) == 1
    assert separator_shapes[0].metadata["role"] == "separator-shape"
    assert separator_shapes[0].metadata["shape_geometry"] == "vertical-line"
