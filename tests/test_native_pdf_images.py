from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from scriptorium.annotations import annotate_document
from scriptorium.html_export import export_html
from scriptorium.native_pdf import extract_native_pdf_to_ir
from scriptorium.pdf_render import render_pdf


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

    assert len(image_elements) == 1
    assert image_elements[0].metadata["source"] == "native-image"
    assert image_elements[0].metadata["annotation"]["role"] == "image"
    assert image_elements[0].source_crop is not None
    assert Path(image_elements[0].source_crop).exists()

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")

    assert 'data-type="image"' in html
    assert 'data-scriptorium-source="native-image"' in html
    assert '<img class="embedded-image"' in html
