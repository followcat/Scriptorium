from pathlib import Path
import shutil

import pytest
from PIL import Image, ImageDraw

from scriptorium.annotations import annotate_document
from scriptorium.ocr import normalize_ocr_to_ir
from scriptorium.pdf_render import render_source
from scriptorium.structure_evidence import apply_structure_evidence


def _require_tesseract() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is required for image source OCR fallback coverage.")


def test_image_source_renders_as_first_class_document_source(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")

    rendered = render_source(image_path, tmp_path / "pages", input_kind="auto", image_dpi=96)
    document = normalize_ocr_to_ir(rendered)
    annotate_document(document)

    assert rendered.source_type == "image"
    assert document.source == str(image_path.resolve())
    assert document.source_type == "image"
    assert document.source_path == str(image_path.resolve())
    assert document.source_pdf is None
    assert document.page_count == 1
    assert document.pages[0].width_px == 320
    assert document.pages[0].width_pt == 240
    assert len(document.pages[0].elements) == 1
    image_element = document.pages[0].elements[0]
    assert image_element.type == "image"
    assert image_element.source_crop == str(rendered.pages[0].background_image)
    assert image_element.metadata["annotation"]["source_kind"] == "image-source"


def test_image_source_uses_ocr_json_as_text_anchor_layer(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)

    document = normalize_ocr_to_ir(
        rendered,
        {
            "source": "unit-ocr",
            "pages": [
                {
                    "page_index": 0,
                    "elements": [
                        {
                            "type": "text",
                            "bbox_px": [24, 28, 190, 58],
                            "text": "Image source text",
                            "confidence": 0.91,
                        }
                    ],
                }
            ],
        },
    )
    annotate_document(document)
    elements = document.pages[0].elements

    assert [element.type for element in elements] == ["image", "text"]
    assert elements[1].source_text == "Image source text"
    assert elements[1].metadata["annotation"]["source_kind"] == "native-ocr"
    assert elements[1].metadata["annotation"]["editable"] is True
    assert document.metadata["image_source_visual_layer"] is True
    assert document.metadata["semantic_layer"]["driver"] == "ocr-json"
    assert document.metadata["semantic_layer"]["payload_kind"] == "ocr-json"


def test_image_source_can_apply_tesseract_ocr_fallback(tmp_path: Path) -> None:
    _require_tesseract()
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)

    document = normalize_ocr_to_ir(
        rendered,
        ocr_fallback="image-only",
        ocr_language="eng",
        ocr_dpi=200,
    )
    annotate_document(document)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]

    assert document.metadata["page_extraction"][0]["ocr_fallback_status"] == "applied"
    assert document.metadata["semantic_layer"]["driver"] == "ocr-fallback"
    assert text_elements
    assert {element.metadata["annotation"]["source_kind"] for element in text_elements} == {"native-ocr"}
    assert all(element.metadata["ocr_fallback"] is True for element in text_elements)


def test_image_source_can_seed_text_from_structure_json_blocks(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    structure_payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 0,
            "parsing_res_list": [
                {
                    "block_label": "title",
                    "block_bbox": [24, 28, 190, 58],
                    "block_order": 1,
                    "block_content": "Structure title",
                    "confidence": 0.93,
                }
            ],
        },
    }

    document = normalize_ocr_to_ir(rendered, structure_payload)
    apply_structure_evidence(document, structure_payload)
    annotate_document(document)
    text = next(element for element in document.pages[0].elements if element.source_text)

    assert text.source_text == "Structure title"
    assert text.type == "title"
    assert text.metadata["external_structure_order"] == 1
    assert text.metadata["structure_evidence"]["source"] == "pp-structurev3"
    assert text.metadata["annotation"]["source_kind"] == "native-ocr"
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert document.metadata["semantic_layer"]["structure_json"]["role"] == "semantic-driver"


