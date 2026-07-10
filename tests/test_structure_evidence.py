from scriptorium.annotations import annotate_document
from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.structure_evidence import (
    apply_structure_evidence,
    normalize_structure_evidence,
    normalize_structure_relations,
    normalize_structure_streams,
)


def test_pp_structure_block_order_can_reorder_native_lines() -> None:
    document = _document_with_text_boxes(
        [
            ("left-one", "Left column one.", BBox(x0=10, y0=10, x1=70, y1=20), 1),
            ("right-one", "Right column one.", BBox(x0=110, y0=10, x1=170, y1=20), 2),
            ("left-two", "Left column two.", BBox(x0=10, y0=30, x1=70, y1=40), 3),
            ("right-two", "Right column two.", BBox(x0=110, y0=30, x1=170, y1=40), 4),
        ]
    )
    payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 0,
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_bbox": [16, 16, 180, 90],
                    "block_order": 1,
                    "block_content": "Left column one. Left column two.",
                    "confidence": 0.94,
                },
                {
                    "block_label": "text",
                    "block_bbox": [216, 16, 380, 90],
                    "block_order": 2,
                    "block_content": "Right column one. Right column two.",
                    "confidence": 0.93,
                },
            ],
        },
    }

    apply_structure_evidence(document, payload)

    ordered_text = [
        element.source_text
        for element in sorted(document.pages[0].elements, key=lambda item: item.reading_order)
        if element.source_text
    ]
    assert ordered_text == [
        "Left column one.",
        "Left column two.",
        "Right column one.",
        "Right column two.",
    ]
    assert document.metadata["structure_evidence"]["matched_element_count"] == 4
    assert document.metadata["structure_evidence"]["reordered_page_count"] == 1
    assert document.metadata["structure_evidence"]["relation_reordered_page_count"] == 0
    assert document.metadata["structure_evidence"]["order_reordered_page_count"] == 1
    assert document.pages[0].elements[0].metadata["reading_order_strategy"] == "external-structure-fusion-v1"
    assert document.pages[0].elements[0].metadata["structure_evidence"]["source"] == "pp-structurev3"


def test_structure_parsing_list_order_can_reorder_when_block_order_is_absent() -> None:
    document = _document_with_text_boxes(
        [
            ("left-one", "Left column one.", BBox(x0=10, y0=10, x1=70, y1=20), 1),
            ("right-one", "Right column one.", BBox(x0=110, y0=10, x1=170, y1=20), 2),
            ("left-two", "Left column two.", BBox(x0=10, y0=30, x1=70, y1=40), 3),
            ("right-two", "Right column two.", BBox(x0=110, y0=30, x1=170, y1=40), 4),
        ]
    )
    payload = {
        "source": "paddleocr-vl",
        "res": {
            "page_index": 0,
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_bbox": [20, 20, 140, 80],
                    "block_content": "Left column one. Left column two.",
                    "confidence": 0.91,
                },
                {
                    "block_label": "text",
                    "block_bbox": [220, 20, 340, 80],
                    "block_content": "Right column one. Right column two.",
                    "confidence": 0.90,
                },
            ],
        },
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)

    ordered_text = [
        element.source_text
        for element in sorted(document.pages[0].elements, key=lambda item: item.reading_order)
        if element.source_text
    ]
    assert [region.order for region in regions] == [1, 2]
    assert {region.order_source for region in regions} == {"implicit-list"}
    assert ordered_text == [
        "Left column one.",
        "Left column two.",
        "Right column one.",
        "Right column two.",
    ]
    assert document.metadata["structure_evidence"]["order_source_counts"] == {"implicit-list": 2}
    assert document.metadata["structure_evidence"]["reordered_page_count"] == 1
    assert document.metadata["structure_evidence"]["relation_reordered_page_count"] == 0
    assert document.metadata["structure_evidence"]["order_reordered_page_count"] == 1
    assert document.pages[0].elements[0].metadata["external_structure_order_source"] == "implicit-list"


def test_layout_detection_boxes_do_not_create_implicit_reading_order() -> None:
    document = _document_with_text_boxes(
        [
            ("left-one", "Left column one.", BBox(x0=10, y0=10, x1=70, y1=20), 1),
            ("right-one", "Right column one.", BBox(x0=110, y0=10, x1=170, y1=20), 2),
            ("left-two", "Left column two.", BBox(x0=10, y0=30, x1=70, y1=40), 3),
            ("right-two", "Right column two.", BBox(x0=110, y0=30, x1=170, y1=40), 4),
        ]
    )
    payload = {
        "source": "layout-detector",
        "res": {
            "page_index": 0,
            "layout_det_res": {
                "boxes": [
                    {
                        "label": "text",
                        "coordinate": [20, 20, 140, 80],
                        "text": "Left column one. Left column two.",
                    },
                    {
                        "label": "text",
                        "coordinate": [220, 20, 340, 80],
                        "text": "Right column one. Right column two.",
                    },
                ]
            },
        },
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)

    assert [region.order for region in regions] == [None, None]
    assert {region.order_source for region in regions} == {None}
    assert document.metadata["structure_evidence"]["order_source_counts"] == {"none": 2}
    assert document.metadata["structure_evidence"]["reordered_page_count"] == 0
    assert document.metadata["structure_evidence"]["relation_reordered_page_count"] == 0
    assert document.metadata["structure_evidence"]["order_reordered_page_count"] == 0
    assert "external_structure_order" not in document.pages[0].elements[0].metadata


