from __future__ import annotations

import json
from pathlib import Path

import fitz


def create_fixture(out_dir: str | Path) -> tuple[Path, Path]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    pdf_path = target / "sample.pdf"
    ocr_path = target / "sample.ocr.json"

    doc = fitz.open()
    page = doc.new_page(width=420, height=594)
    page.insert_text((48, 64), "Scriptorium", fontsize=24, fontname="helv", color=(0.05, 0.08, 0.12))
    page.insert_text((50, 108), "OCR structure -> HTML -> editable output", fontsize=12, fontname="helv")
    page.draw_rect(fitz.Rect(48, 146, 372, 250), color=(0.2, 0.36, 0.62), width=1.2)
    page.insert_text((62, 172), "Structured block", fontsize=16, fontname="helv")
    page.insert_text((62, 204), "source_text / edited_text / translated_text", fontsize=10, fontname="helv")
    page.draw_rect(fitz.Rect(48, 288, 372, 430), color=(0.1, 0.45, 0.28), width=1.0)
    for x in (156, 264):
        page.draw_line(fitz.Point(x, 288), fitz.Point(x, 430), color=(0.1, 0.45, 0.28), width=0.8)
    for y in (323, 358, 393):
        page.draw_line(fitz.Point(48, y), fitz.Point(372, y), color=(0.1, 0.45, 0.28), width=0.8)
    page.insert_text((60, 310), "Table", fontsize=11, fontname="helv")
    page.insert_text((170, 310), "OCR", fontsize=11, fontname="helv")
    page.insert_text((278, 310), "HTML", fontsize=11, fontname="helv")
    page.insert_text((60, 345), "Coords", fontsize=10, fontname="helv")
    page.insert_text((170, 345), "bbox", fontsize=10, fontname="helv")
    page.insert_text((278, 345), "CSS px", fontsize=10, fontname="helv")
    doc.save(pdf_path)
    doc.close()

    # Fixture coordinates are authored in PDF points. The converter will map them
    # through the same scale used by PyMuPDF rendering.
    ocr = {
        "source": "fixture",
        "pages": [
            {
                "page_index": 0,
                "elements": [
                    {
                        "type": "title",
                        "bbox_pdf": [48, 42, 282, 74],
                        "text": "Scriptorium",
                        "confidence": 0.99,
                    },
                    {
                        "type": "text",
                        "bbox_pdf": [50, 92, 330, 114],
                        "text": "OCR structure -> HTML -> editable output",
                        "confidence": 0.98,
                    },
                    {
                        "type": "text",
                        "bbox_pdf": [58, 154, 346, 214],
                        "text": "Structured block\nsource_text / edited_text / translated_text",
                        "confidence": 0.96,
                    },
                    {
                        "type": "table",
                        "bbox_pdf": [48, 288, 372, 430],
                        "text": "Table OCR HTML\nCoords bbox CSS px",
                        "confidence": 0.93,
                    },
                ],
            }
        ],
    }
    ocr_path.write_text(json.dumps(ocr, indent=2), encoding="utf-8")
    return pdf_path, ocr_path
