from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scriptorium.hierarchical_order_benchmark import HIERARCHY_INPUT_SCHEMA
from scriptorium.joint_graph_benchmark import (
    JOINT_GRAPH_BENCHMARK_SCHEMA,
    JOINT_GRAPH_PROPOSAL_SCHEMA,
    _ScoredEdge,
    _paragraph_component_metadata,
    _scored_edges_from_successor_proposal,
    benchmark_joint_graph,
    joint_decode_page,
)
from scriptorium.paragraph_graph_benchmark import (
    PARAGRAPH_GRAPH_PROPOSAL_SCHEMA,
    benchmark_paragraph_graph,
)
from scriptorium.provider_hierarchy_benchmark import (
    PROVIDER_HIERARCHY_CORPUS_SCHEMA,
    PROVIDER_HIERARCHY_LABEL_SCHEMA,
)
from scriptorium.relation_order import merge_relation_edge_path_cover
from scriptorium.successor_graph_benchmark import (
    SUCCESSOR_GRAPH_PROPOSAL_SCHEMA,
    benchmark_successor_graph,
)


def test_joint_decode_protects_within_paragraph_and_accepts_tail_to_head_cross() -> None:
    decoded = joint_decode_page(
        element_ids=["a", "b", "c", "d"],
        paragraph_membership={"a": "p1", "b": "p1", "c": "p2", "d": "p2"},
        successor_edges=[
            _ScoredEdge("a", "b", 0.91),
            _ScoredEdge("c", "d", 0.93),
            _ScoredEdge("b", "c", 0.88),
            _ScoredEdge("a", "c", 0.99),  # not tail→head
            _ScoredEdge("b", "d", 0.87),  # not head
        ],
    )

    # Conflicting edges force the paragraph-protected fallback.
    assert decoded.decoder_mode == "paragraph-protected-path-cover"
    assert sorted(decoded.selected_edges) == [("a", "b"), ("b", "c"), ("c", "d")]
    assert decoded.within_selected_edges == frozenset({("a", "b"), ("c", "d")})
    assert decoded.cross_selected_edges == frozenset({("b", "c")})
    assert decoded.streams == (("a", "b", "c", "d"),)
    assert decoded.diagnostics["endpoint_cross_candidate_count"] == 1


def test_joint_decode_packages_valid_successor_path_cover() -> None:
    decoded = joint_decode_page(
        element_ids=["a", "b", "c", "d"],
        paragraph_membership={"a": "p1", "b": "p1", "c": "p2", "d": "p2"},
        successor_edges=[
            _ScoredEdge("a", "b", 0.91),
            _ScoredEdge("b", "c", 0.88),
            _ScoredEdge("c", "d", 0.93),
        ],
    )

    assert decoded.decoder_mode == "successor-path-cover-package"
    assert sorted(decoded.selected_edges) == [("a", "b"), ("b", "c"), ("c", "d")]
    assert decoded.within_selected_edges == frozenset({("a", "b"), ("c", "d")})
    assert decoded.cross_selected_edges == frozenset({("b", "c")})
    assert decoded.streams == (("a", "b", "c", "d"),)
    assert decoded.diagnostics["endpoint_cross_candidate_count"] == 0


def test_joint_decode_keeps_sparse_page_reviewable_without_successor_edges() -> None:
    edges = _scored_edges_from_successor_proposal(
        {
            "successor_edges": [],
            "candidate_edges": [
                {
                    "source": "a",
                    "target": "b",
                    "score": 0.41,
                    "rank": 1,
                    "selected": False,
                }
            ],
            "threshold": 0.8,
        }
    )
    decoded = joint_decode_page(
        element_ids=["a", "b"],
        paragraph_membership={"a": "p1", "b": "p2"},
        successor_edges=edges,
    )

    assert edges == ()
    assert decoded.decoder_mode == "paragraph-protected-path-cover"
    assert decoded.selected_edges == frozenset()
    assert decoded.streams == (("a",), ("b",))
    assert decoded.diagnostics["successor_candidate_count"] == 0


