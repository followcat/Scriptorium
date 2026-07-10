from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from itertools import combinations
import re
from typing import Literal
from statistics import median

from .geometry import reading_order_key
from .models import BBox
from .reading_streams import assign_reading_streams_to_metadata

ReadingOrderStrategy = Literal["auto", "visual-yx", "column-flow-v1", "recursive-xy-cut-v1"]


@dataclass(frozen=True)
class ReadingOrderAssignment:
    item_index: int
    semantic_order: int
    visual_order: int
    column_index: int | None
    column_count: int
    column_span: str
    flow_segment_index: int
    strategy: str
    region_path: str | None = None
    artifact_type: str | None = None
    scope: str = "body"
    sidebar_type: str | None = None
    caption_type: str | None = None
    confidence: float = 0.5
    evidence: tuple[str, ...] = ()
    reading_order_stream_id: str | None = None
    reading_order_stream_type: str | None = None
    reading_order_stream_index: int | None = None

    def as_metadata(self) -> dict[str, object]:
        scope = self.scope
        if self.artifact_type and scope == "body":
            scope = "page-artifact"
        elif self.sidebar_type and scope == "body":
            scope = "sidebar"
        stream_metadata = _assignment_stream_metadata(self)
        return {
            "semantic_order": self.semantic_order,
            "visual_order": self.visual_order,
            "column_index": self.column_index,
            "column_count": self.column_count,
            "column_span": self.column_span,
            "flow_segment_index": self.flow_segment_index,
            "reading_order_strategy": self.strategy,
            "reading_order_region_path": self.region_path,
            "reading_order_scope": scope,
            "reading_order_artifact_type": self.artifact_type,
            "reading_order_sidebar_type": self.sidebar_type,
            "reading_order_caption_type": self.caption_type,
            "reading_order_stream_id": self.reading_order_stream_id
            or stream_metadata["reading_order_stream_id"],
            "reading_order_stream_type": self.reading_order_stream_type
            or stream_metadata["reading_order_stream_type"],
            "reading_order_stream_index": self.reading_order_stream_index
            or stream_metadata["reading_order_stream_index"],
            "reading_order_confidence": _bounded_confidence(self.confidence),
            "reading_order_evidence": list(self.evidence),
            "reading_order_evidence_summary": ",".join(self.evidence),
        }


def _with_reading_streams(assignments: list[ReadingOrderAssignment]) -> list[ReadingOrderAssignment]:
    if not assignments:
        return []
    metadata_by_assignment = [
        (assignment, _assignment_stream_metadata(assignment, assign_index=False))
        for assignment in assignments
    ]
    assign_reading_streams_to_metadata(
        (metadata for _assignment, metadata in metadata_by_assignment),
        order_key=lambda metadata: (
            int(metadata.get("semantic_order") or 1_000_000),
            int(metadata.get("visual_order") or 1_000_000),
        ),
    )
    return [
        replace(
            assignment,
            reading_order_stream_id=str(metadata["reading_order_stream_id"]),
            reading_order_stream_type=str(metadata["reading_order_stream_type"]),
            reading_order_stream_index=int(metadata["reading_order_stream_index"]),
        )
        for assignment, metadata in metadata_by_assignment
    ]


def _assignment_stream_metadata(
    assignment: ReadingOrderAssignment,
    *,
    assign_index: bool = True,
) -> dict[str, object]:
    scope = assignment.scope
    if assignment.artifact_type and scope == "body":
        scope = "page-artifact"
    elif assignment.sidebar_type and scope == "body":
        scope = "sidebar"
    metadata: dict[str, object] = {
        "semantic_order": assignment.semantic_order,
        "visual_order": assignment.visual_order,
        "column_span": assignment.column_span,
        "flow_segment_index": assignment.flow_segment_index,
        "reading_order_strategy": assignment.strategy,
        "reading_order_region_path": assignment.region_path,
        "reading_order_scope": scope,
        "reading_order_artifact_type": assignment.artifact_type,
        "reading_order_sidebar_type": assignment.sidebar_type,
        "reading_order_caption_type": assignment.caption_type,
        "reading_order_evidence": list(assignment.evidence),
    }
    if assign_index:
        assign_reading_streams_to_metadata(
            [metadata],
            order_key=lambda item: (
                int(item.get("semantic_order") or 1_000_000),
                int(item.get("visual_order") or 1_000_000),
            ),
        )
    return metadata


@dataclass(frozen=True)
class _XyCutResult:
    ordered_indices: list[int]
    region_path_by_item: dict[int, str]
    has_horizontal_split: bool
    has_vertical_split: bool


@dataclass(frozen=True)
class _TableIsland:
    island_index: int
    indices: tuple[int, ...]
    bbox: BBox
    kind: str = "table"

    @property
    def region_path(self) -> str:
        return f"root/{self.kind}-island-{self.island_index:03d}"


@dataclass(frozen=True)
class _OrderToken:
    kind: str
    bbox: BBox
    indices: tuple[int, ...]
    column_index: int | None
    full_width: bool
    region_path: str | None = None
    caption_type: str | None = None
    confidence: float = 0.5
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SpatialGraphResult:
    ordered_indices: list[int]
    columns: list[list[int]]
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class _BoxFlowResult:
    ordered_indices: list[int]
    columns: list[list[int]]
    confidence: float
    evidence: tuple[str, ...]
    full_width_indices: set[int]


@dataclass(frozen=True)
class _SuccessorConsensusArbitrationResult:
    ordered_indices: list[int]
    columns: list[list[int]]
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class _RelationGraphEdge:
    source: int
    target: int
    score: float


@dataclass(frozen=True)
class _RelationGraphResult:
    ordered_indices: list[int]
    selected_edges: tuple[_RelationGraphEdge, ...]


@dataclass(frozen=True)
class PairwiseOrderDisagreement:
    pair_count: int
    disagreement_count: int
    disagreement_ratio: float


@dataclass(frozen=True)
class SuccessorOrderDisagreement:
    edge_count: int
    disagreement_count: int
    disagreement_ratio: float


@dataclass(frozen=True)
class SuccessorConsensusDiagnostics:
    ordered_indices: list[int]
    candidate_count: int
    item_count: int
    candidate_edge_count: int
    unique_edge_count: int
    selected_edge_count: int
    selected_edge_vote_count: int
    selected_edge_support_ratio: float
    selected_edge_coverage_ratio: float
    conflicted_source_count: int
    conflicted_target_count: int
    conflicted_edge_count: int
    conflicted_edge_ratio: float
    agreement_level: str


def infer_semantic_reading_order(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    strategy: ReadingOrderStrategy = "auto",
    texts: list[str] | None = None,
) -> list[ReadingOrderAssignment]:
    """Infer human-oriented reading order from positioned line boxes.

    The default PDF/PyMuPDF sort is useful as a visual order, but multi-column
    pages need a semantic pass: read the left column top-to-bottom before the
    right column. This heuristic keeps line-level edit anchors intact while
    recording enough metadata for downstream replacement by ML/XY-Cut backends.
    """

    if not bboxes:
        return []

    visual_indices = sorted(range(len(bboxes)), key=lambda index: reading_order_key(bboxes[index]))
    visual_rank = {item_index: rank for rank, item_index in enumerate(visual_indices, start=1)}
    if strategy == "visual-yx":
        return _with_reading_streams(_visual_assignments(visual_indices, visual_rank))

    normalized_texts = _normalize_texts(texts, len(bboxes))
    table_islands = _infer_local_structure_islands(bboxes, page_width, page_height)
    if strategy in {"auto", "column-flow-v1"} and table_islands:
        mixed_table_assignments = _mixed_table_column_flow_assignments(
            bboxes,
            page_width,
            page_height,
            visual_indices,
            visual_rank,
            table_islands,
            normalized_texts,
        )
        if mixed_table_assignments is not None:
            return _with_reading_streams(mixed_table_assignments)

    xy_result = _recursive_xy_cut_order(bboxes, page_width, page_height)
    if strategy == "recursive-xy-cut-v1" or (
        strategy == "auto" and xy_result.has_horizontal_split and xy_result.has_vertical_split
    ):
        return _with_reading_streams(
            _assign_order_metadata(
                xy_result.ordered_indices,
                bboxes,
                page_width,
                page_height,
                visual_rank,
                strategy="recursive-xy-cut-v1",
                region_path_by_item=xy_result.region_path_by_item,
                default_confidence=_xy_cut_confidence(xy_result),
                default_evidence=_xy_cut_evidence(xy_result),
            )
        )

    return _with_reading_streams(
        _column_flow_assignments(bboxes, page_width, page_height, visual_indices, visual_rank, normalized_texts)
    )


def infer_box_flow_order(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    boxes_flow: float = -0.5,
) -> list[int]:
    """Return a pdfminer-style continuous box-flow candidate order.

    ``boxes_flow`` follows the same intuition as pdfminer.six LAParams:
    negative values prefer horizontal position and positive values prefer
    vertical position. This is intentionally a candidate/diagnostic primitive,
    not a replacement for the higher-confidence structural strategies above.
    """

    if not bboxes:
        return []
    flow = max(-1.0, min(1.0, boxes_flow))
    horizontal_weight = (1.0 - flow) / 2.0
    vertical_weight = (1.0 + flow) / 2.0
    width = max(page_width, 1.0)
    height = max(page_height, 1.0)
    heights = [bbox.height for bbox in bboxes if bbox.height > 0]
    median_height = median(heights) if heights else 10.0
    row_tolerance = max(4.0, median_height * 0.7)

    def sort_key(index: int) -> tuple[float, int, float, float, float]:
        bbox = bboxes[index]
        normalized_x = bbox.x0 / width
        normalized_y = bbox.y0 / height
        score = horizontal_weight * normalized_x + vertical_weight * normalized_y
        row_bucket = round(_center_y(bbox) / row_tolerance)
        if vertical_weight >= horizontal_weight:
            return (row_bucket, score, bbox.x0, bbox.y0, index)
        return (score, row_bucket, bbox.x0, bbox.y0, index)

    return sorted(range(len(bboxes)), key=sort_key)


def infer_relation_graph_order(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
) -> list[int]:
    """Return a geometry-only successor-graph candidate order.

    This is a diagnostic primitive for relation-graph reading-order work. It
    builds local successor edges, selects a degree-constrained path cover with
    a max-regret rule, then serializes the resulting chains. It deliberately
    avoids changing the selected semantic order until benchmark evidence says
    the candidate should be trusted for a class of pages.
    """

    return _infer_relation_graph_result(bboxes, page_width, page_height).ordered_indices


def infer_relation_graph_selected_edges(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
) -> dict[tuple[int, int], float]:
    """Return selected geometry-only relation-graph edges with their scores.

    Unlike a serialized candidate order, this exposes only path-cover edges
    actually selected by the max-regret relation graph. Consumers can use it
    as independent local evidence without accidentally treating path heads
    joined during serialization as a selected successor relation.
    """

    result = _infer_relation_graph_result(bboxes, page_width, page_height)
    return {(edge.source, edge.target): edge.score for edge in result.selected_edges}


def _infer_relation_graph_result(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
) -> _RelationGraphResult:
    if not bboxes:
        return _RelationGraphResult(ordered_indices=[], selected_edges=())
    visual_order = sorted(range(len(bboxes)), key=lambda index: reading_order_key(bboxes[index]))
    source_indices = [
        index
        for index, bbox in enumerate(bboxes)
        if bbox.width >= 8 and bbox.height >= 4
    ]
    if len(source_indices) < 2 or _looks_like_table_grid([bboxes[index] for index in source_indices], page_width):
        return _RelationGraphResult(ordered_indices=visual_order, selected_edges=())

    heights = [bboxes[index].height for index in source_indices if bboxes[index].height > 0]
    median_height = median(heights) if heights else 10.0
    columns = _infer_column_clusters(bboxes, page_width, page_height, indices=source_indices)
    column_by_item = _assign_columns(bboxes, columns) if columns else {}
    column_bounds = _relation_graph_column_bounds(columns, bboxes)
    edges = _relation_graph_candidate_edges(
        bboxes,
        page_width,
        page_height,
        source_indices,
        median_height,
        column_by_item,
        column_bounds,
    )
    if not edges:
        return _RelationGraphResult(ordered_indices=visual_order, selected_edges=())

    successor_by_item: dict[int, int] = {}
    predecessor_by_item: dict[int, int] = {}
    selected_edges: list[_RelationGraphEdge] = []
    while len(successor_by_item) < len(source_indices) - 1:
        edge = _select_relation_graph_edge(edges, successor_by_item, predecessor_by_item)
        if edge is None:
            break
        successor_by_item[edge.source] = edge.target
        predecessor_by_item[edge.target] = edge.source
        selected_edges.append(edge)

    ordered = _serialize_relation_graph_paths(
        source_indices,
        bboxes,
        successor_by_item,
        predecessor_by_item,
    )
    ordered_set = set(ordered)
    ordered.extend(index for index in visual_order if index not in ordered_set)
    return _RelationGraphResult(ordered_indices=ordered, selected_edges=tuple(selected_edges))


def infer_successor_consensus_order(
    candidate_orders: dict[str, list[int]] | list[list[int]],
    item_count: int | None = None,
    base_order: list[int] | None = None,
) -> list[int]:
    """Return a path-cover order from successor edges shared by candidates.

    Each candidate order contributes adjacent successor edges. The consensus
    order selects high-vote edges under one-predecessor/one-successor acyclic
    constraints, then serializes the resulting paths using ``base_order`` as a
    stable tie-breaker. This is a candidate primitive for arbitration, not a
    guarantee that the consensus should replace the selected reading order.
    """

    return successor_consensus_diagnostics(
        candidate_orders,
        item_count=item_count,
        base_order=base_order,
    ).ordered_indices


