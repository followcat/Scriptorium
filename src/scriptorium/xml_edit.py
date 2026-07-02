from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from .models import DisplayMode, DocumentIR, RevisionIR


def export_document_xml(
    document: DocumentIR,
    xml_path: str | Path,
    text_mode: DisplayMode = "structured",
) -> Path:
    target = Path(xml_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element(
        "scriptorium-document",
        {
            "id": document.id,
            "source-pdf": document.source_pdf,
            "page-count": str(document.page_count),
        },
    )
    for page in document.pages:
        page_node = ET.SubElement(
            root,
            "page",
            {
                "index": str(page.page_index),
                "width-pt": str(page.width_pt),
                "height-pt": str(page.height_pt),
            },
        )
        for element in page.elements:
            element_node = ET.SubElement(
                page_node,
                "element",
                {
                    "id": element.id,
                    "type": element.type,
                    "reading-order": str(element.reading_order),
                    "x0": str(element.bbox_pdf.x0),
                    "y0": str(element.bbox_pdf.y0),
                    "x1": str(element.bbox_pdf.x1),
                    "y1": str(element.bbox_pdf.y1),
                },
            )
            element_node.text = element.text_for_mode(text_mode)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(target, encoding="utf-8", xml_declaration=True)
    return target


def set_xml_element_text(xml_path: str | Path, element_id: str, text: str) -> None:
    path = Path(xml_path)
    tree = ET.parse(path)
    root = tree.getroot()
    for element_node in root.findall(".//element"):
        if element_node.get("id") == element_id:
            element_node.text = text
            ET.indent(root, space="  ")
            tree.write(path, encoding="utf-8", xml_declaration=True)
            return
    raise KeyError(element_id)


def apply_xml_edits(
    document: DocumentIR,
    xml_path: str | Path,
    target_field: str = "edited_text",
) -> int:
    tree = ET.parse(xml_path)
    changed = 0
    for element_node in tree.getroot().findall(".//element"):
        element_id = element_node.get("id")
        if not element_id:
            continue
        try:
            element = document.find_element(element_id)
        except KeyError:
            continue
        new_text = element_node.text or ""
        old_text = element.edited_text if target_field == "edited_text" else element.translated_text
        if new_text != (old_text or element.source_text):
            if target_field == "translated_text":
                element.translated_text = new_text
            else:
                element.edited_text = new_text
            changed += 1
    if changed:
        document.revisions.append(RevisionIR(reason="apply-xml-edits", payload={"changed": changed, "field": target_field}))
    return changed
