from __future__ import annotations

import copy
import json

import pytest
from typer.testing import CliRunner

import scriptorium.cli as cli
import scriptorium.hierarchical_order as hierarchical_order
from scriptorium.chunkr_order_ranker import ChunkrOrderPredictionResult
from scriptorium.hierarchical_order import build_hierarchical_order_proposal
from scriptorium.reading_order import (
    RelationGraphEdgeDiagnostics,
    RelationGraphOrderEvidence,
)
from scriptorium.reading_order_sidecar import (
    is_unaccepted_reading_order_sidecar,
    reading_order_sidecar_summary,
)


def test_hierarchy_groups_lines_without_using_member_list_order() -> None:
    payload = _two_column_payload()

    result = build_hierarchical_order_proposal(payload)

    assert result.payload["runtime_reorder"] is False
    assert result.payload["candidate_consensus_policy"] == "isolated"
    assert result.diagnostics["explicit_membership_count"] == 2
    assert result.diagnostics["geometry_membership_count"] == 2
    assert result.diagnostics["unassigned_element_count"] == 1
    assert result.diagnostics["ambiguous_element_count"] == 0
    assert result.payload["unassigned_element_ids"] == ["footer"]
    assert (
        result.payload["candidate_ordered_element_ids"]
        == result.payload["base_ordered_element_ids"]
    )
    assert result.diagnostics["fine_relation_cross_region_edge_count"] == 0
    assert result.payload["pages"][0]["review_transitions"] == []
    assert (
        result.diagnostics[
            "candidate_expansion_suppressed_missing_cross_region_evidence"
        ]
        is True
    )
    assert is_unaccepted_reading_order_sidecar(result.payload) is True
    assert reading_order_sidecar_summary(result.payload) == {
        "stream_count": 2,
        "member_count": 4,
        "successor_edge_count": 0,
        "review_successor_edge_count": 2,
        "review_transition_count": 0,
        "strict_block_transition_count": 0,
        "review_block_transition_count": 0,
        "stream_type_counts": {"body": 2},
        "stream_origin_counts": {"hierarchical-region-membership": 2},
    }
    streams = {
        stream["region_id"]: stream
        for stream in result.payload["pages"][0]["reading_streams"]
    }
    assert streams["region-left"]["members"] == ["left-top", "left-bottom"]
    assert streams["region-right"]["members"] == ["right-top", "right-bottom"]
    assert streams["region-left"]["successor_edges"] == []
    assert streams["region-left"]["review_successor_edges"][0]["source"] == ("left-top")
    assert streams["region-left"]["review_successor_edges"][0]["target"] == (
        "left-bottom"
    )
    assert set(result.payload["candidate_ordered_element_ids"]) == {
        "left-top",
        "left-bottom",
        "right-top",
        "right-bottom",
        "footer",
    }
    assert result.diagnostics["promotion_decision"] == (
        "review-only-fine-relation-graph"
    )
    assert result.payload["pages"][0]["reading_order_tree"]["type"] == ("ordered-group")

    reordered = copy.deepcopy(payload)
    reordered["elements"].reverse()
    reordered["regions"].reverse()
    reordered["regions"][1]["member_ids"].reverse()
    second = build_hierarchical_order_proposal(reordered)

    assert (
        second.payload["base_ordered_element_ids"]
        == result.payload["base_ordered_element_ids"]
    )
    assert (
        second.payload["coarse_ordered_region_ids"]
        == result.payload["coarse_ordered_region_ids"]
    )
    assert (
        second.payload["pages"][0]["reading_streams"]
        == result.payload["pages"][0]["reading_streams"]
    )
    assert (
        second.payload["candidate_ordered_element_ids"]
        == result.payload["candidate_ordered_element_ids"]
    )


