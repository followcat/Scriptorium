from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bipartite_matching import maximum_weight_bipartite_matching
from .provider_degradation import (
    aggregate_provider_degradation,
    characterize_provider_degradation,
    compare_with_synthetic_profiles,
)
from .relation_noise import perturb_relation_structure


PROVIDER_TRANSITION_CANDIDATES = (
    "visual-yx",
    "box-flow",
    "relation-graph",
)
PROVIDER_TRANSITION_SUPPORT_THRESHOLDS = (0, 1, 2, 3)
PROVIDER_TRANSITION_CONFIDENCE_THRESHOLDS: tuple[float | None, ...] = (
    None,
    0.5,
    0.6,
    0.7,
    0.75,
    0.8,
    0.85,
    0.9,
    0.95,
)


@dataclass(frozen=True)
class ProviderAnchor:
    id: str
    page_index: int
    kind: str
    bbox: tuple[float, float, float, float]
    text: str
    order: int | None
    group_id: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class ProviderAnchorBenchmarkResult:
    report_path: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class ProviderAnchorSuiteResult:
    report_path: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class ProviderTransitionGateResult:
    gate_path: Path
    gate: dict[str, Any]


def benchmark_provider_anchors(
    oracle_structure_path: str | Path,
    semantic_sidecar_path: str | Path,
    provider_json_path: str | Path,
    *,
    floating_model_path: str | Path | None = None,
    output: str | Path | None = None,
) -> ProviderAnchorBenchmarkResult:
    """Match real provider blocks to held-out oracle anchors and score relations."""

    oracle = json.loads(Path(oracle_structure_path).read_text(encoding="utf-8"))
    semantic = json.loads(Path(semantic_sidecar_path).read_text(encoding="utf-8"))
    provider = json.loads(Path(provider_json_path).read_text(encoding="utf-8"))
    provider_name, anchors, explicit_relations = normalize_provider_anchors(provider)
    provider_capabilities = _provider_capabilities(provider)
    oracle_nodes = [node for node in oracle.get("document", []) if isinstance(node, Mapping)]
    assignments = match_provider_anchors(oracle_nodes, anchors)
    image = oracle.get("img", {})
    width = float(image.get("width") or 0)
    height = float(image.get("height") or 0)
    degradation = characterize_provider_degradation(
        oracle_nodes,
        anchors,
        width=width,
        height=height,
        text_recognition_available=provider_capabilities["text_recognition"],
    )
    synthetic_reports: dict[str, dict[str, Any]] = {}
    synthetic_noise: dict[str, dict[str, int | float | str]] = {}
    for profile in ("clean", "mild", "stress"):
        perturbed, noise_diagnostics = perturb_relation_structure(oracle, profile=profile)
        _, synthetic_anchors, _ = normalize_provider_anchors(perturbed)
        synthetic_reports[profile] = characterize_provider_degradation(
            oracle_nodes,
            synthetic_anchors,
            width=width,
            height=height,
        )
        synthetic_noise[profile] = noise_diagnostics
    degradation["synthetic_profile_comparison"] = compare_with_synthetic_profiles(
        degradation,
        synthetic_reports,
    )
    for profile, diagnostics in synthetic_noise.items():
        degradation["synthetic_profile_comparison"]["profiles"][profile][
            "noise"
        ] = diagnostics
    known_oracle_ids = {str(node.get("id")) for node in oracle_nodes}
    matched_oracle_ids = set(assignments)
    matched_provider_ids = {match["provider_id"] for match in assignments.values()}
    anchor_kinds = _anchor_kind_metrics(
        oracle_nodes,
        anchors,
        matched_oracle_ids,
        matched_provider_ids,
    )
    truth = {tuple(map(str, edge)) for edge in semantic.get("ro_linkings", [])}
    serialized_edge_groups = _serialized_provider_edge_groups(anchors, assignments)
    serialized_edges = serialized_edge_groups["all"]
    provider_transition_review = _provider_transition_review(
        anchors,
        assignments,
        oracle_nodes,
        width=width,
        height=height,
        truth=truth,
    )
    explicit_edges = _mapped_explicit_relations(explicit_relations, assignments)
    trained_floating_edges: set[tuple[str, str]] = set()
    reliable_trained_floating_edges: set[tuple[str, str]] = set()
    strict_trained_floating_edges: set[tuple[str, str]] = set()
    strict_in_envelope_trained_floating_edges: set[tuple[str, str]] = set()
    noise_aware_reliable_trained_floating_edges: set[tuple[str, str]] = set()
    noise_aware_strict_trained_floating_edges: set[tuple[str, str]] = set()
    floating_model_sha256 = None
    if floating_model_path is not None:
        from .floating_ranker import _predict_floating_relations, load_floating_relation_ranker

        floating_bundle, floating_manifest = load_floating_relation_ranker(floating_model_path)
        floating_model_sha256 = floating_manifest.get("model_sha256")
        provider_structure = {
            "uid": f"provider:{oracle.get('uid') or 'page'}",
            "img": {"width": image.get("width"), "height": image.get("height")},
            "document": [
                {
                    "id": anchor.id,
                    "block_id": anchor.id,
                    "type": anchor.kind if anchor.kind in {"figure", "table"} else "text",
                    "box": list(anchor.bbox),
                    "text": anchor.text,
                }
                for anchor in anchors
            ],
        }
        floating_prediction = _predict_floating_relations(
            provider_structure,
            bundle=floating_bundle,
            manifest=floating_manifest,
        )
        trained_floating_edges = _mapped_explicit_relations(
            [
                (str(edge["source"]), str(edge["target"]))
                for edge in floating_prediction.successor_edges
            ],
            assignments,
        )
        reliable_trained_floating_edges = _mapped_explicit_relations(
            [
                (str(edge["source"]), str(edge["target"]))
                for edge in floating_prediction.successor_edges
                if edge.get("reliability_tier") == "high-precision-review"
                and int(edge.get("feature_outlier_count", 0)) == 0
            ],
            assignments,
        )
        strict_trained_floating_edges = _mapped_explicit_relations(
            [
                (str(edge["source"]), str(edge["target"]))
                for edge in floating_prediction.successor_edges
                if edge.get("strict_gate_passed") is True
            ],
            assignments,
        )
        strict_in_envelope_trained_floating_edges = _mapped_explicit_relations(
            [
                (str(edge["source"]), str(edge["target"]))
                for edge in floating_prediction.successor_edges
                if edge.get("strict_gate_passed") is True
                and int(edge.get("feature_outlier_count", 0)) == 0
            ],
            assignments,
        )
        noise_aware_reliable_trained_floating_edges = _mapped_explicit_relations(
            [
                (str(edge["source"]), str(edge["target"]))
                for edge in floating_prediction.successor_edges
                if edge.get("noise_aware_reliability_tier")
                == "robust-high-precision-review"
            ],
            assignments,
        )
        noise_aware_strict_trained_floating_edges = _mapped_explicit_relations(
            [
                (str(edge["source"]), str(edge["target"]))
                for edge in floating_prediction.successor_edges
                if edge.get("noise_aware_strict_gate_passed") is True
            ],
            assignments,
        )
    combined_edges = serialized_edges | explicit_edges
    trained_combined_edges = combined_edges | trained_floating_edges
    reliable_trained_combined_edges = combined_edges | reliable_trained_floating_edges
    strict_trained_combined_edges = combined_edges | strict_trained_floating_edges
    strict_in_envelope_trained_combined_edges = (
        combined_edges | strict_in_envelope_trained_floating_edges
    )
    noise_aware_reliable_trained_combined_edges = (
        combined_edges | noise_aware_reliable_trained_floating_edges
    )
    noise_aware_strict_trained_combined_edges = (
        combined_edges | noise_aware_strict_trained_floating_edges
    )
    relation_predictions = {
        "serialized": serialized_edges,
        "serialized_within_anchor": serialized_edge_groups["within_anchor"],
        "serialized_between_anchors": serialized_edge_groups["between_anchors"],
        "serialized_direct_between_anchors": serialized_edge_groups[
            "direct_between_anchors"
        ],
        "explicit": explicit_edges,
        "combined": combined_edges,
        "trained_floating": trained_floating_edges,
        "reliable_trained_floating": reliable_trained_floating_edges,
        "strict_trained_floating": strict_trained_floating_edges,
        "strict_in_envelope_trained_floating": strict_in_envelope_trained_floating_edges,
        "noise_aware_reliable_trained_floating": (
            noise_aware_reliable_trained_floating_edges
        ),
        "noise_aware_strict_trained_floating": (
            noise_aware_strict_trained_floating_edges
        ),
        "combined_with_trained_floating": trained_combined_edges,
        "combined_with_reliable_trained_floating": reliable_trained_combined_edges,
        "combined_with_strict_trained_floating": strict_trained_combined_edges,
        "combined_with_strict_in_envelope_trained_floating": (
            strict_in_envelope_trained_combined_edges
        ),
        "combined_with_noise_aware_reliable_trained_floating": (
            noise_aware_reliable_trained_combined_edges
        ),
        "combined_with_noise_aware_strict_trained_floating": (
            noise_aware_strict_trained_combined_edges
        ),
    }
    report = {
        "schema": "scriptorium-provider-anchor-benchmark/v4",
        "provider": provider_name,
        "provider_capabilities": provider_capabilities,
        "floating_model_sha256": floating_model_sha256,
        "oracle_sample": str(oracle.get("uid") or Path(oracle_structure_path).stem),
        "oracle_anchor_count": len(known_oracle_ids),
        "matched_oracle_anchor_count": len(matched_oracle_ids),
        "oracle_anchor_recall": _ratio(len(matched_oracle_ids), len(known_oracle_ids)),
        "provider_anchor_count": len(anchors),
        "matched_provider_anchor_count": len(matched_provider_ids),
        "provider_anchor_match_rate": _ratio(len(matched_provider_ids), len(anchors)),
        "provider_to_oracle_granularity_ratio": _ratio(len(anchors), len(known_oracle_ids)),
        "anchor_kinds": anchor_kinds,
        "provider_degradation": degradation,
        "assignments": assignments,
        "relations": {
            "labels": len(truth),
            **{
                name: _relation_metrics(predicted, truth)
                for name, predicted in relation_predictions.items()
            },
        },
        "provider_transition_review": provider_transition_review,
        "graphical_relation_audit": _graphical_relation_audit(
            oracle,
            oracle_nodes,
            truth,
            relation_predictions,
        ),
    }
    report_path = (
        Path(output)
        if output is not None
        else Path(provider_json_path).with_name(f"{Path(provider_json_path).stem}.anchor-benchmark.json")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ProviderAnchorBenchmarkResult(report_path, report)


def benchmark_provider_anchor_suite(
    corpus_dir: str | Path,
    provider_dir: str | Path,
    *,
    floating_model_path: str | Path | None = None,
    transition_gate_path: str | Path | None = None,
    output: str | Path | None = None,
) -> ProviderAnchorSuiteResult:
    """Score matching provider JSON files over a rendered Comp-HRDoc prefix."""

    corpus = Path(corpus_dir)
    providers = Path(provider_dir)
    manifest_path = corpus / "comphrdoc_benchmark_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("rendered Comp-HRDoc benchmark manifest is required")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    cases: list[dict[str, Any]] = []
    missing: list[str] = []
    missing_by_partition: dict[str, list[str]] = defaultdict(list)
    for sample in manifest.get("samples", []):
        sample_id = str(sample["id"])
        partition = str(sample.get("partition") or "unspecified")
        provider_path = providers / f"{sample_id}.structure.json"
        if not provider_path.is_file():
            missing.append(sample_id)
            missing_by_partition[partition].append(sample_id)
            continue
        case = benchmark_provider_anchors(
            corpus / str(sample["structure"]),
            corpus / str(sample["semantic_sidecar"]),
            provider_path,
            floating_model_path=floating_model_path,
            output=providers / "anchor-benchmarks" / f"{sample_id}.json",
        ).report
        case["sample_id"] = sample_id
        case["partition"] = partition
        case["layout_stratum"] = str(sample.get("layout_stratum") or "unspecified")
        cases.append(case)
    if not cases:
        raise ValueError("provider directory contains no matching structure JSON files")
    relation_keys = (
        "serialized",
        "serialized_within_anchor",
        "serialized_between_anchors",
        "serialized_direct_between_anchors",
        "explicit",
        "combined",
        "trained_floating",
        "reliable_trained_floating",
        "strict_trained_floating",
        "strict_in_envelope_trained_floating",
        "noise_aware_reliable_trained_floating",
        "noise_aware_strict_trained_floating",
        "combined_with_trained_floating",
        "combined_with_reliable_trained_floating",
        "combined_with_strict_trained_floating",
        "combined_with_strict_in_envelope_trained_floating",
        "combined_with_noise_aware_reliable_trained_floating",
        "combined_with_noise_aware_strict_trained_floating",
    )
    relation_summary = {
        key: _sum_relation_metrics([case["relations"][key] for case in cases])
        for key in relation_keys
    }
    kind_names = sorted(
        {
            kind
            for case in cases
            for kind in case.get("anchor_kinds", {})
        }
    )
    anchor_kind_summary = {
        kind: _sum_anchor_kind_metrics(
            [case.get("anchor_kinds", {}).get(kind, {}) for case in cases]
        )
        for kind in kind_names
    }
    oracle_total = sum(case["oracle_anchor_count"] for case in cases)
    oracle_matched = sum(case["matched_oracle_anchor_count"] for case in cases)
    provider_total = sum(case["provider_anchor_count"] for case in cases)
    provider_matched = sum(case["matched_provider_anchor_count"] for case in cases)
    graphical_audits = [case["graphical_relation_audit"] for case in cases]
    graphical_audit_summary = {
        "reference_policy": "answer-free-local-geometry-diagnostic-not-ground-truth",
        "oracle_graphical_label_count": sum(
            int(audit["oracle_graphical_label_count"]) for audit in graphical_audits
        ),
        "geometry_proposal_count": sum(
            int(audit["geometry_proposal_count"]) for audit in graphical_audits
        ),
        "exact_agreement_count": sum(
            int(audit["exact_agreement_count"]) for audit in graphical_audits
        ),
        "conflicting_label_count": sum(
            int(audit["conflicting_label_count"]) for audit in graphical_audits
        ),
        "oracle_without_geometry_count": sum(
            int(audit["oracle_without_geometry_count"]) for audit in graphical_audits
        ),
        "geometry_without_oracle_count": sum(
            int(audit["geometry_without_oracle_count"]) for audit in graphical_audits
        ),
        "cases_with_conflicts": sum(
            int(bool(audit["conflicting_label_count"])) for audit in graphical_audits
        ),
        "provider_geometry_agreement": {
            key: _sum_relation_metrics(
                [audit["provider_geometry_agreement"][key] for audit in graphical_audits]
            )
            for key in relation_keys
        },
    }
    graphical_audit_summary["oracle_geometry_exact_agreement"] = _ratio(
        graphical_audit_summary["exact_agreement_count"],
        graphical_audit_summary["oracle_graphical_label_count"],
    )
    graphical_audit_summary["oracle_geometry_conflict_rate"] = _ratio(
        graphical_audit_summary["conflicting_label_count"],
        graphical_audit_summary["oracle_graphical_label_count"],
    )
    provider_degradation = aggregate_provider_degradation(
        [case["provider_degradation"] for case in cases]
    )
    provider_transition_review = _sum_provider_transition_reviews(
        [case["provider_transition_review"] for case in cases]
    )
    partitions = {
        partition: _provider_case_subset_summary(
            [case for case in cases if case["partition"] == partition],
            relation_keys=relation_keys,
        )
        for partition in sorted({str(case["partition"]) for case in cases})
    }
    layout_strata = {
        layout_stratum: _provider_case_subset_summary(
            [case for case in cases if case["layout_stratum"] == layout_stratum],
            relation_keys=relation_keys,
        )
        for layout_stratum in sorted(
            {str(case["layout_stratum"]) for case in cases}
        )
    }
    transition_gate_evaluation = None
    gate: Mapping[str, Any] | None = None
    if transition_gate_path is not None:
        gate_path = Path(transition_gate_path)
        gate_bytes = gate_path.read_bytes()
        gate = json.loads(gate_bytes)
        transition_gate_evaluation = _evaluate_provider_transition_gate(
            provider_transition_review,
            gate,
            cases=cases,
        )
        transition_gate_evaluation["gate_path"] = str(gate_path)
        transition_gate_evaluation["gate_sha256"] = hashlib.sha256(
            gate_bytes
        ).hexdigest()
        transition_gate_evaluation["source_corpus_manifest_sha256"] = gate.get(
            "source_corpus_manifest_sha256"
        )
        transition_gate_evaluation["evaluation_corpus_manifest_sha256"] = (
            hashlib.sha256(manifest_bytes).hexdigest()
        )
        transition_gate_evaluation["independent_corpus"] = (
            gate.get("source_corpus_manifest_sha256")
            != hashlib.sha256(manifest_bytes).hexdigest()
        )
        if gate.get("schema") == "scriptorium-provider-transition-gate/v1":
            for subset in (*partitions.values(), *layout_strata.values()):
                subset["provider_transition_gate_evaluation"] = (
                    _evaluate_provider_transition_gate(
                        subset["provider_transition_review"],
                        gate,
                    )
                )
            layout_stratum_evaluations = {
                name: subset["provider_transition_gate_evaluation"]
                for name, subset in layout_strata.items()
            }
            failed_layout_strata = [
                name
                for name, evaluation in layout_stratum_evaluations.items()
                if not evaluation["meets_frozen_acceptance_criteria"]
            ]
            transition_gate_evaluation["post_hoc_safety_audit"] = {
                "policy": "veto-only-post-hoc-stratification-cannot-authorize-runtime-promotion",
                "layout_strata": layout_stratum_evaluations,
                "failed_layout_strata": failed_layout_strata,
                "all_layout_strata_meet_frozen_aggregate_criteria": (
                    not failed_layout_strata
                ),
                "position_bands": _provider_transition_position_audit(cases, gate),
                "runtime_promotion_decision": "reject-runtime-promotion",
            }
    report = {
        "schema": "scriptorium-provider-anchor-suite/v6",
        "corpus_manifest": str(manifest_path),
        "corpus_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "corpus": {
            key: manifest.get(key)
            for key in (
                "schema",
                "dataset",
                "revision",
                "annotation_archive_sha256",
                "annotation_member",
                "selection",
                "selection_uses_relation_labels",
                "partition",
                "sample_count",
                "document_count",
                "document_offset",
                "selection_window",
            )
            if key in manifest
        },
        "selection": manifest.get("selection"),
        "provider": cases[0]["provider"],
        "provider_capabilities": cases[0].get("provider_capabilities"),
        "case_count": len(cases),
        "missing_provider_case_count": len(missing),
        "missing_provider_cases": missing,
        "missing_provider_cases_by_partition": {
            partition: sample_ids
            for partition, sample_ids in sorted(missing_by_partition.items())
        },
        "oracle_anchor_count": oracle_total,
        "matched_oracle_anchor_count": oracle_matched,
        "oracle_anchor_recall": _ratio(oracle_matched, oracle_total),
        "provider_anchor_count": provider_total,
        "matched_provider_anchor_count": provider_matched,
        "provider_anchor_match_rate": _ratio(provider_matched, provider_total),
        "anchor_kinds": anchor_kind_summary,
        "provider_degradation": provider_degradation,
        "provider_transition_review": provider_transition_review,
        "provider_transition_gate_evaluation": transition_gate_evaluation,
        "relations": relation_summary,
        "graphical_relation_audit": graphical_audit_summary,
        "partitions": partitions,
        "layout_strata": layout_strata,
        "cases": cases,
    }
    report_path = Path(output) if output is not None else providers / "provider_anchor_suite_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ProviderAnchorSuiteResult(report_path, report)


