from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .models import DocumentIR


def semantic_ground_truth_path(pdf_path: str | Path) -> Path:
    return Path(pdf_path).with_suffix(".semantic-order.json")


def semantic_ground_truth_candidates(pdf_path: str | Path) -> list[Path]:
    adjacent = semantic_ground_truth_path(pdf_path)
    cwd_sidecar = Path.cwd() / "benchmarks" / "semantic-ground-truth" / adjacent.name
    source_sidecar = Path(__file__).resolve().parents[2] / "benchmarks" / "semantic-ground-truth" / adjacent.name
    candidates: list[Path] = []
    for path in (adjacent, cwd_sidecar, source_sidecar):
        if path not in candidates:
            candidates.append(path)
    return candidates


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
        if isinstance(page_truth, dict)
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


def _compare_page(document: DocumentIR, page_truth: dict[str, Any]) -> dict[str, Any]:
    page_index = int(page_truth.get("page_index", 0))
    match_mode = str(page_truth.get("match_mode", "full-sequence"))
    expected = [str(text).strip() for text in page_truth.get("text_sequence", []) if str(text).strip()]
    page = document.pages[page_index]
    actual = [
        element.source_text.strip()
        for element in sorted(page.elements, key=lambda item: (item.reading_order, item.bbox_pdf.y0, item.bbox_pdf.x0))
        if element.source_text.strip()
    ]

    matched_positions = _matched_positions(expected, actual)
    matched_count = sum(1 for position in matched_positions if position is not None)
    missing_texts = [text for text, position in zip(expected, matched_positions) if position is None]
    extra_texts = [] if match_mode == "ordered-subsequence" else _extra_texts(expected, actual)

    correct_pairs, total_pairs = _pairwise_order_counts(matched_positions)
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
        "expected_sequence": expected,
        "actual_sequence": actual,
    }


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
    return {
        "semantic_page_count": len(pages),
        "semantic_expected_text_count": expected_count,
        "semantic_actual_text_count": actual_count,
        "semantic_matched_text_count": matched_count,
        "semantic_ignored_text_count": sum(int(page["ignored_text_count"]) for page in pages),
        "semantic_missing_text_count": sum(int(page["missing_text_count"]) for page in pages),
        "semantic_extra_text_count": sum(int(page["extra_text_count"]) for page in pages),
        "semantic_sequence_edit_distance": edit_distance,
        "semantic_sequence_similarity": _round_ratio(1.0 - edit_distance / edit_denominator),
        "semantic_pairwise_correct_count": pairwise_correct,
        "semantic_pairwise_total_count": pairwise_total,
        "semantic_order_pair_accuracy": _round_ratio(pairwise_correct / pairwise_total if pairwise_total else 1.0),
        "semantic_exact_page_match_rate": _round_ratio(
            sum(1 for page in pages if bool(page["exact_match"])) / len(pages) if pages else 0.0
        ),
    }


def _round_ratio(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 8)
