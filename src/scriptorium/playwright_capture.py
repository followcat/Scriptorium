from __future__ import annotations

from pathlib import Path
from typing import Literal

from .browser_launch import chromium_launch_kwargs


CaptureMode = Literal["print", "download"]


def capture_pdf(
    source: str | Path,
    pdf_path: str | Path,
    mode: CaptureMode = "print",
    chrome_executable: str | None = None,
) -> Path:
    if mode == "download":
        return download_pdf(source, pdf_path, chrome_executable=chrome_executable)
    return print_page_to_pdf(source, pdf_path, chrome_executable=chrome_executable)


def print_page_to_pdf(
    source: str | Path,
    pdf_path: str | Path,
    chrome_executable: str | None = None,
) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for page-to-PDF capture.") from exc

    target = Path(pdf_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    url = _source_to_url(source)
    with sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_kwargs(chrome_executable))
        try:
            page = browser.new_page(device_scale_factor=1)
            page.goto(url, wait_until="networkidle")
            page.emulate_media(media="print")
            page.pdf(path=str(target), print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()
    return target


def download_pdf(
    source: str | Path,
    pdf_path: str | Path,
    chrome_executable: str | None = None,
) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for PDF download capture.") from exc

    target = Path(pdf_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    url = _source_to_url(source)
    with sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_kwargs(chrome_executable))
        try:
            request_context = p.request.new_context()
            response = request_context.get(url)
            if not response.ok:
                raise RuntimeError(f"Failed to download PDF from {url}: HTTP {response.status}")
            target.write_bytes(response.body())
            request_context.dispose()
        finally:
            browser.close()
    return target


def _source_to_url(source: str | Path) -> str:
    raw = str(source)
    if raw.startswith(("http://", "https://", "file://")):
        return raw
    return Path(raw).resolve().as_uri()
