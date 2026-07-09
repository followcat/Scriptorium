from pathlib import Path

import fitz

from scriptorium.fixture import create_fixture
from scriptorium.html_export import export_html
from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR, RevisionIR
from scriptorium.ocr import load_ocr_json, normalize_ocr_to_ir
from scriptorium.pdf_export import print_html_to_pdf
from scriptorium.pdf_render import render_pdf


def test_fixture_pipeline_exports_html(tmp_path: Path) -> None:
    pdf_path, ocr_path = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = normalize_ocr_to_ir(rendered, load_ocr_json(ocr_path), crop_dir=tmp_path / "crops")

    assert document.page_count == 1
    assert len(document.pages[0].elements) == 4
    assert document.pages[0].elements[0].source_text == "Scriptorium"
    assert document.pages[0].elements[0].bbox_px.width > document.pages[0].elements[0].bbox_pdf.width

    ir_path = tmp_path / "document.ir.json"
    document.save(ir_path)
    reloaded = DocumentIR.load(ir_path)
    assert reloaded.pages[0].elements[0].source_text == "Scriptorium"

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
    assert 'data-scriptorium-translation-target="translated_text"' in html
    assert "data-scriptorium-translation-stream-id" in html
    assert 'contenteditable="true"' in html
    assert "color: transparent !important" in html


def test_fidelity_html_can_use_raster_background(tmp_path: Path) -> None:
    pdf_path, ocr_path = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144, include_svg_background=True)
    document = normalize_ocr_to_ir(rendered, load_ocr_json(ocr_path), crop_dir=tmp_path / "crops")

    html_path = export_html(
        document,
        tmp_path / "fidelity-raster-html",
        display_mode="fidelity",
        fidelity_background="raster",
    )
    html = html_path.read_text(encoding="utf-8")

    assert "mode-fidelity" in html
    assert "page_0001.png" in html
    assert "page_0001.svg" not in html
    assert 'data-scriptorium-editable="true"' in html


def test_fidelity_prints_edited_replacement_overlay(tmp_path: Path) -> None:
    pdf_path, ocr_path = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144, include_svg_background=True)
    document = normalize_ocr_to_ir(rendered, load_ocr_json(ocr_path), crop_dir=tmp_path / "crops")
    document.pages[0].elements[0].edited_text = "Edited title"

    html_path = export_html(document, tmp_path / "fidelity-html", display_mode="fidelity")
    html = html_path.read_text(encoding="utf-8")
    printed_pdf = print_html_to_pdf(html_path, tmp_path / "fidelity-edited.pdf")

    assert 'data-scriptorium-has-replacement="true"' in html
    assert "Edited title" in html

    with fitz.open(printed_pdf) as doc:
        printed_text = "\n".join(page.get_text() for page in doc)
    assert "Edited title" in printed_text


def test_fidelity_replacement_exports_fit_mask_and_conflict_metadata(tmp_path: Path) -> None:
    background = tmp_path / "page.png"
    background.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082"
        )
    )
    replacement = ElementIR(
        id="replace",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=10, y0=10, x1=90, y1=24),
        bbox_px=BBox(x0=10, y0=10, x1=90, y1=24),
        source_text="Buy now",
        translated_text="A much longer translated replacement line",
        style_hint={"font_size_px": 14, "line_height": 1.1, "font_family": "Arial"},
    )
    neighbor = ElementIR(
        id="neighbor",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=91, y0=10, x1=130, y1=24),
        bbox_px=BBox(x0=91, y0=10, x1=130, y1=24),
        source_text="Next",
        style_hint={"font_size_px": 14, "line_height": 1.1, "font_family": "Arial"},
    )
    document = DocumentIR(
        source_pdf="synthetic.pdf",
        render_dpi=72,
        page_count=1,
        pages=[
            PageIR(
                page_index=0,
                width_pt=160,
                height_pt=80,
                width_px=160,
                height_px=80,
                render_dpi=72,
                scale_x=1,
                scale_y=1,
                background_image=str(background),
                elements=[replacement, neighbor],
            )
        ],
    )

    html_path = export_html(document, tmp_path / "fidelity-replacement", display_mode="fidelity")
    html = html_path.read_text(encoding="utf-8")

    assert 'data-scriptorium-replacement-policy="fidelity-replacement-fit-v1"' in html
    assert 'data-scriptorium-replacement-fit-scale="0.' in html
    assert 'data-scriptorium-replacement-mask-padding="1.68,2.52,1.68,2.52"' in html
    assert 'data-scriptorium-replacement-conflict="true"' in html
    assert 'data-scriptorium-replacement-conflict-ids="neighbor"' in html
    assert "left: 7.480px;" in html
    assert "--replacement-padding-left: 2.52px;" in html
    assert "A much longer translated replacement line" in html
    assert ">Buy now</span>" not in html


def test_edit_and_translation_do_not_overwrite_source(tmp_path: Path) -> None:
    pdf_path, ocr_path = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = normalize_ocr_to_ir(rendered, load_ocr_json(ocr_path))
    element = document.pages[0].elements[0]

    element.edited_text = "Edited title"
    element.translated_text = "Titre traduit"
    document.revisions.append(RevisionIR(reason="test-edit", payload={"element_id": element.id}))

    assert element.source_text == "Scriptorium"
    assert element.text_for_mode("source") == "Scriptorium"
    assert element.text_for_mode("edited") == "Edited title"
    assert element.text_for_mode("translated") == "Titre traduit"
    assert element.text_for_mode("fidelity") == "Titre traduit"
    assert document.revisions[-1].reason == "test-edit"