def successor_consensus_diagnostics(
    candidate_orders: dict[str, list[int]] | list[list[int]],
    item_count: int | None = None,
    base_order: list[int] | None = None,
) -> SuccessorConsensusDiagnostics:
    raw_orders = candidate_orders.values() if isinstance(candidate_orders, dict) else candidate_orders
    normalized_orders = [_dedupe_order(order) for order in raw_orders]
    normalized_orders = [order for order in normalized_orders if len(order) >= 2]
    candidate_count = len(normalized_orders)

    if base_order is None:
        base_order = []
        for order in normalized_orders:
            for item in order:
                if item not in base_order:
                    base_order.append(item)
    else:
        base_order = _dedupe_order(base_order)

    universe = list(base_order)
    if item_count is not None:
        for item in range(item_count):
            if item not in universe:
                universe.append(item)
    for order in normalized_orders:
        for item in order:
            if item not in universe:
                universe.append(item)
    if not universe:
        return _empty_successor_consensus_diagnostics()
    if not normalized_orders:
        return _successor_consensus_diagnostics_from_edges(
            universe,
            edge_votes=Counter(),
            selected_edges={},
            candidate_count=0,
        )

    edge_votes: Counter[tuple[int, int]] = Counter()
    for order in normalized_orders:
        clean_order = [item for item in order if item in universe]
        for source, target in zip(clean_order, clean_order[1:]):
            if source != target:
                edge_votes[(source, target)] += 1
    if not edge_votes:
        return _successor_consensus_diagnostics_from_edges(
            universe,
            edge_votes=edge_votes,
            selected_edges={},
            candidate_count=candidate_count,
        )

    base_rank = {item: rank for rank, item in enumerate(universe)}
    outgoing_votes: dict[int, list[int]] = {}
    incoming_votes: dict[int, list[int]] = {}
    for (source, target), vote_count in edge_votes.items():
        outgoing_votes.setdefault(source, []).append(vote_count)
        incoming_votes.setdefault(target, []).append(vote_count)
    for votes in [*outgoing_votes.values(), *incoming_votes.values()]:
        votes.sort(reverse=True)

    def regret(source: int, target: int, vote_count: int) -> int:
        outgoing_alternative = _consensus_alternative_vote(vote_count, outgoing_votes.get(source, []))
        incoming_alternative = _consensus_alternative_vote(vote_count, incoming_votes.get(target, []))
        return (vote_count - outgoing_alternative) + (vote_count - incoming_alternative)

    ranked_edges = sorted(
        edge_votes,
        key=lambda edge: (
            -edge_votes[edge],
            -regret(edge[0], edge[1], edge_votes[edge]),
            base_rank.get(edge[0], 1_000_000),
            base_rank.get(edge[1], 1_000_000),
            edge[0],
            edge[1],
        ),
    )
    successor_by_item: dict[int, int] = {}
    predecessor_by_item: dict[int, int] = {}
    for source, target in ranked_edges:
        if source in successor_by_item or target in predecessor_by_item:
            continue
        if _relation_graph_would_cycle(source, target, successor_by_item):
            continue
        successor_by_item[source] = target
        predecessor_by_item[target] = source

    ordered_indices = _serialize_consensus_paths(universe, successor_by_item, predecessor_by_item, base_rank)
    return _successor_consensus_diagnostics_from_edges(
        universe,
        edge_votes=edge_votes,
        selected_edges=successor_by_item,
        candidate_count=candidate_count,
        ordered_indices=ordered_indices,
    )


def pairwise_order_disagreement(
    reference_order: list[int],
    candidate_order: list[int],
) -> PairwiseOrderDisagreement:
    candidate_items = set(candidate_order)
    shared = [item for item in reference_order if item in candidate_items]
    pair_count = len(shared) * (len(shared) - 1) // 2
    if pair_count == 0:
        return PairwiseOrderDisagreement(pair_count=0, disagreement_count=0, disagreement_ratio=0.0)

    reference_rank = {item: rank for rank, item in enumerate(reference_order)}
    candidate_rank = {item: rank for rank, item in enumerate(candidate_order)}
    disagreement_count = 0
    for first, second in combinations(shared, 2):
        reference_before = reference_rank[first] < reference_rank[second]
        candidate_before = candidate_rank[first] < candidate_rank[second]
        if reference_before != candidate_before:
            disagreement_count += 1
    return PairwiseOrderDisagreement(
        pair_count=pair_count,
        disagreement_count=disagreement_count,
        disagreement_ratio=round(disagreement_count / pair_count, 8),
    )


def successor_order_disagreement(
    reference_order: list[int],
    candidate_order: list[int],
) -> SuccessorOrderDisagreement:
    candidate_items = set(candidate_order)
    shared_reference = [item for item in reference_order if item in candidate_items]
    edge_count = max(0, len(shared_reference) - 1)
    if edge_count == 0:
        return SuccessorOrderDisagreement(edge_count=0, disagreement_count=0, disagreement_ratio=0.0)

    reference_items = set(reference_order)
    shared_candidate = [item for item in candidate_order if item in reference_items]
    candidate_successor_by_item = {
        item: shared_candidate[index + 1]
        for index, item in enumerate(shared_candidate[:-1])
    }
    correct = sum(
        1
        for index, item in enumerate(shared_reference[:-1])
        if candidate_successor_by_item.get(item) == shared_reference[index + 1]
    )
    disagreement_count = edge_count - correct
    return SuccessorOrderDisagreement(
        edge_count=edge_count,
        disagreement_count=disagreement_count,
        disagreement_ratio=round(disagreement_count / edge_count, 8),
    )


def _dedupe_order(order: list[int]) -> list[int]:
    deduped: list[int] = []
    seen: set[int] = set()
    for item in order:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _empty_successor_consensus_diagnostics() -> SuccessorConsensusDiagnostics:
    return SuccessorConsensusDiagnostics(
        ordered_indices=[],
        candidate_count=0,
        item_count=0,
        candidate_edge_count=0,
        unique_edge_count=0,
        selected_edge_count=0,
        selected_edge_vote_count=0,
        selected_edge_support_ratio=0.0,
        selected_edge_coverage_ratio=0.0,
        conflicted_source_count=0,
        conflicted_target_count=0,
        conflicted_edge_count=0,
        conflicted_edge_ratio=0.0,
        agreement_level="unavailable",
    )


def _successor_consensus_diagnostics_from_edges(
    universe: list[int],
    *,
    edge_votes: Counter[tuple[int, int]],
    selected_edges: dict[int, int],
    candidate_count: int,
    ordered_indices: list[int] | None = None,
) -> SuccessorConsensusDiagnostics:
    ordered_indices = list(universe) if ordered_indices is None else ordered_indices
    candidate_edge_count = sum(edge_votes.values())
    selected_edge_vote_count = sum(edge_votes.get(edge, 0) for edge in selected_edges.items())
    selected_edge_count = len(selected_edges)
    selected_support_ratio = round(
        selected_edge_vote_count / max(selected_edge_count * candidate_count, 1),
        8,
    )
    selected_coverage_ratio = round(selected_edge_count / max(len(universe) - 1, 1), 8)
    outgoing_targets: dict[int, set[int]] = {}
    incoming_sources: dict[int, set[int]] = {}
    for source, target in edge_votes:
        outgoing_targets.setdefault(source, set()).add(target)
        incoming_sources.setdefault(target, set()).add(source)
    conflicted_sources = {source for source, targets in outgoing_targets.items() if len(targets) > 1}
    conflicted_targets = {target for target, sources in incoming_sources.items() if len(sources) > 1}
    conflicted_edges = {
        edge
        for edge in edge_votes
        if edge[0] in conflicted_sources or edge[1] in conflicted_targets
    }
    conflicted_edge_ratio = round(len(conflicted_edges) / max(len(edge_votes), 1), 8) if edge_votes else 0.0
    return SuccessorConsensusDiagnostics(
        ordered_indices=ordered_indices,
        candidate_count=candidate_count,
        item_count=len(universe),
        candidate_edge_count=candidate_edge_count,
        unique_edge_count=len(edge_votes),
        selected_edge_count=selected_edge_count,
        selected_edge_vote_count=selected_edge_vote_count,
        selected_edge_support_ratio=selected_support_ratio,
        selected_edge_coverage_ratio=selected_coverage_ratio,
        conflicted_source_count=len(conflicted_sources),
        conflicted_target_count=len(conflicted_targets),
        conflicted_edge_count=len(conflicted_edges),
        conflicted_edge_ratio=conflicted_edge_ratio,
        agreement_level=_successor_consensus_agreement_level(
            selected_support_ratio,
            selected_coverage_ratio,
            conflicted_edge_ratio,
            candidate_count,
            selected_edge_count,
        ),
    )


def _successor_consensus_agreement_level(
    support_ratio: float,
    coverage_ratio: float,
    conflict_ratio: float,
    candidate_count: int,
    selected_edge_count: int,
) -> str:
    if candidate_count < 2 or selected_edge_count == 0:
        return "unavailable"
    if support_ratio >= 0.75 and coverage_ratio >= 0.8 and conflict_ratio <= 0.25:
        return "high"
    if support_ratio >= 0.55 and coverage_ratio >= 0.55 and conflict_ratio <= 0.6:
        return "medium"
    return "low"


def _consensus_alternative_vote(vote_count: int, votes: list[int]) -> int:
    skipped_current = False
    for alternative in votes:
        if not skipped_current and alternative == vote_count:
            skipped_current = True
            continue
        return alternative
    return 0


def _serialize_consensus_paths(
    source_indices: list[int],
    successor_by_item: dict[int, int],
    predecessor_by_item: dict[int, int],
    base_rank: dict[int, int],
) -> list[int]:
    heads = [item for item in source_indices if item not in predecessor_by_item]
    ordered: list[int] = []
    visited: set[int] = set()
    for head_index in sorted(heads, key=lambda index: (base_rank.get(index, 1_000_000), index)):
        cursor = head_index
        while cursor not in visited:
            visited.add(cursor)
            ordered.append(cursor)
            if cursor not in successor_by_item:
                break
            cursor = successor_by_item[cursor]
    for item in sorted(source_indices, key=lambda index: (base_rank.get(index, 1_000_000), index)):
        if item not in visited:
            ordered.append(item)
    return ordered


def _visual_assignments(
    visual_indices: list[int],
    visual_rank: dict[int, int],
    artifact_type_by_item: dict[int, str] | None = None,
    caption_type_by_item: dict[int, str] | None = None,
    strategy: str = "visual-yx",
    base_confidence: float = 0.62,
    base_evidence: tuple[str, ...] = ("visual-yx",),
) -> list[ReadingOrderAssignment]:
    artifact_type_by_item = artifact_type_by_item or {}
    caption_type_by_item = caption_type_by_item or {}
    assignments: list[ReadingOrderAssignment] = []
    for order, item_index in enumerate(visual_indices, start=1):
        artifact_type = artifact_type_by_item.get(item_index)
        caption_type = caption_type_by_item.get(item_index)
        assignments.append(
            ReadingOrderAssignment(
                item_index=item_index,
                semantic_order=order,
                visual_order=visual_rank[item_index],
                column_index=None if artifact_type else 0,
                column_count=1,
                column_span=_artifact_column_span(artifact_type)
                if artifact_type
                else "caption-column"
                if caption_type
                else "single",
                flow_segment_index=1,
                strategy=strategy,
                artifact_type=artifact_type,
                caption_type=caption_type,
                confidence=_artifact_confidence(artifact_type)
                if artifact_type
                else _caption_confidence(caption_type)
                if caption_type
                else base_confidence,
                evidence=_merge_evidence(
                    base_evidence,
                    _artifact_evidence(artifact_type) if artifact_type else (),
                    _caption_evidence(caption_type, full_width=False) if caption_type else (),
                ),
            )
        )
    return assignments


