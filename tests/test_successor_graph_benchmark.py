from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scriptorium.hierarchical_order_benchmark import HIERARCHY_INPUT_SCHEMA
from scriptorium.graph_provenance import DOCUMENT_OOF_MODE, FROZEN_FIT_MODEL_MODE
from scriptorium.provider_hierarchy_benchmark import (
    PROVIDER_HIERARCHY_CORPUS_SCHEMA,
    PROVIDER_HIERARCHY_LABEL_SCHEMA,
)
from scriptorium.relation_order import merge_relation_edge_path_cover
from scriptorium.successor_graph_benchmark import (
    SUCCESSOR_GRAPH_BENCHMARK_SCHEMA,
    SUCCESSOR_GRAPH_PROPOSAL_SCHEMA,
    _page_candidates,
    benchmark_successor_graph,
)


def test_successor_graph_is_answer_separated_and_scores_independent_test(
    tmp_path: Path,
) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    test = _write_corpus(
        tmp_path / "test",
        [(f"test-{index}", "test") for index in range(2)],
    )

    result = benchmark_successor_graph(
        train,
        output=tmp_path / "report.json",
        proposals_dir=tmp_path / "proposals",
        test_corpus_dir=test,
        cross_validation_folds=2,
        nearest_candidates=3,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )

    report = result.report
    assert report["schema"] == SUCCESSOR_GRAPH_BENCHMARK_SCHEMA
    assert report["cross_validation_unit"] == "document"
    assert report["answer_separation"] == {
        "all_inputs_loaded_before_fit_labels": True,
        "fit_labels_role": "document-OOF training and threshold selection",
        "evaluation_predictions_written_before_evaluation_labels": True,
        "candidate_generation_uses_labels": False,
        "paragraph_membership_labels_used_as_features": False,
    }
    assert report["runtime_reorder"] is False
    assert set(report["summary"]) == {"fit_oof", "calibration", "test"}
    assert set(report["summary_by_layout_stratum"]["test"]) == {"multicolumn"}
    for summary in report["summary"].values():
        assert summary["candidate_recall"]["recall"] == 1.0
        assert summary["selected_relation"]["precision"] >= 0.5
        assert summary["selected_relation"]["f1"] > 0.0
        assert summary["runtime_reorder"] is False

    proposals = sorted(result.proposals_dir.glob("*.successor-graph.json"))
    assert len(proposals) == 8
    for proposal_path in proposals:
        proposal_text = proposal_path.read_text(encoding="utf-8")
        proposal = json.loads(proposal_text)
        assert proposal["schema"] == SUCCESSOR_GRAPH_PROPOSAL_SCHEMA
        assert proposal["runtime_reorder"] is False
        assert proposal["prediction_provenance"]["prediction_mode"] == (
            DOCUMENT_OOF_MODE if proposal["partition"] == "fit" else FROZEN_FIT_MODEL_MODE
        )
        assert len(proposal["prediction_provenance"]["input_sha256"]) == 64
        assert "oracle_scope" not in proposal_text
        assert "oracle_region_id" not in proposal_text
        edges = [
            (edge["source"], edge["target"])
            for edge in proposal["successor_edges"]
        ]
        merged = merge_relation_edge_path_cover(edges)
        assert list(merged.selected_edges) == edges

    assert all(
        len(artifact["sha256"]) == 64
        for artifacts in report["proposal_artifacts"].values()
        for artifact in artifacts
    )


def test_successor_graph_candidates_ignore_provider_regions_and_input_order() -> None:
    payload, _labels = _page_payload("sample")
    altered = json.loads(json.dumps(payload))
    altered["regions"] = [
        {
            "id": "provider-noise",
            "box": [0, 0, 600, 800],
            "role": "unknown",
            "text": "provider grouping and order must not enter successor features",
        }
    ]
    altered["elements"].reverse()

    assert _page_candidates(payload) == _page_candidates(altered)


def test_successor_graph_rejects_non_degree_one_labels(tmp_path: Path) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    manifest_path = train / "provider_hierarchy_corpus_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sample = manifest["samples"][0]
    label_path = train / sample["labels"]
    labels = json.loads(label_path.read_text(encoding="utf-8"))
    labels["successor_edges"].append(
        {
            "source": labels["successor_edges"][0]["source"],
            "target": labels["successor_edges"][1]["target"],
            "oracle_scope": "within-oracle-region",
        }
    )
    _write_json(label_path, labels)
    sample["labels_sha256"] = _sha256(label_path)
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="must satisfy degree one"):
        benchmark_successor_graph(
            train,
            output=tmp_path / "report.json",
            cross_validation_folds=2,
            nearest_candidates=3,
            minimum_edge_precision=0.5,
            minimum_selected_edges=1,
        )


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