def test_joint_decode_falls_back_to_successor_chains_when_paragraphs_are_singletons() -> None:
    decoded = joint_decode_page(
        element_ids=["a", "b", "c", "d"],
        paragraph_membership={"a": "p1", "b": "p2", "c": "p3", "d": "p4"},
        successor_edges=[
            _ScoredEdge("a", "b", 0.91),
            _ScoredEdge("b", "c", 0.88),
            _ScoredEdge("c", "d", 0.93),
        ],
    )

    assert decoded.decoder_mode == "successor-path-cover-package-chain-fallback"
    assert sorted(decoded.selected_edges) == [("a", "b"), ("b", "c"), ("c", "d")]
    # Chain packaging treats the path as one hierarchical component without
    # changing the successor path cover.
    assert decoded.within_selected_edges == frozenset(
        {("a", "b"), ("b", "c"), ("c", "d")}
    )
    assert decoded.cross_selected_edges == frozenset()
    assert decoded.streams == (("a", "b", "c", "d"),)
    assert decoded.diagnostics["paragraph_component_count"] == 1
    assert decoded.diagnostics["paragraph_input_component_count"] == 4
    assert decoded.diagnostics["paragraph_singleton_rate_x1000"] == 1000
    metadata = _paragraph_component_metadata(
        decoded,
        source_component_ids=["p1", "p2", "p3", "p4"],
    )
    assert metadata == {
        "origin": "fine-line-successor-chain-fallback",
        "review_required": True,
        "component_policy": "successor-chain",
        "source_paragraph_component_ids": ["p1", "p2", "p3", "p4"],
        "fallback_reason": "paragraph-singleton-rate-at-least-0.85",
    }


def test_joint_decode_splits_singleton_fallback_chains_on_column_wraps() -> None:
    decoded = joint_decode_page(
        element_ids=["a", "b", "c", "d"],
        paragraph_membership={"a": "p1", "b": "p2", "c": "p3", "d": "p4"},
        successor_edges=[
            _ScoredEdge("a", "b", 0.91),
            _ScoredEdge("b", "c", 0.88),  # column wrap in geometry
            _ScoredEdge("c", "d", 0.93),
        ],
        element_boxes={
            "a": (10, 10, 80, 20),
            "b": (10, 25, 80, 35),
            "c": (120, 10, 190, 20),  # right column top
            "d": (120, 25, 190, 35),
        },
    )

    assert decoded.decoder_mode == "successor-path-cover-package-chain-geometry-fallback"
    # Relation edges are unchanged.
    assert sorted(decoded.selected_edges) == [("a", "b"), ("b", "c"), ("c", "d")]
    assert decoded.streams == (("a", "b", "c", "d"),)
    # Packaging membership is split around the column wrap.
    assert decoded.membership["a"] == decoded.membership["b"]
    assert decoded.membership["c"] == decoded.membership["d"]
    assert decoded.membership["a"] != decoded.membership["c"]
    assert decoded.within_selected_edges == frozenset({("a", "b"), ("c", "d")})
    assert decoded.cross_selected_edges == frozenset({("b", "c")})
    assert decoded.diagnostics["geometry_chain_split_count"] == 1
    assert decoded.diagnostics["paragraph_component_count"] == 2
    metadata = _paragraph_component_metadata(
        decoded,
        source_component_ids=["p1", "p2"],
    )
    assert metadata["origin"] == "fine-line-successor-chain-geometry-fallback"
    assert metadata["component_policy"] == (
        "successor-chain-split-on-column-wrap-or-large-gap"
    )


def test_joint_decode_rejects_cross_edge_that_breaks_degree_one() -> None:
    decoded = joint_decode_page(
        element_ids=["a", "b", "c", "d", "e", "f"],
        paragraph_membership={
            "a": "p1",
            "b": "p1",
            "c": "p2",
            "d": "p2",
            "e": "p3",
            "f": "p3",
        },
        successor_edges=[
            _ScoredEdge("a", "b", 0.95),
            _ScoredEdge("c", "d", 0.94),
            _ScoredEdge("e", "f", 0.93),
            _ScoredEdge("b", "c", 0.90),
            _ScoredEdge("b", "e", 0.89),  # same tail; lower score loses
            _ScoredEdge("d", "e", 0.80),
        ],
    )

    assert decoded.decoder_mode == "paragraph-protected-path-cover"
    assert ("b", "c") in decoded.selected_edges
    assert ("b", "e") not in decoded.selected_edges
    assert ("d", "e") in decoded.selected_edges
    assert decoded.diagnostics["cross_outgoing_conflict_rejection_count"] >= 1


