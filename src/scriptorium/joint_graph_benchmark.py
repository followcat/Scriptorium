from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paragraph_graph_benchmark import (
    PARAGRAPH_GRAPH_PROPOSAL_SCHEMA,
    _co_membership_pairs,
    _precision_recall_f1,
)
from .provider_hierarchy_benchmark import (
    PROVIDER_HIERARCHY_CORPUS_SCHEMA,
    PROVIDER_HIERARCHY_LABEL_SCHEMA,
)
from .relation_order import merge_relation_edge_path_cover
from .successor_graph_benchmark import SUCCESSOR_GRAPH_PROPOSAL_SCHEMA


JOINT_GRAPH_BENCHMARK_SCHEMA = "scriptorium-joint-graph-benchmark/v1"
JOINT_GRAPH_PROPOSAL_SCHEMA = "scriptorium-joint-graph-proposal/v1"
JOINT_GRAPH_DECODER_VERSION = "successor-package-geometry-chain-or-protected-v4"


@dataclass(frozen=True)
class JointGraphBenchmarkResult:
    report_path: Path
    proposals_dir: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class _ScoredEdge:
    source: str
    target: str
    score: float
    rank: int | None = None
    top_score_margin: float | None = None


@dataclass(frozen=True)
class _PageProposals:
    sample_id: str
    partition: str
    layout_stratum: str
    document_id: str
    element_ids: tuple[str, ...]
    base_rank: dict[str, int]
    paragraph_membership: dict[str, str]
    paragraph_edges: tuple[_ScoredEdge, ...]
    successor_edges: tuple[_ScoredEdge, ...]
    paragraph_threshold: float
    successor_threshold: float
    paragraph_proposal_path: Path
    successor_proposal_path: Path
    corpus: Path
    labels_relative: str
    labels_sha256: str
    element_boxes: dict[str, tuple[float, float, float, float]] | None = None


@dataclass(frozen=True)
class _DecodedPage:
    membership: dict[str, str]
    selected_edges: frozenset[tuple[str, str]]
    within_selected_edges: frozenset[tuple[str, str]]
    cross_selected_edges: frozenset[tuple[str, str]]
    streams: tuple[tuple[str, ...], ...]
    diagnostics: dict[str, int]
    decoder_mode: str


@dataclass(frozen=True)
class _Labels:
    membership: dict[str, str]
    edges: frozenset[tuple[str, str]]
    scopes: dict[tuple[str, str], str]


