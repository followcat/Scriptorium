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
JOINT_GRAPH_DECODER_VERSION = "paragraph-protected-successor-path-cover-v1"


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


@dataclass(frozen=True)
class _DecodedPage:
    membership: dict[str, str]
    selected_edges: frozenset[tuple[str, str]]
    within_selected_edges: frozenset[tuple[str, str]]
    cross_selected_edges: frozenset[tuple[str, str]]
    streams: tuple[tuple[str, ...], ...]
    diagnostics: dict[str, int]


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
) -> _DecodedPage:
    """Decode paragraph components with protected within-paragraph successors.

    Within-paragraph successor edges are protected in the degree-one acyclic
    path cover. Cross-paragraph edges may only connect a chain tail to a chain
    head and are accepted score-first. The result remains review-only evidence.
    """

    ids = tuple(str(element_id) for element_id in element_ids)
    if not ids:
        raise ValueError("joint decode requires at least one element")
    membership = {element_id: str(paragraph_membership[element_id]) for element_id in ids}
    rank = {
        element_id: int(base_rank[element_id]) if base_rank is not None else index
        for index, element_id in enumerate(ids)
    }

    within: list[_ScoredEdge] = []
    cross: list[_ScoredEdge] = []
    unknown = 0
    for edge in successor_edges:
        if edge.source not in membership or edge.target not in membership:
            unknown += 1
            continue
        if membership[edge.source] == membership[edge.target]:
            within.append(edge)
        else:
            cross.append(edge)

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
        if chain_tail.get(edge.source) == edge.source and chain_head.get(edge.target) == edge.target
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
    streams = tuple(
        tuple(chain)
        for chain in _edge_chains(ids, selected, rank)
    )
    return _DecodedPage(
        membership=membership,
        selected_edges=selected,
        within_selected_edges=within_selected,
        cross_selected_edges=cross_selected,
        streams=streams,
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
            "protect within-paragraph successor edges, then accept score-ordered "
            "tail-to-head cross-paragraph edges under a degree-one acyclic path cover"
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
            )
        )
    return pages


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
    """Load top-1 directed candidates so joint decode can re-run path cover.

    Prefer rank-1 ``candidate_edges`` over the already path-covered
    ``successor_edges`` list. Reusing only the final selected edges would hide
    conflicts that paragraph protection is meant to resolve.
    """

    raw_candidates = payload.get("candidate_edges")
    if isinstance(raw_candidates, list) and raw_candidates:
        top_edges: list[_ScoredEdge] = []
        for raw in raw_candidates:
            if not isinstance(raw, Mapping):
                raise ValueError("successor candidate edges must be objects")
            rank = raw.get("rank")
            if rank is not None and int(rank) != 1:
                continue
            source = str(raw.get("source") or "").strip()
            target = str(raw.get("target") or "").strip()
            if not source or not target:
                continue
            top_edges.append(
                _ScoredEdge(
                    source=source,
                    target=target,
                    score=float(raw.get("score") or 0.0),
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
        if by_source:
            return tuple(by_source[source] for source in sorted(by_source))

    selected = payload.get("successor_edges")
    if not isinstance(selected, list) or not selected:
        raise ValueError("successor proposal requires rank-1 candidates or successor_edges")
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
        successor_only_counts["correct"] += len(
            {(edge.source, edge.target) for edge in page.successor_edges} & page_labels.edges
        )
        successor_only_counts["predicted"] += len(page.successor_edges)
        successor_only_counts["labels"] += len(page_labels.edges)

        within_truth = {
            edge
            for edge, scope in page_labels.scopes.items()
            if scope == "within-oracle-region"
        }
        cross_truth = page_labels.edges - within_truth
        within_counts["correct"] += len(decoded.within_selected_edges & within_truth)
        within_counts["labels"] += len(within_truth)
        cross_counts["correct"] += len(decoded.cross_selected_edges & cross_truth)
        cross_counts["labels"] += len(cross_truth)
        labelled_relation_pages += bool(page_labels.edges)
        decoder_counts.update(decoded.diagnostics)

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
    counts["correct"] += len(predicted & labels)
    counts["predicted"] += len(predicted)
    counts["labels"] += len(labels)


def _partial_relation_summary(counts: Counter[str]) -> dict[str, int | float]:
    return _precision_recall_f1(counts)


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
