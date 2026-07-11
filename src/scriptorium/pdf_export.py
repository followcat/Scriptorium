from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import fitz

from .browser_launch import chromium_launch_kwargs, print_html_with_chromium_cli

PageSizePt = tuple[float, float]


def print_html_to_pdf(
    html_path: str | Path,
    pdf_path: str | Path,
    chrome_executable: str | None = None,
    page_sizes_pt: Sequence[PageSizePt] | None = None,
) -> Path:
    source = Path(html_path)
    target = Path(pdf_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        _print_html_with_playwright(source, target, chrome_executable=chrome_executable)
        if not _pdf_has_visible_content(target):
            raise RuntimeError("Playwright produced a visually blank PDF")
    except Exception as playwright_error:
        try:
            target.unlink(missing_ok=True)
            print_html_with_chromium_cli(source, target, chrome_executable=chrome_executable)
        except RuntimeError as fallback_error:
            raise RuntimeError(
                "HTML-to-PDF export failed through both Playwright and the Chromium CLI fallback: "
                f"{playwright_error}"
            ) from fallback_error

    if page_sizes_pt is not None:
        normalize_pdf_page_boxes(target, page_sizes_pt)
        trim_trailing_blank_pages(target, expected_page_count=len(page_sizes_pt))

    return target


def _print_html_with_playwright(
    source: Path,
    target: Path,
    *,
    chrome_executable: str | None,
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for HTML-to-PDF export.") from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_kwargs(chrome_executable))
        try:
            page = browser.new_page(device_scale_factor=1)
            page.goto(source.resolve().as_uri(), wait_until="networkidle")
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
            page.pdf(path=str(target), print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()


def normalize_pdf_page_boxes(
    pdf_path: str | Path,
    page_sizes_pt: Sequence[PageSizePt],
    tolerance: float = 0.01,
) -> bool:
    """Set exported PDF page boxes to known source dimensions without scaling content."""
    target = Path(pdf_path)
    if not page_sizes_pt:
        return False

    changed = False
    temp_path = target.with_name(f"{target.stem}.normalized.tmp{target.suffix}")
    with fitz.open(target) as doc:
        for index, page in enumerate(doc):
            if index >= len(page_sizes_pt):
                break
            width_pt, height_pt = page_sizes_pt[index]
            if width_pt <= 0 or height_pt <= 0:
                continue
            rect = page.rect
            if abs(rect.width - width_pt) <= tolerance and abs(rect.height - height_pt) <= tolerance:
                continue
            page.set_mediabox(fitz.Rect(0, 0, width_pt, height_pt))
            changed = True

        if changed:
            if temp_path.exists():
                temp_path.unlink()
            doc.save(temp_path, garbage=4, deflate=True)

    if changed:
        temp_path.replace(target)
    return changed


def trim_trailing_blank_pages(pdf_path: str | Path, expected_page_count: int) -> bool:
    """Remove browser-added blank tail pages after fixed-size HTML printing."""
    target = Path(pdf_path)
    if expected_page_count <= 0:
        return False

    changed = False
    temp_path = target.with_name(f"{target.stem}.trimmed.tmp{target.suffix}")
    with fitz.open(target) as doc:
        while doc.page_count > expected_page_count and _is_blank_print_artifact_page(doc[doc.page_count - 1]):
            doc.delete_page(doc.page_count - 1)
            changed = True

        if changed:
            if temp_path.exists():
                temp_path.unlink()
            doc.save(temp_path, garbage=4, deflate=True)

    if changed:
        temp_path.replace(target)
    return changed


def _is_blank_print_artifact_page(page: fitz.Page) -> bool:
    if page.get_text().strip():
        return False
    if page.get_images(full=True):
        return False
    annotations = page.annots()
    if annotations is not None and any(True for _ in annotations):
        return False
    drawings = page.get_drawings()
    return all(_is_blank_background_drawing(drawing) for drawing in drawings)


def _pdf_has_visible_content(pdf_path: Path) -> bool:
    """Reject a successful-looking browser print that contains only blank pages.

    On affected Chromium builds Playwright may return without an exception while
    its PDF contains a blank page. The CLI fallback has a different transport
    and is able to render the same local assets after its virtual-time wait.
    """

    if not pdf_path.is_file():
        return False
    try:
        with fitz.open(pdf_path) as document:
            return any(not _is_blank_print_artifact_page(page) for page in document)
    except fitz.FileDataError:
        return False


def _is_blank_background_drawing(drawing: dict[str, Any]) -> bool:
    fill = drawing.get("fill")
    color = drawing.get("color")
    stroke_opacity = drawing.get("stroke_opacity")
    if color is not None and float(stroke_opacity or 1.0) > 0:
        return False
    if fill is None:
        return True
    if not isinstance(fill, (list, tuple)) or len(fill) < 3:
        return False
    return all(float(channel) >= 0.995 for channel in fill[:3])
