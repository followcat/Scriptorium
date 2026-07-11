from __future__ import annotations

import hashlib
import json
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
    output: str | Path | None = None,
) -> CompHrDocRelationBenchmarkResult:
    """Score relation-role fusion on an answer-separated Comp-HRDoc corpus."""

    from . import relation_ranker

    corpus = Path(corpus_dir)
    manifest_path = corpus / "comphrdoc_relation_corpus_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Comp-HRDoc relation corpus manifest is required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "scriptorium-comphrdoc-relation-corpus/v1":
        raise ValueError("unsupported Comp-HRDoc relation corpus schema")
    bundle, model_manifest = relation_ranker.load_relation_ranker(model_path)
    modes = {"native-ranker": False, "native-plus-structure-role": True}
    totals = {name: _empty_relation_totals() for name in modes}
    page_results: list[dict[str, Any]] = []
    for sample in manifest.get("samples", []):
        structure_path = corpus / str(sample["structure"])
        semantic_path = corpus / str(sample["semantic_sidecar"])
        structure = json.loads(structure_path.read_text(encoding="utf-8"))
        semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
        truth = {tuple(edge) for edge in semantic.get("ro_linkings", [])}
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
        page_result: dict[str, Any] = {"id": sample["id"], "label_count": len(truth)}
        for mode, enabled in modes.items():
            prediction = relation_ranker._predict_roor_page_relations(
                structure,
                bundle=bundle,
                manifest=model_manifest,
                structure_role_fusion=enabled,
            ).structure_payload
            predicted = {
                (edge["source"], edge["target"])
                for edge in prediction.get("successor_edges", [])
            }
            role_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction.get("successor_edges", [])
                if edge.get("relation_origin") == "structure-role-geometry"
            }
            metrics = _relation_counts(predicted, truth)
            role_metrics = _relation_counts(role_predicted, graphical_truth)
            _accumulate_relation_totals(totals[mode], metrics, role_metrics)
            page_result[mode] = {**metrics, "graphical": role_metrics}
        page_results.append(page_result)
    summarized = {name: _summarize_relation_totals(value) for name, value in totals.items()}
    baseline_f1 = summarized["native-ranker"]["f1"]
    fused_f1 = summarized["native-plus-structure-role"]["f1"]
    report = {
        "schema": "scriptorium-comphrdoc-relation-benchmark/v1",
        "corpus_manifest": str(manifest_path),
        "corpus_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "model_sha256": model_manifest.get("model_sha256"),
        "sample_count": len(page_results),
        "selection": manifest.get("selection"),
        "inference_inputs_are_answer_free": manifest.get("inference_inputs_are_answer_free"),
        "summary": summarized,
        "f1_delta": round(fused_f1 - baseline_f1, 8),
        "pages": page_results,
    }
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
    }


def _relation_counts(predicted: set[tuple[Any, Any]], truth: set[tuple[Any, Any]]) -> dict[str, int]:
    return {"correct": len(predicted & truth), "predicted": len(predicted), "labels": len(truth)}


def _accumulate_relation_totals(
    totals: dict[str, int],
    metrics: Mapping[str, int],
    graphical: Mapping[str, int],
) -> None:
    for key in ("correct", "predicted", "labels"):
        totals[key] += int(metrics[key])
        totals[f"graphical_{key}"] += int(graphical[key])


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
    for key in ("graphical_correct", "graphical_predicted", "graphical_labels"):
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