def test_hierarchy_emits_relation_graph_transition_at_stream_boundary() -> None:
    elements = []
    regions = []
    for side, x0 in (("left", 10), ("right", 110)):
        member_ids = []
        for line_index in range(3):
            element_id = f"{side}-{line_index}"
            member_ids.append(element_id)
            elements.append(
                {
                    "id": element_id,
                    "box": [x0, 10 + line_index * 20, x0 + 70, 20 + line_index * 20],
                    "role": "Text Block",
                }
            )
        regions.append(
            {
                "id": f"region-{side}",
                "box": [x0 - 5, 5, x0 + 80, 65],
                "role": "Text Block",
                "member_ids": member_ids,
            }
        )
    payload = {
        "id": "relation-boundary",
        "width": 200,
        "height": 200,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": elements,
        "regions": regions,
    }

    result = build_hierarchical_order_proposal(payload)

    assert result.payload["coarse_ordered_region_ids"] == [
        "region-left",
        "region-right",
    ]
    transition = result.payload["pages"][0]["review_transitions"][0]
    assert transition["source"] == "left-2"
    assert transition["target"] == "right-0"
    assert transition["boundary_aligned"] is True
    assert transition["reason"] == "fine-relation-graph-stream-boundary"
    assert transition["relation_graph"]["score"] >= 0.5
    assert result.diagnostics["fine_relation_cross_region_edge_count"] == 1
    assert result.diagnostics["candidate_expansion_complete_cross_region_chain"] is True
    assert result.diagnostics["candidate_expansion_enabled"] is True

    reordered = copy.deepcopy(payload)
    reordered["elements"].reverse()
    reordered["regions"].reverse()
    for region in reordered["regions"]:
        region["member_ids"].reverse()
    second = build_hierarchical_order_proposal(reordered)
    assert second.payload["coarse_ordered_region_ids"] == (
        result.payload["coarse_ordered_region_ids"]
    )
    assert second.payload["pages"][0]["review_transitions"] == (
        result.payload["pages"][0]["review_transitions"]
    )
    assert second.payload["pages"][0]["cross_region_relation_evidence"] == (
        result.payload["pages"][0]["cross_region_relation_evidence"]
    )


def test_hierarchy_suppresses_region_cycles_from_fine_relation_edges(
    monkeypatch,
) -> None:
    payload = {
        "id": "region-cycle",
        "width": 100,
        "height": 100,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "one", "box": [10, 10, 40, 20], "role": "Text Block"},
            {"id": "two", "box": [10, 35, 40, 45], "role": "Text Block"},
            {"id": "three", "box": [10, 60, 40, 70], "role": "Text Block"},
        ],
        "regions": [
            {
                "id": f"region-{element_id}",
                "box": box,
                "role": "Text Block",
                "member_ids": [element_id],
            }
            for element_id, box in (
                ("one", [5, 5, 45, 25]),
                ("two", [5, 30, 45, 50]),
                ("three", [5, 55, 45, 75]),
            )
        ],
    }

    diagnostics = tuple(
        RelationGraphEdgeDiagnostics(
            source=source,
            target=target,
            score=score,
            source_candidate_count=1,
            target_candidate_count=1,
            source_alternative_score=None,
            target_alternative_score=None,
            source_margin=None,
            target_margin=None,
            source_regret=score,
            target_regret=score,
            selection_regret=score * 2,
            selection_step=step,
        )
        for step, (source, target, score) in enumerate(
            ((0, 1, 0.9), (1, 2, 0.8), (2, 0, 0.7)),
            start=1,
        )
    )
    monkeypatch.setattr(
        hierarchical_order,
        "infer_relation_graph_order_evidence",
        lambda *_args: RelationGraphOrderEvidence((0, 1, 2), diagnostics),
    )

    result = build_hierarchical_order_proposal(payload)

    assert result.diagnostics["fine_relation_boundary_aligned_edge_count"] == 3
    assert result.diagnostics["fine_relation_region_cycle_suppressed_count"] == 1
    assert result.diagnostics["emitted_cross_region_transition_count"] == 2
    evidence = result.payload["pages"][0]["cross_region_relation_evidence"]
    assert sum(item["suppression_reason"] == "region-cycle" for item in evidence) == 1


