from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .graph_model import load_graph_model, save_graph_model
from .graph_provenance import (
    DOCUMENT_OOF_MODE,
    FROZEN_FIT_MODEL_MODE,
    benchmark_prediction_provenance,
    proposal_provenance_for_input,
)
from .relation_order import merge_relation_edge_path_cover
from .successor_graph_benchmark import (
    DEFAULT_NEAREST_CANDIDATES,
    SUCCESSOR_GRAPH_BENCHMARK_SCHEMA,
    SUCCESSOR_GRAPH_FEATURE_NAMES,
    SUCCESSOR_GRAPH_FEATURE_VERSION,
    SUCCESSOR_GRAPH_MODEL_SCHEMA,
    SUCCESSOR_GRAPH_PROPOSAL_SCHEMA,
    SUCCESSOR_GRAPH_TOPOLOGY_V3_FEATURE_VERSION,
    _Candidate,
    _Labels,
    _Page,
    _RankedPage,
    _accumulate_partial_counts,
    _edge_chains,
    _file_sha256,
    _json_object,
    _load_answer_free_pages,
    _load_labels,
    _partial_relation_summary,
    _predict_pages,
    _rank_pages,
    _recall_summary,
    _relation_summary_delta,
    _require_disjoint_documents,
    _require_unique_sample_ids,
    _resolve_report_path,
    _training_matrix,
    _training_modules,
    _write_json,
)


SUCCESSOR_TRANSITION_AB_SCHEMA = "scriptorium-successor-transition-ab/v1"
SUCCESSOR_TRANSITION_PROPOSAL_SCHEMA = "scriptorium-successor-transition-proposal/v1"
SUCCESSOR_TRANSITION_MODEL_SCHEMA = "scriptorium-successor-transition-model/v1"
SUCCESSOR_TRANSITION_FEATURE_VERSION = "protected-local-flow-transition-text-v1"
SUCCESSOR_TRANSITION_DECODER = "protected-local-flow-transition-path-cover-v1"
TRANSITION_HEAD = "directed-successor-transition"
PROPOSAL_ALTERNATIVES_PER_SOURCE = 3

_SAME_COLUMN_INDEX = SUCCESSOR_GRAPH_FEATURE_NAMES.index("same_column")
_SAME_FLOW_SEGMENT_INDEX = SUCCESSOR_GRAPH_FEATURE_NAMES.index("same_flow_segment")


@dataclass(frozen=True)
class SuccessorTransitionBenchmarkResult:
    report_path: Path
    proposals_dir: Path
    report: dict[str, Any]
    model_path: Path | None = None
    model_manifest_path: Path | None = None


@dataclass(frozen=True)
class _BaselinePrediction:
    proposal_path: Path
    proposal_sha256: str
    selected_edges: frozenset[tuple[str, str]]
    edge_scores: dict[tuple[str, str], float]
    protected_local_edges: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class _TransitionDecoded:
    selected_edges: frozenset[tuple[str, str]]
    protected_local_edges: frozenset[tuple[str, str]]
    transition_edges: frozenset[tuple[str, str]]
    diagnostics: dict[str, int]


