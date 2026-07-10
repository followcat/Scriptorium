from __future__ import annotations

import json

from PIL import Image

from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.ocr import normalize_ocr_to_ir
from scriptorium.pdf_render import render_source
from scriptorium.reading_order_sidecar import (
    SIDECAR_PROPOSAL_STATUS,
    propose_reading_order_sidecar,
    reading_order_sidecar_summary,
)
from scriptorium.semantic_quality import compare_reading_order_sidecar_proposal
from scriptorium.structure_evidence import apply_structure_evidence


def test_proposal_splits_multicolumn_body_into_local_successor_streams() -> None:
    document = _document(
        [
            _element("left-one", "Left one", 1, 10, 20, column_index=0),
            _element("left-two", "Left two", 2, 10, 40, column_index=0),
            _element("right-one", "Right one", 3, 120, 20, column_index=1),
            _element("right-two", "Right two", 4, 120, 40, column_index=1),
            _element(
                "sidebar",
                "Related note",
                5,
                210,
                20,
                stream_id="sidebar-right",
                stream_type="sidebar-right",
            ),
        ]
    )

    proposal = propose_reading_order_sidecar(document)
    page = proposal["pages"][0]
    streams = {stream["id"]: stream for stream in page["reading_streams"]}

    assert proposal["sidecar_status"] == SIDECAR_PROPOSAL_STATUS
    assert set(streams) == {"body-column-001", "body-column-002", "sidebar-right"}
    assert streams["body-column-001"]["members"] == ["left-one", "left-two"]
    assert streams["body-column-002"]["members"] == ["right-one", "right-two"]
    assert streams["body-column-001"]["successor_edges"] == [
        {
            "source": "left-one",
            "target": "left-two",
            "confidence": 0.9,
            "review_required": False,
            "evidence": ["column-local-stream", "flow-segment-001", "same-column", "same-flow-segment"],
        }
    ]
    transitions = {(item["source"], item["target"]): item for item in page["review_transitions"]}
    assert transitions[("left-two", "right-one")]["reason"] == "column-handoff"
    assert transitions[("right-two", "sidebar")]["reason"] == "stream-type-boundary"
    assert page["document"][0]["bbox_pdf"] == [10.0, 20.0, 90.0, 32.0]
    assert page["document"][0]["_scriptorium_sidecar_reference"] is True
    assert page["document"][0]["review"]["bbox_pdf"] == [10.0, 20.0, 90.0, 32.0]
    assert reading_order_sidecar_summary(proposal) == {
        "stream_count": 3,
        "member_count": 5,
        "successor_edge_count": 2,
        "review_successor_edge_count": 0,
        "review_transition_count": 2,
        "stream_type_counts": {"body": 2, "sidebar-right": 1},
        "stream_origin_counts": {"column-partition": 2, "existing-local": 1},
    }


def test_accepted_proposal_reapplies_local_stream_relations() -> None:
    document = _document(
        [
            _element("left-one", "Left one", 1, 10, 20, column_index=0),
            _element("right-one", "Right one", 2, 120, 20, column_index=1),
            _element("left-two", "Left two", 3, 10, 40, column_index=0),
            _element("right-two", "Right two", 4, 120, 40, column_index=1),
        ]
    )
    document.pages[0].elements[0].metadata["external_structure_label"] = "table_cell"
    proposal = propose_reading_order_sidecar(document)
    proposal["sidecar_status"] = "accepted"

    apply_structure_evidence(document, proposal)

    ordered_ids = [element.id for element in sorted(document.pages[0].elements, key=lambda item: item.reading_order)]
    assert ordered_ids == ["left-one", "left-two", "right-one", "right-two"]
    by_id = {element.id: element for element in document.pages[0].elements}
    assert by_id["left-one"].metadata["reading_order_stream_id"] == "body-column-001"
    assert by_id["right-one"].metadata["reading_order_stream_id"] == "body-column-002"
    assert by_id["left-one"].metadata["external_structure_label"] == "table_cell"
    assert document.metadata["structure_evidence"]["resolved_relation_edge_count"] == 2
    assert document.metadata["structure_evidence"]["reordered_page_count"] == 1


def test_unaccepted_proposal_is_not_applied_as_structure_evidence() -> None:
    document = _document(
        [
            _element("left-one", "Left one", 1, 10, 20, column_index=0),
            _element("right-one", "Right one", 2, 120, 20, column_index=1),
            _element("left-two", "Left two", 3, 10, 40, column_index=0),
            _element("right-two", "Right two", 4, 120, 40, column_index=1),
        ]
    )
    proposal = propose_reading_order_sidecar(document)

    apply_structure_evidence(document, proposal)

    assert [element.reading_order for element in document.pages[0].elements] == [1, 2, 3, 4]
    assert "external_structure_stream_id" not in document.pages[0].elements[0].metadata
    assert document.metadata["structure_evidence_proposal"]["status"] == "proposal-skipped"
    assert document.revisions[-1].reason == "structure-evidence-proposal-skipped"


