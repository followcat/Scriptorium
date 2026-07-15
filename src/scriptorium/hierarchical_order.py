from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .chunkr_order_ranker import (
    CHUNKR_ORDER_PROVIDER,
    _reject_order_answers,
    predict_chunkr_block_order,
)
from .models import BBox
from .reading_order import (
    RelationGraphEdgeDiagnostics,
    RelationGraphOrderEvidence,
    infer_relation_graph_order_evidence,
    infer_semantic_reading_order,
)
from .reading_order_sidecar import (
    SIDECAR_PROPOSAL_STATUS,
    SIDECAR_SCHEMA_NAME,
    SIDECAR_SCHEMA_VERSION,
)
from .relation_order import (
    merge_relation_edge_path_cover,
    relation_edge_candidate_path_cover,
)


HIERARCHICAL_ORDER_SCHEMA = "scriptorium-hierarchical-order-proposal/v1"
HIERARCHICAL_ORDER_PROVIDER = "scriptorium-hierarchical-block-line-order"
HIERARCHICAL_ORDER_POLICY = "local-streams-with-relation-graph-transitions-v5"
DEFAULT_MIN_GEOMETRY_COVERAGE = 0.8
DEFAULT_MIN_GEOMETRY_MARGIN = 0.1
DEFAULT_MIN_TEXT_PARENT_SCORE = 0.74
DEFAULT_MIN_TEXT_PARENT_MARGIN = 0.08
MIN_EXACT_TEXT_PARENT_CHARACTERS = 4
MIN_CONTAINED_TEXT_PARENT_CHARACTERS = 8
MIN_TEXT_PARENT_AXIS_ALIGNMENT = 0.25
TEXT_PARENT_VERTICAL_GAP_LINE_FACTOR = 4.0
TEXT_PARENT_HORIZONTAL_GAP_WIDTH_FACTOR = 0.75
TEXT_PARENT_PAGE_GAP_RATIO = 0.015
MAX_HIERARCHY_ELEMENTS = 512
MAX_HIERARCHY_REGIONS = 128
EXTERNAL_RELATION_REPLACEMENT_MARGIN = 0.1
UNASSIGNED_FALLBACK_CONFIDENCE = 0.5


@dataclass(frozen=True)
class HierarchicalOrderProposalResult:
    payload: dict[str, Any]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class _HierarchyNode:
    id: str
    bbox: BBox
    role: str
    text: str = ""
    member_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Membership:
    element_id: str
    region_id: str | None
    method: str
    coverage: float | None
    runner_up_coverage: float | None
    margin: float | None
    reason: str | None
    text_parent_score: float | None = None
    text_match_score: float | None = None
    spatial_gap_ratio: float | None = None
    evidence_confidence: float | None = None
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "element_id": self.element_id,
            "region_id": self.region_id,
            "method": self.method,
            "coverage": _rounded_optional(self.coverage),
            "runner_up_coverage": _rounded_optional(self.runner_up_coverage),
            "margin": _rounded_optional(self.margin),
            "reason": self.reason,
        }
        if self.text_parent_score is not None:
            payload["text_parent_score"] = _rounded_optional(
                self.text_parent_score
            )
        if self.text_match_score is not None:
            payload["text_match_score"] = _rounded_optional(self.text_match_score)
        if self.spatial_gap_ratio is not None:
            payload["spatial_gap_ratio"] = _rounded_optional(self.spatial_gap_ratio)
        if self.evidence_confidence is not None:
            payload["evidence_confidence"] = _rounded_optional(
                self.evidence_confidence
            )
        if self.evidence:
            payload["evidence"] = list(self.evidence)
        return payload


@dataclass(frozen=True)
class _TextParentCandidate:
    region_id: str
    score: float
    text_match_score: float
    coverage: float
    spatial_gap_ratio: float


@dataclass(frozen=True)
class _CrossRegionRelationCandidate:
    source: str
    target: str
    source_region: str
    target_region: str
    diagnostic: RelationGraphEdgeDiagnostics
    boundary_aligned: bool

    @property
    def edge_key(self) -> tuple[str, str]:
        return self.source, self.target


