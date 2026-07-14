from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

import scriptorium.cli as cli
import scriptorium.chunkr_order_ranker as chunkr_order_ranker
from scriptorium.chunkr_benchmark import _load_chunkr_coco
from scriptorium.chunkr_order_ranker import (
    _page_fold_assignments,
    _training_matrix,
    _training_pages,
    benchmark_chunkr_order_ranker_roor,
    load_chunkr_order_ranker,
    predict_chunkr_block_order,
    train_chunkr_order_ranker,
)


def test_chunkr_order_training_is_page_oof_and_model_is_review_only(
    tmp_path,
    monkeypatch,
) -> None:
    payload = _training_coco_payload()
    source_bytes = json.dumps(payload).encode("utf-8")
    annotations = tmp_path / "annotations.json"
    annotations.write_bytes(source_bytes)
    monkeypatch.setattr(
        chunkr_order_ranker,
        "CHUNKR_ANNOTATIONS_SHA256",
        hashlib.sha256(source_bytes).hexdigest(),
    )

    result = train_chunkr_order_ranker(
        annotations,
        tmp_path / "order.joblib",
        cross_validation_folds=2,
        random_seed=7,
    )

    assert result.manifest["runtime_reorder"] is False
    assert result.manifest["candidate_consensus_policy"] == "isolated"
    assert result.manifest["cross_validation_folds"] == 2
    assert result.manifest["page_count"] == 6
    assert result.manifest["training_example_count"] == 36
    assert result.report["page_count"] == 6
    assert len(result.report["cases"]) == 6
    assert {case["fold_index"] for case in result.report["cases"]} == {0, 1}
    assert sum(fold["validation_page_count"] for fold in result.report["folds"]) == 6
    assert all("learned_metrics" in fold for fold in result.report["folds"])
    assert all("baseline_metrics" in fold for fold in result.report["folds"])
    assert result.report["fold_unit"].startswith("page")
    bundle, manifest = load_chunkr_order_ranker(result.model_path)
    assert bundle["feature_version"] == manifest["feature_version"]
    assert len(bundle["feature_names"]) == result.manifest["feature_count"]

    prediction = predict_chunkr_block_order(
        {
            "id": "held-out-layout",
            "width": 100,
            "height": 100,
            "elements": [
                {"id": "b", "box": [10, 40, 80, 50], "role": "Text Block"},
                {"id": "a", "box": [10, 10, 80, 20], "role": "Title"},
                {"id": "c", "box": [10, 70, 80, 80], "role": "Unknown"},
            ],
        },
        result.model_path,
    )
    assert set(prediction.ordered_ids) == {"a", "b", "c"}
    assert prediction.payload["runtime_reorder"] is False
    assert prediction.payload["candidate_consensus_policy"] == "isolated"
    assert len(prediction.payload["successor_edges"]) == 2
    assert prediction.diagnostics["page_profile_envelope_available"] is True
    assert isinstance(prediction.diagnostics["page_profile_outlier_names"], list)
    assert set(prediction.diagnostics["page_profile_values"]) == set(
        chunkr_order_ranker.PAGE_PROFILE_NAMES
    )
    assert all(
        {"name", "value", "lower", "upper", "direction"} <= set(outlier)
        for outlier in prediction.diagnostics["page_profile_outliers"]
    )

    input_json = tmp_path / "prediction-input.json"
    output_json = tmp_path / "prediction-output.json"
    input_json.write_text(
        json.dumps(
            {
                "id": "cli-layout",
                "width": 100,
                "height": 100,
                "elements": [
                    {"id": "a", "box": [10, 10, 80, 20], "role": "Title"},
                    {"id": "b", "box": [10, 40, 80, 50], "role": "Text Block"},
                ],
            }
        ),
        encoding="utf-8",
    )
    cli_result = CliRunner().invoke(
        cli.app,
        [
            "predict-chunkr-order",
            str(result.model_path),
            str(input_json),
            "--output",
            str(output_json),
        ],
    )
    assert cli_result.exit_code == 0, cli_result.output
    assert (
        json.loads(output_json.read_text(encoding="utf-8"))["runtime_reorder"] is False
    )

    corpus_dir = _write_roor_corpus(tmp_path)
    external = benchmark_chunkr_order_ranker_roor(
        corpus_dir,
        result.model_path,
    )
    assert external.report["runtime_reorder"] is False
    assert external.report["sample_count"] == 1
    assert external.report["relation_count"] == 2
    assert external.report["candidate_metrics"]["learned"]["relation_labels"] == 2
    assert external.report["answer_boundary"]["execution_policy"] == (
        "predict-all-pages-before-reading-any-label-sidecar"
    )
    assert len(external.report["cases"][0]["structure_sha256"]) == 64
    assert len(external.report["cases"][0]["semantic_sidecar_sha256"]) == 64
    assert external.report["promotion_decision"] == "reject-runtime-promotion"


