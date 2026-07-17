from __future__ import annotations

import json
from pathlib import Path

import pytest

from scriptorium.graph_model import load_graph_model, save_graph_model
from scriptorium.hierarchical_order_benchmark import HIERARCHY_INPUT_SCHEMA
from scriptorium.paragraph_graph_benchmark import (
    PARAGRAPH_GRAPH_FEATURE_VERSION,
    PARAGRAPH_GRAPH_MODEL_SCHEMA,
    PARAGRAPH_GRAPH_PROPOSAL_SCHEMA,
    benchmark_paragraph_graph,
    predict_paragraph_graph,
)
from scriptorium.provider_hierarchy_benchmark import (
    PROVIDER_HIERARCHY_CORPUS_SCHEMA,
    PROVIDER_HIERARCHY_LABEL_SCHEMA,
)
from scriptorium.successor_graph_benchmark import (
    SUCCESSOR_GRAPH_FEATURE_VERSION,
    SUCCESSOR_GRAPH_MODEL_SCHEMA,
    SUCCESSOR_GRAPH_PROPOSAL_SCHEMA,
    benchmark_successor_graph,
    predict_successor_graph,
)
from scriptorium.joint_graph_benchmark import (
    JOINT_GRAPH_PROPOSAL_SCHEMA,
    benchmark_joint_graph,
)


class _StubEstimator:
    """Module-level stub so joblib can pickle it in serialization tests."""

    def predict_proba(self, matrix):  # pragma: no cover - not used here
        raise AssertionError("predict is not part of the roundtrip")


def test_save_and_load_graph_model_roundtrip(tmp_path: Path) -> None:
    artifact = save_graph_model(
        model_path=tmp_path / "graph.joblib",
        schema="scriptorium-test-graph-model/v1",
        head="test-head",
        feature_version="test-features-v1",
        threshold=0.75,
        estimator=_StubEstimator(),
        estimator_parameters={"max_iter": 3},
        feature_count=4,
        nearest_candidates=5,
        train_corpus_manifest_sha256="abc",
        fit_document_count=2,
        fit_page_count=3,
        fit_candidate_count=10,
        fit_positive_count=4,
        cross_validation_folds=2,
        minimum_edge_precision=0.9,
        minimum_selected_edges=1,
        random_seed=7,
        scikit_learn_version="1.9.0",
    )
    loaded = load_graph_model(
        artifact.model_path,
        expected_schema="scriptorium-test-graph-model/v1",
        expected_head="test-head",
        expected_feature_version="test-features-v1",
    )
    assert loaded.manifest["model_sha256"] == artifact.manifest["model_sha256"]
    assert loaded.bundle["threshold"] == 0.75
    assert loaded.bundle["runtime_reorder"] is False
    assert loaded.bundle["nearest_candidates"] == 5


def test_load_graph_model_rejects_hash_mismatch(tmp_path: Path) -> None:
    artifact = save_graph_model(
        model_path=tmp_path / "graph.joblib",
        schema="scriptorium-test-graph-model/v1",
        head="test-head",
        feature_version="test-features-v1",
        threshold=0.5,
        estimator=_StubEstimator(),
        estimator_parameters={},
        feature_count=1,
    )
    artifact.model_path.write_bytes(artifact.model_path.read_bytes() + b"\x00")
    with pytest.raises(ValueError, match="hash does not match"):
        load_graph_model(
            artifact.model_path,
            expected_schema="scriptorium-test-graph-model/v1",
            expected_head="test-head",
            expected_feature_version="test-features-v1",
        )