def build_hierarchical_order_proposal(
    payload: Mapping[str, Any],
    *,
    chunkr_model: str | Path | None = None,
    external_successor_edges: Sequence[Mapping[str, Any]] | None = None,
    min_geometry_coverage: float = DEFAULT_MIN_GEOMETRY_COVERAGE,
    min_geometry_margin: float = DEFAULT_MIN_GEOMETRY_MARGIN,
) -> HierarchicalOrderProposalResult:
    """Build a review-only region order while preserving local line order."""

    _reject_order_answers(payload)
    _validate_unit_interval(
        min_geometry_coverage,
        "min_geometry_coverage",
        allow_zero=False,
    )
    _validate_unit_interval(
        min_geometry_margin,
        "min_geometry_margin",
        allow_zero=True,
    )
    width = _positive_number(payload.get("width"), "width")
    height = _positive_number(payload.get("height"), "height")
    page_id = str(payload.get("id") or "hierarchy-page").strip()
    if not page_id:
        raise ValueError("hierarchy page id must be non-empty")
    page_index = _nonnegative_int(payload.get("page_index", 0), "page_index")
    element_granularity = _declared_granularity(
        payload.get("element_granularity"),
        label="element_granularity",
        expected="fine",
    )
    region_granularity = _declared_granularity(
        payload.get("region_granularity"),
        label="region_granularity",
        expected="coarse",
    )
    input_adapter = payload.get("input_adapter")
    if input_adapter is not None and not isinstance(input_adapter, Mapping):
        raise ValueError("hierarchy input_adapter must be an object")

    elements = _nodes_from_payload(
        payload.get("elements"),
        kind="element",
        width=width,
        height=height,
        maximum=MAX_HIERARCHY_ELEMENTS,
    )
    regions = _nodes_from_payload(
        payload.get("regions"),
        kind="region",
        width=width,
        height=height,
        maximum=MAX_HIERARCHY_REGIONS,
        allow_member_ids=True,
    )
    if set(node.id for node in elements) & set(node.id for node in regions):
        raise ValueError("element and region ids must use separate namespaces")

    base_order = _selected_order(elements, width=width, height=height)
    base_ids = tuple(elements[index].id for index in base_order)
    base_rank = {element_id: rank for rank, element_id in enumerate(base_ids)}
    relation_order_evidence = infer_relation_graph_order_evidence(
        [element.bbox for element in elements],
        width,
        height,
    )
    external_relation_evidence = (
        _relation_evidence_from_external_successors(
            elements,
            base_order=base_order,
            successor_edges=external_successor_edges,
        )
        if external_successor_edges is not None
        else None
    )
    memberships = _assign_memberships(
        elements,
        regions,
        width=width,
        height=height,
        min_geometry_coverage=min_geometry_coverage,
        min_geometry_margin=min_geometry_margin,
    )
    memberships = _refine_ambiguous_memberships_from_continuity(
        elements,
        regions,
        memberships,
        base_ids=base_ids,
        relation_evidence=relation_order_evidence,
        min_geometry_coverage=min_geometry_coverage,
        min_geometry_margin=min_geometry_margin,
    )
    membership_by_element = {
        membership.element_id: membership for membership in memberships
    }
    members_by_region = {
        region.id: tuple(
            sorted(
                (
                    element.id
                    for element in elements
                    if membership_by_element[element.id].region_id == region.id
                ),
                key=base_rank.__getitem__,
            )
        )
        for region in regions
    }

    (
        coarse_order,
        coarse_diagnostics,
        transition_relation_evidence,
    ) = _coarse_region_order(
        page_id=page_id,
        width=width,
        height=height,
        elements=elements,
        regions=regions,
        members_by_region=members_by_region,
        relation_evidence=relation_order_evidence,
        chunkr_model=chunkr_model,
    )
    coarse_ids = tuple(regions[index].id for index in coarse_order)
    base_coarse_order = _selected_order(regions, width=width, height=height)
    base_coarse_ids = tuple(regions[index].id for index in base_coarse_order)
    transition_policy = str(coarse_diagnostics["transition_policy"])
    transitions_enabled = bool(coarse_diagnostics["transitions_enabled"])
    sidecar_region_ids = coarse_ids if transitions_enabled else base_coarse_ids

    streams = _region_streams(
        sidecar_region_ids,
        regions=regions,
        members_by_region=members_by_region,
        membership_by_element=membership_by_element,
    )
    unassigned_ids = tuple(
        element_id
        for element_id in base_ids
        if membership_by_element[element_id].region_id is None
    )
    nonempty_region_ids = tuple(
        region_id for region_id in coarse_ids if members_by_region[region_id]
    )
    adjacent_region_pairs = tuple(zip(coarse_ids, coarse_ids[1:], strict=False))
    membership_resolved_pairs = tuple(
        (source_region, target_region)
        for source_region, target_region in adjacent_region_pairs
        if members_by_region[source_region] and members_by_region[target_region]
    )
    eligible_region_pairs = tuple(
        (source_region, target_region)
        for source_region, target_region in membership_resolved_pairs
        if not _has_unassigned_boundary_gap(
            source_region,
            target_region,
            members_by_region=members_by_region,
            base_rank=base_rank,
            unassigned_ids={
                element_id
                for element_id, membership in membership_by_element.items()
                if membership.region_id is None
            },
        )
    )
    coarse_adjacent_region_pair_count = len(adjacent_region_pairs)
    relation_evidence_records: list[dict[str, Any]] = []
    relation_diagnostics: dict[str, Any] = {}
    native_transition_edge_keys: set[tuple[int, int]] = set()
    external_transition_edge_keys: set[tuple[int, int]] = set()
    if transition_relation_evidence is not None and external_relation_evidence is not None:
        native_transition_edge_keys = {
            (edge.source, edge.target)
            for edge in transition_relation_evidence.selected_edge_diagnostics
        }
        external_transition_edge_keys = {
            (edge.source, edge.target)
            for edge in external_relation_evidence.selected_edge_diagnostics
            if (edge.source, edge.target) not in native_transition_edge_keys
        }
        transition_relation_evidence = RelationGraphOrderEvidence(
            ordered_indices=transition_relation_evidence.ordered_indices,
            selected_edge_diagnostics=(
                *transition_relation_evidence.selected_edge_diagnostics,
                *(
                    edge
                    for edge in external_relation_evidence.selected_edge_diagnostics
                    if (edge.source, edge.target) in external_transition_edge_keys
                ),
            ),
        )
    if transitions_enabled and transition_relation_evidence is not None:
        (
            cross_transitions,
            relation_evidence_records,
            relation_diagnostics,
        ) = _relation_graph_cross_region_transitions(
            transition_relation_evidence,
            elements=elements,
            membership_by_element=membership_by_element,
            members_by_region=members_by_region,
            region_role_by_id={region.id: region.role for region in regions},
            transition_policy=transition_policy,
            protected_edge_keys=native_transition_edge_keys,
            external_edge_keys=external_transition_edge_keys,
        )
        potential_cross_transition_count = len(relation_evidence_records)
        eligible_cross_transition_count = int(
            relation_diagnostics["fine_relation_boundary_aligned_edge_count"]
        )
        empty_region_boundary_count = 0
        unassigned_gap_boundary_count = 0
    else:
        potential_cross_transition_count = coarse_adjacent_region_pair_count
        eligible_cross_transition_count = len(eligible_region_pairs)
        empty_region_boundary_count = (
            potential_cross_transition_count - len(membership_resolved_pairs)
        )
        unassigned_gap_boundary_count = (
            len(membership_resolved_pairs) - len(eligible_region_pairs)
        )
        cross_transitions = (
            _cross_region_transitions(
                eligible_region_pairs,
                members_by_region=members_by_region,
                transition_policy=transition_policy,
                transition_confidence=float(
                    coarse_diagnostics["cross_region_transition_confidence"]
                ),
            )
            if transitions_enabled
            else []
        )
    relation_cross_transition_count = len(cross_transitions)
    complete_cross_region_chain = _is_complete_cross_region_chain(
        coarse_ids,
        members_by_region=members_by_region,
        cross_transitions=cross_transitions,
    )
    candidate_expansion_enabled = complete_cross_region_chain and not unassigned_ids
    candidate_ids = _hierarchical_candidate_order(
        elements,
        base_ids=base_ids,
        streams=streams,
        cross_transitions=cross_transitions,
        enabled=candidate_expansion_enabled,
    )
    (
        fallback_streams,
        fallback_transitions,
        fallback_diagnostics,
    ) = _unassigned_fallback_relations(
        base_ids,
        membership_by_element=membership_by_element,
        region_streams=streams,
        cross_transitions=cross_transitions,
    )
    streams.extend(fallback_streams)
    cross_transitions.extend(fallback_transitions)
    potential_cross_transition_count += int(
        fallback_diagnostics["unassigned_fallback_transition_candidate_count"]
    )
    eligible_cross_transition_count += int(
        fallback_diagnostics["unassigned_fallback_transition_emitted_count"]
    )
    assigned_subsequence = tuple(
        member_id
        for region_id in sidecar_region_ids
        for member_id in members_by_region[region_id]
    )
    changed_position_count = sum(
        base_id != candidate_id
        for base_id, candidate_id in zip(base_ids, candidate_ids, strict=True)
    )
    pair_disagreement = _pair_disagreement(base_ids, candidate_ids)

    explicit_count = sum(item.method == "explicit-parent" for item in memberships)
    text_geometry_count = sum(
        item.method == "text-geometry-parent" for item in memberships
    )
    geometry_count = sum(item.method == "geometry-coverage" for item in memberships)
    continuity_count = sum(
        item.method == "relation-base-continuity-parent" for item in memberships
    )
    boundary_text_count = sum(
        item.method == "relation-base-boundary-text-parent"
        for item in memberships
    )
    ambiguous_count = sum(
        item.reason == "ambiguous-region-overlap" for item in memberships
    )
    diagnostics = {
        "element_count": len(elements),
        "region_count": len(regions),
        "nonempty_region_count": len(nonempty_region_ids),
        "assigned_element_count": (
            explicit_count
            + text_geometry_count
            + geometry_count
            + continuity_count
            + boundary_text_count
        ),
        "explicit_membership_count": explicit_count,
        "text_geometry_membership_count": text_geometry_count,
        "geometry_membership_count": geometry_count,
        "relation_base_continuity_membership_count": continuity_count,
        "relation_base_boundary_text_membership_count": boundary_text_count,
        "external_relation_input_edge_count": (
            len(external_successor_edges)
            if external_successor_edges is not None
            else 0
        ),
        "external_relation_path_selected_edge_count": (
            len(external_relation_evidence.selected_edge_diagnostics)
            if external_relation_evidence is not None
            else 0
        ),
        "external_relation_novel_selected_edge_count": len(
            external_transition_edge_keys
        ),
        "ambiguous_element_count": ambiguous_count,
        "unassigned_element_count": len(unassigned_ids),
        "within_region_successor_count": sum(
            len(stream["review_successor_edges"]) for stream in streams
        ),
        "coarse_adjacent_region_pair_count": coarse_adjacent_region_pair_count,
        "potential_cross_region_transition_count": potential_cross_transition_count,
        "eligible_cross_region_transition_count": eligible_cross_transition_count,
        "empty_region_boundary_count": empty_region_boundary_count,
        "unassigned_gap_boundary_count": unassigned_gap_boundary_count,
        "emitted_cross_region_transition_count": len(cross_transitions),
        "suppressed_cross_region_transition_count": (
            potential_cross_transition_count - len(cross_transitions)
        ),
        "candidate_changed_position_count": changed_position_count,
        "candidate_pair_disagreement": pair_disagreement,
        "candidate_expansion_enabled": candidate_expansion_enabled,
        "candidate_expansion_complete_cross_region_chain": (
            complete_cross_region_chain
        ),
        "candidate_expansion_suppressed_incomplete_cross_region_chain": bool(
            transitions_enabled
            and potential_cross_transition_count
            and not complete_cross_region_chain
        ),
        "candidate_expansion_suppressed_incomplete_membership": bool(
            cross_transitions and unassigned_ids
        ),
        "candidate_expansion_suppressed_missing_cross_region_evidence": bool(
            transition_relation_evidence is not None
            and len(nonempty_region_ids) > 1
            and relation_cross_transition_count == 0
        ),
        "element_granularity": element_granularity,
        "region_granularity": region_granularity,
        "coarse_order_suppressed": not transitions_enabled,
        "min_geometry_coverage": round(min_geometry_coverage, 8),
        "min_geometry_margin": round(min_geometry_margin, 8),
        "min_text_parent_score": DEFAULT_MIN_TEXT_PARENT_SCORE,
        "min_text_parent_margin": DEFAULT_MIN_TEXT_PARENT_MARGIN,
        **fallback_diagnostics,
        **relation_diagnostics,
        **coarse_diagnostics,
    }
    region_by_id = {region.id: region for region in regions}
    coarse_region_records = [
        {
            "id": region_id,
            "coarse_order_index": coarse_order_index,
            "role": region_by_id[region_id].role,
            "box": region_by_id[region_id].bbox.as_list(),
            "member_ids": list(members_by_region[region_id]),
        }
        for coarse_order_index, region_id in enumerate(coarse_ids)
    ]
    stream_type_counts = Counter(str(stream["type"]) for stream in streams)
    fallback_stream_count = len(fallback_streams)
    sidecar_summary = {
        "page_count": 1,
        "stream_count": len(streams),
        "member_count": sum(len(stream["members"]) for stream in streams),
        "successor_edge_count": 0,
        "review_successor_edge_count": diagnostics["within_region_successor_count"],
        "review_transition_count": len(cross_transitions),
        "strict_block_transition_count": 0,
        "review_block_transition_count": len(cross_transitions),
        "stream_type_counts": dict(sorted(stream_type_counts.items())),
        "stream_origin_counts": {
            "hierarchical-region-membership": len(streams) - fallback_stream_count,
            **(
                {"unassigned-base-order-fallback": fallback_stream_count}
                if fallback_stream_count
                else {}
            ),
        },
    }
    reading_order_tree = {
        "type": "ordered-group" if transitions_enabled else "unordered-group",
        "coarse_order_status": "review-only" if transitions_enabled else "suppressed",
        "relation_model": (
            "fine-relation-graph-path-cover"
            if transition_relation_evidence is not None
            else "coarse-adjacent-chain"
        ),
        "total_order_asserted": False,
        "children": [
            {
                "type": (
                    "ordered-group"
                    if members_by_region[region_id]
                    else "unordered-group"
                ),
                "region_id": region_id,
                "membership_status": (
                    "resolved" if members_by_region[region_id] else "empty"
                ),
                "members": list(members_by_region[region_id]),
            }
            for region_id in sidecar_region_ids
        ],
        "unassigned": {
            "type": "ordered-segments" if fallback_streams else "unordered-group",
            "members": list(unassigned_ids),
            "stream_ids": [stream["id"] for stream in fallback_streams],
        },
    }
    document_records = [
        {
            "id": elements[index].id,
            "text": elements[index].text,
            "type": "text",
            "bbox_pdf": elements[index].bbox.as_list(),
            "_scriptorium_sidecar_reference": True,
            "review": {
                "base_order": rank,
                "base_order_source": "selected-auto",
                "hierarchy_region_id": membership_by_element[
                    elements[index].id
                ].region_id,
            },
        }
        for rank, index in enumerate(base_order)
    ]
    output = {
        "schema": HIERARCHICAL_ORDER_SCHEMA,
        "schema_name": SIDECAR_SCHEMA_NAME,
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "sidecar_status": SIDECAR_PROPOSAL_STATUS,
        "source": HIERARCHICAL_ORDER_PROVIDER,
        "source_page_id": page_id,
        "source_input_sha256": _canonical_sha256(payload),
        **(
            {"input_adapter": dict(input_adapter)}
            if isinstance(input_adapter, Mapping)
            else {}
        ),
        "hierarchy_policy": HIERARCHICAL_ORDER_POLICY,
        "element_granularity": element_granularity,
        "region_granularity": region_granularity,
        "semantic_policy": "review-only",
        "order_policy": "review-only",
        "relation_policy": "review-only",
        "candidate_consensus_policy": "isolated",
        "runtime_reorder": False,
        "review_policy": {
            "acceptance_required": True,
            "accepted_status": "accepted",
            "within_region_relations": "review_only",
            "cross_region_relations": "review_only",
            "description": (
                "Local streams remain authoritative. Cross-region relations are "
                "a partial review graph and never assert a page-wide total order."
            ),
        },
        "summary": sidecar_summary,
        "base_order_source": "selected-auto",
        "base_ordered_element_ids": list(base_ids),
        "base_coarse_ordered_region_ids": list(base_coarse_ids),
        "coarse_ordered_region_ids": list(coarse_ids),
        "proposed_coarse_ordered_region_ids": (
            list(coarse_ids) if transitions_enabled else []
        ),
        "assigned_subsequence_ids": list(assigned_subsequence),
        "candidate_ordered_element_ids": list(candidate_ids),
        "unassigned_element_ids": list(unassigned_ids),
        "memberships": [membership.as_dict() for membership in memberships],
        "coarse_regions": coarse_region_records,
        "pages": [
            {
                "page_index": page_index,
                "document": document_records,
                "reading_order_tree": reading_order_tree,
                "reading_streams": streams,
                "review_transitions": cross_transitions,
                "cross_region_relation_evidence": relation_evidence_records,
            }
        ],
        "diagnostics": diagnostics,
    }
    return HierarchicalOrderProposalResult(output, diagnostics)


