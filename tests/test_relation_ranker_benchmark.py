from __future__ import annotations

import json
from pathlib import Path

import scriptorium.relation_ranker as relation_ranker
import scriptorium.relation_ranker_benchmark as ranker_benchmark
from scriptorium.relation_ranker_benchmark import benchmark_relation_rankers_roor
from scriptorium.roor_benchmark import ROOR_DATA_BASE_URL, fetch_roor_benchmark_samples


class _DirectionalEstimator:
    def __init__(self, *, downward: bool) -> None:
        self.downward = downward

    def predict_proba(self, features):  # type: ignore[no-untyped-def]
        scores = [
            0.9 if (row[9] > 0) is self.downward else 0.1
            for row in features
        ]
        return [[1 - score, score] for score in scores]


def test_roor_ranker_ab_predicts_all_pages_before_opening_labels(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records = {
        f"{ROOR_DATA_BASE_URL}/data.val.txt": b"sample-a.json\nsample-b.json\n",
        f"{ROOR_DATA_BASE_URL}/jsons/sample-a.json": _annotation_bytes("sample-a"),
        f"{ROOR_DATA_BASE_URL}/jsons/sample-b.json": _annotation_bytes("sample-b"),
        f"{ROOR_DATA_BASE_URL}/images/sample-a.png": b"a",
        f"{ROOR_DATA_BASE_URL}/images/sample-b.png": b"b",
    }
    corpus = fetch_roor_benchmark_samples(
        tmp_path / "roor",
        split="val",
        sample_count=2,
        downloader=records.__getitem__,
    )

    def load_model(path):  # type: ignore[no-untyped-def]
        is_candidate = "candidate" in str(path)
        return (
            {
                "feature_version": relation_ranker.RELATION_FEATURE_VERSION,
                "estimator": _DirectionalEstimator(downward=is_candidate),
                "threshold": 0.5,
            },
            {
                "model_sha256": "candidate" if is_candidate else "control",
                "feature_version": relation_ranker.RELATION_FEATURE_VERSION,
            },
        )

    monkeypatch.setattr(ranker_benchmark.relation_ranker, "load_relation_ranker", load_model)
    semantic_paths = {sample.semantic_sidecar_path.resolve() for sample in corpus.samples}
    prediction_count = 0
    original_predict = relation_ranker._predict_roor_page_relations
    original_read_text = Path.read_text

    def tracking_predict(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal prediction_count
        prediction_count += 1
        return original_predict(*args, **kwargs)

    def tracking_read_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.resolve() in semantic_paths:
            assert prediction_count == 4
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(relation_ranker, "_predict_roor_page_relations", tracking_predict)
    monkeypatch.setattr(Path, "read_text", tracking_read_text)

    result = benchmark_relation_rankers_roor(
        corpus.out_dir,
        tmp_path / "control.joblib",
        tmp_path / "candidate.joblib",
    )

    assert result.report["labels_opened_after_all_predictions"] is True
    assert result.report["summary"]["control"]["top"]["correct"] == 0
    assert result.report["summary"]["candidate"]["top"]["correct"] == 2
    assert result.report["f1_delta"]["top"] == 1.0
    assert result.report_path.is_file()


def _annotation_bytes(sample_id: str) -> bytes:
    return json.dumps(
        {
            "uid": sample_id,
            "img": {
                "fname": f"images/{sample_id}.png",
                "height": 100,
                "width": 100,
            },
            "document": [
                {"id": 0, "box": [10, 10, 80, 20], "text": "First"},
                {"id": 1, "box": [10, 30, 80, 40], "text": "Second"},
            ],
            "label_entities": [],
            "label_linkings": [],
            "ro_linkings": [[0, 1]],
        }
    ).encode("utf-8")
