from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProviderAnchor:
    id: str
    page_index: int
    kind: str
    bbox: tuple[float, float, float, float]
    text: str
    order: int | None


@dataclass(frozen=True)
class ProviderAnchorBenchmarkResult:
    report_path: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class ProviderAnchorSuiteResult:
    report_path: Path
    report: dict[str, Any]


def benchmark_provider_anchors(
    oracle_structure_path: str | Path,
    semantic_sidecar_path: str | Path,
    provider_json_path: str | Path,
    *,
    floating_model_path: str | Path | None = None,
    output: str | Path | None = None,
) -> ProviderAnchorBenchmarkResult:
    """Match real provider blocks to held-out oracle anchors and score relations."""

    oracle = json.loads(Path(oracle_structure_path).read_text(encoding="utf-8"))
    semantic = json.loads(Path(semantic_sidecar_path).read_text(encoding="utf-8"))
    provider = json.loads(Path(provider_json_path).read_text(encoding="utf-8"))
    provider_name, anchors, explicit_relations = normalize_provider_anchors(provider)
    oracle_nodes = [node for node in oracle.get("document", []) if isinstance(node, Mapping)]
    assignments = match_provider_anchors(oracle_nodes, anchors)
    known_oracle_ids = {str(node.get("id")) for node in oracle_nodes}
    matched_oracle_ids = set(assignments)
    matched_provider_ids = {match["provider_id"] for match in assignments.values()}
    anchor_kinds = _anchor_kind_metrics(
        oracle_nodes,
        anchors,
        matched_oracle_ids,
        matched_provider_ids,
    )
    truth = {tuple(map(str, edge)) for edge in semantic.get("ro_linkings", [])}
    serialized_edges = _serialized_provider_edges(anchors, assignments)
    explicit_edges = _mapped_explicit_relations(explicit_relations, assignments)
    trained_floating_edges: set[tuple[str, str]] = set()
    reliable_trained_floating_edges: set[tuple[str, str]] = set()
    floating_model_sha256 = None
    if floating_model_path is not None:
        from .floating_ranker import _predict_floating_relations, load_floating_relation_ranker

        floating_bundle, floating_manifest = load_floating_relation_ranker(floating_model_path)
        floating_model_sha256 = floating_manifest.get("model_sha256")
        image = oracle.get("img", {})
        provider_structure = {
            "uid": f"provider:{oracle.get('uid') or 'page'}",
            "img": {"width": image.get("width"), "height": image.get("height")},
            "document": [
                {
                    "id": anchor.id,
                    "block_id": anchor.id,
                    "type": anchor.kind if anchor.kind in {"figure", "table"} else "text",
                    "box": list(anchor.bbox),
                    "text": anchor.text,
                }
                for anchor in anchors
            ],
        }
        floating_prediction = _predict_floating_relations(
            provider_structure,
            bundle=floating_bundle,
            manifest=floating_manifest,
        )
        trained_floating_edges = _mapped_explicit_relations(
            [
                (str(edge["source"]), str(edge["target"]))
                for edge in floating_prediction.successor_edges
            ],
            assignments,
        )
        reliable_trained_floating_edges = _mapped_explicit_relations(
            [
                (str(edge["source"]), str(edge["target"]))
                for edge in floating_prediction.successor_edges
                if edge.get("reliability_tier") == "high-precision-review"
                and int(edge.get("feature_outlier_count", 0)) == 0
            ],
            assignments,
        )
    combined_edges = serialized_edges | explicit_edges
    trained_combined_edges = combined_edges | trained_floating_edges
    reliable_trained_combined_edges = combined_edges | reliable_trained_floating_edges
    report = {
        "schema": "scriptorium-provider-anchor-benchmark/v1",
        "provider": provider_name,
        "floating_model_sha256": floating_model_sha256,
        "oracle_sample": str(oracle.get("uid") or Path(oracle_structure_path).stem),
        "oracle_anchor_count": len(known_oracle_ids),
        "matched_oracle_anchor_count": len(matched_oracle_ids),
        "oracle_anchor_recall": _ratio(len(matched_oracle_ids), len(known_oracle_ids)),
        "provider_anchor_count": len(anchors),
        "matched_provider_anchor_count": len(matched_provider_ids),
        "provider_anchor_match_rate": _ratio(len(matched_provider_ids), len(anchors)),
        "provider_to_oracle_granularity_ratio": _ratio(len(anchors), len(known_oracle_ids)),
        "anchor_kinds": anchor_kinds,
        "assignments": assignments,
        "relations": {
            "labels": len(truth),
            "serialized": _relation_metrics(serialized_edges, truth),
            "explicit": _relation_metrics(explicit_edges, truth),
            "combined": _relation_metrics(combined_edges, truth),
            "trained_floating": _relation_metrics(trained_floating_edges, truth),
            "reliable_trained_floating": _relation_metrics(
                reliable_trained_floating_edges,
                truth,
            ),
            "combined_with_trained_floating": _relation_metrics(trained_combined_edges, truth),
            "combined_with_reliable_trained_floating": _relation_metrics(
                reliable_trained_combined_edges,
                truth,
            ),
        },
    }
    report_path = (
        Path(output)
        if output is not None
        else Path(provider_json_path).with_name(f"{Path(provider_json_path).stem}.anchor-benchmark.json")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ProviderAnchorBenchmarkResult(report_path, report)


def benchmark_provider_anchor_suite(
    corpus_dir: str | Path,
    provider_dir: str | Path,
    *,
    floating_model_path: str | Path | None = None,
    output: str | Path | None = None,
) -> ProviderAnchorSuiteResult:
    """Score matching provider JSON files over a rendered Comp-HRDoc prefix."""

    corpus = Path(corpus_dir)
    providers = Path(provider_dir)
    manifest_path = corpus / "comphrdoc_benchmark_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("rendered Comp-HRDoc benchmark manifest is required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    missing: list[str] = []
    for sample in manifest.get("samples", []):
        sample_id = str(sample["id"])
        provider_path = providers / f"{sample_id}.structure.json"
        if not provider_path.is_file():
            missing.append(sample_id)
            continue
        case = benchmark_provider_anchors(
            corpus / str(sample["structure"]),
            corpus / str(sample["semantic_sidecar"]),
            provider_path,
            floating_model_path=floating_model_path,
            output=providers / "anchor-benchmarks" / f"{sample_id}.json",
        ).report
        cases.append(case)
    if not cases:
        raise ValueError("provider directory contains no matching structure JSON files")
    relation_keys = (
        "serialized",
        "explicit",
        "combined",
        "trained_floating",
        "reliable_trained_floating",
        "combined_with_trained_floating",
        "combined_with_reliable_trained_floating",
    )
    relation_summary = {
        key: _sum_relation_metrics([case["relations"][key] for case in cases])
        for key in relation_keys
    }
    kind_names = sorted(
        {
            kind
            for case in cases
            for kind in case.get("anchor_kinds", {})
        }
    )
    anchor_kind_summary = {
        kind: _sum_anchor_kind_metrics(
            [case.get("anchor_kinds", {}).get(kind, {}) for case in cases]
        )
        for kind in kind_names
    }
    oracle_total = sum(case["oracle_anchor_count"] for case in cases)
    oracle_matched = sum(case["matched_oracle_anchor_count"] for case in cases)
    provider_total = sum(case["provider_anchor_count"] for case in cases)
    provider_matched = sum(case["matched_provider_anchor_count"] for case in cases)
    report = {
        "schema": "scriptorium-provider-anchor-suite/v1",
        "corpus_manifest": str(manifest_path),
        "selection": manifest.get("selection"),
        "provider": cases[0]["provider"],
        "case_count": len(cases),
        "missing_provider_case_count": len(missing),
        "missing_provider_cases": missing,
        "oracle_anchor_count": oracle_total,
        "matched_oracle_anchor_count": oracle_matched,
        "oracle_anchor_recall": _ratio(oracle_matched, oracle_total),
        "provider_anchor_count": provider_total,
        "matched_provider_anchor_count": provider_matched,
        "provider_anchor_match_rate": _ratio(provider_matched, provider_total),
        "anchor_kinds": anchor_kind_summary,
        "relations": relation_summary,
        "cases": cases,
    }
    report_path = Path(output) if output is not None else providers / "provider_anchor_suite_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ProviderAnchorSuiteResult(report_path, report)


def normalize_provider_anchors(
    payload: Mapping[str, Any],
) -> tuple[str, list[ProviderAnchor], list[tuple[str, str]]]:
    if isinstance(payload.get("raw_results"), list):
        return _normalize_paddle_vl(payload)
    if payload.get("schema_name") == "DoclingDocument" or isinstance(payload.get("body"), Mapping):
        return _normalize_docling(payload)
    if isinstance(payload.get("document"), list) and isinstance(payload.get("img"), Mapping):
        return _normalize_roor_style(payload)
    if isinstance(payload.get("pages"), list):
        return _normalize_page_elements(payload)
    raise ValueError("unsupported provider anchor JSON schema")


def _normalize_paddle_vl(
    payload: Mapping[str, Any],
) -> tuple[str, list[ProviderAnchor], list[tuple[str, str]]]:
    anchors: list[ProviderAnchor] = []
    order = 0
    for page_position, page in enumerate(payload.get("raw_results", [])):
        if not isinstance(page, Mapping):
            continue
        page_index = int(page.get("page_index", page_position))
        for item_position, item in enumerate(page.get("parsing_res_list", [])):
            if not isinstance(item, Mapping):
                continue
            box = item.get("block_bbox")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            label = str(item.get("block_label") or "text")
            block_id = item.get("block_id", item_position)
            anchors.append(
                ProviderAnchor(
                    f"page-{page_index}:block-{block_id}",
                    page_index,
                    _kind_alias(label),
                    tuple(map(float, box)),
                    str(item.get("block_content") or ""),
                    order,
                )
            )
            order += 1
    return str(payload.get("source") or "paddleocr-vl"), anchors, []


def match_provider_anchors(
    oracle_nodes: Sequence[Mapping[str, Any]],
    provider_anchors: Sequence[ProviderAnchor],
    *,
    minimum_score: float = 0.45,
) -> dict[str, dict[str, Any]]:
    """Assign every oracle node to its strongest compatible provider block."""

    assignments: dict[str, dict[str, Any]] = {}
    for oracle in oracle_nodes:
        oracle_id = str(oracle.get("id"))
        box = oracle.get("box")
        if not oracle_id or not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        oracle_box = tuple(map(float, box))
        oracle_kind = _kind_alias(str(oracle.get("type") or "text"))
        best: tuple[float, float, ProviderAnchor] | None = None
        for provider in provider_anchors:
            if not _compatible_kinds(oracle_kind, provider.kind):
                continue
            oracle_coverage, provider_coverage = _bbox_coverages(oracle_box, provider.bbox)
            center_score = _center_containment_score(oracle_box, provider.bbox)
            score = oracle_coverage * 0.65 + min(1.0, provider_coverage * 4) * 0.20 + center_score * 0.15
            ranking = (score, oracle_coverage, provider)
            if best is None or ranking[:2] > best[:2]:
                best = ranking
        if best is None or best[0] < minimum_score:
            continue
        score, oracle_coverage, provider = best
        _, provider_coverage = _bbox_coverages(oracle_box, provider.bbox)
        assignments[oracle_id] = {
            "provider_id": provider.id,
            "provider_kind": provider.kind,
            "score": round(score, 8),
            "oracle_coverage": round(oracle_coverage, 8),
            "provider_coverage": round(provider_coverage, 8),
            "oracle_box": [round(value, 8) for value in oracle_box],
        }
    return assignments


def _normalize_docling(
    payload: Mapping[str, Any],
) -> tuple[str, list[ProviderAnchor], list[tuple[str, str]]]:
    page_heights = {
        int(value.get("page_no", int(key))): float(value.get("size", {}).get("height", 0))
        for key, value in payload.get("pages", {}).items()
        if isinstance(value, Mapping)
    }
    index: dict[str, Mapping[str, Any]] = {}
    for collection in ("texts", "pictures", "tables", "groups", "key_value_items", "form_items"):
        for item in payload.get(collection, []):
            if isinstance(item, Mapping) and item.get("self_ref"):
                index[str(item["self_ref"])] = item
    anchors_by_id: dict[str, ProviderAnchor] = {}
    explicit: list[tuple[str, str]] = []
    order = 0

    def emit(ref: str, *, forced_order: int | None = None) -> None:
        nonlocal order
        if ref in anchors_by_id:
            return
        item = index.get(ref)
        if item is None:
            return
        anchor = _docling_anchor(item, page_heights, order=forced_order if forced_order is not None else order)
        if anchor is not None:
            anchors_by_id[ref] = anchor
            if forced_order is None:
                order += 1
        for caption_ref in _docling_refs(item.get("captions")):
            emit(caption_ref)
            if anchor is not None and anchor.kind == "table":
                explicit.append((caption_ref, ref))
            elif anchor is not None:
                explicit.append((ref, caption_ref))
        for child_ref in _docling_refs(item.get("children")):
            emit(child_ref)

    body = payload.get("body")
    if isinstance(body, Mapping):
        for ref in _docling_refs(body.get("children")):
            emit(ref)
    for ref in index:
        emit(ref, forced_order=None)
    return str(payload.get("source") or "docling"), list(anchors_by_id.values()), explicit


def _docling_anchor(
    item: Mapping[str, Any],
    page_heights: Mapping[int, float],
    *,
    order: int,
) -> ProviderAnchor | None:
    prov = next((value for value in item.get("prov", []) if isinstance(value, Mapping)), None)
    if prov is None or not isinstance(prov.get("bbox"), Mapping):
        return None
    page_no = int(prov.get("page_no", 1))
    page_height = page_heights.get(page_no)
    if not page_height:
        return None
    bbox = prov["bbox"]
    left, top, right, bottom = map(float, (bbox["l"], bbox["t"], bbox["r"], bbox["b"]))
    origin = str(bbox.get("coord_origin") or "BOTTOMLEFT").upper()
    normalized_box = (
        (left, page_height - top, right, page_height - bottom)
        if origin == "BOTTOMLEFT"
        else (left, top, right, bottom)
    )
    ref = str(item.get("self_ref") or "")
    collection = ref.split("/")[1] if ref.startswith("#/") and len(ref.split("/")) > 2 else ""
    label = str(item.get("label") or "")
    kind = "figure" if collection == "pictures" else "table" if collection == "tables" else _kind_alias(label)
    return ProviderAnchor(
        ref,
        page_no - 1,
        kind,
        normalized_box,
        str(item.get("text") or item.get("orig") or ""),
        order,
    )


def _normalize_roor_style(
    payload: Mapping[str, Any],
) -> tuple[str, list[ProviderAnchor], list[tuple[str, str]]]:
    anchors = []
    for order, item in enumerate(payload["document"]):
        if not isinstance(item, Mapping) or not isinstance(item.get("box"), (list, tuple)):
            continue
        anchors.append(
            ProviderAnchor(
                str(item.get("id", order)),
                0,
                _kind_alias(str(item.get("type") or "text")),
                tuple(map(float, item["box"])),
                str(item.get("text") or ""),
                order,
            )
        )
    return str(payload.get("source") or "roor-style"), anchors, _edge_pairs(payload.get("successor_edges"))


def _normalize_page_elements(
    payload: Mapping[str, Any],
) -> tuple[str, list[ProviderAnchor], list[tuple[str, str]]]:
    anchors: list[ProviderAnchor] = []
    explicit: list[tuple[str, str]] = []
    order = 0
    for page_position, page in enumerate(payload["pages"]):
        if not isinstance(page, Mapping):
            continue
        page_index = int(page.get("page_index", page_position))
        for item in page.get("elements", []):
            if not isinstance(item, Mapping):
                continue
            box = item.get("bbox_pdf") or item.get("box") or item.get("bbox")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            anchors.append(
                ProviderAnchor(
                    str(item.get("id", f"page-{page_index}-anchor-{order}")),
                    page_index,
                    _kind_alias(str(item.get("block_label") or item.get("type") or "text")),
                    tuple(map(float, box)),
                    str(item.get("block_content") or item.get("text") or ""),
                    order,
                )
            )
            order += 1
        explicit.extend(_edge_pairs(page.get("successor_edges")))
    return str(payload.get("source") or "page-elements"), anchors, explicit


def _serialized_provider_edges(
    anchors: Sequence[ProviderAnchor],
    assignments: Mapping[str, Mapping[str, Any]],
) -> set[tuple[str, str]]:
    oracle_by_provider: dict[str, list[str]] = defaultdict(list)
    for oracle_id, match in assignments.items():
        oracle_by_provider[str(match["provider_id"])].append(oracle_id)
    ordered = sorted((anchor for anchor in anchors if anchor.order is not None), key=lambda item: item.order)
    flattened: list[str] = []
    for anchor in ordered:
        flattened.extend(
            sorted(
                oracle_by_provider.get(anchor.id, []),
                key=lambda oracle_id: _assignment_geometry_key(assignments[oracle_id]),
            )
        )
    return {(source, target) for source, target in zip(flattened, flattened[1:]) if source != target}


def _mapped_explicit_relations(
    relations: Sequence[tuple[str, str]],
    assignments: Mapping[str, Mapping[str, Any]],
) -> set[tuple[str, str]]:
    oracle_by_provider: dict[str, list[str]] = defaultdict(list)
    for oracle_id, match in assignments.items():
        oracle_by_provider[str(match["provider_id"])].append(oracle_id)
    result: set[tuple[str, str]] = set()
    for source, target in relations:
        source_ids = sorted(
            oracle_by_provider.get(source, []),
            key=lambda oracle_id: _assignment_geometry_key(assignments[oracle_id]),
        )
        target_ids = sorted(
            oracle_by_provider.get(target, []),
            key=lambda oracle_id: _assignment_geometry_key(assignments[oracle_id]),
        )
        if source_ids and target_ids:
            result.add((source_ids[-1], target_ids[0]))
    return result


def _assignment_geometry_key(match: Mapping[str, Any]) -> tuple[float, float]:
    box = match.get("oracle_box", [0, 0, 0, 0])
    return float(box[1]), float(box[0])


def _docling_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item["$ref"]) for item in value if isinstance(item, Mapping) and item.get("$ref")]


