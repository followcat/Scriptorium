from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hierarchical_order import build_hierarchical_order_proposal


HIERARCHY_CORPUS_SCHEMA = "scriptorium-hierarchical-order-corpus/v1"
HIERARCHY_INPUT_SCHEMA = "scriptorium-hierarchical-order-benchmark-input/v1"
HIERARCHY_LABEL_SCHEMA = "scriptorium-hierarchical-order-benchmark-labels/v1"
HIERARCHY_BENCHMARK_SCHEMA = "scriptorium-hierarchical-order-benchmark-report/v1"
RELATION_DIAGNOSTIC_COUNTERS = (
    "relation_base_continuity_membership_count",
    "fine_relation_selected_edge_count",
    "fine_relation_cross_region_edge_count",
    "fine_relation_boundary_aligned_edge_count",
    "fine_relation_nonboundary_evidence_count",
    "fine_relation_tied_cross_region_edge_count",
    "fine_relation_region_cycle_suppressed_count",
    "fine_relation_region_degree_suppressed_count",
    "emitted_cross_region_transition_count",
)
SUPPORTED_COMPHRDOC_SOURCE_SCHEMAS = frozenset(
    {
        "scriptorium-comphrdoc-provider-calibration/v1",
        "scriptorium-comphrdoc-provider-test/v1",
    }
)


@dataclass(frozen=True)
class HierarchyCorpusMaterializationResult:
    out_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class HierarchicalOrderBenchmarkResult:
    report_path: Path
    proposals_dir: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class _MaterializedInput:
    sample: Mapping[str, Any]
    input_path: Path
    input_payload: dict[str, Any]
    source_node_fingerprints: dict[str, str]
    source_to_element_id: dict[str, str]
    element_region_ids: dict[str, str]


