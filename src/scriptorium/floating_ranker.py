from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile


FLOATING_RANKER_SCHEMA = "scriptorium-floating-relation-ranker/v1"
FLOATING_FEATURE_VERSION = "comphrdoc-float-pair-v1"
COMPHRDOC_TRAIN_MEMBER = (
    "datasets/Comp-HRDoc/HRDH_MSRA_POD_TRAIN/unified_layout_analysis_train.json"
)


@dataclass(frozen=True)
class FloatingRankerTrainingResult:
    model_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class FloatingRankerPredictionResult:
    successor_edges: list[dict[str, Any]]
    graphical_source_count: int
    candidate_pair_count: int
    diagnostics: dict[str, Any]


def train_floating_relation_ranker(
    annotation_archive: str | Path,
    output: str | Path,
    *,
    calibration_fraction: float = 0.2,
    negative_candidates: int = 12,
    random_seed: int = 29,
) -> FloatingRankerTrainingResult:
    """Train a float/caption pair gate from Comp-HRDoc official train only."""

    if not 0.05 <= calibration_fraction <= 0.5:
        raise ValueError("calibration_fraction must be between 0.05 and 0.5")
    if negative_candidates < 1:
        raise ValueError("negative_candidates must be at least 1")
    archive_path = Path(annotation_archive)
    archive_bytes = archive_path.read_bytes()
    with ZipFile(BytesIO(archive_bytes)) as archive:
        train_bytes = archive.read(COMPHRDOC_TRAIN_MEMBER)
    payload = json.loads(train_bytes)
    pages = _training_pages(payload)
    fit_pages, calibration_pages = _document_hash_split(
        pages,
        calibration_fraction=calibration_fraction,
    )
    features, labels = _training_examples(fit_pages, negative_candidates=negative_candidates)
    estimator, sklearn_version = _fit_estimator(features, labels, random_seed=random_seed)
    threshold, calibration = _calibrate_threshold(estimator, calibration_pages)

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema": FLOATING_RANKER_SCHEMA,
        "feature_version": FLOATING_FEATURE_VERSION,
        "threshold": threshold,
        "estimator": estimator,
    }
    joblib = _joblib_module()
    joblib.dump(bundle, output_path)
    model_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
    manifest = {
        "schema": FLOATING_RANKER_SCHEMA,
        "feature_version": FLOATING_FEATURE_VERSION,
        "dataset": "Comp-HRDoc",
        "dataset_split": "official train only",
        "dataset_repository": "https://github.com/microsoft/CompHRDoc",
        "dataset_license": "MIT",
        "annotation_archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "train_member": COMPHRDOC_TRAIN_MEMBER,
        "train_member_sha256": hashlib.sha256(train_bytes).hexdigest(),
        "split_policy": "document-id-hash-fit-calibration",
        "fit_page_count": len(fit_pages),
        "calibration_page_count": len(calibration_pages),
        "training_example_count": len(labels),
        "training_positive_count": int(sum(labels)),
        "negative_candidates_per_graphical_block": negative_candidates,
        "calibration_fraction": calibration_fraction,
        "threshold": threshold,
        "calibration": calibration,
        "random_seed": random_seed,
        "scikit_learn_version": sklearn_version,
        "model_sha256": model_sha256,
        "model_file": output_path.name,
        "security": "Load only locally generated model files; joblib loading can execute code.",
    }
    manifest_path = output_path.with_suffix(f"{output_path.suffix}.manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return FloatingRankerTrainingResult(output_path, manifest_path, manifest)


def predict_floating_relations(
    payload: Mapping[str, Any],
    model_path: str | Path,
) -> FloatingRankerPredictionResult:
    bundle, manifest = load_floating_relation_ranker(model_path)
    return _predict_floating_relations(payload, bundle=bundle, manifest=manifest)


def _predict_floating_relations(
    payload: Mapping[str, Any],
    *,
    bundle: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> FloatingRankerPredictionResult:
    if any(key in payload for key in ("ro_linkings", "successor_edges", "reading_order_edges")):
        raise ValueError("floating ranker input must not contain answer relations")
    width = _positive_float(payload.get("img", {}).get("width"), "img.width")
    height = _positive_float(payload.get("img", {}).get("height"), "img.height")
    blocks = _inference_blocks(payload.get("document"))
    graphical = [block for block in blocks if block["kind"] in {"figure", "table"}]
    text_blocks = [block for block in blocks if block["kind"] == "text" and block["text"]]
    estimator = bundle["estimator"]
    threshold = float(bundle["threshold"])
    ranked: list[tuple[float, float, dict[str, Any], dict[str, Any]]] = []
    candidate_count = 0
    for source in graphical:
        if not text_blocks:
            continue
        rows = [_pair_features(source, target, width=width, height=height) for target in text_blocks]
        scores = [float(row[1]) for row in estimator.predict_proba(rows)]
        candidate_count += len(scores)
        order = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
        best = order[0]
        second_score = scores[order[1]] if len(order) > 1 else 0.0
        ranked.append((scores[best], scores[best] - second_score, source, text_blocks[best]))

    claimed_graphical: set[Any] = set()
    claimed_caption_blocks: set[Any] = set()
    edges: list[dict[str, Any]] = []
    for confidence, margin, graphical_block, caption_block in sorted(
        ranked,
        key=lambda item: (item[1], item[0]),
        reverse=True,
    ):
        if confidence < threshold:
            continue
        if graphical_block["block_id"] in claimed_graphical or caption_block["block_id"] in claimed_caption_blocks:
            continue
        claimed_graphical.add(graphical_block["block_id"])
        claimed_caption_blocks.add(caption_block["block_id"])
        if graphical_block["kind"] == "figure":
            source_id, target_id = graphical_block["first_id"], caption_block["first_id"]
        else:
            source_id, target_id = caption_block["last_id"], graphical_block["first_id"]
        edges.append(
            {
                "source": source_id,
                "target": target_id,
                "kind": "successor",
                "confidence": round(confidence, 8),
                "selection_margin": round(margin, 8),
                "review_required": True,
                "relation_policy": "review-only",
                "provider": "scriptorium-trained-floating-ranker",
                "relation_origin": "trained-floating-pair",
            }
        )
    return FloatingRankerPredictionResult(
        edges,
        len(graphical),
        candidate_count,
        {
            "feature_version": FLOATING_FEATURE_VERSION,
            "threshold": threshold,
            "model_sha256": manifest.get("model_sha256"),
            "selected_edge_count": len(edges),
            "runtime_reorder": False,
        },
    )


def load_floating_relation_ranker(model_path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(model_path)
    manifest_path = path.with_suffix(f"{path.suffix}.manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        raise ValueError("floating model and adjacent .manifest.json are required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get("model_sha256"):
        raise ValueError("floating ranker model hash does not match its manifest")
    bundle = _joblib_module().load(path)
    if not isinstance(bundle, dict) or bundle.get("schema") != FLOATING_RANKER_SCHEMA:
        raise ValueError("unsupported floating relation ranker schema")
    if bundle.get("feature_version") != FLOATING_FEATURE_VERSION:
        raise ValueError("unsupported floating relation feature version")
    return bundle, manifest


def _training_pages(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    images = {int(image["id"]): image for image in payload.get("images", []) if isinstance(image, Mapping)}
    annotations_by_image: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for annotation in payload.get("annotations", []):
        if isinstance(annotation, dict):
            annotations_by_image[int(annotation.get("image_id", -1))].append(annotation)
    pages: list[dict[str, Any]] = []
    for image_id, annotations in annotations_by_image.items():
        image = images.get(image_id)
        if image is None:
            continue
        blocks = [_annotation_block(annotation) for annotation in annotations]
        blocks = [block for block in blocks if block is not None]
        groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            groups[block["reading_order_id"]].append(block)
        positives: set[tuple[Any, Any]] = set()
        for group in groups.values():
            if not any(block["reading_order_label"] == 2 for block in group):
                continue
            graphical = [block for block in group if block["kind"] in {"figure", "table"}]
            captions = [block for block in group if block["kind"] == "text" and block["text"]]
            if len(graphical) == 1 and len(captions) == 1:
                positives.add((graphical[0]["block_id"], captions[0]["block_id"]))
        if not positives:
            continue
        file_name = str(image.get("file_name") or "")
        pages.append(
            {
                "uid": file_name,
                "document_id": Path(file_name).stem.rsplit("_", 1)[0],
                "width": float(image["width"]),
                "height": float(image["height"]),
                "blocks": blocks,
                "positives": positives,
            }
        )
    return pages


def _annotation_block(annotation: Mapping[str, Any]) -> dict[str, Any] | None:
    bbox = _xywh_bbox(annotation.get("bbox"))
    if bbox is None:
        return None
    category = int(annotation.get("category_id", -1))
    kind = {1: "figure", 2: "table"}.get(category, "text")
    contents = [str(item or "").strip() for item in annotation.get("textline_contents", [])]
    contents = [item for item in contents if item]
    return {
        "block_id": int(annotation.get("in_page_id", -1)),
        "kind": kind,
        "box": bbox,
        "text": " ".join(contents),
        "line_count": len(contents),
        "reading_order_id": int(annotation.get("reading_order_id", -1)),
        "reading_order_label": int(annotation.get("reading_order_label", 0)),
    }


def _inference_blocks(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, list):
        raise ValueError("floating ranker input must contain a document list")
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for index, segment in enumerate(document):
        if not isinstance(segment, Mapping) or "id" not in segment:
            continue
        box = segment.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        normalized = dict(segment)
        normalized["box"] = [float(value) for value in box]
        grouped[segment.get("block_id", f"segment-{index}")].append(normalized)
    blocks: list[dict[str, Any]] = []
    for block_id, members in grouped.items():
        ordered = sorted(members, key=lambda item: (item["box"][1], item["box"][0]))
        kinds = {_segment_kind(member) for member in ordered}
        kind = next((item for item in ("figure", "table") if item in kinds), "text")
        boxes = [member["box"] for member in ordered]
        blocks.append(
            {
                "block_id": block_id,
                "kind": kind,
                "box": [
                    min(box[0] for box in boxes),
                    min(box[1] for box in boxes),
                    max(box[2] for box in boxes),
                    max(box[3] for box in boxes),
                ],
                "text": " ".join(str(member.get("text") or "").strip() for member in ordered).strip(),
                "line_count": len(ordered),
                "first_id": ordered[0]["id"],
                "last_id": ordered[-1]["id"],
            }
        )
    return blocks


def _document_hash_split(
    pages: Sequence[dict[str, Any]],
    *,
    calibration_fraction: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    boundary = int(calibration_fraction * 10_000)
    fit: list[dict[str, Any]] = []
    calibration: list[dict[str, Any]] = []
    for page in pages:
        bucket = int.from_bytes(
            hashlib.sha256(page["document_id"].encode("utf-8")).digest()[:4], "big"
        ) % 10_000
        (calibration if bucket < boundary else fit).append(page)
    if not fit or not calibration:
        raise ValueError("document hash calibration split produced an empty partition")
    return fit, calibration


def _training_examples(
    pages: Sequence[dict[str, Any]],
    *,
    negative_candidates: int,
) -> tuple[list[list[float]], list[int]]:
    features: list[list[float]] = []
    labels: list[int] = []
    for page in pages:
        graphical = [block for block in page["blocks"] if block["kind"] in {"figure", "table"}]
        texts = [block for block in page["blocks"] if block["kind"] == "text" and block["text"]]
        for source in graphical:
            ranked = sorted(
                texts,
                key=lambda target: (
                    (source["block_id"], target["block_id"]) not in page["positives"],
                    _center_distance(source, target, page["width"], page["height"]),
                ),
            )
            negatives = 0
            for target in ranked:
                positive = (source["block_id"], target["block_id"]) in page["positives"]
                if not positive and negatives >= negative_candidates:
                    continue
                features.append(_pair_features(source, target, width=page["width"], height=page["height"]))
                labels.append(int(positive))
                negatives += int(not positive)
    return features, labels


def _calibrate_threshold(estimator: Any, pages: Sequence[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    records: list[tuple[float, bool]] = []
    label_count = sum(len(page["positives"]) for page in pages)
    for page in pages:
        graphical = [block for block in page["blocks"] if block["kind"] in {"figure", "table"}]
        texts = [block for block in page["blocks"] if block["kind"] == "text" and block["text"]]
        for source in graphical:
            if not texts:
                continue
            rows = [_pair_features(source, target, width=page["width"], height=page["height"]) for target in texts]
            scores = [float(row[1]) for row in estimator.predict_proba(rows)]
            best = max(range(len(scores)), key=scores.__getitem__)
            records.append((scores[best], (source["block_id"], texts[best]["block_id"]) in page["positives"]))
    best_result: tuple[float, float, float, int, int] | None = None
    for step in range(5, 100):
        threshold = step / 100
        selected = [correct for score, correct in records if score >= threshold]
        predicted = len(selected)
        correct = sum(selected)
        precision = correct / predicted if predicted else 0.0
        recall = correct / label_count if label_count else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        candidate = (f1, precision, threshold, predicted, correct)
        if best_result is None or candidate > best_result:
            best_result = candidate
    assert best_result is not None
    f1, precision, threshold, predicted, correct = best_result
    recall = correct / label_count if label_count else 0.0
    return threshold, {
        "page_count": len(pages),
        "label_count": label_count,
        "predicted_count": predicted,
        "correct_count": correct,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
    }


def _pair_features(source: Mapping[str, Any], target: Mapping[str, Any], *, width: float, height: float) -> list[float]:
    sx0, sy0, sx1, sy1 = map(float, source["box"])
    tx0, ty0, tx1, ty1 = map(float, target["box"])
    sw, sh = max(sx1 - sx0, 1.0), max(sy1 - sy0, 1.0)
    tw, th = max(tx1 - tx0, 1.0), max(ty1 - ty0, 1.0)
    overlap = max(0.0, min(sx1, tx1) - max(sx0, tx0)) / max(1.0, min(sw, tw))
    text = str(target.get("text") or "").strip().casefold()
    return [
        float(source["kind"] == "figure"),
        float(source["kind"] == "table"),
        sx0 / width,
        sy0 / height,
        sx1 / width,
        sy1 / height,
        tx0 / width,
        ty0 / height,
        tx1 / width,
        ty1 / height,
        (((tx0 + tx1) - (sx0 + sx1)) / 2) / width,
        (((ty0 + ty1) - (sy0 + sy1)) / 2) / height,
        abs(((tx0 + tx1) - (sx0 + sx1)) / 2) / width,
        abs(((ty0 + ty1) - (sy0 + sy1)) / 2) / height,
        sw / width,
        sh / height,
        tw / width,
        th / height,
        overlap,
        float(ty0 >= sy1),
        float(sy0 >= ty1),
        min(int(target.get("line_count", 1)), 16) / 16,
        math.log1p(len(text)) / 8,
        float(text.startswith(("figure ", "fig. ", "fig "))),
        float(text.startswith("table ")),
        float(text.startswith(("\u56fe ", "\u56fe\u8868 "))),
        float(text.startswith("\u8868 ")),
    ]


def _fit_estimator(features: Sequence[Sequence[float]], labels: Sequence[int], *, random_seed: int) -> tuple[Any, str]:
    try:
        import sklearn
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError as exc:
        raise RuntimeError("Install requirements-relation-ranker.txt to train a floating ranker") from exc
    estimator = HistGradientBoostingClassifier(
        max_iter=180,
        max_leaf_nodes=31,
        learning_rate=0.08,
        l2_regularization=1.5,
        class_weight="balanced",
        random_state=random_seed,
    )
    estimator.fit(features, labels)
    return estimator, sklearn.__version__


def _xywh_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 4:
        return None
    x, y, width, height = map(float, value[:4])
    if width <= 0 or height <= 0:
        return None
    return [x, y, x + width, y + height]


def _segment_kind(segment: Mapping[str, Any]) -> str:
    value = str(segment.get("type") or segment.get("block_label") or "").lower()
    return {"fig": "figure", "image": "figure", "tab": "table"}.get(value, value)


def _center_distance(source: Mapping[str, Any], target: Mapping[str, Any], width: float, height: float) -> float:
    sx0, sy0, sx1, sy1 = map(float, source["box"])
    tx0, ty0, tx1, ty1 = map(float, target["box"])
    return abs(tx0 + tx1 - sx0 - sx1) / (2 * width) + abs(ty0 + ty1 - sy0 - sy1) / (2 * height)


def _positive_float(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a positive number")
    return number


def _joblib_module() -> Any:
    try:
        import joblib
    except ImportError as exc:
        raise RuntimeError("Install requirements-relation-ranker.txt to use floating ranker models") from exc
    return joblib
