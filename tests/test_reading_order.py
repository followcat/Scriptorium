from pathlib import Path

from scriptorium.annotations import annotate_document
from scriptorium.benchmark_fixtures import create_benchmark_fixtures
from scriptorium.html_export import export_html
from scriptorium.native_pdf import extract_native_pdf_to_ir
from scriptorium.pdf_render import render_pdf


def test_two_column_fixture_uses_column_semantic_order(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")
    columns_pdf = next(path for path in pdfs if path.name == "two_column_notes.pdf")
    rendered = render_pdf(columns_pdf, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))

    text_by_value = {element.source_text: element for element in document.pages[0].elements if element.source_text}

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

    left_orders = [text_by_value[text].reading_order for text in left_lines]
    right_orders = [text_by_value[text].reading_order for text in right_lines]
    assert max(left_orders) < min(right_orders)
    assert all(text_by_value[text].metadata["column_index"] == 0 for text in left_lines)
    assert all(text_by_value[text].metadata["column_index"] == 1 for text in right_lines)
    assert all(text_by_value[text].metadata["column_count"] == 2 for text in left_lines + right_lines)
    assert all(text_by_value[text].metadata["reading_order_strategy"] == "column-flow-v1" for text in left_lines + right_lines)

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")
    assert 'data-scriptorium-reading-order-strategy="column-flow-v1"' in html
    assert 'data-scriptorium-column-count="2"' in html
    assert 'data-scriptorium-semantic-order="' in html


def test_table_fixture_keeps_row_major_order(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")
    table_pdf = next(path for path in pdfs if path.name == "table_report.pdf")
    rendered = render_pdf(table_pdf, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))

    text_by_value = {element.source_text: element for element in document.pages[0].elements if element.source_text}
    row_major = ["Layer", "Signal", "Metric", "Value", "Text", "Native spans", "nodes", "18"]
    orders = [text_by_value[text].reading_order for text in row_major]

    assert orders == sorted(orders)
    assert all(text_by_value[text].metadata["column_count"] == 1 for text in row_major)
    assert all(text_by_value[text].metadata["reading_order_strategy"] == "visual-yx" for text in row_major)
