from pathlib import Path

from scriptorium.annotations import annotate_document
from scriptorium.benchmark_fixtures import create_benchmark_fixtures
from scriptorium.html_export import export_html
from scriptorium.models import BBox
from scriptorium.native_pdf import extract_native_pdf_to_ir
from scriptorium.pdf_render import render_pdf
from scriptorium.reading_order import infer_box_flow_order, infer_semantic_reading_order, pairwise_order_disagreement
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
    assert all(
        text_by_value[text].metadata["reading_order_confidence"] >= 0.8
        for text in left_lines + right_lines
    )
    assert all(
        "recursive-xy-cut" in text_by_value[text].metadata["reading_order_evidence"]
        for text in left_lines + right_lines
    )

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")
    assert 'data-scriptorium-reading-order-strategy="recursive-xy-cut-v1"' in html
    assert 'data-scriptorium-reading-order-region="root/' in html
    assert 'data-scriptorium-reading-order-confidence="0.83"' in html
    assert 'data-scriptorium-reading-order-evidence="recursive-xy-cut' in html
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
    assert all(text_by_value[text].metadata["reading_order_strategy"] == "table-row-major-v1" for text in row_major)
    assert all("table-row-major" in text_by_value[text].metadata["reading_order_evidence"] for text in row_major)
    assert all("table-grid-slots" in text_by_value[text].metadata["reading_order_evidence"] for text in row_major)
    assert all(text_by_value[text].metadata["reading_order_confidence"] >= 0.8 for text in row_major)


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
    assert all(by_item[index].confidence >= 0.75 for index in left_indices + right_indices)
    assert all("column-flow" in by_item[index].evidence for index in left_indices + right_indices)
    assert all("repeated-left-edge" in by_item[index].evidence for index in left_indices + right_indices)


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
    assert all(by_item[index].scope == "body" for index in first_column + second_column + third_column)


def test_spatial_graph_orders_overlapping_weak_columns() -> None:
    bboxes: list[BBox] = []
    left_indices: list[int] = []
    right_indices: list[int] = []

    for row in range(8):
        left_indices.append(len(bboxes))
        left_x0 = 72 + (row % 4) * 24
        bboxes.append(BBox(x0=left_x0, y0=90 + row * 15, x1=left_x0 + 288, y1=101 + row * 15))
        right_indices.append(len(bboxes))
        right_x0 = 252 + (row % 4) * 24
        bboxes.append(BBox(x0=right_x0, y0=90 + row * 15, x1=right_x0 + 288, y1=101 + row * 15))

    assignments = infer_semantic_reading_order(bboxes, page_width=612, page_height=792, strategy="column-flow-v1")
    by_item = {assignment.item_index: assignment for assignment in assignments}

    assert {by_item[index].strategy for index in left_indices + right_indices} == {"spatial-graph-v1"}
    assert {by_item[index].column_count for index in left_indices + right_indices} == {2}
    assert max(by_item[index].semantic_order for index in left_indices) < min(
        by_item[index].semantic_order for index in right_indices
    )
    assert {by_item[index].column_index for index in left_indices} == {0}
    assert {by_item[index].column_index for index in right_indices} == {1}
    assert all("spatial-graph" in by_item[index].evidence for index in left_indices + right_indices)
    assert all("horizontal-overlap-chain" in by_item[index].evidence for index in left_indices + right_indices)


def test_box_flow_candidate_exposes_horizontal_vs_vertical_ordering() -> None:
    bboxes: list[BBox] = []
    left_indices: list[int] = []
    right_indices: list[int] = []

    for row in range(6):
        left_indices.append(len(bboxes))
        bboxes.append(BBox(x0=60, y0=70 + row * 18, x1=240, y1=80 + row * 18))
        right_indices.append(len(bboxes))
        bboxes.append(BBox(x0=320, y0=70 + row * 18, x1=500, y1=80 + row * 18))

    vertical_order = infer_box_flow_order(bboxes, page_width=612, page_height=792, boxes_flow=0.75)
    column_biased_order = infer_box_flow_order(bboxes, page_width=612, page_height=792, boxes_flow=-0.75)
    disagreement = pairwise_order_disagreement(vertical_order, column_biased_order)

    assert vertical_order == list(range(len(bboxes)))
    assert column_biased_order == [*left_indices, *right_indices]
    assert disagreement.pair_count == 66
    assert disagreement.disagreement_count > 0
    assert disagreement.disagreement_ratio > 0.2


