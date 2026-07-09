from __future__ import annotations

import json
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from .models import DocumentIR


def semantic_ground_truth_path(pdf_path: str | Path) -> Path:
    return Path(pdf_path).with_suffix(".semantic-order.json")


def semantic_ground_truth_candidates(pdf_path: str | Path) -> list[Path]:
    source_pdf = Path(pdf_path)
    adjacent = semantic_ground_truth_path(source_pdf)
    repo_sidecar_names = _repo_sidecar_names(source_pdf)
    cwd_sidecar_dir = Path.cwd() / "benchmarks" / "semantic-ground-truth"
    source_sidecar_dir = Path(__file__).resolve().parents[2] / "benchmarks" / "semantic-ground-truth"
    candidates: list[Path] = []
    for path in (
        adjacent,
        *(cwd_sidecar_dir / name for name in repo_sidecar_names),
        *(source_sidecar_dir / name for name in repo_sidecar_names),
    ):
        if path not in candidates:
            candidates.append(path)
    return candidates


def _repo_sidecar_names(pdf_path: Path) -> list[str]:
    base_name = semantic_ground_truth_path(pdf_path).name
    parent_name = pdf_path.parent.name.strip()
    names = [base_name]
    if parent_name:
        names.append(f"{parent_name}.{base_name}")
    return names


