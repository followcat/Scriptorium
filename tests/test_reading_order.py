from pathlib import Path

from scriptorium.annotations import annotate_document
from scriptorium.benchmark_fixtures import create_benchmark_fixtures
from scriptorium.html_export import export_html
from scriptorium.models import BBox
from scriptorium.native_pdf import extract_native_pdf_to_ir
from scriptorium.pdf_render import render_pdf
from scriptorium.reading_order import infer_semantic_reading_order
from scriptorium.semantic_quality import compare_semantic_reading_order


def test_two_column_fixture_uses_recursive_xy_cut_semantic_order(tmp_path: Path) -> None:
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
    assert all(
        text_by_value[text].metadata["reading_order_strategy"] == "recursive-xy-cut-v1"
        for text in left_lines + right_lines
    )

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")
    assert 'data-scriptorium-reading-order-strategy="recursive-xy-cut-v1"' in html
    assert 'data-scriptorium-reading-order-region="root/' in html
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


def test_recursive_xy_cut_keeps_section_heading_between_column_regions(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")
    sectioned_pdf = next(path for path in pdfs if path.name == "sectioned_columns.pdf")
    rendered = render_pdf(sectioned_pdf, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))
    semantic_report = compare_semantic_reading_order(document, sectioned_pdf, tmp_path / "semantic")

    assert semantic_report["semantic_order_pair_accuracy"] == 1
    text_elements = [element for element in document.pages[0].elements if element.source_text]
    assert all(element.metadata["reading_order_strategy"] == "recursive-xy-cut-v1" for element in text_elements)

    bboxes = [element.bbox_pdf for element in text_elements]
    column_flow_assignments = infer_semantic_reading_order(
        bboxes,
        document.pages[0].width_pt,
        document.pages[0].height_pt,
        strategy="column-flow-v1",
    )
    column_flow_sequence = [
        text_elements[assignment.item_index].source_text
        for assignment in sorted(column_flow_assignments, key=lambda assignment: assignment.semantic_order)
    ]

    assert column_flow_sequence.index("Methods") < column_flow_sequence.index("Background right one.")
    assert semantic_report["pages"][0]["actual_sequence"].index("Methods") > semantic_report["pages"][0][
        "actual_sequence"
    ].index("Background right two.")


def test_column_flow_detects_academic_columns_from_repeated_left_edges() -> None:
    bboxes = [BBox(x0=120, y0=42, x1=470, y1=62)]
    left_indices: list[int] = []
    right_indices: list[int] = []

    for row in range(18):
        left_indices.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=100 + row * 13, x1=292, y1=110 + row * 13))
        right_indices.append(len(bboxes))
        bboxes.append(BBox(x0=307, y0=100 + row * 13, x1=527, y1=110 + row * 13))
        if row in {3, 8, 13}:
            left_indices.append(len(bboxes))
            bboxes.append(BBox(x0=246, y0=100 + row * 13, x1=286, y1=110 + row * 13))

    assignments = infer_semantic_reading_order(bboxes, page_width=612, page_height=792, strategy="column-flow-v1")
    by_item = {assignment.item_index: assignment for assignment in assignments}

    assert {by_item[index].column_count for index in left_indices + right_indices} == {2}
    assert max(by_item[index].semantic_order for index in left_indices) < min(
        by_item[index].semantic_order for index in right_indices
    )
    assert all(by_item[index].strategy == "column-flow-v1" for index in left_indices + right_indices)


def test_column_flow_tolerates_formula_noise_between_academic_columns() -> None:
    bboxes: list[BBox] = []
    left_indices: list[int] = []
    right_indices: list[int] = []

    for row in range(20):
        left_indices.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=64 + row * 13.5, x1=290, y1=74 + row * 13.5))
        right_x0 = 307 if row % 3 else 318
        right_indices.append(len(bboxes))
        bboxes.append(BBox(x0=right_x0, y0=64 + row * 13.5, x1=526, y1=74 + row * 13.5))

    for row in range(18):
        bboxes.append(BBox(x0=180 + (row % 5) * 23, y0=74 + row * 12.5, x1=203 + (row % 5) * 23, y1=84 + row * 12.5))

    assignments = infer_semantic_reading_order(bboxes, page_width=612, page_height=792, strategy="column-flow-v1")
    by_item = {assignment.item_index: assignment for assignment in assignments}

    assert {by_item[index].column_count for index in left_indices + right_indices} == {2}
    assert max(by_item[index].semantic_order for index in left_indices) < min(
        by_item[index].semantic_order for index in right_indices
    )


def test_column_flow_detects_three_repeated_anchor_columns() -> None:
    bboxes: list[BBox] = [BBox(x0=72, y0=44, x1=520, y1=58)]
    first_column: list[int] = []
    second_column: list[int] = []
    third_column: list[int] = []

    for row in range(12):
        first_column.append(len(bboxes))
        bboxes.append(BBox(x0=52, y0=92 + row * 13, x1=176, y1=102 + row * 13))
        second_column.append(len(bboxes))
        bboxes.append(BBox(x0=224, y0=92 + row * 13, x1=348, y1=102 + row * 13))
        third_column.append(len(bboxes))
        bboxes.append(BBox(x0=396, y0=92 + row * 13, x1=520, y1=102 + row * 13))

    assignments = infer_semantic_reading_order(bboxes, page_width=576, page_height=792, strategy="column-flow-v1")
    by_item = {assignment.item_index: assignment for assignment in assignments}

    assert {by_item[index].column_count for index in first_column + second_column + third_column} == {3}
    assert max(by_item[index].semantic_order for index in first_column) < min(
        by_item[index].semantic_order for index in second_column
    )
    assert max(by_item[index].semantic_order for index in second_column) < min(
        by_item[index].semantic_order for index in third_column
    )
    assert {by_item[index].column_index for index in first_column} == {0}
    assert {by_item[index].column_index for index in second_column} == {1}
    assert {by_item[index].column_index for index in third_column} == {2}


