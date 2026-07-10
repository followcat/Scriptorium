from __future__ import annotations

from types import SimpleNamespace

from scriptorium.browser_launch import chromium_launch_kwargs, print_html_with_chromium_cli


def test_chromium_launch_kwargs_keep_local_rendering_stable(monkeypatch) -> None:
    monkeypatch.setattr("scriptorium.browser_launch.shutil.which", lambda _name: None)

    kwargs = chromium_launch_kwargs()

    assert kwargs == {
        "headless": True,
        "args": [
            "--no-proxy-server",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-crash-reporter",
        ],
    }


def test_chromium_launch_kwargs_prefer_explicit_executable(monkeypatch) -> None:
    monkeypatch.setattr("scriptorium.browser_launch.shutil.which", lambda _name: "/usr/bin/chromium")

    kwargs = chromium_launch_kwargs("/custom/google-chrome")

    assert kwargs["executable_path"] == "/custom/google-chrome"


def test_chromium_cli_print_uses_local_file_and_print_flags(monkeypatch, tmp_path) -> None:
    html_path = tmp_path / "document.html"
    pdf_path = tmp_path / "output.pdf"
    html_path.write_text("<html><body>Test</body></html>", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr("scriptorium.browser_launch.chromium_executable", lambda _value: "/custom/chrome")

    def fake_run(command, **kwargs):
        commands.append(command)
        assert all(key not in kwargs["env"] for key in ("TMPDIR", "TMP", "TEMP"))
        pdf_path.write_bytes(b"%PDF-test")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("scriptorium.browser_launch.subprocess.run", fake_run)

    result = print_html_with_chromium_cli(html_path, pdf_path)

    assert result == pdf_path.resolve()
    assert commands == [
        [
            "/custom/chrome",
            "--headless",
            "--no-sandbox",
            "--no-proxy-server",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-crash-reporter",
            "--no-first-run",
            "--no-pdf-header-footer",
            "--virtual-time-budget=3000",
            f"--print-to-pdf={pdf_path.resolve()}",
            html_path.resolve().as_uri(),
        ]
    ]