def test_structure_relation_edges_attach_to_matched_elements() -> None:
    document = _document_with_text_boxes(
        [
            ("a", "A", BBox(x0=10, y0=10, x1=40, y1=20), 1),
            ("c", "C", BBox(x0=110, y0=10, x1=140, y1=20), 2),
            ("b", "B", BBox(x0=10, y0=30, x1=40, y1=40), 3),
            ("d", "D", BBox(x0=110, y0=30, x1=140, y1=40), 4),
        ]
    )
    boxes = [
        {"id": element.id, "label": "text", "bbox": element.bbox_px.as_list(), "text": element.source_text}
        for element in document.pages[0].elements
    ]
    payload = {
        "source": "relation-model",
        "res": {
            "page_index": 0,
            "layout_det_res": {"boxes": boxes},
            "successor_edges": [
                {"source": "a", "target": "b"},
                {"source": "c", "target": "d"},
            ],
            "precedence_edges": [["b", "d"]],
        },
    }

    relations = normalize_structure_relations(payload, document)
    apply_structure_evidence(document, payload)
    by_id = {element.id: element for element in document.pages[0].elements}

    assert [(edge.kind, edge.source_ref, edge.target_ref) for edge in relations] == [
        ("successor", "a", "b"),
        ("successor", "c", "d"),
        ("precedence", "b", "d"),
    ]
    assert document.metadata["structure_evidence"]["relation_edge_count"] == 3
    assert document.metadata["structure_evidence"]["resolved_relation_edge_count"] == 3
    assert document.metadata["structure_evidence"]["relation_stream_count"] == 2
    assert document.metadata["structure_evidence"]["resolved_relation_stream_member_count"] == 4
    assert document.metadata["structure_evidence"]["relation_stream_conflict_count"] == 0
    assert document.metadata["structure_evidence"]["reordered_page_count"] == 1
    assert document.metadata["structure_evidence"]["relation_reordered_page_count"] == 1
    assert document.metadata["structure_evidence"]["order_reordered_page_count"] == 0
    assert [
        element.id
        for element in sorted(document.pages[0].elements, key=lambda item: item.reading_order)
        if element.source_text
    ] == ["a", "b", "c", "d"]
    assert by_id["a"].metadata["external_structure_successor_ids"] == ["b"]
    assert by_id["c"].metadata["external_structure_successor_ids"] == ["d"]
    assert by_id["b"].metadata["external_structure_precedence_target_ids"] == ["d"]
    assert by_id["a"].metadata["reading_order_strategy"] == "external-structure-relation-fusion-v1"
    assert "external-structure-relation" in by_id["a"].metadata["reading_order_evidence"]
    assert by_id["a"].metadata["reading_order_stream_id"] == "external-relation-body-001-001"
    assert by_id["b"].metadata["reading_order_stream_id"] == "external-relation-body-001-001"
    assert by_id["c"].metadata["reading_order_stream_id"] == "external-relation-body-001-002"
    assert by_id["d"].metadata["reading_order_stream_id"] == "external-relation-body-001-002"
    assert "external-structure-relation-stream" in by_id["a"].metadata["reading_order_evidence"]
    assert "external_structure_order" not in by_id["a"].metadata


def test_roor_document_relations_drive_order_without_implicit_block_order() -> None:
    document = _document_with_text_boxes(
        [
            ("a", "A", BBox(x0=10, y0=10, x1=40, y1=20), 1),
            ("c", "C", BBox(x0=110, y0=10, x1=140, y1=20), 2),
            ("b", "B", BBox(x0=10, y0=30, x1=40, y1=40), 3),
            ("d", "D", BBox(x0=110, y0=30, x1=140, y1=40), 4),
        ]
    )
    payload = {
        "source": "roor",
        "page_index": 0,
        "document": [
            {"id": 0, "box": [20, 20, 80, 40], "text": "A"},
            {"id": 1, "box": [220, 20, 280, 40], "text": "C"},
            {"id": 2, "box": [20, 60, 80, 80], "text": "B"},
            {"id": 3, "box": [220, 60, 280, 80], "text": "D"},
        ],
        "ro_linkings": [[0, 2], [2, 1], [1, 3]],
    }

    regions = normalize_structure_evidence(payload, document)
    relations = normalize_structure_relations(payload, document)
    apply_structure_evidence(document, payload)
    by_id = {element.id: element for element in document.pages[0].elements}

    assert [(region.label, region.order, region.order_source) for region in regions] == [
        ("text", None, None),
        ("text", None, None),
        ("text", None, None),
        ("text", None, None),
    ]
    assert [(edge.kind, edge.source_ref, edge.target_ref) for edge in relations] == [
        ("successor", "0", "2"),
        ("successor", "2", "1"),
        ("successor", "1", "3"),
    ]
    assert [
        element.source_text
        for element in sorted(document.pages[0].elements, key=lambda item: item.reading_order)
        if element.source_text
    ] == ["A", "B", "C", "D"]
    assert document.metadata["structure_evidence"]["order_source_counts"] == {"none": 4}
    assert document.metadata["structure_evidence"]["relation_edge_count"] == 3
    assert document.metadata["structure_evidence"]["resolved_relation_edge_count"] == 3
    assert document.metadata["structure_evidence"]["relation_stream_count"] == 1
    assert document.metadata["structure_evidence"]["resolved_relation_stream_member_count"] == 4
    assert document.metadata["structure_evidence"]["relation_stream_conflict_count"] == 0
    assert document.metadata["structure_evidence"]["relation_reordered_page_count"] == 1
    assert "external_structure_order" not in by_id["a"].metadata
    assert by_id["a"].metadata["external_structure_successor_ids"] == ["b"]
    assert {element.metadata["reading_order_stream_id"] for element in by_id.values()} == {
        "external-relation-body-001-001"
    }