def test_table_grid_guard_allows_strong_mixed_layout_columns() -> None:
    bboxes: list[BBox] = []
    left_indices: list[int] = []
    right_indices: list[int] = []

    for row in range(14):
        left_indices.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=70 + row * 11, x1=218, y1=79 + row * 11))
        bboxes.append(BBox(x0=244, y0=70 + row * 11, x1=264, y1=79 + row * 11))
        bboxes.append(BBox(x0=276, y0=70 + row * 11, x1=292, y1=79 + row * 11))
        right_indices.append(len(bboxes))
        bboxes.append(BBox(x0=307, y0=70 + row * 11, x1=526, y1=79 + row * 11))
    for row in range(14, 22):
        left_indices.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=70 + row * 11, x1=290, y1=79 + row * 11))
        right_indices.append(len(bboxes))
        bboxes.append(BBox(x0=307, y0=70 + row * 11, x1=526, y1=79 + row * 11))

    assignments = infer_semantic_reading_order(bboxes, page_width=612, page_height=792, strategy="column-flow-v1")
    by_item = {assignment.item_index: assignment for assignment in assignments}

    assert {by_item[index].column_count for index in left_indices + right_indices} == {2}
    assert max(by_item[index].semantic_order for index in left_indices) < min(
        by_item[index].semantic_order for index in right_indices
    )


def test_mixed_table_island_keeps_body_columns_and_table_rows() -> None:
    bboxes: list[BBox] = [BBox(x0=72, y0=44, x1=540, y1=60)]
    upper_left: list[int] = []
    upper_right: list[int] = []
    lower_left: list[int] = []
    lower_right: list[int] = []
    table_rows: list[list[int]] = []

    for row in range(5):
        upper_left.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=82 + row * 13, x1=286, y1=92 + row * 13))
        upper_right.append(len(bboxes))
        bboxes.append(BBox(x0=320, y0=82 + row * 13, x1=532, y1=92 + row * 13))

    for row in range(4):
        table_row: list[int] = []
        y0 = 170 + row * 15
        for x0, x1 in [(72, 164), (250, 294), (360, 404), (472, 516)]:
            table_row.append(len(bboxes))
            bboxes.append(BBox(x0=x0, y0=y0, x1=x1, y1=y0 + 10))
        table_rows.append(table_row)

    for row in range(5):
        lower_left.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=260 + row * 13, x1=286, y1=270 + row * 13))
        lower_right.append(len(bboxes))
        bboxes.append(BBox(x0=320, y0=260 + row * 13, x1=532, y1=270 + row * 13))

    assignments = infer_semantic_reading_order(bboxes, page_width=612, page_height=792, strategy="column-flow-v1")
    by_item = {assignment.item_index: assignment for assignment in assignments}
    table_indices = [index for row in table_rows for index in row]

    assert {by_item[index].strategy for index in table_indices} == {"mixed-table-column-flow-v1"}
    assert {by_item[index].column_span for index in table_indices} == {"table-full"}
    assert all(by_item[index].region_path == "root/table-island-001" for index in table_indices)
    assert max(by_item[index].semantic_order for index in upper_left) < min(
        by_item[index].semantic_order for index in upper_right
    )
    assert max(by_item[index].semantic_order for index in upper_right) < min(
        by_item[index].semantic_order for index in table_indices
    )
    assert [by_item[index].semantic_order for index in table_indices] == sorted(
        by_item[index].semantic_order for index in table_indices
    )
    assert max(by_item[index].semantic_order for index in table_indices) < min(
        by_item[index].semantic_order for index in lower_left
    )
    assert max(by_item[index].semantic_order for index in lower_left) < min(
        by_item[index].semantic_order for index in lower_right
    )


def test_column_flow_does_not_treat_sparse_author_grid_as_body_columns() -> None:
    bboxes = [
        BBox(x0=124, y0=72, x1=488, y1=84),
        BBox(x0=212, y0=148, x1=400, y1=166),
        BBox(x0=133, y0=233, x1=204, y1=245),
        BBox(x0=239, y0=233, x1=305, y1=245),
        BBox(x0=339, y0=233, x1=397, y1=245),
        BBox(x0=424, y0=233, x1=497, y1=245),
        BBox(x0=284, y0=386, x1=328, y1=398),
    ]
    for row in range(12):
        bboxes.append(BBox(x0=144, y0=413 + row * 11, x1=468, y1=423 + row * 11))

    assignments = infer_semantic_reading_order(bboxes, page_width=612, page_height=792, strategy="column-flow-v1")

    assert {assignment.column_count for assignment in assignments} == {1}
    assert [assignment.semantic_order for assignment in assignments] == list(range(1, len(bboxes) + 1))


def test_semantic_quality_penalizes_column_order_regression(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")
    columns_pdf = next(path for path in pdfs if path.name == "two_column_notes.pdf")
    rendered = render_pdf(columns_pdf, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))

    baseline = compare_semantic_reading_order(document, columns_pdf, tmp_path / "semantic-baseline")
    assert baseline["semantic_order_pair_accuracy"] == 1

    text_by_value = {element.source_text: element for element in document.pages[0].elements if element.source_text}
    text_by_value["Right column paragraph one."].reading_order = 2
    text_by_value["Left column paragraph one."].reading_order = 6

    regressed = compare_semantic_reading_order(document, columns_pdf, tmp_path / "semantic-regressed")
    assert regressed["semantic_order_pair_accuracy"] < 1
    assert regressed["semantic_sequence_similarity"] < 1
