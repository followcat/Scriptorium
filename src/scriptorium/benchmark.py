from __future__ import annotations

import csv
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from .annotations import annotate_document
from .benchmark_fixtures import create_benchmark_fixtures
from .html_export import export_html
from .models import DocumentIR
from .native_pdf import FontProfile, RasterPolicy, extract_native_pdf_to_ir
from .pdf_export import print_html_to_pdf
from .pdf_render import render_pdf
from .quality import compare_pdf_renderings
from .semantic_quality import compare_semantic_reading_order
from .structure_evidence import apply_structure_evidence, load_structure_json

BenchmarkFontProfile = Literal["browser-default", "local-urw", "auto"]
BenchmarkHtmlMode = Literal["structured", "fidelity"]
BenchmarkFontSizeScale = float | Literal["auto"]
FONT_PROFILE_CANDIDATES: tuple[FontProfile, ...] = ("browser-default", "local-urw")
FONT_SIZE_SCALE_CANDIDATES: tuple[float, ...] = (0.99, 1.0)


def run_benchmark(
    pdfs: list[str | Path] | None,
    out_dir: str | Path,
    dpi: int = 192,
    structure_jsons: list[str | Path] | None = None,
    font_profile: BenchmarkFontProfile = "browser-default",
    raster_policy: RasterPolicy = "dense",
    html_mode: BenchmarkHtmlMode = "structured",
    font_size_scale: BenchmarkFontSizeScale = 1.0,
) -> dict[str, Any]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    font_size_scale_request = _font_size_scale_request(font_size_scale)
    input_pdfs = [Path(pdf) for pdf in pdfs] if pdfs else create_benchmark_fixtures(target / "fixtures")
    structure_json_by_pdf = _structure_json_by_pdf(input_pdfs, structure_jsons or [])

    cases: list[dict[str, Any]] = []
    for pdf_path in input_pdfs:
        if font_profile == "auto" or font_size_scale_request == "auto":
            cases.append(
                _run_calibrated_case(
                    pdf_path,
                    target / "cases" / pdf_path.stem,
                    dpi=dpi,
                    structure_json=structure_json_by_pdf.get(pdf_path.resolve()),
                    raster_policy=raster_policy,
                    html_mode=html_mode,
                    font_size_scale=font_size_scale_request,
                    font_profile=font_profile,
                )
            )
        else:
            cases.append(
                _run_case(
                    pdf_path,
                    target / "cases" / pdf_path.stem,
                    dpi=dpi,
                    structure_json=structure_json_by_pdf.get(pdf_path.resolve()),
                    font_profile=font_profile,
                    raster_policy=raster_policy,
                    html_mode=html_mode,
                    font_size_scale=float(font_size_scale_request),
                )
            )

    summary = _summarize(cases)
    report = {
        "version": 1,
        "dpi": dpi,
        "font_profile": font_profile,
        "raster_policy": raster_policy,
        "html_mode": html_mode,
        "font_size_scale": font_size_scale_request,
        "case_count": len(cases),
        "summary": summary,
        "cases": cases,
    }
    (target / "benchmark_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_csv(target / "benchmark_summary.csv", cases)
    return report


def _run_case(
    pdf_path: Path,
    out_dir: Path,
    dpi: int,
    structure_json: Path | None = None,
    font_profile: FontProfile = "browser-default",
    raster_policy: RasterPolicy = "dense",
    html_mode: BenchmarkHtmlMode = "structured",
    font_size_scale: float = 1.0,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    start = time.perf_counter()
    rendered = render_pdf(pdf_path, out_dir / "pages", dpi=dpi, include_svg_background=html_mode == "fidelity")
    timings["render_seconds"] = _elapsed(start)

    start = time.perf_counter()
    document = extract_native_pdf_to_ir(
        rendered,
        font_profile=font_profile,
        raster_policy=raster_policy,
        font_size_scale=font_size_scale,
    )
    if structure_json is not None:
        apply_structure_evidence(document, load_structure_json(structure_json), source=_structure_source_name(structure_json))
    annotate_document(document)
    ir_path = out_dir / "document.ir.json"
    document.save(ir_path)
    timings["extract_annotate_seconds"] = _elapsed(start)

    start = time.perf_counter()
    html_path = export_html(document, out_dir / "html", display_mode=html_mode)
    timings["export_html_seconds"] = _elapsed(start)

    start = time.perf_counter()
    exported_pdf = print_html_to_pdf(html_path, out_dir / f"{html_mode}-export.pdf")
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
        "image_count": stats["image_count"],
        "shape_count": stats["shape_count"],
        "style_count": stats["style_count"],
        "annotation_count": stats["annotation_count"],
        "text_run_count": stats["text_run_count"],
        "mixed_inline_style_element_count": stats["mixed_inline_style_element_count"],
        "multi_column_element_count": stats["multi_column_element_count"],
        "column_flow_element_count": stats["column_flow_element_count"],
        "recursive_xy_cut_element_count": stats["recursive_xy_cut_element_count"],
        "reading_order_strategy_counts": stats["reading_order_strategy_counts"],
        "layout_region_counts": stats["layout_region_counts"],
        "table_region_count": stats["table_region_count"],
        "figure_region_count": stats["figure_region_count"],
        "raster_fallback_count": stats["raster_fallback_count"],
        "rasterized_text_count": stats["rasterized_text_count"],
        "rasterized_image_count": stats["rasterized_image_count"],
        "rasterized_shape_count": stats["rasterized_shape_count"],
        "vector_background_page_count": stats["vector_background_page_count"],
        "font_profile": stats["font_profile"],
        "raster_policy": stats["raster_policy"],
        "html_mode": html_mode,
        "font_size_scale": stats["font_size_scale"],
        "structure_evidence_source": stats["structure_evidence_source"],
        "structure_evidence_region_count": stats["structure_evidence_region_count"],
        "structure_evidence_matched_element_count": stats["structure_evidence_matched_element_count"],
        "structure_evidence_reordered_page_count": stats["structure_evidence_reordered_page_count"],
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


def _run_calibrated_case(
    pdf_path: Path,
    out_dir: Path,
    dpi: int,
    structure_json: Path | None,
    raster_policy: RasterPolicy,
    html_mode: BenchmarkHtmlMode,
    font_size_scale: BenchmarkFontSizeScale,
    font_profile: BenchmarkFontProfile,
) -> dict[str, Any]:
    start = time.perf_counter()
    profile_candidates = FONT_PROFILE_CANDIDATES if font_profile == "auto" else (font_profile,)
    scale_candidates = _font_size_scale_candidates(font_size_scale)
    candidates = [
        _run_case(
            pdf_path,
            out_dir / f"{profile}-scale-{_font_size_scale_slug(scale)}",
            dpi=dpi,
            structure_json=structure_json,
            font_profile=profile,
            raster_policy=raster_policy,
            html_mode=html_mode,
            font_size_scale=scale,
        )
        for profile in profile_candidates
        for scale in scale_candidates
    ]
    calibration_total_seconds = _elapsed(start)
    selected = max(
        candidates,
        key=lambda case: (
            float(case["visual_similarity"]),
            -float(case["mean_diff_ratio"]),
            -float(case["total_seconds"]),
        ),
    )
    selected_candidate_seconds = selected["total_seconds"]
    candidate_summaries = [
        {
            "font_profile": candidate["font_profile"],
            "html_mode": candidate["html_mode"],
            "font_size_scale": candidate["font_size_scale"],
            "visual_similarity": candidate["visual_similarity"],
            "max_diff_ratio": candidate["max_diff_ratio"],
            "mean_diff_ratio": candidate["mean_diff_ratio"],
            "p95_diff_ratio": candidate["p95_diff_ratio"],
            "total_seconds": candidate["total_seconds"],
            "html": candidate["html"],
            "exported_pdf": candidate["exported_pdf"],
            "quality_report": candidate["quality_report"],
        }
        for candidate in candidates
    ]
    selected["font_profile_request"] = font_profile
    selected["font_profile_selected"] = selected["font_profile"]
    selected["font_size_scale_request"] = font_size_scale
    selected["font_size_scale_selected"] = selected["font_size_scale"]
    selected["calibration_selected_total_seconds"] = selected_candidate_seconds
    selected["calibration_total_seconds"] = calibration_total_seconds
    selected["total_seconds"] = calibration_total_seconds
    selected["font_profile_candidates"] = candidate_summaries
    selected["font_size_scale_candidates"] = candidate_summaries
    if font_profile == "auto":
        selected["font_profile_auto_total_seconds"] = calibration_total_seconds
        selected["font_profile_selected_total_seconds"] = selected_candidate_seconds
    if font_size_scale == "auto":
        selected["font_size_scale_auto_total_seconds"] = calibration_total_seconds
        selected["font_size_scale_selected_total_seconds"] = selected_candidate_seconds
    return selected


def _document_stats(document: DocumentIR) -> dict[str, Any]:
    elements = [element for page in document.pages for element in page.elements]
    text_elements = [element for element in elements if element.source_text.strip()]
    raster_elements = [element for element in elements if element.metadata.get("raster_fallback")]
    layout_region_counts = _layout_region_counts(document)
    reading_order_strategy_counts = Counter(
        str(element.metadata.get("reading_order_strategy") or "unknown") for element in text_elements
    )
    structure_evidence = document.metadata.get("structure_evidence")
    if not isinstance(structure_evidence, dict):
        structure_evidence = {}
    return {
        "page_count": document.page_count,
        "element_count": len(elements),
        "editable_element_count": len(text_elements),
        "image_count": sum(1 for element in elements if element.type == "image"),
        "shape_count": sum(1 for element in elements if element.type == "shape"),
        "style_count": len(document.metadata.get("styles", {})),
        "annotation_count": sum(1 for element in elements if "annotation" in element.metadata),
        "text_run_count": sum(int(element.metadata.get("text_run_count") or 0) for element in text_elements),
        "mixed_inline_style_element_count": sum(
            1 for element in text_elements if bool(element.metadata.get("mixed_inline_style"))
        ),
        "multi_column_element_count": sum(
            1
            for element in text_elements
            if int(element.metadata.get("column_count") or 1) > 1
        ),
        "column_flow_element_count": sum(
            1
            for element in text_elements
            if element.metadata.get("reading_order_strategy") == "column-flow-v1"
        ),
        "recursive_xy_cut_element_count": sum(
            1
            for element in text_elements
            if element.metadata.get("reading_order_strategy") == "recursive-xy-cut-v1"
        ),
        "reading_order_strategy_counts": dict(sorted(reading_order_strategy_counts.items())),
        "layout_region_counts": layout_region_counts,
        "table_region_count": int(layout_region_counts.get("table", 0)),
        "figure_region_count": int(layout_region_counts.get("figure", 0)),
        "raster_fallback_count": len(raster_elements),
        "rasterized_text_count": sum(int(element.metadata.get("rasterized_text_count") or 0) for element in raster_elements),
        "rasterized_image_count": sum(
            int(element.metadata.get("rasterized_image_count") or 0) for element in raster_elements
        ),
        "rasterized_shape_count": sum(
            int(element.metadata.get("rasterized_shape_count") or 0) for element in raster_elements
        ),
        "vector_background_page_count": sum(1 for page in document.pages if page.background_svg),
        "font_profile": str(document.metadata.get("font_profile") or "unknown"),
        "raster_policy": str(document.metadata.get("raster_policy") or "unknown"),
        "font_size_scale": float(document.metadata.get("font_size_scale") or 1.0),
        "structure_evidence_source": structure_evidence.get("source"),
        "structure_evidence_region_count": int(structure_evidence.get("region_count") or 0),
        "structure_evidence_matched_element_count": int(structure_evidence.get("matched_element_count") or 0),
        "structure_evidence_reordered_page_count": int(structure_evidence.get("reordered_page_count") or 0),
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
        "total_image_elements": sum(int(case["image_count"]) for case in cases),
        "total_text_runs": sum(int(case["text_run_count"]) for case in cases),
        "total_mixed_inline_style_elements": sum(int(case["mixed_inline_style_element_count"]) for case in cases),
        "total_multi_column_elements": sum(int(case["multi_column_element_count"]) for case in cases),
        "total_column_flow_elements": sum(int(case["column_flow_element_count"]) for case in cases),
        "total_recursive_xy_cut_elements": sum(int(case["recursive_xy_cut_element_count"]) for case in cases),
        "reading_order_strategy_counts": _sum_strategy_counts(cases),
        "font_profile_counts": _sum_case_values(cases, "font_profile"),
        "html_mode_counts": _sum_case_values(cases, "html_mode"),
        "font_size_scale_counts": _sum_case_values(cases, "font_size_scale"),
        "layout_region_counts": _sum_case_count_dicts(cases, "layout_region_counts"),
        "total_table_regions": sum(int(case["table_region_count"]) for case in cases),
        "total_figure_regions": sum(int(case["figure_region_count"]) for case in cases),
        "total_raster_fallbacks": sum(int(case["raster_fallback_count"]) for case in cases),
        "total_rasterized_text_elements": sum(int(case["rasterized_text_count"]) for case in cases),
        "total_rasterized_image_elements": sum(int(case["rasterized_image_count"]) for case in cases),
        "total_rasterized_shape_elements": sum(int(case["rasterized_shape_count"]) for case in cases),
        "total_vector_background_pages": sum(int(case["vector_background_page_count"]) for case in cases),
        "total_structure_evidence_regions": sum(int(case["structure_evidence_region_count"]) for case in cases),
        "total_structure_evidence_matched_elements": sum(
            int(case["structure_evidence_matched_element_count"]) for case in cases
        ),
        "total_structure_evidence_reordered_pages": sum(
            int(case["structure_evidence_reordered_page_count"]) for case in cases
        ),
        **_summarize_semantic_cases(semantic_cases),
    }


def _write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    fieldnames = [
        "name",
        "page_count",
        "element_count",
        "editable_element_count",
        "image_count",
        "shape_count",
        "style_count",
        "annotation_count",
        "text_run_count",
        "mixed_inline_style_element_count",
        "multi_column_element_count",
        "column_flow_element_count",
        "recursive_xy_cut_element_count",
        "table_region_count",
        "figure_region_count",
        "raster_fallback_count",
        "rasterized_text_count",
        "rasterized_image_count",
        "rasterized_shape_count",
        "vector_background_page_count",
        "font_profile",
        "raster_policy",
        "html_mode",
        "font_size_scale",
        "structure_evidence_source",
        "structure_evidence_region_count",
        "structure_evidence_matched_element_count",
        "structure_evidence_reordered_page_count",
        "visual_similarity",
        "semantic_ground_truth_available",
        "semantic_order_pair_accuracy",
        "semantic_sequence_similarity",
        "semantic_exact_page_match_rate",
        "semantic_sequence_edit_distance",
        "semantic_pairwise_correct_count",
        "semantic_pairwise_total_count",
        "semantic_ignored_text_count",
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


def _sum_strategy_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for case in cases:
        counts.update(case.get("reading_order_strategy_counts") or {})
    return dict(sorted(counts.items()))


def _sum_case_values(cases: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter(str(case.get(key) or "unknown") for case in cases)
    return dict(sorted(counts.items()))


def _font_size_scale_candidates(font_size_scale: BenchmarkFontSizeScale) -> tuple[float, ...]:
    if font_size_scale == "auto":
        return FONT_SIZE_SCALE_CANDIDATES
    scale = float(font_size_scale)
    if scale < 0.9 or scale > 1.1:
        raise ValueError(f"font_size_scale must be between 0.9 and 1.1, got {scale}")
    return (scale,)


def _font_size_scale_request(font_size_scale: BenchmarkFontSizeScale) -> BenchmarkFontSizeScale:
    if font_size_scale == "auto":
        return "auto"
    return _font_size_scale_candidates(font_size_scale)[0]


def _font_size_scale_slug(scale: float) -> str:
    return f"{scale:.3f}".rstrip("0").rstrip(".").replace(".", "p")


def _layout_region_counts(document: DocumentIR) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for page_regions in document.metadata.get("layout_regions", []):
        if not isinstance(page_regions, dict):
            continue
        regions = page_regions.get("regions")
        if not isinstance(regions, list):
            continue
        for region in regions:
            if isinstance(region, dict) and region.get("kind"):
                counts[str(region["kind"])] += 1
    return dict(sorted(counts.items()))


def _structure_json_by_pdf(input_pdfs: list[Path], structure_jsons: list[str | Path]) -> dict[Path, Path]:
    if not structure_jsons:
        return {}

    pdfs = [pdf.resolve() for pdf in input_pdfs]
    paths = [Path(path).resolve() for path in structure_jsons]
    if len(paths) == 1 and len(pdfs) == 1:
        return {pdfs[0]: paths[0]}
    if len(paths) == len(pdfs):
        return dict(zip(pdfs, paths))

    pdf_keys: dict[str, Path] = {}
    for pdf in pdfs:
        pdf_keys[pdf.stem] = pdf
        if pdf.parent.name:
            pdf_keys[f"{pdf.parent.name}.{pdf.stem}"] = pdf

    mapping: dict[Path, Path] = {}
    unmatched: list[Path] = []
    for path in paths:
        pdf = pdf_keys.get(_structure_match_key(path))
        if pdf is None:
            unmatched.append(path)
            continue
        mapping[pdf] = path

    if unmatched or len(mapping) != len(paths):
        names = ", ".join(str(path) for path in unmatched or paths)
        raise ValueError(
            "Could not match structure JSON files to PDFs. "
            "Pass one JSON for one PDF, pass the same number of PDFs and JSON files, "
            f"or use matching names such as <pdf-stem>.structure.json. Unmatched: {names}"
        )
    return mapping


def _structure_match_key(path: Path) -> str:
    name = path.name
    if name.endswith(".json"):
        name = name[:-5]
    for suffix in (".structure", ".ppstructure", ".paddleocr", ".paddle", ".ocr"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _structure_source_name(path: Path) -> str:
    return f"structure-json:{path.name}"


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
        "semantic_ignored_text_count": report.get("semantic_ignored_text_count") if available else 0,
        "semantic_ignored_text_zone_counts": report.get("semantic_ignored_text_zone_counts") if available else {},
        "semantic_ignored_text_role_counts": report.get("semantic_ignored_text_role_counts") if available else {},
        "semantic_ignored_text_source_counts": report.get("semantic_ignored_text_source_counts") if available else {},
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
            "total_semantic_ignored_text_count": 0,
            "total_semantic_ignored_text_zone_counts": {},
            "total_semantic_ignored_text_role_counts": {},
            "total_semantic_ignored_text_source_counts": {},
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
        "total_semantic_ignored_text_count": sum(int(case["semantic_ignored_text_count"]) for case in cases),
        "total_semantic_ignored_text_zone_counts": _sum_case_count_dicts(cases, "semantic_ignored_text_zone_counts"),
        "total_semantic_ignored_text_role_counts": _sum_case_count_dicts(cases, "semantic_ignored_text_role_counts"),
        "total_semantic_ignored_text_source_counts": _sum_case_count_dicts(cases, "semantic_ignored_text_source_counts"),
        "total_semantic_missing_text_count": sum(int(case["semantic_missing_text_count"]) for case in cases),
        "total_semantic_extra_text_count": sum(int(case["semantic_extra_text_count"]) for case in cases),
    }


def _sum_case_count_dicts(cases: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for case in cases:
        value = case.get(key)
        if isinstance(value, dict):
            counts.update({str(item_key): int(item_value) for item_key, item_value in value.items()})
    return dict(sorted(counts.items()))


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