def test_structure_reading_streams_attach_to_elements_without_regions() -> None:
    document = _document_with_text_boxes(
        [
            ("a", "A", BBox(x0=10, y0=10, x1=40, y1=20), 1),
            ("c", "C", BBox(x0=110, y0=10, x1=140, y1=20), 2),
            ("b", "B", BBox(x0=10, y0=30, x1=40, y1=40), 3),
            ("d", "D", BBox(x0=110, y0=30, x1=140, y1=40), 4),
        ]
    )
    payload = {
        "source": "stream-model",
        "res": {
            "page_index": 0,
            "reading_streams": [
                {"id": "hero-grid", "stream_type": "product_grid", "text_sequence": ["A", "B"]},
                {"id": "right-rail", "stream_type": "right_sidebar", "text_sequence": ["C", "D"]},
            ],
        },
    }

    streams = normalize_structure_streams(payload, document)
    apply_structure_evidence(document, payload)
    by_id = {element.id: element for element in document.pages[0].elements}

    assert [(stream.stream_id, stream.stream_type, list(stream.member_refs)) for stream in streams] == [
        ("hero-grid", "grid-island", ["A", "B"]),
        ("right-rail", "sidebar-right", ["C", "D"]),
    ]
    assert document.metadata["structure_evidence"]["region_count"] == 0
    assert document.metadata["structure_evidence"]["stream_count"] == 2
    assert document.metadata["structure_evidence"]["resolved_stream_member_count"] == 4
    assert document.metadata["structure_evidence"]["stream_conflict_count"] == 0
    assert document.metadata["structure_evidence"]["relation_edge_count"] == 2
    assert document.metadata["structure_evidence"]["resolved_relation_edge_count"] == 2
    assert document.metadata["structure_evidence"]["relation_stream_count"] == 0
    assert document.metadata["structure_evidence"]["resolved_relation_stream_member_count"] == 0
    assert document.metadata["structure_evidence"]["relation_stream_conflict_count"] == 0
    assert document.metadata["structure_evidence"]["relation_reordered_page_count"] == 1
    assert by_id["a"].metadata["reading_order_stream_id"] == "hero-grid"
    assert by_id["a"].metadata["reading_order_stream_type"] == "grid-island"
    assert by_id["b"].metadata["reading_order_stream_index"] == 2
    assert by_id["c"].metadata["reading_order_stream_id"] == "right-rail"
    assert by_id["c"].metadata["reading_order_scope"] == "sidebar"
    assert by_id["c"].metadata["reading_order_sidebar_type"] == "right"
    assert "external-structure-stream" in by_id["a"].metadata["reading_order_evidence"]


def test_paddle_nested_structure_label_feeds_annotation_role() -> None:
    document = _document_with_text_boxes(
        [
            ("formula", "E = mc2", BBox(x0=40, y0=50, x1=95, y1=65), 1),
        ]
    )
    payload = {
        "raw_results": [
            {
                "res": {
                    "page_index": None,
                    "parsing_res_list": [
                        {
                            "block_label": "formula",
                            "block_bbox": [[78, 98], [192, 98], [192, 132], [78, 132]],
                            "block_order": 1,
                            "block_content": "E = mc2",
                        }
                    ],
                }
            }
        ]
    }

    regions = normalize_structure_evidence(payload, document, source="paddleocr-vl")
    apply_structure_evidence(document, payload, source="paddleocr-vl")
    annotate_document(document)

    assert len(regions) == 1
    assert regions[0].bbox_pdf.as_list() == [39.0, 49.0, 96.0, 66.0]
    assert document.pages[0].elements[0].metadata["role"] == "formula"
    assert document.pages[0].elements[0].metadata["annotation"]["role"] == "formula"
    assert document.metadata["structure_evidence"]["source"] == "paddleocr-vl"


def test_docling_body_tree_order_can_reorder_native_lines() -> None:
    document = _document_with_text_boxes(
        [
            ("left-one", "Left column one.", BBox(x0=10, y0=10, x1=70, y1=20), 1),
            ("right-one", "Right column one.", BBox(x0=110, y0=10, x1=170, y1=20), 2),
            ("left-two", "Left column two.", BBox(x0=10, y0=30, x1=70, y1=40), 3),
            ("right-two", "Right column two.", BBox(x0=110, y0=30, x1=170, y1=40), 4),
        ]
    )
    payload = {
        "schema_name": "DoclingDocument",
        "body": {"self_ref": "#/body", "children": [{"$ref": "#/groups/0"}]},
        "groups": [
            {
                "self_ref": "#/groups/0",
                "label": "section",
                "children": [
                    {"$ref": "#/texts/0"},
                    {"$ref": "#/texts/2"},
                    {"$ref": "#/texts/1"},
                    {"$ref": "#/texts/3"},
                ],
            }
        ],
        "texts": [
            {
                "self_ref": "#/texts/0",
                "label": "text",
                "text": "Left column one.",
                "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 10, "r": 70, "b": 20, "coord_origin": "TOPLEFT"}}],
            },
            {
                "self_ref": "#/texts/1",
                "label": "text",
                "text": "Right column one.",
                "prov": [{"page_no": 1, "bbox": {"l": 110, "t": 10, "r": 170, "b": 20, "coord_origin": "TOPLEFT"}}],
            },
            {
                "self_ref": "#/texts/2",
                "label": "text",
                "text": "Left column two.",
                "prov": [
                    {"page_no": 1, "bbox": {"l": 10, "t": 170, "r": 70, "b": 160, "coord_origin": "BOTTOMLEFT"}}
                ],
            },
            {
                "self_ref": "#/texts/3",
                "label": "text",
                "text": "Right column two.",
                "prov": [{"page_no": 1, "bbox": {"l": 110, "t": 30, "r": 170, "b": 40, "coord_origin": "TOPLEFT"}}],
            },
        ],
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)

    assert [region.order for region in regions] == [1, 2, 3, 4]
    assert regions[1].bbox_pdf.as_list() == [10.0, 30.0, 70.0, 40.0]
    assert {region.source for region in regions} == {"docling"}
    ordered_text = [
        element.source_text
        for element in sorted(document.pages[0].elements, key=lambda item: item.reading_order)
        if element.source_text
    ]
    assert ordered_text == [
        "Left column one.",
        "Left column two.",
        "Right column one.",
        "Right column two.",
    ]
    assert document.metadata["structure_evidence"]["region_count"] == 4
    assert document.metadata["structure_evidence"]["matched_element_count"] == 4
    assert document.metadata["structure_evidence"]["reordered_page_count"] == 1
    assert document.metadata["structure_evidence"]["relation_reordered_page_count"] == 0
    assert document.metadata["structure_evidence"]["order_reordered_page_count"] == 1
    assert document.pages[0].elements[2].metadata["external_structure_order"] == 2
    assert document.pages[0].elements[2].metadata["structure_evidence"]["source"] == "docling"


