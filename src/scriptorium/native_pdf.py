from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Literal

import fitz

from .geometry import clamp_bbox, pdf_to_px_bbox, reading_order_key
from .models import BBox, DocumentIR, ElementIR, PageIR, RevisionIR
from .pdf_render import RenderedDocument, RenderedPage
from .reading_order import infer_semantic_reading_order

FontProfile = Literal["browser-default", "local-urw"]
RasterPolicy = Literal["none", "dense", "tables"]
OcrFallback = Literal["off", "image-only"]


@dataclass(frozen=True)
class _RasterRegion:
    bbox: BBox
    reason: str
    kind: str


@dataclass(frozen=True)
class _PageExtraction:
    elements: list[ElementIR]
    diagnostics: dict[str, Any]


def extract_native_pdf_to_ir(
    rendered: RenderedDocument,
    font_profile: FontProfile = "browser-default",
    raster_policy: RasterPolicy = "dense",
    font_size_scale: float = 1.0,
    ocr_fallback: OcrFallback = "image-only",
    ocr_language: str = "eng+chi_sim",
    ocr_dpi: int = 144,
) -> DocumentIR:
    pages: list[PageIR] = []
    page_diagnostics: list[dict[str, Any]] = []
    with fitz.open(rendered.source_pdf) as doc:
        for rendered_page in rendered.pages:
            page = doc[rendered_page.page_index]
            extraction = _extract_page_text_elements(
                page,
                rendered_page,
                font_profile=font_profile,
                raster_policy=raster_policy,
                font_size_scale=font_size_scale,
                ocr_fallback=ocr_fallback,
                ocr_language=ocr_language,
                ocr_dpi=ocr_dpi,
            )
            page_diagnostics.append(extraction.diagnostics)
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
                    background_svg=str(rendered_page.background_svg) if rendered_page.background_svg else None,
                    elements=extraction.elements,
                )
            )

    return DocumentIR(
        source_pdf=str(rendered.source_pdf),
        render_dpi=rendered.render_dpi,
        page_count=len(rendered.pages),
        pages=pages,
        revisions=[
            RevisionIR(
                reason="native-pdf-extraction",
                payload={
                    "source": "pymupdf-text-dict",
                    "font_profile": font_profile,
                    "raster_policy": raster_policy,
                    "font_size_scale": font_size_scale,
                    "ocr_fallback": ocr_fallback,
                    "ocr_language": ocr_language,
                    "ocr_dpi": ocr_dpi,
                },
            )
        ],
        metadata={
            "extraction_mode": "native",
            "font_profile": font_profile,
            "raster_policy": raster_policy,
            "font_size_scale": font_size_scale,
            "ocr_fallback": ocr_fallback,
            "ocr_language": ocr_language,
            "ocr_dpi": ocr_dpi,
            "page_extraction": page_diagnostics,
        },
    )


