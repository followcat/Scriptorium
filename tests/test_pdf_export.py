from pathlib import Path

import fitz

from scriptorium.pdf_export import normalize_pdf_page_boxes, print_html_to_pdf, trim_trailing_blank_pages


def test_normalize_pdf_page_boxes_sets_source_dimensions(tmp_path: Path) -> None:
    pdf_path = tmp_path / "page.pdf"
    doc = fitz.open()
    doc.new_page(width=100, height=120)
    doc.save(pdf_path)

    changed = normalize_pdf_page_boxes(pdf_path, [(123.5, 98.25)])

    assert changed is True
    with fitz.open(pdf_path) as normalized:
        rect = normalized[0].rect
        assert round(rect.width, 2) == 123.5
        assert round(rect.height, 2) == 98.25

    unchanged = normalize_pdf_page_boxes(pdf_path, [(123.5, 98.25)])

    assert unchanged is False


def test_trim_trailing_blank_pages_removes_browser_blank_tail(tmp_path: Path) -> None:
    pdf_path = tmp_path / "extra_blank.pdf"
    doc = fitz.open()
    doc.new_page(width=100, height=120).insert_text((12, 24), "Real page", fontsize=10)
    blank = doc.new_page(width=100, height=120)
    blank.draw_rect(blank.rect, color=None, fill=(1, 1, 1), width=0)
    doc.save(pdf_path)
    doc.close()

    changed = trim_trailing_blank_pages(pdf_path, expected_page_count=1)

    assert changed is True
    with fitz.open(pdf_path) as trimmed:
        assert trimmed.page_count == 1
        assert "Real page" in trimmed[0].get_text()


def test_trim_trailing_blank_pages_keeps_nonblank_tail(tmp_path: Path) -> None:
    pdf_path = tmp_path / "extra_nonblank.pdf"
    doc = fitz.open()
    doc.new_page(width=100, height=120).insert_text((12, 24), "Real page", fontsize=10)
    doc.new_page(width=100, height=120).insert_text((12, 24), "Overflow page", fontsize=10)
    doc.save(pdf_path)
    doc.close()

    changed = trim_trailing_blank_pages(pdf_path, expected_page_count=1)

    assert changed is False
    with fitz.open(pdf_path) as unchanged:
        assert unchanged.page_count == 2


def test_print_html_to_pdf_uses_chromium_cli_after_playwright_failure(monkeypatch, tmp_path: Path) -> None:
    html_path = tmp_path / "document.html"
    pdf_path = tmp_path / "document.pdf"
    html_path.write_text("<html><body>Fallback</body></html>", encoding="utf-8")

    def fake_playwright(*_args, **_kwargs) -> None:
        raise RuntimeError("remote-debugging pipe crashed")

    def fake_cli(source: Path, target: Path, *, chrome_executable: str | None) -> Path:
        assert source == html_path
        assert target == pdf_path
        assert chrome_executable is None
        document = fitz.open()
        document.new_page(width=100, height=120)
        document.save(target)
        document.close()
        return target

    monkeypatch.setattr("scriptorium.pdf_export._print_html_with_playwright", fake_playwright)
    monkeypatch.setattr("scriptorium.pdf_export.print_html_with_chromium_cli", fake_cli)

    result = print_html_to_pdf(html_path, pdf_path)

    assert result == pdf_path
    assert pdf_path.is_file()


def test_print_html_to_pdf_uses_chromium_cli_after_blank_playwright_output(monkeypatch, tmp_path: Path) -> None:
    html_path = tmp_path / "document.html"
    pdf_path = tmp_path / "document.pdf"
    html_path.write_text("<html><body>Fallback</body></html>", encoding="utf-8")
    calls: list[str] = []

    def fake_playwright(_source: Path, target: Path, **_kwargs) -> None:
        calls.append("playwright")
        document = fitz.open()
        document.new_page(width=100, height=120)
        document.save(target)
        document.close()

    def fake_cli(_source: Path, target: Path, **_kwargs) -> Path:
        calls.append("cli")
        document = fitz.open()
        page = document.new_page(width=100, height=120)
        page.insert_text((12, 24), "Fallback", fontsize=10)
        document.save(target)
        document.close()
        return target

    monkeypatch.setattr("scriptorium.pdf_export._print_html_with_playwright", fake_playwright)
    monkeypatch.setattr("scriptorium.pdf_export.print_html_with_chromium_cli", fake_cli)

    result = print_html_to_pdf(html_path, pdf_path)

    assert result == pdf_path
    assert calls == ["playwright", "cli"]
    with fitz.open(pdf_path) as exported:
        assert "Fallback" in exported[0].get_text()