def test_sidebar_notes_do_not_become_body_columns() -> None:
    bboxes: list[BBox] = [BBox(x0=72, y0=44, x1=526, y1=60)]
    left_indices: list[int] = []
    right_indices: list[int] = []
    sidebar_indices: list[int] = []

    for row in range(10):
        left_indices.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=92 + row * 14, x1=286, y1=103 + row * 14))
        right_indices.append(len(bboxes))
        bboxes.append(BBox(x0=318, y0=92 + row * 14, x1=526, y1=103 + row * 14))

    for row in range(4):
        sidebar_indices.append(len(bboxes))
        bboxes.append(BBox(x0=588, y0=112 + row * 18, x1=660, y1=124 + row * 18))

    assignments = infer_semantic_reading_order(bboxes, page_width=720, page_height=792, strategy="column-flow-v1")
    by_item = {assignment.item_index: assignment for assignment in assignments}

    assert {by_item[index].column_count for index in left_indices + right_indices} == {2}
    assert max(by_item[index].semantic_order for index in left_indices) < min(
        by_item[index].semantic_order for index in right_indices
    )
    assert max(by_item[index].semantic_order for index in right_indices) < min(
        by_item[index].semantic_order for index in sidebar_indices
    )
    assert {by_item[index].scope for index in sidebar_indices} == {"sidebar"}
    assert {by_item[index].sidebar_type for index in sidebar_indices} == {"right"}
    assert {by_item[index].column_span for index in sidebar_indices} == {"sidebar-right"}
    assert {by_item[index].column_index for index in sidebar_indices} == {None}
    assert {by_item[index].strategy for index in sidebar_indices} == {"sidebar-aware-column-flow-v1"}
    assert all("sidebar-secondary-flow" in by_item[index].evidence for index in sidebar_indices)
    assert all("marginalia-outside-print-space" in by_item[index].evidence for index in sidebar_indices)


def test_footnotes_do_not_interrupt_multicolumn_body_flow() -> None:
    bboxes: list[BBox] = [BBox(x0=72, y0=44, x1=540, y1=60)]
    left_indices: list[int] = []
    right_indices: list[int] = []
    footnote_indices: list[int] = []

    for row in range(10):
        left_indices.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=92 + row * 14, x1=286, y1=103 + row * 14))
        right_indices.append(len(bboxes))
        bboxes.append(BBox(x0=320, y0=92 + row * 14, x1=532, y1=103 + row * 14))

    for row in range(2):
        footnote_indices.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=672 + row * 10, x1=274, y1=679 + row * 10))

    footer_index = len(bboxes)
    bboxes.append(BBox(x0=298, y0=758, x1=314, y1=768))

    assignments = infer_semantic_reading_order(bboxes, page_width=612, page_height=792, strategy="column-flow-v1")
    by_item = {assignment.item_index: assignment for assignment in assignments}

    assert max(by_item[index].semantic_order for index in left_indices) < min(
        by_item[index].semantic_order for index in right_indices
    )
    assert max(by_item[index].semantic_order for index in right_indices) < min(
        by_item[index].semantic_order for index in footnote_indices
    )
    assert max(by_item[index].semantic_order for index in footnote_indices) < by_item[footer_index].semantic_order
    assert {by_item[index].scope for index in footnote_indices} == {"footnote"}
    assert {by_item[index].column_span for index in footnote_indices} == {"footnote"}
    assert {by_item[index].column_index for index in footnote_indices} == {None}
    assert {by_item[index].strategy for index in footnote_indices} == {"marginal-footnote-aware-column-flow-v1"}
    assert all("footnote-secondary-flow" in by_item[index].evidence for index in footnote_indices)
    assert all("bottom-note-zone" in by_item[index].evidence for index in footnote_indices)