def test_hierarchy_model_reorders_regions_but_preserves_local_lines(
    monkeypatch,
) -> None:
    payload = _two_column_payload()
    payload["elements"] = [
        element for element in payload["elements"] if element["id"] != "footer"
    ]

    monkeypatch.setattr(
        hierarchical_order,
        "predict_chunkr_block_order",
        _fake_chunkr_prediction(in_envelope=True),
    )
    result = build_hierarchical_order_proposal(
        payload,
        chunkr_model="unused-test-model.joblib",
    )

    assert result.payload["coarse_ordered_region_ids"] == [
        "region-right",
        "region-left",
    ]
    assert result.payload["proposed_coarse_ordered_region_ids"] == [
        "region-right",
        "region-left",
    ]
    assert result.diagnostics["emitted_cross_region_transition_count"] == 1
    assert result.diagnostics["suppressed_cross_region_transition_count"] == 0
    assert result.diagnostics["promotion_decision"] == "review-only-model-in-envelope"
    transition = result.payload["pages"][0]["review_transitions"][0]
    assert transition["source"] == "right-bottom"
    assert transition["target"] == "left-top"
    candidate = result.payload["candidate_ordered_element_ids"]
    assert candidate.index("right-top") < candidate.index("right-bottom")
    assert candidate.index("left-top") < candidate.index("left-bottom")
    assert max(candidate.index(item) for item in ("right-top", "right-bottom")) < min(
        candidate.index(item) for item in ("left-top", "left-bottom")
    )


def test_hierarchy_suppresses_ood_model_transitions(monkeypatch) -> None:
    payload = _two_column_payload()
    monkeypatch.setattr(
        hierarchical_order,
        "predict_chunkr_block_order",
        _fake_chunkr_prediction(in_envelope=False),
    )

    result = build_hierarchical_order_proposal(
        payload,
        chunkr_model="unused-test-model.joblib",
    )

    assert result.payload["coarse_ordered_region_ids"] == [
        "region-right",
        "region-left",
    ]
    assert result.payload["pages"][0]["review_transitions"] == []
    assert (
        result.payload["candidate_ordered_element_ids"]
        == result.payload["base_ordered_element_ids"]
    )
    assert result.diagnostics["emitted_cross_region_transition_count"] == 0
    assert result.diagnostics["suppressed_cross_region_transition_count"] == 1
    assert result.diagnostics["promotion_decision"] == (
        "reject-cross-region-transitions-page-profile-ood"
    )
    assert result.payload["proposed_coarse_ordered_region_ids"] == []
    assert result.payload["pages"][0]["reading_order_tree"]["type"] == (
        "unordered-group"
    )
    assert result.diagnostics["coarse_order_suppressed"] is True


def test_hierarchy_keeps_ambiguous_geometry_unassigned() -> None:
    payload = {
        "id": "ambiguous",
        "width": 100,
        "height": 100,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "line", "box": [20, 20, 80, 30], "role": "Text Block"},
        ],
        "regions": [
            {"id": "first", "box": [10, 10, 90, 40], "role": "Text Block"},
            {"id": "second", "box": [10, 10, 90, 40], "role": "Text Block"},
        ],
    }

    result = build_hierarchical_order_proposal(payload)

    assert result.payload["memberships"] == [
        {
            "element_id": "line",
            "region_id": None,
            "method": "unassigned",
            "coverage": 1.0,
            "runner_up_coverage": 1.0,
            "margin": 0.0,
            "reason": "ambiguous-region-overlap",
        }
    ]
    assert result.payload["pages"][0]["reading_streams"] == []
    assert result.diagnostics["ambiguous_element_count"] == 1


def test_hierarchy_margin_compares_near_threshold_runner_up() -> None:
    payload = {
        "id": "near-threshold",
        "width": 100,
        "height": 100,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "line", "box": [0, 0, 100, 10], "role": "Text Block"},
        ],
        "regions": [
            {"id": "coverage-80", "box": [0, 0, 80, 10], "role": "Text Block"},
            {"id": "coverage-79", "box": [0, 0, 79, 10], "role": "Text Block"},
        ],
    }

    result = build_hierarchical_order_proposal(payload)

    membership = result.payload["memberships"][0]
    assert membership["region_id"] is None
    assert membership["coverage"] == 0.8
    assert membership["runner_up_coverage"] == 0.79
    assert membership["margin"] == 0.01
    assert membership["reason"] == "ambiguous-region-overlap"


