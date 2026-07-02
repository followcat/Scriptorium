from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from .geometry import clamp_bbox, pdf_to_px_bbox, reading_order_key
from .models import BBox, DocumentIR, ElementIR, PageIR, RevisionIR
from .pdf_render import RenderedDocument, RenderedPage


def extract_native_pdf_to_ir(rendered: RenderedDocument) -> DocumentIR:
    pages: list[PageIR] = []
    with fitz.open(rendered.source_pdf) as doc:
        for rendered_page in rendered.pages:
            page = doc[rendered_page.page_index]
            elements = _extract_page_text_elements(page, rendered_page)
            pages.append(
                PageIR(
                    page_index=rendered_page.page_index,
                    width_pt=rendered_page.width_pt,
                    height_pt=rendered_page.height_pt,
                    width_px=rendered_page.width_px,
                    height_px=rendered_page.height_px,
                    render_dpi=rendered_page.render_dpi,
                    scale_x=rendered_page.scale_x,
                    scale_y=rendered_page.scale_y,
                    background_image=str(rendered_page.background_image),
                    elements=elements,
                )
            )

    return DocumentIR(
        source_pdf=str(rendered.source_pdf),
        render_dpi=rendered.render_dpi,
        page_count=len(rendered.pages),
        pages=pages,
        revisions=[RevisionIR(reason="native-pdf-extraction", payload={"source": "pymupdf-text-dict"})],
        metadata={"extraction_mode": "native"},
    )


def _extract_page_text_elements(page: fitz.Page, rendered_page: RenderedPage) -> list[ElementIR]:
    text_dict = page.get_text("dict")
    raw_lines: list[dict[str, Any]] = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue
            bbox = BBox.from_any(line.get("bbox"))
            raw_lines.append({"bbox": bbox, "text": text, "spans": spans})

    raw_lines.sort(key=lambda item: reading_order_key(item["bbox"]))
    elements: list[ElementIR] = []
    for order, raw in enumerate(raw_lines, start=1):
        bbox_pdf = clamp_bbox(raw["bbox"], rendered_page.width_pt, rendered_page.height_pt)
        bbox_px = pdf_to_px_bbox(bbox_pdf, rendered_page.scale_x, rendered_page.scale_y)
        style = _style_from_spans(raw["spans"], bbox_pdf)
        elements.append(
            ElementIR(
                id=f"p{rendered_page.page_index + 1:04d}-n{order:04d}",
                page_index=rendered_page.page_index,
                type="title" if style["font_size_px"] >= 22 else "text",
                bbox_pdf=bbox_pdf,
                bbox_px=bbox_px,
                source_text=raw["text"],
                confidence=1.0,
                reading_order=order,
                style_hint=style,
                metadata={"source": "native-pdf", "span_count": len(raw["spans"])},
            )
        )
    elements.extend(_extract_page_shape_elements(page, rendered_page, start_order=len(elements) + 1))
    return elements


def _extract_page_shape_elements(page: fitz.Page, rendered_page: RenderedPage, start_order: int) -> list[ElementIR]:
    shapes: list[ElementIR] = []
    for offset, drawing in enumerate(page.get_drawings(), start=0):
        width_pt = float(drawing.get("width") or 0)
        shape_geometry = _drawing_geometry(drawing)
        bbox_pdf = _drawing_bbox(drawing, rendered_page, width_pt)
        if bbox_pdf is None:
            continue
        if bbox_pdf.width <= 0 or bbox_pdf.height <= 0:
            continue
        bbox_px = pdf_to_px_bbox(bbox_pdf, rendered_page.scale_x, rendered_page.scale_y)
        fill = _rgb_to_css(drawing.get("fill"))
        stroke = _rgb_to_css(drawing.get("color"))
        if not fill and not stroke:
            continue
        if shape_geometry in {"horizontal-line", "vertical-line"}:
            fill_color = stroke or fill or "transparent"
            stroke_color = "transparent"
            border_width_pt = 0
            border_width_px = 0
        else:
            fill_color = fill or "transparent"
            stroke_color = stroke or "transparent"
            border_width_pt = round(width_pt, 3) if width_pt else 0
            border_width_px = round(width_pt * rendered_page.scale_x, 3) if width_pt else 0
        shapes.append(
            ElementIR(
                id=f"p{rendered_page.page_index + 1:04d}-s{offset + 1:04d}",
                page_index=rendered_page.page_index,
                type="shape",
                bbox_pdf=bbox_pdf,
                bbox_px=bbox_px,
                source_text="",
                confidence=1.0,
                reading_order=start_order + offset,
                style_hint={
                    "fill_color": fill_color,
                    "stroke_color": stroke_color,
                    "border_width_pt": border_width_pt,
                    "border_width_px": border_width_px,
                    "line_height": 1,
                    "font_size_px": 1,
                    "font_family": "Arial, sans-serif",
                },
                visibility=True,
                metadata={
                    "source": "native-drawing",
                    "drawing_type": drawing.get("type"),
                    "seqno": drawing.get("seqno"),
                    "shape_geometry": shape_geometry,
                    "drawing_item_count": len(drawing.get("items") or []),
                },
            )
        )
    return shapes


