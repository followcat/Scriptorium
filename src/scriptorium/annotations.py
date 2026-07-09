from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .models import BBox, DocumentIR, ElementIR, PageIR
from .reading_streams import assign_reading_streams_to_metadata


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

STRUCTURE_ROLE_MAP = {
    "abstract": "abstract",
    "algorithm": "algorithm",
    "card": "card-text",
    "card_grid": "card-text",
    "chart": "chart-text",
    "content_card": "card-text",
    "content_grid": "card-text",
    "doc_title": "heading",
    "document_title": "heading",
    "figure": "figure-text",
    "figure_caption": "caption",
    "figure_table_title": "caption",
    "figure_title": "caption",
    "footer": "footer",
    "footnote": "footnote",
    "formula": "formula",
    "formula_number": "formula-number",
    "grid": "card-text",
    "grid_area": "card-text",
    "grid_block": "card-text",
    "header": "running-header",
    "list": "list-item",
    "menu_grid": "card-text",
    "nav_grid": "card-text",
    "number": "page-number",
    "page_number": "page-number",
    "paragraph": "paragraph",
    "paragraph_title": "heading",
    "product": "card-text",
    "product_card": "card-text",
    "product_grid": "card-text",
    "references": "references",
    "seal": "seal-text",
    "sidebar_text": "sidebar-text",
    "table": "table-cell-text",
    "table_caption": "caption",
    "text": "paragraph",
    "tile": "card-text",
    "tile_grid": "card-text",
    "title": "heading",
}


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


@dataclass(frozen=True)
class _CaptionTarget:
    id: str
    kind: str
    bbox: BBox
    source: str
    element: ElementIR | None = None


@dataclass(frozen=True)
class _CaptionTargetMatch:
    target: _CaptionTarget
    position: str
    distance_pt: float
    confidence: float


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
    for element in text_elements:
        element.metadata.setdefault("semantic_order", element.reading_order)
        element.metadata.setdefault("visual_order", element.reading_order)
    assign_reading_streams_to_metadata(
        (element.metadata for element in text_elements),
        order_key=lambda metadata: (
            int(metadata.get("semantic_order") or 1_000_000),
            int(metadata.get("visual_order") or 1_000_000),
        ),
    )

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
            "semantic_order": int(element.metadata.get("semantic_order") or element.reading_order),
            "visual_order": int(element.metadata.get("visual_order") or element.reading_order),
            "column_index": element.metadata.get("column_index"),
            "column_count": int(element.metadata.get("column_count") or 1),
            "column_span": element.metadata.get("column_span"),
            "flow_segment_index": int(element.metadata.get("flow_segment_index") or 1),
            "reading_order_strategy": element.metadata.get("reading_order_strategy", "visual-yx"),
            "reading_order_region_path": element.metadata.get("reading_order_region_path"),
            "reading_order_scope": element.metadata.get("reading_order_scope", "body"),
            "reading_order_artifact_type": element.metadata.get("reading_order_artifact_type"),
            "reading_order_sidebar_type": element.metadata.get("reading_order_sidebar_type"),
            "reading_order_caption_type": element.metadata.get("reading_order_caption_type"),
            "reading_order_stream_id": element.metadata.get("reading_order_stream_id"),
            "reading_order_stream_type": element.metadata.get("reading_order_stream_type"),
            "reading_order_stream_index": element.metadata.get("reading_order_stream_index"),
            "reading_order_confidence": float(element.metadata.get("reading_order_confidence") or 0.0),
            "reading_order_evidence": _reading_order_evidence(element),
            "reading_order_evidence_summary": element.metadata.get("reading_order_evidence_summary", ""),
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
    _annotate_caption_targets(page, layout_regions)
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
    if element.type == "image":
        return "image"

    if element.type == "shape":
        if layout_region:
            return f"{layout_region.kind}-shape"
        return "graphic-shape"

    if layout_region and layout_region.kind == "table":
        return "table-cell-text"

    structure_role = _external_structure_role(element)
    if structure_role:
        return structure_role

    artifact_role = _reading_order_artifact_role(element)
    if artifact_role:
        return artifact_role
    footnote_role = _reading_order_footnote_role(element)
    if footnote_role:
        return footnote_role
    caption_role = _reading_order_caption_role(element)
    if caption_role:
        return caption_role
    sidebar_role = _reading_order_sidebar_role(element)
    if sidebar_role:
        return sidebar_role

    font_size = float(element.style_hint.get("font_size_px", 0))
    font_weight = int(element.style_hint.get("font_weight", 400) or 400)
    if element.type == "title" or font_size >= max(20.0, median_font * 1.5):
        return "heading"
    if font_weight >= 700 and font_size >= median_font:
        return "emphasized-text"
    return "paragraph"