def test_low_confidence_local_edges_stay_review_only() -> None:
    proposal = propose_reading_order_sidecar(
        _document(
            [
                _element("first", "First", 1, 10, 20, confidence=0.5),
                _element("second", "Second", 2, 10, 140, confidence=0.5),
            ]
        )
    )
    stream = proposal["pages"][0]["reading_streams"][0]

    assert stream["successor_edges"] == []
    assert stream["review_successor_edges"] == [
        {
            "source": "first",
            "target": "second",
            "confidence": 0.5,
            "review_required": True,
            "evidence": ["preserve-body-stream", "same-flow-segment"],
        }
    ]
    assert reading_order_sidecar_summary(proposal)["successor_edge_count"] == 0
    assert reading_order_sidecar_summary(proposal)["review_successor_edge_count"] == 1


def test_stable_low_confidence_local_edges_promote_with_independent_evidence() -> None:
    proposal = propose_reading_order_sidecar(
        _document(
            [
                _element("first", "First", 1, 10, 20, confidence=0.5),
                _element("second", "Second", 2, 10, 40, confidence=0.5),
            ]
        )
    )
    stream = proposal["pages"][0]["reading_streams"][0]

    assert stream["review_successor_edges"] == []
    assert stream["successor_edges"][0]["confidence"] == 0.82
    assert stream["successor_edges"][0]["review_required"] is False
    assert stream["successor_edges"][0]["evidence"][-3:] == [
        "geometry-mutual-neighbor",
        "relation-graph-selected",
        "stream-consensus-3-of-3",
    ]
    promotion = stream["successor_edges"][0]["promotion"]
    assert promotion["kind"] == "independent-local-evidence"
    assert promotion["geometry_score"] >= 0.82
    assert promotion["relation_graph_score"] >= 0.86
    assert promotion["candidate_consensus"] == "3-of-3"


def test_low_confidence_cross_stream_handoff_stays_review_only() -> None:
    proposal = propose_reading_order_sidecar(
        _document(
            [
                _element("left", "Left", 1, 10, 20, column_index=0, confidence=0.5),
                _element("right", "Right", 2, 120, 20, column_index=1, confidence=0.5),
            ]
        )
    )
    page = proposal["pages"][0]

    assert sum(len(stream["successor_edges"]) for stream in page["reading_streams"]) == 0
    assert page["review_transitions"] == [
        {
            "source": "left",
            "target": "right",
            "source_stream_id": "body-column-001",
            "target_stream_id": "body-column-002",
            "reason": "column-handoff",
            "confidence": 0.5,
            "review_required": True,
        }
    ]


def test_same_baseline_grid_edges_stay_review_only() -> None:
    proposal = propose_reading_order_sidecar(
        _document(
            [
                _element("top-left", "Top left", 1, 10, 20, confidence=0.5),
                _element("top-right", "Top right", 2, 120, 20, confidence=0.5),
                _element("bottom-left", "Bottom left", 3, 10, 40, confidence=0.5),
                _element("bottom-right", "Bottom right", 4, 120, 40, confidence=0.5),
            ]
        )
    )
    stream = proposal["pages"][0]["reading_streams"][0]

    assert stream["successor_edges"] == []
    assert len(stream["review_successor_edges"]) == 3
    assert all("promotion" not in edge for edge in stream["review_successor_edges"])


