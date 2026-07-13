from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Protocol


class ProviderAnchorLike(Protocol):
    id: str
    kind: str
    bbox: tuple[float, float, float, float]
    text: str
    order: int | None
    group_id: str | None


@dataclass(frozen=True)
class _OracleAnchor:
    id: str
    group_id: str
    kind: str
    bbox: tuple[float, float, float, float]
    text: str


_CAPTION_PREFIX = re.compile(
    r"^\s*(?P<family>figure|fig\.?|table|chart|algorithm|\u56fe\u8868|\u56fe|\u8868)"
    r"[\s\u00a0]*(?P<marker>\d+(?:[.\-]\d+)*|[ivxlcdm]+|[\u4e00-\u9fff\d]+)",
    re.IGNORECASE,
)

_SIGNATURE_KEYS = (
    "oracle_miss_rate",
    "provider_hallucination_rate",
    "type_incompatibility_rate",
    "fragmented_unit_rate",
    "merged_unit_rate",
    "size_error_rate",
    "provider_overlap_unit_rate",
    "center_error_p90",
    "edge_error_p90",
    "character_error_mean",
    "token_error_mean",
    "caption_prefix_loss_rate",
)


def characterize_provider_degradation(
    oracle_nodes: Sequence[Mapping[str, Any]],
    provider_anchors: Sequence[ProviderAnchorLike],
    *,
    width: float,
    height: float,
    minimum_score: float = 0.45,
) -> dict[str, Any]:
    """Describe provider layout/OCR degradation without reading relation labels."""

    if width <= 0 or height <= 0:
        raise ValueError("provider degradation page dimensions must be positive")
    oracle = _normalize_oracle_anchors(oracle_nodes)
    providers = [anchor for anchor in provider_anchors if _valid_box(anchor.bbox)]
    oracle_best, provider_best = _geometry_correspondence(
        oracle,
        providers,
        minimum_score=minimum_score,
    )
    oracle_groups = _group_oracle_anchors(oracle)
    provider_groups = _group_provider_anchors(providers)
    oracle_to_provider_groups: dict[str, set[str]] = defaultdict(set)
    provider_to_oracle_groups: dict[str, set[str]] = defaultdict(set)
    oracle_by_provider_group: dict[str, list[_OracleAnchor]] = defaultdict(list)
    for anchor in oracle:
        match = oracle_best.get(anchor.id)
        if match is None:
            continue
        provider = providers[match[0]]
        provider_group_id = _provider_group_id(provider)
        oracle_to_provider_groups[anchor.group_id].add(provider_group_id)
        provider_to_oracle_groups[provider_group_id].add(anchor.group_id)
        oracle_by_provider_group[provider_group_id].append(anchor)

    reverse_oracle_groups: dict[str, set[str]] = defaultdict(set)
    for provider in providers:
        match = provider_best.get(provider.id)
        if match is None:
            continue
        reverse_oracle_groups[_provider_group_id(provider)].add(oracle[match[0]].group_id)

    unmatched_oracle = [anchor for anchor in oracle if anchor.id not in oracle_best]
    unmatched_provider = [anchor for anchor in providers if anchor.id not in provider_best]
    nested_graphical_content = _nested_graphical_content(
        providers,
        provider_best,
        oracle,
        provider_count=len(providers),
    )
    nested_provider_ids = {
        str(record["provider_id"]) for record in nested_graphical_content["records"]
    }
    hallucinated_provider = [
        anchor for anchor in unmatched_provider if anchor.id not in nested_provider_ids
    ]
    type_confusion = _type_confusion(oracle, providers, oracle_best)
    geometry = _geometry_diagnostics(
        provider_groups,
        oracle_by_provider_group,
        width=width,
        height=height,
    )
    segmentation = _segmentation_diagnostics(
        oracle_groups,
        provider_groups,
        oracle_to_provider_groups,
        provider_to_oracle_groups,
    )
    overlap = _provider_overlap_diagnostics(
        oracle_groups,
        provider_groups,
        provider_to_oracle_groups,
        reverse_oracle_groups,
        nested_graphical_content,
    )
    duplicates = _duplicate_diagnostics(
        oracle_groups,
        provider_groups,
        reverse_oracle_groups,
    )
    text_fidelity = _text_fidelity_diagnostics(
        oracle_groups,
        provider_groups,
        oracle_to_provider_groups,
        oracle_by_provider_group,
    )
    unmatched_by_kind = {
        "oracle": _unmatched_by_kind(oracle, {anchor.id for anchor in unmatched_oracle}),
        "provider": _unmatched_by_kind(
            providers,
            {anchor.id for anchor in unmatched_provider},
        ),
    }
    size_error_records = [
        record
        for record in geometry["records"]
        if float(record["area_ratio"]) < 0.6 or float(record["area_ratio"]) > 1.4
    ]
    error_taxonomy = {
        "policy": {
            "name": "led-inspired-granularity-aware-layout-diagnostic-v1",
            "geometry_match_minimum_score": minimum_score,
            "size_area_ratio_range": [0.6, 1.4],
            "overlap_iou_threshold": 0.1,
            "duplicate_iou_threshold": 0.9,
            "note": (
                "LED categories are adapted to provider/oracle granularity by grouping "
                "anchors before split, merge, size, overlap, and duplicate diagnosis."
            ),
        },
        "missing": _count_rate(len(unmatched_oracle), len(oracle)),
        "hallucination": _count_rate(len(hallucinated_provider), len(providers)),
        "size_error": _count_rate(len(size_error_records), geometry["matched_group_count"]),
        "split": _count_rate(
            segmentation["fragmented_oracle_unit_count"],
            segmentation["oracle_unit_count"],
        ),
        "merge": _count_rate(
            segmentation["merged_provider_unit_count"],
            segmentation["provider_unit_count"],
        ),
        "overlap": {
            "count": overlap["overlapping_pair_count"],
            "denominator": overlap["possible_pair_count"],
            "rate": overlap["overlapping_pair_rate"],
        },
        "duplicate": _count_rate(
            duplicates["duplicated_oracle_unit_count"],
            len(oracle_groups),
        ),
        "misclassification": _count_rate(
            type_confusion["incompatible_count"],
            type_confusion["geometry_matched_count"],
        ),
        "details": {
            "missing_oracle_ids": [anchor.id for anchor in unmatched_oracle],
            "hallucinated_provider_ids": [anchor.id for anchor in hallucinated_provider],
            "nested_graphical_provider_ids": sorted(nested_provider_ids),
            "size_error_provider_group_ids": [
                str(record["provider_group_id"]) for record in size_error_records
            ],
            "misclassified_oracle_ids": type_confusion["incompatible_oracle_ids"],
        },
    }
    report = {
        "schema": "scriptorium-provider-degradation/v1",
        "answer_free_relation_policy": {
            "uses_oracle_geometry": True,
            "uses_oracle_text": True,
            "uses_oracle_types": True,
            "uses_relation_labels": False,
            "runtime_reorder": False,
        },
        "reference": {
            "page_width": width,
            "page_height": height,
            "oracle_anchor_count": len(oracle),
            "provider_anchor_count": len(providers),
            "oracle_unit_count": len(oracle_groups),
            "provider_unit_count": len(provider_groups),
        },
        "error_taxonomy": error_taxonomy,
        "unmatched_by_kind": unmatched_by_kind,
        "type_confusion": type_confusion,
        "segmentation": segmentation,
        "geometry": geometry,
        "provider_overlap": overlap,
        "duplicates": duplicates,
        "nested_graphical_content": nested_graphical_content,
        "text_fidelity": text_fidelity,
    }
    report["signature"] = _degradation_signature(report)
    return report