def _column_flow_assignments(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    visual_indices: list[int],
    visual_rank: dict[int, int],
    texts: list[str],
) -> list[ReadingOrderAssignment]:
    artifact_type_by_item = _infer_marginal_artifacts(bboxes, page_width, page_height)
    non_artifact_indices = [index for index in range(len(bboxes)) if index not in artifact_type_by_item]
    sidebar_type_by_item = _infer_sidebar_items(bboxes, page_width, page_height, indices=non_artifact_indices)
    non_sidebar_indices = [index for index in non_artifact_indices if index not in sidebar_type_by_item]
    footnote_indices = _infer_footnote_items(bboxes, page_width, page_height, indices=non_sidebar_indices)
    caption_type_by_item = _infer_caption_items(bboxes, texts, page_width, page_height, indices=non_sidebar_indices)
    body_indices = [
        index
        for index in range(len(bboxes))
        if index not in artifact_type_by_item
        and index not in sidebar_type_by_item
        and index not in footnote_indices
    ]
    columns = _infer_column_clusters(bboxes, page_width, page_height, indices=body_indices)
    column_count = len(columns)
    if column_count < 2 and not sidebar_type_by_item and not footnote_indices:
        if _looks_like_table_grid([bboxes[index] for index in body_indices], page_width):
            return _visual_assignments(
                visual_indices,
                visual_rank,
                artifact_type_by_item=artifact_type_by_item,
                caption_type_by_item=caption_type_by_item,
                strategy=_table_row_major_strategy(artifact_type_by_item),
                base_confidence=0.82,
                base_evidence=("table-row-major", "table-grid-slots"),
            )
        spatial_graph_result = _spatial_graph_order(bboxes, page_width, page_height, body_indices)
        if spatial_graph_result is not None:
            header_indices = {
                index for index, artifact_type in artifact_type_by_item.items() if artifact_type == "header"
            }
            footer_indices = {
                index for index, artifact_type in artifact_type_by_item.items() if artifact_type == "footer"
            }
            ordered_indices = [
                *sorted(header_indices, key=lambda index: reading_order_key(bboxes[index])),
                *spatial_graph_result.ordered_indices,
                *sorted(footer_indices, key=lambda index: reading_order_key(bboxes[index])),
            ]
            return _assign_order_metadata(
                ordered_indices,
                bboxes,
                page_width,
                page_height,
                visual_rank,
                strategy=_spatial_graph_strategy(artifact_type_by_item),
                flow_segment_by_item=_flow_segments_for_order(ordered_indices, bboxes),
                artifact_type_by_item=artifact_type_by_item,
                scope_by_item={item_index: "page-artifact" for item_index in artifact_type_by_item},
                column_span_by_item={
                    **{
                        item_index: _artifact_column_span(artifact_type)
                        for item_index, artifact_type in artifact_type_by_item.items()
                    },
                    **{
                        item_index: "caption-full"
                        if bboxes[item_index].width >= page_width * 0.62
                        else "caption-column"
                        for item_index in caption_type_by_item
                    },
                },
                caption_type_by_item=caption_type_by_item,
                columns=spatial_graph_result.columns,
                full_width_by_item={
                    item_index
                    for item_index in body_indices
                    if bboxes[item_index].width >= page_width * 0.62
                }
                | set(artifact_type_by_item),
                confidence_by_item={
                    item_index: _artifact_confidence(artifact_type_by_item[item_index])
                    if item_index in artifact_type_by_item
                    else _caption_confidence(caption_type_by_item[item_index])
                    if item_index in caption_type_by_item
                    else spatial_graph_result.confidence
                    for item_index in range(len(bboxes))
                },
                evidence_by_item={
                    item_index: _artifact_evidence(artifact_type_by_item[item_index])
                    if item_index in artifact_type_by_item
                    else _caption_evidence(
                        caption_type_by_item[item_index],
                        bboxes[item_index].width >= page_width * 0.62,
                    )
                    if item_index in caption_type_by_item
                    else spatial_graph_result.evidence
                    for item_index in range(len(bboxes))
                },
            )
        box_flow_result = _box_flow_order(bboxes, page_width, page_height, body_indices)
        if box_flow_result is not None:
            header_indices = {
                index for index, artifact_type in artifact_type_by_item.items() if artifact_type == "header"
            }
            footer_indices = {
                index for index, artifact_type in artifact_type_by_item.items() if artifact_type == "footer"
            }
            ordered_indices = [
                *sorted(header_indices, key=lambda index: reading_order_key(bboxes[index])),
                *box_flow_result.ordered_indices,
                *sorted(footer_indices, key=lambda index: reading_order_key(bboxes[index])),
            ]
            return _assign_order_metadata(
                ordered_indices,
                bboxes,
                page_width,
                page_height,
                visual_rank,
                strategy=_box_flow_strategy(artifact_type_by_item),
                flow_segment_by_item=_flow_segments_for_order(ordered_indices, bboxes),
                artifact_type_by_item=artifact_type_by_item,
                scope_by_item={item_index: "page-artifact" for item_index in artifact_type_by_item},
                column_span_by_item={
                    **{
                        item_index: _artifact_column_span(artifact_type)
                        for item_index, artifact_type in artifact_type_by_item.items()
                    },
                    **{
                        item_index: "caption-full"
                        if item_index in box_flow_result.full_width_indices
                        else "caption-column"
                        for item_index in caption_type_by_item
                    },
                },
                caption_type_by_item=caption_type_by_item,
                columns=box_flow_result.columns,
                full_width_by_item=box_flow_result.full_width_indices | set(artifact_type_by_item),
                confidence_by_item={
                    item_index: _artifact_confidence(artifact_type_by_item[item_index])
                    if item_index in artifact_type_by_item
                    else _caption_confidence(caption_type_by_item[item_index])
                    if item_index in caption_type_by_item
                    else box_flow_result.confidence
                    for item_index in range(len(bboxes))
                },
                evidence_by_item={
                    item_index: _artifact_evidence(artifact_type_by_item[item_index])
                    if item_index in artifact_type_by_item
                    else _caption_evidence(
                        caption_type_by_item[item_index],
                        item_index in box_flow_result.full_width_indices,
                    )
                    if item_index in caption_type_by_item
                    else box_flow_result.evidence
                    for item_index in range(len(bboxes))
                },
            )
        consensus_result = _successor_consensus_arbitration_order(
            bboxes,
            page_width,
            page_height,
            body_indices,
            base_order=[index for index in visual_indices if index in body_indices],
        )
        if consensus_result is not None:
            header_indices = {
                index for index, artifact_type in artifact_type_by_item.items() if artifact_type == "header"
            }
            footer_indices = {
                index for index, artifact_type in artifact_type_by_item.items() if artifact_type == "footer"
            }
            ordered_indices = [
                *sorted(header_indices, key=lambda index: reading_order_key(bboxes[index])),
                *consensus_result.ordered_indices,
                *sorted(footer_indices, key=lambda index: reading_order_key(bboxes[index])),
            ]
            return _assign_order_metadata(
                ordered_indices,
                bboxes,
                page_width,
                page_height,
                visual_rank,
                strategy=_successor_consensus_arbitration_strategy(artifact_type_by_item),
                flow_segment_by_item=_flow_segments_for_order(ordered_indices, bboxes),
                artifact_type_by_item=artifact_type_by_item,
                scope_by_item={item_index: "page-artifact" for item_index in artifact_type_by_item},
                column_span_by_item={
                    **{
                        item_index: _artifact_column_span(artifact_type)
                        for item_index, artifact_type in artifact_type_by_item.items()
                    },
                    **{
                        item_index: "caption-full"
                        if bboxes[item_index].width >= page_width * 0.62
                        else "caption-column"
                        for item_index in caption_type_by_item
                    },
                },
                caption_type_by_item=caption_type_by_item,
                columns=consensus_result.columns,
                full_width_by_item={
                    item_index
                    for item_index in body_indices
                    if bboxes[item_index].width >= page_width * 0.62
                }
                | set(artifact_type_by_item),
                confidence_by_item={
                    item_index: _artifact_confidence(artifact_type_by_item[item_index])
                    if item_index in artifact_type_by_item
                    else _caption_confidence(caption_type_by_item[item_index])
                    if item_index in caption_type_by_item
                    else consensus_result.confidence
                    for item_index in range(len(bboxes))
                },
                evidence_by_item={
                    item_index: _artifact_evidence(artifact_type_by_item[item_index])
                    if item_index in artifact_type_by_item
                    else _caption_evidence(
                        caption_type_by_item[item_index],
                        bboxes[item_index].width >= page_width * 0.62,
                    )
                    if item_index in caption_type_by_item
                    else consensus_result.evidence
                    for item_index in range(len(bboxes))
                },
            )
        return _visual_assignments(
            visual_indices,
            visual_rank,
            artifact_type_by_item=artifact_type_by_item,
            caption_type_by_item=caption_type_by_item,
            base_confidence=0.74,
            base_evidence=("single-column-visual-order",),
        )

    column_by_item = _assign_columns(bboxes, columns)
    column_confidence, column_evidence = _column_flow_profile(columns, bboxes, body_indices, page_width)
    full_width = {
        item_index
        for item_index, bbox in enumerate(bboxes)
        if item_index in artifact_type_by_item
        or item_index in sidebar_type_by_item
        or item_index in footnote_indices
        or (
            item_index in caption_type_by_item
            and _is_cross_column_caption(bbox, columns, bboxes, page_width)
        )
        or _is_full_width_box(bbox, columns, bboxes, page_width)
    }
    column_span_by_item = {
        item_index: _artifact_column_span(artifact_type)
        for item_index, artifact_type in artifact_type_by_item.items()
    }
    column_span_by_item.update(
        {
            item_index: _sidebar_column_span(sidebar_type)
            for item_index, sidebar_type in sidebar_type_by_item.items()
        }
    )
    column_span_by_item.update({item_index: "footnote" for item_index in footnote_indices})
    column_span_by_item.update(
        {
            item_index: "caption-full" if item_index in full_width else "caption-column"
            for item_index in caption_type_by_item
        }
    )

    ordered_indices: list[int] = []
    flow_segment_by_item: dict[int, int] = {}
    segment_index = 0
    pending_column_items: list[int] = []
    footer_indices = {index for index, artifact_type in artifact_type_by_item.items() if artifact_type == "footer"}
    sidebar_indices = set(sidebar_type_by_item)

    def flush_column_segment() -> None:
        nonlocal segment_index
        if not pending_column_items:
            return
        segment_index += 1
        for item_index in sorted(
            pending_column_items,
            key=lambda index: (column_by_item[index], reading_order_key(bboxes[index])),
        ):
            ordered_indices.append(item_index)
            flow_segment_by_item[item_index] = segment_index
        pending_column_items.clear()

    for item_index in visual_indices:
        if item_index in footer_indices or item_index in sidebar_indices or item_index in footnote_indices:
            continue
        if item_index in full_width:
            flush_column_segment()
            segment_index += 1
            ordered_indices.append(item_index)
            flow_segment_by_item[item_index] = segment_index
        else:
            pending_column_items.append(item_index)
    flush_column_segment()
    for secondary_indices in (footnote_indices, sidebar_indices, footer_indices):
        for item_index in sorted(secondary_indices, key=lambda index: reading_order_key(bboxes[index])):
            segment_index += 1
            ordered_indices.append(item_index)
            flow_segment_by_item[item_index] = segment_index

    return _assign_order_metadata(
        ordered_indices,
        bboxes,
        page_width,
        page_height,
        visual_rank,
        strategy=_column_flow_strategy(artifact_type_by_item, sidebar_type_by_item, footnote_indices),
        flow_segment_by_item=flow_segment_by_item,
        artifact_type_by_item=artifact_type_by_item,
        scope_by_item={
            **{item_index: "page-artifact" for item_index in artifact_type_by_item},
            **{item_index: "sidebar" for item_index in sidebar_type_by_item},
            **{item_index: "footnote" for item_index in footnote_indices},
        },
        sidebar_type_by_item=sidebar_type_by_item,
        caption_type_by_item=caption_type_by_item,
        column_span_by_item=column_span_by_item,
        columns=columns,
        full_width_by_item=full_width,
        confidence_by_item={
            item_index: _artifact_confidence(artifact_type_by_item[item_index])
            if item_index in artifact_type_by_item
            else _sidebar_confidence(sidebar_type_by_item[item_index])
            if item_index in sidebar_type_by_item
            else _footnote_confidence()
            if item_index in footnote_indices
            else _caption_confidence(caption_type_by_item[item_index])
            if item_index in caption_type_by_item
            else column_confidence
            for item_index in range(len(bboxes))
        },
        evidence_by_item={
            item_index: _merge_evidence(
                column_evidence,
                ("full-width-flow-break",)
                if item_index in full_width
                and item_index not in artifact_type_by_item
                and item_index not in sidebar_type_by_item
                else (),
                _artifact_evidence(artifact_type_by_item[item_index]) if item_index in artifact_type_by_item else (),
                _sidebar_evidence(sidebar_type_by_item[item_index]) if item_index in sidebar_type_by_item else (),
                _footnote_evidence() if item_index in footnote_indices else (),
                _caption_evidence(caption_type_by_item[item_index], item_index in full_width)
                if item_index in caption_type_by_item
                else (),
            )
            for item_index in range(len(bboxes))
        },
    )


def _mixed_table_column_flow_assignments(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    visual_indices: list[int],
    visual_rank: dict[int, int],
    table_islands: list[_TableIsland],
    texts: list[str],
) -> list[ReadingOrderAssignment] | None:
    island_by_item = {
        item_index: island
        for island in table_islands
        for item_index in island.indices
    }
    island_kinds = {island.kind for island in table_islands}
    if not island_by_item:
        return None
    if island_kinds == {"table"} and len(island_by_item) / max(len(bboxes), 1) >= 0.75:
        return None

    artifact_type_by_item = _infer_marginal_artifacts(bboxes, page_width, page_height)
    non_artifact_indices = [index for index in range(len(bboxes)) if index not in artifact_type_by_item]
    sidebar_type_by_item = _infer_sidebar_items(bboxes, page_width, page_height, indices=non_artifact_indices)
    non_sidebar_indices = [index for index in non_artifact_indices if index not in sidebar_type_by_item]
    footnote_indices = _infer_footnote_items(bboxes, page_width, page_height, indices=non_sidebar_indices)
    non_table_indices = [
        index
        for index in range(len(bboxes))
        if index not in island_by_item
        and index not in artifact_type_by_item
        and index not in sidebar_type_by_item
        and index not in footnote_indices
    ]
    caption_type_by_item = _infer_caption_items(bboxes, texts, page_width, page_height, indices=non_table_indices)
    columns = _infer_column_clusters(bboxes, page_width, page_height, indices=non_table_indices)
    column_by_item = _assign_columns(bboxes, columns)
    column_confidence, column_evidence = _column_flow_profile(columns, bboxes, non_table_indices, page_width)
    full_width_items = {
        item_index
        for item_index in range(len(bboxes))
        if item_index in artifact_type_by_item
        or item_index in sidebar_type_by_item
        or item_index in footnote_indices
        or (
            item_index in caption_type_by_item
            and _is_cross_column_caption(bboxes[item_index], columns, bboxes, page_width)
        )
        or (item_index in non_table_indices and _is_full_width_box(bboxes[item_index], columns, bboxes, page_width))
    }

    emitted_islands: set[tuple[str, int]] = set()
    pending_tokens: list[_OrderToken] = []
    ordered_indices: list[int] = []
    flow_segment_by_item: dict[int, int] = {}
    column_index_by_item: dict[int, int | None] = {}
    column_span_by_item: dict[int, str] = {}
    region_path_by_item: dict[int, str] = {}
    confidence_by_item: dict[int, float] = {}
    evidence_by_item: dict[int, tuple[str, ...]] = {}
    segment_index = 0
    footer_indices = {index for index, artifact_type in artifact_type_by_item.items() if artifact_type == "footer"}
    sidebar_indices = set(sidebar_type_by_item)

    def emit_token(token: _OrderToken) -> None:
        for item_index in token.indices:
            ordered_indices.append(item_index)
            flow_segment_by_item[item_index] = segment_index
            column_index_by_item[item_index] = token.column_index
            column_span_by_item[item_index] = _column_span_for_token(token, len(columns))
            confidence_by_item[item_index] = token.confidence
            evidence_by_item[item_index] = token.evidence
            if token.region_path:
                region_path_by_item[item_index] = token.region_path

    def flush_column_segment() -> None:
        nonlocal segment_index
        if not pending_tokens:
            return
        segment_index += 1
        for token in sorted(pending_tokens, key=_order_token_sort_key):
            emit_token(token)
        pending_tokens.clear()

    for item_index in visual_indices:
        if item_index in footer_indices or item_index in sidebar_indices or item_index in footnote_indices:
            continue
        island = island_by_item.get(item_index)
        if island is not None:
            island_key = (island.kind, island.island_index)
            if island_key in emitted_islands:
                continue
            emitted_islands.add(island_key)
            table_full_width = _is_full_width_table_island(island.bbox, columns, bboxes, page_width)
            token = _OrderToken(
                kind=island.kind,
                bbox=island.bbox,
                indices=tuple(sorted(island.indices, key=lambda index: reading_order_key(bboxes[index]))),
                column_index=None if table_full_width else column_by_item[item_index],
                full_width=table_full_width,
                region_path=island.region_path,
                confidence=_table_island_confidence(island, bboxes, page_width),
                evidence=_island_evidence(island, full_width=table_full_width),
            )
            if token.full_width:
                flush_column_segment()
                segment_index += 1
                emit_token(token)
            else:
                pending_tokens.append(token)
            continue

        item_full_width = item_index in full_width_items
        artifact_type = artifact_type_by_item.get(item_index)
        sidebar_type = sidebar_type_by_item.get(item_index)
        is_footnote = item_index in footnote_indices
        token = _OrderToken(
            kind="item",
            bbox=bboxes[item_index],
            indices=(item_index,),
            column_index=None if item_full_width else column_by_item[item_index],
            full_width=item_full_width,
            caption_type=caption_type_by_item.get(item_index),
            confidence=_artifact_confidence(artifact_type)
            if artifact_type
            else _sidebar_confidence(sidebar_type)
            if sidebar_type
            else _footnote_confidence()
            if is_footnote
            else _caption_confidence(caption_type_by_item[item_index])
            if item_index in caption_type_by_item
            else column_confidence,
            evidence=_merge_evidence(
                column_evidence,
                ("full-width-flow-break",)
                if item_full_width and not artifact_type and not sidebar_type and not is_footnote
                else (),
                _artifact_evidence(artifact_type) if artifact_type else (),
                _sidebar_evidence(sidebar_type) if sidebar_type else (),
                _footnote_evidence() if is_footnote else (),
                _caption_evidence(caption_type_by_item[item_index], item_full_width)
                if item_index in caption_type_by_item
                else (),
            ),
        )
        if token.full_width:
            flush_column_segment()
            segment_index += 1
            emit_token(token)
        else:
            pending_tokens.append(token)
    flush_column_segment()
    for secondary_indices in (footnote_indices, sidebar_indices, footer_indices):
        for item_index in sorted(secondary_indices, key=lambda index: reading_order_key(bboxes[index])):
            segment_index += 1
            token = _OrderToken(
                kind="item",
                bbox=bboxes[item_index],
                indices=(item_index,),
                column_index=None,
                full_width=True,
                confidence=_artifact_confidence(artifact_type_by_item[item_index])
                if item_index in artifact_type_by_item
                else _sidebar_confidence(sidebar_type_by_item[item_index])
                if item_index in sidebar_type_by_item
                else _footnote_confidence(),
                evidence=_artifact_evidence(artifact_type_by_item[item_index])
                if item_index in artifact_type_by_item
                else _sidebar_evidence(sidebar_type_by_item[item_index])
                if item_index in sidebar_type_by_item
                else _footnote_evidence(),
            )
            emit_token(token)

    return [
        ReadingOrderAssignment(
            item_index=item_index,
            semantic_order=semantic_order,
            visual_order=visual_rank[item_index],
            column_index=None
            if item_index in artifact_type_by_item or item_index in sidebar_type_by_item or item_index in footnote_indices
            else column_index_by_item[item_index],
            column_count=len(columns),
            column_span=_artifact_column_span(artifact_type_by_item[item_index])
            if item_index in artifact_type_by_item
            else _sidebar_column_span(sidebar_type_by_item[item_index])
            if item_index in sidebar_type_by_item
            else "footnote"
            if item_index in footnote_indices
            else column_span_by_item[item_index],
            flow_segment_index=flow_segment_by_item[item_index],
            strategy=_mixed_island_flow_strategy(
                table_islands,
                artifact_type_by_item,
                sidebar_type_by_item,
                footnote_indices,
            ),
            region_path=region_path_by_item.get(item_index),
            artifact_type=artifact_type_by_item.get(item_index),
            scope="page-artifact"
            if item_index in artifact_type_by_item
            else "sidebar"
            if item_index in sidebar_type_by_item
            else "footnote"
            if item_index in footnote_indices
            else "body",
            sidebar_type=sidebar_type_by_item.get(item_index),
            caption_type=caption_type_by_item.get(item_index),
            confidence=confidence_by_item[item_index],
            evidence=evidence_by_item[item_index],
        )
        for semantic_order, item_index in enumerate(ordered_indices, start=1)
    ]


