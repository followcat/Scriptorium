from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import scriptorium.cli as cli
from scriptorium.hierarchical_order_benchmark import (
    HIERARCHY_CORPUS_SCHEMA,
    HIERARCHY_INPUT_SCHEMA,
    HIERARCHY_LABEL_SCHEMA,
)
from scriptorium.provider_hierarchy_benchmark import (
    PROVIDER_HIERARCHY_BENCHMARK_SCHEMA,
    PROVIDER_HIERARCHY_CORPUS_SCHEMA,
    PROVIDER_HIERARCHY_LABEL_SCHEMA,
    benchmark_provider_hierarchy_corpus,
    materialize_provider_hierarchy_corpus,
)


_FORBIDDEN_INPUT_KEYS = {
    "member_ids",
    "memberships",
    "oracle_region_id",
    "provider_order",
    "reading_order",
    "ro_linkings",
    "successor_edges",
}


def test_provider_hierarchy_materialization_is_answer_separated_and_invariant(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source_hierarchy_corpus(tmp_path / "source", sample_count=2)
    providers = _write_provider_corpus(tmp_path / "provider", sample_count=2)
    events: list[str] = []
    original_read_text = Path.read_text

    def tracked_read_text(path: Path, *args, **kwargs):
        if path.parent == source / "inputs":
            events.append("input")
        elif path.parent == source / "labels":
            events.append("label")
        elif path.parent == providers and path.name.endswith(".structure.json"):
            events.append("provider")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read_text)
    result = materialize_provider_hierarchy_corpus(
        source,
        providers,
        tmp_path / "materialized",
    )

    assert result.manifest["schema"] == PROVIDER_HIERARCHY_CORPUS_SCHEMA
    assert result.manifest["provider"] == "fixture-layout"
    assert result.manifest["inference_inputs_are_answer_free"] is True
    assert events == ["input", "provider", "input", "provider", "label", "label"]
    sample = result.manifest["samples"][0]
    input_payload = _read_json(result.out_dir / sample["input"])
    labels = _read_json(result.out_dir / sample["labels"])
    assert input_payload["schema"] == HIERARCHY_INPUT_SCHEMA
    assert len(input_payload["elements"]) == 6
    assert len(input_payload["regions"]) == 2
    assert {region["role"] for region in input_payload["regions"]} == {"text"}
    assert _recursive_keys(input_payload).isdisjoint(_FORBIDDEN_INPUT_KEYS)
    assert labels["schema"] == PROVIDER_HIERARCHY_LABEL_SCHEMA
    assert len(labels["memberships"]) == 6
    assert len(labels["successor_edges"]) == 5

    first_inference = _without_source_hashes(input_payload)
    provider_path = providers / "sample-0.structure.json"
    changed_provider = _read_json(provider_path)
    changed_provider["pages"][0]["elements"][0]["provider_order"] = 99
    changed_provider["pages"][0]["successor_edges"] = [
        {"source": "provider-right", "target": "provider-left"}
    ]
    _write_json(provider_path, changed_provider)
    second = materialize_provider_hierarchy_corpus(
        source,
        providers,
        tmp_path / "provider-order-changed",
    )
    second_input = _read_json(second.out_dir / second.manifest["samples"][0]["input"])
    assert _without_source_hashes(second_input) == first_inference

    source_manifest_path = source / "hierarchical_order_corpus_manifest.json"
    source_manifest = _read_json(source_manifest_path)
    source_label_path = source / source_manifest["samples"][0]["labels"]
    changed_labels = _read_json(source_label_path)
    changed_labels["cross_region_transition_edges"][0]["target"] = "right-1"
    _write_json(source_label_path, changed_labels)
    source_manifest["samples"][0]["labels_sha256"] = _sha256(source_label_path)
    _write_json(source_manifest_path, source_manifest)
    third = materialize_provider_hierarchy_corpus(
        source,
        providers,
        tmp_path / "labels-changed",
    )
    third_input = _read_json(third.out_dir / third.manifest["samples"][0]["input"])
    assert _without_source_hashes(third_input) == _without_source_hashes(second_input)


