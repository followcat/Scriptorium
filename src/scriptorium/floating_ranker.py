from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from .bipartite_matching import maximum_weight_bipartite_matching
from .relation_noise import RelationNoiseProfile, perturb_relation_structure


FLOATING_RANKER_SCHEMA = "scriptorium-floating-relation-ranker/v1"
FLOATING_FEATURE_VERSION = "comphrdoc-float-pair-v1"
FLOATING_ASSIGNMENT_POLICY_LEGACY = "source-best-margin-greedy-v1"
FLOATING_ASSIGNMENT_POLICY_GLOBAL = "global-cardinality-weight-v1"
FLOATING_MARGIN_POLICY_LEGACY = "source-score-gap-v1"
FLOATING_MARGIN_POLICY_GLOBAL = "min-row-column-score-gap-v1"
FLOATING_CORRECTNESS_FEATURE_VERSION = "comphrdoc-float-correctness-v2"
FLOATING_CORRECTNESS_POLICY = "crossfit-noise-selective-v1"
FLOATING_CORRECTNESS_PROFILES: tuple[RelationNoiseProfile, ...] = (
    "clean",
    "mild",
    "stress",
)
FLOATING_CORRECTNESS_CROSSFIT_FOLDS = 4
FLOATING_CORRECTNESS_FEATURE_NAMES = (
    "pair_confidence",
    "selection_margin",
    "source_competitor_score",
    "target_competitor_score",
    "source_score_gap",
    "target_score_gap",
    "alternative_cardinality_loss",
    "alternative_assignment_score_gap",
    "pair_feature_outlier_ratio",
    "log_graphical_count",
    "log_text_count",
    "selected_source_coverage",
)
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