def _edge_pairs(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    result = []
    for edge in value:
        if isinstance(edge, Mapping) and "source" in edge and "target" in edge:
            result.append((str(edge["source"]), str(edge["target"])))
        elif isinstance(edge, (list, tuple)) and len(edge) >= 2:
            result.append((str(edge[0]), str(edge[1])))
    return result


def _kind_alias(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"picture", "image", "fig", "figure", "chart"}:
        return "figure"
    if "table" in normalized:
        return "table"
    if "caption" in normalized or normalized in {
        "figure-title",
        "image-title",
        "table-title",
        "chart-title",
    }:
        return "caption"
    return "text"


def _compatible_kinds(oracle: str, provider: str) -> bool:
    if oracle == "figure":
        return provider == "figure"
    if oracle == "table":
        return provider == "table"
    return provider in {"text", "caption"}


def _bbox_coverages(
    oracle: tuple[float, float, float, float],
    provider: tuple[float, float, float, float],
) -> tuple[float, float]:
    intersection_width = max(0.0, min(oracle[2], provider[2]) - max(oracle[0], provider[0]))
    intersection_height = max(0.0, min(oracle[3], provider[3]) - max(oracle[1], provider[1]))
    intersection = intersection_width * intersection_height
    oracle_area = max((oracle[2] - oracle[0]) * (oracle[3] - oracle[1]), 1.0)
    provider_area = max((provider[2] - provider[0]) * (provider[3] - provider[1]), 1.0)
    return intersection / oracle_area, intersection / provider_area


def _center_containment_score(
    oracle: tuple[float, float, float, float],
    provider: tuple[float, float, float, float],
) -> float:
    center_x = (oracle[0] + oracle[2]) / 2
    center_y = (oracle[1] + oracle[3]) / 2
    return float(provider[0] <= center_x <= provider[2] and provider[1] <= center_y <= provider[3])


def _relation_metrics(predicted: set[tuple[str, str]], truth: set[tuple[str, str]]) -> dict[str, Any]:
    correct = len(predicted & truth)
    precision = _ratio(correct, len(predicted))
    recall = _ratio(correct, len(truth))
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "correct": correct,
        "predicted": len(predicted),
        "labels": len(truth),
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
    }


