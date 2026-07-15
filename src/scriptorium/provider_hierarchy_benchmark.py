from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from .hierarchical_order import MAX_HIERARCHY_REGIONS, build_hierarchical_order_proposal
from .hierarchical_order_benchmark import (
    HIERARCHY_CORPUS_SCHEMA,
    HIERARCHY_INPUT_SCHEMA,
    HIERARCHY_LABEL_SCHEMA,
    RELATION_DIAGNOSTIC_COUNTERS,
    _relation_ranker_input_from_hierarchy,
)
from .provider_anchor_benchmark import ProviderAnchor, normalize_provider_anchors


PROVIDER_HIERARCHY_CORPUS_SCHEMA = (
    "scriptorium-provider-derived-hierarchy-corpus/v1"
)
PROVIDER_HIERARCHY_LABEL_SCHEMA = (
    "scriptorium-provider-derived-hierarchy-labels/v1"
)
PROVIDER_HIERARCHY_BENCHMARK_SCHEMA = (
    "scriptorium-provider-derived-hierarchy-benchmark/v1"
)
PROVIDER_HIERARCHY_ADAPTER_SCHEMA = (
    "scriptorium-provider-derived-hierarchy-input/v1"
)
SUPPORTED_PROVIDER_RUN_SCHEMA = "scriptorium-paddle-layout-corpus-run/v1"
DEFAULT_PROVIDER_MIN_GEOMETRY_COVERAGE = 0.1
DEFAULT_PROVIDER_MIN_GEOMETRY_MARGIN = 0.1


@dataclass(frozen=True)
class ProviderHierarchyCorpusResult:
    out_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class ProviderHierarchyBenchmarkResult:
    report_path: Path
    proposals_dir: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class _MaterializedProviderInput:
    sample: Mapping[str, Any]
    input_path: Path
    input_payload: dict[str, Any]
    provider_path: Path
    provider_sha256: str
    provider_name: str
    provider_region_count: int