def test_docling_furniture_tree_feeds_page_artifact_streams() -> None:
    document = _document_with_text_boxes(
        [
            ("header", "Quarterly Report", BBox(x0=10, y0=8, x1=150, y1=18), 1),
            ("body", "Body paragraph.", BBox(x0=10, y0=50, x1=130, y1=64), 2),
            ("footer", "Page 1", BBox(x0=10, y0=180, x1=60, y1=192), 3),
        ],
    )
    payload = {
        "schema_name": "DoclingDocument",
        "body": {"self_ref": "#/body", "children": [{"$ref": "#/texts/1"}]},
        "furniture": {"self_ref": "#/furniture", "children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/2"}]},
        "texts": [
            {
                "self_ref": "#/texts/0",
                "label": "page_header",
                "text": "Quarterly Report",
                "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 8, "r": 150, "b": 18, "coord_origin": "TOPLEFT"}}],
            },
            {
                "self_ref": "#/texts/1",
                "label": "text",
                "text": "Body paragraph.",
                "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 50, "r": 130, "b": 64, "coord_origin": "TOPLEFT"}}],
            },
            {
                "self_ref": "#/texts/2",
                "label": "page_footer",
                "text": "Page 1",
                "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 180, "r": 60, "b": 192, "coord_origin": "TOPLEFT"}}],
            },
        ],
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)
    annotate_document(document)
    by_id = {element.id: element for element in document.pages[0].elements}

    assert [(region.label, region.order, region.order_source) for region in regions] == [
        ("text", 1, "docling-body"),
        ("page_header", None, "docling-furniture"),
        ("page_footer", None, "docling-furniture"),
    ]
    assert document.metadata["structure_evidence"]["region_count"] == 3
    assert document.metadata["structure_evidence"]["matched_element_count"] == 3
    assert document.metadata["structure_evidence"]["order_source_counts"] == {
        "docling-body": 1,
        "docling-furniture": 2,
    }
    assert by_id["header"].metadata["reading_order_scope"] == "page-artifact"
    assert by_id["header"].metadata["reading_order_artifact_type"] == "header"
    assert by_id["header"].metadata["reading_order_stream_id"] == "page-artifact-header"
    assert by_id["footer"].metadata["reading_order_scope"] == "page-artifact"
    assert by_id["footer"].metadata["reading_order_artifact_type"] == "footer"
    assert by_id["footer"].metadata["reading_order_stream_id"] == "page-artifact-footer"
    assert by_id["body"].metadata["external_structure_order"] == 1
    assert by_id["body"].metadata["reading_order_stream_id"] == "body-main"


def test_docling_table_cells_drive_row_major_table_order() -> None:
    document = _document_with_text_boxes(
        [
            ("b", "B", BBox(x0=60, y0=50, x1=95, y1=62), 1),
            ("a", "A", BBox(x0=20, y0=50, x1=55, y1=62), 2),
            ("d", "D", BBox(x0=60, y0=70, x1=95, y1=82), 3),
            ("c", "C", BBox(x0=20, y0=70, x1=55, y1=82), 4),
        ]
    )
    payload = {
        "schema_name": "DoclingDocument",
        "body": {"self_ref": "#/body", "children": [{"$ref": "#/tables/0"}]},
        "tables": [
            {
                "self_ref": "#/tables/0",
                "label": "table",
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {"l": 18, "t": 48, "r": 98, "b": 84, "coord_origin": "TOPLEFT"},
                    }
                ],
                "data": {
                    "num_rows": 2,
                    "num_cols": 2,
                    "table_cells": [
                        _docling_cell("A", 0, 0, 20, 50, 55, 62),
                        _docling_cell("B", 0, 1, 60, 50, 95, 62),
                        _docling_cell("C", 1, 0, 20, 70, 55, 82),
                        _docling_cell("D", 1, 1, 60, 70, 95, 82),
                    ],
                },
            }
        ],
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)
    annotate_document(document)
    by_id = {element.id: element for element in document.pages[0].elements}
    ordered_text = [
        element.source_text
        for element in sorted(document.pages[0].elements, key=lambda item: item.reading_order)
        if element.source_text
    ]

    assert [(region.label, region.order, region.order_source) for region in regions] == [
        ("table", 1, "docling-body"),
        ("table_cell", 1, "docling-table-cell"),
        ("table_cell", 1, "docling-table-cell"),
        ("table_cell", 1, "docling-table-cell"),
        ("table_cell", 1, "docling-table-cell"),
    ]
    assert document.metadata["structure_evidence"]["region_count"] == 5
    assert document.metadata["structure_evidence"]["matched_element_count"] == 4
    assert document.metadata["structure_evidence"]["order_source_counts"] == {
        "docling-body": 1,
        "docling-table-cell": 4,
    }
    assert ordered_text == ["A", "B", "C", "D"]
    assert by_id["a"].metadata["external_structure_label"] == "table_cell"
    assert by_id["a"].metadata["external_structure_order_subindex"] == 1
    assert by_id["b"].metadata["external_structure_order_subindex"] == 2
    assert by_id["c"].metadata["external_structure_order_subindex"] == 3
    assert by_id["d"].metadata["external_structure_order_subindex"] == 4
    assert by_id["a"].metadata["external_structure_table_cell_row"] == 0
    assert by_id["a"].metadata["external_structure_table_cell_col"] == 0
    assert by_id["b"].metadata["external_structure_table_cell_col"] == 1
    assert by_id["a"].metadata["reading_order_stream_type"] == "table-island"
    assert by_id["a"].metadata["role"] == "table-cell-text"