def joint_decode_page(
    *,
    element_ids: Sequence[str],
    paragraph_membership: Mapping[str, str],
    successor_edges: Sequence[_ScoredEdge],
    base_rank: Mapping[str, int] | None = None,
    element_boxes: Mapping[str, Sequence[float]] | None = None,
) -> _DecodedPage:
    """Package successor path covers with paragraph hierarchy metadata.

    Prefer a successor-primary policy: when the provided successor edges already
    form a degree-one acyclic path cover, keep them and only use paragraph
    membership for within/cross labeling and hierarchical stream packaging.
    When the paragraph head is over-fragmented, package with successor chains
    and optionally split those chains on column wraps / large gaps when boxes
    are available. Otherwise fall back to paragraph-protected decoding. The
    result remains review-only evidence.
    """

    ids = tuple(str(element_id) for element_id in element_ids)
    if not ids:
        raise ValueError("joint decode requires at least one element")
    membership = {element_id: str(paragraph_membership[element_id]) for element_id in ids}
    rank = {
        element_id: int(base_rank[element_id]) if base_rank is not None else index
        for index, element_id in enumerate(ids)
    }
    boxes = _normalize_element_boxes(element_boxes)

    known_edges: list[_ScoredEdge] = []
    unknown = 0
    for edge in successor_edges:
        if edge.source not in membership or edge.target not in membership:
            unknown += 1
            continue
        known_edges.append(edge)

    within = [
        edge
        for edge in known_edges
        if membership[edge.source] == membership[edge.target]
    ]
    cross = [
        edge
        for edge in known_edges
        if membership[edge.source] != membership[edge.target]
    ]
    edge_pairs = [(edge.source, edge.target) for edge in known_edges]
    if edge_pairs and _is_degree_one_acyclic_path_cover(edge_pairs):
        selected = frozenset((str(source), str(target)) for source, target in edge_pairs)
        package_membership = dict(membership)
        decoder_mode = "successor-path-cover-package"
        geometry_splits = 0
        # Over-fragmented paragraph heads (common OOD) collapse hierarchy packaging.
        # Fall back to successor chains as paragraph components without changing edges.
        if _singleton_rate(package_membership) >= 0.85:
            chains = _edge_chains(ids, selected, rank)
            if boxes:
                refined: list[list[str]] = []
                for chain in chains:
                    parts = _split_chain_by_geometry(chain, boxes)
                    geometry_splits += max(0, len(parts) - 1)
                    refined.extend(parts)
                chains = refined
            package_membership = _membership_from_chains(chains)
            decoder_mode = (
                "successor-path-cover-package-chain-geometry-fallback"
                if geometry_splits
                else "successor-path-cover-package-chain-fallback"
            )
        within_selected = frozenset(
            (source, target)
            for source, target in selected
            if package_membership[source] == package_membership[target]
        )
        cross_selected = selected - within_selected
        streams = tuple(tuple(chain) for chain in _edge_chains(ids, selected, rank))
        return _DecodedPage(
            membership=package_membership,
            selected_edges=selected,
            within_selected_edges=within_selected,
            cross_selected_edges=cross_selected,
            streams=streams,
            decoder_mode=decoder_mode,
            diagnostics={
                "element_count": len(ids),
                "paragraph_component_count": len(set(package_membership.values())),
                "paragraph_singleton_rate_x1000": int(
                    round(_singleton_rate(membership) * 1000)
                ),
                "geometry_chain_split_count": geometry_splits,
                "successor_candidate_count": len(successor_edges),
                "unknown_successor_count": unknown,
                "within_candidate_count": sum(
                    1
                    for source, target in selected
                    if membership[source] == membership[target]
                ),
                "cross_candidate_count": sum(
                    1
                    for source, target in selected
                    if membership[source] != membership[target]
                ),
                "endpoint_cross_candidate_count": 0,
                "within_selected_count": len(within_selected),
                "cross_selected_count": len(cross_selected),
                "selected_edge_count": len(selected),
                "within_outgoing_conflict_rejection_count": 0,
                "within_incoming_conflict_rejection_count": 0,
                "within_cycle_rejection_count": 0,
                "cross_outgoing_conflict_rejection_count": 0,
                "cross_incoming_conflict_rejection_count": 0,
                "cross_cycle_rejection_count": 0,
                "within_chain_count": len(
                    _chain_endpoints(ids, within_selected, rank)[2]
                ),
                "joint_stream_count": len(streams),
            },
        )

    within.sort(
        key=lambda edge: (
            -edge.score,
            rank[edge.source],
            rank[edge.target],
            edge.source,
            edge.target,
        )
    )
    protected = [(edge.source, edge.target) for edge in within]
    protected_merge = merge_relation_edge_path_cover((), protected_edges=protected)
    protected_selected = frozenset(
        (str(source), str(target)) for source, target in protected_merge.selected_edges
    )

    chain_head, chain_tail, within_chains = _chain_endpoints(ids, protected_selected, rank)
    endpoint_cross = [
        edge
        for edge in cross
        if chain_tail.get(edge.source) == edge.source
        and chain_head.get(edge.target) == edge.target
    ]
    endpoint_cross.sort(
        key=lambda edge: (
            -edge.score,
            rank[edge.source],
            rank[edge.target],
            edge.source,
            edge.target,
        )
    )
    cross_candidates = [(edge.source, edge.target) for edge in endpoint_cross]
    joint_merge = merge_relation_edge_path_cover(
        cross_candidates,
        protected_edges=protected_selected,
    )
    selected = frozenset(
        (str(source), str(target)) for source, target in joint_merge.selected_edges
    )
    within_selected = selected & protected_selected
    cross_selected = selected - protected_selected
    streams = tuple(tuple(chain) for chain in _edge_chains(ids, selected, rank))
    return _DecodedPage(
        membership=membership,
        selected_edges=selected,
        within_selected_edges=within_selected,
        cross_selected_edges=cross_selected,
        streams=streams,
        decoder_mode="paragraph-protected-path-cover",
        diagnostics={
            "element_count": len(ids),
            "paragraph_component_count": len(set(membership.values())),
            "successor_candidate_count": len(successor_edges),
            "unknown_successor_count": unknown,
            "within_candidate_count": len(within),
            "cross_candidate_count": len(cross),
            "endpoint_cross_candidate_count": len(endpoint_cross),
            "within_selected_count": len(within_selected),
            "cross_selected_count": len(cross_selected),
            "selected_edge_count": len(selected),
            "within_outgoing_conflict_rejection_count": (
                protected_merge.rejected_outgoing_conflict_count
            ),
            "within_incoming_conflict_rejection_count": (
                protected_merge.rejected_incoming_conflict_count
            ),
            "within_cycle_rejection_count": protected_merge.rejected_cycle_count,
            "cross_outgoing_conflict_rejection_count": (
                joint_merge.rejected_outgoing_conflict_count
                - protected_merge.rejected_outgoing_conflict_count
            ),
            "cross_incoming_conflict_rejection_count": (
                joint_merge.rejected_incoming_conflict_count
                - protected_merge.rejected_incoming_conflict_count
            ),
            "cross_cycle_rejection_count": (
                joint_merge.rejected_cycle_count - protected_merge.rejected_cycle_count
            ),
            "within_chain_count": len(within_chains),
            "joint_stream_count": len(streams),
        },
    )


def _is_degree_one_acyclic_path_cover(edges: Sequence[tuple[str, str]]) -> bool:
    successor: dict[str, str] = {}
    predecessor: dict[str, str] = {}
    for source, target in edges:
        if source == target or source in successor or target in predecessor:
            return False
        successor[source] = target
        predecessor[target] = source
    seen: set[str] = set()
    for start in successor:
        if start in seen:
            continue
        current = start
        path: set[str] = set()
        while current in successor:
            if current in path:
                return False
            path.add(current)
            seen.add(current)
            current = successor[current]
        seen.add(current)
    return True


def _singleton_rate(membership: Mapping[str, str]) -> float:
    if not membership:
        return 1.0
    counts: dict[str, int] = defaultdict(int)
    for group_id in membership.values():
        counts[str(group_id)] += 1
    singletons = sum(1 for size in counts.values() if size == 1)
    return singletons / len(membership)


def _membership_from_chains(chains: Sequence[Sequence[str]]) -> dict[str, str]:
    membership: dict[str, str] = {}
    for index, chain in enumerate(chains, start=1):
        group_id = f"successor-chain-{index:04d}"
        for element_id in chain:
            membership[str(element_id)] = group_id
    return membership


def _normalize_element_boxes(
    element_boxes: Mapping[str, Sequence[float]] | None,
) -> dict[str, tuple[float, float, float, float]] | None:
    if not element_boxes:
        return None
    normalized: dict[str, tuple[float, float, float, float]] = {}
    for element_id, raw in element_boxes.items():
        if raw is None or len(raw) != 4:
            continue
        box = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        normalized[str(element_id)] = box
    return normalized or None