def _extract_page_text_elements(
    page: fitz.Page,
    rendered_page: RenderedPage,
    *,
    font_profile: FontProfile,
    raster_policy: RasterPolicy,
    font_size_scale: float,
    ocr_fallback: OcrFallback,
    ocr_language: str,
    ocr_dpi: int,
) -> _PageExtraction:
    text_dict = page.get_text("dict")
    raw_lines = _raw_text_lines_from_dict(text_dict)
    image_coverage = _image_area_coverage(text_dict, rendered_page)
    source_kind = "native-pdf"
    id_prefix = "n"
    text_confidence = 1.0
    ocr_status = "not-needed" if raw_lines else "not-candidate"
    ocr_language_used: str | None = None
    ocr_error: str | None = None
    ocr_candidate = not raw_lines and image_coverage >= 0.6
    if ocr_candidate:
        if ocr_fallback == "image-only":
            ocr_lines, ocr_language_used, ocr_error = _ocr_raw_text_lines(
                page,
                language=ocr_language,
                dpi=ocr_dpi,
            )
            if ocr_lines:
                raw_lines = ocr_lines
                source_kind = "native-ocr"
                id_prefix = "o"
                text_confidence = 0.72
                ocr_status = "applied"
            elif ocr_error:
                ocr_status = "unavailable"
            else:
                ocr_status = "no-text"
        else:
            ocr_status = "disabled"

    raw_lines.sort(key=lambda item: reading_order_key(item["bbox"]))
    reading_order_assignments = {
        assignment.item_index: assignment
        for assignment in infer_semantic_reading_order(
            [raw["bbox"] for raw in raw_lines],
            rendered_page.width_pt,
            rendered_page.height_pt,
        )
    }
    text_elements: list[ElementIR] = []
    for visual_index, raw in enumerate(raw_lines):
        order_assignment = reading_order_assignments[visual_index]
        bbox_pdf = clamp_bbox(raw["bbox"], rendered_page.width_pt, rendered_page.height_pt)
        bbox_px = pdf_to_px_bbox(bbox_pdf, rendered_page.scale_x, rendered_page.scale_y)
        style = _style_from_spans(raw["spans"], bbox_pdf, font_profile=font_profile, font_size_scale=font_size_scale)
        text_runs = _text_runs_from_spans(
            raw["spans"],
            rendered_page,
            font_profile=font_profile,
            font_size_scale=font_size_scale,
        )
        metadata = {
            "source": source_kind,
            "span_count": len(raw["spans"]),
            "text_run_count": len(text_runs),
            "mixed_inline_style": _has_mixed_run_styles(text_runs),
            "text_runs": text_runs,
            **order_assignment.as_metadata(),
        }
        if source_kind == "native-ocr":
            metadata.update(
                {
                    "ocr_fallback": True,
                    "ocr_language": ocr_language_used or ocr_language,
                    "ocr_dpi": ocr_dpi,
                }
            )
        text_elements.append(
            ElementIR(
                id=f"p{rendered_page.page_index + 1:04d}-{id_prefix}{visual_index + 1:04d}",
                page_index=rendered_page.page_index,
                type="title" if style["font_size_px"] >= 22 else "text",
                bbox_pdf=bbox_pdf,
                bbox_px=bbox_px,
                source_text=raw["text"],
                confidence=text_confidence,
                reading_order=order_assignment.semantic_order,
                style_hint=style,
                metadata=metadata,
            )
        )
    image_elements = _extract_page_image_elements(text_dict, rendered_page, start_order=len(text_elements) + 1)
    shape_elements = _extract_page_shape_elements(
        page,
        rendered_page,
        start_order=len(text_elements) + len(image_elements) + 1,
    )
    raster_elements = _extract_complex_vector_region_elements(
        page,
        rendered_page,
        text_elements,
        image_elements,
        shape_elements,
        start_order=len(text_elements) + len(image_elements) + 1,
        raster_policy=raster_policy,
    )
    if raster_elements:
        raster_regions = [element.bbox_pdf for element in raster_elements]
        text_elements = _elements_outside_regions(text_elements, raster_regions)
        image_elements = _elements_outside_regions(image_elements, raster_regions)
        shape_elements = _elements_outside_regions(shape_elements, raster_regions)
    diagnostics = {
        "page_index": rendered_page.page_index,
        "native_text_line_count": sum(1 for line in _raw_text_lines_from_dict(text_dict) if line["text"].strip()),
        "text_line_count": len(text_elements),
        "image_area_coverage": round(image_coverage, 6),
        "image_only_candidate": ocr_candidate,
        "ocr_fallback": ocr_fallback,
        "ocr_fallback_status": ocr_status,
        "ocr_language_requested": ocr_language,
        "ocr_language_used": ocr_language_used,
        "ocr_dpi": ocr_dpi,
        "ocr_error": ocr_error,
    }
    return _PageExtraction(
        elements=[*text_elements, *image_elements, *raster_elements, *shape_elements],
        diagnostics=diagnostics,
    )


def _raw_text_lines_from_dict(text_dict: dict[str, Any]) -> list[dict[str, Any]]:
    raw_lines: list[dict[str, Any]] = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text", "")]
            if not spans:
                continue
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue
            bbox = BBox.from_any(line.get("bbox"))
            raw_lines.append({"bbox": bbox, "text": text, "spans": spans})
    return raw_lines