def freeze_provider_transition_gate(
    suite_report_path: str | Path,
    *,
    partition: str = "fit",
    minimum_precision: float = 0.95,
    minimum_wilson_lower_95: float = 0.9,
    minimum_predicted: int = 50,
    output: str | Path | None = None,
) -> ProviderTransitionGateResult:
    """Freeze a review-only transition gate using one named fit partition."""

    if not 0.0 <= minimum_precision <= 1.0:
        raise ValueError("minimum_precision must be between 0 and 1")
    if not 0.0 <= minimum_wilson_lower_95 <= 1.0:
        raise ValueError("minimum_wilson_lower_95 must be between 0 and 1")
    if minimum_predicted < 1:
        raise ValueError("minimum_predicted must be at least 1")
    source_path = Path(suite_report_path)
    source_bytes = source_path.read_bytes()
    suite = json.loads(source_bytes)
    partition_report = suite.get("partitions", {}).get(partition)
    if not isinstance(partition_report, Mapping):
        raise ValueError(f"provider suite report does not contain partition {partition!r}")
    review = partition_report.get("provider_transition_review")
    if not isinstance(review, Mapping):
        raise ValueError("provider suite report does not contain transition review curves")
    qualified = [
        point
        for point in review.get("curve", [])
        if isinstance(point, Mapping)
        and int(point.get("minimum_native_support", 0)) >= 1
        and point.get("minimum_provider_confidence") is not None
        and int(point.get("predicted", 0)) >= minimum_predicted
        and float(point.get("precision", 0.0)) >= minimum_precision
        and float(point.get("precision_wilson_lower_95", 0.0))
        >= minimum_wilson_lower_95
    ]
    if not qualified:
        raise ValueError("no provider transition curve point satisfies the freeze criteria")
    selected = max(
        qualified,
        key=lambda point: (
            int(point["predicted"]),
            float(point["precision_wilson_lower_95"]),
            float(point["precision"]),
            int(point["minimum_native_support"]),
            float(point["minimum_provider_confidence"]),
        ),
    )
    gate = {
        "schema": "scriptorium-provider-transition-gate/v1",
        "status": "frozen-review-only",
        "runtime_reorder": False,
        "source_suite_report": str(source_path),
        "source_suite_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_corpus_manifest_sha256": suite.get("corpus_manifest_sha256"),
        "source_corpus": suite.get("corpus"),
        "selection_partition": partition,
        "selection_uses_semantic_labels": True,
        "candidate_orders": review.get("candidate_orders"),
        "selection_policy": {
            "objective": "maximize-eligible-transitions-after-quality-constraints",
            "minimum_native_support": 1,
            "provider_confidence_required": True,
            "minimum_precision": minimum_precision,
            "minimum_precision_wilson_lower_95": minimum_wilson_lower_95,
            "minimum_predicted": minimum_predicted,
        },
        "acceptance_criteria": {
            "minimum_precision": minimum_precision,
            "minimum_precision_wilson_lower_95": minimum_wilson_lower_95,
            "minimum_predicted": minimum_predicted,
        },
        "minimum_native_support": int(selected["minimum_native_support"]),
        "minimum_provider_confidence": float(
            selected["minimum_provider_confidence"]
        ),
        "fit_metrics": dict(selected),
    }
    gate_path = (
        Path(output)
        if output is not None
        else source_path.with_name(f"{source_path.stem}.transition-gate.json")
    )
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(
        json.dumps(gate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ProviderTransitionGateResult(gate_path, gate)


def freeze_stratified_provider_transition_gate(
    suite_report_path: str | Path,
    *,
    fit_partition: str = "fit",
    calibration_partition: str = "calibration",
    fit_minimum_precision: float = 0.95,
    fit_minimum_wilson_lower_95: float = 0.8,
    fit_minimum_predicted: int = 20,
    calibration_minimum_precision: float = 0.95,
    calibration_minimum_wilson_lower_95: float = 0.85,
    calibration_minimum_predicted: int = 30,
    test_minimum_precision: float = 0.95,
    test_minimum_wilson_lower_95: float = 0.9,
    test_minimum_predicted: int = 50,
    test_bucket_minimum_precision: float = 0.95,
    test_bucket_minimum_wilson_lower_95: float = 0.8,
    test_bucket_minimum_predicted: int = 20,
    allowed_layout_strata: Sequence[str] | None = None,
    allowed_position_bands: Sequence[str] | None = None,
    output: str | Path | None = None,
) -> ProviderTransitionGateResult:
    """Freeze layout/position bucket rules, then accept or reject on calibration."""

    _validate_transition_quality_criteria(
        precision=fit_minimum_precision,
        wilson=fit_minimum_wilson_lower_95,
        predicted=fit_minimum_predicted,
        prefix="fit",
    )
    _validate_transition_quality_criteria(
        precision=calibration_minimum_precision,
        wilson=calibration_minimum_wilson_lower_95,
        predicted=calibration_minimum_predicted,
        prefix="calibration",
    )
    _validate_transition_quality_criteria(
        precision=test_minimum_precision,
        wilson=test_minimum_wilson_lower_95,
        predicted=test_minimum_predicted,
        prefix="test",
    )
    _validate_transition_quality_criteria(
        precision=test_bucket_minimum_precision,
        wilson=test_bucket_minimum_wilson_lower_95,
        predicted=test_bucket_minimum_predicted,
        prefix="test_bucket",
    )
    source_path = Path(suite_report_path)
    source_bytes = source_path.read_bytes()
    suite = json.loads(source_bytes)
    fit_records = _suite_transition_records(suite, partition=fit_partition)
    calibration_records = _suite_transition_records(
        suite,
        partition=calibration_partition,
    )
    if not fit_records:
        raise ValueError(f"provider suite contains no {fit_partition!r} transitions")
    if not calibration_records:
        raise ValueError(
            f"provider suite contains no {calibration_partition!r} transitions"
        )

    fit_buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    calibration_buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for record in fit_records:
        fit_buckets[(record["layout_stratum"], record["position_band"])].append(
            record
        )
    for record in calibration_records:
        calibration_buckets[
            (record["layout_stratum"], record["position_band"])
        ].append(record)

    allowed_layout_values = (
        (allowed_layout_strata,)
        if isinstance(allowed_layout_strata, str)
        else allowed_layout_strata
    )
    normalized_allowed_layout_strata = (
        tuple(
            sorted(
                {
                    str(value).strip()
                    for value in allowed_layout_values
                    if str(value).strip()
                }
            )
        )
        if allowed_layout_values is not None
        else None
    )
    if allowed_layout_strata is not None and not normalized_allowed_layout_strata:
        raise ValueError("allowed_layout_strata must contain at least one value")
    allowed_position_values = (
        (allowed_position_bands,)
        if isinstance(allowed_position_bands, str)
        else allowed_position_bands
    )
    normalized_allowed_position_bands = (
        tuple(
            sorted(
                {
                    str(value).strip()
                    for value in allowed_position_values
                    if str(value).strip()
                }
            )
        )
        if allowed_position_values is not None
        else None
    )
    valid_position_bands = {"start", "middle", "end", "single"}
    if allowed_position_bands is not None and not normalized_allowed_position_bands:
        raise ValueError("allowed_position_bands must contain at least one value")
    if normalized_allowed_position_bands is not None and not set(
        normalized_allowed_position_bands
    ).issubset(valid_position_bands):
        raise ValueError(
            "allowed_position_bands must contain only start, middle, end, or single"
        )

    rules: list[dict[str, Any]] = []
    inactive_buckets: list[dict[str, Any]] = []
    for layout_stratum, position_band in sorted(fit_buckets):
        bucket_records = fit_buckets[(layout_stratum, position_band)]
        if (
            normalized_allowed_layout_strata is not None
            and layout_stratum not in normalized_allowed_layout_strata
        ):
            inactive_buckets.append(
                {
                    "layout_stratum": layout_stratum,
                    "position_band": position_band,
                    "fit_transition_count": len(bucket_records),
                    "reason": "excluded-by-predeclared-layout-policy",
                }
            )
            continue
        if (
            normalized_allowed_position_bands is not None
            and position_band not in normalized_allowed_position_bands
        ):
            inactive_buckets.append(
                {
                    "layout_stratum": layout_stratum,
                    "position_band": position_band,
                    "fit_transition_count": len(bucket_records),
                    "reason": "excluded-by-predeclared-position-policy",
                }
            )
            continue
        qualified: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        for minimum_support in PROVIDER_TRANSITION_SUPPORT_THRESHOLDS[1:]:
            for minimum_confidence in (
                value
                for value in PROVIDER_TRANSITION_CONFIDENCE_THRESHOLDS
                if value is not None
            ):
                selected = _select_transition_records(
                    bucket_records,
                    minimum_support=minimum_support,
                    minimum_confidence=minimum_confidence,
                )
                metrics = _transition_record_metrics(
                    selected,
                    denominator=len(bucket_records),
                )
                metrics["minimum_native_support"] = minimum_support
                metrics["minimum_provider_confidence"] = minimum_confidence
                if _transition_metrics_meet(
                    metrics,
                    minimum_precision=fit_minimum_precision,
                    minimum_wilson_lower_95=fit_minimum_wilson_lower_95,
                    minimum_predicted=fit_minimum_predicted,
                ):
                    qualified.append((metrics, selected))
        if not qualified:
            inactive_buckets.append(
                {
                    "layout_stratum": layout_stratum,
                    "position_band": position_band,
                    "fit_transition_count": len(bucket_records),
                    "reason": "no-fit-curve-point-meets-quality-and-coverage",
                }
            )
            continue
        fit_metrics, _fit_selected = max(
            qualified,
            key=lambda item: (
                int(item[0]["predicted"]),
                float(item[0]["precision_wilson_lower_95"]),
                float(item[0]["precision"]),
                int(item[0]["minimum_native_support"]),
                float(item[0]["minimum_provider_confidence"]),
            ),
        )
        calibration_bucket_records = calibration_buckets.get(
            (layout_stratum, position_band),
            [],
        )
        calibration_selected = _select_transition_records(
            calibration_bucket_records,
            minimum_support=int(fit_metrics["minimum_native_support"]),
            minimum_confidence=float(fit_metrics["minimum_provider_confidence"]),
        )
        rules.append(
            {
                "layout_stratum": layout_stratum,
                "position_band": position_band,
                "minimum_native_support": int(
                    fit_metrics["minimum_native_support"]
                ),
                "minimum_provider_confidence": float(
                    fit_metrics["minimum_provider_confidence"]
                ),
                "fit_metrics": fit_metrics,
                "calibration_metrics": _transition_record_metrics(
                    calibration_selected,
                    denominator=len(calibration_bucket_records),
                ),
            }
        )
    if not rules:
        raise ValueError("no layout/position transition bucket has a qualified fit rule")

    fit_selected = _apply_stratified_transition_rules(fit_records, rules)
    calibration_selected = _apply_stratified_transition_rules(
        calibration_records,
        rules,
    )
    fit_aggregate = _transition_record_metrics(
        fit_selected,
        denominator=len(fit_records),
    )
    calibration_aggregate = _transition_record_metrics(
        calibration_selected,
        denominator=len(calibration_records),
    )
    calibration_accepted = _transition_metrics_meet(
        calibration_aggregate,
        minimum_precision=calibration_minimum_precision,
        minimum_wilson_lower_95=calibration_minimum_wilson_lower_95,
        minimum_predicted=calibration_minimum_predicted,
    )
    gate = {
        "schema": "scriptorium-provider-transition-gate/v2",
        "status": (
            "frozen-review-only"
            if calibration_accepted
            else "calibration-rejected-review-only"
        ),
        "runtime_reorder": False,
        "policy_type": "layout-position-selective-abstention",
        "source_suite_report": str(source_path),
        "source_suite_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_corpus_manifest_sha256": suite.get("corpus_manifest_sha256"),
        "source_corpus": suite.get("corpus"),
        "fit_partition": fit_partition,
        "calibration_partition": calibration_partition,
        "selection_uses_semantic_labels": True,
        "calibration_can_modify_rules": False,
        "candidate_orders": list(PROVIDER_TRANSITION_CANDIDATES),
        "bucket_definition": {
            "dimensions": ["layout_stratum", "position_band"],
            "position_bands": ["start", "middle", "end", "single"],
            "allowed_layout_strata": (
                list(normalized_allowed_layout_strata)
                if normalized_allowed_layout_strata is not None
                else "all-fit-layout-strata"
            ),
            "allowed_position_bands": (
                list(normalized_allowed_position_bands)
                if normalized_allowed_position_bands is not None
                else "all-position-bands"
            ),
            "unruled_bucket_policy": "abstain",
        },
        "fit_selection_criteria": {
            "minimum_precision": fit_minimum_precision,
            "minimum_precision_wilson_lower_95": fit_minimum_wilson_lower_95,
            "minimum_predicted": fit_minimum_predicted,
            "objective": "maximize-eligible-transitions-after-bucket-quality-constraints",
        },
        "calibration_acceptance_criteria": {
            "minimum_precision": calibration_minimum_precision,
            "minimum_precision_wilson_lower_95": (
                calibration_minimum_wilson_lower_95
            ),
            "minimum_predicted": calibration_minimum_predicted,
        },
        "independent_acceptance_criteria": {
            "minimum_precision": test_minimum_precision,
            "minimum_precision_wilson_lower_95": test_minimum_wilson_lower_95,
            "minimum_predicted": test_minimum_predicted,
        },
        "independent_bucket_acceptance_criteria": {
            "minimum_precision": test_bucket_minimum_precision,
            "minimum_precision_wilson_lower_95": (
                test_bucket_minimum_wilson_lower_95
            ),
            "minimum_predicted": test_bucket_minimum_predicted,
        },
        "rules": rules,
        "inactive_buckets": inactive_buckets,
        "fit_aggregate_metrics": fit_aggregate,
        "calibration_aggregate_metrics": calibration_aggregate,
        "calibration_accepted": calibration_accepted,
    }
    gate_path = (
        Path(output)
        if output is not None
        else source_path.with_name(f"{source_path.stem}.stratified-transition-gate.json")
    )
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(
        json.dumps(gate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ProviderTransitionGateResult(gate_path, gate)


def _evaluate_provider_transition_gate(
    review: Mapping[str, Any],
    gate: Mapping[str, Any],
    *,
    cases: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if gate.get("runtime_reorder") is not False:
        raise ValueError("provider transition gate must remain review-only")
    if list(gate.get("candidate_orders") or []) != list(
        review.get("candidate_orders") or []
    ):
        raise ValueError("provider transition gate candidate orders do not match")
    if gate.get("schema") == "scriptorium-provider-transition-gate/v2":
        if cases is None:
            raise ValueError("stratified provider transition gate requires case records")
        return _evaluate_stratified_provider_transition_gate(cases, gate)
    if gate.get("schema") != "scriptorium-provider-transition-gate/v1":
        raise ValueError("unsupported provider transition gate schema")
    minimum_support = int(gate["minimum_native_support"])
    minimum_confidence = float(gate["minimum_provider_confidence"])
    point = next(
        (
            item
            for item in review.get("curve", [])
            if isinstance(item, Mapping)
            and int(item.get("minimum_native_support", -1)) == minimum_support
            and item.get("minimum_provider_confidence") is not None
            and abs(
                float(item["minimum_provider_confidence"])
                - minimum_confidence
            )
            <= 1e-12
        ),
        None,
    )
    if point is None:
        raise ValueError("provider transition gate threshold is absent from review curve")
    criteria = gate.get("acceptance_criteria")
    if not isinstance(criteria, Mapping):
        raise ValueError("provider transition gate acceptance criteria are required")
    checks = {
        "minimum_precision": float(point.get("precision", 0.0))
        >= float(criteria["minimum_precision"]),
        "minimum_precision_wilson_lower_95": float(
            point.get("precision_wilson_lower_95", 0.0)
        )
        >= float(criteria["minimum_precision_wilson_lower_95"]),
        "minimum_predicted": int(point.get("predicted", 0))
        >= int(criteria["minimum_predicted"]),
    }
    return {
        "schema": "scriptorium-provider-transition-gate-evaluation/v1",
        "status": "review-only",
        "runtime_reorder": False,
        "minimum_native_support": minimum_support,
        "minimum_provider_confidence": minimum_confidence,
        "acceptance_criteria": dict(criteria),
        "criterion_results": checks,
        "meets_frozen_acceptance_criteria": all(checks.values()),
        "metrics": dict(point),
    }


def _evaluate_stratified_provider_transition_gate(
    cases: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> dict[str, Any]:
    if gate.get("runtime_reorder") is not False:
        raise ValueError("provider transition gate must remain review-only")
    if gate.get("calibration_accepted") is not True:
        raise ValueError("stratified provider transition gate failed calibration")
    rules = [
        dict(rule)
        for rule in gate.get("rules", [])
        if isinstance(rule, Mapping)
    ]
    if not rules:
        raise ValueError("stratified provider transition gate has no active rules")
    records = _suite_transition_records({"cases": list(cases)}, partition=None)
    selected = _apply_stratified_transition_rules(records, rules)
    aggregate_metrics = _transition_record_metrics(
        selected,
        denominator=len(records),
    )
    aggregate_criteria = gate.get("independent_acceptance_criteria")
    bucket_criteria = gate.get("independent_bucket_acceptance_criteria")
    if not isinstance(aggregate_criteria, Mapping) or not isinstance(
        bucket_criteria,
        Mapping,
    ):
        raise ValueError("stratified gate independent acceptance criteria are required")
    aggregate_checks = _transition_metric_checks(
        aggregate_metrics,
        aggregate_criteria,
    )
    bucket_evaluations: list[dict[str, Any]] = []
    for rule in rules:
        bucket_records = [
            record
            for record in records
            if record["layout_stratum"] == rule["layout_stratum"]
            and record["position_band"] == rule["position_band"]
        ]
        bucket_selected = _select_transition_records(
            bucket_records,
            minimum_support=int(rule["minimum_native_support"]),
            minimum_confidence=float(rule["minimum_provider_confidence"]),
        )
        metrics = _transition_record_metrics(
            bucket_selected,
            denominator=len(bucket_records),
        )
        checks = _transition_metric_checks(metrics, bucket_criteria)
        bucket_evaluations.append(
            {
                "layout_stratum": rule["layout_stratum"],
                "position_band": rule["position_band"],
                "minimum_native_support": int(rule["minimum_native_support"]),
                "minimum_provider_confidence": float(
                    rule["minimum_provider_confidence"]
                ),
                "metrics": metrics,
                "criterion_results": checks,
                "meets_frozen_acceptance_criteria": all(checks.values()),
            }
        )
    active_bucket_keys = {
        (str(rule["layout_stratum"]), str(rule["position_band"]))
        for rule in rules
    }
    unruled_transition_count = sum(
        (record["layout_stratum"], record["position_band"])
        not in active_bucket_keys
        for record in records
    )
    bucket_checks_passed = all(
        evaluation["meets_frozen_acceptance_criteria"]
        for evaluation in bucket_evaluations
    )
    accepted = all(aggregate_checks.values()) and bucket_checks_passed
    return {
        "schema": "scriptorium-provider-transition-gate-evaluation/v2",
        "status": "review-only",
        "runtime_reorder": False,
        "policy_type": gate.get("policy_type"),
        "aggregate_metrics": aggregate_metrics,
        "aggregate_acceptance_criteria": dict(aggregate_criteria),
        "aggregate_criterion_results": aggregate_checks,
        "bucket_acceptance_criteria": dict(bucket_criteria),
        "bucket_evaluations": bucket_evaluations,
        "all_active_buckets_meet_frozen_acceptance_criteria": (
            bucket_checks_passed
        ),
        "unruled_transition_count": unruled_transition_count,
        "threshold_rejected_transition_count": (
            len(records) - unruled_transition_count - len(selected)
        ),
        "abstained_transition_count": len(records) - len(selected),
        "meets_frozen_acceptance_criteria": accepted,
        "next_stage_decision": (
            "eligible-for-shadow-runtime-experiment"
            if accepted
            else "reject-runtime-promotion"
        ),
    }


def _validate_transition_quality_criteria(
    *,
    precision: float,
    wilson: float,
    predicted: int,
    prefix: str,
) -> None:
    if not 0.0 <= precision <= 1.0:
        raise ValueError(f"{prefix}_minimum_precision must be between 0 and 1")
    if not 0.0 <= wilson <= 1.0:
        raise ValueError(
            f"{prefix}_minimum_wilson_lower_95 must be between 0 and 1"
        )
    if predicted < 1:
        raise ValueError(f"{prefix}_minimum_predicted must be at least 1")


def _suite_transition_records(
    suite: Mapping[str, Any],
    *,
    partition: str | None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for case in suite.get("cases", []):
        if not isinstance(case, Mapping):
            continue
        if partition is not None and str(case.get("partition")) != partition:
            continue
        review = case.get("provider_transition_review")
        if not isinstance(review, Mapping):
            continue
        for transition in review.get("transitions", []):
            if not isinstance(transition, Mapping):
                continue
            record = dict(transition)
            record["sample_id"] = str(case.get("sample_id") or "")
            record["partition"] = str(case.get("partition") or "unspecified")
            record["layout_stratum"] = str(
                case.get("layout_stratum") or "unspecified"
            )
            record["position_band"] = _provider_transition_position_band(record)
            records.append(record)
    return records


def _select_transition_records(
    records: Sequence[Mapping[str, Any]],
    *,
    minimum_support: int,
    minimum_confidence: float,
) -> list[dict[str, Any]]:
    return [
        dict(record)
        for record in records
        if int(record.get("native_support_count", 0)) >= minimum_support
        and record.get("minimum_provider_confidence") is not None
        and float(record["minimum_provider_confidence"])
        >= minimum_confidence
    ]


def _transition_record_metrics(
    records: Sequence[Mapping[str, Any]],
    *,
    denominator: int,
) -> dict[str, Any]:
    predicted = len(records)
    correct = sum(int(bool(record.get("correct"))) for record in records)
    return {
        "correct": correct,
        "incorrect": predicted - correct,
        "predicted": predicted,
        "precision": _ratio(correct, predicted),
        "precision_wilson_lower_95": _wilson_lower_bound(correct, predicted),
        "eligible_fraction": _ratio(predicted, denominator),
        "denominator": denominator,
    }


def _transition_metrics_meet(
    metrics: Mapping[str, Any],
    *,
    minimum_precision: float,
    minimum_wilson_lower_95: float,
    minimum_predicted: int,
) -> bool:
    return all(
        _transition_metric_checks(
            metrics,
            {
                "minimum_precision": minimum_precision,
                "minimum_precision_wilson_lower_95": minimum_wilson_lower_95,
                "minimum_predicted": minimum_predicted,
            },
        ).values()
    )


def _transition_metric_checks(
    metrics: Mapping[str, Any],
    criteria: Mapping[str, Any],
) -> dict[str, bool]:
    return {
        "minimum_precision": float(metrics.get("precision", 0.0))
        >= float(criteria["minimum_precision"]),
        "minimum_precision_wilson_lower_95": float(
            metrics.get("precision_wilson_lower_95", 0.0)
        )
        >= float(criteria["minimum_precision_wilson_lower_95"]),
        "minimum_predicted": int(metrics.get("predicted", 0))
        >= int(criteria["minimum_predicted"]),
    }


def _apply_stratified_transition_rules(
    records: Sequence[Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rules_by_bucket = {
        (str(rule["layout_stratum"]), str(rule["position_band"])): rule
        for rule in rules
    }
    selected: list[dict[str, Any]] = []
    for record in records:
        rule = rules_by_bucket.get(
            (str(record["layout_stratum"]), str(record["position_band"]))
        )
        if rule is None:
            continue
        selected.extend(
            _select_transition_records(
                [record],
                minimum_support=int(rule["minimum_native_support"]),
                minimum_confidence=float(rule["minimum_provider_confidence"]),
            )
        )
    return selected


def _provider_transition_position_band(transition: Mapping[str, Any]) -> str:
    transition_count = int(transition.get("page_transition_count", 0))
    transition_index = int(transition.get("transition_index", 0))
    if transition_count <= 1:
        return "single"
    relative_position = transition_index / (transition_count - 1)
    if relative_position < 1 / 3:
        return "start"
    if relative_position < 2 / 3:
        return "middle"
    return "end"


def _provider_transition_position_audit(
    cases: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    minimum_support = int(gate["minimum_native_support"])
    minimum_confidence = float(gate["minimum_provider_confidence"])
    counts: dict[str, list[int]] = {
        "start": [0, 0],
        "middle": [0, 0],
        "end": [0, 0],
        "single": [0, 0],
    }
    for case in cases:
        review = case.get("provider_transition_review")
        if not isinstance(review, Mapping):
            continue
        for transition in review.get("transitions", []):
            if not isinstance(transition, Mapping):
                continue
            confidence = transition.get("minimum_provider_confidence")
            if (
                int(transition.get("native_support_count", 0)) < minimum_support
                or confidence is None
                or float(confidence) < minimum_confidence
            ):
                continue
            band = _provider_transition_position_band(transition)
            counts[band][0] += int(bool(transition.get("correct")))
            counts[band][1] += 1
    return {
        band: {
            "correct": correct,
            "predicted": predicted,
            "precision": _ratio(correct, predicted),
            "precision_wilson_lower_95": _wilson_lower_bound(
                correct,
                predicted,
            ),
        }
        for band, (correct, predicted) in counts.items()
    }


def normalize_provider_anchors(
    payload: Mapping[str, Any],
) -> tuple[str, list[ProviderAnchor], list[tuple[str, str]]]:
    if isinstance(payload.get("raw_results"), list):
        return _normalize_paddle_vl(payload)
    if payload.get("schema_name") == "DoclingDocument" or isinstance(payload.get("body"), Mapping):
        return _normalize_docling(payload)
    if isinstance(payload.get("document"), list) and isinstance(payload.get("img"), Mapping):
        return _normalize_roor_style(payload)
    if isinstance(payload.get("pages"), list):
        return _normalize_page_elements(payload)
    raise ValueError("unsupported provider anchor JSON schema")


def _provider_capabilities(payload: Mapping[str, Any]) -> dict[str, bool]:
    declared = payload.get("capabilities")
    if not isinstance(declared, Mapping):
        return {"layout": True, "reading_order": True, "text_recognition": True}
    return {
        "layout": bool(declared.get("layout", True)),
        "reading_order": bool(declared.get("reading_order", True)),
        "text_recognition": bool(declared.get("text_recognition", True)),
    }


def _provider_confidence(item: Mapping[str, Any]) -> float | None:
    candidates = [item.get("confidence"), item.get("score")]
    provider_box = item.get("provider_box")
    if isinstance(provider_box, Mapping):
        candidates.extend((provider_box.get("confidence"), provider_box.get("score")))
    for candidate in candidates:
        try:
            confidence = float(candidate)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(confidence) and 0.0 <= confidence <= 1.0:
            return round(confidence, 8)
    return None


def _normalize_paddle_vl(
    payload: Mapping[str, Any],
) -> tuple[str, list[ProviderAnchor], list[tuple[str, str]]]:
    anchors: list[ProviderAnchor] = []
    order = 0
    for page_position, page in enumerate(payload.get("raw_results", [])):
        if not isinstance(page, Mapping):
            continue
        page_index = int(page.get("page_index", page_position))
        for item_position, item in enumerate(page.get("parsing_res_list", [])):
            if not isinstance(item, Mapping):
                continue
            box = item.get("block_bbox")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            label = str(item.get("block_label") or "text")
            block_id = item.get("block_id", item_position)
            anchor_id = f"page-{page_index}:block-{block_id}"
            anchors.append(
                ProviderAnchor(
                    anchor_id,
                    page_index,
                    _kind_alias(label),
                    tuple(map(float, box)),
                    str(item.get("block_content") or ""),
                    order,
                    anchor_id,
                    _provider_confidence(item),
                )
            )
            order += 1
    return str(payload.get("source") or "paddleocr-vl"), anchors, []


def match_provider_anchors(
    oracle_nodes: Sequence[Mapping[str, Any]],
    provider_anchors: Sequence[ProviderAnchor],
    *,
    minimum_score: float = 0.45,
) -> dict[str, dict[str, Any]]:
    """Match text many-to-one and graphical anchors globally one-to-one."""

    assignments: dict[str, dict[str, Any]] = {}
    graphical_oracles: list[tuple[str, tuple[float, float, float, float], str]] = []
    for oracle in oracle_nodes:
        oracle_id = str(oracle.get("id"))
        box = oracle.get("box")
        if not oracle_id or not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        oracle_box = tuple(map(float, box))
        oracle_kind = _kind_alias(str(oracle.get("type") or "text"))
        if oracle_kind in {"figure", "table"}:
            graphical_oracles.append((oracle_id, oracle_box, oracle_kind))
            continue
        best: tuple[float, float, ProviderAnchor, float] | None = None
        for provider in provider_anchors:
            if not _compatible_kinds(oracle_kind, provider.kind):
                continue
            score, oracle_coverage, provider_coverage = _anchor_match_metrics(
                oracle_box,
                provider.bbox,
            )
            ranking = (score, oracle_coverage, provider, provider_coverage)
            if best is None or ranking[:2] > best[:2]:
                best = ranking
        if best is None or best[0] < minimum_score:
            continue
        score, oracle_coverage, provider, provider_coverage = best
        assignments[oracle_id] = _anchor_assignment(
            provider,
            oracle_box,
            score,
            oracle_coverage,
            provider_coverage,
        )

    graphical_providers = list(
        {
            provider.id: provider
            for provider in provider_anchors
            if provider.kind in {"figure", "table"}
        }.values()
    )
    score_matrix: list[list[float | None]] = []
    metric_matrix: list[list[tuple[float, float, float] | None]] = []
    for _, oracle_box, oracle_kind in graphical_oracles:
        score_row: list[float | None] = []
        metric_row: list[tuple[float, float, float] | None] = []
        for provider in graphical_providers:
            if not _compatible_kinds(oracle_kind, provider.kind):
                score_row.append(None)
                metric_row.append(None)
                continue
            metrics = _anchor_match_metrics(oracle_box, provider.bbox)
            score_row.append(metrics[0])
            metric_row.append(metrics)
        score_matrix.append(score_row)
        metric_matrix.append(metric_row)
    for match in maximum_weight_bipartite_matching(
        score_matrix,
        minimum_score=minimum_score,
    ):
        oracle_id, oracle_box, _ = graphical_oracles[match.left_index]
        provider = graphical_providers[match.right_index]
        metrics = metric_matrix[match.left_index][match.right_index]
        assert metrics is not None
        score, oracle_coverage, provider_coverage = metrics
        assignments[oracle_id] = _anchor_assignment(
            provider,
            oracle_box,
            score,
            oracle_coverage,
            provider_coverage,
        )
    return assignments


def _anchor_match_metrics(
    oracle_box: tuple[float, float, float, float],
    provider_box: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    oracle_coverage, provider_coverage = _bbox_coverages(oracle_box, provider_box)
    center_score = _center_containment_score(oracle_box, provider_box)
    score = (
        oracle_coverage * 0.65
        + min(1.0, provider_coverage * 4) * 0.20
        + center_score * 0.15
    )
    return score, oracle_coverage, provider_coverage


def _anchor_assignment(
    provider: ProviderAnchor,
    oracle_box: tuple[float, float, float, float],
    score: float,
    oracle_coverage: float,
    provider_coverage: float,
) -> dict[str, Any]:
    return {
        "provider_id": provider.id,
        "provider_kind": provider.kind,
        "score": round(score, 8),
        "oracle_coverage": round(oracle_coverage, 8),
        "provider_coverage": round(provider_coverage, 8),
        "provider_confidence": provider.confidence,
        "oracle_box": [round(value, 8) for value in oracle_box],
    }


def _normalize_docling(
    payload: Mapping[str, Any],
) -> tuple[str, list[ProviderAnchor], list[tuple[str, str]]]:
    page_heights = {
        int(value.get("page_no", int(key))): float(value.get("size", {}).get("height", 0))
        for key, value in payload.get("pages", {}).items()
        if isinstance(value, Mapping)
    }
    index: dict[str, Mapping[str, Any]] = {}
    for collection in ("texts", "pictures", "tables", "groups", "key_value_items", "form_items"):
        for item in payload.get(collection, []):
            if isinstance(item, Mapping) and item.get("self_ref"):
                index[str(item["self_ref"])] = item
    anchors_by_id: dict[str, ProviderAnchor] = {}
    explicit: list[tuple[str, str]] = []
    order = 0

    def emit(ref: str, *, forced_order: int | None = None) -> None:
        nonlocal order
        if ref in anchors_by_id:
            return
        item = index.get(ref)
        if item is None:
            return
        anchor = _docling_anchor(item, page_heights, order=forced_order if forced_order is not None else order)
        if anchor is not None:
            anchors_by_id[ref] = anchor
            if forced_order is None:
                order += 1
        for caption_ref in _docling_refs(item.get("captions")):
            emit(caption_ref)
            if anchor is not None and anchor.kind == "table":
                explicit.append((caption_ref, ref))
            elif anchor is not None:
                explicit.append((ref, caption_ref))
        for child_ref in _docling_refs(item.get("children")):
            emit(child_ref)

    body = payload.get("body")
    if isinstance(body, Mapping):
        for ref in _docling_refs(body.get("children")):
            emit(ref)
    for ref in index:
        emit(ref, forced_order=None)
    return str(payload.get("source") or "docling"), list(anchors_by_id.values()), explicit


def _docling_anchor(
    item: Mapping[str, Any],
    page_heights: Mapping[int, float],
    *,
    order: int,
) -> ProviderAnchor | None:
    prov = next((value for value in item.get("prov", []) if isinstance(value, Mapping)), None)
    if prov is None or not isinstance(prov.get("bbox"), Mapping):
        return None
    page_no = int(prov.get("page_no", 1))
    page_height = page_heights.get(page_no)
    if not page_height:
        return None
    bbox = prov["bbox"]
    left, top, right, bottom = map(float, (bbox["l"], bbox["t"], bbox["r"], bbox["b"]))
    origin = str(bbox.get("coord_origin") or "BOTTOMLEFT").upper()
    normalized_box = (
        (left, page_height - top, right, page_height - bottom)
        if origin == "BOTTOMLEFT"
        else (left, top, right, bottom)
    )
    ref = str(item.get("self_ref") or "")
    collection = ref.split("/")[1] if ref.startswith("#/") and len(ref.split("/")) > 2 else ""
    label = str(item.get("label") or "")
    kind = "figure" if collection == "pictures" else "table" if collection == "tables" else _kind_alias(label)
    return ProviderAnchor(
        ref,
        page_no - 1,
        kind,
        normalized_box,
        str(item.get("text") or item.get("orig") or ""),
        order,
        ref,
        _provider_confidence(item),
    )


def _normalize_roor_style(
    payload: Mapping[str, Any],
) -> tuple[str, list[ProviderAnchor], list[tuple[str, str]]]:
    anchors = []
    for order, item in enumerate(payload["document"]):
        if not isinstance(item, Mapping) or not isinstance(item.get("box"), (list, tuple)):
            continue
        anchors.append(
            ProviderAnchor(
                str(item.get("id", order)),
                0,
                _kind_alias(str(item.get("type") or "text")),
                tuple(map(float, item["box"])),
                str(item.get("text") or ""),
                order,
                str(item.get("block_id", item.get("id", order))),
                _provider_confidence(item),
            )
        )
    return str(payload.get("source") or "roor-style"), anchors, _edge_pairs(payload.get("successor_edges"))


def _normalize_page_elements(
    payload: Mapping[str, Any],
) -> tuple[str, list[ProviderAnchor], list[tuple[str, str]]]:
    anchors: list[ProviderAnchor] = []
    explicit: list[tuple[str, str]] = []
    order = 0
    for page_position, page in enumerate(payload["pages"]):
        if not isinstance(page, Mapping):
            continue
        page_index = int(page.get("page_index", page_position))
        for item in page.get("elements", []):
            if not isinstance(item, Mapping):
                continue
            box = item.get("bbox_pdf") or item.get("box") or item.get("bbox")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            anchor_id = str(item.get("id", f"page-{page_index}-anchor-{order}"))
            if "provider_order" in item:
                raw_order = item.get("provider_order")
                try:
                    provider_order = int(raw_order) if raw_order is not None else None
                except (TypeError, ValueError, OverflowError):
                    provider_order = None
            else:
                provider_order = order
            anchors.append(
                ProviderAnchor(
                    anchor_id,
                    page_index,
                    _kind_alias(str(item.get("block_label") or item.get("type") or "text")),
                    tuple(map(float, box)),
                    str(item.get("block_content") or item.get("text") or ""),
                    provider_order,
                    str(item.get("block_id", item.get("group_id", anchor_id))),
                    _provider_confidence(item),
                )
            )
            order += 1
        explicit.extend(_edge_pairs(page.get("successor_edges")))
    return str(payload.get("source") or "page-elements"), anchors, explicit


def _serialized_provider_edges(
    anchors: Sequence[ProviderAnchor],
    assignments: Mapping[str, Mapping[str, Any]],
) -> set[tuple[str, str]]:
    return _serialized_provider_edge_groups(anchors, assignments)["all"]


def _serialized_provider_edge_groups(
    anchors: Sequence[ProviderAnchor],
    assignments: Mapping[str, Mapping[str, Any]],
) -> dict[str, set[tuple[str, str]]]:
    """Separate geometry-local edges from model anchor transitions.

    A provider paragraph often owns many oracle text lines. Sorting those lines
    inside one matched anchor tests segmentation-local geometry, while crossing
    from one provider anchor to the next tests the provider's actual block
    order. Keeping both under one score can hide weak inter-block ordering.
    """

    oracle_by_provider: dict[str, list[str]] = defaultdict(list)
    for oracle_id, match in assignments.items():
        oracle_by_provider[str(match["provider_id"])].append(oracle_id)
    ordered = sorted(
        (anchor for anchor in anchors if anchor.order is not None),
        key=lambda item: (item.page_index, item.order, item.id),
    )
    within_anchor: set[tuple[str, str]] = set()
    between_anchors: set[tuple[str, str]] = set()
    direct_between_anchors: set[tuple[str, str]] = set()
    anchors_by_page: dict[int, list[tuple[ProviderAnchor, list[str]]]] = defaultdict(list)
    for anchor in ordered:
        oracle_ids = sorted(
            oracle_by_provider.get(anchor.id, []),
            key=lambda oracle_id: _assignment_geometry_key(assignments[oracle_id]),
        )
        anchors_by_page[anchor.page_index].append((anchor, oracle_ids))
        within_anchor.update(
            (source, target)
            for source, target in zip(oracle_ids, oracle_ids[1:], strict=False)
            if source != target
        )

    for page_anchors in anchors_by_page.values():
        matched_groups = [oracle_ids for _anchor, oracle_ids in page_anchors if oracle_ids]
        between_anchors.update(
            (source_ids[-1], target_ids[0])
            for source_ids, target_ids in zip(
                matched_groups,
                matched_groups[1:],
                strict=False,
            )
            if source_ids[-1] != target_ids[0]
        )
        direct_between_anchors.update(
            (source_ids[-1], target_ids[0])
            for (_source_anchor, source_ids), (_target_anchor, target_ids) in zip(
                page_anchors,
                page_anchors[1:],
                strict=False,
            )
            if source_ids and target_ids and source_ids[-1] != target_ids[0]
        )

    return {
        "all": within_anchor | between_anchors,
        "within_anchor": within_anchor,
        "between_anchors": between_anchors,
        "direct_between_anchors": direct_between_anchors,
    }


def _provider_transition_review(
    anchors: Sequence[ProviderAnchor],
    assignments: Mapping[str, Mapping[str, Any]],
    oracle_nodes: Sequence[Mapping[str, Any]],
    *,
    width: float,
    height: float,
    truth: set[tuple[str, str]],
) -> dict[str, Any]:
    """Score answer-free support gates without promoting them to runtime order."""

    candidate_edges = _native_candidate_direct_edges(
        oracle_nodes,
        width=width,
        height=height,
    )
    transitions = _mapped_direct_provider_transitions(anchors, assignments)
    transition_records: list[dict[str, Any]] = []
    for transition in transitions:
        edge = (transition["source"], transition["target"])
        supporting_candidates = [
            name
            for name in PROVIDER_TRANSITION_CANDIDATES
            if edge in candidate_edges[name]
        ]
        endpoint_confidences = [
            confidence
            for confidence in (
                transition["source_provider_confidence"],
                transition["target_provider_confidence"],
            )
            if confidence is not None
        ]
        minimum_confidence = (
            round(min(endpoint_confidences), 8)
            if len(endpoint_confidences) == 2
            else None
        )
        transition_records.append(
            {
                **transition,
                "minimum_provider_confidence": minimum_confidence,
                "native_support_count": len(supporting_candidates),
                "native_supporting_candidates": supporting_candidates,
                "correct": edge in truth,
            }
        )

    direct_edges = {
        (record["source"], record["target"])
        for record in transition_records
    }
    curve: list[dict[str, Any]] = []
    for minimum_support in PROVIDER_TRANSITION_SUPPORT_THRESHOLDS:
        for minimum_confidence in PROVIDER_TRANSITION_CONFIDENCE_THRESHOLDS:
            eligible = {
                (record["source"], record["target"])
                for record in transition_records
                if int(record["native_support_count"]) >= minimum_support
                and (
                    minimum_confidence is None
                    or (
                        record["minimum_provider_confidence"] is not None
                        and float(record["minimum_provider_confidence"])
                        >= minimum_confidence
                    )
                )
            }
            curve.append(
                {
                    "minimum_native_support": minimum_support,
                    "minimum_provider_confidence": minimum_confidence,
                    "eligible_fraction": _ratio(len(eligible), len(direct_edges)),
                    "precision_wilson_lower_95": _wilson_lower_bound(
                        len(eligible & truth),
                        len(eligible),
                    ),
                    **_relation_metrics(eligible, truth),
                }
            )

    support_histogram = Counter(
        int(record["native_support_count"])
        for record in transition_records
    )
    return {
        "schema": "scriptorium-provider-transition-review/v1",
        "policy": {
            "status": "review-only",
            "runtime_reorder": False,
            "selection_uses_semantic_labels": False,
            "evaluation_uses_semantic_labels": True,
            "confidence": "minimum-detection-confidence-of-provider-transition-endpoints",
            "support": "exact-direct-successor-votes-from-answer-free-native-candidates",
            "unknown_confidence_policy": "eligible-only-when-minimum-provider-confidence-is-null",
        },
        "candidate_orders": list(PROVIDER_TRANSITION_CANDIDATES),
        "candidate_direct_edge_counts": {
            name: len(candidate_edges[name])
            for name in PROVIDER_TRANSITION_CANDIDATES
        },
        "direct_transition_count": len(direct_edges),
        "confidence_available_transition_count": sum(
            record["minimum_provider_confidence"] is not None
            for record in transition_records
        ),
        "support_histogram": {
            str(support): support_histogram.get(support, 0)
            for support in PROVIDER_TRANSITION_SUPPORT_THRESHOLDS
        },
        "curve": curve,
        "transitions": transition_records,
    }


def _native_candidate_direct_edges(
    oracle_nodes: Sequence[Mapping[str, Any]],
    *,
    width: float,
    height: float,
) -> dict[str, set[tuple[str, str]]]:
    from .geometry import reading_order_key
    from .models import BBox
    from .reading_order import infer_box_flow_order, infer_relation_graph_order

    node_ids: list[str] = []
    bboxes: list[BBox] = []
    for node in oracle_nodes:
        node_id = str(node.get("id") or "")
        box = node.get("box")
        if not node_id or not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            bbox = BBox.from_any(box)
        except (TypeError, ValueError):
            continue
        node_ids.append(node_id)
        bboxes.append(bbox)
    if not bboxes or width <= 0 or height <= 0:
        return {name: set() for name in PROVIDER_TRANSITION_CANDIDATES}

    visual_order = sorted(
        range(len(bboxes)),
        key=lambda index: reading_order_key(bboxes[index]),
    )
    candidate_orders = {
        "visual-yx": visual_order,
        "box-flow": infer_box_flow_order(bboxes, width, height),
        "relation-graph": infer_relation_graph_order(bboxes, width, height),
    }
    return {
        name: {
            (node_ids[source], node_ids[target])
            for source, target in zip(order, order[1:], strict=False)
            if source != target
        }
        for name, order in candidate_orders.items()
    }


def _mapped_direct_provider_transitions(
    anchors: Sequence[ProviderAnchor],
    assignments: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    oracle_by_provider: dict[str, list[str]] = defaultdict(list)
    for oracle_id, match in assignments.items():
        oracle_by_provider[str(match["provider_id"])].append(oracle_id)
    anchors_by_page: dict[int, list[tuple[ProviderAnchor, list[str]]]] = defaultdict(list)
    for anchor in sorted(
        (item for item in anchors if item.order is not None),
        key=lambda item: (item.page_index, item.order, item.id),
    ):
        oracle_ids = sorted(
            oracle_by_provider.get(anchor.id, []),
            key=lambda oracle_id: _assignment_geometry_key(assignments[oracle_id]),
        )
        anchors_by_page[anchor.page_index].append((anchor, oracle_ids))

    transitions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for page_index, page_anchors in sorted(anchors_by_page.items()):
        transition_count = max(0, len(page_anchors) - 1)
        for transition_index, ((source_anchor, source_ids), (target_anchor, target_ids)) in enumerate(
            zip(page_anchors, page_anchors[1:], strict=False)
        ):
            if not source_ids or not target_ids:
                continue
            edge = (source_ids[-1], target_ids[0])
            if edge[0] == edge[1] or edge in seen:
                continue
            seen.add(edge)
            transitions.append(
                {
                    "page_index": page_index,
                    "transition_index": transition_index,
                    "page_transition_count": transition_count,
                    "source": edge[0],
                    "target": edge[1],
                    "source_provider_id": source_anchor.id,
                    "target_provider_id": target_anchor.id,
                    "source_provider_order": source_anchor.order,
                    "target_provider_order": target_anchor.order,
                    "source_provider_confidence": source_anchor.confidence,
                    "target_provider_confidence": target_anchor.confidence,
                }
            )
    return transitions


def _sum_provider_transition_reviews(
    reviews: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not reviews:
        raise ValueError("provider transition review aggregation requires at least one case")
    first = reviews[0]
    direct_transition_count = sum(
        int(review.get("direct_transition_count", 0))
        for review in reviews
    )
    curve_by_key = {
        (
            int(point["minimum_native_support"]),
            point.get("minimum_provider_confidence"),
        ): []
        for point in first.get("curve", [])
        if isinstance(point, Mapping)
    }
    for review in reviews:
        for point in review.get("curve", []):
            if not isinstance(point, Mapping):
                continue
            key = (
                int(point["minimum_native_support"]),
                point.get("minimum_provider_confidence"),
            )
            if key not in curve_by_key:
                raise ValueError("provider transition review curves use different grids")
            curve_by_key[key].append(point)
    curve = []
    for point in first.get("curve", []):
        if not isinstance(point, Mapping):
            continue
        key = (
            int(point["minimum_native_support"]),
            point.get("minimum_provider_confidence"),
        )
        metrics = _sum_relation_metrics(curve_by_key[key])
        curve.append(
            {
                "minimum_native_support": key[0],
                "minimum_provider_confidence": key[1],
                "eligible_fraction": _ratio(
                    int(metrics["predicted"]),
                    direct_transition_count,
                ),
                "precision_wilson_lower_95": _wilson_lower_bound(
                    int(metrics["correct"]),
                    int(metrics["predicted"]),
                ),
                **metrics,
            }
        )
    return {
        "schema": "scriptorium-provider-transition-review-suite/v1",
        "policy": first.get("policy"),
        "candidate_orders": first.get("candidate_orders"),
        "case_count": len(reviews),
        "candidate_direct_edge_counts": {
            name: sum(
                int(review.get("candidate_direct_edge_counts", {}).get(name, 0))
                for review in reviews
            )
            for name in PROVIDER_TRANSITION_CANDIDATES
        },
        "direct_transition_count": direct_transition_count,
        "confidence_available_transition_count": sum(
            int(review.get("confidence_available_transition_count", 0))
            for review in reviews
        ),
        "support_histogram": {
            str(support): sum(
                int(review.get("support_histogram", {}).get(str(support), 0))
                for review in reviews
            )
            for support in PROVIDER_TRANSITION_SUPPORT_THRESHOLDS
        },
        "curve": curve,
    }


def _mapped_explicit_relations(
    relations: Sequence[tuple[str, str]],
    assignments: Mapping[str, Mapping[str, Any]],
) -> set[tuple[str, str]]:
    oracle_by_provider: dict[str, list[str]] = defaultdict(list)
    for oracle_id, match in assignments.items():
        oracle_by_provider[str(match["provider_id"])].append(oracle_id)
    result: set[tuple[str, str]] = set()
    for source, target in relations:
        source_ids = sorted(
            oracle_by_provider.get(source, []),
            key=lambda oracle_id: _assignment_geometry_key(assignments[oracle_id]),
        )
        target_ids = sorted(
            oracle_by_provider.get(target, []),
            key=lambda oracle_id: _assignment_geometry_key(assignments[oracle_id]),
        )
        if source_ids and target_ids:
            result.add((source_ids[-1], target_ids[0]))
    return result


def _assignment_geometry_key(match: Mapping[str, Any]) -> tuple[float, float]:
    box = match.get("oracle_box", [0, 0, 0, 0])
    return float(box[1]), float(box[0])


def _graphical_relation_audit(
    oracle: Mapping[str, Any],
    oracle_nodes: Sequence[Mapping[str, Any]],
    truth: set[tuple[str, str]],
    predictions: Mapping[str, set[tuple[str, str]]],
) -> dict[str, Any]:
    """Compare official float labels with an answer-free local geometry diagnostic."""

    from .relation_ranker import _structure_role_successors

    nodes = {str(node.get("id")): node for node in oracle_nodes}
    graphical_ids = {
        node_id
        for node_id, node in nodes.items()
        if _kind_alias(str(node.get("type") or "text")) in {"figure", "table"}
    }
    image = oracle.get("img", {})
    width = float(image.get("width") or 0)
    height = float(image.get("height") or 0)
    geometry_relations: set[tuple[str, str]] = set()
    if width > 0 and height > 0:
        geometry_relations = {
            (str(source), str(target))
            for source, (target, _) in _structure_role_successors(
                oracle_nodes,
                width=width,
                height=height,
            ).items()
        }
    oracle_graphical = {
        edge for edge in truth if edge[0] in graphical_ids or edge[1] in graphical_ids
    }
    geometry_graphical = {
        edge
        for edge in geometry_relations
        if edge[0] in graphical_ids or edge[1] in graphical_ids
    }
    oracle_by_graphical = _relations_by_graphical(oracle_graphical, graphical_ids)
    geometry_by_graphical = _relations_by_graphical(geometry_graphical, graphical_ids)
    conflicts: list[dict[str, Any]] = []
    conflicting_labels = 0
    for graphical_id in sorted(set(oracle_by_graphical) & set(geometry_by_graphical)):
        oracle_edges = oracle_by_graphical[graphical_id]
        geometry_edges = geometry_by_graphical[graphical_id]
        conflicting_edges = oracle_edges - geometry_edges
        if not conflicting_edges:
            continue
        conflicting_labels += len(conflicting_edges)
        conflicts.append(
            {
                "graphical_id": graphical_id,
                "graphical_kind": _kind_alias(
                    str(nodes.get(graphical_id, {}).get("type") or "text")
                ),
                "oracle_edges": [list(edge) for edge in sorted(conflicting_edges)],
                "geometry_edges": [list(edge) for edge in sorted(geometry_edges)],
            }
        )
    exact_agreement = oracle_graphical & geometry_graphical
    oracle_without_geometry = {
        edge
        for graphical_id, edges in oracle_by_graphical.items()
        if graphical_id not in geometry_by_graphical
        for edge in edges
    }
    geometry_without_oracle = {
        edge
        for graphical_id, edges in geometry_by_graphical.items()
        if graphical_id not in oracle_by_graphical
        for edge in edges
    }
    report = {
        "reference_policy": "answer-free-local-geometry-diagnostic-not-ground-truth",
        "oracle_graphical_label_count": len(oracle_graphical),
        "geometry_proposal_count": len(geometry_graphical),
        "exact_agreement_count": len(exact_agreement),
        "conflicting_label_count": conflicting_labels,
        "oracle_without_geometry_count": len(oracle_without_geometry),
        "geometry_without_oracle_count": len(geometry_without_oracle),
        "oracle_geometry_exact_agreement": _ratio(
            len(exact_agreement),
            len(oracle_graphical),
        ),
        "oracle_geometry_conflict_rate": _ratio(
            conflicting_labels,
            len(oracle_graphical),
        ),
        "conflicts": conflicts,
        "provider_geometry_agreement": {},
    }
    report["provider_geometry_agreement"] = {
        name: _relation_metrics(
            {
                edge
                for edge in predicted
                if edge[0] in graphical_ids or edge[1] in graphical_ids
            },
            geometry_graphical,
        )
        for name, predicted in predictions.items()
    }
    return report


def _relations_by_graphical(
    relations: set[tuple[str, str]],
    graphical_ids: set[str],
) -> dict[str, set[tuple[str, str]]]:
    grouped: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for edge in relations:
        for endpoint in edge:
            if endpoint in graphical_ids:
                grouped[endpoint].add(edge)
    return grouped


def _docling_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item["$ref"]) for item in value if isinstance(item, Mapping) and item.get("$ref")]


def _edge_pairs(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    result = []
    for edge in value:
        if isinstance(edge, Mapping) and "source" in edge and "target" in edge:
            result.append((str(edge["source"]), str(edge["target"])))
        elif isinstance(edge, (list, tuple)) and len(edge) >= 2:
            result.append((str(edge[0]), str(edge[1])))
    return result


def _kind_alias(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"picture", "image", "fig", "figure", "chart"}:
        return "figure"
    if "table" in normalized:
        return "table"
    if "caption" in normalized or normalized in {
        "figure-title",
        "image-title",
        "table-title",
        "chart-title",
    }:
        return "caption"
    return "text"


def _compatible_kinds(oracle: str, provider: str) -> bool:
    if oracle == "figure":
        return provider == "figure"
    if oracle == "table":
        return provider == "table"
    return provider in {"text", "caption"}


def _bbox_coverages(
    oracle: tuple[float, float, float, float],
    provider: tuple[float, float, float, float],
) -> tuple[float, float]:
    intersection_width = max(0.0, min(oracle[2], provider[2]) - max(oracle[0], provider[0]))
    intersection_height = max(0.0, min(oracle[3], provider[3]) - max(oracle[1], provider[1]))
    intersection = intersection_width * intersection_height
    oracle_area = max((oracle[2] - oracle[0]) * (oracle[3] - oracle[1]), 1.0)
    provider_area = max((provider[2] - provider[0]) * (provider[3] - provider[1]), 1.0)
    return intersection / oracle_area, intersection / provider_area


def _center_containment_score(
    oracle: tuple[float, float, float, float],
    provider: tuple[float, float, float, float],
) -> float:
    center_x = (oracle[0] + oracle[2]) / 2
    center_y = (oracle[1] + oracle[3]) / 2
    return float(provider[0] <= center_x <= provider[2] and provider[1] <= center_y <= provider[3])


def _relation_metrics(predicted: set[tuple[str, str]], truth: set[tuple[str, str]]) -> dict[str, Any]:
    correct = len(predicted & truth)
    precision = _ratio(correct, len(predicted))
    recall = _ratio(correct, len(truth))
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "correct": correct,
        "predicted": len(predicted),
        "labels": len(truth),
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
    }


def _anchor_kind_metrics(
    oracle_nodes: Sequence[Mapping[str, Any]],
    provider_anchors: Sequence[ProviderAnchor],
    matched_oracle_ids: set[str],
    matched_provider_ids: set[str],
) -> dict[str, dict[str, Any]]:
    oracle_counts = Counter(_kind_alias(str(node.get("type") or "text")) for node in oracle_nodes)
    matched_oracle_counts = Counter(
        _kind_alias(str(node.get("type") or "text"))
        for node in oracle_nodes
        if str(node.get("id")) in matched_oracle_ids
    )
    provider_counts = Counter(anchor.kind for anchor in provider_anchors)
    matched_provider_counts = Counter(
        anchor.kind for anchor in provider_anchors if anchor.id in matched_provider_ids
    )
    return {
        kind: {
            "oracle": oracle_counts[kind],
            "matched_oracle": matched_oracle_counts[kind],
            "oracle_recall": _ratio(matched_oracle_counts[kind], oracle_counts[kind]),
            "provider": provider_counts[kind],
            "matched_provider": matched_provider_counts[kind],
            "provider_match_rate": _ratio(matched_provider_counts[kind], provider_counts[kind]),
        }
        for kind in sorted(set(oracle_counts) | set(provider_counts))
    }


def _sum_anchor_kind_metrics(metrics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    oracle = sum(int(item.get("oracle", 0)) for item in metrics)
    matched_oracle = sum(int(item.get("matched_oracle", 0)) for item in metrics)
    provider = sum(int(item.get("provider", 0)) for item in metrics)
    matched_provider = sum(int(item.get("matched_provider", 0)) for item in metrics)
    return {
        "oracle": oracle,
        "matched_oracle": matched_oracle,
        "oracle_recall": _ratio(matched_oracle, oracle),
        "provider": provider,
        "matched_provider": matched_provider,
        "provider_match_rate": _ratio(matched_provider, provider),
    }


def _provider_case_subset_summary(
    cases: Sequence[Mapping[str, Any]],
    *,
    relation_keys: Sequence[str],
) -> dict[str, Any]:
    oracle_total = sum(int(case["oracle_anchor_count"]) for case in cases)
    oracle_matched = sum(int(case["matched_oracle_anchor_count"]) for case in cases)
    provider_total = sum(int(case["provider_anchor_count"]) for case in cases)
    provider_matched = sum(int(case["matched_provider_anchor_count"]) for case in cases)
    kind_names = sorted(
        {
            kind
            for case in cases
            for kind in case.get("anchor_kinds", {})
        }
    )
    return {
        "case_count": len(cases),
        "sample_ids": [str(case["sample_id"]) for case in cases],
        "layout_strata": dict(
            sorted(Counter(str(case["layout_stratum"]) for case in cases).items())
        ),
        "oracle_anchor_count": oracle_total,
        "matched_oracle_anchor_count": oracle_matched,
        "oracle_anchor_recall": _ratio(oracle_matched, oracle_total),
        "provider_anchor_count": provider_total,
        "matched_provider_anchor_count": provider_matched,
        "provider_anchor_match_rate": _ratio(provider_matched, provider_total),
        "anchor_kinds": {
            kind: _sum_anchor_kind_metrics(
                [case.get("anchor_kinds", {}).get(kind, {}) for case in cases]
            )
            for kind in kind_names
        },
        "relations": {
            key: _sum_relation_metrics([case["relations"][key] for case in cases])
            for key in relation_keys
        },
        "provider_degradation": aggregate_provider_degradation(
            [case["provider_degradation"] for case in cases]
        ),
        "provider_transition_review": _sum_provider_transition_reviews(
            [case["provider_transition_review"] for case in cases]
        ),
    }


def _sum_relation_metrics(metrics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    correct = sum(int(item["correct"]) for item in metrics)
    predicted = sum(int(item["predicted"]) for item in metrics)
    labels = sum(int(item["labels"]) for item in metrics)
    precision = _ratio(correct, predicted)
    recall = _ratio(correct, labels)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "correct": correct,
        "predicted": predicted,
        "labels": labels,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 8) if denominator else 0.0


def _wilson_lower_bound(
    successes: int,
    trials: int,
    *,
    z: float = 1.959963984540054,
) -> float:
    if trials <= 0:
        return 0.0
    proportion = successes / trials
    z_squared = z * z
    denominator = 1 + z_squared / trials
    center = proportion + z_squared / (2 * trials)
    adjustment = z * math.sqrt(
        (proportion * (1 - proportion) + z_squared / (4 * trials)) / trials
    )
    return round((center - adjustment) / denominator, 8)
