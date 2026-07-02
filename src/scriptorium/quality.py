from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

from .models import DocumentIR
from .pdf_render import render_pdf


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
        pages.append(compare_images(expected, actual, diff_path))

    report = {
        "page_count": len(document.pages),
        "max_diff_ratio": max((p["diff_ratio"] for p in pages), default=0.0),
        "pages": pages,
    }
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

    executable = chrome_executable or shutil.which("google-chrome") or shutil.which("chromium")
    launch_kwargs: dict[str, Any] = {"headless": True, "args": ["--no-proxy-server"]}
    if executable:
        launch_kwargs["executable_path"] = executable

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
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
) -> dict[str, Any]:
    target = Path(out_dir)
    expected_render = render_pdf(expected_pdf, target / "expected_pages", dpi=dpi)
    actual_render = render_pdf(actual_pdf, target / "actual_pages", dpi=dpi)
    page_count = min(len(expected_render.pages), len(actual_render.pages))

    pages: list[dict[str, Any]] = []
    for index in range(page_count):
        diff_path = target / f"pdf_page_{index + 1:04d}.diff.png"
        pages.append(
            compare_images(
                expected_render.pages[index].background_image,
                actual_render.pages[index].background_image,
                diff_path,
            )
        )

    report = {
        "expected_pdf": str(expected_pdf),
        "actual_pdf": str(actual_pdf),
        "expected_page_count": len(expected_render.pages),
        "actual_page_count": len(actual_render.pages),
        "compared_page_count": page_count,
        "max_diff_ratio": max((p["diff_ratio"] for p in pages), default=0.0),
        "pages": pages,
    }
    (target / "pdf_quality_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def compare_images(expected_path: Path, actual_path: Path, diff_path: Path) -> dict[str, Any]:
    with Image.open(expected_path) as expected_raw, Image.open(actual_path) as actual_raw:
        expected = expected_raw.convert("RGB")
        actual = actual_raw.convert("RGB")
        if expected.size != actual.size:
            actual = actual.resize(expected.size)
        diff = ImageChops.difference(expected, actual)
        diff.save(diff_path)
        histogram = diff.histogram()
        channel_count = len(expected.getbands())
        total = expected.width * expected.height * 255 * channel_count
        diff_sum = sum(value * (index % 256) for index, value in enumerate(histogram))
        diff_ratio = diff_sum / total if total else 0.0
        bbox = diff.getbbox()
        return {
            "expected": str(expected_path),
            "actual": str(actual_path),
            "diff": str(diff_path),
            "width": expected.width,
            "height": expected.height,
            "diff_ratio": round(diff_ratio, 8),
            "has_visual_difference": bbox is not None,
        }
