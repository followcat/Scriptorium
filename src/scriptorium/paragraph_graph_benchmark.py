from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from .graph_model import (
    flatten_page_scores,
    predict_feature_batches,
    save_graph_model,
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


PARAGRAPH_GRAPH_BENCHMARK_SCHEMA = "scriptorium-paragraph-graph-benchmark/v1"
PARAGRAPH_GRAPH_PROPOSAL_SCHEMA = "scriptorium-paragraph-graph-proposal/v1"
PARAGRAPH_GRAPH_MODEL_SCHEMA = "scriptorium-paragraph-graph-model/v1"
PARAGRAPH_GRAPH_FEATURE_VERSION = "fine-line-local-relation-text-v1"


@dataclass(frozen=True)
class ParagraphGraphBenchmarkResult:
    report_path: Path
    proposals_dir: Path
    report: dict[str, Any]
    model_path: Path | None = None
    model_manifest_path: Path | None = None


@dataclass(frozen=True)
class _Candidate:
    source: str
    target: str
    features: tuple[float, ...]


@dataclass(frozen=True)
class _Page:
    corpus: Path
    sample: dict[str, Any]
    split: str
    element_ids: tuple[str, ...]
    candidates: tuple[_Candidate, ...]


def benchmark_paragraph_graph(
    train_corpus_dir: str | Path,
    *,
    output: str | Path,
    proposals_dir: str | Path | None = None,
    test_corpus_dir: str | Path | None = None,
    model_output: str | Path | None = None,
    cross_validation_folds: int = 5,
    minimum_edge_precision: float = 0.97,
    minimum_selected_edges: int = 100,
    random_seed: int = 43,
) -> ParagraphGraphBenchmarkResult:
    """Train and score a review-only fine-line paragraph graph."""

    if cross_validation_folds < 2:
        raise ValueError("cross_validation_folds must be at least 2")
    if not 0.5 <= minimum_edge_precision <= 1.0:
        raise ValueError("minimum_edge_precision must be between 0.5 and 1.0")
    if minimum_selected_edges < 1:
        raise ValueError("minimum_selected_edges must be at least 1")

    train_corpus = Path(train_corpus_dir).resolve()
    train_manifest_path, train_manifest = _corpus_manifest(train_corpus)
    fit_pages = _load_answer_free_pages(
        train_corpus,
        train_manifest,
        split="fit",
    )
    calibration_pages = _load_answer_free_pages(
        train_corpus,
        train_manifest,
        split="calibration",
    )
    if not fit_pages or not calibration_pages:
        raise ValueError("training corpus requires fit and calibration pages")

    test_manifest_path: Path | None = None
    test_manifest: dict[str, Any] | None = None
    test_pages: list[_Page] = []
    if test_corpus_dir is not None:
        test_corpus = Path(test_corpus_dir).resolve()
        test_manifest_path, test_manifest = _corpus_manifest(test_corpus)
        test_pages = _load_answer_free_pages(
            test_corpus,
            test_manifest,
            split="test",
            accept_all_partitions=True,
        )
        if not test_pages:
            raise ValueError("test corpus contains no pages")

    _require_disjoint_documents(fit_pages, calibration_pages, test_pages)
    _require_unique_sample_ids(fit_pages, calibration_pages, test_pages)
    fit_labels = [_load_labels(page) for page in fit_pages]
    x_fit, y_fit, groups = _training_matrix(fit_pages, fit_labels)
    unique_groups = sorted(set(groups))
    if len(unique_groups) < cross_validation_folds:
        raise ValueError("fit partition has fewer documents than cross-validation folds")

    estimator_parameters = {
        "max_iter": 160,
        "max_leaf_nodes": 15,
        "learning_rate": 0.06,
        "l2_regularization": 2.0,
        "min_samples_leaf": 24,
        "random_state": random_seed,
    }
    estimator_class, group_kfold_class, numpy, sklearn_version = _training_modules()
    oof_scores = numpy.zeros(len(y_fit), dtype=float)
    folds = group_kfold_class(n_splits=cross_validation_folds)
    for train_indices, validation_indices in folds.split(x_fit, y_fit, groups):
        fold_estimator = estimator_class(**estimator_parameters).fit(
            x_fit[train_indices],
            y_fit[train_indices],
        )
        oof_scores[validation_indices] = fold_estimator.predict_proba(
            x_fit[validation_indices]
        )[:, 1]

    threshold, operating_point = _freeze_threshold(
        fit_pages,
        fit_labels,
        oof_scores,
        minimum_edge_precision=minimum_edge_precision,
        minimum_selected_edges=minimum_selected_edges,
        numpy=numpy,
    )
    feature_count = int(x_fit.shape[1])
    fit_candidate_count = int(x_fit.shape[0])
    fit_positive_count = int(y_fit.sum())
    estimator = estimator_class(**estimator_parameters).fit(x_fit, y_fit)
    # Free the dense fit matrix before page-wise evaluation scoring.
    del x_fit
    del y_fit
    calibration_scores = _predict_pages(estimator, calibration_pages, numpy=numpy)
    test_scores = _predict_pages(estimator, test_pages, numpy=numpy)

    report_path = Path(output)
    proposal_root = (
        Path(proposals_dir)
        if proposals_dir is not None
        else report_path.parent / f"{report_path.stem}.proposals"
    )
    proposal_root.mkdir(parents=True, exist_ok=True)
    fit_proposals = _write_proposals(fit_pages, oof_scores, threshold, proposal_root)
    calibration_proposals = _write_proposals(
        calibration_pages,
        calibration_scores,
        threshold,
        proposal_root,
    )
    test_proposals = _write_proposals(test_pages, test_scores, threshold, proposal_root)

    model_path: Path | None = None
    model_manifest_path: Path | None = None
    model_manifest: dict[str, Any] | None = None
    if model_output is not None:
        artifact = save_graph_model(
            model_path=model_output,
            schema=PARAGRAPH_GRAPH_MODEL_SCHEMA,
            head="paragraph-comembership",
            feature_version=PARAGRAPH_GRAPH_FEATURE_VERSION,
            threshold=threshold,
            estimator=estimator,
            estimator_parameters=estimator_parameters,
            feature_count=feature_count,
            train_corpus_manifest_sha256=_file_sha256(train_manifest_path),
            fit_document_count=len({page.sample["document_id"] for page in fit_pages}),
            fit_page_count=len(fit_pages),
            fit_candidate_count=fit_candidate_count,
            fit_positive_count=fit_positive_count,
            cross_validation_folds=cross_validation_folds,
            minimum_edge_precision=minimum_edge_precision,
            minimum_selected_edges=minimum_selected_edges,
            random_seed=random_seed,
            scikit_learn_version=sklearn_version,
            extra_manifest={
                "component_policy": "thresholded undirected edges with union-find",
                "promotion_decision": "benchmark-only-line-paragraph-graph",
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
        "fit_oof": _score_pages(fit_pages, fit_labels, oof_scores, threshold),
        "calibration": _score_pages(
            calibration_pages,
            calibration_labels,
            calibration_scores,
            threshold,
        ),
    }
    if test_pages:
        summaries["test"] = _score_pages(
            test_pages,
            test_labels,
            test_scores,
            threshold,
        )
    summaries_by_layout_stratum = {
        "fit_oof": _score_pages_by_layout_stratum(
            fit_pages,
            fit_labels,
            oof_scores,
            threshold,
        ),
        "calibration": _score_pages_by_layout_stratum(
            calibration_pages,
            calibration_labels,
            calibration_scores,
            threshold,
        ),
    }
    if test_pages:
        summaries_by_layout_stratum["test"] = _score_pages_by_layout_stratum(
            test_pages,
            test_labels,
            test_scores,
            threshold,
        )

    report = {
        "schema": PARAGRAPH_GRAPH_BENCHMARK_SCHEMA,
        "feature_version": PARAGRAPH_GRAPH_FEATURE_VERSION,
        "train_corpus_manifest": str(train_manifest_path),
        "train_corpus_manifest_sha256": _file_sha256(train_manifest_path),
        "test_corpus_manifest": (
            str(test_manifest_path) if test_manifest_path is not None else None
        ),
        "test_corpus_manifest_sha256": (
            _file_sha256(test_manifest_path)
            if test_manifest_path is not None
            else None
        ),
        "candidate_policy": (
            "selected adjacency plus sparse relation candidates plus three local "
            "forward geometry neighbors"
        ),
        "component_policy": "thresholded undirected edges with union-find",
        "label_policy": "complete oracle co-membership",
        "fit_document_count": len({page.sample["document_id"] for page in fit_pages}),
        "calibration_document_count": len(
            {page.sample["document_id"] for page in calibration_pages}
        ),
        "test_document_count": len({page.sample["document_id"] for page in test_pages}),
        "fit_page_count": len(fit_pages),
        "calibration_page_count": len(calibration_pages),
        "test_page_count": len(test_pages),
        "feature_count": feature_count,
        "fit_candidate_count": fit_candidate_count,
        "fit_positive_count": fit_positive_count,
        "prediction_policy": "page-wise feature batches",
        "cross_validation_folds": cross_validation_folds,
        "cross_validation_unit": "document",
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
        },
        "runtime_reorder": False,
        "promotion_decision": "benchmark-only-line-paragraph-graph",
        "summary": summaries,
        "summary_by_layout_stratum": summaries_by_layout_stratum,
        "proposals": {
            "fit_oof": fit_proposals,
            "calibration": calibration_proposals,
            "test": test_proposals,
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
    return ParagraphGraphBenchmarkResult(
        report_path,
        proposal_root,
        report,
        model_path=model_path,
        model_manifest_path=model_manifest_path,
    )


def _corpus_manifest(corpus: Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = corpus / "provider_hierarchy_corpus_manifest.json"
    manifest = _json_object(manifest_path, label="provider hierarchy manifest")
    if manifest.get("schema") != PROVIDER_HIERARCHY_CORPUS_SCHEMA:
        raise ValueError("unsupported provider hierarchy corpus schema")
    if manifest.get("inference_inputs_are_answer_free") is not True:
        raise ValueError("paragraph graph corpus must declare answer-free inputs")
    if not isinstance(manifest.get("samples"), list):
        raise ValueError("provider hierarchy corpus has no samples")
    return manifest_path, manifest


def _load_answer_free_pages(
    corpus: Path,
    manifest: Mapping[str, Any],
    *,
    split: str,
    accept_all_partitions: bool = False,
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
            raise ValueError("paragraph graph samples require id and document_id")
        sample["id"] = sample_id
        sample["document_id"] = document_id
        input_path = _confined_path(corpus, sample.get("input"), label="sample input")
        _verify_hash(input_path, sample.get("input_sha256"), label="sample input")
        payload = _json_object(input_path, label="paragraph graph input")
        if payload.get("schema") != HIERARCHY_INPUT_SCHEMA:
            raise ValueError("paragraph graph input has an unsupported schema")
        element_ids, candidates = _page_candidates(payload)
        pages.append(
            _Page(
                corpus=corpus,
                sample=sample,
                split=split,
                element_ids=element_ids,
                candidates=candidates,
            )
        )
    return pages


def _page_candidates(payload: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[_Candidate, ...]]:
    width = float(payload.get("width") or 0.0)
    height = float(payload.get("height") or 0.0)
    raw_elements = payload.get("elements")
    if width <= 0 or height <= 0 or not isinstance(raw_elements, list):
        raise ValueError("paragraph graph input requires page geometry and elements")
    elements: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_element in raw_elements:
        if not isinstance(raw_element, Mapping):
            raise ValueError("paragraph graph elements must be objects")
        element = dict(raw_element)
        element_id = str(element.get("id") or "").strip()
        if not element_id or element_id in seen_ids:
            raise ValueError("paragraph graph element ids must be unique")
        seen_ids.add(element_id)
        element["_bbox"] = BBox.from_any(element.get("box"))
        elements.append(element)
    # Upstream extractors are free to serialize equivalent elements in different
    # orders. Canonicalization prevents graph tie-breaks from learning that order.
    elements.sort(key=_element_sort_key)

    assignments = infer_semantic_reading_order(
        [element["_bbox"] for element in elements],
        page_width=width,
        page_height=height,
        texts=[""] * len(elements),
    )
    base_indices = [
        assignment.item_index
        for assignment in sorted(assignments, key=lambda item: item.semantic_order)
    ]
    if len(base_indices) != len(elements) or len(set(base_indices)) != len(elements):
        raise ValueError("selected order did not return a complete line permutation")
    base_ids = [str(elements[index]["id"]) for index in base_indices]
    base_rank = {element_id: rank for rank, element_id in enumerate(base_ids)}
    by_id = {str(element["id"]): element for element in elements}

    relation = infer_relation_graph_order_evidence(
        [element["_bbox"] for element in elements],
        page_width=width,
        page_height=height,
    )
    relation_scores: dict[tuple[str, str], float] = {}
    for edge in relation.candidate_edges:
        source = str(elements[edge.source]["id"])
        target = str(elements[edge.target]["id"])
        relation_scores[(source, target)] = max(
            relation_scores.get((source, target), 0.0),
            float(edge.score),
        )
    selected_relation = {
        (str(elements[edge.source]["id"]), str(elements[edge.target]["id"]))
        for edge in relation.selected_edge_diagnostics
    }

    candidate_pairs = {
        (source, target) for source, target in zip(base_ids, base_ids[1:])
    }
    candidate_pairs.update(relation_scores)
    median_height = _median(
        [float(element["_bbox"].height) for element in elements]
    )
    for source in base_ids:
        source_box = by_id[source]["_bbox"]
        local_targets: list[tuple[float, float, int, str]] = []
        for target in base_ids:
            if source == target:
                continue
            target_box = by_id[target]["_bbox"]
            gap = target_box.y0 - source_box.y1
            overlap = _horizontal_overlap(source_box, target_box)
            if -0.25 * median_height <= gap <= 2.0 * median_height and overlap >= 0.3:
                local_targets.append(
                    (
                        max(gap, 0.0),
                        abs(target_box.x0 - source_box.x0),
                        base_rank[target],
                        target,
                    )
                )
        for _gap, _left_delta, _target_rank, target in sorted(local_targets)[:3]:
            candidate_pairs.add(_base_oriented_pair(source, target, base_rank))

    candidates: dict[tuple[str, str], _Candidate] = {}
    for first, second in candidate_pairs:
        source, target = _base_oriented_pair(first, second, base_rank)
        if source == target:
            continue
        source_element = by_id[source]
        target_element = by_id[target]
        if (
            str(source_element.get("role") or "").casefold() != "text"
            or str(target_element.get("role") or "").casefold() != "text"
        ):
            continue
        features = _pair_features(
            source_element,
            target_element,
            width=width,
            base_adjacent=base_rank[target] - base_rank[source] == 1,
            relation_selected=(source, target) in selected_relation,
            relation_forward=relation_scores.get((source, target), 0.0),
            relation_reverse=relation_scores.get((target, source), 0.0),
        )
        candidates[(source, target)] = _Candidate(source, target, features)
    return tuple(base_ids), tuple(candidates[pair] for pair in sorted(candidates))


def _pair_features(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    width: float,
    base_adjacent: bool,
    relation_selected: bool,
    relation_forward: float,
    relation_reverse: float,
) -> tuple[float, ...]:
    source_box = source["_bbox"]
    target_box = target["_bbox"]
    mean_height = max((source_box.height + target_box.height) / 2, 1.0)
    source_text = str(source.get("text") or "").strip()
    target_text = str(target.get("text") or "").strip()
    return (
        (target_box.x0 - source_box.x0) / mean_height,
        (target_box.x1 - source_box.x1) / mean_height,
        (target_box.y0 - source_box.y1) / mean_height,
        (target_box.y0 - source_box.y0) / mean_height,
        _horizontal_overlap(source_box, target_box),
        min(source_box.width, target_box.width) / max(source_box.width, target_box.width),
        min(source_box.height, target_box.height) / max(source_box.height, target_box.height),
        source_box.x0 / width,
        target_box.x0 / width,
        source_box.width / width,
        target_box.width / width,
        math.log1p(len(source_text)),
        math.log1p(len(target_text)),
        float(bool(source_text) and source_text[-1] in ".!?"),
        float(bool(source_text) and source_text[-1] in ";:"),
        float(bool(source_text) and source_text[-1] in ",-\u00ad"),
        float(bool(re.match(r"^\s*[a-zà-öø-ÿ]", target_text))),
        float(bool(re.match(r"^\s*[A-ZÀ-ÖØ-Þ]", target_text))),
        float(bool(re.match(r"^\s*\d", target_text))),
        float(base_adjacent),
        float(relation_selected),
        float(relation_forward),
        float(relation_reverse),
    )


def _training_matrix(pages: list[_Page], labels: list[dict[str, str]]) -> tuple[Any, Any, Any]:
    _, _, numpy, _ = _training_modules()
    features: list[tuple[float, ...]] = []
    targets: list[int] = []
    groups: list[str] = []
    for page, membership in zip(pages, labels, strict=True):
        for candidate in page.candidates:
            features.append(candidate.features)
            targets.append(int(membership[candidate.source] == membership[candidate.target]))
            groups.append(str(page.sample["document_id"]))
    if not features or len(set(targets)) < 2:
        raise ValueError("fit paragraph graph candidates require positive and negative labels")
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


def _freeze_threshold(
    pages: list[_Page],
    labels: list[dict[str, str]],
    scores: Any,
    *,
    minimum_edge_precision: float,
    minimum_selected_edges: int,
    numpy: Any,
) -> tuple[float, dict[str, Any]]:
    thresholds = numpy.unique(numpy.quantile(scores, numpy.linspace(0.0, 1.0, 401)))
    candidates: list[tuple[float, float, float, float, int, float, dict[str, Any]]] = []
    targets = _candidate_targets(pages, labels, numpy=numpy)
    for raw_threshold in thresholds:
        threshold = float(raw_threshold)
        selected = scores >= threshold
        selected_count = int(selected.sum())
        edge_correct = int(targets[selected].sum())
        edge_precision = _ratio(edge_correct, selected_count)
        if selected_count < minimum_selected_edges or edge_precision < minimum_edge_precision:
            continue
        metrics = _score_pages(pages, labels, scores, threshold)
        pair = metrics["segmentation_pairwise"]
        candidates.append(
            (
                float(pair["f1"]),
                float(pair["precision"]),
                float(pair["recall"]),
                edge_precision,
                selected_count,
                threshold,
                metrics,
            )
        )
    if not candidates:
        raise ValueError("fit OOF scores have no paragraph graph operating point")
    pair_f1, pair_precision, pair_recall, edge_precision, selected_count, threshold, metrics = max(
        candidates,
        key=lambda item: item[:6],
    )
    return threshold, {
        "selected_edge_count": selected_count,
        "edge_precision": round(edge_precision, 8),
        "segmentation_pairwise": metrics["segmentation_pairwise"],
        "pair_f1": round(pair_f1, 8),
        "pair_precision": round(pair_precision, 8),
        "pair_recall": round(pair_recall, 8),
    }


def _score_pages(
    pages: list[_Page],
    labels: list[dict[str, str]],
    scores: Any,
    threshold: float,
) -> dict[str, Any]:
    expected_score_count = sum(len(page.candidates) for page in pages)
    if len(scores) != expected_score_count:
        raise ValueError("paragraph graph score count does not match candidates")
    edge_counts = Counter()
    pair_counts = Counter()
    component_count = 0
    candidate_page_count = 0
    segmentation_label_page_count = 0
    cursor = 0
    for page, membership in zip(pages, labels, strict=True):
        page_scores = scores[cursor : cursor + len(page.candidates)]
        cursor += len(page.candidates)
        selected_edges = [
            (candidate.source, candidate.target)
            for candidate, score in zip(page.candidates, page_scores, strict=True)
            if float(score) >= threshold
        ]
        candidate_page_count += bool(page.candidates)
        edge_counts["predicted"] += len(selected_edges)
        edge_counts["labels"] += sum(
            membership[candidate.source] == membership[candidate.target]
            for candidate in page.candidates
        )
        edge_counts["correct"] += sum(
            membership[source] == membership[target]
            for source, target in selected_edges
        )
        predicted_membership = _components(page.element_ids, selected_edges)
        component_count += len(set(predicted_membership.values()))
        predicted_pairs = _co_membership_pairs(predicted_membership)
        truth_pairs = _co_membership_pairs(membership)
        segmentation_label_page_count += bool(truth_pairs)
        pair_counts["predicted"] += len(predicted_pairs)
        pair_counts["labels"] += len(truth_pairs)
        pair_counts["correct"] += len(predicted_pairs & truth_pairs)
    return {
        "page_count": len(pages),
        "candidate_page_count": candidate_page_count,
        "segmentation_label_page_count": segmentation_label_page_count,
        "candidate_edge_count": sum(len(page.candidates) for page in pages),
        "selected_edge": _precision_recall_f1(edge_counts),
        "segmentation_pairwise": _precision_recall_f1(pair_counts),
        "component_count": component_count,
        "runtime_reorder": False,
    }


def _score_pages_by_layout_stratum(
    pages: list[_Page],
    labels: list[dict[str, str]],
    scores: Any,
    threshold: float,
) -> dict[str, dict[str, Any]]:
    expected_score_count = sum(len(page.candidates) for page in pages)
    if len(scores) != expected_score_count:
        raise ValueError("paragraph graph score count does not match candidates")
    grouped_pages: dict[str, list[_Page]] = defaultdict(list)
    grouped_labels: dict[str, list[dict[str, str]]] = defaultdict(list)
    grouped_scores: dict[str, list[float]] = defaultdict(list)
    cursor = 0
    for page, membership in zip(pages, labels, strict=True):
        stratum = str(page.sample.get("layout_stratum") or "unspecified")
        page_scores = scores[cursor : cursor + len(page.candidates)]
        cursor += len(page.candidates)
        grouped_pages[stratum].append(page)
        grouped_labels[stratum].append(membership)
        grouped_scores[stratum].extend(float(score) for score in page_scores)
    return {
        stratum: _score_pages(
            grouped_pages[stratum],
            grouped_labels[stratum],
            grouped_scores[stratum],
            threshold,
        )
        for stratum in sorted(grouped_pages)
    }


def _write_proposals(pages: list[_Page], scores: Any, threshold: float, root: Path) -> list[str]:
    expected_score_count = sum(len(page.candidates) for page in pages)
    if len(scores) != expected_score_count:
        raise ValueError("paragraph graph score count does not match candidates")
    paths: list[str] = []
    cursor = 0
    for page in pages:
        page_scores = scores[cursor : cursor + len(page.candidates)]
        cursor += len(page.candidates)
        selected_edges = [
            (candidate.source, candidate.target)
            for candidate, score in zip(page.candidates, page_scores, strict=True)
            if float(score) >= threshold
        ]
        membership = _components(page.element_ids, selected_edges)
        grouped: dict[str, list[str]] = defaultdict(list)
        for element_id in page.element_ids:
            grouped[membership[element_id]].append(element_id)
        proposal = {
            "schema": PARAGRAPH_GRAPH_PROPOSAL_SCHEMA,
            "id": page.sample["id"],
            "partition": page.split,
            "feature_version": PARAGRAPH_GRAPH_FEATURE_VERSION,
            "threshold": round(threshold, 8),
            "runtime_reorder": False,
            "candidate_edges": [
                {
                    "source": candidate.source,
                    "target": candidate.target,
                    "score": round(float(score), 8),
                    "selected": float(score) >= threshold,
                }
                for candidate, score in zip(page.candidates, page_scores, strict=True)
            ],
            "reading_streams": [
                {
                    "id": f"paragraph-graph-{index + 1:04d}",
                    "type": "body",
                    "members": members,
                    "proposal": {
                        "origin": "fine-line-paragraph-graph",
                        "review_required": True,
                    },
                }
                for index, (_root_id, members) in enumerate(sorted(grouped.items()))
            ],
        }
        path = _proposal_path(root, str(page.sample["id"]))
        _write_json(path, proposal)
        paths.append(str(path))
    return paths


def _load_labels(page: _Page) -> dict[str, str]:
    path = _confined_path(page.corpus, page.sample.get("labels"), label="sample labels")
    _verify_hash(path, page.sample.get("labels_sha256"), label="sample labels")
    payload = _json_object(path, label="paragraph graph labels")
    if payload.get("schema") != PROVIDER_HIERARCHY_LABEL_SCHEMA:
        raise ValueError("paragraph graph labels have an unsupported schema")
    raw_memberships = payload.get("memberships")
    if not isinstance(raw_memberships, list):
        raise ValueError("paragraph graph labels require memberships")
    membership: dict[str, str] = {}
    for item in raw_memberships:
        if not isinstance(item, Mapping):
            raise ValueError("paragraph graph memberships must be objects")
        element_id = str(item.get("element_id") or "").strip()
        region_id = str(item.get("oracle_region_id") or "").strip()
        if not element_id or not region_id:
            raise ValueError("paragraph graph memberships require element and region ids")
        if element_id in membership:
            raise ValueError("paragraph graph membership labels must be unique")
        membership[element_id] = region_id
    if set(membership) != set(page.element_ids):
        raise ValueError("paragraph graph membership labels are incomplete")
    return membership


def _candidate_targets(pages: list[_Page], labels: list[dict[str, str]], *, numpy: Any) -> Any:
    return numpy.asarray(
        [
            int(membership[candidate.source] == membership[candidate.target])
            for page, membership in zip(pages, labels, strict=True)
            for candidate in page.candidates
        ],
        dtype=int,
    )


def _components(
    element_ids: tuple[str, ...],
    edges: list[tuple[str, str]],
) -> dict[str, str]:
    parent = {element_id: element_id for element_id in element_ids}

    def find(element_id: str) -> str:
        while parent[element_id] != element_id:
            parent[element_id] = parent[parent[element_id]]
            element_id = parent[element_id]
        return element_id

    for source, target in edges:
        source_root = find(source)
        target_root = find(target)
        if source_root != target_root:
            canonical, merged = sorted((source_root, target_root))
            parent[merged] = canonical
    return {element_id: find(element_id) for element_id in element_ids}


def _co_membership_pairs(membership: Mapping[str, str]) -> set[tuple[str, str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for element_id, group_id in membership.items():
        grouped[group_id].append(element_id)
    return {
        pair
        for members in grouped.values()
        for pair in combinations(sorted(members), 2)
    }


def _precision_recall_f1(counts: Counter[str]) -> dict[str, int | float]:
    correct = int(counts["correct"])
    predicted = int(counts["predicted"])
    labels = int(counts["labels"])
    precision = _ratio(correct, predicted)
    recall = _ratio(correct, labels)
    f1 = _ratio(2 * precision * recall, precision + recall)
    return {
        "correct": correct,
        "predicted": predicted,
        "labels": labels,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
    }


def _require_disjoint_documents(*groups: list[_Page]) -> None:
    seen: set[str] = set()
    for pages in groups:
        documents = {str(page.sample["document_id"]) for page in pages}
        overlap = seen & documents
        if overlap:
            raise ValueError("paragraph graph partitions must be document-disjoint")
        seen.update(documents)


def _require_unique_sample_ids(*groups: list[_Page]) -> None:
    seen: set[str] = set()
    for pages in groups:
        for page in pages:
            sample_id = str(page.sample["id"])
            if sample_id in seen:
                raise ValueError("paragraph graph sample ids must be globally unique")
            seen.add(sample_id)


def _element_sort_key(element: Mapping[str, Any]) -> tuple[float, float, float, float, str]:
    box = element["_bbox"]
    return (box.y0, box.x0, box.y1, box.x1, str(element["id"]))


def _base_oriented_pair(
    first: str,
    second: str,
    base_rank: Mapping[str, int],
) -> tuple[str, str]:
    return (first, second) if base_rank[first] < base_rank[second] else (second, first)


def _horizontal_overlap(first: BBox, second: BBox) -> float:
    return _ratio(
        max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0)),
        min(first.width, second.width),
    )


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 1.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return max(ordered[middle], 1.0)
    return max((ordered[middle - 1] + ordered[middle]) / 2, 1.0)


def _training_modules() -> tuple[Any, Any, Any, str]:
    try:
        import numpy
        import sklearn
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.model_selection import GroupKFold
    except ImportError as exc:
        raise RuntimeError(
            "paragraph graph benchmark requires requirements-relation-ranker.txt"
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
    return root / f"{_safe_filename(sample_id)}--{digest}.paragraph-graph.json"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0