def _ocr_raw_text_lines(
    page: fitz.Page,
    *,
    language: str,
    dpi: int,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    errors: list[str] = []
    for candidate in _ocr_language_candidates(language):
        try:
            textpage = page.get_textpage_ocr(language=candidate, dpi=dpi)
            raw_lines = _raw_text_lines_from_dict(page.get_text("dict", textpage=textpage))
        except Exception as exc:  # pragma: no cover - depends on local Tesseract installation.
            errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
            continue
        if raw_lines:
            return raw_lines, candidate, None
    return [], None, "; ".join(errors) if errors else None


def _ocr_language_candidates(language: str) -> tuple[str, ...]:
    requested = language.strip() or "eng"
    candidates = [requested]
    if requested != "eng":
        candidates.append("eng")
    return tuple(dict.fromkeys(candidates))


def _image_area_coverage(text_dict: dict[str, Any], rendered_page: RenderedPage) -> float:
    page_area = max(1.0, rendered_page.width_pt * rendered_page.height_pt)
    blocks = _dedupe_image_blocks(
        block for block in text_dict.get("blocks", []) if block.get("type") == 1 and block.get("image")
    )
    area = 0.0
    for block in blocks:
        try:
            bbox = clamp_bbox(BBox.from_any(block.get("bbox")), rendered_page.width_pt, rendered_page.height_pt)
        except (TypeError, ValueError):
            continue
        area += bbox.width * bbox.height
    return min(area / page_area, 1.0)


def _extract_complex_vector_region_elements(
    page: fitz.Page,
    rendered_page: RenderedPage,
    text_elements: list[ElementIR],
    image_elements: list[ElementIR],
    shape_elements: list[ElementIR],
    start_order: int,
    raster_policy: RasterPolicy,
) -> list[ElementIR]:
    raster_regions = _complex_vector_regions(shape_elements, rendered_page, raster_policy=raster_policy)
    if not raster_regions:
        return []

    raster_dir = rendered_page.background_image.parent / "native-raster-regions" / f"page_{rendered_page.page_index + 1:04d}"
    raster_dir.mkdir(parents=True, exist_ok=True)

    elements: list[ElementIR] = []
    for offset, raster_region in enumerate(raster_regions, start=1):
        region = raster_region.bbox
        raster_path = raster_dir / f"region_{offset:04d}.png"
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(rendered_page.scale_x, rendered_page.scale_y),
            clip=fitz.Rect(region.x0, region.y0, region.x1, region.y1),
            alpha=False,
        )
        pixmap.save(raster_path)
        bbox_px = pdf_to_px_bbox(region, rendered_page.scale_x, rendered_page.scale_y)
        hidden_text_count = sum(1 for element in text_elements if _bbox_center_inside(region, element.bbox_pdf))
        hidden_image_count = sum(1 for element in image_elements if _bbox_center_inside(region, element.bbox_pdf))
        hidden_shape_count = sum(1 for element in shape_elements if _bbox_center_inside(region, element.bbox_pdf))
        elements.append(
            ElementIR(
                id=f"p{rendered_page.page_index + 1:04d}-r{offset:04d}",
                page_index=rendered_page.page_index,
                type="image",
                bbox_pdf=region,
                bbox_px=bbox_px,
                source_text="",
                confidence=0.92,
                reading_order=start_order + offset - 1,
                style_hint={
                    "line_height": 1,
                    "font_size_px": 1,
                    "font_family": "Arial, sans-serif",
                    "object_fit": "fill",
                },
                source_crop=str(raster_path),
                metadata={
                    "source": "native-raster-region",
                    "raster_fallback": True,
                    "raster_policy": raster_policy,
                    "raster_reason": raster_region.reason,
                    "raster_region_kind": raster_region.kind,
                    "raster_region_index": offset,
                    "rasterized_text_count": hidden_text_count,
                    "rasterized_image_count": hidden_image_count,
                    "rasterized_shape_count": hidden_shape_count,
                },
            )
        )
    return elements


