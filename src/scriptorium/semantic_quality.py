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
        _compare_page(document, page_truth)
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
    (target / "semantic_quality_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _page_truth_in_document(document: DocumentIR, page_truth: dict[str, Any]) -> bool:
    try:
        page_index = int(page_truth.get("page_index", 0))
    except (TypeError, ValueError):
        return False
    return 0 <= page_index < len(document.pages)


def _compare_page(document: DocumentIR, page_truth: dict[str, Any]) -> dict[str, Any]:
    page_index = int(page_truth.get("page_index", 0))
    match_mode = str(page_truth.get("match_mode", "full-sequence"))
    expected = [str(text).strip() for text in page_truth.get("text_sequence", []) if str(text).strip()]
    page = document.pages[page_index]
    actual_elements = [
        element
        for element in sorted(page.elements, key=lambda item: (item.reading_order, item.bbox_pdf.y0, item.bbox_pdf.x0))
        if element.source_text.strip()
    ]
    actual = [element.source_text.strip() for element in actual_elements]

    matched_positions = _matched_positions(expected, actual)
    matched_count = sum(1 for position in matched_positions if position is not None)
    missing_texts = [text for text, position in zip(expected, matched_positions) if position is None]
    extra_texts = [] if match_mode == "ordered-subsequence" else _extra_texts(expected, actual)
    ignored_texts = _ignored_text_entries(actual_elements, matched_positions, page.height_pt) if match_mode == "ordered-subsequence" else []
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
    return {
        "page_index": page_index,
        "match_mode": match_mode,
        "expected_text_count": len(expected),
        "actual_text_count": len(actual),
        "matched_text_count": matched_count,
        "ignored_text_count": max(0, len(actual) - matched_count) if match_mode == "ordered-subsequence" else 0,
        "ignored_texts": ignored_texts,
        "ignored_text_zone_counts": dict(sorted(ignored_zone_counts.items())),
        "ignored_text_role_counts": dict(sorted(ignored_role_counts.items())),
        "ignored_text_source_counts": dict(sorted(ignored_source_counts.items())),
        "missing_text_count": len(missing_texts),
        "extra_text_count": len(extra_texts),
        "missing_texts": missing_texts,
        "extra_texts": extra_texts,
        "exact_match": exact_match,
        "sequence_edit_distance": edit_distance,
        "sequence_similarity": _round_ratio(1.0 - edit_distance / denominator),
        "pairwise_correct_count": correct_pairs,
        "pairwise_total_count": total_pairs,
        "pairwise_order_accuracy": _round_ratio(correct_pairs / total_pairs if total_pairs else 1.0),
        "successor_correct_count": successor_correct,
        "successor_total_count": successor_total,
        "successor_order_accuracy": _round_ratio(
            successor_correct / successor_total if successor_total else 1.0
        ),
        "expected_sequence": expected,
        "actual_sequence": actual,
    }


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
        "semantic_exact_page_match_rate": _round_ratio(
            sum(1 for page in pages if bool(page["exact_match"])) / len(pages) if pages else 0.0
        ),
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
