from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scriptorium import cli, relation_ranker
from scriptorium.relation_ranker import (
    RelationRankerPredictionResult,
    RelationRankerTrainingResult,
    predict_structure_relations,
)


def _answer_free_payload() -> dict[str, object]:
    return {
        "schema": "scriptorium-roor-layout-anchor-only/v1",
        "uid": "sample",
        "img": {"fname": "images/sample.png", "width": 100, "height": 100},
        "document": [
            {"id": 10, "box": [10, 10, 90, 20], "text": "First line."},
            {"id": 20, "box": [10, 30, 90, 40], "text": "Second line."},
            {"id": 30, "box": [10, 50, 90, 60], "text": "Third line."},
        ],
    }


class _FakeEstimator:
    def predict_proba(self, features: list[list[float]]) -> list[list[float]]:
        scores = [0.9 if row[9] > 0 and row[1] < 0.5 else 0.1 for row in features]
        return [[1 - score, score] for score in scores]


class _FakeBranchEstimator:
    def predict_proba(self, features: list[list[float]]) -> list[list[float]]:
        return [[0.1, 0.9] for _ in features]


def test_prediction_emits_isolated_review_only_successors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        relation_ranker,
        "load_relation_ranker",
        lambda _: (
            {
                "estimator": _FakeEstimator(),
                "threshold": 0.5,
            },
            {"model_sha256": "abc123"},
        ),
    )

    result = predict_structure_relations(_answer_free_payload(), "model.joblib")

    payload = result.structure_payload
    assert payload["candidate_consensus_policy"] == "isolated"
    assert payload["relation_policy"] == "review-only"
    assert payload["semantic_policy"] == "review-only"
    assert payload["runtime_reorder"] is False
    assert result.source_count == 3
    assert result.predicted_edge_count == 2
    assert payload["successor_edges"] == [
        {
            "source": 10,
            "target": 20,
            "kind": "successor",
            "confidence": 0.9,
            "review_required": True,
            "relation_policy": "review-only",
            "provider": "scriptorium-trained-relation-ranker",
            "rank": 1,
        },
        {
            "source": 20,
            "target": 30,
            "kind": "successor",
            "confidence": 0.9,
            "review_required": True,
            "relation_policy": "review-only",
            "provider": "scriptorium-trained-relation-ranker",
            "rank": 1,
        },
    ]


def test_prediction_can_emit_calibrated_second_successors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        relation_ranker,
        "load_relation_ranker",
        lambda _: (
            {
                "estimator": _FakeEstimator(),
                "threshold": 0.5,
                "branch_estimator": _FakeBranchEstimator(),
                "branch_threshold": 0.7,
            },
            {"model_sha256": "abc123"},
        ),
    )

    result = predict_structure_relations(_answer_free_payload(), "model.joblib")

    branch_edges = [
        edge
        for edge in result.structure_payload["successor_edges"]
        if edge["rank"] == 2
    ]
    assert result.predicted_branch_edge_count == 2
    assert len(branch_edges) == 2
    assert all(edge["branch_confidence"] == 0.9 for edge in branch_edges)


@pytest.mark.parametrize("relation_key", ["ro_linkings", "successor_edges", "reading_order_edges"])
def test_prediction_rejects_structure_inputs_with_answer_relations(
    relation_key: str,
) -> None:
    payload = _answer_free_payload()
    payload[relation_key] = [[10, 20]]

    with pytest.raises(ValueError, match="must not contain"):
        predict_structure_relations(payload, "model.joblib")


def test_cli_writes_relation_ranker_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset_dir = tmp_path / "data"
    dataset_dir.mkdir()
    structure_path = tmp_path / "page.structure.json"
    structure_path.write_text(json.dumps(_answer_free_payload()), encoding="utf-8")
    model_path = tmp_path / "model.joblib"
    model_path.write_bytes(b"model")
    model_manifest = model_path.with_suffix(".joblib.manifest.json")
    model_manifest.write_text("{}", encoding="utf-8")

    training_manifest = {
        "calibration": {"document_count": 4, "f1": 0.75},
        "successor_threshold": 0.42,
        "branch_calibration": {"document_count": 4, "f1": 0.78},
        "branch_threshold": 0.65,
    }
    monkeypatch.setattr(
        cli,
        "train_relation_ranker",
        lambda *args, **kwargs: RelationRankerTrainingResult(
            model_path,
            model_manifest,
            training_manifest,
        ),
    )
    prediction = RelationRankerPredictionResult(
        {**_answer_free_payload(), "successor_edges": []},
        predicted_edge_count=0,
        source_count=3,
    )
    monkeypatch.setattr(cli, "predict_structure_relations", lambda *args, **kwargs: prediction)

    train_result = CliRunner().invoke(
        cli.app,
        ["train-relation-ranker", str(dataset_dir), "--output", str(model_path)],
    )
    output_path = tmp_path / "predicted.structure.json"
    run_result = CliRunner().invoke(
        cli.app,
        [
            "run-relation-ranker",
            str(structure_path),
            "--model",
            str(model_path),
            "--output",
            str(output_path),
        ],
    )

    assert train_result.exit_code == 0, train_result.output
    assert "Calibration F1: 0.75" in train_result.output
    assert run_result.exit_code == 0, run_result.output
    assert "Predicted successor edges: 0" in run_result.output
    assert "Predicted branch edges: 0" in run_result.output
    assert json.loads(output_path.read_text(encoding="utf-8"))["document"][0]["id"] == 10