def _split_chain_by_geometry(
    chain: Sequence[str],
    boxes: Mapping[str, tuple[float, float, float, float]],
) -> list[list[str]]:
    """Split a successor chain on column wraps and large vertical gaps.

    Relation edges stay unchanged; this only refines packaging membership for
    over-fragmented paragraph heads.
    """

    if len(chain) <= 1:
        return [list(chain)]

    gaps: list[float] = []
    for source, target in zip(chain, chain[1:], strict=False):
        if source not in boxes or target not in boxes:
            gaps.append(0.0)
            continue
        source_box = boxes[source]
        target_box = boxes[target]
        gaps.append(target_box[1] - source_box[3])
    positive = sorted(gap for gap in gaps if gap > 0)
    median_gap = positive[len(positive) // 2] if positive else 1.0
    gap_threshold = max(6.0, median_gap * 2.5)

    parts: list[list[str]] = [[str(chain[0])]]
    for index, (source, target) in enumerate(zip(chain, chain[1:], strict=False)):
        split = False
        if source in boxes and target in boxes:
            source_box = boxes[source]
            target_box = boxes[target]
            source_center_x = (source_box[0] + source_box[2]) / 2
            target_center_x = (target_box[0] + target_box[2]) / 2
            dx = target_center_x - source_center_x
            source_width = max(source_box[2] - source_box[0], 1.0)
            target_width = max(target_box[2] - target_box[0], 1.0)
            overlap = max(
                0.0,
                min(source_box[2], target_box[2]) - max(source_box[0], target_box[0]),
            )
            horizontal_overlap = overlap / min(source_width, target_width)
            gap = gaps[index]
            # Column wrap: large horizontal jump with little overlap,
            # or a large negative vertical jump after finishing a column.
            if (abs(dx) > 40.0 and horizontal_overlap < 0.25) or gap < -50.0:
                split = True
            elif gap > gap_threshold and horizontal_overlap >= 0.5:
                split = True
        if split:
            parts.append([str(target)])
        else:
            parts[-1].append(str(target))
    return parts


@dataclass(frozen=True)
class JointGraphProposalResult:
    proposal_path: Path
    proposal: dict[str, Any]
    hierarchy_input_path: Path | None
    paragraph_proposal_path: Path
    successor_proposal_path: Path
    decoder_mode: str


def propose_joint_graph(
    hierarchy_input: str | Path | Mapping[str, Any],
    *,
    paragraph_model: str | Path,
    successor_model: str | Path,
    output: str | Path,
    sample_id: str | None = None,
    work_dir: str | Path | None = None,
) -> JointGraphProposalResult:
    """Predict paragraph/successor proposals and package a review-only joint graph.

    This is the single-page operator path for real DocumentIR/hierarchy inputs.
    It never reorders runtime IR.
    """

    from .hierarchical_order_benchmark import HIERARCHY_INPUT_SCHEMA
    from .paragraph_graph_benchmark import predict_paragraph_graph
    from .successor_graph_benchmark import predict_successor_graph

    if isinstance(hierarchy_input, Mapping):
        payload = dict(hierarchy_input)
        hierarchy_path: Path | None = None
    else:
        hierarchy_path = Path(hierarchy_input)
        payload = _json_object(hierarchy_path, label="hierarchy input")
    if payload.get("schema") != HIERARCHY_INPUT_SCHEMA:
        raise ValueError("joint proposal input has an unsupported hierarchy schema")

    sample = sample_id or str(payload.get("id") or Path(str(output)).stem)
    root = Path(work_dir) if work_dir is not None else Path(output).parent
    root.mkdir(parents=True, exist_ok=True)
    if hierarchy_path is None:
        hierarchy_path = root / f"{_safe_filename(sample)}.hierarchy-input.json"
        _write_json(hierarchy_path, payload)

    paragraph_path = root / f"{_safe_filename(sample)}.paragraph-graph.json"
    successor_path = root / f"{_safe_filename(sample)}.successor-graph.json"
    paragraph = predict_paragraph_graph(
        hierarchy_path,
        paragraph_model,
        output=paragraph_path,
        sample_id=sample,
    )
    successor = predict_successor_graph(
        hierarchy_path,
        successor_model,
        output=successor_path,
        sample_id=sample,
    )

    membership, element_ids = _membership_from_paragraph_proposal(paragraph.proposal)
    base_rank = _base_rank_from_proposals(
        element_ids,
        successor_payload=successor.proposal,
    )
    element_ids = tuple(
        sorted(element_ids, key=lambda element_id: (base_rank[element_id], element_id))
    )
    successor_edges = _scored_edges_from_successor_proposal(successor.proposal)
    element_boxes: dict[str, tuple[float, float, float, float]] = {}
    for raw in payload.get("elements") or []:
        if not isinstance(raw, Mapping):
            continue
        element_id = str(raw.get("id") or "").strip()
        raw_box = raw.get("box")
        if not element_id or not isinstance(raw_box, list) or len(raw_box) != 4:
            continue
        try:
            box = (
                float(raw_box[0]),
                float(raw_box[1]),
                float(raw_box[2]),
                float(raw_box[3]),
            )
        except (TypeError, ValueError):
            continue
        if box[2] > box[0] and box[3] > box[1]:
            element_boxes[element_id] = box

    decoded = joint_decode_page(
        element_ids=element_ids,
        paragraph_membership=membership,
        successor_edges=successor_edges,
        base_rank=base_rank,
        element_boxes=element_boxes or None,
    )
    page = _PageProposals(
        sample_id=sample,
        partition="predict",
        layout_stratum=str(payload.get("layout_stratum") or "unspecified"),
        document_id=str(payload.get("document_id") or sample),
        element_ids=element_ids,
        base_rank=base_rank,
        paragraph_membership=membership,
        paragraph_edges=(),
        successor_edges=successor_edges,
        paragraph_threshold=float(paragraph.proposal.get("threshold") or 0.0),
        successor_threshold=float(successor.proposal.get("threshold") or 0.0),
        paragraph_proposal_path=paragraph.proposal_path,
        successor_proposal_path=successor.proposal_path,
        corpus=root,
        labels_relative="",
        labels_sha256="",
        element_boxes=element_boxes or None,
    )
    proposal_path = Path(output)
    written = _write_proposals([page], [decoded], proposal_path.parent)
    if not written:
        raise ValueError("joint proposal generation produced no output")
    generated = Path(written[0])
    if generated.resolve() != proposal_path.resolve():
        proposal_path.parent.mkdir(parents=True, exist_ok=True)
        proposal_path.write_text(generated.read_text(encoding="utf-8"), encoding="utf-8")
        if generated != proposal_path:
            generated.unlink(missing_ok=True)
    proposal = _json_object(proposal_path, label="joint graph proposal")
    return JointGraphProposalResult(
        proposal_path=proposal_path,
        proposal=proposal,
        hierarchy_input_path=hierarchy_path,
        paragraph_proposal_path=paragraph.proposal_path,
        successor_proposal_path=successor.proposal_path,
        decoder_mode=decoded.decoder_mode,
    )


def benchmark_joint_graph(
    train_corpus_dir: str | Path,
    *,
    paragraph_proposals_dir: str | Path,
    successor_proposals_dir: str | Path,
    output: str | Path,
    proposals_dir: str | Path | None = None,
    test_corpus_dir: str | Path | None = None,
) -> JointGraphBenchmarkResult:
    """Score hierarchical joint decoding over existing review-only graph proposals.

    The joint decoder never retrains either head. It only consumes answer-free
    paragraph and successor proposals, writes joint proposals, then opens labels.
    """

    train_corpus = Path(train_corpus_dir).resolve()
    train_manifest_path, train_manifest = _corpus_manifest(train_corpus)
    paragraph_root = Path(paragraph_proposals_dir).resolve()
    successor_root = Path(successor_proposals_dir).resolve()

    fit_pages = _load_pages(
        train_corpus,
        train_manifest,
        paragraph_root=paragraph_root,
        successor_root=successor_root,
        split="fit",
    )
    calibration_pages = _load_pages(
        train_corpus,
        train_manifest,
        paragraph_root=paragraph_root,
        successor_root=successor_root,
        split="calibration",
    )
    if not fit_pages or not calibration_pages:
        raise ValueError("training corpus requires fit and calibration pages")

    test_manifest_path: Path | None = None
    test_pages: list[_PageProposals] = []
    if test_corpus_dir is not None:
        test_corpus = Path(test_corpus_dir).resolve()
        test_manifest_path, test_manifest = _corpus_manifest(test_corpus)
        test_pages = _load_pages(
            test_corpus,
            test_manifest,
            paragraph_root=paragraph_root,
            successor_root=successor_root,
            split="test",
            accept_all_partitions=True,
        )
        if not test_pages:
            raise ValueError("test corpus contains no pages")

    _require_disjoint_documents(fit_pages, calibration_pages, test_pages)
    _require_unique_sample_ids(fit_pages, calibration_pages, test_pages)

    report_path = Path(output)
    proposal_root = (
        Path(proposals_dir)
        if proposals_dir is not None
        else report_path.parent / f"{report_path.stem}.proposals"
    )
    proposal_root.mkdir(parents=True, exist_ok=True)

    fit_decoded = [_decode_loaded_page(page) for page in fit_pages]
    calibration_decoded = [_decode_loaded_page(page) for page in calibration_pages]
    test_decoded = [_decode_loaded_page(page) for page in test_pages]
    fit_proposal_paths = _write_proposals(fit_pages, fit_decoded, proposal_root)
    calibration_proposal_paths = _write_proposals(
        calibration_pages,
        calibration_decoded,
        proposal_root,
    )
    test_proposal_paths = _write_proposals(test_pages, test_decoded, proposal_root)

    # Evaluation labels open only after every joint proposal is on disk.
    fit_labels = [_load_labels(page) for page in fit_pages]
    calibration_labels = [_load_labels(page) for page in calibration_pages]
    test_labels = [_load_labels(page) for page in test_pages]

    summaries = {
        "fit": _score_pages(fit_pages, fit_decoded, fit_labels),
        "calibration": _score_pages(
            calibration_pages,
            calibration_decoded,
            calibration_labels,
        ),
    }
    if test_pages:
        summaries["test"] = _score_pages(test_pages, test_decoded, test_labels)
    summaries_by_layout_stratum = {
        "fit": _score_pages_by_layout_stratum(fit_pages, fit_decoded, fit_labels),
        "calibration": _score_pages_by_layout_stratum(
            calibration_pages,
            calibration_decoded,
            calibration_labels,
        ),
    }
    if test_pages:
        summaries_by_layout_stratum["test"] = _score_pages_by_layout_stratum(
            test_pages,
            test_decoded,
            test_labels,
        )

    report = {
        "schema": JOINT_GRAPH_BENCHMARK_SCHEMA,
        "decoder_version": JOINT_GRAPH_DECODER_VERSION,
        "train_corpus_manifest": str(train_manifest_path),
        "train_corpus_manifest_sha256": _file_sha256(train_manifest_path),
        "test_corpus_manifest": (
            str(test_manifest_path) if test_manifest_path is not None else None
        ),
        "test_corpus_manifest_sha256": (
            _file_sha256(test_manifest_path) if test_manifest_path is not None else None
        ),
        "paragraph_proposals_dir": str(paragraph_root),
        "successor_proposals_dir": str(successor_root),
        "decoder_policy": (
            "prefer packaging a successor path cover with paragraph hierarchy "
            "labels; fall back to successor-chain packaging when paragraph "
            "singleton rate is at least 0.85, optionally splitting chains on "
            "column wraps and large vertical gaps when boxes are available; "
            "fall back to paragraph-protected path cover with score-ordered "
            "tail-to-head cross edges when successors are not a valid path cover"
        ),
        "label_policy": (
            "complete oracle co-membership plus published Comp-HRDoc immediate "
            "successors with partial endpoints"
        ),
        "head_policy": (
            "joint decode only; paragraph and successor heads remain independently "
            "trained and review-only"
        ),
        "fit_document_count": len({page.document_id for page in fit_pages}),
        "calibration_document_count": len(
            {page.document_id for page in calibration_pages}
        ),
        "test_document_count": len({page.document_id for page in test_pages}),
        "fit_page_count": len(fit_pages),
        "calibration_page_count": len(calibration_pages),
        "test_page_count": len(test_pages),
        "answer_separation": {
            "proposals_loaded_before_labels": True,
            "joint_predictions_written_before_evaluation_labels": True,
            "decoder_uses_labels": False,
            "retraining_disabled": True,
        },
        "runtime_reorder": False,
        "promotion_decision": "benchmark-only-joint-paragraph-successor-decode",
        "summary": summaries,
        "summary_by_layout_stratum": summaries_by_layout_stratum,
        "proposals": {
            "fit": fit_proposal_paths,
            "calibration": calibration_proposal_paths,
            "test": test_proposal_paths,
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(report_path, report)
    return JointGraphBenchmarkResult(report_path, proposal_root, report)


def _decode_loaded_page(page: _PageProposals) -> _DecodedPage:
    return joint_decode_page(
        element_ids=page.element_ids,
        paragraph_membership=page.paragraph_membership,
        successor_edges=page.successor_edges,
        base_rank=page.base_rank,
        element_boxes=page.element_boxes,
    )


def _load_pages(
    corpus: Path,
    manifest: Mapping[str, Any],
    *,
    paragraph_root: Path,
    successor_root: Path,
    split: str,
    accept_all_partitions: bool = False,
) -> list[_PageProposals]:
    pages: list[_PageProposals] = []
    for raw_sample in manifest["samples"]:
        if not isinstance(raw_sample, Mapping):
            raise ValueError("joint graph samples must be objects")
        partition = str(raw_sample.get("partition") or "")
        if not accept_all_partitions and partition != split:
            continue
        sample_id = str(raw_sample.get("id") or "").strip()
        document_id = str(raw_sample.get("document_id") or "").strip()
        if not sample_id or not document_id:
            raise ValueError("joint graph samples require id and document_id")
        labels_relative = str(raw_sample.get("labels") or "").strip()
        labels_sha256 = str(raw_sample.get("labels_sha256") or "").strip()
        if not labels_relative or not labels_sha256:
            raise ValueError("joint graph samples require hashed labels")

        paragraph_path = _proposal_path(paragraph_root, sample_id, "paragraph-graph")
        successor_path = _proposal_path(successor_root, sample_id, "successor-graph")
        if not paragraph_path.is_file():
            raise ValueError(f"missing paragraph proposal for {sample_id}: {paragraph_path}")
        if not successor_path.is_file():
            raise ValueError(f"missing successor proposal for {sample_id}: {successor_path}")

        paragraph_payload = _json_object(paragraph_path, label="paragraph proposal")
        successor_payload = _json_object(successor_path, label="successor proposal")
        if paragraph_payload.get("schema") != PARAGRAPH_GRAPH_PROPOSAL_SCHEMA:
            raise ValueError("unsupported paragraph graph proposal schema")
        if successor_payload.get("schema") != SUCCESSOR_GRAPH_PROPOSAL_SCHEMA:
            raise ValueError("unsupported successor graph proposal schema")
        if paragraph_payload.get("runtime_reorder") is not False:
            raise ValueError("paragraph proposal must declare runtime_reorder=false")
        if successor_payload.get("runtime_reorder") is not False:
            raise ValueError("successor proposal must declare runtime_reorder=false")
        if str(paragraph_payload.get("id") or "") != sample_id:
            raise ValueError("paragraph proposal id does not match sample id")
        if str(successor_payload.get("id") or "") != sample_id:
            raise ValueError("successor proposal id does not match sample id")

        membership, element_ids = _membership_from_paragraph_proposal(paragraph_payload)
        base_rank = _base_rank_from_proposals(
            element_ids,
            successor_payload=successor_payload,
        )
        # Keep a base-rank order for deterministic proposal emission.
        element_ids = tuple(
            sorted(element_ids, key=lambda element_id: (base_rank[element_id], element_id))
        )
        paragraph_edges = _scored_edges_from_candidates(
            paragraph_payload.get("candidate_edges"),
            selected_only=True,
            undirected=True,
        )
        successor_edges = _scored_edges_from_successor_proposal(successor_payload)
        element_boxes = _load_element_boxes_from_corpus(
            corpus,
            raw_sample.get("input"),
            expected_ids=set(element_ids),
        )
        pages.append(
            _PageProposals(
                sample_id=sample_id,
                partition=split if accept_all_partitions else partition,
                layout_stratum=str(raw_sample.get("layout_stratum") or "unspecified"),
                document_id=document_id,
                element_ids=element_ids,
                base_rank=base_rank,
                paragraph_membership=membership,
                paragraph_edges=paragraph_edges,
                successor_edges=successor_edges,
                paragraph_threshold=float(paragraph_payload.get("threshold") or 0.0),
                successor_threshold=float(successor_payload.get("threshold") or 0.0),
                paragraph_proposal_path=paragraph_path,
                successor_proposal_path=successor_path,
                corpus=corpus,
                labels_relative=labels_relative,
                labels_sha256=labels_sha256,
                element_boxes=element_boxes,
            )
        )
    return pages


def _load_element_boxes_from_corpus(
    corpus: Path,
    relative_input: object,
    *,
    expected_ids: set[str],
) -> dict[str, tuple[float, float, float, float]] | None:
    if not isinstance(relative_input, str) or not relative_input.strip():
        return None
    try:
        input_path = _confined_path(corpus, relative_input, label="sample input")
        payload = _json_object(input_path, label="sample input")
    except ValueError:
        return None
    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list):
        return None
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for raw in raw_elements:
        if not isinstance(raw, Mapping):
            continue
        element_id = str(raw.get("id") or "").strip()
        if not element_id or element_id not in expected_ids:
            continue
        raw_box = raw.get("box")
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            continue
        try:
            box = (
                float(raw_box[0]),
                float(raw_box[1]),
                float(raw_box[2]),
                float(raw_box[3]),
            )
        except (TypeError, ValueError):
            continue
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        boxes[element_id] = box
    return boxes or None


def _membership_from_paragraph_proposal(
    payload: Mapping[str, Any],
) -> tuple[dict[str, str], tuple[str, ...]]:
    streams = payload.get("reading_streams")
    if not isinstance(streams, list) or not streams:
        raise ValueError("paragraph proposal requires reading_streams")
    membership: dict[str, str] = {}
    element_ids: list[str] = []
    for stream in streams:
        if not isinstance(stream, Mapping):
            raise ValueError("paragraph proposal streams must be objects")
        stream_id = str(stream.get("id") or "").strip()
        members = stream.get("members")
        if not stream_id or not isinstance(members, list) or not members:
            raise ValueError("paragraph proposal streams require id and members")
        for raw_member in members:
            element_id = str(raw_member or "").strip()
            if not element_id:
                raise ValueError("paragraph proposal members must be non-empty ids")
            if element_id in membership:
                raise ValueError("paragraph proposal members must be unique")
            membership[element_id] = stream_id
            element_ids.append(element_id)
    if not element_ids:
        raise ValueError("paragraph proposal has no members")
    return membership, tuple(element_ids)


def _base_rank_from_proposals(
    element_ids: Sequence[str],
    *,
    successor_payload: Mapping[str, Any],
) -> dict[str, int]:
    """Prefer successor-chain order for stable joint decode ties.

    Paragraph streams are unordered components. Successor proposals already
    emit local chains; use those as a stable base rank when available, then
    fall back to the remaining element-id order.
    """

    ordered: list[str] = []
    seen: set[str] = set()
    streams = successor_payload.get("reading_streams")
    if isinstance(streams, list):
        for stream in streams:
            if not isinstance(stream, Mapping):
                continue
            members = stream.get("members")
            if not isinstance(members, list):
                continue
            for raw_member in members:
                element_id = str(raw_member or "").strip()
                if not element_id or element_id in seen or element_id not in element_ids:
                    continue
                ordered.append(element_id)
                seen.add(element_id)
    for element_id in element_ids:
        if element_id not in seen:
            ordered.append(element_id)
            seen.add(element_id)
    if set(ordered) != set(element_ids):
        raise ValueError("joint base rank does not cover paragraph proposal members")
    return {element_id: index for index, element_id in enumerate(ordered)}


def _scored_edges_from_candidates(
    raw_edges: object,
    *,
    selected_only: bool,
    undirected: bool,
) -> tuple[_ScoredEdge, ...]:
    if not isinstance(raw_edges, list):
        raise ValueError("proposal candidate_edges must be a list")
    edges: list[_ScoredEdge] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_edges:
        if not isinstance(raw, Mapping):
            raise ValueError("proposal candidate edges must be objects")
        if selected_only and raw.get("selected") is not True:
            continue
        source = str(raw.get("source") or "").strip()
        target = str(raw.get("target") or "").strip()
        if not source or not target or source == target:
            continue
        pair = (source, target)
        if undirected:
            pair = tuple(sorted(pair))  # type: ignore[assignment]
            source, target = pair
        if pair in seen:
            continue
        seen.add(pair)
        edges.append(
            _ScoredEdge(
                source=source,
                target=target,
                score=float(raw.get("score") or 0.0),
                rank=int(raw["rank"]) if raw.get("rank") is not None else None,
                top_score_margin=(
                    float(raw["top_score_margin"])
                    if raw.get("top_score_margin") is not None
                    else None
                ),
            )
        )
    return tuple(edges)


def _scored_edges_from_successor_proposal(payload: Mapping[str, Any]) -> tuple[_ScoredEdge, ...]:
    """Load successor edges for joint packaging or constrained re-decode.

    Prefer the already path-covered ``successor_edges`` list so successor-primary
    packaging can preserve the successor head exactly. Fall back to thresholded
    rank-1 ``candidate_edges`` only when selected edges are absent.
    """

    selected = payload.get("successor_edges")
    if isinstance(selected, list) and selected:
        edges: list[_ScoredEdge] = []
        for raw in selected:
            if not isinstance(raw, Mapping):
                raise ValueError("successor proposal edges must be objects")
            source = str(raw.get("source") or "").strip()
            target = str(raw.get("target") or "").strip()
            if not source or not target:
                raise ValueError("successor proposal edges require source and target")
            edges.append(
                _ScoredEdge(
                    source=source,
                    target=target,
                    score=float(raw.get("confidence") or raw.get("score") or 0.0),
                    rank=1,
                )
            )
        return tuple(edges)

    threshold = float(payload.get("threshold") or 0.0)
    raw_candidates = payload.get("candidate_edges")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError("successor proposal requires successor_edges or candidate_edges")
    top_edges: list[_ScoredEdge] = []
    for raw in raw_candidates:
        if not isinstance(raw, Mapping):
            raise ValueError("successor candidate edges must be objects")
        rank = raw.get("rank")
        if rank is not None and int(rank) != 1:
            continue
        score = float(raw.get("score") or 0.0)
        selected_flag = raw.get("selected") is True
        if score < threshold and not selected_flag:
            continue
        source = str(raw.get("source") or "").strip()
        target = str(raw.get("target") or "").strip()
        if not source or not target:
            continue
        top_edges.append(
            _ScoredEdge(
                source=source,
                target=target,
                score=score,
                rank=1,
                top_score_margin=(
                    float(raw["top_score_margin"])
                    if raw.get("top_score_margin") is not None
                    else None
                ),
            )
        )
    by_source: dict[str, _ScoredEdge] = {}
    for edge in top_edges:
        previous = by_source.get(edge.source)
        if previous is None or edge.score > previous.score:
            by_source[edge.source] = edge
    if not by_source:
        raise ValueError("successor proposal has no usable directed candidates")
    return tuple(by_source[source] for source in sorted(by_source))


def _write_proposals(
    pages: Sequence[_PageProposals],
    decoded_pages: Sequence[_DecodedPage],
    root: Path,
) -> list[str]:
    if len(pages) != len(decoded_pages):
        raise ValueError("joint graph pages and decoded pages must align")
    paths: list[str] = []
    for page, decoded in zip(pages, decoded_pages, strict=True):
        within_scores = {
            (edge.source, edge.target): edge.score for edge in page.successor_edges
        }
        proposal = {
            "schema": JOINT_GRAPH_PROPOSAL_SCHEMA,
            "id": page.sample_id,
            "partition": page.partition,
            "decoder_version": JOINT_GRAPH_DECODER_VERSION,
            "decoder_mode": decoded.decoder_mode,
            "runtime_reorder": False,
            "paragraph_threshold": round(page.paragraph_threshold, 8),
            "successor_threshold": round(page.successor_threshold, 8),
            "paragraph_proposal": str(page.paragraph_proposal_path),
            "successor_proposal": str(page.successor_proposal_path),
            "decoder_diagnostics": decoded.diagnostics,
            "paragraph_streams": [
                {
                    "id": stream_id,
                    "type": "body",
                    "members": [
                        element_id
                        for element_id in page.element_ids
                        if decoded.membership[element_id] == stream_id
                    ],
                    "proposal": {
                        "origin": "fine-line-paragraph-graph",
                        "review_required": True,
                    },
                }
                for stream_id in sorted(set(decoded.membership.values()))
            ],
            "successor_edges": [
                {
                    "source": source,
                    "target": target,
                    "kind": "successor",
                    "scope": (
                        "within-paragraph"
                        if (source, target) in decoded.within_selected_edges
                        else "cross-paragraph"
                    ),
                    "confidence": round(within_scores.get((source, target), 0.0), 8),
                    "review_required": True,
                    "relation_policy": "review-only",
                    "origin": "joint-paragraph-successor-graph",
                }
                for source, target in sorted(
                    decoded.selected_edges,
                    key=lambda edge: (
                        page.base_rank[edge[0]],
                        page.base_rank[edge[1]],
                        edge,
                    ),
                )
            ],
            "reading_streams": [
                {
                    "id": f"joint-graph-{index + 1:04d}",
                    "type": "body",
                    "members": list(members),
                    "proposal": {
                        "origin": "joint-paragraph-successor-graph",
                        "review_required": True,
                        "paragraph_component_ids": sorted(
                            {
                                decoded.membership[element_id]
                                for element_id in members
                            }
                        ),
                    },
                }
                for index, members in enumerate(decoded.streams)
            ],
        }
        path = _proposal_path(root, page.sample_id, "joint-graph")
        _write_json(path, proposal)
        paths.append(str(path))
    return paths


def _score_pages(
    pages: Sequence[_PageProposals],
    decoded_pages: Sequence[_DecodedPage],
    labels: Sequence[_Labels],
) -> dict[str, Any]:
    if len(pages) != len(decoded_pages) or len(pages) != len(labels):
        raise ValueError("joint graph pages, decoded pages, and labels must align")

    pair_counts = Counter()
    selected_counts = Counter()
    within_counts = Counter()
    cross_counts = Counter()
    successor_only_counts = Counter()
    decoder_counts = Counter()
    labelled_relation_pages = 0
    labelled_segmentation_pages = 0

    for page, decoded, page_labels in zip(pages, decoded_pages, labels, strict=True):
        predicted_pairs = _co_membership_pairs(decoded.membership)
        truth_pairs = _co_membership_pairs(page_labels.membership)
        labelled_segmentation_pages += bool(truth_pairs)
        pair_counts["predicted"] += len(predicted_pairs)
        pair_counts["labels"] += len(truth_pairs)
        pair_counts["correct"] += len(predicted_pairs & truth_pairs)

        _accumulate_partial_counts(
            selected_counts,
            decoded.selected_edges,
            page_labels.edges,
        )
        successor_only_edges = frozenset(
            (edge.source, edge.target) for edge in page.successor_edges
        )
        _accumulate_partial_counts(
            successor_only_counts,
            successor_only_edges,
            page_labels.edges,
        )

        within_truth = {
            edge
            for edge, scope in page_labels.scopes.items()
            if scope == "within-oracle-region"
        }
        cross_truth = page_labels.edges - within_truth
        # Recovery is over oracle scopes among selected edges, independent of
        # predicted paragraph membership packaging.
        within_counts["correct"] += len(decoded.selected_edges & within_truth)
        within_counts["labels"] += len(within_truth)
        cross_counts["correct"] += len(decoded.selected_edges & cross_truth)
        cross_counts["labels"] += len(cross_truth)
        labelled_relation_pages += bool(page_labels.edges)
        decoder_counts.update(decoded.diagnostics)
        decoder_counts[f"mode:{decoded.decoder_mode}"] += 1

    return {
        "page_count": len(pages),
        "labelled_segmentation_page_count": labelled_segmentation_pages,
        "labelled_relation_page_count": labelled_relation_pages,
        "segmentation_pairwise": _precision_recall_f1(pair_counts),
        "selected_relation": _partial_relation_summary(selected_counts),
        "successor_only_relation": _partial_relation_summary(successor_only_counts),
        "within_region_recovery": _recall_summary(within_counts),
        "cross_region_recovery": _recall_summary(cross_counts),
        "decoder_diagnostics": dict(sorted(decoder_counts.items())),
        "runtime_reorder": False,
    }


def _score_pages_by_layout_stratum(
    pages: Sequence[_PageProposals],
    decoded_pages: Sequence[_DecodedPage],
    labels: Sequence[_Labels],
) -> dict[str, dict[str, Any]]:
    grouped_pages: dict[str, list[_PageProposals]] = defaultdict(list)
    grouped_decoded: dict[str, list[_DecodedPage]] = defaultdict(list)
    grouped_labels: dict[str, list[_Labels]] = defaultdict(list)
    for page, decoded, page_labels in zip(pages, decoded_pages, labels, strict=True):
        stratum = page.layout_stratum
        grouped_pages[stratum].append(page)
        grouped_decoded[stratum].append(decoded)
        grouped_labels[stratum].append(page_labels)
    return {
        stratum: _score_pages(
            grouped_pages[stratum],
            grouped_decoded[stratum],
            grouped_labels[stratum],
        )
        for stratum in sorted(grouped_pages)
    }


def _load_labels(page: _PageProposals) -> _Labels:
    path = _confined_path(page.corpus, page.labels_relative, label="sample labels")
    _verify_hash(path, page.labels_sha256, label="sample labels")
    payload = _json_object(path, label="joint graph labels")
    if payload.get("schema") != PROVIDER_HIERARCHY_LABEL_SCHEMA:
        raise ValueError("joint graph labels have an unsupported schema")

    raw_memberships = payload.get("memberships")
    if not isinstance(raw_memberships, list):
        raise ValueError("joint graph labels require memberships")
    membership: dict[str, str] = {}
    for item in raw_memberships:
        if not isinstance(item, Mapping):
            raise ValueError("joint graph memberships must be objects")
        element_id = str(item.get("element_id") or "").strip()
        region_id = str(item.get("oracle_region_id") or "").strip()
        if not element_id or not region_id:
            raise ValueError("joint graph memberships require element and region ids")
        if element_id in membership:
            raise ValueError("joint graph membership labels must be unique")
        membership[element_id] = region_id
    if set(membership) != set(page.element_ids):
        raise ValueError("joint graph membership labels are incomplete")

    raw_edges = payload.get("successor_edges")
    if not isinstance(raw_edges, list):
        raise ValueError("joint graph labels require successor_edges")
    edges: set[tuple[str, str]] = set()
    scopes: dict[tuple[str, str], str] = {}
    outgoing: set[str] = set()
    incoming: set[str] = set()
    for raw in raw_edges:
        if not isinstance(raw, Mapping):
            raise ValueError("joint graph successor labels must be objects")
        source = str(raw.get("source") or "").strip()
        target = str(raw.get("target") or "").strip()
        scope = str(raw.get("oracle_scope") or "").strip() or "unspecified"
        if source not in membership or target not in membership:
            raise ValueError("joint graph successor labels reference unknown elements")
        if source == target:
            raise ValueError("joint graph successor labels must not self-loop")
        edge = (source, target)
        if edge in edges:
            raise ValueError("joint graph successor labels must be unique")
        if source in outgoing or target in incoming:
            raise ValueError("joint graph successor labels must satisfy degree one")
        outgoing.add(source)
        incoming.add(target)
        edges.add(edge)
        scopes[edge] = scope
    return _Labels(membership=membership, edges=frozenset(edges), scopes=scopes)


def _chain_endpoints(
    element_ids: Sequence[str],
    edges: frozenset[tuple[str, str]],
    base_rank: Mapping[str, int],
) -> tuple[dict[str, str], dict[str, str], list[list[str]]]:
    chains = _edge_chains(element_ids, edges, base_rank)
    head: dict[str, str] = {}
    tail: dict[str, str] = {}
    for chain in chains:
        if not chain:
            continue
        head[chain[0]] = chain[0]
        tail[chain[-1]] = chain[-1]
    return head, tail, chains


def _edge_chains(
    element_ids: Sequence[str],
    edges: frozenset[tuple[str, str]],
    base_rank: Mapping[str, int],
) -> list[list[str]]:
    successor = {source: target for source, target in edges}
    predecessor = {target: source for source, target in edges}
    starts = sorted(
        (element_id for element_id in element_ids if element_id not in predecessor),
        key=lambda element_id: (base_rank[element_id], element_id),
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
    for element_id in element_ids:
        if element_id not in seen:
            chains.append([element_id])
    return chains


def _accumulate_partial_counts(
    counts: Counter[str],
    predicted: frozenset[tuple[str, str]],
    labels: frozenset[tuple[str, str]],
) -> None:
    endpoints = {endpoint for edge in labels for endpoint in edge}
    scorable = {
        edge for edge in predicted if edge[0] in endpoints and edge[1] in endpoints
    }
    counts["correct"] += len(predicted & labels)
    counts["predicted"] += len(predicted)
    counts["scorable"] += len(scorable)
    counts["unscored"] += len(predicted - scorable)
    counts["labels"] += len(labels)


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


def _corpus_manifest(corpus: Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = corpus / "provider_hierarchy_corpus_manifest.json"
    manifest = _json_object(manifest_path, label="provider hierarchy manifest")
    if manifest.get("schema") != PROVIDER_HIERARCHY_CORPUS_SCHEMA:
        raise ValueError("unsupported provider hierarchy corpus schema")
    if manifest.get("inference_inputs_are_answer_free") is not True:
        raise ValueError("joint graph corpus must declare answer-free inputs")
    if not isinstance(manifest.get("samples"), list):
        raise ValueError("provider hierarchy corpus has no samples")
    return manifest_path, manifest


def _require_disjoint_documents(*groups: Sequence[_PageProposals]) -> None:
    seen: set[str] = set()
    for pages in groups:
        documents = {page.document_id for page in pages}
        if seen & documents:
            raise ValueError("joint graph partitions must be document-disjoint")
        seen.update(documents)


def _require_unique_sample_ids(*groups: Sequence[_PageProposals]) -> None:
    seen: set[str] = set()
    for pages in groups:
        for page in pages:
            if page.sample_id in seen:
                raise ValueError("joint graph sample ids must be globally unique")
            seen.add(page.sample_id)


def _proposal_path(root: Path, sample_id: str, suffix: str) -> Path:
    digest = hashlib.sha256(sample_id.encode("utf-8")).hexdigest()[:12]
    return root / f"{_safe_filename(sample_id)}--{digest}.{suffix}.json"


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return safe or "sample"


def _confined_path(root: Path, relative: object, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(f"{label} path is required")
    candidate = (root / relative).resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError(f"{label} path escapes corpus root")
    if not candidate.is_file():
        raise ValueError(f"{label} is missing: {candidate}")
    return candidate


def _verify_hash(path: Path, expected: object, *, label: str) -> None:
    actual = _file_sha256(path)
    if not isinstance(expected, str) or not expected or actual != expected:
        raise ValueError(f"{label} hash mismatch for {path}")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


# Re-export helpers used by tests without importing private paragraph internals twice.
__all__ = [
    "JOINT_GRAPH_BENCHMARK_SCHEMA",
    "JOINT_GRAPH_DECODER_VERSION",
    "JOINT_GRAPH_PROPOSAL_SCHEMA",
    "JointGraphBenchmarkResult",
    "benchmark_joint_graph",
    "joint_decode_page",
]
