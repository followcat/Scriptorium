from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen
from zipfile import ZipFile

import fitz


CompHrDocDownloader = Callable[[str], bytes]

COMPHRDOC_REPOSITORY = "https://github.com/microsoft/CompHRDoc"
COMPHRDOC_REVISION = "ca0fb394bdf01918f49ce2b61ef1564adb8e8b04"
COMPHRDOC_LICENSE = "MIT"
COMPHRDOC_ARCHIVE_SHA256 = "530f482b75523a80fe1b0a7480fd8273c44f9239e0189650a4841c0aae61d03d"
COMPHRDOC_ARCHIVE_URL = (
    "https://media.githubusercontent.com/media/microsoft/CompHRDoc/"
    f"{COMPHRDOC_REVISION}/CompHRDoc.zip"
)
COMPHRDOC_ANNOTATION_MEMBER = (
    "datasets/Comp-HRDoc/HRDH_MSRA_POD_TEST/unified_layout_analysis_test.json"
)
COMPHRDOC_FETCH_SCHEMA = "scriptorium-comphrdoc-benchmark/v1"
DEFAULT_COMPHRDOC_DOCUMENT_ID = "1401.3699"


@dataclass(frozen=True)
class CompHrDocBenchmarkSample:
    sample_id: str
    page_index: int
    image_path: Path
    structure_path: Path
    semantic_sidecar_path: Path


@dataclass(frozen=True)
class CompHrDocBenchmarkFetchResult:
    out_dir: Path
    manifest_path: Path
    source_pdf_path: Path
    samples: tuple[CompHrDocBenchmarkSample, ...]


@dataclass(frozen=True)
class CompHrDocRelationCorpusResult:
    out_dir: Path
    manifest_path: Path
    sample_count: int


@dataclass(frozen=True)
class CompHrDocRelationBenchmarkResult:
    report_path: Path
    report: dict[str, Any]


