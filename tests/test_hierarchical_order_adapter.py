from __future__ import annotations

import copy
import json

import pytest
from typer.testing import CliRunner

import scriptorium.cli as cli
from scriptorium.hierarchical_order import build_hierarchical_order_proposal
from scriptorium.hierarchical_order_adapter import (
    HIERARCHY_INPUT_ADAPTER_SCHEMA,
    build_hierarchy_input_from_document,
)
from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.reading_order_sidecar import SIDECAR_SCHEMA_NAME


def test_adapter_keeps_provider_blocks_and_rejects_ocr_lines() -> None:
    document = _two_column_document()
    structure = _provider_structure()

    adapted = build_hierarchy_input_from_document(document, structure)
    proposal = build_hierarchical_order_proposal(adapted.payload)

    assert [element["id"] for element in adapted.payload["elements"]] == [
        "left-one",
        "left-two",
        "right-one",
        "right-two",
    ]
    assert len(adapted.payload["regions"]) == 2
    assert {tuple(region.get("member_ids", [])) for region in adapted.payload["regions"]} == {
        ("left-one", "left-two"),
        ("right-one", "right-two"),
    }
    assert adapted.diagnostics == {
        "fine_element_count": 4,
        "fine_rejected_element_count": 2,
        "fine_rejected_reason_counts": {
            "empty-text": 1,
            "source-visual-layer": 1,
        },
        "normalized_page_region_count": 6,
        "selected_coarse_region_count": 2,
        "rejected_region_count": 4,
        "selected_region_reason_counts": {"provider-block-list": 2},
        "rejected_region_reason_counts": {"fine-ocr-or-document-element": 4},
        "provider_source_counts": {"provider-layout": 2},
        "provider_order_source_counts": {"explicit": 2},
        "declared_coarse_region_granularity": False,
        "explicit_reference_membership_count": 4,
        "explicit_reference_ambiguous_count": 0,
        "explicit_reference_geometry_conflict_count": 0,
        "min_explicit_reference_coverage": 0.5,
    }
    assert proposal.payload["runtime_reorder"] is False
    assert proposal.payload["input_adapter"]["schema"] == (
        HIERARCHY_INPUT_ADAPTER_SCHEMA
    )
    assert proposal.diagnostics["explicit_membership_count"] == 4
    assert proposal.diagnostics["unassigned_element_count"] == 0
    assert proposal.diagnostics["within_region_successor_count"] == 2
    assert proposal.diagnostics["emitted_cross_region_transition_count"] == 0
    assert proposal.diagnostics["fine_relation_cross_region_edge_count"] == 0
    assert (
        proposal.diagnostics[
            "candidate_expansion_suppressed_missing_cross_region_evidence"
        ]
        is True
    )


def test_adapter_does_not_consume_provider_sequence_or_relation_fields() -> None:
    document = _two_column_document()
    first_structure = _provider_structure()
    second_structure = copy.deepcopy(first_structure)
    second_structure["pages"][0]["parsing_res_list"].reverse()
    for index, block in enumerate(
        second_structure["pages"][0]["parsing_res_list"],
        start=71,
    ):
        block["block_order"] = index
    second_structure["pages"][0]["successor_edges"] = [
        {"source": "right", "target": "left", "review_required": True}
    ]

    first = build_hierarchy_input_from_document(document, first_structure)
    second = build_hierarchy_input_from_document(document, second_structure)

    assert second.payload["elements"] == first.payload["elements"]
    assert second.payload["regions"] == first.payload["regions"]
    assert (
        second.payload["input_adapter"]["region_provenance"]
        == first.payload["input_adapter"]["region_provenance"]
    )
    assert (
        second.payload["input_adapter"]["source_structure_sha256"]
        != first.payload["input_adapter"]["source_structure_sha256"]
    )