def test_image_source_completes_structure_anchors_with_ocr_fallback(tmp_path: Path, monkeypatch) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    structure_payload = {
        "source": "paddleocr-vl",
        "res": {
            "page_index": 0,
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_bbox": [24, 28, 132, 48],
                    "block_order": 1,
                    "block_content": "Model anchor",
                    "confidence": 0.94,
                },
                {
                    "block_label": "text",
                    "block_bbox": [20, 116, 140, 136],
                    "block_order": 2,
                    "block_content": "Unpaired generic model text",
                    "confidence": 0.9,
                },
            ],
        },
    }

    def fake_fallback(*_args, **_kwargs):
        return (
            [
                {
                    "type": "text",
                    "bbox_px": [24, 28, 132, 48],
                    "text": "Model anchor",
                    "confidence": 0.72,
                    "source": "native-ocr",
                },
                {
                    "type": "text",
                    "bbox_px": [182, 70, 282, 90],
                    "text": "Fallback card label",
                    "confidence": 0.72,
                    "source": "native-ocr",
                },
            ],
            "eng",
            None,
        )

    monkeypatch.setattr("scriptorium.ocr._ocr_image_raw_elements", fake_fallback)
    document = normalize_ocr_to_ir(rendered, structure_payload, ocr_fallback="image-only")
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]
    by_text = {element.source_text: element for element in text_elements}
    diagnostics = document.metadata["page_extraction"][0]

    assert set(by_text) == {"Model anchor", "Fallback card label"}
    assert by_text["Model anchor"].metadata["ocr_anchor_origin"] == "ocr-fallback-completion"
    assert by_text["Fallback card label"].metadata["ocr_anchor_origin"] == "ocr-fallback-completion"
    assert diagnostics["structure_text_anchor_count"] == 2
    assert diagnostics["ocr_fallback_anchor_count"] == 2
    assert diagnostics["ocr_fallback_added_anchor_count"] == 2
    assert diagnostics["ocr_fallback_suppressed_duplicate_count"] == 0
    assert diagnostics["structure_anchor_overlap_count"] == 1
    assert diagnostics["structure_anchor_completion_count"] == 0
    assert diagnostics["structure_generic_completion_suppressed_count"] == 1
    assert diagnostics["ocr_fallback_status"] == "applied"
    assert document.metadata["semantic_layer"]["driver"] == "structure-plus-ocr-fallback"
    assert document.metadata["semantic_layer"]["structure_text_anchor_count"] == 2
    assert document.metadata["semantic_layer"]["ocr_fallback_completion_anchor_count"] == 2


def test_image_source_can_seed_table_cells_from_pp_structure_json(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    structure_payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 0,
            "table_res_list": [
                {
                    "cell_box_list": [
                        [24, 28, 90, 48],
                        [180, 28, 246, 48],
                        [24, 70, 90, 90],
                        [180, 70, 246, 90],
                    ],
                    "table_ocr_pred": {
                        "rec_texts": ["A", "B", "C", "D"],
                        "rec_scores": [0.96, 0.95, 0.94, 0.93],
                    },
                }
            ],
        },
    }

    document = normalize_ocr_to_ir(rendered, structure_payload)
    apply_structure_evidence(document, structure_payload)
    annotate_document(document)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]
    by_text = {element.source_text: element for element in text_elements}

    assert [element.source_text for element in sorted(text_elements, key=lambda item: item.reading_order)] == [
        "A",
        "B",
        "C",
        "D",
    ]
    assert {element.type for element in text_elements} == {"table"}
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert document.metadata["structure_evidence"]["region_count"] == 4
    assert document.metadata["structure_evidence"]["matched_element_count"] == 4
    assert document.metadata["structure_evidence"]["order_source_counts"] == {"implicit-table-cell": 4}
    assert by_text["A"].metadata["external_structure_label"] == "table_cell"
    assert by_text["A"].metadata["external_structure_order_subindex"] == 1
    assert by_text["A"].metadata["reading_order_stream_type"] == "table-island"
    assert by_text["A"].metadata["annotation"]["role"] == "table-cell-text"


