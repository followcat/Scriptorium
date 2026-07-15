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
    RelationGraphCandidateEdge,
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
    assert [
        (edge["source"], edge["target"], edge["reason"])
        for edge in result.payload["pages"][0]["review_transitions"]
    ] == [("right-bottom", "footer", "unassigned-base-order-fallback")]
    assert result.diagnostics["unassigned_fallback_stream_count"] == 1
    assert result.diagnostics["unassigned_fallback_transition_emitted_count"] == 1
    assert (
        result.diagnostics[
            "candidate_expansion_suppressed_missing_cross_region_evidence"
        ]
        is True
    )
    assert is_unaccepted_reading_order_sidecar(result.payload) is True
    assert reading_order_sidecar_summary(result.payload) == {
        "stream_count": 3,
        "member_count": 5,
        "successor_edge_count": 0,
        "review_successor_edge_count": 2,
        "review_transition_count": 1,
        "strict_block_transition_count": 0,
        "review_block_transition_count": 1,
        "stream_type_counts": {"body": 2, "unassigned-fallback": 1},
        "stream_origin_counts": {
            "hierarchical-region-membership": 2,
            "unassigned-base-order-fallback": 1,
        },
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


def test_hierarchy_replaces_one_lower_confidence_native_region_edge(
    monkeypatch,
) -> None:
    payload = {
        "id": "semantic-replacement",
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
    native_diagnostic = RelationGraphEdgeDiagnostics(
        source=0,
        target=1,
        score=0.6,
        source_candidate_count=1,
        target_candidate_count=1,
        source_alternative_score=None,
        target_alternative_score=None,
        source_margin=None,
        target_margin=None,
        source_regret=0.6,
        target_regret=0.6,
        selection_regret=1.2,
        selection_step=0,
    )
    monkeypatch.setattr(
        hierarchical_order,
        "infer_relation_graph_order_evidence",
        lambda *_args: RelationGraphOrderEvidence(
            (0, 1, 2),
            (native_diagnostic,),
        ),
    )

    result = build_hierarchical_order_proposal(
        payload,
        external_successor_edges=[
            {
                "source": "one",
                "target": "three",
                "confidence": 0.91,
                "rank": 1,
            }
        ],
    )

    transitions = result.payload["pages"][0]["review_transitions"]
    assert [(edge["source"], edge["target"]) for edge in transitions] == [
        ("one", "three")
    ]
    assert transitions[0]["provenance"]["relation_source"] == (
        "semantic-successor-ranker"
    )
    assert "semantic-successor-ranker" in transitions[0]["evidence"]
    assert result.diagnostics["external_relation_input_edge_count"] == 1
    assert result.diagnostics["external_relation_path_selected_edge_count"] == 1
    assert result.diagnostics["external_relation_novel_selected_edge_count"] == 1
    assert result.diagnostics["external_relation_emitted_transition_count"] == 1
    assert result.diagnostics["external_relation_replacement_count"] == 1
    assert "successor_edges" not in payload


def test_hierarchy_does_not_fill_empty_region_slot_from_semantics_alone() -> None:
    payload = _two_column_payload()

    result = build_hierarchical_order_proposal(
        payload,
        external_successor_edges=[
            {
                "source": "left-bottom",
                "target": "right-top",
                "confidence": 0.99,
                "rank": 1,
            }
        ],
    )

    assert [
        edge["reason"]
        for edge in result.payload["pages"][0]["review_transitions"]
    ] == ["unassigned-base-order-fallback"]
    evidence = result.payload["pages"][0]["cross_region_relation_evidence"]
    external = [
        edge for edge in evidence if edge["relation_source"] == "semantic-successor-ranker"
    ]
    assert external[0]["suppression_reason"] == (
        "semantic-requires-single-native-region-conflict"
    )
    assert result.diagnostics["external_relation_replacement_count"] == 0


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


def test_hierarchy_keeps_figure_and_table_relations_as_object_branches(
    monkeypatch,
) -> None:
    roles = ("Text", "Figure", "Text", "Text", "Table", "Text")
    element_ids = (
        "body-before-figure",
        "figure-object",
        "figure-caption",
        "table-caption",
        "table-object",
        "body-after-table",
    )
    elements = [
        {
            "id": element_id,
            "box": [10, 10 + index * 15, 90, 20 + index * 15],
            "role": "Text",
        }
        for index, element_id in enumerate(element_ids)
    ]
    payload = {
        "id": "object-branch-endpoints",
        "width": 100,
        "height": 120,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": elements,
        "regions": [
            {
                "id": f"region-{element_id}",
                "box": element["box"],
                "role": role,
                "member_ids": [element_id],
            }
            for element_id, element, role in zip(
                element_ids,
                elements,
                roles,
                strict=True,
            )
        ],
    }
    stable_elements = hierarchical_order._nodes_from_payload(
        payload["elements"],
        kind="element",
        width=100,
        height=120,
        maximum=hierarchical_order.MAX_HIERARCHY_ELEMENTS,
    )
    index_by_id = {
        element.id: index for index, element in enumerate(stable_elements)
    }
    edge_specs = (
        ("body-before-figure", "figure-object", 0.99),
        ("figure-object", "figure-caption", 0.98),
        ("table-caption", "table-object", 0.97),
        ("table-object", "body-after-table", 0.96),
    )
    diagnostics = tuple(
        RelationGraphEdgeDiagnostics(
            source=index_by_id[source],
            target=index_by_id[target],
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
        for step, (source, target, score) in enumerate(edge_specs)
    )
    monkeypatch.setattr(
        hierarchical_order,
        "infer_relation_graph_order_evidence",
        lambda *_args: RelationGraphOrderEvidence(
            tuple(range(len(element_ids))),
            diagnostics,
        ),
    )

    result = build_hierarchical_order_proposal(payload)

    transitions = result.payload["pages"][0]["review_transitions"]
    assert {(edge["source"], edge["target"]) for edge in transitions} == {
        ("figure-object", "figure-caption"),
        ("table-caption", "table-object"),
    }
    evidence = {
        (edge["source"], edge["target"]): edge
        for edge in result.payload["pages"][0]["cross_region_relation_evidence"]
    }
    assert evidence[("body-before-figure", "figure-object")][
        "suppression_reason"
    ] == "figure-region-root-branch"
    assert evidence[("table-object", "body-after-table")][
        "suppression_reason"
    ] == "table-region-terminal-branch"
    assert result.diagnostics["fine_relation_object_branch_suppressed_count"] == 2
    assert result.diagnostics["fine_relation_figure_target_suppressed_count"] == 1
    assert result.diagnostics["fine_relation_table_source_suppressed_count"] == 1


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
    assert [
        edge["reason"]
        for edge in result.payload["pages"][0]["review_transitions"]
    ] == ["unassigned-base-order-fallback"]
    assert (
        result.payload["candidate_ordered_element_ids"]
        == result.payload["base_ordered_element_ids"]
    )
    assert result.diagnostics["emitted_cross_region_transition_count"] == 1
    assert result.diagnostics["unassigned_fallback_transition_emitted_count"] == 1
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
    assert result.payload["pages"][0]["reading_streams"] == [
        {
            "id": "hierarchy-unassigned-001",
            "type": "unassigned-fallback",
            "region_id": None,
            "order_policy": "preserve-selected-auto-relative-order",
            "members": ["line"],
            "successor_edges": [],
            "review_successor_edges": [],
            "proposal": {
                "origin": "unassigned-base-order-fallback",
                "confidence": 0.5,
                "evidence": [
                    "membership-abstention",
                    "preserve-selected-auto-relative-order",
                ],
            },
        }
    ]
    assert result.diagnostics["unassigned_fallback_stream_count"] == 1
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

    assert [
        edge["reason"]
        for edge in result.payload["pages"][0]["review_transitions"]
    ] == ["unassigned-base-order-fallback"]
    assert result.diagnostics["potential_cross_region_transition_count"] == 3
    assert result.diagnostics["eligible_cross_region_transition_count"] == 1
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

    assert [
        (edge["source"], edge["target"], edge["reason"])
        for edge in result.payload["pages"][0]["review_transitions"]
    ] == [
        ("top", "gap", "unassigned-base-order-fallback"),
        ("gap", "bottom", "unassigned-base-order-fallback"),
    ]
    assert result.payload["pages"][0]["reading_streams"][-1]["members"] == ["gap"]
    assert result.diagnostics["eligible_cross_region_transition_count"] == 2
    assert result.diagnostics["empty_region_boundary_count"] == 0
    assert result.diagnostics["unassigned_gap_boundary_count"] == 1
    assert result.diagnostics["suppressed_cross_region_transition_count"] == 1
    assert result.payload["candidate_ordered_element_ids"] == [
        "top",
        "gap",
        "bottom",
    ]


def test_hierarchy_unassigned_fallback_preserves_existing_relation_degree(
    monkeypatch,
) -> None:
    payload = {
        "id": "unassigned-degree-conflict",
        "width": 100,
        "height": 120,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "one", "box": [10, 10, 90, 20], "role": "Text"},
            {"id": "gap", "box": [10, 50, 90, 60], "role": "Formula"},
            {"id": "two", "box": [10, 90, 90, 100], "role": "Text"},
        ],
        "regions": [
            {
                "id": "region-one",
                "box": [5, 5, 95, 25],
                "role": "Text",
                "member_ids": ["one"],
            },
            {
                "id": "region-two",
                "box": [5, 85, 95, 105],
                "role": "Text",
                "member_ids": ["two"],
            },
        ],
    }
    stable_elements = hierarchical_order._nodes_from_payload(
        payload["elements"],
        kind="element",
        width=100,
        height=120,
        maximum=hierarchical_order.MAX_HIERARCHY_ELEMENTS,
    )
    index_by_id = {
        element.id: index for index, element in enumerate(stable_elements)
    }
    diagnostic = RelationGraphEdgeDiagnostics(
        source=index_by_id["one"],
        target=index_by_id["two"],
        score=0.95,
        source_candidate_count=1,
        target_candidate_count=1,
        source_alternative_score=None,
        target_alternative_score=None,
        source_margin=None,
        target_margin=None,
        source_regret=0.95,
        target_regret=0.95,
        selection_regret=1.9,
        selection_step=0,
    )
    monkeypatch.setattr(
        hierarchical_order,
        "infer_relation_graph_order_evidence",
        lambda *_args: RelationGraphOrderEvidence(
            tuple(range(len(stable_elements))),
            (diagnostic,),
        ),
    )

    result = build_hierarchical_order_proposal(payload)

    assert [
        (edge["source"], edge["target"], edge["reason"])
        for edge in result.payload["pages"][0]["review_transitions"]
    ] == [("one", "two", "fine-relation-graph-stream-boundary")]
    assert result.diagnostics["unassigned_fallback_transition_candidate_count"] == 2
    assert result.diagnostics[
        "unassigned_fallback_transition_degree_suppressed_count"
    ] == 2
    assert result.diagnostics["unassigned_fallback_transition_emitted_count"] == 0


def test_provider_hierarchy_splits_discontinuous_members_into_local_segments(
    monkeypatch,
) -> None:
    payload = {
        "id": "provider-local-discontinuity",
        "width": 200,
        "height": 140,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "region-a-top", "box": [10, 10, 80, 20], "role": "Text"},
            {"id": "between", "box": [110, 45, 180, 55], "role": "Text"},
            {"id": "region-a-bottom", "box": [10, 90, 80, 100], "role": "Text"},
        ],
        "regions": [
            {
                "id": "region-a",
                "box": [5, 5, 85, 105],
                "role": "Text",
                "member_ids": ["region-a-top", "region-a-bottom"],
            },
            {
                "id": "region-between",
                "box": [105, 40, 185, 60],
                "role": "Text",
                "member_ids": ["between"],
            },
        ],
        "input_adapter": {
            "coarse_region_source": (
                hierarchical_order.PROVIDER_COARSE_REGION_SOURCE
            )
        },
    }

    def selected_order(nodes, **_kwargs):
        desired = (
            ["region-a-top", "between", "region-a-bottom"]
            if len(nodes) == 3
            else ["region-a", "region-between"]
        )
        index_by_id = {node.id: index for index, node in enumerate(nodes)}
        return tuple(index_by_id[node_id] for node_id in desired)

    monkeypatch.setattr(hierarchical_order, "_selected_order", selected_order)
    monkeypatch.setattr(
        hierarchical_order,
        "infer_relation_graph_order_evidence",
        lambda boxes, *_args: RelationGraphOrderEvidence(
            tuple(range(len(boxes))),
            (),
        ),
    )

    result = build_hierarchical_order_proposal(payload)

    region_streams = [
        stream
        for stream in result.payload["pages"][0]["reading_streams"]
        if stream.get("region_id") == "region-a"
    ]
    assert [stream["members"] for stream in region_streams] == [
        ["region-a-top"],
        ["region-a-bottom"],
    ]
    assert all(stream["review_successor_edges"] == [] for stream in region_streams)
    assert result.diagnostics["provider_local_stream_split_count"] == 1
    assert result.diagnostics["provider_local_stream_gap_discontinuity_count"] == 1
    assert result.diagnostics["provider_local_stream_backward_discontinuity_count"] == 0


