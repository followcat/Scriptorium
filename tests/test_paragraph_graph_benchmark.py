from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scriptorium.hierarchical_order_benchmark import HIERARCHY_INPUT_SCHEMA
from scriptorium.paragraph_graph_benchmark import (
    PARAGRAPH_GRAPH_BENCHMARK_SCHEMA,
    PARAGRAPH_GRAPH_PROPOSAL_SCHEMA,
    _page_candidates,
    benchmark_paragraph_graph,
)
from scriptorium.provider_hierarchy_benchmark import (
    PROVIDER_HIERARCHY_CORPUS_SCHEMA,
    PROVIDER_HIERARCHY_LABEL_SCHEMA,
)


def test_paragraph_graph_is_answer_separated_and_scores_independent_test(
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

    result = benchmark_paragraph_graph(
        train,
        output=tmp_path / "report.json",
        proposals_dir=tmp_path / "proposals",
        test_corpus_dir=test,
        cross_validation_folds=2,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )

    report = result.report
    assert report["schema"] == PARAGRAPH_GRAPH_BENCHMARK_SCHEMA
    assert report["cross_validation_unit"] == "document"
    assert report["answer_separation"] == {
        "all_inputs_loaded_before_fit_labels": True,
        "fit_labels_role": "document-OOF training and threshold selection",
        "evaluation_predictions_written_before_evaluation_labels": True,
        "candidate_generation_uses_labels": False,
    }
    assert report["runtime_reorder"] is False
    assert set(report["summary"]) == {"fit_oof", "calibration", "test"}
    assert set(report["summary_by_layout_stratum"]) == {
        "fit_oof",
        "calibration",
        "test",
    }
    assert set(report["summary_by_layout_stratum"]["test"]) == {"multicolumn"}
    for summary in report["summary"].values():
        assert summary["candidate_page_count"] == summary["page_count"]
        assert summary["segmentation_label_page_count"] == summary["page_count"]
        assert summary["selected_edge"]["precision"] >= 0.5
        assert summary["segmentation_pairwise"]["f1"] > 0.0
        assert summary["runtime_reorder"] is False
    proposals = sorted(result.proposals_dir.glob("*.paragraph-graph.json"))
    assert len(proposals) == 8
    for proposal_path in proposals:
        proposal_text = proposal_path.read_text(encoding="utf-8")
        proposal = json.loads(proposal_text)
        assert proposal["schema"] == PARAGRAPH_GRAPH_PROPOSAL_SCHEMA
        assert proposal["runtime_reorder"] is False
        assert "oracle_region_id" not in proposal_text


def test_paragraph_graph_candidates_ignore_provider_regions() -> None:
    payload, _labels = _page_payload("sample")
    altered = json.loads(json.dumps(payload))
    altered["regions"] = [
        {
            "id": "provider-noise",
            "box": [0, 0, 600, 800],
            "role": "unknown",
            "text": "provider order and grouping must not enter line features",
        }
    ]

    assert _page_candidates(payload) == _page_candidates(altered)


def test_paragraph_graph_candidates_ignore_input_element_order() -> None:
    payload, _labels = _page_payload("sample")
    reversed_payload = json.loads(json.dumps(payload))
    reversed_payload["elements"].reverse()

    assert _page_candidates(payload) == _page_candidates(reversed_payload)


def test_paragraph_graph_rejects_duplicate_sample_ids(tmp_path: Path) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    manifest_path = train / "provider_hierarchy_corpus_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    duplicate = dict(manifest["samples"][0])
    duplicate["document_id"] = "duplicate-document"
    manifest["samples"].append(duplicate)
    manifest["sample_count"] += 1
    manifest["partition_counts"]["fit"] += 1
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="sample ids must be globally unique"):
        benchmark_paragraph_graph(
            train,
            output=tmp_path / "report.json",
            cross_validation_folds=2,
            minimum_edge_precision=0.5,
            minimum_selected_edges=1,
        )


def test_paragraph_graph_rejects_duplicate_membership_labels(tmp_path: Path) -> None:
    train = _write_corpus(
        tmp_path / "train",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )
    manifest = json.loads(
        (train / "provider_hierarchy_corpus_manifest.json").read_text(encoding="utf-8")
    )
    sample = manifest["samples"][0]
    label_path = train / sample["labels"]
    labels = json.loads(label_path.read_text(encoding="utf-8"))
    labels["memberships"].append(dict(labels["memberships"][0]))
    _write_json(label_path, labels)
    sample["labels_sha256"] = _sha256(label_path)
    _write_json(train / "provider_hierarchy_corpus_manifest.json", manifest)

    with pytest.raises(ValueError, match="membership labels must be unique"):
        benchmark_paragraph_graph(
            train,
            output=tmp_path / "report.json",
            cross_validation_folds=2,
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
    for paragraph_index, start_y in enumerate((80 + offset, 190 + offset)):
        for line_index in range(3):
            element_id = f"{sample_id}-p{paragraph_index}-l{line_index}"
            elements.append(
                {
                    "id": element_id,
                    "box": [
                        60 + paragraph_index * 4,
                        start_y + line_index * 18,
                        300 - line_index * 8,
                        start_y + line_index * 18 + 11,
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
                {
                    "element_id": element_id,
                    "oracle_region_id": f"oracle-{paragraph_index}",
                }
            )
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
        "successor_edges": [],
    }
    return input_payload, labels


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