def test_pp_structure_table_res_cells_inherit_parent_table_order() -> None:
    document = _document_with_text_boxes(
        [
            ("intro", "Intro", BBox(x0=20, y0=24, x1=62, y1=36), 1),
            ("b", "B", BBox(x0=60, y0=70, x1=95, y1=82), 2),
            ("a", "A", BBox(x0=20, y0=70, x1=55, y1=82), 3),
            ("d", "D", BBox(x0=60, y0=90, x1=95, y1=102), 4),
            ("c", "C", BBox(x0=20, y0=90, x1=55, y1=102), 5),
        ]
    )
    payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 0,
            "parsing_res_list": [
                _pp_region("intro", "text", "Intro", 1, document),
                {
                    "block_id": "table-1",
                    "block_label": "table",
                    "block_bbox": [36, 132, 200, 208],
                    "block_order": 2,
                    "block_content": "A B C D",
                    "confidence": 0.90,
                },
            ],
            "table_res_list": [
                {
                    "table_region_id": "table-1",
                    "cell_box_list": [
                        [40, 140, 110, 164],
                        [120, 140, 190, 164],
                        [40, 180, 110, 204],
                        [120, 180, 190, 204],
                    ],
                    "table_ocr_pred": {
                        "rec_texts": ["A", "B", "C", "D"],
                        "rec_scores": [0.96, 0.95, 0.94, 0.93],
                    },
                }
            ],
        },
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)
    annotate_document(document)
    by_id = {element.id: element for element in document.pages[0].elements}
    ordered_text = [
        element.source_text
        for element in sorted(document.pages[0].elements, key=lambda item: item.reading_order)
        if element.source_text
    ]

    assert [(region.label, region.order, region.order_source) for region in regions] == [
        ("text", 1, "explicit"),
        ("table", 2, "explicit"),
        ("table_cell", 2, "paddle-table-cell"),
        ("table_cell", 2, "paddle-table-cell"),
        ("table_cell", 2, "paddle-table-cell"),
        ("table_cell", 2, "paddle-table-cell"),
    ]
    assert document.metadata["structure_evidence"]["matched_element_count"] == 5
    assert document.metadata["structure_evidence"]["order_source_counts"] == {
        "explicit": 2,
        "paddle-table-cell": 4,
    }
    assert ordered_text == ["Intro", "A", "B", "C", "D"]
    assert by_id["a"].metadata["external_structure_label"] == "table_cell"
    assert by_id["a"].metadata["external_structure_order"] == 2
    assert by_id["a"].metadata["external_structure_order_subindex"] == 1
    assert by_id["b"].metadata["external_structure_order_subindex"] == 2
    assert by_id["c"].metadata["external_structure_order_subindex"] == 3
    assert by_id["d"].metadata["external_structure_order_subindex"] == 4
    assert by_id["a"].metadata["external_structure_table_ref"] == "table-1"
    assert by_id["a"].metadata["external_structure_table_cell_row"] == 0
    assert by_id["b"].metadata["external_structure_table_cell_col"] == 1
    assert by_id["a"].metadata["reading_order_stream_type"] == "table-island"
    assert by_id["a"].metadata["role"] == "table-cell-text"


def test_pp_structure_ocr_formula_and_seal_results_are_region_evidence() -> None:
    document = _document_with_text_boxes(
        [
            ("body", "Body OCR", BBox(x0=12, y0=14, x1=65, y1=24), 1),
            ("paragraph", "Paragraph OCR", BBox(x0=12, y0=28, x1=90, y1=37), 2),
            ("formula", "E=mc^2", BBox(x0=12, y0=42, x1=55, y1=52), 3),
            ("seal", "Seal", BBox(x0=90, y0=42, x1=123, y1=52), 4),
        ]
    )
    payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 0,
            "overall_ocr_res": {
                "rec_boxes": [[24, 28, 130, 48]],
                "rec_texts": ["Body OCR"],
                "rec_scores": [0.91],
            },
            "text_paragraphs_ocr_res": {
                "rec_polys": [[[24, 56], [180, 56], [180, 74], [24, 74]]],
                "rec_texts": ["Paragraph OCR"],
                "rec_scores": [0.9],
            },
            "formula_res_list": [
                {
                    "formula_region_id": "formula-1",
                    "rec_formula": "E=mc^2",
                    "rec_polys": [[24, 84], [110, 84], [110, 104], [24, 104]],
                    "rec_score": 0.89,
                }
            ],
            "seal_res_list": [
                {
                    "seal_region_id": "seal-1",
                    "rec_boxes": [[180, 84, 246, 104]],
                    "rec_texts": ["Seal"],
                    "rec_scores": [0.88],
                }
            ],
        },
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)
    annotate_document(document)
    by_id = {element.id: element for element in document.pages[0].elements}

    assert [(region.label, region.order, region.order_source, region.text) for region in regions] == [
        ("text", None, None, "Body OCR"),
        ("text", None, None, "Paragraph OCR"),
        ("formula", None, None, "E=mc^2"),
        ("seal", None, None, "Seal"),
    ]
    assert document.metadata["structure_evidence"]["region_count"] == 4
    assert document.metadata["structure_evidence"]["matched_element_count"] == 4
    assert document.metadata["structure_evidence"]["reordered_page_count"] == 0
    assert document.metadata["structure_evidence"]["order_source_counts"] == {"none": 4}
    assert by_id["body"].metadata["external_structure_label"] == "text"
    assert by_id["body"].metadata["structure_evidence"]["confidence"] == 0.91
    assert by_id["formula"].metadata["external_structure_label"] == "formula"
    assert by_id["formula"].metadata["role"] == "formula"
    assert by_id["seal"].metadata["external_structure_label"] == "seal"
    assert by_id["seal"].metadata["role"] == "seal-text"


