from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RELATION_RANKER_SCHEMA = "scriptorium-relation-ranker/v1"
RELATION_FEATURE_VERSION = "roor-pair-geometry-text-branch-v2"
RELATION_PROVIDER_SOURCE = "scriptorium-trained-relation-ranker"
RELATION_DATASET_LICENSE = "CC-BY-4.0"
DEFAULT_NEGATIVE_CANDIDATES = 20


@dataclass(frozen=True)
class RelationRankerTrainingResult:
    model_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class RelationRankerPredictionResult:
    structure_payload: dict[str, Any]
    predicted_edge_count: int
    source_count: int
    predicted_branch_edge_count: int = 0


def train_relation_ranker(
    dataset_dir: str | Path,
    output: str | Path,
    *,
    calibration_fraction: float = 0.2,
    random_seed: int = 17,
    negative_candidates: int = DEFAULT_NEGATIVE_CANDIDATES,
) -> RelationRankerTrainingResult:
    """Train a local successor ranker from the official ROOR train split only."""

    if not 0.05 <= calibration_fraction <= 0.5:
        raise ValueError("calibration_fraction must be between 0.05 and 0.5")
    if negative_candidates < 1:
        raise ValueError("negative_candidates must be at least 1")
    data_dir = Path(dataset_dir)
    train_index = data_dir / "data.train.txt"
    json_dir = data_dir / "jsons"
    if not train_index.is_file() or not json_dir.is_dir():
        raise ValueError("dataset directory must contain data.train.txt and jsons/")
    names = tuple(name.strip() for name in train_index.read_text(encoding="utf-8").splitlines() if name.strip())
    if len(names) < 2:
        raise ValueError("ROOR train split must contain at least two documents")
    documents = [_load_training_document(json_dir / name) for name in names]
    fit_documents, calibration_documents = _calibration_split(
        documents,
        calibration_fraction=calibration_fraction,
    )
    x_train, y_train = _training_examples(
        fit_documents,
        negative_candidates=negative_candidates,
    )
    estimator, sklearn_version = _fit_estimator(x_train, y_train, random_seed=random_seed)
    threshold, calibration = _calibrate_top_successor_threshold(estimator, calibration_documents)
    branch_estimator = _fit_branch_estimator(
        estimator,
        fit_documents,
        random_seed=random_seed + 6,
    )
    branch_threshold, branch_calibration = _calibrate_branch_threshold(
        estimator,
        branch_estimator,
        calibration_documents,
        top_threshold=threshold,
    )

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = output_path.with_suffix(f"{output_path.suffix}.manifest.json")
    bundle = {
        "schema": RELATION_RANKER_SCHEMA,
        "feature_version": RELATION_FEATURE_VERSION,
        "threshold": threshold,
        "estimator": estimator,
        "branch_estimator": branch_estimator,
        "branch_threshold": branch_threshold,
    }
    joblib = _joblib_module()
    joblib.dump(bundle, output_path)
    model_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
    manifest = {
        "schema": RELATION_RANKER_SCHEMA,
        "feature_version": RELATION_FEATURE_VERSION,
        "provider": RELATION_PROVIDER_SOURCE,
        "dataset": "ROOR",
        "dataset_repository": "https://github.com/chongzhangFDU/ROOR-Datasets",
        "dataset_license": RELATION_DATASET_LICENSE,
        "train_index_sha256": hashlib.sha256(train_index.read_bytes()).hexdigest(),
        "split_policy": "official-train-only-with-uid-hash-calibration",
        "fit_document_count": len(fit_documents),
        "calibration_document_count": len(calibration_documents),
        "training_example_count": len(y_train),
        "training_positive_count": int(sum(y_train)),
        "negative_candidates_per_source": negative_candidates,
        "calibration_fraction": calibration_fraction,
        "successor_threshold": threshold,
        "calibration": calibration,
        "branch_threshold": branch_threshold,
        "branch_calibration": branch_calibration,
        "random_seed": random_seed,
        "scikit_learn_version": sklearn_version,
        "model_sha256": model_sha256,
        "model_file": output_path.name,
        "security": "Load only locally generated model files; joblib loading can execute code.",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return RelationRankerTrainingResult(output_path, manifest_path, manifest)


def predict_structure_relations(
    payload: Mapping[str, Any],
    model_path: str | Path,
) -> RelationRankerPredictionResult:
    """Predict isolated review-only successors for one ROOR-style structure page."""

    if _payload_contains_answer_relations(payload):
        raise ValueError("input structure JSON must not contain ro_linkings or successor relations")
    document = payload.get("document")
    image = payload.get("img")
    if not isinstance(document, list) or not isinstance(image, Mapping):
        raise ValueError("input must contain ROOR-style document and img objects")
    segments = [_validated_segment(segment) for segment in document]
    width = _positive_float(image.get("width"), "img.width")
    height = _positive_float(image.get("height"), "img.height")
    bundle, manifest = load_relation_ranker(model_path)
    estimator = bundle["estimator"]
    threshold = float(bundle["threshold"])
    branch_estimator = bundle.get("branch_estimator")
    branch_threshold = float(bundle.get("branch_threshold", 1.1))

    successor_edges: list[dict[str, Any]] = []
    branch_edge_count = 0
    for source_segment in segments:
        targets = [target for target in segments if target["id"] != source_segment["id"]]
        if not targets:
            continue
        features = [_pair_features(source_segment, target, width=width, height=height) for target in targets]
        probabilities = estimator.predict_proba(features)
        scores = [float(row[1]) for row in probabilities]
        ranked_indices = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
        best_index = ranked_indices[0]
        confidence = scores[best_index]
        if confidence < threshold:
            continue
        successor_edges.append(
            {
                "source": source_segment["id"],
                "target": targets[best_index]["id"],
                "kind": "successor",
                "confidence": round(confidence, 8),
                "review_required": True,
                "relation_policy": "review-only",
                "provider": RELATION_PROVIDER_SOURCE,
                "rank": 1,
            }
        )
        if branch_estimator is None or len(ranked_indices) < 2:
            continue
        ranked = [(scores[index], targets[index], features[index]) for index in ranked_indices]
        branch_confidence = float(
            branch_estimator.predict_proba(
                [_branch_features(payload, source_segment, ranked)]
            )[0][1]
        )
        if branch_confidence < branch_threshold:
            continue
        second_index = ranked_indices[1]
        successor_edges.append(
            {
                "source": source_segment["id"],
                "target": targets[second_index]["id"],
                "kind": "successor",
                "confidence": round(scores[second_index], 8),
                "branch_confidence": round(branch_confidence, 8),
                "review_required": True,
                "relation_policy": "review-only",
                "provider": RELATION_PROVIDER_SOURCE,
                "rank": 2,
            }
        )
        branch_edge_count += 1

    normalized = dict(payload)
    normalized.pop("label_entities", None)
    normalized.pop("label_linkings", None)
    normalized.update(
        {
            "source": RELATION_PROVIDER_SOURCE,
            "model": RELATION_FEATURE_VERSION,
            "provider_version": str(manifest.get("model_sha256") or "unknown")[:16],
            "provider_code_license": "project-license",
            "training_dataset": "ROOR official train split",
            "training_dataset_license": RELATION_DATASET_LICENSE,
            "semantic_policy": "review-only",
            "order_policy": "review-only",
            "relation_policy": "review-only",
            "candidate_consensus_policy": "isolated",
            "runtime_reorder": False,
            "successor_edges": successor_edges,
            "relation_ranker": {
                "feature_version": RELATION_FEATURE_VERSION,
                "threshold": threshold,
                "branch_threshold": branch_threshold,
                "source_count": len(segments),
                "predicted_edge_count": len(successor_edges),
                "predicted_branch_edge_count": branch_edge_count,
                "model_sha256": manifest.get("model_sha256"),
            },
        }
    )
    return RelationRankerPredictionResult(
        normalized,
        len(successor_edges),
        len(segments),
        branch_edge_count,
    )


def load_relation_ranker(model_path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(model_path)
    manifest_path = path.with_suffix(f"{path.suffix}.manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        raise ValueError("model and adjacent .manifest.json are required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_sha256 = str(manifest.get("model_sha256") or "")
    actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if not expected_sha256 or actual_sha256 != expected_sha256:
        raise ValueError("relation ranker model hash does not match its manifest")
    bundle = _joblib_module().load(path)
    if not isinstance(bundle, dict) or bundle.get("schema") != RELATION_RANKER_SCHEMA:
        raise ValueError("unsupported relation ranker model schema")
    if bundle.get("feature_version") != RELATION_FEATURE_VERSION:
        raise ValueError("unsupported relation ranker feature version")
    return bundle, manifest


def _load_training_document(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read ROOR training document: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("ro_linkings"), list):
        raise ValueError(f"ROOR training document lacks ro_linkings: {path}")
    return payload


def _calibration_split(
    documents: Sequence[dict[str, Any]],
    *,
    calibration_fraction: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fit: list[dict[str, Any]] = []
    calibration: list[dict[str, Any]] = []
    boundary = int(calibration_fraction * 10_000)
    for document in documents:
        uid = str(document.get("uid") or document.get("img", {}).get("fname") or "")
        bucket = int.from_bytes(hashlib.sha256(uid.encode("utf-8")).digest()[:4], "big") % 10_000
        (calibration if bucket < boundary else fit).append(document)
    if not fit or not calibration:
        raise ValueError("UID hash calibration split produced an empty partition")
    return fit, calibration


def _training_examples(
    documents: Sequence[dict[str, Any]],
    *,
    negative_candidates: int,
) -> tuple[list[list[float]], list[int]]:
    features: list[list[float]] = []
    labels: list[int] = []
    for payload in documents:
        segments = [_validated_segment(segment) for segment in payload["document"]]
        image = payload["img"]
        width = _positive_float(image["width"], "img.width")
        height = _positive_float(image["height"], "img.height")
        relations = {tuple(edge) for edge in payload["ro_linkings"]}
        for source in segments:
            candidates: list[tuple[bool, float, dict[str, Any]]] = []
            for target in segments:
                if source["id"] == target["id"]:
                    continue
                is_positive = (source["id"], target["id"]) in relations
                candidates.append((is_positive, _normalized_center_distance(source, target, width, height), target))
            negatives_added = 0
            for is_positive, _, target in sorted(candidates, key=lambda item: (not item[0], item[1])):
                if not is_positive and negatives_added >= negative_candidates:
                    continue
                features.append(_pair_features(source, target, width=width, height=height))
                labels.append(int(is_positive))
                if not is_positive:
                    negatives_added += 1
    return features, labels


def _fit_estimator(
    features: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    random_seed: int,
) -> tuple[Any, str]:
    try:
        import sklearn
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError as exc:
        raise RuntimeError("Install requirements-relation-ranker.txt to train a relation ranker") from exc
    estimator = HistGradientBoostingClassifier(
        max_iter=180,
        max_leaf_nodes=31,
        learning_rate=0.08,
        l2_regularization=1.0,
        class_weight="balanced",
        random_state=random_seed,
    )
    estimator.fit(features, labels)
    return estimator, sklearn.__version__


def _fit_branch_estimator(
    pair_estimator: Any,
    documents: Sequence[dict[str, Any]],
    *,
    random_seed: int,
) -> Any:
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError as exc:
        raise RuntimeError("Install requirements-relation-ranker.txt to train a relation ranker") from exc
    features: list[list[float]] = []
    labels: list[int] = []
    for payload in documents:
        outgoing: dict[Any, set[Any]] = defaultdict(set)
        for source_ref, target_ref in payload["ro_linkings"]:
            outgoing[source_ref].add(target_ref)
        for source, ranked in _ranked_document_targets(pair_estimator, payload):
            if len(ranked) < 2:
                continue
            features.append(_branch_features(payload, source, ranked))
            labels.append(int(len(outgoing[source["id"]]) >= 2))
    estimator = HistGradientBoostingClassifier(
        max_iter=120,
        max_leaf_nodes=15,
        learning_rate=0.08,
        l2_regularization=2.0,
        class_weight="balanced",
        random_state=random_seed,
    )
    estimator.fit(features, labels)
    return estimator


def _calibrate_top_successor_threshold(
    estimator: Any,
    documents: Sequence[dict[str, Any]],
) -> tuple[float, dict[str, Any]]:
    ranked: list[tuple[float, bool]] = []
    total_relation_count = 0
    for payload in documents:
        segments = [_validated_segment(segment) for segment in payload["document"]]
        image = payload["img"]
        width = _positive_float(image["width"], "img.width")
        height = _positive_float(image["height"], "img.height")
        relations = {tuple(edge) for edge in payload["ro_linkings"]}
        total_relation_count += len(relations)
        outgoing: dict[Any, set[Any]] = defaultdict(set)
        for source_ref, target_ref in relations:
            outgoing[source_ref].add(target_ref)
        for source in segments:
            targets = [target for target in segments if target["id"] != source["id"]]
            if not targets:
                continue
            probabilities = estimator.predict_proba(
                [_pair_features(source, target, width=width, height=height) for target in targets]
            )
            scores = [float(row[1]) for row in probabilities]
            best_index = max(range(len(scores)), key=scores.__getitem__)
            ranked.append((scores[best_index], targets[best_index]["id"] in outgoing[source["id"]]))

    best: tuple[float, float, float, float, int, int] | None = None
    for step in range(5, 96):
        threshold = step / 100
        selected = [correct for score, correct in ranked if score >= threshold]
        predicted = len(selected)
        correct = sum(selected)
        precision = correct / predicted if predicted else 0.0
        recall = correct / total_relation_count if total_relation_count else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        candidate = (f1, precision, threshold, recall, predicted, correct)
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    f1, precision, threshold, recall, predicted, correct = best
    return threshold, {
        "document_count": len(documents),
        "relation_count": total_relation_count,
        "predicted_edge_count": predicted,
        "correct_edge_count": correct,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
    }


def _calibrate_branch_threshold(
    pair_estimator: Any,
    branch_estimator: Any,
    documents: Sequence[dict[str, Any]],
    *,
    top_threshold: float,
) -> tuple[float, dict[str, Any]]:
    records: list[tuple[float, bool, bool]] = []
    relation_count = 0
    for payload in documents:
        relations = {tuple(edge) for edge in payload["ro_linkings"]}
        relation_count += len(relations)
        for source, ranked in _ranked_document_targets(pair_estimator, payload):
            if not ranked or ranked[0][0] < top_threshold:
                continue
            first_edge = (source["id"], ranked[0][1]["id"])
            records.append((0.0, first_edge in relations, False))
            if len(ranked) < 2:
                continue
            branch_probability = float(
                branch_estimator.predict_proba([_branch_features(payload, source, ranked)])[0][1]
            )
            second_edge = (source["id"], ranked[1][1]["id"])
            records.append((branch_probability, second_edge in relations, True))

    best: tuple[float, float, float, float, int, int, int] | None = None
    for step in range(5, 100):
        threshold = step / 100
        selected = [record for record in records if not record[2] or record[0] >= threshold]
        predicted = len(selected)
        correct = sum(int(record[1]) for record in selected)
        branch_edges = sum(int(record[2]) for record in selected)
        precision = correct / predicted if predicted else 0.0
        recall = correct / relation_count if relation_count else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        candidate = (f1, precision, threshold, recall, predicted, correct, branch_edges)
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    f1, precision, threshold, recall, predicted, correct, branch_edges = best
    return threshold, {
        "document_count": len(documents),
        "relation_count": relation_count,
        "predicted_edge_count": predicted,
        "predicted_branch_edge_count": branch_edges,
        "correct_edge_count": correct,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
    }


def _ranked_document_targets(
    estimator: Any,
    payload: Mapping[str, Any],
) -> list[tuple[dict[str, Any], list[tuple[float, dict[str, Any], list[float]]]]]:
    segments = [_validated_segment(segment) for segment in payload["document"]]
    image = payload["img"]
    width = _positive_float(image["width"], "img.width")
    height = _positive_float(image["height"], "img.height")
    ranked_sources: list[tuple[dict[str, Any], list[tuple[float, dict[str, Any], list[float]]]]] = []
    for source in segments:
        targets = [target for target in segments if target["id"] != source["id"]]
        if not targets:
            ranked_sources.append((source, []))
            continue
        features = [_pair_features(source, target, width=width, height=height) for target in targets]
        probabilities = estimator.predict_proba(features)
        ranked = sorted(
            (
                (float(probability[1]), target, pair_features)
                for probability, target, pair_features in zip(probabilities, targets, features, strict=True)
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        ranked_sources.append((source, ranked))
    return ranked_sources


def _branch_features(
    payload: Mapping[str, Any],
    source: Mapping[str, Any],
    ranked: Sequence[tuple[float, dict[str, Any], list[float]]],
) -> list[float]:
    width = _positive_float(payload["img"]["width"], "img.width")
    height = _positive_float(payload["img"]["height"], "img.height")
    x0, y0, x1, y1 = (float(value) for value in source["box"])
    text = str(source.get("text") or "").strip()
    first = ranked[0]
    second = ranked[1]
    return [
        x0 / width,
        y0 / height,
        x1 / width,
        y1 / height,
        (x1 - x0) / width,
        (y1 - y0) / height,
        len(payload["document"]) / 256,
        first[0],
        second[0],
        first[0] - second[0],
        len(text) / 256,
        float(text.endswith((".", ":", ";", "?", "!"))),
        *first[2][8:20],
        *second[2][8:20],
    ]


def _pair_features(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    width: float,
    height: float,
) -> list[float]:
    sx0, sy0, sx1, sy1 = (float(value) for value in source["box"])
    tx0, ty0, tx1, ty1 = (float(value) for value in target["box"])
    sw, sh = max(sx1 - sx0, 1.0), max(sy1 - sy0, 1.0)
    tw, th = max(tx1 - tx0, 1.0), max(ty1 - ty0, 1.0)
    scx, scy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
    tcx, tcy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    horizontal_overlap = max(0.0, min(sx1, tx1) - max(sx0, tx0)) / max(1.0, min(sw, tw))
    vertical_overlap = max(0.0, min(sy1, ty1) - max(sy0, ty0)) / max(1.0, min(sh, th))
    source_text = str(source.get("text") or "").strip()
    target_text = str(target.get("text") or "").strip()
    return [
        sx0 / width,
        sy0 / height,
        sx1 / width,
        sy1 / height,
        tx0 / width,
        ty0 / height,
        tx1 / width,
        ty1 / height,
        (tcx - scx) / width,
        (tcy - scy) / height,
        abs(tcx - scx) / width,
        abs(tcy - scy) / height,
        sw / width,
        sh / height,
        tw / width,
        th / height,
        horizontal_overlap,
        vertical_overlap,
        float(ty0 >= sy0),
        float(tx0 >= sx0),
        math.log1p(len(source_text)) / 8,
        math.log1p(len(target_text)) / 8,
        float(source_text.endswith((".", ":", ";", "?", "!"))),
        float(bool(target_text) and target_text[0].islower()),
        float(bool(target_text) and target_text[0].isdigit()),
    ]


def _normalized_center_distance(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    width: float,
    height: float,
) -> float:
    sx0, sy0, sx1, sy1 = (float(value) for value in source["box"])
    tx0, ty0, tx1, ty1 = (float(value) for value in target["box"])
    return abs((tx0 + tx1 - sx0 - sx1) / 2) / width + abs((ty0 + ty1 - sy0 - sy1) / 2) / height


def _validated_segment(segment: Any) -> dict[str, Any]:
    if not isinstance(segment, Mapping):
        raise ValueError("document segments must be objects")
    if "id" not in segment:
        raise ValueError("document segment is missing id")
    box = segment.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("document segment box must contain four coordinates")
    normalized = dict(segment)
    normalized["box"] = [float(value) for value in box]
    return normalized


def _payload_contains_answer_relations(payload: Mapping[str, Any]) -> bool:
    relation_keys = {
        "ro_linkings",
        "successor_edges",
        "reading_order_edges",
        "reading_order_linkings",
        "reading_order_relations",
    }
    return any(key in payload for key in relation_keys)


def _positive_float(value: Any, label: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a positive number") from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{label} must be a positive number")
    return normalized


def _joblib_module() -> Any:
    try:
        import joblib
    except ImportError as exc:
        raise RuntimeError("Install requirements-relation-ranker.txt to use relation ranker models") from exc
    return joblib