def _reading_order_artifact_role(element: ElementIR) -> str | None:
    artifact_type = str(element.metadata.get("reading_order_artifact_type") or "").strip()
    if artifact_type == "header":
        return "running-header"
    if artifact_type == "footer":
        return "footer"
    return None


def _reading_order_sidebar_role(element: ElementIR) -> str | None:
    if str(element.metadata.get("reading_order_scope") or "").strip() == "sidebar":
        return "sidebar-text"
    if str(element.metadata.get("reading_order_sidebar_type") or "").strip():
        return "sidebar-text"
    return None


def _reading_order_footnote_role(element: ElementIR) -> str | None:
    if str(element.metadata.get("reading_order_scope") or "").strip() == "footnote":
        return "footnote"
    return None


def _reading_order_caption_role(element: ElementIR) -> str | None:
    if str(element.metadata.get("reading_order_caption_type") or "").strip():
        return "caption"
    return None


def _reading_order_evidence(element: ElementIR) -> list[str]:
    evidence = element.metadata.get("reading_order_evidence")
    if not isinstance(evidence, list):
        return []
    return [str(item) for item in evidence if str(item).strip()]


def _annotate_caption_targets(page: PageIR, layout_regions: list[LayoutRegion]) -> None:
    targets = _caption_targets_for_page(page, layout_regions)
    if not targets:
        return
    captions = [
        element
        for element in page.elements
        if element.source_text.strip() and str(element.metadata.get("reading_order_caption_type") or "").strip()
    ]
    for caption in captions:
        match = _match_caption_target(
            caption,
            targets,
            page_width=page.width_pt,
            page_height=page.height_pt,
        )
        if match is None:
            continue
        target = match.target
        caption.metadata.update(
            {
                "reading_order_caption_target_id": target.id,
                "reading_order_caption_target_kind": target.kind,
                "reading_order_caption_target_source": target.source,
                "reading_order_caption_target_bbox_pdf": target.bbox.as_list(),
                "reading_order_caption_target_distance_pt": round(match.distance_pt, 4),
                "reading_order_caption_target_position": match.position,
                "reading_order_caption_target_confidence": match.confidence,
            }
        )
        _append_reading_order_evidence(
            caption,
            (
                "caption-target-proximity",
                f"{target.kind}-target",
                match.position,
            ),
        )
        annotation = caption.metadata.get("annotation")
        if isinstance(annotation, dict):
            annotation.update(
                {
                    "reading_order_caption_target_id": target.id,
                    "reading_order_caption_target_kind": target.kind,
                    "reading_order_caption_target_source": target.source,
                    "reading_order_caption_target_bbox_pdf": target.bbox.as_list(),
                    "reading_order_caption_target_distance_pt": round(match.distance_pt, 4),
                    "reading_order_caption_target_position": match.position,
                    "reading_order_caption_target_confidence": match.confidence,
                    "reading_order_evidence": _reading_order_evidence(caption),
                    "reading_order_evidence_summary": caption.metadata.get("reading_order_evidence_summary", ""),
                }
            )
        if target.element is not None:
            _append_target_caption(target.element, caption.id)


def _caption_targets_for_page(page: PageIR, layout_regions: list[LayoutRegion]) -> list[_CaptionTarget]:
    targets: list[_CaptionTarget] = []
    page_area = max(page.width_pt * page.height_pt, 1.0)
    for region in layout_regions:
        if region.kind not in {"figure", "table"} or _too_large_for_caption_target(region.bbox, page_area):
            continue
        targets.append(
            _CaptionTarget(
                id=region.id,
                kind=region.kind,
                bbox=region.bbox,
                source="layout-region",
            )
        )

    for element in page.elements:
        source_kind = str(element.metadata.get("source") or "")
        target_kind: str | None = None
        if element.type == "image" and source_kind == "native-image":
            target_kind = "figure"
        elif element.type == "image" and source_kind == "native-raster-region":
            raster_kind = str(element.metadata.get("raster_region_kind") or "").strip().lower()
            if raster_kind in {"figure", "table"}:
                target_kind = raster_kind
        if target_kind is None:
            continue
        if _too_large_for_caption_target(element.bbox_pdf, page_area):
            continue
        targets.append(
            _CaptionTarget(
                id=element.id,
                kind=target_kind,
                bbox=element.bbox_pdf,
                source=source_kind,
                element=element,
            )
        )
    return targets