def test_provider_hierarchy_benchmark_scores_relations_after_provider_segmentation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source_hierarchy_corpus(tmp_path / "source", sample_count=2)
    providers = _write_provider_corpus(tmp_path / "provider", sample_count=2)
    corpus = materialize_provider_hierarchy_corpus(
        source,
        providers,
        tmp_path / "corpus",
    )
    events: list[str] = []
    original_read_text = Path.read_text

    def tracked_read_text(path: Path, *args, **kwargs):
        if path.parent == corpus.out_dir / "inputs":
            events.append("input")
        elif path.parent == corpus.out_dir / "labels":
            events.append("label")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read_text)
    result = benchmark_provider_hierarchy_corpus(
        corpus.out_dir,
        output=tmp_path / "report.json",
        proposals_dir=tmp_path / "proposals",
    )

    assert events == ["input", "input", "label", "label"]
    assert result.report["schema"] == PROVIDER_HIERARCHY_BENCHMARK_SCHEMA
    assert result.report["runtime_reorder"] is False
    assert result.report["labels_opened_after_all_predictions"] is True
    summary = result.report["summary"]
    assert summary["assignment_coverage"] == {
        "assigned": 12,
        "labels": 12,
        "coverage": 1.0,
        "unassigned": 0,
    }
    assert summary["segmentation_pairwise"]["f1"] == 1.0
    assert summary["provider_hierarchy_relation"]["f1"] == 1.0
    assert summary["truth_within_recovery"]["recall"] == 1.0
    assert summary["truth_cross_recovery"]["recall"] == 1.0
    assert result.report["groups"]["partition"]["fit"][
        "provider_hierarchy_relation"
    ]["f1"] == 1.0


def test_provider_hierarchy_rejects_provenance_mismatch_and_tampering(
    tmp_path: Path,
) -> None:
    source = _write_source_hierarchy_corpus(tmp_path / "source")
    providers = _write_provider_corpus(tmp_path / "provider")
    provider_manifest_path = providers / "paddle_layout_corpus_run.json"
    provider_manifest = _read_json(provider_manifest_path)
    provider_manifest["corpus_manifest_sha256"] = "f" * 64
    _write_json(provider_manifest_path, provider_manifest)
    with pytest.raises(ValueError, match="provenance differ"):
        materialize_provider_hierarchy_corpus(
            source,
            providers,
            tmp_path / "mismatch",
        )

    _write_provider_corpus(providers, overwrite=True)
    corpus = materialize_provider_hierarchy_corpus(
        source,
        providers,
        tmp_path / "corpus",
    )
    sample = corpus.manifest["samples"][0]
    input_path = corpus.out_dir / sample["input"]
    input_path.write_text(input_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="input SHA-256 mismatch"):
        benchmark_provider_hierarchy_corpus(corpus.out_dir)


def test_provider_hierarchy_keeps_unlabelled_relation_pages_in_segmentation(
    tmp_path: Path,
) -> None:
    source = _write_source_hierarchy_corpus(tmp_path / "source")
    source_manifest_path = source / "hierarchical_order_corpus_manifest.json"
    source_manifest = _read_json(source_manifest_path)
    label_path = source / source_manifest["samples"][0]["labels"]
    labels = _read_json(label_path)
    labels["within_region_successor_edges"] = []
    labels["cross_region_transition_edges"] = []
    _write_json(label_path, labels)
    source_manifest["samples"][0]["labels_sha256"] = _sha256(label_path)
    _write_json(source_manifest_path, source_manifest)
    providers = _write_provider_corpus(tmp_path / "provider")
    corpus = materialize_provider_hierarchy_corpus(
        source,
        providers,
        tmp_path / "corpus",
    )

    result = benchmark_provider_hierarchy_corpus(corpus.out_dir)

    relation = result.report["summary"]["provider_hierarchy_relation"]
    assert relation["labels"] == 0
    assert relation["scorable"] == 0
    assert relation["unscored"] == relation["predicted"]
    assert result.report["summary"]["segmentation_pairwise"]["f1"] == 1.0


def test_provider_hierarchy_cli_commands(tmp_path: Path) -> None:
    source = _write_source_hierarchy_corpus(tmp_path / "source")
    providers = _write_provider_corpus(tmp_path / "provider")
    corpus = tmp_path / "corpus"
    runner = CliRunner()

    materialized = runner.invoke(
        cli.app,
        [
            "materialize-provider-hierarchy",
            str(source),
            str(providers),
            "--output",
            str(corpus),
        ],
    )
    assert materialized.exit_code == 0, materialized.output
    assert "Provider hierarchy samples: 1" in materialized.output

    benchmarked = runner.invoke(
        cli.app,
        [
            "benchmark-provider-hierarchy",
            str(corpus),
            "--output",
            str(tmp_path / "report.json"),
        ],
    )
    assert benchmarked.exit_code == 0, benchmarked.output
    assert "Provider hierarchy relation (precision/recall/F1): 1.0/1.0/1.0" in (
        benchmarked.output
    )