def materialize_provider_hierarchy_corpus(
    source_hierarchy_corpus: str | Path,
    provider_dir: str | Path,
    out_dir: str | Path,
    *,
    provider_manifest: str | Path | None = None,
) -> ProviderHierarchyCorpusResult:
    """Replace oracle coarse regions with answer-free provider blocks.

    Every source input and provider output is materialized before any source
    hierarchy label is resolved or read.
    """

    source_root = Path(source_hierarchy_corpus).resolve()
    source_manifest_path = source_root / "hierarchical_order_corpus_manifest.json"
    source_manifest = _json_object(source_manifest_path, label="source manifest")
    if source_manifest.get("schema") != HIERARCHY_CORPUS_SCHEMA:
        raise ValueError("unsupported source hierarchy corpus schema")
    if source_manifest.get("inference_inputs_are_answer_free") is not True:
        raise ValueError("source hierarchy corpus must declare answer-free inputs")
    samples = source_manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("source hierarchy corpus must contain samples")

    provider_root = Path(provider_dir).resolve()
    if not provider_root.is_dir():
        raise ValueError("provider directory does not exist")
    provider_manifest_path, provider_run = _load_provider_run_manifest(
        provider_root,
        provider_manifest,
    )
    if provider_run is not None:
        _validate_provider_run(
            provider_run,
            source_manifest=source_manifest,
            sample_ids={str(sample.get("id") or "") for sample in samples},
        )

    target = Path(out_dir)
    inputs_dir = target / "inputs"
    labels_dir = target / "labels"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    materialized: list[_MaterializedProviderInput] = []
    seen_ids: set[str] = set()
    provider_names: set[str] = set()
    # Phase one deliberately does not resolve a source label path.
    for raw_sample in samples:
        if not isinstance(raw_sample, Mapping):
            raise ValueError("source hierarchy samples must be objects")
        sample_id = str(raw_sample.get("id") or "").strip()
        if not sample_id or sample_id in seen_ids:
            raise ValueError("source hierarchy sample ids must be non-empty and unique")
        seen_ids.add(sample_id)
        source_input_path = _confined_path(
            source_root,
            raw_sample.get("input"),
            label=f"sample {sample_id} source input",
        )
        _verify_file_hash(
            source_input_path,
            raw_sample.get("input_sha256"),
            label=f"sample {sample_id} source input",
        )
        source_input = _json_object(
            source_input_path,
            label=f"sample {sample_id} source input",
        )
        if source_input.get("schema") != HIERARCHY_INPUT_SCHEMA:
            raise ValueError(f"sample {sample_id} has an unsupported source input")
        provider_path = _provider_path_for_sample(provider_root, sample_id)
        provider_payload = _json_object(
            provider_path,
            label=f"sample {sample_id} provider output",
        )
        input_payload, provider_name, provider_region_count = (
            _provider_hierarchy_input(
                source_input,
                provider_payload,
                sample_id=sample_id,
                page_index=_nonnegative_int(
                    raw_sample.get("page_index", source_input.get("page_index", 0)),
                    f"sample {sample_id} page_index",
                ),
                source_input_sha256=_file_sha256(source_input_path),
                provider_sha256=_file_sha256(provider_path),
                provider_manifest_sha256=(
                    _file_sha256(provider_manifest_path)
                    if provider_manifest_path is not None
                    else None
                ),
            )
        )
        provider_names.add(provider_name)
        input_path = inputs_dir / f"{_safe_filename(sample_id)}.provider-input.json"
        _write_json(input_path, input_payload)
        materialized.append(
            _MaterializedProviderInput(
                sample=raw_sample,
                input_path=input_path,
                input_payload=input_payload,
                provider_path=provider_path,
                provider_sha256=_file_sha256(provider_path),
                provider_name=provider_name,
                provider_region_count=provider_region_count,
            )
        )
    if len(provider_names) != 1:
        raise ValueError("provider corpus must use one normalized provider")

    manifest_samples: list[dict[str, Any]] = []
    # Phase two resolves and reads labels only after every input is written.
    for item in materialized:
        sample_id = str(item.sample["id"])
        source_label_path = _confined_path(
            source_root,
            item.sample.get("labels"),
            label=f"sample {sample_id} source labels",
        )
        _verify_file_hash(
            source_label_path,
            item.sample.get("labels_sha256"),
            label=f"sample {sample_id} source labels",
        )
        source_labels = _json_object(
            source_label_path,
            label=f"sample {sample_id} source labels",
        )
        labels = _provider_hierarchy_labels(
            source_labels,
            input_payload=item.input_payload,
            sample_id=sample_id,
        )
        label_path = labels_dir / f"{_safe_filename(sample_id)}.provider-labels.json"
        _write_json(label_path, labels)
        manifest_samples.append(
            {
                "id": sample_id,
                "document_id": item.sample.get("document_id"),
                "page_index": item.sample.get("page_index"),
                "partition": item.sample.get("partition"),
                "layout_stratum": item.sample.get("layout_stratum"),
                "input": str(item.input_path.relative_to(target)),
                "input_sha256": _file_sha256(item.input_path),
                "labels": str(label_path.relative_to(target)),
                "labels_sha256": _file_sha256(label_path),
                "provider_output": str(item.provider_path),
                "provider_output_sha256": item.provider_sha256,
                "fine_element_count": len(item.input_payload["elements"]),
                "provider_region_count": item.provider_region_count,
                "successor_label_count": len(labels["successor_edges"]),
            }
        )

    provider_name = next(iter(provider_names))
    manifest = {
        "schema": PROVIDER_HIERARCHY_CORPUS_SCHEMA,
        "source_dataset": source_manifest.get("source_dataset"),
        "source_schema": source_manifest.get("source_schema"),
        "source_hierarchy_manifest": str(source_manifest_path),
        "source_hierarchy_manifest_sha256": _file_sha256(source_manifest_path),
        "source_corpus_manifest_sha256": source_manifest.get(
            "source_manifest_sha256"
        ),
        "provider": provider_name,
        "provider_root": str(provider_root),
        "provider_manifest": (
            str(provider_manifest_path) if provider_manifest_path is not None else None
        ),
        "provider_manifest_sha256": (
            _file_sha256(provider_manifest_path)
            if provider_manifest_path is not None
            else None
        ),
        "sample_count": len(manifest_samples),
        "partition_counts": dict(
            sorted(
                Counter(
                    str(sample.get("partition") or "unknown")
                    for sample in manifest_samples
                ).items()
            )
        ),
        "inference_inputs_are_answer_free": True,
        "selection_uses_relation_labels": False,
        "answer_separation": {
            "input": "fine text/geometry plus provider block geometry/role only",
            "labels": "oracle co-membership and complete published successor sidecar",
            "provider_sequence_values_in_input": False,
            "provider_relation_values_in_input": False,
            "prediction_reads_all_inputs_before_labels": True,
        },
        "samples": manifest_samples,
    }
    manifest_path = target / "provider_hierarchy_corpus_manifest.json"
    _write_json(manifest_path, manifest)
    return ProviderHierarchyCorpusResult(target, manifest_path, manifest)