def test_joint_graph_is_answer_separated_and_scores_independent_test(tmp_path: Path) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    test = _write_corpus(
        tmp_path / "test",
        [(f"test-{index}", "test") for index in range(2)],
    )

    paragraph = benchmark_paragraph_graph(
        train,
        output=tmp_path / "paragraph-report.json",
        proposals_dir=tmp_path / "paragraph-proposals",
        test_corpus_dir=test,
        cross_validation_folds=2,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    successor = benchmark_successor_graph(
        train,
        output=tmp_path / "successor-report.json",
        proposals_dir=tmp_path / "successor-proposals",
        test_corpus_dir=test,
        cross_validation_folds=2,
        nearest_candidates=3,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )

    result = benchmark_joint_graph(
        train,
        paragraph_proposals_dir=paragraph.proposals_dir,
        successor_proposals_dir=successor.proposals_dir,
        output=tmp_path / "joint-report.json",
        proposals_dir=tmp_path / "joint-proposals",
        test_corpus_dir=test,
    )

    report = result.report
    assert report["schema"] == JOINT_GRAPH_BENCHMARK_SCHEMA
    assert report["runtime_reorder"] is False
    assert report["answer_separation"] == {
        "proposals_loaded_before_labels": True,
        "joint_predictions_written_before_evaluation_labels": True,
        "decoder_uses_labels": False,
        "retraining_disabled": True,
        "fit_proposals_verified_document_oof": True,
        "evaluation_proposals_verified_frozen_fit_model": True,
        "proposal_input_hashes_verified": True,
        "proposal_corpus_provenance_verified": True,
    }
    assert set(report["summary"]) == {"fit", "calibration", "test"}
    assert set(report["summary_by_layout_stratum"]["test"]) == {"multicolumn"}
    for summary in report["summary"].values():
        assert summary["selected_relation"]["f1"] > 0.0
        assert summary["segmentation_pairwise"]["f1"] > 0.0
        assert summary["runtime_reorder"] is False

    proposals = sorted(result.proposals_dir.glob("*.joint-graph.json"))
    assert len(proposals) == 8
    for proposal_path in proposals:
        text = proposal_path.read_text(encoding="utf-8")
        proposal = json.loads(text)
        assert proposal["schema"] == JOINT_GRAPH_PROPOSAL_SCHEMA
        assert proposal["runtime_reorder"] is False
        assert len(proposal["paragraph_proposal_sha256"]) == 64
        assert len(proposal["successor_proposal_sha256"]) == 64
        assert "oracle_region_id" not in text
        assert "oracle_scope" not in text
        edges = [(edge["source"], edge["target"]) for edge in proposal["successor_edges"]]
        merged = merge_relation_edge_path_cover(edges)
        assert list(merged.selected_edges) == edges
        for stream in proposal["reading_streams"]:
            assert stream["proposal"]["review_required"] is True


def test_joint_graph_requires_matching_proposals(tmp_path: Path) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    paragraph = benchmark_paragraph_graph(
        train,
        output=tmp_path / "paragraph-report.json",
        proposals_dir=tmp_path / "paragraph-proposals",
        cross_validation_folds=2,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    empty_successor = tmp_path / "empty-successor"
    empty_successor.mkdir()

    with pytest.raises(ValueError, match="missing successor proposal"):
        benchmark_joint_graph(
            train,
            paragraph_proposals_dir=paragraph.proposals_dir,
            successor_proposals_dir=empty_successor,
            output=tmp_path / "joint-report.json",
        )


def test_joint_graph_rejects_runtime_reorder_proposals(tmp_path: Path) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    paragraph = benchmark_paragraph_graph(
        train,
        output=tmp_path / "paragraph-report.json",
        proposals_dir=tmp_path / "paragraph-proposals",
        cross_validation_folds=2,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    successor = benchmark_successor_graph(
        train,
        output=tmp_path / "successor-report.json",
        proposals_dir=tmp_path / "successor-proposals",
        cross_validation_folds=2,
        nearest_candidates=3,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    poisoned = next(successor.proposals_dir.glob("*.successor-graph.json"))
    payload = json.loads(poisoned.read_text(encoding="utf-8"))
    payload["runtime_reorder"] = True
    poisoned.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="runtime_reorder=false"):
        benchmark_joint_graph(
            train,
            paragraph_proposals_dir=paragraph.proposals_dir,
            successor_proposals_dir=successor.proposals_dir,
            output=tmp_path / "joint-report.json",
        )


def test_joint_graph_rejects_full_fit_predictions_as_fit_oof(tmp_path: Path) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    paragraph = benchmark_paragraph_graph(
        train,
        output=tmp_path / "paragraph-report.json",
        proposals_dir=tmp_path / "paragraph-proposals",
        cross_validation_folds=2,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    successor = benchmark_successor_graph(
        train,
        output=tmp_path / "successor-report.json",
        proposals_dir=tmp_path / "successor-proposals",
        cross_validation_folds=2,
        nearest_candidates=3,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    poisoned = next(
        path
        for path in paragraph.proposals_dir.glob("*.paragraph-graph.json")
        if json.loads(path.read_text(encoding="utf-8"))["partition"] == "fit"
    )
    payload = json.loads(poisoned.read_text(encoding="utf-8"))
    payload["prediction_provenance"]["prediction_mode"] = "serialized-fit-model"
    payload["prediction_provenance"]["fit_model_training"] = "all-fit-documents"
    poisoned.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="prediction_mode.*document-oof"):
        benchmark_joint_graph(
            train,
            paragraph_proposals_dir=paragraph.proposals_dir,
            successor_proposals_dir=successor.proposals_dir,
            output=tmp_path / "joint-report.json",
        )


def test_generated_head_proposals_use_expected_schemas(tmp_path: Path) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    paragraph = benchmark_paragraph_graph(
        train,
        output=tmp_path / "paragraph-report.json",
        proposals_dir=tmp_path / "paragraph-proposals",
        cross_validation_folds=2,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    successor = benchmark_successor_graph(
        train,
        output=tmp_path / "successor-report.json",
        proposals_dir=tmp_path / "successor-proposals",
        cross_validation_folds=2,
        nearest_candidates=3,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    paragraph_payload = json.loads(
        next(paragraph.proposals_dir.glob("*.paragraph-graph.json")).read_text(
            encoding="utf-8"
        )
    )
    successor_payload = json.loads(
        next(successor.proposals_dir.glob("*.successor-graph.json")).read_text(
            encoding="utf-8"
        )
    )
    assert paragraph_payload["schema"] == PARAGRAPH_GRAPH_PROPOSAL_SCHEMA
    assert successor_payload["schema"] == SUCCESSOR_GRAPH_PROPOSAL_SCHEMA


def _write_corpus(root: Path, documents: list[tuple[str, str]]) -> Path:
    inputs = root / "inputs"
    labels_dir = root / "labels"
    inputs.mkdir(parents=True)
    labels_dir.mkdir(parents=True)
    samples = []
    for index, (document_id, partition) in enumerate(documents):
        sample_id = f"{document_id}-page"
        input_payload, labels = _page_payload(sample_id, offset=index % 2)
        input_path = inputs / f"{sample_id}.json"
        label_path = labels_dir / f"{sample_id}.json"
        _write_json(input_path, input_payload)
        _write_json(label_path, labels)
        samples.append(
            {
                "id": sample_id,
                "document_id": document_id,
                "page_index": 0,
                "partition": partition,
                "layout_stratum": "multicolumn",
                "input": str(input_path.relative_to(root)),
                "input_sha256": _sha256(input_path),
                "labels": str(label_path.relative_to(root)),
                "labels_sha256": _sha256(label_path),
            }
        )
    manifest = {
        "schema": PROVIDER_HIERARCHY_CORPUS_SCHEMA,
        "sample_count": len(samples),
        "partition_counts": {
            partition: sum(item["partition"] == partition for item in samples)
            for partition in sorted({item["partition"] for item in samples})
        },
        "inference_inputs_are_answer_free": True,
        "samples": samples,
    }
    _write_json(root / "provider_hierarchy_corpus_manifest.json", manifest)
    return root


def _page_payload(sample_id: str, *, offset: int = 0) -> tuple[dict, dict]:
    elements = []
    memberships = []
    successor_edges = []
    previous_id = None
    previous_region = None
    for paragraph_index, start_x in enumerate((60, 330)):
        region_id = f"oracle-{paragraph_index}"
        for line_index in range(3):
            element_id = f"{sample_id}-p{paragraph_index}-l{line_index}"
            elements.append(
                {
                    "id": element_id,
                    "box": [
                        start_x,
                        80 + offset + line_index * 18,
                        start_x + 210 - line_index * 8,
                        91 + offset + line_index * 18,
                    ],
                    "role": "text",
                    "text": (
                        f"paragraph {paragraph_index} continuation {line_index},"
                        if line_index < 2
                        else f"paragraph {paragraph_index} ending."
                    ),
                }
            )
            memberships.append(
                {"element_id": element_id, "oracle_region_id": region_id}
            )
            if previous_id is not None:
                successor_edges.append(
                    {
                        "source": previous_id,
                        "target": element_id,
                        "oracle_scope": (
                            "within-oracle-region"
                            if previous_region == region_id
                            else "cross-oracle-region"
                        ),
                    }
                )
            previous_id = element_id
            previous_region = region_id
    input_payload = {
        "schema": HIERARCHY_INPUT_SCHEMA,
        "id": sample_id,
        "page_index": 0,
        "width": 600,
        "height": 800,
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": list(reversed(elements)),
        "regions": [],
    }
    labels = {
        "schema": PROVIDER_HIERARCHY_LABEL_SCHEMA,
        "id": sample_id,
        "memberships": memberships,
        "successor_edges": successor_edges,
    }
    return input_payload, labels


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
