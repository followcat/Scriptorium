from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Literal
from statistics import median

from .geometry import reading_order_key
from .models import BBox

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
    confidence: float = 0.5
    evidence: tuple[str, ...] = ()

    def as_metadata(self) -> dict[str, object]:
        scope = self.scope
        if self.artifact_type and scope == "body":
            scope = "page-artifact"
        elif self.sidebar_type and scope == "body":
            scope = "sidebar"
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
            "reading_order_confidence": _bounded_confidence(self.confidence),
            "reading_order_evidence": list(self.evidence),
            "reading_order_evidence_summary": ",".join(self.evidence),
        }


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

    @property
    def region_path(self) -> str:
        return f"root/table-island-{self.island_index:03d}"


@dataclass(frozen=True)
class _OrderToken:
    kind: str
    bbox: BBox
    indices: tuple[int, ...]
    column_index: int | None
    full_width: bool
    region_path: str | None = None
    confidence: float = 0.5
    evidence: tuple[str, ...] = ()


def infer_semantic_reading_order(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    strategy: ReadingOrderStrategy = "auto",
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
        return _visual_assignments(visual_indices, visual_rank)

    table_islands = _infer_table_islands(bboxes, page_width, page_height)
    if strategy in {"auto", "column-flow-v1"} and table_islands:
        mixed_table_assignments = _mixed_table_column_flow_assignments(
            bboxes,
            page_width,
            page_height,
            visual_indices,
            visual_rank,
            table_islands,
        )
        if mixed_table_assignments is not None:
            return mixed_table_assignments

    xy_result = _recursive_xy_cut_order(bboxes, page_width, page_height)
    if strategy == "recursive-xy-cut-v1" or (
        strategy == "auto" and xy_result.has_horizontal_split and xy_result.has_vertical_split
    ):
        return _assign_order_metadata(
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

    return _column_flow_assignments(bboxes, page_width, page_height, visual_indices, visual_rank)


def _visual_assignments(
    visual_indices: list[int],
    visual_rank: dict[int, int],
    artifact_type_by_item: dict[int, str] | None = None,
    strategy: str = "visual-yx",
    base_confidence: float = 0.62,
    base_evidence: tuple[str, ...] = ("visual-yx",),
) -> list[ReadingOrderAssignment]:
    artifact_type_by_item = artifact_type_by_item or {}
    assignments: list[ReadingOrderAssignment] = []
    for order, item_index in enumerate(visual_indices, start=1):
        artifact_type = artifact_type_by_item.get(item_index)
        assignments.append(
            ReadingOrderAssignment(
                item_index=item_index,
                semantic_order=order,
                visual_order=visual_rank[item_index],
                column_index=None if artifact_type else 0,
                column_count=1,
                column_span=_artifact_column_span(artifact_type) if artifact_type else "single",
                flow_segment_index=1,
                strategy=strategy,
                artifact_type=artifact_type,
                confidence=_artifact_confidence(artifact_type) if artifact_type else base_confidence,
                evidence=_merge_evidence(
                    base_evidence,
                    _artifact_evidence(artifact_type) if artifact_type else (),
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
) -> list[ReadingOrderAssignment]:
    artifact_type_by_item = _infer_marginal_artifacts(bboxes, page_width, page_height)
    non_artifact_indices = [index for index in range(len(bboxes)) if index not in artifact_type_by_item]
    sidebar_type_by_item = _infer_sidebar_items(bboxes, page_width, page_height, indices=non_artifact_indices)
    non_sidebar_indices = [index for index in non_artifact_indices if index not in sidebar_type_by_item]
    footnote_indices = _infer_footnote_items(bboxes, page_width, page_height, indices=non_sidebar_indices)
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
                strategy=_table_row_major_strategy(artifact_type_by_item),
                base_confidence=0.82,
                base_evidence=("table-row-major", "table-grid-slots"),
            )
        return _visual_assignments(
            visual_indices,
            visual_rank,
            artifact_type_by_item=artifact_type_by_item,
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
        column_span_by_item=column_span_by_item,
        columns=columns,
        confidence_by_item={
            item_index: _artifact_confidence(artifact_type_by_item[item_index])
            if item_index in artifact_type_by_item
            else _sidebar_confidence(sidebar_type_by_item[item_index])
            if item_index in sidebar_type_by_item
            else _footnote_confidence()
            if item_index in footnote_indices
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
) -> list[ReadingOrderAssignment] | None:
    table_by_item = {
        item_index: island
        for island in table_islands
        for item_index in island.indices
    }
    if not table_by_item or len(table_by_item) / max(len(bboxes), 1) >= 0.75:
        return None

    artifact_type_by_item = _infer_marginal_artifacts(bboxes, page_width, page_height)
    non_artifact_indices = [index for index in range(len(bboxes)) if index not in artifact_type_by_item]
    sidebar_type_by_item = _infer_sidebar_items(bboxes, page_width, page_height, indices=non_artifact_indices)
    non_sidebar_indices = [index for index in non_artifact_indices if index not in sidebar_type_by_item]
    footnote_indices = _infer_footnote_items(bboxes, page_width, page_height, indices=non_sidebar_indices)
    non_table_indices = [
        index
        for index in range(len(bboxes))
        if index not in table_by_item
        and index not in artifact_type_by_item
        and index not in sidebar_type_by_item
        and index not in footnote_indices
    ]
    columns = _infer_column_clusters(bboxes, page_width, page_height, indices=non_table_indices)
    column_by_item = _assign_columns(bboxes, columns)
    column_confidence, column_evidence = _column_flow_profile(columns, bboxes, non_table_indices, page_width)
    full_width_items = {
        item_index
        for item_index in range(len(bboxes))
        if item_index in artifact_type_by_item
        or item_index in sidebar_type_by_item
        or item_index in footnote_indices
        or (item_index in non_table_indices and _is_full_width_box(bboxes[item_index], columns, bboxes, page_width))
    }

    emitted_islands: set[int] = set()
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
        island = table_by_item.get(item_index)
        if island is not None:
            if island.island_index in emitted_islands:
                continue
            emitted_islands.add(island.island_index)
            table_full_width = _is_full_width_table_island(island.bbox, columns, bboxes, page_width)
            token = _OrderToken(
                kind="table",
                bbox=island.bbox,
                indices=tuple(sorted(island.indices, key=lambda index: reading_order_key(bboxes[index]))),
                column_index=None if table_full_width else column_by_item[item_index],
                full_width=table_full_width,
                region_path=island.region_path,
                confidence=_table_island_confidence(island, bboxes, page_width),
                evidence=_merge_evidence(
                    ("table-island-row-major", "table-grid-slots"),
                    ("full-width-table-island",) if table_full_width else ("column-table-island",),
                ),
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
            confidence=_artifact_confidence(artifact_type)
            if artifact_type
            else _sidebar_confidence(sidebar_type)
            if sidebar_type
            else _footnote_confidence()
            if is_footnote
            else column_confidence,
            evidence=_merge_evidence(
                column_evidence,
                ("full-width-flow-break",)
                if item_full_width and not artifact_type and not sidebar_type and not is_footnote
                else (),
                _artifact_evidence(artifact_type) if artifact_type else (),
                _sidebar_evidence(sidebar_type) if sidebar_type else (),
                _footnote_evidence() if is_footnote else (),
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
            strategy=_mixed_table_flow_strategy(artifact_type_by_item, sidebar_type_by_item, footnote_indices),
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
    column_span_by_item: dict[int, str] | None = None,
    columns: list[list[int]] | None = None,
    confidence_by_item: dict[int, float] | None = None,
    evidence_by_item: dict[int, tuple[str, ...]] | None = None,
    default_confidence: float = 0.62,
    default_evidence: tuple[str, ...] = ("visual-yx",),
) -> list[ReadingOrderAssignment]:
    artifact_type_by_item = artifact_type_by_item or {}
    scope_by_item = scope_by_item or {}
    sidebar_type_by_item = sidebar_type_by_item or {}
    column_span_by_item = column_span_by_item or {}
    confidence_by_item = confidence_by_item or {}
    evidence_by_item = evidence_by_item or {}
    columns = columns or _infer_column_clusters(bboxes, page_width, page_height)
    column_count = len(columns)
    column_by_item = _assign_columns(bboxes, columns)
    full_width = {
        item_index
        for item_index, bbox in enumerate(bboxes)
        if item_index in artifact_type_by_item or _is_full_width_box(bbox, columns, bboxes, page_width)
        or item_index in sidebar_type_by_item
        or scope_by_item.get(item_index) == "footnote"
    }
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
                confidence=confidence_by_item.get(
                    item_index,
                    _artifact_confidence(artifact_type_by_item[item_index])
                    if item_index in artifact_type_by_item
                    else _sidebar_confidence(sidebar_type_by_item[item_index])
                    if item_index in sidebar_type_by_item
                    else _footnote_confidence()
                    if scope_by_item.get(item_index) == "footnote"
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


def _mixed_table_flow_strategy(
    artifact_type_by_item: dict[int, str],
    sidebar_type_by_item: dict[int, str],
    footnote_indices: set[int],
) -> str:
    return _qualified_strategy(
        "mixed-table-column-flow-v1",
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
    return _bounded_confidence(0.72 + 0.08 * row_score + 0.08 * cell_score + 0.05 * width_score)


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
