from __future__ import annotations

import hashlib
import json

import pytest

import scriptorium.floating_ranker as floating_ranker


class _FakeEstimator:
    def predict_proba(self, features):
        scores = [0.9 if row[19] or row[20] else 0.1 for row in features]
        return [[1 - score, score] for score in scores]


class _MatrixEstimator:
    def predict_proba(self, features):
        scores = {
            (0.1, 0.1): 0.90,
            (0.1, 0.8): 0.80,
            (0.5, 0.1): 0.88,
            (0.5, 0.8): 0.10,
        }
        values = [scores[(round(row[2], 1), round(row[6], 1))] for row in features]
        return [[1 - score, score] for score in values]


class _CorrectnessEstimator:
    def predict_proba(self, features):
        return [[0.02, 0.98] for _ in features]


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


def _ambiguous_payload() -> dict:
    return {
        "img": {"width": 100, "height": 100},
        "document": [
            {
                "id": "figure-a",
                "block_id": "figure-a-block",
                "type": "figure",
                "box": [10, 10, 40, 40],
            },
            {
                "id": "figure-b",
                "block_id": "figure-b-block",
                "type": "figure",
                "box": [50, 10, 70, 40],
            },
            {
                "id": "caption-a",
                "block_id": "caption-a-block",
                "type": "text",
                "box": [10, 50, 40, 60],
                "text": "Figure 1. Shared candidate",
            },
            {
                "id": "caption-b",
                "block_id": "caption-b-block",
                "type": "text",
                "box": [80, 50, 95, 60],
                "text": "Figure 2. Alternate candidate",
            },
        ],
    }