def materialize_comphrdoc_hierarchy_corpus(
    source_corpus: str | Path,
    out_dir: str | Path,
) -> HierarchyCorpusMaterializationResult:
    """Create answer-separated hierarchy inputs from oracle Comp-HRDoc layout.

    The source ``block_id`` is used only by this materialization step to create
    coarse oracle regions and label files. It is never copied into inference
    input, and semantic relations are opened only after every input is written.
    """

    source_root = Path(source_corpus).resolve()
    source_manifest_path = source_root / "comphrdoc_benchmark_manifest.json"
    source_manifest = _json_object(source_manifest_path, label="source manifest")
    source_schema = str(source_manifest.get("schema") or "")
    if source_schema not in SUPPORTED_COMPHRDOC_SOURCE_SCHEMAS:
        raise ValueError("unsupported Comp-HRDoc source corpus schema")
    source_samples = source_manifest.get("samples")
    if not isinstance(source_samples, list) or not source_samples:
        raise ValueError("source corpus manifest must contain samples")

    target = Path(out_dir)
    inputs_dir = target / "inputs"
    labels_dir = target / "labels"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    materialized_inputs: list[_MaterializedInput] = []
    seen_sample_ids: set[str] = set()
    # Phase one deliberately never opens semantic sidecars.
    for raw_sample in source_samples:
        if not isinstance(raw_sample, Mapping):
            raise ValueError("source corpus samples must be objects")
        sample_id = str(raw_sample.get("id") or "").strip()
        if not sample_id or sample_id in seen_sample_ids:
            raise ValueError("source corpus sample ids must be non-empty and unique")
        seen_sample_ids.add(sample_id)
        structure_path = _confined_source_path(
            source_root,
            raw_sample.get("structure"),
            label=f"sample {sample_id} structure",
        )
        structure = _json_object(structure_path, label=f"sample {sample_id} structure")
        (
            input_payload,
            source_node_fingerprints,
            source_to_element_id,
            element_region_ids,
        ) = _hierarchy_input_from_comphrdoc_structure(
            structure,
            sample_id=sample_id,
            page_index=_nonnegative_int(
                raw_sample.get("page_index", 0),
                "sample page_index",
            ),
        )
        input_path = inputs_dir / f"{_safe_filename(sample_id)}.hierarchy-input.json"
        _write_json(input_path, input_payload)
        materialized_inputs.append(
            _MaterializedInput(
                sample=raw_sample,
                input_path=input_path,
                input_payload=input_payload,
                source_node_fingerprints=source_node_fingerprints,
                source_to_element_id=source_to_element_id,
                element_region_ids=element_region_ids,
            )
        )

    manifest_samples: list[dict[str, Any]] = []
    # Phase two opens labels only after all answer-free inputs exist.
    for materialized in materialized_inputs:
        sample_id = str(materialized.sample["id"])
        semantic_path = _confined_source_path(
            source_root,
            materialized.sample.get("semantic_sidecar"),
            label=f"sample {sample_id} semantic sidecar",
        )
        semantic = _json_object(
            semantic_path,
            label=f"sample {sample_id} semantic sidecar",
        )
        labels = _hierarchy_labels_from_comphrdoc_semantic(
            semantic,
            sample_id=sample_id,
            source_node_fingerprints=materialized.source_node_fingerprints,
            source_to_element_id=materialized.source_to_element_id,
            element_region_ids=materialized.element_region_ids,
        )
        label_path = labels_dir / f"{_safe_filename(sample_id)}.hierarchy-labels.json"
        _write_json(label_path, labels)
        manifest_samples.append(
            {
                "id": sample_id,
                "document_id": materialized.sample.get("document_id"),
                "page_index": materialized.sample.get("page_index"),
                "partition": materialized.sample.get("partition"),
                "layout_stratum": materialized.sample.get("layout_stratum"),
                "input": str(materialized.input_path.relative_to(target)),
                "input_sha256": _file_sha256(materialized.input_path),
                "labels": str(label_path.relative_to(target)),
                "labels_sha256": _file_sha256(label_path),
                "fine_element_count": len(materialized.input_payload["elements"]),
                "coarse_region_count": len(materialized.input_payload["regions"]),
                "within_region_label_count": len(
                    labels["within_region_successor_edges"]
                ),
                "cross_region_label_count": len(
                    labels["cross_region_transition_edges"]
                ),
            }
        )

    manifest = {
        "schema": HIERARCHY_CORPUS_SCHEMA,
        "source_dataset": source_manifest.get("dataset"),
        "source_schema": source_schema,
        "source_manifest_sha256": _file_sha256(source_manifest_path),
        "source_annotation_archive_sha256": source_manifest.get(
            "annotation_archive_sha256"
        ),
        "source_selection": source_manifest.get("selection"),
        "source_split_policy": source_manifest.get("split_policy"),
        "selection_uses_relation_labels": False,
        "selection_uses_oracle_layout": True,
        "oracle_layout_policy": (
            "source block membership constructs coarse regions during "
            "materialization; member ids are removed from inference input"
        ),
        "inference_inputs_are_answer_free": True,
        "answer_separation": {
            "input": "fine element and coarse region id/text/role/bbox only",
            "labels": (
                "element-region membership plus within/cross successor relations"
            ),
            "provider_sequence_values_in_input": False,
            "provider_relation_values_in_input": False,
            "prediction_reads_all_inputs_before_labels": True,
        },
        "sample_count": len(manifest_samples),
        "partition_counts": dict(
            sorted(
                Counter(
                    str(sample.get("partition") or "unknown")
                    for sample in manifest_samples
                ).items()
            )
        ),
        "layout_stratum_counts": dict(
            sorted(
                Counter(
                    str(sample.get("layout_stratum") or "unknown")
                    for sample in manifest_samples
                ).items()
            )
        ),
        "samples": manifest_samples,
    }
    manifest_path = target / "hierarchical_order_corpus_manifest.json"
    _write_json(manifest_path, manifest)
    return HierarchyCorpusMaterializationResult(target, manifest_path, manifest)