def test_hierarchy_uses_text_and_local_gap_to_correct_geometry_parent() -> None:
    payload = {
        "id": "text-parent",
        "width": 100,
        "height": 120,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {
                "id": "footer-line",
                "box": [10, 70, 90, 80],
                "role": "Footer",
                "text": "Conference proceedings 2026",
            },
        ],
        "regions": [
            {
                "id": "footnote-region",
                "box": [5, 65, 95, 82],
                "role": "Footnote",
                "text": "Author contribution details",
            },
            {
                "id": "footer-region",
                "box": [5, 90, 95, 105],
                "role": "Footer",
                "text": "Conference proceedings 2026",
            },
        ],
    }

    result = build_hierarchical_order_proposal(payload)

    membership = result.payload["memberships"][0]
    assert membership["region_id"] == "footer-region"
    assert membership["method"] == "text-geometry-parent"
    assert membership["coverage"] == 0.0
    assert membership["text_parent_score"] > 0.8
    assert membership["text_match_score"] == 1.0
    assert membership["spatial_gap_ratio"] == 0.25
    assert membership["evidence_confidence"] == 0.76
    assert result.diagnostics["text_geometry_membership_count"] == 1
    assert result.diagnostics["geometry_membership_count"] == 0


def test_hierarchy_does_not_choose_between_ambiguous_text_parents() -> None:
    payload = {
        "id": "ambiguous-text-parent",
        "width": 100,
        "height": 100,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {
                "id": "repeated-line",
                "box": [20, 20, 80, 30],
                "role": "Text Block",
                "text": "Repeated section title",
            },
        ],
        "regions": [
            {
                "id": "first",
                "box": [10, 10, 90, 40],
                "role": "Section Header",
                "text": "Repeated section title",
            },
            {
                "id": "second",
                "box": [10, 10, 90, 40],
                "role": "Section Header",
                "text": "Repeated section title",
            },
        ],
    }

    result = build_hierarchical_order_proposal(payload)

    assert result.payload["memberships"][0]["region_id"] is None
    assert result.payload["memberships"][0]["reason"] == (
        "ambiguous-region-overlap"
    )
    assert result.diagnostics["text_geometry_membership_count"] == 0


def test_hierarchy_unique_text_parent_resolves_geometry_tie() -> None:
    payload = {
        "id": "text-resolves-geometry-tie",
        "width": 100,
        "height": 100,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {
                "id": "line",
                "box": [20, 20, 80, 30],
                "role": "Text Block",
                "text": "Unique paragraph line",
            },
        ],
        "regions": [
            {
                "id": "correct",
                "box": [10, 10, 90, 40],
                "role": "Text Block",
                "text": "Unique paragraph line followed by more text",
            },
            {
                "id": "overlapping",
                "box": [10, 10, 90, 40],
                "role": "Figure",
                "text": "Unrelated graphical content",
            },
        ],
    }

    result = build_hierarchical_order_proposal(payload)

    membership = result.payload["memberships"][0]
    assert membership["region_id"] == "correct"
    assert membership["method"] == "text-geometry-parent"
    assert result.diagnostics["text_geometry_membership_count"] == 1
    assert result.diagnostics["ambiguous_element_count"] == 0