def test_chunkr_features_and_folds_do_not_use_published_order_labels() -> None:
    first_payload = _load_chunkr_coco(
        json.dumps(_training_coco_payload()).encode("utf-8")
    )
    second_source = copy.deepcopy(_training_coco_payload())
    by_image: dict[int, list[dict]] = {}
    for annotation in second_source["annotations"]:
        by_image.setdefault(annotation["image_id"], []).append(annotation)
    reordered: list[dict] = []
    for image_id in sorted(by_image):
        annotations = list(reversed(by_image[image_id]))
        first_id = min(annotation["id"] for annotation in annotations)
        for offset, annotation in enumerate(annotations):
            annotation["id"] = first_id + offset
        reordered.extend(annotations)
    second_source["annotations"] = reordered
    second_payload = _load_chunkr_coco(json.dumps(second_source).encode("utf-8"))
    roles = ("Text Block", "Title", "Unknown")
    first_pages = _training_pages(first_payload, role_vocabulary=roles)
    second_pages = _training_pages(second_payload, role_vocabulary=roles)

    assert _page_fold_assignments(first_pages, fold_count=2) == (
        _page_fold_assignments(second_pages, fold_count=2)
    )
    first_features, first_labels, first_page_indices = _training_matrix(
        first_pages,
        role_vocabulary=roles,
    )
    second_features, second_labels, second_page_indices = _training_matrix(
        second_pages,
        role_vocabulary=roles,
    )
    np.testing.assert_array_equal(first_features, second_features)
    np.testing.assert_array_equal(first_page_indices, second_page_indices)
    assert not np.array_equal(first_labels, second_labels)


def test_chunkr_prediction_rejects_order_answers(tmp_path, monkeypatch) -> None:
    payload = _training_coco_payload()
    source_bytes = json.dumps(payload).encode("utf-8")
    annotations = tmp_path / "annotations.json"
    annotations.write_bytes(source_bytes)
    monkeypatch.setattr(
        chunkr_order_ranker,
        "CHUNKR_ANNOTATIONS_SHA256",
        hashlib.sha256(source_bytes).hexdigest(),
    )
    result = train_chunkr_order_ranker(
        annotations,
        tmp_path / "order.joblib",
        cross_validation_folds=2,
    )
    base_payload = {
        "width": 100,
        "height": 100,
        "elements": [
            {"id": "a", "box": [10, 10, 80, 20], "role": "Title"},
            {"id": "b", "box": [10, 40, 80, 50], "role": "Text Block"},
        ],
    }

    with pytest.raises(ValueError, match="order/relation answers"):
        predict_chunkr_block_order(
            {**base_payload, "successor_edges": [["a", "b"]]},
            result.model_path,
        )
    tainted_element_payload = copy.deepcopy(base_payload)
    tainted_element_payload["elements"][0]["semantic_order"] = 1
    with pytest.raises(ValueError, match="order/relation answers"):
        predict_chunkr_block_order(
            tainted_element_payload,
            result.model_path,
        )
    nested_tainted_payload = copy.deepcopy(base_payload)
    nested_tainted_payload["metadata"] = {"provider": {"reading_order": ["a", "b"]}}
    with pytest.raises(ValueError, match="order/relation answers"):
        predict_chunkr_block_order(
            nested_tainted_payload,
            result.model_path,
        )
    for answer_key, answer_value in (
        ("precedence_edges", [["a", "b"]]),
        ("reading_streams", [{"members": ["a", "b"]}]),
        ("ordered_element_ids", ["a", "b"]),
    ):
        with pytest.raises(ValueError, match=answer_key):
            predict_chunkr_block_order(
                {**base_payload, answer_key: answer_value},
                result.model_path,
            )

    report_bytes = result.report_path.read_bytes()
    result.report_path.write_bytes(report_bytes + b"\n")
    with pytest.raises(ValueError, match="OOF report hash does not match"):
        load_chunkr_order_ranker(result.model_path)
    result.report_path.write_bytes(report_bytes)

    manifest_bytes = result.manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    manifest["runtime_reorder"] = True
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot enable runtime reorder"):
        load_chunkr_order_ranker(result.model_path)
    result.manifest_path.write_bytes(manifest_bytes)

    result.model_path.write_bytes(result.model_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="hash does not match"):
        load_chunkr_order_ranker(result.model_path)


