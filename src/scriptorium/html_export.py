from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from jinja2 import Environment, PackageLoader, select_autoescape

from .models import BBox, DisplayMode, DocumentIR, ElementIR, PageIR

HtmlTextFit = Literal["none", "svg"]


def export_html(
    document: DocumentIR,
    out_dir: str | Path,
    display_mode: DisplayMode = "background",
    text_fit: HtmlTextFit = "none",
) -> Path:
    target = Path(out_dir)
    assets_dir = target / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    include_background = display_mode != "structured"
    pages = [
        _prepare_page_assets(page, assets_dir, display_mode=display_mode, include_background=include_background)
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
    if display_mode in {"structured", "edited", "fidelity"}:
        return element.edited_text is None
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
) -> dict[str, object]:
    background_source = _background_source(page, display_mode)
    page_asset_dir = assets_dir / f"page_{page.page_index + 1:04d}"
    page_asset_dir.mkdir(parents=True, exist_ok=True)

    background_rel: str | None = None
    if include_background:
        background_target = page_asset_dir / background_source.name
        if background_source.resolve() != background_target.resolve():
            shutil.copy2(background_source, background_target)
        background_rel = background_target.relative_to(assets_dir.parent).as_posix()

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
        elements.append({"ir": element, "crop": crop_rel})

    return {
        "ir": page,
        "background": background_rel,
        "elements": elements,
    }


def _background_source(page: PageIR, display_mode: DisplayMode) -> Path:
    if display_mode == "fidelity" and page.background_svg:
        svg_source = Path(page.background_svg)
        if svg_source.exists():
            return svg_source
    return Path(page.background_image)
