from __future__ import annotations

import hashlib
import json

import pytest

import scriptorium.chunkr_benchmark as chunkr_benchmark
from scriptorium.chunkr_benchmark import (
    benchmark_chunkr_reading_order,
    fetch_chunkr_reading_order_annotations,
)


def test_fetch_chunkr_annotations_pins_bytes_and_records_answer_boundary(
    tmp_path,
    monkeypatch,
) -> None:
    payload = _coco_payload()
    source_bytes = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(
        chunkr_benchmark,
        "CHUNKR_ANNOTATIONS_SHA256",
        hashlib.sha256(source_bytes).hexdigest(),
    )

    result = fetch_chunkr_reading_order_annotations(
        tmp_path / "chunkr",
        downloader=lambda url: source_bytes,
    )

    assert result.annotations_path.read_bytes() == source_bytes
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["development_only"] is True
    assert manifest["runtime_reorder"] is False
    assert manifest["image_count"] == 2
    assert manifest["annotation_count"] == 3
    assert manifest["answer_boundary"]["candidate_input_order"].startswith(
        "sha256-category-and-bbox"
    )


def test_chunkr_benchmark_scores_order_without_annotation_id_tie_break(
    tmp_path,
) -> None:
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps(_coco_payload()), encoding="utf-8")

    result = benchmark_chunkr_reading_order(annotations)

    report = result.report
    assert report["runtime_reorder"] is False
    assert report["answer_boundary"]["candidate_uses_annotation_id"] is False
    visual = report["order_candidates"]["visual-yx"]["all"]
    assert visual["page_count"] == 2
    assert visual["exact_match_count"] == 1
    assert visual["exact_match"] == 0.5
    assert visual["pairwise_accuracy"] == 0.0
    visual_edges = report["edge_channels"]["visual-yx"]["all"]
    assert visual_edges == {
        "page_count": 2,
        "correct": 0,
        "predicted": 1,
        "labels": 1,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
    }
    assert result.report_path.exists()


def test_chunkr_annotations_require_contiguous_order_ids(tmp_path) -> None:
    payload = _coco_payload()
    payload["annotations"][1]["id"] = 12
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="contiguous and ascending"):
        benchmark_chunkr_reading_order(annotations)


def _coco_payload() -> dict:
    return {
        "images": [
            {
                "id": 1,
                "file_name": "complex.png",
                "width": 100,
                "height": 100,
                "doc_category": "financial",
            },
            {
                "id": 2,
                "file_name": "single.png",
                "width": 100,
                "height": 100,
                "doc_category": "research",
            },
        ],
        "categories": [
            {"id": 1, "name": "Text Block", "supercategory": "object"}
        ],
        "annotations": [
            {
                "image_id": 1,
                "bbox": [10, 60, 70, 10],
                "area": 700,
                "iscrowd": 0,
                "id": 10,
                "category_id": 1,
            },
            {
                "image_id": 1,
                "bbox": [10, 10, 70, 10],
                "area": 700,
                "iscrowd": 0,
                "id": 11,
                "category_id": 1,
            },
            {
                "image_id": 2,
                "bbox": [10, 10, 70, 10],
                "area": 700,
                "iscrowd": 0,
                "id": 20,
                "category_id": 1,
            },
        ],
    }