def test_hierarchy_continuity_resolves_geometry_tie_between_same_region_neighbors() -> None:
    payload = {
        "id": "continuity-resolves-tie",
        "width": 100,
        "height": 100,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "top", "box": [10, 10, 90, 20], "role": "Text Block"},
            {"id": "middle", "box": [10, 35, 90, 45], "role": "Text Block"},
            {"id": "bottom", "box": [10, 60, 90, 70], "role": "Text Block"},
        ],
        "regions": [
            {
                "id": "correct",
                "box": [5, 5, 95, 75],
                "role": "Text Block",
                "member_ids": ["top", "bottom"],
            },
            {
                "id": "overlap",
                "box": [5, 5, 95, 75],
                "role": "Picture",
            },
        ],
    }

    result = build_hierarchical_order_proposal(payload)

    membership = next(
        item
        for item in result.payload["memberships"]
        if item["element_id"] == "middle"
    )
    assert membership["region_id"] == "correct"
    assert membership["method"] == "relation-base-continuity-parent"
    assert membership["evidence"] == [
        "geometry-tied-candidate",
        "relation-graph-bidirectional-continuity",
        "selected-order-bidirectional-continuity",
    ]
    assert result.diagnostics["relation_base_continuity_membership_count"] == 1
    assert result.diagnostics["ambiguous_element_count"] == 0

    reordered = copy.deepcopy(payload)
    reordered["elements"].reverse()
    reordered["regions"].reverse()
    second = build_hierarchical_order_proposal(reordered)
    assert second.payload["memberships"] == result.payload["memberships"]


def test_hierarchy_continuity_does_not_choose_between_different_neighbor_regions() -> None:
    payload = {
        "id": "continuity-keeps-tie",
        "width": 100,
        "height": 100,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "top", "box": [10, 10, 90, 20], "role": "Text Block"},
            {"id": "middle", "box": [10, 35, 90, 45], "role": "Text Block"},
            {"id": "bottom", "box": [10, 60, 90, 70], "role": "Text Block"},
        ],
        "regions": [
            {
                "id": "top-region",
                "box": [5, 5, 95, 50],
                "role": "Text Block",
                "member_ids": ["top"],
            },
            {
                "id": "bottom-region",
                "box": [5, 30, 95, 75],
                "role": "Text Block",
                "member_ids": ["bottom"],
            },
        ],
    }

    result = build_hierarchical_order_proposal(payload)

    membership = next(
        item
        for item in result.payload["memberships"]
        if item["element_id"] == "middle"
    )
    assert membership["region_id"] is None
    assert membership["reason"] == "ambiguous-region-overlap"
    assert result.diagnostics["relation_base_continuity_membership_count"] == 0


def test_hierarchy_boundary_continuity_uses_unique_tied_region_text() -> None:
    payload = {
        "id": "boundary-text-continuity",
        "width": 100,
        "height": 100,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {
                "id": "top",
                "box": [10, 10, 90, 20],
                "role": "Text Block",
                "text": "Alpha",
            },
            {
                "id": "middle",
                "box": [10, 35, 90, 45],
                "role": "Text Block",
                "text": "Fig 2",
            },
            {
                "id": "bottom",
                "box": [10, 60, 90, 70],
                "role": "Text Block",
                "text": "Beta",
            },
        ],
        "regions": [
            {
                "id": "top-region",
                "box": [5, 5, 95, 50],
                "role": "Text Block",
                "text": "Alpha Fig 2",
                "member_ids": ["top"],
            },
            {
                "id": "bottom-region",
                "box": [5, 30, 95, 75],
                "role": "Text Block",
                "text": "Beta",
                "member_ids": ["bottom"],
            },
        ],
    }

    result = build_hierarchical_order_proposal(payload)

    membership = next(
        item
        for item in result.payload["memberships"]
        if item["element_id"] == "middle"
    )
    assert membership["region_id"] == "top-region"
    assert membership["method"] == "relation-base-boundary-text-parent"
    assert membership["evidence"] == [
        "geometry-tied-candidate",
        "relation-graph-boundary-split",
        "selected-order-boundary-split",
        "unique-tied-region-text-containment",
    ]
    assert result.diagnostics["relation_base_boundary_text_membership_count"] == 1
    assert result.diagnostics["ambiguous_element_count"] == 0

    ambiguous = copy.deepcopy(payload)
    ambiguous["regions"][1]["text"] = "Fig 2 Beta"
    second = build_hierarchical_order_proposal(ambiguous)
    second_membership = next(
        item
        for item in second.payload["memberships"]
        if item["element_id"] == "middle"
    )
    assert second_membership["region_id"] is None
    assert second_membership["reason"] == "ambiguous-region-overlap"
    assert second.diagnostics["relation_base_boundary_text_membership_count"] == 0