def _match_caption_target(
    caption: ElementIR,
    targets: list[_CaptionTarget],
    *,
    page_width: float,
    page_height: float,
) -> _CaptionTargetMatch | None:
    caption_type = str(caption.metadata.get("reading_order_caption_type") or "").strip().lower()
    if not caption_type:
        return None
    candidate_matches: list[tuple[float, _CaptionTargetMatch]] = []
    for target in targets:
        if not _caption_target_kind_matches(caption_type, target.kind):
            continue
        scored = _score_caption_target(caption.bbox_pdf, target, caption_type, page_width, page_height)
        if scored is not None:
            candidate_matches.append(scored)
    if not candidate_matches:
        return None
    candidate_matches.sort(key=lambda item: item[0])
    return candidate_matches[0][1]


def _caption_target_kind_matches(caption_type: str, target_kind: str) -> bool:
    if caption_type == "figure":
        return target_kind == "figure"
    if caption_type == "table":
        return target_kind == "table"
    return False


def _score_caption_target(
    caption_box: BBox,
    target: _CaptionTarget,
    caption_type: str,
    page_width: float,
    page_height: float,
) -> tuple[float, _CaptionTargetMatch] | None:
    target_box = target.bbox
    if caption_box.width <= 0 or caption_box.height <= 0 or target_box.width <= 0 or target_box.height <= 0:
        return None
    if target_box.y1 <= caption_box.y0:
        vertical_gap = caption_box.y0 - target_box.y1
        position = "caption-below-target"
    elif caption_box.y1 <= target_box.y0:
        vertical_gap = target_box.y0 - caption_box.y1
        position = "caption-above-target"
    else:
        vertical_gap = 0.0
        position = "caption-overlaps-target"
    max_gap = max(24.0, min(76.0, page_height * 0.09))
    if vertical_gap > max_gap:
        return None

    overlap_ratio = _horizontal_overlap_ratio(caption_box, target_box)
    center_delta = abs(_center_x(caption_box) - _center_x(target_box))
    center_limit = max(24.0, min(page_width * 0.22, max(caption_box.width, target_box.width) * 0.42))
    if overlap_ratio < 0.25 and center_delta > center_limit:
        return None

    normalized_gap = vertical_gap / max(max_gap, 1.0)
    normalized_center_delta = center_delta / max(page_width, 1.0)
    type_penalty = 0.0
    if caption_type == "figure" and position != "caption-below-target":
        type_penalty = 0.08
    elif caption_type == "table" and position != "caption-above-target":
        type_penalty = 0.06
    score = normalized_gap + normalized_center_delta * 0.65 - overlap_ratio * 0.22 + type_penalty
    confidence = max(0.58, min(0.92, 0.9 - normalized_gap * 0.24 - normalized_center_delta * 0.28 - type_penalty))
    return (
        score,
        _CaptionTargetMatch(
            target=target,
            position=position,
            distance_pt=vertical_gap,
            confidence=round(confidence, 4),
        ),
    )


def _too_large_for_caption_target(bbox: BBox, page_area: float) -> bool:
    if bbox.width <= 0 or bbox.height <= 0:
        return True
    return (bbox.width * bbox.height) / max(page_area, 1.0) >= 0.72


def _horizontal_overlap_ratio(left: BBox, right: BBox) -> float:
    overlap = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    denominator = max(1.0, min(left.width, right.width))
    return min(1.0, overlap / denominator)


def _center_x(bbox: BBox) -> float:
    return (bbox.x0 + bbox.x1) / 2


def _append_reading_order_evidence(element: ElementIR, evidence: tuple[str, ...]) -> None:
    existing = _reading_order_evidence(element)
    for item in evidence:
        if item and item not in existing:
            existing.append(item)
    element.metadata["reading_order_evidence"] = existing
    element.metadata["reading_order_evidence_summary"] = ",".join(existing)


def _append_target_caption(element: ElementIR, caption_id: str) -> None:
    existing = element.metadata.get("caption_ids")
    caption_ids = [str(item) for item in existing] if isinstance(existing, list) else []
    if caption_id not in caption_ids:
        caption_ids.append(caption_id)
    element.metadata["caption_ids"] = caption_ids
    annotation = element.metadata.get("annotation")
    if isinstance(annotation, dict):
        annotation["caption_ids"] = caption_ids


def _external_structure_role(element: ElementIR) -> str | None:
    label = str(element.metadata.get("external_structure_label") or "").strip().lower()
    if not label:
        return None
    normalized = label.replace("-", "_").replace(" ", "_")
    return STRUCTURE_ROLE_MAP.get(normalized)


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