def fetch_comphrdoc_benchmark_samples(
    out_dir: str | Path,
    *,
    document_id: str = DEFAULT_COMPHRDOC_DOCUMENT_ID,
    max_pages: int = 5,
    refresh: bool = False,
    downloader: CompHrDocDownloader | None = None,
) -> CompHrDocBenchmarkFetchResult:
    """Fetch one fixed Comp-HRDoc test document with answer-separated pages."""

    if not document_id or any(character not in "0123456789." for character in document_id):
        raise ValueError("Comp-HRDoc document_id must be an arXiv-style numeric identifier")
    if max_pages < 1:
        raise ValueError("Comp-HRDoc max_pages must be at least 1")
    download = downloader or _download_bytes
    archive_bytes = download(COMPHRDOC_ARCHIVE_URL)
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    if archive_sha256 != COMPHRDOC_ARCHIVE_SHA256:
        raise ValueError("Comp-HRDoc annotation archive SHA-256 mismatch")
    annotations = _load_annotation_archive(archive_bytes)
    page_records = _document_page_records(annotations, document_id)
    if not page_records:
        raise ValueError(f"Comp-HRDoc test annotations do not contain document {document_id}")

    pdf_url = f"https://arxiv.org/pdf/{document_id}"
    pdf_bytes = download(pdf_url)
    target = Path(out_dir)
    images_dir = target / "images"
    structure_dir = target / "structure"
    images_dir.mkdir(parents=True, exist_ok=True)
    structure_dir.mkdir(parents=True, exist_ok=True)
    source_pdf_path = target / f"{document_id}.pdf"
    if refresh or not source_pdf_path.exists():
        source_pdf_path.write_bytes(pdf_bytes)

    samples: list[CompHrDocBenchmarkSample] = []
    manifest_samples: list[dict[str, Any]] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        selected_pages = min(max_pages, len(page_records), len(pdf))
        for page_index in range(selected_pages):
            image_record, page_annotations = page_records[page_index]
            sample_id = f"{document_id}_{page_index}"
            image_path = images_dir / f"{sample_id}.png"
            structure_path = structure_dir / f"{sample_id}.structure.json"
            semantic_path = image_path.with_suffix(".semantic-order.json")
            width = int(image_record["width"])
            height = int(image_record["height"])
            if refresh or not image_path.exists():
                _render_annotated_page(pdf[page_index], image_path, width=width, height=height)
            structure_payload, semantic_payload = _page_payloads(
                document_id,
                page_index,
                width,
                height,
                page_annotations,
            )
            if refresh or not structure_path.exists():
                structure_path.write_text(
                    json.dumps(structure_payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if refresh or not semantic_path.exists():
                semantic_path.write_text(
                    json.dumps(semantic_payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            samples.append(
                CompHrDocBenchmarkSample(
                    sample_id,
                    page_index,
                    image_path,
                    structure_path,
                    semantic_path,
                )
            )
            manifest_samples.append(
                {
                    "id": sample_id,
                    "page_index": page_index,
                    "image": str(image_path.relative_to(target)),
                    "structure": str(structure_path.relative_to(target)),
                    "semantic_sidecar": str(semantic_path.relative_to(target)),
                    "relation_count": len(semantic_payload["ro_linkings"]),
                }
            )

    manifest_path = target / "comphrdoc_benchmark_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": COMPHRDOC_FETCH_SCHEMA,
                "dataset": "Comp-HRDoc test",
                "repository": COMPHRDOC_REPOSITORY,
                "revision": COMPHRDOC_REVISION,
                "annotation_license": COMPHRDOC_LICENSE,
                "annotation_archive_sha256": archive_sha256,
                "document_id": document_id,
                "source_pdf_url": pdf_url,
                "source_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "selection": "fixed-document-page-prefix",
                "sample_count": len(samples),
                "evaluation_scope": "oracle-layout reading-order relations; source PDF rendered locally",
                "structure_input": {
                    "kind": "line-layout-anchor-only",
                    "relations_removed": True,
                },
                "samples": manifest_samples,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return CompHrDocBenchmarkFetchResult(target, manifest_path, source_pdf_path, tuple(samples))


def fetch_comphrdoc_relation_corpus(
    out_dir: str | Path,
    *,
    sample_count: int = 250,
    refresh: bool = False,
    downloader: CompHrDocDownloader | None = None,
) -> CompHrDocRelationCorpusResult:
    """Build a fixed cross-document floating relation corpus without PDF images."""

    if sample_count < 1:
        raise ValueError("Comp-HRDoc relation corpus sample_count must be at least 1")
    archive_bytes = (downloader or _download_bytes)(COMPHRDOC_ARCHIVE_URL)
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    if archive_sha256 != COMPHRDOC_ARCHIVE_SHA256:
        raise ValueError("Comp-HRDoc annotation archive SHA-256 mismatch")
    payload = _load_annotation_archive(archive_bytes)
    annotations_by_image: dict[int, list[dict[str, Any]]] = {}
    for annotation in payload["annotations"]:
        if isinstance(annotation, dict):
            annotations_by_image.setdefault(int(annotation.get("image_id", -1)), []).append(annotation)
    images = sorted(
        (image for image in payload["images"] if isinstance(image, dict)),
        key=lambda image: str(image.get("file_name") or ""),
    )
    selected = []
    for image in images:
        annotations = annotations_by_image.get(int(image.get("id", -1)), [])
        if not _has_graphical_floating_group(annotations):
            continue
        selected.append((image, annotations))
        if len(selected) >= sample_count:
            break

    target = Path(out_dir)
    structure_dir = target / "structure"
    semantic_dir = target / "semantic"
    structure_dir.mkdir(parents=True, exist_ok=True)
    semantic_dir.mkdir(parents=True, exist_ok=True)
    manifest_samples: list[dict[str, Any]] = []
    for image, annotations in selected:
        file_name = str(image["file_name"])
        document_id, raw_page_index = Path(file_name).stem.rsplit("_", 1)
        page_index = int(raw_page_index)
        ordered_annotations = sorted(
            annotations,
            key=lambda annotation: (
                int(annotation.get("reading_order_id", 0)),
                int(annotation.get("in_page_id", 0)),
            ),
        )
        structure, semantic = _page_payloads(
            document_id,
            page_index,
            int(image["width"]),
            int(image["height"]),
            ordered_annotations,
        )
        sample_id = f"{document_id}_{page_index}"
        structure_path = structure_dir / f"{sample_id}.structure.json"
        semantic_path = semantic_dir / f"{sample_id}.semantic-order.json"
        if refresh or not structure_path.exists():
            structure_path.write_text(json.dumps(structure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if refresh or not semantic_path.exists():
            semantic_path.write_text(json.dumps(semantic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        graphical_relation_count = _graphical_relation_count(semantic)
        manifest_samples.append(
            {
                "id": sample_id,
                "source_annotation_image": file_name,
                "structure": str(structure_path.relative_to(target)),
                "semantic_sidecar": str(semantic_path.relative_to(target)),
                "relation_count": len(semantic["ro_linkings"]),
                "graphical_relation_count": graphical_relation_count,
            }
        )

    manifest_path = target / "comphrdoc_relation_corpus_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "scriptorium-comphrdoc-relation-corpus/v1",
                "dataset": "Comp-HRDoc test",
                "repository": COMPHRDOC_REPOSITORY,
                "revision": COMPHRDOC_REVISION,
                "annotation_license": COMPHRDOC_LICENSE,
                "annotation_archive_sha256": archive_sha256,
                "selection": "published-image-name-order-floating-page-prefix",
                "selection_uses_labels": True,
                "inference_inputs_are_answer_free": True,
                "sample_count": len(manifest_samples),
                "requested_sample_count": sample_count,
                "source_images_redistributed": False,
                "samples": manifest_samples,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return CompHrDocRelationCorpusResult(target, manifest_path, len(manifest_samples))


def benchmark_comphrdoc_relation_corpus(
    corpus_dir: str | Path,
    model_path: str | Path,
    *,
    floating_model_path: str | Path | None = None,
    noise_profile: str = "clean",
    output: str | Path | None = None,
) -> CompHrDocRelationBenchmarkResult:
    """Score relation-role fusion on an answer-separated Comp-HRDoc corpus."""

    from . import relation_ranker
    from .provider_anchor_benchmark import _graphical_relation_audit
    from .relation_order import merge_relation_edge_path_cover
    from .relation_noise import perturb_relation_structure

    corpus = Path(corpus_dir)
    manifest_path = corpus / "comphrdoc_relation_corpus_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Comp-HRDoc relation corpus manifest is required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "scriptorium-comphrdoc-relation-corpus/v1":
        raise ValueError("unsupported Comp-HRDoc relation corpus schema")
    bundle, model_manifest = relation_ranker.load_relation_ranker(model_path)
    modes = {"native-ranker": False, "native-plus-structure-role": True}
    floating_bundle = None
    floating_manifest = None
    if floating_model_path is not None:
        from . import floating_ranker

        floating_bundle, floating_manifest = floating_ranker.load_floating_relation_ranker(
            floating_model_path
        )
        modes["native-plus-trained-floating"] = False
    totals = {name: _empty_relation_totals() for name in modes}
    noise_totals: Counter[str] = Counter()
    graphical_audit_totals: Counter[str] = Counter()
    page_results: list[dict[str, Any]] = []
    for sample in manifest.get("samples", []):
        structure_path = corpus / str(sample["structure"])
        semantic_path = corpus / str(sample["semantic_sidecar"])
        structure = json.loads(structure_path.read_text(encoding="utf-8"))
        structure, noise_diagnostics = perturb_relation_structure(
            structure,
            profile=noise_profile,
        )
        for key in (
            "source_element_count",
            "retained_element_count",
            "jittered_element",
            "fragmented_element",
            "type_dropout",
            "element_dropout",
            "prefix_corruption",
        ):
            noise_totals[key] += int(noise_diagnostics.get(key, 0))
        semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
        truth = {tuple(edge) for edge in semantic.get("ro_linkings", [])}
        retained_ids = {
            element.get("id")
            for element in structure.get("document", [])
            if isinstance(element, Mapping)
        }
        noise_totals["label_count"] += len(truth)
        noise_totals["resolvable_label_count"] += sum(
            int(source in retained_ids and target in retained_ids)
            for source, target in truth
        )
        nodes = {
            node.get("id"): node
            for node in semantic.get("document", [])
            if isinstance(node, Mapping)
        }
        graphical_truth = {
            edge
            for edge in truth
            if nodes.get(edge[0], {}).get("type") in {"figure", "table"}
            or nodes.get(edge[1], {}).get("type") in {"figure", "table"}
        }
        graphical_audit = _graphical_relation_audit(
            semantic,
            [node for node in semantic.get("document", []) if isinstance(node, Mapping)],
            truth,
            {},
        )
        conflict_graphical_ids = {
            str(conflict["graphical_id"])
            for conflict in graphical_audit["conflicts"]
        }
        for key in (
            "oracle_graphical_label_count",
            "geometry_proposal_count",
            "exact_agreement_count",
            "conflicting_label_count",
            "oracle_without_geometry_count",
            "geometry_without_oracle_count",
        ):
            graphical_audit_totals[key] += int(graphical_audit[key])
        graphical_audit_totals["cases_with_conflicts"] += int(
            bool(graphical_audit["conflicting_label_count"])
        )
        page_result: dict[str, Any] = {
            "id": sample["id"],
            "label_count": len(truth),
            "graphical_label_audit": {
                key: graphical_audit[key]
                for key in (
                    "reference_policy",
                    "oracle_graphical_label_count",
                    "geometry_proposal_count",
                    "exact_agreement_count",
                    "conflicting_label_count",
                    "oracle_without_geometry_count",
                    "geometry_without_oracle_count",
                    "conflicts",
                )
            },
        }
        prediction_cache: dict[bool, dict[str, Any]] = {}
        for mode, enabled in modes.items():
            if enabled not in prediction_cache:
                prediction_cache[enabled] = relation_ranker._predict_roor_page_relations(
                    structure,
                    bundle=bundle,
                    manifest=model_manifest,
                    structure_role_fusion=enabled,
                ).structure_payload
            prediction = prediction_cache[enabled]
            prediction_edges = list(prediction.get("successor_edges", []))
            if mode == "native-plus-trained-floating":
                assert floating_bundle is not None and floating_manifest is not None
                learned = floating_ranker._predict_floating_relations(
                    structure,
                    bundle=floating_bundle,
                    manifest=floating_manifest,
                )
                learned_sources = {edge["source"] for edge in learned.successor_edges}
                prediction_edges = [
                    edge for edge in prediction_edges if edge["source"] not in learned_sources
                ] + learned.successor_edges
                role_origin = "trained-floating-pair"
            else:
                role_origin = "structure-role-geometry"
            predicted = {(edge["source"], edge["target"]) for edge in prediction_edges}
            role_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("relation_origin") == role_origin
            }
            high_reliability_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("reliability_tier") == "high-precision-review"
            }
            high_reliability_in_envelope_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("reliability_tier") == "high-precision-review"
                and int(edge.get("feature_outlier_count", 0)) == 0
            }
            strict_gate_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("strict_gate_passed") is True
            }
            strict_gate_in_envelope_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("strict_gate_passed") is True
                and int(edge.get("feature_outlier_count", 0)) == 0
            }
            metrics = _relation_counts(predicted, truth)
            role_metrics = _relation_counts(role_predicted, graphical_truth)
            high_reliability_metrics = _relation_counts(
                high_reliability_predicted,
                graphical_truth,
            )
            high_reliability_in_envelope_metrics = _relation_counts(
                high_reliability_in_envelope_predicted,
                graphical_truth,
            )
            strict_gate_metrics = _relation_counts(
                strict_gate_predicted,
                graphical_truth,
            )
            strict_gate_in_envelope_metrics = _relation_counts(
                strict_gate_in_envelope_predicted,
                graphical_truth,
            )
            strict_conflict_predictions = _graphical_conflict_prediction_count(
                strict_gate_predicted,
                conflict_graphical_ids,
            )
            strict_conflict_incorrect = _graphical_conflict_prediction_count(
                strict_gate_predicted - graphical_truth,
                conflict_graphical_ids,
            )
            strict_in_envelope_conflict_predictions = (
                _graphical_conflict_prediction_count(
                    strict_gate_in_envelope_predicted,
                    conflict_graphical_ids,
                )
            )
            strict_in_envelope_conflict_incorrect = (
                _graphical_conflict_prediction_count(
                    strict_gate_in_envelope_predicted - graphical_truth,
                    conflict_graphical_ids,
                )
            )
            if mode == "native-plus-trained-floating":
                graphical_audit_totals["strict_gate_conflict_prediction_count"] += (
                    strict_conflict_predictions
                )
                graphical_audit_totals["strict_gate_conflict_incorrect_count"] += (
                    strict_conflict_incorrect
                )
                graphical_audit_totals[
                    "strict_gate_in_envelope_conflict_prediction_count"
                ] += strict_in_envelope_conflict_predictions
                graphical_audit_totals[
                    "strict_gate_in_envelope_conflict_incorrect_count"
                ] += strict_in_envelope_conflict_incorrect
            ordered_edges = sorted(
                prediction_edges,
                key=lambda edge: float(edge.get("confidence", 0.0)),
                reverse=True,
            )
            protected_path_edges = [
                (edge["source"], edge["target"])
                for edge in ordered_edges
                if edge.get("reliability_tier") == "high-precision-review"
                and int(edge.get("feature_outlier_count", 0)) == 0
            ]
            merged_path = merge_relation_edge_path_cover(
                ((edge["source"], edge["target"]) for edge in ordered_edges),
                protected_edges=protected_path_edges,
            )
            joint_path_metrics = _relation_counts(set(merged_path.selected_edges), truth)
            _accumulate_relation_totals(
                totals[mode],
                metrics,
                role_metrics,
                high_reliability_metrics,
                high_reliability_in_envelope_metrics,
                strict_gate_metrics,
                strict_gate_in_envelope_metrics,
                joint_path_metrics,
                merged_path,
            )
            page_result[mode] = {
                **metrics,
                "graphical": role_metrics,
                "high_reliability_graphical": high_reliability_metrics,
                "high_reliability_in_envelope_graphical": high_reliability_in_envelope_metrics,
                "strict_gate_graphical": strict_gate_metrics,
                "strict_gate_in_envelope_graphical": strict_gate_in_envelope_metrics,
                "strict_gate_conflict_prediction_count": strict_conflict_predictions,
                "strict_gate_conflict_incorrect_count": strict_conflict_incorrect,
                "strict_gate_in_envelope_conflict_prediction_count": (
                    strict_in_envelope_conflict_predictions
                ),
                "strict_gate_in_envelope_conflict_incorrect_count": (
                    strict_in_envelope_conflict_incorrect
                ),
                "joint_path_cover": {
                    **joint_path_metrics,
                    "protected_selected": len(merged_path.protected_selected_edges),
                    "rejected_outgoing_conflict": merged_path.rejected_outgoing_conflict_count,
                    "rejected_incoming_conflict": merged_path.rejected_incoming_conflict_count,
                    "rejected_cycle": merged_path.rejected_cycle_count,
                },
            }
        page_results.append(page_result)
    summarized = {name: _summarize_relation_totals(value) for name, value in totals.items()}
    baseline_f1 = summarized["native-ranker"]["f1"]
    fused_f1 = summarized["native-plus-structure-role"]["f1"]
    report = {
        "schema": "scriptorium-comphrdoc-relation-benchmark/v1",
        "corpus_manifest": str(manifest_path),
        "corpus_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "model_sha256": model_manifest.get("model_sha256"),
        "floating_model_sha256": (
            floating_manifest.get("model_sha256") if floating_manifest is not None else None
        ),
        "sample_count": len(page_results),
        "selection": manifest.get("selection"),
        "inference_inputs_are_answer_free": manifest.get("inference_inputs_are_answer_free"),
        "noise": {
            "profile": noise_profile,
            **dict(noise_totals),
            "element_retention_ratio": round(
                noise_totals["retained_element_count"]
                / noise_totals["source_element_count"],
                8,
            )
            if noise_totals["source_element_count"]
            else 0.0,
            "resolvable_label_ratio": round(
                noise_totals["resolvable_label_count"] / noise_totals["label_count"],
                8,
            )
            if noise_totals["label_count"]
            else 0.0,
        },
        "graphical_label_audit": {
            "reference_policy": "answer-free-local-geometry-diagnostic-not-ground-truth",
            **dict(graphical_audit_totals),
            "oracle_geometry_exact_agreement": round(
                graphical_audit_totals["exact_agreement_count"]
                / graphical_audit_totals["oracle_graphical_label_count"],
                8,
            )
            if graphical_audit_totals["oracle_graphical_label_count"]
            else 0.0,
            "oracle_geometry_conflict_rate": round(
                graphical_audit_totals["conflicting_label_count"]
                / graphical_audit_totals["oracle_graphical_label_count"],
                8,
            )
            if graphical_audit_totals["oracle_graphical_label_count"]
            else 0.0,
        },
        "summary": summarized,
        "f1_delta": round(fused_f1 - baseline_f1, 8),
        "pages": page_results,
    }
    if "native-plus-trained-floating" in summarized:
        report["trained_floating_f1_delta"] = round(
            summarized["native-plus-trained-floating"]["f1"] - baseline_f1,
            8,
        )
    report_path = Path(output) if output is not None else corpus / "relation_benchmark_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CompHrDocRelationBenchmarkResult(report_path, report)


def _empty_relation_totals() -> dict[str, int]:
    return {
        "correct": 0,
        "predicted": 0,
        "labels": 0,
        "graphical_correct": 0,
        "graphical_predicted": 0,
        "graphical_labels": 0,
        "high_reliability_correct": 0,
        "high_reliability_predicted": 0,
        "high_reliability_labels": 0,
        "high_reliability_in_envelope_correct": 0,
        "high_reliability_in_envelope_predicted": 0,
        "high_reliability_in_envelope_labels": 0,
        "strict_gate_correct": 0,
        "strict_gate_predicted": 0,
        "strict_gate_labels": 0,
        "strict_gate_in_envelope_correct": 0,
        "strict_gate_in_envelope_predicted": 0,
        "strict_gate_in_envelope_labels": 0,
        "joint_path_correct": 0,
        "joint_path_predicted": 0,
        "joint_path_labels": 0,
        "joint_path_protected_selected": 0,
        "joint_path_rejected_outgoing_conflict": 0,
        "joint_path_rejected_incoming_conflict": 0,
        "joint_path_rejected_cycle": 0,
    }


def _relation_counts(predicted: set[tuple[Any, Any]], truth: set[tuple[Any, Any]]) -> dict[str, int]:
    return {"correct": len(predicted & truth), "predicted": len(predicted), "labels": len(truth)}


def _graphical_conflict_prediction_count(
    predicted: set[tuple[Any, Any]],
    conflict_graphical_ids: set[str],
) -> int:
    return sum(
        int(str(source) in conflict_graphical_ids or str(target) in conflict_graphical_ids)
        for source, target in predicted
    )


def _accumulate_relation_totals(
    totals: dict[str, int],
    metrics: Mapping[str, int],
    graphical: Mapping[str, int],
    high_reliability: Mapping[str, int],
    high_reliability_in_envelope: Mapping[str, int],
    strict_gate: Mapping[str, int],
    strict_gate_in_envelope: Mapping[str, int],
    joint_path: Mapping[str, int],
    merged_path: Any,
) -> None:
    for key in ("correct", "predicted", "labels"):
        totals[key] += int(metrics[key])
        totals[f"graphical_{key}"] += int(graphical[key])
        totals[f"high_reliability_{key}"] += int(high_reliability[key])
        totals[f"high_reliability_in_envelope_{key}"] += int(
            high_reliability_in_envelope[key]
        )
        totals[f"strict_gate_{key}"] += int(strict_gate[key])
        totals[f"strict_gate_in_envelope_{key}"] += int(
            strict_gate_in_envelope[key]
        )
        totals[f"joint_path_{key}"] += int(joint_path[key])
    totals["joint_path_protected_selected"] += len(merged_path.protected_selected_edges)
    totals["joint_path_rejected_outgoing_conflict"] += merged_path.rejected_outgoing_conflict_count
    totals["joint_path_rejected_incoming_conflict"] += merged_path.rejected_incoming_conflict_count
    totals["joint_path_rejected_cycle"] += merged_path.rejected_cycle_count


def _summarize_relation_totals(totals: Mapping[str, int]) -> dict[str, Any]:
    result: dict[str, Any] = dict(totals)
    result.update(_precision_recall_f1(totals["correct"], totals["predicted"], totals["labels"]))
    result["graphical"] = {
        "correct": totals["graphical_correct"],
        "predicted": totals["graphical_predicted"],
        "labels": totals["graphical_labels"],
        **_precision_recall_f1(
            totals["graphical_correct"],
            totals["graphical_predicted"],
            totals["graphical_labels"],
        ),
    }
    result["high_reliability_graphical"] = {
        "correct": totals["high_reliability_correct"],
        "predicted": totals["high_reliability_predicted"],
        "labels": totals["high_reliability_labels"],
        **_precision_recall_f1(
            totals["high_reliability_correct"],
            totals["high_reliability_predicted"],
            totals["high_reliability_labels"],
        ),
    }
    result["high_reliability_in_envelope_graphical"] = {
        "correct": totals["high_reliability_in_envelope_correct"],
        "predicted": totals["high_reliability_in_envelope_predicted"],
        "labels": totals["high_reliability_in_envelope_labels"],
        **_precision_recall_f1(
            totals["high_reliability_in_envelope_correct"],
            totals["high_reliability_in_envelope_predicted"],
            totals["high_reliability_in_envelope_labels"],
        ),
    }
    result["strict_gate_graphical"] = {
        "correct": totals["strict_gate_correct"],
        "predicted": totals["strict_gate_predicted"],
        "labels": totals["strict_gate_labels"],
        **_precision_recall_f1(
            totals["strict_gate_correct"],
            totals["strict_gate_predicted"],
            totals["strict_gate_labels"],
        ),
    }
    result["strict_gate_in_envelope_graphical"] = {
        "correct": totals["strict_gate_in_envelope_correct"],
        "predicted": totals["strict_gate_in_envelope_predicted"],
        "labels": totals["strict_gate_in_envelope_labels"],
        **_precision_recall_f1(
            totals["strict_gate_in_envelope_correct"],
            totals["strict_gate_in_envelope_predicted"],
            totals["strict_gate_in_envelope_labels"],
        ),
    }
    result["joint_path_cover"] = {
        "correct": totals["joint_path_correct"],
        "predicted": totals["joint_path_predicted"],
        "labels": totals["joint_path_labels"],
        **_precision_recall_f1(
            totals["joint_path_correct"],
            totals["joint_path_predicted"],
            totals["joint_path_labels"],
        ),
        "protected_selected": totals["joint_path_protected_selected"],
        "rejected_outgoing_conflict": totals["joint_path_rejected_outgoing_conflict"],
        "rejected_incoming_conflict": totals["joint_path_rejected_incoming_conflict"],
        "rejected_cycle": totals["joint_path_rejected_cycle"],
    }
    for key in ("graphical_correct", "graphical_predicted", "graphical_labels"):
        result.pop(key)
    for key in (
        "high_reliability_correct",
        "high_reliability_predicted",
        "high_reliability_labels",
    ):
        result.pop(key)
    for key in (
        "joint_path_correct",
        "joint_path_predicted",
        "joint_path_labels",
        "joint_path_protected_selected",
        "joint_path_rejected_outgoing_conflict",
        "joint_path_rejected_incoming_conflict",
        "joint_path_rejected_cycle",
    ):
        result.pop(key)
    for key in (
        "high_reliability_in_envelope_correct",
        "high_reliability_in_envelope_predicted",
        "high_reliability_in_envelope_labels",
    ):
        result.pop(key)
    for key in (
        "strict_gate_correct",
        "strict_gate_predicted",
        "strict_gate_labels",
        "strict_gate_in_envelope_correct",
        "strict_gate_in_envelope_predicted",
        "strict_gate_in_envelope_labels",
    ):
        result.pop(key)
    return result


def _precision_recall_f1(correct: int, predicted: int, labels: int) -> dict[str, float]:
    precision = correct / predicted if predicted else 0.0
    recall = correct / labels if labels else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 8), "recall": round(recall, 8), "f1": round(f1, 8)}


def _has_graphical_floating_group(annotations: list[dict[str, Any]]) -> bool:
    groups: dict[int, list[dict[str, Any]]] = {}
    for annotation in annotations:
        groups.setdefault(int(annotation.get("reading_order_id", -1)), []).append(annotation)
    return any(
        any(int(item.get("reading_order_label", 0)) == 2 for item in group)
        and any(_graphical_kind(item) is not None for item in group)
        for group in groups.values()
    )


def _graphical_relation_count(semantic: Mapping[str, Any]) -> int:
    nodes = {
        node.get("id"): node
        for node in semantic.get("document", [])
        if isinstance(node, Mapping)
    }
    return sum(
        1
        for source, target in semantic.get("ro_linkings", [])
        if nodes.get(source, {}).get("type") in {"figure", "table"}
        or nodes.get(target, {}).get("type") in {"figure", "table"}
    )


def _download_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Scriptorium/0.1"})
    with urlopen(request, timeout=180) as response:
        return response.read()


def _load_annotation_archive(payload: bytes) -> dict[str, Any]:
    try:
        with ZipFile(BytesIO(payload)) as archive:
            raw = json.loads(archive.read(COMPHRDOC_ANNOTATION_MEMBER))
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Comp-HRDoc annotation archive") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("images"), list) or not isinstance(raw.get("annotations"), list):
        raise ValueError("invalid Comp-HRDoc unified layout annotation")
    return raw


def _document_page_records(
    payload: dict[str, Any],
    document_id: str,
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    images = [
        image
        for image in payload["images"]
        if isinstance(image, dict) and str(image.get("file_name") or "").startswith(f"{document_id}_")
    ]
    images.sort(key=lambda image: _page_number_from_name(str(image["file_name"]), document_id))
    annotations_by_image: dict[int, list[dict[str, Any]]] = {}
    for annotation in payload["annotations"]:
        if isinstance(annotation, dict):
            annotations_by_image.setdefault(int(annotation.get("image_id", -1)), []).append(annotation)
    return [
        (
            image,
            sorted(
                annotations_by_image.get(int(image["id"]), []),
                key=lambda annotation: (
                    int(annotation.get("reading_order_id", 0)),
                    int(annotation.get("in_page_id", 0)),
                ),
            ),
        )
        for image in images
    ]


def _page_number_from_name(file_name: str, document_id: str) -> int:
    stem = Path(file_name).stem
    try:
        return int(stem.removeprefix(f"{document_id}_"))
    except ValueError as exc:
        raise ValueError(f"invalid Comp-HRDoc page name: {file_name}") from exc


def _render_annotated_page(page: fitz.Page, path: Path, *, width: int, height: int) -> None:
    matrix = fitz.Matrix(width / page.rect.width, height / page.rect.height)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    if pixmap.width != width or pixmap.height != height:
        raise ValueError(
            f"rendered page dimensions {pixmap.width}x{pixmap.height} do not match annotations {width}x{height}"
        )
    pixmap.save(path)


def _page_payloads(
    document_id: str,
    page_index: int,
    width: int,
    height: int,
    annotations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    annotation_node_ids: list[list[str]] = []
    edges: list[list[str]] = []
    for annotation in annotations:
        contents = annotation.get("textline_contents")
        polygons = annotation.get("textline_polys")
        block_node_ids: list[str] = []
        block_id = f"comphrdoc-p{page_index + 1:04d}-b{int(annotation.get('in_page_id', 0)) + 1:04d}"
        if isinstance(contents, list) and isinstance(polygons, list):
            for content, polygon in zip(contents, polygons, strict=False):
                bbox = _polygon_bbox(polygon)
                text = str(content or "").strip()
                if bbox is None or not text:
                    continue
                node_id = f"comphrdoc-p{page_index + 1:04d}-l{len(nodes) + 1:04d}"
                nodes.append(
                    {
                        "id": node_id,
                        "box": bbox,
                        "text": text,
                        "words": [],
                        "type": "text",
                        "block_id": block_id,
                    }
                )
                block_node_ids.append(node_id)
        graphical_kind = _graphical_kind(annotation)
        if not block_node_ids and graphical_kind is not None:
            bbox = _annotation_bbox(annotation.get("bbox"))
            if bbox is not None:
                node_id = f"comphrdoc-p{page_index + 1:04d}-l{len(nodes) + 1:04d}"
                text = f"[{graphical_kind} p{page_index + 1:04d} g{int(annotation.get('in_page_id', 0)) + 1:04d}]"
                nodes.append(
                    {
                        "id": node_id,
                        "box": bbox,
                        "text": text,
                        "words": [],
                        "type": graphical_kind,
                        "block_id": block_id,
                    }
                )
                block_node_ids.append(node_id)
        for source, target in zip(block_node_ids, block_node_ids[1:], strict=False):
            edges.append([source, target])
        annotation_node_ids.append(block_node_ids)
    floating_order_ids = {
        int(annotation.get("reading_order_id", -1))
        for annotation in annotations
        if int(annotation.get("reading_order_label", 0)) == 2
    }
    body_indices = [
        index
        for index, annotation in enumerate(annotations)
        if int(annotation.get("reading_order_id", -1)) not in floating_order_ids
    ]
    for body_position, index in enumerate(body_indices[:-1]):
        annotation = annotations[index]
        if int(annotation.get("reading_order_label", 0)) != 1:
            continue
        following_index = body_indices[body_position + 1]
        current = annotation_node_ids[index] if index < len(annotation_node_ids) else []
        following = annotation_node_ids[following_index]
        if current and following:
            edges.append([current[-1], following[0]])
    floating_groups: dict[int, list[int]] = {}
    for index, annotation in enumerate(annotations):
        floating_groups.setdefault(int(annotation.get("reading_order_id", -1)), []).append(index)
    for group_indices in floating_groups.values():
        if not any(int(annotations[index].get("reading_order_label", 0)) == 2 for index in group_indices):
            continue
        graphical_indices = [index for index in group_indices if _graphical_kind(annotations[index]) is not None]
        text_indices = [index for index in group_indices if _graphical_kind(annotations[index]) is None]
        for graphical_index in graphical_indices:
            graphical_kind = _graphical_kind(annotations[graphical_index])
            graphical = annotation_node_ids[graphical_index]
            caption_nodes = [
                node_id
                for text_index in text_indices
                for node_id in annotation_node_ids[text_index]
            ]
            if not graphical or not caption_nodes:
                continue
            if graphical_kind == "figure":
                edges.append([graphical[-1], caption_nodes[0]])
            else:
                edges.append([caption_nodes[-1], graphical[0]])
    base = {
        "uid": f"{document_id}_{page_index}",
        "img": {
            "fname": f"images/{document_id}_{page_index}.png",
            "width": width,
            "height": height,
        },
        "document": nodes,
    }
    return (
        {
            "schema": "scriptorium-comphrdoc-layout-anchor-only/v1",
            **base,
            "relations_removed": True,
        },
        {
            "schema": "scriptorium-comphrdoc-reading-order/v1",
            **base,
            "ro_linkings": edges,
            "match_mode": "ordered-subsequence",
        },
    )


def _polygon_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 8 or len(value) % 2:
        return None
    try:
        xs = [float(value[index]) for index in range(0, len(value), 2)]
        ys = [float(value[index]) for index in range(1, len(value), 2)]
    except (TypeError, ValueError, OverflowError):
        return None
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    return bbox if bbox[2] > bbox[0] and bbox[3] > bbox[1] else None


def _graphical_kind(annotation: Mapping[str, Any]) -> str | None:
    try:
        category_id = int(annotation.get("category_id"))
    except (TypeError, ValueError):
        return None
    return {1: "figure", 2: "table"}.get(category_id)


def _annotation_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 4:
        return None
    try:
        x, y, width, height = (float(item) for item in value[:4])
    except (TypeError, ValueError, OverflowError):
        return None
    if width <= 0 or height <= 0:
        return None
    return [x, y, x + width, y + height]