def _complex_vector_regions(
    shape_elements: list[ElementIR],
    rendered_page: RenderedPage,
    raster_policy: RasterPolicy,
) -> list[_RasterRegion]:
    if raster_policy == "none":
        return []

    candidates: list[_RasterRegion] = []
    dense_region = _complex_vector_region(shape_elements, rendered_page)
    if dense_region is not None:
        candidates.append(_RasterRegion(dense_region, reason="dense-vector-region", kind="dense-vector"))
    if raster_policy == "tables":
        candidates.extend(_complex_table_vector_regions(shape_elements, rendered_page))
    return _dedupe_raster_regions(candidates)


def _complex_vector_region(shape_elements: list[ElementIR], rendered_page: RenderedPage) -> BBox | None:
    if len(shape_elements) < 120:
        return None
    line_count = sum(1 for element in shape_elements if element.metadata.get("line_points_pdf") is not None)
    if line_count < 20:
        return None

    region = _union_element_bbox(shape_elements)
    page_area = rendered_page.width_pt * rendered_page.height_pt
    region_area = region.width * region.height
    if region.width < 80 or region.height < 80:
        return None
    if region_area > page_area * 0.7:
        return None
    return _pad_bbox(region, 2.0, rendered_page.width_pt, rendered_page.height_pt)


def _complex_table_vector_regions(shape_elements: list[ElementIR], rendered_page: RenderedPage) -> list[_RasterRegion]:
    regions: list[_RasterRegion] = []
    for cluster in _cluster_vector_shapes(shape_elements):
        if not _is_complex_table_vector_cluster(cluster, rendered_page):
            continue
        region = _pad_bbox(_union_element_bbox(cluster), 1.5, rendered_page.width_pt, rendered_page.height_pt)
        regions.append(_RasterRegion(region, reason="complex-table-vector-region", kind="table"))
    return sorted(regions, key=lambda item: (item.bbox.y0, item.bbox.x0))


def _is_complex_table_vector_cluster(cluster: list[ElementIR], rendered_page: RenderedPage) -> bool:
    if len(cluster) < 24:
        return False

    bbox = _union_element_bbox(cluster)
    page_area = rendered_page.width_pt * rendered_page.height_pt
    if bbox.width < 80 or bbox.height < 40:
        return False
    if bbox.width * bbox.height > page_area * 0.65:
        return False

    horizontal_lines = [element for element in cluster if _shape_geometry(element) == "horizontal-line"]
    vertical_lines = [element for element in cluster if _shape_geometry(element) == "vertical-line"]
    rectangles = [element for element in cluster if _shape_geometry(element) == "rectangle"]
    if len(horizontal_lines) >= 4 and len(vertical_lines) >= 4:
        return True

    if len(rectangles) >= 24:
        x_positions = _unique_positions([element.bbox_pdf.x0 for element in rectangles])
        y_positions = _unique_positions([element.bbox_pdf.y0 for element in rectangles])
        return len(x_positions) >= 3 and len(y_positions) >= 3

    return False


def _cluster_vector_shapes(shape_elements: list[ElementIR]) -> list[list[ElementIR]]:
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
    return max(3.0, min(8.0, median(widths) * 4)) if widths else 3.0


def _bboxes_close(left: BBox, right: BBox, tolerance: float) -> bool:
    horizontal_gap = max(0.0, max(left.x0, right.x0) - min(left.x1, right.x1))
    vertical_gap = max(0.0, max(left.y0, right.y0) - min(left.y1, right.y1))
    return horizontal_gap <= tolerance and vertical_gap <= tolerance


def _shape_geometry(element: ElementIR) -> str:
    return str(element.metadata.get("shape_geometry") or "unknown")


def _unique_positions(values: list[float], tolerance: float = 2.0) -> list[float]:
    positions: list[float] = []
    for value in sorted(values):
        if not positions or abs(value - positions[-1]) > tolerance:
            positions.append(value)
    return positions


def _dedupe_raster_regions(candidates: list[_RasterRegion]) -> list[_RasterRegion]:
    accepted: list[_RasterRegion] = []
    for candidate in sorted(candidates, key=lambda item: item.bbox.width * item.bbox.height, reverse=True):
        if any(_bbox_overlap_ratio(candidate.bbox, existing.bbox) >= 0.82 for existing in accepted):
            continue
        accepted.append(candidate)
    return sorted(accepted, key=lambda item: (item.bbox.y0, item.bbox.x0))


def _bbox_overlap_ratio(left: BBox, right: BBox) -> float:
    overlap_width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    overlap_height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
    overlap_area = overlap_width * overlap_height
    if overlap_area <= 0:
        return 0.0
    smaller_area = max(1.0, min(left.width * left.height, right.width * right.height))
    return overlap_area / smaller_area


def _union_element_bbox(elements: list[ElementIR]) -> BBox:
    return BBox(
        x0=min(element.bbox_pdf.x0 for element in elements),
        y0=min(element.bbox_pdf.y0 for element in elements),
        x1=max(element.bbox_pdf.x1 for element in elements),
        y1=max(element.bbox_pdf.y1 for element in elements),
    )


def _pad_bbox(bbox: BBox, padding: float, page_width: float, page_height: float) -> BBox:
    return clamp_bbox(
        BBox(x0=bbox.x0 - padding, y0=bbox.y0 - padding, x1=bbox.x1 + padding, y1=bbox.y1 + padding),
        page_width,
        page_height,
    )


def _elements_outside_regions(elements: list[ElementIR], regions: list[BBox]) -> list[ElementIR]:
    return [element for element in elements if not any(_bbox_center_inside(region, element.bbox_pdf) for region in regions)]


def _bbox_center_inside(region: BBox, bbox: BBox) -> bool:
    center_x = (bbox.x0 + bbox.x1) / 2
    center_y = (bbox.y0 + bbox.y1) / 2
    return region.x0 <= center_x <= region.x1 and region.y0 <= center_y <= region.y1


def _extract_page_image_elements(
    text_dict: dict[str, Any],
    rendered_page: RenderedPage,
    start_order: int,
) -> list[ElementIR]:
    image_blocks = _dedupe_image_blocks(
        block for block in text_dict.get("blocks", []) if block.get("type") == 1 and block.get("image")
    )
    if not image_blocks:
        return []

    image_dir = rendered_page.background_image.parent / "native-images" / f"page_{rendered_page.page_index + 1:04d}"
    image_dir.mkdir(parents=True, exist_ok=True)
    elements: list[ElementIR] = []
    for offset, block in enumerate(image_blocks):
        bbox_pdf = clamp_bbox(BBox.from_any(block.get("bbox")), rendered_page.width_pt, rendered_page.height_pt)
        if bbox_pdf.width <= 0 or bbox_pdf.height <= 0:
            continue
        image_bytes = bytes(block.get("image") or b"")
        if not image_bytes:
            continue

        ext = _safe_image_ext(block.get("ext"))
        image_path = image_dir / f"image_{offset + 1:04d}.{ext}"
        image_path.write_bytes(image_bytes)
        bbox_px = pdf_to_px_bbox(bbox_pdf, rendered_page.scale_x, rendered_page.scale_y)
        elements.append(
            ElementIR(
                id=f"p{rendered_page.page_index + 1:04d}-i{offset + 1:04d}",
                page_index=rendered_page.page_index,
                type="image",
                bbox_pdf=bbox_pdf,
                bbox_px=bbox_px,
                source_text="",
                confidence=1.0,
                reading_order=start_order + offset,
                style_hint={
                    "line_height": 1,
                    "font_size_px": 1,
                    "font_family": "Arial, sans-serif",
                    "object_fit": "fill",
                },
                source_crop=str(image_path),
                metadata={
                    "source": "native-image",
                    "image_ext": ext,
                    "image_width": int(block.get("width") or 0),
                    "image_height": int(block.get("height") or 0),
                    "image_size_bytes": len(image_bytes),
                    "image_number": block.get("number"),
                    "xres": int(block.get("xres") or 0),
                    "yres": int(block.get("yres") or 0),
                },
            )
        )
    return elements


def _dedupe_image_blocks(blocks: Iterable[Any]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[tuple[float, float, float, float], str, str]] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        image_bytes = bytes(block.get("image") or b"")
        if not image_bytes:
            continue
        bbox = BBox.from_any(block.get("bbox"))
        key = (
            tuple(round(value, 3) for value in bbox.as_list()),
            str(block.get("ext") or ""),
            sha1(image_bytes).hexdigest(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(block)
    return unique


def _safe_image_ext(value: Any) -> str:
    ext = str(value or "png").lower().strip().lstrip(".")
    return ext if ext in {"png", "jpg", "jpeg", "webp"} else "png"


def _extract_page_shape_elements(page: fitz.Page, rendered_page: RenderedPage, start_order: int) -> list[ElementIR]:
    shapes: list[ElementIR] = []
    for offset, drawing in enumerate(page.get_drawings(), start=0):
        width_pt = float(drawing.get("width") or 0)
        shape_geometry = _drawing_geometry(drawing)
        line_points_pdf = _line_points_from_drawing(drawing)
        bbox_pdf = _drawing_bbox(drawing, rendered_page, width_pt)
        if bbox_pdf is None:
            continue
        if bbox_pdf.width <= 0 or bbox_pdf.height <= 0:
            continue
        svg_path_pdf = None if line_points_pdf is not None else _svg_path_from_drawing(drawing, bbox_pdf)
        bbox_px = pdf_to_px_bbox(bbox_pdf, rendered_page.scale_x, rendered_page.scale_y)
        fill = _rgb_to_css(drawing.get("fill"))
        stroke = _rgb_to_css(drawing.get("color"))
        if not fill and not stroke:
            continue
        if line_points_pdf is not None:
            fill_color = "transparent"
            stroke_color = stroke or fill or "transparent"
            border_width_pt = round(width_pt, 3) if width_pt else 1
            border_width_px = round(border_width_pt * rendered_page.scale_x, 3)
        else:
            fill_color = fill or "transparent"
            stroke_color = stroke or "transparent"
            border_width_pt = round(width_pt, 3) if width_pt else 0
            border_width_px = round(width_pt * rendered_page.scale_x, 3) if width_pt else 0
        svg_stroke_width_pt = border_width_pt if border_width_pt else (0.5 if svg_path_pdf and stroke else 0)
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
                    "line_points_pdf": [round(value, 4) for value in line_points_pdf]
                    if line_points_pdf is not None
                    else None,
                    "stroke_opacity": drawing.get("stroke_opacity"),
                    "fill_opacity": drawing.get("fill_opacity"),
                    "drawing_item_count": len(drawing.get("items") or []),
                    "svg_path_pdf": svg_path_pdf,
                    "svg_fill_rule": "evenodd" if drawing.get("even_odd") else "nonzero",
                    "svg_stroke_width_pt": round(svg_stroke_width_pt, 3),
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
    if _line_points_from_drawing(drawing) is not None:
        stroke_padding = max(width_pt, 0.5) / 2
        x0 -= stroke_padding
        y0 -= stroke_padding
        x1 += stroke_padding
        y1 += stroke_padding
    else:
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


def _line_points_from_drawing(drawing: dict[str, Any]) -> tuple[float, float, float, float] | None:
    items = drawing.get("items") or []
    if len(items) != 1:
        return None
    item = items[0]
    if not item or item[0] != "l" or len(item) < 3:
        return None
    start, end = item[1], item[2]
    if not isinstance(start, fitz.Point) or not isinstance(end, fitz.Point):
        return None
    return (float(start.x), float(start.y), float(end.x), float(end.y))


def _svg_path_from_drawing(drawing: dict[str, Any], bbox: BBox) -> str | None:
    path: list[str] = []
    current: fitz.Point | None = None
    for item in drawing.get("items") or []:
        operator = item[0] if item else None
        if operator == "l" and len(item) >= 3:
            start, end = item[1], item[2]
            if not isinstance(start, fitz.Point) or not isinstance(end, fitz.Point):
                return None
            if current is None or not _same_point(current, start):
                path.append(f"M {_svg_point(start, bbox)}")
            path.append(f"L {_svg_point(end, bbox)}")
            current = end
        elif operator == "c" and len(item) >= 5:
            start, control_1, control_2, end = item[1], item[2], item[3], item[4]
            if not all(isinstance(point, fitz.Point) for point in (start, control_1, control_2, end)):
                return None
            if current is None or not _same_point(current, start):
                path.append(f"M {_svg_point(start, bbox)}")
            path.append(f"C {_svg_point(control_1, bbox)} {_svg_point(control_2, bbox)} {_svg_point(end, bbox)}")
            current = end
        elif operator == "re" and len(item) >= 2:
            rect = item[1]
            if not isinstance(rect, fitz.Rect):
                return None
            path.append(
                " ".join(
                    [
                        f"M {_svg_xy(rect.x0, rect.y0, bbox)}",
                        f"L {_svg_xy(rect.x1, rect.y0, bbox)}",
                        f"L {_svg_xy(rect.x1, rect.y1, bbox)}",
                        f"L {_svg_xy(rect.x0, rect.y1, bbox)}",
                        "Z",
                    ]
                )
            )
            current = None
        elif operator == "qu" and len(item) >= 2:
            points = _quad_points(item[1])
            if points is None:
                return None
            path.append(
                " ".join(
                    [
                        f"M {_svg_point(points[0], bbox)}",
                        f"L {_svg_point(points[1], bbox)}",
                        f"L {_svg_point(points[2], bbox)}",
                        f"L {_svg_point(points[3], bbox)}",
                        "Z",
                    ]
                )
            )
            current = None
        else:
            return None

    if not path:
        return None
    if drawing.get("closePath") and path[-1] != "Z":
        path.append("Z")
    return " ".join(path)


def _quad_points(value: Any) -> tuple[fitz.Point, fitz.Point, fitz.Point, fitz.Point] | None:
    if all(hasattr(value, attribute) for attribute in ("ul", "ur", "lr", "ll")):
        return (value.ul, value.ur, value.lr, value.ll)
    if isinstance(value, (list, tuple)) and len(value) == 4 and all(isinstance(point, fitz.Point) for point in value):
        return (value[0], value[1], value[2], value[3])
    return None


def _same_point(left: fitz.Point, right: fitz.Point) -> bool:
    return abs(float(left.x) - float(right.x)) <= 0.01 and abs(float(left.y) - float(right.y)) <= 0.01


def _svg_point(point: fitz.Point, bbox: BBox) -> str:
    return _svg_xy(float(point.x), float(point.y), bbox)


def _svg_xy(x: float, y: float, bbox: BBox) -> str:
    return f"{_svg_number(x - bbox.x0)} {_svg_number(y - bbox.y0)}"


def _svg_number(value: float) -> str:
    rounded = round(value, 4)
    if abs(rounded) < 0.0001:
        rounded = 0
    return f"{rounded:g}"


def _style_from_spans(
    spans: list[dict[str, Any]],
    bbox: BBox,
    *,
    font_profile: FontProfile,
    font_size_scale: float,
) -> dict[str, Any]:
    if not spans:
        return {
            "font_size_px": round(max(7.0, bbox.height * 0.72), 2),
            "line_height": 1.15,
            "font_family": _css_font_family("serif", font_profile=font_profile),
        }
    first = next((span for span in spans if span.get("text", "").strip()), spans[0])
    return _style_from_span(first, bbox, font_profile=font_profile, font_size_scale=font_size_scale)


def _style_from_span(
    span: dict[str, Any],
    bbox: BBox,
    *,
    font_profile: FontProfile,
    font_size_scale: float,
) -> dict[str, Any]:
    raw_size = float(span.get("size", max(7.0, bbox.height * 0.72)))
    size = raw_size * font_size_scale
    font = str(span.get("font", "serif"))
    flags = int(span.get("flags", 0))
    script = "superscript" if flags & 1 else "baseline"
    return {
        "font_size_px": round(size * 96.0 / 72.0, 2),
        "font_size_pt": round(raw_size, 2),
        "font_size_scale": round(font_size_scale, 4),
        "line_height": 1.12,
        "font_family": _css_font_family(font, font_profile=font_profile),
        "font_weight": 700 if flags & 16 else 400,
        "font_style": "italic" if flags & 2 else "normal",
        "text_color": _int_color_to_css(span.get("color")) or "rgb(17, 32, 42)",
        "font_name": font,
        "bold": bool(flags & 16),
        "italic": bool(flags & 2),
        "script": script,
        "vertical_align": "super" if script == "superscript" else "baseline",
    }


def _text_runs_from_spans(
    spans: list[dict[str, Any]],
    rendered_page: RenderedPage,
    *,
    font_profile: FontProfile,
    font_size_scale: float,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for index, span in enumerate(spans):
        text = str(span.get("text", ""))
        if not text:
            continue
        bbox_pdf = clamp_bbox(BBox.from_any(span.get("bbox")), rendered_page.width_pt, rendered_page.height_pt)
        bbox_px = pdf_to_px_bbox(bbox_pdf, rendered_page.scale_x, rendered_page.scale_y)
        style = _style_from_span(span, bbox_pdf, font_profile=font_profile, font_size_scale=font_size_scale)
        origin = span.get("origin")
        runs.append(
            {
                "index": index,
                "text": text,
                "bbox_pdf": bbox_pdf.as_list(),
                "bbox_px": bbox_px.as_list(),
                "origin_pdf": [float(origin[0]), float(origin[1])] if isinstance(origin, (list, tuple)) else None,
                "style": style,
                "script": style["script"],
                "font_name": style["font_name"],
                "flags": int(span.get("flags", 0)),
            }
        )
    return _trim_and_reindex_runs(runs)


def _trim_and_reindex_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    while runs and not str(runs[0].get("text", "")).strip():
        runs.pop(0)
    if runs:
        runs[0]["text"] = str(runs[0]["text"]).lstrip()

    while runs and not str(runs[-1].get("text", "")).strip():
        runs.pop()
    if runs:
        runs[-1]["text"] = str(runs[-1]["text"]).rstrip()

    cleaned: list[dict[str, Any]] = []
    for index, run in enumerate(runs):
        if not str(run.get("text", "")):
            continue
        run["index"] = index
        cleaned.append(run)
    return cleaned


def _has_mixed_run_styles(runs: list[dict[str, Any]]) -> bool:
    signatures = {
        (
            run.get("style", {}).get("font_family"),
            run.get("style", {}).get("font_size_px"),
            run.get("style", {}).get("font_weight"),
            run.get("style", {}).get("font_style"),
            run.get("style", {}).get("text_color"),
            run.get("style", {}).get("vertical_align"),
        )
        for run in runs
        if str(run.get("text", "")).strip()
    }
    return len(signatures) > 1


def _css_font_family(pdf_font: str, font_profile: FontProfile = "browser-default") -> str:
    normalized = pdf_font.lower()
    if font_profile == "local-urw":
        return _local_urw_font_family(normalized)
    return _browser_default_font_family(normalized)


def _browser_default_font_family(normalized: str) -> str:
    if "arial" in normalized or "helvetica" in normalized or "liberationsans" in normalized or "nimbussan" in normalized:
        return "Arial, sans-serif"
    if "courier" in normalized or "mono" in normalized or "nimbusmono" in normalized or "sftt" in normalized:
        return "Courier New, monospace"
    if any(name in normalized for name in ("cmmi", "cmsy", "cmex", "msbm")):
        return "Cambria Math, Times New Roman, serif"
    if any(
        name in normalized
        for name in (
            "times",
            "serif",
            "nimbusrom",
            "nimbusroman",
            "cmr",
            "cmbx",
        )
    ):
        return "Times New Roman, serif"
    return "Arial, sans-serif"


def _local_urw_font_family(normalized: str) -> str:
    if "arial" in normalized or "helvetica" in normalized or "liberationsans" in normalized or "nimbussan" in normalized:
        return "Arial, Nimbus Sans, Liberation Sans, sans-serif"
    if "courier" in normalized or "mono" in normalized or "nimbusmono" in normalized or "sftt" in normalized:
        return "Nimbus Mono PS, Nimbus Mono L, Courier New, monospace"
    if any(name in normalized for name in ("cmmi", "cmsy", "cmex", "msbm", "cmr", "cmbx", "cmbsy", "cmmib", "cmuserif")):
        return "DejaVu Math TeX Gyre, Nimbus Roman, Times New Roman, serif"
    if "nimbusrom" in normalized or "times" in normalized:
        return "Nimbus Roman, Nimbus Roman No9 L, Times New Roman, serif"
    if any(
        name in normalized
        for name in (
            "serif",
        )
    ):
        return "Nimbus Roman, Times New Roman, serif"
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