def test_prediction_emits_review_only_trained_float_edge(monkeypatch) -> None:
    monkeypatch.setattr(
        floating_ranker,
        "load_floating_relation_ranker",
        lambda _: (
            {
                "estimator": _FakeEstimator(),
                "threshold": 0.5,
                "reliability_gate": None,
                "promotion_gate": None,
            },
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
    assert (
        result.diagnostics["assignment_policy"]
        == floating_ranker.FLOATING_ASSIGNMENT_POLICY_LEGACY
    )


def test_global_assignment_recovers_conflicting_second_choice(monkeypatch) -> None:
    monkeypatch.setattr(
        floating_ranker,
        "load_floating_relation_ranker",
        lambda _: (
            {
                "estimator": _MatrixEstimator(),
                "threshold": 0.5,
                "assignment_policy": floating_ranker.FLOATING_ASSIGNMENT_POLICY_GLOBAL,
                "selection_margin_policy": floating_ranker.FLOATING_MARGIN_POLICY_GLOBAL,
            },
            {"model_sha256": "global"},
        ),
    )

    result = floating_ranker.predict_floating_relations(
        _ambiguous_payload(),
        "model.joblib",
    )
    reversed_payload = _ambiguous_payload()
    reversed_payload["document"].reverse()
    reversed_result = floating_ranker.predict_floating_relations(
        reversed_payload,
        "model.joblib",
    )

    expected = {("figure-a", "caption-b"), ("figure-b", "caption-a")}
    assert {(edge["source"], edge["target"]) for edge in result.successor_edges} == expected
    assert {
        (edge["source"], edge["target"])
        for edge in reversed_result.successor_edges
    } == expected
    assert result.candidate_pair_count == 4
    assert result.diagnostics["assignment_policy"] == "global-cardinality-weight-v1"
    assert all(edge["selection_margin"] < 0 for edge in result.successor_edges)


def test_noise_aware_correctness_stays_review_only(monkeypatch) -> None:
    monkeypatch.setattr(
        floating_ranker,
        "load_floating_relation_ranker",
        lambda _: (
            {
                "estimator": _FakeEstimator(),
                "threshold": 0.5,
                "assignment_policy": floating_ranker.FLOATING_ASSIGNMENT_POLICY_GLOBAL,
                "selection_margin_policy": floating_ranker.FLOATING_MARGIN_POLICY_GLOBAL,
                "feature_envelope": {"lower": [0.0] * 27, "upper": [1.0] * 27},
                "reliability_gate": {
                    "available": True,
                    "confidence_threshold": 0.8,
                    "margin_threshold": 0.8,
                },
                "promotion_gate": {
                    "available": True,
                    "confidence_threshold": 0.8,
                    "margin_threshold": 0.8,
                },
                "correctness_estimator": _CorrectnessEstimator(),
                "correctness_feature_version": (
                    floating_ranker.FLOATING_CORRECTNESS_FEATURE_VERSION
                ),
                "correctness_policy": floating_ranker.FLOATING_CORRECTNESS_POLICY,
                "noise_aware_reliability_gate": {
                    "available": True,
                    "confidence_threshold": 0.95,
                },
                "noise_aware_promotion_gate": {
                    "available": True,
                    "confidence_threshold": 0.97,
                },
            },
            {"model_sha256": "robust"},
        ),
    )

    result = floating_ranker.predict_floating_relations(_payload(), "model.joblib")

    edge = result.successor_edges[0]
    assert edge["correctness_confidence"] == 0.98
    assert edge["noise_aware_reliability_tier"] == "robust-high-precision-review"
    assert edge["noise_aware_strict_gate_passed"] is True
    assert edge["assignment_alternative_cardinality_loss"] == 1
    assert edge["relation_policy"] == "review-only"
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


def test_loader_keeps_legacy_model_without_correctness_layer(tmp_path) -> None:
    model_path = tmp_path / "legacy.joblib"
    bundle = {
        "schema": floating_ranker.FLOATING_RANKER_SCHEMA,
        "feature_version": floating_ranker.FLOATING_FEATURE_VERSION,
        "estimator": _FakeEstimator(),
        "threshold": 0.5,
    }
    floating_ranker._joblib_module().dump(bundle, model_path)
    manifest = {
        "schema": floating_ranker.FLOATING_RANKER_SCHEMA,
        "feature_version": floating_ranker.FLOATING_FEATURE_VERSION,
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
    }
    model_path.with_suffix(".joblib.manifest.json").write_text(json.dumps(manifest))

    loaded, _ = floating_ranker.load_floating_relation_ranker(model_path)

    assert loaded["threshold"] == 0.5
    assert "correctness_estimator" not in loaded


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


def test_global_threshold_calibration_runs_the_assignment_constraint() -> None:
    graphical = [{"block_id": "figure-a"}, {"block_id": "figure-b"}]
    text_blocks = [{"block_id": "caption-a"}, {"block_id": "caption-b"}]
    scored_pages = [
        {
            "graphical": graphical,
            "text_blocks": text_blocks,
            "feature_rows": [[[0.0], [0.0]], [[0.0], [0.0]]],
            "score_rows": [[0.90, 0.80], [0.88, 0.10]],
            "positives": {("figure-a", "caption-b"), ("figure-b", "caption-a")},
        }
    ]

    threshold, report, records = floating_ranker._calibrate_global_assignment_threshold(
        scored_pages,
        page_count=1,
    )

    assert threshold == 0.8
    assert report["correct_count"] == 2
    assert report["predicted_count"] == 2
    assert report["f1"] == 1.0
    assert report["negative_margin_count"] == 2
    assert len(records) == 2


def test_global_assignment_context_reports_same_cardinality_alternative() -> None:
    contexts = floating_ranker._global_assignment_contexts(
        [[0.9, 0.8], [0.7, 0.6]],
        threshold=0.5,
    )

    assert len(contexts) == 2
    assert all(
        context["alternative_cardinality_loss"] == 0
        for context in contexts.values()
    )
    assert all(
        context["alternative_score_gap"] == pytest.approx(0.0)
        for context in contexts.values()
    )


def test_noise_aware_gate_requires_precision_on_every_profile() -> None:
    scored = {
        "clean": [(0.96, True, 0.9, 0.8)] * 30
        + [(0.90, False, 0.9, 0.8)] * 5,
        "mild": [(0.96, True, 0.9, 0.8)] * 30
        + [(0.90, False, 0.9, 0.8)] * 5,
        "stress": [(0.96, True, 0.9, 0.8)] * 30
        + [(0.90, False, 0.9, 0.8)] * 10,
    }

    gate = floating_ranker._calibrate_noise_aware_gate(
        scored,
        label_counts={profile: 30 for profile in scored},
        minimum_precision=0.97,
        base_gate={
            "available": True,
            "confidence_threshold": 0.8,
            "margin_threshold": 0.7,
        },
    )

    assert gate["available"] is True
    assert gate["confidence_threshold"] == 0.91
    assert gate["worst_profile_precision"] == 1.0
    assert set(gate["profiles"]) == {"clean", "mild", "stress"}


def test_training_noise_view_is_deterministic_and_answer_free() -> None:
    page = {
        "uid": "paper_1.png",
        "width": 100,
        "height": 100,
        "blocks": [
            {
                "block_id": 1,
                "kind": "figure",
                "box": [10, 10, 90, 45],
                "text": "",
                "noise_segments": [{"box": [10, 10, 90, 45], "text": ""}],
            },
            {
                "block_id": 2,
                "kind": "text",
                "box": [10, 50, 90, 70],
                "text": "Figure 1. Caption continuation",
                "noise_segments": [
                    {"box": [10, 50, 90, 59], "text": "Figure 1. Caption"},
                    {"box": [10, 61, 90, 70], "text": "continuation"},
                ],
            },
        ],
        "positives": {(1, 2)},
    }

    first = floating_ranker._training_noise_view(page, profile="mild")
    second = floating_ranker._training_noise_view(page, profile="mild")

    assert first == second
    graphical, text_blocks, origins = first
    assert graphical or text_blocks
    assert set(origins.values()) == {1, 2}


def test_feature_envelope_reports_pair_domain_shift() -> None:
    envelope = {"lower": [0.0, 0.0], "upper": [1.0, 1.0]}

    assert floating_ranker._feature_outliers([0.5, 1.5], envelope) == (1, 0.5)
    assert floating_ranker._feature_outliers([0.5, 0.75], envelope) == (0, 0.0)