def test_proposal_semantic_quality_tracks_multihop_strict_anchor_paths(tmp_path) -> None:
    source_path = tmp_path / "anchor-path.pdf"
    source_path.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {
                        "page_index": 0,
                        "match_mode": "ordered-subsequence",
                        "text_sequence": ["First anchor", "Last anchor"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document(
        [
            _element("first", "First anchor", 1, 10, 20, column_index=0),
            _element("middle", "Unlabelled middle", 2, 10, 40, column_index=0),
            _element("last", "Last anchor", 3, 10, 60, column_index=0),
        ]
    )

    report = compare_reading_order_sidecar_proposal(
        document,
        source_path,
        tmp_path / "semantic",
        propose_reading_order_sidecar(document),
    )

    assert report["expected_successor_edge_count"] == 1
    assert report["successor_correct_count"] == 0
    assert report["successor_coverage"] == 0.0
    assert report["anchor_transition_count"] == 1
    assert report["strict_anchor_path_correct_count"] == 1
    assert report["strict_anchor_path_coverage"] == 1.0
    assert report["local_reviewable_anchor_path_coverage"] == 1.0
    assert report["reviewable_anchor_path_coverage"] == 1.0


def test_proposal_semantic_quality_tracks_review_transition_anchor_paths(tmp_path) -> None:
    source_path = tmp_path / "transition-path.pdf"
    source_path.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {
                        "page_index": 0,
                        "match_mode": "ordered-subsequence",
                        "text_sequence": ["Left anchor", "Right anchor"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document(
        [
            _element("left", "Left anchor", 1, 10, 20, column_index=0),
            _element("right", "Right anchor", 2, 120, 20, column_index=1),
        ]
    )

    report = compare_reading_order_sidecar_proposal(
        document,
        source_path,
        tmp_path / "semantic",
        propose_reading_order_sidecar(document),
    )

    assert report["strict_anchor_path_coverage"] == 0.0
    assert report["local_reviewable_anchor_path_coverage"] == 0.0
    assert report["review_transition_anchor_path_correct_count"] == 1
    assert report["reviewable_anchor_path_coverage"] == 1.0


def test_proposal_anchor_path_does_not_skip_another_label(tmp_path) -> None:
    source_path = tmp_path / "interloper-path.pdf"
    source_path.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {
                        "page_index": 0,
                        "match_mode": "ordered-subsequence",
                        "text_sequence": ["A", "B", "C"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document(
        [
            _element("a", "A", 1, 10, 20, column_index=0),
            _element("c", "C", 2, 10, 40, column_index=0),
            _element("b", "B", 3, 10, 60, column_index=0),
        ]
    )

    report = compare_reading_order_sidecar_proposal(
        document,
        source_path,
        tmp_path / "semantic",
        propose_reading_order_sidecar(document),
    )

    assert report["strict_anchor_path_correct_count"] == 0
    assert report["reviewable_anchor_path_correct_count"] == 0
    assert report["unresolved_anchor_transition_count"] == 2


def test_textual_structure_blocks_promote_only_with_independent_local_evidence() -> None:
    first = _element("first", "First", 1, 10, 20, confidence=0.9)
    second = _element("second", "Second", 2, 10, 40, confidence=0.9)
    for element in (first, second):
        element.metadata["column_count"] = 1
        element.metadata["reading_order_strategy"] = "mixed-grid-column-flow-v1"
        element.metadata["external_structure_label"] = "text"
        element.metadata["structure_evidence"] = {
            "source": "layout-model",
            "label": "text",
            "bbox_pdf": [10, 20, 90, 60],
        }

    proposal = propose_reading_order_sidecar(_document([first, second]))
    stream = proposal["pages"][0]["reading_streams"][0]

    assert stream["id"] == "external-block-body-001"
    assert stream["proposal"]["origin"] == "external-structure-block"
    assert stream["members"] == ["first", "second"]
    assert stream["review_successor_edges"] == []
    assert stream["successor_edges"][0]["confidence"] == 0.82
    assert "structure-block-membership" in stream["successor_edges"][0]["evidence"]
    assert "stream-consensus-3-of-3" in stream["successor_edges"][0]["evidence"]


def test_generic_model_text_does_not_split_a_stable_native_column_stream() -> None:
    first = _element("first", "First", 1, 10, 20, column_index=0, confidence=0.9)
    second = _element("second", "Second", 2, 10, 40, column_index=0, confidence=0.9)
    for element in (first, second):
        element.metadata["external_structure_label"] = "text"
        element.metadata["structure_evidence"] = {
            "source": "layout-model",
            "label": "text",
            "bbox_pdf": [10, 20, 90, 60],
        }

    proposal = propose_reading_order_sidecar(_document([first, second]))
    stream = proposal["pages"][0]["reading_streams"][0]

    assert stream["id"] == "body-column-001"
    assert stream["proposal"]["origin"] == "column-partition"


def test_structural_flow_segments_split_a_single_body_stream() -> None:
    first = _element("first", "First", 1, 10, 20, confidence=0.5)
    second = _element("second", "Second", 2, 10, 40, confidence=0.5)
    third = _element("third", "Third", 3, 10, 60, confidence=0.5)
    for element, segment in ((first, 1), (second, 2), (third, 2)):
        element.metadata["column_count"] = 1
        element.metadata["flow_segment_index"] = segment
        element.metadata["reading_order_strategy"] = "mixed-grid-column-flow-v1"

    proposal = propose_reading_order_sidecar(_document([first, second, third]))
    streams = {stream["id"]: stream for stream in proposal["pages"][0]["reading_streams"]}

    assert set(streams) == {"body-segment-001", "body-segment-002"}
    assert streams["body-segment-001"]["members"] == ["first"]
    assert streams["body-segment-002"]["members"] == ["second", "third"]
    assert len(streams["body-segment-002"]["successor_edges"]) == 1
    assert streams["body-segment-002"]["review_successor_edges"] == []


def test_proposal_document_nodes_can_seed_an_image_ocr_layer(tmp_path) -> None:
    proposal = propose_reading_order_sidecar(
        _document(
            [
                _element("left-one", "Left one", 1, 10, 20, column_index=0),
                _element("right-one", "Right one", 2, 120, 20, column_index=1),
                _element("left-two", "Left two", 3, 10, 40, column_index=0),
                _element("right-two", "Right two", 4, 120, 40, column_index=1),
            ]
        )
    )
    image_path = tmp_path / "source.png"
    Image.new("RGB", (260, 180), "white").save(image_path)

    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=72)
    document = normalize_ocr_to_ir(rendered, proposal, ocr_fallback="off")
    text_elements = [element for element in document.pages[0].elements if element.source_text]

    assert {element.source_text for element in text_elements} == {"Left one", "Right one", "Left two", "Right two"}
    assert {element.metadata["id"] for element in text_elements} == {"left-one", "right-one", "left-two", "right-two"}
    assert text_elements[0].bbox_pdf.as_list() == [10.0, 20.0, 90.0, 32.0]
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"

    proposal["sidecar_status"] = "accepted"
    apply_structure_evidence(document, proposal)

    assert document.metadata["structure_evidence"]["resolved_relation_edge_count"] == 2
    assert {element.metadata["reading_order_stream_id"] for element in text_elements} == {
        "body-column-001",
        "body-column-002",
    }


def test_proposal_semantic_quality_separates_executable_and_review_edges(tmp_path) -> None:
    source_path = tmp_path / "proposal.pdf"
    source_path.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {
                        "page_index": 0,
                        "match_mode": "ordered-subsequence",
                        "successor_edges": [
                            ["Strict one", "Strict two"],
                            ["Review one", "Review two"],
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    proposal = propose_reading_order_sidecar(
        _document(
            [
                _element("strict-one", "Strict one", 1, 10, 20, column_index=0, confidence=0.9),
                _element("strict-two", "Strict two", 2, 10, 40, column_index=0, confidence=0.9),
                _element("review-one", "Review one", 3, 120, 20, confidence=0.5),
                _element("review-two", "Review two", 4, 120, 140, confidence=0.5),
            ]
        )
    )

    report = compare_reading_order_sidecar_proposal(
        _document(
            [
                _element("strict-one", "Strict one", 1, 10, 20, column_index=0, confidence=0.9),
                _element("strict-two", "Strict two", 2, 10, 40, column_index=0, confidence=0.9),
                _element("review-one", "Review one", 3, 120, 20, confidence=0.5),
                _element("review-two", "Review two", 4, 120, 140, confidence=0.5),
            ]
        ),
        source_path,
        tmp_path / "semantic",
        proposal,
    )

    assert report["ground_truth_available"] is True
    assert report["expected_successor_edge_count"] == 2
    assert report["successor_candidate_edge_count"] == 1
    assert report["successor_labelled_edge_count"] == 1
    assert report["successor_correct_count"] == 1
    assert report["successor_precision"] == 1.0
    assert report["successor_coverage"] == 0.5
    assert report["review_successor_candidate_edge_count"] == 1
    assert report["review_successor_correct_count"] == 1
    assert report["review_successor_precision"] == 1.0
    assert report["review_successor_coverage"] == 0.5
    assert report["reviewable_successor_correct_count"] == 2
    assert report["reviewable_successor_coverage"] == 1.0
    assert (tmp_path / "semantic" / "reading_order_sidecar_proposal_quality_report.json").exists()


def _document(elements: list[ElementIR]) -> DocumentIR:
    return DocumentIR(
        source="synthetic.pdf",
        render_dpi=72,
        page_count=1,
        pages=[
            PageIR(
                page_index=0,
                width_pt=260,
                height_pt=180,
                width_px=260,
                height_px=180,
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
    reading_order: int,
    x0: float,
    y0: float,
    *,
    column_index: int | None = None,
    stream_id: str = "body-main",
    stream_type: str = "body",
    confidence: float = 0.9,
) -> ElementIR:
    bbox = BBox(x0=x0, y0=y0, x1=x0 + 80, y1=y0 + 12)
    return ElementIR(
        id=element_id,
        page_index=0,
        type="text",
        bbox_pdf=bbox,
        bbox_px=bbox,
        source_text=text,
        reading_order=reading_order,
        metadata={
            "column_count": 2 if stream_type == "body" else 1,
            "column_index": column_index,
            "column_span": "column",
            "flow_segment_index": 1,
            "reading_order_confidence": confidence,
            "reading_order_strategy": "column-flow-v1",
            "reading_order_stream_id": stream_id,
            "reading_order_stream_type": stream_type,
        },
    )
