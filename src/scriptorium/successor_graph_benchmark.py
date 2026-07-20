from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .graph_model import (
    flatten_page_scores,
    load_graph_model,
    predict_feature_batches,
    save_graph_model,
)
from .graph_provenance import (
    DOCUMENT_OOF_MODE,
    FROZEN_FIT_MODEL_MODE,
    benchmark_prediction_provenance,
    input_payload_sha256,
    proposal_provenance_for_input,
    serialized_prediction_provenance,
)
from .hierarchical_order_benchmark import HIERARCHY_INPUT_SCHEMA
from .models import BBox
from .provider_hierarchy_benchmark import (
    PROVIDER_HIERARCHY_CORPUS_SCHEMA,
    PROVIDER_HIERARCHY_LABEL_SCHEMA,
)
from .reading_order import (
    infer_relation_graph_order_evidence,
    infer_semantic_reading_order,
)
from .relation_order import (
    merge_relation_edge_path_cover,
    merge_scored_relation_edge_path_cover_max_regret,
)


SUCCESSOR_GRAPH_BENCHMARK_SCHEMA = "scriptorium-successor-graph-benchmark/v1"
SUCCESSOR_DECODER_AB_SCHEMA = "scriptorium-successor-decoder-ab/v1"
SUCCESSOR_GRAPH_PROPOSAL_SCHEMA = "scriptorium-successor-graph-proposal/v2"
SUCCESSOR_GRAPH_MODEL_SCHEMA = "scriptorium-successor-graph-model/v1"
SUCCESSOR_GRAPH_TOPOLOGY_V3_FEATURE_VERSION = "fine-line-directed-topology-text-v3"
SUCCESSOR_GRAPH_FEATURE_VERSION = "fine-line-directed-sparse-topology-text-v4"
SCORE_GREEDY_DECODER = "score-greedy-top-one-v1"
MAX_REGRET_DECODER = "max-regret-all-candidates-v1"
DEFAULT_NEAREST_CANDIDATES = 20
PROPOSAL_ALTERNATIVES_PER_SOURCE = 3

SUCCESSOR_GRAPH_TOPOLOGY_V3_FEATURE_NAMES = (
    "source_x0",
    "source_y0",
    "source_x1",
    "source_y1",
    "target_x0",
    "target_y0",
    "target_x1",
    "target_y1",
    "signed_center_dx",
    "signed_center_dy",
    "absolute_center_dx",
    "absolute_center_dy",
    "source_width",
    "source_height",
    "target_width",
    "target_height",
    "horizontal_overlap",
    "vertical_overlap",
    "target_not_above",
    "target_not_left",
    "source_text_length",
    "target_text_length",
    "source_terminal_punctuation",
    "target_starts_lowercase",
    "target_starts_digit",
    "base_rank_delta",
    "base_immediate_successor",
    "base_immediate_predecessor",
    "relation_forward_score",
    "relation_reverse_score",
    "relation_forward_selected",
    "relation_reverse_selected",
    "source_role_text",
    "source_role_figure",
    "source_role_table",
    "target_role_text",
    "target_role_figure",
    "target_role_table",
    "same_role",
    "source_column_index",
    "target_column_index",
    "column_index_delta",
    "same_column",
    "same_flow_segment",
    "flow_segment_delta",
    "same_xy_region",
    "source_distance_rank",
    "target_distance_rank",
    "mutual_geometry_nearest",
    "aligned_forward_eligible",
    "aligned_forward_rank",
    "aligned_predecessor_eligible",
    "aligned_predecessor_rank",
    "mutual_aligned_nearest",
    "relative_vertical_gap",
    "relative_left_edge_delta",
    "vertical_corridor",
    "vertical_blocker_fraction",
    "vertical_corridor_unblocked",
)

SUCCESSOR_GRAPH_FEATURE_NAMES = (
    *SUCCESSOR_GRAPH_TOPOLOGY_V3_FEATURE_NAMES,
    "aligned_expansion_candidate",
    "column_handoff_candidate",
)


@dataclass(frozen=True)
class SuccessorGraphBenchmarkResult:
    report_path: Path
    proposals_dir: Path
    report: dict[str, Any]
    model_path: Path | None = None
    model_manifest_path: Path | None = None


@dataclass(frozen=True)
class SuccessorDecoderAbResult:
    report_path: Path
    proposals_dir: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class _Candidate:
    source: str
    target: str
    features: tuple[float, ...]
    tie_priority: tuple[float | str, ...]


@dataclass(frozen=True)
class _Page:
    corpus: Path
    sample: dict[str, Any]
    split: str
    element_ids: tuple[str, ...]
    base_rank: dict[str, int]
    base_edges: frozenset[tuple[str, str]]
    candidates: tuple[_Candidate, ...]


@dataclass(frozen=True)
class _Labels:
    edges: frozenset[tuple[str, str]]
    scopes: dict[tuple[str, str], str]


@dataclass(frozen=True)
class _RankedEdge:
    source: str
    target: str
    score: float
    rank: int
    top_score_margin: float


@dataclass(frozen=True)
class _RankedPage:
    top_edges: tuple[_RankedEdge, ...]
    all_edges: tuple[_RankedEdge, ...]


@dataclass(frozen=True)
class _DecodedPage:
    selected_edges: frozenset[tuple[str, str]]
    top_threshold_edges: frozenset[tuple[str, str]]
    diagnostics: dict[str, int]


@dataclass(frozen=True)
class _PageTopologyContext:
    distance_rank_by_source: dict[tuple[str, str], int]
    distance_rank_by_target: dict[tuple[str, str], int]
    aligned_rank_by_source: dict[tuple[str, str], int]
    aligned_rank_by_target: dict[tuple[str, str], int]
    median_height: float


