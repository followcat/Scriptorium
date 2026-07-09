from pathlib import Path
import shutil

import pytest
from PIL import Image, ImageDraw

from scriptorium.annotations import annotate_document
from scriptorium.ocr import normalize_ocr_to_ir
from scriptorium.pdf_render import render_source
from scriptorium.structure_evidence import apply_structure_evidence


def _require_tesseract() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is required for image source OCR fallback coverage.")


def test_image_source_renders_as_first_class_document_source(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")

    rendered = render_source(image_path, tmp_path / "pages", input_kind="auto", image_dpi=96)
    document = normalize_ocr_to_ir(rendered)
    annotate_document(document)

    assert rendered.source_type == "image"
    assert document.source == str(image_path.resolve())
    assert document.source_type == "image"
    assert document.source_path == str(image_path.resolve())
    assert document.page_count == 1
    assert document.pages[0].width_px == 320
    assert document.pages[0].width_pt == 240
    assert len(document.pages[0].elements) == 1
    image_element = document.pages[0].elements[0]
    assert image_element.type == "image"
    assert image_element.source_crop == str(rendered.pages[0].background_image)
    assert image_element.metadata["annotation"]["source_kind"] == "image-source"


def test_image_source_uses_ocr_json_as_text_anchor_layer(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)

    document = normalize_ocr_to_ir(
        rendered,
        {
            "source": "unit-ocr",
            "pages": [
                {
                    "page_index": 0,
                    "elements": [
                        {
                            "type": "text",
                            "bbox_px": [24, 28, 190, 58],
                            "text": "Image source text",
                            "confidence": 0.91,
                        }
                    ],
                }
            ],
        },
    )
    annotate_document(document)
    elements = document.pages[0].elements

    assert [element.type for element in elements] == ["image", "text"]
    assert elements[1].source_text == "Image source text"
    assert elements[1].metadata["annotation"]["source_kind"] == "native-ocr"
    assert elements[1].metadata["annotation"]["editable"] is True
    assert document.metadata["image_source_visual_layer"] is True
    assert document.metadata["semantic_layer"]["driver"] == "ocr-json"
    assert document.metadata["semantic_layer"]["payload_kind"] == "ocr-json"


def test_image_source_can_apply_tesseract_ocr_fallback(tmp_path: Path) -> None:
    _require_tesseract()
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)

    document = normalize_ocr_to_ir(
        rendered,
        ocr_fallback="image-only",
        ocr_language="eng",
        ocr_dpi=200,
    )
    annotate_document(document)
    text_elements = [element for element in document.pages[0].elements if element.source_text.strip()]

    assert document.metadata["page_extraction"][0]["ocr_fallback_status"] == "applied"
    assert document.metadata["semantic_layer"]["driver"] == "ocr-fallback"
    assert text_elements
    assert {element.metadata["annotation"]["source_kind"] for element in text_elements} == {"native-ocr"}
    assert all(element.metadata["ocr_fallback"] is True for element in text_elements)


def test_image_source_can_seed_text_from_structure_json_blocks(tmp_path: Path) -> None:
    image_path = _make_image(tmp_path / "source.png")
    rendered = render_source(image_path, tmp_path / "pages", input_kind="image", image_dpi=96)
    structure_payload = {
        "source": "pp-structurev3",
        "res": {
            "page_index": 0,
            "parsing_res_list": [
                {
                    "block_label": "title",
                    "block_bbox": [24, 28, 190, 58],
                    "block_order": 1,
                    "block_content": "Structure title",
                    "confidence": 0.93,
                }
            ],
        },
    }

    document = normalize_ocr_to_ir(rendered, structure_payload)
    apply_structure_evidence(document, structure_payload)
    annotate_document(document)
    text = next(element for element in document.pages[0].elements if element.source_text)

    assert text.source_text == "Structure title"
    assert text.type == "title"
    assert text.metadata["external_structure_order"] == 1
    assert text.metadata["structure_evidence"]["source"] == "pp-structurev3"
    assert text.metadata["annotation"]["source_kind"] == "native-ocr"
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert document.metadata["semantic_layer"]["structure_json"]["role"] == "semantic-driver"


def _make_image(path: Path) -> Path:
    image = Image.new("RGB", (320, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.text((24, 30), "Image source text", fill=(0, 0, 0))
    image.save(path)
    return path