def _anchor_kind_metrics(
    oracle_nodes: Sequence[Mapping[str, Any]],
    provider_anchors: Sequence[ProviderAnchor],
    matched_oracle_ids: set[str],
    matched_provider_ids: set[str],
) -> dict[str, dict[str, Any]]:
    oracle_counts = Counter(_kind_alias(str(node.get("type") or "text")) for node in oracle_nodes)
    matched_oracle_counts = Counter(
        _kind_alias(str(node.get("type") or "text"))
        for node in oracle_nodes
        if str(node.get("id")) in matched_oracle_ids
    )
    provider_counts = Counter(anchor.kind for anchor in provider_anchors)
    matched_provider_counts = Counter(
        anchor.kind for anchor in provider_anchors if anchor.id in matched_provider_ids
    )
    return {
        kind: {
            "oracle": oracle_counts[kind],
            "matched_oracle": matched_oracle_counts[kind],
            "oracle_recall": _ratio(matched_oracle_counts[kind], oracle_counts[kind]),
            "provider": provider_counts[kind],
            "matched_provider": matched_provider_counts[kind],
            "provider_match_rate": _ratio(matched_provider_counts[kind], provider_counts[kind]),
        }
        for kind in sorted(set(oracle_counts) | set(provider_counts))
    }


def _sum_anchor_kind_metrics(metrics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    oracle = sum(int(item.get("oracle", 0)) for item in metrics)
    matched_oracle = sum(int(item.get("matched_oracle", 0)) for item in metrics)
    provider = sum(int(item.get("provider", 0)) for item in metrics)
    matched_provider = sum(int(item.get("matched_provider", 0)) for item in metrics)
    return {
        "oracle": oracle,
        "matched_oracle": matched_oracle,
        "oracle_recall": _ratio(matched_oracle, oracle),
        "provider": provider,
        "matched_provider": matched_provider,
        "provider_match_rate": _ratio(matched_provider, provider),
    }


def _sum_relation_metrics(metrics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    correct = sum(int(item["correct"]) for item in metrics)
    predicted = sum(int(item["predicted"]) for item in metrics)
    labels = sum(int(item["labels"]) for item in metrics)
    precision = _ratio(correct, predicted)
    recall = _ratio(correct, labels)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "correct": correct,
        "predicted": predicted,
        "labels": labels,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 8) if denominator else 0.0
