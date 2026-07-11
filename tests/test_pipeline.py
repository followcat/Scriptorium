from pathlib import Path

import fitz
from PIL import Image

from scriptorium.fixture import create_fixture
from scriptorium.html_export import export_html, page_replacement_geometries
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


def test_document_ir_keeps_legacy_source_pdf_compatibility(tmp_path: Path) -> None:
    background = tmp_path / "page.png"
    background.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082"
        )
    )
    document = DocumentIR(
        source_pdf="legacy.pdf",
        render_dpi=72,
        page_count=1,
        pages=[
            PageIR(
                page_index=0,
                width_pt=100,
                height_pt=100,
                width_px=100,
                height_px=100,
                render_dpi=72,
                scale_x=1,
                scale_y=1,
                background_image=str(background),
            )
        ],
    )

    ir_path = tmp_path / "legacy.ir.json"
    document.save(ir_path)
    reloaded = DocumentIR.load(ir_path)

    assert reloaded.source == "legacy.pdf"
    assert reloaded.source_path == "legacy.pdf"
    assert reloaded.source_pdf == "legacy.pdf"


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
    assert 'data-scriptorium-source-text="Scriptorium"' in html
    assert "scriptorium-html-edits/v1" in html
    assert "window.ScriptoriumEdits" in html
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

    assert 'data-scriptorium-replacement-policy="fidelity-replacement-fit-v3-browser"' in html
    assert 'data-scriptorium-replacement-fit-scale="0.' in html
    assert 'data-scriptorium-replacement-mask-padding="1.68,0.75,1.68,2.52"' in html
    assert 'data-scriptorium-replacement-conflict="true"' in html
    assert 'data-scriptorium-replacement-conflict-ids=""' in html
    assert 'data-scriptorium-replacement-padding-constrained="true"' in html
    assert 'data-scriptorium-replacement-padding-constraint-ids="neighbor"' in html
    assert 'data-scriptorium-replacement-padding-constraints="right:neighbor"' in html
    assert 'data-scriptorium-replacement-estimated-overflow="true"' in html
    assert 'data-scriptorium-replacement-rendered-fit-policy=""' in html
    assert 'data-scriptorium-replacement-mask-color="#fff"' in html
    assert 'data-scriptorium-replacement-mask-color-source="white-default"' in html
    assert "window.ScriptoriumFitting" in html
    assert "browser-layout-v1" in html
    assert "left: 7.480px;" in html
    assert "--replacement-padding-left: 2.52px;" in html
    assert "A much longer translated replacement line" in html
    assert ">Buy now</span>" not in html


def test_fidelity_replacement_clamps_vertical_padding_at_adjacent_text() -> None:
    replacement = ElementIR(
        id="replace",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=10, y0=10, x1=90, y1=24),
        bbox_px=BBox(x0=10, y0=10, x1=90, y1=24),
        source_text="Source",
        translated_text="Expanded translated source",
        style_hint={"font_size_px": 14, "line_height": 1.1, "font_family": "Arial"},
    )
    below = ElementIR(
        id="below",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=10, y0=24.1, x1=90, y1=38),
        bbox_px=BBox(x0=10, y0=24.1, x1=90, y1=38),
        source_text="Adjacent source",
        style_hint={"font_size_px": 14, "line_height": 1.1, "font_family": "Arial"},
    )
    page = PageIR(
        page_index=0,
        width_pt=120,
        height_pt=80,
        width_px=120,
        height_px=80,
        render_dpi=72,
        scale_x=1,
        scale_y=1,
        background_image="page.png",
        elements=[replacement, below],
    )

    geometry = page_replacement_geometries(page, "fidelity")["replace"]

    assert geometry["padding_bottom"] == 0
    assert geometry["padding_top"] == 1.68
    assert geometry["padding_constrained"] is True
    assert geometry["padding_constraint_ids"] == ["below"]
    assert geometry["padding_constraint_summary"] == "bottom:below"
    assert geometry["mask_bbox"]["y1"] == 24


def test_fidelity_replacement_uses_dark_edge_sample_for_light_text(tmp_path: Path) -> None:
    background = tmp_path / "dark-page.png"
    Image.new("RGB", (120, 80), "black").save(background)
    replacement = ElementIR(
        id="replace",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=20, y0=20, x1=100, y1=36),
        bbox_px=BBox(x0=20, y0=20, x1=100, y1=36),
        source_text="Original",
        translated_text="Translated replacement",
        style_hint={"font_size_px": 12, "line_height": 1.1, "text_color": "rgb(255, 255, 255)"},
    )
    page = PageIR(
        page_index=0,
        width_pt=120,
        height_pt=80,
        width_px=120,
        height_px=80,
        render_dpi=72,
        scale_x=1,
        scale_y=1,
        background_image=str(background),
        elements=[replacement],
    )

    geometry = page_replacement_geometries(page, "fidelity")["replace"]

    assert geometry["mask_color"] == "rgb(0, 0, 0)"
    assert geometry["mask_color_source"] == "edge-sampled-dark-background"


def test_fidelity_print_coordinates_convert_render_pixels_to_css_pixels(tmp_path: Path) -> None:
    background = tmp_path / "page.png"
    Image.new("RGB", (240, 160), "white").save(background)
    replacement = ElementIR(
        id="replace",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=15, y0=12, x1=75, y1=24),
        bbox_px=BBox(x0=30, y0=24, x1=150, y1=48),
        source_text="Source",
        translated_text="Translated",
        style_hint={"font_size_px": 18, "line_height": 1.1, "text_color": "rgb(0, 0, 0)"},
    )
    document = DocumentIR(
        source_pdf="synthetic.pdf",
        render_dpi=144,
        page_count=1,
        pages=[
            PageIR(
                page_index=0,
                width_pt=120,
                height_pt=80,
                width_px=240,
                height_px=160,
                render_dpi=144,
                scale_x=2,
                scale_y=2,
                background_image=str(background),
                elements=[replacement],
            )
        ],
    )

    html = export_html(document, tmp_path / "print-scale", display_mode="fidelity").read_text(encoding="utf-8")

    # Fidelity masks include the conservative local padding before conversion
    # from source render pixels to the 96-DPI print coordinate system.
    assert "--print-left: 17.840px;" in html
    assert "--print-top: 14.560px;" in html
    assert "--print-width: 84.320px;" in html
    assert "--print-height: 18.880px;" in html
    assert "--print-font-size: 12.0000px;" in html


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