def benchmark_provider_hierarchy_corpus(
    corpus_dir: str | Path,
    *,
    output: str | Path | None = None,
    proposals_dir: str | Path | None = None,
    relation_model_path: str | Path | None = None,
    semantic_scorer: Any | None = None,
    partition: str | None = None,
    min_geometry_coverage: float = DEFAULT_PROVIDER_MIN_GEOMETRY_COVERAGE,
    min_geometry_margin: float = DEFAULT_PROVIDER_MIN_GEOMETRY_MARGIN,
) -> ProviderHierarchyBenchmarkResult:
    """Score line successors after provider segmentation in two phases."""

    corpus = Path(corpus_dir).resolve()
    manifest_path = corpus / "provider_hierarchy_corpus_manifest.json"
    manifest = _json_object(manifest_path, label="provider hierarchy manifest")
    if manifest.get("schema") != PROVIDER_HIERARCHY_CORPUS_SCHEMA:
        raise ValueError("unsupported provider hierarchy corpus schema")
    if manifest.get("inference_inputs_are_answer_free") is not True:
        raise ValueError("provider hierarchy corpus must declare answer-free inputs")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("provider hierarchy corpus must contain samples")
    normalized_partition = str(partition or "").strip() or None
    selected_samples = [
        sample
        for sample in samples
        if normalized_partition is None
        or str(sample.get("partition") or "") == normalized_partition
    ]
    if not selected_samples:
        raise ValueError("provider hierarchy partition selected no samples")

    report_path = (
        Path(output)
        if output is not None
        else corpus / "provider_hierarchy_benchmark_report.json"
    )
    proposal_root = (
        Path(proposals_dir)
        if proposals_dir is not None
        else report_path.parent / f"{report_path.stem}.proposals"
    )
    proposal_root.mkdir(parents=True, exist_ok=True)
    relation_bundle = None
    relation_manifest = None
    if relation_model_path is not None:
        from . import relation_ranker

        relation_bundle, relation_manifest = relation_ranker.load_relation_ranker(
            relation_model_path
        )

    predictions: list[tuple[Mapping[str, Any], dict[str, Any], Path]] = []
    seen_ids: set[str] = set()
    # Phase one predicts every provider-derived input before resolving labels.
    for raw_sample in selected_samples:
        if not isinstance(raw_sample, Mapping):
            raise ValueError("provider hierarchy samples must be objects")
        sample_id = str(raw_sample.get("id") or "").strip()
        if not sample_id or sample_id in seen_ids:
            raise ValueError("provider hierarchy sample ids must be unique")
        seen_ids.add(sample_id)
        input_path = _confined_path(
            corpus,
            raw_sample.get("input"),
            label=f"sample {sample_id} input",
        )
        _verify_file_hash(
            input_path,
            raw_sample.get("input_sha256"),
            label=f"sample {sample_id} input",
        )
        input_payload = _json_object(input_path, label=f"sample {sample_id} input")
        if input_payload.get("schema") != HIERARCHY_INPUT_SCHEMA:
            raise ValueError(f"sample {sample_id} has an unsupported input schema")
        external_successor_edges = None
        if relation_bundle is not None and relation_manifest is not None:
            relation_input = _relation_ranker_input_from_hierarchy(input_payload)
            external_successor_edges = relation_ranker._predict_roor_page_relations(
                relation_input,
                bundle=relation_bundle,
                manifest=relation_manifest,
                structure_role_fusion=False,
                semantic_scorer=semantic_scorer,
            ).structure_payload["successor_edges"]
        prediction = build_hierarchical_order_proposal(
            input_payload,
            external_successor_edges=external_successor_edges,
            min_geometry_coverage=min_geometry_coverage,
            min_geometry_margin=min_geometry_margin,
        ).payload
        proposal_path = proposal_root / f"{_safe_filename(sample_id)}.proposal.json"
        _write_json(proposal_path, prediction)
        predictions.append((raw_sample, prediction, proposal_path))

    aggregate = _empty_totals()
    grouped: dict[str, dict[str, dict[str, Counter[str]]]] = {
        "partition": {},
        "layout_stratum": {},
    }
    pages: list[dict[str, Any]] = []
    # Phase two opens labels only after all provider hierarchy predictions exist.
    for raw_sample, prediction, proposal_path in predictions:
        sample_id = str(raw_sample["id"])
        label_path = _confined_path(
            corpus,
            raw_sample.get("labels"),
            label=f"sample {sample_id} labels",
        )
        _verify_file_hash(
            label_path,
            raw_sample.get("labels_sha256"),
            label=f"sample {sample_id} labels",
        )
        labels = _json_object(label_path, label=f"sample {sample_id} labels")
        if labels.get("schema") != PROVIDER_HIERARCHY_LABEL_SCHEMA:
            raise ValueError(f"sample {sample_id} has unsupported labels")
        counts, metrics = _score_provider_hierarchy_page(prediction, labels)
        _accumulate(aggregate, counts)
        for group_name in grouped:
            value = str(raw_sample.get(group_name) or "unknown")
            totals = grouped[group_name].setdefault(value, _empty_totals())
            _accumulate(totals, counts)
        diagnostics = prediction.get("diagnostics")
        pages.append(
            {
                "id": sample_id,
                "partition": raw_sample.get("partition"),
                "layout_stratum": raw_sample.get("layout_stratum"),
                "proposal": str(proposal_path),
                "proposal_sha256": _file_sha256(proposal_path),
                "metrics": metrics,
                "diagnostics": (
                    dict(diagnostics) if isinstance(diagnostics, Mapping) else {}
                ),
            }
        )

    report = {
        "schema": PROVIDER_HIERARCHY_BENCHMARK_SCHEMA,
        "corpus_manifest": str(manifest_path),
        "corpus_manifest_sha256": _file_sha256(manifest_path),
        "provider": manifest.get("provider"),
        "sample_count": len(pages),
        "source_sample_count": len(samples),
        "partition_filter": normalized_partition,
        "min_geometry_coverage": round(min_geometry_coverage, 8),
        "min_geometry_margin": round(min_geometry_margin, 8),
        "prediction_policy": "provider-block-hierarchy-object-branches-v1",
        "relation_metric_policy": (
            "segmentation-invariant union of local-stream and transition edges; "
            "partial-label endpoint-aware precision"
        ),
        "segmentation_metric_policy": "oracle co-membership pair F1",
        "external_relation_model_sha256": (
            relation_manifest.get("model_sha256")
            if relation_manifest is not None
            else None
        ),
        "external_relation_feature_version": (
            relation_manifest.get("feature_version")
            if relation_manifest is not None
            else None
        ),
        "external_relation_semantic_scorer": (
            relation_manifest.get("semantic_scorer")
            if relation_manifest is not None
            else None
        ),
        "runtime_reorder": False,
        "labels_opened_after_all_predictions": True,
        "summary": _summarize_totals(aggregate),
        "groups": {
            group_name: {
                value: _summarize_totals(totals)
                for value, totals in sorted(values.items())
            }
            for group_name, values in grouped.items()
        },
        "diagnostic_totals": _aggregate_diagnostics(pages),
        "promotion_decision": "provider-derived-development-benchmark-review-only",
        "pages": pages,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(report_path, report)
    return ProviderHierarchyBenchmarkResult(report_path, proposal_root, report)


def _provider_hierarchy_input(
    source_input: Mapping[str, Any],
    provider_payload: Mapping[str, Any],
    *,
    sample_id: str,
    page_index: int,
    source_input_sha256: str,
    provider_sha256: str,
    provider_manifest_sha256: str | None,
) -> tuple[dict[str, Any], str, int]:
    width = _positive_number(source_input.get("width"), "source input width")
    height = _positive_number(source_input.get("height"), "source input height")
    source_page_index = _nonnegative_int(
        source_input.get("page_index", 0),
        "source hierarchy page_index",
    )
    elements = source_input.get("elements")
    if not isinstance(elements, list) or not elements:
        raise ValueError(f"sample {sample_id} source input requires elements")
    provider_name, anchors, _explicit_relations = normalize_provider_anchors(
        provider_payload
    )
    selected, remapped_from = _provider_page_anchors(
        anchors,
        provider_payload,
        sample_id=sample_id,
        page_index=page_index,
    )
    if not selected:
        raise ValueError(f"sample {sample_id} provider output contains no page blocks")
    if len(selected) > MAX_HIERARCHY_REGIONS:
        raise ValueError(
            f"sample {sample_id} provider output exceeds "
            f"{MAX_HIERARCHY_REGIONS} regions"
        )
    regions: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    seen_region_ids: set[str] = set()
    for anchor in selected:
        box = _valid_provider_box(anchor.bbox, width=width, height=height)
        identity = {
            "provider": provider_name,
            "anchor_id": anchor.id,
            "page_index": page_index,
            "kind": anchor.kind,
            "box": box,
            "text": anchor.text,
        }
        region_id = "provider-region-" + _canonical_sha256(identity)[:20]
        if region_id in seen_region_ids:
            raise ValueError(f"sample {sample_id} provider regions must be unique")
        seen_region_ids.add(region_id)
        regions.append(
            {
                "id": region_id,
                "box": box,
                "role": anchor.kind,
                "text": anchor.text,
            }
        )
        provenance.append(
            {
                "region_id": region_id,
                "provider_anchor_id": anchor.id,
                "provider_kind": anchor.kind,
                "provider_confidence": anchor.confidence,
            }
        )
    regions.sort(key=lambda region: str(region["id"]))
    provenance.sort(key=lambda region: str(region["region_id"]))
    input_payload = {
        "schema": HIERARCHY_INPUT_SCHEMA,
        "id": sample_id,
        "page_index": page_index,
        "width": width,
        "height": height,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [dict(element) for element in elements],
        "regions": regions,
        "input_adapter": {
            "schema": PROVIDER_HIERARCHY_ADAPTER_SCHEMA,
            "source_hierarchy_input_sha256": source_input_sha256,
            "source_provider_output_sha256": provider_sha256,
            "source_provider_manifest_sha256": provider_manifest_sha256,
            "provider": provider_name,
            "provider_sequence_policy": "stripped-before-hierarchy-input",
            "provider_relation_policy": "stripped-before-hierarchy-input",
            "source_hierarchy_page_index": source_page_index,
            "provider_page_index": page_index,
            "provider_page_index_remapped_from": remapped_from,
            "provider_regions": provenance,
        },
    }
    return input_payload, provider_name, len(regions)


def _provider_page_anchors(
    anchors: Sequence[ProviderAnchor],
    provider_payload: Mapping[str, Any],
    *,
    sample_id: str,
    page_index: int,
) -> tuple[list[ProviderAnchor], int | None]:
    selected = [anchor for anchor in anchors if anchor.page_index == page_index]
    if selected:
        return selected, None
    page_indices = {anchor.page_index for anchor in anchors}
    corpus_sample = provider_payload.get("corpus_sample")
    declared_sample = (
        str(corpus_sample.get("id") or "")
        if isinstance(corpus_sample, Mapping)
        else ""
    )
    declared_page = (
        corpus_sample.get("page_index")
        if isinstance(corpus_sample, Mapping)
        else None
    )
    if (
        len(page_indices) == 1
        and declared_sample == sample_id
        and _optional_int(declared_page) == page_index
    ):
        source_page = next(iter(page_indices))
        return list(anchors), source_page
    raise ValueError(
        f"sample {sample_id} provider page indices do not contain {page_index}"
    )


def _provider_hierarchy_labels(
    source_labels: Mapping[str, Any],
    *,
    input_payload: Mapping[str, Any],
    sample_id: str,
) -> dict[str, Any]:
    if source_labels.get("schema") != HIERARCHY_LABEL_SCHEMA:
        raise ValueError(f"sample {sample_id} has unsupported source labels")
    memberships = [
        {
            "element_id": str(item.get("element_id") or ""),
            "oracle_region_id": str(item.get("region_id") or ""),
        }
        for item in source_labels.get("memberships", [])
        if isinstance(item, Mapping)
    ]
    element_ids = {
        str(element.get("id") or "")
        for element in input_payload.get("elements", [])
        if isinstance(element, Mapping)
    }
    if not memberships or {item["element_id"] for item in memberships} != element_ids:
        raise ValueError(f"sample {sample_id} membership labels do not match input")
    successor_edges: list[dict[str, str]] = []
    for scope, key in (
        ("within-oracle-region", "within_region_successor_edges"),
        ("cross-oracle-region", "cross_region_transition_edges"),
    ):
        for item in source_labels.get(key, []):
            if not isinstance(item, Mapping):
                continue
            source = str(item.get("source") or "")
            target = str(item.get("target") or "")
            if source not in element_ids or target not in element_ids or source == target:
                raise ValueError(f"sample {sample_id} has invalid successor labels")
            successor_edges.append(
                {"source": source, "target": target, "oracle_scope": scope}
            )
    unique_edges = {
        (edge["source"], edge["target"], edge["oracle_scope"])
        for edge in successor_edges
    }
    if len(unique_edges) != len(successor_edges):
        raise ValueError(f"sample {sample_id} successor labels must be unique")
    return {
        "schema": PROVIDER_HIERARCHY_LABEL_SCHEMA,
        "id": sample_id,
        "membership_policy": "complete oracle co-membership labels",
        "relation_policy": "published Comp-HRDoc immediate successors",
        "memberships": sorted(
            memberships,
            key=lambda item: (item["element_id"], item["oracle_region_id"]),
        ),
        "successor_edges": sorted(
            successor_edges,
            key=lambda edge: (
                edge["source"],
                edge["target"],
                edge["oracle_scope"],
            ),
        ),
    }


def _score_provider_hierarchy_page(
    prediction: Mapping[str, Any],
    labels: Mapping[str, Any],
) -> tuple[dict[str, Counter[str]], dict[str, Any]]:
    truth_edges = {
        (str(edge["source"]), str(edge["target"]))
        for edge in labels.get("successor_edges", [])
        if isinstance(edge, Mapping)
    }
    truth_within = {
        (str(edge["source"]), str(edge["target"]))
        for edge in labels.get("successor_edges", [])
        if isinstance(edge, Mapping)
        and edge.get("oracle_scope") == "within-oracle-region"
    }
    truth_cross = truth_edges - truth_within
    page = next(
        (item for item in prediction.get("pages", []) if isinstance(item, Mapping)),
        {},
    )
    predicted_within = {
        (str(edge["source"]), str(edge["target"]))
        for stream in page.get("reading_streams", [])
        if isinstance(stream, Mapping)
        for key in ("successor_edges", "review_successor_edges")
        for edge in stream.get(key, [])
        if isinstance(edge, Mapping)
    }
    predicted_cross = {
        (str(edge["source"]), str(edge["target"]))
        for edge in page.get("review_transitions", [])
        if isinstance(edge, Mapping)
    }
    predicted_all = predicted_within | predicted_cross
    base_ids = [str(value) for value in prediction.get("base_ordered_element_ids", [])]
    flat_edges = set(zip(base_ids, base_ids[1:], strict=False))

    truth_membership = {
        str(item["element_id"]): str(item["oracle_region_id"])
        for item in labels.get("memberships", [])
        if isinstance(item, Mapping)
    }
    predicted_membership = {
        str(item["element_id"]): str(item["region_id"])
        for item in prediction.get("memberships", [])
        if isinstance(item, Mapping) and item.get("region_id") is not None
    }
    truth_pairs = _co_membership_pairs(truth_membership)
    predicted_pairs = _co_membership_pairs(
        {
            element_id: region_id
            for element_id, region_id in predicted_membership.items()
            if element_id in truth_membership
        }
    )
    counts = {
        "provider_hierarchy_relation": _partial_edge_counts(
            predicted_all,
            truth_edges,
        ),
        "flat_relation": _partial_edge_counts(flat_edges, truth_edges),
        "local_stream_relation": _partial_edge_counts(
            predicted_within,
            truth_edges,
        ),
        "cross_stream_relation": _partial_edge_counts(
            predicted_cross,
            truth_edges,
        ),
        "truth_within_recovery": Counter(
            correct=len(predicted_all & truth_within),
            labels=len(truth_within),
        ),
        "truth_cross_recovery": Counter(
            correct=len(predicted_all & truth_cross),
            labels=len(truth_cross),
        ),
        "segmentation_pairwise": Counter(
            correct=len(predicted_pairs & truth_pairs),
            predicted=len(predicted_pairs),
            labels=len(truth_pairs),
        ),
        "assignment_coverage": Counter(
            predicted=sum(
                element_id in predicted_membership for element_id in truth_membership
            ),
            labels=len(truth_membership),
        ),
    }
    return counts, _summarize_totals(counts)


def _co_membership_pairs(membership: Mapping[str, str]) -> set[tuple[str, str]]:
    members_by_region: dict[str, list[str]] = defaultdict(list)
    for element_id, region_id in membership.items():
        members_by_region[region_id].append(element_id)
    return {
        pair
        for members in members_by_region.values()
        for pair in combinations(sorted(members), 2)
    }


def _partial_edge_counts(
    predicted: set[tuple[str, str]],
    truth: set[tuple[str, str]],
) -> Counter[str]:
    endpoints = {endpoint for edge in truth for endpoint in edge}
    scorable = {
        edge for edge in predicted if edge[0] in endpoints and edge[1] in endpoints
    }
    return Counter(
        correct=len(scorable & truth),
        predicted=len(predicted),
        scorable=len(scorable),
        unscored=len(predicted - scorable),
        labels=len(truth),
    )


def _empty_totals() -> dict[str, Counter[str]]:
    return {
        name: Counter()
        for name in (
            "provider_hierarchy_relation",
            "flat_relation",
            "local_stream_relation",
            "cross_stream_relation",
            "truth_within_recovery",
            "truth_cross_recovery",
            "segmentation_pairwise",
            "assignment_coverage",
        )
    }


def _accumulate(
    totals: dict[str, Counter[str]],
    counts: Mapping[str, Counter[str]],
) -> None:
    for name, values in counts.items():
        totals[name].update(values)


def _summarize_totals(
    totals: Mapping[str, Counter[str]],
) -> dict[str, dict[str, int | float]]:
    result: dict[str, dict[str, int | float]] = {}
    for name, counts in totals.items():
        correct = int(counts.get("correct", 0))
        predicted = int(counts.get("predicted", 0))
        labels = int(counts.get("labels", 0))
        if name == "assignment_coverage":
            result[name] = {
                "assigned": predicted,
                "labels": labels,
                "coverage": _ratio(predicted, labels),
                "unassigned": labels - predicted,
            }
            continue
        if name in {"truth_within_recovery", "truth_cross_recovery"}:
            result[name] = {
                "correct": correct,
                "labels": labels,
                "recall": _ratio(correct, labels),
            }
            continue
        scorable = int(counts.get("scorable", predicted))
        unscored = int(counts.get("unscored", 0))
        precision = correct / scorable if scorable else 0.0
        recall = correct / labels if labels else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        result[name] = {
            "correct": correct,
            "predicted": predicted,
            "labels": labels,
            "precision": round(precision, 8),
            "recall": round(recall, 8),
            "f1": round(f1, 8),
            "scorable": scorable,
            "unscored": unscored,
            "scorable_fraction": _ratio(scorable, predicted),
        }
    return result


def _aggregate_diagnostics(pages: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    names = (
        *RELATION_DIAGNOSTIC_COUNTERS,
        "fine_relation_object_branch_suppressed_count",
        "fine_relation_table_source_suppressed_count",
        "fine_relation_figure_target_suppressed_count",
    )
    return {
        name: sum(
            int(page.get("diagnostics", {}).get(name) or 0) for page in pages
        )
        for name in dict.fromkeys(names)
    }


def _load_provider_run_manifest(
    provider_root: Path,
    explicit: str | Path | None,
) -> tuple[Path | None, dict[str, Any] | None]:
    if explicit is not None:
        path = Path(explicit).resolve()
        if not path.is_file():
            raise ValueError("provider manifest does not exist")
        return path, _json_object(path, label="provider manifest")
    candidate = provider_root / "paddle_layout_corpus_run.json"
    if candidate.is_file():
        return candidate, _json_object(candidate, label="provider manifest")
    return None, None


def _validate_provider_run(
    provider_run: Mapping[str, Any],
    *,
    source_manifest: Mapping[str, Any],
    sample_ids: set[str],
) -> None:
    if provider_run.get("schema") != SUPPORTED_PROVIDER_RUN_SCHEMA:
        raise ValueError("unsupported provider run manifest schema")
    if provider_run.get("corpus_manifest_sha256") != source_manifest.get(
        "source_manifest_sha256"
    ):
        raise ValueError("provider run and hierarchy corpus provenance differ")
    declared_ids = {
        str(value)
        for key in ("generated_sample_ids", "skipped_sample_ids")
        for value in provider_run.get(key, [])
    }
    if declared_ids != sample_ids:
        raise ValueError("provider run sample ids do not match hierarchy corpus")


def _provider_path_for_sample(provider_root: Path, sample_id: str) -> Path:
    name = _safe_filename(sample_id)
    candidates = [
        path
        for path in (
            provider_root / f"{name}.structure.json",
            provider_root / f"{name}.json",
        )
        if path.is_file()
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"sample {sample_id} requires exactly one provider JSON output"
        )
    resolved = candidates[0].resolve()
    if not resolved.is_relative_to(provider_root):
        raise ValueError(f"sample {sample_id} provider path escapes provider root")
    return resolved


def _valid_provider_box(
    value: Sequence[float],
    *,
    width: float,
    height: float,
) -> list[float]:
    if len(value) != 4:
        raise ValueError("provider region box must contain four values")
    try:
        box = [float(item) for item in value]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("provider region box is invalid") from exc
    if (
        not all(math.isfinite(item) for item in box)
        or box[0] < 0
        or box[1] < 0
        or box[2] > width
        or box[3] > height
        or box[2] <= box[0]
        or box[3] <= box[1]
    ):
        raise ValueError("provider region box must be non-empty and inside the page")
    return box


def _confined_path(root: Path, value: Any, *, label: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} path must be non-empty")
    path = (root / raw).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"{label} must be a file inside its corpus")
    return path


def _verify_file_hash(path: Path, expected: Any, *, label: str) -> None:
    value = str(expected or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} SHA-256 is invalid")
    if _file_sha256(path) != value:
        raise ValueError(f"{label} SHA-256 mismatch")


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-") or "sample"


def _positive_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be positive") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if number < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return number


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 8) if denominator else 0.0