def test_successor_and_paragraph_benchmarks_serialize_models(tmp_path: Path) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    test = _write_corpus(
        tmp_path / "test",
        [(f"test-{index}", "test") for index in range(2)],
    )

    paragraph = benchmark_paragraph_graph(
        train,
        output=tmp_path / "paragraph-report.json",
        proposals_dir=tmp_path / "paragraph-proposals",
        test_corpus_dir=test,
        model_output=tmp_path / "paragraph.joblib",
        cross_validation_folds=2,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    successor = benchmark_successor_graph(
        train,
        output=tmp_path / "successor-report.json",
        proposals_dir=tmp_path / "successor-proposals",
        test_corpus_dir=test,
        model_output=tmp_path / "successor.joblib",
        cross_validation_folds=2,
        nearest_candidates=3,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )

    assert paragraph.model_path is not None
    assert successor.model_path is not None
    assert paragraph.report["prediction_policy"] == "page-wise feature batches"
    assert successor.report["prediction_policy"] == "page-wise feature batches"
    assert paragraph.report["model"]["path"] == str(paragraph.model_path)
    assert successor.report["model"]["path"] == str(successor.model_path)

    paragraph_model = load_graph_model(
        paragraph.model_path,
        expected_schema=PARAGRAPH_GRAPH_MODEL_SCHEMA,
        expected_head="paragraph-comembership",
        expected_feature_version=PARAGRAPH_GRAPH_FEATURE_VERSION,
    )
    successor_model = load_graph_model(
        successor.model_path,
        expected_schema=SUCCESSOR_GRAPH_MODEL_SCHEMA,
        expected_head="directed-successor",
        expected_feature_version=SUCCESSOR_GRAPH_FEATURE_VERSION,
    )
    assert round(float(paragraph_model.bundle["threshold"]), 8) == paragraph.report[
        "frozen_threshold"
    ]
    assert round(float(successor_model.bundle["threshold"]), 8) == successor.report[
        "frozen_threshold"
    ]
    assert successor_model.bundle["nearest_candidates"] == 3
    assert paragraph_model.manifest["runtime_reorder"] is False
    assert successor_model.manifest["runtime_reorder"] is False

    # Manifest JSON remains answer-free and review-only.
    for path in (paragraph.model_manifest_path, successor.model_manifest_path):
        text = Path(path).read_text(encoding="utf-8")
        assert "oracle_region_id" not in text
        assert "oracle_scope" not in text
        payload = json.loads(text)
        assert payload["runtime_reorder"] is False


def test_serialized_models_predict_hierarchy_inputs_and_joint_decode(
    tmp_path: Path,
) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    paragraph = benchmark_paragraph_graph(
        train,
        output=tmp_path / "paragraph-report.json",
        proposals_dir=tmp_path / "paragraph-proposals",
        model_output=tmp_path / "paragraph.joblib",
        cross_validation_folds=2,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    successor = benchmark_successor_graph(
        train,
        output=tmp_path / "successor-report.json",
        proposals_dir=tmp_path / "successor-proposals",
        model_output=tmp_path / "successor.joblib",
        cross_validation_folds=2,
        nearest_candidates=3,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    assert paragraph.model_path is not None
    assert successor.model_path is not None

    hierarchy_input, _labels = _page_payload("holdout-page", offset=1)
    hierarchy_path = tmp_path / "holdout-input.json"
    _write_json(hierarchy_path, hierarchy_input)

    paragraph_prediction = predict_paragraph_graph(
        hierarchy_path,
        paragraph.model_path,
        output=tmp_path / "holdout.paragraph-graph.json",
        sample_id="holdout-page",
    )
    successor_prediction = predict_successor_graph(
        hierarchy_path,
        successor.model_path,
        output=tmp_path / "holdout.successor-graph.json",
        sample_id="holdout-page",
    )

    assert paragraph_prediction.proposal["schema"] == PARAGRAPH_GRAPH_PROPOSAL_SCHEMA
    assert successor_prediction.proposal["schema"] == SUCCESSOR_GRAPH_PROPOSAL_SCHEMA
    assert paragraph_prediction.proposal["runtime_reorder"] is False
    assert successor_prediction.proposal["runtime_reorder"] is False
    assert paragraph_prediction.proposal["id"] == "holdout-page"
    assert successor_prediction.proposal["id"] == "holdout-page"
    assert paragraph_prediction.proposal["reading_streams"]
    assert successor_prediction.proposal["successor_edges"] or successor_prediction.proposal[
        "candidate_edges"
    ]
    for path in (
        paragraph_prediction.proposal_path,
        successor_prediction.proposal_path,
    ):
        text = path.read_text(encoding="utf-8")
        assert "oracle_region_id" not in text
        assert "oracle_scope" not in text

    # Place predicted proposals into directories named for joint decode sample ids.
    predict_train = _write_corpus(
        tmp_path / "predict-train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    # Reuse the trained models' proposal dirs by predicting over the fit corpus
    # samples so joint decode has matching sample ids.
    paragraph_predict_dir = tmp_path / "predicted-paragraph"
    successor_predict_dir = tmp_path / "predicted-successor"
    paragraph_predict_dir.mkdir()
    successor_predict_dir.mkdir()
    manifest = json.loads(
        (predict_train / "provider_hierarchy_corpus_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for sample in manifest["samples"]:
        input_path = predict_train / sample["input"]
        sample_id = sample["id"]
        predict_paragraph_graph(
            input_path,
            paragraph.model_path,
            output=paragraph_predict_dir / f"{sample_id}.paragraph-graph.json",
            sample_id=sample_id,
        )
        predict_successor_graph(
            input_path,
            successor.model_path,
            output=successor_predict_dir / f"{sample_id}.successor-graph.json",
            sample_id=sample_id,
        )
    # Joint loader expects hashed proposal filenames from the benchmark writers.
    # Copy/rename predicted proposals into the expected hashed layout by re-running
    # through the benchmark proposal writers is hard; instead rewrite using the
    # same filename helper as the loaders.
    from scriptorium.joint_graph_benchmark import _proposal_path as joint_proposal_path

    paragraph_joint_dir = tmp_path / "joint-paragraph-proposals"
    successor_joint_dir = tmp_path / "joint-successor-proposals"
    paragraph_joint_dir.mkdir()
    successor_joint_dir.mkdir()
    for sample in manifest["samples"]:
        sample_id = sample["id"]
        for src_dir, dst_dir, suffix in (
            (paragraph_predict_dir, paragraph_joint_dir, "paragraph-graph"),
            (successor_predict_dir, successor_joint_dir, "successor-graph"),
        ):
            src = src_dir / f"{sample_id}.{suffix}.json"
            dst = joint_proposal_path(dst_dir, sample_id, suffix)
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    joint = benchmark_joint_graph(
        predict_train,
        paragraph_proposals_dir=paragraph_joint_dir,
        successor_proposals_dir=successor_joint_dir,
        output=tmp_path / "joint-from-predict-report.json",
        proposals_dir=tmp_path / "joint-from-predict-proposals",
    )
    assert joint.report["runtime_reorder"] is False
    assert set(joint.report["summary"]) == {"fit", "calibration"}
    for summary in joint.report["summary"].values():
        assert summary["selected_relation"]["f1"] > 0.0
        assert summary["segmentation_pairwise"]["f1"] > 0.0
    joint_proposals = list(joint.proposals_dir.glob("*.joint-graph.json"))
    assert joint_proposals
    for path in joint_proposals:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema"] == JOINT_GRAPH_PROPOSAL_SCHEMA
        assert payload["runtime_reorder"] is False


def _write_corpus(root: Path, documents: list[tuple[str, str]]) -> Path:
    import hashlib

    inputs = root / "inputs"
    labels_dir = root / "labels"
    inputs.mkdir(parents=True)
    labels_dir.mkdir(parents=True)
    samples = []
    for index, (document_id, partition) in enumerate(documents):
        sample_id = f"{document_id}-page"
        input_payload, labels = _page_payload(sample_id, offset=index % 2)
        input_path = inputs / f"{sample_id}.json"
        label_path = labels_dir / f"{sample_id}.json"
        _write_json(input_path, input_payload)
        _write_json(label_path, labels)
        samples.append(
            {
                "id": sample_id,
                "document_id": document_id,
                "page_index": 0,
                "partition": partition,
                "layout_stratum": "multicolumn",
                "input": str(input_path.relative_to(root)),
                "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                "labels": str(label_path.relative_to(root)),
                "labels_sha256": hashlib.sha256(label_path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "schema": PROVIDER_HIERARCHY_CORPUS_SCHEMA,
        "sample_count": len(samples),
        "partition_counts": {
            partition: sum(item["partition"] == partition for item in samples)
            for partition in sorted({item["partition"] for item in samples})
        },
        "inference_inputs_are_answer_free": True,
        "samples": samples,
    }
    _write_json(root / "provider_hierarchy_corpus_manifest.json", manifest)
    return root


def _page_payload(sample_id: str, *, offset: int = 0) -> tuple[dict, dict]:
    elements = []
    memberships = []
    successor_edges = []
    previous_id = None
    previous_region = None
    for paragraph_index, start_x in enumerate((60, 330)):
        region_id = f"oracle-{paragraph_index}"
        for line_index in range(3):
            element_id = f"{sample_id}-p{paragraph_index}-l{line_index}"
            elements.append(
                {
                    "id": element_id,
                    "box": [
                        start_x,
                        80 + offset + line_index * 18,
                        start_x + 210 - line_index * 8,
                        91 + offset + line_index * 18,
                    ],
                    "role": "text",
                    "text": (
                        f"paragraph {paragraph_index} continuation {line_index},"
                        if line_index < 2
                        else f"paragraph {paragraph_index} ending."
                    ),
                }
            )
            memberships.append(
                {"element_id": element_id, "oracle_region_id": region_id}
            )
            if previous_id is not None:
                successor_edges.append(
                    {
                        "source": previous_id,
                        "target": element_id,
                        "oracle_scope": (
                            "within-oracle-region"
                            if previous_region == region_id
                            else "cross-oracle-region"
                        ),
                    }
                )
            previous_id = element_id
            previous_region = region_id
    input_payload = {
        "schema": HIERARCHY_INPUT_SCHEMA,
        "id": sample_id,
        "page_index": 0,
        "width": 600,
        "height": 800,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": list(reversed(elements)),
        "regions": [],
    }
    labels = {
        "schema": PROVIDER_HIERARCHY_LABEL_SCHEMA,
        "id": sample_id,
        "memberships": memberships,
        "successor_edges": successor_edges,
    }
    return input_payload, labels


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