def test_column_flow_keeps_running_margins_outside_body_columns() -> None:
    bboxes: list[BBox] = [
        BBox(x0=248, y0=16, x1=364, y1=27),
        BBox(x0=72, y0=58, x1=540, y1=74),
    ]
    header_index = 0
    title_index = 1
    left_indices: list[int] = []
    right_indices: list[int] = []

    for row in range(8):
        left_indices.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=112 + row * 14, x1=286, y1=122 + row * 14))
        right_indices.append(len(bboxes))
        bboxes.append(BBox(x0=320, y0=112 + row * 14, x1=532, y1=122 + row * 14))
    footer_index = len(bboxes)
    bboxes.append(BBox(x0=296, y0=758, x1=316, y1=770))

    assignments = infer_semantic_reading_order(bboxes, page_width=612, page_height=792, strategy="column-flow-v1")
    by_item = {assignment.item_index: assignment for assignment in assignments}

    assert by_item[header_index].artifact_type == "header"
    assert by_item[header_index].column_span == "artifact-header"
    assert by_item[header_index].confidence == 0.84
    assert "page-edge-artifact" in by_item[header_index].evidence
    assert "header-margin" in by_item[header_index].evidence
    assert by_item[footer_index].artifact_type == "footer"
    assert by_item[footer_index].column_span == "artifact-footer"
    assert by_item[footer_index].confidence == 0.84
    assert "footer-margin" in by_item[footer_index].evidence
    assert by_item[header_index].semantic_order < by_item[title_index].semantic_order
    assert max(by_item[index].semantic_order for index in left_indices) < min(
        by_item[index].semantic_order for index in right_indices
    )
    assert by_item[footer_index].semantic_order > max(by_item[index].semantic_order for index in right_indices)
    assert {by_item[index].strategy for index in [header_index, *left_indices, *right_indices, footer_index]} == {
        "marginal-aware-column-flow-v1"
    }


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
    assert all(by_item[index].confidence >= 0.8 for index in table_indices)
    assert all("table-island-row-major" in by_item[index].evidence for index in table_indices)
    assert all("table-grid-slots" in by_item[index].evidence for index in table_indices)
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


def test_formula_fragments_do_not_become_table_islands() -> None:
    bboxes: list[BBox] = []
    left_indices: list[int] = []
    right_indices: list[int] = []

    for row in range(8):
        left_indices.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=110 + row * 13.5, x1=290, y1=122 + row * 13.5))
        right_indices.append(len(bboxes))
        bboxes.append(BBox(x0=307, y0=110 + row * 13.5, x1=525, y1=122 + row * 13.5))

    for y0 in [252, 266, 280]:
        bboxes.append(BBox(x0=72, y0=y0, x1=290, y1=y0 + 12))
        for x0, x1 in [
            (314, 335),
            (321, 345),
            (351, 386),
            (387, 419),
            (405, 419),
            (425, 456),
            (426, 454),
            (467, 498),
            (484, 515),
        ]:
            bboxes.append(BBox(x0=x0, y0=y0 + 1, x1=x1, y1=y0 + 12))

    for row in range(8):
        left_indices.append(len(bboxes))
        bboxes.append(BBox(x0=72, y0=350 + row * 13.5, x1=290, y1=362 + row * 13.5))
        right_indices.append(len(bboxes))
        bboxes.append(BBox(x0=307, y0=350 + row * 13.5, x1=525, y1=362 + row * 13.5))

    assignments = infer_semantic_reading_order(bboxes, page_width=612, page_height=792, strategy="column-flow-v1")
    by_item = {assignment.item_index: assignment for assignment in assignments}

    assert all("mixed-table-column-flow-v1" not in assignment.strategy for assignment in assignments)
    assert max(by_item[index].semantic_order for index in left_indices) < min(
        by_item[index].semantic_order for index in right_indices
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