def benchmark_hierarchical_order_corpus(
    corpus_dir: str | Path,
    *,
    output: str | Path | None = None,
    proposals_dir: str | Path | None = None,
    chunkr_model: str | Path | None = None,
) -> HierarchicalOrderBenchmarkResult:
    """Score hierarchy membership and local/cross relations in two phases."""

    corpus = Path(corpus_dir).resolve()
    manifest_path = corpus / "hierarchical_order_corpus_manifest.json"
    manifest = _json_object(manifest_path, label="hierarchy corpus manifest")
    if manifest.get("schema") != HIERARCHY_CORPUS_SCHEMA:
        raise ValueError("unsupported hierarchy corpus schema")
    if manifest.get("inference_inputs_are_answer_free") is not True:
        raise ValueError("hierarchy corpus must declare answer-free inference inputs")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("hierarchy corpus manifest must contain samples")

    report_path = (
        Path(output)
        if output is not None
        else corpus / "hierarchical_order_benchmark_report.json"
    )
    proposal_root = (
        Path(proposals_dir)
        if proposals_dir is not None
        else report_path.parent / f"{report_path.stem}.proposals"
    )
    proposal_root.mkdir(parents=True, exist_ok=True)

    predictions: list[tuple[Mapping[str, Any], dict[str, Any], Path]] = []
    seen_ids: set[str] = set()
    # Phase one predicts every page before any label path is resolved or read.
    for raw_sample in samples:
        if not isinstance(raw_sample, Mapping):
            raise ValueError("hierarchy corpus samples must be objects")
        sample_id = str(raw_sample.get("id") or "").strip()
        if not sample_id or sample_id in seen_ids:
            raise ValueError("hierarchy corpus sample ids must be non-empty and unique")
        seen_ids.add(sample_id)
        input_path = _confined_source_path(
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
        prediction = build_hierarchical_order_proposal(
            input_payload,
            chunkr_model=chunkr_model,
        ).payload
        proposal_path = proposal_root / f"{_safe_filename(sample_id)}.proposal.json"
        _write_json(proposal_path, prediction)
        predictions.append((raw_sample, prediction, proposal_path))

    aggregate = _empty_benchmark_totals()
    grouped: dict[str, dict[str, dict[str, Counter[str]]]] = {
        "partition": {},
        "layout_stratum": {},
    }
    page_results: list[dict[str, Any]] = []
    # Phase two scores immutable predictions against independently hashed labels.
    for raw_sample, prediction, proposal_path in predictions:
        sample_id = str(raw_sample["id"])
        label_path = _confined_source_path(
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
        if labels.get("schema") != HIERARCHY_LABEL_SCHEMA:
            raise ValueError(f"sample {sample_id} has an unsupported label schema")
        page_counts, page_metrics = _score_hierarchy_page(prediction, labels)
        _accumulate_benchmark_totals(aggregate, page_counts)
        for group_name in grouped:
            group_value = str(raw_sample.get(group_name) or "unknown")
            totals = grouped[group_name].setdefault(
                group_value,
                _empty_benchmark_totals(),
            )
            _accumulate_benchmark_totals(totals, page_counts)
        diagnostics = prediction.get("diagnostics")
        page_results.append(
            {
                "id": sample_id,
                "partition": raw_sample.get("partition"),
                "layout_stratum": raw_sample.get("layout_stratum"),
                "proposal": str(proposal_path),
                "proposal_sha256": _file_sha256(proposal_path),
                "metrics": page_metrics,
                "diagnostics": (
                    dict(diagnostics) if isinstance(diagnostics, Mapping) else {}
                ),
            }
        )

    summary = _summarize_benchmark_totals(aggregate)
    report = {
        "schema": HIERARCHY_BENCHMARK_SCHEMA,
        "corpus_manifest": str(manifest_path),
        "corpus_manifest_sha256": _file_sha256(manifest_path),
        "sample_count": len(page_results),
        "prediction_policy": (
            "hierarchical-review-only-relation-dag-with-continuity-membership-v2"
        ),
        "coarse_order_model": (
            "chunkr-pairwise-ranker"
            if chunkr_model is not None
            else "fine-relation-graph-boundary"
        ),
        "transition_representation": (
            "partial-dag-boundary-aligned-review-relations"
        ),
        "runtime_reorder": False,
        "labels_opened_after_all_predictions": True,
        "metric_policy": {
            "membership": "complete element-to-oracle-region labels",
            "within_region": "complete direct successors inside each oracle block",
            "cross_region": (
                "partial Comp-HRDoc labels; precision uses endpoint-aware scorable "
                "predictions and reports unscored count"
            ),
            "region_transition": "partial source-region to target-region relations",
        },
        "summary": summary,
        "groups": {
            group_name: {
                group_value: _summarize_benchmark_totals(totals)
                for group_value, totals in sorted(values.items())
            }
            for group_name, values in grouped.items()
        },
        "diagnostic_totals": _aggregate_relation_diagnostics(page_results),
        "promotion_decision": "development-benchmark-only-review-only",
        "pages": page_results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(report_path, report)
    return HierarchicalOrderBenchmarkResult(report_path, proposal_root, report)


def _aggregate_relation_diagnostics(
    page_results: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    totals = {
        name: sum(
            int(page.get("diagnostics", {}).get(name) or 0)
            for page in page_results
        )
        for name in RELATION_DIAGNOSTIC_COUNTERS
    }
    totals["missing_cross_region_evidence_page_count"] = sum(
        bool(
            page.get("diagnostics", {}).get(
                "candidate_expansion_suppressed_missing_cross_region_evidence"
            )
        )
        for page in page_results
    )
    return totals


def _hierarchy_input_from_comphrdoc_structure(
    structure: Mapping[str, Any],
    *,
    sample_id: str,
    page_index: int = 0,
) -> tuple[
    dict[str, Any],
    dict[str, str],
    dict[str, str],
    dict[str, str],
]:
    image = structure.get("img")
    if not isinstance(image, Mapping):
        raise ValueError(f"sample {sample_id} structure requires img metadata")
    width = _positive_number(image.get("width"), "image width")
    height = _positive_number(image.get("height"), "image height")
    raw_nodes = structure.get("document")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError(f"sample {sample_id} structure requires document nodes")

    parsed_nodes: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    base_id_counts: Counter[str] = Counter()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, Mapping):
            raise ValueError(f"sample {sample_id} document nodes must be objects")
        source_id = str(raw_node.get("id") or "").strip()
        block_id = str(raw_node.get("block_id") or "").strip()
        text = str(raw_node.get("text") or "").strip()
        role = str(raw_node.get("type") or "text").strip() or "text"
        box = _valid_box(raw_node.get("box"), width=width, height=height)
        if not source_id or source_id in source_ids:
            raise ValueError(f"sample {sample_id} source node ids must be unique")
        if not block_id or not text:
            raise ValueError(
                f"sample {sample_id} nodes require block_id and non-empty text"
            )
        source_ids.add(source_id)
        identity = {"box": box, "text": text, "role": role}
        base_digest = _canonical_sha256(identity)[:20]
        base_id_counts[base_digest] += 1
        parsed_nodes.append(
            {
                "source_id": source_id,
                "source_block_id": block_id,
                "box": box,
                "text": text,
                "role": role,
                "base_digest": base_digest,
                "source_fingerprint": _canonical_sha256(
                    {
                        "id": source_id,
                        "block_id": block_id,
                        **identity,
                    }
                ),
            }
        )

    source_to_element_id: dict[str, str] = {}
    source_node_fingerprints: dict[str, str] = {}
    for node in parsed_nodes:
        digest = str(node["base_digest"])
        if base_id_counts[digest] > 1:
            digest = _canonical_sha256(
                {
                    "base_digest": digest,
                    "source_disambiguator": node["source_id"],
                }
            )[:20]
        element_id = f"element-{digest}"
        if element_id in source_to_element_id.values():
            raise ValueError(f"sample {sample_id} generated duplicate element ids")
        source_to_element_id[str(node["source_id"])] = element_id
        source_node_fingerprints[str(node["source_id"])] = str(
            node["source_fingerprint"]
        )
        node["element_id"] = element_id

    nodes_by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in parsed_nodes:
        nodes_by_block[str(node["source_block_id"])].append(node)
    source_block_to_region_id: dict[str, str] = {}
    regions: list[dict[str, Any]] = []
    for source_block_id, members in nodes_by_block.items():
        member_ids = sorted(str(member["element_id"]) for member in members)
        box = _union_boxes([member["box"] for member in members])
        role = _block_role(members)
        text = " ".join(
            str(member["text"])
            for member in sorted(members, key=lambda member: str(member["element_id"]))
        )
        region_id = "region-" + _canonical_sha256(
            {"box": box, "role": role, "member_fingerprints": member_ids}
        )[:20]
        if region_id in source_block_to_region_id.values():
            raise ValueError(f"sample {sample_id} generated duplicate region ids")
        source_block_to_region_id[source_block_id] = region_id
        regions.append(
            {
                "id": region_id,
                "box": box,
                "role": role,
                "text": text,
            }
        )

    elements = [
        {
            "id": str(node["element_id"]),
            "box": node["box"],
            "role": node["role"],
            "text": node["text"],
        }
        for node in parsed_nodes
    ]
    elements.sort(key=lambda item: _canonical_sha256(item))
    regions.sort(key=lambda item: _canonical_sha256(item))
    element_region_ids = {
        str(node["element_id"]): source_block_to_region_id[
            str(node["source_block_id"])
        ]
        for node in parsed_nodes
    }
    input_payload = {
        "schema": HIERARCHY_INPUT_SCHEMA,
        "id": sample_id,
        "page_index": page_index,
        "width": width,
        "height": height,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "oracle_region_policy": (
            "coarse geometry/text materialized from source blocks; membership removed"
        ),
        "elements": elements,
        "regions": regions,
    }
    return (
        input_payload,
        source_node_fingerprints,
        source_to_element_id,
        element_region_ids,
    )


def _hierarchy_labels_from_comphrdoc_semantic(
    semantic: Mapping[str, Any],
    *,
    sample_id: str,
    source_node_fingerprints: Mapping[str, str],
    source_to_element_id: Mapping[str, str],
    element_region_ids: Mapping[str, str],
) -> dict[str, Any]:
    raw_nodes = semantic.get("document")
    if not isinstance(raw_nodes, list):
        raise ValueError(f"sample {sample_id} labels require document nodes")
    semantic_fingerprints: dict[str, str] = {}
    for raw_node in raw_nodes:
        if not isinstance(raw_node, Mapping):
            raise ValueError(f"sample {sample_id} label nodes must be objects")
        source_id = str(raw_node.get("id") or "").strip()
        block_id = str(raw_node.get("block_id") or "").strip()
        semantic_fingerprints[source_id] = _canonical_sha256(
            {
                "id": source_id,
                "block_id": block_id,
                "box": _box_values(raw_node.get("box")),
                "text": str(raw_node.get("text") or "").strip(),
                "role": str(raw_node.get("type") or "text").strip() or "text",
            }
        )
    if semantic_fingerprints != dict(source_node_fingerprints):
        raise ValueError(
            f"sample {sample_id} structure and semantic node fingerprints differ"
        )

    raw_edges = semantic.get("ro_linkings")
    if not isinstance(raw_edges, list):
        raise ValueError(f"sample {sample_id} labels require ro_linkings")
    within_edges: set[tuple[str, str, str]] = set()
    cross_edges: set[tuple[str, str, str, str]] = set()
    for raw_edge in raw_edges:
        if (
            not isinstance(raw_edge, Sequence)
            or isinstance(raw_edge, (str, bytes))
            or len(raw_edge) != 2
        ):
            raise ValueError(f"sample {sample_id} relation edges must be pairs")
        source_ref = str(raw_edge[0])
        target_ref = str(raw_edge[1])
        try:
            source = source_to_element_id[source_ref]
            target = source_to_element_id[target_ref]
            source_region = element_region_ids[source]
            target_region = element_region_ids[target]
        except KeyError as exc:
            raise ValueError(
                f"sample {sample_id} relation references an unknown node"
            ) from exc
        if source_region == target_region:
            within_edges.add((source, target, source_region))
        else:
            cross_edges.add((source, target, source_region, target_region))

    return {
        "schema": HIERARCHY_LABEL_SCHEMA,
        "id": sample_id,
        "membership_policy": "complete oracle block membership",
        "within_region_policy": "complete annotation-local line succession",
        "cross_region_policy": "partial Comp-HRDoc reading-order relations",
        "memberships": [
            {"element_id": element_id, "region_id": region_id}
            for element_id, region_id in sorted(element_region_ids.items())
        ],
        "within_region_successor_edges": [
            {"source": source, "target": target, "region_id": region_id}
            for source, target, region_id in sorted(within_edges)
        ],
        "cross_region_transition_edges": [
            {
                "source": source,
                "target": target,
                "source_region_id": source_region,
                "target_region_id": target_region,
            }
            for source, target, source_region, target_region in sorted(cross_edges)
        ],
    }


def _score_hierarchy_page(
    prediction: Mapping[str, Any],
    labels: Mapping[str, Any],
) -> tuple[dict[str, Counter[str]], dict[str, Any]]:
    truth_membership = {
        str(item["element_id"]): str(item["region_id"])
        for item in labels.get("memberships", [])
        if isinstance(item, Mapping)
    }
    if not truth_membership:
        raise ValueError("hierarchy labels require memberships")
    predicted_membership = {
        str(item["element_id"]): str(item["region_id"])
        for item in prediction.get("memberships", [])
        if isinstance(item, Mapping) and item.get("region_id") is not None
    }
    membership_counts = Counter(
        {
            "correct": sum(
                predicted_membership.get(element_id) == region_id
                for element_id, region_id in truth_membership.items()
            ),
            "predicted": sum(
                element_id in predicted_membership for element_id in truth_membership
            ),
            "labels": len(truth_membership),
        }
    )

    truth_within = {
        (str(item["source"]), str(item["target"]))
        for item in labels.get("within_region_successor_edges", [])
        if isinstance(item, Mapping)
    }
    truth_cross = {
        (str(item["source"]), str(item["target"]))
        for item in labels.get("cross_region_transition_edges", [])
        if isinstance(item, Mapping)
    }
    truth_region_cross = {
        (str(item["source_region_id"]), str(item["target_region_id"]))
        for item in labels.get("cross_region_transition_edges", [])
        if isinstance(item, Mapping)
    }

    page = next(
        (
            page
            for page in prediction.get("pages", [])
            if isinstance(page, Mapping)
        ),
        {},
    )
    hierarchy_within = {
        (str(edge["source"]), str(edge["target"]))
        for stream in page.get("reading_streams", [])
        if isinstance(stream, Mapping)
        for key in ("successor_edges", "review_successor_edges")
        for edge in stream.get(key, [])
        if isinstance(edge, Mapping)
    }
    hierarchy_cross = {
        (str(edge["source"]), str(edge["target"]))
        for edge in page.get("review_transitions", [])
        if isinstance(edge, Mapping)
    }
    hierarchy_region_cross = {
        (str(edge["source_region_id"]), str(edge["target_region_id"]))
        for edge in page.get("review_transitions", [])
        if isinstance(edge, Mapping)
    }

    base_ids = [str(value) for value in prediction.get("base_ordered_element_ids", [])]
    base_edges = set(zip(base_ids, base_ids[1:], strict=False))
    flat_within = {
        edge
        for edge in base_edges
        if truth_membership.get(edge[0]) == truth_membership.get(edge[1])
    }
    flat_cross = base_edges - flat_within
    flat_region_cross = {
        (truth_membership[source], truth_membership[target])
        for source, target in flat_cross
        if source in truth_membership and target in truth_membership
    }

    counts = {
        "membership": membership_counts,
        "hierarchy_within": _complete_edge_counts(hierarchy_within, truth_within),
        "flat_within": _complete_edge_counts(flat_within, truth_within),
        "hierarchy_cross": _partial_edge_counts(hierarchy_cross, truth_cross),
        "flat_cross": _partial_edge_counts(flat_cross, truth_cross),
        "hierarchy_region_cross": _partial_edge_counts(
            hierarchy_region_cross,
            truth_region_cross,
        ),
        "flat_region_cross": _partial_edge_counts(
            flat_region_cross,
            truth_region_cross,
        ),
    }
    return counts, {
        name: _summarize_counts(value, membership=name == "membership")
        for name, value in counts.items()
    }


def _complete_edge_counts(
    predicted: set[tuple[str, str]],
    truth: set[tuple[str, str]],
) -> Counter[str]:
    return Counter(
        {
            "correct": len(predicted & truth),
            "predicted": len(predicted),
            "scorable": len(predicted),
            "unscored": 0,
            "labels": len(truth),
        }
    )


def _partial_edge_counts(
    predicted: set[tuple[str, str]],
    truth: set[tuple[str, str]],
) -> Counter[str]:
    endpoint_universe = {endpoint for edge in truth for endpoint in edge}
    scorable = {
        edge
        for edge in predicted
        if edge[0] in endpoint_universe and edge[1] in endpoint_universe
    }
    return Counter(
        {
            "correct": len(scorable & truth),
            "predicted": len(predicted),
            "scorable": len(scorable),
            "unscored": len(predicted - scorable),
            "labels": len(truth),
        }
    )


def _empty_benchmark_totals() -> dict[str, Counter[str]]:
    return {
        name: Counter()
        for name in (
            "membership",
            "hierarchy_within",
            "flat_within",
            "hierarchy_cross",
            "flat_cross",
            "hierarchy_region_cross",
            "flat_region_cross",
        )
    }


def _accumulate_benchmark_totals(
    totals: dict[str, Counter[str]],
    page_counts: Mapping[str, Counter[str]],
) -> None:
    for name, counts in page_counts.items():
        totals[name].update(counts)


def _summarize_benchmark_totals(
    totals: Mapping[str, Counter[str]],
) -> dict[str, Any]:
    return {
        name: _summarize_counts(counts, membership=name == "membership")
        for name, counts in totals.items()
    }


def _summarize_counts(
    counts: Mapping[str, int],
    *,
    membership: bool,
) -> dict[str, int | float]:
    correct = int(counts.get("correct", 0))
    predicted = int(counts.get("predicted", 0))
    labels = int(counts.get("labels", 0))
    scorable = predicted if membership else int(counts.get("scorable", predicted))
    unscored = 0 if membership else int(counts.get("unscored", 0))
    precision = correct / scorable if scorable else 0.0
    recall = correct / labels if labels else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    summary: dict[str, int | float] = {
        "correct": correct,
        "predicted": predicted,
        "labels": labels,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
    }
    if membership:
        summary["coverage"] = round(predicted / labels, 8) if labels else 0.0
        summary["wrong"] = predicted - correct
        summary["unassigned"] = labels - predicted
    else:
        summary["scorable"] = scorable
        summary["unscored"] = unscored
        summary["scorable_fraction"] = (
            round(scorable / predicted, 8) if predicted else 0.0
        )
    return summary


def _block_role(members: Sequence[Mapping[str, Any]]) -> str:
    roles = [str(member.get("role") or "text") for member in members]
    for preferred in ("table", "figure"):
        if preferred in roles:
            return preferred
    counts = Counter(roles)
    return sorted(counts, key=lambda role: (-counts[role], role))[0]


def _union_boxes(boxes: Sequence[Sequence[float]]) -> list[float]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _valid_box(value: Any, *, width: float, height: float) -> list[float]:
    box = _box_values(value)
    if (
        box[0] < 0
        or box[1] < 0
        or box[2] > width
        or box[3] > height
        or box[2] <= box[0]
        or box[3] <= box[1]
    ):
        raise ValueError("node box must be non-empty and inside the page")
    return box


def _box_values(value: Any) -> list[float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 4
    ):
        raise ValueError("node box must contain x0, y0, x1, y1")
    try:
        box = [float(item) for item in value]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("node box must contain finite numbers") from exc
    if not all(math.isfinite(item) for item in box):
        raise ValueError("node box must contain finite numbers")
    return box


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
        raise ValueError(f"{label} must be non-negative") from exc
    if number < 0:
        raise ValueError(f"{label} must be non-negative")
    return number


def _confined_source_path(root: Path, value: Any, *, label: str) -> Path:
    relative = str(value or "").strip()
    if not relative:
        raise ValueError(f"{label} path is required")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside its corpus") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} is missing: {resolved}")
    return resolved


def _verify_file_hash(path: Path, expected: Any, *, label: str) -> None:
    normalized = str(expected or "").strip().lower()
    if len(normalized) != 64 or _file_sha256(path) != normalized:
        raise ValueError(f"{label} SHA-256 mismatch")


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be an object")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _safe_filename(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in {"-", ".", "_"} else "_"
        for character in value
    ).strip("._")
    if not normalized:
        raise ValueError("sample id cannot produce an empty filename")
    return normalized


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