def test_provider_local_stream_keeps_nearby_vertical_continuity() -> None:
    element_by_id = {
        "source": hierarchical_order._HierarchyNode(
            "source",
            hierarchical_order.BBox(x0=10, y0=10, x1=80, y1=20),
            "Text",
        ),
        "target": hierarchical_order._HierarchyNode(
            "target",
            hierarchical_order.BBox(x0=10, y0=22, x1=80, y1=32),
            "Text",
        ),
    }

    segments, reasons = hierarchical_order._provider_local_stream_segments(
        ("source", "target"),
        element_by_id=element_by_id,
        base_rank={"source": 0, "target": 2},
    )

    assert segments == (("source", "target"),)
    assert reasons == ()


def test_provider_hierarchy_rescues_high_confidence_native_adjacency(
    monkeypatch,
) -> None:
    payload = {
        "id": "provider-adjacency-rescue",
        "width": 100,
        "height": 80,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {"id": "source", "box": [10, 10, 90, 20], "role": "Text"},
            {"id": "target", "box": [10, 25, 90, 35], "role": "Text"},
        ],
        "regions": [
            {
                "id": "region-source",
                "box": [5, 5, 95, 22],
                "role": "Text",
                "member_ids": ["source"],
            },
            {
                "id": "region-target",
                "box": [5, 23, 95, 40],
                "role": "Text",
                "member_ids": ["target"],
            },
        ],
        "input_adapter": {
            "coarse_region_source": (
                hierarchical_order.PROVIDER_COARSE_REGION_SOURCE
            )
        },
    }
    stable_elements = hierarchical_order._nodes_from_payload(
        payload["elements"],
        kind="element",
        width=100,
        height=80,
        maximum=hierarchical_order.MAX_HIERARCHY_ELEMENTS,
    )
    index_by_id = {
        element.id: index for index, element in enumerate(stable_elements)
    }
    candidate = RelationGraphCandidateEdge(
        source=index_by_id["source"],
        target=index_by_id["target"],
        score=0.97,
    )

    def selected_order(nodes, **_kwargs):
        desired = (
            ["source", "target"]
            if all(node.id in {"source", "target"} for node in nodes)
            else ["region-source", "region-target"]
        )
        node_index = {node.id: index for index, node in enumerate(nodes)}
        return tuple(node_index[node_id] for node_id in desired)

    monkeypatch.setattr(hierarchical_order, "_selected_order", selected_order)
    monkeypatch.setattr(
        hierarchical_order,
        "infer_relation_graph_order_evidence",
        lambda *_args: RelationGraphOrderEvidence(
            tuple(range(len(stable_elements))),
            (),
            (candidate,),
        ),
    )

    result = build_hierarchical_order_proposal(payload)

    transitions = result.payload["pages"][0]["review_transitions"]
    assert [
        (edge["source"], edge["target"], edge["reason"])
        for edge in transitions
    ] == [
        ("source", "target", "provider-native-adjacency-relation-rescue")
    ]
    assert transitions[0]["relation_graph_candidate"] == {
        "score": 0.97,
        "minimum_score": 0.95,
    }
    assert result.diagnostics[
        "provider_native_adjacency_rescue_emitted_count"
    ] == 1
    evidence = result.payload["pages"][0]["cross_region_relation_evidence"]
    assert evidence[0]["relation_source"] == (
        "native-relation-graph-adjacency-candidate"
    )

    low_score_candidate = RelationGraphCandidateEdge(
        source=index_by_id["source"],
        target=index_by_id["target"],
        score=0.94,
    )
    monkeypatch.setattr(
        hierarchical_order,
        "infer_relation_graph_order_evidence",
        lambda *_args: RelationGraphOrderEvidence(
            tuple(range(len(stable_elements))),
            (),
            (low_score_candidate,),
        ),
    )

    rejected = build_hierarchical_order_proposal(payload)

    assert rejected.payload["pages"][0]["review_transitions"] == []
    assert rejected.diagnostics[
        "provider_native_adjacency_rescue_score_supported_count"
    ] == 0


