from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import fitz
import pytest

import scriptorium.comphrdoc_benchmark as comphrdoc
import scriptorium.floating_ranker as floating_ranker
import scriptorium.relation_ranker as relation_ranker
from scriptorium.comphrdoc_benchmark import (
    COMPHRDOC_ANNOTATION_MEMBER,
    COMPHRDOC_ARCHIVE_URL,
    COMPHRDOC_TRAIN_ANNOTATION_MEMBER,
    benchmark_comphrdoc_relation_corpus,
    fetch_comphrdoc_benchmark_samples,
    fetch_comphrdoc_provider_calibration_corpus,
    fetch_comphrdoc_provider_test_corpus,
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


def test_fetch_provider_calibration_reconstructs_train_without_selection_labels(
    tmp_path,
    monkeypatch,
) -> None:
    annotation_archive = _provider_calibration_archive()
    pdf_bytes = _pdf_bytes()
    monkeypatch.setattr(
        comphrdoc,
        "COMPHRDOC_ARCHIVE_SHA256",
        hashlib.sha256(annotation_archive).hexdigest(),
    )
    downloads = {
        COMPHRDOC_ARCHIVE_URL: annotation_archive,
        "https://arxiv.org/pdf/2401.01000v1": pdf_bytes,
        "https://arxiv.org/pdf/2401.01007v1": pdf_bytes,
    }

    first = fetch_comphrdoc_provider_calibration_corpus(
        tmp_path / "first",
        sample_count=4,
        document_count=2,
        arxiv_version="v1",
        downloader=downloads.__getitem__,
    )
    second = fetch_comphrdoc_provider_calibration_corpus(
        tmp_path / "second",
        sample_count=4,
        document_count=2,
        arxiv_version="V1",
        downloader=downloads.__getitem__,
    )

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    repeated_manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
    assert manifest == repeated_manifest
    assert manifest["dataset"] == "Comp-HRDoc train"
    assert manifest["arxiv_version"] == "v1"
    assert all(document["source_pdf_url"].endswith("v1") for document in manifest["documents"])
    assert manifest["selection_uses_relation_labels"] is False
    assert manifest["selection_uses_oracle_layout"] is True
    assert manifest["source_alignment_failure_policy"] == "fail-closed"
    assert manifest["skip_unaligned_documents"] is False
    assert manifest["skipped_document_count"] == 0
    assert manifest["skipped_documents"] == []
    assert manifest["selection_excluded_annotation_fields"] == [
        "reading_order_id",
        "reading_order_label",
        "ro_linkings",
    ]
    assert manifest["split_unit"] == "document"
    assert manifest["inference_inputs_are_answer_free"] is True
    assert manifest["answer_separation"] == {
        "provider_input": "rendered-image-only",
        "oracle_structure_role": "evaluation-anchor-matching-only",
        "semantic_sidecar_role": "evaluation-labels-only",
        "provider_reads_oracle_structure": False,
        "provider_reads_semantic_sidecar": False,
    }
    assert manifest["annotation_images_present_in_archive"] is False
    assert {document["partition"] for document in manifest["documents"]} == {
        "fit",
        "calibration",
    }
    assert {sample["partition"] for sample in manifest["samples"]} == {
        "fit",
        "calibration",
    }
    assert len(first.samples) == 4
    assert len(first.source_pdf_paths) == 2
    assert all(path.is_file() for path in first.source_pdf_paths)
    assert any(
        sample["layout_stratum"] == "graphical-multicolumn"
        for sample in manifest["samples"]
    )
    assert all("f1" in sample["source_text_alignment"] for sample in manifest["samples"])
    assert all("source_page_alignment" in sample for sample in manifest["samples"])
    assert manifest["source_page_alignment"]["selection_uses_relation_labels"] is False
    structure = json.loads(first.samples[0].structure_path.read_text(encoding="utf-8"))
    semantic = json.loads(first.samples[0].semantic_sidecar_path.read_text(encoding="utf-8"))
    assert structure["relations_removed"] is True
    assert "ro_linkings" not in structure
    assert "reading_order_id" not in first.samples[0].structure_path.read_text(encoding="utf-8")
    assert "ro_linkings" in semantic


def test_provider_calibration_selection_is_relation_label_invariant() -> None:
    with ZipFile(BytesIO(_provider_calibration_archive())) as archive:
        payload = json.loads(archive.read(COMPHRDOC_TRAIN_ANNOTATION_MEMBER))
    relabeled = json.loads(json.dumps(payload))
    for index, annotation in enumerate(relabeled["annotations"]):
        annotation["reading_order_id"] = 10_000 - index
        annotation["reading_order_label"] = index % 3

    def selection_signature(annotation_payload):
        documents = comphrdoc._provider_calibration_document_pages(annotation_payload)
        selected = comphrdoc._select_provider_calibration_documents(
            documents,
            document_count=2,
            calibration_fraction=0.2,
        )
        return [
            (
                document["document_id"],
                document["partition"],
                [
                    (page["page_index"], page["layout_stratum"], page["layout_features"])
                    for page in comphrdoc._select_provider_calibration_pages(
                        document["pages"],
                        quota=2,
                    )
                ],
            )
            for document in selected
        ]

    assert selection_signature(payload) == selection_signature(relabeled)


def test_provider_calibration_rejects_unpinned_arxiv_version_syntax(tmp_path) -> None:
    with pytest.raises(ValueError, match="arxiv_version"):
        fetch_comphrdoc_provider_calibration_corpus(
            tmp_path / "invalid-version",
            sample_count=2,
            document_count=2,
            arxiv_version="1",
        )


def test_provider_calibration_skips_whole_unaligned_document_and_replenishes(
    tmp_path,
    monkeypatch,
) -> None:
    document_ids = [f"2401.{index:05d}" for index in range(40)]
    annotation_archive = _provider_alignment_archive(document_ids)
    monkeypatch.setattr(
        comphrdoc,
        "COMPHRDOC_ARCHIVE_SHA256",
        hashlib.sha256(annotation_archive).hexdigest(),
    )
    with ZipFile(BytesIO(annotation_archive)) as archive:
        payload = json.loads(archive.read(COMPHRDOC_TRAIN_ANNOTATION_MEMBER))
    document_pages = comphrdoc._provider_calibration_document_pages(payload)
    initial = comphrdoc._select_provider_calibration_documents(
        document_pages,
        document_count=2,
        calibration_fraction=0.2,
    )
    failed_document = initial[0]
    replacement = comphrdoc._select_provider_calibration_documents(
        document_pages,
        document_count=2,
        calibration_fraction=0.2,
        excluded_document_ids={failed_document["document_id"]},
    )
    expected_document_ids = {document["document_id"] for document in replacement}
    one_page_pdf = _pdf_bytes(page_count=1)
    two_page_pdf = _pdf_bytes(page_count=2)

    def download(url: str) -> bytes:
        document_id = url.rsplit("/", 1)[-1].removesuffix("v1")
        return (
            one_page_pdf
            if document_id == failed_document["document_id"]
            else two_page_pdf
        )

    with pytest.raises(
        ValueError,
        match="annotation page is outside the source PDF",
    ):
        fetch_comphrdoc_provider_calibration_corpus(
            tmp_path / "fail-closed",
            sample_count=2,
            document_count=2,
            arxiv_version="v1",
            annotation_archive=_write_archive(tmp_path, annotation_archive),
            downloader=download,
        )

    result = fetch_comphrdoc_provider_calibration_corpus(
        tmp_path / "skip",
        sample_count=2,
        document_count=2,
        arxiv_version="v1",
        annotation_archive=_write_archive(tmp_path, annotation_archive),
        skip_unaligned_documents=True,
        downloader=download,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    skipped = manifest["skipped_documents"]
    assert manifest["source_alignment_failure_policy"] == (
        "skip-whole-document-and-replenish-same-partition"
    )
    assert manifest["skip_unaligned_documents"] is True
    assert manifest["skipped_document_count"] == 1
    assert skipped[0]["id"] == failed_document["document_id"]
    assert skipped[0]["partition"] == failed_document["partition"]
    assert skipped[0]["reason"] == (
        "annotation-page-outside-source-pdf-ambiguous-text-alignment"
    )
    assert skipped[0]["annotation_page_index"] == 1
    assert skipped[0]["selection_attempt"] == 1
    assert skipped[0]["alignment"]["selection_uses_relation_labels"] is False
    assert {document["id"] for document in manifest["documents"]} == (
        expected_document_ids
    )
    assert failed_document["document_id"] not in {
        sample["document_id"] for sample in manifest["samples"]
    }
    assert not list(
        (result.out_dir / "images").glob(f"{failed_document['document_id']}_*")
    )
    assert not list(
        (result.out_dir / "structure").glob(f"{failed_document['document_id']}_*")
    )
    assert not list(
        (result.out_dir / "semantic").glob(f"{failed_document['document_id']}_*")
    )
    assert len(result.samples) == 2
    assert len(result.source_pdf_paths) == 2


def test_fetch_provider_test_uses_official_split_and_local_verified_archive(
    tmp_path,
    monkeypatch,
) -> None:
    annotation_archive = _provider_calibration_archive()
    annotation_archive_path = tmp_path / "CompHRDoc.zip"
    annotation_archive_path.write_bytes(annotation_archive)
    pdf_bytes = _pdf_bytes()
    monkeypatch.setattr(
        comphrdoc,
        "COMPHRDOC_ARCHIVE_SHA256",
        hashlib.sha256(annotation_archive).hexdigest(),
    )
    downloaded_urls = []

    def download(url: str) -> bytes:
        downloaded_urls.append(url)
        assert url != COMPHRDOC_ARCHIVE_URL
        return pdf_bytes

    result = fetch_comphrdoc_provider_test_corpus(
        tmp_path / "test-corpus",
        sample_count=4,
        document_count=2,
        arxiv_version="v1",
        annotation_archive=annotation_archive_path,
        downloader=download,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["dataset"] == "Comp-HRDoc test"
    assert manifest["partition"] == "test"
    assert manifest["document_offset"] == 0
    assert manifest["selection_window"] == {
        "document_offset": 0,
        "document_count": 2,
    }
    assert manifest["arxiv_version"] == "v1"
    assert manifest["selection_uses_relation_labels"] is False
    assert manifest["selection_uses_oracle_layout"] is True
    assert manifest["selection"] == (
        "document-hash-layout-stratified-test-documents-and-pages-v1"
    )
    assert {sample["partition"] for sample in manifest["samples"]} == {"test"}
    assert len(result.samples) == 4
    assert len(result.source_pdf_paths) == 2
    assert len(downloaded_urls) == 2
    assert all(url.endswith("v1") for url in downloaded_urls)
    assert any(
        sample["layout_stratum"] == "graphical-multicolumn"
        for sample in manifest["samples"]
    )


def test_provider_test_selection_is_relation_label_invariant() -> None:
    with ZipFile(BytesIO(_provider_calibration_archive())) as archive:
        payload = json.loads(archive.read(COMPHRDOC_ANNOTATION_MEMBER))
    relabeled = json.loads(json.dumps(payload))
    for index, annotation in enumerate(relabeled["annotations"]):
        annotation["reading_order_id"] = index * 17
        annotation["reading_order_label"] = 99 - index

    def selection_signature(annotation_payload):
        documents = comphrdoc._provider_calibration_document_pages(annotation_payload)
        selected = comphrdoc._select_provider_test_documents(
            documents,
            document_count=2,
        )
        return [
            (
                document["document_id"],
                [
                    (page["page_index"], page["layout_stratum"], page["layout_features"])
                    for page in comphrdoc._select_provider_test_pages(
                        document["pages"],
                        quota=2,
                    )
                ],
            )
            for document in selected
        ]

    assert selection_signature(payload) == selection_signature(relabeled)


def test_provider_test_document_windows_are_deterministic_and_disjoint() -> None:
    document_pages = {
        f"2401.{index:05d}": [
            {
                "layout_stratum": "graphical-multicolumn",
            }
        ]
        for index in range(6)
    }

    first = comphrdoc._select_provider_test_documents(
        document_pages,
        document_count=3,
        document_offset=0,
    )
    second = comphrdoc._select_provider_test_documents(
        document_pages,
        document_count=3,
        document_offset=3,
    )

    first_ids = {document["document_id"] for document in first}
    second_ids = {document["document_id"] for document in second}
    assert first_ids.isdisjoint(second_ids)
    assert len(first_ids | second_ids) == 6


def test_source_page_alignment_remaps_only_unique_high_confidence_text_match() -> None:
    target_text = " ".join(f"unique-token-{index}" for index in range(50))
    pdf = fitz.open()
    matching_page = pdf.new_page(width=400, height=400)
    matching_page.insert_textbox(
        fitz.Rect(10, 10, 390, 390),
        target_text,
        fontsize=6,
    )
    wrong_page = pdf.new_page(width=400, height=400)
    wrong_page.insert_textbox(
        fitz.Rect(10, 10, 390, 390),
        "unrelated content " * 30,
        fontsize=6,
    )
    annotations = [{"textline_contents": [target_text]}]

    selected_page, diagnostics = comphrdoc._align_source_page(
        pdf,
        annotation_page_index=1,
        annotations=annotations,
    )

    assert selected_page == 0
    assert diagnostics["policy"] == "remapped-by-text-alignment"
    assert diagnostics["selection_uses_relation_labels"] is False
    assert diagnostics["best_candidate_alignment"]["f1"] == 1.0
    assert diagnostics["same_index_alignment"]["f1"] == 0.0
    pdf.close()


def test_source_page_alignment_keeps_sparse_annotation_on_same_index() -> None:
    pdf = fitz.open()
    pdf.new_page(width=100, height=100).insert_text((10, 20), "Figure appendix")
    pdf.new_page(width=100, height=100).insert_text((10, 20), "Figure appendix")

    selected_page, diagnostics = comphrdoc._align_source_page(
        pdf,
        annotation_page_index=1,
        annotations=[{"textline_contents": ["Figure"]}],
    )

    assert selected_page == 1
    assert diagnostics["policy"] == "same-index-sparse-annotation"
    assert diagnostics["remapped"] is False
    pdf.close()


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
    manifest = json.loads(corpus.manifest_path.read_text(encoding="utf-8"))
    semantic_paths = {
        (corpus.out_dir / sample["semantic_sidecar"]).resolve()
        for sample in manifest["samples"]
    }
    prediction_count = 0
    original_predict = relation_ranker._predict_roor_page_relations
    original_read_text = Path.read_text

    def tracking_predict(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal prediction_count
        prediction_count += 1
        return original_predict(*args, **kwargs)

    def tracking_read_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.resolve() in semantic_paths:
            assert prediction_count == 2 * len(manifest["samples"])
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(relation_ranker, "_predict_roor_page_relations", tracking_predict)
    monkeypatch.setattr(Path, "read_text", tracking_read_text)

    result = benchmark_comphrdoc_relation_corpus(corpus.out_dir, tmp_path / "model.joblib")

    assert result.report["sample_count"] == 1
    assert result.report["inference_inputs_are_answer_free"] is True
    assert result.report["labels_opened_after_all_predictions"] is True
    assert result.report["noise"]["profile"] == "clean"
    assert result.report["noise"]["resolvable_label_ratio"] == 1.0
    assert result.report["graphical_label_audit"]["oracle_graphical_label_count"] == 1
    assert result.report["graphical_label_audit"]["conflicting_label_count"] == 0
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
                    "reliability_tier": "high-precision-review",
                    "strict_gate_passed": True,
                    "noise_aware_reliability_tier": "robust-high-precision-review",
                    "noise_aware_strict_gate_passed": True,
                    "feature_outlier_count": 0,
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
    assert trained["strict_gate_graphical"]["correct"] == 1
    assert trained["strict_gate_in_envelope_graphical"]["correct"] == 1
    assert trained["noise_aware_review_graphical"]["correct"] == 1
    assert trained["noise_aware_strict_graphical"]["correct"] == 1
    assert trained["noise_aware_joint_path_cover"]["protected_selected"] == 1
    assert result.report["graphical_label_audit"]["strict_gate_conflict_prediction_count"] == 0
    assert result.report["floating_model_sha256"] == "floating"
    assert "trained_floating_f1_delta" in result.report


def test_relation_corpus_benchmark_counts_strict_errors_on_conflicting_graphicals(
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
    manifest = json.loads(corpus.manifest_path.read_text(encoding="utf-8"))
    semantic_path = corpus.out_dir / manifest["samples"][0]["semantic_sidecar"]
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    semantic["ro_linkings"] = [list(reversed(semantic["ro_linkings"][0]))]
    semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
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
                    "strict_gate_passed": True,
                    "noise_aware_reliability_tier": "robust-high-precision-review",
                    "noise_aware_strict_gate_passed": True,
                    "feature_outlier_count": 0,
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
    assert trained["strict_gate_graphical"]["correct"] == 0
    audit = result.report["graphical_label_audit"]
    assert audit["conflicting_label_count"] == 1
    assert audit["strict_gate_conflict_prediction_count"] == 1
    assert audit["strict_gate_conflict_incorrect_count"] == 1
    assert audit["noise_aware_strict_conflict_prediction_count"] == 1
    assert audit["noise_aware_strict_conflict_incorrect_count"] == 1


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


def _provider_calibration_archive() -> bytes:
    images = []
    annotations = []
    image_id = 1
    for document_id in ("2401.01000", "2401.01007"):
        for page_index in range(2):
            images.append(
                {
                    "id": image_id,
                    "file_name": f"{document_id}_{page_index}.png",
                    "width": 100,
                    "height": 100,
                }
            )
            if page_index == 0:
                annotations.extend(
                    [
                        {
                            "image_id": image_id,
                            "reading_order_id": 1,
                            "reading_order_label": 2,
                            "in_page_id": 0,
                            "category_id": 1,
                            "bbox": [10, 35, 80, 25, 0],
                            "textline_contents": [],
                            "textline_polys": [],
                        },
                        {
                            "image_id": image_id,
                            "reading_order_id": 2,
                            "reading_order_label": 1,
                            "in_page_id": 1,
                            "category_id": 3,
                            "textline_contents": [f"Line {index}" for index in range(6)],
                            "textline_polys": [
                                [
                                    5 if index < 3 else 55,
                                    5 + (index % 3) * 10,
                                    45 if index < 3 else 95,
                                    5 + (index % 3) * 10,
                                    45 if index < 3 else 95,
                                    12 + (index % 3) * 10,
                                    5 if index < 3 else 55,
                                    12 + (index % 3) * 10,
                                ]
                                for index in range(6)
                            ],
                        },
                    ]
                )
            else:
                annotations.append(
                    {
                        "image_id": image_id,
                        "reading_order_id": 1,
                        "reading_order_label": 1,
                        "in_page_id": 0,
                        "category_id": 3,
                        "textline_contents": ["Plain first", "Plain second"],
                        "textline_polys": [
                            [10, 10, 90, 10, 90, 20, 10, 20],
                            [10, 25, 90, 25, 90, 35, 10, 35],
                        ],
                    }
                )
            image_id += 1
    payload = {"images": images, "annotations": annotations}
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(COMPHRDOC_TRAIN_ANNOTATION_MEMBER, json.dumps(payload))
        archive.writestr(COMPHRDOC_ANNOTATION_MEMBER, json.dumps(payload))
    return buffer.getvalue()


def _provider_alignment_archive(document_ids: list[str]) -> bytes:
    payload = {
        "images": [
            {
                "id": index,
                "file_name": f"{document_id}_1.png",
                "width": 100,
                "height": 100,
            }
            for index, document_id in enumerate(document_ids, start=1)
        ],
        "annotations": [
            {
                "image_id": index,
                "reading_order_id": 1,
                "reading_order_label": 0,
                "in_page_id": 0,
                "category_id": 3,
                "textline_contents": ["Sparse page"],
                "textline_polys": [[10, 10, 90, 10, 90, 20, 10, 20]],
            }
            for index in range(1, len(document_ids) + 1)
        ],
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(COMPHRDOC_TRAIN_ANNOTATION_MEMBER, json.dumps(payload))
    return buffer.getvalue()


def _write_archive(tmp_path: Path, payload: bytes) -> Path:
    path = tmp_path / "CompHRDoc.zip"
    path.write_bytes(payload)
    return path


def _pdf_bytes(*, page_count: int = 2) -> bytes:
    pdf = fitz.open()
    for _ in range(page_count):
        pdf.new_page(width=100, height=100)
    payload = pdf.tobytes()
    pdf.close()
    return payload
