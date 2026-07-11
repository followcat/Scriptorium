from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

from .browser_launch import chromium_launch_kwargs
from .models import DocumentIR
from .pdf_render import SourceKind, render_pdf, render_source


def inspect_fidelity_replacement_layout(
    html_path: str | Path,
    out_path: str | Path,
    chrome_executable: str | None = None,
) -> dict[str, Any]:
    """Measure replacement clipping after the exported HTML has fitted in Chromium."""

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any]
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**chromium_launch_kwargs(chrome_executable))
            try:
                page = browser.new_page(device_scale_factor=1)
                page.goto(Path(html_path).resolve().as_uri(), wait_until="networkidle")
                page.emulate_media(media="print")
                page.evaluate(
                    """async () => {
                        const fitting = window.ScriptoriumFitting;
                        if (fitting && fitting.ready) {
                          await fitting.ready;
                        }
                        if (fitting && fitting.fitAll) {
                          fitting.fitAll();
                        }
                    }"""
                )
                elements = page.eval_on_selector_all(
                    '.element[data-scriptorium-has-replacement="true"]',
                    """nodes => nodes.map(node => {
                        const computed = getComputedStyle(node);
                        const declaredLineHeight = Number.parseFloat(
                          node.style.getPropertyValue("--line-height") || computed.getPropertyValue("--line-height") || "0"
                        );
                        const renderedLineHeight = Number.parseFloat(
                          node.dataset.scriptoriumReplacementRenderedLineHeight || "0"
                        );
                        const renderedScale = Number.parseFloat(
                          node.dataset.scriptoriumReplacementRenderedFitScale
                          || node.dataset.scriptoriumReplacementFitScale
                          || "1"
                        );
                        const horizontalOverflow = node.scrollWidth > node.clientWidth + 1;
                        const verticalOverflow = node.scrollHeight > node.clientHeight + 1;
                        return {
                          element_id: node.dataset.scriptoriumElementId || node.id,
                          estimated_overflow: node.dataset.scriptoriumReplacementEstimatedOverflow === "true",
                          rendered_overflow: node.dataset.scriptoriumReplacementRenderedOverflow === "true",
                          overflow: horizontalOverflow || verticalOverflow,
                          horizontal_overflow: horizontalOverflow,
                          vertical_overflow: verticalOverflow,
                          client_width: node.clientWidth,
                          client_height: node.clientHeight,
                          scroll_width: node.scrollWidth,
                          scroll_height: node.scrollHeight,
                          fit_scale: Number.isFinite(renderedScale) ? renderedScale : null,
                          declared_line_height: Number.isFinite(declaredLineHeight) ? declaredLineHeight : null,
                          rendered_line_height: Number.isFinite(renderedLineHeight) ? renderedLineHeight : null,
                          fit_policy: node.dataset.scriptoriumReplacementRenderedFitPolicy || null,
                        };
                      })""",
                )
            finally:
                browser.close()

        report = _fidelity_replacement_layout_report(elements)
    except Exception as exc:
        report = {
            "available": False,
            "measurement_policy": "browser-dom-v1",
            "error": str(exc),
            "element_count": 0,
            "overflow_count": 0,
            "horizontal_overflow_count": 0,
            "vertical_overflow_count": 0,
            "fitted_element_count": 0,
            "line_height_compacted_count": 0,
            "elements": [],
        }

    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _fidelity_replacement_layout_report(elements: list[dict[str, Any]]) -> dict[str, Any]:
    overflow_count = sum(1 for element in elements if bool(element.get("overflow")))
    horizontal_overflow_count = sum(1 for element in elements if bool(element.get("horizontal_overflow")))
    vertical_overflow_count = sum(1 for element in elements if bool(element.get("vertical_overflow")))
    fitted_element_count = sum(1 for element in elements if element.get("fit_policy") == "browser-layout-v1")
    line_height_compacted_count = sum(
        1
        for element in elements
        if isinstance(element.get("declared_line_height"), (int, float))
        and isinstance(element.get("rendered_line_height"), (int, float))
        and float(element["rendered_line_height"]) < float(element["declared_line_height"]) - 0.01
    )
    return {
        "available": True,
        "measurement_policy": "browser-dom-v1",
        "error": None,
        "element_count": len(elements),
        "overflow_count": overflow_count,
        "horizontal_overflow_count": horizontal_overflow_count,
        "vertical_overflow_count": vertical_overflow_count,
        "fitted_element_count": fitted_element_count,
        "line_height_compacted_count": line_height_compacted_count,
        "elements": elements,
    }


