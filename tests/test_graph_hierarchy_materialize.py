from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scriptorium.hierarchical_order_benchmark import (
    HIERARCHY_CORPUS_SCHEMA,
    HIERARCHY_INPUT_SCHEMA,
    HIERARCHY_LABEL_SCHEMA,
)
from scriptorium.paragraph_graph_benchmark import benchmark_paragraph_graph
from scriptorium.provider_hierarchy_benchmark import (
    PROVIDER_HIERARCHY_CORPUS_SCHEMA,
    PROVIDER_HIERARCHY_LABEL_SCHEMA,
    materialize_graph_hierarchy_corpus,
)
from scriptorium.successor_graph_benchmark import benchmark_successor_graph


def test_materialize_graph_hierarchy_is_answer_separated_and_graph_ready(
    tmp_path: Path,
) -> None:
    source = _write_hierarchy_corpus(
        tmp_path / "hierarchy",
        [(f"fit-{index}", "fit") for index in range(4)]
        + [(f"calibration-{index}", "calibration") for index in range(2)],
    )

    result = materialize_graph_hierarchy_corpus(
        source,
        tmp_path / "graph-hierarchy",
    )

    assert result.manifest["schema"] == PROVIDER_HIERARCHY_CORPUS_SCHEMA
    assert result.manifest["inference_inputs_are_answer_free"] is True
    assert result.manifest["provider"] == "none-graph-hierarchy-export"
    assert result.manifest["partition_counts"] == {"calibration": 2, "fit": 4}
    assert result.manifest["answer_separation"]["labels_opened_after_all_inputs_written"] is True

    for sample in result.manifest["samples"]:
        input_path = result.out_dir / sample["input"]
        label_path = result.out_dir / sample["labels"]
        input_payload = json.loads(input_path.read_text(encoding="utf-8"))
        labels = json.loads(label_path.read_text(encoding="utf-8"))
        assert input_payload["schema"] == HIERARCHY_INPUT_SCHEMA
        assert input_payload["regions"] == []
        assert "oracle_region_id" not in input_path.read_text(encoding="utf-8")
        assert labels["schema"] == PROVIDER_HIERARCHY_LABEL_SCHEMA
        assert {item["element_id"] for item in labels["memberships"]} == {
            element["id"] for element in input_payload["elements"]
        }
        assert labels["successor_edges"]
        assert all("oracle_scope" in edge for edge in labels["successor_edges"])

    paragraph = benchmark_paragraph_graph(
        result.out_dir,
        output=tmp_path / "paragraph-report.json",
        proposals_dir=tmp_path / "paragraph-proposals",
        cross_validation_folds=2,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    successor = benchmark_successor_graph(
        result.out_dir,
        output=tmp_path / "successor-report.json",
        proposals_dir=tmp_path / "successor-proposals",
        cross_validation_folds=2,
        nearest_candidates=3,
        minimum_edge_precision=0.5,
        minimum_selected_edges=1,
    )
    assert paragraph.report["runtime_reorder"] is False
    assert successor.report["runtime_reorder"] is False
    assert paragraph.report["summary"]["fit_oof"]["segmentation_pairwise"]["f1"] > 0.0
    assert successor.report["summary"]["fit_oof"]["selected_relation"]["f1"] > 0.0


def _write_hierarchy_corpus(root: Path, documents: list[tuple[str, str]]) -> Path:
    inputs = root / "inputs"
    labels_dir = root / "labels"
    inputs.mkdir(parents=True)
    labels_dir.mkdir(parents=True)
    samples = []
    for index, (document_id, partition) in enumerate(documents):
        sample_id = f"{document_id}-page"
        input_payload, labels = _page_payload(sample_id, offset=index % 2)
        input_path = inputs / f"{sample_id}.hierarchy-input.json"
        label_path = labels_dir / f"{sample_id}.hierarchy-labels.json"
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
                "fine_element_count": len(input_payload["elements"]),
                "coarse_region_count": 2,
            }
        )
    manifest = {
        "schema": HIERARCHY_CORPUS_SCHEMA,
        "source_dataset": "fixture",
        "source_schema": "fixture",
        "sample_count": len(samples),
        "partition_counts": {
            partition: sum(item["partition"] == partition for item in samples)
            for partition in sorted({item["partition"] for item in samples})
        },
        "inference_inputs_are_answer_free": True,
        "answer_separation": {
            "input": "fine text/geometry only",
            "labels": "oracle membership and successors",
        },
        "samples": samples,
    }
    _write_json(root / "hierarchical_order_corpus_manifest.json", manifest)
    return root


def _page_payload(sample_id: str, *, offset: int = 0) -> tuple[dict, dict]:
    elements = []
    memberships = []
    within_edges = []
    cross_edges = []
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
                    "text": f"paragraph {paragraph_index} line {line_index}",
                }
            )
            memberships.append({"element_id": element_id, "region_id": region_id})
            if previous_id is not None:
                edge = {"source": previous_id, "target": element_id}
                if previous_region == region_id:
                    edge["region_id"] = region_id
                    within_edges.append(edge)
                else:
                    edge["source_region_id"] = previous_region
                    edge["target_region_id"] = region_id
                    cross_edges.append(edge)
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
        "regions": [
            {
                "id": "oracle-0",
                "box": [60, 80, 270, 140],
                "role": "text",
                "text": "left",
            },
            {
                "id": "oracle-1",
                "box": [330, 80, 540, 140],
                "role": "text",
                "text": "right",
            },
        ],
    }
    labels = {
        "schema": HIERARCHY_LABEL_SCHEMA,
        "id": sample_id,
        "membership_policy": "complete oracle block membership",
        "within_region_policy": "complete annotation-local line succession",
        "cross_region_policy": "partial Comp-HRDoc reading-order relations",
        "memberships": memberships,
        "within_region_successor_edges": within_edges,
        "cross_region_transition_edges": cross_edges,
    }
    return input_payload, labels


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