def _order_token_sort_key(token: _OrderToken) -> tuple[int, float, float, str]:
    column_index = token.column_index if token.column_index is not None else 0
    y_key, x_key = reading_order_key(token.bbox)
    return (column_index, y_key, x_key, token.kind)


def _column_span_for_token(token: _OrderToken, column_count: int) -> str:
    if token.kind == "table":
        return "table-full" if token.full_width else "table-column"
    if token.kind == "grid":
        return "grid-full" if token.full_width else "grid-column"
    if token.caption_type:
        return "caption-full" if token.full_width else "caption-column"
    if token.full_width:
        return "full"
    return "single" if column_count == 1 else "column"


def _assign_order_metadata(
    ordered_indices: list[int],
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    visual_rank: dict[int, int],
    strategy: str,
    flow_segment_by_item: dict[int, int] | None = None,
    region_path_by_item: dict[int, str] | None = None,
    artifact_type_by_item: dict[int, str] | None = None,
    scope_by_item: dict[int, str] | None = None,
    sidebar_type_by_item: dict[int, str] | None = None,
    caption_type_by_item: dict[int, str] | None = None,
    column_span_by_item: dict[int, str] | None = None,
    columns: list[list[int]] | None = None,
    confidence_by_item: dict[int, float] | None = None,
    evidence_by_item: dict[int, tuple[str, ...]] | None = None,
    full_width_by_item: set[int] | None = None,
    default_confidence: float = 0.62,
    default_evidence: tuple[str, ...] = ("visual-yx",),
) -> list[ReadingOrderAssignment]:
    artifact_type_by_item = artifact_type_by_item or {}
    scope_by_item = scope_by_item or {}
    sidebar_type_by_item = sidebar_type_by_item or {}
    caption_type_by_item = caption_type_by_item or {}
    column_span_by_item = column_span_by_item or {}
    confidence_by_item = confidence_by_item or {}
    evidence_by_item = evidence_by_item or {}
    columns = columns or _infer_column_clusters(bboxes, page_width, page_height)
    column_count = len(columns)
    column_by_item = _assign_columns(bboxes, columns)
    if full_width_by_item is None:
        full_width = {
            item_index
            for item_index, bbox in enumerate(bboxes)
            if item_index in artifact_type_by_item or _is_full_width_box(bbox, columns, bboxes, page_width)
            or item_index in sidebar_type_by_item
            or scope_by_item.get(item_index) == "footnote"
        }
    else:
        full_width = set(full_width_by_item)
    if flow_segment_by_item is None:
        flow_segment_by_item = _flow_segments_for_order(ordered_indices, bboxes)

    assignments: list[ReadingOrderAssignment] = []
    for semantic_order, item_index in enumerate(ordered_indices, start=1):
        is_full_width = item_index in full_width
        assignments.append(
            ReadingOrderAssignment(
                item_index=item_index,
                semantic_order=semantic_order,
                visual_order=visual_rank[item_index],
                column_index=None
                if is_full_width or item_index in sidebar_type_by_item or scope_by_item.get(item_index) == "footnote"
                else column_by_item[item_index],
                column_count=column_count,
                column_span=column_span_by_item.get(
                    item_index,
                    "full" if is_full_width else "column",
                ),
                flow_segment_index=flow_segment_by_item[item_index],
                strategy=strategy,
                region_path=(region_path_by_item or {}).get(item_index),
                artifact_type=artifact_type_by_item.get(item_index),
                scope=scope_by_item.get(item_index, "body"),
                sidebar_type=sidebar_type_by_item.get(item_index),
                caption_type=caption_type_by_item.get(item_index),
                confidence=confidence_by_item.get(
                    item_index,
                    _artifact_confidence(artifact_type_by_item[item_index])
                    if item_index in artifact_type_by_item
                    else _sidebar_confidence(sidebar_type_by_item[item_index])
                    if item_index in sidebar_type_by_item
                    else _footnote_confidence()
                    if scope_by_item.get(item_index) == "footnote"
                    else _caption_confidence(caption_type_by_item[item_index])
                    if item_index in caption_type_by_item
                    else default_confidence,
                ),
                evidence=_merge_evidence(
                    evidence_by_item.get(item_index, default_evidence),
                    ("full-width-flow-break",)
                    if is_full_width
                    and item_index not in artifact_type_by_item
                    and item_index not in sidebar_type_by_item
                    and scope_by_item.get(item_index) != "footnote"
                    else (),
                    _artifact_evidence(artifact_type_by_item[item_index]) if item_index in artifact_type_by_item else (),
                    _sidebar_evidence(sidebar_type_by_item[item_index]) if item_index in sidebar_type_by_item else (),
                    _footnote_evidence() if scope_by_item.get(item_index) == "footnote" else (),
                    _caption_evidence(caption_type_by_item[item_index], is_full_width)
                    if item_index in caption_type_by_item
                    else (),
                ),
            )
        )
    return assignments


def _recursive_xy_cut_order(bboxes: list[BBox], page_width: float, page_height: float) -> _XyCutResult:
    if len(bboxes) < 2 or _looks_like_table_grid(bboxes, page_width):
        ordered = sorted(range(len(bboxes)), key=lambda index: reading_order_key(bboxes[index]))
        return _XyCutResult(
            ordered_indices=ordered,
            region_path_by_item={index: "root" for index in ordered},
            has_horizontal_split=False,
            has_vertical_split=False,
        )
    return _xy_cut_region(
        list(range(len(bboxes))),
        bboxes,
        page_width,
        page_height,
        depth=0,
        path="root",
    )


def _xy_cut_region(
    indices: list[int],
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    depth: int,
    path: str,
) -> _XyCutResult:
    if depth >= 8 or len(indices) <= 2:
        ordered = sorted(indices, key=lambda index: reading_order_key(bboxes[index]))
        return _XyCutResult(
            ordered_indices=ordered,
            region_path_by_item={index: path for index in ordered},
            has_horizontal_split=False,
            has_vertical_split=False,
        )

    horizontal_split = _find_horizontal_cut(indices, bboxes, page_height)
    if horizontal_split is not None:
        top, bottom = horizontal_split
        top_result = _xy_cut_region(top, bboxes, page_width, page_height, depth + 1, f"{path}/h0")
        bottom_result = _xy_cut_region(bottom, bboxes, page_width, page_height, depth + 1, f"{path}/h1")
        return _merge_xy_results(top_result, bottom_result, split_axis="h")

    vertical_split = _find_vertical_cut(indices, bboxes, page_width)
    if vertical_split is not None:
        left, right = vertical_split
        left_result = _xy_cut_region(left, bboxes, page_width, page_height, depth + 1, f"{path}/v0")
        right_result = _xy_cut_region(right, bboxes, page_width, page_height, depth + 1, f"{path}/v1")
        return _merge_xy_results(left_result, right_result, split_axis="v")

    ordered = sorted(indices, key=lambda index: reading_order_key(bboxes[index]))
    return _XyCutResult(
        ordered_indices=ordered,
        region_path_by_item={index: path for index in ordered},
        has_horizontal_split=False,
        has_vertical_split=False,
    )


def _merge_xy_results(first: _XyCutResult, second: _XyCutResult, split_axis: str) -> _XyCutResult:
    return _XyCutResult(
        ordered_indices=[*first.ordered_indices, *second.ordered_indices],
        region_path_by_item={**first.region_path_by_item, **second.region_path_by_item},
        has_horizontal_split=first.has_horizontal_split or second.has_horizontal_split or split_axis == "h",
        has_vertical_split=first.has_vertical_split or second.has_vertical_split or split_axis == "v",
    )


def _find_horizontal_cut(indices: list[int], bboxes: list[BBox], page_height: float) -> tuple[list[int], list[int]] | None:
    ordered = sorted(indices, key=lambda index: bboxes[index].y0)
    heights = [bboxes[index].height for index in indices if bboxes[index].height > 0]
    min_gap = max(page_height * 0.025, (median(heights) if heights else 10.0) * 1.2)

    best_gap = 0.0
    best_position: int | None = None
    current_bottom = bboxes[ordered[0]].y1
    for position in range(len(ordered) - 1):
        current_bottom = max(current_bottom, bboxes[ordered[position]].y1)
        next_top = bboxes[ordered[position + 1]].y0
        gap = next_top - current_bottom
        if gap > best_gap:
            best_gap = gap
            best_position = position

    if best_position is None or best_gap < min_gap:
        return None
    top = ordered[: best_position + 1]
    bottom = ordered[best_position + 1 :]
    if not top or not bottom:
        return None
    return top, bottom


def _find_vertical_cut(indices: list[int], bboxes: list[BBox], page_width: float) -> tuple[list[int], list[int]] | None:
    if len(indices) < 4:
        return None
    ordered = sorted(indices, key=lambda index: bboxes[index].x0)
    widths = [bboxes[index].width for index in indices if bboxes[index].width > 0]
    min_gap = max(page_width * 0.055, (median(widths) if widths else 20.0) * 0.4)

    best_gap = 0.0
    best_position: int | None = None
    current_right = bboxes[ordered[0]].x1
    for position in range(len(ordered) - 1):
        current_right = max(current_right, bboxes[ordered[position]].x1)
        next_left = bboxes[ordered[position + 1]].x0
        gap = next_left - current_right
        if gap > best_gap:
            best_gap = gap
            best_position = position

    if best_position is None or best_gap < min_gap:
        return None
    left = ordered[: best_position + 1]
    right = ordered[best_position + 1 :]
    if len(left) < 2 or len(right) < 2:
        return None
    if _vertical_overlap_ratio(left, right, bboxes) < 0.2:
        return None
    return left, right


def _flow_segments_for_order(ordered_indices: list[int], bboxes: list[BBox]) -> dict[int, int]:
    if not ordered_indices:
        return {}
    heights = [bboxes[index].height for index in ordered_indices if bboxes[index].height > 0]
    min_gap = max(12.0, (median(heights) if heights else 10.0) * 1.4)
    segments: dict[int, int] = {}
    segment_index = 1
    previous_bottom = bboxes[ordered_indices[0]].y1
    for item_index in ordered_indices:
        bbox = bboxes[item_index]
        if bbox.y0 - previous_bottom > min_gap:
            segment_index += 1
        segments[item_index] = segment_index
        previous_bottom = max(previous_bottom, bbox.y1)
    return segments


def _infer_marginal_artifacts(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
) -> dict[int, str]:
    if len(bboxes) < 6:
        return {}

    top_limit = min(page_height * 0.06, 42.0)
    bottom_limit = max(page_height * 0.92, page_height - 42.0)
    max_height = max(8.0, min(24.0, page_height * 0.035))
    artifacts: dict[int, str] = {}
    for index, bbox in enumerate(bboxes):
        if bbox.height <= 0 or bbox.height > max_height:
            continue
        if bbox.y1 <= top_limit and _looks_like_running_margin_line(bbox, page_width):
            artifacts[index] = "header"
        elif bbox.y0 >= bottom_limit and _looks_like_running_margin_line(bbox, page_width):
            artifacts[index] = "footer"
    return artifacts


def _looks_like_running_margin_line(bbox: BBox, page_width: float) -> bool:
    if bbox.width <= 0:
        return False
    center = _center_x(bbox)
    centered = page_width * 0.28 <= center <= page_width * 0.72
    near_edge = bbox.x0 <= page_width * 0.18 or bbox.x1 >= page_width * 0.82
    compact = bbox.width <= page_width * 0.72
    return compact and (centered or near_edge)


def _artifact_column_span(artifact_type: str) -> str:
    return f"artifact-{artifact_type}"


def _sidebar_column_span(sidebar_type: str) -> str:
    return f"sidebar-{sidebar_type}"


def _artifact_confidence(artifact_type: str | None) -> float:
    return 0.84 if artifact_type else 0.62


def _artifact_evidence(artifact_type: str | None) -> tuple[str, ...]:
    if not artifact_type:
        return ()
    return ("page-edge-artifact", f"{artifact_type}-margin")


def _sidebar_confidence(sidebar_type: str | None) -> float:
    return 0.78 if sidebar_type else 0.62


def _sidebar_evidence(sidebar_type: str | None) -> tuple[str, ...]:
    if not sidebar_type:
        return ()
    return ("sidebar-secondary-flow", "marginalia-outside-print-space", f"{sidebar_type}-sidebar")


def _footnote_confidence() -> float:
    return 0.76


def _footnote_evidence() -> tuple[str, ...]:
    return ("footnote-secondary-flow", "bottom-note-zone")


def _normalize_texts(texts: list[str] | None, expected_count: int) -> list[str]:
    if not texts:
        return [""] * expected_count
    normalized = [str(text or "") for text in texts[:expected_count]]
    if len(normalized) < expected_count:
        normalized.extend("" for _ in range(expected_count - len(normalized)))
    return normalized


