from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import relation_ranker
from .relation_order import merge_relation_edge_path_cover


RELATION_RANKER_ROOR_AB_SCHEMA = "scriptorium-relation-ranker-roor-ab/v1"


@dataclass(frozen=True)
class RelationRankerRoorBenchmarkResult:
    report_path: Path
    report: dict[str, Any]


def benchmark_relation_rankers_roor(
    corpus_dir: str | Path,
    control_model_path: str | Path,
    candidate_model_path: str | Path,
    *,
    candidate_semantic_scorer: Any | None = None,
    output: str | Path | None = None,
) -> RelationRankerRoorBenchmarkResult:
    """A/B two local rankers without opening ROOR labels during prediction."""

    corpus = Path(corpus_dir).resolve()
    manifest_path = corpus / "roor_benchmark_manifest.json"
    manifest = _json_object(manifest_path, label="ROOR benchmark manifest")
    if manifest.get("schema") != "scriptorium-roor-benchmark/v1":
        raise ValueError("unsupported ROOR benchmark corpus schema")
    if manifest.get("structure_input", {}).get("relations_removed") is not True:
        raise ValueError("ROOR benchmark structures must declare removed relations")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("ROOR benchmark manifest must contain samples")
    control_bundle, control_manifest = relation_ranker.load_relation_ranker(
        control_model_path
    )
    candidate_bundle, candidate_manifest = relation_ranker.load_relation_ranker(
        candidate_model_path
    )

    pending: list[
        tuple[Mapping[str, Any], dict[str, Any], dict[str, Any]]
    ] = []
    seen_ids: set[str] = set()
    # Phase one predicts every sample before resolving any semantic sidecar path.
    for raw_sample in samples:
        if not isinstance(raw_sample, Mapping):
            raise ValueError("ROOR benchmark samples must be objects")
        sample_id = str(raw_sample.get("id") or "").strip()
        if not sample_id or sample_id in seen_ids:
            raise ValueError("ROOR benchmark sample ids must be non-empty and unique")
        seen_ids.add(sample_id)
        structure_path = _confined_path(
            corpus,
            raw_sample.get("structure"),
            label=f"sample {sample_id} structure",
        )
        structure = _json_object(structure_path, label=f"sample {sample_id} structure")
        if relation_ranker._payload_contains_answer_relations(structure):
            raise ValueError(f"sample {sample_id} structure contains answer relations")
        control = relation_ranker._predict_roor_page_relations(
            structure,
            bundle=control_bundle,
            manifest=control_manifest,
            structure_role_fusion=False,
        ).structure_payload
        candidate = relation_ranker._predict_roor_page_relations(
            structure,
            bundle=candidate_bundle,
            manifest=candidate_manifest,
            structure_role_fusion=False,
            semantic_scorer=candidate_semantic_scorer,
        ).structure_payload
        pending.append((raw_sample, control, candidate))

    totals = {"control": _empty_metrics(), "candidate": _empty_metrics()}
    page_results: list[dict[str, Any]] = []
    # Phase two opens immutable labels only after every prediction is complete.
    for raw_sample, control, candidate in pending:
        sample_id = str(raw_sample["id"])
        semantic_path = _confined_path(
            corpus,
            raw_sample.get("semantic_sidecar"),
            label=f"sample {sample_id} semantic sidecar",
        )
        semantic = _json_object(
            semantic_path,
            label=f"sample {sample_id} semantic sidecar",
        )
        raw_truth = semantic.get("ro_linkings")
        if not isinstance(raw_truth, list):
            raise ValueError(f"sample {sample_id} semantic sidecar lacks ro_linkings")
        truth = {tuple(edge) for edge in raw_truth}
        page_result: dict[str, Any] = {"id": sample_id, "label_count": len(truth)}
        for mode, prediction in (("control", control), ("candidate", candidate)):
            metrics = _prediction_metrics(prediction, truth)
            _accumulate_metrics(totals[mode], metrics)
            page_result[mode] = metrics
        page_results.append(page_result)

    summary = {mode: _summarize_metrics(values) for mode, values in totals.items()}
    report = {
        "schema": RELATION_RANKER_ROOR_AB_SCHEMA,
        "corpus_manifest": str(manifest_path),
        "corpus_manifest_sha256": _file_sha256(manifest_path),
        "dataset": manifest.get("dataset"),
        "split": manifest.get("split"),
        "selection": manifest.get("selection"),
        "sample_count": len(page_results),
        "inference_inputs_are_answer_free": True,
        "labels_opened_after_all_predictions": True,
        "structure_role_fusion": False,
        "control_model": _model_metadata(control_manifest),
        "candidate_model": _model_metadata(candidate_manifest),
        "summary": summary,
        "f1_delta": {
            metric: round(
                summary["candidate"][metric]["f1"]
                - summary["control"][metric]["f1"],
                8,
            )
            for metric in ("top", "branch", "path_cover")
        },
        "pages": page_results,
    }
    report_path = (
        Path(output)
        if output is not None
        else corpus / "relation_ranker_roor_ab_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return RelationRankerRoorBenchmarkResult(report_path, report)


def _prediction_metrics(
    prediction: Mapping[str, Any],
    truth: set[tuple[Any, Any]],
) -> dict[str, dict[str, int]]:
    raw_edges = prediction.get("successor_edges")
    if not isinstance(raw_edges, list):
        raise ValueError("relation ranker prediction lacks successor_edges")
    edges = [edge for edge in raw_edges if isinstance(edge, Mapping)]
    top = {
        (edge.get("source"), edge.get("target"))
        for edge in edges
        if int(edge.get("rank") or 1) == 1
    }
    branch = {(edge.get("source"), edge.get("target")) for edge in edges}
    ordered = sorted(
        edges,
        key=lambda edge: float(edge.get("confidence") or 0.0),
        reverse=True,
    )
    path_cover = set(
        merge_relation_edge_path_cover(
            ((edge.get("source"), edge.get("target")) for edge in ordered)
        ).selected_edges
    )
    return {
        name: {
            "correct": len(predicted & truth),
            "predicted": len(predicted),
            "labels": len(truth),
        }
        for name, predicted in (
            ("top", top),
            ("branch", branch),
            ("path_cover", path_cover),
        )
    }


def _empty_metrics() -> dict[str, dict[str, int]]:
    return {
        name: {"correct": 0, "predicted": 0, "labels": 0}
        for name in ("top", "branch", "path_cover")
    }


def _accumulate_metrics(
    totals: dict[str, dict[str, int]],
    metrics: Mapping[str, Mapping[str, int]],
) -> None:
    for name in totals:
        for key in totals[name]:
            totals[name][key] += int(metrics[name][key])


def _summarize_metrics(
    totals: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for name, values in totals.items():
        correct = int(values["correct"])
        predicted = int(values["predicted"])
        labels = int(values["labels"])
        precision = correct / predicted if predicted else 0.0
        recall = correct / labels if labels else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        result[name] = {
            "correct": correct,
            "predicted": predicted,
            "labels": labels,
            "precision": round(precision, 8),
            "recall": round(recall, 8),
            "f1": round(f1, 8),
        }
    return result


def _model_metadata(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model_sha256": manifest.get("model_sha256"),
        "feature_version": manifest.get("feature_version"),
        "semantic_scorer": manifest.get("semantic_scorer"),
        "semantic_fusion": manifest.get("semantic_fusion"),
        "semantic_top_k": manifest.get("semantic_top_k"),
    }


def _confined_path(root: Path, value: Any, *, label: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} path is required")
    candidate = (root / raw).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"{label} must stay inside the corpus directory")
    if not candidate.is_file():
        raise ValueError(f"{label} is missing")
    return candidate


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