def benchmark_successor_transition_ab(
    baseline_report_path: str | Path,
    *,
    output: str | Path,
    proposals_dir: str | Path | None = None,
    model_output: str | Path | None = None,
    minimum_transition_precision: float = 0.9,
    minimum_selected_transition_edges: int = 100,
    random_seed: int = 211,
) -> SuccessorTransitionBenchmarkResult:
    """Train a transition head while protecting answer-free local-flow edges."""

    if not 0.5 <= minimum_transition_precision <= 1.0:
        raise ValueError("minimum_transition_precision must be between 0.5 and 1.0")
    if minimum_selected_transition_edges < 1:
        raise ValueError("minimum_selected_transition_edges must be at least 1")

    baseline_path = Path(baseline_report_path).resolve()
    baseline_report = _json_object(
        baseline_path,
        label="successor transition baseline report",
    )
    baseline_feature_version = _validate_baseline_report(
        baseline_path,
        baseline_report,
    )
    baseline_sha256 = _file_sha256(baseline_path)
    nearest_candidates = int(
        baseline_report.get("nearest_candidates") or DEFAULT_NEAREST_CANDIDATES
    )
    cross_validation_folds = int(baseline_report.get("cross_validation_folds") or 0)
    if cross_validation_folds < 2:
        raise ValueError("successor transition baseline requires document OOF folds")

    train_manifest_path = _resolve_report_path(
        baseline_path,
        baseline_report.get("train_corpus_manifest"),
    )
    train_manifest = _json_object(
        train_manifest_path,
        label="successor transition train manifest",
    )
    train_manifest_sha256 = _file_sha256(train_manifest_path)
    if train_manifest_sha256 != baseline_report.get("train_corpus_manifest_sha256"):
        raise ValueError("successor transition train corpus hash differs from baseline")

    fit_baseline_pages = _load_answer_free_pages(
        train_manifest_path.parent,
        train_manifest,
        split="fit",
        nearest_candidates=nearest_candidates,
        feature_version=baseline_feature_version,
    )
    calibration_baseline_pages = _load_answer_free_pages(
        train_manifest_path.parent,
        train_manifest,
        split="calibration",
        nearest_candidates=nearest_candidates,
        feature_version=baseline_feature_version,
    )
    fit_pages = _load_answer_free_pages(
        train_manifest_path.parent,
        train_manifest,
        split="fit",
        nearest_candidates=nearest_candidates,
        feature_version=SUCCESSOR_GRAPH_FEATURE_VERSION,
    )
    calibration_pages = _load_answer_free_pages(
        train_manifest_path.parent,
        train_manifest,
        split="calibration",
        nearest_candidates=nearest_candidates,
        feature_version=SUCCESSOR_GRAPH_FEATURE_VERSION,
    )
    if not fit_pages or not calibration_pages:
        raise ValueError("successor transition baseline requires fit and calibration pages")

    test_manifest_path: Path | None = None
    test_baseline_pages: list[_Page] = []
    test_pages: list[_Page] = []
    raw_test_manifest_path = baseline_report.get("test_corpus_manifest")
    if raw_test_manifest_path:
        test_manifest_path = _resolve_report_path(
            baseline_path,
            raw_test_manifest_path,
        )
        test_manifest = _json_object(
            test_manifest_path,
            label="successor transition test manifest",
        )
        if _file_sha256(test_manifest_path) != baseline_report.get(
            "test_corpus_manifest_sha256"
        ):
            raise ValueError("successor transition test corpus hash differs from baseline")
        test_baseline_pages = _load_answer_free_pages(
            test_manifest_path.parent,
            test_manifest,
            split="test",
            nearest_candidates=nearest_candidates,
            accept_all_partitions=True,
            feature_version=baseline_feature_version,
        )
        test_pages = _load_answer_free_pages(
            test_manifest_path.parent,
            test_manifest,
            split="test",
            nearest_candidates=nearest_candidates,
            accept_all_partitions=True,
            feature_version=SUCCESSOR_GRAPH_FEATURE_VERSION,
        )

    _validate_parallel_pages(fit_baseline_pages, fit_pages)
    _validate_parallel_pages(calibration_baseline_pages, calibration_pages)
    _validate_parallel_pages(test_baseline_pages, test_pages)
    _require_disjoint_documents(fit_pages, calibration_pages, test_pages)
    _require_unique_sample_ids(fit_pages, calibration_pages, test_pages)

    fit_baselines = _load_baseline_predictions(
        baseline_path,
        baseline_report,
        split="fit_oof",
        pages=fit_pages,
    )
    calibration_baselines = _load_baseline_predictions(
        baseline_path,
        baseline_report,
        split="calibration",
        pages=calibration_pages,
    )
    test_baselines = _load_baseline_predictions(
        baseline_path,
        baseline_report,
        split="test",
        pages=test_pages,
    )

    transition_fit_pages = [_transition_page(page) for page in fit_pages]
    transition_calibration_pages = [
        _transition_page(page) for page in calibration_pages
    ]
    transition_test_pages = [_transition_page(page) for page in test_pages]

    # Candidate graphs and OOF baseline proposals are durable before fit labels open.
    fit_labels = [_load_labels(page) for page in fit_pages]
    x_fit, y_fit, groups = _training_matrix(transition_fit_pages, fit_labels)
    if not len(y_fit) or len(set(int(value) for value in y_fit)) != 2:
        raise ValueError("successor transition fit candidates require both classes")
    if len(set(groups)) < cross_validation_folds:
        raise ValueError("successor transition fit has fewer documents than OOF folds")

    estimator_parameters = {
        "max_iter": 180,
        "max_leaf_nodes": 15,
        "learning_rate": 0.05,
        "l2_regularization": 4.0,
        "min_samples_leaf": 16,
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

    fit_ranked = _rank_pages(transition_fit_pages, oof_scores)
    threshold, operating_point = _freeze_transition_threshold(
        fit_pages,
        fit_labels,
        fit_ranked,
        fit_baselines,
        oof_scores,
        minimum_transition_precision=minimum_transition_precision,
        minimum_selected_transition_edges=minimum_selected_transition_edges,
        numpy=numpy,
    )

    feature_count = int(x_fit.shape[1])
    if feature_count != len(SUCCESSOR_GRAPH_FEATURE_NAMES):
        raise ValueError("successor transition feature count does not match v4")
    fit_candidate_count = int(x_fit.shape[0])
    fit_positive_candidate_count = int(y_fit.sum())
    estimator = estimator_class(**estimator_parameters).fit(x_fit, y_fit)
    del x_fit
    del y_fit

    calibration_scores = _predict_pages(
        estimator,
        transition_calibration_pages,
        numpy=numpy,
    )
    test_scores = _predict_pages(
        estimator,
        transition_test_pages,
        numpy=numpy,
    )
    calibration_ranked = _rank_pages(
        transition_calibration_pages,
        calibration_scores,
    )
    test_ranked = _rank_pages(transition_test_pages, test_scores)

    report_path = Path(output)
    proposal_root = (
        Path(proposals_dir)
        if proposals_dir is not None
        else report_path.parent / f"{report_path.stem}.proposals"
    )
    proposal_root.mkdir(parents=True, exist_ok=True)
    test_manifest_sha256 = (
        _file_sha256(test_manifest_path) if test_manifest_path is not None else None
    )
    fit_proposals = _write_transition_proposals(
        fit_pages,
        fit_ranked,
        fit_baselines,
        threshold,
        proposal_root,
        prediction_provenance={
            **benchmark_prediction_provenance(
                producer_schema=SUCCESSOR_TRANSITION_AB_SCHEMA,
                head=TRANSITION_HEAD,
                feature_version=SUCCESSOR_TRANSITION_FEATURE_VERSION,
                prediction_mode=DOCUMENT_OOF_MODE,
                train_corpus_manifest_sha256=train_manifest_sha256,
                source_corpus_manifest_sha256=train_manifest_sha256,
                cross_validation_folds=cross_validation_folds,
            ),
            "baseline_report_sha256": baseline_sha256,
        },
    )
    calibration_proposals = _write_transition_proposals(
        calibration_pages,
        calibration_ranked,
        calibration_baselines,
        threshold,
        proposal_root,
        prediction_provenance={
            **benchmark_prediction_provenance(
                producer_schema=SUCCESSOR_TRANSITION_AB_SCHEMA,
                head=TRANSITION_HEAD,
                feature_version=SUCCESSOR_TRANSITION_FEATURE_VERSION,
                prediction_mode=FROZEN_FIT_MODEL_MODE,
                train_corpus_manifest_sha256=train_manifest_sha256,
                source_corpus_manifest_sha256=train_manifest_sha256,
                cross_validation_folds=cross_validation_folds,
            ),
            "baseline_report_sha256": baseline_sha256,
        },
    )
    test_proposals = _write_transition_proposals(
        test_pages,
        test_ranked,
        test_baselines,
        threshold,
        proposal_root,
        prediction_provenance={
            **benchmark_prediction_provenance(
                producer_schema=SUCCESSOR_TRANSITION_AB_SCHEMA,
                head=TRANSITION_HEAD,
                feature_version=SUCCESSOR_TRANSITION_FEATURE_VERSION,
                prediction_mode=FROZEN_FIT_MODEL_MODE,
                train_corpus_manifest_sha256=train_manifest_sha256,
                source_corpus_manifest_sha256=(
                    test_manifest_sha256 or train_manifest_sha256
                ),
                cross_validation_folds=cross_validation_folds,
            ),
            "baseline_report_sha256": baseline_sha256,
        },
    )

    model_path: Path | None = None
    model_manifest_path: Path | None = None
    model_manifest: dict[str, Any] | None = None
    if model_output is not None:
        artifact = save_graph_model(
            model_path=model_output,
            schema=SUCCESSOR_TRANSITION_MODEL_SCHEMA,
            head=TRANSITION_HEAD,
            feature_version=SUCCESSOR_TRANSITION_FEATURE_VERSION,
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
            minimum_edge_precision=minimum_transition_precision,
            minimum_selected_edges=minimum_selected_transition_edges,
            random_seed=random_seed,
            scikit_learn_version=sklearn_version,
            extra_manifest={
                "feature_names": list(SUCCESSOR_GRAPH_FEATURE_NAMES),
                "candidate_feature_version": SUCCESSOR_GRAPH_FEATURE_VERSION,
                "decoder_policy": SUCCESSOR_TRANSITION_DECODER,
                "protected_edge_policy": "same inferred flow segment and column",
                "baseline_report_sha256": baseline_sha256,
                "baseline_feature_version": baseline_feature_version,
                "fit_operating_point": operating_point,
                "promotion_decision": "benchmark-only-successor-transition-head",
            },
        )
        model_path = artifact.model_path
        model_manifest_path = artifact.manifest_path
        model_manifest = artifact.manifest

    # Evaluation labels open only after all calibration/test proposals are durable.
    calibration_labels = [_load_labels(page) for page in calibration_pages]
    test_labels = [_load_labels(page) for page in test_pages]
    split_records: dict[
        str,
        tuple[
            list[_Page],
            list[_Page],
            list[_Labels],
            list[_RankedPage],
            list[_BaselinePrediction],
        ],
    ] = {
        "fit_oof": (
            fit_baseline_pages,
            fit_pages,
            fit_labels,
            fit_ranked,
            fit_baselines,
        ),
        "calibration": (
            calibration_baseline_pages,
            calibration_pages,
            calibration_labels,
            calibration_ranked,
            calibration_baselines,
        ),
    }
    if test_pages:
        split_records["test"] = (
            test_baseline_pages,
            test_pages,
            test_labels,
            test_ranked,
            test_baselines,
        )

    summaries: dict[str, dict[str, Any]] = {}
    summaries_by_layout: dict[str, dict[str, Any]] = {}
    expansion_feasibility: dict[str, dict[str, int | float]] = {}
    deltas: dict[str, dict[str, float | int]] = {}
    for split, (
        baseline_pages,
        pages,
        labels,
        ranked,
        baselines,
    ) in split_records.items():
        summary = _score_transition_pages(
            pages,
            labels,
            ranked,
            baselines,
            threshold,
        )
        expected = baseline_report.get("summary", {}).get(split, {}).get(
            "selected_relation"
        )
        if not isinstance(expected, Mapping) or summary["baseline_relation"] != dict(
            expected
        ):
            raise ValueError(
                f"successor transition {split} baseline replay does not match report"
            )
        summaries[split] = summary
        summaries_by_layout[split] = _score_transition_pages_by_layout(
            pages,
            labels,
            ranked,
            baselines,
            threshold,
        )
        expansion_feasibility[split] = _score_expansion_feasibility(
            baseline_pages,
            pages,
            labels,
            baselines,
        )
        deltas[split] = _relation_summary_delta(
            summary["baseline_relation"],
            summary["selected_relation"],
        )

    calibration_gate = _calibration_gate(
        summaries["calibration"],
        minimum_transition_precision=minimum_transition_precision,
    )
    report = {
        "schema": SUCCESSOR_TRANSITION_AB_SCHEMA,
        "feature_version": SUCCESSOR_TRANSITION_FEATURE_VERSION,
        "candidate_feature_version": SUCCESSOR_GRAPH_FEATURE_VERSION,
        "baseline_feature_version": baseline_feature_version,
        "baseline_report": str(baseline_path),
        "baseline_report_sha256": baseline_sha256,
        "train_corpus_manifest": str(train_manifest_path),
        "train_corpus_manifest_sha256": train_manifest_sha256,
        "test_corpus_manifest": (
            str(test_manifest_path) if test_manifest_path is not None else None
        ),
        "test_corpus_manifest_sha256": test_manifest_sha256,
        "nearest_candidates": nearest_candidates,
        "candidate_policy": (
            "v4 sparse topology candidates outside one inferred local flow/column"
        ),
        "protected_edge_policy": (
            "baseline selected edge protected only when source and target share "
            "the inferred flow segment and column"
        ),
        "decoder_policy": SUCCESSOR_TRANSITION_DECODER,
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
        "cross_validation_folds": cross_validation_folds,
        "cross_validation_unit": "document",
        "minimum_transition_precision": minimum_transition_precision,
        "minimum_selected_transition_edges": minimum_selected_transition_edges,
        "frozen_transition_threshold": round(threshold, 8),
        "threshold_source": "fit-document-OOF transition candidates only",
        "fit_operating_point": operating_point,
        "estimator": {
            "type": "HistGradientBoostingClassifier",
            "parameters": estimator_parameters,
            "scikit_learn_version": sklearn_version,
        },
        "answer_separation": {
            "all_candidate_graphs_loaded_before_fit_labels": True,
            "baseline_fit_predictions_are_document_oof": True,
            "fit_labels_role": "transition-head training and threshold selection",
            "evaluation_predictions_written_before_evaluation_labels": True,
            "candidate_generation_uses_labels": False,
            "paragraph_membership_labels_used_as_features": False,
        },
        "runtime_reorder": False,
        "promotion_decision": "benchmark-only-successor-transition-head",
        "calibration_gate": calibration_gate,
        "summary": summaries,
        "summary_by_layout_stratum": summaries_by_layout,
        "delta": deltas,
        "expansion_feasibility": expansion_feasibility,
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
    return SuccessorTransitionBenchmarkResult(
        report_path,
        proposal_root,
        report,
        model_path=model_path,
        model_manifest_path=model_manifest_path,
    )


def _validate_baseline_report(
    report_path: Path,
    report: Mapping[str, Any],
) -> str:
    if report.get("schema") != SUCCESSOR_GRAPH_BENCHMARK_SCHEMA:
        raise ValueError("successor transition A/B requires a successor graph report")
    feature_version = str(report.get("feature_version") or "")
    if feature_version not in {
        SUCCESSOR_GRAPH_TOPOLOGY_V3_FEATURE_VERSION,
        SUCCESSOR_GRAPH_FEATURE_VERSION,
    }:
        raise ValueError("successor transition baseline feature version is unsupported")
    if report.get("runtime_reorder") is not False:
        raise ValueError("successor transition baseline must keep runtime_reorder=false")
    model_record = report.get("model")
    if not isinstance(model_record, Mapping) or not model_record.get("path"):
        raise ValueError("successor transition baseline requires a serialized model")
    model_path = _resolve_report_path(report_path, model_record.get("path"))
    artifact = load_graph_model(
        model_path,
        expected_schema=SUCCESSOR_GRAPH_MODEL_SCHEMA,
        expected_head="directed-successor",
        expected_feature_version=feature_version,
    )
    if model_record.get("manifest") != artifact.manifest:
        raise ValueError("successor transition model manifest differs from baseline")
    if int(artifact.bundle.get("nearest_candidates") or 0) != int(
        report.get("nearest_candidates") or -1
    ):
        raise ValueError("successor transition nearest-candidate policy differs")
    return feature_version


def _validate_parallel_pages(
    baseline_pages: list[_Page],
    current_pages: list[_Page],
) -> None:
    if len(baseline_pages) != len(current_pages):
        raise ValueError("successor transition candidate page counts differ")
    for baseline, current in zip(baseline_pages, current_pages, strict=True):
        if (
            baseline.sample["id"] != current.sample["id"]
            or baseline.element_ids != current.element_ids
            or baseline.sample.get("input_sha256")
            != current.sample.get("input_sha256")
        ):
            raise ValueError("successor transition candidate pages are not aligned")


def _load_baseline_predictions(
    report_path: Path,
    report: Mapping[str, Any],
    *,
    split: str,
    pages: list[_Page],
) -> list[_BaselinePrediction]:
    raw_paths = report.get("proposals", {}).get(split)
    if not isinstance(raw_paths, list) or len(raw_paths) != len(pages):
        raise ValueError(f"successor transition baseline {split} proposals are incomplete")
    payloads: dict[str, tuple[Path, dict[str, Any]]] = {}
    for raw_path in raw_paths:
        path = _resolve_report_path(report_path, raw_path)
        payload = _json_object(path, label="successor transition baseline proposal")
        sample_id = str(payload.get("id") or "")
        if sample_id in payloads:
            raise ValueError("successor transition baseline proposal ids must be unique")
        payloads[sample_id] = (path, payload)

    predictions: list[_BaselinePrediction] = []
    for page in pages:
        sample_id = str(page.sample["id"])
        record = payloads.get(sample_id)
        if record is None:
            raise ValueError("successor transition baseline proposal is missing a page")
        path, payload = record
        if (
            payload.get("schema") != SUCCESSOR_GRAPH_PROPOSAL_SCHEMA
            or payload.get("runtime_reorder") is not False
        ):
            raise ValueError("successor transition baseline proposal is unsupported")
        provenance = payload.get("prediction_provenance")
        if not isinstance(provenance, Mapping) or provenance.get(
            "input_sha256"
        ) != page.sample.get("input_sha256"):
            raise ValueError("successor transition baseline input provenance differs")
        raw_edges = payload.get("successor_edges")
        if not isinstance(raw_edges, list):
            raise ValueError("successor transition baseline requires successor_edges")
        edge_scores: dict[tuple[str, str], float] = {}
        for item in raw_edges:
            if not isinstance(item, Mapping):
                raise ValueError("successor transition baseline edge must be an object")
            edge = (str(item.get("source") or ""), str(item.get("target") or ""))
            if not all(edge) or edge in edge_scores:
                raise ValueError("successor transition baseline edge is invalid")
            edge_scores[edge] = float(item.get("confidence") or 0.0)
        selected = frozenset(edge_scores)
        merged = merge_relation_edge_path_cover(
            sorted(
                selected,
                key=lambda edge: (
                    page.base_rank[edge[0]],
                    page.base_rank[edge[1]],
                    edge,
                ),
            )
        )
        if frozenset(merged.selected_edges) != selected:
            raise ValueError("successor transition baseline is not a path cover")
        candidates = {
            (candidate.source, candidate.target): candidate
            for candidate in page.candidates
        }
        if not selected.issubset(candidates):
            raise ValueError("successor transition baseline edge left the v4 graph")
        protected = frozenset(
            edge for edge in selected if _is_local_candidate(candidates[edge])
        )
        predictions.append(
            _BaselinePrediction(
                proposal_path=path,
                proposal_sha256=_file_sha256(path),
                selected_edges=selected,
                edge_scores=edge_scores,
                protected_local_edges=protected,
            )
        )
    return predictions


def _is_local_candidate(candidate: _Candidate) -> bool:
    return bool(
        candidate.features[_SAME_COLUMN_INDEX]
        and candidate.features[_SAME_FLOW_SEGMENT_INDEX]
    )


def _transition_page(page: _Page) -> _Page:
    return replace(
        page,
        candidates=tuple(
            candidate
            for candidate in page.candidates
            if not _is_local_candidate(candidate)
        ),
    )


def _freeze_transition_threshold(
    pages: list[_Page],
    labels: list[_Labels],
    ranked_pages: list[_RankedPage],
    baselines: list[_BaselinePrediction],
    scores: Any,
    *,
    minimum_transition_precision: float,
    minimum_selected_transition_edges: int,
    numpy: Any,
) -> tuple[float, dict[str, Any]]:
    thresholds = numpy.unique(numpy.quantile(scores, numpy.linspace(0.0, 1.0, 501)))
    qualified: list[tuple[float, float, float, int, float, dict[str, Any]]] = []
    for raw_threshold in thresholds:
        threshold = float(raw_threshold)
        metrics = _score_transition_pages(
            pages,
            labels,
            ranked_pages,
            baselines,
            threshold,
        )
        transition = metrics["transition_relation"]
        selected = metrics["selected_relation"]
        if (
            int(transition["predicted"]) >= minimum_selected_transition_edges
            and float(transition["precision"]) >= minimum_transition_precision
        ):
            qualified.append(
                (
                    float(selected["f1"]),
                    float(transition["precision"]),
                    float(transition["recall"]),
                    int(transition["predicted"]),
                    threshold,
                    metrics,
                )
            )
    if not qualified:
        raise ValueError(
            "successor transition fit has no threshold satisfying its precision/volume gate"
        )
    _f1, _precision, _recall, _count, threshold, metrics = max(qualified)
    return threshold, {
        "selected_relation": metrics["selected_relation"],
        "baseline_relation": metrics["baseline_relation"],
        "transition_relation": metrics["transition_relation"],
        "protected_local_relation": metrics["protected_local_relation"],
        "baseline_within_region_recovery": metrics[
            "baseline_within_region_recovery"
        ],
        "baseline_cross_region_recovery": metrics[
            "baseline_cross_region_recovery"
        ],
        "within_region_recovery": metrics["within_region_recovery"],
        "cross_region_recovery": metrics["cross_region_recovery"],
        "decoder_diagnostics": metrics["decoder_diagnostics"],
    }


def _decode_transition_page(
    page: _Page,
    ranked: _RankedPage,
    baseline: _BaselinePrediction,
    threshold: float,
) -> _TransitionDecoded:
    protected = sorted(
        baseline.protected_local_edges,
        key=lambda edge: (
            page.base_rank[edge[0]],
            page.base_rank[edge[1]],
            edge,
        ),
    )
    protected_merge = merge_relation_edge_path_cover((), protected_edges=protected)
    if frozenset(protected_merge.selected_edges) != baseline.protected_local_edges:
        raise ValueError("successor transition protected local edges are not a path cover")
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
    merged = merge_relation_edge_path_cover(
        ((edge.source, edge.target) for edge in accepted),
        protected_edges=protected,
    )
    selected = frozenset(
        (str(source), str(target)) for source, target in merged.selected_edges
    )
    protected_selected = frozenset(
        (str(source), str(target)) for source, target in merged.protected_selected_edges
    )
    return _TransitionDecoded(
        selected_edges=selected,
        protected_local_edges=protected_selected,
        transition_edges=selected - protected_selected,
        diagnostics={
            "baseline_selected_edge_count": len(baseline.selected_edges),
            "baseline_transition_edge_count": len(
                baseline.selected_edges - baseline.protected_local_edges
            ),
            "protected_local_edge_count": len(protected_selected),
            "top_threshold_transition_edge_count": len(accepted),
            "selected_transition_edge_count": len(selected - protected_selected),
            "selected_edge_count": len(selected),
            "removed_baseline_transition_edge_count": len(
                (baseline.selected_edges - baseline.protected_local_edges) - selected
            ),
            "added_transition_edge_count": len(selected - baseline.selected_edges),
            "outgoing_conflict_rejection_count": (
                merged.rejected_outgoing_conflict_count
            ),
            "incoming_conflict_rejection_count": (
                merged.rejected_incoming_conflict_count
            ),
            "cycle_rejection_count": merged.rejected_cycle_count,
            "self_loop_rejection_count": merged.rejected_self_loop_count,
        },
    )


def _score_transition_pages(
    pages: list[_Page],
    labels: list[_Labels],
    ranked_pages: list[_RankedPage],
    baselines: list[_BaselinePrediction],
    threshold: float,
) -> dict[str, Any]:
    baseline_counts: Counter[str] = Counter()
    selected_counts: Counter[str] = Counter()
    protected_counts: Counter[str] = Counter()
    transition_counts: Counter[str] = Counter()
    baseline_transition_counts: Counter[str] = Counter()
    candidate_counts: Counter[str] = Counter()
    within_counts: Counter[str] = Counter()
    cross_counts: Counter[str] = Counter()
    baseline_within_counts: Counter[str] = Counter()
    baseline_cross_counts: Counter[str] = Counter()
    decoder_counts: Counter[str] = Counter()
    for page, page_labels, ranked, baseline in zip(
        pages,
        labels,
        ranked_pages,
        baselines,
        strict=True,
    ):
        decoded = _decode_transition_page(page, ranked, baseline, threshold)
        transition_candidates = {
            (candidate.source, candidate.target)
            for candidate in page.candidates
            if not _is_local_candidate(candidate)
        }
        _accumulate_partial_counts(
            baseline_counts,
            baseline.selected_edges,
            page_labels.edges,
        )
        _accumulate_partial_counts(
            selected_counts,
            decoded.selected_edges,
            page_labels.edges,
        )
        _accumulate_partial_counts(
            protected_counts,
            decoded.protected_local_edges,
            page_labels.edges,
        )
        _accumulate_partial_counts(
            transition_counts,
            decoded.transition_edges,
            page_labels.edges,
        )
        _accumulate_partial_counts(
            baseline_transition_counts,
            baseline.selected_edges - baseline.protected_local_edges,
            page_labels.edges,
        )
        candidate_counts["correct"] += len(transition_candidates & page_labels.edges)
        candidate_counts["labels"] += len(page_labels.edges)
        within_truth = {
            edge
            for edge, scope in page_labels.scopes.items()
            if scope == "within-oracle-region"
        }
        cross_truth = page_labels.edges - within_truth
        within_counts["correct"] += len(decoded.selected_edges & within_truth)
        within_counts["labels"] += len(within_truth)
        cross_counts["correct"] += len(decoded.selected_edges & cross_truth)
        cross_counts["labels"] += len(cross_truth)
        baseline_within_counts["correct"] += len(
            baseline.selected_edges & within_truth
        )
        baseline_within_counts["labels"] += len(within_truth)
        baseline_cross_counts["correct"] += len(baseline.selected_edges & cross_truth)
        baseline_cross_counts["labels"] += len(cross_truth)
        decoder_counts.update(decoded.diagnostics)
    return {
        "page_count": len(pages),
        "transition_candidate_edge_count": sum(
            1
            for page in pages
            for candidate in page.candidates
            if not _is_local_candidate(candidate)
        ),
        "transition_candidate_recall": _recall_summary(candidate_counts),
        "baseline_relation": _partial_relation_summary(baseline_counts),
        "selected_relation": _partial_relation_summary(selected_counts),
        "protected_local_relation": _partial_relation_summary(protected_counts),
        "baseline_transition_relation": _partial_relation_summary(
            baseline_transition_counts
        ),
        "transition_relation": _partial_relation_summary(transition_counts),
        "baseline_within_region_recovery": _recall_summary(
            baseline_within_counts
        ),
        "baseline_cross_region_recovery": _recall_summary(baseline_cross_counts),
        "within_region_recovery": _recall_summary(within_counts),
        "cross_region_recovery": _recall_summary(cross_counts),
        "decoder_diagnostics": dict(sorted(decoder_counts.items())),
        "runtime_reorder": False,
    }


def _score_transition_pages_by_layout(
    pages: list[_Page],
    labels: list[_Labels],
    ranked_pages: list[_RankedPage],
    baselines: list[_BaselinePrediction],
    threshold: float,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, page in enumerate(pages):
        groups[str(page.sample.get("layout_stratum") or "unspecified")].append(index)
    return {
        stratum: _score_transition_pages(
            [pages[index] for index in indices],
            [labels[index] for index in indices],
            [ranked_pages[index] for index in indices],
            [baselines[index] for index in indices],
            threshold,
        )
        for stratum, indices in sorted(groups.items())
    }


def _score_expansion_feasibility(
    baseline_pages: list[_Page],
    current_pages: list[_Page],
    labels: list[_Labels],
    baselines: list[_BaselinePrediction],
) -> dict[str, int | float]:
    counts: Counter[str] = Counter()
    for baseline_page, current_page, page_labels, baseline in zip(
        baseline_pages,
        current_pages,
        labels,
        baselines,
        strict=True,
    ):
        baseline_candidates = {
            (candidate.source, candidate.target)
            for candidate in baseline_page.candidates
        }
        expansion_candidates = {
            (candidate.source, candidate.target)
            for candidate in current_page.candidates
        } - baseline_candidates
        outgoing = {source for source, _target in baseline.selected_edges}
        incoming = {target for _source, target in baseline.selected_edges}
        unresolved = {
            edge for edge in expansion_candidates if edge[0] not in outgoing
        }
        free_target = {edge for edge in unresolved if edge[1] not in incoming}
        counts["candidate"] += len(expansion_candidates)
        counts["positive"] += len(expansion_candidates & page_labels.edges)
        counts["unresolved_source_candidate"] += len(unresolved)
        counts["unresolved_source_positive"] += len(unresolved & page_labels.edges)
        counts["degree_feasible_candidate"] += len(free_target)
        counts["degree_feasible_positive"] += len(free_target & page_labels.edges)
    return {
        "candidate_count": int(counts["candidate"]),
        "positive_count": int(counts["positive"]),
        "unresolved_source_candidate_count": int(
            counts["unresolved_source_candidate"]
        ),
        "unresolved_source_positive_count": int(
            counts["unresolved_source_positive"]
        ),
        "degree_feasible_candidate_count": int(counts["degree_feasible_candidate"]),
        "degree_feasible_positive_count": int(counts["degree_feasible_positive"]),
        "degree_feasible_positive_rate": round(
            _ratio(
                counts["degree_feasible_positive"],
                counts["degree_feasible_candidate"],
            ),
            8,
        ),
    }


def _write_transition_proposals(
    pages: list[_Page],
    ranked_pages: list[_RankedPage],
    baselines: list[_BaselinePrediction],
    threshold: float,
    root: Path,
    *,
    prediction_provenance: Mapping[str, Any],
) -> list[str]:
    paths: list[str] = []
    for page, ranked, baseline in zip(pages, ranked_pages, baselines, strict=True):
        decoded = _decode_transition_page(page, ranked, baseline, threshold)
        selected = decoded.selected_edges
        transition_selected = decoded.transition_edges
        scores = {
            (edge.source, edge.target): edge.score for edge in ranked.all_edges
        }
        serialized_candidates: list[Any] = []
        per_source: Counter[str] = Counter()
        for edge in ranked.all_edges:
            if per_source[edge.source] >= PROPOSAL_ALTERNATIVES_PER_SOURCE:
                continue
            serialized_candidates.append(edge)
            per_source[edge.source] += 1
        successor_edges = []
        for source, target in sorted(
            selected,
            key=lambda edge: (
                page.base_rank[edge[0]],
                page.base_rank[edge[1]],
                edge,
            ),
        ):
            protected = (source, target) in decoded.protected_local_edges
            successor_edges.append(
                {
                    "source": source,
                    "target": target,
                    "kind": "successor",
                    "confidence": round(
                        (
                            baseline.edge_scores[(source, target)]
                            if protected
                            else scores[(source, target)]
                        ),
                        8,
                    ),
                    "head": "protected-local-baseline" if protected else TRANSITION_HEAD,
                    "review_required": True,
                    "relation_policy": "review-only",
                    "origin": "protected-local-successor-transition",
                }
            )
        proposal = {
            "schema": SUCCESSOR_TRANSITION_PROPOSAL_SCHEMA,
            "id": str(page.sample["id"]),
            "document_id": str(page.sample["document_id"]),
            "page_index": int(page.sample.get("page_index") or 0),
            "partition": str(page.sample.get("partition") or page.split),
            "layout_stratum": str(page.sample.get("layout_stratum") or "unspecified"),
            "feature_version": SUCCESSOR_TRANSITION_FEATURE_VERSION,
            "candidate_feature_version": SUCCESSOR_GRAPH_FEATURE_VERSION,
            "threshold": round(threshold, 8),
            "decoder_policy": SUCCESSOR_TRANSITION_DECODER,
            "runtime_reorder": False,
            "baseline": {
                "proposal": str(baseline.proposal_path),
                "proposal_sha256": baseline.proposal_sha256,
                "selected_edge_count": len(baseline.selected_edges),
                "protected_local_edge_count": len(baseline.protected_local_edges),
            },
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
                    "selected": (edge.source, edge.target) in transition_selected,
                }
                for edge in serialized_candidates
            ],
            "protected_local_edges": [
                {
                    "source": source,
                    "target": target,
                    "confidence": round(baseline.edge_scores[(source, target)], 8),
                }
                for source, target in sorted(
                    decoded.protected_local_edges,
                    key=lambda edge: (
                        page.base_rank[edge[0]],
                        page.base_rank[edge[1]],
                        edge,
                    ),
                )
            ],
            "successor_edges": successor_edges,
            "reading_streams": [
                {
                    "id": f"successor-transition-{index + 1:04d}",
                    "type": "body",
                    "members": members,
                    "proposal": {
                        "origin": "protected-local-successor-transition",
                        "review_required": True,
                    },
                }
                for index, members in enumerate(_edge_chains(page, selected))
            ],
        }
        path = root / f"{_safe_filename(str(page.sample['id']))}--{_id_digest(str(page.sample['id']))}.successor-transition.json"
        _write_json(path, proposal)
        paths.append(str(path))
    return paths


def _calibration_gate(
    summary: Mapping[str, Any],
    *,
    minimum_transition_precision: float,
) -> dict[str, Any]:
    baseline = summary["baseline_relation"]
    selected = summary["selected_relation"]
    transition = summary["transition_relation"]
    baseline_cross = summary.get("baseline_cross_region_recovery")
    checks = {
        "overall_f1_not_below_baseline": (
            float(selected["f1"]) >= float(baseline["f1"])
        ),
        "transition_precision_gate": (
            float(transition["precision"]) >= minimum_transition_precision
        ),
    }
    if isinstance(baseline_cross, Mapping):
        checks["cross_region_recall_not_below_baseline"] = (
            float(summary["cross_region_recovery"]["recall"])
            >= float(baseline_cross["recall"])
        )
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "policy": "calibration is an acceptance gate and never selects the threshold",
    }


def _proposal_artifacts(paths: list[str]) -> list[dict[str, str]]:
    return [{"path": path, "sha256": _file_sha256(Path(path))} for path in paths]


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return safe or "sample"


def _id_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0
