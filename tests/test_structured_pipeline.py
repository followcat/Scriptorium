from pathlib import Path

from scriptorium.annotations import annotate_document
from scriptorium.fixture import create_fixture
from scriptorium.html_export import export_html
from scriptorium.native_pdf import extract_native_pdf_to_ir
from scriptorium.pdf_render import render_pdf
from scriptorium.xml_edit import apply_xml_edits, export_document_xml, set_xml_element_text


def test_native_pdf_extraction_produces_structured_elements(tmp_path: Path) -> None:
    pdf_path, _ = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))

    texts = [element.source_text for element in document.pages[0].elements]
    text_elements = [element for element in document.pages[0].elements if element.source_text]
    assert "Scriptorium PDF" in texts
    assert any("OCR structure" in text for text in texts)
    assert all(element.metadata["source"] == "native-pdf" for element in text_elements)
    assert any(element.type == "shape" for element in document.pages[0].elements)
    assert document.metadata["annotation_version"] == "v2"
    assert any(element.metadata["annotation"]["role"] == "heading" for element in text_elements)
    assert any(element.metadata["annotation"]["style_id"].startswith("style-") for element in text_elements)


def test_structured_html_uses_editable_nodes_without_page_image(tmp_path: Path) -> None:
    pdf_path, _ = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")

    assert 'class="page-bg"' not in html
    assert 'contenteditable="true"' in html
    assert 'contenteditable="false"' in html
    assert 'data-xml-node="element"' in html
    assert 'data-scriptorium-role="heading"' in html
    assert 'data-scriptorium-style-id="style-' in html
    assert 'data-scriptorium-source="native-pdf"' in html
    assert 'data-scriptorium-source="native-drawing"' in html
    assert 'data-scriptorium-layout-kind="table"' in html
    assert 'data-scriptorium-layout-confidence="' in html
    assert 'data-scriptorium-edit-target="edited_text"' in html
    assert 'data-bbox-pdf="' in html
    assert "Scriptorium PDF" in html


def test_xml_node_edit_updates_only_edited_text(tmp_path: Path) -> None:
    pdf_path, _ = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))
    target = document.pages[0].elements[0]

    xml_path = export_document_xml(document, tmp_path / "document.xml")
    set_xml_element_text(xml_path, target.id, "Edited XML Node")
    changed = apply_xml_edits(document, xml_path)

    assert changed == 1
    assert target.source_text == "Scriptorium PDF"
    assert target.edited_text == "Edited XML Node"
    untouched = [element for element in document.pages[0].elements if element.id != target.id]
    assert all(element.edited_text is None for element in untouched)
