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


def _document_with_text_boxes(items: list[tuple[str, str, BBox, int]]) -> DocumentIR:
    elements = [
        ElementIR(
            id=element_id,
            page_index=0,
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
        page_index=0,
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
