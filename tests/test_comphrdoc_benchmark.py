from __future__ import annotations

import hashlib
import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import fitz

import scriptorium.comphrdoc_benchmark as comphrdoc
from scriptorium.comphrdoc_benchmark import (
    COMPHRDOC_ANNOTATION_MEMBER,
    COMPHRDOC_ARCHIVE_URL,
    fetch_comphrdoc_benchmark_samples,
)


def test_fetch_comphrdoc_separates_layout_anchors_from_order_labels(
    tmp_path,
    monkeypatch,
) -> None:
    annotation_archive = _annotation_archive()
    pdf_bytes = _pdf_bytes()
    monkeypatch.setattr(
        comphrdoc,
        "COMPHRDOC_ARCHIVE_SHA256",
        hashlib.sha256(annotation_archive).hexdigest(),
    )
    downloads = {
        COMPHRDOC_ARCHIVE_URL: annotation_archive,
        "https://arxiv.org/pdf/1401.3699": pdf_bytes,
    }

    result = fetch_comphrdoc_benchmark_samples(
        tmp_path / "benchmark",
        document_id="1401.3699",
        max_pages=2,
        downloader=downloads.__getitem__,
    )

    assert len(result.samples) == 2
    first = result.samples[0]
    structure = json.loads(first.structure_path.read_text(encoding="utf-8"))
    semantic = json.loads(first.semantic_sidecar_path.read_text(encoding="utf-8"))
    assert structure["relations_removed"] is True
    assert "ro_linkings" not in structure
    assert "reading_order_id" not in first.structure_path.read_text(encoding="utf-8")
    assert [node["text"] for node in structure["document"]] == [
        "First line",
        "Second line",
        "Separate title",
    ]
    assert semantic["ro_linkings"] == [
        ["comphrdoc-p0001-l0001", "comphrdoc-p0001-l0002"],
        ["comphrdoc-p0001-l0002", "comphrdoc-p0001-l0003"],
    ]
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["selection"] == "fixed-document-page-prefix"
    assert manifest["structure_input"]["relations_removed"] is True
    assert manifest["samples"][0]["relation_count"] == 2
    second_structure = json.loads(result.samples[1].structure_path.read_text(encoding="utf-8"))
    second_semantic = json.loads(result.samples[1].semantic_sidecar_path.read_text(encoding="utf-8"))
    assert second_structure["document"][0]["type"] == "figure"
    assert second_structure["document"][0]["text"] == "[figure p0002 g0001]"
    assert second_semantic["ro_linkings"] == [
        ["comphrdoc-p0002-l0001", "comphrdoc-p0002-l0002"]
    ]
    with fitz.open(result.source_pdf_path) as pdf:
        assert len(pdf) == 2


def _annotation_archive() -> bytes:
    payload = {
        "images": [
            {"id": 1, "file_name": "1401.3699_0.png", "width": 100, "height": 100},
            {"id": 2, "file_name": "1401.3699_1.png", "width": 100, "height": 100},
            {"id": 3, "file_name": "other_0.png", "width": 100, "height": 100},
        ],
        "annotations": [
            {
                "image_id": 1,
                "reading_order_id": 1,
                "reading_order_label": 1,
                "in_page_id": 1,
                "textline_contents": ["First line", "Second line"],
                "textline_polys": [
                    [10, 10, 90, 10, 90, 20, 10, 20],
                    [10, 25, 90, 25, 90, 35, 10, 35],
                ],
            },
            {
                "image_id": 1,
                "reading_order_id": 2,
                "reading_order_label": 0,
                "in_page_id": 2,
                "textline_contents": ["Separate title"],
                "textline_polys": [[10, 50, 90, 50, 90, 60, 10, 60]],
            },
            {
                "image_id": 2,
                "reading_order_id": 1,
                "reading_order_label": 2,
                "in_page_id": 0,
                "category_id": 1,
                "bbox": [10, 10, 80, 40, 0],
                "textline_contents": [],
                "textline_polys": [],
            },
            {
                "image_id": 2,
                "reading_order_id": 1,
                "reading_order_label": 0,
                "in_page_id": 1,
                "category_id": 3,
                "textline_contents": ["Figure caption"],
                "textline_polys": [[10, 55, 90, 55, 90, 65, 10, 65]],
            },
        ],
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(COMPHRDOC_ANNOTATION_MEMBER, json.dumps(payload))
    return buffer.getvalue()


def _pdf_bytes() -> bytes:
    pdf = fitz.open()
    pdf.new_page(width=100, height=100)
    pdf.new_page(width=100, height=100)
    payload = pdf.tobytes()
    pdf.close()
    return payload
