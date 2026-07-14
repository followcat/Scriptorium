from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import scriptorium.cli as cli
from scriptorium.hierarchical_order_benchmark import (
    HIERARCHY_BENCHMARK_SCHEMA,
    HIERARCHY_CORPUS_SCHEMA,
    HIERARCHY_INPUT_SCHEMA,
    HIERARCHY_LABEL_SCHEMA,
    benchmark_hierarchical_order_corpus,
    materialize_comphrdoc_hierarchy_corpus,
)


_FORBIDDEN_INPUT_KEYS = {
    "block_id",
    "member_ids",
    "memberships",
    "order",
    "reading_order",
    "ro_linkings",
    "successor_edges",
    "within_region_successor_edges",
    "cross_region_transition_edges",
}


def test_materialized_hierarchy_corpus_is_answer_separated_and_scoreable(
    tmp_path: Path,
) -> None:
    source = _write_source_corpus(tmp_path / "source")
    corpus = tmp_path / "hierarchy"

    materialized = materialize_comphrdoc_hierarchy_corpus(source, corpus)
    sample = materialized.manifest["samples"][0]
    input_payload = _read_json(corpus / sample["input"])
    labels = _read_json(corpus / sample["labels"])

    assert materialized.manifest["schema"] == HIERARCHY_CORPUS_SCHEMA
    assert materialized.manifest["inference_inputs_are_answer_free"] is True
    assert input_payload["schema"] == HIERARCHY_INPUT_SCHEMA
    assert input_payload["element_granularity"] == "fine"
    assert input_payload["region_granularity"] == "coarse"
    assert len(input_payload["elements"]) == 4
    assert len(input_payload["regions"]) == 2
    assert _recursive_keys(input_payload).isdisjoint(_FORBIDDEN_INPUT_KEYS)
    assert labels["schema"] == HIERARCHY_LABEL_SCHEMA
    assert len(labels["memberships"]) == 4
    assert len(labels["within_region_successor_edges"]) == 2
    assert len(labels["cross_region_transition_edges"]) == 1

    result = benchmark_hierarchical_order_corpus(
        corpus,
        output=tmp_path / "report.json",
        proposals_dir=tmp_path / "proposals",
    )

    assert result.report["schema"] == HIERARCHY_BENCHMARK_SCHEMA
    assert result.report["runtime_reorder"] is False
    assert result.report["labels_opened_after_all_predictions"] is True
    assert result.report["coarse_order_model"] == (
        "fine-relation-graph-boundary"
    )
    assert result.report["prediction_policy"] == (
        "hierarchical-review-only-relation-dag-with-continuity-membership-v2"
    )
    assert result.report["transition_representation"] == (
        "partial-dag-boundary-aligned-review-relations"
    )
    assert result.report["summary"]["membership"]["recall"] == 1.0
    assert result.report["summary"]["hierarchy_within"]["f1"] == 1.0
    # Two lines per column are intentionally insufficient for a graph handoff.
    assert result.report["summary"]["hierarchy_region_cross"]["f1"] == 0.0
    assert result.report["summary"]["flat_region_cross"]["f1"] == 1.0
    assert result.report["diagnostic_totals"][
        "missing_cross_region_evidence_page_count"
    ] == 1
    assert result.report["diagnostic_totals"][
        "relation_base_continuity_membership_count"
    ] == 0
    assert result.report["promotion_decision"] == (
        "development-benchmark-only-review-only"
    )


def test_materialization_is_input_order_invariant_and_label_sensitive(
    tmp_path: Path,
) -> None:
    first_source = _write_source_corpus(tmp_path / "source-a")
    second_source = _write_source_corpus(
        tmp_path / "source-b",
        reverse_nodes=True,
    )
    changed_source = _write_source_corpus(
        tmp_path / "source-c",
        cross_edge=("right-two", "left-one"),
    )

    first = materialize_comphrdoc_hierarchy_corpus(
        first_source,
        tmp_path / "hierarchy-a",
    )
    second = materialize_comphrdoc_hierarchy_corpus(
        second_source,
        tmp_path / "hierarchy-b",
    )
    changed = materialize_comphrdoc_hierarchy_corpus(
        changed_source,
        tmp_path / "hierarchy-c",
    )

    first_sample = first.manifest["samples"][0]
    second_sample = second.manifest["samples"][0]
    changed_sample = changed.manifest["samples"][0]
    first_input = _read_json(first.out_dir / first_sample["input"])
    second_input = _read_json(second.out_dir / second_sample["input"])
    changed_input = _read_json(changed.out_dir / changed_sample["input"])
    first_labels = _read_json(first.out_dir / first_sample["labels"])
    second_labels = _read_json(second.out_dir / second_sample["labels"])
    changed_labels = _read_json(changed.out_dir / changed_sample["labels"])

    assert second_input == first_input == changed_input
    assert second_labels == first_labels
    assert changed_labels != first_labels


def test_materializer_and_benchmark_enforce_two_phase_reads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source_corpus(tmp_path / "source", sample_count=2)
    corpus = tmp_path / "hierarchy"
    events: list[str] = []
    original_read_text = Path.read_text

    def tracked_read_text(path: Path, *args, **kwargs):
        if path.parent.name in {"structure", "semantic", "inputs", "labels"}:
            events.append(path.parent.name)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read_text)
    materialize_comphrdoc_hierarchy_corpus(source, corpus)

    assert events == ["structure", "structure", "semantic", "semantic"]
    events.clear()
    benchmark_hierarchical_order_corpus(
        corpus,
        output=tmp_path / "report.json",
        proposals_dir=tmp_path / "proposals",
    )
    assert events == ["inputs", "inputs", "labels", "labels"]