def _infer_caption_items(
    bboxes: list[BBox],
    texts: list[str],
    page_width: float,
    page_height: float,
    indices: list[int] | None = None,
) -> dict[int, str]:
    source_indices = list(range(len(bboxes))) if indices is None else list(indices)
    if not source_indices or not texts:
        return {}

    caption_type_by_item: dict[int, str] = {}
    candidate_indices = [
        index
        for index in source_indices
        if 0 <= index < len(texts)
        and _caption_type_for_text(texts[index]) is not None
        and _caption_geometry_is_plausible(bboxes[index], page_width, page_height)
    ]
    for index in candidate_indices:
        caption_type_by_item[index] = _caption_type_for_text(texts[index]) or "figure"

    if not caption_type_by_item:
        return {}

    heights = [bboxes[index].height for index in source_indices if bboxes[index].height > 0]
    median_height = median(heights) if heights else 10.0
    max_gap = max(4.0, median_height * 0.85)
    max_continuations = 2
    ordered_source = sorted(source_indices, key=lambda index: reading_order_key(bboxes[index]))
    used = set(caption_type_by_item)

    for anchor_index in sorted(candidate_indices, key=lambda index: reading_order_key(bboxes[index])):
        anchor_box = bboxes[anchor_index]
        caption_type = caption_type_by_item[anchor_index]
        continuation_count = 0
        current_bottom = anchor_box.y1
        for candidate_index in ordered_source:
            if candidate_index in used or candidate_index == anchor_index:
                continue
            candidate_box = bboxes[candidate_index]
            gap = candidate_box.y0 - current_bottom
            if gap < -median_height * 0.25:
                continue
            if gap > max_gap:
                if candidate_box.y0 > current_bottom:
                    break
                continue
            if not _caption_continuation_geometry(anchor_box, candidate_box, page_width, median_height):
                continue
            caption_type_by_item[candidate_index] = caption_type
            used.add(candidate_index)
            continuation_count += 1
            current_bottom = max(current_bottom, candidate_box.y1)
            if continuation_count >= max_continuations:
                break

    return caption_type_by_item


def _caption_type_for_text(text: str) -> str | None:
    compact = " ".join(text.strip().split()).lower()
    if not compact:
        return None
    if re.match(r"^(fig\.?|figure)\s+([0-9]+|[ivxlcdm]+)[a-z]?\s*([:.\-]|$|\s)", compact):
        return "figure"
    if re.match(r"^(tab\.?|table)\s+([0-9]+|[ivxlcdm]+)[a-z]?\s*([:.\-]|$|\s)", compact):
        return "table"
    if re.match(r"^(alg\.?|algorithm)\s+([0-9]+|[ivxlcdm]+)[a-z]?\s*([:.\-]|$|\s)", compact):
        return "algorithm"
    return None


def _caption_geometry_is_plausible(bbox: BBox, page_width: float, page_height: float) -> bool:
    if bbox.width < max(28.0, page_width * 0.08) or bbox.height <= 0:
        return False
    if bbox.y1 <= min(page_height * 0.06, 42.0):
        return False
    if bbox.y0 >= max(page_height * 0.92, page_height - 42.0):
        return False
    return bbox.height <= max(28.0, page_height * 0.055)


def _caption_continuation_geometry(
    anchor_box: BBox,
    candidate_box: BBox,
    page_width: float,
    median_height: float,
) -> bool:
    if candidate_box.height <= 0 or candidate_box.height > max(28.0, median_height * 1.55):
        return False
    if candidate_box.width < max(24.0, page_width * 0.08):
        return False
    x0_delta = abs(candidate_box.x0 - anchor_box.x0)
    overlap = _horizontal_overlap_ratio(anchor_box, candidate_box)
    return overlap >= 0.5 or x0_delta <= max(14.0, page_width * 0.025)


def _is_cross_column_caption(
    bbox: BBox,
    columns: list[list[int]],
    bboxes: list[BBox],
    page_width: float,
) -> bool:
    if len(columns) < 2:
        return False
    if _is_full_width_box(bbox, columns, bboxes, page_width):
        return True
    center = _center_x(bbox)
    return (
        bbox.width >= page_width * 0.32
        and page_width * 0.25 <= center <= page_width * 0.75
        and bbox.x0 <= max(bboxes[index].x1 for index in columns[0])
        and bbox.x1 >= min(bboxes[index].x0 for index in columns[-1])
    )


def _caption_confidence(caption_type: str | None) -> float:
    return 0.82 if caption_type else 0.62


def _caption_evidence(caption_type: str | None, full_width: bool) -> tuple[str, ...]:
    if not caption_type:
        return ()
    return (
        "caption-label",
        f"{caption_type}-caption",
        "cross-column-caption" if full_width else "column-caption",
        "float-caption",
    )


def _column_flow_strategy(
    artifact_type_by_item: dict[int, str],
    sidebar_type_by_item: dict[int, str],
    footnote_indices: set[int],
) -> str:
    return _qualified_strategy(
        "column-flow-v1",
        artifact_type_by_item=artifact_type_by_item,
        sidebar_type_by_item=sidebar_type_by_item,
        footnote_indices=footnote_indices,
    )


def _mixed_island_flow_strategy(
    islands: list[_TableIsland],
    artifact_type_by_item: dict[int, str],
    sidebar_type_by_item: dict[int, str],
    footnote_indices: set[int],
) -> str:
    kinds = {island.kind for island in islands}
    base_strategy = "mixed-grid-column-flow-v1" if kinds == {"grid"} else "mixed-table-column-flow-v1"
    return _qualified_strategy(
        base_strategy,
        artifact_type_by_item=artifact_type_by_item,
        sidebar_type_by_item=sidebar_type_by_item,
        footnote_indices=footnote_indices,
    )


def _table_row_major_strategy(artifact_type_by_item: dict[int, str]) -> str:
    return _qualified_strategy(
        "table-row-major-v1",
        artifact_type_by_item=artifact_type_by_item,
        sidebar_type_by_item={},
        footnote_indices=set(),
    )


def _spatial_graph_strategy(artifact_type_by_item: dict[int, str]) -> str:
    return _qualified_strategy(
        "spatial-graph-v1",
        artifact_type_by_item=artifact_type_by_item,
        sidebar_type_by_item={},
        footnote_indices=set(),
    )


def _box_flow_strategy(artifact_type_by_item: dict[int, str]) -> str:
    return _qualified_strategy(
        "box-flow-v1",
        artifact_type_by_item=artifact_type_by_item,
        sidebar_type_by_item={},
        footnote_indices=set(),
    )


def _successor_consensus_arbitration_strategy(artifact_type_by_item: dict[int, str]) -> str:
    return _qualified_strategy(
        "successor-consensus-arbitration-v1",
        artifact_type_by_item=artifact_type_by_item,
        sidebar_type_by_item={},
        footnote_indices=set(),
    )


def _qualified_strategy(
    base_strategy: str,
    artifact_type_by_item: dict[int, str],
    sidebar_type_by_item: dict[int, str],
    footnote_indices: set[int],
) -> str:
    qualifiers: list[str] = []
    if artifact_type_by_item:
        qualifiers.append("marginal")
    if sidebar_type_by_item:
        qualifiers.append("sidebar")
    if footnote_indices:
        qualifiers.append("footnote")
    return f"{'-'.join(qualifiers)}-aware-{base_strategy}" if qualifiers else base_strategy


def _column_flow_profile(
    columns: list[list[int]],
    bboxes: list[BBox],
    source_indices: list[int],
    page_width: float,
) -> tuple[float, tuple[str, ...]]:
    if len(columns) < 2:
        return 0.74, ("single-column-visual-order",)

    coverage = sum(len(column) for column in columns) / max(len(source_indices), 1)
    balance = min(len(column) for column in columns) / max(max(len(column) for column in columns), 1)
    overlap = _min_column_vertical_overlap(columns, bboxes)
    separation = _min_column_center_separation(columns, bboxes) / max(page_width, 1.0)
    anchor_strength = min(_column_left_edge_anchor_ratio(column, bboxes, page_width) for column in columns)
    separation_target = 0.16 if len(columns) >= 3 else 0.25

    confidence = (
        0.45
        + 0.16 * min(coverage, 1.0)
        + 0.14 * balance
        + 0.14 * min(overlap, 1.0)
        + 0.14 * anchor_strength
        + 0.12 * min(separation / separation_target, 1.0)
    )

    evidence: list[str] = ["column-flow"]
    if anchor_strength >= 0.35:
        evidence.append("repeated-left-edge")
    else:
        evidence.append("x-cluster-columns")
    if overlap >= 0.2:
        evidence.append("vertical-overlap")
    if balance >= 0.45:
        evidence.append("balanced-columns")
    if separation >= separation_target:
        evidence.append("wide-gutter")
    return _bounded_confidence(min(confidence, 0.96)), tuple(evidence)


def _min_column_vertical_overlap(columns: list[list[int]], bboxes: list[BBox]) -> float:
    if len(columns) < 2:
        return 0.0
    return min(
        _vertical_overlap_ratio(columns[index], columns[index + 1], bboxes)
        for index in range(len(columns) - 1)
    )


def _min_column_center_separation(columns: list[list[int]], bboxes: list[BBox]) -> float:
    if len(columns) < 2:
        return 0.0
    centers = [_cluster_x_center(column, bboxes) for column in columns]
    return min(centers[index + 1] - centers[index] for index in range(len(centers) - 1))


def _column_left_edge_anchor_ratio(column: list[int], bboxes: list[BBox], page_width: float) -> float:
    if not column:
        return 0.0
    clusters = _cluster_positions([bboxes[index].x0 for index in column], tolerance=max(8.0, page_width * 0.02))
    return max((len(cluster) for cluster in clusters), default=0) / len(column)


def _table_island_confidence(island: _TableIsland, bboxes: list[BBox], page_width: float) -> float:
    island_indices = list(island.indices)
    heights = [bboxes[index].height for index in island_indices if bboxes[index].height > 0]
    rows = _cluster_index_rows(island_indices, bboxes, tolerance=max(4.0, (median(heights) if heights else 8.0) * 0.8))
    cells_per_row = [len(row) for row in rows]
    row_score = min(len(rows) / 6.0, 1.0)
    cell_score = min((median(cells_per_row) if cells_per_row else 0.0) / 5.0, 1.0)
    width_score = min(island.bbox.width / max(page_width * 0.42, 1.0), 1.0)
    base = 0.68 if island.kind == "grid" else 0.72
    return _bounded_confidence(base + 0.08 * row_score + 0.08 * cell_score + 0.05 * width_score)


def _island_evidence(island: _TableIsland, *, full_width: bool) -> tuple[str, ...]:
    if island.kind == "grid":
        return _merge_evidence(
            ("grid-island-row-major", "repeated-grid-slots", "local-structure-grid"),
            ("full-width-grid-island",) if full_width else ("column-grid-island",),
        )
    return _merge_evidence(
        ("table-island-row-major", "table-grid-slots"),
        ("full-width-table-island",) if full_width else ("column-table-island",),
    )


def _xy_cut_confidence(result: _XyCutResult) -> float:
    if result.has_horizontal_split and result.has_vertical_split:
        return 0.83
    if result.has_horizontal_split or result.has_vertical_split:
        return 0.7
    return 0.58


def _xy_cut_evidence(result: _XyCutResult) -> tuple[str, ...]:
    evidence: list[str] = ["recursive-xy-cut"]
    if result.has_horizontal_split:
        evidence.append("horizontal-whitespace-cut")
    if result.has_vertical_split:
        evidence.append("vertical-whitespace-cut")
    return tuple(evidence)


def _relation_graph_column_bounds(columns: list[list[int]], bboxes: list[BBox]) -> dict[int, tuple[float, float]]:
    bounds: dict[int, tuple[float, float]] = {}
    for column_index, column in enumerate(columns):
        if not column:
            continue
        bounds[column_index] = (
            min(bboxes[item_index].y0 for item_index in column),
            max(bboxes[item_index].y1 for item_index in column),
        )
    return bounds


def _relation_graph_candidate_edges(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    source_indices: list[int],
    median_height: float,
    column_by_item: dict[int, int],
    column_bounds: dict[int, tuple[float, float]],
) -> list[_RelationGraphEdge]:
    visual_order = sorted(source_indices, key=lambda index: reading_order_key(bboxes[index]))
    visual_successor = {
        item_index: visual_order[position + 1]
        for position, item_index in enumerate(visual_order[:-1])
    }
    edges: list[_RelationGraphEdge] = []
    max_candidates_per_source = 6
    for source_index in source_indices:
        scored: list[_RelationGraphEdge] = []
        for target_index in source_indices:
            if source_index == target_index:
                continue
            score = _relation_graph_edge_score(
                bboxes[source_index],
                bboxes[target_index],
                page_width,
                page_height,
                median_height,
                column_by_item.get(source_index),
                column_by_item.get(target_index),
                column_bounds,
                visual_successor.get(source_index) == target_index,
            )
            if score > 0:
                scored.append(_RelationGraphEdge(source=source_index, target=target_index, score=score))
        edges.extend(
            sorted(
                scored,
                key=lambda edge: (-edge.score, reading_order_key(bboxes[edge.target])),
            )[:max_candidates_per_source]
        )
    return edges


def _relation_graph_edge_score(
    source: BBox,
    target: BBox,
    page_width: float,
    page_height: float,
    median_height: float,
    source_column: int | None,
    target_column: int | None,
    column_bounds: dict[int, tuple[float, float]],
    is_visual_successor: bool,
) -> float:
    vertical_gap = target.y0 - source.y1
    horizontal_overlap = _horizontal_overlap_ratio(source, target)
    center_delta = abs(_center_x(source) - _center_x(target))
    same_column = (
        source_column is not None
        and target_column is not None
        and source_column == target_column
    )
    same_stream = same_column or _spatial_graph_horizontally_related(source, target, page_width)
    max_forward_gap = max(page_height * 0.08, median_height * 7.0)

    if same_stream and -median_height * 0.35 <= vertical_gap <= max_forward_gap:
        gap_score = 1.0 - min(max(vertical_gap, 0.0) / max_forward_gap, 1.0)
        center_score = 1.0 - min(center_delta / max(page_width * 0.28, 1.0), 1.0)
        overlap_score = min(horizontal_overlap, 1.0)
        return _bounded_confidence(
            0.42
            + 0.24 * gap_score
            + 0.18 * overlap_score
            + 0.1 * center_score
            + (0.08 if same_column else 0.0)
        )

    if (
        source_column is not None
        and target_column is not None
        and target_column > source_column
        and target.y0 < source.y0
    ):
        source_top, source_bottom = column_bounds.get(source_column, (0.0, page_height))
        target_top, target_bottom = column_bounds.get(target_column, (0.0, page_height))
        source_height = max(source_bottom - source_top, 1.0)
        target_height = max(target_bottom - target_top, 1.0)
        source_progress = (source.y1 - source_top) / source_height
        target_progress = (target.y0 - target_top) / target_height
        if source_progress >= 0.55 and target_progress <= 0.45:
            horizontal_step = (_center_x(target) - _center_x(source)) / max(page_width, 1.0)
            if horizontal_step > 0.08:
                source_score = min((source_progress - 0.5) / 0.5, 1.0)
                target_score = min((0.5 - target_progress) / 0.5, 1.0)
                step_score = min(horizontal_step / 0.35, 1.0)
                return _bounded_confidence(0.38 + 0.16 * source_score + 0.16 * target_score + 0.12 * step_score)

    if is_visual_successor:
        return 0.28
    return 0.0


