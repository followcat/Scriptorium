from __future__ import annotations

import json
from pathlib import Path

import pytest

from scriptorium.html_edits import HTML_EDIT_PATCH_FORMAT, apply_html_edit_patch, load_html_edit_patch
from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR


def _document() -> DocumentIR:
    return DocumentIR(
        id="browser-edit-document",
        source="fixture.pdf",
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
                background_image="fixture.png",
                elements=[
                    ElementIR(
                        id="title",
                        page_index=0,
                        type="title",
                        bbox_pdf=BBox(x0=10, y0=10, x1=100, y1=24),
                        bbox_px=BBox(x0=10, y0=10, x1=100, y1=24),
                        source_text="Original title",
                    ),
                    ElementIR(
                        id="body",
                        page_index=0,
                        type="text",
                        bbox_pdf=BBox(x0=10, y0=30, x1=140, y1=44),
                        bbox_px=BBox(x0=10, y0=30, x1=140, y1=44),
                        source_text="Original body",
                    ),
                ],
            )
        ],
    )


def test_apply_html_edit_patch_preserves_source_and_targets_fields() -> None:
    document = _document()
    patch = {
        "format": HTML_EDIT_PATCH_FORMAT,
        "document_id": document.id,
        "edits": [
            {
                "element_id": "title",
                "target": "edited_text",
                "text": "Edited title",
                "source_text": "Original title",
            },
            {
                "element_id": "body",
                "target": "translated_text",
                "text": "Translated body",
                "source_text": "Original body",
            },
        ],
    }

    changed = apply_html_edit_patch(document, patch)

    title = document.find_element("title")
    body = document.find_element("body")
    assert changed == 2
    assert title.source_text == "Original title"
    assert title.edited_text == "Edited title"
    assert body.source_text == "Original body"
    assert body.translated_text == "Translated body"
    assert document.revisions[-1].reason == "apply-html-edits"
    assert document.revisions[-1].payload["target_counts"] == {"edited_text": 1, "translated_text": 1}


def test_html_edit_patch_validation_is_atomic() -> None:
    document = _document()
    patch = {
        "format": HTML_EDIT_PATCH_FORMAT,
        "document_id": document.id,
        "edits": [
            {
                "element_id": "title",
                "target": "edited_text",
                "text": "Edited title",
                "source_text": "Original title",
            },
            {
                "element_id": "missing",
                "target": "translated_text",
                "text": "Missing",
                "source_text": "Missing",
            },
        ],
    }

    with pytest.raises(ValueError, match="unknown element"):
        apply_html_edit_patch(document, patch)

    assert document.find_element("title").edited_text is None
    assert not document.revisions


def test_html_edit_patch_rejects_stale_identity_and_source() -> None:
    document = _document()
    patch = {
        "format": HTML_EDIT_PATCH_FORMAT,
        "document_id": "other-document",
        "edits": [
            {
                "element_id": "title",
                "target": "edited_text",
                "text": "Edited title",
                "source_text": "Stale title",
            }
        ],
    }

    with pytest.raises(ValueError, match="document_id"):
        apply_html_edit_patch(document, patch)
    with pytest.raises(ValueError, match="source text"):
        apply_html_edit_patch(document, patch, require_document_id=False)


def test_load_html_edit_patch_normalizes_json(tmp_path: Path) -> None:
    path = tmp_path / "browser.edits.json"
    path.write_text(
        json.dumps(
            {
                "format": HTML_EDIT_PATCH_FORMAT,
                "document_id": "browser-edit-document",
                "edits": [
                    {"element_id": "title", "target": "edited_text", "text": "Edited title"},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_html_edit_patch(path) == {
        "format": HTML_EDIT_PATCH_FORMAT,
        "document_id": "browser-edit-document",
        "edits": [{"element_id": "title", "target": "edited_text", "text": "Edited title"}],
    }
