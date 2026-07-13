from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http.client import IncompleteRead
from pathlib import Path
from time import sleep
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .geometry import reading_order_key
from .models import BBox
from .reading_order import (
    infer_box_flow_order,
    infer_recursive_xy_cut_edges,
    infer_recursive_xy_cut_order,
    infer_relation_graph_order,
    infer_relation_graph_selected_edges,
    infer_semantic_reading_order,
)


ChunkrDownloader = Callable[[str], bytes]

CHUNKR_DATASET_REPOSITORY = (
    "https://huggingface.co/datasets/ChunkrAI/chunkr-reading-order-bench-oss"
)
CHUNKR_DATA_REVISION = "d6b5ddf06a6479a42bb0b33c243801171e042fc7"
CHUNKR_DATASET_LICENSE = "MIT"
CHUNKR_ANNOTATIONS_SHA256 = (
    "93974a16cb43a44656f293b933abd1a713d2bff2bfa71cd7b74987edb26bdbfa"
)
CHUNKR_ANNOTATIONS_URL = (
    f"{CHUNKR_DATASET_REPOSITORY}/resolve/{CHUNKR_DATA_REVISION}/"
    "_annotations.coco.json"
)
CHUNKR_FETCH_SCHEMA = "scriptorium-chunkr-reading-order-corpus/v1"
CHUNKR_REPORT_SCHEMA = "scriptorium-chunkr-reading-order-benchmark/v1"
CHUNKR_DOWNLOAD_ATTEMPTS = 5
CHUNKR_DOWNLOAD_RETRY_DELAY_SECONDS = 1.0

ORDER_CANDIDATES = (
    "selected-auto",
    "visual-yx",
    "box-flow",
    "recursive-xy-cut",
    "relation-graph",
)
EDGE_CHANNELS = (
    "visual-yx",
    "box-flow",
    "recursive-xy-cut",
    "relation-graph",
)
STABLE_SUPPORT_CHANNELS = (
    "visual-yx",
    "box-flow",
    "relation-graph",
)


@dataclass(frozen=True)
class ChunkrReadingOrderFetchResult:
    out_dir: Path
    manifest_path: Path
    annotations_path: Path


@dataclass(frozen=True)
class ChunkrReadingOrderBenchmarkResult:
    report_path: Path
    report: dict[str, Any]


def fetch_chunkr_reading_order_annotations(
    out_dir: str | Path,
    *,
    annotation_file: str | Path | None = None,
    refresh: bool = False,
    downloader: ChunkrDownloader | None = None,
) -> ChunkrReadingOrderFetchResult:
    """Fetch the pinned COCO labels without downloading the 733 source images."""

    if annotation_file is not None:
        source_path = Path(annotation_file)
        source_bytes = source_path.read_bytes()
        source = str(source_path)
    else:
        source_bytes = _download_with_retry(
            downloader or _download_bytes,
            CHUNKR_ANNOTATIONS_URL,
        )
        source = CHUNKR_ANNOTATIONS_URL
    annotation_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if annotation_sha256 != CHUNKR_ANNOTATIONS_SHA256:
        raise ValueError("Chunkr annotation SHA-256 mismatch")
    payload = _load_chunkr_coco(source_bytes)
    summary = _chunkr_corpus_summary(payload)

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    annotations_path = target / "_annotations.coco.json"
    if annotations_path.exists() and not refresh:
        existing_sha256 = hashlib.sha256(annotations_path.read_bytes()).hexdigest()
        if existing_sha256 != CHUNKR_ANNOTATIONS_SHA256:
            raise ValueError(
                "existing Chunkr annotations do not match the pinned SHA-256"
            )
    else:
        annotations_path.write_bytes(source_bytes)

    manifest_path = target / "chunkr_reading_order_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": CHUNKR_FETCH_SCHEMA,
                "dataset": "Chunkr Reading Order Bench OSS",
                "repository": CHUNKR_DATASET_REPOSITORY,
                "revision": CHUNKR_DATA_REVISION,
                "license": CHUNKR_DATASET_LICENSE,
                "annotation_source": source,
                "annotation_sha256": annotation_sha256,
                "selection": "complete-published-coco-annotation-set",
                "selection_uses_reading_order_labels": False,
                "development_only": True,
                "runtime_reorder": False,
                "images_downloaded": False,
                "answer_boundary": {
                    "ground_truth": (
                        "ascending contiguous annotation id within each image"
                    ),
                    "candidate_input_order": (
                        "sha256-category-and-bbox-fingerprint; annotation ids excluded"
                    ),
                },
                **summary,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return ChunkrReadingOrderFetchResult(
        target,
        manifest_path,
        annotations_path,
    )