def test_hierarchy_benchmark_rejects_tampering_and_path_traversal(
    tmp_path: Path,
) -> None:
    source = _write_source_corpus(tmp_path / "source")
    corpus = tmp_path / "hierarchy"
    materialized = materialize_comphrdoc_hierarchy_corpus(source, corpus)
    sample = materialized.manifest["samples"][0]
    input_path = corpus / sample["input"]
    input_path.write_text(input_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(ValueError, match="input SHA-256 mismatch"):
        benchmark_hierarchical_order_corpus(corpus)

    materialize_comphrdoc_hierarchy_corpus(source, corpus)
    manifest_path = corpus / "hierarchical_order_corpus_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["samples"][0]["input"] = "../../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="must stay inside its corpus"):
        benchmark_hierarchical_order_corpus(corpus)


def test_hierarchy_benchmark_cli_commands(tmp_path: Path) -> None:
    source = _write_source_corpus(tmp_path / "source")
    corpus = tmp_path / "hierarchy"
    report = tmp_path / "report.json"
    proposals = tmp_path / "proposals"
    runner = CliRunner()

    materialize_result = runner.invoke(
        cli.app,
        [
            "materialize-comphrdoc-hierarchy",
            str(source),
            "--output",
            str(corpus),
        ],
    )
    assert materialize_result.exit_code == 0, materialize_result.output
    assert "Hierarchy samples: 1" in materialize_result.output

    benchmark_result = runner.invoke(
        cli.app,
        [
            "benchmark-hierarchical-order-corpus",
            str(corpus),
            "--output",
            str(report),
            "--proposals-dir",
            str(proposals),
        ],
    )
    assert benchmark_result.exit_code == 0, benchmark_result.output
    assert "Membership (accuracy/coverage): 1.0/1.0" in benchmark_result.output
    assert "Within-region successor (precision/recall/F1): 1.0/1.0/1.0" in (
        benchmark_result.output
    )
    assert report.is_file()


def _write_source_corpus(
    root: Path,
    *,
    reverse_nodes: bool = False,
    cross_edge: tuple[str, str] = ("left-two", "right-one"),
    sample_count: int = 1,
) -> Path:
    structure_dir = root / "structure"
    semantic_dir = root / "semantic"
    structure_dir.mkdir(parents=True)
    semantic_dir.mkdir(parents=True)
    samples = []
    for sample_index in range(sample_count):
        sample_id = f"fixture_{sample_index}"
        nodes = _source_nodes()
        if reverse_nodes:
            nodes.reverse()
        structure = {
            "schema": "scriptorium-comphrdoc-layout-anchor-only/v1",
            "uid": sample_id,
            "img": {"fname": f"images/{sample_id}.png", "width": 200, "height": 120},
            "document": nodes,
            "relations_removed": True,
        }
        semantic = {
            "schema": "scriptorium-comphrdoc-reading-order/v1",
            "uid": sample_id,
            "img": copy.deepcopy(structure["img"]),
            "document": copy.deepcopy(nodes),
            "ro_linkings": [
                ["left-one", "left-two"],
                ["right-one", "right-two"],
                list(cross_edge),
            ],
        }
        structure_path = structure_dir / f"{sample_id}.structure.json"
        semantic_path = semantic_dir / f"{sample_id}.semantic-order.json"
        structure_path.write_text(json.dumps(structure), encoding="utf-8")
        semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
        samples.append(
            {
                "id": sample_id,
                "document_id": "fixture-document",
                "page_index": sample_index,
                "partition": "fit" if sample_index == 0 else "calibration",
                "layout_stratum": "multicolumn",
                "structure": str(structure_path.relative_to(root)),
                "semantic_sidecar": str(semantic_path.relative_to(root)),
            }
        )
    manifest = {
        "schema": "scriptorium-comphrdoc-provider-calibration/v1",
        "dataset": "Comp-HRDoc train fixture",
        "annotation_archive_sha256": "a" * 64,
        "selection": "fixture-answer-free-selection",
        "split_policy": "fixture-document-split",
        "samples": samples,
    }
    (root / "comphrdoc_benchmark_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return root


def _source_nodes() -> list[dict]:
    return [
        {
            "id": "left-one",
            "box": [10, 10, 80, 20],
            "text": "Left column one",
            "type": "text",
            "block_id": "left-block",
        },
        {
            "id": "left-two",
            "box": [10, 35, 80, 45],
            "text": "Left column two",
            "type": "text",
            "block_id": "left-block",
        },
        {
            "id": "right-one",
            "box": [110, 10, 180, 20],
            "text": "Right column one",
            "type": "text",
            "block_id": "right-block",
        },
        {
            "id": "right-two",
            "box": [110, 35, 180, 45],
            "text": "Right column two",
            "type": "text",
            "block_id": "right-block",
        },
    ]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _recursive_keys(value) -> set[str]:
    keys: set[str] = set()
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            keys.update(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return keys
