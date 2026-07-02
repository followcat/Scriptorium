from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .models import BBox, DocumentIR, ElementIR, PageIR


STYLE_KEYS = (
    "font_family",
    "font_size_px",
    "font_size_pt",
    "font_weight",
    "font_style",
    "text_color",
    "fill_color",
    "stroke_color",
    "border_width_pt",
    "vertical_align",
)


@dataclass(frozen=True)
class LayoutRegion:
    id: str
    kind: str
    bbox: BBox
    shape_ids: tuple[str, ...]
    confidence: float

    @property
    def shape_count(self) -> int:
        return len(self.shape_ids)

    def as_metadata(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "bbox_pdf": self.bbox.as_list(),
            "shape_count": self.shape_count,
            "shape_ids": list(self.shape_ids),
            "confidence": self.confidence,
        }


def annotate_document(document: DocumentIR) -> DocumentIR:
    """Infer semantic/style annotations from extraction evidence."""

    style_registry: dict[str, dict[str, object]] = {}
    layout_regions_by_page: list[dict[str, object]] = []
    for page in document.pages:
        layout_regions = _annotate_page(page, style_registry)
        layout_regions_by_page.append(
            {
                "page_index": page.page_index,
                "regions": [region.as_metadata() for region in layout_regions],
            }
        )

    document.metadata["annotation_version"] = "v2"
    document.metadata["styles"] = style_registry
    document.metadata["layout_regions"] = layout_regions_by_page
    return document


def _annotate_page(page: PageIR, style_registry: dict[str, dict[str, object]]) -> list[LayoutRegion]:
    text_elements = [element for element in page.elements if element.source_text.strip()]
    shape_elements = [element for element in page.elements if element.type == "shape"]
    median_font = _median_font_size(text_elements)
    layout_regions = _infer_layout_regions(shape_elements)

    for element in page.elements:
        source_kind = str(element.metadata.get("source", "unknown"))
        style_id = _style_id_for(element, style_registry)
        _annotate_text_runs(element, style_registry)
        layout_region = _layout_region_for(element, layout_regions)
        layout_group_id = layout_region.id if layout_region else None
        layout_group_kind = layout_region.kind if layout_region else None
        role = _infer_role(element, median_font, layout_region)
        annotation = {
            "role": role,
            "source_kind": source_kind,
            "style_id": style_id,
            "layout_group_id": layout_group_id,
            "layout_group_kind": layout_group_kind,
            "layout_group_bbox_pdf": layout_region.bbox.as_list() if layout_region else None,
            "layout_group_confidence": layout_region.confidence if layout_region else None,
            "text_run_count": int(element.metadata.get("text_run_count") or 0),
            "mixed_inline_style": bool(element.metadata.get("mixed_inline_style")),
            "editable": bool(element.source_text.strip()),
            "edit_target": "edited_text" if element.source_text.strip() else None,
            "bbox_pdf": element.bbox_pdf.as_list(),
            "bbox_px": element.bbox_px.as_list(),
            "reading_order": element.reading_order,
        }
        element.metadata["annotation"] = annotation
        element.metadata["role"] = role
        element.metadata["style_id"] = style_id
        element.metadata.pop("layout_group_id", None)
        element.metadata.pop("layout_group_kind", None)
        if layout_group_id:
            element.metadata["layout_group_id"] = layout_group_id
            element.metadata["layout_group_kind"] = layout_group_kind
    return layout_regions


def _median_font_size(elements: list[ElementIR]) -> float:
    sizes = [float(element.style_hint.get("font_size_px", 0)) for element in elements]
    sizes = [size for size in sizes if size > 0]
    return median(sizes) if sizes else 12.0


def _style_id_for(element: ElementIR, style_registry: dict[str, dict[str, object]]) -> str:
    return _style_id_for_hint(element.style_hint, style_registry)


def _style_id_for_hint(style_hint: dict[str, object], style_registry: dict[str, dict[str, object]]) -> str:
    style = {key: style_hint.get(key) for key in STYLE_KEYS if key in style_hint}
    signature = "|".join(f"{key}={style.get(key)}" for key in sorted(style))
    for style_id, existing in style_registry.items():
        if existing.get("signature") == signature:
            return style_id
    style_id = f"style-{len(style_registry) + 1:03d}"
    style_registry[style_id] = {"signature": signature, **style}
    return style_id


def _annotate_text_runs(element: ElementIR, style_registry: dict[str, dict[str, object]]) -> None:
    runs = element.metadata.get("text_runs")
    if not isinstance(runs, list):
        return
    for run in runs:
        if not isinstance(run, dict):
            continue
        style = run.get("style")
        if not isinstance(style, dict):
            continue
        run["style_id"] = _style_id_for_hint(style, style_registry)


def _infer_layout_regions(shape_elements: list[ElementIR]) -> list[LayoutRegion]:
    clusters = _cluster_shapes(shape_elements)
    counters: dict[str, int] = {}
    regions: list[LayoutRegion] = []
    for cluster in sorted(clusters, key=lambda items: (_union_bbox(items).y0, _union_bbox(items).x0)):
        classified = _classify_shape_cluster(cluster)
        if classified is None:
            continue
        kind, confidence = classified
        counters[kind] = counters.get(kind, 0) + 1
        region_id = f"{kind}-{counters[kind]:03d}"
        regions.append(
            LayoutRegion(
                id=region_id,
                kind=kind,
                bbox=_union_bbox(cluster),
                shape_ids=tuple(element.id for element in cluster),
                confidence=confidence,
            )
        )
    return regions