def _nodes_from_payload(
    value: Any,
    *,
    kind: str,
    width: float,
    height: float,
    maximum: int,
    allow_member_ids: bool = False,
) -> tuple[_HierarchyNode, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"hierarchy payload must contain a non-empty {kind}s list")
    if len(value) > maximum:
        raise ValueError(f"hierarchy payload cannot exceed {maximum} {kind}s")
    nodes = tuple(
        sorted(
            (
                _node_from_payload(
                    item,
                    kind=kind,
                    width=width,
                    height=height,
                    allow_member_ids=allow_member_ids,
                )
                for item in value
            ),
            key=_node_sort_key,
        )
    )
    if len({node.id for node in nodes}) != len(nodes):
        raise ValueError(f"hierarchy {kind} ids must be unique")
    return nodes


def _node_from_payload(
    value: Any,
    *,
    kind: str,
    width: float,
    height: float,
    allow_member_ids: bool,
) -> _HierarchyNode:
    if not isinstance(value, Mapping):
        raise ValueError(f"hierarchy {kind}s must be objects")
    _reject_order_answers(value)
    node_id = str(value.get("id") or "").strip()
    if not node_id:
        raise ValueError(f"hierarchy {kind}s require non-empty ids")
    box = value.get("box")
    if not isinstance(box, Sequence) or isinstance(box, (str, bytes)) or len(box) != 4:
        raise ValueError(f"hierarchy {kind} box must contain x0, y0, x1, y1")
    try:
        bbox = BBox.from_any(box)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"hierarchy {kind} box is invalid") from exc
    if (
        bbox.x0 < 0
        or bbox.y0 < 0
        or bbox.x1 > width
        or bbox.y1 > height
        or bbox.width <= 0
        or bbox.height <= 0
    ):
        raise ValueError(f"hierarchy {kind} box must be non-empty and inside the page")
    role = str(
        value.get("role") or value.get("block_label") or value.get("type") or "Unknown"
    ).strip()
    text = str(value.get("text") or value.get("content") or "")
    raw_member_ids = value.get("member_ids")
    if raw_member_ids is not None and not allow_member_ids:
        raise ValueError("only hierarchy regions may declare member_ids")
    member_ids: tuple[str, ...] = ()
    if raw_member_ids is not None:
        if not isinstance(raw_member_ids, Sequence) or isinstance(
            raw_member_ids, (str, bytes)
        ):
            raise ValueError("hierarchy region member_ids must be a list")
        normalized = tuple(sorted(str(item).strip() for item in raw_member_ids))
        if any(not item for item in normalized) or len(set(normalized)) != len(
            normalized
        ):
            raise ValueError("hierarchy region member_ids must be non-empty and unique")
        member_ids = normalized
    return _HierarchyNode(node_id, bbox, role or "Unknown", text, member_ids)


def _node_sort_key(node: _HierarchyNode) -> tuple[str, str]:
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "box": [round(value, 8) for value in node.bbox.as_list()],
                "role": node.role,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return fingerprint, node.id


def _selected_order(
    nodes: Sequence[_HierarchyNode],
    *,
    width: float,
    height: float,
) -> tuple[int, ...]:
    assignments = infer_semantic_reading_order(
        [node.bbox for node in nodes],
        width,
        height,
        texts=[""] * len(nodes),
    )
    order = tuple(
        assignment.item_index
        for assignment in sorted(
            assignments,
            key=lambda assignment: assignment.semantic_order,
        )
    )
    if len(order) != len(nodes) or set(order) != set(range(len(nodes))):
        raise ValueError("selected-auto did not produce a complete hierarchy order")
    return order