def _select_relation_graph_edge(
    edges: list[_RelationGraphEdge],
    successor_by_item: dict[int, int],
    predecessor_by_item: dict[int, int],
) -> _RelationGraphEdge | None:
    feasible = [
        edge
        for edge in edges
        if edge.source not in successor_by_item
        and edge.target not in predecessor_by_item
        and edge.score >= 0.3
        and not _relation_graph_would_cycle(edge.source, edge.target, successor_by_item)
    ]
    if not feasible:
        return None

    outgoing: dict[int, list[_RelationGraphEdge]] = {}
    incoming: dict[int, list[_RelationGraphEdge]] = {}
    for edge in feasible:
        outgoing.setdefault(edge.source, []).append(edge)
        incoming.setdefault(edge.target, []).append(edge)
    for grouped_edges in [*outgoing.values(), *incoming.values()]:
        grouped_edges.sort(key=lambda item: item.score, reverse=True)

    def priority(edge: _RelationGraphEdge) -> tuple[float, float, int, int]:
        source_regret = _relation_graph_regret(edge, outgoing[edge.source])
        target_regret = _relation_graph_regret(edge, incoming[edge.target])
        return (source_regret + target_regret, edge.score, -edge.source, -edge.target)

    return max(feasible, key=priority)


def _relation_graph_regret(edge: _RelationGraphEdge, alternatives: list[_RelationGraphEdge]) -> float:
    for alternative in alternatives:
        if alternative != edge:
            return edge.score - alternative.score
    return edge.score


def _relation_graph_would_cycle(source: int, target: int, successor_by_item: dict[int, int]) -> bool:
    cursor = target
    visited: set[int] = set()
    while cursor in successor_by_item:
        if cursor == source or cursor in visited:
            return True
        visited.add(cursor)
        cursor = successor_by_item[cursor]
    return cursor == source


def _serialize_relation_graph_paths(
    source_indices: list[int],
    bboxes: list[BBox],
    successor_by_item: dict[int, int],
    predecessor_by_item: dict[int, int],
) -> list[int]:
    heads = [item_index for item_index in source_indices if item_index not in predecessor_by_item]
    ordered: list[int] = []
    visited: set[int] = set()
    for head_index in sorted(heads, key=lambda index: reading_order_key(bboxes[index])):
        cursor = head_index
        while cursor not in visited:
            visited.add(cursor)
            ordered.append(cursor)
            if cursor not in successor_by_item:
                break
            cursor = successor_by_item[cursor]
    for item_index in sorted(source_indices, key=lambda index: reading_order_key(bboxes[index])):
        if item_index not in visited:
            ordered.append(item_index)
    return ordered


def _successor_consensus_arbitration_order(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    indices: list[int],
    base_order: list[int],
) -> _SuccessorConsensusArbitrationResult | None:
    source_indices = [
        index
        for index in sorted(indices, key=lambda item_index: reading_order_key(bboxes[item_index]))
        if bboxes[index].width >= 8 and bboxes[index].height >= 4
    ]
    if len(source_indices) < 4 or _looks_like_table_grid([bboxes[index] for index in source_indices], page_width):
        return None

    source_set = set(source_indices)
    candidate_orders = {
        "box_flow": _box_flow_candidate_order_for_indices(
            bboxes,
            page_width=page_width,
            page_height=page_height,
            indices=source_indices,
        ),
        "relation_graph": [
            index
            for index in infer_relation_graph_order(bboxes, page_width=page_width, page_height=page_height)
            if index in source_set
        ],
    }
    candidate_orders = {
        name: order
        for name, order in candidate_orders.items()
        if len(order) >= 2 and len(set(order)) == len(order)
    }
    if len(candidate_orders) < 2:
        return None

    reference_order = [index for index in base_order if index in source_set]
    if len(reference_order) < 2:
        reference_order = source_indices
    diagnostics = successor_consensus_diagnostics(candidate_orders, base_order=reference_order)
    if diagnostics.agreement_level != "high":
        return None

    successor_disagreement = successor_order_disagreement(reference_order, diagnostics.ordered_indices)
    pairwise_disagreement = pairwise_order_disagreement(reference_order, diagnostics.ordered_indices)
    if successor_disagreement.disagreement_ratio < 0.4 or pairwise_disagreement.disagreement_ratio < 0.12:
        return None

    columns = _columns_from_consensus_handoff(diagnostics.ordered_indices, bboxes, page_width)
    if columns is None:
        return None

    multi_column_evidence = ("multi-column-handoff",) if len(columns) > 2 else ()
    evidence = (
        "successor-consensus-arbitration",
        "candidate-successor-consensus",
        "box-flow",
        "relation-graph",
        "column-handoff",
        *multi_column_evidence,
    )
    confidence = _bounded_confidence(
        0.74
        + 0.1 * diagnostics.selected_edge_support_ratio
        + 0.06 * diagnostics.selected_edge_coverage_ratio
        + 0.04 * min(pairwise_disagreement.disagreement_ratio / 0.25, 1.0)
    )
    return _SuccessorConsensusArbitrationResult(
        ordered_indices=diagnostics.ordered_indices,
        columns=columns,
        confidence=confidence,
        evidence=evidence,
    )


def _box_flow_candidate_order_for_indices(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    indices: list[int],
) -> list[int]:
    local_order = infer_box_flow_order(
        [bboxes[index] for index in indices],
        page_width=page_width,
        page_height=page_height,
        boxes_flow=-0.75,
    )
    return [indices[index] for index in local_order]


def _columns_from_consensus_handoff(
    ordered_indices: list[int],
    bboxes: list[BBox],
    page_width: float,
) -> list[list[int]] | None:
    if len(ordered_indices) < 4:
        return None
    heights = [bboxes[index].height for index in ordered_indices if bboxes[index].height > 0]
    median_height = median(heights) if heights else 10.0
    split_positions: list[int] = []
    for position, (source, target) in enumerate(zip(ordered_indices, ordered_indices[1:]), start=1):
        source_box = bboxes[source]
        target_box = bboxes[target]
        upward_jump = source_box.y0 - target_box.y0
        horizontal_jump = _center_x(target_box) - _center_x(source_box)
        if upward_jump < max(6.0, median_height * 0.75):
            continue
        if horizontal_jump < page_width * 0.14:
            continue
        split_positions.append(position)

    if not split_positions or len(split_positions) > 3:
        return None
    boundaries = [0, *split_positions, len(ordered_indices)]
    columns = [
        ordered_indices[boundaries[index] : boundaries[index + 1]]
        for index in range(len(boundaries) - 1)
    ]
    if any(len(column) < 2 for column in columns):
        return None
    centers = [_cluster_x_center(column, bboxes) for column in columns]
    if any(centers[index + 1] - centers[index] < page_width * 0.14 for index in range(len(centers) - 1)):
        return None
    balance = min(len(column) for column in columns) / max(len(column) for column in columns)
    if balance < 0.35:
        return None
    if _min_column_vertical_overlap(columns, bboxes) < 0.2:
        return None
    return columns


def _spatial_graph_order(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    indices: list[int],
) -> _SpatialGraphResult | None:
    source_indices = [
        index
        for index in indices
        if bboxes[index].width >= 8 and bboxes[index].height >= 4
    ]
    if len(source_indices) < 8 or _looks_like_table_grid([bboxes[index] for index in source_indices], page_width):
        return None

    heights = [bboxes[index].height for index in source_indices if bboxes[index].height > 0]
    median_height = median(heights) if heights else 10.0
    max_vertical_gap = max(page_height * 0.08, median_height * 6.0)
    predecessor_by_item: dict[int, int] = {}
    successors_by_item: dict[int, list[int]] = {index: [] for index in source_indices}

    for child_index in sorted(source_indices, key=lambda index: (bboxes[index].y0, bboxes[index].x0)):
        candidates: list[tuple[float, float, float, int]] = []
        child_box = bboxes[child_index]
        for parent_index in source_indices:
            if parent_index == child_index:
                continue
            parent_box = bboxes[parent_index]
            vertical_gap = child_box.y0 - parent_box.y1
            if vertical_gap < -median_height * 0.25 or vertical_gap > max_vertical_gap:
                continue
            if not _spatial_graph_horizontally_related(parent_box, child_box, page_width):
                continue
            overlap = _horizontal_overlap_ratio(parent_box, child_box)
            center_delta = abs(_center_x(parent_box) - _center_x(child_box))
            candidates.append((max(vertical_gap, 0.0), center_delta, -overlap, parent_index))
        if not candidates:
            continue
        _gap, _center_delta, _overlap, parent_index = min(candidates)
        predecessor_by_item[child_index] = parent_index
        successors_by_item[parent_index].append(child_index)

    full_width_indices = {index for index in source_indices if bboxes[index].width >= page_width * 0.62}
    non_full_indices = [index for index in source_indices if index not in full_width_indices]
    if len(non_full_indices) < 6:
        return None

    chain_groups: dict[int, list[int]] = {}
    for item_index in non_full_indices:
        root = item_index
        while predecessor_by_item.get(root) is not None and predecessor_by_item[root] not in full_width_indices:
            root = predecessor_by_item[root]
        chain_groups.setdefault(root, []).append(item_index)

    significant_chains = [
        sorted(chain, key=lambda index: reading_order_key(bboxes[index]))
        for chain in chain_groups.values()
        if len(chain) >= 3
    ]
    if len(significant_chains) < 2:
        return None
    significant_chains = sorted(significant_chains, key=lambda chain: _cluster_x_center(chain, bboxes))

    coverage = sum(len(chain) for chain in significant_chains) / max(len(non_full_indices), 1)
    if coverage < 0.65:
        return None
    if _min_column_center_separation(significant_chains, bboxes) < page_width * 0.14:
        return None
    if _min_column_vertical_overlap(significant_chains, bboxes) < 0.2:
        return None

    row_tolerance = max(4.0, median_height * 0.8)
    heads = [index for index in source_indices if index not in predecessor_by_item]
    ordered_indices: list[int] = []
    visited: set[int] = set()

    def visit(item_index: int) -> None:
        if item_index in visited:
            return
        visited.add(item_index)
        ordered_indices.append(item_index)
        for child_index in sorted(
            successors_by_item.get(item_index, []),
            key=lambda index: _spatial_graph_sort_key(bboxes[index], row_tolerance),
        ):
            visit(child_index)

    for head_index in sorted(heads, key=lambda index: _spatial_graph_sort_key(bboxes[index], row_tolerance)):
        visit(head_index)
    for item_index in sorted(source_indices, key=lambda index: reading_order_key(bboxes[index])):
        visit(item_index)

    remaining_indices = [index for index in indices if index not in visited]
    if remaining_indices:
        ordered_indices.extend(sorted(remaining_indices, key=lambda index: reading_order_key(bboxes[index])))

    confidence = _bounded_confidence(0.76 + 0.08 * min(coverage, 1.0) + 0.06 * min(len(significant_chains) / 3, 1.0))
    evidence = ("spatial-graph", "horizontal-overlap-chain", "multi-head-flow")
    return _SpatialGraphResult(
        ordered_indices=ordered_indices,
        columns=significant_chains,
        confidence=confidence,
        evidence=evidence,
    )


def _box_flow_order(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    indices: list[int],
) -> _BoxFlowResult | None:
    source_indices = [
        index
        for index in indices
        if bboxes[index].width >= 8 and bboxes[index].height >= 4
    ]
    if len(source_indices) < 8 or _looks_like_table_grid([bboxes[index] for index in source_indices], page_width):
        return None

    full_width_indices = {
        index
        for index in source_indices
        if bboxes[index].width >= page_width * 0.62
    }
    ordered_indices: list[int] = []
    selected_columns: list[list[int]] | None = None
    max_disagreement_ratio = 0.0
    accepted_segment_count = 0
    pending_segment: list[int] = []

    def flush_segment() -> None:
        nonlocal selected_columns
        nonlocal max_disagreement_ratio
        nonlocal accepted_segment_count
        if not pending_segment:
            return
        segment = list(pending_segment)
        pending_segment.clear()
        segment_result = _box_flow_segment_order(bboxes, page_width, page_height, segment)
        if segment_result is None:
            ordered_indices.extend(sorted(segment, key=lambda index: reading_order_key(bboxes[index])))
            return
        ordered_segment, segment_columns, disagreement_ratio = segment_result
        ordered_indices.extend(ordered_segment)
        accepted_segment_count += 1
        max_disagreement_ratio = max(max_disagreement_ratio, disagreement_ratio)
        if selected_columns is None or sum(len(column) for column in segment_columns) > sum(
            len(column) for column in selected_columns
        ):
            selected_columns = segment_columns

    for item_index in sorted(indices, key=lambda index: reading_order_key(bboxes[index])):
        if item_index in full_width_indices:
            flush_segment()
            ordered_indices.append(item_index)
        else:
            pending_segment.append(item_index)
    flush_segment()

    if accepted_segment_count == 0 or not selected_columns or len(selected_columns) < 2:
        return None

    confidence = _bounded_confidence(0.7 + 0.12 * min(max_disagreement_ratio / 0.35, 1.0))
    return _BoxFlowResult(
        ordered_indices=ordered_indices,
        columns=selected_columns,
        confidence=confidence,
        evidence=("box-flow", "candidate-order-disagreement", "column-biased-flow"),
        full_width_indices=full_width_indices,
    )


def _box_flow_segment_order(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    segment: list[int],
) -> tuple[list[int], list[list[int]], float] | None:
    source_segment = [
        index
        for index in segment
        if bboxes[index].width >= 8 and bboxes[index].height >= 4 and bboxes[index].width <= page_width * 0.62
    ]
    if len(source_segment) < 8:
        return None
    if _looks_like_table_grid([bboxes[index] for index in source_segment], page_width):
        return None

    visual_order = sorted(source_segment, key=lambda index: reading_order_key(bboxes[index]))
    local_order = infer_box_flow_order(
        [bboxes[index] for index in source_segment],
        page_width=page_width,
        page_height=page_height,
        boxes_flow=-0.75,
    )
    candidate_order = [source_segment[index] for index in local_order]
    disagreement = pairwise_order_disagreement(visual_order, candidate_order)
    if disagreement.disagreement_ratio < 0.12:
        return None

    columns = _box_flow_columns_from_candidate(source_segment, bboxes, page_width)
    if columns is None:
        return None
    if _min_column_vertical_overlap(columns, bboxes) < 0.2:
        return None

    ordered = [
        item_index
        for column in sorted(columns, key=lambda column_indices: _cluster_x_center(column_indices, bboxes))
        for item_index in sorted(column, key=lambda index: reading_order_key(bboxes[index]))
    ]
    ordered_set = set(ordered)
    ordered.extend(
        index
        for index in sorted(segment, key=lambda item: reading_order_key(bboxes[item]))
        if index not in ordered_set
    )
    return ordered, columns, disagreement.disagreement_ratio


