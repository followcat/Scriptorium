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


def _infer_column_clusters(bboxes: list[BBox], page_width: float, page_height: float) -> list[list[int]]:
    candidate_indices = [
        index
        for index, bbox in enumerate(bboxes)
        if bbox.width >= 8 and bbox.height >= 4 and bbox.width <= page_width * 0.72
    ]
    if len(candidate_indices) < 6:
        return [list(range(len(bboxes)))]

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
        return [list(range(len(bboxes)))]
    return sorted(clusters, key=lambda cluster: _cluster_x_center(cluster, bboxes))


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
