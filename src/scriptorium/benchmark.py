from __future__ import annotations

import csv
import json
import math
import time
from pathlib import Path
from typing import Any

from .annotations import annotate_document
from .benchmark_fixtures import create_benchmark_fixtures
from .html_export import export_html
from .models import DocumentIR
from .native_pdf import extract_native_pdf_to_ir
from .pdf_export import print_html_to_pdf
from .pdf_render import render_pdf
from .quality import compare_pdf_renderings
from .semantic_quality import compare_semantic_reading_order


def run_benchmark(
    pdfs: list[str | Path] | None,
    out_dir: str | Path,
    dpi: int = 192,
) -> dict[str, Any]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    input_pdfs = [Path(pdf) for pdf in pdfs] if pdfs else create_benchmark_fixtures(target / "fixtures")

    cases: list[dict[str, Any]] = []
    for pdf_path in input_pdfs:
        cases.append(_run_case(pdf_path, target / "cases" / pdf_path.stem, dpi=dpi))

    summary = _summarize(cases)
    report = {
        "version": 1,
        "dpi": dpi,
        "case_count": len(cases),
        "summary": summary,
        "cases": cases,
    }
    (target / "benchmark_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_csv(target / "benchmark_summary.csv", cases)
    return report


def _run_case(pdf_path: Path, out_dir: Path, dpi: int) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    start = time.perf_counter()
    rendered = render_pdf(pdf_path, out_dir / "pages", dpi=dpi)
    timings["render_seconds"] = _elapsed(start)

    start = time.perf_counter()
    document = annotate_document(extract_native_pdf_to_ir(rendered))
    ir_path = out_dir / "document.ir.json"
    document.save(ir_path)
    timings["extract_annotate_seconds"] = _elapsed(start)

    start = time.perf_counter()
    html_path = export_html(document, out_dir / "html", display_mode="structured")
    timings["export_html_seconds"] = _elapsed(start)

    start = time.perf_counter()
    exported_pdf = print_html_to_pdf(html_path, out_dir / "structured-export.pdf")
    timings["print_pdf_seconds"] = _elapsed(start)

    start = time.perf_counter()
    quality = compare_pdf_renderings(pdf_path, exported_pdf, out_dir / "quality", dpi=dpi)
    timings["compare_seconds"] = _elapsed(start)

    start = time.perf_counter()
    semantic_quality = compare_semantic_reading_order(document, pdf_path, out_dir / "semantic")
    timings["semantic_compare_seconds"] = _elapsed(start)

    stats = _document_stats(document)
    max_diff_ratio = float(quality["max_diff_ratio"])
    mean_diff_ratio = float(quality["mean_diff_ratio"])
    p95_diff_ratio = float(quality["p95_diff_ratio"])
    similarity = round(max(0.0, 1.0 - max_diff_ratio), 8)
    total_seconds = round(sum(timings.values()), 6)
    return {
        "name": pdf_path.stem,
        "source_pdf": str(pdf_path),
        "ir": str(ir_path),
        "html": str(html_path),
        "exported_pdf": str(exported_pdf),
        "quality_report": str(out_dir / "quality" / "pdf_quality_report.json"),
        "semantic_report": str(out_dir / "semantic" / "semantic_quality_report.json"),
        "page_count": stats["page_count"],
        "element_count": stats["element_count"],
        "editable_element_count": stats["editable_element_count"],
        "shape_count": stats["shape_count"],
        "style_count": stats["style_count"],
        "annotation_count": stats["annotation_count"],
        "multi_column_element_count": stats["multi_column_element_count"],
        "column_flow_element_count": stats["column_flow_element_count"],
        "max_diff_ratio": max_diff_ratio,
        "mean_diff_ratio": mean_diff_ratio,
        "p95_diff_ratio": p95_diff_ratio,
        "worst_page": quality["worst_page"],
        "dimension_match": bool(quality["dimension_match"]),
        "page_count_match": bool(quality["page_count_match"]),
        "mismatched_page_count": int(quality["mismatched_page_count"]),
        "unmatched_page_count": int(quality["unmatched_page_count"]),
        "visual_similarity": similarity,
        **_semantic_case_metrics(semantic_quality),
        "total_seconds": total_seconds,
        "timings": timings,
    }


def _document_stats(document: DocumentIR) -> dict[str, int]:
    elements = [element for page in document.pages for element in page.elements]
    return {
        "page_count": document.page_count,
        "element_count": len(elements),
        "editable_element_count": sum(1 for element in elements if element.source_text.strip()),
        "shape_count": sum(1 for element in elements if element.type == "shape"),
        "style_count": len(document.metadata.get("styles", {})),
        "annotation_count": sum(1 for element in elements if "annotation" in element.metadata),
        "multi_column_element_count": sum(
            1
            for element in elements
            if element.source_text.strip() and int(element.metadata.get("column_count") or 1) > 1
        ),
        "column_flow_element_count": sum(
            1
            for element in elements
            if element.source_text.strip() and element.metadata.get("reading_order_strategy") == "column-flow-v1"
        ),
    }


def _summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not cases:
        return {}
    similarities = [float(case["visual_similarity"]) for case in cases]
    diff_ratios = [float(case["max_diff_ratio"]) for case in cases]
    mean_diff_ratios = [float(case["mean_diff_ratio"]) for case in cases]
    durations = [float(case["total_seconds"]) for case in cases]
    worst_case = max(cases, key=lambda case: float(case["max_diff_ratio"]))
    semantic_cases = [case for case in cases if bool(case["semantic_ground_truth_available"])]
    return {
        "mean_visual_similarity": round(sum(similarities) / len(similarities), 8),
        "min_visual_similarity": round(min(similarities), 8),
        "max_diff_ratio": round(max(diff_ratios), 8),
        "mean_diff_ratio": round(sum(mean_diff_ratios) / len(mean_diff_ratios), 8),
        "p95_diff_ratio": round(_percentile(diff_ratios, 95.0), 8),
        "worst_case": worst_case["name"],
        "worst_page": worst_case["worst_page"],
        "dimension_match_rate": round(
            sum(1 for case in cases if bool(case["dimension_match"])) / len(cases),
            8,
        ),
        "page_count_match_rate": round(
            sum(1 for case in cases if bool(case["page_count_match"])) / len(cases),
            8,
        ),
        "mismatched_case_count": sum(
            1
            for case in cases
            if not bool(case["dimension_match"]) or not bool(case["page_count_match"])
        ),
        "mean_total_seconds": round(sum(durations) / len(durations), 6),
        "total_pages": sum(int(case["page_count"]) for case in cases),
        "total_elements": sum(int(case["element_count"]) for case in cases),
        "total_editable_elements": sum(int(case["editable_element_count"]) for case in cases),
        "total_multi_column_elements": sum(int(case["multi_column_element_count"]) for case in cases),
        "total_column_flow_elements": sum(int(case["column_flow_element_count"]) for case in cases),
        **_summarize_semantic_cases(semantic_cases),
    }


def _write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    fieldnames = [
        "name",
        "page_count",
        "element_count",
        "editable_element_count",
        "shape_count",
        "style_count",
        "annotation_count",
        "multi_column_element_count",
        "column_flow_element_count",
        "visual_similarity",
        "semantic_ground_truth_available",
        "semantic_order_pair_accuracy",
        "semantic_sequence_similarity",
        "semantic_exact_page_match_rate",
        "semantic_sequence_edit_distance",
        "semantic_pairwise_correct_count",
        "semantic_pairwise_total_count",
        "semantic_missing_text_count",
        "semantic_extra_text_count",
        "max_diff_ratio",
        "mean_diff_ratio",
        "p95_diff_ratio",
        "worst_page",
        "dimension_match",
        "page_count_match",
        "mismatched_page_count",
        "unmatched_page_count",
        "total_seconds",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for case in cases:
            writer.writerow({field: case[field] for field in fieldnames})


def _elapsed(start: float) -> float:
    return round(time.perf_counter() - start, 6)


def _semantic_case_metrics(report: dict[str, Any]) -> dict[str, Any]:
    available = bool(report.get("ground_truth_available"))
    return {
        "semantic_ground_truth_available": available,
        "semantic_order_pair_accuracy": report.get("semantic_order_pair_accuracy") if available else None,
        "semantic_sequence_similarity": report.get("semantic_sequence_similarity") if available else None,
        "semantic_exact_page_match_rate": report.get("semantic_exact_page_match_rate") if available else None,
        "semantic_expected_text_count": report.get("semantic_expected_text_count") if available else 0,
        "semantic_actual_text_count": report.get("semantic_actual_text_count") if available else 0,
        "semantic_sequence_edit_distance": report.get("semantic_sequence_edit_distance") if available else 0,
        "semantic_pairwise_correct_count": report.get("semantic_pairwise_correct_count") if available else 0,
        "semantic_pairwise_total_count": report.get("semantic_pairwise_total_count") if available else 0,
        "semantic_missing_text_count": report.get("semantic_missing_text_count") if available else 0,
        "semantic_extra_text_count": report.get("semantic_extra_text_count") if available else 0,
    }


def _summarize_semantic_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not cases:
        return {
            "semantic_case_count": 0,
            "mean_semantic_order_pair_accuracy": None,
            "mean_semantic_sequence_similarity": None,
            "mean_semantic_exact_page_match_rate": None,
            "total_semantic_expected_text_count": 0,
            "total_semantic_missing_text_count": 0,
            "total_semantic_extra_text_count": 0,
        }

    expected_count = sum(int(case["semantic_expected_text_count"]) for case in cases)
    actual_count = sum(int(case["semantic_actual_text_count"]) for case in cases)
    edit_distance = sum(int(case["semantic_sequence_edit_distance"]) for case in cases)
    pairwise_correct = sum(int(case["semantic_pairwise_correct_count"]) for case in cases)
    pairwise_total = sum(int(case["semantic_pairwise_total_count"]) for case in cases)
    return {
        "semantic_case_count": len(cases),
        "mean_semantic_order_pair_accuracy": round(pairwise_correct / pairwise_total if pairwise_total else 1.0, 8),
        "mean_semantic_sequence_similarity": round(
            1.0 - edit_distance / max(expected_count, actual_count, 1),
            8,
        ),
        "mean_semantic_exact_page_match_rate": round(
            sum(float(case["semantic_exact_page_match_rate"]) for case in cases) / len(cases),
            8,
        ),
        "total_semantic_expected_text_count": expected_count,
        "total_semantic_missing_text_count": sum(int(case["semantic_missing_text_count"]) for case in cases),
        "total_semantic_extra_text_count": sum(int(case["semantic_extra_text_count"]) for case in cases),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (percentile / 100)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction
