from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Literal

from jinja2 import Environment, PackageLoader, select_autoescape

from .models import BBox, DisplayMode, DocumentIR, ElementIR, PageIR

HtmlTextFit = Literal["none", "svg"]
FidelityBackground = Literal["svg", "raster"]

_REPLACEMENT_PADDING_GUARD_PX = 0.25
_REPLACEMENT_CROSS_AXIS_OVERLAP_RATIO = 0.2


def export_html(
    document: DocumentIR,
    out_dir: str | Path,
    display_mode: DisplayMode = "background",
    text_fit: HtmlTextFit = "none",
    fidelity_background: FidelityBackground = "svg",
) -> Path:
    target = Path(out_dir)
    assets_dir = target / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    include_background = display_mode != "structured"
    pages = [
        _prepare_page_assets(
            page,
            assets_dir,
            display_mode=display_mode,
            include_background=include_background,
            fidelity_background=fidelity_background,
        )
        for page in document.pages
    ]
    env = Environment(
        loader=PackageLoader("scriptorium", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("document.html.j2")
    html = template.render(
        document=document,
        pages=pages,
        display_mode=display_mode,
        element_text=element_text,
        element_text_runs=element_text_runs,
        should_position_text_runs=should_position_text_runs,
        should_use_svg_text_fit=should_use_svg_text_fit,
        svg_text_fit_geometry=svg_text_fit_geometry,
        has_replacement_text=has_replacement_text,
        shape_line=shape_line,
        shape_path=shape_path,
        annotation_attr=annotation_attr,
        text_fit=text_fit,
    )
    index_path = target / "index.html"
    index_path.write_text(html, encoding="utf-8")
    return index_path


def element_text(element: ElementIR, display_mode: DisplayMode) -> str:
    return element.text_for_mode(display_mode)


def element_text_runs(element: ElementIR, display_mode: DisplayMode) -> list[dict[str, object]]:
    if not _should_render_source_runs(element, display_mode):
        return []
    runs = element.metadata.get("text_runs")
    if not isinstance(runs, list):
        return []
    return [run for run in runs if isinstance(run, dict) and str(run.get("text", ""))]


def should_position_text_runs(element: ElementIR, display_mode: DisplayMode) -> bool:
    if display_mode != "structured":
        return False
    if not element.source_text.strip() or not bool(element.metadata.get("mixed_inline_style")):
        return False

    runs = element_text_runs(element, display_mode)
    if len(runs) < 2 or len(runs) > 4:
        return False
    if len(element.source_text) > 90:
        return False
    return any(_run_script(run) != "baseline" for run in runs)


def should_use_svg_text_fit(element: ElementIR, display_mode: DisplayMode, text_fit: HtmlTextFit) -> bool:
    if display_mode != "structured" or text_fit != "svg":
        return False
    if not element.source_text.strip() or element.edited_text is not None or element.translated_text is not None:
        return False
    if element.type not in {"text", "title"}:
        return False
    if element.bbox_pdf.width <= 0 or element.bbox_pdf.height <= 0:
        return False
    return True


def svg_text_fit_geometry(element: ElementIR, display_mode: DisplayMode) -> dict[str, object] | None:
    if display_mode != "structured" or not element.source_text.strip():
        return None

    box = element.bbox_pdf
    if box.width <= 0 or box.height <= 0:
        return None

    runs = element_text_runs(element, display_mode)
    if not runs:
        runs = [
            {
                "text": element.source_text,
                "bbox_pdf": box.as_list(),
                "origin_pdf": None,
                "style": element.style_hint,
                "script": element.style_hint.get("script", "baseline"),
            }
        ]

    geometry_runs: list[dict[str, object]] = []
    for run in runs:
        text = str(run.get("text") or "")
        if not text:
            continue
        try:
            run_box = _bbox_list(run.get("bbox_pdf"), fallback=box.as_list())
        except (TypeError, ValueError):
            continue
        style = run.get("style") if isinstance(run.get("style"), dict) else element.style_hint
        origin = run.get("origin_pdf")
        y = _run_baseline_y(origin, run_box, box)
        geometry_runs.append(
            {
                "text": text,
                "x": _round_svg(max(0.0, run_box[0] - box.x0)),
                "y": _round_svg(y),
                "text_length": _round_svg(max(0.01, run_box[2] - run_box[0])),
                "font_family": str(style.get("font_family") or element.style_hint.get("font_family") or "serif"),
                "font_size": _round_svg(_svg_font_size(style, element.style_hint)),
                "font_weight": _font_weight(style, element.style_hint),
                "font_style": str(style.get("font_style") or element.style_hint.get("font_style") or "normal"),
                "text_color": str(style.get("text_color") or element.style_hint.get("text_color") or "#111"),
            }
        )

    if not geometry_runs:
        return None

    return {
        "width": _round_svg(box.width),
        "height": _round_svg(box.height),
        "runs": geometry_runs,
    }


def has_replacement_text(element: ElementIR, display_mode: DisplayMode) -> bool:
    return display_mode == "fidelity" and bool((element.translated_text or element.edited_text or "").strip())


def page_replacement_geometries(page: PageIR, display_mode: DisplayMode) -> dict[str, dict[str, object]]:
    return _replacement_candidates(page, display_mode)


def replacement_geometry(element: ElementIR, page: PageIR, display_mode: DisplayMode) -> dict[str, object] | None:
    if not has_replacement_text(element, display_mode):
        return None
    return _replacement_geometry_for_page(element, page, page_replacement_geometries(page, display_mode))


def _run_script(run: dict[str, object]) -> str:
    script = str(run.get("script") or "").strip()
    if script:
        return script
    style = run.get("style")
    if isinstance(style, dict):
        return str(style.get("vertical_align") or "baseline")
    return "baseline"


def _should_render_source_runs(element: ElementIR, display_mode: DisplayMode) -> bool:
    if not element.source_text:
        return False
    if display_mode == "source":
        return True
    if display_mode in {"structured", "edited"}:
        return element.edited_text is None
    if display_mode == "fidelity":
        return element.edited_text is None and element.translated_text is None
    if display_mode == "translated":
        return element.translated_text is None and element.edited_text is None
    return False


def _bbox_list(value: object, fallback: list[float]) -> list[float]:
    source = value if isinstance(value, (list, tuple)) and len(value) == 4 else fallback
    return [float(item) for item in source]


def _run_baseline_y(origin: object, run_box: list[float], element_box: BBox) -> float:
    if isinstance(origin, (list, tuple)) and len(origin) >= 2:
        return max(0.0, float(origin[1]) - element_box.y0)
    return max(0.0, run_box[3] - element_box.y0 - max(0.0, run_box[3] - run_box[1]) * 0.16)


def _svg_font_size(style: dict[str, object], fallback: dict[str, object]) -> float:
    scale = _font_size_scale(style, fallback)
    font_size_pt = style.get("font_size_pt")
    if isinstance(font_size_pt, (int, float)) and font_size_pt > 0:
        return float(font_size_pt) * scale
    font_size_px = style.get("font_size_px") or fallback.get("font_size_px")
    if isinstance(font_size_px, (int, float)) and font_size_px > 0:
        return float(font_size_px) * 72.0 / 96.0
    return 9.0


def _font_size_scale(style: dict[str, object], fallback: dict[str, object]) -> float:
    value = style.get("font_size_scale") or fallback.get("font_size_scale") or 1.0
    return float(value) if isinstance(value, (int, float)) and value > 0 else 1.0


def _font_weight(style: dict[str, object], fallback: dict[str, object]) -> int | str:
    value = style.get("font_weight") or fallback.get("font_weight") or 400
    if isinstance(value, (int, float)):
        return int(value)
    return str(value)


def _round_svg(value: float) -> float:
    return round(float(value), 4)


def annotation_attr(element: ElementIR, key: str, default: str = "") -> str:
    annotation = element.metadata.get("annotation")
    if isinstance(annotation, dict):
        value = annotation.get(key)
        if value is not None:
            return str(value)
    value = element.metadata.get(key)
    return default if value is None else str(value)


def shape_line(element: ElementIR) -> dict[str, float] | None:
    points = element.metadata.get("line_points_pdf")
    if not isinstance(points, list) or len(points) != 4:
        return None
    x0, y0, x1, y1 = (float(value) for value in points)
    bbox = element.bbox_pdf
    if bbox.width <= 0 or bbox.height <= 0:
        return None
    return {
        "x0": round(x0 - bbox.x0, 4),
        "y0": round(y0 - bbox.y0, 4),
        "x1": round(x1 - bbox.x0, 4),
        "y1": round(y1 - bbox.y0, 4),
        "width": round(bbox.width, 4),
        "height": round(bbox.height, 4),
    }


def shape_path(element: ElementIR) -> dict[str, object] | None:
    path = element.metadata.get("svg_path_pdf")
    if not isinstance(path, str) or not path:
        return None
    bbox = element.bbox_pdf
    if bbox.width <= 0 or bbox.height <= 0:
        return None
    return {
        "path": path,
        "width": round(bbox.width, 4),
        "height": round(bbox.height, 4),
        "fill_rule": str(element.metadata.get("svg_fill_rule") or "nonzero"),
        "stroke_width": float(element.metadata.get("svg_stroke_width_pt") or 0),
    }


def _prepare_page_assets(
    page: PageIR,
    assets_dir: Path,
    display_mode: DisplayMode,
    include_background: bool = True,
    fidelity_background: FidelityBackground = "svg",
) -> dict[str, object]:
    background_source = _background_source(page, display_mode, fidelity_background=fidelity_background)
    page_asset_dir = assets_dir / f"page_{page.page_index + 1:04d}"
    page_asset_dir.mkdir(parents=True, exist_ok=True)

    background_rel: str | None = None
    if include_background:
        background_target = page_asset_dir / background_source.name
        if background_source.resolve() != background_target.resolve():
            shutil.copy2(background_source, background_target)
        background_rel = background_target.relative_to(assets_dir.parent).as_posix()

    replacements = page_replacement_geometries(page, display_mode)
    elements: list[dict[str, object]] = []
    for element in page.elements:
        crop_rel: str | None = None
        if element.source_crop:
            crop_source = Path(element.source_crop)
            if crop_source.exists():
                crop_target = page_asset_dir / "crops" / crop_source.name
                crop_target.parent.mkdir(parents=True, exist_ok=True)
                if crop_source.resolve() != crop_target.resolve():
                    shutil.copy2(crop_source, crop_target)
                crop_rel = crop_target.relative_to(assets_dir.parent).as_posix()
        elements.append(
            {
                "ir": element,
                "crop": crop_rel,
                "replacement": _replacement_geometry_for_page(element, page, replacements),
            }
        )

    return {
        "ir": page,
        "background": background_rel,
        "elements": elements,
    }


def _background_source(
    page: PageIR,
    display_mode: DisplayMode,
    fidelity_background: FidelityBackground = "svg",
) -> Path:
    if display_mode == "fidelity" and fidelity_background == "svg" and page.background_svg:
        svg_source = Path(page.background_svg)
        if svg_source.exists():
            return svg_source
    return Path(page.background_image)


def _replacement_candidates(page: PageIR, display_mode: DisplayMode) -> dict[str, dict[str, object]]:
    if display_mode != "fidelity":
        return {}

    geometries: dict[str, dict[str, object]] = {}
    for element in page.elements:
        if not has_replacement_text(element, display_mode):
            continue
        geometry = _base_replacement_geometry(element, page, page.elements)
        if geometry is not None:
            geometries[element.id] = geometry

    for element in page.elements:
        geometry = geometries.get(element.id)
        if geometry is None:
            continue
        conflict_ids = _replacement_conflict_ids(element, page.elements, geometry["mask_bbox"])
        geometry["conflict_ids"] = conflict_ids
        geometry["conflict"] = bool(conflict_ids or geometry["overflow"])
        geometry["conflict_summary"] = ",".join(conflict_ids)
    return geometries


def _replacement_geometry_for_page(
    element: ElementIR,
    page: PageIR,
    replacements: dict[str, dict[str, object]],
) -> dict[str, object] | None:
    if not replacements:
        return None
    return replacements.get(element.id)


def _base_replacement_geometry(
    element: ElementIR,
    page: PageIR,
    page_elements: list[ElementIR],
) -> dict[str, object] | None:
    text = (element.translated_text or element.edited_text or "").strip()
    box = element.bbox_px
    if not text or box.width <= 0 or box.height <= 0:
        return None

    font_size = _replacement_font_size_px(element, box)
    line_height = _replacement_line_height(element)
    pad_x = _clamp(font_size * 0.18, 1.0, 6.0)
    pad_y = _clamp(font_size * 0.12, 1.0, 5.0)
    requested_padding = {
        "top": pad_y,
        "right": pad_x,
        "bottom": pad_y,
        "left": pad_x,
    }
    padding, padding_constraints = _constrained_replacement_padding(
        element,
        page_elements,
        requested_padding,
    )
    x0 = max(0.0, box.x0 - padding["left"])
    y0 = max(0.0, box.y0 - padding["top"])
    x1 = min(float(page.width_px), box.x1 + padding["right"])
    y1 = min(float(page.height_px), box.y1 + padding["bottom"])
    inner_width = max(1.0, x1 - x0 - (box.x0 - x0) - (x1 - box.x1))
    inner_height = max(1.0, y1 - y0 - (box.y0 - y0) - (y1 - box.y1))

    text_width = _estimated_replacement_text_width(text, font_size)
    width_scale = min(1.0, inner_width / text_width) if text_width > 0 else 1.0
    scale = max(0.62, width_scale)
    soft_line_count = _estimated_replacement_line_count(text, font_size, inner_width, scale)
    height_scale = min(1.0, inner_height / max(1.0, soft_line_count * font_size * line_height))
    scale = round(max(0.62, min(scale, height_scale)), 4)
    fitted_line_count = _estimated_replacement_line_count(text, font_size, inner_width, scale)
    fitted_width = text_width * scale
    fitted_height = fitted_line_count * font_size * line_height * scale
    overflow = fitted_width > inner_width * 1.05 or fitted_height > inner_height * 1.08

    constraint_side_count = sum(1 for ids in padding_constraints.values() if ids)
    constraint_ids = sorted({element_id for ids in padding_constraints.values() for element_id in ids})
    constraint_summary = ";".join(
        f"{side}:{','.join(ids)}"
        for side, ids in padding_constraints.items()
        if ids
    )
    return {
        "mask_bbox": {
            "x0": round(x0, 4),
            "y0": round(y0, 4),
            "x1": round(x1, 4),
            "y1": round(y1, 4),
            "width": round(max(0.0, x1 - x0), 4),
            "height": round(max(0.0, y1 - y0), 4),
        },
        "padding_top": round(box.y0 - y0, 4),
        "padding_right": round(x1 - box.x1, 4),
        "padding_bottom": round(y1 - box.y1, 4),
        "padding_left": round(box.x0 - x0, 4),
        "padding_requested": {side: round(value, 4) for side, value in requested_padding.items()},
        "padding_constrained": constraint_side_count > 0,
        "padding_constraint_side_count": constraint_side_count,
        "padding_constraint_ids": constraint_ids,
        "padding_constraint_summary": constraint_summary,
        "fit_scale": scale,
        "overflow": overflow,
        "estimated_text_width": round(text_width, 4),
        "estimated_text_height": round(fitted_height, 4),
        "estimated_line_count": fitted_line_count,
        "policy": "fidelity-replacement-fit-v2",
        "conflict": overflow,
        "conflict_ids": [],
        "conflict_summary": "",
    }


def _constrained_replacement_padding(
    element: ElementIR,
    page_elements: list[ElementIR],
    requested: dict[str, float],
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Keep a replacement mask from expanding into adjacent visible boxes."""

    box = element.bbox_px
    padding = dict(requested)
    constraints = {side: [] for side in requested}
    for other in page_elements:
        if other.id == element.id or not other.visibility:
            continue
        other_box = other.bbox_px
        if other_box.width <= 0 or other_box.height <= 0 or _is_mask_background_container(other_box, box):
            continue

        if _vertical_overlap_ratio(box, other_box) >= _REPLACEMENT_CROSS_AXIS_OVERLAP_RATIO:
            if _center_x(other_box) < _center_x(box):
                _limit_replacement_padding(
                    padding,
                    constraints,
                    side="left",
                    clearance=max(0.0, box.x0 - other_box.x1),
                    element_id=other.id,
                )
            elif _center_x(other_box) > _center_x(box):
                _limit_replacement_padding(
                    padding,
                    constraints,
                    side="right",
                    clearance=max(0.0, other_box.x0 - box.x1),
                    element_id=other.id,
                )

        if _horizontal_overlap_ratio(box, other_box) >= _REPLACEMENT_CROSS_AXIS_OVERLAP_RATIO:
            if _center_y(other_box) < _center_y(box):
                _limit_replacement_padding(
                    padding,
                    constraints,
                    side="top",
                    clearance=max(0.0, box.y0 - other_box.y1),
                    element_id=other.id,
                )
            elif _center_y(other_box) > _center_y(box):
                _limit_replacement_padding(
                    padding,
                    constraints,
                    side="bottom",
                    clearance=max(0.0, other_box.y0 - box.y1),
                    element_id=other.id,
                )
    return padding, constraints


def _limit_replacement_padding(
    padding: dict[str, float],
    constraints: dict[str, list[str]],
    *,
    side: str,
    clearance: float,
    element_id: str,
) -> None:
    allowed = max(0.0, clearance - _REPLACEMENT_PADDING_GUARD_PX)
    current = padding[side]
    if allowed >= current - 1e-6:
        return
    if allowed < current - 1e-6:
        padding[side] = allowed
        constraints[side] = [element_id]


def _is_mask_background_container(container: BBox, target: BBox) -> bool:
    if container.width * container.height < target.width * target.height * 1.25:
        return False
    return (
        container.x0 <= target.x0
        and container.y0 <= target.y0
        and container.x1 >= target.x1
        and container.y1 >= target.y1
    )


def _horizontal_overlap_ratio(first: BBox, second: BBox) -> float:
    overlap = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))
    return overlap / max(1.0, min(first.width, second.width))


def _vertical_overlap_ratio(first: BBox, second: BBox) -> float:
    overlap = max(0.0, min(first.y1, second.y1) - max(first.y0, second.y0))
    return overlap / max(1.0, min(first.height, second.height))


def _center_x(bbox: BBox) -> float:
    return (bbox.x0 + bbox.x1) / 2


def _center_y(bbox: BBox) -> float:
    return (bbox.y0 + bbox.y1) / 2


def _replacement_conflict_ids(
    element: ElementIR,
    page_elements: list[ElementIR],
    mask_bbox: dict[str, object],
) -> list[str]:
    conflicts: list[str] = []
    mask = _bbox_from_mapping(mask_bbox)
    if mask.width <= 0 or mask.height <= 0:
        return conflicts
    mask_area = mask.width * mask.height
    for other in page_elements:
        if other.id == element.id or not other.visibility:
            continue
        other_box = other.bbox_px
        if other_box.width <= 0 or other_box.height <= 0:
            continue
        overlap = _intersection_area(mask, other_box)
        if overlap <= 0:
            continue
        other_area = other_box.width * other_box.height
        threshold = max(4.0, min(mask_area, other_area) * 0.02)
        if overlap > threshold:
            conflicts.append(other.id)
    return conflicts


def _bbox_from_mapping(value: dict[str, object]) -> BBox:
    return BBox(
        x0=float(value["x0"]),
        y0=float(value["y0"]),
        x1=float(value["x1"]),
        y1=float(value["y1"]),
    )


def _intersection_area(first: BBox, second: BBox) -> float:
    x0 = max(first.x0, second.x0)
    y0 = max(first.y0, second.y0)
    x1 = min(first.x1, second.x1)
    y1 = min(first.y1, second.y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _replacement_font_size_px(element: ElementIR, box: BBox) -> float:
    value = element.style_hint.get("font_size_px")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return _clamp(box.height * 0.72, 8.0, 24.0)


def _replacement_line_height(element: ElementIR) -> float:
    value = element.style_hint.get("line_height")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return 1.15


def _estimated_replacement_text_width(text: str, font_size: float) -> float:
    return max((_estimated_line_width(line, font_size) for line in text.splitlines()), default=0.0)


def _estimated_replacement_line_count(text: str, font_size: float, width: float, scale: float) -> int:
    total = 0
    available = max(1.0, width)
    for line in text.splitlines() or [text]:
        line_width = _estimated_line_width(line, font_size) * scale
        total += max(1, int(math.ceil(line_width / available)))
    return max(1, total)


def _estimated_line_width(text: str, font_size: float) -> float:
    return sum(_glyph_width_factor(character) * font_size for character in text)


def _glyph_width_factor(character: str) -> float:
    if character.isspace():
        return 0.34
    if _is_cjk(character):
        return 1.0
    if character in "ilI.,'`|!":
        return 0.3
    if character in "mwMW@#%&":
        return 0.82
    if character.isdigit():
        return 0.56
    return 0.58


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