def test_hierarchy_does_not_jump_across_empty_coarse_region(monkeypatch) -> None:
    payload = _two_column_payload()
    payload["regions"].append(
        {
            "id": "region-figure",
            "box": [90, 80, 110, 140],
            "role": "Picture",
        }
    )

    def predict(_payload, _model_path):
        return ChunkrOrderPredictionResult(
            ("region-left", "region-figure", "region-right"),
            {"chunkr_order_ranker": {"model_sha256": "b" * 64}},
            {
                "page_profile_in_envelope": True,
                "page_profile_outlier_names": [],
                "mean_adjacent_precedence": 0.9,
            },
        )

    monkeypatch.setattr(
        hierarchical_order,
        "predict_chunkr_block_order",
        predict,
    )
    result = build_hierarchical_order_proposal(
        payload,
        chunkr_model="unused-test-model.joblib",
    )

    assert result.payload["pages"][0]["review_transitions"] == []
    assert result.diagnostics["potential_cross_region_transition_count"] == 2
    assert result.diagnostics["eligible_cross_region_transition_count"] == 0
    assert result.diagnostics["empty_region_boundary_count"] == 2
    assert result.diagnostics["suppressed_cross_region_transition_count"] == 2
    assert result.diagnostics[
        "candidate_expansion_suppressed_incomplete_cross_region_chain"
    ] is True
    tree_children = result.payload["pages"][0]["reading_order_tree"]["children"]
    assert [child["region_id"] for child in tree_children] == [
        "region-left",
        "region-figure",
        "region-right",
    ]
    assert tree_children[1]["membership_status"] == "empty"


def test_hierarchy_does_not_jump_across_unassigned_line(monkeypatch) -> None:
    payload = {
        "id": "unassigned-gap",
        "width": 100,
        "height": 120,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "top", "box": [10, 10, 90, 20], "role": "Text Block"},
            {"id": "gap", "box": [10, 50, 90, 60], "role": "Formula"},
            {"id": "bottom", "box": [10, 90, 90, 100], "role": "Text Block"},
        ],
        "regions": [
            {
                "id": "region-top",
                "box": [5, 5, 95, 25],
                "role": "Text Block",
                "member_ids": ["top"],
            },
            {
                "id": "region-bottom",
                "box": [5, 85, 95, 105],
                "role": "Text Block",
                "member_ids": ["bottom"],
            },
        ],
    }

    def predict(_payload, _model_path):
        return ChunkrOrderPredictionResult(
            ("region-top", "region-bottom"),
            {"chunkr_order_ranker": {"model_sha256": "c" * 64}},
            {
                "page_profile_in_envelope": True,
                "page_profile_outlier_names": [],
                "mean_adjacent_precedence": 0.9,
            },
        )

    monkeypatch.setattr(hierarchical_order, "predict_chunkr_block_order", predict)
    result = build_hierarchical_order_proposal(
        payload,
        chunkr_model="unused-test-model.joblib",
    )

    assert result.payload["pages"][0]["review_transitions"] == []
    assert result.diagnostics["eligible_cross_region_transition_count"] == 0
    assert result.diagnostics["empty_region_boundary_count"] == 0
    assert result.diagnostics["unassigned_gap_boundary_count"] == 1
    assert result.diagnostics["suppressed_cross_region_transition_count"] == 1
    assert result.payload["candidate_ordered_element_ids"] == [
        "top",
        "gap",
        "bottom",
    ]