def _assign_memberships(
    elements: Sequence[_HierarchyNode],
    regions: Sequence[_HierarchyNode],
    *,
    width: float,
    height: float,
    min_geometry_coverage: float,
    min_geometry_margin: float,
) -> tuple[_Membership, ...]:
    element_by_id = {element.id: element for element in elements}
    assigned_region_by_element: dict[str, str] = {}
    memberships: dict[str, _Membership] = {}
    for region in regions:
        for element_id in region.member_ids:
            element = element_by_id.get(element_id)
            if element is None:
                raise ValueError(
                    f"hierarchy region {region.id!r} references unknown element "
                    f"{element_id!r}"
                )
            previous = assigned_region_by_element.get(element_id)
            if previous is not None:
                raise ValueError(
                    f"hierarchy element {element_id!r} belongs to explicit regions "
                    f"{previous!r} and {region.id!r}"
                )
            assigned_region_by_element[element_id] = region.id
            memberships[element_id] = _Membership(
                element_id,
                region.id,
                "explicit-parent",
                _bbox_coverage(element.bbox, region.bbox),
                None,
                None,
                None,
            )

    for element in elements:
        if element.id in memberships:
            continue
        candidates = sorted(
            (
                (_bbox_coverage(element.bbox, region.bbox), region.id)
                for region in regions
            ),
            key=lambda item: (-item[0], item[1]),
        )
        text_parent = _text_parent_candidate(
            element,
            regions,
            width=width,
            height=height,
            min_score=DEFAULT_MIN_TEXT_PARENT_SCORE,
            min_margin=DEFAULT_MIN_TEXT_PARENT_MARGIN,
        )
        best_geometry_region_id = candidates[0][1] if candidates else None
        best_geometry_coverage = candidates[0][0] if candidates else 0.0
        runner_up_geometry_coverage = (
            candidates[1][0] if len(candidates) > 1 else 0.0
        )
        geometry_assignment_is_ambiguous = bool(
            len(candidates) > 1
            and best_geometry_coverage - runner_up_geometry_coverage
            < min_geometry_margin
        )
        best_geometry_region = next(
            (
                region
                for region in regions
                if region.id == best_geometry_region_id
            ),
            None,
        )
        geometry_text_agrees = bool(
            best_geometry_region
            and _parent_text_match_score(element.text, best_geometry_region.text)
            is not None
        )
        if text_parent is not None and (
            best_geometry_coverage < min_geometry_coverage
            or geometry_assignment_is_ambiguous
            or (
                text_parent.region_id != best_geometry_region_id
                and not geometry_text_agrees
            )
        ):
            memberships[element.id] = _Membership(
                element.id,
                text_parent.region_id,
                "text-geometry-parent",
                text_parent.coverage,
                None,
                None,
                None,
                text_parent_score=text_parent.score,
                text_match_score=text_parent.text_match_score,
                spatial_gap_ratio=text_parent.spatial_gap_ratio,
                evidence_confidence=min(text_parent.score, 0.76),
            )
            continue
        qualified = [item for item in candidates if item[0] >= min_geometry_coverage]
        if not qualified:
            runner_up = candidates[1][0] if len(candidates) > 1 else None
            memberships[element.id] = _Membership(
                element.id,
                None,
                "unassigned",
                candidates[0][0] if candidates else None,
                runner_up,
                candidates[0][0] - runner_up if runner_up is not None else None,
                "insufficient-region-coverage",
            )
            continue
        best_coverage, best_region_id = qualified[0]
        runner_up = candidates[1][0] if len(candidates) > 1 else 0.0
        margin = best_coverage - runner_up
        if len(candidates) > 1 and margin < min_geometry_margin:
            memberships[element.id] = _Membership(
                element.id,
                None,
                "unassigned",
                best_coverage,
                runner_up,
                margin,
                "ambiguous-region-overlap",
            )
            continue
        memberships[element.id] = _Membership(
            element.id,
            best_region_id,
            "geometry-coverage",
            best_coverage,
            runner_up if len(candidates) > 1 else None,
            margin if len(candidates) > 1 else None,
            None,
        )
    return tuple(memberships[element.id] for element in elements)


def _refine_ambiguous_memberships_from_continuity(
    elements: Sequence[_HierarchyNode],
    regions: Sequence[_HierarchyNode],
    memberships: Sequence[_Membership],
    *,
    base_ids: Sequence[str],
    relation_evidence: RelationGraphOrderEvidence,
    min_geometry_coverage: float,
    min_geometry_margin: float,
) -> tuple[_Membership, ...]:
    membership_by_element = {
        membership.element_id: membership for membership in memberships
    }
    element_ids = tuple(element.id for element in elements)
    predecessor: dict[str, tuple[str, RelationGraphEdgeDiagnostics]] = {}
    successor: dict[str, tuple[str, RelationGraphEdgeDiagnostics]] = {}
    for diagnostic in relation_evidence.selected_edge_diagnostics:
        if not (
            0 <= diagnostic.source < len(elements)
            and 0 <= diagnostic.target < len(elements)
        ):
            raise ValueError("fine relation graph returned an unknown element index")
        source = element_ids[diagnostic.source]
        target = element_ids[diagnostic.target]
        successor[source] = (target, diagnostic)
        predecessor[target] = (source, diagnostic)

    base_rank = {element_id: rank for rank, element_id in enumerate(base_ids)}
    region_by_id = {region.id: region for region in regions}
    refined = dict(membership_by_element)
    for element in elements:
        membership = membership_by_element[element.id]
        if membership.reason != "ambiguous-region-overlap":
            continue
        previous_relation = predecessor.get(element.id)
        next_relation = successor.get(element.id)
        if previous_relation is None or next_relation is None:
            continue
        relation_neighbors = (previous_relation, next_relation)
        if any(
            diagnostic.has_tied_alternative
            for _neighbor_id, diagnostic in relation_neighbors
        ):
            continue
        relation_regions = tuple(
            membership_by_element[neighbor_id].region_id
            for neighbor_id, _diagnostic in relation_neighbors
        )
        if relation_regions[0] is None or relation_regions[1] is None:
            continue

        rank = base_rank[element.id]
        if rank == 0 or rank + 1 >= len(base_ids):
            continue
        base_neighbor_ids = (base_ids[rank - 1], base_ids[rank + 1])
        base_regions = tuple(
            membership_by_element[neighbor_id].region_id
            for neighbor_id in base_neighbor_ids
        )
        if base_regions[0] is None or base_regions[1] is None:
            continue

        best_coverage = float(membership.coverage or 0.0)
        tied_region_coverages = {
            region.id: coverage
            for region in regions
            for coverage in [_bbox_coverage(element.bbox, region.bbox)]
            if coverage >= min_geometry_coverage
            and best_coverage - coverage < min_geometry_margin
        }
        if not tied_region_coverages:
            continue

        method: str
        evidence: tuple[str, ...]
        if (
            relation_regions[0] == relation_regions[1]
            and base_regions[0] == base_regions[1] == relation_regions[0]
        ):
            region_id = str(relation_regions[0])
            method = "relation-base-continuity-parent"
            evidence = (
                "geometry-tied-candidate",
                "relation-graph-bidirectional-continuity",
                "selected-order-bidirectional-continuity",
            )
        elif (
            relation_regions[0] != relation_regions[1]
            and base_regions == relation_regions
        ):
            compact_text = _compact_semantic_text(element.text)
            if len(compact_text) < MIN_EXACT_TEXT_PARENT_CHARACTERS:
                continue
            matching_regions = tuple(
                region_id
                for region_id in sorted(tied_region_coverages)
                if compact_text
                in _compact_semantic_text(region_by_id[region_id].text)
            )
            if (
                len(matching_regions) != 1
                or matching_regions[0] not in relation_regions
            ):
                continue
            region_id = matching_regions[0]
            method = "relation-base-boundary-text-parent"
            evidence = (
                "geometry-tied-candidate",
                "relation-graph-boundary-split",
                "selected-order-boundary-split",
                "unique-tied-region-text-containment",
            )
        else:
            continue

        coverage = tied_region_coverages.get(region_id)
        if coverage is None:
            continue
        relation_confidence = min(
            diagnostic.score for _neighbor_id, diagnostic in relation_neighbors
        )
        refined[element.id] = _Membership(
            element_id=element.id,
            region_id=region_id,
            method=method,
            coverage=coverage,
            runner_up_coverage=membership.runner_up_coverage,
            margin=membership.margin,
            reason=None,
            evidence_confidence=min(relation_confidence, 0.76),
            evidence=evidence,
        )
    return tuple(refined[element.id] for element in elements)


def _text_parent_candidate(
    element: _HierarchyNode,
    regions: Sequence[_HierarchyNode],
    *,
    width: float,
    height: float,
    min_score: float,
    min_margin: float,
) -> _TextParentCandidate | None:
    candidates: list[_TextParentCandidate] = []
    for region in regions:
        text_match_score = _parent_text_match_score(element.text, region.text)
        if text_match_score is None:
            continue
        spatial_gap_ratio = _text_parent_spatial_gap_ratio(
            element.bbox,
            region.bbox,
            width=width,
            height=height,
        )
        if spatial_gap_ratio is None:
            continue
        coverage = _bbox_coverage(element.bbox, region.bbox)
        score = (
            text_match_score * 0.7
            + (1.0 - spatial_gap_ratio) * 0.2
            + coverage * 0.1
        )
        candidates.append(
            _TextParentCandidate(
                region_id=region.id,
                score=score,
                text_match_score=text_match_score,
                coverage=coverage,
                spatial_gap_ratio=spatial_gap_ratio,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item.score, item.region_id))
    best = candidates[0]
    if best.score < min_score:
        return None
    if len(candidates) > 1 and best.score - candidates[1].score < min_margin:
        return None
    return best


def _parent_text_match_score(element_text: str, region_text: str) -> float | None:
    element = _compact_semantic_text(element_text)
    region = _compact_semantic_text(region_text)
    if not element or not region:
        return None
    if element == region and len(element) >= MIN_EXACT_TEXT_PARENT_CHARACTERS:
        return 1.0
    if (
        len(element) < MIN_CONTAINED_TEXT_PARENT_CHARACTERS
        or element not in region
    ):
        return None
    return min(0.99, 0.9 + 0.1 * len(element) / len(region))