def test_pp_structure_region_ids_resolve_relations_and_stream_members() -> None:
    document = _document_with_text_boxes(
        [
            ("formula", "E=mc^2", BBox(x0=12, y0=42, x1=55, y1=52), 1),
            ("seal", "Seal", BBox(x0=90, y0=42, x1=123, y1=52), 2),
        ]
    )
    payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 0,
            "formula_res_list": [
                {
                    "formula_region_id": "formula-1",
                    "rec_formula": "E=mc^2",
                    "rec_polys": [[24, 84], [110, 84], [110, 104], [24, 104]],
                    "rec_score": 0.89,
                }
            ],
            "seal_res_list": [
                {
                    "seal_region_id": "seal-1",
                    "rec_boxes": [[180, 84, 246, 104]],
                    "rec_texts": ["Seal"],
                    "rec_scores": [0.88],
                }
            ],
            "reading_streams": [
                {
                    "id": "stamp-flow",
                    "stream_type": "body",
                    "members": ["formula-1", "seal-1"],
                    "successor_edges": [["formula-1", "seal-1"]],
                }
            ],
        },
    }

    apply_structure_evidence(document, payload)
    by_id = {element.id: element for element in document.pages[0].elements}

    assert by_id["formula"].metadata["external_structure_node_keys"] == ["formula-1", "E=mc^2"]
    assert by_id["seal"].metadata["external_structure_node_keys"] == ["seal-1", "Seal"]
    assert by_id["formula"].metadata["external_structure_successor_ids"] == ["seal"]
    assert by_id["formula"].metadata["reading_order_stream_id"] == "stamp-flow"
    assert by_id["formula"].metadata["reading_order_stream_index"] == 1
    assert by_id["seal"].metadata["reading_order_stream_id"] == "stamp-flow"
    assert by_id["seal"].metadata["reading_order_stream_index"] == 2
    assert document.metadata["structure_evidence"]["relation_edge_count"] == 1
    assert document.metadata["structure_evidence"]["resolved_relation_edge_count"] == 1
    assert document.metadata["structure_evidence"]["stream_count"] == 1
    assert document.metadata["structure_evidence"]["resolved_stream_member_count"] == 2


def test_pp_structure_ocr_result_regions_are_deduped() -> None:
    document = _document_with_text_boxes(
        [
            ("line", "Repeated text", BBox(x0=12, y0=14, x1=65, y1=24), 1),
        ]
    )
    payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 0,
            "overall_ocr_res": {
                "rec_boxes": [[24, 28, 130, 48]],
                "rec_texts": ["Repeated text"],
                "rec_scores": [0.88],
            },
            "text_paragraphs_ocr_res": {
                "rec_boxes": [[24, 28, 130, 48]],
                "rec_texts": ["Repeated text"],
                "rec_scores": [0.92],
            },
        },
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)
    line = document.pages[0].elements[0]

    assert len(regions) == 1
    assert regions[0].text == "Repeated text"
    assert regions[0].confidence == 0.92
    assert regions[0].raw["_scriptorium_structure_list_key"] == "text_paragraphs_ocr_res"
    assert document.metadata["structure_evidence"]["region_count"] == 1
    assert document.metadata["structure_evidence"]["matched_element_count"] == 1
    assert line.metadata["structure_evidence"]["confidence"] == 0.92


def test_structure_evidence_matches_sparse_source_page_index() -> None:
    document = _document_with_text_boxes(
        [
            ("line", "Sampled page line.", BBox(x0=10, y0=10, x1=100, y1=22), 1),
        ],
        page_index=4,
    )
    payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 4,
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_bbox": [20, 20, 200, 44],
                    "block_order": 1,
                    "block_content": "Sampled page line.",
                    "confidence": 0.94,
                },
            ],
        },
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)

    assert len(regions) == 1
    assert regions[0].page_index == 4
    assert document.metadata["structure_evidence"]["matched_element_count"] == 1
    assert document.pages[0].elements[0].metadata["external_structure_order"] == 1


def test_structure_evidence_inherits_sparse_page_index_through_nested_data() -> None:
    document = _document_with_text_boxes(
        [
            ("line", "Nested page line.", BBox(x0=10, y0=10, x1=100, y1=22), 1),
        ],
        page_index=4,
    )
    payload = {
        "source": "pp-structurev3",
        "page_results": [
            {
                "page_index": 4,
                "data": {
                    "overall_ocr_res": {
                        "rec_boxes": [[20, 20, 200, 44]],
                        "rec_texts": ["Nested page line."],
                        "rec_scores": [0.95],
                    }
                },
            }
        ],
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)

    assert len(regions) == 1
    assert regions[0].page_index == 4
    assert document.metadata["structure_evidence"]["regions_by_page"][0]["page_index"] == 4
    assert document.metadata["structure_evidence"]["matched_element_count"] == 1
    assert document.pages[0].elements[0].metadata["structure_evidence"]["confidence"] == 0.95