def test_adapter_keeps_docling_parent_block_but_not_table_cells() -> None:
    document = _table_document()
    structure = {
        "schema_name": "DoclingDocument",
        "body": {"self_ref": "#/body", "children": [{"$ref": "#/tables/0"}]},
        "tables": [
            {
                "self_ref": "#/tables/0",
                "label": "table",
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {
                            "l": 15,
                            "t": 45,
                            "r": 105,
                            "b": 90,
                            "coord_origin": "TOPLEFT",
                        },
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

    adapted = build_hierarchy_input_from_document(document, structure)
    proposal = build_hierarchical_order_proposal(adapted.payload)

    assert adapted.diagnostics["normalized_page_region_count"] == 5
    assert adapted.diagnostics["selected_coarse_region_count"] == 1
    assert adapted.diagnostics["rejected_region_reason_counts"] == {
        "fine-table-cell": 4
    }
    assert adapted.diagnostics["selected_region_reason_counts"] == {
        "docling-document-block": 1
    }
    assert proposal.diagnostics["assigned_element_count"] == 4
    assert proposal.diagnostics["within_region_successor_count"] == 3


def test_adapter_rejects_sidecars_and_fine_only_structure() -> None:
    document = _two_column_document()
    with pytest.raises(ValueError, match="does not accept reading-order sidecars"):
        build_hierarchy_input_from_document(
            document,
            {
                "schema_name": SIDECAR_SCHEMA_NAME,
                "sidecar_status": "accepted",
                "pages": [],
            },
        )

    fine_only = copy.deepcopy(_provider_structure())
    del fine_only["pages"][0]["parsing_res_list"]
    with pytest.raises(ValueError, match="no coarse provider regions"):
        build_hierarchy_input_from_document(document, fine_only)

    answer_like = {
        "source": "benchmark-labels",
        "pages": [
            {
                "page_index": 0,
                "elements": [
                    {
                        "id": "label-node",
                        "block_label": "text",
                        "block_content": "label",
                        "bbox_pdf": [10, 10, 90, 30],
                        "coordinate_space": "pdf",
                        "reading_order": 1,
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="answer-like-region-order-field=1"):
        build_hierarchy_input_from_document(document, answer_like)


def test_build_hierarchical_order_cli_adapts_document_ir(tmp_path) -> None:
    document_path = tmp_path / "document.ir.json"
    structure_path = tmp_path / "provider.structure.json"
    output_path = tmp_path / "hierarchy.proposal.json"
    _two_column_document().save(document_path)
    structure_path.write_text(
        json.dumps(_provider_structure()),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "build-hierarchical-order",
            str(document_path),
            "--structure-json",
            str(structure_path),
            "--page-index",
            "0",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Adapter regions (selected/rejected): 2/4" in result.output
    assert "Membership (assigned/ambiguous/unassigned): 4/0/0" in result.output
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["input_adapter"]["schema"] == HIERARCHY_INPUT_ADAPTER_SCHEMA
    assert output["runtime_reorder"] is False


def _two_column_document() -> DocumentIR:
    elements = [
        _element("left-one", "Left one", 10, 10, 80, 20, parent_id="left"),
        _element("left-two", "Left two", 10, 35, 80, 45, parent_id="left"),
        _element("right-one", "Right one", 110, 10, 180, 20, parent_id="right"),
        _element("right-two", "Right two", 110, 35, 180, 45, parent_id="right"),
        ElementIR(
            id="source-image",
            page_index=0,
            type="image",
            bbox_pdf=BBox(x0=0, y0=0, x1=200, y1=200),
            bbox_px=BBox(x0=0, y0=0, x1=200, y1=200),
            metadata={"image_source_visual_layer": True},
        ),
        ElementIR(
            id="empty-shape",
            page_index=0,
            type="shape",
            bbox_pdf=BBox(x0=90, y0=70, x1=110, y1=90),
            bbox_px=BBox(x0=90, y0=70, x1=110, y1=90),
        ),
    ]
    return _document(elements, source_type="image")


def _table_document() -> DocumentIR:
    return _document(
        [
            _element("a", "A", 20, 50, 55, 62),
            _element("b", "B", 60, 50, 95, 62),
            _element("c", "C", 20, 70, 55, 82),
            _element("d", "D", 60, 70, 95, 82),
        ]
    )


def _document(
    elements: list[ElementIR],
    *,
    source_type: str = "pdf",
) -> DocumentIR:
    return DocumentIR(
        id="adapter-document",
        source="fixture.png" if source_type == "image" else "fixture.pdf",
        source_type=source_type,
        render_dpi=72,
        page_count=1,
        pages=[
            PageIR(
                page_index=0,
                width_pt=200,
                height_pt=200,
                width_px=200,
                height_px=200,
                render_dpi=72,
                scale_x=1,
                scale_y=1,
                background_image="page.png",
                elements=elements,
            )
        ],
    )


def _element(
    element_id: str,
    text: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    parent_id: str | None = None,
) -> ElementIR:
    metadata = {"parent_id": parent_id} if parent_id is not None else {}
    return ElementIR(
        id=element_id,
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        bbox_px=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        source_text=text,
        metadata=metadata,
    )


def _provider_structure() -> dict:
    return {
        "source": "provider-layout",
        "pages": [
            {
                "page_index": 0,
                "parsing_res_list": [
                    {
                        "block_id": "left",
                        "block_label": "text_block",
                        "block_content": "Left region",
                        "block_order": 2,
                        "bbox_pdf": [5, 5, 90, 55],
                        "coordinate_space": "pdf",
                    },
                    {
                        "block_id": "right",
                        "block_label": "text_block",
                        "block_content": "Right region",
                        "block_order": 1,
                        "bbox_pdf": [105, 5, 190, 55],
                        "coordinate_space": "pdf",
                    },
                ],
                "overall_ocr_res": {
                    "rec_texts": ["Left one", "Left two", "Right one", "Right two"],
                    "rec_boxes": [
                        [10, 10, 80, 20],
                        [10, 35, 80, 45],
                        [110, 10, 180, 20],
                        [110, 35, 180, 45],
                    ],
                    "rec_scores": [0.99, 0.99, 0.99, 0.99],
                },
                "successor_edges": [
                    {"source": "left", "target": "right", "review_required": True}
                ],
            }
        ],
    }


def _docling_cell(
    text: str,
    row: int,
    col: int,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> dict:
    return {
        "text": text,
        "start_row_offset_idx": row,
        "end_row_offset_idx": row + 1,
        "start_col_offset_idx": col,
        "end_col_offset_idx": col + 1,
        "bbox": {
            "l": left,
            "t": top,
            "r": right,
            "b": bottom,
            "coord_origin": "TOPLEFT",
        },
    }
