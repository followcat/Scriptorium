from scriptorium.annotations import annotate_document
from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.structure_evidence import apply_structure_evidence, normalize_structure_evidence


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
    assert document.pages[0].elements[0].metadata["reading_order_strategy"] == "external-structure-fusion-v1"
    assert document.pages[0].elements[0].metadata["structure_evidence"]["source"] == "pp-structurev3"


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
    assert document.pages[0].elements[2].metadata["external_structure_order"] == 2
    assert document.pages[0].elements[2].metadata["structure_evidence"]["source"] == "docling"


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