def benchmark_chunkr_reading_order(
    annotations_path: str | Path,
    *,
    output: str | Path | None = None,
) -> ChunkrReadingOrderBenchmarkResult:
    """Score answer-free Scriptorium geometry candidates on Chunkr labels."""

    source_path = Path(annotations_path)
    source_bytes = source_path.read_bytes()
    payload = _load_chunkr_coco(source_bytes)
    images = {int(image["id"]): image for image in payload["images"]}
    annotations_by_image = _annotations_by_image(payload)

    cases: list[dict[str, Any]] = []
    order_pages: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ORDER_CANDIDATES
    }
    edge_pages: dict[str, list[dict[str, Any]]] = {
        name: [] for name in EDGE_CHANNELS
    }
    support_pages: dict[str, dict[int, list[dict[str, Any]]]] = {
        "stable": {
            threshold: []
            for threshold in range(1, len(STABLE_SUPPORT_CHANNELS) + 1)
        },
        "all": {
            threshold: []
            for threshold in range(1, len(EDGE_CHANNELS) + 1)
        },
    }
    selected_strategy_counts: Counter[str] = Counter()

    for image_id in sorted(images):
        image = images[image_id]
        truth_annotations = annotations_by_image[image_id]
        input_annotations = sorted(
            truth_annotations,
            key=_answer_free_anchor_fingerprint,
        )
        truth_rank_by_id = {
            int(annotation["id"]): rank
            for rank, annotation in enumerate(truth_annotations)
        }
        truth_rank_by_input = [
            truth_rank_by_id[int(annotation["id"])]
            for annotation in input_annotations
        ]
        bboxes = [_annotation_bbox(annotation) for annotation in input_annotations]
        width = float(image["width"])
        height = float(image["height"])

        visual_order = sorted(
            range(len(bboxes)),
            key=lambda index: reading_order_key(bboxes[index]),
        )
        box_flow_order = infer_box_flow_order(bboxes, width, height)
        xy_cut_order = infer_recursive_xy_cut_order(bboxes, width, height)
        relation_graph_order = infer_relation_graph_order(bboxes, width, height)
        selected_assignments = infer_semantic_reading_order(
            bboxes,
            width,
            height,
            texts=[""] * len(bboxes),
        )
        selected_order = [
            assignment.item_index
            for assignment in sorted(
                selected_assignments,
                key=lambda assignment: assignment.semantic_order,
            )
        ]
        selected_strategy_counts.update(
            assignment.strategy for assignment in selected_assignments
        )
        candidate_orders = {
            "selected-auto": selected_order,
            "visual-yx": visual_order,
            "box-flow": box_flow_order,
            "recursive-xy-cut": xy_cut_order,
            "relation-graph": relation_graph_order,
        }
        candidate_metrics: dict[str, dict[str, Any]] = {}
        for name, order in candidate_orders.items():
            truth_ranks = _truth_rank_order(order, truth_rank_by_input)
            metrics = _order_page_metrics(truth_ranks)
            order_pages[name].append(metrics)
            candidate_metrics[name] = _public_order_page_metrics(metrics)

        edge_sets = {
            "visual-yx": _adjacent_edges(visual_order),
            "box-flow": _adjacent_edges(box_flow_order),
            "recursive-xy-cut": infer_recursive_xy_cut_edges(
                bboxes,
                width,
                height,
            ),
            "relation-graph": set(
                infer_relation_graph_selected_edges(
                    bboxes,
                    width,
                    height,
                )
            ),
        }
        truth_edge_sets = {
            name: _truth_rank_edges(edges, truth_rank_by_input)
            for name, edges in edge_sets.items()
        }
        edge_metrics: dict[str, dict[str, Any]] = {}
        for name, edges in truth_edge_sets.items():
            metrics = _edge_page_metrics(edges, len(bboxes))
            edge_pages[name].append(metrics)
            edge_metrics[name] = _public_edge_page_metrics(metrics)

        for support_name, channels in (
            ("stable", STABLE_SUPPORT_CHANNELS),
            ("all", EDGE_CHANNELS),
        ):
            support_counts = Counter(
                edge
                for channel in channels
                for edge in truth_edge_sets[channel]
            )
            for threshold in support_pages[support_name]:
                supported_edges = {
                    edge for edge, count in support_counts.items() if count >= threshold
                }
                support_pages[support_name][threshold].append(
                    _edge_page_metrics(supported_edges, len(bboxes))
                )

        cases.append(
            {
                "image_id": image_id,
                "file_name": image["file_name"],
                "doc_category": image["doc_category"],
                "width": image["width"],
                "height": image["height"],
                "element_count": len(bboxes),
                "candidate_metrics": candidate_metrics,
                "edge_channel_metrics": edge_metrics,
            }
        )

    report = {
        "schema": CHUNKR_REPORT_SCHEMA,
        "status": "development-benchmark-only",
        "runtime_reorder": False,
        "annotations": str(source_path),
        "annotations_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "dataset": _chunkr_corpus_summary(payload),
        "answer_boundary": {
            "ground_truth": "ascending contiguous annotation id within each image",
            "candidate_input_order": (
                "sha256-category-and-bbox-fingerprint; annotation ids excluded"
            ),
            "candidate_uses_annotation_id": False,
            "labels_used_for_selection": False,
        },
        "order_candidates": {
            name: _order_metric_slices(order_pages[name])
            for name in ORDER_CANDIDATES
        },
        "edge_channels": {
            name: _edge_metric_slices(edge_pages[name])
            for name in EDGE_CHANNELS
        },
        "support_curves": {
            "stable": {
                "channels": list(STABLE_SUPPORT_CHANNELS),
                "thresholds": {
                    str(threshold): _edge_metric_slices(pages)
                    for threshold, pages in support_pages["stable"].items()
                },
            },
            "all": {
                "channels": list(EDGE_CHANNELS),
                "thresholds": {
                    str(threshold): _edge_metric_slices(pages)
                    for threshold, pages in support_pages["all"].items()
                },
            },
        },
        "selected_strategy_element_counts": dict(
            sorted(selected_strategy_counts.items())
        ),
        "domains": _domain_order_metrics(cases),
        "cases": cases,
    }
    report_path = (
        Path(output)
        if output is not None
        else source_path.with_name("chunkr_reading_order_report.json")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ChunkrReadingOrderBenchmarkResult(report_path, report)


def _load_chunkr_coco(payload: bytes) -> dict[str, Any]:
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Chunkr COCO annotation JSON") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("Chunkr COCO annotation root must be an object")
    images = raw.get("images")
    annotations = raw.get("annotations")
    categories = raw.get("categories")
    if not isinstance(images, list) or not images:
        raise ValueError("Chunkr COCO annotations require images")
    if not isinstance(annotations, list) or not annotations:
        raise ValueError("Chunkr COCO annotations require annotations")
    if not isinstance(categories, list) or not categories:
        raise ValueError("Chunkr COCO annotations require categories")

    image_by_id: dict[int, Mapping[str, Any]] = {}
    for image in images:
        if not isinstance(image, Mapping):
            raise ValueError("Chunkr image records must be objects")
        image_id = _required_int(image, "id", "image")
        if image_id in image_by_id:
            raise ValueError(f"duplicate Chunkr image id: {image_id}")
        file_name = str(image.get("file_name") or "").strip()
        if not file_name or Path(file_name).name != file_name:
            raise ValueError(f"invalid Chunkr image file name: {file_name!r}")
        width = _required_positive_number(image, "width", "image")
        height = _required_positive_number(image, "height", "image")
        category = str(image.get("doc_category") or "unknown").strip()
        if not category:
            raise ValueError("Chunkr image doc_category must be non-empty")
        image_by_id[image_id] = {
            **image,
            "id": image_id,
            "file_name": file_name,
            "width": width,
            "height": height,
            "doc_category": category,
        }

    category_ids: set[int] = set()
    normalized_categories: list[dict[str, Any]] = []
    for category in categories:
        if not isinstance(category, Mapping):
            raise ValueError("Chunkr category records must be objects")
        category_id = _required_int(category, "id", "category")
        if category_id in category_ids:
            raise ValueError(f"duplicate Chunkr category id: {category_id}")
        name = str(category.get("name") or "").strip()
        if not name:
            raise ValueError("Chunkr category names must be non-empty")
        category_ids.add(category_id)
        normalized_categories.append({**category, "id": category_id, "name": name})

    annotation_ids: set[int] = set()
    normalized_annotations: list[dict[str, Any]] = []
    annotations_by_image: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for annotation in annotations:
        if not isinstance(annotation, Mapping):
            raise ValueError("Chunkr annotation records must be objects")
        annotation_id = _required_int(annotation, "id", "annotation")
        image_id = _required_int(annotation, "image_id", "annotation")
        category_id = _required_int(annotation, "category_id", "annotation")
        if annotation_id in annotation_ids:
            raise ValueError(f"duplicate Chunkr annotation id: {annotation_id}")
        if image_id not in image_by_id:
            raise ValueError(f"unknown Chunkr annotation image id: {image_id}")
        if category_id not in category_ids:
            raise ValueError(f"unknown Chunkr annotation category id: {category_id}")
        bbox = annotation.get("bbox")
        if (
            not isinstance(bbox, Sequence)
            or isinstance(bbox, (str, bytes))
            or len(bbox) != 4
        ):
            raise ValueError("Chunkr annotation bbox must contain four numbers")
        try:
            x, y, width, height = (float(value) for value in bbox)
        except (TypeError, ValueError) as exc:
            raise ValueError("Chunkr annotation bbox must contain numbers") from exc
        image = image_by_id[image_id]
        if (
            not all(math.isfinite(value) for value in (x, y, width, height))
            or x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > float(image["width"]) + 1e-6
            or y + height > float(image["height"]) + 1e-6
        ):
            raise ValueError(f"invalid Chunkr bbox for annotation {annotation_id}")
        normalized = {
            **annotation,
            "id": annotation_id,
            "image_id": image_id,
            "category_id": category_id,
            "bbox": [x, y, width, height],
        }
        annotation_ids.add(annotation_id)
        normalized_annotations.append(normalized)
        annotations_by_image[image_id].append(normalized)

    if set(annotations_by_image) != set(image_by_id):
        raise ValueError("every Chunkr image must contain at least one annotation")
    for image_id, image_annotations in annotations_by_image.items():
        ids = [int(annotation["id"]) for annotation in image_annotations]
        if ids != list(range(ids[0], ids[0] + len(ids))):
            raise ValueError(
                "Chunkr annotation ids must be contiguous and ascending within "
                f"image {image_id}"
            )
        fingerprints = [
            _answer_free_anchor_fingerprint(annotation)
            for annotation in image_annotations
        ]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError(
                f"Chunkr image {image_id} contains indistinguishable layout anchors"
            )
    return {
        **raw,
        "images": [dict(image_by_id[int(image["id"])]) for image in images],
        "annotations": normalized_annotations,
        "categories": normalized_categories,
    }


def _required_int(record: Mapping[str, Any], key: str, kind: str) -> int:
    value = record.get(key)
    if isinstance(value, bool):
        raise ValueError(f"Chunkr {kind} {key} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Chunkr {kind} {key} must be an integer") from exc
    if value != normalized:
        raise ValueError(f"Chunkr {kind} {key} must be an integer")
    return normalized


def _required_positive_number(
    record: Mapping[str, Any],
    key: str,
    kind: str,
) -> int | float:
    value = record.get(key)
    if isinstance(value, bool):
        raise ValueError(f"Chunkr {kind} {key} must be positive")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Chunkr {kind} {key} must be positive") from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"Chunkr {kind} {key} must be positive")
    return int(normalized) if normalized.is_integer() else normalized


def _annotations_by_image(
    payload: Mapping[str, Any],
) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for annotation in payload["annotations"]:
        grouped[int(annotation["image_id"])].append(dict(annotation))
    return dict(grouped)


def _chunkr_corpus_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    annotations_by_image = _annotations_by_image(payload)
    category_names = {
        int(category["id"]): str(category["name"])
        for category in payload["categories"]
    }
    category_counts = Counter(
        category_names[int(annotation["category_id"])]
        for annotation in payload["annotations"]
    )
    domain_counts = Counter(
        str(image["doc_category"]) for image in payload["images"]
    )
    element_counts = [len(annotations_by_image[int(image["id"])]) for image in payload["images"]]
    return {
        "image_count": len(payload["images"]),
        "annotation_count": len(payload["annotations"]),
        "category_count": len(payload["categories"]),
        "domain_counts": dict(sorted(domain_counts.items())),
        "element_category_counts": dict(sorted(category_counts.items())),
        "minimum_elements_per_image": min(element_counts),
        "maximum_elements_per_image": max(element_counts),
        "mean_elements_per_image": round(sum(element_counts) / len(element_counts), 8),
    }


def _answer_free_anchor_fingerprint(annotation: Mapping[str, Any]) -> str:
    payload = {
        "bbox": [round(float(value), 8) for value in annotation["bbox"]],
        "category_id": int(annotation["category_id"]),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _annotation_bbox(annotation: Mapping[str, Any]) -> BBox:
    x, y, width, height = (float(value) for value in annotation["bbox"])
    return BBox(x0=x, y0=y, x1=x + width, y1=y + height)


def _truth_rank_order(
    order: Sequence[int],
    truth_rank_by_input: Sequence[int],
) -> list[int]:
    expected = set(range(len(truth_rank_by_input)))
    if len(order) != len(truth_rank_by_input) or set(order) != expected:
        raise ValueError("reading-order candidate must be a complete permutation")
    return [int(truth_rank_by_input[index]) for index in order]


def _adjacent_edges(order: Sequence[int]) -> set[tuple[int, int]]:
    return set(zip(order, order[1:], strict=False))


def _truth_rank_edges(
    edges: set[tuple[int, int]],
    truth_rank_by_input: Sequence[int],
) -> set[tuple[int, int]]:
    return {
        (int(truth_rank_by_input[source]), int(truth_rank_by_input[target]))
        for source, target in edges
        if source != target
    }


def _order_page_metrics(order: Sequence[int]) -> dict[str, Any]:
    count = len(order)
    expected = list(range(count))
    position_correct = sum(actual == target for actual, target in zip(order, expected))
    pair_total = count * (count - 1) // 2
    pair_correct = 0
    position_by_item = {item: position for position, item in enumerate(order)}
    for source in range(count):
        for target in range(source + 1, count):
            pair_correct += position_by_item[source] < position_by_item[target]
    successor_labels = max(0, count - 1)
    successor_correct = sum(
        target == source + 1
        for source, target in zip(order, order[1:], strict=False)
    )
    return {
        "element_count": count,
        "exact": order == expected,
        "position_correct": position_correct,
        "position_total": count,
        "pair_correct": pair_correct,
        "pair_total": pair_total,
        "successor_correct": successor_correct,
        "successor_predicted": successor_labels,
        "successor_labels": successor_labels,
    }


def _public_order_page_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    pair_accuracy = _ratio(int(metrics["pair_correct"]), int(metrics["pair_total"]), empty=1.0)
    return {
        "exact": bool(metrics["exact"]),
        "position_accuracy": _ratio(
            int(metrics["position_correct"]),
            int(metrics["position_total"]),
        ),
        "pairwise_accuracy": pair_accuracy,
        "kendall_tau": round(2 * pair_accuracy - 1, 8),
        "successor_accuracy": _ratio(
            int(metrics["successor_correct"]),
            int(metrics["successor_labels"]),
            empty=1.0,
        ),
    }


def _edge_page_metrics(
    edges: set[tuple[int, int]],
    element_count: int,
) -> dict[str, Any]:
    truth = {(index, index + 1) for index in range(max(0, element_count - 1))}
    return {
        "element_count": element_count,
        "correct": len(edges & truth),
        "predicted": len(edges),
        "labels": len(truth),
    }


def _public_edge_page_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    correct = int(metrics["correct"])
    predicted = int(metrics["predicted"])
    labels = int(metrics["labels"])
    precision = _ratio(correct, predicted)
    recall = _ratio(correct, labels)
    return {
        "correct": correct,
        "predicted": predicted,
        "labels": labels,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
    }


def _order_metric_slices(pages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "all": _aggregate_order_metrics(pages),
        "nontrivial": _aggregate_order_metrics(
            [page for page in pages if int(page["element_count"]) >= 2]
        ),
        "complex": _aggregate_order_metrics(
            [page for page in pages if int(page["element_count"]) >= 10]
        ),
    }


def _aggregate_order_metrics(pages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    page_count = len(pages)
    if not page_count:
        return {
            "page_count": 0,
            "exact_match_count": 0,
            "exact_match": 0.0,
            "position_accuracy": 0.0,
            "pairwise_accuracy": 0.0,
            "kendall_tau": 0.0,
            "successor_accuracy": 0.0,
        }
    exact = sum(bool(page["exact"]) for page in pages)
    position_correct = sum(int(page["position_correct"]) for page in pages)
    position_total = sum(int(page["position_total"]) for page in pages)
    pair_correct = sum(int(page["pair_correct"]) for page in pages)
    pair_total = sum(int(page["pair_total"]) for page in pages)
    successor_correct = sum(int(page["successor_correct"]) for page in pages)
    successor_labels = sum(int(page["successor_labels"]) for page in pages)
    pair_accuracy = _ratio(pair_correct, pair_total, empty=1.0)
    return {
        "page_count": page_count,
        "exact_match_count": exact,
        "exact_match": _ratio(exact, page_count),
        "position_correct": position_correct,
        "position_total": position_total,
        "position_accuracy": _ratio(position_correct, position_total),
        "pair_correct": pair_correct,
        "pair_total": pair_total,
        "pairwise_accuracy": pair_accuracy,
        "kendall_tau": round(2 * pair_accuracy - 1, 8),
        "successor_correct": successor_correct,
        "successor_labels": successor_labels,
        "successor_accuracy": _ratio(
            successor_correct,
            successor_labels,
            empty=1.0,
        ),
    }


def _edge_metric_slices(pages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "all": _aggregate_edge_metrics(pages),
        "nontrivial": _aggregate_edge_metrics(
            [page for page in pages if int(page["element_count"]) >= 2]
        ),
        "complex": _aggregate_edge_metrics(
            [page for page in pages if int(page["element_count"]) >= 10]
        ),
    }


def _aggregate_edge_metrics(pages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    correct = sum(int(page["correct"]) for page in pages)
    predicted = sum(int(page["predicted"]) for page in pages)
    labels = sum(int(page["labels"]) for page in pages)
    precision = _ratio(correct, predicted)
    recall = _ratio(correct, labels)
    return {
        "page_count": len(pages),
        "correct": correct,
        "predicted": predicted,
        "labels": labels,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
    }


def _domain_order_metrics(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    domains: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for case in cases:
        domains[str(case["doc_category"])].append(case)
    return {
        domain: {
            "page_count": len(domain_cases),
            "order_candidates": {
                candidate: _aggregate_public_order_metrics(
                    [
                        case["candidate_metrics"][candidate]
                        for case in domain_cases
                    ]
                )
                for candidate in ORDER_CANDIDATES
            },
        }
        for domain, domain_cases in sorted(domains.items())
    }


def _aggregate_public_order_metrics(
    pages: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    page_count = len(pages)
    return {
        "exact_match_count": sum(bool(page["exact"]) for page in pages),
        "exact_match": _ratio(
            sum(bool(page["exact"]) for page in pages),
            page_count,
        ),
        "mean_position_accuracy": round(
            sum(float(page["position_accuracy"]) for page in pages) / page_count,
            8,
        ),
        "mean_pairwise_accuracy": round(
            sum(float(page["pairwise_accuracy"]) for page in pages) / page_count,
            8,
        ),
        "mean_kendall_tau": round(
            sum(float(page["kendall_tau"]) for page in pages) / page_count,
            8,
        ),
    }


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return round(numerator / denominator, 8) if denominator else empty


def _f1(precision: float, recall: float) -> float:
    if not precision + recall:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 8)


def _download_bytes(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Scriptorium/0.1 (+https://github.com/followcat/Scriptorium)"
            )
        },
    )
    with urlopen(request, timeout=60) as response:
        return response.read()


def _download_with_retry(download: ChunkrDownloader, url: str) -> bytes:
    last_error: BaseException | None = None
    for attempt in range(1, CHUNKR_DOWNLOAD_ATTEMPTS + 1):
        try:
            return download(url)
        except HTTPError as error:
            last_error = error
            if error.code not in {408, 425, 429} and error.code < 500:
                raise RuntimeError(
                    f"Chunkr download failed for {url}: HTTP {error.code}"
                ) from error
        except (URLError, TimeoutError, OSError, IncompleteRead) as error:
            last_error = error
        if attempt < CHUNKR_DOWNLOAD_ATTEMPTS:
            sleep(CHUNKR_DOWNLOAD_RETRY_DELAY_SECONDS * attempt)
    detail = str(getattr(last_error, "reason", last_error))
    raise RuntimeError(
        f"Chunkr download failed for {url} after "
        f"{CHUNKR_DOWNLOAD_ATTEMPTS} attempts: {detail}"
    ) from last_error