def _infer_role(element: ElementIR, median_font: float, layout_region: LayoutRegion | None) -> str:
    if element.type == "shape":
        if layout_region:
            return f"{layout_region.kind}-shape"
        return "graphic-shape"

    if layout_region and layout_region.kind == "table":
        return "table-cell-text"

    font_size = float(element.style_hint.get("font_size_px", 0))
    font_weight = int(element.style_hint.get("font_weight", 400) or 400)
    if element.type == "title" or font_size >= max(20.0, median_font * 1.5):
        return "heading"
    if font_weight >= 700 and font_size >= median_font:
        return "emphasized-text"
    return "paragraph"


def _layout_region_for(element: ElementIR, layout_regions: list[LayoutRegion]) -> LayoutRegion | None:
    for region in layout_regions:
        if element.type == "shape":
            if element.id in region.shape_ids:
                return region
            continue
        if region.kind == "table" and _bbox_center_inside(region.bbox, element.bbox_pdf, tolerance=2.0):
            return region
    return None


def _cluster_shapes(shape_elements: list[ElementIR]) -> list[list[ElementIR]]:
    if not shape_elements:
        return []

    parent = list(range(len(shape_elements)))
    tolerance = _shape_cluster_tolerance(shape_elements)

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(shape_elements)):
        for right in range(left + 1, len(shape_elements)):
            if _bboxes_close(shape_elements[left].bbox_pdf, shape_elements[right].bbox_pdf, tolerance):
                union(left, right)

    grouped: dict[int, list[ElementIR]] = {}
    for index, element in enumerate(shape_elements):
        grouped.setdefault(find(index), []).append(element)
    return list(grouped.values())


def _shape_cluster_tolerance(shape_elements: list[ElementIR]) -> float:
    widths = [float(element.style_hint.get("border_width_pt", 0) or 0) for element in shape_elements]
    widths = [width for width in widths if width > 0]
    if not widths:
        return 3.0
    return max(3.0, min(8.0, median(widths) * 4))


def _bboxes_close(left: BBox, right: BBox, tolerance: float) -> bool:
    horizontal_gap = max(0.0, max(left.x0, right.x0) - min(left.x1, right.x1))
    vertical_gap = max(0.0, max(left.y0, right.y0) - min(left.y1, right.y1))
    return horizontal_gap <= tolerance and vertical_gap <= tolerance


def _classify_shape_cluster(cluster: list[ElementIR]) -> tuple[str, float] | None:
    if _is_separator_cluster(cluster):
        return ("separator", 0.9)
    if _is_table_cluster(cluster):
        return ("table", 0.86)
    if _is_figure_cluster(cluster):
        confidence = 0.76 if len(cluster) == 1 else 0.82
        return ("figure", confidence)
    return None


def _is_separator_cluster(cluster: list[ElementIR]) -> bool:
    if len(cluster) != 1:
        return False
    element = cluster[0]
    geometry = _shape_geometry(element)
    if geometry not in {"horizontal-line", "vertical-line"}:
        return False
    bbox = element.bbox_pdf
    long_side = max(bbox.width, bbox.height)
    short_side = max(0.1, min(bbox.width, bbox.height))
    return long_side >= 24 and long_side / short_side >= 8


def _is_table_cluster(cluster: list[ElementIR]) -> bool:
    if len(cluster) < 4:
        return False

    horizontal_lines = [element for element in cluster if _shape_geometry(element) == "horizontal-line"]
    vertical_lines = [element for element in cluster if _shape_geometry(element) == "vertical-line"]
    rectangles = [element for element in cluster if _shape_geometry(element) == "rectangle"]

    if len(horizontal_lines) >= 2 and len(vertical_lines) >= 2:
        return True

    if len(rectangles) >= 4:
        x_positions = _unique_positions([element.bbox_pdf.x0 for element in rectangles])
        y_positions = _unique_positions([element.bbox_pdf.y0 for element in rectangles])
        if len(x_positions) >= 2 and len(y_positions) >= 2:
            return True

    return False


def _is_figure_cluster(cluster: list[ElementIR]) -> bool:
    bbox = _union_bbox(cluster)
    if bbox.width < 24 or bbox.height < 24:
        return False
    if len(cluster) == 1 and _shape_geometry(cluster[0]) in {"horizontal-line", "vertical-line"}:
        return False
    return True


def _shape_geometry(element: ElementIR) -> str:
    geometry = str(element.metadata.get("shape_geometry") or "")
    if geometry:
        return geometry
    bbox = element.bbox_pdf
    short_side = max(0.1, min(bbox.width, bbox.height))
    long_side = max(bbox.width, bbox.height)
    if long_side / short_side >= 12:
        return "horizontal-line" if bbox.width >= bbox.height else "vertical-line"
    return "rectangle"


def _union_bbox(elements: list[ElementIR]) -> BBox:
    return BBox(
        x0=min(element.bbox_pdf.x0 for element in elements),
        y0=min(element.bbox_pdf.y0 for element in elements),
        x1=max(element.bbox_pdf.x1 for element in elements),
        y1=max(element.bbox_pdf.y1 for element in elements),
    )


def _unique_positions(values: list[float], tolerance: float = 2.0) -> list[float]:
    unique: list[float] = []
    for value in sorted(values):
        if not unique or abs(value - unique[-1]) > tolerance:
            unique.append(value)
    return unique


def _bbox_center_inside(container: BBox, bbox: BBox, tolerance: float = 0.0) -> bool:
    center_x = (bbox.x0 + bbox.x1) / 2
    center_y = (bbox.y0 + bbox.y1) / 2
    return (
        container.x0 - tolerance <= center_x <= container.x1 + tolerance
        and container.y0 - tolerance <= center_y <= container.y1 + tolerance
    )