def _compact_semantic_text(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _text_parent_spatial_gap_ratio(
    element: BBox,
    region: BBox,
    *,
    width: float,
    height: float,
) -> float | None:
    horizontal_overlap = _axis_overlap(element.x0, element.x1, region.x0, region.x1)
    vertical_overlap = _axis_overlap(element.y0, element.y1, region.y0, region.y1)
    horizontal_alignment = horizontal_overlap / max(
        min(element.width, region.width),
        1.0,
    )
    vertical_alignment = vertical_overlap / max(
        min(element.height, region.height),
        1.0,
    )
    ratios: list[float] = []
    if horizontal_alignment >= MIN_TEXT_PARENT_AXIS_ALIGNMENT:
        vertical_limit = max(
            element.height * TEXT_PARENT_VERTICAL_GAP_LINE_FACTOR,
            height * TEXT_PARENT_PAGE_GAP_RATIO,
        )
        ratios.append(
            _axis_gap(element.y0, element.y1, region.y0, region.y1)
            / vertical_limit
        )
    if vertical_alignment >= MIN_TEXT_PARENT_AXIS_ALIGNMENT:
        horizontal_limit = max(
            element.width * TEXT_PARENT_HORIZONTAL_GAP_WIDTH_FACTOR,
            width * TEXT_PARENT_PAGE_GAP_RATIO,
        )
        ratios.append(
            _axis_gap(element.x0, element.x1, region.x0, region.x1)
            / horizontal_limit
        )
    if not ratios:
        return None
    ratio = min(ratios)
    return min(max(ratio, 0.0), 1.0) if ratio <= 1.0 else None


def _axis_overlap(left0: float, left1: float, right0: float, right1: float) -> float:
    return max(0.0, min(left1, right1) - max(left0, right0))


def _axis_gap(left0: float, left1: float, right0: float, right1: float) -> float:
    return max(0.0, max(left0, right0) - min(left1, right1))


def _relation_evidence_from_external_successors(
    elements: Sequence[_HierarchyNode],
    *,
    base_order: Sequence[int],
    successor_edges: Sequence[Mapping[str, Any]],
) -> RelationGraphOrderEvidence:
    element_index = {element.id: index for index, element in enumerate(elements)}
    candidates_by_edge: dict[tuple[int, int], tuple[float, int]] = {}
    for raw_edge in successor_edges:
        if not isinstance(raw_edge, Mapping):
            raise ValueError("external successor edges must be objects")
        source_id = str(raw_edge.get("source") or "").strip()
        target_id = str(raw_edge.get("target") or "").strip()
        if source_id not in element_index or target_id not in element_index:
            raise ValueError("external successor edge references an unknown element")
        source = element_index[source_id]
        target = element_index[target_id]
        if source == target:
            continue
        try:
            confidence = float(raw_edge.get("confidence"))
            rank = int(raw_edge.get("rank") or 1)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("external successor edge confidence/rank is invalid") from exc
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("external successor edge confidence must be in [0, 1]")
        edge_key = (source, target)
        current = candidates_by_edge.get(edge_key)
        if current is None or (confidence, -rank) > (current[0], -current[1]):
            candidates_by_edge[edge_key] = (confidence, rank)

    ordered_candidates = sorted(
        candidates_by_edge,
        key=lambda edge: (
            -candidates_by_edge[edge][0],
            candidates_by_edge[edge][1],
            edge[0],
            edge[1],
        ),
    )
    merged = merge_relation_edge_path_cover(ordered_candidates)
    selected_edges = list(merged.selected_edges)
    outgoing: dict[int, list[tuple[int, float]]] = {}
    incoming: dict[int, list[tuple[int, float]]] = {}
    for source, target in ordered_candidates:
        score = candidates_by_edge[(source, target)][0]
        outgoing.setdefault(source, []).append((target, score))
        incoming.setdefault(target, []).append((source, score))
    diagnostics: list[RelationGraphEdgeDiagnostics] = []
    for selection_step, (source, target) in enumerate(selected_edges):
        score = candidates_by_edge[(source, target)][0]
        source_alternatives = sorted(
            (
                alternative_score
                for alternative_target, alternative_score in outgoing[source]
                if alternative_target != target
            ),
            reverse=True,
        )
        target_alternatives = sorted(
            (
                alternative_score
                for alternative_source, alternative_score in incoming[target]
                if alternative_source != source
            ),
            reverse=True,
        )
        source_alternative = source_alternatives[0] if source_alternatives else None
        target_alternative = target_alternatives[0] if target_alternatives else None
        source_margin = score - source_alternative if source_alternative is not None else None
        target_margin = score - target_alternative if target_alternative is not None else None
        source_regret = source_margin if source_margin is not None else score
        target_regret = target_margin if target_margin is not None else score
        diagnostics.append(
            RelationGraphEdgeDiagnostics(
                source=source,
                target=target,
                score=score,
                source_candidate_count=len(outgoing[source]),
                target_candidate_count=len(incoming[target]),
                source_alternative_score=source_alternative,
                target_alternative_score=target_alternative,
                source_margin=source_margin,
                target_margin=target_margin,
                source_regret=source_regret,
                target_regret=target_regret,
                selection_regret=source_regret + target_regret,
                selection_step=selection_step,
            )
        )
    ordered_indices, _ = relation_edge_candidate_path_cover(
        item_count=len(elements),
        successor_edges=selected_edges,
        precedence_edges=[],
        base_order=list(base_order),
    )
    if len(ordered_indices) != len(elements):
        ordered_indices = list(base_order)
    return RelationGraphOrderEvidence(
        ordered_indices=tuple(ordered_indices),
        selected_edge_diagnostics=tuple(diagnostics),
    )


def _coarse_region_order(
    *,
    page_id: str,
    width: float,
    height: float,
    elements: Sequence[_HierarchyNode],
    regions: Sequence[_HierarchyNode],
    members_by_region: Mapping[str, Sequence[str]],
    relation_evidence: RelationGraphOrderEvidence,
    chunkr_model: str | Path | None,
) -> tuple[
    tuple[int, ...],
    dict[str, Any],
    RelationGraphOrderEvidence | None,
]:
    region_index = {region.id: index for index, region in enumerate(regions)}
    if chunkr_model is None:
        order = _member_completion_region_order(
            elements,
            regions,
            members_by_region=members_by_region,
            relation_order=relation_evidence.ordered_indices,
            width=width,
            height=height,
        )
        return order, {
            "coarse_order_source": "fine-relation-member-completion",
            "coarse_model_provider": None,
            "coarse_model_sha256": None,
            "coarse_model_page_profile_in_envelope": None,
            "coarse_model_page_profile_outlier_names": [],
            "transitions_enabled": True,
            "transition_policy": "fine-relation-graph-review-only",
            "cross_region_transition_confidence": None,
            "cross_region_transition_confidence_policy": (
                "selected-edge-score-capped-at-review-confidence"
            ),
            "promotion_decision": "review-only-fine-relation-graph",
        }, relation_evidence

    prediction = predict_chunkr_block_order(
        {
            "id": f"{page_id}-coarse-regions",
            "doc_category": "hierarchical-order-proposal",
            "width": width,
            "height": height,
            "elements": [
                {
                    "id": region.id,
                    "box": region.bbox.as_list(),
                    "role": region.role,
                }
                for region in regions
            ],
        },
        chunkr_model,
    )
    try:
        order = tuple(region_index[region_id] for region_id in prediction.ordered_ids)
    except KeyError as exc:
        raise ValueError(
            "coarse model returned an unknown hierarchy region id"
        ) from exc
    if len(order) != len(regions) or set(order) != set(range(len(regions))):
        raise ValueError(
            "coarse model did not return a complete hierarchy region order"
        )
    in_envelope = prediction.diagnostics.get("page_profile_in_envelope")
    transitions_enabled = in_envelope is True
    if in_envelope is True:
        decision = "review-only-model-in-envelope"
        transition_policy = "chunkr-coarse-model-review-only"
    elif in_envelope is False:
        decision = "reject-cross-region-transitions-page-profile-ood"
        transition_policy = "chunkr-coarse-model-suppressed-ood"
    else:
        decision = "reject-cross-region-transitions-profile-unavailable"
        transition_policy = "chunkr-coarse-model-suppressed-profile-unavailable"
    ranker_metadata = prediction.payload.get("chunkr_order_ranker")
    if not isinstance(ranker_metadata, Mapping):
        ranker_metadata = {}
    return order, {
        "coarse_order_source": "chunkr-pairwise-ranker",
        "coarse_model_provider": CHUNKR_ORDER_PROVIDER,
        "coarse_model_sha256": ranker_metadata.get("model_sha256"),
        "coarse_model_page_profile_in_envelope": in_envelope,
        "coarse_model_page_profile_outlier_names": list(
            prediction.diagnostics.get("page_profile_outlier_names") or []
        ),
        "transitions_enabled": transitions_enabled,
        "transition_policy": transition_policy,
        "cross_region_transition_confidence": round(
            min(
                float(prediction.diagnostics.get("mean_adjacent_precedence") or 0.0),
                0.76,
            ),
            8,
        ),
        "cross_region_transition_confidence_policy": (
            "chunkr-mean-adjacent-precedence-capped-at-review-confidence"
        ),
        "promotion_decision": decision,
    }, None


def _member_completion_region_order(
    elements: Sequence[_HierarchyNode],
    regions: Sequence[_HierarchyNode],
    *,
    members_by_region: Mapping[str, Sequence[str]],
    relation_order: Sequence[int],
    width: float,
    height: float,
) -> tuple[int, ...]:
    if len(relation_order) != len(elements) or set(relation_order) != set(
        range(len(elements))
    ):
        raise ValueError("fine relation graph did not return a complete order")
    relation_rank = {
        elements[element_index].id: rank
        for rank, element_index in enumerate(relation_order)
    }
    geometry_order = _selected_order(regions, width=width, height=height)
    geometry_rank = {
        regions[region_index].id: rank
        for rank, region_index in enumerate(geometry_order)
    }

    def completion_key(region_index: int) -> tuple[int, int, int, str]:
        region = regions[region_index]
        member_ranks = [
            relation_rank[member_id]
            for member_id in members_by_region[region.id]
            if member_id in relation_rank
        ]
        if not member_ranks:
            return (1, len(elements), geometry_rank[region.id], region.id)
        return (
            0,
            max(member_ranks),
            geometry_rank[region.id],
            region.id,
        )

    return tuple(sorted(range(len(regions)), key=completion_key))


def _region_streams(
    coarse_ids: Sequence[str],
    *,
    regions: Sequence[_HierarchyNode],
    members_by_region: Mapping[str, Sequence[str]],
    membership_by_element: Mapping[str, _Membership],
) -> list[dict[str, Any]]:
    role_by_region = {region.id: region.role for region in regions}
    streams: list[dict[str, Any]] = []
    for region_id in coarse_ids:
        members = tuple(members_by_region[region_id])
        if not members:
            continue
        membership_confidences = [
            float(
                membership_by_element[member_id].evidence_confidence
                if membership_by_element[member_id].evidence_confidence is not None
                else membership_by_element[member_id].coverage or 0.0
            )
            for member_id in members
        ]
        stream_confidence = min(min(membership_confidences), 0.76)
        streams.append(
            {
                "id": f"hierarchy-region-{region_id}",
                "type": _stream_type(role_by_region[region_id]),
                "region_id": region_id,
                "order_policy": "preserve-selected-auto-relative-order",
                "members": list(members),
                "successor_edges": [],
                "review_successor_edges": [
                    {
                        "source": source,
                        "target": target,
                        "confidence": round(stream_confidence, 8),
                        "review_required": True,
                        "evidence": [
                            "hierarchical-region-membership",
                            "preserve-selected-auto-relative-order",
                        ],
                        "provenance": {
                            "kind": "hierarchical-within-region-successor-v1",
                            "region_id": region_id,
                            "provider": HIERARCHICAL_ORDER_PROVIDER,
                        },
                    }
                    for source, target in zip(members, members[1:], strict=False)
                ],
                "proposal": {
                    "origin": "hierarchical-region-membership",
                    "confidence": round(stream_confidence, 8),
                    "evidence": [
                        "coarse-region-membership",
                        "base-local-line-order",
                    ],
                },
            }
        )
    return streams


def _unassigned_fallback_relations(
    base_ids: Sequence[str],
    *,
    membership_by_element: Mapping[str, _Membership],
    region_streams: Sequence[Mapping[str, Any]],
    cross_transitions: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Preserve native adjacency only where provider membership is absent."""

    unassigned = {
        element_id
        for element_id in base_ids
        if membership_by_element[element_id].region_id is None
    }
    if not unassigned:
        return [], [], {
            "unassigned_fallback_stream_count": 0,
            "unassigned_fallback_member_count": 0,
            "unassigned_fallback_within_edge_count": 0,
            "unassigned_fallback_transition_candidate_count": 0,
            "unassigned_fallback_transition_emitted_count": 0,
            "unassigned_fallback_transition_degree_suppressed_count": 0,
            "unassigned_fallback_transition_cycle_suppressed_count": 0,
        }

    segments: list[list[str]] = []
    current: list[str] = []
    for element_id in base_ids:
        if element_id in unassigned:
            current.append(element_id)
        elif current:
            segments.append(current)
            current = []
    if current:
        segments.append(current)

    fallback_streams: list[dict[str, Any]] = []
    stream_by_element: dict[str, str] = {}
    region_by_element = {
        element_id: membership.region_id
        for element_id, membership in membership_by_element.items()
    }
    for stream in region_streams:
        stream_id = str(stream.get("id") or "")
        for element_id in stream.get("members", []):
            stream_by_element[str(element_id)] = stream_id
    for index, segment in enumerate(segments, start=1):
        stream_id = f"hierarchy-unassigned-{index:03d}"
        stream_by_element.update((element_id, stream_id) for element_id in segment)
        fallback_streams.append(
            {
                "id": stream_id,
                "type": "unassigned-fallback",
                "region_id": None,
                "order_policy": "preserve-selected-auto-relative-order",
                "members": list(segment),
                "successor_edges": [],
                "review_successor_edges": [
                    {
                        "source": source,
                        "target": target,
                        "confidence": UNASSIGNED_FALLBACK_CONFIDENCE,
                        "review_required": True,
                        "evidence": [
                            "unassigned-membership",
                            "selected-auto-adjacency",
                        ],
                        "provenance": {
                            "kind": "hierarchical-unassigned-fallback-successor-v1",
                            "provider": HIERARCHICAL_ORDER_PROVIDER,
                            "stream_id": stream_id,
                        },
                    }
                    for source, target in zip(segment, segment[1:], strict=False)
                ],
                "proposal": {
                    "origin": "unassigned-base-order-fallback",
                    "confidence": UNASSIGNED_FALLBACK_CONFIDENCE,
                    "evidence": [
                        "membership-abstention",
                        "preserve-selected-auto-relative-order",
                    ],
                },
            }
        )

    existing_edges = {
        (str(edge.get("source") or ""), str(edge.get("target") or ""))
        for stream in (*region_streams, *fallback_streams)
        for key in ("successor_edges", "review_successor_edges")
        for edge in stream.get(key, [])
        if isinstance(edge, Mapping)
    }
    existing_edges.update(
        (str(edge.get("source") or ""), str(edge.get("target") or ""))
        for edge in cross_transitions
        if isinstance(edge, Mapping)
    )
    successor = {source: target for source, target in existing_edges}
    predecessor = {target: source for source, target in existing_edges}
    candidate_count = 0
    degree_suppressed = 0
    cycle_suppressed = 0
    fallback_transitions: list[dict[str, Any]] = []
    for source, target in zip(base_ids, base_ids[1:], strict=False):
        if source not in unassigned and target not in unassigned:
            continue
        if (source, target) in existing_edges:
            continue
        if stream_by_element[source] == stream_by_element[target]:
            continue
        candidate_count += 1
        if source in successor or target in predecessor:
            degree_suppressed += 1
            continue
        if _element_successor_would_cycle(source, target, successor):
            cycle_suppressed += 1
            continue
        successor[source] = target
        predecessor[target] = source
        fallback_transitions.append(
            {
                "source_region_id": region_by_element[source],
                "target_region_id": region_by_element[target],
                "source": source,
                "target": target,
                "source_stream_id": stream_by_element[source],
                "target_stream_id": stream_by_element[target],
                "reason": "unassigned-base-order-fallback",
                "boundary_aligned": True,
                "confidence": UNASSIGNED_FALLBACK_CONFIDENCE,
                "review_required": True,
                "evidence": [
                    "membership-abstention-boundary",
                    "selected-auto-adjacency",
                    "degree-one-acyclic-fallback",
                ],
                "provenance": {
                    "kind": "hierarchical-unassigned-fallback-transition-v1",
                    "provider": HIERARCHICAL_ORDER_PROVIDER,
                    "transition_policy": "unassigned-base-order-fallback-review-only",
                },
            }
        )
    return fallback_streams, fallback_transitions, {
        "unassigned_fallback_stream_count": len(fallback_streams),
        "unassigned_fallback_member_count": len(unassigned),
        "unassigned_fallback_within_edge_count": sum(
            len(stream["review_successor_edges"]) for stream in fallback_streams
        ),
        "unassigned_fallback_transition_candidate_count": candidate_count,
        "unassigned_fallback_transition_emitted_count": len(fallback_transitions),
        "unassigned_fallback_transition_degree_suppressed_count": degree_suppressed,
        "unassigned_fallback_transition_cycle_suppressed_count": cycle_suppressed,
    }


def _element_successor_would_cycle(
    source: str,
    target: str,
    successor: Mapping[str, str],
) -> bool:
    cursor = target
    visited: set[str] = set()
    while cursor in successor and cursor not in visited:
        if cursor == source:
            return True
        visited.add(cursor)
        cursor = successor[cursor]
    return cursor == source


def _relation_graph_cross_region_transitions(
    relation_evidence: RelationGraphOrderEvidence,
    *,
    elements: Sequence[_HierarchyNode],
    membership_by_element: Mapping[str, _Membership],
    members_by_region: Mapping[str, Sequence[str]],
    region_role_by_id: Mapping[str, str],
    transition_policy: str,
    protected_edge_keys: set[tuple[int, int]] | None = None,
    external_edge_keys: set[tuple[int, int]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    protected = protected_edge_keys or set()
    external = external_edge_keys or set()
    candidates: list[_CrossRegionRelationCandidate] = []
    for diagnostic in relation_evidence.selected_edge_diagnostics:
        if not (
            0 <= diagnostic.source < len(elements)
            and 0 <= diagnostic.target < len(elements)
        ):
            raise ValueError("fine relation graph returned an unknown element index")
        source = elements[diagnostic.source].id
        target = elements[diagnostic.target].id
        source_region = membership_by_element[source].region_id
        target_region = membership_by_element[target].region_id
        if (
            source_region is None
            or target_region is None
            or source_region == target_region
        ):
            continue
        source_members = members_by_region[source_region]
        target_members = members_by_region[target_region]
        boundary_aligned = bool(
            source_members
            and target_members
            and source_members[-1] == source
            and target_members[0] == target
        )
        candidates.append(
            _CrossRegionRelationCandidate(
                source=source,
                target=target,
                source_region=source_region,
                target_region=target_region,
                diagnostic=diagnostic,
                boundary_aligned=boundary_aligned,
            )
        )

    boundary_candidates = [
        candidate for candidate in candidates if candidate.boundary_aligned
    ]
    native_boundary_candidates = sorted(
        (
            candidate
            for candidate in boundary_candidates
            if (candidate.diagnostic.source, candidate.diagnostic.target)
            not in external
        ),
        key=lambda candidate: (
            candidate.diagnostic.has_tied_alternative,
            -candidate.diagnostic.selection_regret,
            -candidate.diagnostic.score,
            candidate.diagnostic.selection_step,
            candidate.source_region,
            candidate.target_region,
        ),
    )
    external_boundary_candidates = sorted(
        (
            candidate
            for candidate in boundary_candidates
            if (candidate.diagnostic.source, candidate.diagnostic.target)
            in external
        ),
        key=lambda candidate: (
            -candidate.diagnostic.score,
            -candidate.diagnostic.selection_regret,
            candidate.diagnostic.selection_step,
            candidate.source_region,
            candidate.target_region,
        ),
    )
    successor_by_region: dict[str, str] = {}
    predecessor_by_region: dict[str, str] = {}
    selected_keys: set[tuple[str, str]] = set()
    suppression_reasons: dict[tuple[str, str], str] = {}
    candidate_by_key = {candidate.edge_key: candidate for candidate in boundary_candidates}
    for candidate in native_boundary_candidates:
        object_branch_reason = _object_branch_endpoint_suppression_reason(
            candidate,
            region_role_by_id=region_role_by_id,
        )
        if object_branch_reason is not None:
            suppression_reasons[candidate.edge_key] = object_branch_reason
            continue
        if candidate.source_region in successor_by_region:
            suppression_reasons[candidate.edge_key] = "region-outgoing-conflict"
            continue
        if candidate.target_region in predecessor_by_region:
            suppression_reasons[candidate.edge_key] = "region-incoming-conflict"
            continue
        if _region_successor_would_cycle(
            candidate.source_region,
            candidate.target_region,
            successor_by_region,
        ):
            suppression_reasons[candidate.edge_key] = "region-cycle"
            continue
        successor_by_region[candidate.source_region] = candidate.target_region
        predecessor_by_region[candidate.target_region] = candidate.source_region
        selected_keys.add(candidate.edge_key)

    external_replacement_count = 0
    for candidate in external_boundary_candidates:
        object_branch_reason = _object_branch_endpoint_suppression_reason(
            candidate,
            region_role_by_id=region_role_by_id,
        )
        if object_branch_reason is not None:
            suppression_reasons[candidate.edge_key] = object_branch_reason
            continue
        conflicting = [
            candidate_by_key[edge_key]
            for edge_key in selected_keys
            if candidate_by_key[edge_key].source_region == candidate.source_region
            or candidate_by_key[edge_key].target_region == candidate.target_region
        ]
        if len(conflicting) != 1:
            suppression_reasons[candidate.edge_key] = (
                "semantic-requires-single-native-region-conflict"
            )
            continue
        replaced = conflicting[0]
        replaced_index_key = (
            replaced.diagnostic.source,
            replaced.diagnostic.target,
        )
        if replaced_index_key not in protected:
            suppression_reasons[candidate.edge_key] = (
                "semantic-conflict-is-not-native-relation"
            )
            continue
        score_margin = candidate.diagnostic.score - replaced.diagnostic.score
        if score_margin < EXTERNAL_RELATION_REPLACEMENT_MARGIN:
            suppression_reasons[candidate.edge_key] = (
                "semantic-native-confidence-margin-too-small"
            )
            continue
        selected_keys.remove(replaced.edge_key)
        del successor_by_region[replaced.source_region]
        del predecessor_by_region[replaced.target_region]
        if _region_successor_would_cycle(
            candidate.source_region,
            candidate.target_region,
            successor_by_region,
        ):
            successor_by_region[replaced.source_region] = replaced.target_region
            predecessor_by_region[replaced.target_region] = replaced.source_region
            selected_keys.add(replaced.edge_key)
            suppression_reasons[candidate.edge_key] = "region-cycle"
            continue
        suppression_reasons[replaced.edge_key] = (
            "replaced-by-semantic-confidence-margin"
        )
        successor_by_region[candidate.source_region] = candidate.target_region
        predecessor_by_region[candidate.target_region] = candidate.source_region
        selected_keys.add(candidate.edge_key)
        external_replacement_count += 1

    transitions: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates,
        key=lambda candidate: (
            candidate.diagnostic.selection_step,
            candidate.source,
            candidate.target,
        ),
    ):
        relation_payload = candidate.diagnostic.as_payload()
        relation_edge_key = (
            candidate.diagnostic.source,
            candidate.diagnostic.target,
        )
        relation_source = (
            "semantic-successor-ranker"
            if relation_edge_key in external
            else "native-relation-graph"
        )
        confidence = round(
            min(max(candidate.diagnostic.score, 0.0), 0.76),
            8,
        )
        suppression_reason = suppression_reasons.get(candidate.edge_key)
        if not candidate.boundary_aligned:
            suppression_reason = "not-local-stream-boundary"
        selected = candidate.edge_key in selected_keys
        evidence_record = {
            "source": candidate.source,
            "target": candidate.target,
            "source_region_id": candidate.source_region,
            "target_region_id": candidate.target_region,
            "boundary_aligned": candidate.boundary_aligned,
            "transition_status": "selected" if selected else "evidence-only",
            "suppression_reason": suppression_reason,
            "confidence": confidence,
            "relation_source": relation_source,
            "relation_graph": relation_payload,
        }
        evidence_records.append(evidence_record)
        if not selected:
            continue
        transition_evidence = [
            "fine-relation-graph-selected-edge",
            "local-stream-boundary",
        ]
        if relation_edge_key in external:
            transition_evidence.append("semantic-successor-ranker")
        if candidate.diagnostic.has_tied_alternative:
            transition_evidence.append("tied-alternative-review-only")
        transitions.append(
            {
                "source_region_id": candidate.source_region,
                "target_region_id": candidate.target_region,
                "source": candidate.source,
                "target": candidate.target,
                "source_stream_id": (
                    f"hierarchy-region-{candidate.source_region}"
                ),
                "target_stream_id": (
                    f"hierarchy-region-{candidate.target_region}"
                ),
                "reason": "fine-relation-graph-stream-boundary",
                "boundary_aligned": True,
                "confidence": confidence,
                "review_required": True,
                "evidence": transition_evidence,
                "relation_graph": relation_payload,
                "provenance": {
                    "kind": "hierarchical-cross-region-relation-v2",
                    "source_region_id": candidate.source_region,
                    "target_region_id": candidate.target_region,
                    "transition_policy": transition_policy,
                    "provider": HIERARCHICAL_ORDER_PROVIDER,
                    "relation_source": relation_source,
                },
            }
        )

    tied_count = sum(
        candidate.diagnostic.has_tied_alternative for candidate in candidates
    )
    return transitions, evidence_records, {
        "fine_relation_selected_edge_count": len(
            relation_evidence.selected_edge_diagnostics
        ),
        "fine_relation_cross_region_edge_count": len(candidates),
        "fine_relation_boundary_aligned_edge_count": len(boundary_candidates),
        "fine_relation_nonboundary_evidence_count": (
            len(candidates) - len(boundary_candidates)
        ),
        "fine_relation_tied_cross_region_edge_count": tied_count,
        "external_relation_selected_edge_count": len(external),
        "external_relation_cross_region_edge_count": sum(
            (candidate.diagnostic.source, candidate.diagnostic.target) in external
            for candidate in candidates
        ),
        "external_relation_boundary_aligned_edge_count": sum(
            candidate.boundary_aligned
            and (candidate.diagnostic.source, candidate.diagnostic.target) in external
            for candidate in candidates
        ),
        "external_relation_emitted_transition_count": sum(
            transition.get("provenance", {}).get("relation_source")
            == "semantic-successor-ranker"
            for transition in transitions
        ),
        "external_relation_replacement_count": external_replacement_count,
        "fine_relation_region_cycle_suppressed_count": sum(
            reason == "region-cycle" for reason in suppression_reasons.values()
        ),
        "fine_relation_region_degree_suppressed_count": sum(
            reason in {"region-outgoing-conflict", "region-incoming-conflict"}
            for reason in suppression_reasons.values()
        ),
        "fine_relation_object_branch_suppressed_count": sum(
            reason
            in {
                "table-region-terminal-branch",
                "figure-region-root-branch",
            }
            for reason in suppression_reasons.values()
        ),
        "fine_relation_table_source_suppressed_count": sum(
            reason == "table-region-terminal-branch"
            for reason in suppression_reasons.values()
        ),
        "fine_relation_figure_target_suppressed_count": sum(
            reason == "figure-region-root-branch"
            for reason in suppression_reasons.values()
        ),
    }


def _object_branch_endpoint_suppression_reason(
    candidate: _CrossRegionRelationCandidate,
    *,
    region_role_by_id: Mapping[str, str],
) -> str | None:
    """Keep graphical object/caption relations as branches, not through-paths."""

    source_role = str(region_role_by_id.get(candidate.source_region) or "").strip().lower()
    target_role = str(region_role_by_id.get(candidate.target_region) or "").strip().lower()
    if source_role == "table":
        return "table-region-terminal-branch"
    if target_role == "figure":
        return "figure-region-root-branch"
    return None


def _region_successor_would_cycle(
    source_region: str,
    target_region: str,
    successor_by_region: Mapping[str, str],
) -> bool:
    cursor = target_region
    visited: set[str] = set()
    while cursor in successor_by_region and cursor not in visited:
        if cursor == source_region:
            return True
        visited.add(cursor)
        cursor = successor_by_region[cursor]
    return cursor == source_region


def _is_complete_cross_region_chain(
    coarse_ids: Sequence[str],
    *,
    members_by_region: Mapping[str, Sequence[str]],
    cross_transitions: Sequence[Mapping[str, Any]],
) -> bool:
    if len(coarse_ids) < 2 or any(
        not members_by_region[region_id] for region_id in coarse_ids
    ):
        return False
    expected_pairs = list(zip(coarse_ids, coarse_ids[1:], strict=False))
    if len(cross_transitions) != len(expected_pairs):
        return False
    actual_pairs = [
        (
            str(transition.get("source_region_id") or ""),
            str(transition.get("target_region_id") or ""),
        )
        for transition in cross_transitions
    ]
    if set(actual_pairs) != set(expected_pairs):
        return False
    return all(
        str(transition.get("source") or "")
        == members_by_region[source_region][-1]
        and str(transition.get("target") or "")
        == members_by_region[target_region][0]
        for transition in cross_transitions
        for source_region, target_region in [
            (
                str(transition.get("source_region_id") or ""),
                str(transition.get("target_region_id") or ""),
            )
        ]
    )


def _cross_region_transitions(
    region_pairs: Sequence[tuple[str, str]],
    *,
    members_by_region: Mapping[str, Sequence[str]],
    transition_policy: str,
    transition_confidence: float,
) -> list[dict[str, Any]]:
    return [
        {
            "source_region_id": source_region,
            "target_region_id": target_region,
            "source": members_by_region[source_region][-1],
            "target": members_by_region[target_region][0],
            "source_stream_id": f"hierarchy-region-{source_region}",
            "target_stream_id": f"hierarchy-region-{target_region}",
            "reason": "hierarchical-coarse-region-order",
            "confidence": round(min(max(transition_confidence, 0.0), 0.76), 8),
            "review_required": True,
            "evidence": [
                "coarse-region-order",
                "preserve-within-region-order",
            ],
            "provenance": {
                "kind": "hierarchical-cross-region-transition-v1",
                "source_region_id": source_region,
                "target_region_id": target_region,
                "transition_policy": transition_policy,
                "provider": HIERARCHICAL_ORDER_PROVIDER,
            },
        }
        for source_region, target_region in region_pairs
    ]


def _has_unassigned_boundary_gap(
    source_region: str,
    target_region: str,
    *,
    members_by_region: Mapping[str, Sequence[str]],
    base_rank: Mapping[str, int],
    unassigned_ids: set[str],
) -> bool:
    source = members_by_region[source_region][-1]
    target = members_by_region[target_region][0]
    lower = min(base_rank[source], base_rank[target])
    upper = max(base_rank[source], base_rank[target])
    return any(lower < base_rank[element_id] < upper for element_id in unassigned_ids)


def _hierarchical_candidate_order(
    elements: Sequence[_HierarchyNode],
    *,
    base_ids: Sequence[str],
    streams: Sequence[Mapping[str, Any]],
    cross_transitions: Sequence[Mapping[str, Any]],
    enabled: bool,
) -> tuple[str, ...]:
    if not enabled:
        return tuple(base_ids)
    element_index = {element.id: index for index, element in enumerate(elements)}
    base_order = [element_index[element_id] for element_id in base_ids]
    successor_edges = [
        (element_index[str(edge["source"])], element_index[str(edge["target"])])
        for stream in streams
        for edge in stream["review_successor_edges"]
    ]
    precedence_edges = [
        (
            element_index[str(transition["source"])],
            element_index[str(transition["target"])],
        )
        for transition in cross_transitions
    ]
    order, _chains = relation_edge_candidate_path_cover(
        item_count=len(elements),
        successor_edges=successor_edges,
        precedence_edges=precedence_edges,
        base_order=base_order,
    )
    if len(order) != len(elements) or set(order) != set(range(len(elements))):
        raise ValueError("hierarchical relation expansion did not produce a full order")
    return tuple(elements[index].id for index in order)


def _stream_type(role: str) -> str:
    normalized = "-".join(role.strip().casefold().replace("_", "-").split())
    if "table" in normalized:
        return "table"
    if normalized in {"caption", "figure-caption", "table-caption", "legend"}:
        return "caption"
    if normalized in {"footnote", "footer", "page-number"}:
        return "footnote"
    if normalized in {"aside", "aside-text", "sidebar", "sidebar-text"}:
        return "sidebar"
    if normalized in {"picture", "figure", "image", "graphical-item", "chart"}:
        return "figure"
    return "body"


def _bbox_coverage(inner: BBox, outer: BBox) -> float:
    intersection_width = max(
        0.0,
        min(inner.x1, outer.x1) - max(inner.x0, outer.x0),
    )
    intersection_height = max(
        0.0,
        min(inner.y1, outer.y1) - max(inner.y0, outer.y0),
    )
    intersection = intersection_width * intersection_height
    area = max(inner.width * inner.height, 1e-9)
    return max(0.0, min(1.0, intersection / area))


def _pair_disagreement(first: Sequence[str], second: Sequence[str]) -> float:
    if set(first) != set(second) or len(first) != len(second):
        raise ValueError("hierarchy candidate orders must contain the same ids")
    pair_count = len(first) * (len(first) - 1) // 2
    if not pair_count:
        return 0.0
    first_rank = {item_id: index for index, item_id in enumerate(first)}
    second_rank = {item_id: index for index, item_id in enumerate(second)}
    disagreement = sum(
        (first_rank[source] < first_rank[target])
        != (second_rank[source] < second_rank[target])
        for source_index, source in enumerate(first)
        for target in first[source_index + 1 :]
    )
    return round(disagreement / pair_count, 8)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("hierarchy input must be JSON-serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _positive_number(value: Any, label: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a positive number") from exc
    if normalized <= 0 or normalized == float("inf") or normalized != normalized:
        raise ValueError(f"{label} must be a positive number")
    return normalized


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if normalized < 0 or str(normalized) != str(value).strip():
        raise ValueError(f"{label} must be a non-negative integer")
    return normalized


def _declared_granularity(value: Any, *, label: str, expected: str) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized != expected:
        raise ValueError(f"{label} must explicitly declare {expected!r} granularity")
    return normalized


def _validate_unit_interval(value: float, label: str, *, allow_zero: bool) -> None:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be between 0 and 1") from exc
    valid_lower = normalized >= 0.0 if allow_zero else normalized > 0.0
    if not valid_lower or normalized > 1 or normalized != normalized:
        raise ValueError(f"{label} must be between 0 and 1")


def _rounded_optional(value: float | None) -> float | None:
    return round(value, 8) if value is not None else None
