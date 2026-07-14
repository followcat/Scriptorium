from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .chunkr_benchmark import (
    CHUNKR_ANNOTATIONS_SHA256,
    CHUNKR_DATASET_LICENSE,
    CHUNKR_DATASET_REPOSITORY,
    CHUNKR_DATA_REVISION,
    _aggregate_order_metrics,
    _annotation_bbox,
    _annotations_by_image,
    _answer_free_anchor_fingerprint,
    _load_chunkr_coco,
    _order_metric_slices,
    _order_page_metrics,
)
from .geometry import reading_order_key
from .models import BBox
from .reading_order import (
    infer_box_flow_order,
    infer_recursive_xy_cut_order,
    infer_relation_graph_order,
    infer_semantic_reading_order,
)
from .roor_benchmark import ROOR_FETCH_SCHEMA


CHUNKR_ORDER_RANKER_SCHEMA = "scriptorium-chunkr-order-ranker/v1"
CHUNKR_ORDER_OOF_SCHEMA = "scriptorium-chunkr-order-ranker-oof/v1"
CHUNKR_ORDER_PREDICTION_SCHEMA = "scriptorium-chunkr-order-prediction/v1"
CHUNKR_ORDER_EXTERNAL_ROOR_SCHEMA = "scriptorium-chunkr-order-ranker-roor-benchmark/v1"
CHUNKR_ORDER_FEATURE_VERSION = "chunkr-role-geometry-candidate-rank-v1"
CHUNKR_ORDER_PROVIDER = "scriptorium-chunkr-pairwise-order-ranker"
CHUNKR_ORDER_FOLD_POLICY = "category-complexity-sha256-page-round-robin-v1"
CHUNKR_ORDER_DECODER = "bidirectional-probability-borda-visual-tiebreak-v1"
DEFAULT_CROSS_VALIDATION_FOLDS = 5
DEFAULT_RANDOM_SEED = 17
MAX_PREDICTION_ELEMENTS = 256
PAGE_PROFILE_NAMES = (
    "log_element_count",
    "width_p10",
    "width_median",
    "width_p90",
    "height_p10",
    "height_median",
    "height_p90",
    "area_median",
    "area_p90",
    "area_sum",
    "log_aspect_median",
    "role_entropy",
    "text_block_ratio",
    "unknown_role_ratio",
    "selected_visual_pair_disagreement",
    "xy_visual_pair_disagreement",
    "relation_visual_pair_disagreement",
)

ROLE_ALIASES = {
    "caption": "Caption",
    "figure-caption": "Caption",
    "table-caption": "Caption",
    "text": "Text Block",
    "text-block": "Text Block",
    "paragraph": "Text Block",
    "body": "Text Block",
    "footer": "Footer",
    "footnote": "Footnote",
    "form": "Form Region",
    "form-region": "Form Region",
    "formula": "Formula",
    "equation": "Formula",
    "graphical-item": "Graphical Item",
    "chart": "Graphical Item",
    "header": "Header",
    "title": "Title",
    "heading": "Title",
    "section-heading": "Title",
    "legend": "Legend",
    "line-number": "Line Number",
    "list": "List Item",
    "list-item": "List Item",
    "page-number": "Page Number",
    "picture": "Picture",
    "figure": "Picture",
    "image": "Picture",
    "table": "Table",
    "unknown": "Unknown",
}

_FORBIDDEN_ORDER_KEYS = {
    "block_order",
    "candidate_orders",
    "external_structure_order",
    "native_reading_order",
    "order",
    "order_edges",
    "order_index",
    "provider_order",
    "precedence_edges",
    "reading_order",
    "reading_order_edges",
    "reading_order_linkings",
    "reading_order_relations",
    "reading_streams",
    "ro_linkings",
    "semantic_order",
    "stream_order",
    "streams",
    "successor_edges",
    "successor_relations",
    "ordered_element_ids",
}


@dataclass(frozen=True)
class ChunkrOrderRankerTrainingResult:
    model_path: Path
    manifest_path: Path
    report_path: Path
    manifest: dict[str, Any]
    report: dict[str, Any]


@dataclass(frozen=True)
class ChunkrOrderPredictionResult:
    ordered_ids: tuple[str, ...]
    payload: dict[str, Any]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class ChunkrOrderExternalBenchmarkResult:
    report_path: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class _OrderBlock:
    id: str
    bbox: BBox
    role: str


@dataclass(frozen=True)
class _OrderPage:
    image_id: int | str
    file_name: str
    doc_category: str
    width: float
    height: float
    blocks: tuple[_OrderBlock, ...]
    truth_ranks: tuple[int, ...] | None
    candidate_orders: dict[str, tuple[int, ...]]
    candidate_ranks: dict[str, tuple[float, ...]]