def compare_html_to_rendered_pdf(
    document: DocumentIR,
    html_path: str | Path,
    out_dir: str | Path,
    chrome_executable: str | None = None,
) -> dict[str, Any]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)

    screenshot_dir = target / "screenshots"
    screenshot_dir.mkdir(exist_ok=True)
    _capture_html_pages(document, Path(html_path), screenshot_dir, chrome_executable)

    pages: list[dict[str, Any]] = []
    for page in document.pages:
        expected = Path(page.background_image)
        actual = screenshot_dir / f"page_{page.page_index + 1:04d}.png"
        diff_path = target / f"page_{page.page_index + 1:04d}.diff.png"
        pages.append(compare_images(expected, actual, diff_path, page_number=page.page_index + 1))

    report = {
        "page_count": len(document.pages),
        "compared_page_count": len(pages),
        "page_count_match": True,
        "unmatched_page_count": 0,
        "pages": pages,
    }
    report.update(_summarize_pages(pages))
    (target / "quality_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _capture_html_pages(
    document: DocumentIR,
    html_path: Path,
    screenshot_dir: Path,
    chrome_executable: str | None,
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for HTML screenshot quality checks.") from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_kwargs(chrome_executable))
        try:
            page = browser.new_page(device_scale_factor=1)
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            for page_ir in document.pages:
                locator = page.locator(f'.page[data-page-index="{page_ir.page_index}"]')
                locator.screenshot(path=str(screenshot_dir / f"page_{page_ir.page_index + 1:04d}.png"))
        finally:
            browser.close()


def compare_pdf_renderings(
    expected_pdf: str | Path,
    actual_pdf: str | Path,
    out_dir: str | Path,
    dpi: int = 192,
    max_pages: int | None = None,
    expected_page_indices: Sequence[int] | None = None,
    actual_page_indices: Sequence[int] | None = None,
) -> dict[str, Any]:
    return compare_source_to_pdf_rendering(
        expected_pdf,
        actual_pdf,
        out_dir,
        dpi=dpi,
        max_pages=max_pages,
        expected_page_indices=expected_page_indices,
        actual_page_indices=actual_page_indices,
        expected_input_kind="pdf",
        image_dpi=dpi,
        report_filename="pdf_quality_report.json",
    )


def compare_source_to_pdf_rendering(
    expected_source: str | Path,
    actual_pdf: str | Path,
    out_dir: str | Path,
    dpi: int = 192,
    max_pages: int | None = None,
    expected_page_indices: Sequence[int] | None = None,
    actual_page_indices: Sequence[int] | None = None,
    expected_input_kind: SourceKind = "auto",
    image_dpi: int = 96,
    report_filename: str = "source_quality_report.json",
) -> dict[str, Any]:
    target = Path(out_dir)
    if max_pages is not None and (expected_page_indices is not None or actual_page_indices is not None):
        raise ValueError("max_pages cannot be combined with explicit page indices")
    expected_render = render_source(
        expected_source,
        target / "expected_pages",
        dpi=dpi,
        max_pages=max_pages,
        page_indices=expected_page_indices,
        input_kind=expected_input_kind,
        image_dpi=image_dpi,
    )
    actual_dpi = image_dpi if expected_render.source_type == "image" else dpi
    actual_render = render_pdf(
        actual_pdf,
        target / "actual_pages",
        dpi=actual_dpi,
        max_pages=max_pages,
        page_indices=actual_page_indices,
    )
    expected_page_count = len(expected_render.pages)
    actual_page_count = len(actual_render.pages)
    compared_page_count = min(expected_page_count, actual_page_count)
    report_page_count = max(expected_page_count, actual_page_count)

    pages: list[dict[str, Any]] = []
    for index in range(report_page_count):
        diff_path = target / f"pdf_page_{index + 1:04d}.diff.png"
        if index < expected_page_count and index < actual_page_count:
            page_report = compare_images(
                expected_render.pages[index].background_image,
                actual_render.pages[index].background_image,
                diff_path,
                page_number=index + 1,
            )
            page_report.update(_source_page_metadata(expected_render.pages[index], actual_render.pages[index]))
            pages.append(page_report)
        elif index < expected_page_count:
            page_report = _unmatched_page_report(
                expected_render.pages[index].background_image,
                None,
                diff_path,
                page_number=index + 1,
                mismatch_type="missing_actual_page",
            )
            page_report.update(_source_page_metadata(expected_render.pages[index], None))
            pages.append(page_report)
        else:
            page_report = _unmatched_page_report(
                None,
                actual_render.pages[index].background_image,
                diff_path,
                page_number=index + 1,
                mismatch_type="extra_actual_page",
            )
            page_report.update(_source_page_metadata(None, actual_render.pages[index]))
            pages.append(page_report)

    report = {
        "expected_source": str(expected_source),
        "expected_pdf": str(expected_source),
        "expected_source_type": expected_render.source_type,
        "actual_pdf": str(actual_pdf),
        "max_pages": max_pages,
        "dpi": dpi,
        "actual_render_dpi": actual_dpi,
        "image_dpi": image_dpi if expected_render.source_type == "image" else None,
        "expected_page_indices": list(expected_page_indices) if expected_page_indices is not None else None,
        "actual_page_indices": list(actual_page_indices) if actual_page_indices is not None else None,
        "expected_page_count": expected_page_count,
        "actual_page_count": actual_page_count,
        "compared_page_count": compared_page_count,
        "page_count_match": expected_page_count == actual_page_count,
        "unmatched_page_count": abs(expected_page_count - actual_page_count),
        "pages": pages,
    }
    report.update(_summarize_pages(pages))
    (target / report_filename).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _source_page_metadata(expected: Any, actual: Any) -> dict[str, int | None]:
    expected_index = getattr(expected, "page_index", None)
    actual_index = getattr(actual, "page_index", None)
    return {
        "expected_source_page_index": int(expected_index) if expected_index is not None else None,
        "expected_source_page_number": int(expected_index) + 1 if expected_index is not None else None,
        "actual_source_page_index": int(actual_index) if actual_index is not None else None,
        "actual_source_page_number": int(actual_index) + 1 if actual_index is not None else None,
    }


def compare_images(
    expected_path: Path,
    actual_path: Path,
    diff_path: Path,
    page_number: int | None = None,
) -> dict[str, Any]:
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(expected_path) as expected_raw, Image.open(actual_path) as actual_raw:
        expected = expected_raw.convert("RGB")
        actual = actual_raw.convert("RGB")
        expected_size = expected.size
        actual_size = actual.size
        dimension_match = expected_size == actual_size

        comparison_size = (
            max(expected_size[0], actual_size[0]),
            max(expected_size[1], actual_size[1]),
        )
        expected_canvas = _place_on_canvas(expected, comparison_size)
        actual_canvas = _place_on_canvas(actual, comparison_size)
        diff = ImageChops.difference(expected_canvas, actual_canvas)
        pixel_diff_ratio = _image_diff_ratio(diff)
        dimension_penalty = _dimension_mismatch_penalty(expected_size, actual_size)
        diff_ratio = max(pixel_diff_ratio, dimension_penalty)

        diff.save(diff_path)
        bbox = diff.getbbox()
        result = {
            "expected": str(expected_path),
            "actual": str(actual_path),
            "diff": str(diff_path),
            "page_match": True,
            "dimension_match": dimension_match,
            "mismatch_type": None if dimension_match else "dimension_mismatch",
            "width": expected_size[0],
            "height": expected_size[1],
            "expected_width": expected_size[0],
            "expected_height": expected_size[1],
            "actual_width": actual_size[0],
            "actual_height": actual_size[1],
            "comparison_width": comparison_size[0],
            "comparison_height": comparison_size[1],
            "pixel_diff_ratio": _round_ratio(pixel_diff_ratio),
            "size_mismatch_penalty": _round_ratio(dimension_penalty),
            "diff_ratio": _round_ratio(diff_ratio),
            "has_visual_difference": bbox is not None or not dimension_match,
        }
        if page_number is not None:
            result["page_number"] = page_number
        return result


def _unmatched_page_report(
    expected_path: Path | None,
    actual_path: Path | None,
    diff_path: Path,
    page_number: int,
    mismatch_type: str,
) -> dict[str, Any]:
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path = expected_path or actual_path
    if reference_path is None:
        raise ValueError("An unmatched page report requires an expected or actual image.")

    with Image.open(reference_path) as raw:
        reference = raw.convert("RGB")
        blank = Image.new("RGB", reference.size, "white")
        diff = ImageChops.difference(reference, blank)
        diff.save(diff_path)
        pixel_diff_ratio = _image_diff_ratio(diff)
        return {
            "expected": str(expected_path) if expected_path else None,
            "actual": str(actual_path) if actual_path else None,
            "diff": str(diff_path),
            "page_number": page_number,
            "page_match": False,
            "dimension_match": False,
            "mismatch_type": mismatch_type,
            "width": reference.width if expected_path else None,
            "height": reference.height if expected_path else None,
            "expected_width": reference.width if expected_path else None,
            "expected_height": reference.height if expected_path else None,
            "actual_width": reference.width if actual_path else None,
            "actual_height": reference.height if actual_path else None,
            "comparison_width": reference.width,
            "comparison_height": reference.height,
            "pixel_diff_ratio": _round_ratio(pixel_diff_ratio),
            "size_mismatch_penalty": 1.0,
            "diff_ratio": 1.0,
            "has_visual_difference": True,
        }


def _place_on_canvas(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    if image.size == size:
        return image
    canvas = Image.new("RGB", size, "white")
    canvas.paste(image, (0, 0))
    return canvas


def _image_diff_ratio(diff: Image.Image) -> float:
    histogram = diff.histogram()
    channel_count = len(diff.getbands())
    total = diff.width * diff.height * 255 * channel_count
    diff_sum = sum(value * (index % 256) for index, value in enumerate(histogram))
    return diff_sum / total if total else 0.0


def _dimension_mismatch_penalty(expected_size: tuple[int, int], actual_size: tuple[int, int]) -> float:
    if expected_size == actual_size:
        return 0.0
    expected_width, expected_height = expected_size
    actual_width, actual_height = actual_size
    width_penalty = _relative_delta(expected_width, actual_width)
    height_penalty = _relative_delta(expected_height, actual_height)
    area_penalty = _relative_delta(expected_width * expected_height, actual_width * actual_height)
    return min(1.0, max(width_penalty, height_penalty, area_penalty))


def _relative_delta(left: int, right: int) -> float:
    denominator = max(left, right)
    return abs(left - right) / denominator if denominator else 0.0


def _summarize_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    diff_ratios = [float(page["diff_ratio"]) for page in pages]
    worst_page = None
    if pages:
        worst = max(pages, key=lambda page: float(page["diff_ratio"]))
        worst_page = worst.get("page_number")
    return {
        "max_diff_ratio": _round_ratio(max(diff_ratios, default=0.0)),
        "mean_diff_ratio": _round_ratio(sum(diff_ratios) / len(diff_ratios) if diff_ratios else 0.0),
        "p95_diff_ratio": _round_ratio(_percentile(diff_ratios, 95.0)),
        "worst_page": worst_page,
        "dimension_match": all(bool(page.get("dimension_match")) for page in pages),
        "mismatched_page_count": sum(
            1
            for page in pages
            if not bool(page.get("page_match", True)) or not bool(page.get("dimension_match", True))
        ),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (percentile / 100)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _round_ratio(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 8)