def compare_with_synthetic_profiles(
    real_report: Mapping[str, Any],
    profile_reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare normalized diagnostic signatures; this is not a fitted classifier."""

    real_signature = {
        key: float(real_report.get("signature", {}).get(key, 0.0))
        for key in _SIGNATURE_KEYS
    }
    profiles: dict[str, Any] = {}
    for name in sorted(profile_reports):
        signature = {
            key: float(profile_reports[name].get("signature", {}).get(key, 0.0))
            for key in _SIGNATURE_KEYS
        }
        profiles[name] = {
            "distance": _signature_distance(real_signature, signature),
            "signature": signature,
        }
    nearest = min(profiles, key=lambda name: (profiles[name]["distance"], name)) if profiles else None
    return {
        "policy": "unweighted-normalized-rmse-v1",
        "interpretation": (
            "Descriptive proximity to deterministic source-neutral profiles; "
            "not a calibrated provider classifier or a runtime promotion gate."
        ),
        "feature_names": list(_SIGNATURE_KEYS),
        "real_signature": real_signature,
        "nearest_profile": nearest,
        "profiles": profiles,
    }


def aggregate_provider_degradation(
    reports: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Micro-aggregate per-page provider diagnostics for a benchmark suite."""

    if not reports:
        raise ValueError("at least one provider degradation report is required")
    reference = {
        key: sum(int(report["reference"][key]) for report in reports)
        for key in (
            "oracle_anchor_count",
            "provider_anchor_count",
            "oracle_unit_count",
            "provider_unit_count",
        )
    }
    taxonomy_denominators = {
        "missing": reference["oracle_anchor_count"],
        "hallucination": reference["provider_anchor_count"],
        "size_error": sum(
            int(report["geometry"]["matched_group_count"]) for report in reports
        ),
        "split": reference["oracle_unit_count"],
        "merge": reference["provider_unit_count"],
        "overlap": sum(
            int(report["provider_overlap"]["possible_pair_count"]) for report in reports
        ),
        "duplicate": reference["oracle_unit_count"],
        "misclassification": sum(
            int(report["type_confusion"]["geometry_matched_count"]) for report in reports
        ),
    }
    taxonomy_counts = {
        name: sum(int(report["error_taxonomy"][name]["count"]) for report in reports)
        for name in taxonomy_denominators
    }
    error_taxonomy = {
        "policy": reports[0]["error_taxonomy"]["policy"],
        **{
            name: _count_rate(taxonomy_counts[name], denominator)
            for name, denominator in taxonomy_denominators.items()
        },
    }
    type_confusion = _aggregate_type_confusion(reports)
    segmentation = _aggregate_segmentation(reports, reference)
    geometry_records = [
        record
        for report in reports
        for record in report["geometry"].get("records", [])
    ]
    geometry = _geometry_report_from_records(geometry_records)
    overlap_pairs = [
        pair
        for report in reports
        for pair in report["provider_overlap"].get("pairs", [])
    ]
    overlap_involved = sum(
        int(report["provider_overlap"]["involved_provider_unit_count"])
        for report in reports
    )
    provider_overlap = {
        "policy": reports[0]["provider_overlap"]["policy"],
        "possible_pair_count": taxonomy_denominators["overlap"],
        "overlapping_pair_count": taxonomy_counts["overlap"],
        "overlapping_pair_rate": _ratio(
            taxonomy_counts["overlap"], taxonomy_denominators["overlap"]
        ),
        "involved_provider_unit_count": overlap_involved,
        "involved_provider_unit_rate": _ratio(
            overlap_involved, reference["provider_unit_count"]
        ),
        "pairs": overlap_pairs,
    }
    duplicate_units = [
        item
        for report in reports
        for item in report["duplicates"].get("duplicated_oracle_units", [])
    ]
    duplicates = {
        "duplicated_oracle_unit_count": len(duplicate_units),
        "duplicated_oracle_unit_rate": _ratio(
            len(duplicate_units), reference["oracle_unit_count"]
        ),
        "duplicated_oracle_units": duplicate_units,
    }
    nested_records = [
        record
        for report in reports
        for record in report["nested_graphical_content"].get("records", [])
    ]
    nested_graphical_content = _nested_graphical_report(
        nested_records,
        reference["provider_anchor_count"],
    )
    text_fidelity = _aggregate_text_fidelity(reports)
    unmatched_by_kind = {
        side: _aggregate_unmatched_by_kind(reports, side)
        for side in ("oracle", "provider")
    }
    aggregate = {
        "schema": "scriptorium-provider-degradation-suite/v1",
        "case_count": len(reports),
        "answer_free_relation_policy": reports[0]["answer_free_relation_policy"],
        "reference": reference,
        "error_taxonomy": error_taxonomy,
        "unmatched_by_kind": unmatched_by_kind,
        "type_confusion": type_confusion,
        "segmentation": segmentation,
        "geometry": geometry,
        "provider_overlap": provider_overlap,
        "duplicates": duplicates,
        "nested_graphical_content": nested_graphical_content,
        "text_fidelity": text_fidelity,
    }
    aggregate["signature"] = _degradation_signature(aggregate)
    profile_names = sorted(
        set.intersection(
            *(
                set(report.get("synthetic_profile_comparison", {}).get("profiles", {}))
                for report in reports
            )
        )
    )
    if profile_names:
        profile_signatures: dict[str, dict[str, float]] = {}
        weights = [max(1, int(report["reference"]["oracle_anchor_count"])) for report in reports]
        for profile_name in profile_names:
            profile_signatures[profile_name] = {
                key: round(
                    sum(
                        weight
                        * float(
                            report["synthetic_profile_comparison"]["profiles"][profile_name][
                                "signature"
                            ].get(key, 0.0)
                        )
                        for report, weight in zip(reports, weights, strict=True)
                    )
                    / sum(weights),
                    8,
                )
                for key in _SIGNATURE_KEYS
            }
        aggregate["synthetic_profile_comparison"] = compare_with_synthetic_profiles(
            aggregate,
            {name: {"signature": signature} for name, signature in profile_signatures.items()},
        )
    return aggregate


def _normalize_oracle_anchors(
    nodes: Sequence[Mapping[str, Any]],
) -> list[_OracleAnchor]:
    anchors: list[_OracleAnchor] = []
    for index, node in enumerate(nodes):
        box = node.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        normalized_box = tuple(map(float, box))
        if not _valid_box(normalized_box):
            continue
        anchor_id = str(node.get("id", index))
        block_id = node.get("block_id")
        anchors.append(
            _OracleAnchor(
                anchor_id,
                str(block_id) if block_id is not None else anchor_id,
                _kind_alias(str(node.get("type") or "text")),
                normalized_box,
                str(node.get("text") or ""),
            )
        )
    return anchors


def _geometry_correspondence(
    oracle: Sequence[_OracleAnchor],
    providers: Sequence[ProviderAnchorLike],
    *,
    minimum_score: float,
) -> tuple[dict[str, tuple[int, float]], dict[str, tuple[int, float]]]:
    oracle_best: dict[str, tuple[int, float]] = {}
    for anchor in oracle:
        candidates = [
            (index, _anchor_match_score(anchor.bbox, provider.bbox), provider.id)
            for index, provider in enumerate(providers)
        ]
        if not candidates:
            continue
        index, score, _ = max(candidates, key=lambda item: (item[1], item[2]))
        if score >= minimum_score:
            oracle_best[anchor.id] = (index, score)
    provider_best: dict[str, tuple[int, float]] = {}
    for provider in providers:
        candidates = [
            (index, _anchor_match_score(anchor.bbox, provider.bbox), anchor.id)
            for index, anchor in enumerate(oracle)
        ]
        if not candidates:
            continue
        index, score, _ = max(candidates, key=lambda item: (item[1], item[2]))
        if score >= minimum_score:
            provider_best[provider.id] = (index, score)
    return oracle_best, provider_best


def _type_confusion(
    oracle: Sequence[_OracleAnchor],
    providers: Sequence[ProviderAnchorLike],
    oracle_best: Mapping[str, tuple[int, float]],
) -> dict[str, Any]:
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    incompatible_ids: list[str] = []
    for anchor in oracle:
        match = oracle_best.get(anchor.id)
        if match is None:
            continue
        provider_kind = _kind_alias(providers[match[0]].kind)
        matrix[anchor.kind][provider_kind] += 1
        if not _compatible_kinds(anchor.kind, provider_kind):
            incompatible_ids.append(anchor.id)
    matched = sum(sum(row.values()) for row in matrix.values())
    return {
        "geometry_matched_count": matched,
        "compatible_count": matched - len(incompatible_ids),
        "incompatible_count": len(incompatible_ids),
        "incompatible_rate": _ratio(len(incompatible_ids), matched),
        "matrix": {
            source: dict(sorted(targets.items()))
            for source, targets in sorted(matrix.items())
        },
        "incompatible_oracle_ids": incompatible_ids,
    }


def _geometry_diagnostics(
    provider_groups: Mapping[str, Sequence[ProviderAnchorLike]],
    oracle_by_provider_group: Mapping[str, Sequence[_OracleAnchor]],
    *,
    width: float,
    height: float,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for group_id in sorted(oracle_by_provider_group):
        provider_members = provider_groups.get(group_id)
        oracle_members = oracle_by_provider_group[group_id]
        if not provider_members or not oracle_members:
            continue
        provider_box = _envelope([anchor.bbox for anchor in provider_members])
        oracle_box = _envelope([anchor.bbox for anchor in oracle_members])
        metrics = _normalized_bbox_metrics(
            oracle_box,
            provider_box,
            width=width,
            height=height,
        )
        records.append(
            {
                "provider_group_id": group_id,
                "provider_anchor_count": len(provider_members),
                "mapped_oracle_anchor_count": len(oracle_members),
                "oracle_group_count": len({anchor.group_id for anchor in oracle_members}),
                "oracle_box": _rounded_box(oracle_box),
                "provider_box": _rounded_box(provider_box),
                **metrics,
            }
        )
    return _geometry_report_from_records(records)


def _geometry_report_from_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = (
        "center_distance_ratio",
        "edge_mean_error_ratio",
        "edge_max_error_ratio",
        "iou",
        "oracle_coverage",
        "provider_coverage",
        "area_ratio",
    )
    return {
        "matched_group_count": len(records),
        **{
            field: _distribution([float(record[field]) for record in records])
            for field in fields
        },
        "records": list(records),
    }


def _segmentation_diagnostics(
    oracle_groups: Mapping[str, Sequence[_OracleAnchor]],
    provider_groups: Mapping[str, Sequence[ProviderAnchorLike]],
    oracle_to_provider: Mapping[str, set[str]],
    provider_to_oracle: Mapping[str, set[str]],
) -> dict[str, Any]:
    fragmented = [
        {"oracle_group_id": group_id, "provider_group_ids": sorted(provider_ids), "parts": len(provider_ids)}
        for group_id, provider_ids in sorted(oracle_to_provider.items())
        if len(provider_ids) > 1
    ]
    merged = [
        {"provider_group_id": group_id, "oracle_group_ids": sorted(oracle_ids), "sources": len(oracle_ids)}
        for group_id, oracle_ids in sorted(provider_to_oracle.items())
        if len(oracle_ids) > 1
    ]
    matched_oracle = sum(bool(oracle_to_provider.get(group_id)) for group_id in oracle_groups)
    matched_provider = sum(bool(provider_to_oracle.get(group_id)) for group_id in provider_groups)
    return {
        "oracle_unit_count": len(oracle_groups),
        "matched_oracle_unit_count": matched_oracle,
        "fragmented_oracle_unit_count": len(fragmented),
        "fragmented_oracle_unit_rate": _ratio(len(fragmented), len(oracle_groups)),
        "fragmentation_excess_part_count": sum(item["parts"] - 1 for item in fragmented),
        "fragment_multiplicity": _distribution([float(item["parts"]) for item in fragmented]),
        "provider_unit_count": len(provider_groups),
        "matched_provider_unit_count": matched_provider,
        "merged_provider_unit_count": len(merged),
        "merged_provider_unit_rate": _ratio(len(merged), len(provider_groups)),
        "merge_excess_source_count": sum(item["sources"] - 1 for item in merged),
        "merge_multiplicity": _distribution([float(item["sources"]) for item in merged]),
        "fragmented_oracle_units": fragmented,
        "merged_provider_units": merged,
    }


def _provider_overlap_diagnostics(
    oracle_groups: Mapping[str, Sequence[_OracleAnchor]],
    provider_groups: Mapping[str, Sequence[ProviderAnchorLike]],
    provider_to_oracle_groups: Mapping[str, set[str]],
    reverse_oracle_groups: Mapping[str, set[str]],
    nested_graphical_content: Mapping[str, Any],
) -> dict[str, Any]:
    oracle_boxes = {
        group_id: _envelope([anchor.bbox for anchor in members])
        for group_id, members in oracle_groups.items()
    }
    group_boxes = {
        group_id: _envelope([anchor.bbox for anchor in members])
        for group_id, members in provider_groups.items()
    }
    associations = {
        group_id: set(provider_to_oracle_groups.get(group_id, set()))
        | set(reverse_oracle_groups.get(group_id, set()))
        for group_id in provider_groups
    }
    nested_parent_associations: dict[str, set[str]] = defaultdict(set)
    for record in nested_graphical_content.get("records", []):
        provider_group_id = str(record["provider_group_id"])
        oracle_parent_id = str(record["oracle_parent_id"])
        associations[provider_group_id].add(oracle_parent_id)
        nested_parent_associations[provider_group_id].add(oracle_parent_id)
    pairs: list[dict[str, Any]] = []
    group_ids = sorted(group_boxes)
    for left_index, left_id in enumerate(group_ids):
        for right_id in group_ids[left_index + 1 :]:
            iou = _bbox_iou(group_boxes[left_id], group_boxes[right_id])
            if iou < 0.1:
                continue
            oracle_pairs = {
                (left_oracle, right_oracle)
                for left_oracle in associations[left_id]
                for right_oracle in associations[right_id]
            }
            expected_overlap = any(
                (
                    left_oracle != right_oracle
                    and _bbox_iou(oracle_boxes[left_oracle], oracle_boxes[right_oracle]) >= 0.1
                )
                or (
                    left_oracle == right_oracle
                    and (
                        left_oracle in nested_parent_associations[left_id]
                        or right_oracle in nested_parent_associations[right_id]
                    )
                )
                for left_oracle, right_oracle in oracle_pairs
            )
            duplicate_overlap = any(
                left_oracle == right_oracle
                and _bbox_iou(group_boxes[left_id], oracle_boxes[left_oracle]) >= 0.9
                and _bbox_iou(group_boxes[right_id], oracle_boxes[right_oracle]) >= 0.9
                for left_oracle, right_oracle in oracle_pairs
            )
            if not expected_overlap and not duplicate_overlap:
                pairs.append({"left": left_id, "right": right_id, "iou": round(iou, 8)})
    possible = len(group_ids) * (len(group_ids) - 1) // 2
    involved = {str(pair[side]) for pair in pairs for side in ("left", "right")}
    return {
        "possible_pair_count": possible,
        "overlapping_pair_count": len(pairs),
        "overlapping_pair_rate": _ratio(len(pairs), possible),
        "involved_provider_unit_count": len(involved),
        "involved_provider_unit_rate": _ratio(len(involved), len(group_ids)),
        "policy": "provider-overlap-minus-oracle-overlap-and-duplicates-v1",
        "pairs": pairs,
    }


def _duplicate_diagnostics(
    oracle_groups: Mapping[str, Sequence[_OracleAnchor]],
    provider_groups: Mapping[str, Sequence[ProviderAnchorLike]],
    reverse_oracle_groups: Mapping[str, set[str]],
) -> dict[str, Any]:
    provider_boxes = {
        group_id: _envelope([anchor.bbox for anchor in members])
        for group_id, members in provider_groups.items()
    }
    duplicated: list[dict[str, Any]] = []
    for oracle_group_id, members in sorted(oracle_groups.items()):
        oracle_box = _envelope([anchor.bbox for anchor in members])
        candidates = [
            provider_group_id
            for provider_group_id, matched_oracle_groups in reverse_oracle_groups.items()
            if oracle_group_id in matched_oracle_groups
            and _bbox_iou(oracle_box, provider_boxes[provider_group_id]) >= 0.9
        ]
        if len(candidates) >= 2:
            duplicated.append(
                {
                    "oracle_group_id": oracle_group_id,
                    "provider_group_ids": sorted(candidates),
                    "copies": len(candidates),
                }
            )
    return {
        "duplicated_oracle_unit_count": len(duplicated),
        "duplicated_oracle_unit_rate": _ratio(len(duplicated), len(oracle_groups)),
        "duplicated_oracle_units": duplicated,
    }


def _nested_graphical_content(
    providers: Sequence[ProviderAnchorLike],
    provider_best: Mapping[str, tuple[int, float]],
    oracle: Sequence[_OracleAnchor],
    *,
    provider_count: int,
) -> dict[str, Any]:
    graphical = [anchor for anchor in oracle if anchor.kind in {"figure", "table"}]
    records: list[dict[str, Any]] = []
    for provider in providers:
        if _kind_alias(provider.kind) not in {"text", "caption"}:
            continue
        best_match = provider_best.get(provider.id)
        if best_match is not None and oracle[best_match[0]].kind not in {"figure", "table"}:
            continue
        candidates: list[tuple[float, _OracleAnchor]] = []
        provider_area = _bbox_area(provider.bbox)
        for parent in graphical:
            parent_area = _bbox_area(parent.bbox)
            _, provider_coverage = _bbox_coverages(parent.bbox, provider.bbox)
            area_ratio = provider_area / parent_area if parent_area else 0.0
            if provider_coverage >= 0.9 and area_ratio <= 0.25:
                candidates.append((provider_coverage - area_ratio, parent))
        if not candidates:
            continue
        _, parent = max(candidates, key=lambda item: (item[0], item[1].id))
        records.append(
            {
                "provider_id": provider.id,
                "provider_group_id": _provider_group_id(provider),
                "provider_kind": _kind_alias(provider.kind),
                "oracle_parent_id": parent.id,
                "oracle_parent_kind": parent.kind,
                "provider_to_parent_area_ratio": round(
                    provider_area / max(_bbox_area(parent.bbox), 1e-9),
                    8,
                ),
            }
        )
    return _nested_graphical_report(records, provider_count)


def _nested_graphical_report(
    records: Sequence[Mapping[str, Any]],
    provider_denominator: int,
) -> dict[str, Any]:
    by_parent_kind = Counter(str(record["oracle_parent_kind"]) for record in records)
    by_provider_kind = Counter(str(record["provider_kind"]) for record in records)
    return {
        "policy": (
            "unmatched-provider-text-anchor-contained-90pct-in-graphical-parent-"
            "and-at-most-25pct-parent-area-v1"
        ),
        "count": len(records),
        "provider_anchor_rate": _ratio(len(records), provider_denominator),
        "by_oracle_parent_kind": dict(sorted(by_parent_kind.items())),
        "by_provider_kind": dict(sorted(by_provider_kind.items())),
        "records": list(records),
    }


def _text_fidelity_diagnostics(
    oracle_groups: Mapping[str, Sequence[_OracleAnchor]],
    provider_groups: Mapping[str, Sequence[ProviderAnchorLike]],
    oracle_to_provider_groups: Mapping[str, set[str]],
    oracle_by_provider_group: Mapping[str, Sequence[_OracleAnchor]],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for provider_group_id, oracle_members in sorted(oracle_by_provider_group.items()):
        provider_members = provider_groups.get(provider_group_id, [])
        oracle_text = _joined_text(oracle_members)
        provider_text = _joined_text(provider_members)
        if not oracle_text and not provider_text:
            continue
        precision, recall, token_f1 = _token_metrics(oracle_text, provider_text)
        records.append(
            {
                "provider_group_id": provider_group_id,
                "oracle_group_ids": sorted({anchor.group_id for anchor in oracle_members}),
                "oracle_character_count": len(oracle_text),
                "provider_character_count": len(provider_text),
                "character_similarity": round(_character_similarity(oracle_text, provider_text), 8),
                "token_precision": round(precision, 8),
                "token_recall": round(recall, 8),
                "token_f1": round(token_f1, 8),
            }
        )
    caption_records: list[dict[str, Any]] = []
    for oracle_group_id, oracle_members in sorted(oracle_groups.items()):
        oracle_text = _joined_text(oracle_members)
        prefix = _parse_caption_prefix(oracle_text)
        if prefix is None:
            continue
        provider_group_ids = sorted(oracle_to_provider_groups.get(oracle_group_id, set()))
        candidate_texts = [
            _joined_text(provider_groups.get(group_id, [])) for group_id in provider_group_ids
        ]
        provider_prefixes = [_parse_caption_prefix(text) for text in candidate_texts]
        preserved = any(
            candidate is not None and candidate[:2] == prefix[:2]
            for candidate in provider_prefixes
        )
        prefix_similarities = [
            _character_similarity(prefix[2], _normalized_text(text)[: len(prefix[2])])
            for text in candidate_texts
        ]
        explicitly_labeled = any(
            any(_kind_alias(anchor.kind) == "caption" for anchor in provider_groups.get(group_id, []))
            for group_id in provider_group_ids
        )
        caption_records.append(
            {
                "oracle_group_id": oracle_group_id,
                "oracle_prefix": prefix[2],
                "provider_group_ids": provider_group_ids,
                "matched": bool(provider_group_ids),
                "prefix_preserved": preserved,
                "prefix_similarity": round(max(prefix_similarities, default=0.0), 8),
                "explicit_caption_label": explicitly_labeled,
            }
        )
    return _text_report_from_records(records, caption_records)


def _text_report_from_records(
    records: Sequence[Mapping[str, Any]],
    caption_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    caption_count = len(caption_records)
    prefix_preserved = sum(bool(record["prefix_preserved"]) for record in caption_records)
    explicit_labels = sum(bool(record["explicit_caption_label"]) for record in caption_records)
    return {
        "matched_text_group_count": len(records),
        "character_similarity": _distribution(
            [float(record["character_similarity"]) for record in records]
        ),
        "token_precision": _distribution([float(record["token_precision"]) for record in records]),
        "token_recall": _distribution([float(record["token_recall"]) for record in records]),
        "token_f1": _distribution([float(record["token_f1"]) for record in records]),
        "records": list(records),
        "caption": {
            "oracle_caption_candidate_count": caption_count,
            "matched_caption_candidate_count": sum(
                bool(record["matched"]) for record in caption_records
            ),
            "prefix_preserved_count": prefix_preserved,
            "prefix_preservation_rate": _ratio(prefix_preserved, caption_count),
            "explicit_caption_label_count": explicit_labels,
            "explicit_caption_label_recall": _ratio(explicit_labels, caption_count),
            "prefix_similarity": _distribution(
                [float(record["prefix_similarity"]) for record in caption_records]
            ),
            "records": list(caption_records),
        },
    }


def _aggregate_type_confusion(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    for report in reports:
        for source, targets in report["type_confusion"].get("matrix", {}).items():
            matrix[str(source)].update({str(target): int(count) for target, count in targets.items()})
    matched = sum(int(report["type_confusion"]["geometry_matched_count"]) for report in reports)
    incompatible = sum(int(report["type_confusion"]["incompatible_count"]) for report in reports)
    return {
        "geometry_matched_count": matched,
        "compatible_count": matched - incompatible,
        "incompatible_count": incompatible,
        "incompatible_rate": _ratio(incompatible, matched),
        "matrix": {
            source: dict(sorted(targets.items()))
            for source, targets in sorted(matrix.items())
        },
    }


def _aggregate_segmentation(
    reports: Sequence[Mapping[str, Any]],
    reference: Mapping[str, int],
) -> dict[str, Any]:
    fragmented = [
        item
        for report in reports
        for item in report["segmentation"].get("fragmented_oracle_units", [])
    ]
    merged = [
        item
        for report in reports
        for item in report["segmentation"].get("merged_provider_units", [])
    ]
    matched_oracle = sum(
        int(report["segmentation"]["matched_oracle_unit_count"]) for report in reports
    )
    matched_provider = sum(
        int(report["segmentation"]["matched_provider_unit_count"]) for report in reports
    )
    return {
        "oracle_unit_count": reference["oracle_unit_count"],
        "matched_oracle_unit_count": matched_oracle,
        "fragmented_oracle_unit_count": len(fragmented),
        "fragmented_oracle_unit_rate": _ratio(len(fragmented), reference["oracle_unit_count"]),
        "fragmentation_excess_part_count": sum(int(item["parts"]) - 1 for item in fragmented),
        "fragment_multiplicity": _distribution([float(item["parts"]) for item in fragmented]),
        "provider_unit_count": reference["provider_unit_count"],
        "matched_provider_unit_count": matched_provider,
        "merged_provider_unit_count": len(merged),
        "merged_provider_unit_rate": _ratio(len(merged), reference["provider_unit_count"]),
        "merge_excess_source_count": sum(int(item["sources"]) - 1 for item in merged),
        "merge_multiplicity": _distribution([float(item["sources"]) for item in merged]),
        "fragmented_oracle_units": fragmented,
        "merged_provider_units": merged,
    }


def _aggregate_text_fidelity(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    records = [
        record
        for report in reports
        for record in report["text_fidelity"].get("records", [])
    ]
    caption_records = [
        record
        for report in reports
        for record in report["text_fidelity"].get("caption", {}).get("records", [])
    ]
    return _text_report_from_records(records, caption_records)


def _aggregate_unmatched_by_kind(
    reports: Sequence[Mapping[str, Any]],
    side: str,
) -> dict[str, Any]:
    kinds = sorted(
        {
            kind
            for report in reports
            for kind in report["unmatched_by_kind"].get(side, {})
        }
    )
    result: dict[str, Any] = {}
    for kind in kinds:
        total = sum(
            int(report["unmatched_by_kind"].get(side, {}).get(kind, {}).get("total", 0))
            for report in reports
        )
        unmatched = sum(
            int(report["unmatched_by_kind"].get(side, {}).get(kind, {}).get("unmatched", 0))
            for report in reports
        )
        result[kind] = {"total": total, "unmatched": unmatched, "rate": _ratio(unmatched, total)}
    return result


def _degradation_signature(report: Mapping[str, Any]) -> dict[str, float]:
    taxonomy = report["error_taxonomy"]
    geometry = report["geometry"]
    text = report["text_fidelity"]
    caption = text["caption"]
    signature = {
        "oracle_miss_rate": taxonomy["missing"]["rate"],
        "provider_hallucination_rate": taxonomy["hallucination"]["rate"],
        "type_incompatibility_rate": report["type_confusion"]["incompatible_rate"],
        "fragmented_unit_rate": report["segmentation"]["fragmented_oracle_unit_rate"],
        "merged_unit_rate": report["segmentation"]["merged_provider_unit_rate"],
        "size_error_rate": taxonomy["size_error"]["rate"],
        "provider_overlap_unit_rate": report["provider_overlap"]["involved_provider_unit_rate"],
        "center_error_p90": geometry["center_distance_ratio"]["p90"],
        "edge_error_p90": geometry["edge_mean_error_ratio"]["p90"],
        "character_error_mean": 1.0 - text["character_similarity"]["mean"],
        "token_error_mean": 1.0 - text["token_f1"]["mean"],
        "caption_prefix_loss_rate": 1.0 - caption["prefix_preservation_rate"] if caption["oracle_caption_candidate_count"] else 0.0,
    }
    return {key: round(min(1.0, max(0.0, float(value))), 8) for key, value in signature.items()}


def _signature_distance(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> float:
    squared = [(float(left[key]) - float(right[key])) ** 2 for key in _SIGNATURE_KEYS]
    return round(math.sqrt(sum(squared) / len(squared)), 8)


def _group_oracle_anchors(
    anchors: Sequence[_OracleAnchor],
) -> dict[str, list[_OracleAnchor]]:
    groups: dict[str, list[_OracleAnchor]] = defaultdict(list)
    for anchor in anchors:
        groups[anchor.group_id].append(anchor)
    return dict(groups)


def _group_provider_anchors(
    anchors: Sequence[ProviderAnchorLike],
) -> dict[str, list[ProviderAnchorLike]]:
    groups: dict[str, list[ProviderAnchorLike]] = defaultdict(list)
    for anchor in anchors:
        groups[_provider_group_id(anchor)].append(anchor)
    return dict(groups)


def _provider_group_id(anchor: ProviderAnchorLike) -> str:
    return str(anchor.group_id) if anchor.group_id is not None else str(anchor.id)


def _unmatched_by_kind(
    anchors: Sequence[Any],
    unmatched_ids: set[str],
) -> dict[str, Any]:
    totals = Counter(_kind_alias(anchor.kind) for anchor in anchors)
    unmatched = Counter(
        _kind_alias(anchor.kind) for anchor in anchors if anchor.id in unmatched_ids
    )
    return {
        kind: {
            "total": totals[kind],
            "unmatched": unmatched[kind],
            "rate": _ratio(unmatched[kind], totals[kind]),
        }
        for kind in sorted(totals)
    }


def _normalized_bbox_metrics(
    oracle: tuple[float, float, float, float],
    provider: tuple[float, float, float, float],
    *,
    width: float,
    height: float,
) -> dict[str, float]:
    oracle_center = ((oracle[0] + oracle[2]) / 2, (oracle[1] + oracle[3]) / 2)
    provider_center = ((provider[0] + provider[2]) / 2, (provider[1] + provider[3]) / 2)
    center_distance = math.hypot(
        (provider_center[0] - oracle_center[0]) / width,
        (provider_center[1] - oracle_center[1]) / height,
    ) / math.sqrt(2)
    edge_errors = (
        abs(provider[0] - oracle[0]) / width,
        abs(provider[1] - oracle[1]) / height,
        abs(provider[2] - oracle[2]) / width,
        abs(provider[3] - oracle[3]) / height,
    )
    oracle_coverage, provider_coverage = _bbox_coverages(oracle, provider)
    oracle_area = _bbox_area(oracle)
    provider_area = _bbox_area(provider)
    return {
        "center_distance_ratio": round(center_distance, 8),
        "edge_mean_error_ratio": round(fmean(edge_errors), 8),
        "edge_max_error_ratio": round(max(edge_errors), 8),
        "iou": round(_bbox_iou(oracle, provider), 8),
        "oracle_coverage": round(oracle_coverage, 8),
        "provider_coverage": round(provider_coverage, 8),
        "area_ratio": round(provider_area / oracle_area if oracle_area else 0.0, 8),
    }


def _anchor_match_score(
    oracle: tuple[float, float, float, float],
    provider: tuple[float, float, float, float],
) -> float:
    oracle_coverage, provider_coverage = _bbox_coverages(oracle, provider)
    center_x = (oracle[0] + oracle[2]) / 2
    center_y = (oracle[1] + oracle[3]) / 2
    center_score = float(
        provider[0] <= center_x <= provider[2]
        and provider[1] <= center_y <= provider[3]
    )
    return oracle_coverage * 0.65 + min(1.0, provider_coverage * 4) * 0.20 + center_score * 0.15


def _bbox_coverages(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float]:
    intersection = _bbox_intersection_area(left, right)
    return (
        intersection / max(_bbox_area(left), 1e-9),
        intersection / max(_bbox_area(right), 1e-9),
    )


def _bbox_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    intersection = _bbox_intersection_area(left, right)
    union = _bbox_area(left) + _bbox_area(right) - intersection
    return intersection / union if union > 0 else 0.0


def _bbox_intersection_area(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0,
        min(left[3], right[3]) - max(left[1], right[1]),
    )


def _bbox_area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _valid_box(box: Sequence[float]) -> bool:
    return len(box) == 4 and float(box[2]) > float(box[0]) and float(box[3]) > float(box[1])


def _envelope(
    boxes: Sequence[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _joined_text(anchors: Sequence[Any]) -> str:
    ordered = sorted(
        anchors,
        key=lambda anchor: (
            float(anchor.bbox[1]),
            float(anchor.bbox[0]),
            float(anchor.bbox[3]),
            str(anchor.id),
        ),
    )
    return _normalized_text(" ".join(str(anchor.text or "") for anchor in ordered))


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _token_metrics(reference: str, candidate: str) -> tuple[float, float, float]:
    reference_tokens = Counter(re.findall(r"\w+", reference.casefold()))
    candidate_tokens = Counter(re.findall(r"\w+", candidate.casefold()))
    overlap = sum((reference_tokens & candidate_tokens).values())
    precision = overlap / sum(candidate_tokens.values()) if candidate_tokens else 0.0
    recall = overlap / sum(reference_tokens.values()) if reference_tokens else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _character_similarity(reference: str, candidate: str) -> float:
    reference = _normalized_text(reference)
    candidate = _normalized_text(candidate)
    denominator = max(len(reference), len(candidate))
    if denominator == 0:
        return 1.0
    return max(0.0, 1.0 - _levenshtein_distance(reference, candidate) / denominator)


def _levenshtein_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _parse_caption_prefix(value: str) -> tuple[str, str, str] | None:
    normalized = _normalized_text(value)
    match = _CAPTION_PREFIX.match(normalized)
    if match is None:
        return None
    family = match.group("family").casefold().rstrip(".")
    family = {
        "fig": "figure",
        "\u56fe": "figure",
        "\u56fe\u8868": "figure",
        "\u8868": "table",
    }.get(family, family)
    marker = re.sub(r"[^\w]+", "", match.group("marker").casefold())
    return family, marker, match.group(0).strip()


def _distribution(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "mean": 0.0, "median": 0.0, "p90": 0.0, "max": 0.0}
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "mean": round(fmean(ordered), 8),
        "median": round(_percentile(ordered, 0.5), 8),
        "p90": round(_percentile(ordered, 0.9), 8),
        "max": round(ordered[-1], 8),
    }


def _percentile(values: Sequence[float], quantile: float) -> float:
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(values[lower])
    fraction = position - lower
    return float(values[lower]) * (1 - fraction) + float(values[upper]) * fraction


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


def _rounded_box(box: Sequence[float]) -> list[float]:
    return [round(float(value), 8) for value in box]


def _count_rate(count: int, denominator: int) -> dict[str, int | float]:
    return {"count": count, "denominator": denominator, "rate": _ratio(count, denominator)}


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 8) if denominator else 0.0
