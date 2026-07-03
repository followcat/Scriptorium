from pathlib import Path

import fitz

from scriptorium.annotations import annotate_document
from scriptorium.html_export import export_html
from scriptorium.native_pdf import _css_font_family, extract_native_pdf_to_ir
from scriptorium.pdf_render import render_pdf


def test_native_pdf_preserves_inline_text_runs(tmp_path: Path) -> None:
    pdf_path = _create_mixed_inline_pdf(tmp_path / "mixed-inline.pdf")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))

    element = next(element for element in document.pages[0].elements if element.source_text.startswith("Normal"))
    runs = element.metadata["text_runs"]

    assert element.source_text == "Normal Bold Big 2"
    assert element.metadata["mixed_inline_style"] is True
    assert element.metadata["annotation"]["mixed_inline_style"] is True
    assert element.metadata["annotation"]["text_run_count"] == len(runs)
    assert len(runs) >= 5
    assert all(run["style_id"].startswith("style-") for run in runs)

    bold_run = next(run for run in runs if run["text"].strip() == "Bold")
    assert bold_run["style"]["font_weight"] == 700

    big_run = next(run for run in runs if run["text"] == "Big")
    assert big_run["style"]["font_size_px"] > element.style_hint["font_size_px"]

    superscript_run = next(run for run in runs if run["text"] == "2")
    assert superscript_run["script"] == "superscript"
    assert superscript_run["style"]["vertical_align"] == "super"


def test_structured_html_renders_inline_runs_until_element_is_edited(tmp_path: Path) -> None:
    pdf_path = _create_mixed_inline_pdf(tmp_path / "mixed-inline.pdf")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")

    assert 'class="text-run"' in html
    assert 'data-scriptorium-run-index="0"' in html
    assert 'data-scriptorium-run-style-id="style-' in html
    assert "font-weight: 700" in html
    assert "vertical-align: super" in html

    element = next(element for element in document.pages[0].elements if element.source_text.startswith("Normal"))
    element.edited_text = "Edited plain text"
    edited_html_path = export_html(document, tmp_path / "edited-html", display_mode="structured")
    edited_html = edited_html_path.read_text(encoding="utf-8")

    assert 'class="text-run"' not in edited_html
    assert "Edited plain text" in edited_html


def test_common_pdf_fonts_map_to_closer_browser_families() -> None:
    assert _css_font_family("AECCXO+NimbusRomNo9L-Regu").startswith("Times New Roman")
    assert _css_font_family("RCUMTF+NimbusRomNo9L-Medi").startswith("Times New Roman")
    assert _css_font_family("XEXHSJ+SFTT1000").startswith("Courier New")
    assert _css_font_family("FUIULY+CMR10").startswith("Times New Roman")
    assert _css_font_family("LICAEO+CMMI10").startswith("Cambria Math")
    assert _css_font_family("AECCXO+NimbusRomNo9L-Regu", font_profile="local-urw").startswith("Nimbus Roman")
    assert _css_font_family("XEXHSJ+SFTT1000", font_profile="local-urw").startswith("Nimbus Mono")
    assert _css_font_family("FUIULY+CMR10", font_profile="local-urw").startswith("DejaVu Math TeX Gyre")


def test_native_pdf_records_font_profile(tmp_path: Path) -> None:
    pdf_path = _create_mixed_inline_pdf(tmp_path / "font-profile.pdf")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = extract_native_pdf_to_ir(rendered, font_profile="local-urw")

    assert document.metadata["font_profile"] == "local-urw"
    assert document.revisions[-1].payload["font_profile"] == "local-urw"


def _create_mixed_inline_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((72, 80), "Normal ", fontsize=12, fontname="helv")
    page.insert_text((114, 80), "Bold ", fontsize=12, fontname="Helvetica-Bold")
    page.insert_text((145, 80), "Big", fontsize=18, fontname="helv")
    page.insert_text((176, 74), "2", fontsize=8, fontname="helv")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path