def test_image_source_can_seed_pp_ocr_formula_and_seal_results(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    structure_payload = {
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

    document = normalize_ocr_to_ir(rendered, structure_payload)
    apply_structure_evidence(document, structure_payload)
    annotate_document(document)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]
    by_text = {element.source_text: element for element in text_elements}

    assert [element.source_text for element in text_elements] == [
        "Body OCR",
        "Paragraph OCR",
        "E=mc^2",
        "Seal",
    ]
    assert by_text["Body OCR"].type == "text"
    assert by_text["Paragraph OCR"].type == "text"
    assert by_text["E=mc^2"].type == "formula"
    assert by_text["Seal"].type == "text"
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert document.metadata["semantic_layer"]["payload_kind"] == "structure-json"
    assert document.metadata["structure_evidence"]["region_count"] == 4
    assert document.metadata["structure_evidence"]["matched_element_count"] == 4
    assert document.metadata["structure_evidence"]["order_source_counts"] == {"none": 4}
    assert by_text["Body OCR"].metadata["external_structure_label"] == "text"
    assert by_text["E=mc^2"].metadata["external_structure_label"] == "formula"
    assert by_text["E=mc^2"].metadata["annotation"]["role"] == "formula"
    assert by_text["Seal"].metadata["external_structure_label"] == "seal"
    assert by_text["Seal"].metadata["annotation"]["role"] == "seal-text"


def test_image_source_dedupes_overlapping_pp_ocr_result_anchors(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    structure_payload = {
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

    document = normalize_ocr_to_ir(rendered, structure_payload)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]

    assert [element.source_text for element in text_elements] == ["Repeated text"]
    assert text_elements[0].metadata["paddle_result_key"] == "text_paragraphs_ocr_res"
    assert text_elements[0].confidence == 0.92


def test_image_source_reads_pp_ocr_results_from_page_results_data(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    structure_payload = {
        "source": "pp-structurev3",
        "page_results": [
            {
                "page_index": 0,
                "data": {
                    "overall_ocr_res": {
                        "rec_boxes": [[24, 28, 130, 48]],
                        "rec_texts": ["Nested OCR"],
                        "rec_scores": [0.93],
                    }
                },
            }
        ],
    }

    document = normalize_ocr_to_ir(rendered, structure_payload)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]

    assert [element.source_text for element in text_elements] == ["Nested OCR"]
    assert text_elements[0].metadata["paddle_result_key"] == "overall_ocr_res"
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert document.metadata["semantic_layer"]["payload_kind"] == "structure-json"


def test_image_source_roor_json_seeds_text_and_drives_semantic_order(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    structure_payload = {
        "source": "roor",
        "page_index": 0,
        "document": [
            {"id": 0, "box": [24, 28, 90, 48], "text": "A"},
            {"id": 1, "box": [180, 28, 246, 48], "text": "C"},
            {"id": 2, "box": [24, 70, 90, 90], "text": "B"},
            {"id": 3, "box": [180, 70, 246, 90], "text": "D"},
        ],
        "ro_linkings": [[0, 2], [2, 1], [1, 3]],
    }

    document = normalize_ocr_to_ir(rendered, structure_payload)
    apply_structure_evidence(document, structure_payload)
    annotate_document(document)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]
    by_text = {element.source_text: element for element in text_elements}

    assert [element.source_text for element in sorted(text_elements, key=lambda item: item.reading_order)] == [
        "A",
        "B",
        "C",
        "D",
    ]
    assert {element.type for element in text_elements} == {"text"}
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert document.metadata["semantic_layer"]["payload_kind"] == "structure-json"
    assert document.metadata["semantic_layer"]["structure_json"]["role"] == "semantic-driver"
    assert document.metadata["structure_evidence"]["order_source_counts"] == {"none": 4}
    assert document.metadata["structure_evidence"]["resolved_relation_edge_count"] == 3
    assert document.metadata["structure_evidence"]["relation_stream_count"] == 1
    assert document.metadata["structure_evidence"]["resolved_relation_stream_member_count"] == 4
    assert document.metadata["structure_evidence"]["relation_stream_conflict_count"] == 0
    assert by_text["A"].metadata["external_structure_successor_ids"] == [by_text["B"].id]
    assert {element.metadata["reading_order_stream_id"] for element in text_elements} == {
        "external-relation-body-001-001"
    }
    assert "external-structure-relation-stream" in by_text["A"].metadata["reading_order_evidence"]
    assert "external_structure_order" not in by_text["A"].metadata


def test_image_source_structure_relations_drive_semantic_order(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    ocr_payload = {
        "source": "unit-ocr",
        "pages": [
            {
                "page_index": 0,
                "elements": [
                    {"bbox_px": [24, 28, 90, 48], "text": "A"},
                    {"bbox_px": [180, 28, 246, 48], "text": "C"},
                    {"bbox_px": [24, 70, 90, 90], "text": "B"},
                    {"bbox_px": [180, 70, 246, 90], "text": "D"},
                ],
            }
        ],
    }
    structure_payload = {
        "source": "relation-structure",
        "res": {
            "page_index": 0,
            "layout_det_res": {
                "boxes": [
                    {"id": "a", "label": "text", "coordinate": [24, 28, 90, 48], "text": "A"},
                    {"id": "c", "label": "text", "coordinate": [180, 28, 246, 48], "text": "C"},
                    {"id": "b", "label": "text", "coordinate": [24, 70, 90, 90], "text": "B"},
                    {"id": "d", "label": "text", "coordinate": [180, 70, 246, 90], "text": "D"},
                ]
            },
            "successor_edges": [["a", "b"], ["c", "d"]],
            "precedence_edges": [["b", "d"]],
        },
    }

    document = normalize_ocr_to_ir(rendered, ocr_payload)
    apply_structure_evidence(document, structure_payload)
    annotate_document(document)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]

    assert [element.source_text for element in sorted(text_elements, key=lambda item: item.reading_order)] == [
        "A",
        "B",
        "C",
        "D",
    ]
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert document.metadata["structure_evidence"]["relation_reordered_page_count"] == 1
    assert text_elements[0].metadata["reading_order_strategy"] == "external-structure-relation-fusion-v1"


def test_image_source_structure_relations_resolve_raw_ocr_anchor_ids(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    ocr_payload = {
        "source": "unit-ocr",
        "pages": [
            {
                "page_index": 0,
                "elements": [
                    {"id": "a", "bbox_px": [24, 28, 90, 48], "text": "A"},
                    {"id": "c", "bbox_px": [180, 28, 246, 48], "text": "C"},
                    {"id": "b", "bbox_px": [24, 70, 90, 90], "text": "B"},
                    {"id": "d", "bbox_px": [180, 70, 246, 90], "text": "D"},
                ],
            }
        ],
    }
    structure_payload = {
        "source": "relation-structure",
        "res": {
            "page_index": 0,
            "ro_linkings": [["a", "b"], ["b", "c"], ["c", "d"]],
        },
    }

    document = normalize_ocr_to_ir(rendered, ocr_payload)
    apply_structure_evidence(document, structure_payload)
    annotate_document(document)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]
    by_text = {element.source_text: element for element in text_elements}

    assert [element.source_text for element in sorted(text_elements, key=lambda item: item.reading_order)] == [
        "A",
        "B",
        "C",
        "D",
    ]
    assert document.metadata["structure_evidence"]["region_count"] == 0
    assert document.metadata["structure_evidence"]["relation_edge_count"] == 3
    assert document.metadata["structure_evidence"]["resolved_relation_edge_count"] == 3
    assert document.metadata["structure_evidence"]["relation_stream_count"] == 1
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert by_text["A"].metadata["external_structure_successor_ids"] == [by_text["B"].id]
    assert by_text["A"].metadata["reading_order_strategy"] == "external-structure-relation-fusion-v1"
    assert {element.metadata["reading_order_stream_id"] for element in text_elements} == {
        "external-relation-body-001-001"
    }


def test_image_source_stream_only_structure_json_drives_semantic_layer(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    ocr_payload = {
        "source": "unit-ocr",
        "pages": [
            {
                "page_index": 0,
                "elements": [
                    {"bbox_px": [24, 28, 90, 48], "text": "A"},
                    {"bbox_px": [180, 28, 246, 48], "text": "C"},
                    {"bbox_px": [24, 70, 90, 90], "text": "B"},
                    {"bbox_px": [180, 70, 246, 90], "text": "D"},
                ],
            }
        ],
    }
    structure_payload = {
        "source": "stream-structure",
        "res": {
            "page_index": 0,
            "reading_streams": [
                {"id": "product-row", "stream_type": "product_grid", "text_sequence": ["A", "B"]},
                {"id": "right-rail", "stream_type": "right_sidebar", "text_sequence": ["C", "D"]},
            ],
        },
    }

    document = normalize_ocr_to_ir(rendered, ocr_payload)
    apply_structure_evidence(document, structure_payload)
    annotate_document(document)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]
    by_text = {element.source_text: element for element in text_elements}

    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert document.metadata["semantic_layer"]["structure_json"]["role"] == "semantic-driver"
    assert document.metadata["structure_evidence"]["region_count"] == 0
    assert document.metadata["structure_evidence"]["stream_count"] == 2
    assert document.metadata["structure_evidence"]["resolved_stream_member_count"] == 4
    assert by_text["A"].metadata["reading_order_stream_id"] == "product-row"
    assert by_text["A"].metadata["reading_order_stream_type"] == "grid-island"
    assert by_text["C"].metadata["reading_order_stream_id"] == "right-rail"
    assert by_text["C"].metadata["reading_order_stream_type"] == "sidebar-right"


def test_image_source_stream_linkings_resolve_raw_ocr_anchor_ids(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    ocr_payload = {
        "source": "unit-ocr",
        "pages": [
            {
                "page_index": 0,
                "elements": [
                    {"id": "a", "bbox_px": [24, 28, 90, 48], "text": "A"},
                    {"id": "c", "bbox_px": [180, 28, 246, 48], "text": "C"},
                    {"id": "b", "bbox_px": [24, 70, 90, 90], "text": "B"},
                    {"id": "d", "bbox_px": [180, 70, 246, 90], "text": "D"},
                ],
            }
        ],
    }
    structure_payload = {
        "source": "stream-structure",
        "res": {
            "page_index": 0,
            "reading_streams": [
                {
                    "id": "product-row",
                    "stream_type": "product_grid",
                    "reading_order_linkings": [["a", "b"]],
                },
                {
                    "id": "right-rail",
                    "stream_type": "right_sidebar",
                    "ro_linkings": [["c", "d"]],
                },
            ],
        },
    }

    document = normalize_ocr_to_ir(rendered, ocr_payload)
    apply_structure_evidence(document, structure_payload)
    annotate_document(document)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]
    by_text = {element.source_text: element for element in text_elements}

    assert document.metadata["structure_evidence"]["region_count"] == 0
    assert document.metadata["structure_evidence"]["stream_count"] == 2
    assert document.metadata["structure_evidence"]["resolved_stream_member_count"] == 4
    assert document.metadata["structure_evidence"]["relation_edge_count"] == 2
    assert document.metadata["structure_evidence"]["resolved_relation_edge_count"] == 2
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert by_text["A"].metadata["reading_order_stream_id"] == "product-row"
    assert by_text["A"].metadata["reading_order_stream_type"] == "grid-island"
    assert by_text["B"].metadata["reading_order_stream_index"] == 2
    assert by_text["C"].metadata["reading_order_stream_id"] == "right-rail"
    assert by_text["C"].metadata["reading_order_stream_type"] == "sidebar-right"
    assert by_text["D"].metadata["reading_order_stream_index"] == 2


def _make_image(path: Path) -> Path:
    image = Image.new("RGB", (320, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.text((24, 30), "Image source text", fill=(0, 0, 0))
    image.save(path)
    return path