def benchmark_successor_graph(
    train_corpus_dir: str | Path,
    *,
    output: str | Path,
    proposals_dir: str | Path | None = None,
    test_corpus_dir: str | Path | None = None,
    model_output: str | Path | None = None,
    cross_validation_folds: int = 5,
    nearest_candidates: int = DEFAULT_NEAREST_CANDIDATES,
    minimum_edge_precision: float = 0.97,
    minimum_selected_edges: int = 1000,
    random_seed: int = 101,
) -> SuccessorGraphBenchmarkResult:
    """Train and score a review-only fine-line directed successor graph."""

    if cross_validation_folds < 2:
        raise ValueError("cross_validation_folds must be at least 2")
    if nearest_candidates < 1:
        raise ValueError("nearest_candidates must be at least 1")
    if not 0.5 <= minimum_edge_precision <= 1.0:
        raise ValueError("minimum_edge_precision must be between 0.5 and 1.0")
    if minimum_selected_edges < 1:
        raise ValueError("minimum_selected_edges must be at least 1")

    train_corpus = Path(train_corpus_dir).resolve()
    train_manifest_path, train_manifest = _corpus_manifest(train_corpus)
    train_manifest_sha256 = _file_sha256(train_manifest_path)
    fit_pages = _load_answer_free_pages(
        train_corpus,
        train_manifest,
        split="fit",
        nearest_candidates=nearest_candidates,
    )
    calibration_pages = _load_answer_free_pages(
        train_corpus,
        train_manifest,
        split="calibration",
        nearest_candidates=nearest_candidates,
    )
    if not fit_pages or not calibration_pages:
        raise ValueError("training corpus requires fit and calibration pages")

    test_manifest_path: Path | None = None
    test_pages: list[_Page] = []
    if test_corpus_dir is not None:
        test_corpus = Path(test_corpus_dir).resolve()
        test_manifest_path, test_manifest = _corpus_manifest(test_corpus)
        test_pages = _load_answer_free_pages(
            test_corpus,
            test_manifest,
            split="test",
            nearest_candidates=nearest_candidates,
            accept_all_partitions=True,
        )
        if not test_pages:
            raise ValueError("test corpus contains no pages")

    _require_disjoint_documents(fit_pages, calibration_pages, test_pages)
    _require_unique_sample_ids(fit_pages, calibration_pages, test_pages)

    # Every answer-free page and candidate is materialized before fit labels open.
    fit_labels = [_load_labels(page) for page in fit_pages]
    x_fit, y_fit, groups = _training_matrix(fit_pages, fit_labels)
    if len(set(groups)) < cross_validation_folds:
        raise ValueError("fit partition has fewer documents than cross-validation folds")

    estimator_parameters = {
        "max_iter": 160,
        "max_leaf_nodes": 31,
        "learning_rate": 0.07,
        "l2_regularization": 2.0,
        "min_samples_leaf": 24,
        "class_weight": "balanced",
        "random_state": random_seed,
    }
    estimator_class, group_kfold_class, numpy, sklearn_version = _training_modules()
    oof_scores = numpy.zeros(len(y_fit), dtype=float)
    folds = group_kfold_class(n_splits=cross_validation_folds)
    for fold_index, (train_indices, validation_indices) in enumerate(
        folds.split(x_fit, y_fit, groups)
    ):
        parameters = dict(estimator_parameters)
        parameters["random_state"] = random_seed + fold_index
        fold_estimator = estimator_class(**parameters).fit(
            x_fit[train_indices],
            y_fit[train_indices],
        )
        oof_scores[validation_indices] = fold_estimator.predict_proba(
            x_fit[validation_indices]
        )[:, 1]

    fit_ranked = _rank_pages(fit_pages, oof_scores)
    threshold, operating_point = _freeze_threshold(
        fit_pages,
        fit_labels,
        fit_ranked,
        oof_scores,
        minimum_edge_precision=minimum_edge_precision,
        minimum_selected_edges=minimum_selected_edges,
        numpy=numpy,
    )

    feature_count = int(x_fit.shape[1])
    if feature_count != len(SUCCESSOR_GRAPH_FEATURE_NAMES):
        raise ValueError("successor graph feature names do not match generated features")
    fit_candidate_count = int(x_fit.shape[0])
    fit_positive_candidate_count = int(y_fit.sum())
    estimator = estimator_class(**estimator_parameters).fit(x_fit, y_fit)
    # Free the dense fit matrix before page-wise evaluation scoring.
    del x_fit
    del y_fit
    calibration_scores = _predict_pages(estimator, calibration_pages, numpy=numpy)
    test_scores = _predict_pages(estimator, test_pages, numpy=numpy)
    calibration_ranked = _rank_pages(calibration_pages, calibration_scores)
    test_ranked = _rank_pages(test_pages, test_scores)

    report_path = Path(output)
    proposal_root = (
        Path(proposals_dir)
        if proposals_dir is not None
        else report_path.parent / f"{report_path.stem}.proposals"
    )
    proposal_root.mkdir(parents=True, exist_ok=True)
    fit_proposals = _write_proposals(
        fit_pages,
        fit_ranked,
        threshold,
        proposal_root,
        prediction_provenance=benchmark_prediction_provenance(
            producer_schema=SUCCESSOR_GRAPH_BENCHMARK_SCHEMA,
            head="directed-successor",
            feature_version=SUCCESSOR_GRAPH_FEATURE_VERSION,
            prediction_mode=DOCUMENT_OOF_MODE,
            train_corpus_manifest_sha256=train_manifest_sha256,
            source_corpus_manifest_sha256=train_manifest_sha256,
            cross_validation_folds=cross_validation_folds,
        ),
    )
    calibration_proposals = _write_proposals(
        calibration_pages,
        calibration_ranked,
        threshold,
        proposal_root,
        prediction_provenance=benchmark_prediction_provenance(
            producer_schema=SUCCESSOR_GRAPH_BENCHMARK_SCHEMA,
            head="directed-successor",
            feature_version=SUCCESSOR_GRAPH_FEATURE_VERSION,
            prediction_mode=FROZEN_FIT_MODEL_MODE,
            train_corpus_manifest_sha256=train_manifest_sha256,
            source_corpus_manifest_sha256=train_manifest_sha256,
            cross_validation_folds=cross_validation_folds,
        ),
    )
    test_source_sha256 = (
        _file_sha256(test_manifest_path) if test_manifest_path is not None else ""
    )
    test_proposals = _write_proposals(
        test_pages,
        test_ranked,
        threshold,
        proposal_root,
        prediction_provenance=benchmark_prediction_provenance(
            producer_schema=SUCCESSOR_GRAPH_BENCHMARK_SCHEMA,
            head="directed-successor",
            feature_version=SUCCESSOR_GRAPH_FEATURE_VERSION,
            prediction_mode=FROZEN_FIT_MODEL_MODE,
            train_corpus_manifest_sha256=train_manifest_sha256,
            source_corpus_manifest_sha256=test_source_sha256 or train_manifest_sha256,
            cross_validation_folds=cross_validation_folds,
        ),
    )

    model_path: Path | None = None
    model_manifest_path: Path | None = None
    model_manifest: dict[str, Any] | None = None
    if model_output is not None:
        artifact = save_graph_model(
            model_path=model_output,
            schema=SUCCESSOR_GRAPH_MODEL_SCHEMA,
            head="directed-successor",
            feature_version=SUCCESSOR_GRAPH_FEATURE_VERSION,
            threshold=threshold,
            estimator=estimator,
            estimator_parameters=estimator_parameters,
            feature_count=feature_count,
            nearest_candidates=nearest_candidates,
            train_corpus_manifest_sha256=train_manifest_sha256,
            fit_document_count=len({page.sample["document_id"] for page in fit_pages}),
            fit_page_count=len(fit_pages),
            fit_candidate_count=fit_candidate_count,
            fit_positive_count=fit_positive_candidate_count,
            cross_validation_folds=cross_validation_folds,
            minimum_edge_precision=minimum_edge_precision,
            minimum_selected_edges=minimum_selected_edges,
            random_seed=random_seed,
            scikit_learn_version=sklearn_version,
            extra_manifest={
                "feature_names": list(SUCCESSOR_GRAPH_FEATURE_NAMES),
                "decoder_policy": (
                    "top-one-per-source then score-ordered degree-one acyclic path cover"
                ),
                "promotion_decision": "benchmark-only-directed-successor-graph",
                "fit_operating_point": operating_point,
                "prediction_policy": "page-wise feature batches",
            },
        )
        model_path = artifact.model_path
        model_manifest_path = artifact.manifest_path
        model_manifest = artifact.manifest

    # Evaluation labels are resolved only after every evaluation proposal exists.
    calibration_labels = [_load_labels(page) for page in calibration_pages]
    test_labels = [_load_labels(page) for page in test_pages]
    summaries = {
        "fit_oof": _score_pages(fit_pages, fit_labels, fit_ranked, threshold),
        "calibration": _score_pages(
            calibration_pages,
            calibration_labels,
            calibration_ranked,
            threshold,
        ),
    }
    if test_pages:
        summaries["test"] = _score_pages(
            test_pages,
            test_labels,
            test_ranked,
            threshold,
        )
    summaries_by_layout_stratum = {
        "fit_oof": _score_pages_by_layout_stratum(
            fit_pages,
            fit_labels,
            fit_ranked,
            threshold,
        ),
        "calibration": _score_pages_by_layout_stratum(
            calibration_pages,
            calibration_labels,
            calibration_ranked,
            threshold,
        ),
    }
    if test_pages:
        summaries_by_layout_stratum["test"] = _score_pages_by_layout_stratum(
            test_pages,
            test_labels,
            test_ranked,
            threshold,
        )

    report = {
        "schema": SUCCESSOR_GRAPH_BENCHMARK_SCHEMA,
        "feature_version": SUCCESSOR_GRAPH_FEATURE_VERSION,
        "train_corpus_manifest": str(train_manifest_path),
        "train_corpus_manifest_sha256": train_manifest_sha256,
        "test_corpus_manifest": (
            str(test_manifest_path) if test_manifest_path is not None else None
        ),
        "test_corpus_manifest_sha256": (
            _file_sha256(test_manifest_path)
            if test_manifest_path is not None
            else None
        ),
        "candidate_policy": (
            "bidirectional selected adjacency and relation candidates plus "
            f"{nearest_candidates} directed nearest geometry candidates per source"
        ),
        "candidate_direction": "directed",
        "decoder_policy": "top-one-per-source then score-ordered degree-one acyclic path cover",
        "label_policy": "published Comp-HRDoc immediate successors with partial endpoints",
        "head_policy": "successor head independent of provider regions and paragraph labels",
        "nearest_candidates": nearest_candidates,
        "fit_document_count": len({page.sample["document_id"] for page in fit_pages}),
        "calibration_document_count": len(
            {page.sample["document_id"] for page in calibration_pages}
        ),
        "test_document_count": len({page.sample["document_id"] for page in test_pages}),
        "fit_page_count": len(fit_pages),
        "calibration_page_count": len(calibration_pages),
        "test_page_count": len(test_pages),
        "feature_count": feature_count,
        "feature_names": list(SUCCESSOR_GRAPH_FEATURE_NAMES),
        "fit_candidate_count": fit_candidate_count,
        "fit_positive_candidate_count": fit_positive_candidate_count,
        "prediction_policy": "page-wise feature batches",
        "cross_validation_folds": cross_validation_folds,
        "cross_validation_unit": "document",
        "cross_validation_seed_policy": "random_seed plus zero-based fold index",
        "minimum_edge_precision": minimum_edge_precision,
        "minimum_selected_edges": minimum_selected_edges,
        "frozen_threshold": round(threshold, 8),
        "fit_operating_point": operating_point,
        "estimator": {
            "type": "HistGradientBoostingClassifier",
            "parameters": estimator_parameters,
            "scikit_learn_version": sklearn_version,
        },
        "answer_separation": {
            "all_inputs_loaded_before_fit_labels": True,
            "fit_labels_role": "document-OOF training and threshold selection",
            "evaluation_predictions_written_before_evaluation_labels": True,
            "candidate_generation_uses_labels": False,
            "paragraph_membership_labels_used_as_features": False,
        },
        "runtime_reorder": False,
        "promotion_decision": "benchmark-only-directed-successor-graph",
        "summary": summaries,
        "summary_by_layout_stratum": summaries_by_layout_stratum,
        "proposals": {
            "fit_oof": fit_proposals,
            "calibration": calibration_proposals,
            "test": test_proposals,
        },
        "proposal_artifacts": {
            "fit_oof": _proposal_artifacts(fit_proposals),
            "calibration": _proposal_artifacts(calibration_proposals),
            "test": _proposal_artifacts(test_proposals),
        },
        "model": (
            {
                "path": str(model_path),
                "manifest_path": str(model_manifest_path),
                "manifest": model_manifest,
            }
            if model_path is not None
            else None
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(report_path, report)
    return SuccessorGraphBenchmarkResult(
        report_path,
        proposal_root,
        report,
        model_path=model_path,
        model_manifest_path=model_manifest_path,
    )


@dataclass(frozen=True)
class SuccessorGraphPredictionResult:
    proposal_path: Path
    proposal: dict[str, Any]
    model_path: Path
    model_manifest_path: Path


def predict_successor_graph(
    hierarchy_input: str | Path | Mapping[str, Any],
    model_path: str | Path,
    *,
    output: str | Path,
    sample_id: str | None = None,
    partition: str = "predict",
) -> SuccessorGraphPredictionResult:
    """Score one answer-free hierarchy input with a serialized successor model."""

    input_sha256 = input_payload_sha256(hierarchy_input)
    payload = (
        dict(hierarchy_input)
        if isinstance(hierarchy_input, Mapping)
        else _json_object(Path(hierarchy_input), label="successor graph input")
    )
    if payload.get("schema") != HIERARCHY_INPUT_SCHEMA:
        raise ValueError("successor graph input has an unsupported schema")
    artifact = load_graph_model(
        model_path,
        expected_schema=SUCCESSOR_GRAPH_MODEL_SCHEMA,
        expected_head="directed-successor",
        expected_feature_version=SUCCESSOR_GRAPH_FEATURE_VERSION,
    )
    nearest_candidates = int(
        artifact.bundle.get("nearest_candidates") or DEFAULT_NEAREST_CANDIDATES
    )
    threshold = float(artifact.bundle["threshold"])
    estimator = artifact.bundle["estimator"]
    element_ids, base_rank, base_edges, candidates = _page_candidates(
        payload,
        nearest_candidates=nearest_candidates,
    )
    if candidates and len(candidates[0].features) != int(artifact.bundle["feature_count"]):
        raise ValueError("successor model feature_count does not match input candidates")
    sample = {
        "id": sample_id or str(payload.get("id") or Path(str(output)).stem),
        "document_id": str(payload.get("document_id") or payload.get("id") or "document"),
        "layout_stratum": str(payload.get("layout_stratum") or "unspecified"),
        "input_sha256": input_sha256,
    }
    page = _Page(
        corpus=Path("."),
        sample=sample,
        split=partition,
        element_ids=element_ids,
        base_rank=base_rank,
        base_edges=base_edges,
        candidates=candidates,
    )
    _, _, numpy, _ = _training_modules()
    scores = _predict_pages(estimator, [page], numpy=numpy)
    ranked = _rank_pages([page], scores)
    proposal_path = Path(output)
    proposal_root = proposal_path.parent
    proposal_root.mkdir(parents=True, exist_ok=True)
    # Write through the shared proposal helper into a temp root, then move/rename.
    written = _write_proposals(
        [page],
        ranked,
        threshold,
        proposal_root,
        prediction_provenance=serialized_prediction_provenance(
            producer_schema=SUCCESSOR_GRAPH_MODEL_SCHEMA,
            head="directed-successor",
            feature_version=SUCCESSOR_GRAPH_FEATURE_VERSION,
            model_manifest=artifact.manifest,
            model_manifest_path=artifact.manifest_path,
        ),
    )
    if not written:
        raise ValueError("successor graph prediction produced no proposal")
    generated = Path(written[0])
    if generated.resolve() != proposal_path.resolve():
        proposal_path.write_text(generated.read_text(encoding="utf-8"), encoding="utf-8")
        if generated != proposal_path:
            generated.unlink(missing_ok=True)
    proposal = _json_object(proposal_path, label="successor graph proposal")
    return SuccessorGraphPredictionResult(
        proposal_path=proposal_path,
        proposal=proposal,
        model_path=artifact.model_path,
        model_manifest_path=artifact.manifest_path,
    )


def benchmark_successor_decoder_ab(
    baseline_report_path: str | Path,
    *,
    output: str | Path,
    proposals_dir: str | Path | None = None,
) -> SuccessorDecoderAbResult:
    """Replay a frozen successor model with greedy and max-regret decoders."""

    baseline_path = Path(baseline_report_path).resolve()
    baseline = _json_object(baseline_path, label="successor graph baseline report")
    if baseline.get("schema") != SUCCESSOR_GRAPH_BENCHMARK_SCHEMA:
        raise ValueError("successor decoder A/B requires a successor graph report")
    baseline_feature_version = str(baseline.get("feature_version") or "")
    if baseline_feature_version not in {
        SUCCESSOR_GRAPH_TOPOLOGY_V3_FEATURE_VERSION,
        SUCCESSOR_GRAPH_FEATURE_VERSION,
    }:
        raise ValueError("successor decoder A/B baseline feature version is stale")
    if baseline.get("runtime_reorder") is not False:
        raise ValueError("successor decoder A/B baseline must keep runtime_reorder=false")

    model_record = baseline.get("model")
    if not isinstance(model_record, Mapping) or not model_record.get("path"):
        raise ValueError("successor decoder A/B baseline requires a serialized model")
    model_path = _resolve_report_path(baseline_path, model_record["path"])
    artifact = load_graph_model(
        model_path,
        expected_schema=SUCCESSOR_GRAPH_MODEL_SCHEMA,
        expected_head="directed-successor",
        expected_feature_version=baseline_feature_version,
    )
    recorded_manifest = model_record.get("manifest")
    if not isinstance(recorded_manifest, Mapping) or dict(recorded_manifest) != artifact.manifest:
        raise ValueError("successor decoder A/B model manifest differs from baseline report")
    threshold = float(baseline.get("frozen_threshold"))
    if float(artifact.bundle["threshold"]) != threshold:
        raise ValueError("successor decoder A/B threshold differs from frozen model")
    nearest_candidates = int(
        artifact.bundle.get("nearest_candidates") or DEFAULT_NEAREST_CANDIDATES
    )
    if nearest_candidates != int(baseline.get("nearest_candidates") or 0):
        raise ValueError("successor decoder A/B nearest-candidate policy differs")

    train_manifest_path = _resolve_report_path(
        baseline_path,
        baseline.get("train_corpus_manifest"),
    )
    train_manifest = _json_object(
        train_manifest_path,
        label="successor decoder A/B train manifest",
    )
    train_manifest_sha256 = _file_sha256(train_manifest_path)
    if train_manifest_sha256 != baseline.get("train_corpus_manifest_sha256"):
        raise ValueError("successor decoder A/B train corpus hash differs from baseline")
    calibration_pages = _load_answer_free_pages(
        train_manifest_path.parent,
        train_manifest,
        split="calibration",
        nearest_candidates=nearest_candidates,
        feature_version=baseline_feature_version,
    )
    if not calibration_pages:
        raise ValueError("successor decoder A/B baseline has no calibration pages")

    test_pages: list[_Page] = []
    test_manifest_path: Path | None = None
    raw_test_manifest_path = baseline.get("test_corpus_manifest")
    if raw_test_manifest_path:
        test_manifest_path = _resolve_report_path(
            baseline_path,
            raw_test_manifest_path,
        )
        test_manifest = _json_object(
            test_manifest_path,
            label="successor decoder A/B test manifest",
        )
        if _file_sha256(test_manifest_path) != baseline.get(
            "test_corpus_manifest_sha256"
        ):
            raise ValueError("successor decoder A/B test corpus hash differs from baseline")
        test_pages = _load_answer_free_pages(
            test_manifest_path.parent,
            test_manifest,
            split="test",
            nearest_candidates=nearest_candidates,
            accept_all_partitions=True,
            feature_version=baseline_feature_version,
        )

    _require_disjoint_documents([], calibration_pages, test_pages)
    _require_unique_sample_ids([], calibration_pages, test_pages)
    _, _, numpy, _ = _training_modules()
    calibration_scores = _predict_pages(
        artifact.bundle["estimator"],
        calibration_pages,
        numpy=numpy,
    )
    test_scores = _predict_pages(
        artifact.bundle["estimator"],
        test_pages,
        numpy=numpy,
    )
    calibration_ranked = _rank_pages(calibration_pages, calibration_scores)
    test_ranked = _rank_pages(test_pages, test_scores)

    report_path = Path(output)
    proposal_root = (
        Path(proposals_dir)
        if proposals_dir is not None
        else report_path.parent / f"{report_path.stem}.proposals"
    )
    proposal_root.mkdir(parents=True, exist_ok=True)
    baseline_sha256 = _file_sha256(baseline_path)
    prediction_provenance = serialized_prediction_provenance(
        producer_schema=SUCCESSOR_GRAPH_MODEL_SCHEMA,
        head="directed-successor",
        feature_version=baseline_feature_version,
        model_manifest=artifact.manifest,
        model_manifest_path=artifact.manifest_path,
    )
    prediction_provenance.update(
        {
            "decoder_policy": MAX_REGRET_DECODER,
            "decoder_baseline_report_sha256": baseline_sha256,
        }
    )
    calibration_proposals = _write_proposals(
        calibration_pages,
        calibration_ranked,
        threshold,
        proposal_root,
        prediction_provenance=prediction_provenance,
        decoder_policy=MAX_REGRET_DECODER,
        feature_version=baseline_feature_version,
    )
    test_proposals = _write_proposals(
        test_pages,
        test_ranked,
        threshold,
        proposal_root,
        prediction_provenance=prediction_provenance,
        decoder_policy=MAX_REGRET_DECODER,
        feature_version=baseline_feature_version,
    )

    # Evaluation labels open only after every max-regret proposal is durable.
    calibration_labels = [_load_labels(page) for page in calibration_pages]
    test_labels = [_load_labels(page) for page in test_pages]
    pages_by_split = {"calibration": calibration_pages}
    labels_by_split = {"calibration": calibration_labels}
    ranked_by_split = {"calibration": calibration_ranked}
    if test_pages:
        pages_by_split["test"] = test_pages
        labels_by_split["test"] = test_labels
        ranked_by_split["test"] = test_ranked

    summaries: dict[str, dict[str, Any]] = {
        SCORE_GREEDY_DECODER: {},
        MAX_REGRET_DECODER: {},
    }
    summaries_by_layout: dict[str, dict[str, Any]] = {
        SCORE_GREEDY_DECODER: {},
        MAX_REGRET_DECODER: {},
    }
    deltas: dict[str, dict[str, float | int]] = {}
    for split in pages_by_split:
        baseline_summary = _score_pages(
            pages_by_split[split],
            labels_by_split[split],
            ranked_by_split[split],
            threshold,
            decoder_policy=SCORE_GREEDY_DECODER,
        )
        expected_summary = baseline.get("summary", {}).get(split)
        if not isinstance(expected_summary, Mapping) or (
            baseline_summary["selected_relation"]
            != expected_summary.get("selected_relation")
        ):
            raise ValueError(
                f"successor decoder A/B {split} baseline replay does not match report"
            )
        candidate_summary = _score_pages(
            pages_by_split[split],
            labels_by_split[split],
            ranked_by_split[split],
            threshold,
            decoder_policy=MAX_REGRET_DECODER,
        )
        summaries[SCORE_GREEDY_DECODER][split] = baseline_summary
        summaries[MAX_REGRET_DECODER][split] = candidate_summary
        summaries_by_layout[SCORE_GREEDY_DECODER][split] = (
            _score_pages_by_layout_stratum(
                pages_by_split[split],
                labels_by_split[split],
                ranked_by_split[split],
                threshold,
                decoder_policy=SCORE_GREEDY_DECODER,
            )
        )
        summaries_by_layout[MAX_REGRET_DECODER][split] = (
            _score_pages_by_layout_stratum(
                pages_by_split[split],
                labels_by_split[split],
                ranked_by_split[split],
                threshold,
                decoder_policy=MAX_REGRET_DECODER,
            )
        )
        deltas[split] = _relation_summary_delta(
            baseline_summary["selected_relation"],
            candidate_summary["selected_relation"],
        )

    report = {
        "schema": SUCCESSOR_DECODER_AB_SCHEMA,
        "feature_version": baseline_feature_version,
        "baseline_report": str(baseline_path),
        "baseline_report_sha256": baseline_sha256,
        "model": str(artifact.model_path),
        "model_sha256": artifact.manifest["model_sha256"],
        "train_corpus_manifest": str(train_manifest_path),
        "train_corpus_manifest_sha256": train_manifest_sha256,
        "test_corpus_manifest": (
            str(test_manifest_path) if test_manifest_path is not None else None
        ),
        "test_corpus_manifest_sha256": (
            _file_sha256(test_manifest_path)
            if test_manifest_path is not None
            else None
        ),
        "frozen_threshold": round(threshold, 8),
        "threshold_source": "baseline fit-document-OOF operating point",
        "nearest_candidates": nearest_candidates,
        "baseline_decoder": SCORE_GREEDY_DECODER,
        "candidate_decoder": MAX_REGRET_DECODER,
        "candidate_scope": "all model-scored directed edges above frozen threshold",
        "answer_separation": {
            "model_and_threshold_frozen_before_decoder_ab": True,
            "candidate_generation_uses_labels": False,
            "evaluation_predictions_written_before_evaluation_labels": True,
            "baseline_replay_matches_source_report": True,
        },
        "runtime_reorder": False,
        "promotion_decision": "benchmark-only-max-regret-successor-decoder",
        "summary": summaries,
        "summary_by_layout_stratum": summaries_by_layout,
        "delta": deltas,
        "proposals": {
            "calibration": calibration_proposals,
            "test": test_proposals,
        },
        "proposal_artifacts": {
            "calibration": _proposal_artifacts(calibration_proposals),
            "test": _proposal_artifacts(test_proposals),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(report_path, report)
    return SuccessorDecoderAbResult(report_path, proposal_root, report)


def _resolve_report_path(report_path: Path, value: object) -> Path:
    raw_path = Path(str(value or ""))
    if not str(raw_path):
        raise ValueError("successor decoder A/B report artifact path is missing")
    candidates = [raw_path, report_path.parent / raw_path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError(f"successor decoder A/B report artifact is missing: {raw_path}")


def _relation_summary_delta(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, float | int]:
    return {
        "correct": int(candidate.get("correct") or 0)
        - int(baseline.get("correct") or 0),
        "predicted": int(candidate.get("predicted") or 0)
        - int(baseline.get("predicted") or 0),
        "precision": round(
            float(candidate.get("precision") or 0.0)
            - float(baseline.get("precision") or 0.0),
            8,
        ),
        "recall": round(
            float(candidate.get("recall") or 0.0)
            - float(baseline.get("recall") or 0.0),
            8,
        ),
        "f1": round(
            float(candidate.get("f1") or 0.0)
            - float(baseline.get("f1") or 0.0),
            8,
        ),
    }


def _corpus_manifest(corpus: Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = corpus / "provider_hierarchy_corpus_manifest.json"
    manifest = _json_object(manifest_path, label="provider hierarchy manifest")
    if manifest.get("schema") != PROVIDER_HIERARCHY_CORPUS_SCHEMA:
        raise ValueError("unsupported provider hierarchy corpus schema")
    if manifest.get("inference_inputs_are_answer_free") is not True:
        raise ValueError("successor graph corpus must declare answer-free inputs")
    if not isinstance(manifest.get("samples"), list):
        raise ValueError("provider hierarchy corpus has no samples")
    return manifest_path, manifest


def _load_answer_free_pages(
    corpus: Path,
    manifest: Mapping[str, Any],
    *,
    split: str,
    nearest_candidates: int,
    accept_all_partitions: bool = False,
    feature_version: str = SUCCESSOR_GRAPH_FEATURE_VERSION,
) -> list[_Page]:
    pages: list[_Page] = []
    for raw_sample in manifest["samples"]:
        if not isinstance(raw_sample, Mapping):
            raise ValueError("provider hierarchy samples must be objects")
        partition = str(raw_sample.get("partition") or "")
        if not accept_all_partitions and partition != split:
            continue
        sample = dict(raw_sample)
        sample_id = str(sample.get("id") or "").strip()
        document_id = str(sample.get("document_id") or "").strip()
        if not sample_id or not document_id:
            raise ValueError("successor graph samples require id and document_id")
        sample["id"] = sample_id
        sample["document_id"] = document_id
        input_path = _confined_path(corpus, sample.get("input"), label="sample input")
        _verify_hash(input_path, sample.get("input_sha256"), label="sample input")
        payload = _json_object(input_path, label="successor graph input")
        if payload.get("schema") != HIERARCHY_INPUT_SCHEMA:
            raise ValueError("successor graph input has an unsupported schema")
        element_ids, base_rank, base_edges, candidates = _page_candidates(
            payload,
            nearest_candidates=nearest_candidates,
            feature_version=feature_version,
        )
        pages.append(
            _Page(
                corpus=corpus,
                sample=sample,
                split=split,
                element_ids=element_ids,
                base_rank=base_rank,
                base_edges=base_edges,
                candidates=candidates,
            )
        )
    return pages


def _page_candidates(
    payload: Mapping[str, Any],
    *,
    nearest_candidates: int = DEFAULT_NEAREST_CANDIDATES,
    feature_version: str = SUCCESSOR_GRAPH_FEATURE_VERSION,
) -> tuple[
    tuple[str, ...],
    dict[str, int],
    frozenset[tuple[str, str]],
    tuple[_Candidate, ...],
]:
    if feature_version not in {
        SUCCESSOR_GRAPH_TOPOLOGY_V3_FEATURE_VERSION,
        SUCCESSOR_GRAPH_FEATURE_VERSION,
    }:
        raise ValueError("unsupported successor graph candidate feature version")
    width = float(payload.get("width") or 0.0)
    height = float(payload.get("height") or 0.0)
    raw_elements = payload.get("elements")
    if width <= 0 or height <= 0 or not isinstance(raw_elements, list):
        raise ValueError("successor graph input requires page geometry and elements")
    elements: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_element in raw_elements:
        if not isinstance(raw_element, Mapping):
            raise ValueError("successor graph elements must be objects")
        element = dict(raw_element)
        element_id = str(element.get("id") or "").strip()
        if not element_id or element_id in seen_ids:
            raise ValueError("successor graph element ids must be unique")
        seen_ids.add(element_id)
        element["id"] = element_id
        element["_bbox"] = BBox.from_any(element.get("box"))
        elements.append(element)
    elements.sort(key=_element_sort_key)

    assignments = infer_semantic_reading_order(
        [element["_bbox"] for element in elements],
        page_width=width,
        page_height=height,
        texts=[""] * len(elements),
    )
    assignment_by_id = {
        str(elements[assignment.item_index]["id"]): assignment
        for assignment in assignments
    }
    base_indices = [
        assignment.item_index
        for assignment in sorted(assignments, key=lambda item: item.semantic_order)
    ]
    if len(base_indices) != len(elements) or len(set(base_indices)) != len(elements):
        raise ValueError("selected order did not return a complete line permutation")
    base_ids = [str(elements[index]["id"]) for index in base_indices]
    base_rank = {element_id: rank for rank, element_id in enumerate(base_ids)}
    base_edges = frozenset(zip(base_ids, base_ids[1:]))
    by_id = {str(element["id"]): element for element in elements}

    relation = infer_relation_graph_order_evidence(
        [element["_bbox"] for element in elements],
        page_width=width,
        page_height=height,
    )
    relation_scores: dict[tuple[str, str], float] = defaultdict(float)
    for edge in relation.candidate_edges:
        source = str(elements[edge.source]["id"])
        target = str(elements[edge.target]["id"])
        relation_scores[(source, target)] = max(
            relation_scores[(source, target)],
            float(edge.score),
        )
    selected_relation = {
        (str(elements[edge.source]["id"]), str(elements[edge.target]["id"]))
        for edge in relation.selected_edge_diagnostics
    }
    topology = _page_topology_context(elements, width=width, height=height)
    aligned_expansion_pairs: set[tuple[str, str]] = set()
    column_handoff_pairs: set[tuple[str, str]] = set()
    if feature_version == SUCCESSOR_GRAPH_FEATURE_VERSION:
        aligned_expansion_pairs, column_handoff_pairs = (
            _sparse_topology_candidate_pairs(
                elements,
                assignment_by_id=assignment_by_id,
                topology=topology,
            )
        )

    candidate_pairs = set(base_edges)
    candidate_pairs.update((target, source) for source, target in base_edges)
    for source, target in relation_scores:
        candidate_pairs.add((source, target))
        candidate_pairs.add((target, source))
    candidate_pairs.update(aligned_expansion_pairs)
    candidate_pairs.update(column_handoff_pairs)
    for source in elements:
        source_box = source["_bbox"]
        source_center_x = (source_box.x0 + source_box.x1) / 2
        source_center_y = (source_box.y0 + source_box.y1) / 2
        nearest: list[tuple[float, float, float, str]] = []
        for target in elements:
            if source["id"] == target["id"]:
                continue
            target_box = target["_bbox"]
            target_center_x = (target_box.x0 + target_box.x1) / 2
            target_center_y = (target_box.y0 + target_box.y1) / 2
            distance = (
                abs(target_center_x - source_center_x) / width
                + abs(target_center_y - source_center_y) / height
            )
            nearest.append((distance, target_box.y0, target_box.x0, str(target["id"])))
        candidate_pairs.update(
            (str(source["id"]), target_id)
            for _distance, _y0, _x0, target_id in sorted(nearest)[:nearest_candidates]
        )

    candidates: list[_Candidate] = []
    page_size = max(len(elements), 1)
    for source_id, target_id in sorted(candidate_pairs):
        source = by_id[source_id]
        target = by_id[target_id]
        source_role = str(source.get("role") or "").casefold()
        target_role = str(target.get("role") or "").casefold()
        rank_delta = base_rank[target_id] - base_rank[source_id]
        relation_forward = relation_scores[(source_id, target_id)]
        relation_reverse = relation_scores[(target_id, source_id)]
        features = (
            *_pair_features(source, target, width=width, height=height),
            rank_delta / page_size,
            float(rank_delta == 1),
            float(rank_delta == -1),
            relation_forward,
            relation_reverse,
            float((source_id, target_id) in selected_relation),
            float((target_id, source_id) in selected_relation),
            float(source_role == "text"),
            float(source_role == "figure"),
            float(source_role == "table"),
            float(target_role == "text"),
            float(target_role == "figure"),
            float(target_role == "table"),
            float(source_role == target_role),
            *_assignment_pair_features(
                assignment_by_id[source_id],
                assignment_by_id[target_id],
                page_size=page_size,
            ),
            *_topology_pair_features(
                source,
                target,
                elements=elements,
                context=topology,
                page_size=page_size,
            ),
            *(
                (
                    float((source_id, target_id) in aligned_expansion_pairs),
                    float((source_id, target_id) in column_handoff_pairs),
                )
                if feature_version == SUCCESSOR_GRAPH_FEATURE_VERSION
                else ()
            ),
        )
        target_box = target["_bbox"]
        source_box = source["_bbox"]
        center_distance = (
            abs(target_box.x0 + target_box.x1 - source_box.x0 - source_box.x1)
            / (2 * width)
            + abs(target_box.y0 + target_box.y1 - source_box.y0 - source_box.y1)
            / (2 * height)
        )
        tie_priority: tuple[float | str, ...] = (
            -float((source_id, target_id) in selected_relation),
            -float(rank_delta == 1),
            -relation_forward,
            center_distance,
            float(base_rank[target_id]),
            target_id,
        )
        candidates.append(_Candidate(source_id, target_id, features, tie_priority))
    return tuple(base_ids), base_rank, base_edges, tuple(candidates)


def _assignment_pair_features(
    source: Any,
    target: Any,
    *,
    page_size: int,
) -> tuple[float, ...]:
    column_count = max(int(source.column_count), int(target.column_count), 1)
    source_column = source.column_index
    target_column = target.column_index
    source_column_feature = (
        float(source_column) / column_count if source_column is not None else -1.0
    )
    target_column_feature = (
        float(target_column) / column_count if target_column is not None else -1.0
    )
    comparable_columns = source_column is not None and target_column is not None
    column_delta = (
        float(target_column - source_column) / column_count
        if comparable_columns
        else 0.0
    )
    source_region = str(source.region_path or "")
    target_region = str(target.region_path or "")
    return (
        source_column_feature,
        target_column_feature,
        column_delta,
        float(comparable_columns and source_column == target_column),
        float(source.flow_segment_index == target.flow_segment_index),
        (target.flow_segment_index - source.flow_segment_index) / max(page_size, 1),
        float(bool(source_region) and source_region == target_region),
    )


def _sparse_topology_candidate_pairs(
    elements: list[dict[str, Any]],
    *,
    assignment_by_id: Mapping[str, Any],
    topology: _PageTopologyContext,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    page_size = max(len(elements), 1)
    budget = max(2, min(16, math.ceil(math.sqrt(page_size))))
    aligned_pairs = {
        edge
        for edge, rank in topology.aligned_rank_by_source.items()
        if rank < budget
    }

    element_by_id = {str(element["id"]): element for element in elements}
    column_groups: dict[tuple[int, int], list[str]] = defaultdict(list)
    for element_id, assignment in assignment_by_id.items():
        column_index = assignment.column_index
        if column_index is None or int(assignment.column_count) < 2:
            continue
        column_groups[
            (int(assignment.flow_segment_index), int(column_index))
        ].append(element_id)

    def center_y(element_id: str) -> float:
        box = element_by_id[element_id]["_bbox"]
        return (box.y0 + box.y1) / 2

    handoff_pairs: set[tuple[str, str]] = set()
    for (segment_index, column_index), source_ids in column_groups.items():
        target_ids = column_groups.get((segment_index, column_index + 1), [])
        if not target_ids:
            continue
        ordered_sources = sorted(
            source_ids,
            key=lambda element_id: (
                center_y(element_id),
                element_by_id[element_id]["_bbox"].x0,
                element_id,
            ),
        )
        ordered_targets = sorted(
            target_ids,
            key=lambda element_id: (
                center_y(element_id),
                element_by_id[element_id]["_bbox"].x0,
                element_id,
            ),
        )
        for source_id in ordered_sources[-budget:]:
            source_center_y = center_y(source_id)
            handoff_pairs.update(
                (source_id, target_id)
                for target_id in ordered_targets
                if center_y(target_id) < source_center_y
            )
    return aligned_pairs, handoff_pairs


def _page_topology_context(
    elements: list[dict[str, Any]],
    *,
    width: float,
    height: float,
) -> _PageTopologyContext:
    distance_by_source: dict[str, list[tuple[float, float, float, str]]] = defaultdict(list)
    distance_by_target: dict[str, list[tuple[float, float, float, str]]] = defaultdict(list)
    aligned_by_source: dict[str, list[tuple[float, float, float, str]]] = defaultdict(list)
    aligned_by_target: dict[str, list[tuple[float, float, float, str]]] = defaultdict(list)
    heights = [element["_bbox"].height for element in elements if element["_bbox"].height > 0]
    median_height = float(median(heights)) if heights else 1.0

    for source in elements:
        source_id = str(source["id"])
        source_box = source["_bbox"]
        source_center_x = (source_box.x0 + source_box.x1) / 2
        source_center_y = (source_box.y0 + source_box.y1) / 2
        for target in elements:
            target_id = str(target["id"])
            if source_id == target_id:
                continue
            target_box = target["_bbox"]
            target_center_x = (target_box.x0 + target_box.x1) / 2
            target_center_y = (target_box.y0 + target_box.y1) / 2
            distance = (
                abs(target_center_x - source_center_x) / width
                + abs(target_center_y - source_center_y) / height
            )
            distance_by_source[source_id].append(
                (distance, target_box.y0, target_box.x0, target_id)
            )
            distance_by_target[target_id].append(
                (distance, source_box.y0, source_box.x0, source_id)
            )
            horizontal_overlap = _box_horizontal_overlap(source_box, target_box)
            if target_center_y > source_center_y and horizontal_overlap >= 0.25:
                vertical_gap = max(0.0, target_box.y0 - source_box.y1)
                aligned_by_source[source_id].append(
                    (vertical_gap, abs(target_center_x - source_center_x), target_box.x0, target_id)
                )
                aligned_by_target[target_id].append(
                    (vertical_gap, abs(target_center_x - source_center_x), source_box.x0, source_id)
                )

    return _PageTopologyContext(
        distance_rank_by_source=_rank_pairs(distance_by_source, reverse_pair=False),
        distance_rank_by_target=_rank_pairs(distance_by_target, reverse_pair=True),
        aligned_rank_by_source=_rank_pairs(aligned_by_source, reverse_pair=False),
        aligned_rank_by_target=_rank_pairs(aligned_by_target, reverse_pair=True),
        median_height=max(median_height, 1.0),
    )


def _rank_pairs(
    values: Mapping[str, list[tuple[float, float, float, str]]],
    *,
    reverse_pair: bool,
) -> dict[tuple[str, str], int]:
    ranks: dict[tuple[str, str], int] = {}
    for anchor, alternatives in values.items():
        for rank, (*_sort_values, other) in enumerate(sorted(alternatives)):
            pair = (str(other), str(anchor)) if reverse_pair else (str(anchor), str(other))
            ranks[pair] = rank
    return ranks


def _topology_pair_features(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    elements: list[dict[str, Any]],
    context: _PageTopologyContext,
    page_size: int,
) -> tuple[float, ...]:
    source_id = str(source["id"])
    target_id = str(target["id"])
    edge = (source_id, target_id)
    denominator = max(page_size - 2, 1)
    source_distance_rank = context.distance_rank_by_source[edge]
    target_distance_rank = context.distance_rank_by_target[edge]
    aligned_source_rank = context.aligned_rank_by_source.get(edge)
    aligned_target_rank = context.aligned_rank_by_target.get(edge)
    source_box = source["_bbox"]
    target_box = target["_bbox"]
    vertical_gap = (target_box.y0 - source_box.y1) / context.median_height
    left_edge_delta = (target_box.x0 - source_box.x0) / context.median_height
    corridor, blocker_count = _vertical_corridor_blockers(source, target, elements)
    return (
        source_distance_rank / denominator,
        target_distance_rank / denominator,
        float(source_distance_rank == 0 and target_distance_rank == 0),
        float(aligned_source_rank is not None),
        (aligned_source_rank / denominator if aligned_source_rank is not None else 1.0),
        float(aligned_target_rank is not None),
        (aligned_target_rank / denominator if aligned_target_rank is not None else 1.0),
        float(aligned_source_rank == 0 and aligned_target_rank == 0),
        max(-1.0, min(1.0, vertical_gap / 8.0)),
        max(-1.0, min(1.0, left_edge_delta / 20.0)),
        float(corridor),
        blocker_count / max(page_size, 1),
        float(corridor and blocker_count == 0),
    )


def _vertical_corridor_blockers(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    elements: list[dict[str, Any]],
) -> tuple[bool, int]:
    source_box = source["_bbox"]
    target_box = target["_bbox"]
    source_center_y = (source_box.y0 + source_box.y1) / 2
    target_center_y = (target_box.y0 + target_box.y1) / 2
    if target_center_y <= source_center_y or _box_horizontal_overlap(source_box, target_box) < 0.25:
        return False, 0
    blockers = 0
    for other in elements:
        if other["id"] in {source["id"], target["id"]}:
            continue
        other_box = other["_bbox"]
        other_center_y = (other_box.y0 + other_box.y1) / 2
        if not source_center_y < other_center_y < target_center_y:
            continue
        if (
            _box_horizontal_overlap(source_box, other_box) >= 0.1
            and _box_horizontal_overlap(target_box, other_box) >= 0.1
        ):
            blockers += 1
    return True, blockers


def _box_horizontal_overlap(source: BBox, target: BBox) -> float:
    overlap = max(0.0, min(source.x1, target.x1) - max(source.x0, target.x0))
    return _ratio(overlap, min(max(source.width, 1.0), max(target.width, 1.0)))


def _pair_features(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    width: float,
    height: float,
) -> tuple[float, ...]:
    source_box = source["_bbox"]
    target_box = target["_bbox"]
    source_width = max(source_box.width, 1.0)
    source_height = max(source_box.height, 1.0)
    target_width = max(target_box.width, 1.0)
    target_height = max(target_box.height, 1.0)
    source_center_x = (source_box.x0 + source_box.x1) / 2
    source_center_y = (source_box.y0 + source_box.y1) / 2
    target_center_x = (target_box.x0 + target_box.x1) / 2
    target_center_y = (target_box.y0 + target_box.y1) / 2
    horizontal_overlap = _ratio(
        max(0.0, min(source_box.x1, target_box.x1) - max(source_box.x0, target_box.x0)),
        min(source_width, target_width),
    )
    vertical_overlap = _ratio(
        max(0.0, min(source_box.y1, target_box.y1) - max(source_box.y0, target_box.y0)),
        min(source_height, target_height),
    )
    source_text = str(source.get("text") or "").strip()
    target_text = str(target.get("text") or "").strip()
    return (
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
        math.log1p(len(source_text)) / 8,
        math.log1p(len(target_text)) / 8,
        float(source_text.endswith((".", ":", ";", "?", "!"))),
        float(bool(target_text) and target_text[0].islower()),
        float(bool(target_text) and target_text[0].isdigit()),
    )


def _training_matrix(
    pages: list[_Page],
    labels: list[_Labels],
) -> tuple[Any, Any, Any]:
    _, _, numpy, _ = _training_modules()
    features: list[tuple[float, ...]] = []
    targets: list[int] = []
    groups: list[str] = []
    for page, page_labels in zip(pages, labels, strict=True):
        for candidate in page.candidates:
            features.append(candidate.features)
            targets.append(int((candidate.source, candidate.target) in page_labels.edges))
            groups.append(str(page.sample["document_id"]))
    if not features or len(set(targets)) < 2:
        raise ValueError("fit successor graph candidates require positive and negative labels")
    return (
        numpy.asarray(features, dtype=float),
        numpy.asarray(targets, dtype=int),
        numpy.asarray(groups),
    )


def _predict_pages(estimator: Any, pages: list[_Page], *, numpy: Any) -> Any:
    page_batches = [
        [candidate.features for candidate in page.candidates] for page in pages
    ]
    return flatten_page_scores(
        predict_feature_batches(estimator, page_batches, numpy=numpy),
        numpy=numpy,
    )


def _rank_pages(pages: list[_Page], scores: Any) -> list[_RankedPage]:
    expected_score_count = sum(len(page.candidates) for page in pages)
    if len(scores) != expected_score_count:
        raise ValueError("successor graph score count does not match candidates")
    ranked_pages: list[_RankedPage] = []
    cursor = 0
    for page in pages:
        page_scores = scores[cursor : cursor + len(page.candidates)]
        cursor += len(page.candidates)
        by_source: dict[str, list[tuple[float, _Candidate]]] = defaultdict(list)
        for candidate, score in zip(page.candidates, page_scores, strict=True):
            by_source[candidate.source].append((float(score), candidate))
        top_edges: list[_RankedEdge] = []
        all_edges: list[_RankedEdge] = []
        for source in sorted(by_source, key=lambda value: (page.base_rank[value], value)):
            alternatives = sorted(
                by_source[source],
                key=lambda item: (-item[0], item[1].tie_priority),
            )
            top_score = alternatives[0][0]
            second_score = alternatives[1][0] if len(alternatives) > 1 else 0.0
            for rank, (score, candidate) in enumerate(alternatives, start=1):
                edge = _RankedEdge(
                    source=candidate.source,
                    target=candidate.target,
                    score=score,
                    rank=rank,
                    top_score_margin=(top_score - second_score if rank == 1 else top_score - score),
                )
                all_edges.append(edge)
                if rank == 1:
                    top_edges.append(edge)
        ranked_pages.append(_RankedPage(tuple(top_edges), tuple(all_edges)))
    return ranked_pages


def _freeze_threshold(
    pages: list[_Page],
    labels: list[_Labels],
    ranked_pages: list[_RankedPage],
    scores: Any,
    *,
    minimum_edge_precision: float,
    minimum_selected_edges: int,
    numpy: Any,
) -> tuple[float, dict[str, Any]]:
    thresholds = numpy.unique(numpy.quantile(scores, numpy.linspace(0.0, 1.0, 501)))
    candidates: list[tuple[float, float, float, int, float, dict[str, Any]]] = []
    for raw_threshold in thresholds:
        threshold = float(raw_threshold)
        metrics = _score_pages(pages, labels, ranked_pages, threshold)
        relation = metrics["selected_relation"]
        selected_count = int(relation["predicted"])
        edge_precision = float(relation["precision"])
        if selected_count < minimum_selected_edges or edge_precision < minimum_edge_precision:
            continue
        candidates.append(
            (
                float(relation["f1"]),
                edge_precision,
                float(relation["recall"]),
                selected_count,
                threshold,
                metrics,
            )
        )
    if not candidates:
        raise ValueError("fit OOF scores have no successor graph operating point")
    relation_f1, edge_precision, edge_recall, selected_count, threshold, metrics = max(
        candidates,
        key=lambda item: item[:5],
    )
    return threshold, {
        "selected_edge_count": selected_count,
        "edge_precision": round(edge_precision, 8),
        "edge_recall": round(edge_recall, 8),
        "edge_f1": round(relation_f1, 8),
        "selected_relation": metrics["selected_relation"],
        "candidate_recall": metrics["candidate_recall"],
        "decoder_diagnostics": metrics["decoder_diagnostics"],
    }


def _score_pages(
    pages: list[_Page],
    labels: list[_Labels],
    ranked_pages: list[_RankedPage],
    threshold: float,
    *,
    decoder_policy: str = SCORE_GREEDY_DECODER,
) -> dict[str, Any]:
    if len(pages) != len(labels) or len(pages) != len(ranked_pages):
        raise ValueError("successor graph pages, labels, and rankings must align")
    selected_counts = Counter()
    top_counts = Counter()
    flat_counts = Counter()
    candidate_counts = Counter()
    within_counts = Counter()
    cross_counts = Counter()
    decoder_counts = Counter()
    candidate_page_count = 0
    labelled_page_count = 0
    for page, page_labels, ranked in zip(pages, labels, ranked_pages, strict=True):
        decoded = _decode_page(
            page,
            ranked,
            threshold,
            decoder_policy=decoder_policy,
        )
        candidate_pairs = {
            (candidate.source, candidate.target) for candidate in page.candidates
        }
        candidate_page_count += bool(page.candidates)
        labelled_page_count += bool(page_labels.edges)
        _accumulate_partial_counts(selected_counts, decoded.selected_edges, page_labels.edges)
        _accumulate_partial_counts(top_counts, decoded.top_threshold_edges, page_labels.edges)
        _accumulate_partial_counts(flat_counts, page.base_edges, page_labels.edges)
        candidate_counts["correct"] += len(candidate_pairs & page_labels.edges)
        candidate_counts["labels"] += len(page_labels.edges)
        within_truth = {
            edge for edge, scope in page_labels.scopes.items() if scope == "within-oracle-region"
        }
        cross_truth = page_labels.edges - within_truth
        within_counts["correct"] += len(decoded.selected_edges & within_truth)
        within_counts["labels"] += len(within_truth)
        cross_counts["correct"] += len(decoded.selected_edges & cross_truth)
        cross_counts["labels"] += len(cross_truth)
        decoder_counts.update(decoded.diagnostics)
    return {
        "page_count": len(pages),
        "candidate_page_count": candidate_page_count,
        "labelled_page_count": labelled_page_count,
        "candidate_edge_count": sum(len(page.candidates) for page in pages),
        "candidate_recall": _recall_summary(candidate_counts),
        "top_candidate_relation": _partial_relation_summary(top_counts),
        "selected_relation": _partial_relation_summary(selected_counts),
        "flat_relation": _partial_relation_summary(flat_counts),
        "within_region_recovery": _recall_summary(within_counts),
        "cross_region_recovery": _recall_summary(cross_counts),
        "decoder_diagnostics": dict(sorted(decoder_counts.items())),
        "runtime_reorder": False,
    }


def _score_pages_by_layout_stratum(
    pages: list[_Page],
    labels: list[_Labels],
    ranked_pages: list[_RankedPage],
    threshold: float,
    *,
    decoder_policy: str = SCORE_GREEDY_DECODER,
) -> dict[str, dict[str, Any]]:
    grouped_pages: dict[str, list[_Page]] = defaultdict(list)
    grouped_labels: dict[str, list[_Labels]] = defaultdict(list)
    grouped_ranked: dict[str, list[_RankedPage]] = defaultdict(list)
    for page, page_labels, ranked in zip(pages, labels, ranked_pages, strict=True):
        stratum = str(page.sample.get("layout_stratum") or "unspecified")
        grouped_pages[stratum].append(page)
        grouped_labels[stratum].append(page_labels)
        grouped_ranked[stratum].append(ranked)
    return {
        stratum: _score_pages(
            grouped_pages[stratum],
            grouped_labels[stratum],
            grouped_ranked[stratum],
            threshold,
            decoder_policy=decoder_policy,
        )
        for stratum in sorted(grouped_pages)
    }


def _decode_page(
    page: _Page,
    ranked: _RankedPage,
    threshold: float,
    *,
    decoder_policy: str = SCORE_GREEDY_DECODER,
) -> _DecodedPage:
    if decoder_policy == MAX_REGRET_DECODER:
        return _decode_page_max_regret(page, ranked, threshold)
    if decoder_policy != SCORE_GREEDY_DECODER:
        raise ValueError(f"unsupported successor decoder policy: {decoder_policy}")
    accepted = [edge for edge in ranked.top_edges if edge.score >= threshold]
    accepted.sort(
        key=lambda edge: (
            -edge.score,
            page.base_rank[edge.source],
            page.base_rank[edge.target],
            edge.source,
            edge.target,
        )
    )
    ordered_edges = [(edge.source, edge.target) for edge in accepted]
    top_threshold_edges = frozenset(ordered_edges)
    merged = merge_relation_edge_path_cover(ordered_edges)
    return _DecodedPage(
        selected_edges=frozenset(
            (str(source), str(target)) for source, target in merged.selected_edges
        ),
        top_threshold_edges=top_threshold_edges,
        diagnostics={
            "top_threshold_edge_count": len(top_threshold_edges),
            "selected_edge_count": len(merged.selected_edges),
            "outgoing_conflict_rejection_count": merged.rejected_outgoing_conflict_count,
            "incoming_conflict_rejection_count": merged.rejected_incoming_conflict_count,
            "cycle_rejection_count": merged.rejected_cycle_count,
            "self_loop_rejection_count": merged.rejected_self_loop_count,
        },
    )


def _decode_page_max_regret(
    page: _Page,
    ranked: _RankedPage,
    threshold: float,
) -> _DecodedPage:
    accepted = [edge for edge in ranked.all_edges if edge.score >= threshold]
    accepted.sort(
        key=lambda edge: (
            page.base_rank[edge.source],
            edge.rank,
            -edge.score,
            page.base_rank[edge.target],
            edge.source,
            edge.target,
        )
    )
    top_threshold_edges = frozenset(
        (edge.source, edge.target)
        for edge in ranked.top_edges
        if edge.score >= threshold
    )
    merged = merge_scored_relation_edge_path_cover_max_regret(
        (edge.source, edge.target, edge.score) for edge in accepted
    )
    return _DecodedPage(
        selected_edges=frozenset(
            (str(source), str(target)) for source, target in merged.selected_edges
        ),
        top_threshold_edges=top_threshold_edges,
        diagnostics={
            "top_threshold_edge_count": len(top_threshold_edges),
            "accepted_candidate_edge_count": len(accepted),
            "selected_edge_count": len(merged.selected_edges),
            "max_regret_decision_count": merged.decision_count,
            "positive_regret_decision_count": (
                merged.positive_regret_decision_count
            ),
            "single_feasible_candidate_decision_count": (
                merged.single_feasible_candidate_decision_count
            ),
            "exhausted_source_count": merged.exhausted_source_count,
        },
    )


def _write_proposals(
    pages: list[_Page],
    ranked_pages: list[_RankedPage],
    threshold: float,
    root: Path,
    *,
    prediction_provenance: Mapping[str, Any],
    decoder_policy: str = SCORE_GREEDY_DECODER,
    feature_version: str = SUCCESSOR_GRAPH_FEATURE_VERSION,
) -> list[str]:
    if len(pages) != len(ranked_pages):
        raise ValueError("successor graph pages and rankings must align")
    paths: list[str] = []
    for page, ranked in zip(pages, ranked_pages, strict=True):
        decoded = _decode_page(
            page,
            ranked,
            threshold,
            decoder_policy=decoder_policy,
        )
        selected = decoded.selected_edges
        edge_scores = {
            (edge.source, edge.target): edge.score for edge in ranked.all_edges
        }
        serialized_candidates = [
            edge
            for edge in ranked.all_edges
            if edge.rank <= PROPOSAL_ALTERNATIVES_PER_SOURCE
            or (edge.source, edge.target) in selected
        ]
        proposal = {
            "schema": SUCCESSOR_GRAPH_PROPOSAL_SCHEMA,
            "id": page.sample["id"],
            "partition": page.split,
            "feature_version": feature_version,
            "threshold": round(threshold, 8),
            "decoder_policy": decoder_policy,
            "runtime_reorder": False,
            "prediction_provenance": proposal_provenance_for_input(
                prediction_provenance,
                input_sha256=page.sample.get("input_sha256"),
            ),
            "decoder_diagnostics": decoded.diagnostics,
            "candidate_edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "score": round(edge.score, 8),
                    "rank": edge.rank,
                    "top_score_margin": round(edge.top_score_margin, 8),
                    "selected": (edge.source, edge.target) in selected,
                }
                for edge in serialized_candidates
            ],
            "successor_edges": [
                {
                    "source": source,
                    "target": target,
                    "kind": "successor",
                    "confidence": round(edge_scores[(source, target)], 8),
                    "review_required": True,
                    "relation_policy": "review-only",
                    "origin": "fine-line-directed-successor-graph",
                }
                for source, target in sorted(
                    selected,
                    key=lambda edge: (
                        page.base_rank[edge[0]],
                        page.base_rank[edge[1]],
                        edge,
                    ),
                )
            ],
            "reading_streams": [
                {
                    "id": f"successor-graph-{index + 1:04d}",
                    "type": "body",
                    "members": members,
                    "proposal": {
                        "origin": "fine-line-directed-successor-graph",
                        "review_required": True,
                    },
                }
                for index, members in enumerate(_edge_chains(page, selected))
            ],
        }
        path = _proposal_path(root, str(page.sample["id"]))
        _write_json(path, proposal)
        paths.append(str(path))
    return paths


def _proposal_artifacts(paths: list[str]) -> list[dict[str, str]]:
    return [
        {"path": path, "sha256": _file_sha256(Path(path))}
        for path in paths
    ]


def _edge_chains(page: _Page, edges: frozenset[tuple[str, str]]) -> list[list[str]]:
    successor = {source: target for source, target in edges}
    predecessor = {target: source for source, target in edges}
    starts = sorted(
        (element_id for element_id in page.element_ids if element_id not in predecessor),
        key=lambda element_id: (page.base_rank[element_id], element_id),
    )
    chains: list[list[str]] = []
    seen: set[str] = set()
    for start in starts:
        if start in seen:
            continue
        chain: list[str] = []
        current = start
        while current not in seen:
            chain.append(current)
            seen.add(current)
            if current not in successor:
                break
            current = successor[current]
        chains.append(chain)
    for element_id in page.element_ids:
        if element_id not in seen:
            chains.append([element_id])
    return chains


def _load_labels(page: _Page) -> _Labels:
    path = _confined_path(page.corpus, page.sample.get("labels"), label="sample labels")
    _verify_hash(path, page.sample.get("labels_sha256"), label="sample labels")
    payload = _json_object(path, label="successor graph labels")
    if payload.get("schema") != PROVIDER_HIERARCHY_LABEL_SCHEMA:
        raise ValueError("successor graph labels have an unsupported schema")
    raw_edges = payload.get("successor_edges")
    if not isinstance(raw_edges, list):
        raise ValueError("successor graph labels require successor_edges")
    edges: set[tuple[str, str]] = set()
    scopes: dict[tuple[str, str], str] = {}
    outgoing: set[str] = set()
    incoming: set[str] = set()
    element_ids = set(page.element_ids)
    for item in raw_edges:
        if not isinstance(item, Mapping):
            raise ValueError("successor graph label edges must be objects")
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        edge = (source, target)
        if not source or not target or source == target:
            raise ValueError("successor graph label edges require distinct endpoints")
        if source not in element_ids or target not in element_ids:
            raise ValueError("successor graph label edge endpoint is unknown")
        if edge in edges:
            raise ValueError("successor graph label edges must be unique")
        if source in outgoing or target in incoming:
            raise ValueError("successor graph labels must satisfy degree one")
        edges.add(edge)
        outgoing.add(source)
        incoming.add(target)
        scopes[edge] = str(item.get("oracle_scope") or "")
    merged = merge_relation_edge_path_cover(sorted(edges))
    if len(merged.selected_edges) != len(edges):
        raise ValueError("successor graph labels must be acyclic")
    return _Labels(frozenset(edges), scopes)


def _accumulate_partial_counts(
    counts: Counter[str],
    predicted: frozenset[tuple[str, str]],
    truth: frozenset[tuple[str, str]],
) -> None:
    endpoints = {endpoint for edge in truth for endpoint in edge}
    scorable = {
        edge for edge in predicted if edge[0] in endpoints and edge[1] in endpoints
    }
    counts["correct"] += len(predicted & truth)
    counts["predicted"] += len(predicted)
    counts["scorable"] += len(scorable)
    counts["unscored"] += len(predicted - scorable)
    counts["labels"] += len(truth)


def _partial_relation_summary(counts: Counter[str]) -> dict[str, int | float]:
    correct = int(counts["correct"])
    predicted = int(counts["predicted"])
    scorable = int(counts["scorable"])
    unscored = int(counts["unscored"])
    labels = int(counts["labels"])
    precision = _ratio(correct, scorable)
    recall = _ratio(correct, labels)
    f1 = _ratio(2 * precision * recall, precision + recall)
    return {
        "correct": correct,
        "predicted": predicted,
        "labels": labels,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
        "scorable": scorable,
        "unscored": unscored,
        "scorable_fraction": round(_ratio(scorable, predicted), 8),
    }


def _recall_summary(counts: Counter[str]) -> dict[str, int | float]:
    correct = int(counts["correct"])
    labels = int(counts["labels"])
    return {
        "correct": correct,
        "labels": labels,
        "recall": round(_ratio(correct, labels), 8),
    }


def _require_disjoint_documents(*groups: list[_Page]) -> None:
    seen: set[str] = set()
    for pages in groups:
        documents = {str(page.sample["document_id"]) for page in pages}
        if seen & documents:
            raise ValueError("successor graph partitions must be document-disjoint")
        seen.update(documents)


def _require_unique_sample_ids(*groups: list[_Page]) -> None:
    seen: set[str] = set()
    for pages in groups:
        for page in pages:
            sample_id = str(page.sample["id"])
            if sample_id in seen:
                raise ValueError("successor graph sample ids must be globally unique")
            seen.add(sample_id)


def _element_sort_key(element: Mapping[str, Any]) -> tuple[float, float, float, float, str]:
    box = element["_bbox"]
    return (box.y0, box.x0, box.y1, box.x1, str(element["id"]))


def _training_modules() -> tuple[Any, Any, Any, str]:
    try:
        import numpy
        import sklearn
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.model_selection import GroupKFold
    except ImportError as exc:
        raise RuntimeError(
            "successor graph benchmark requires requirements-relation-ranker.txt"
        ) from exc
    return HistGradientBoostingClassifier, GroupKFold, numpy, sklearn.__version__


def _confined_path(root: Path, raw_path: Any, *, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{label} path is required")
    relative = Path(raw_path)
    if relative.is_absolute():
        raise ValueError(f"{label} path must be relative")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes its corpus") from exc
    if not path.is_file():
        raise ValueError(f"{label} must be a file inside its corpus")
    return path


def _verify_hash(path: Path, expected: Any, *, label: str) -> None:
    if not isinstance(expected, str) or _file_sha256(path) != expected:
        raise ValueError(f"{label} SHA-256 mismatch")


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return safe or "sample"


def _proposal_path(root: Path, sample_id: str) -> Path:
    digest = hashlib.sha256(sample_id.encode("utf-8")).hexdigest()[:12]
    return root / f"{_safe_filename(sample_id)}--{digest}.successor-graph.json"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0
