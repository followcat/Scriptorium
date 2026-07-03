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

    def as_metadata(self) -> dict[str, object]:
        return {
            "semantic_order": self.semantic_order,
            "visual_order": self.visual_order,
            "column_index": self.column_index,
            "column_count": self.column_count,
            "column_span": self.column_span,
            "flow_segment_index": self.flow_segment_index,
            "reading_order_strategy": self.strategy,
            "reading_order_region_path": self.region_path,
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
        )

    return _column_flow_assignments(bboxes, page_width, page_height, visual_indices, visual_rank)


def _visual_assignments(
    visual_indices: list[int],
    visual_rank: dict[int, int],
) -> list[ReadingOrderAssignment]:
    return [
        ReadingOrderAssignment(
            item_index=item_index,
            semantic_order=order,
            visual_order=visual_rank[item_index],
            column_index=0,
            column_count=1,
            column_span="single",
            flow_segment_index=1,
            strategy="visual-yx",
        )
        for order, item_index in enumerate(visual_indices, start=1)
    ]


def _column_flow_assignments(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
    visual_indices: list[int],
    visual_rank: dict[int, int],
) -> list[ReadingOrderAssignment]:
    columns = _infer_column_clusters(bboxes, page_width, page_height)
    column_count = len(columns)
    if column_count < 2:
        return _visual_assignments(visual_indices, visual_rank)

    column_by_item = _assign_columns(bboxes, columns)
    full_width = {
        item_index
        for item_index, bbox in enumerate(bboxes)
        if _is_full_width_box(bbox, columns, bboxes, page_width)
    }

    ordered_indices: list[int] = []
    flow_segment_by_item: dict[int, int] = {}
    segment_index = 0
    pending_column_items: list[int] = []

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
        if item_index in full_width:
            flush_column_segment()
            segment_index += 1
            ordered_indices.append(item_index)
            flow_segment_by_item[item_index] = segment_index
        else:
            pending_column_items.append(item_index)
    flush_column_segment()

    return _assign_order_metadata(
        ordered_indices,
        bboxes,
        page_width,
        page_height,
        visual_rank,
        strategy="column-flow-v1",
        flow_segment_by_item=flow_segment_by_item,
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

    non_table_indices = [index for index in range(len(bboxes)) if index not in table_by_item]
    columns = _infer_column_clusters(bboxes, page_width, page_height, indices=non_table_indices)
    column_by_item = _assign_columns(bboxes, columns)
    full_width_items = {
        item_index
        for item_index in non_table_indices
        if _is_full_width_box(bboxes[item_index], columns, bboxes, page_width)
    }

    emitted_islands: set[int] = set()
    pending_tokens: list[_OrderToken] = []
    ordered_indices: list[int] = []
    flow_segment_by_item: dict[int, int] = {}
    column_index_by_item: dict[int, int | None] = {}
    column_span_by_item: dict[int, str] = {}
    region_path_by_item: dict[int, str] = {}
    segment_index = 0

    def emit_token(token: _OrderToken) -> None:
        for item_index in token.indices:
            ordered_indices.append(item_index)
            flow_segment_by_item[item_index] = segment_index
            column_index_by_item[item_index] = token.column_index
            column_span_by_item[item_index] = _column_span_for_token(token, len(columns))
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
            )
            if token.full_width:
                flush_column_segment()
                segment_index += 1
                emit_token(token)
            else:
                pending_tokens.append(token)
            continue

        item_full_width = item_index in full_width_items
        token = _OrderToken(
            kind="item",
            bbox=bboxes[item_index],
            indices=(item_index,),
            column_index=None if item_full_width else column_by_item[item_index],
            full_width=item_full_width,
        )
        if token.full_width:
            flush_column_segment()
            segment_index += 1
            emit_token(token)
        else:
            pending_tokens.append(token)
    flush_column_segment()

    return [
        ReadingOrderAssignment(
            item_index=item_index,
            semantic_order=semantic_order,
            visual_order=visual_rank[item_index],
            column_index=column_index_by_item[item_index],
            column_count=len(columns),
            column_span=column_span_by_item[item_index],
            flow_segment_index=flow_segment_by_item[item_index],
            strategy="mixed-table-column-flow-v1",
            region_path=region_path_by_item.get(item_index),
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
) -> list[ReadingOrderAssignment]:
    columns = _infer_column_clusters(bboxes, page_width, page_height)
    column_count = len(columns)
    column_by_item = _assign_columns(bboxes, columns)
    full_width = {
        item_index
        for item_index, bbox in enumerate(bboxes)
        if _is_full_width_box(bbox, columns, bboxes, page_width)
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
                column_index=None if is_full_width else column_by_item[item_index],
                column_count=column_count,
                column_span="full" if is_full_width else "column",
                flow_segment_index=flow_segment_by_item[item_index],
                strategy=strategy,
                region_path=(region_path_by_item or {}).get(item_index),
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
        return [list(range(len(bboxes)))]

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
