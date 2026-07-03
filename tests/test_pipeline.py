from pathlib import Path

from scriptorium.fixture import create_fixture
from scriptorium.html_export import export_html
from scriptorium.models import DocumentIR, RevisionIR
from scriptorium.ocr import load_ocr_json, normalize_ocr_to_ir
from scriptorium.pdf_render import render_pdf


def test_fixture_pipeline_exports_html(tmp_path: Path) -> None:
    pdf_path, ocr_path = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = normalize_ocr_to_ir(rendered, load_ocr_json(ocr_path), crop_dir=tmp_path / "crops")

    assert document.page_count == 1
    assert len(document.pages[0].elements) == 4
    assert document.pages[0].elements[0].source_text == "Scriptorium PDF"
    assert document.pages[0].elements[0].bbox_px.width > document.pages[0].elements[0].bbox_pdf.width

    ir_path = tmp_path / "document.ir.json"
    document.save(ir_path)
    reloaded = DocumentIR.load(ir_path)
    assert reloaded.pages[0].elements[0].source_text == "Scriptorium PDF"

    html_path = export_html(reloaded, tmp_path / "html", display_mode="background")
    html = html_path.read_text(encoding="utf-8")
    assert "mode-background" in html
    assert "page_0001.png" in html


def test_fidelity_html_keeps_background_with_editable_overlay(tmp_path: Path) -> None:
    pdf_path, ocr_path = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144, include_svg_background=True)
    document = normalize_ocr_to_ir(rendered, load_ocr_json(ocr_path), crop_dir=tmp_path / "crops")

    html_path = export_html(document, tmp_path / "fidelity-html", display_mode="fidelity")
    html = html_path.read_text(encoding="utf-8")

    assert "mode-fidelity" in html
    assert "page_0001.svg" in html
    assert 'data-scriptorium-editable="true"' in html
    assert 'contenteditable="true"' in html
    assert "color: transparent !important" in html


def test_edit_and_translation_do_not_overwrite_source(tmp_path: Path) -> None:
    pdf_path, ocr_path = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = normalize_ocr_to_ir(rendered, load_ocr_json(ocr_path))
    element = document.pages[0].elements[0]

    element.edited_text = "Edited title"
    element.translated_text = "Titre traduit"
    document.revisions.append(RevisionIR(reason="test-edit", payload={"element_id": element.id}))

    assert element.source_text == "Scriptorium PDF"
    assert element.text_for_mode("source") == "Scriptorium PDF"
    assert element.text_for_mode("edited") == "Edited title"
    assert element.text_for_mode("translated") == "Titre traduit"
    assert document.revisions[-1].reason == "test-edit"