def _drawing_bbox(drawing: dict[str, Any], rendered_page: RenderedPage, width_pt: float) -> BBox | None:
    rect = drawing.get("rect")
    if rect is None:
        rect = _rect_from_drawing_items(drawing.get("items") or [])
    if rect is None:
        return None

    x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    stroke_width = max(width_pt, 0.5)
    if x1 == x0:
        x0 -= stroke_width / 2
        x1 += stroke_width / 2
    if y1 == y0:
        y0 -= stroke_width / 2
        y1 += stroke_width / 2
    return clamp_bbox(BBox.from_any([x0, y0, x1, y1]), rendered_page.width_pt, rendered_page.height_pt)


def _rect_from_drawing_items(items: list[Any]) -> fitz.Rect | None:
    points: list[fitz.Point] = []
    for item in items:
        operator = item[0] if item else None
        if operator == "l" and len(item) >= 3:
            points.extend([item[1], item[2]])
        elif operator == "re" and len(item) >= 2:
            rect = item[1]
            points.extend([fitz.Point(rect.x0, rect.y0), fitz.Point(rect.x1, rect.y1)])
        elif operator in {"c", "qu"}:
            points.extend(point for point in item[1:] if isinstance(point, fitz.Point))
    if not points:
        return None
    x0 = min(point.x for point in points)
    y0 = min(point.y for point in points)
    x1 = max(point.x for point in points)
    y1 = max(point.y for point in points)
    return fitz.Rect(x0, y0, x1, y1)


def _drawing_geometry(drawing: dict[str, Any]) -> str:
    items = drawing.get("items") or []
    if len(items) == 1:
        item = items[0]
        operator = item[0] if item else None
        if operator == "l" and len(item) >= 3:
            start, end = item[1], item[2]
            if abs(start.y - end.y) <= 0.01:
                return "horizontal-line"
            if abs(start.x - end.x) <= 0.01:
                return "vertical-line"
            return "line"
        if operator == "re":
            return "rectangle"

    rect = drawing.get("rect")
    if rect is not None:
        width = abs(float(rect.x1) - float(rect.x0))
        height = abs(float(rect.y1) - float(rect.y0))
        if height <= 0.01 and width > 0:
            return "horizontal-line"
        if width <= 0.01 and height > 0:
            return "vertical-line"
    return "path"


def _style_from_spans(spans: list[dict[str, Any]], bbox: BBox) -> dict[str, Any]:
    if not spans:
        return {"font_size_px": round(max(7.0, bbox.height * 0.72), 2), "line_height": 1.15, "font_family": "serif"}
    first = spans[0]
    size = float(first.get("size", max(7.0, bbox.height * 0.72)))
    font = str(first.get("font", "serif"))
    flags = int(first.get("flags", 0))
    return {
        "font_size_px": round(size * 96.0 / 72.0, 2),
        "font_size_pt": round(size, 2),
        "line_height": 1.12,
        "font_family": _css_font_family(font),
        "font_weight": 700 if flags & 16 else 400,
        "font_style": "italic" if flags & 2 else "normal",
        "text_color": _int_color_to_css(first.get("color")) or "rgb(17, 32, 42)",
        "font_name": font,
        "bold": bool(flags & 16),
        "italic": bool(flags & 2),
    }


def _css_font_family(pdf_font: str) -> str:
    normalized = pdf_font.lower()
    if "arial" in normalized or "helvetica" in normalized:
        return "Arial, sans-serif"
    if "times" in normalized or "serif" in normalized:
        return "Times New Roman, serif"
    if "courier" in normalized or "mono" in normalized:
        return "Courier New, monospace"
    return "Arial, sans-serif"


def _rgb_to_css(value: Any) -> str | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    red = max(0, min(255, round(float(value[0]) * 255)))
    green = max(0, min(255, round(float(value[1]) * 255)))
    blue = max(0, min(255, round(float(value[2]) * 255)))
    return f"rgb({red}, {green}, {blue})"


def _int_color_to_css(value: Any) -> str | None:
    if not isinstance(value, int):
        return None
    red = (value >> 16) & 255
    green = (value >> 8) & 255
    blue = value & 255
    return f"rgb({red}, {green}, {blue})"