@dataclass(frozen=True)
class _FloatingCorrectnessRecord:
    features: tuple[float, ...]
    correct: bool
    profile: RelationNoiseProfile


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
    feature_envelope = _feature_envelope(features)
    estimator, sklearn_version = _fit_estimator(features, labels, random_seed=random_seed)
    scored_calibration_pages = _score_training_pages(
        estimator,
        calibration_pages,
    )
    threshold, calibration, calibration_records = _calibrate_global_assignment_threshold(
        scored_calibration_pages,
        page_count=len(calibration_pages),
    )
    calibration_label_count = int(calibration["label_count"])
    reliability_gate = _calibrate_reliability_gate(
        calibration_records,
        label_count=calibration_label_count,
        minimum_precision=0.95,
    )
    promotion_gate = _calibrate_reliability_gate(
        calibration_records,
        label_count=calibration_label_count,
        minimum_precision=0.97,
    )
    correctness_records, correctness_crossfit = _crossfit_correctness_records(
        fit_pages,
        threshold=threshold,
        negative_candidates=negative_candidates,
        random_seed=random_seed,
        fold_count=FLOATING_CORRECTNESS_CROSSFIT_FOLDS,
    )
    correctness_estimator = _fit_correctness_estimator(
        correctness_records,
        random_seed=random_seed,
    )
    correctness_calibration_records = _noise_correctness_records(
        estimator,
        calibration_pages,
        threshold=threshold,
        feature_envelope=feature_envelope,
    )
    scored_correctness_calibration = _score_correctness_records(
        correctness_estimator,
        correctness_calibration_records,
    )
    correctness_label_counts = {
        profile: sum(len(page["positives"]) for page in calibration_pages)
        for profile in FLOATING_CORRECTNESS_PROFILES
    }
    noise_aware_reliability_gate = _calibrate_noise_aware_gate(
        scored_correctness_calibration,
        label_counts=correctness_label_counts,
        minimum_precision=0.95,
        base_gate=reliability_gate,
    )
    noise_aware_promotion_gate = _calibrate_noise_aware_gate(
        scored_correctness_calibration,
        label_counts=correctness_label_counts,
        minimum_precision=0.97,
        base_gate=promotion_gate,
    )
    correctness_calibration = _correctness_calibration_summary(
        scored_correctness_calibration,
        label_counts=correctness_label_counts,
    )

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema": FLOATING_RANKER_SCHEMA,
        "feature_version": FLOATING_FEATURE_VERSION,
        "assignment_policy": FLOATING_ASSIGNMENT_POLICY_GLOBAL,
        "selection_margin_policy": FLOATING_MARGIN_POLICY_GLOBAL,
        "threshold": threshold,
        "reliability_gate": reliability_gate,
        "promotion_gate": promotion_gate,
        "estimator": estimator,
        "feature_envelope": feature_envelope,
        "correctness_feature_version": FLOATING_CORRECTNESS_FEATURE_VERSION,
        "correctness_policy": FLOATING_CORRECTNESS_POLICY,
        "correctness_estimator": correctness_estimator,
        "noise_aware_reliability_gate": noise_aware_reliability_gate,
        "noise_aware_promotion_gate": noise_aware_promotion_gate,
    }
    joblib = _joblib_module()
    joblib.dump(bundle, output_path)
    model_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
    manifest = {
        "schema": FLOATING_RANKER_SCHEMA,
        "feature_version": FLOATING_FEATURE_VERSION,
        "assignment_policy": FLOATING_ASSIGNMENT_POLICY_GLOBAL,
        "selection_margin_policy": FLOATING_MARGIN_POLICY_GLOBAL,
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
        "feature_count": len(feature_envelope["lower"]),
        "feature_envelope_quantiles": [0.01, 0.99],
        "negative_candidates_per_graphical_block": negative_candidates,
        "calibration_fraction": calibration_fraction,
        "threshold": threshold,
        "calibration": calibration,
        "reliability_gate": reliability_gate,
        "promotion_gate": promotion_gate,
        "correctness_model": {
            "feature_version": FLOATING_CORRECTNESS_FEATURE_VERSION,
            "policy": FLOATING_CORRECTNESS_POLICY,
            "profiles": list(FLOATING_CORRECTNESS_PROFILES),
            "crossfit_fold_count": FLOATING_CORRECTNESS_CROSSFIT_FOLDS,
            "feature_count": len(correctness_records[0].features),
            "feature_names": list(FLOATING_CORRECTNESS_FEATURE_NAMES),
            "fit_record_count": len(correctness_records),
            "fit_correct_count": sum(
                int(record.correct) for record in correctness_records
            ),
            "crossfit": correctness_crossfit,
            "calibration": correctness_calibration,
        },
        "noise_aware_reliability_gate": noise_aware_reliability_gate,
        "noise_aware_promotion_gate": noise_aware_promotion_gate,
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
    assignment_policy = _floating_assignment_policy(bundle)
    graphical = [block for block in blocks if block["kind"] in {"figure", "table"}]
    text_blocks = [block for block in blocks if block["kind"] == "text" and block["text"]]
    if assignment_policy == FLOATING_ASSIGNMENT_POLICY_GLOBAL:
        graphical = sorted(graphical, key=_block_geometry_key)
        text_blocks = sorted(text_blocks, key=_block_geometry_key)
    estimator = bundle["estimator"]
    threshold = float(bundle["threshold"])
    reliability_gate = bundle.get("reliability_gate") or {}
    reliability_confidence = float(reliability_gate.get("confidence_threshold", 1.1))
    reliability_margin = float(reliability_gate.get("margin_threshold", 1.1))
    promotion_gate = bundle.get("promotion_gate") or {}
    promotion_confidence = float(promotion_gate.get("confidence_threshold", 1.1))
    promotion_margin = float(promotion_gate.get("margin_threshold", 1.1))
    feature_rows, score_rows = _score_pair_matrix(
        estimator,
        graphical,
        text_blocks,
        width=width,
        height=height,
    )
    candidate_count = sum(len(row) for row in score_rows)
    if assignment_policy == FLOATING_ASSIGNMENT_POLICY_GLOBAL:
        ranked = _global_assignment_pairs(
            graphical,
            text_blocks,
            feature_rows,
            score_rows,
            threshold=threshold,
        )
    else:
        ranked = _legacy_assignment_pairs(
            graphical,
            text_blocks,
            feature_rows,
            score_rows,
            threshold=threshold,
        )
    correctness_metadata: list[dict[str, Any] | None] = [None] * len(ranked)
    correctness_estimator = bundle.get("correctness_estimator")
    if correctness_estimator is not None and ranked:
        source_indices = {id(block): index for index, block in enumerate(graphical)}
        target_indices = {id(block): index for index, block in enumerate(text_blocks)}
        assignment_contexts = _global_assignment_contexts(
            score_rows,
            threshold=threshold,
        )
        correctness_rows = []
        contexts = []
        for _, _, graphical_block, caption_block, pair_features in ranked:
            context = assignment_contexts[
                (source_indices[id(graphical_block)], target_indices[id(caption_block)])
            ]
            contexts.append(context)
            correctness_rows.append(
                _correctness_features(
                    context,
                    pair_features,
                    feature_envelope=bundle.get("feature_envelope"),
                    graphical_count=len(graphical),
                    text_count=len(text_blocks),
                    selected_count=len(ranked),
                )
            )
        correctness_scores = [
            float(row[1])
            for row in correctness_estimator.predict_proba(correctness_rows)
        ]
        review_gate = bundle.get("noise_aware_reliability_gate") or {}
        strict_gate = bundle.get("noise_aware_promotion_gate") or {}
        review_threshold = float(review_gate.get("confidence_threshold", 1.1))
        strict_threshold = float(strict_gate.get("confidence_threshold", 1.1))
        correctness_metadata = [
            {
                "correctness_confidence": round(score, 8),
                "noise_aware_reliability_tier": (
                    "robust-high-precision-review"
                    if review_gate.get("available")
                    and float(context["confidence"]) >= reliability_confidence
                    and float(context["margin"]) >= reliability_margin
                    and score >= review_threshold
                    else "robust-standard-review"
                ),
                "noise_aware_strict_gate_passed": bool(
                    strict_gate.get("available")
                    and float(context["confidence"]) >= promotion_confidence
                    and float(context["margin"]) >= promotion_margin
                    and score >= strict_threshold
                ),
                "assignment_alternative_cardinality_loss": int(
                    context["alternative_cardinality_loss"]
                ),
                "assignment_alternative_score_gap": round(
                    float(context["alternative_score_gap"]),
                    8,
                ),
                "selection_source_margin": round(
                    float(context["source_margin"]),
                    8,
                ),
                "selection_target_margin": round(
                    float(context["target_margin"]),
                    8,
                ),
            }
            for score, context in zip(correctness_scores, contexts, strict=True)
        ]
    edges: list[dict[str, Any]] = []
    for (
        confidence,
        margin,
        graphical_block,
        caption_block,
        pair_features,
    ), correctness in zip(ranked, correctness_metadata, strict=True):
        outlier_count, outlier_ratio = _feature_outliers(
            pair_features,
            bundle.get("feature_envelope"),
        )
        if graphical_block["kind"] == "figure":
            source_id, target_id = graphical_block["first_id"], caption_block["first_id"]
        else:
            source_id, target_id = caption_block["last_id"], graphical_block["first_id"]
        edge = {
            "source": source_id,
            "target": target_id,
            "kind": "successor",
            "confidence": round(confidence, 8),
            "selection_margin": round(margin, 8),
            "review_required": True,
            "relation_policy": "review-only",
            "provider": "scriptorium-trained-floating-ranker",
            "relation_origin": "trained-floating-pair",
            "reliability_tier": (
                "high-precision-review"
                if confidence >= reliability_confidence and margin >= reliability_margin
                else "standard-review"
            ),
            "strict_gate_passed": bool(
                promotion_gate.get("available")
                and confidence >= promotion_confidence
                and margin >= promotion_margin
            ),
            "feature_outlier_count": outlier_count,
            "feature_outlier_ratio": outlier_ratio,
        }
        if correctness is not None:
            edge.update(correctness)
        edges.append(edge)
    return FloatingRankerPredictionResult(
        edges,
        len(graphical),
        candidate_count,
        {
            "feature_version": FLOATING_FEATURE_VERSION,
            "assignment_policy": assignment_policy,
            "selection_margin_policy": (
                FLOATING_MARGIN_POLICY_GLOBAL
                if assignment_policy == FLOATING_ASSIGNMENT_POLICY_GLOBAL
                else FLOATING_MARGIN_POLICY_LEGACY
            ),
            "threshold": threshold,
            "reliability_gate": reliability_gate,
            "promotion_gate": promotion_gate,
            "correctness_feature_version": bundle.get(
                "correctness_feature_version"
            ),
            "correctness_policy": bundle.get("correctness_policy"),
            "noise_aware_reliability_gate": bundle.get(
                "noise_aware_reliability_gate"
            ),
            "noise_aware_promotion_gate": bundle.get(
                "noise_aware_promotion_gate"
            ),
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
    assignment_policy = _floating_assignment_policy(bundle)
    manifest_policy = manifest.get("assignment_policy")
    if manifest_policy is not None and str(manifest_policy) != assignment_policy:
        raise ValueError("floating assignment policy does not match its manifest")
    if assignment_policy == FLOATING_ASSIGNMENT_POLICY_GLOBAL:
        margin_policy = str(bundle.get("selection_margin_policy") or "")
        if margin_policy != FLOATING_MARGIN_POLICY_GLOBAL:
            raise ValueError("unsupported floating selection margin policy")
    correctness_estimator = bundle.get("correctness_estimator")
    manifest_correctness = manifest.get("correctness_model")
    if correctness_estimator is not None:
        if assignment_policy != FLOATING_ASSIGNMENT_POLICY_GLOBAL:
            raise ValueError("floating correctness model requires global assignment")
        if bundle.get("correctness_feature_version") != FLOATING_CORRECTNESS_FEATURE_VERSION:
            raise ValueError("unsupported floating correctness feature version")
        if bundle.get("correctness_policy") != FLOATING_CORRECTNESS_POLICY:
            raise ValueError("unsupported floating correctness policy")
        if not isinstance(manifest_correctness, Mapping):
            raise ValueError("floating correctness model manifest is required")
        if manifest_correctness.get("feature_version") != FLOATING_CORRECTNESS_FEATURE_VERSION:
            raise ValueError("floating correctness feature version does not match its manifest")
        if manifest_correctness.get("policy") != FLOATING_CORRECTNESS_POLICY:
            raise ValueError("floating correctness policy does not match its manifest")
        if manifest_correctness.get("feature_names") != list(
            FLOATING_CORRECTNESS_FEATURE_NAMES
        ):
            raise ValueError("floating correctness feature names do not match")
        for gate_name in (
            "noise_aware_reliability_gate",
            "noise_aware_promotion_gate",
        ):
            if not isinstance(bundle.get(gate_name), Mapping):
                raise ValueError(f"floating {gate_name} is required")
    elif manifest_correctness is not None:
        raise ValueError("floating correctness estimator is missing from its model")
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
    raw_contents = annotation.get("textline_contents", [])
    contents = [str(item or "").strip() for item in raw_contents]
    contents = [item for item in contents if item]
    noise_segments: list[dict[str, Any]] = []
    if kind == "text":
        polygons = annotation.get("textline_polys", [])
        if isinstance(raw_contents, list) and isinstance(polygons, list):
            for content, polygon in zip(raw_contents, polygons, strict=False):
                text = str(content or "").strip()
                line_box = _polygon_bbox(polygon)
                if text and line_box is not None:
                    noise_segments.append({"box": line_box, "text": text})
        if not noise_segments and contents:
            noise_segments.append({"box": bbox, "text": " ".join(contents)})
    else:
        noise_segments.append({"box": bbox, "text": ""})
    return {
        "block_id": int(annotation.get("in_page_id", -1)),
        "kind": kind,
        "box": bbox,
        "text": " ".join(contents),
        "line_count": len(contents),
        "noise_segments": noise_segments,
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


def _floating_assignment_policy(bundle: Mapping[str, Any]) -> str:
    policy = str(
        bundle.get("assignment_policy") or FLOATING_ASSIGNMENT_POLICY_LEGACY
    )
    if policy not in {
        FLOATING_ASSIGNMENT_POLICY_LEGACY,
        FLOATING_ASSIGNMENT_POLICY_GLOBAL,
    }:
        raise ValueError(f"unsupported floating assignment policy: {policy}")
    return policy


def _block_geometry_key(block: Mapping[str, Any]) -> tuple[float, float, str]:
    box = block["box"]
    return float(box[1]), float(box[0]), str(block.get("block_id"))


def _score_pair_matrix(
    estimator: Any,
    graphical: Sequence[Mapping[str, Any]],
    text_blocks: Sequence[Mapping[str, Any]],
    *,
    width: float,
    height: float,
) -> tuple[list[list[list[float]]], list[list[float]]]:
    feature_rows: list[list[list[float]]] = []
    score_rows: list[list[float]] = []
    for source in graphical:
        features = [
            _pair_features(source, target, width=width, height=height)
            for target in text_blocks
        ]
        scores = (
            [float(row[1]) for row in estimator.predict_proba(features)]
            if features
            else []
        )
        feature_rows.append(features)
        score_rows.append(scores)
    return feature_rows, score_rows


def _legacy_assignment_pairs(
    graphical: Sequence[dict[str, Any]],
    text_blocks: Sequence[dict[str, Any]],
    feature_rows: Sequence[Sequence[list[float]]],
    score_rows: Sequence[Sequence[float]],
    *,
    threshold: float,
) -> list[tuple[float, float, dict[str, Any], dict[str, Any], list[float]]]:
    ranked: list[tuple[float, float, dict[str, Any], dict[str, Any], list[float]]] = []
    for source_index, source in enumerate(graphical):
        scores = score_rows[source_index]
        if not scores:
            continue
        order = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
        best = order[0]
        second_score = scores[order[1]] if len(order) > 1 else 0.0
        ranked.append(
            (
                scores[best],
                scores[best] - second_score,
                source,
                text_blocks[best],
                feature_rows[source_index][best],
            )
        )

    claimed_graphical: set[Any] = set()
    claimed_caption_blocks: set[Any] = set()
    selected = []
    for candidate in sorted(
        ranked,
        key=lambda item: (item[1], item[0]),
        reverse=True,
    ):
        confidence, _, graphical_block, caption_block, _ = candidate
        if confidence < threshold:
            continue
        if (
            graphical_block["block_id"] in claimed_graphical
            or caption_block["block_id"] in claimed_caption_blocks
        ):
            continue
        claimed_graphical.add(graphical_block["block_id"])
        claimed_caption_blocks.add(caption_block["block_id"])
        selected.append(candidate)
    return selected


def _global_assignment_pairs(
    graphical: Sequence[dict[str, Any]],
    text_blocks: Sequence[dict[str, Any]],
    feature_rows: Sequence[Sequence[list[float]]],
    score_rows: Sequence[Sequence[float]],
    *,
    threshold: float,
) -> list[tuple[float, float, dict[str, Any], dict[str, Any], list[float]]]:
    selected = []
    for match in maximum_weight_bipartite_matching(
        score_rows,
        minimum_score=threshold,
    ):
        source_index = match.left_index
        target_index = match.right_index
        selected.append(
            (
                match.score,
                _global_selection_margin(score_rows, source_index, target_index),
                graphical[source_index],
                text_blocks[target_index],
                feature_rows[source_index][target_index],
            )
        )
    return selected


def _global_selection_margin(
    score_rows: Sequence[Sequence[float]],
    source_index: int,
    target_index: int,
) -> float:
    selected_score = float(score_rows[source_index][target_index])
    row_competitor = max(
        (
            float(score)
            for index, score in enumerate(score_rows[source_index])
            if index != target_index
        ),
        default=0.0,
    )
    column_competitor = max(
        (
            float(row[target_index])
            for index, row in enumerate(score_rows)
            if index != source_index
        ),
        default=0.0,
    )
    return selected_score - max(row_competitor, column_competitor)


def _global_assignment_contexts(
    score_rows: Sequence[Sequence[float]],
    *,
    threshold: float,
) -> dict[tuple[int, int], dict[str, float | int]]:
    matches = list(
        maximum_weight_bipartite_matching(
            score_rows,
            minimum_score=threshold,
        )
    )
    base_total = sum(float(match.score) for match in matches)
    contexts: dict[tuple[int, int], dict[str, float | int]] = {}
    for match in matches:
        source_index = match.left_index
        target_index = match.right_index
        confidence = float(match.score)
        row_competitor = max(
            (
                float(score)
                for index, score in enumerate(score_rows[source_index])
                if index != target_index
            ),
            default=0.0,
        )
        column_competitor = max(
            (
                float(row[target_index])
                for index, row in enumerate(score_rows)
                if index != source_index
            ),
            default=0.0,
        )
        alternate_scores = [list(row) for row in score_rows]
        alternate_scores[source_index][target_index] = -1.0
        alternate = list(
            maximum_weight_bipartite_matching(
                alternate_scores,
                minimum_score=threshold,
            )
        )
        cardinality_loss = len(matches) - len(alternate)
        alternate_total = sum(float(value.score) for value in alternate)
        contexts[(source_index, target_index)] = {
            "confidence": confidence,
            "margin": confidence - max(row_competitor, column_competitor),
            "row_competitor": row_competitor,
            "column_competitor": column_competitor,
            "source_margin": confidence - row_competitor,
            "target_margin": confidence - column_competitor,
            "alternative_cardinality_loss": cardinality_loss,
            "alternative_score_gap": (
                base_total - alternate_total
                if cardinality_loss == 0
                else confidence
            ),
        }
    return contexts


def _correctness_features(
    context: Mapping[str, float | int],
    pair_features: Sequence[float],
    *,
    feature_envelope: Any,
    graphical_count: int,
    text_count: int,
    selected_count: int,
) -> tuple[float, ...]:
    _, outlier_ratio = _feature_outliers(pair_features, feature_envelope)
    return (
        float(context["confidence"]),
        float(context["margin"]),
        float(context["row_competitor"]),
        float(context["column_competitor"]),
        float(context["source_margin"]),
        float(context["target_margin"]),
        float(int(context["alternative_cardinality_loss"]) > 0),
        float(context["alternative_score_gap"]),
        outlier_ratio,
        math.log1p(graphical_count) / 4,
        math.log1p(text_count) / 8,
        selected_count / max(1, graphical_count),
    )


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


def _crossfit_correctness_records(
    pages: Sequence[dict[str, Any]],
    *,
    threshold: float,
    negative_candidates: int,
    random_seed: int,
    fold_count: int,
) -> tuple[list[_FloatingCorrectnessRecord], dict[str, Any]]:
    if fold_count < 2:
        raise ValueError("floating correctness cross-fit requires at least two folds")
    folds: list[list[dict[str, Any]]] = [[] for _ in range(fold_count)]
    for page in pages:
        digest = hashlib.sha256(
            f"floating-correctness-fold:{page['document_id']}".encode("utf-8")
        ).digest()
        folds[int.from_bytes(digest[:4], "big") % fold_count].append(page)
    if any(not fold for fold in folds):
        raise ValueError("floating correctness cross-fit produced an empty fold")

    records: list[_FloatingCorrectnessRecord] = []
    fold_reports: list[dict[str, Any]] = []
    profile_counts: Counter[str] = Counter()
    profile_correct: Counter[str] = Counter()
    for fold_index, holdout_pages in enumerate(folds):
        fold_fit_pages = [
            page
            for index, fold in enumerate(folds)
            if index != fold_index
            for page in fold
        ]
        features, labels = _training_examples(
            fold_fit_pages,
            negative_candidates=negative_candidates,
        )
        fold_estimator, _ = _fit_estimator(
            features,
            labels,
            random_seed=random_seed,
        )
        fold_records = _noise_correctness_records(
            fold_estimator,
            holdout_pages,
            threshold=threshold,
            feature_envelope=_feature_envelope(features),
        )
        records.extend(fold_records)
        fold_profile_counts = Counter(record.profile for record in fold_records)
        fold_profile_correct = Counter(
            record.profile for record in fold_records if record.correct
        )
        profile_counts.update(fold_profile_counts)
        profile_correct.update(fold_profile_correct)
        fold_reports.append(
            {
                "fold": fold_index,
                "fit_page_count": len(fold_fit_pages),
                "holdout_page_count": len(holdout_pages),
                "record_counts": dict(sorted(fold_profile_counts.items())),
                "correct_counts": dict(sorted(fold_profile_correct.items())),
            }
        )
    if not records or len({record.correct for record in records}) < 2:
        raise ValueError("floating correctness cross-fit needs positive and negative records")
    return records, {
        "split_policy": "document-id-hash-crossfit",
        "folds": fold_reports,
        "record_counts": dict(sorted(profile_counts.items())),
        "correct_counts": dict(sorted(profile_correct.items())),
    }


def _noise_correctness_records(
    estimator: Any,
    pages: Sequence[dict[str, Any]],
    *,
    threshold: float,
    feature_envelope: Any,
) -> list[_FloatingCorrectnessRecord]:
    records: list[_FloatingCorrectnessRecord] = []
    for profile in FLOATING_CORRECTNESS_PROFILES:
        for page in pages:
            graphical, text_blocks, origin_by_segment_id = _training_noise_view(
                page,
                profile=profile,
            )
            feature_rows, score_rows = _score_pair_matrix(
                estimator,
                graphical,
                text_blocks,
                width=page["width"],
                height=page["height"],
            )
            contexts = _global_assignment_contexts(
                score_rows,
                threshold=threshold,
            )
            selected_count = len(contexts)
            for (source_index, target_index), context in contexts.items():
                source = graphical[source_index]
                target = text_blocks[target_index]
                source_origin = origin_by_segment_id[source["first_id"]]
                target_origin = origin_by_segment_id[target["first_id"]]
                pair_features = feature_rows[source_index][target_index]
                records.append(
                    _FloatingCorrectnessRecord(
                        _correctness_features(
                            context,
                            pair_features,
                            feature_envelope=feature_envelope,
                            graphical_count=len(graphical),
                            text_count=len(text_blocks),
                            selected_count=selected_count,
                        ),
                        (source_origin, target_origin) in page["positives"],
                        profile,
                    )
                )
    return records


def _training_noise_view(
    page: Mapping[str, Any],
    *,
    profile: RelationNoiseProfile,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    document: list[dict[str, Any]] = []
    origin_by_segment_id: dict[str, Any] = {}
    for block_position, block in enumerate(page["blocks"]):
        segments = block.get("noise_segments")
        if not isinstance(segments, list) or not segments:
            segments = [{"box": block["box"], "text": block.get("text", "")}]
        synthetic_block_id = f"training-block-{block_position:04d}"
        for segment_position, segment in enumerate(segments):
            segment_id = f"{synthetic_block_id}-line-{segment_position:04d}"
            origin_by_segment_id[segment_id] = block["block_id"]
            document.append(
                {
                    "id": segment_id,
                    "block_id": synthetic_block_id,
                    "type": block["kind"],
                    "box": list(segment["box"]),
                    "text": str(segment.get("text") or ""),
                }
            )
    perturbed, _ = perturb_relation_structure(
        {
            "uid": page["uid"],
            "img": {"width": page["width"], "height": page["height"]},
            "document": document,
        },
        profile=profile,
    )
    blocks = _inference_blocks(perturbed["document"])
    graphical = sorted(
        [block for block in blocks if block["kind"] in {"figure", "table"}],
        key=_block_geometry_key,
    )
    text_blocks = sorted(
        [block for block in blocks if block["kind"] == "text" and block["text"]],
        key=_block_geometry_key,
    )
    return graphical, text_blocks, origin_by_segment_id


def _fit_correctness_estimator(
    records: Sequence[_FloatingCorrectnessRecord],
    *,
    random_seed: int,
) -> Any:
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise RuntimeError(
            "Install requirements-relation-ranker.txt to train floating correctness"
        ) from exc
    estimator = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=0.5,
            class_weight="balanced",
            max_iter=1000,
            random_state=random_seed + 14,
        ),
    )
    estimator.fit(
        [record.features for record in records],
        [int(record.correct) for record in records],
    )
    return estimator


def _score_correctness_records(
    estimator: Any,
    records: Sequence[_FloatingCorrectnessRecord],
) -> dict[str, list[tuple[float, bool, float, float]]]:
    if not records:
        return {}
    scores = [
        float(row[1])
        for row in estimator.predict_proba([record.features for record in records])
    ]
    scored: dict[str, list[tuple[float, bool, float, float]]] = defaultdict(list)
    for score, record in zip(scores, records, strict=True):
        scored[record.profile].append(
            (
                score,
                record.correct,
                float(record.features[0]),
                float(record.features[1]),
            )
        )
    return dict(scored)


def _correctness_calibration_summary(
    scored: Mapping[str, Sequence[tuple[float, bool, float, float]]],
    *,
    label_counts: Mapping[str, int],
) -> dict[str, Any]:
    profiles = {}
    for profile in FLOATING_CORRECTNESS_PROFILES:
        records = scored.get(profile, ())
        correct = sum(int(value[1]) for value in records)
        predicted = len(records)
        profiles[profile] = {
            "label_count": int(label_counts.get(profile, 0)),
            "predicted_count": predicted,
            "correct_count": correct,
            "precision": round(correct / predicted, 8) if predicted else 0.0,
            "recall": round(correct / label_counts[profile], 8)
            if label_counts.get(profile)
            else 0.0,
        }
    return {
        "profile_policy": "deterministic-clean-mild-stress-v1",
        "profiles": profiles,
    }


def _calibrate_noise_aware_gate(
    scored: Mapping[str, Sequence[tuple[float, bool, float, float]]],
    *,
    label_counts: Mapping[str, int],
    minimum_precision: float,
    base_gate: Mapping[str, Any],
) -> dict[str, Any]:
    minimum_predictions = {
        profile: max(25, math.ceil(int(label_counts.get(profile, 0)) * 0.01))
        for profile in FLOATING_CORRECTNESS_PROFILES
    }
    base_available = bool(base_gate.get("available"))
    base_confidence = float(base_gate.get("confidence_threshold", 1.1))
    base_margin = float(base_gate.get("margin_threshold", 1.1))
    if not base_available:
        return {
            "available": False,
            "policy": "base-gate-conjunction-minimum-profile-precision-v1",
            "minimum_precision": minimum_precision,
            "minimum_predictions": minimum_predictions,
            "confidence_threshold": 1.1,
            "base_gate": {
                "available": False,
                "confidence_threshold": base_confidence,
                "margin_threshold": base_margin,
            },
            "profiles": {},
        }
    best_score: tuple[int, int, float, float] | None = None
    best_metrics: dict[str, Any] | None = None
    for threshold_step in range(5, 100):
        confidence_threshold = threshold_step / 100
        metrics: dict[str, Any] = {}
        valid = True
        for profile in FLOATING_CORRECTNESS_PROFILES:
            selected = [
                correct
                for score, correct, pair_confidence, pair_margin in scored.get(
                    profile,
                    (),
                )
                if score >= confidence_threshold
                and pair_confidence >= base_confidence
                and pair_margin >= base_margin
            ]
            predicted = len(selected)
            if predicted < minimum_predictions[profile]:
                valid = False
                break
            correct = sum(selected)
            precision = correct / predicted
            if precision < minimum_precision:
                valid = False
                break
            labels = int(label_counts.get(profile, 0))
            metrics[profile] = {
                "label_count": labels,
                "predicted_count": predicted,
                "correct_count": correct,
                "precision": round(precision, 8),
                "recall": round(correct / labels, 8) if labels else 0.0,
            }
        if not valid:
            continue
        candidate_score = (
            min(value["correct_count"] for value in metrics.values()),
            sum(value["correct_count"] for value in metrics.values()),
            min(value["precision"] for value in metrics.values()),
            -confidence_threshold,
        )
        if best_score is None or candidate_score > best_score:
            best_score = candidate_score
            best_metrics = metrics
    if best_score is None or best_metrics is None:
        return {
            "available": False,
            "policy": "base-gate-conjunction-minimum-profile-precision-v1",
            "minimum_precision": minimum_precision,
            "minimum_predictions": minimum_predictions,
            "confidence_threshold": 1.1,
            "base_gate": {
                "available": True,
                "confidence_threshold": base_confidence,
                "margin_threshold": base_margin,
            },
            "profiles": {},
        }
    _, _, worst_precision, negative_threshold = best_score
    return {
        "available": True,
        "policy": "base-gate-conjunction-minimum-profile-precision-v1",
        "minimum_precision": minimum_precision,
        "minimum_predictions": minimum_predictions,
        "confidence_threshold": round(-negative_threshold, 2),
        "base_gate": {
            "available": True,
            "confidence_threshold": base_confidence,
            "margin_threshold": base_margin,
        },
        "worst_profile_precision": round(worst_precision, 8),
        "profiles": best_metrics,
    }


def _score_training_pages(
    estimator: Any,
    pages: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    scored_pages: list[dict[str, Any]] = []
    for page in pages:
        graphical = sorted(
            [
                block
                for block in page["blocks"]
                if block["kind"] in {"figure", "table"}
            ],
            key=_block_geometry_key,
        )
        text_blocks = sorted(
            [
                block
                for block in page["blocks"]
                if block["kind"] == "text" and block["text"]
            ],
            key=_block_geometry_key,
        )
        feature_rows, score_rows = _score_pair_matrix(
            estimator,
            graphical,
            text_blocks,
            width=page["width"],
            height=page["height"],
        )
        scored_pages.append(
            {
                "graphical": graphical,
                "text_blocks": text_blocks,
                "feature_rows": feature_rows,
                "score_rows": score_rows,
                "positives": page["positives"],
            }
        )
    return scored_pages


def _global_calibration_records(
    scored_pages: Sequence[Mapping[str, Any]],
    *,
    threshold: float,
) -> tuple[list[tuple[float, float, bool]], dict[str, int]]:
    records: list[tuple[float, float, bool]] = []
    diagnostics = {
        "negative_margin_count": 0,
        "non_top_source_assignment_count": 0,
        "contested_target_assignment_count": 0,
    }
    for page in scored_pages:
        graphical = page["graphical"]
        text_blocks = page["text_blocks"]
        score_rows = page["score_rows"]
        source_indices = {id(block): index for index, block in enumerate(graphical)}
        target_indices = {id(block): index for index, block in enumerate(text_blocks)}
        for confidence, margin, source, target, _ in _global_assignment_pairs(
            graphical,
            text_blocks,
            page["feature_rows"],
            score_rows,
            threshold=threshold,
        ):
            source_index = source_indices[id(source)]
            target_index = target_indices[id(target)]
            row_competitor = max(
                (
                    float(score)
                    for index, score in enumerate(score_rows[source_index])
                    if index != target_index
                ),
                default=0.0,
            )
            column_competitor = max(
                (
                    float(row[target_index])
                    for index, row in enumerate(score_rows)
                    if index != source_index
                ),
                default=0.0,
            )
            diagnostics["negative_margin_count"] += int(margin < 0)
            diagnostics["non_top_source_assignment_count"] += int(
                row_competitor > confidence
            )
            diagnostics["contested_target_assignment_count"] += int(
                column_competitor > confidence
            )
            records.append(
                (
                    confidence,
                    margin,
                    (source["block_id"], target["block_id"])
                    in page["positives"],
                )
            )
    return records, diagnostics


def _calibrate_global_assignment_threshold(
    scored_pages: Sequence[Mapping[str, Any]],
    *,
    page_count: int,
) -> tuple[float, dict[str, Any], list[tuple[float, float, bool]]]:
    label_count = sum(len(page["positives"]) for page in scored_pages)
    best_result: tuple[float, float, float, int, int] | None = None
    for step in range(5, 100):
        threshold = step / 100
        records, _ = _global_calibration_records(
            scored_pages,
            threshold=threshold,
        )
        predicted = len(records)
        correct = sum(int(record[2]) for record in records)
        precision = correct / predicted if predicted else 0.0
        recall = correct / label_count if label_count else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        candidate = (f1, precision, threshold, predicted, correct)
        if best_result is None or candidate > best_result:
            best_result = candidate
    assert best_result is not None
    f1, precision, threshold, predicted, correct = best_result
    recall = correct / label_count if label_count else 0.0
    selected_records, assignment_diagnostics = _global_calibration_records(
        scored_pages,
        threshold=threshold,
    )
    return threshold, {
        "page_count": page_count,
        "label_count": label_count,
        "predicted_count": predicted,
        "correct_count": correct,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
        "assignment_policy": FLOATING_ASSIGNMENT_POLICY_GLOBAL,
        "selection_margin_policy": FLOATING_MARGIN_POLICY_GLOBAL,
        **assignment_diagnostics,
    }, selected_records


def _calibrate_reliability_gate(
    records: Sequence[tuple[float, float, bool]],
    *,
    label_count: int,
    minimum_precision: float,
) -> dict[str, Any]:
    minimum_predictions = max(25, math.ceil(label_count * 0.01))
    best: tuple[float, float, float, float, int, int] | None = None
    for confidence_step in range(5, 100, 2):
        confidence_threshold = confidence_step / 100
        for margin_step in range(0, 91, 2):
            margin_threshold = margin_step / 100
            selected = [
                correct
                for confidence, margin, correct in records
                if confidence >= confidence_threshold and margin >= margin_threshold
            ]
            predicted = len(selected)
            if predicted < minimum_predictions:
                continue
            correct = sum(selected)
            precision = correct / predicted
            if precision < minimum_precision:
                continue
            recall = correct / label_count if label_count else 0.0
            candidate = (
                recall,
                precision,
                -confidence_threshold,
                -margin_threshold,
                predicted,
                correct,
            )
            if best is None or candidate > best:
                best = candidate
    if best is None:
        return {
            "available": False,
            "minimum_precision": minimum_precision,
            "minimum_predictions": minimum_predictions,
            "confidence_threshold": 1.1,
            "margin_threshold": 1.1,
            "predicted_count": 0,
            "correct_count": 0,
            "precision": None,
            "recall": 0.0,
        }
    recall, precision, negative_confidence, negative_margin, predicted, correct = best
    return {
        "available": True,
        "minimum_precision": minimum_precision,
        "minimum_predictions": minimum_predictions,
        "confidence_threshold": round(-negative_confidence, 2),
        "margin_threshold": round(-negative_margin, 2),
        "predicted_count": predicted,
        "correct_count": correct,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
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


def _feature_envelope(features: Sequence[Sequence[float]]) -> dict[str, list[float]]:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Install requirements-relation-ranker.txt to train a floating ranker") from exc
    matrix = np.asarray(features, dtype=float)
    return {
        "lower": [round(float(value), 10) for value in np.quantile(matrix, 0.01, axis=0)],
        "upper": [round(float(value), 10) for value in np.quantile(matrix, 0.99, axis=0)],
    }


def _feature_outliers(features: Sequence[float], envelope: Any) -> tuple[int, float]:
    if not isinstance(envelope, Mapping):
        return 0, 0.0
    lower = envelope.get("lower")
    upper = envelope.get("upper")
    if not isinstance(lower, list) or not isinstance(upper, list) or len(features) != len(lower) or len(lower) != len(upper):
        return 0, 0.0
    count = sum(
        int(float(value) < float(minimum) or float(value) > float(maximum))
        for value, minimum, maximum in zip(features, lower, upper, strict=True)
    )
    return count, round(count / len(features), 8) if features else 0.0


def _xywh_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 4:
        return None
    x, y, width, height = map(float, value[:4])
    if width <= 0 or height <= 0:
        return None
    return [x, y, x + width, y + height]


def _polygon_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 8 or len(value) % 2:
        return None
    try:
        xs = [float(value[index]) for index in range(0, len(value), 2)]
        ys = [float(value[index]) for index in range(1, len(value), 2)]
    except (TypeError, ValueError, OverflowError):
        return None
    box = [min(xs), min(ys), max(xs), max(ys)]
    return box if box[2] > box[0] and box[3] > box[1] else None


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
