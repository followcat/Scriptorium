from pathlib import Path
import shutil

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from scriptorium.annotations import annotate_document
from scriptorium.html_export import export_html
from scriptorium.native_pdf import extract_native_pdf_to_ir
from scriptorium.pdf_render import render_pdf


def _require_tesseract() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is required for image-only OCR fallback coverage.")


def _readable_test_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size=size)
    return ImageFont.load_default()


def test_native_pdf_image_blocks_render_as_structured_html(tmp_path: Path) -> None:
    image_path = tmp_path / "figure.png"
    image = Image.new("RGB", (64, 64), (244, 248, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, 56, 56), fill=(116, 185, 255), outline=(21, 67, 96), width=3)
    draw.line((16, 48, 32, 22, 48, 42), fill=(192, 57, 43), width=4)
    image.save(image_path)

    pdf_path = tmp_path / "image_fixture.pdf"
    doc = fitz.open()
    page = doc.new_page(width=240, height=220)
    page.insert_image(fitz.Rect(72, 42, 168, 138), filename=image_path)
    page.insert_text((62, 170), "Figure 1: Embedded PDF image block.", fontsize=11, fontname="helv")
    doc.save(pdf_path)
    doc.close()

    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))
    image_elements = [element for element in document.pages[0].elements if element.type == "image"]
    caption_elements = [
        element
        for element in document.pages[0].elements
        if element.source_text.startswith("Figure 1:")
    ]

    assert len(image_elements) == 1
    assert image_elements[0].metadata["source"] == "native-image"
    assert image_elements[0].metadata["annotation"]["role"] == "image"
    assert image_elements[0].source_crop is not None
    assert Path(image_elements[0].source_crop).exists()
    assert len(caption_elements) == 1
    assert caption_elements[0].metadata["reading_order_caption_type"] == "figure"
    assert caption_elements[0].metadata["reading_order_caption_target_id"] == image_elements[0].id
    assert caption_elements[0].metadata["reading_order_caption_target_kind"] == "figure"
    assert caption_elements[0].metadata["reading_order_caption_target_position"] == "caption-below-target"
    assert "caption-target-proximity" in caption_elements[0].metadata["reading_order_evidence"]
    assert caption_elements[0].metadata["annotation"]["role"] == "caption"

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")

    assert 'data-type="image"' in html
    assert 'data-scriptorium-source="native-image"' in html
    assert 'data-scriptorium-role="caption"' in html
    assert 'data-scriptorium-reading-order-caption="figure"' in html
    assert 'data-scriptorium-caption-target-kind="figure"' in html
    assert f'data-scriptorium-caption-target-id="{image_elements[0].id}"' in html
    assert '<img class="embedded-image"' in html


def test_image_only_pdf_gets_transparent_ocr_edit_anchors(tmp_path: Path) -> None:
    _require_tesseract()
    image_path = tmp_path / "image_only_text.png"
    image = Image.new("RGB", (1200, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.text((82, 96), "HELLO OCR 2026", font=_readable_test_font(86), fill=(0, 0, 0))
    draw.text((86, 236), "IMAGE ONLY PDF", font=_readable_test_font(70), fill=(12, 12, 12))
    image.save(image_path)

    pdf_path = tmp_path / "image_only_text.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=250)
    page.insert_image(fitz.Rect(0, 0, 600, 250), filename=image_path)
    doc.save(pdf_path)
    doc.close()

    rendered = render_pdf(pdf_path, tmp_path / "ocr-pages", dpi=144)
    document = annotate_document(
        extract_native_pdf_to_ir(rendered, ocr_language="eng", ocr_dpi=200)
    )
    page_ir = document.pages[0]
    image_elements = [element for element in page_ir.elements if element.metadata.get("source") == "native-image"]
    ocr_elements = [element for element in page_ir.elements if element.metadata.get("source") == "native-ocr"]
    ocr_text = " ".join(element.source_text.upper() for element in ocr_elements)
    diagnostics = document.metadata["page_extraction"][0]

    assert len(image_elements) == 1
    assert ocr_elements
    assert "HELLO" in ocr_text
    assert "OCR" in ocr_text
    assert diagnostics["native_text_line_count"] == 0
    assert diagnostics["image_only_candidate"] is True
    assert diagnostics["ocr_fallback_status"] == "applied"
    assert diagnostics["ocr_language_used"] == "eng"

    html_path = export_html(document, tmp_path / "ocr-html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")

    assert 'data-scriptorium-source="native-image"' in html
    assert 'data-scriptorium-source="native-ocr"' in html
    assert 'body.mode-structured .element[data-scriptorium-source="native-ocr"]' in html
    assert "-webkit-text-fill-color: transparent" in html