def train_chunkr_order_ranker(
    annotations_path: str | Path,
    output: str | Path,
    *,
    cross_validation_folds: int = DEFAULT_CROSS_VALIDATION_FOLDS,
    random_seed: int = DEFAULT_RANDOM_SEED,
    require_pinned_annotations: bool = True,
) -> ChunkrOrderRankerTrainingResult:
    """Train one role-aware pairwise order model with page-grouped OOF evidence."""

    if cross_validation_folds < 2:
        raise ValueError("cross_validation_folds must be at least 2")
    source_path = Path(annotations_path)
    source_bytes = source_path.read_bytes()
    annotation_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if require_pinned_annotations and annotation_sha256 != CHUNKR_ANNOTATIONS_SHA256:
        raise ValueError("Chunkr annotation SHA-256 does not match the pinned corpus")
    payload = _load_chunkr_coco(source_bytes)
    role_vocabulary = tuple(
        str(category["name"])
        for category in sorted(payload["categories"], key=lambda item: int(item["id"]))
    )
    if len(role_vocabulary) != len(set(role_vocabulary)):
        raise ValueError("Chunkr role names must be unique")
    pages = _training_pages(payload, role_vocabulary=role_vocabulary)
    if len(pages) < cross_validation_folds:
        raise ValueError("cross_validation_folds cannot exceed the page count")
    fold_by_page = _page_fold_assignments(
        pages,
        fold_count=cross_validation_folds,
    )
    feature_names = _feature_names(role_vocabulary)
    features, labels, example_pages = _training_matrix(
        pages,
        role_vocabulary=role_vocabulary,
    )
    if len(labels) == 0 or len(set(int(label) for label in labels)) != 2:
        raise ValueError("Chunkr pairwise training requires both order classes")

    oof_records: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    for fold_index in range(cross_validation_folds):
        training_mask = [
            fold_by_page[int(page_index)] != fold_index for page_index in example_pages
        ]
        estimator, sklearn_version = _fit_estimator(
            features[training_mask],
            labels[training_mask],
            random_seed=random_seed + fold_index,
        )
        fold_page_profile_envelope = _page_profile_envelope(
            [
                page
                for page_index, page in enumerate(pages)
                if fold_by_page[page_index] != fold_index
            ],
            role_vocabulary=role_vocabulary,
        )
        fold_records: list[dict[str, Any]] = []
        for page_index, page in enumerate(pages):
            if fold_by_page[page_index] != fold_index:
                continue
            prediction = _predict_page_order(
                page,
                estimator,
                role_vocabulary=role_vocabulary,
                feature_envelope=None,
                page_profile_envelope=fold_page_profile_envelope,
            )
            record = _oof_page_record(
                page,
                prediction,
                fold_index=fold_index,
            )
            fold_records.append(record)
            oof_records.append(record)
        fold_learned_counts = [record["_learned_counts"] for record in fold_records]
        fold_baseline_names = tuple(fold_records[0]["_baseline_counts"])
        folds.append(
            {
                "fold_index": fold_index,
                "training_page_count": sum(
                    assigned_fold != fold_index for assigned_fold in fold_by_page
                ),
                "validation_page_count": len(fold_records),
                "training_example_count": sum(training_mask),
                "learned_metrics": _order_metric_slices(fold_learned_counts),
                "baseline_metrics": {
                    name: _order_metric_slices(
                        [record["_baseline_counts"][name] for record in fold_records]
                    )
                    for name in fold_baseline_names
                },
            }
        )

    feature_envelope = _feature_envelope(features)
    page_profile_envelope = _page_profile_envelope(
        pages,
        role_vocabulary=role_vocabulary,
    )
    final_estimator, sklearn_version = _fit_estimator(
        features,
        labels,
        random_seed=random_seed,
    )
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema": CHUNKR_ORDER_RANKER_SCHEMA,
        "feature_version": CHUNKR_ORDER_FEATURE_VERSION,
        "decoder": CHUNKR_ORDER_DECODER,
        "role_vocabulary": list(role_vocabulary),
        "feature_names": feature_names,
        "feature_envelope": feature_envelope,
        "page_profile_names": list(PAGE_PROFILE_NAMES),
        "page_profile_envelope": page_profile_envelope,
        "estimator": final_estimator,
    }
    _joblib_module().dump(bundle, output_path)
    model_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()

    report = _oof_report(
        oof_records,
        folds=folds,
        annotation_path=source_path,
        annotation_sha256=annotation_sha256,
        model_sha256=model_sha256,
        fold_count=cross_validation_folds,
        random_seed=random_seed,
        feature_names=feature_names,
        role_vocabulary=role_vocabulary,
    )
    report_path = output_path.with_suffix(f"{output_path.suffix}.oof.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()

    manifest = {
        "schema": CHUNKR_ORDER_RANKER_SCHEMA,
        "status": "development-review-only",
        "runtime_reorder": False,
        "candidate_consensus_policy": "isolated",
        "provider": CHUNKR_ORDER_PROVIDER,
        "feature_version": CHUNKR_ORDER_FEATURE_VERSION,
        "decoder": CHUNKR_ORDER_DECODER,
        "dataset": "Chunkr Reading Order Bench OSS",
        "dataset_repository": CHUNKR_DATASET_REPOSITORY,
        "dataset_revision": CHUNKR_DATA_REVISION,
        "dataset_license": CHUNKR_DATASET_LICENSE,
        "annotations": str(source_path),
        "annotations_sha256": annotation_sha256,
        "answer_boundary": {
            "labels": "contiguous ascending annotation ids within each page",
            "candidate_input": "category-and-bbox SHA-256 order",
            "candidate_features_use_annotation_ids": False,
            "fold_assignment_uses_order_labels": False,
            "test_split_claimed": False,
        },
        "split_policy": CHUNKR_ORDER_FOLD_POLICY,
        "split_unit": "page; upstream corpus does not publish document ids",
        "cross_validation_folds": cross_validation_folds,
        "page_count": len(pages),
        "training_example_count": int(len(labels)),
        "training_positive_count": int(sum(int(label) for label in labels)),
        "feature_count": len(feature_names),
        "feature_names": feature_names,
        "role_vocabulary": list(role_vocabulary),
        "feature_envelope_quantiles": [0.01, 0.99],
        "page_profile_names": list(PAGE_PROFILE_NAMES),
        "page_profile_envelope_quantiles": [0.01, 0.99],
        "training_weight_policy": "uniform-directed-pairs",
        "estimator": {
            "type": "HistGradientBoostingClassifier",
            "max_iter": 100,
            "max_leaf_nodes": 31,
            "learning_rate": 0.08,
            "l2_regularization": 1.0,
        },
        "random_seed": random_seed,
        "scikit_learn_version": sklearn_version,
        "model_file": output_path.name,
        "model_sha256": model_sha256,
        "oof_report_file": report_path.name,
        "oof_report_sha256": report_sha256,
        "security": "Load only locally generated model files; joblib loading can execute code.",
    }
    manifest_path = output_path.with_suffix(f"{output_path.suffix}.manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ChunkrOrderRankerTrainingResult(
        output_path,
        manifest_path,
        report_path,
        manifest,
        report,
    )


def load_chunkr_order_ranker(
    model_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(model_path)
    manifest_path = path.with_suffix(f"{path.suffix}.manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        raise ValueError("model and adjacent .manifest.json are required")
    manifest = _json_object(manifest_path, label="Chunkr order model manifest")
    if manifest.get("schema") != CHUNKR_ORDER_RANKER_SCHEMA:
        raise ValueError("unsupported Chunkr order model manifest schema")
    if manifest.get("status") != "development-review-only":
        raise ValueError("Chunkr order model manifest must remain review-only")
    if manifest.get("runtime_reorder") is not False:
        raise ValueError("Chunkr order model manifest cannot enable runtime reorder")
    if manifest.get("candidate_consensus_policy") != "isolated":
        raise ValueError("Chunkr order model manifest must remain isolated")
    if manifest.get("feature_version") != CHUNKR_ORDER_FEATURE_VERSION:
        raise ValueError("unsupported Chunkr order manifest feature version")
    if manifest.get("decoder") != CHUNKR_ORDER_DECODER:
        raise ValueError("unsupported Chunkr order manifest decoder")
    if manifest.get("model_file") != path.name:
        raise ValueError("Chunkr order manifest model_file does not match the model")
    expected_sha256 = str(manifest.get("model_sha256") or "")
    actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if not expected_sha256 or actual_sha256 != expected_sha256:
        raise ValueError("Chunkr order model hash does not match its manifest")

    report_path = _adjacent_artifact_path(
        path.parent,
        manifest.get("oof_report_file"),
        label="Chunkr order OOF report",
    )
    expected_report_sha256 = str(manifest.get("oof_report_sha256") or "")
    actual_report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
    if not expected_report_sha256 or actual_report_sha256 != expected_report_sha256:
        raise ValueError("Chunkr order OOF report hash does not match its manifest")
    report = _json_object(report_path, label="Chunkr order OOF report")
    if report.get("schema") != CHUNKR_ORDER_OOF_SCHEMA:
        raise ValueError("unsupported Chunkr order OOF report schema")
    report_contract = {
        "status": "development-review-only",
        "runtime_reorder": False,
        "candidate_consensus_policy": "isolated",
        "model_sha256": actual_sha256,
        "annotations_sha256": manifest.get("annotations_sha256"),
        "feature_version": CHUNKR_ORDER_FEATURE_VERSION,
        "decoder": CHUNKR_ORDER_DECODER,
    }
    if any(report.get(key) != value for key, value in report_contract.items()):
        raise ValueError("Chunkr order OOF report does not match its model manifest")

    bundle = _joblib_module().load(path)
    if (
        not isinstance(bundle, dict)
        or bundle.get("schema") != CHUNKR_ORDER_RANKER_SCHEMA
    ):
        raise ValueError("unsupported Chunkr order model schema")
    if bundle.get("feature_version") != CHUNKR_ORDER_FEATURE_VERSION:
        raise ValueError("unsupported Chunkr order feature version")
    if bundle.get("decoder") != CHUNKR_ORDER_DECODER:
        raise ValueError("unsupported Chunkr order decoder")
    feature_names = bundle.get("feature_names")
    role_vocabulary = bundle.get("role_vocabulary")
    if not isinstance(feature_names, list) or feature_names != manifest.get(
        "feature_names"
    ):
        raise ValueError("Chunkr order model features do not match its manifest")
    if not isinstance(role_vocabulary, list) or role_vocabulary != manifest.get(
        "role_vocabulary"
    ):
        raise ValueError("Chunkr order model roles do not match its manifest")
    if manifest.get("feature_count") != len(feature_names):
        raise ValueError("Chunkr order manifest feature_count is inconsistent")
    if bundle.get("page_profile_names") != list(PAGE_PROFILE_NAMES):
        raise ValueError("unsupported Chunkr order page profile")
    return bundle, manifest


def predict_chunkr_block_order(
    payload: Mapping[str, Any],
    model_path: str | Path,
) -> ChunkrOrderPredictionResult:
    """Predict one isolated review-only block order from answer-free layout JSON."""

    _reject_order_answers(payload)
    width = _positive_number(payload.get("width"), "width")
    height = _positive_number(payload.get("height"), "height")
    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list) or not raw_elements:
        raise ValueError("prediction payload must contain a non-empty elements list")
    if len(raw_elements) > MAX_PREDICTION_ELEMENTS:
        raise ValueError(
            f"prediction payload cannot exceed {MAX_PREDICTION_ELEMENTS} elements"
        )
    bundle, manifest = load_chunkr_order_ranker(model_path)
    role_vocabulary = tuple(str(role) for role in bundle["role_vocabulary"])
    blocks = tuple(
        sorted(
            (
                _prediction_block(element, role_vocabulary=role_vocabulary)
                for element in raw_elements
            ),
            key=_block_fingerprint,
        )
    )
    if len({block.id for block in blocks}) != len(blocks):
        raise ValueError("prediction element ids must be unique")
    if len({_block_fingerprint(block) for block in blocks}) != len(blocks):
        raise ValueError("prediction elements must have unique role/bbox fingerprints")
    page = _page_context(
        image_id=str(payload.get("id") or "prediction-page"),
        file_name=str(payload.get("id") or "prediction-page"),
        doc_category=str(payload.get("doc_category") or "unknown"),
        width=width,
        height=height,
        blocks=blocks,
        truth_ranks=None,
    )
    prediction = _predict_page_order(
        page,
        bundle["estimator"],
        role_vocabulary=role_vocabulary,
        feature_envelope=bundle.get("feature_envelope"),
        page_profile_envelope=bundle.get("page_profile_envelope"),
    )
    ordered_ids = tuple(blocks[index].id for index in prediction["order"])
    successor_edges = [
        {
            "source": source,
            "target": target,
            "kind": "successor",
            "review_required": True,
            "relation_policy": "review-only",
            "provider": CHUNKR_ORDER_PROVIDER,
        }
        for source, target in zip(ordered_ids, ordered_ids[1:], strict=False)
    ]
    output = dict(payload)
    output.update(
        {
            "schema": CHUNKR_ORDER_PREDICTION_SCHEMA,
            "source": CHUNKR_ORDER_PROVIDER,
            "semantic_policy": "review-only",
            "order_policy": "review-only",
            "relation_policy": "review-only",
            "candidate_consensus_policy": "isolated",
            "runtime_reorder": False,
            "ordered_element_ids": list(ordered_ids),
            "successor_edges": successor_edges,
            "chunkr_order_ranker": {
                **prediction["diagnostics"],
                "feature_version": CHUNKR_ORDER_FEATURE_VERSION,
                "decoder": CHUNKR_ORDER_DECODER,
                "model_sha256": manifest["model_sha256"],
                "training_dataset": "Chunkr Reading Order Bench OSS",
            },
        }
    )
    return ChunkrOrderPredictionResult(
        ordered_ids,
        output,
        dict(prediction["diagnostics"]),
    )


def benchmark_chunkr_order_ranker_roor(
    corpus_dir: str | Path,
    model_path: str | Path,
    *,
    output: str | Path | None = None,
) -> ChunkrOrderExternalBenchmarkResult:
    """Replay one frozen Chunkr model on answer-separated ROOR validation pages."""

    root = Path(corpus_dir)
    manifest_path = root / "roor_benchmark_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("ROOR corpus must contain roor_benchmark_manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest = _json_object_bytes(manifest_bytes, label="ROOR corpus manifest")
    if manifest.get("schema") != ROOR_FETCH_SCHEMA:
        raise ValueError("unsupported ROOR corpus manifest schema")
    structure_input = manifest.get("structure_input")
    if (
        not isinstance(structure_input, Mapping)
        or structure_input.get("relations_removed") is not True
    ):
        raise ValueError(
            "ROOR corpus manifest must declare relation-free structure input"
        )
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("ROOR manifest must contain samples")
    bundle, model_manifest = load_chunkr_order_ranker(model_path)
    role_vocabulary = tuple(str(role) for role in bundle["role_vocabulary"])
    pending_cases: list[dict[str, Any]] = []
    candidate_names = (
        "learned",
        "selected-auto",
        "visual-yx",
        "box-flow",
        "recursive-xy-cut",
        "relation-graph",
    )
    sample_ids: set[str] = set()
    for sample in samples:
        if not isinstance(sample, Mapping):
            raise ValueError("ROOR manifest sample records must be objects")
        sample_id = str(sample.get("id") or "").strip()
        if not sample_id or sample_id in sample_ids:
            raise ValueError("ROOR manifest sample ids must be non-empty and unique")
        sample_ids.add(sample_id)
        structure_path = _corpus_artifact_path(
            root,
            sample.get("structure"),
            label=f"ROOR structure for {sample_id}",
        )
        semantic_path = _corpus_artifact_path(
            root,
            sample.get("semantic_sidecar"),
            label=f"ROOR semantic sidecar for {sample_id}",
        )
        if structure_path == semantic_path:
            raise ValueError(
                "ROOR structure and semantic sidecar must be separate files"
            )
        structure_bytes = structure_path.read_bytes()
        structure = _json_object_bytes(
            structure_bytes,
            label=f"ROOR structure for {sample_id}",
        )
        _reject_order_answers(structure)
        if structure.get("relations_removed") is not True:
            raise ValueError("ROOR structure input must declare relations_removed=true")
        image = structure.get("img")
        document = structure.get("document")
        if (
            not isinstance(image, Mapping)
            or not isinstance(document, list)
            or not document
            or any(not isinstance(segment, Mapping) for segment in document)
        ):
            raise ValueError("ROOR structure input requires img and document")
        elements = [
            {
                "id": str(segment["id"]),
                "box": segment["box"],
                "role": segment.get("type")
                or segment.get("block_label")
                or "Text Block",
            }
            for segment in document
        ]
        prediction_payload = {
            "id": str(structure.get("uid") or sample_id),
            "doc_category": "roor",
            "width": image.get("width"),
            "height": image.get("height"),
            "elements": elements,
        }
        prediction = predict_chunkr_block_order(
            prediction_payload,
            model_path,
        )
        blocks = tuple(
            sorted(
                (
                    _prediction_block(
                        element,
                        role_vocabulary=role_vocabulary,
                    )
                    for element in elements
                ),
                key=_block_fingerprint,
            )
        )
        page = _page_context(
            image_id=prediction_payload["id"],
            file_name=prediction_payload["id"],
            doc_category="roor",
            width=_positive_number(image.get("width"), "img.width"),
            height=_positive_number(image.get("height"), "img.height"),
            blocks=blocks,
            truth_ranks=None,
        )
        orders = {
            "learned": list(prediction.ordered_ids),
            **{
                name: [blocks[index].id for index in order]
                for name, order in page.candidate_orders.items()
            },
        }
        pending_cases.append(
            {
                "sample_id": prediction_payload["id"],
                "manifest_sample_id": sample_id,
                "element_count": len(elements),
                "structure_sha256": hashlib.sha256(structure_bytes).hexdigest(),
                "semantic_path": semantic_path,
                "orders": orders,
                "prediction_diagnostics": prediction.diagnostics,
            }
        )

    # Labels are intentionally opened only after every model prediction has
    # completed. This makes the benchmark answer boundary structural, not just
    # a promise that the predictor ignores an already-loaded object.
    cases: list[dict[str, Any]] = []
    for pending in pending_cases:
        semantic_path = pending["semantic_path"]
        semantic_bytes = semantic_path.read_bytes()
        semantic = _json_object_bytes(
            semantic_bytes,
            label=f"ROOR semantic sidecar for {pending['manifest_sample_id']}",
        )
        raw_relations = semantic.get("ro_linkings")
        if not isinstance(raw_relations, list):
            raise ValueError("ROOR semantic sidecar requires ro_linkings")
        relations = {
            (str(edge[0]), str(edge[1]))
            for edge in raw_relations
            if isinstance(edge, Sequence)
            and not isinstance(edge, (str, bytes))
            and len(edge) == 2
        }
        if len(relations) != len(raw_relations):
            raise ValueError("ROOR relations must be unique source/target pairs")
        candidate_metrics = {
            name: _relation_order_metrics(order, relations)
            for name, order in pending["orders"].items()
        }
        diagnostics = pending["prediction_diagnostics"]
        cases.append(
            {
                "sample_id": pending["sample_id"],
                "element_count": pending["element_count"],
                "relation_count": len(relations),
                "structure_sha256": pending["structure_sha256"],
                "semantic_sidecar_sha256": hashlib.sha256(semantic_bytes).hexdigest(),
                "page_profile_in_envelope": diagnostics["page_profile_in_envelope"],
                "page_profile_outlier_names": diagnostics["page_profile_outlier_names"],
                "prediction_diagnostics": diagnostics,
                "candidate_metrics": candidate_metrics,
            }
        )
    candidate_metrics = {
        name: _aggregate_relation_order_metrics(
            [case["candidate_metrics"][name] for case in cases]
        )
        for name in candidate_names
    }
    report = {
        "schema": CHUNKR_ORDER_EXTERNAL_ROOR_SCHEMA,
        "status": "external-rejected-review-only",
        "runtime_reorder": False,
        "candidate_consensus_policy": "isolated",
        "model": str(model_path),
        "model_sha256": model_manifest["model_sha256"],
        "model_training_dataset": "Chunkr Reading Order Bench OSS",
        "corpus": str(root),
        "corpus_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "corpus_dataset": manifest.get("dataset"),
        "corpus_revision": manifest.get("revision"),
        "sample_count": len(cases),
        "relation_count": sum(int(case["relation_count"]) for case in cases),
        "answer_boundary": {
            "model_input": "ROOR structure files with ro_linkings removed",
            "evaluation_labels": "adjacent semantic sidecars only",
            "model_reads_semantic_sidecars": False,
            "execution_policy": "predict-all-pages-before-reading-any-label-sidecar",
            "prediction_phase_page_count": len(pending_cases),
            "label_phase_started_after_prediction_count": len(pending_cases),
        },
        "candidate_metrics": candidate_metrics,
        "learned_vs_selected": _relation_candidate_comparison(
            cases,
            first="learned",
            second="selected-auto",
        ),
        "page_profile_in_envelope_count": sum(
            case["page_profile_in_envelope"] is True for case in cases
        ),
        "page_profile_outlier_page_count": sum(
            case["page_profile_in_envelope"] is False for case in cases
        ),
        "promotion_decision": "reject-runtime-promotion",
        "cases": cases,
    }
    report_path = (
        Path(output)
        if output is not None
        else root / "chunkr_order_ranker_roor_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ChunkrOrderExternalBenchmarkResult(report_path, report)


def _relation_order_metrics(
    order: Sequence[str],
    relations: set[tuple[str, str]],
) -> dict[str, Any]:
    if len(order) != len(set(order)):
        raise ValueError("relation candidate order must contain unique ids")
    positions = {item_id: index for index, item_id in enumerate(order)}
    unresolved = sum(
        source not in positions or target not in positions
        for source, target in relations
    )
    if unresolved:
        raise ValueError("relation candidate order has unresolved ROOR ids")
    predicted_edges = set(zip(order, order[1:], strict=False))
    direct_correct = len(predicted_edges & relations)
    precedence_correct = sum(
        positions[source] < positions[target] for source, target in relations
    )
    return {
        "direct_correct": direct_correct,
        "direct_predicted": len(predicted_edges),
        "relation_labels": len(relations),
        "precedence_correct": precedence_correct,
    }


def _aggregate_relation_order_metrics(
    metrics: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    direct_correct = sum(int(item["direct_correct"]) for item in metrics)
    direct_predicted = sum(int(item["direct_predicted"]) for item in metrics)
    labels = sum(int(item["relation_labels"]) for item in metrics)
    precedence_correct = sum(int(item["precedence_correct"]) for item in metrics)
    direct_precision = _safe_ratio(direct_correct, direct_predicted)
    direct_recall = _safe_ratio(direct_correct, labels)
    return {
        "direct_correct": direct_correct,
        "direct_predicted": direct_predicted,
        "relation_labels": labels,
        "direct_precision": direct_precision,
        "direct_recall": direct_recall,
        "direct_f1": _harmonic_mean(direct_precision, direct_recall),
        "precedence_correct": precedence_correct,
        "precedence_accuracy": _safe_ratio(precedence_correct, labels),
    }


def _relation_candidate_comparison(
    cases: Sequence[Mapping[str, Any]],
    *,
    first: str,
    second: str,
) -> dict[str, Any]:
    direct_deltas = [
        int(case["candidate_metrics"][first]["direct_correct"])
        - int(case["candidate_metrics"][second]["direct_correct"])
        for case in cases
    ]
    precedence_deltas = [
        int(case["candidate_metrics"][first]["precedence_correct"])
        - int(case["candidate_metrics"][second]["precedence_correct"])
        for case in cases
    ]
    return {
        "direct_better_page_count": sum(delta > 0 for delta in direct_deltas),
        "direct_equal_page_count": sum(delta == 0 for delta in direct_deltas),
        "direct_worse_page_count": sum(delta < 0 for delta in direct_deltas),
        "precedence_better_page_count": sum(delta > 0 for delta in precedence_deltas),
        "precedence_equal_page_count": sum(delta == 0 for delta in precedence_deltas),
        "precedence_worse_page_count": sum(delta < 0 for delta in precedence_deltas),
    }


def _harmonic_mean(first: float, second: float) -> float:
    if not first + second:
        return 0.0
    return round(2 * first * second / (first + second), 8)


def _training_pages(
    payload: Mapping[str, Any],
    *,
    role_vocabulary: Sequence[str],
) -> list[_OrderPage]:
    images = {int(image["id"]): image for image in payload["images"]}
    annotations_by_image = _annotations_by_image(payload)
    category_names = {
        int(category["id"]): str(category["name"]) for category in payload["categories"]
    }
    pages: list[_OrderPage] = []
    for image_id in sorted(images):
        image = images[image_id]
        truth_annotations = annotations_by_image[image_id]
        input_annotations = sorted(
            truth_annotations,
            key=_answer_free_anchor_fingerprint,
        )
        truth_rank_by_id = {
            int(annotation["id"]): rank
            for rank, annotation in enumerate(truth_annotations)
        }
        blocks = tuple(
            _OrderBlock(
                id=str(annotation["id"]),
                bbox=_annotation_bbox(annotation),
                role=category_names[int(annotation["category_id"])],
            )
            for annotation in input_annotations
        )
        truth_ranks = tuple(
            truth_rank_by_id[int(annotation["id"])] for annotation in input_annotations
        )
        pages.append(
            _page_context(
                image_id=image_id,
                file_name=str(image["file_name"]),
                doc_category=str(image["doc_category"]),
                width=float(image["width"]),
                height=float(image["height"]),
                blocks=blocks,
                truth_ranks=truth_ranks,
            )
        )
    return pages


def _page_context(
    *,
    image_id: int | str,
    file_name: str,
    doc_category: str,
    width: float,
    height: float,
    blocks: tuple[_OrderBlock, ...],
    truth_ranks: tuple[int, ...] | None,
) -> _OrderPage:
    bboxes = [block.bbox for block in blocks]
    count = len(blocks)
    visual = tuple(
        sorted(range(count), key=lambda index: reading_order_key(bboxes[index]))
    )
    box_flow = tuple(infer_box_flow_order(bboxes, width, height))
    xy_cut = tuple(infer_recursive_xy_cut_order(bboxes, width, height))
    relation_graph = tuple(infer_relation_graph_order(bboxes, width, height))
    assignments = infer_semantic_reading_order(
        bboxes,
        width,
        height,
        texts=[""] * count,
    )
    selected = tuple(
        assignment.item_index
        for assignment in sorted(
            assignments,
            key=lambda assignment: assignment.semantic_order,
        )
    )
    candidate_orders = {
        "selected-auto": selected,
        "visual-yx": visual,
        "box-flow": box_flow,
        "recursive-xy-cut": xy_cut,
        "relation-graph": relation_graph,
    }
    expected = set(range(count))
    for name, order in candidate_orders.items():
        if len(order) != count or set(order) != expected:
            raise ValueError(f"{name} did not produce a complete order for {file_name}")
    candidate_ranks = {
        name: _normalized_ranks(order, count)
        for name, order in candidate_orders.items()
    }
    if truth_ranks is not None and set(truth_ranks) != expected:
        raise ValueError(f"truth order is not a complete permutation for {file_name}")
    return _OrderPage(
        image_id,
        file_name,
        doc_category,
        width,
        height,
        blocks,
        truth_ranks,
        candidate_orders,
        candidate_ranks,
    )


def _normalized_ranks(order: Sequence[int], count: int) -> tuple[float, ...]:
    denominator = max(count - 1, 1)
    ranks = [0.0] * count
    for rank, item_index in enumerate(order):
        ranks[item_index] = rank / denominator
    return tuple(ranks)


def _page_fold_assignments(
    pages: Sequence[_OrderPage],
    *,
    fold_count: int,
) -> list[int]:
    strata: dict[tuple[str, str], list[int]] = defaultdict(list)
    for page_index, page in enumerate(pages):
        strata[(page.doc_category, _complexity_bucket(len(page.blocks)))].append(
            page_index
        )
    fold_by_page = [-1] * len(pages)
    for stratum, page_indices in sorted(strata.items()):
        page_indices.sort(
            key=lambda index: hashlib.sha256(
                pages[index].file_name.encode("utf-8")
            ).hexdigest()
        )
        offset = (
            int.from_bytes(
                hashlib.sha256(
                    json.dumps(stratum, separators=(",", ":")).encode("utf-8")
                ).digest()[:4],
                "big",
            )
            % fold_count
        )
        for position, page_index in enumerate(page_indices):
            fold_by_page[page_index] = (position + offset) % fold_count
    if any(fold < 0 for fold in fold_by_page):
        raise ValueError("failed to assign every Chunkr page to a fold")
    if len(set(fold_by_page)) != fold_count:
        raise ValueError("Chunkr fold assignment produced an empty fold")
    return fold_by_page


def _complexity_bucket(element_count: int) -> str:
    if element_count <= 1:
        return "1"
    if element_count <= 5:
        return "2-5"
    if element_count <= 10:
        return "6-10"
    if element_count <= 20:
        return "11-20"
    return "21+"


def _training_matrix(
    pages: Sequence[_OrderPage],
    *,
    role_vocabulary: Sequence[str],
) -> tuple[Any, Any, Any]:
    numpy = _numpy_module()
    features: list[list[float]] = []
    labels: list[int] = []
    page_indices: list[int] = []
    for page_index, page in enumerate(pages):
        if page.truth_ranks is None:
            raise ValueError("training page is missing truth ranks")
        for source in range(len(page.blocks)):
            for target in range(len(page.blocks)):
                if source == target:
                    continue
                features.append(
                    _pair_features(
                        page,
                        source,
                        target,
                        role_vocabulary=role_vocabulary,
                    )
                )
                labels.append(int(page.truth_ranks[source] < page.truth_ranks[target]))
                page_indices.append(page_index)
    return (
        numpy.asarray(features, dtype=numpy.float32),
        numpy.asarray(labels, dtype=numpy.int8),
        numpy.asarray(page_indices, dtype=numpy.int32),
    )


def _pair_features(
    page: _OrderPage,
    source_index: int,
    target_index: int,
    *,
    role_vocabulary: Sequence[str],
) -> list[float]:
    source = page.blocks[source_index]
    target = page.blocks[target_index]
    source_box = source.bbox
    target_box = target.bbox
    width = page.width
    height = page.height
    source_width = max(source_box.width, 1.0)
    source_height = max(source_box.height, 1.0)
    target_width = max(target_box.width, 1.0)
    target_height = max(target_box.height, 1.0)
    source_center_x = (source_box.x0 + source_box.x1) / 2
    source_center_y = (source_box.y0 + source_box.y1) / 2
    target_center_x = (target_box.x0 + target_box.x1) / 2
    target_center_y = (target_box.y0 + target_box.y1) / 2
    horizontal_overlap = max(
        0.0,
        min(source_box.x1, target_box.x1) - max(source_box.x0, target_box.x0),
    ) / max(1.0, min(source_width, target_width))
    vertical_overlap = max(
        0.0,
        min(source_box.y1, target_box.y1) - max(source_box.y0, target_box.y0),
    ) / max(1.0, min(source_height, target_height))
    features = [
        source_box.x0 / width,
        source_box.y0 / height,
        source_box.x1 / width,
        source_box.y1 / height,
        target_box.x0 / width,
        target_box.y0 / height,
        target_box.x1 / width,
        target_box.y1 / height,
        (target_center_x - source_center_x) / width,
        (target_center_y - source_center_y) / height,
        abs(target_center_x - source_center_x) / width,
        abs(target_center_y - source_center_y) / height,
        source_width / width,
        source_height / height,
        target_width / width,
        target_height / height,
        horizontal_overlap,
        vertical_overlap,
        float(target_box.y0 >= source_box.y0),
        float(target_box.x0 >= source_box.x0),
        math.log1p(len(page.blocks)) / 5.0,
    ]
    adjacency_threshold = 1 / max(len(page.blocks) - 1, 1) + 1e-9
    for candidate_name in (
        "selected-auto",
        "visual-yx",
        "box-flow",
        "recursive-xy-cut",
        "relation-graph",
    ):
        ranks = page.candidate_ranks[candidate_name]
        rank_delta = ranks[target_index] - ranks[source_index]
        features.extend(
            (
                rank_delta,
                float(rank_delta > 0),
                float(abs(rank_delta) <= adjacency_threshold),
            )
        )
    role_index = {role: index for index, role in enumerate(role_vocabulary)}
    unknown_index = role_index.get("Unknown")
    source_role = role_index.get(source.role, unknown_index)
    target_role = role_index.get(target.role, unknown_index)
    if source_role is None or target_role is None:
        raise ValueError("role vocabulary must contain every block role or Unknown")
    source_one_hot = [0.0] * len(role_vocabulary)
    target_one_hot = [0.0] * len(role_vocabulary)
    source_one_hot[source_role] = 1.0
    target_one_hot[target_role] = 1.0
    return [*features, *source_one_hot, *target_one_hot]


def _feature_names(role_vocabulary: Sequence[str]) -> list[str]:
    names = [
        "source_x0",
        "source_y0",
        "source_x1",
        "source_y1",
        "target_x0",
        "target_y0",
        "target_x1",
        "target_y1",
        "center_dx",
        "center_dy",
        "absolute_center_dx",
        "absolute_center_dy",
        "source_width",
        "source_height",
        "target_width",
        "target_height",
        "horizontal_overlap",
        "vertical_overlap",
        "target_below_source",
        "target_right_of_source",
        "log_page_element_count",
    ]
    for candidate in (
        "selected_auto",
        "visual_yx",
        "box_flow",
        "recursive_xy_cut",
        "relation_graph",
    ):
        names.extend(
            (
                f"{candidate}_rank_delta",
                f"{candidate}_source_before_target",
                f"{candidate}_adjacent",
            )
        )
    names.extend(f"source_role_{_feature_slug(role)}" for role in role_vocabulary)
    names.extend(f"target_role_{_feature_slug(role)}" for role in role_vocabulary)
    return names


def _feature_slug(value: str) -> str:
    return "_".join(value.strip().casefold().replace("-", " ").split())


def _fit_estimator(
    features: Any,
    labels: Any,
    *,
    random_seed: int,
) -> tuple[Any, str]:
    try:
        import sklearn
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError as exc:
        raise RuntimeError(
            "Install requirements-relation-ranker.txt to train a Chunkr order ranker"
        ) from exc
    estimator = HistGradientBoostingClassifier(
        max_iter=100,
        max_leaf_nodes=31,
        learning_rate=0.08,
        l2_regularization=1.0,
        random_state=random_seed,
    )
    estimator.fit(features, labels)
    return estimator, sklearn.__version__


def _predict_page_order(
    page: _OrderPage,
    estimator: Any,
    *,
    role_vocabulary: Sequence[str],
    feature_envelope: Mapping[str, Any] | None,
    page_profile_envelope: Mapping[str, Any] | None,
) -> dict[str, Any]:
    numpy = _numpy_module()
    count = len(page.blocks)
    if count == 1:
        profile_diagnostics = _page_profile_diagnostics(
            page,
            page_profile_envelope,
            role_vocabulary=role_vocabulary,
        )
        return {
            "order": (0,),
            "diagnostics": {
                "element_count": 1,
                "pair_count": 0,
                "mean_pair_margin": 1.0,
                "minimum_pair_margin": 1.0,
                "pairwise_consistency": 1.0,
                "mean_adjacent_precedence": 1.0,
                "feature_outlier_value_ratio": 0.0,
                **profile_diagnostics,
            },
        }
    pairs: list[tuple[int, int]] = []
    directed_features: list[list[float]] = []
    for source in range(count):
        for target in range(source + 1, count):
            pairs.append((source, target))
            directed_features.append(
                _pair_features(
                    page,
                    source,
                    target,
                    role_vocabulary=role_vocabulary,
                )
            )
            directed_features.append(
                _pair_features(
                    page,
                    target,
                    source,
                    role_vocabulary=role_vocabulary,
                )
            )
    feature_matrix = numpy.asarray(directed_features, dtype=numpy.float32)
    probabilities = estimator.predict_proba(feature_matrix)[:, 1]
    scores = [0.0] * count
    pair_probability: dict[tuple[int, int], float] = {}
    margins: list[float] = []
    for pair_index, (source, target) in enumerate(pairs):
        probability = (
            float(probabilities[2 * pair_index])
            + 1.0
            - float(probabilities[2 * pair_index + 1])
        ) / 2.0
        pair_probability[(source, target)] = probability
        pair_probability[(target, source)] = 1.0 - probability
        scores[source] += probability
        scores[target] += 1.0 - probability
        margins.append(abs(probability - 0.5) * 2.0)
    visual_rank = {
        item_index: rank
        for rank, item_index in enumerate(page.candidate_orders["visual-yx"])
    }
    order = tuple(
        sorted(
            range(count),
            key=lambda item_index: (-scores[item_index], visual_rank[item_index]),
        )
    )
    order_rank = {item_index: rank for rank, item_index in enumerate(order)}
    consistent_pairs = sum(
        (probability >= 0.5) == (order_rank[source] < order_rank[target])
        for (source, target), probability in pair_probability.items()
        if source < target
    )
    adjacent_probabilities = [
        pair_probability[(source, target)]
        for source, target in zip(order, order[1:], strict=False)
    ]
    profile_diagnostics = _page_profile_diagnostics(
        page,
        page_profile_envelope,
        role_vocabulary=role_vocabulary,
    )
    return {
        "order": order,
        "diagnostics": {
            "element_count": count,
            "pair_count": len(pairs),
            "mean_pair_margin": round(sum(margins) / len(margins), 8),
            "minimum_pair_margin": round(min(margins), 8),
            "pairwise_consistency": round(consistent_pairs / len(pairs), 8),
            "mean_adjacent_precedence": round(
                sum(adjacent_probabilities) / len(adjacent_probabilities),
                8,
            ),
            "feature_outlier_value_ratio": _feature_outlier_value_ratio(
                feature_matrix,
                feature_envelope,
            ),
            **profile_diagnostics,
        },
    }


def _oof_page_record(
    page: _OrderPage,
    prediction: Mapping[str, Any],
    *,
    fold_index: int,
) -> dict[str, Any]:
    if page.truth_ranks is None:
        raise ValueError("OOF page is missing truth ranks")
    learned_order = tuple(int(index) for index in prediction["order"])
    learned_counts = _order_page_metrics(
        [page.truth_ranks[index] for index in learned_order]
    )
    baseline_counts = {
        name: _order_page_metrics([page.truth_ranks[index] for index in order])
        for name, order in page.candidate_orders.items()
    }
    return {
        "image_id": page.image_id,
        "file_name": page.file_name,
        "doc_category": page.doc_category,
        "element_count": len(page.blocks),
        "fold_index": fold_index,
        "learned_metrics": _public_order_counts(learned_counts),
        "baseline_metrics": {
            name: _public_order_counts(counts)
            for name, counts in baseline_counts.items()
        },
        "diagnostics": dict(prediction["diagnostics"]),
        "_learned_counts": learned_counts,
        "_baseline_counts": baseline_counts,
    }


def _public_order_counts(counts: Mapping[str, Any]) -> dict[str, Any]:
    pair_accuracy = _safe_ratio(
        int(counts["pair_correct"]),
        int(counts["pair_total"]),
        empty=1.0,
    )
    return {
        "exact": bool(counts["exact"]),
        "position_accuracy": _safe_ratio(
            int(counts["position_correct"]),
            int(counts["position_total"]),
        ),
        "pairwise_accuracy": pair_accuracy,
        "kendall_tau": round(2 * pair_accuracy - 1, 8),
        "successor_accuracy": _safe_ratio(
            int(counts["successor_correct"]),
            int(counts["successor_labels"]),
            empty=1.0,
        ),
    }


def _oof_report(
    records: Sequence[Mapping[str, Any]],
    *,
    folds: Sequence[Mapping[str, Any]],
    annotation_path: Path,
    annotation_sha256: str,
    model_sha256: str,
    fold_count: int,
    random_seed: int,
    feature_names: Sequence[str],
    role_vocabulary: Sequence[str],
) -> dict[str, Any]:
    learned_counts = [record["_learned_counts"] for record in records]
    baseline_names = tuple(records[0]["_baseline_counts"])
    baseline_metrics = {
        name: _order_metric_slices(
            [record["_baseline_counts"][name] for record in records]
        )
        for name in baseline_names
    }
    public_records = [
        {key: value for key, value in record.items() if not str(key).startswith("_")}
        for record in records
    ]
    return {
        "schema": CHUNKR_ORDER_OOF_SCHEMA,
        "status": "development-review-only",
        "runtime_reorder": False,
        "candidate_consensus_policy": "isolated",
        "annotations": str(annotation_path),
        "annotations_sha256": annotation_sha256,
        "model_sha256": model_sha256,
        "feature_version": CHUNKR_ORDER_FEATURE_VERSION,
        "decoder": CHUNKR_ORDER_DECODER,
        "feature_names": list(feature_names),
        "role_vocabulary": list(role_vocabulary),
        "fold_policy": CHUNKR_ORDER_FOLD_POLICY,
        "fold_unit": "page; upstream corpus does not publish document ids",
        "fold_assignment_uses_order_labels": False,
        "cross_validation_folds": fold_count,
        "random_seed": random_seed,
        "page_count": len(records),
        "learned_oof_metrics": _order_metric_slices(learned_counts),
        "baseline_metrics": baseline_metrics,
        "comparisons": {
            name: _candidate_comparison(records, baseline_name=name)
            for name in baseline_names
        },
        "domains": _domain_metrics(records, baseline_names=baseline_names),
        "folds": list(folds),
        "cases": public_records,
    }


def _candidate_comparison(
    records: Sequence[Mapping[str, Any]],
    *,
    baseline_name: str,
) -> dict[str, Any]:
    exact_gain = 0
    exact_loss = 0
    exact_equal = 0
    pair_better = 0
    pair_worse = 0
    pair_equal = 0
    successor_better = 0
    successor_worse = 0
    successor_equal = 0
    for record in records:
        learned = record["_learned_counts"]
        baseline = record["_baseline_counts"][baseline_name]
        learned_exact = bool(learned["exact"])
        baseline_exact = bool(baseline["exact"])
        exact_gain += learned_exact and not baseline_exact
        exact_loss += baseline_exact and not learned_exact
        exact_equal += learned_exact == baseline_exact
        pair_delta = int(learned["pair_correct"]) - int(baseline["pair_correct"])
        pair_better += pair_delta > 0
        pair_worse += pair_delta < 0
        pair_equal += pair_delta == 0
        successor_delta = int(learned["successor_correct"]) - int(
            baseline["successor_correct"]
        )
        successor_better += successor_delta > 0
        successor_worse += successor_delta < 0
        successor_equal += successor_delta == 0
    return {
        "exact_gain_page_count": exact_gain,
        "exact_loss_page_count": exact_loss,
        "exact_equal_page_count": exact_equal,
        "pairwise_better_page_count": pair_better,
        "pairwise_worse_page_count": pair_worse,
        "pairwise_equal_page_count": pair_equal,
        "successor_better_page_count": successor_better,
        "successor_worse_page_count": successor_worse,
        "successor_equal_page_count": successor_equal,
    }


def _domain_metrics(
    records: Sequence[Mapping[str, Any]],
    *,
    baseline_names: Sequence[str],
) -> dict[str, Any]:
    by_domain: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        by_domain[str(record["doc_category"])].append(record)
    return {
        domain: {
            "page_count": len(domain_records),
            "learned": _aggregate_order_metrics(
                [record["_learned_counts"] for record in domain_records]
            ),
            "baselines": {
                name: _aggregate_order_metrics(
                    [record["_baseline_counts"][name] for record in domain_records]
                )
                for name in baseline_names
            },
        }
        for domain, domain_records in sorted(by_domain.items())
    }


def _feature_envelope(features: Any) -> dict[str, list[float]]:
    numpy = _numpy_module()
    return {
        "lower": [
            round(float(value), 10) for value in numpy.quantile(features, 0.01, axis=0)
        ],
        "upper": [
            round(float(value), 10) for value in numpy.quantile(features, 0.99, axis=0)
        ],
    }


def _page_profile(
    page: _OrderPage,
    *,
    role_vocabulary: Sequence[str],
) -> list[float]:
    numpy = _numpy_module()
    widths = numpy.asarray(
        [block.bbox.width / page.width for block in page.blocks],
        dtype=float,
    )
    heights = numpy.asarray(
        [block.bbox.height / page.height for block in page.blocks],
        dtype=float,
    )
    areas = widths * heights
    aspects = numpy.log1p(widths / numpy.maximum(heights, 1e-9))
    count = len(page.blocks)
    role_counts = Counter(block.role for block in page.blocks)
    role_probabilities = [
        role_counts.get(role, 0) / count
        for role in role_vocabulary
        if role_counts.get(role, 0)
    ]
    entropy_denominator = max(math.log(max(len(role_vocabulary), 2)), 1.0)
    role_entropy = (
        -sum(probability * math.log(probability) for probability in role_probabilities)
        / entropy_denominator
    )
    return [
        math.log1p(count),
        float(numpy.quantile(widths, 0.1)),
        float(numpy.median(widths)),
        float(numpy.quantile(widths, 0.9)),
        float(numpy.quantile(heights, 0.1)),
        float(numpy.median(heights)),
        float(numpy.quantile(heights, 0.9)),
        float(numpy.median(areas)),
        float(numpy.quantile(areas, 0.9)),
        float(areas.sum()),
        float(numpy.median(aspects)),
        role_entropy,
        role_counts.get("Text Block", 0) / count,
        role_counts.get("Unknown", 0) / count,
        _candidate_pair_disagreement(page, "selected-auto", "visual-yx"),
        _candidate_pair_disagreement(page, "recursive-xy-cut", "visual-yx"),
        _candidate_pair_disagreement(page, "relation-graph", "visual-yx"),
    ]


def _candidate_pair_disagreement(
    page: _OrderPage,
    first: str,
    second: str,
) -> float:
    count = len(page.blocks)
    pair_count = count * (count - 1) // 2
    if not pair_count:
        return 0.0
    first_ranks = page.candidate_ranks[first]
    second_ranks = page.candidate_ranks[second]
    disagreement = sum(
        (first_ranks[source] < first_ranks[target])
        != (second_ranks[source] < second_ranks[target])
        for source in range(count)
        for target in range(source + 1, count)
    )
    return disagreement / pair_count


def _page_profile_envelope(
    pages: Sequence[_OrderPage],
    *,
    role_vocabulary: Sequence[str],
) -> dict[str, list[float]]:
    if not pages:
        raise ValueError("page profile envelope requires at least one page")
    numpy = _numpy_module()
    matrix = numpy.asarray(
        [_page_profile(page, role_vocabulary=role_vocabulary) for page in pages],
        dtype=float,
    )
    return {
        "lower": [
            round(float(value), 10) for value in numpy.quantile(matrix, 0.01, axis=0)
        ],
        "upper": [
            round(float(value), 10) for value in numpy.quantile(matrix, 0.99, axis=0)
        ],
    }


def _page_profile_diagnostics(
    page: _OrderPage,
    envelope: Mapping[str, Any] | None,
    *,
    role_vocabulary: Sequence[str],
) -> dict[str, Any]:
    profile = _page_profile(page, role_vocabulary=role_vocabulary)
    profile_values = {
        name: round(float(value), 10)
        for name, value in zip(PAGE_PROFILE_NAMES, profile, strict=True)
    }
    lower = envelope.get("lower") if isinstance(envelope, Mapping) else None
    upper = envelope.get("upper") if isinstance(envelope, Mapping) else None
    if (
        not isinstance(lower, list)
        or not isinstance(upper, list)
        or len(lower) != len(PAGE_PROFILE_NAMES)
        or len(upper) != len(PAGE_PROFILE_NAMES)
    ):
        return {
            "page_profile_envelope_available": False,
            "page_profile_in_envelope": None,
            "page_profile_outlier_value_count": 0,
            "page_profile_outlier_value_ratio": 0.0,
            "page_profile_outlier_names": [],
            "page_profile_values": profile_values,
            "page_profile_outliers": [],
        }
    outliers = [
        {
            "name": name,
            "value": round(float(value), 10),
            "lower": round(float(minimum), 10),
            "upper": round(float(maximum), 10),
            "direction": "below" if value < float(minimum) else "above",
        }
        for name, value, minimum, maximum in zip(
            PAGE_PROFILE_NAMES,
            profile,
            lower,
            upper,
            strict=True,
        )
        if value < float(minimum) or value > float(maximum)
    ]
    outlier_names = [str(item["name"]) for item in outliers]
    return {
        "page_profile_envelope_available": True,
        "page_profile_in_envelope": not outlier_names,
        "page_profile_outlier_value_count": len(outlier_names),
        "page_profile_outlier_value_ratio": round(
            len(outlier_names) / len(PAGE_PROFILE_NAMES),
            8,
        ),
        "page_profile_outlier_names": outlier_names,
        "page_profile_values": profile_values,
        "page_profile_outliers": outliers,
    }


def _feature_outlier_value_ratio(
    features: Any,
    envelope: Mapping[str, Any] | None,
) -> float:
    if not isinstance(envelope, Mapping):
        return 0.0
    lower = envelope.get("lower")
    upper = envelope.get("upper")
    if not isinstance(lower, list) or not isinstance(upper, list):
        return 0.0
    if features.shape[1] != len(lower) or len(lower) != len(upper):
        return 0.0
    numpy = _numpy_module()
    minimum = numpy.asarray(lower, dtype=float)
    maximum = numpy.asarray(upper, dtype=float)
    outliers = (features < minimum) | (features > maximum)
    return round(float(outliers.sum() / outliers.size), 8)


def _prediction_block(
    element: Any,
    *,
    role_vocabulary: Sequence[str],
) -> _OrderBlock:
    if not isinstance(element, Mapping):
        raise ValueError("prediction elements must be objects")
    _reject_order_answers(element)
    element_id = str(element.get("id") or "").strip()
    if not element_id:
        raise ValueError("prediction elements require non-empty ids")
    box = element.get("box")
    if not isinstance(box, Sequence) or isinstance(box, (str, bytes)) or len(box) != 4:
        raise ValueError("prediction element box must contain x0, y0, x1, y1")
    try:
        bbox = BBox.from_any(box)
    except (TypeError, ValueError) as exc:
        raise ValueError("prediction element box is invalid") from exc
    role_value = str(
        element.get("role")
        or element.get("block_label")
        or element.get("type")
        or "Unknown"
    )
    role = _normalized_role(role_value, role_vocabulary=role_vocabulary)
    return _OrderBlock(element_id, bbox, role)


def _normalized_role(value: str, *, role_vocabulary: Sequence[str]) -> str:
    direct = {role.casefold(): role for role in role_vocabulary}
    normalized = "-".join(value.strip().casefold().replace("_", "-").split())
    role = direct.get(normalized) or ROLE_ALIASES.get(normalized)
    if role in role_vocabulary:
        return role
    if "Unknown" in role_vocabulary:
        return "Unknown"
    raise ValueError(f"unsupported prediction role: {value!r}")


def _block_fingerprint(block: _OrderBlock) -> str:
    payload = {
        "bbox": [round(value, 8) for value in block.bbox.as_list()],
        "role": block.role,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _reject_order_answers(payload: Mapping[str, Any]) -> None:
    present: set[str] = set()
    pending: list[Any] = [payload]
    visited: set[int] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            if id(value) in visited:
                continue
            visited.add(id(value))
            present.update(_FORBIDDEN_ORDER_KEYS & set(value))
            pending.extend(value.values())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if id(value) in visited:
                continue
            visited.add(id(value))
            pending.extend(value)
    if present:
        raise ValueError(
            "prediction input must not contain order/relation answers: "
            + ", ".join(sorted(present))
        )


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    return _json_object_bytes(path.read_bytes(), label=label)


def _json_object_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _adjacent_artifact_path(root: Path, value: Any, *, label: str) -> Path:
    relative = str(value or "").strip()
    if not relative or Path(relative).name != relative:
        raise ValueError(f"{label} must be an adjacent file")
    path = root / relative
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    return path


def _corpus_artifact_path(root: Path, value: Any, *, label: str) -> Path:
    relative = str(value or "").strip()
    if not relative:
        raise ValueError(f"{label} path is required")
    resolved_root = root.resolve()
    path = (resolved_root / relative).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the ROOR corpus") from exc
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    return path


def _positive_number(value: Any, label: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a positive number") from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{label} must be a positive number")
    return normalized


def _safe_ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return round(numerator / denominator, 8) if denominator else empty


def _numpy_module() -> Any:
    try:
        import numpy
    except ImportError as exc:
        raise RuntimeError(
            "Install requirements-relation-ranker.txt to use Chunkr order models"
        ) from exc
    return numpy


def _joblib_module() -> Any:
    try:
        import joblib
    except ImportError as exc:
        raise RuntimeError(
            "Install requirements-relation-ranker.txt to use Chunkr order models"
        ) from exc
    return joblib
