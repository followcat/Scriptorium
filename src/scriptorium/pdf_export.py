from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import fitz

PageSizePt = tuple[float, float]


def print_html_to_pdf(
    html_path: str | Path,
    pdf_path: str | Path,
    chrome_executable: str | None = None,
    page_sizes_pt: Sequence[PageSizePt] | None = None,
) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for HTML-to-PDF export.") from exc

    source = Path(html_path)
    target = Path(pdf_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    executable = chrome_executable or shutil.which("google-chrome") or shutil.which("chromium")
    launch_kwargs: dict[str, Any] = {"headless": True, "args": ["--no-proxy-server"]}
    if executable:
        launch_kwargs["executable_path"] = executable

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            page = browser.new_page(device_scale_factor=1)
            page.goto(source.resolve().as_uri(), wait_until="networkidle")
            page.pdf(path=str(target), print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()

    if page_sizes_pt is not None:
        normalize_pdf_page_boxes(target, page_sizes_pt)
        trim_trailing_blank_pages(target, expected_page_count=len(page_sizes_pt))

    return target


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
