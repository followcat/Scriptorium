from __future__ import annotations

from statistics import median

from .geometry import bbox_intersects_page
from .models import BBox, DocumentIR, ElementIR, PageIR


def annotate_document(document: DocumentIR) -> DocumentIR:
    """Infer semantic/style annotations from extraction evidence."""

    style_registry: dict[str, dict[str, object]] = {}
    for page in document.pages:
        _annotate_page(page, style_registry)

    document.metadata["annotation_version"] = "v1"
    document.metadata["styles"] = style_registry
    return document


def _annotate_page(page: PageIR, style_registry: dict[str, dict[str, object]]) -> None:
    text_elements = [element for element in page.elements if element.source_text.strip()]
    shape_elements = [element for element in page.elements if element.type == "shape"]
    median_font = _median_font_size(text_elements)
    table_regions = _infer_table_regions(shape_elements)

    for element in page.elements:
        source_kind = str(element.metadata.get("source", "unknown"))
        style_id = _style_id_for(element, style_registry)
        role = _infer_role(element, median_font, table_regions)
        layout_group_id = _layout_group_for(element, table_regions)
        annotation = {
            "role": role,
            "source_kind": source_kind,
            "style_id": style_id,
            "layout_group_id": layout_group_id,
            "editable": bool(element.source_text.strip()),
            "edit_target": "edited_text" if element.source_text.strip() else None,
            "bbox_pdf": element.bbox_pdf.as_list(),
            "bbox_px": element.bbox_px.as_list(),
            "reading_order": element.reading_order,
        }
        element.metadata["annotation"] = annotation
        element.metadata["role"] = role
        element.metadata["style_id"] = style_id
        if layout_group_id:
            element.metadata["layout_group_id"] = layout_group_id


def _median_font_size(elements: list[ElementIR]) -> float:
    sizes = [float(element.style_hint.get("font_size_px", 0)) for element in elements]
    sizes = [size for size in sizes if size > 0]
    return median(sizes) if sizes else 12.0


def _style_id_for(element: ElementIR, style_registry: dict[str, dict[str, object]]) -> str:
    keys = (
        "font_family",
        "font_size_px",
        "font_weight",
        "font_style",
        "text_color",
        "fill_color",
        "stroke_color",
        "border_width_pt",
    )
    style = {key: element.style_hint.get(key) for key in keys if key in element.style_hint}
    signature = "|".join(f"{key}={style.get(key)}" for key in sorted(style))
    for style_id, existing in style_registry.items():
        if existing.get("signature") == signature:
            return style_id
    style_id = f"style-{len(style_registry) + 1:03d}"
    style_registry[style_id] = {"signature": signature, **style}
    return style_id


def _infer_table_regions(shape_elements: list[ElementIR]) -> list[dict[str, object]]:
    if len(shape_elements) < 4:
        return []

    x0 = min(element.bbox_pdf.x0 for element in shape_elements)
    y0 = min(element.bbox_pdf.y0 for element in shape_elements)
    x1 = max(element.bbox_pdf.x1 for element in shape_elements)
    y1 = max(element.bbox_pdf.y1 for element in shape_elements)
    return [
        {
            "id": "table-001",
            "bbox": BBox(x0=x0, y0=y0, x1=x1, y1=y1),
            "shape_count": len(shape_elements),
        }
    ]


def _infer_role(element: ElementIR, median_font: float, table_regions: list[dict[str, object]]) -> str:
    if element.type == "shape":
        return "table-shape" if _layout_group_for(element, table_regions) else "graphic-shape"

    if _layout_group_for(element, table_regions):
        return "table-cell-text"

    font_size = float(element.style_hint.get("font_size_px", 0))
    font_weight = int(element.style_hint.get("font_weight", 400) or 400)
    if element.type == "title" or font_size >= max(20.0, median_font * 1.5):
        return "heading"
    if font_weight >= 700 and font_size >= median_font:
        return "emphasized-text"
    return "paragraph"


def _layout_group_for(element: ElementIR, table_regions: list[dict[str, object]]) -> str | None:
    for region in table_regions:
        bbox = region["bbox"]
        if isinstance(bbox, BBox) and _bbox_contains_or_intersects(bbox, element.bbox_pdf):
            return str(region["id"])
    return None


def _bbox_contains_or_intersects(container: BBox, bbox: BBox) -> bool:
    if (
        bbox.x0 >= container.x0
        and bbox.y0 >= container.y0
        and bbox.x1 <= container.x1
        and bbox.y1 <= container.y1
    ):
        return True
    return bbox_intersects_page(
        BBox(
            x0=bbox.x0 - container.x0,
            y0=bbox.y0 - container.y0,
            x1=bbox.x1 - container.x0,
            y1=bbox.y1 - container.y0,
        ),
        container.width,
        container.height,
    )
