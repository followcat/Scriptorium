from pathlib import Path

import fitz

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
    assert "Scriptorium" in texts
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
    assert "text-align-last: justify" in html
    assert "Scriptorium" in html


def test_structured_html_can_render_svg_text_fit_layer(tmp_path: Path) -> None:
    pdf_path, _ = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))

    html_path = export_html(document, tmp_path / "html", display_mode="structured", text_fit="svg")
    html = html_path.read_text(encoding="utf-8")

    assert 'data-scriptorium-text-fit="svg"' in html
    assert '<svg class="text-fit-layer"' in html
    assert 'lengthAdjust="spacingAndGlyphs"' in html
    assert 'class="editable-text-proxy" contenteditable="true"' in html
    assert "Scriptorium" in html


def test_structured_html_renders_native_line_shapes_as_svg(tmp_path: Path) -> None:
    pdf_path = tmp_path / "line_fixture.pdf"
    doc = fitz.open()
    page = doc.new_page(width=220, height=180)
    page.draw_line(fitz.Point(40, 132), fitz.Point(172, 42), color=(0.1, 0.55, 0.25), width=2.5)
    page.insert_text((36, 154), "Diagonal line", fontsize=11, fontname="helv")
    doc.save(pdf_path)
    doc.close()

    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))
    line_shapes = [
        element
        for element in document.pages[0].elements
        if element.type == "shape" and element.metadata.get("shape_geometry") == "line"
    ]

    assert len(line_shapes) == 1
    assert line_shapes[0].metadata["line_points_pdf"] is not None

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")

    assert 'data-scriptorium-shape-geometry="line"' in html
    assert '<svg class="shape-line"' in html
    assert "<line " in html


def test_structured_html_renders_native_path_shapes_as_svg(tmp_path: Path) -> None:
    pdf_path = tmp_path / "path_fixture.pdf"
    doc = fitz.open()
    page = doc.new_page(width=220, height=180)
    page.draw_polyline(
        [fitz.Point(42, 42), fitz.Point(168, 64), fitz.Point(96, 132)],
        color=(0.05, 0.25, 0.65),
        fill=(0.7, 0.82, 0.95),
        width=1.25,
        closePath=True,
    )
    page.insert_text((42, 156), "Polygon shape", fontsize=11, fontname="helv")
    doc.save(pdf_path)
    doc.close()

    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))
    path_shapes = [
        element
        for element in document.pages[0].elements
        if element.type == "shape" and element.metadata.get("svg_path_pdf")
    ]

    assert len(path_shapes) == 1
    assert path_shapes[0].metadata["drawing_item_count"] >= 3
    assert "L " in path_shapes[0].metadata["svg_path_pdf"]

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")

    assert 'data-scriptorium-shape-path="true"' in html
    assert '<svg class="shape-vector"' in html
    assert "<path " in html


def test_dense_vector_region_uses_local_raster_fallback(tmp_path: Path) -> None:
    pdf_path = tmp_path / "dense_vector_fixture.pdf"
    doc = fitz.open()
    page = doc.new_page(width=360, height=300)
    for index in range(130):
        x0 = 42 + (index % 26) * 10
        x1 = 48 + ((index * 7) % 26) * 10
        color = (0.1, 0.5, 0.2) if index % 2 else (0.75, 0.1, 0.1)
        page.draw_line(fitz.Point(x0, 72), fitz.Point(x1, 190), color=color, width=1.4)
    page.insert_text((52, 118), "inside dense vector chart", fontsize=10, fontname="helv")
    page.insert_text((52, 236), "Figure caption remains editable.", fontsize=11, fontname="helv")
    doc.save(pdf_path)
    doc.close()

    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))
    page_ir = document.pages[0]
    raster_regions = [
        element
        for element in page_ir.elements
        if element.type == "image" and element.metadata.get("source") == "native-raster-region"
    ]
    texts = [element.source_text for element in page_ir.elements if element.source_text]

    assert len(raster_regions) == 1
    assert raster_regions[0].metadata["rasterized_shape_count"] >= 120
    assert "inside dense vector chart" not in texts
    assert "Figure caption remains editable." in texts

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")

    assert 'data-scriptorium-source="native-raster-region"' in html
    assert '<img class="embedded-image"' in html


def test_complex_table_region_uses_local_raster_fallback(tmp_path: Path) -> None:
    pdf_path = tmp_path / "complex_table_fixture.pdf"
    doc = fitz.open()
    page = doc.new_page(width=420, height=360)
    x0, y0 = 42, 54
    cell_width, cell_height = 44, 24
    for row in range(6):
        for column in range(5):
            left = x0 + column * cell_width
            top = y0 + row * cell_height
            page.draw_rect(
                fitz.Rect(left, top, left + cell_width, top + cell_height),
                color=(0.08, 0.26, 0.46),
                width=0.7,
            )
            if row < 2 and column < 2:
                page.insert_text((left + 5, top + 16), f"C{row}{column}", fontsize=8, fontname="helv")
    page.insert_text((42, 260), "Table caption remains editable.", fontsize=11, fontname="helv")
    doc.save(pdf_path)
    doc.close()

    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered, raster_policy="tables"))
    page_ir = document.pages[0]
    raster_regions = [
        element
        for element in page_ir.elements
        if element.type == "image" and element.metadata.get("raster_reason") == "complex-table-vector-region"
    ]
    texts = [element.source_text for element in page_ir.elements if element.source_text]

    assert len(raster_regions) == 1
    assert raster_regions[0].metadata["raster_region_kind"] == "table"
    assert raster_regions[0].metadata["rasterized_shape_count"] >= 30
    assert raster_regions[0].metadata["rasterized_text_count"] >= 4
    assert "C00" not in texts
    assert "Table caption remains editable." in texts

    html_path = export_html(document, tmp_path / "html", display_mode="structured")
    html = html_path.read_text(encoding="utf-8")

    assert 'data-scriptorium-source="native-raster-region"' in html
    assert 'data-scriptorium-role="image"' in html


def test_xml_node_edit_updates_only_edited_text(tmp_path: Path) -> None:
    pdf_path, _ = create_fixture(tmp_path / "fixture")
    rendered = render_pdf(pdf_path, tmp_path / "pages", dpi=144)
    document = annotate_document(extract_native_pdf_to_ir(rendered))
    target = document.pages[0].elements[0]

    xml_path = export_document_xml(document, tmp_path / "document.xml")
    set_xml_element_text(xml_path, target.id, "Edited XML Node")
    changed = apply_xml_edits(document, xml_path)

    assert changed == 1
    assert target.source_text == "Scriptorium"
    assert target.edited_text == "Edited XML Node"
    untouched = [element for element in document.pages[0].elements if element.id != target.id]
    assert all(element.edited_text is None for element in untouched)
