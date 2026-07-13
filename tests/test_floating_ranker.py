from __future__ import annotations

import pytest

import scriptorium.floating_ranker as floating_ranker


class _FakeEstimator:
    def predict_proba(self, features):
        scores = [0.9 if row[19] or row[20] else 0.1 for row in features]
        return [[1 - score, score] for score in scores]


def _payload() -> dict:
    return {
        "img": {"width": 100, "height": 100},
        "document": [
            {
                "id": "figure",
                "block_id": "figure-block",
                "type": "figure",
                "box": [10, 10, 90, 50],
                "text": "[figure]",
            },
            {
                "id": "caption-1",
                "block_id": "caption-block",
                "type": "text",
                "box": [10, 55, 90, 65],
                "text": "Figure 1. Result",
            },
            {
                "id": "caption-2",
                "block_id": "caption-block",
                "type": "text",
                "box": [10, 66, 90, 75],
                "text": "continued",
            },
        ],
    }


def test_prediction_emits_review_only_trained_float_edge(monkeypatch) -> None:
    monkeypatch.setattr(
        floating_ranker,
        "load_floating_relation_ranker",
        lambda _: (
            {"estimator": _FakeEstimator(), "threshold": 0.5},
            {"model_sha256": "test"},
        ),
    )

    result = floating_ranker.predict_floating_relations(_payload(), "model.joblib")

    assert result.graphical_source_count == 1
    assert result.candidate_pair_count == 1
    assert result.successor_edges == [
        {
            "source": "figure",
            "target": "caption-1",
            "kind": "successor",
            "confidence": 0.9,
            "selection_margin": 0.9,
            "review_required": True,
            "relation_policy": "review-only",
            "provider": "scriptorium-trained-floating-ranker",
            "relation_origin": "trained-floating-pair",
            "reliability_tier": "standard-review",
            "strict_gate_passed": False,
            "feature_outlier_count": 0,
            "feature_outlier_ratio": 0.0,
        }
    ]
    assert result.diagnostics["runtime_reorder"] is False


def test_prediction_rejects_answer_bearing_input(monkeypatch) -> None:
    monkeypatch.setattr(
        floating_ranker,
        "load_floating_relation_ranker",
        lambda _: ({"estimator": _FakeEstimator(), "threshold": 0.5}, {}),
    )
    payload = _payload()
    payload["ro_linkings"] = [["figure", "caption-1"]]

    with pytest.raises(ValueError, match="must not contain"):
        floating_ranker.predict_floating_relations(payload, "model.joblib")


def test_document_hash_split_keeps_documents_isolated() -> None:
    pages = [
        {"document_id": f"doc-{index}", "uid": f"doc-{index}_0"}
        for index in range(100)
    ]

    fit, calibration = floating_ranker._document_hash_split(pages, calibration_fraction=0.2)

    assert fit
    assert calibration
    assert {page["document_id"] for page in fit}.isdisjoint(
        {page["document_id"] for page in calibration}
    )


def test_reliability_gate_meets_precision_floor_without_test_labels() -> None:
    records = [
        *[(0.95, 0.8, True) for _ in range(40)],
        *[(0.90, 0.1, False) for _ in range(10)],
        *[(0.40, 0.2, True) for _ in range(20)],
    ]

    gate = floating_ranker._calibrate_reliability_gate(
        records,
        label_count=60,
        minimum_precision=0.97,
    )

    assert gate["available"] is True
    assert gate["predicted_count"] == 60
    assert gate["precision"] == 1.0
    assert gate["recall"] == 1.0


def test_feature_envelope_reports_pair_domain_shift() -> None:
    envelope = {"lower": [0.0, 0.0], "upper": [1.0, 1.0]}

    assert floating_ranker._feature_outliers([0.5, 1.5], envelope) == (1, 0.5)
    assert floating_ranker._feature_outliers([0.5, 0.75], envelope) == (0, 0.0)
