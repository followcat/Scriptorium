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
        rect = drawing.get("rect")
        if rect is None:
            continue
        bbox_pdf = clamp_bbox(BBox.from_any([rect.x0, rect.y0, rect.x1, rect.y1]), rendered_page.width_pt, rendered_page.height_pt)
        if bbox_pdf.width <= 0 or bbox_pdf.height <= 0:
            continue
        bbox_px = pdf_to_px_bbox(bbox_pdf, rendered_page.scale_x, rendered_page.scale_y)
        width_pt = float(drawing.get("width") or 0)
        fill = _rgb_to_css(drawing.get("fill"))
        stroke = _rgb_to_css(drawing.get("color"))
        if not fill and not stroke:
            continue
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
                    "fill_color": fill or "transparent",
                    "stroke_color": stroke or "transparent",
                    "border_width_pt": round(width_pt, 3) if width_pt else 0,
                    "border_width_px": round(width_pt * rendered_page.scale_x, 3) if width_pt else 0,
                    "line_height": 1,
                    "font_size_px": 1,
                    "font_family": "Arial, sans-serif",
                },
                visibility=True,
                metadata={
                    "source": "native-drawing",
                    "drawing_type": drawing.get("type"),
                    "seqno": drawing.get("seqno"),
                },
            )
        )
    return shapes


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