def _write_source_hierarchy_corpus(root: Path, *, sample_count: int = 1) -> Path:
    inputs = root / "inputs"
    labels = root / "labels"
    inputs.mkdir(parents=True, exist_ok=True)
    labels.mkdir(parents=True, exist_ok=True)
    samples = []
    for sample_index in range(sample_count):
        sample_id = f"sample-{sample_index}"
        elements = []
        for side, x0 in (("left", 10), ("right", 110)):
            for line_index in range(3):
                elements.append(
                    {
                        "id": f"{side}-{line_index}",
                        "box": [
                            x0,
                            10 + line_index * 20,
                            x0 + 70,
                            20 + line_index * 20,
                        ],
                        "role": "text",
                        "text": f"{side} line {line_index}",
                    }
                )
        input_payload = {
            "schema": HIERARCHY_INPUT_SCHEMA,
            "id": sample_id,
            "page_index": 0,
            "width": 200,
            "height": 100,
            "element_granularity": "fine",
            "region_granularity": "coarse",
            "elements": elements,
            "regions": [
                {"id": "oracle-left", "box": [5, 5, 90, 65], "role": "text"},
                {"id": "oracle-right", "box": [105, 5, 190, 65], "role": "text"},
            ],
        }
        input_path = inputs / f"{sample_id}.json"
        _write_json(input_path, input_payload)
        label_payload = {
            "schema": HIERARCHY_LABEL_SCHEMA,
            "id": sample_id,
            "memberships": [
                {"element_id": f"{side}-{index}", "region_id": f"oracle-{side}"}
                for side in ("left", "right")
                for index in range(3)
            ],
            "within_region_successor_edges": [
                {
                    "source": f"{side}-{index}",
                    "target": f"{side}-{index + 1}",
                    "region_id": f"oracle-{side}",
                }
                for side in ("left", "right")
                for index in range(2)
            ],
            "cross_region_transition_edges": [
                {
                    "source": "left-2",
                    "target": "right-0",
                    "source_region_id": "oracle-left",
                    "target_region_id": "oracle-right",
                }
            ],
        }
        label_path = labels / f"{sample_id}.json"
        _write_json(label_path, label_payload)
        samples.append(
            {
                "id": sample_id,
                "document_id": f"document-{sample_index}",
                "page_index": 0,
                "partition": "fit" if sample_index == 0 else "calibration",
                "layout_stratum": "multicolumn",
                "input": str(input_path.relative_to(root)),
                "input_sha256": _sha256(input_path),
                "labels": str(label_path.relative_to(root)),
                "labels_sha256": _sha256(label_path),
            }
        )
    _write_json(
        root / "hierarchical_order_corpus_manifest.json",
        {
            "schema": HIERARCHY_CORPUS_SCHEMA,
            "source_dataset": "fixture",
            "source_schema": "fixture-source/v1",
            "source_manifest_sha256": "a" * 64,
            "inference_inputs_are_answer_free": True,
            "sample_count": sample_count,
            "samples": samples,
        },
    )
    return root


def _write_provider_corpus(
    root: Path,
    *,
    sample_count: int = 1,
    overwrite: bool = False,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    sample_ids = [f"sample-{index}" for index in range(sample_count)]
    for sample_id in sample_ids:
        path = root / f"{sample_id}.structure.json"
        if path.exists() and not overwrite:
            continue
        _write_json(
            path,
            {
                "schema": "fixture-provider/v1",
                "source": "fixture-layout",
                "pages": [
                    {
                        "page_index": 0,
                        "elements": [
                            {
                                "id": "provider-left",
                                "block_label": "text",
                                "bbox": [5, 5, 90, 65],
                                "provider_order": 0,
                                "confidence": 0.98,
                            },
                            {
                                "id": "provider-right",
                                "block_label": "text",
                                "bbox": [105, 5, 190, 65],
                                "provider_order": 1,
                                "confidence": 0.97,
                            },
                        ],
                        "successor_edges": [
                            {
                                "source": "provider-left",
                                "target": "provider-right",
                            }
                        ],
                    }
                ],
            },
        )
    _write_json(
        root / "paddle_layout_corpus_run.json",
        {
            "schema": "scriptorium-paddle-layout-corpus-run/v1",
            "corpus_manifest_sha256": "a" * 64,
            "provider": "fixture-layout",
            "generated_sample_ids": sample_ids,
            "skipped_sample_ids": [],
        },
    )
    return root


def _without_source_hashes(payload: dict) -> dict:
    value = copy.deepcopy(payload)
    adapter = value["input_adapter"]
    adapter["source_provider_output_sha256"] = None
    return value


def _recursive_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for item in value.values() for key in _recursive_keys(item)
        }
    if isinstance(value, list):
        return {key for item in value for key in _recursive_keys(item)}
    return set()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