def test_provider_hierarchy_keeps_nonlocal_native_relation_as_evidence(
    monkeypatch,
) -> None:
    element_ids = ["source", "one", "two", "three", "four", "target"]
    payload = {
        "id": "provider-nonlocal-relation",
        "width": 200,
        "height": 180,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {
                "id": element_id,
                "box": [10, 10 + index * 25, 90, 20 + index * 25],
                "role": "Text",
            }
            for index, element_id in enumerate(element_ids)
        ],
        "regions": [
            {
                "id": f"region-{element_id}",
                "box": [5, 5 + index * 25, 95, 25 + index * 25],
                "role": "Text",
                "member_ids": [element_id],
            }
            for index, element_id in enumerate(element_ids)
        ],
        "input_adapter": {
            "coarse_region_source": (
                hierarchical_order.PROVIDER_COARSE_REGION_SOURCE
            )
        },
    }
    stable_elements = hierarchical_order._nodes_from_payload(
        payload["elements"],
        kind="element",
        width=200,
        height=180,
        maximum=hierarchical_order.MAX_HIERARCHY_ELEMENTS,
    )
    index_by_id = {
        element.id: index for index, element in enumerate(stable_elements)
    }
    diagnostic = RelationGraphEdgeDiagnostics(
        source=index_by_id["source"],
        target=index_by_id["target"],
        score=0.95,
        source_candidate_count=1,
        target_candidate_count=1,
        source_alternative_score=None,
        target_alternative_score=None,
        source_margin=None,
        target_margin=None,
        source_regret=0.95,
        target_regret=0.95,
        selection_regret=1.9,
        selection_step=0,
    )

    def selected_order(nodes, **_kwargs):
        desired = (
            element_ids
            if len(nodes) == len(element_ids)
            and all(node.id in element_ids for node in nodes)
            else [f"region-{element_id}" for element_id in element_ids]
        )
        node_index = {node.id: index for index, node in enumerate(nodes)}
        return tuple(node_index[node_id] for node_id in desired)

    monkeypatch.setattr(hierarchical_order, "_selected_order", selected_order)
    monkeypatch.setattr(
        hierarchical_order,
        "infer_relation_graph_order_evidence",
        lambda *_args: RelationGraphOrderEvidence(
            tuple(range(len(stable_elements))),
            (diagnostic,),
        ),
    )

    result = build_hierarchical_order_proposal(payload)

    assert result.payload["pages"][0]["review_transitions"] == []
    evidence = result.payload["pages"][0]["cross_region_relation_evidence"]
    assert [
        (
            edge["source"],
            edge["target"],
            edge["suppression_reason"],
            edge["selected_base_rank_displacement"],
        )
        for edge in evidence
    ] == [
        (
            "source",
            "target",
            "provider-nonlocal-selected-rank-gap",
            5,
        )
    ]
    assert result.diagnostics[
        "fine_relation_provider_nonlocal_suppressed_count"
    ] == 1


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