def test_structure_relations_inherit_sparse_page_index_through_nested_data() -> None:
    document = _document_with_text_boxes(
        [
            ("a", "A", BBox(x0=10, y0=10, x1=40, y1=20), 1),
            ("b", "B", BBox(x0=10, y0=30, x1=40, y1=40), 2),
        ],
        page_index=4,
    )
    payload = {
        "source": "stream-structure",
        "page_results": [
            {
                "page_index": 4,
                "data": {
                    "reading_streams": [
                        {
                            "id": "sampled-body",
                            "stream_type": "body",
                            "members": ["a", "b"],
                            "successor_edges": [["a", "b"]],
                        }
                    ]
                },
            }
        ],
    }

    relations = normalize_structure_relations(payload, document)
    streams = normalize_structure_streams(payload, document)
    apply_structure_evidence(document, payload)
    by_id = {element.id: element for element in document.pages[0].elements}

    assert len(relations) == 1
    assert relations[0].page_index == 4
    assert len(streams) == 1
    assert streams[0].page_index == 4
    assert document.metadata["structure_evidence"]["relations_by_page"][0]["page_index"] == 4
    assert document.metadata["structure_evidence"]["streams_by_page"][0]["page_index"] == 4
    assert document.metadata["structure_evidence"]["resolved_relation_edge_count"] == 1
    assert document.metadata["structure_evidence"]["resolved_stream_member_count"] == 2
    assert by_id["a"].metadata["external_structure_successor_ids"] == ["b"]
    assert by_id["a"].metadata["reading_order_stream_id"] == "sampled-body"
    assert by_id["b"].metadata["reading_order_stream_index"] == 2


def test_structure_evidence_does_not_fallback_to_position_for_sparse_source_page_index() -> None:
    document = _document_with_text_boxes(
        [
            ("line", "Sampled page line.", BBox(x0=10, y0=10, x1=100, y1=22), 1),
        ],
        page_index=4,
    )
    payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 0,
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_bbox": [20, 20, 200, 44],
                    "block_order": 1,
                    "block_content": "Wrong source page.",
                    "confidence": 0.94,
                },
            ],
        },
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)

    assert regions == []
    assert document.metadata["structure_evidence"]["region_count"] == 0
    assert "external_structure_order" not in document.pages[0].elements[0].metadata


def test_external_structure_labels_feed_reading_stream_scopes() -> None:
    document = _document_with_text_boxes(
        [
            ("header", "Quarterly Report", BBox(x0=70, y0=8, x1=132, y1=18), 1),
            ("body", "Main body line.", BBox(x0=28, y0=44, x1=118, y1=56), 2),
            ("sidebar", "Side note", BBox(x0=154, y0=52, x1=190, y1=88), 3),
            ("caption", "Figure 1. Revenue mix.", BBox(x0=42, y0=104, x1=146, y1=116), 4),
            ("table", "Revenue 42", BBox(x0=28, y0=128, x1=142, y1=142), 5),
            ("footnote", "1 Unaudited figures.", BBox(x0=28, y0=174, x1=126, y1=184), 6),
            ("footer", "12", BBox(x0=96, y0=188, x1=104, y1=196), 7),
        ]
    )
    payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 0,
            "parsing_res_list": [
                _pp_region("header", "header", "Quarterly Report", 1, document),
                _pp_region("body", "text", "Main body line.", 2, document),
                _pp_region("sidebar", "sidebar_text", "Side note", 3, document),
                _pp_region("caption", "figure_title", "Figure 1. Revenue mix.", 4, document),
                _pp_region("table", "table", "Revenue 42", 5, document),
                _pp_region("footnote", "footnote", "1 Unaudited figures.", 6, document),
                _pp_region("footer", "page_number", "12", 7, document),
            ],
        },
    }

    apply_structure_evidence(document, payload)
    annotate_document(document)
    by_id = {element.id: element for element in document.pages[0].elements}

    assert by_id["header"].metadata["reading_order_scope"] == "page-artifact"
    assert by_id["header"].metadata["reading_order_artifact_type"] == "header"
    assert by_id["header"].metadata["reading_order_stream_id"] == "page-artifact-header"
    assert by_id["header"].metadata["role"] == "running-header"
    assert "external-structure-header" in by_id["header"].metadata["reading_order_evidence"]

    assert by_id["sidebar"].metadata["reading_order_scope"] == "sidebar"
    assert by_id["sidebar"].metadata["reading_order_sidebar_type"] == "right"
    assert by_id["sidebar"].metadata["reading_order_stream_id"] == "sidebar-right"
    assert by_id["sidebar"].metadata["role"] == "sidebar-text"

    assert by_id["caption"].metadata["reading_order_caption_type"] == "figure"
    assert by_id["caption"].metadata["reading_order_stream_type"] == "caption-figure"
    assert by_id["caption"].metadata["role"] == "caption"

    assert by_id["table"].metadata["column_span"] == "table-external"
    assert by_id["table"].metadata["reading_order_stream_type"] == "table-island"
    assert by_id["table"].metadata["reading_order_stream_id"] == "table-island-external-005"
    assert by_id["table"].metadata["role"] == "table-cell-text"

    assert by_id["footnote"].metadata["reading_order_scope"] == "footnote"
    assert by_id["footnote"].metadata["reading_order_stream_id"] == "footnote"
    assert by_id["footnote"].metadata["role"] == "footnote"

    assert by_id["footer"].metadata["reading_order_scope"] == "page-artifact"
    assert by_id["footer"].metadata["reading_order_artifact_type"] == "footer"
    assert by_id["footer"].metadata["reading_order_stream_id"] == "page-artifact-footer"
    assert by_id["footer"].metadata["role"] == "page-number"


