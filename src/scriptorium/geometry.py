from __future__ import annotations

from .models import BBox


def px_to_pdf_bbox(bbox: BBox, scale_x: float, scale_y: float) -> BBox:
    return BBox(
        x0=bbox.x0 / scale_x,
        y0=bbox.y0 / scale_y,
        x1=bbox.x1 / scale_x,
        y1=bbox.y1 / scale_y,
    )


def pdf_to_px_bbox(bbox: BBox, scale_x: float, scale_y: float) -> BBox:
    return BBox(
        x0=bbox.x0 * scale_x,
        y0=bbox.y0 * scale_y,
        x1=bbox.x1 * scale_x,
        y1=bbox.y1 * scale_y,
    )


def clamp_bbox(bbox: BBox, width: float, height: float) -> BBox:
    x0 = min(max(0.0, bbox.x0), width)
    y0 = min(max(0.0, bbox.y0), height)
    x1 = min(max(0.0, bbox.x1), width)
    y1 = min(max(0.0, bbox.y1), height)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def bbox_intersects_page(bbox: BBox, width: float, height: float) -> bool:
    return bbox.x1 > 0 and bbox.y1 > 0 and bbox.x0 < width and bbox.y0 < height


def reading_order_key(bbox: BBox) -> tuple[float, float]:
    # Group lines loosely by top edge, then scan left to right.
    return (round(bbox.y0 / 12.0) * 12.0, bbox.x0)
