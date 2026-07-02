from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def print_html_to_pdf(
    html_path: str | Path,
    pdf_path: str | Path,
    chrome_executable: str | None = None,
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

    return target