def test_external_card_grid_label_creates_grid_translation_stream() -> None:
    document = _document_with_text_boxes(
        [
            ("intro", "Featured products", BBox(x0=20, y0=20, x1=116, y1=32), 1),
            ("card-a", "Camera deal", BBox(x0=24, y0=58, x1=86, y1=70), 2),
            ("card-b", "Phone deal", BBox(x0=104, y0=58, x1=164, y1=70), 3),
            ("card-c", "Laptop deal", BBox(x0=24, y0=92, x1=90, y1=104), 4),
            ("card-d", "Watch deal", BBox(x0=104, y0=92, x1=166, y1=104), 5),
        ]
    )
    payload = {
        "source": "paddleocr-vl",
        "res": {
            "page_index": 0,
            "parsing_res_list": [
                _pp_region("intro", "text", "Featured products", 1, document),
                {
                    "block_label": "product_grid",
                    "block_bbox": [40, 112, 340, 216],
                    "block_order": 2,
                    "block_content": "Camera deal Phone deal Laptop deal Watch deal",
                    "confidence": 0.91,
                },
            ],
        },
    }

    apply_structure_evidence(document, payload)
    annotate_document(document)
    by_id = {element.id: element for element in document.pages[0].elements}
    grid_items = [by_id[element_id] for element_id in ("card-a", "card-b", "card-c", "card-d")]

    assert {item.metadata["column_span"] for item in grid_items} == {"grid-external"}
    assert {item.metadata["reading_order_stream_type"] for item in grid_items} == {"grid-island"}
    assert {item.metadata["reading_order_stream_id"] for item in grid_items} == {"grid-island-external-002"}
    assert [item.metadata["reading_order_stream_index"] for item in grid_items] == [1, 2, 3, 4]
    assert {item.metadata["role"] for item in grid_items} == {"card-text"}
    assert all("external-structure-grid-island" in item.metadata["reading_order_evidence"] for item in grid_items)
    assert by_id["intro"].metadata["reading_order_stream_id"] == "body-main"


def test_nested_structure_children_prefer_specific_card_regions_for_ordering() -> None:
    document = _document_with_text_boxes(
        [
            ("intro", "Featured products", BBox(x0=20, y0=20, x1=116, y1=32), 1),
            ("card-b", "Phone deal", BBox(x0=104, y0=58, x1=164, y1=70), 2),
            ("card-a", "Camera deal", BBox(x0=24, y0=58, x1=86, y1=70), 3),
            ("card-d", "Watch deal", BBox(x0=104, y0=92, x1=166, y1=104), 4),
            ("card-c", "Laptop deal", BBox(x0=24, y0=92, x1=90, y1=104), 5),
        ]
    )
    payload = {
        "source": "paddleocr-vl",
        "res": {
            "page_index": 0,
            "parsing_res_list": [
                _pp_region("intro", "text", "Featured products", 1, document),
                {
                    "block_label": "product_grid",
                    "block_bbox": [40, 112, 340, 216],
                    "block_content": "Camera deal Phone deal Laptop deal Watch deal",
                    "confidence": 0.90,
                    "children": [
                        _nested_region("card-a", "product_card", "Camera deal", document),
                        _nested_region("card-b", "product_card", "Phone deal", document),
                        _nested_region("card-c", "product_card", "Laptop deal", document),
                        _nested_region("card-d", "product_card", "Watch deal", document),
                    ],
                },
            ],
        },
    }

    regions = normalize_structure_evidence(payload, document)
    apply_structure_evidence(document, payload)
    annotate_document(document)
    by_id = {element.id: element for element in document.pages[0].elements}
    ordered_text = [
        element.source_text
        for element in sorted(document.pages[0].elements, key=lambda item: item.reading_order)
        if element.source_text
    ]

    assert len(regions) == 6
    assert document.metadata["structure_evidence"]["order_source_counts"] == {"explicit": 1, "implicit-list": 5}
    assert ordered_text == [
        "Featured products",
        "Camera deal",
        "Phone deal",
        "Laptop deal",
        "Watch deal",
    ]
    assert by_id["card-a"].metadata["external_structure_label"] == "product_card"
    assert by_id["card-a"].metadata["external_structure_order_source"] == "implicit-list"
    assert by_id["card-a"].metadata["structure_evidence"]["text_similarity"] == 1.0
    assert by_id["card-a"].metadata["reading_order_stream_type"] == "grid-island"
    assert by_id["card-a"].metadata["role"] == "card-text"


def _document_with_text_boxes(items: list[tuple[str, str, BBox, int]], page_index: int = 0) -> DocumentIR:
    elements = [
        ElementIR(
            id=element_id,
            page_index=page_index,
            type="text",
            bbox_pdf=bbox,
            bbox_px=BBox(x0=bbox.x0 * 2, y0=bbox.y0 * 2, x1=bbox.x1 * 2, y1=bbox.y1 * 2),
            source_text=text,
            reading_order=reading_order,
            metadata={"reading_order_strategy": "visual-yx", "source": "native-pdf"},
        )
        for element_id, text, bbox, reading_order in items
    ]
    page = PageIR(
        page_index=page_index,
        width_pt=200,
        height_pt=200,
        width_px=400,
        height_px=400,
        render_dpi=144,
        scale_x=2.0,
        scale_y=2.0,
        background_image="page.png",
        elements=elements,
    )
    return DocumentIR(source_pdf="paper.pdf", render_dpi=144, page_count=1, pages=[page])


def _pp_region(
    element_id: str,
    label: str,
    text: str,
    order: int,
    document: DocumentIR,
) -> dict[str, object]:
    element = next(item for item in document.pages[0].elements if item.id == element_id)
    return {
        "block_label": label,
        "block_bbox": element.bbox_px.as_list(),
        "block_order": order,
        "block_content": text,
        "confidence": 0.95,
    }


def _nested_region(
    element_id: str,
    label: str,
    text: str,
    document: DocumentIR,
) -> dict[str, object]:
    element = next(item for item in document.pages[0].elements if item.id == element_id)
    return {
        "block_label": label,
        "block_bbox": element.bbox_px.as_list(),
        "block_content": text,
        "confidence": 0.92,
    }


def _docling_cell(text: str, row: int, col: int, x0: float, y0: float, x1: float, y1: float) -> dict[str, object]:
    return {
        "text": text,
        "start_row_offset_idx": row,
        "end_row_offset_idx": row + 1,
        "start_col_offset_idx": col,
        "end_col_offset_idx": col + 1,
        "bbox": {"l": x0, "t": y0, "r": x1, "b": y1, "coord_origin": "TOPLEFT"},
    }