def test_hierarchy_does_not_expand_partial_cross_region_chain() -> None:
    payload = {
        "id": "partial-chain",
        "width": 100,
        "height": 120,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "top", "box": [10, 10, 90, 20], "role": "Text Block"},
            {"id": "middle", "box": [10, 35, 90, 45], "role": "Text Block"},
            {"id": "bottom", "box": [10, 90, 90, 100], "role": "Text Block"},
        ],
        "regions": [
            {
                "id": "region-top",
                "box": [5, 5, 95, 25],
                "role": "Text Block",
                "member_ids": ["top"],
            },
            {
                "id": "region-middle",
                "box": [5, 30, 95, 50],
                "role": "Text Block",
                "member_ids": ["middle"],
            },
            {
                "id": "region-figure",
                "box": [5, 55, 95, 70],
                "role": "Picture",
            },
            {
                "id": "region-bottom",
                "box": [5, 85, 95, 105],
                "role": "Text Block",
                "member_ids": ["bottom"],
            },
        ],
    }

    result = build_hierarchical_order_proposal(payload)

    assert result.diagnostics["unassigned_element_count"] == 0
    assert result.diagnostics["coarse_adjacent_region_pair_count"] == 3
    assert result.diagnostics["potential_cross_region_transition_count"] == 2
    assert result.diagnostics["emitted_cross_region_transition_count"] == 2
    assert result.diagnostics["candidate_expansion_enabled"] is False
    assert result.diagnostics[
        "candidate_expansion_suppressed_incomplete_cross_region_chain"
    ] is True
    assert result.payload["candidate_ordered_element_ids"] == [
        "top",
        "middle",
        "bottom",
    ]


def test_hierarchy_rejects_conflicting_membership_and_order_answers() -> None:
    payload = _two_column_payload()
    payload["regions"][1]["member_ids"] = ["left-top"]
    with pytest.raises(ValueError, match="belongs to explicit regions"):
        build_hierarchical_order_proposal(payload)

    tainted = _two_column_payload()
    tainted["metadata"] = {"reading_streams": [{"members": ["left-top"]}]}
    with pytest.raises(ValueError, match="reading_streams"):
        build_hierarchical_order_proposal(tainted)

    undeclared = _two_column_payload()
    del undeclared["region_granularity"]
    with pytest.raises(ValueError, match="region_granularity must explicitly declare"):
        build_hierarchical_order_proposal(undeclared)


def test_build_hierarchical_order_cli(tmp_path) -> None:
    source = tmp_path / "hierarchy.json"
    output = tmp_path / "proposal.json"
    source.write_text(json.dumps(_two_column_payload()), encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        [
            "build-hierarchical-order",
            str(source),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Membership (assigned/ambiguous/unassigned): 4/0/1" in result.output
    proposal = json.loads(output.read_text(encoding="utf-8"))
    assert proposal["schema"] == "scriptorium-hierarchical-order-proposal/v1"
    assert proposal["runtime_reorder"] is False


def _fake_chunkr_prediction(*, in_envelope: bool):
    def predict(payload, model_path):
        assert model_path == "unused-test-model.joblib"
        assert set(element["id"] for element in payload["elements"]) == {
            "region-left",
            "region-right",
        }
        diagnostics = {
            "page_profile_in_envelope": in_envelope,
            "page_profile_outlier_names": [] if in_envelope else ["text_block_ratio"],
            "mean_adjacent_precedence": 0.9,
        }
        return ChunkrOrderPredictionResult(
            ("region-right", "region-left"),
            {"chunkr_order_ranker": {"model_sha256": "a" * 64}},
            diagnostics,
        )

    return predict


def _two_column_payload() -> dict:
    return {
        "id": "two-column",
        "width": 200,
        "height": 200,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "right-bottom", "box": [110, 40, 180, 50], "role": "Text Block"},
            {"id": "left-top", "box": [10, 10, 80, 20], "role": "Text Block"},
            {"id": "footer", "box": [10, 180, 190, 190], "role": "Footer"},
            {"id": "right-top", "box": [110, 10, 180, 20], "role": "Text Block"},
            {"id": "left-bottom", "box": [10, 40, 80, 50], "role": "Text Block"},
        ],
        "regions": [
            {
                "id": "region-left",
                "box": [5, 5, 90, 60],
                "role": "Text Block",
                "member_ids": ["left-bottom", "left-top"],
            },
            {
                "id": "region-right",
                "box": [105, 5, 190, 60],
                "role": "Text Block",
            },
        ],
    }