def compare_semantic_reading_order(
    document: DocumentIR,
    source_pdf: str | Path,
    out_dir: str | Path,
    candidate_orders: dict[str, dict[int, list[str]]] | None = None,
) -> dict[str, Any]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    candidates = semantic_ground_truth_candidates(source_pdf)
    ground_truth_path = next((path for path in candidates if path.exists()), candidates[0])

    if not ground_truth_path.exists():
        report = {
            "ground_truth_available": False,
            "ground_truth": str(ground_truth_path),
            "ground_truth_candidates": [str(path) for path in candidates],
            "pages": [],
        }
        (target / "semantic_quality_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    page_reports = [
        _compare_page(document, page_truth, candidate_orders or {})
        for page_truth in ground_truth.get("pages", [])
        if isinstance(page_truth, dict) and _page_truth_in_document(document, page_truth)
    ]
    report = {
        "ground_truth_available": True,
        "ground_truth": str(ground_truth_path),
        "version": ground_truth.get("version", 1),
        "pages": page_reports,
    }
    report.update(_summarize_pages(page_reports))
    report.update(_summarize_candidate_orders(page_reports))
    (target / "semantic_quality_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _page_truth_in_document(document: DocumentIR, page_truth: dict[str, Any]) -> bool:
    try:
        page_index = int(page_truth.get("page_index", 0))
    except (TypeError, ValueError):
        return False
    return _document_page_by_index(document, page_index) is not None


def _compare_page(
    document: DocumentIR,
    page_truth: dict[str, Any],
    candidate_orders: dict[str, dict[int, list[str]]],
) -> dict[str, Any]:
    truth_page_index = int(page_truth.get("page_index", 0))
    expected = [str(text).strip() for text in page_truth.get("text_sequence", []) if str(text).strip()]
    relation_edges = _page_relation_edges(page_truth)
    reading_streams = _page_reading_streams(page_truth)
    has_relation_edges = bool(relation_edges["successor_edges"] or relation_edges["precedence_edges"])
    has_stream_labels = bool(reading_streams)
    match_mode = str(
        page_truth.get(
            "match_mode",
            "ordered-subsequence" if (has_relation_edges or has_stream_labels) and not expected else "full-sequence",
        )
    )
    page = _document_page_by_index(document, truth_page_index)
    if page is None:
        raise ValueError(f"Page {truth_page_index} is not present in document")
    actual_elements = [
        element
        for element in sorted(page.elements, key=lambda item: (item.reading_order, item.bbox_pdf.y0, item.bbox_pdf.x0))
        if element.source_text.strip()
    ]

    page_report = {
        "page_index": page.page_index,
        "truth_page_index": truth_page_index,
        "match_mode": match_mode,
    }
    page_report.update(
        _sequence_quality(
            expected,
            actual_elements,
            match_mode,
            page.height_pt,
            include_sequences=True,
            include_ignored_texts=True,
            ignored_label_texts=_merged_label_texts(expected, relation_edges, reading_streams),
        )
    )
    page_report.update(_relation_quality(actual_elements, relation_edges))
    page_report.update(_stream_quality(actual_elements, reading_streams))

    candidate_reports: dict[str, Any] = {}
    for candidate_name, page_orders in sorted(candidate_orders.items()):
        ordered_ids = page_orders.get(page.page_index)
        if not ordered_ids:
            continue
        candidate_elements = _candidate_ordered_elements(actual_elements, ordered_ids)
        candidate_reports[candidate_name] = _sequence_quality(
            expected,
            candidate_elements,
            match_mode,
            page.height_pt,
            include_sequences=False,
            include_ignored_texts=False,
        )
        candidate_reports[candidate_name].update(_relation_quality(candidate_elements, relation_edges))
        candidate_reports[candidate_name].update(_stream_quality(candidate_elements, reading_streams))
    if candidate_reports:
        page_report["candidate_orders"] = candidate_reports

    return page_report


def _document_page_by_index(document: DocumentIR, page_index: int) -> Any:
    for page in document.pages:
        if page.page_index == page_index:
            return page
    if _document_uses_positional_page_indices(document) and 0 <= page_index < len(document.pages):
        return document.pages[page_index]
    return None


def _document_uses_positional_page_indices(document: DocumentIR) -> bool:
    return all(page.page_index == index for index, page in enumerate(document.pages))


def _page_relation_edges(page_truth: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    return {
        "successor_edges": _relation_edges_from_any(
            _combined_relation_values(
                page_truth.get("successor_edges"),
                page_truth.get("successor_relations"),
            )
        ),
        "precedence_edges": _relation_edges_from_any(
            _combined_relation_values(
                page_truth.get("precedence_edges"),
                page_truth.get("order_edges"),
            )
        ),
    }


def _page_reading_streams(page_truth: dict[str, Any]) -> list[dict[str, Any]]:
    streams: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_stream in _combined_relation_values(
        page_truth.get("reading_streams"),
        page_truth.get("streams"),
    ):
        if not isinstance(raw_stream, dict):
            continue
        stream_id = str(raw_stream.get("stream_id", raw_stream.get("id", "")) or "").strip()
        stream_type = str(raw_stream.get("stream_type", raw_stream.get("type", "")) or "").strip()
        if not stream_id:
            stream_id = stream_type or f"stream-{len(streams) + 1}"
        if not stream_type:
            stream_type = "unknown"
        key = (stream_id, stream_type)
        if key in seen:
            continue
        seen.add(key)

        sequence = _texts_from_any(
            raw_stream.get(
                "text_sequence",
                raw_stream.get("sequence", raw_stream.get("texts", [])),
            )
        )
        successor_edges = _dedupe_edges(
            [
                *_adjacent_edges(sequence),
                *_relation_edges_from_any(
                    _combined_relation_values(
                        raw_stream.get("successor_edges"),
                        raw_stream.get("successor_relations"),
                    )
                ),
            ]
        )
        precedence_edges = _dedupe_edges(
            [
                *_ordered_pairs(sequence),
                *_relation_edges_from_any(
                    _combined_relation_values(
                        raw_stream.get("precedence_edges"),
                        raw_stream.get("order_edges"),
                    )
                ),
            ]
        )
        labels = _dedupe_texts(
            [
                *sequence,
                *(text for edge in [*successor_edges, *precedence_edges] for text in edge),
            ]
        )
        if not labels:
            continue
        streams.append(
            {
                "stream_id": stream_id,
                "stream_type": stream_type,
                "labels": labels,
                "successor_edges": successor_edges,
                "precedence_edges": precedence_edges,
            }
        )
    return streams


def _combined_relation_values(*values: Any) -> list[Any]:
    combined: list[Any] = []
    for value in values:
        if isinstance(value, list):
            combined.extend(value)
    return combined


def _texts_from_any(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedupe_texts([str(text).strip() for text in value if str(text).strip()])


def _relation_edges_from_any(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        source: Any = None
        target: Any = None
        if isinstance(item, dict):
            source = item.get("source", item.get("from"))
            target = item.get("target", item.get("to"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            source = item[0]
            target = item[1]
        source_text = str(source or "").strip()
        target_text = str(target or "").strip()
        edge = (source_text, target_text)
        if not source_text or not target_text or source_text == target_text or edge in seen:
            continue
        edges.append(edge)
        seen.add(edge)
    return edges


def _dedupe_edges(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    unique_edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source, target in edges:
        edge = (str(source).strip(), str(target).strip())
        if not edge[0] or not edge[1] or edge[0] == edge[1] or edge in seen:
            continue
        unique_edges.append(edge)
        seen.add(edge)
    return unique_edges


def _adjacent_edges(sequence: list[str]) -> list[tuple[str, str]]:
    return [(sequence[index], sequence[index + 1]) for index in range(max(0, len(sequence) - 1))]


def _ordered_pairs(sequence: list[str]) -> list[tuple[str, str]]:
    return [
        (sequence[left], sequence[right])
        for left in range(len(sequence))
        for right in range(left + 1, len(sequence))
    ]


def _dedupe_texts(texts: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for text in texts:
        normalized = str(text).strip()
        if not normalized or normalized in seen:
            continue
        unique.append(normalized)
        seen.add(normalized)
    return unique


def _merged_label_texts(
    expected: list[str],
    relation_edges: dict[str, list[tuple[str, str]]],
    reading_streams: list[dict[str, Any]],
) -> list[str]:
    labels = list(expected)
    seen = set(labels)
    for edge in [*relation_edges["successor_edges"], *relation_edges["precedence_edges"]]:
        for text in edge:
            if text not in seen:
                labels.append(text)
                seen.add(text)
    for stream in reading_streams:
        for text in stream.get("labels", []):
            if text not in seen:
                labels.append(str(text))
                seen.add(str(text))
    return labels


def _relation_quality(
    actual_elements: list[Any],
    relation_edges: dict[str, list[tuple[str, str]]],
) -> dict[str, Any]:
    successor_edges = relation_edges["successor_edges"]
    precedence_edges = relation_edges["precedence_edges"]
    positions = _first_text_positions([element.source_text.strip() for element in actual_elements])

    successor_correct, successor_missing = _relation_successor_counts(successor_edges, positions)
    precedence_correct, precedence_missing = _relation_precedence_counts(precedence_edges, positions)
    missing_labels = successor_missing | precedence_missing
    return {
        "relation_successor_correct_count": successor_correct,
        "relation_successor_total_count": len(successor_edges),
        "relation_successor_accuracy": _optional_ratio(successor_correct, len(successor_edges)),
        "relation_precedence_correct_count": precedence_correct,
        "relation_precedence_total_count": len(precedence_edges),
        "relation_precedence_accuracy": _optional_ratio(precedence_correct, len(precedence_edges)),
        "relation_missing_text_count": len(missing_labels),
        "relation_missing_texts": sorted(missing_labels),
    }


def _stream_quality(
    actual_elements: list[Any],
    reading_streams: list[dict[str, Any]],
) -> dict[str, Any]:
    positions = _first_text_positions([element.source_text.strip() for element in actual_elements])
    stream_reports: list[dict[str, Any]] = []
    successor_correct_total = 0
    successor_edge_total = 0
    precedence_correct_total = 0
    precedence_edge_total = 0
    missing_labels: set[str] = set()
    for stream in reading_streams:
        labels = [str(text).strip() for text in stream.get("labels", []) if str(text).strip()]
        successor_edges = list(stream.get("successor_edges", []))
        precedence_edges = list(stream.get("precedence_edges", []))
        successor_correct, successor_missing = _stream_successor_counts(
            successor_edges,
            positions,
            labels,
        )
        precedence_correct, precedence_missing = _relation_precedence_counts(precedence_edges, positions)
        successor_correct_total += successor_correct
        successor_edge_total += len(successor_edges)
        precedence_correct_total += precedence_correct
        precedence_edge_total += len(precedence_edges)
        missing_labels.update(successor_missing)
        missing_labels.update(precedence_missing)
        missing_labels.update(text for text in labels if text not in positions)
        stream_reports.append(
            {
                "stream_id": stream.get("stream_id"),
                "stream_type": stream.get("stream_type"),
                "label_count": len(labels),
                "successor_correct_count": successor_correct,
                "successor_total_count": len(successor_edges),
                "successor_accuracy": _optional_ratio(successor_correct, len(successor_edges)),
                "precedence_correct_count": precedence_correct,
                "precedence_total_count": len(precedence_edges),
                "precedence_accuracy": _optional_ratio(precedence_correct, len(precedence_edges)),
                "missing_text_count": len(successor_missing | precedence_missing | {text for text in labels if text not in positions}),
            }
        )

    return {
        "stream_count": len(reading_streams),
        "stream_successor_correct_count": successor_correct_total,
        "stream_successor_total_count": successor_edge_total,
        "stream_successor_accuracy": _optional_ratio(successor_correct_total, successor_edge_total),
        "stream_precedence_correct_count": precedence_correct_total,
        "stream_precedence_total_count": precedence_edge_total,
        "stream_precedence_accuracy": _optional_ratio(precedence_correct_total, precedence_edge_total),
        "stream_missing_text_count": len(missing_labels),
        "stream_missing_texts": sorted(missing_labels),
        "reading_streams": stream_reports,
    }


def _stream_successor_counts(
    edges: list[tuple[str, str]],
    positions: dict[str, int],
    stream_labels: list[str],
) -> tuple[int, set[str]]:
    if not edges:
        return 0, set()
    stream_label_set = set(stream_labels)
    labelled_positions = {
        positions[text]
        for text in stream_label_set
        if text in positions
    }
    correct = 0
    missing: set[str] = set()
    for source, target in edges:
        source_position = positions.get(source)
        target_position = positions.get(target)
        if source_position is None:
            missing.add(source)
        if target_position is None:
            missing.add(target)
        if source_position is None or target_position is None or source_position >= target_position:
            continue
        if any(source_position < other_position < target_position for other_position in labelled_positions):
            continue
        correct += 1
    return correct, missing


def _first_text_positions(actual: list[str]) -> dict[str, int]:
    positions: dict[str, int] = {}
    for index, text in enumerate(actual):
        if text and text not in positions:
            positions[text] = index
    return positions


def _relation_successor_counts(
    edges: list[tuple[str, str]],
    positions: dict[str, int],
) -> tuple[int, set[str]]:
    if not edges:
        return 0, set()
    labelled_positions = {
        positions[text]
        for edge in edges
        for text in edge
        if text in positions
    }
    correct = 0
    missing: set[str] = set()
    for source, target in edges:
        source_position = positions.get(source)
        target_position = positions.get(target)
        if source_position is None:
            missing.add(source)
        if target_position is None:
            missing.add(target)
        if source_position is None or target_position is None or source_position >= target_position:
            continue
        if any(source_position < other_position < target_position for other_position in labelled_positions):
            continue
        correct += 1
    return correct, missing


def _relation_precedence_counts(
    edges: list[tuple[str, str]],
    positions: dict[str, int],
) -> tuple[int, set[str]]:
    correct = 0
    missing: set[str] = set()
    for source, target in edges:
        source_position = positions.get(source)
        target_position = positions.get(target)
        if source_position is None:
            missing.add(source)
        if target_position is None:
            missing.add(target)
        if source_position is not None and target_position is not None and source_position < target_position:
            correct += 1
    return correct, missing


def _sequence_quality(
    expected: list[str],
    actual_elements: list[Any],
    match_mode: str,
    page_height: float,
    *,
    include_sequences: bool,
    include_ignored_texts: bool,
    ignored_label_texts: list[str] | None = None,
) -> dict[str, Any]:
    actual = [element.source_text.strip() for element in actual_elements]
    matched_positions = _matched_positions(expected, actual)
    ignored_label_positions = _matched_positions(ignored_label_texts or expected, actual)
    matched_count = sum(1 for position in matched_positions if position is not None)
    missing_texts = [text for text, position in zip(expected, matched_positions) if position is None]
    extra_texts = [] if match_mode == "ordered-subsequence" else _extra_texts(expected, actual)
    ignored_texts = (
        _ignored_text_entries(actual_elements, ignored_label_positions, page_height)
        if match_mode == "ordered-subsequence"
        else []
    )
    ignored_zone_counts = Counter(str(item["zone"]) for item in ignored_texts)
    ignored_role_counts = Counter(str(item["role"]) for item in ignored_texts)
    ignored_source_counts = Counter(str(item["source"]) for item in ignored_texts)

    correct_pairs, total_pairs = _pairwise_order_counts(matched_positions)
    successor_correct, successor_total = _successor_order_counts(matched_positions)
    if match_mode == "ordered-subsequence":
        edit_distance = len(expected) - matched_count
        denominator = max(len(expected), 1)
        exact_match = matched_count == len(expected) and correct_pairs == total_pairs
    else:
        edit_distance = _levenshtein_distance(expected, actual)
        denominator = max(len(expected), len(actual), 1)
        exact_match = expected == actual

    report = {
        "expected_text_count": len(expected),
        "actual_text_count": len(actual),
        "matched_text_count": matched_count,
        "ignored_text_count": len(ignored_texts) if match_mode == "ordered-subsequence" else 0,
        "ignored_text_zone_counts": dict(sorted(ignored_zone_counts.items())),
        "ignored_text_role_counts": dict(sorted(ignored_role_counts.items())),
        "ignored_text_source_counts": dict(sorted(ignored_source_counts.items())),
        "missing_text_count": len(missing_texts),
        "extra_text_count": len(extra_texts),
        "exact_match": exact_match,
        "sequence_edit_distance": edit_distance,
        "sequence_similarity": _round_ratio(1.0 - edit_distance / denominator),
        "pairwise_correct_count": correct_pairs,
        "pairwise_total_count": total_pairs,
        "pairwise_order_accuracy": _round_ratio(correct_pairs / total_pairs if total_pairs else 1.0),
        "successor_correct_count": successor_correct,
        "successor_total_count": successor_total,
        "successor_order_accuracy": _round_ratio(successor_correct / successor_total if successor_total else 1.0),
    }
    if include_ignored_texts:
        report["ignored_texts"] = ignored_texts
        report["missing_texts"] = missing_texts
        report["extra_texts"] = extra_texts
    if include_sequences:
        report["expected_sequence"] = expected
        report["actual_sequence"] = actual
    return report


def _candidate_ordered_elements(actual_elements: list[Any], ordered_ids: list[str]) -> list[Any]:
    by_id = {str(element.id): element for element in actual_elements}
    ordered: list[Any] = []
    seen: set[str] = set()
    for element_id in ordered_ids:
        normalized_id = str(element_id)
        element = by_id.get(normalized_id)
        if element is None or normalized_id in seen:
            continue
        ordered.append(element)
        seen.add(normalized_id)
    ordered.extend(element for element in actual_elements if str(element.id) not in seen)
    return ordered


def _ignored_text_entries(
    actual_elements: list[Any],
    matched_positions: list[int | None],
    page_height: float,
) -> list[dict[str, Any]]:
    matched_actual_positions = {position for position in matched_positions if position is not None}
    ignored: list[dict[str, Any]] = []
    for actual_index, element in enumerate(actual_elements):
        if actual_index in matched_actual_positions:
            continue
        ignored.append(
            {
                "text": element.source_text.strip(),
                "reading_order": element.reading_order,
                "bbox_pdf": element.bbox_pdf.as_list(),
                "zone": _page_zone(element.bbox_pdf, page_height),
                "role": _element_role(element),
                "source": _element_source(element),
            }
        )
    return ignored


def _page_zone(bbox: Any, page_height: float) -> str:
    height = max(float(page_height), 1.0)
    y0_ratio = float(bbox.y0) / height
    y1_ratio = float(bbox.y1) / height
    if y1_ratio <= 0.1:
        return "header"
    if y0_ratio >= 0.92:
        return "footer"
    if y0_ratio >= 0.72:
        return "bottom"
    return "body"


def _element_role(element: Any) -> str:
    annotation = element.metadata.get("annotation")
    if isinstance(annotation, dict) and annotation.get("role"):
        return str(annotation["role"])
    if element.metadata.get("role"):
        return str(element.metadata["role"])
    return str(element.type or "unknown")


def _element_source(element: Any) -> str:
    annotation = element.metadata.get("annotation")
    if isinstance(annotation, dict) and annotation.get("source_kind"):
        return str(annotation["source_kind"])
    if element.metadata.get("source"):
        return str(element.metadata["source"])
    return "unknown"


def _matched_positions(expected: list[str], actual: list[str]) -> list[int | None]:
    positions: dict[str, deque[int]] = defaultdict(deque)
    for index, text in enumerate(actual):
        positions[text].append(index)

    matched: list[int | None] = []
    for text in expected:
        matched.append(positions[text].popleft() if positions[text] else None)
    return matched


def _extra_texts(expected: list[str], actual: list[str]) -> list[str]:
    remaining: dict[str, int] = defaultdict(int)
    for text in expected:
        remaining[text] += 1

    extra: list[str] = []
    for text in actual:
        if remaining[text] > 0:
            remaining[text] -= 1
        else:
            extra.append(text)
    return extra


def _pairwise_order_counts(positions: list[int | None]) -> tuple[int, int]:
    total = len(positions) * (len(positions) - 1) // 2
    correct = 0
    for left in range(len(positions)):
        for right in range(left + 1, len(positions)):
            left_position = positions[left]
            right_position = positions[right]
            if left_position is not None and right_position is not None and left_position < right_position:
                correct += 1
    return correct, total


def _successor_order_counts(positions: list[int | None]) -> tuple[int, int]:
    total = max(0, len(positions) - 1)
    correct = 0
    for left in range(total):
        right = left + 1
        left_position = positions[left]
        right_position = positions[right]
        if left_position is None or right_position is None or left_position >= right_position:
            continue
        if any(
            other_position is not None and left_position < other_position < right_position
            for index, other_position in enumerate(positions)
            if index not in {left, right}
        ):
            continue
        correct += 1
    return correct, total


def _levenshtein_distance(expected: list[str], actual: list[str]) -> int:
    if not expected:
        return len(actual)
    if not actual:
        return len(expected)

    previous = list(range(len(actual) + 1))
    for row_index, expected_text in enumerate(expected, start=1):
        current = [row_index]
        for column_index, actual_text in enumerate(actual, start=1):
            cost = 0 if expected_text == actual_text else 1
            current.append(
                min(
                    previous[column_index] + 1,
                    current[column_index - 1] + 1,
                    previous[column_index - 1] + cost,
                )
            )
        previous = current
    return previous[-1]


def _summarize_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    expected_count = sum(int(page["expected_text_count"]) for page in pages)
    actual_count = sum(int(page["actual_text_count"]) for page in pages)
    matched_count = sum(int(page["matched_text_count"]) for page in pages)
    edit_distance = sum(int(page["sequence_edit_distance"]) for page in pages)
    edit_denominator = max(expected_count, actual_count, 1)
    pairwise_correct = sum(int(page["pairwise_correct_count"]) for page in pages)
    pairwise_total = sum(int(page["pairwise_total_count"]) for page in pages)
    successor_correct = sum(int(page["successor_correct_count"]) for page in pages)
    successor_total = sum(int(page["successor_total_count"]) for page in pages)
    relation_successor_correct = sum(int(page["relation_successor_correct_count"]) for page in pages)
    relation_successor_total = sum(int(page["relation_successor_total_count"]) for page in pages)
    relation_precedence_correct = sum(int(page["relation_precedence_correct_count"]) for page in pages)
    relation_precedence_total = sum(int(page["relation_precedence_total_count"]) for page in pages)
    stream_successor_correct = sum(int(page["stream_successor_correct_count"]) for page in pages)
    stream_successor_total = sum(int(page["stream_successor_total_count"]) for page in pages)
    stream_precedence_correct = sum(int(page["stream_precedence_correct_count"]) for page in pages)
    stream_precedence_total = sum(int(page["stream_precedence_total_count"]) for page in pages)
    relation_missing_texts = sorted(
        {
            str(text)
            for page in pages
            for text in page.get("relation_missing_texts", [])
        }
    )
    stream_missing_texts = sorted(
        {
            str(text)
            for page in pages
            for text in page.get("stream_missing_texts", [])
        }
    )
    return {
        "semantic_page_count": len(pages),
        "semantic_expected_text_count": expected_count,
        "semantic_actual_text_count": actual_count,
        "semantic_matched_text_count": matched_count,
        "semantic_ignored_text_count": sum(int(page["ignored_text_count"]) for page in pages),
        "semantic_ignored_text_zone_counts": _sum_page_count_dicts(pages, "ignored_text_zone_counts"),
        "semantic_ignored_text_role_counts": _sum_page_count_dicts(pages, "ignored_text_role_counts"),
        "semantic_ignored_text_source_counts": _sum_page_count_dicts(pages, "ignored_text_source_counts"),
        "semantic_missing_text_count": sum(int(page["missing_text_count"]) for page in pages),
        "semantic_extra_text_count": sum(int(page["extra_text_count"]) for page in pages),
        "semantic_sequence_edit_distance": edit_distance,
        "semantic_sequence_similarity": _round_ratio(1.0 - edit_distance / edit_denominator),
        "semantic_pairwise_correct_count": pairwise_correct,
        "semantic_pairwise_total_count": pairwise_total,
        "semantic_order_pair_accuracy": _round_ratio(pairwise_correct / pairwise_total if pairwise_total else 1.0),
        "semantic_successor_correct_count": successor_correct,
        "semantic_successor_total_count": successor_total,
        "semantic_successor_accuracy": _round_ratio(
            successor_correct / successor_total if successor_total else 1.0
        ),
        "semantic_relation_successor_correct_count": relation_successor_correct,
        "semantic_relation_successor_total_count": relation_successor_total,
        "semantic_relation_successor_accuracy": _optional_ratio(
            relation_successor_correct,
            relation_successor_total,
        ),
        "semantic_relation_precedence_correct_count": relation_precedence_correct,
        "semantic_relation_precedence_total_count": relation_precedence_total,
        "semantic_relation_precedence_accuracy": _optional_ratio(
            relation_precedence_correct,
            relation_precedence_total,
        ),
        "semantic_relation_missing_text_count": len(relation_missing_texts),
        "semantic_relation_missing_texts": relation_missing_texts,
        "semantic_stream_count": sum(int(page["stream_count"]) for page in pages),
        "semantic_stream_successor_correct_count": stream_successor_correct,
        "semantic_stream_successor_total_count": stream_successor_total,
        "semantic_stream_successor_accuracy": _optional_ratio(stream_successor_correct, stream_successor_total),
        "semantic_stream_precedence_correct_count": stream_precedence_correct,
        "semantic_stream_precedence_total_count": stream_precedence_total,
        "semantic_stream_precedence_accuracy": _optional_ratio(stream_precedence_correct, stream_precedence_total),
        "semantic_stream_missing_text_count": len(stream_missing_texts),
        "semantic_stream_missing_texts": stream_missing_texts,
        "semantic_exact_page_match_rate": _round_ratio(
            sum(1 for page in pages if bool(page["exact_match"])) / len(pages) if pages else 0.0
        ),
    }


def _summarize_candidate_orders(pages: list[dict[str, Any]]) -> dict[str, Any]:
    names = sorted(
        {
            str(candidate_name)
            for page in pages
            for candidate_name in (page.get("candidate_orders") or {})
        }
    )
    metrics: dict[str, dict[str, Any]] = {}
    for name in names:
        page_metrics = [
            page["candidate_orders"][name]
            for page in pages
            if isinstance(page.get("candidate_orders"), dict) and name in page["candidate_orders"]
        ]
        if not page_metrics:
            continue
        expected_count = sum(int(page["expected_text_count"]) for page in page_metrics)
        actual_count = sum(int(page["actual_text_count"]) for page in page_metrics)
        edit_distance = sum(int(page["sequence_edit_distance"]) for page in page_metrics)
        pairwise_correct = sum(int(page["pairwise_correct_count"]) for page in page_metrics)
        pairwise_total = sum(int(page["pairwise_total_count"]) for page in page_metrics)
        successor_correct = sum(int(page["successor_correct_count"]) for page in page_metrics)
        successor_total = sum(int(page["successor_total_count"]) for page in page_metrics)
        relation_successor_correct = sum(int(page["relation_successor_correct_count"]) for page in page_metrics)
        relation_successor_total = sum(int(page["relation_successor_total_count"]) for page in page_metrics)
        relation_precedence_correct = sum(int(page["relation_precedence_correct_count"]) for page in page_metrics)
        relation_precedence_total = sum(int(page["relation_precedence_total_count"]) for page in page_metrics)
        stream_successor_correct = sum(int(page["stream_successor_correct_count"]) for page in page_metrics)
        stream_successor_total = sum(int(page["stream_successor_total_count"]) for page in page_metrics)
        stream_precedence_correct = sum(int(page["stream_precedence_correct_count"]) for page in page_metrics)
        stream_precedence_total = sum(int(page["stream_precedence_total_count"]) for page in page_metrics)
        metrics[name] = {
            "semantic_page_count": len(page_metrics),
            "semantic_expected_text_count": expected_count,
            "semantic_actual_text_count": actual_count,
            "semantic_matched_text_count": sum(int(page["matched_text_count"]) for page in page_metrics),
            "semantic_sequence_edit_distance": edit_distance,
            "semantic_sequence_similarity": _round_ratio(
                1.0 - edit_distance / max(expected_count, actual_count, 1)
            ),
            "semantic_pairwise_correct_count": pairwise_correct,
            "semantic_pairwise_total_count": pairwise_total,
            "semantic_order_pair_accuracy": _round_ratio(
                pairwise_correct / pairwise_total if pairwise_total else 1.0
            ),
            "semantic_successor_correct_count": successor_correct,
            "semantic_successor_total_count": successor_total,
            "semantic_successor_accuracy": _round_ratio(
                successor_correct / successor_total if successor_total else 1.0
            ),
            "semantic_relation_successor_correct_count": relation_successor_correct,
            "semantic_relation_successor_total_count": relation_successor_total,
            "semantic_relation_successor_accuracy": _optional_ratio(
                relation_successor_correct,
                relation_successor_total,
            ),
            "semantic_relation_precedence_correct_count": relation_precedence_correct,
            "semantic_relation_precedence_total_count": relation_precedence_total,
            "semantic_relation_precedence_accuracy": _optional_ratio(
                relation_precedence_correct,
                relation_precedence_total,
            ),
            "semantic_stream_count": sum(int(page["stream_count"]) for page in page_metrics),
            "semantic_stream_successor_correct_count": stream_successor_correct,
            "semantic_stream_successor_total_count": stream_successor_total,
            "semantic_stream_successor_accuracy": _optional_ratio(
                stream_successor_correct,
                stream_successor_total,
            ),
            "semantic_stream_precedence_correct_count": stream_precedence_correct,
            "semantic_stream_precedence_total_count": stream_precedence_total,
            "semantic_stream_precedence_accuracy": _optional_ratio(
                stream_precedence_correct,
                stream_precedence_total,
            ),
            "semantic_exact_page_match_rate": _round_ratio(
                sum(1 for page in page_metrics if bool(page["exact_match"])) / len(page_metrics)
            ),
        }
    if not metrics:
        return {"semantic_candidate_order_metrics": {}}

    best_name, best_metrics = max(
        metrics.items(),
        key=lambda item: (
            float(item[1]["semantic_successor_accuracy"]),
            float(item[1]["semantic_order_pair_accuracy"]),
            str(item[0]),
        ),
    )
    return {
        "semantic_candidate_order_metrics": metrics,
        "semantic_best_candidate_by_successor": best_name,
        "semantic_best_candidate_successor_accuracy": best_metrics["semantic_successor_accuracy"],
        **_best_relation_candidate(metrics),
        **_best_stream_candidate(metrics),
    }


def _best_relation_candidate(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    valid_candidates: list[tuple[str, float, float, float]] = []
    for candidate_name, candidate_metrics in metrics.items():
        if int(candidate_metrics.get("semantic_relation_successor_total_count") or 0) <= 0:
            continue
        relation_successor_accuracy = candidate_metrics.get("semantic_relation_successor_accuracy")
        if relation_successor_accuracy is None:
            continue
        relation_precedence_accuracy = candidate_metrics.get("semantic_relation_precedence_accuracy")
        valid_candidates.append(
            (
                str(candidate_name),
                float(relation_successor_accuracy),
                float(relation_precedence_accuracy if relation_precedence_accuracy is not None else 0.0),
                float(candidate_metrics["semantic_successor_accuracy"]),
            )
        )
    if not valid_candidates:
        return {
            "semantic_best_candidate_by_relation_successor": None,
            "semantic_best_candidate_relation_successor_accuracy": None,
        }

    best_name, relation_successor, _relation_precedence, _sequence_successor = max(
        valid_candidates,
        key=lambda item: (item[1], item[2], item[3], item[0]),
    )
    return {
        "semantic_best_candidate_by_relation_successor": best_name,
        "semantic_best_candidate_relation_successor_accuracy": relation_successor,
    }


def _best_stream_candidate(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    valid_candidates: list[tuple[str, float, float, float]] = []
    for candidate_name, candidate_metrics in metrics.items():
        if int(candidate_metrics.get("semantic_stream_successor_total_count") or 0) <= 0:
            continue
        stream_successor_accuracy = candidate_metrics.get("semantic_stream_successor_accuracy")
        if stream_successor_accuracy is None:
            continue
        stream_precedence_accuracy = candidate_metrics.get("semantic_stream_precedence_accuracy")
        valid_candidates.append(
            (
                str(candidate_name),
                float(stream_successor_accuracy),
                float(stream_precedence_accuracy if stream_precedence_accuracy is not None else 0.0),
                float(candidate_metrics["semantic_successor_accuracy"]),
            )
        )
    if not valid_candidates:
        return {
            "semantic_best_candidate_by_stream_successor": None,
            "semantic_best_candidate_stream_successor_accuracy": None,
        }

    best_name, stream_successor, _stream_precedence, _sequence_successor = max(
        valid_candidates,
        key=lambda item: (item[1], item[2], item[3], item[0]),
    )
    return {
        "semantic_best_candidate_by_stream_successor": best_name,
        "semantic_best_candidate_stream_successor_accuracy": stream_successor,
    }


def _sum_page_count_dicts(pages: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for page in pages:
        value = page.get(key)
        if isinstance(value, dict):
            counts.update({str(item_key): int(item_value) for item_key, item_value in value.items()})
    return dict(sorted(counts.items()))


def _round_ratio(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 8)


def _optional_ratio(correct: int, total: int) -> float | None:
    if total <= 0:
        return None
    return _round_ratio(correct / total)
