from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .geometry import reading_order_key
from .models import BBox


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

    def as_metadata(self) -> dict[str, object]:
        return {
            "semantic_order": self.semantic_order,
            "visual_order": self.visual_order,
            "column_index": self.column_index,
            "column_count": self.column_count,
            "column_span": self.column_span,
            "flow_segment_index": self.flow_segment_index,
            "reading_order_strategy": self.strategy,
        }


def infer_semantic_reading_order(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
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
    columns = _infer_column_clusters(bboxes, page_width, page_height)
    column_count = len(columns)

    if column_count < 2:
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
                strategy="column-flow-v1",
            )
        )
    return assignments


def _infer_column_clusters(bboxes: list[BBox], page_width: float, page_height: float) -> list[list[int]]:
    candidate_indices = [
        index
        for index, bbox in enumerate(bboxes)
        if bbox.width >= 8 and bbox.height >= 4 and bbox.width <= page_width * 0.72
    ]
    if len(candidate_indices) < 6:
        return [list(range(len(bboxes)))]

    if _looks_like_table_grid([bboxes[index] for index in candidate_indices], page_width):
        return [list(range(len(bboxes)))]

    clusters = _split_column_cluster(candidate_indices, bboxes, page_width, page_height, max_columns=3)
    if len(clusters) < 2:
        return [list(range(len(bboxes)))]
    return sorted(clusters, key=lambda cluster: _cluster_x_center(cluster, bboxes))


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
    x_cluster_count = len(_cluster_positions([_center_x(bbox) for bbox in bboxes], tolerance=page_width * 0.04))
    if x_cluster_count < 3:
        return False

    heights = [bbox.height for bbox in bboxes if bbox.height > 0]
    y_tolerance = max(4.0, median(heights) * 0.8) if heights else 8.0
    rows = _cluster_rows(bboxes, tolerance=y_tolerance)
    if len(rows) < 3:
        return False
    multi_cell_rows = [row for row in rows if len(row) >= 2]
    return len(multi_cell_rows) >= 3 and len(multi_cell_rows) / len(rows) >= 0.5


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
