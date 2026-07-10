from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


_HEADLESS_CHROMIUM_ARGS = (
    "--no-proxy-server",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-crash-reporter",
)
_CLI_PRINT_VIRTUAL_TIME_BUDGET_MS = 3_000


def chromium_launch_kwargs(chrome_executable: str | None = None) -> dict[str, Any]:
    """Return stable Playwright launch options for local HTML/PDF rendering."""

    executable = chromium_executable(chrome_executable)
    kwargs: dict[str, Any] = {"headless": True, "args": list(_HEADLESS_CHROMIUM_ARGS)}
    if executable:
        kwargs["executable_path"] = executable
    return kwargs


def chromium_executable(chrome_executable: str | None = None) -> str | None:
    return chrome_executable or shutil.which("google-chrome") or shutil.which("chromium")


def print_html_with_chromium_cli(
    html_path: str | Path,
    pdf_path: str | Path,
    chrome_executable: str | None = None,
) -> Path:
    """Print local HTML without Playwright's remote-debugging transport.

    This is a targeted fallback for hosts where Chromium starts normally but
    crashes when Playwright opens its remote-debugging pipe.
    """

    executable = chromium_executable(chrome_executable)
    if not executable:
        raise RuntimeError("Chromium CLI fallback is unavailable: no Chrome/Chromium executable was found.")

    source = Path(html_path).resolve()
    target = Path(pdf_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "--headless",
        "--no-sandbox",
        *_HEADLESS_CHROMIUM_ARGS,
        "--no-first-run",
        "--no-pdf-header-footer",
        # The CLI prints as soon as navigation completes unless virtual time is
        # advanced. Local HTML exports can otherwise race their image assets,
        # producing an intermittent blank first PDF page on a cold cache.
        f"--virtual-time-budget={_CLI_PRINT_VIRTUAL_TIME_BUDGET_MS}",
        f"--print-to-pdf={target}",
        source.as_uri(),
    ]
    environment = dict(os.environ)
    # Python test/benchmark runners can redirect TMPDIR into their output tree.
    # Chromium on some Linux hosts crashes before startup with that inherited
    # directory, while the host default temporary directory works normally.
    for key in ("TMPDIR", "TMP", "TEMP"):
        environment.pop(key, None)
    completed = subprocess.run(command, check=False, capture_output=True, text=True, env=environment)
    if completed.returncode != 0 or not target.is_file():
        details = (completed.stderr or completed.stdout or "Chrome did not produce a PDF.").strip()
        raise RuntimeError(f"Chromium CLI HTML-to-PDF fallback failed: {details}")
    return target