def test_roor_replay_predicts_every_page_before_reading_answers(
    tmp_path,
    monkeypatch,
) -> None:
    payload = _training_coco_payload()
    source_bytes = json.dumps(payload).encode("utf-8")
    annotations = tmp_path / "annotations.json"
    annotations.write_bytes(source_bytes)
    monkeypatch.setattr(
        chunkr_order_ranker,
        "CHUNKR_ANNOTATIONS_SHA256",
        hashlib.sha256(source_bytes).hexdigest(),
    )
    result = train_chunkr_order_ranker(
        annotations,
        tmp_path / "order.joblib",
        cross_validation_folds=2,
    )
    corpus_dir = _write_roor_corpus(tmp_path, sample_count=2)
    events: list[str] = []
    original_read_bytes = Path.read_bytes
    original_predict = chunkr_order_ranker.predict_chunkr_block_order

    def tracked_read_bytes(path: Path) -> bytes:
        if path.name.endswith(".semantic-order.json"):
            events.append("label")
        return original_read_bytes(path)

    def tracked_predict(*args, **kwargs):
        assert "label" not in events
        events.append("predict")
        return original_predict(*args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    monkeypatch.setattr(
        chunkr_order_ranker,
        "predict_chunkr_block_order",
        tracked_predict,
    )

    replay = benchmark_chunkr_order_ranker_roor(corpus_dir, result.model_path)

    assert events == ["predict", "predict", "label", "label"]
    assert replay.report["answer_boundary"]["prediction_phase_page_count"] == 2
    assert (
        replay.report["answer_boundary"]["label_phase_started_after_prediction_count"]
        == 2
    )

    manifest_path = corpus_dir / "roor_benchmark_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["samples"][0]["semantic_sidecar"] = "../outside.semantic-order.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "outside.semantic-order.json").write_text(
        json.dumps({"ro_linkings": [[0, 1]]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must stay inside the ROOR corpus"):
        benchmark_chunkr_order_ranker_roor(corpus_dir, result.model_path)


def _training_coco_payload() -> dict:
    images = []
    annotations = []
    next_id = 10
    for image_id in range(1, 7):
        images.append(
            {
                "id": image_id,
                "file_name": f"page-{image_id}.png",
                "width": 100,
                "height": 100,
                "doc_category": "financial" if image_id <= 3 else "research",
            }
        )
        boxes = (
            ([10, 10, 70, 10], [10, 40, 70, 10], [10, 70, 70, 10])
            if image_id % 2
            else ([10, 70, 70, 10], [10, 10, 70, 10], [10, 40, 70, 10])
        )
        for offset, bbox in enumerate(boxes):
            annotations.append(
                {
                    "image_id": image_id,
                    "bbox": bbox,
                    "area": bbox[2] * bbox[3],
                    "iscrowd": 0,
                    "id": next_id + offset,
                    "category_id": offset,
                }
            )
        next_id += 10
    return {
        "images": images,
        "categories": [
            {"id": 0, "name": "Text Block", "supercategory": "object"},
            {"id": 1, "name": "Title", "supercategory": "object"},
            {"id": 2, "name": "Unknown", "supercategory": "object"},
        ],
        "annotations": annotations,
    }


def _write_roor_corpus(tmp_path, *, sample_count: int = 1) -> Path:
    root = tmp_path / "roor"
    (root / "structure").mkdir(parents=True)
    (root / "images").mkdir()
    samples = []
    for index in range(sample_count):
        sample_id = f"sample-{index}"
        structure = {
            "schema": "scriptorium-roor-layout-anchor-only/v1",
            "uid": sample_id,
            "img": {"width": 100, "height": 100},
            "document": [
                {"id": 0, "box": [10, 10, 80, 20], "text": "Title"},
                {"id": 1, "box": [10, 40, 80, 50], "text": "Body"},
                {"id": 2, "box": [10, 70, 80, 80], "text": "Footer"},
            ],
            "relations_removed": True,
        }
        semantic = {**structure, "ro_linkings": [[0, 1], [1, 2]]}
        structure_relative = f"structure/{sample_id}.structure.json"
        semantic_relative = f"images/{sample_id}.semantic-order.json"
        (root / structure_relative).write_text(
            json.dumps(structure),
            encoding="utf-8",
        )
        (root / semantic_relative).write_text(
            json.dumps(semantic),
            encoding="utf-8",
        )
        samples.append(
            {
                "id": sample_id,
                "structure": structure_relative,
                "semantic_sidecar": semantic_relative,
            }
        )
    (root / "roor_benchmark_manifest.json").write_text(
        json.dumps(
            {
                "schema": "scriptorium-roor-benchmark/v1",
                "dataset": "ROOR",
                "revision": "test",
                "structure_input": {
                    "kind": "layout-anchor-only",
                    "relations_removed": True,
                },
                "samples": samples,
            }
        ),
        encoding="utf-8",
    )
    return root
