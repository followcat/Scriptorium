from __future__ import annotations

import hashlib
import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import fitz

import scriptorium.comphrdoc_benchmark as comphrdoc
import scriptorium.floating_ranker as floating_ranker
import scriptorium.relation_ranker as relation_ranker
from scriptorium.comphrdoc_benchmark import (
    COMPHRDOC_ANNOTATION_MEMBER,
    COMPHRDOC_ARCHIVE_URL,
    benchmark_comphrdoc_relation_corpus,
    fetch_comphrdoc_benchmark_samples,
    fetch_comphrdoc_relation_corpus,
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
    assert second_structure["document"][0]["block_id"] == "comphrdoc-p0002-b0001"
    assert second_structure["document"][0]["text"] == "[figure p0002 g0001]"
    assert second_semantic["ro_linkings"] == [
        ["comphrdoc-p0002-l0001", "comphrdoc-p0002-l0002"]
    ]
    with fitz.open(result.source_pdf_path) as pdf:
        assert len(pdf) == 2


def test_table_floating_order_is_independent_of_annotation_sequence() -> None:
    caption = {
        "reading_order_id": 4,
        "reading_order_label": 2,
        "in_page_id": 1,
        "category_id": 3,
        "textline_contents": ["Table 1. Results", "Caption continuation"],
        "textline_polys": [
            [10, 70, 90, 70, 90, 78, 10, 78],
            [10, 80, 90, 80, 90, 88, 10, 88],
        ],
    }
    table = {
        "reading_order_id": 4,
        "reading_order_label": 0,
        "in_page_id": 2,
        "category_id": 2,
        "bbox": [10, 10, 80, 55, 0],
        "textline_contents": [],
        "textline_polys": [],
    }

    for annotations in ([caption, table], [table, caption]):
        structure, semantic = comphrdoc._page_payloads(
            "sample",
            0,
            100,
            100,
            annotations,
        )
        table_id = next(node["id"] for node in structure["document"] if node["type"] == "table")
        caption_tail_id = next(
            node["id"]
            for node in structure["document"]
            if node["text"] == "Caption continuation"
        )
        assert [caption_tail_id, table_id] in semantic["ro_linkings"]


def test_body_flow_skips_floating_groups() -> None:
    annotations = [
        {
            "reading_order_id": 1,
            "reading_order_label": 1,
            "in_page_id": 1,
            "category_id": 3,
            "textline_contents": ["Body before"],
            "textline_polys": [[10, 10, 90, 10, 90, 20, 10, 20]],
        },
        {
            "reading_order_id": 2,
            "reading_order_label": 2,
            "in_page_id": 2,
            "category_id": 1,
            "bbox": [10, 25, 80, 30, 0],
            "textline_contents": [],
            "textline_polys": [],
        },
        {
            "reading_order_id": 2,
            "reading_order_label": 0,
            "in_page_id": 3,
            "category_id": 3,
            "textline_contents": ["Figure 1. Caption"],
            "textline_polys": [[10, 58, 90, 58, 90, 65, 10, 65]],
        },
        {
            "reading_order_id": 3,
            "reading_order_label": 0,
            "in_page_id": 4,
            "category_id": 3,
            "textline_contents": ["Body after"],
            "textline_polys": [[10, 75, 90, 75, 90, 85, 10, 85]],
        },
    ]

    structure, semantic = comphrdoc._page_payloads("sample", 0, 100, 100, annotations)
    ids = {node["text"]: node["id"] for node in structure["document"]}

    assert [ids["Body before"], ids["Body after"]] in semantic["ro_linkings"]
    assert [ids["[figure p0001 g0003]"], ids["Figure 1. Caption"]] in semantic["ro_linkings"]
    assert [ids["Body before"], ids["[figure p0001 g0003]"]] not in semantic["ro_linkings"]


def test_relation_corpus_is_answer_free_and_does_not_redistribute_images(
    tmp_path,
    monkeypatch,
) -> None:
    archive = _annotation_archive()
    monkeypatch.setattr(
        comphrdoc,
        "COMPHRDOC_ARCHIVE_SHA256",
        hashlib.sha256(archive).hexdigest(),
    )

    result = fetch_comphrdoc_relation_corpus(
        tmp_path / "relations",
        sample_count=1,
        downloader=lambda _: archive,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    structure_path = result.out_dir / manifest["samples"][0]["structure"]
    assert result.sample_count == 1
    assert manifest["selection_uses_labels"] is True
    assert manifest["inference_inputs_are_answer_free"] is True
    assert manifest["source_images_redistributed"] is False
    assert manifest["samples"][0]["graphical_relation_count"] == 1
    assert not (result.out_dir / "images").exists()
    assert "reading_order_id" not in structure_path.read_text(encoding="utf-8")


def test_relation_corpus_benchmark_reports_role_fusion_ab(
    tmp_path,
    monkeypatch,
) -> None:
    archive = _annotation_archive()
    monkeypatch.setattr(comphrdoc, "COMPHRDOC_ARCHIVE_SHA256", hashlib.sha256(archive).hexdigest())
    corpus = fetch_comphrdoc_relation_corpus(
        tmp_path / "relations",
        sample_count=1,
        downloader=lambda _: archive,
    )
    monkeypatch.setattr(
        relation_ranker,
        "load_relation_ranker",
        lambda _: ({"estimator": _DownwardEstimator(), "threshold": 0.5}, {"model_sha256": "test"}),
    )

    result = benchmark_comphrdoc_relation_corpus(corpus.out_dir, tmp_path / "model.joblib")

    assert result.report["sample_count"] == 1
    assert result.report["inference_inputs_are_answer_free"] is True
    assert result.report["summary"]["native-plus-structure-role"]["graphical"] == {
        "correct": 1,
        "predicted": 1,
        "labels": 1,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert result.report_path.is_file()


def test_relation_corpus_benchmark_can_score_trained_floating_mode(
    tmp_path,
    monkeypatch,
) -> None:
    archive = _annotation_archive()
    monkeypatch.setattr(comphrdoc, "COMPHRDOC_ARCHIVE_SHA256", hashlib.sha256(archive).hexdigest())
    corpus = fetch_comphrdoc_relation_corpus(
        tmp_path / "relations",
        sample_count=1,
        downloader=lambda _: archive,
    )
    monkeypatch.setattr(
        relation_ranker,
        "load_relation_ranker",
        lambda _: ({"estimator": _DownwardEstimator(), "threshold": 0.5}, {"model_sha256": "base"}),
    )
    monkeypatch.setattr(
        floating_ranker,
        "load_floating_relation_ranker",
        lambda _: ({}, {"model_sha256": "floating"}),
    )
    monkeypatch.setattr(
        floating_ranker,
        "_predict_floating_relations",
        lambda *args, **kwargs: floating_ranker.FloatingRankerPredictionResult(
            [
                {
                    "source": "comphrdoc-p0002-l0001",
                    "target": "comphrdoc-p0002-l0002",
                    "relation_origin": "trained-floating-pair",
                }
            ],
            1,
            1,
            {},
        ),
    )

    result = benchmark_comphrdoc_relation_corpus(
        corpus.out_dir,
        tmp_path / "base.joblib",
        floating_model_path=tmp_path / "floating.joblib",
    )

    trained = result.report["summary"]["native-plus-trained-floating"]
    assert trained["graphical"]["correct"] == 1
    assert result.report["floating_model_sha256"] == "floating"
    assert "trained_floating_f1_delta" in result.report


class _DownwardEstimator:
    def predict_proba(self, features):
        scores = [0.9 if feature[9] > 0 else 0.1 for feature in features]
        return [[1 - score, score] for score in scores]


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