def _box_flow_columns_from_candidate(
    indices: list[int],
    bboxes: list[BBox],
    page_width: float,
) -> list[list[int]] | None:
    ordered = sorted(indices, key=lambda index: _center_x(bboxes[index]))
    gaps = [
        (_center_x(bboxes[ordered[position + 1]]) - _center_x(bboxes[ordered[position]]), position)
        for position in range(len(ordered) - 1)
    ]
    if not gaps:
        return None

    best_columns: list[list[int]] | None = None
    best_score = -1.0
    for gap, split_position in gaps:
        if gap < page_width * 0.12:
            continue
        left = ordered[: split_position + 1]
        right = ordered[split_position + 1 :]
        if len(left) < 3 or len(right) < 3:
            continue
        center_separation = _cluster_x_center(right, bboxes) - _cluster_x_center(left, bboxes)
        if center_separation < page_width * 0.18:
            continue
        vertical_overlap = _vertical_overlap_ratio(left, right, bboxes)
        if vertical_overlap < 0.2:
            continue
        balance = min(len(left), len(right)) / max(len(left), len(right))
        score = 3.0 * balance + min(vertical_overlap, 1.0) + center_separation / max(page_width, 1.0)
        if score > best_score:
            best_score = score
            best_columns = [left, right]
    return best_columns


def _spatial_graph_horizontally_related(first: BBox, second: BBox, page_width: float) -> bool:
    overlap = _horizontal_overlap_ratio(first, second)
    center_delta = abs(_center_x(first) - _center_x(second))
    max_width = max(first.width, second.width)
    center_limit = max(24.0, min(max_width * 0.42, page_width * 0.12))
    return center_delta <= center_limit or (overlap >= 0.65 and center_delta <= page_width * 0.16)


def _horizontal_overlap_ratio(first: BBox, second: BBox) -> float:
    overlap = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))
    denominator = max(1.0, min(first.width, second.width))
    return overlap / denominator


def _spatial_graph_sort_key(bbox: BBox, row_tolerance: float) -> tuple[int, float, float]:
    return (round(_center_y(bbox) / max(row_tolerance, 1.0)), bbox.x0, bbox.y0)


def _merge_evidence(*groups: tuple[str, ...]) -> tuple[str, ...]:
    evidence: list[str] = []
    for group in groups:
        for item in group:
            if item and item not in evidence:
                evidence.append(item)
    return tuple(evidence)


def _bounded_confidence(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _infer_footnote_items(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    indices: list[int] | None = None,
) -> set[int]:
    source_indices = list(range(len(bboxes))) if indices is None else list(indices)
    if len(source_indices) < 8:
        return set()

    body_like_heights = [
        bboxes[index].height
        for index in source_indices
        if bboxes[index].height > 0
        and bboxes[index].width >= page_width * 0.18
        and _center_y(bboxes[index]) < page_height * 0.82
    ]
    if len(body_like_heights) < 4:
        return set()

    body_height = median(body_like_heights)
    bottom_start = max(page_height * 0.72, page_height - 220.0)
    bottom_end = max(page_height * 0.92, page_height - 42.0)
    max_note_height = max(5.0, body_height * 0.9)
    max_note_width = page_width * 0.62
    candidates = [
        index
        for index in source_indices
        if bottom_start <= bboxes[index].y0 < bottom_end
        and 2.5 <= bboxes[index].height <= max_note_height
        and 4 <= bboxes[index].width <= max_note_width
    ]
    if not candidates:
        return set()

    rows = _cluster_index_rows(candidates, bboxes, tolerance=max(3.0, body_height * 0.55))
    rows = sorted(rows, key=lambda row: min(bboxes[index].y0 for index in row))
    for row_position, row in enumerate(rows):
        row_top = min(bboxes[index].y0 for index in row)
        previous_bottom = max(
            (bboxes[index].y1 for index in source_indices if index not in candidates and bboxes[index].y1 <= row_top),
            default=0.0,
        )
        if row_top - previous_bottom < max(6.0, body_height * 0.65) and row_top < page_height * 0.82:
            continue
        note_rows = rows[row_position:]
        note_indices = {index for note_row in note_rows for index in note_row}
        if _looks_like_footnote_cluster(note_indices, bboxes, page_width, page_height, body_height):
            return note_indices
    return set()


def _looks_like_footnote_cluster(
    indices: set[int],
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    body_height: float,
) -> bool:
    if not indices:
        return False
    y0, y1 = _cluster_y_extent(list(indices), bboxes)
    if y0 < max(page_height * 0.72, page_height - 220.0):
        return False
    if y1 - y0 > page_height * 0.18:
        return False
    widths = [bboxes[index].width for index in indices if bboxes[index].width > 0]
    heights = [bboxes[index].height for index in indices if bboxes[index].height > 0]
    if not widths or not heights:
        return False
    return median(heights) <= body_height * 0.9 and median(widths) <= page_width * 0.5


def _infer_sidebar_items(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    indices: list[int] | None = None,
) -> dict[int, str]:
    source_indices = list(range(len(bboxes))) if indices is None else list(indices)
    candidates = [
        index
        for index in source_indices
        if bboxes[index].width >= 4
        and bboxes[index].height >= 3
        and page_height * 0.06 <= _center_y(bboxes[index]) <= page_height * 0.92
    ]
    if len(candidates) < 8:
        return {}

    body_candidates = [
        index
        for index in candidates
        if page_width * 0.18 <= bboxes[index].width <= page_width * 0.72
    ]
    if len(body_candidates) < 4:
        return {}

    print_left = min(bboxes[index].x0 for index in body_candidates)
    print_right = max(bboxes[index].x1 for index in body_candidates)
    if print_right - print_left < page_width * 0.32:
        return {}

    gap = max(14.0, page_width * 0.035)
    narrow_limit = max(56.0, page_width * 0.18)
    side_candidates: dict[str, list[int]] = {"left": [], "right": []}
    for index in candidates:
        bbox = bboxes[index]
        if bbox.width > narrow_limit:
            continue
        if bbox.x1 <= print_left - gap and bbox.x1 <= page_width * 0.32:
            side_candidates["left"].append(index)
        elif bbox.x0 >= print_right + gap and bbox.x0 >= page_width * 0.68:
            side_candidates["right"].append(index)

    side_by_item: dict[int, str] = {}
    for side, side_indices in side_candidates.items():
        for cluster in _sidebar_x_clusters(side_indices, bboxes, page_width):
            if not _looks_like_sidebar_cluster(cluster, bboxes, page_width, page_height):
                continue
            for index in cluster:
                side_by_item[index] = side
    return side_by_item


def _sidebar_x_clusters(indices: list[int], bboxes: list[BBox], page_width: float) -> list[list[int]]:
    if not indices:
        return []
    tolerance = max(10.0, page_width * 0.025)
    clusters: list[list[int]] = []
    centers: list[float] = []
    for index in sorted(indices, key=lambda item: bboxes[item].x0):
        x0 = bboxes[index].x0
        if not clusters or abs(x0 - centers[-1]) > tolerance:
            clusters.append([index])
            centers.append(x0)
            continue
        clusters[-1].append(index)
        centers[-1] = sum(bboxes[item].x0 for item in clusters[-1]) / len(clusters[-1])
    return clusters


def _looks_like_sidebar_cluster(
    indices: list[int],
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
) -> bool:
    if len(indices) < 2:
        return False
    widths = [bboxes[index].width for index in indices if bboxes[index].width > 0]
    if not widths or median(widths) > page_width * 0.18:
        return False
    y0, y1 = _cluster_y_extent(indices, bboxes)
    if y1 - y0 < max(page_height * 0.035, 24.0) and len(indices) < 3:
        return False
    return len(indices) <= 18 or (y1 - y0) <= page_height * 0.55


def _infer_column_clusters(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    indices: list[int] | None = None,
) -> list[list[int]]:
    source_indices = list(range(len(bboxes))) if indices is None else list(indices)
    if not source_indices:
        return [list(range(len(bboxes)))]

    candidate_indices = [
        index
        for index in source_indices
        if bboxes[index].width >= 8 and bboxes[index].height >= 4 and bboxes[index].width <= page_width * 0.72
    ]
    if len(candidate_indices) < 6:
        return [source_indices]

    anchored_columns = _infer_repeated_start_columns(candidate_indices, bboxes, page_width)
    if _looks_like_table_grid([bboxes[index] for index in candidate_indices], page_width):
        if (
            len(anchored_columns) >= 2
            and _anchor_coverage(anchored_columns, candidate_indices) >= 0.6
            and _anchored_columns_look_like_text_flows(anchored_columns, bboxes, page_width)
        ):
            return anchored_columns
        return [source_indices]

    if len(anchored_columns) >= 2:
        return anchored_columns

    clusters = _split_column_cluster(candidate_indices, bboxes, page_width, page_height, max_columns=3)
    if len(clusters) < 2:
        return [source_indices]
    return sorted(clusters, key=lambda cluster: _cluster_x_center(cluster, bboxes))


def _infer_table_islands(bboxes: list[BBox], page_width: float, page_height: float) -> list[_TableIsland]:
    candidates = [
        index
        for index, bbox in enumerate(bboxes)
        if bbox.width >= 4 and bbox.height >= 3 and bbox.width <= page_width * 0.55
    ]
    if len(candidates) < 9:
        return []

    heights = [bboxes[index].height for index in candidates if bboxes[index].height > 0]
    y_tolerance = max(4.0, (median(heights) if heights else 8.0) * 0.8)
    rows = _cluster_index_rows(candidates, bboxes, tolerance=y_tolerance)
    tableish_rows = [
        tuple(sorted(row, key=lambda index: bboxes[index].x0))
        for row in rows
        if _row_looks_like_table_cells(row, bboxes, page_width)
    ]
    if len(tableish_rows) < 3:
        return []

    repeated_x_clusters = _table_repeated_x_clusters(tableish_rows, bboxes, page_width)
    if len(repeated_x_clusters) < 3:
        return []

    repeated_slots_by_item = _table_repeated_slot_by_item(repeated_x_clusters, bboxes, page_width)
    eligible_rows = [
        tuple(index for index in row if index in repeated_slots_by_item)
        for row in tableish_rows
        if sum(1 for index in row if index in repeated_slots_by_item) >= 3
        and _row_has_unique_repeated_slots(row, repeated_slots_by_item)
    ]
    if len(eligible_rows) < 3:
        return []

    islands: list[_TableIsland] = []
    consumed: set[int] = set()
    for run in _consecutive_table_row_runs(eligible_rows, bboxes, page_height):
        island_indices = tuple(
            sorted({index for row in run for index in row}, key=lambda index: reading_order_key(bboxes[index]))
        )
        if len(island_indices) < 9 or any(index in consumed for index in island_indices):
            continue
        island_bboxes = [bboxes[index] for index in island_indices]
        if not _looks_like_table_grid(island_bboxes, page_width):
            continue
        if _table_run_looks_like_text_columns(island_indices, repeated_slots_by_item, bboxes, page_width):
            continue
        islands.append(
            _TableIsland(
                island_index=len(islands) + 1,
                indices=island_indices,
                bbox=_union_bbox_for_indices(island_indices, bboxes),
            )
        )
        consumed.update(island_indices)
    return islands


def _infer_local_structure_islands(bboxes: list[BBox], page_width: float, page_height: float) -> list[_TableIsland]:
    table_islands = _infer_table_islands(bboxes, page_width, page_height)
    table_indices = {index for island in table_islands for index in island.indices}
    grid_islands = _infer_grid_islands(
        bboxes,
        page_width,
        page_height,
        excluded_indices=table_indices,
        start_index=1,
    )
    return [*table_islands, *grid_islands]


def _infer_grid_islands(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    *,
    excluded_indices: set[int],
    start_index: int,
) -> list[_TableIsland]:
    candidates = [
        index
        for index, bbox in enumerate(bboxes)
        if index not in excluded_indices
        and bbox.width >= 4
        and bbox.height >= 3
        and bbox.width <= page_width * 0.32
    ]
    if len(candidates) < 6:
        return []

    heights = [bboxes[index].height for index in candidates if bboxes[index].height > 0]
    y_tolerance = max(4.0, (median(heights) if heights else 8.0) * 0.9)
    rows = _cluster_index_rows(candidates, bboxes, tolerance=y_tolerance)
    grid_rows = [
        tuple(sorted(row, key=lambda index: bboxes[index].x0))
        for row in rows
        if _row_looks_like_grid_items(row, bboxes, page_width)
    ]
    if len(grid_rows) < 2:
        return []

    repeated_x_clusters = _grid_repeated_x_clusters(grid_rows, bboxes, page_width)
    if len(repeated_x_clusters) < 3:
        return []

    repeated_slots_by_item = _grid_repeated_slot_by_item(repeated_x_clusters, bboxes, page_width)
    eligible_rows = [
        tuple(index for index in row if index in repeated_slots_by_item)
        for row in grid_rows
        if sum(1 for index in row if index in repeated_slots_by_item) >= 3
        and _row_has_unique_repeated_slots(row, repeated_slots_by_item)
    ]
    if len(eligible_rows) < 2:
        return []

    islands: list[_TableIsland] = []
    consumed: set[int] = set()
    for run in _consecutive_grid_row_runs(eligible_rows, bboxes, page_height):
        island_indices = tuple(
            sorted({index for row in run for index in row}, key=lambda index: reading_order_key(bboxes[index]))
        )
        if len(island_indices) < 6 or any(index in consumed for index in island_indices):
            continue
        if _grid_run_looks_like_body_columns(island_indices, repeated_slots_by_item, bboxes, page_width):
            continue
        islands.append(
            _TableIsland(
                island_index=start_index + len(islands),
                indices=island_indices,
                bbox=_union_bbox_for_indices(island_indices, bboxes),
                kind="grid",
            )
        )
        consumed.update(island_indices)
    return islands


def _row_looks_like_table_cells(row: list[int], bboxes: list[BBox], page_width: float) -> bool:
    if len(row) < 3:
        return False
    row_bboxes = [bboxes[index] for index in row]
    widths = [bbox.width for bbox in row_bboxes if bbox.width > 0]
    if not widths:
        return False
    row_width = max(bbox.x1 for bbox in row_bboxes) - min(bbox.x0 for bbox in row_bboxes)
    if row_width < page_width * 0.22:
        return False
    short_threshold = max(32.0, page_width * 0.14)
    short_ratio = sum(1 for width in widths if width <= short_threshold) / len(widths)
    return short_ratio > 0.5 and median(widths) <= page_width * 0.18


def _row_looks_like_grid_items(row: list[int], bboxes: list[BBox], page_width: float) -> bool:
    if len(row) < 3:
        return False
    row_bboxes = [bboxes[index] for index in row]
    widths = [bbox.width for bbox in row_bboxes if bbox.width > 0]
    if not widths:
        return False
    row_width = max(bbox.x1 for bbox in row_bboxes) - min(bbox.x0 for bbox in row_bboxes)
    if row_width < page_width * 0.28:
        return False
    if median(widths) > page_width * 0.24:
        return False
    return sum(1 for width in widths if width <= page_width * 0.28) / len(widths) >= 0.75


def _table_repeated_x_clusters(
    rows: list[tuple[int, ...]],
    bboxes: list[BBox],
    page_width: float,
) -> list[list[int]]:
    tolerance = max(10.0, page_width * 0.035)
    clusters: list[list[int]] = []
    centers: list[float] = []
    for index in sorted((item for row in rows for item in row), key=lambda item: _center_x(bboxes[item])):
        center = _center_x(bboxes[index])
        if not clusters or abs(center - centers[-1]) > tolerance:
            clusters.append([index])
            centers.append(center)
        else:
            clusters[-1].append(index)
            centers[-1] = sum(_center_x(bboxes[item]) for item in clusters[-1]) / len(clusters[-1])

    return [cluster for cluster in clusters if len(cluster) >= 3]


def _table_repeated_slot_by_item(
    clusters: list[list[int]],
    bboxes: list[BBox],
    page_width: float,
) -> dict[int, int]:
    tolerance = max(10.0, page_width * 0.035)
    slot_by_item: dict[int, int] = {}
    cluster_centers = [
        sum(_center_x(bboxes[index]) for index in cluster) / len(cluster)
        for cluster in clusters
    ]
    for slot_index, (center, cluster) in enumerate(zip(cluster_centers, clusters, strict=True)):
        for item_index in cluster:
            if abs(_center_x(bboxes[item_index]) - center) <= tolerance:
                slot_by_item[item_index] = slot_index
    return slot_by_item


def _grid_repeated_x_clusters(
    rows: list[tuple[int, ...]],
    bboxes: list[BBox],
    page_width: float,
) -> list[list[int]]:
    tolerance = max(12.0, page_width * 0.04)
    clusters: list[list[int]] = []
    centers: list[float] = []
    for index in sorted((item for row in rows for item in row), key=lambda item: bboxes[item].x0):
        x0 = bboxes[index].x0
        if not clusters or abs(x0 - centers[-1]) > tolerance:
            clusters.append([index])
            centers.append(x0)
        else:
            clusters[-1].append(index)
            centers[-1] = sum(bboxes[item].x0 for item in clusters[-1]) / len(clusters[-1])

    return [cluster for cluster in clusters if len(cluster) >= 2]


def _grid_repeated_slot_by_item(
    clusters: list[list[int]],
    bboxes: list[BBox],
    page_width: float,
) -> dict[int, int]:
    tolerance = max(12.0, page_width * 0.04)
    slot_by_item: dict[int, int] = {}
    cluster_starts = [
        sum(bboxes[index].x0 for index in cluster) / len(cluster)
        for cluster in clusters
    ]
    for slot_index, (start, cluster) in enumerate(zip(cluster_starts, clusters, strict=True)):
        for item_index in cluster:
            if abs(bboxes[item_index].x0 - start) <= tolerance:
                slot_by_item[item_index] = slot_index
    return slot_by_item


def _row_has_unique_repeated_slots(row: tuple[int, ...], slot_by_item: dict[int, int]) -> bool:
    slots = [slot_by_item[index] for index in row if index in slot_by_item]
    return len(slots) == len(set(slots))


def _consecutive_table_row_runs(
    rows: list[tuple[int, ...]],
    bboxes: list[BBox],
    page_height: float,
) -> list[list[tuple[int, ...]]]:
    if not rows:
        return []
    heights = [bboxes[index].height for row in rows for index in row if bboxes[index].height > 0]
    max_gap = max(page_height * 0.03, (median(heights) if heights else 8.0) * 2.8)
    ordered_rows = sorted(rows, key=lambda row: min(bboxes[index].y0 for index in row))
    runs: list[list[tuple[int, ...]]] = [[ordered_rows[0]]]
    previous_bottom = max(bboxes[index].y1 for index in ordered_rows[0])
    for row in ordered_rows[1:]:
        row_top = min(bboxes[index].y0 for index in row)
        if row_top - previous_bottom > max_gap:
            runs.append([row])
        else:
            runs[-1].append(row)
        previous_bottom = max(previous_bottom, max(bboxes[index].y1 for index in row))
    return [run for run in runs if len(run) >= 3]


def _consecutive_grid_row_runs(
    rows: list[tuple[int, ...]],
    bboxes: list[BBox],
    page_height: float,
) -> list[list[tuple[int, ...]]]:
    if not rows:
        return []
    heights = [bboxes[index].height for row in rows for index in row if bboxes[index].height > 0]
    max_gap = max(page_height * 0.055, (median(heights) if heights else 8.0) * 4.5)
    ordered_rows = sorted(rows, key=lambda row: min(bboxes[index].y0 for index in row))
    runs: list[list[tuple[int, ...]]] = [[ordered_rows[0]]]
    previous_bottom = max(bboxes[index].y1 for index in ordered_rows[0])
    for row in ordered_rows[1:]:
        row_top = min(bboxes[index].y0 for index in row)
        if row_top - previous_bottom > max_gap:
            runs.append([row])
        else:
            runs[-1].append(row)
        previous_bottom = max(previous_bottom, max(bboxes[index].y1 for index in row))
    return [run for run in runs if len(run) >= 2]


def _table_run_looks_like_text_columns(
    indices: tuple[int, ...],
    slot_by_item: dict[int, int],
    bboxes: list[BBox],
    page_width: float,
) -> bool:
    widths_by_slot: dict[int, list[float]] = {}
    for index in indices:
        slot = slot_by_item.get(index)
        if slot is None:
            continue
        widths_by_slot.setdefault(slot, []).append(bboxes[index].width)
    if len(widths_by_slot) < 3:
        return False
    return all(median(widths) >= page_width * 0.08 for widths in widths_by_slot.values() if widths)


def _grid_run_looks_like_body_columns(
    indices: tuple[int, ...],
    slot_by_item: dict[int, int],
    bboxes: list[BBox],
    page_width: float,
) -> bool:
    widths_by_slot: dict[int, list[float]] = {}
    for index in indices:
        slot = slot_by_item.get(index)
        if slot is None:
            continue
        widths_by_slot.setdefault(slot, []).append(bboxes[index].width)
    if len(widths_by_slot) < 3:
        return True
    broad_slots = sum(1 for widths in widths_by_slot.values() if widths and median(widths) >= page_width * 0.18)
    return broad_slots >= max(2, len(widths_by_slot) - 1)


def _cluster_index_rows(indices: list[int], bboxes: list[BBox], tolerance: float) -> list[list[int]]:
    rows: list[list[int]] = []
    row_centers: list[float] = []
    for index in sorted(indices, key=lambda item: _center_y(bboxes[item])):
        center = _center_y(bboxes[index])
        matched = False
        for row_index, row_center in enumerate(row_centers):
            if abs(center - row_center) <= tolerance:
                rows[row_index].append(index)
                row_centers[row_index] = sum(_center_y(bboxes[item]) for item in rows[row_index]) / len(
                    rows[row_index]
                )
                matched = True
                break
        if not matched:
            rows.append([index])
            row_centers.append(center)
    return rows


def _is_full_width_table_island(
    bbox: BBox,
    columns: list[list[int]],
    bboxes: list[BBox],
    page_width: float,
) -> bool:
    if len(columns) < 2:
        return True
    return bbox.width >= page_width * 0.42 or _is_full_width_box(bbox, columns, bboxes, page_width)


def _infer_repeated_start_columns(
    indices: list[int],
    bboxes: list[BBox],
    page_width: float,
) -> list[list[int]]:
    tolerance = max(12.0, page_width * 0.03)
    start_clusters: list[list[int]] = []
    cluster_centers: list[float] = []
    for index in sorted(indices, key=lambda item: bboxes[item].x0):
        x0 = bboxes[index].x0
        if not start_clusters or abs(x0 - cluster_centers[-1]) > tolerance:
            start_clusters.append([index])
            cluster_centers.append(x0)
        else:
            start_clusters[-1].append(index)
            cluster_centers[-1] = sum(bboxes[item].x0 for item in start_clusters[-1]) / len(start_clusters[-1])

    min_items = max(4, round(len(indices) * 0.12))
    candidates = [
        (cluster_centers[position], cluster)
        for position, cluster in enumerate(start_clusters)
        if len(cluster) >= min_items
    ]
    if len(candidates) < 2:
        return []

    return _select_repeated_start_columns(candidates, bboxes, page_width, total_count=len(indices))


def _select_repeated_start_columns(
    candidates: list[tuple[float, list[int]]],
    bboxes: list[BBox],
    page_width: float,
    total_count: int,
) -> list[list[int]]:
    best_columns: list[list[int]] = []
    best_score = -1.0
    max_columns = min(3, len(candidates))
    for column_count in range(max_columns, 1, -1):
        min_separation = page_width * (0.16 if column_count >= 3 else 0.25)
        min_coverage = 0.48 if column_count >= 3 else 0.45
        for selected in combinations(candidates, column_count):
            centers = [center for center, _cluster in selected]
            clusters = [cluster for _center, cluster in selected]
            if any(centers[index + 1] - centers[index] < min_separation for index in range(len(centers) - 1)):
                continue
            if any(
                _vertical_overlap_ratio(clusters[index], clusters[index + 1], bboxes) < 0.2
                for index in range(len(clusters) - 1)
            ):
                continue
            coverage = sum(len(cluster) for cluster in clusters) / max(total_count, 1)
            if coverage < min_coverage:
                continue
            balance = min(len(cluster) for cluster in clusters) / max(len(cluster) for cluster in clusters)
            spread = (centers[-1] - centers[0]) / max(page_width, 1.0)
            score = coverage * 3 + balance + spread
            if score > best_score:
                best_score = score
                best_columns = clusters
        if best_columns:
            return sorted(best_columns, key=lambda cluster: _cluster_x_center(cluster, bboxes))
    return []


def _anchor_coverage(columns: list[list[int]], indices: list[int]) -> float:
    return sum(len(column) for column in columns) / max(len(indices), 1)


def _anchored_columns_look_like_text_flows(columns: list[list[int]], bboxes: list[BBox], page_width: float) -> bool:
    min_median_width = page_width * 0.08
    for column in columns:
        widths = [bboxes[index].width for index in column if bboxes[index].width > 0]
        if not widths or median(widths) < min_median_width:
            return False
    return True


def _split_column_cluster(
    indices: list[int],
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    max_columns: int,
) -> list[list[int]]:
    if max_columns <= 1 or len(indices) < 6:
        return [indices]

    ordered = sorted(indices, key=lambda index: _center_x(bboxes[index]))
    gaps = [
        (_center_x(bboxes[ordered[position + 1]]) - _center_x(bboxes[ordered[position]]), position)
        for position in range(len(ordered) - 1)
    ]
    if not gaps:
        return [indices]

    widths = [bbox.width for bbox in (bboxes[index] for index in ordered)]
    min_gap = max(page_width * 0.08, median(widths) * 0.75)
    best_gap, split_position = max(gaps, key=lambda item: item[0])
    if best_gap < min_gap:
        return [indices]

    left = ordered[: split_position + 1]
    right = ordered[split_position + 1 :]
    min_items_per_column = 3
    if len(left) < min_items_per_column or len(right) < min_items_per_column:
        return [indices]
    if _vertical_overlap_ratio(left, right, bboxes) < 0.2 and _cluster_height(indices, bboxes) < page_height * 0.25:
        return [indices]

    return [
        *_split_column_cluster(left, bboxes, page_width, page_height, max_columns=max_columns - 1),
        *_split_column_cluster(right, bboxes, page_width, page_height, max_columns=max_columns - 1),
    ]


def _looks_like_table_grid(bboxes: list[BBox], page_width: float) -> bool:
    heights = [bbox.height for bbox in bboxes if bbox.height > 0]
    y_tolerance = max(4.0, median(heights) * 0.8) if heights else 8.0
    rows = _cluster_rows(bboxes, tolerance=y_tolerance)
    if len(rows) < 3:
        return False
    multi_cell_rows = [row for row in rows if len(row) >= 3]
    if len(multi_cell_rows) < 3 or len(multi_cell_rows) / len(rows) < 0.5:
        return False

    repeated_x_clusters = _cluster_positions(
        [_center_x(bbox) for row in multi_cell_rows for bbox in row],
        tolerance=page_width * 0.04,
    )
    return len(repeated_x_clusters) >= 3


def _cluster_rows(bboxes: list[BBox], tolerance: float) -> list[list[BBox]]:
    rows: list[list[BBox]] = []
    row_centers: list[float] = []
    for bbox in sorted(bboxes, key=lambda item: _center_y(item)):
        center = _center_y(bbox)
        matched = False
        for index, row_center in enumerate(row_centers):
            if abs(center - row_center) <= tolerance:
                rows[index].append(bbox)
                row_centers[index] = sum(_center_y(item) for item in rows[index]) / len(rows[index])
                matched = True
                break
        if not matched:
            rows.append([bbox])
            row_centers.append(center)
    return rows


def _assign_columns(bboxes: list[BBox], columns: list[list[int]]) -> dict[int, int]:
    column_centers = [_cluster_x_center(column, bboxes) for column in columns]
    assignments: dict[int, int] = {}
    for index, bbox in enumerate(bboxes):
        assignments[index] = min(
            range(len(column_centers)),
            key=lambda column_index: abs(_center_x(bbox) - column_centers[column_index]),
        )
    return assignments


def _is_full_width_box(bbox: BBox, columns: list[list[int]], bboxes: list[BBox], page_width: float) -> bool:
    if bbox.width >= page_width * 0.62:
        return True
    if len(columns) < 2:
        return False
    first_x1 = max(bboxes[index].x1 for index in columns[0])
    last_x0 = min(bboxes[index].x0 for index in columns[-1])
    return bbox.x0 <= first_x1 and bbox.x1 >= last_x0 and bbox.width >= page_width * 0.42


def _vertical_overlap_ratio(left: list[int], right: list[int], bboxes: list[BBox]) -> float:
    left_y0, left_y1 = _cluster_y_extent(left, bboxes)
    right_y0, right_y1 = _cluster_y_extent(right, bboxes)
    overlap = max(0.0, min(left_y1, right_y1) - max(left_y0, right_y0))
    denominator = max(1.0, min(left_y1 - left_y0, right_y1 - right_y0))
    return overlap / denominator


def _cluster_x_center(indices: list[int], bboxes: list[BBox]) -> float:
    return sum(_center_x(bboxes[index]) for index in indices) / len(indices)


def _cluster_y_extent(indices: list[int], bboxes: list[BBox]) -> tuple[float, float]:
    return (min(bboxes[index].y0 for index in indices), max(bboxes[index].y1 for index in indices))


def _cluster_height(indices: list[int], bboxes: list[BBox]) -> float:
    y0, y1 = _cluster_y_extent(indices, bboxes)
    return y1 - y0


def _union_bbox_for_indices(indices: tuple[int, ...], bboxes: list[BBox]) -> BBox:
    return BBox(
        x0=min(bboxes[index].x0 for index in indices),
        y0=min(bboxes[index].y0 for index in indices),
        x1=max(bboxes[index].x1 for index in indices),
        y1=max(bboxes[index].y1 for index in indices),
    )


def _cluster_positions(values: list[float], tolerance: float) -> list[list[float]]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or abs(value - clusters[-1][-1]) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return clusters


def _center_x(bbox: BBox) -> float:
    return (bbox.x0 + bbox.x1) / 2


def _center_y(bbox: BBox) -> float:
    return (bbox.y0 + bbox.y1) / 2
