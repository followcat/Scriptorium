from __future__ import annotations

import csv
import json
import math
import time
from collections import Counter
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Any, Literal

from .annotations import annotate_document
from .benchmark_fixtures import create_benchmark_fixtures
from .html_export import FidelityBackground, HtmlTextFit, export_html
from .models import DocumentIR
from .native_pdf import FontProfile, OcrFallback, RasterPolicy, extract_native_pdf_to_ir
from .pdf_export import print_html_to_pdf
from .pdf_render import render_pdf
from .quality import compare_pdf_renderings
from .semantic_quality import compare_semantic_reading_order
from .structure_evidence import apply_structure_evidence, load_structure_json

BenchmarkFontProfile = Literal["browser-default", "local-urw", "auto"]
HtmlMode = Literal["structured", "fidelity"]
BenchmarkHtmlMode = Literal["structured", "fidelity", "auto"]
BenchmarkFontSizeScale = float | Literal["auto"]
BenchmarkTextFit = Literal["none", "svg", "auto"]
BenchmarkFidelityBackground = Literal["svg", "raster", "auto"]
FidelityBackgroundChoice = Literal["none", "svg", "raster"]
FONT_PROFILE_CANDIDATES: tuple[FontProfile, ...] = ("browser-default", "local-urw")
HTML_MODE_CANDIDATES: tuple[HtmlMode, ...] = ("structured", "fidelity")
FONT_SIZE_SCALE_CANDIDATES: tuple[float, ...] = (0.99, 1.0)
TEXT_FIT_CANDIDATES: tuple[HtmlTextFit, ...] = ("none", "svg")
FIDELITY_BACKGROUND_CANDIDATES: tuple[FidelityBackground, ...] = ("svg", "raster")


def run_benchmark(
    pdfs: list[str | Path] | None,
    out_dir: str | Path,
    dpi: int = 192,
    max_pages: int | None = None,
    structure_jsons: list[str | Path] | None = None,
    font_profile: BenchmarkFontProfile = "browser-default",
    raster_policy: RasterPolicy = "dense",
    ocr_fallback: OcrFallback = "image-only",
    ocr_language: str = "eng+chi_sim",
    ocr_dpi: int = 144,
    html_mode: BenchmarkHtmlMode = "structured",
    font_size_scale: BenchmarkFontSizeScale = 1.0,
    text_fit: BenchmarkTextFit = "none",
    fidelity_background: BenchmarkFidelityBackground = "auto",
) -> dict[str, Any]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    max_pages_request = _max_pages_request(max_pages)
    html_mode_request = _html_mode_request(html_mode)
    font_size_scale_request = _font_size_scale_request(font_size_scale)
    text_fit_request = _text_fit_request(text_fit)
    fidelity_background_request = _fidelity_background_request(fidelity_background)
    input_pdfs = [Path(pdf) for pdf in pdfs] if pdfs else create_benchmark_fixtures(target / "fixtures")
    structure_json_by_pdf = _structure_json_by_pdf(input_pdfs, structure_jsons or [])

    cases: list[dict[str, Any]] = []
    for pdf_path in input_pdfs:
        if (
            font_profile == "auto"
            or html_mode_request == "auto"
            or font_size_scale_request == "auto"
            or text_fit_request == "auto"
            or _fidelity_background_needs_calibration(html_mode_request, fidelity_background_request)
        ):
            cases.append(
                _run_calibrated_case(
                    pdf_path,
                    target / "cases" / pdf_path.stem,
                    dpi=dpi,
                    max_pages=max_pages_request,
                    structure_json=structure_json_by_pdf.get(pdf_path.resolve()),
                    raster_policy=raster_policy,
                    ocr_fallback=ocr_fallback,
                    ocr_language=ocr_language,
                    ocr_dpi=ocr_dpi,
                    html_mode=html_mode_request,
                    font_size_scale=font_size_scale_request,
                    font_profile=font_profile,
                    text_fit=text_fit_request,
                    fidelity_background=fidelity_background_request,
                )
            )
        else:
            case_fidelity_background = _single_fidelity_background(html_mode_request, fidelity_background_request)
            cases.append(
                _run_case(
                    pdf_path,
                    target / "cases" / pdf_path.stem,
                    dpi=dpi,
                    max_pages=max_pages_request,
                    structure_json=structure_json_by_pdf.get(pdf_path.resolve()),
                    font_profile=font_profile,
                    raster_policy=raster_policy,
                    ocr_fallback=ocr_fallback,
                    ocr_language=ocr_language,
                    ocr_dpi=ocr_dpi,
                    html_mode=html_mode_request,
                    font_size_scale=float(font_size_scale_request),
                    text_fit=text_fit_request,
                    fidelity_background=case_fidelity_background,
                )
            )

    summary = _summarize(cases)
    report = {
        "version": 1,
        "dpi": dpi,
        "max_pages": max_pages_request,
        "font_profile": font_profile,
        "raster_policy": raster_policy,
        "ocr_fallback": ocr_fallback,
        "ocr_language": ocr_language,
        "ocr_dpi": ocr_dpi,
        "html_mode": html_mode_request,
        "font_size_scale": font_size_scale_request,
        "text_fit": text_fit_request,
        "fidelity_background": fidelity_background_request,
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
    max_pages: int | None = None,
    structure_json: Path | None = None,
    font_profile: FontProfile = "browser-default",
    raster_policy: RasterPolicy = "dense",
    ocr_fallback: OcrFallback = "image-only",
    ocr_language: str = "eng+chi_sim",
    ocr_dpi: int = 144,
    html_mode: HtmlMode = "structured",
    font_size_scale: float = 1.0,
    text_fit: HtmlTextFit = "none",
    fidelity_background: FidelityBackground = "svg",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    start = time.perf_counter()
    rendered = render_pdf(
        pdf_path,
        out_dir / "pages",
        dpi=dpi,
        include_svg_background=html_mode == "fidelity" and fidelity_background == "svg",
        max_pages=max_pages,
    )
    timings["render_seconds"] = _elapsed(start)

    start = time.perf_counter()
    document = extract_native_pdf_to_ir(
        rendered,
        font_profile=font_profile,
        raster_policy=raster_policy,
        font_size_scale=font_size_scale,
        ocr_fallback=ocr_fallback,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
    )
    if structure_json is not None:
        apply_structure_evidence(document, load_structure_json(structure_json), source=_structure_source_name(structure_json))
    annotate_document(document)
    ir_path = out_dir / "document.ir.json"
    document.save(ir_path)
    timings["extract_annotate_seconds"] = _elapsed(start)

    start = time.perf_counter()
    html_path = export_html(
        document,
        out_dir / "html",
        display_mode=html_mode,
        text_fit=text_fit,
        fidelity_background=fidelity_background,
    )
    timings["export_html_seconds"] = _elapsed(start)

    start = time.perf_counter()
    export_name = f"{html_mode}-{fidelity_background}-export.pdf" if html_mode == "fidelity" else f"{html_mode}-export.pdf"
    exported_pdf = print_html_to_pdf(
        html_path,
        out_dir / export_name,
        page_sizes_pt=[(page.width_pt, page.height_pt) for page in document.pages],
    )
    timings["print_pdf_seconds"] = _elapsed(start)

    start = time.perf_counter()
    quality = compare_pdf_renderings(pdf_path, exported_pdf, out_dir / "quality", dpi=dpi, max_pages=max_pages)
    timings["compare_seconds"] = _elapsed(start)

    start = time.perf_counter()
    semantic_quality = compare_semantic_reading_order(document, pdf_path, out_dir / "semantic")
    timings["semantic_compare_seconds"] = _elapsed(start)

    stats = _document_stats(document)
    reading_order_risk = _reading_order_risk_metrics(document, semantic_quality)
    max_diff_ratio = float(quality["max_diff_ratio"])
    mean_diff_ratio = float(quality["mean_diff_ratio"])
    p95_diff_ratio = float(quality["p95_diff_ratio"])
    similarity = round(max(0.0, 1.0 - max_diff_ratio), 8)
    total_seconds = round(sum(timings.values()), 6)
    return {
        "name": pdf_path.stem,
        "source_pdf": str(pdf_path),
        "max_pages": max_pages,
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
        "ocr_fallback": stats["ocr_fallback"],
        "ocr_fallback_applied_page_count": stats["ocr_fallback_applied_page_count"],
        "ocr_text_count": stats["ocr_text_count"],
        "image_only_candidate_page_count": stats["image_only_candidate_page_count"],
        "textless_page_count": stats["textless_page_count"],
        "html_mode": html_mode,
        "font_size_scale": stats["font_size_scale"],
        "text_fit": text_fit,
        "fidelity_background": fidelity_background if html_mode == "fidelity" else "none",
        "structure_evidence_source": stats["structure_evidence_source"],
        "structure_evidence_region_count": stats["structure_evidence_region_count"],
        "structure_evidence_matched_element_count": stats["structure_evidence_matched_element_count"],
        "structure_evidence_reordered_page_count": stats["structure_evidence_reordered_page_count"],
        **reading_order_risk,
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
    max_pages: int | None,
    structure_json: Path | None,
    raster_policy: RasterPolicy,
    ocr_fallback: OcrFallback,
    ocr_language: str,
    ocr_dpi: int,
    html_mode: BenchmarkHtmlMode,
    font_size_scale: BenchmarkFontSizeScale,
    font_profile: BenchmarkFontProfile,
    text_fit: BenchmarkTextFit,
    fidelity_background: BenchmarkFidelityBackground,
) -> dict[str, Any]:
    start = time.perf_counter()
    candidates = [
        _run_case(
            pdf_path,
            out_dir / _candidate_slug(mode, background, profile, scale, fit),
            dpi=dpi,
            max_pages=max_pages,
            structure_json=structure_json,
            font_profile=profile,
            raster_policy=raster_policy,
            ocr_fallback=ocr_fallback,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
            html_mode=mode,
            font_size_scale=scale,
            text_fit=fit,
            fidelity_background=_case_fidelity_background(background),
        )
        for mode, background, profile, scale, fit in _calibration_candidates(
            font_profile=font_profile,
            html_mode=html_mode,
            font_size_scale=font_size_scale,
            text_fit=text_fit,
            fidelity_background=fidelity_background,
        )
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
            "text_fit": candidate["text_fit"],
            "fidelity_background": candidate["fidelity_background"],
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
    selected["html_mode_request"] = html_mode
    selected["html_mode_selected"] = selected["html_mode"]
    selected["font_size_scale_request"] = font_size_scale
    selected["font_size_scale_selected"] = selected["font_size_scale"]
    selected["text_fit_request"] = text_fit
    selected["text_fit_selected"] = selected["text_fit"]
    selected["fidelity_background_request"] = fidelity_background
    selected["fidelity_background_selected"] = selected["fidelity_background"]
    selected["calibration_selected_total_seconds"] = selected_candidate_seconds
    selected["calibration_total_seconds"] = calibration_total_seconds
    selected["total_seconds"] = calibration_total_seconds
    selected["font_profile_candidates"] = candidate_summaries
    selected["html_mode_candidates"] = candidate_summaries
    selected["font_size_scale_candidates"] = candidate_summaries
    selected["text_fit_candidates"] = candidate_summaries
    selected["fidelity_background_candidates"] = candidate_summaries
    if font_profile == "auto":
        selected["font_profile_auto_total_seconds"] = calibration_total_seconds
        selected["font_profile_selected_total_seconds"] = selected_candidate_seconds
    if html_mode == "auto":
        selected["html_mode_auto_total_seconds"] = calibration_total_seconds
        selected["html_mode_selected_total_seconds"] = selected_candidate_seconds
    if font_size_scale == "auto":
        selected["font_size_scale_auto_total_seconds"] = calibration_total_seconds
        selected["font_size_scale_selected_total_seconds"] = selected_candidate_seconds
    if text_fit == "auto":
        selected["text_fit_auto_total_seconds"] = calibration_total_seconds
        selected["text_fit_selected_total_seconds"] = selected_candidate_seconds
    if _fidelity_background_needs_calibration(html_mode, fidelity_background):
        selected["fidelity_background_auto_total_seconds"] = calibration_total_seconds
        selected["fidelity_background_selected_total_seconds"] = selected_candidate_seconds
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
        "ocr_fallback": str(document.metadata.get("ocr_fallback") or "unknown"),
        "ocr_fallback_applied_page_count": _ocr_fallback_applied_page_count(document),
        "ocr_text_count": sum(1 for element in text_elements if element.metadata.get("source") == "native-ocr"),
        "image_only_candidate_page_count": _page_extraction_count(document, "image_only_candidate", True),
        "textless_page_count": sum(
            1 for page in document.pages if not any(element.source_text.strip() for element in page.elements)
        ),
        "font_size_scale": float(document.metadata.get("font_size_scale") or 1.0),
        "structure_evidence_source": structure_evidence.get("source"),
        "structure_evidence_region_count": int(structure_evidence.get("region_count") or 0),
        "structure_evidence_matched_element_count": int(structure_evidence.get("matched_element_count") or 0),
        "structure_evidence_reordered_page_count": int(structure_evidence.get("reordered_page_count") or 0),
    }


def _reading_order_risk_metrics(document: DocumentIR, semantic_quality: dict[str, Any]) -> dict[str, Any]:
    column_geometry_pages = 0
    visual_yx_column_pages = 0
    repeated_anchor_pages = 0
    max_repeated_anchor_columns = 0
    table_like_pages = 0
    table_like_visual_yx_pages = 0
    text_count = 0
    for page in document.pages:
        text_elements = [element for element in page.elements if element.source_text.strip()]
        text_count += len(text_elements)
        geometry = _page_reading_order_geometry_profile(page.width_pt, [element.bbox_pdf for element in text_elements])
        if int(geometry["repeated_anchor_column_count"]) >= 2:
            repeated_anchor_pages += 1
            max_repeated_anchor_columns = max(max_repeated_anchor_columns, int(geometry["repeated_anchor_column_count"]))
        if bool(geometry["table_like"]):
            table_like_pages += 1
        page_strategies = Counter(
            str(element.metadata.get("reading_order_strategy") or "unknown") for element in text_elements
        )
        visual_yx_dominant = page_strategies.get("visual-yx", 0) > sum(page_strategies.values()) * 0.6
        if bool(geometry["text_flow_column_geometry"]):
            column_geometry_pages += 1
            if visual_yx_dominant:
                visual_yx_column_pages += 1
        if bool(geometry["table_like"]) and visual_yx_dominant:
            table_like_visual_yx_pages += 1

    semantic_available = bool(semantic_quality.get("ground_truth_available"))
    ignored_count = int(semantic_quality.get("semantic_ignored_text_count") or 0) if semantic_available else 0
    missing_count = int(semantic_quality.get("semantic_missing_text_count") or 0) if semantic_available else 0
    extra_count = int(semantic_quality.get("semantic_extra_text_count") or 0) if semantic_available else 0
    actual_count = int(semantic_quality.get("semantic_actual_text_count") or 0) if semantic_available else text_count
    expected_count = int(semantic_quality.get("semantic_expected_text_count") or 0) if semantic_available else 0
    unlabeled_count = ignored_count if semantic_available else text_count

    page_count = max(document.page_count, 1)
    column_risk = visual_yx_column_pages / page_count
    unlabeled_ratio = unlabeled_count / max(actual_count, text_count, 1)
    missing_extra_ratio = (missing_count + extra_count) / max(expected_count, actual_count, text_count, 1)
    no_ground_truth_risk = 1.0 if not semantic_available and text_count else 0.0
    score = min(
        1.0,
        0.45 * column_risk
        + 0.25 * min(unlabeled_ratio, 1.0)
        + 0.2 * min(missing_extra_ratio, 1.0)
        + 0.1 * no_ground_truth_risk,
    )
    return {
        "reading_order_risk_score": round(score, 8),
        "reading_order_risk_level": _risk_level(score),
        "reading_order_column_geometry_page_count": column_geometry_pages,
        "reading_order_visual_yx_column_page_count": visual_yx_column_pages,
        "reading_order_repeated_anchor_page_count": repeated_anchor_pages,
        "reading_order_max_repeated_anchor_columns": max_repeated_anchor_columns,
        "reading_order_table_like_page_count": table_like_pages,
        "reading_order_table_like_visual_yx_page_count": table_like_visual_yx_pages,
        "reading_order_unlabeled_text_risk_count": unlabeled_count,
        "reading_order_semantic_ignored_text_ratio": round(unlabeled_ratio, 8),
        "reading_order_semantic_missing_extra_ratio": round(missing_extra_ratio, 8),
        "reading_order_ground_truth_available": semantic_available,
    }


def _page_reading_order_geometry_profile(page_width: float, bboxes: list[Any]) -> dict[str, Any]:
    candidates = [
        bbox
        for bbox in bboxes
        if bbox.width >= 8 and bbox.height >= 4 and bbox.width <= page_width * 0.72
    ]
    if len(candidates) < 8:
        return {
            "repeated_anchor_column_count": 0,
            "text_flow_column_geometry": False,
            "table_like": False,
        }

    repeated_clusters = _repeated_anchor_clusters(page_width, candidates)
    repeated_anchor_column_count = _selected_repeated_anchor_column_count(page_width, repeated_clusters)
    table_like = _bboxes_look_like_table_grid(candidates, page_width)
    text_flow_column_geometry = repeated_anchor_column_count >= 2 and (
        not table_like or _anchor_clusters_look_like_text_flows(repeated_clusters, page_width)
    )
    return {
        "repeated_anchor_column_count": repeated_anchor_column_count,
        "text_flow_column_geometry": text_flow_column_geometry,
        "table_like": table_like,
    }


def _repeated_anchor_clusters(page_width: float, bboxes: list[Any]) -> list[tuple[float, list[Any]]]:
    tolerance = max(12.0, page_width * 0.03)
    clusters: list[list[Any]] = []
    centers: list[float] = []
    for bbox in sorted(bboxes, key=lambda item: item.x0):
        if not clusters or abs(bbox.x0 - centers[-1]) > tolerance:
            clusters.append([bbox])
            centers.append(bbox.x0)
            continue
        clusters[-1].append(bbox)
        centers[-1] = sum(item.x0 for item in clusters[-1]) / len(clusters[-1])

    min_items = max(4, round(len(bboxes) * 0.12))
    return [(centers[index], cluster) for index, cluster in enumerate(clusters) if len(cluster) >= min_items]


def _selected_repeated_anchor_column_count(page_width: float, clusters: list[tuple[float, list[Any]]]) -> int:
    for column_count in range(min(3, len(clusters)), 1, -1):
        min_separation = page_width * (0.16 if column_count >= 3 else 0.25)
        for selected in combinations(clusters, column_count):
            centers = [center for center, _cluster in selected]
            groups = [cluster for _center, cluster in selected]
            if any(centers[index + 1] - centers[index] < min_separation for index in range(len(centers) - 1)):
                continue
            if any(
                _bbox_group_vertical_overlap(groups[index], groups[index + 1]) < 0.2
                for index in range(len(groups) - 1)
            ):
                continue
            return column_count
    return 0


def _anchor_clusters_look_like_text_flows(clusters: list[tuple[float, list[Any]]], page_width: float) -> bool:
    min_median_width = page_width * 0.08
    for _center, cluster in clusters:
        widths = [bbox.width for bbox in cluster if bbox.width > 0]
        if widths and median(widths) >= min_median_width:
            return True
    return False


def _bboxes_look_like_table_grid(bboxes: list[Any], page_width: float) -> bool:
    heights = [bbox.height for bbox in bboxes if bbox.height > 0]
    y_tolerance = max(4.0, median(heights) * 0.8) if heights else 8.0
    rows = _bbox_row_clusters(bboxes, tolerance=y_tolerance)
    if len(rows) < 3:
        return False
    multi_cell_rows = [row for row in rows if len(row) >= 3]
    if len(multi_cell_rows) < 3 or len(multi_cell_rows) / len(rows) < 0.5:
        return False

    repeated_x_clusters = _numeric_position_clusters(
        [_bbox_center_x(bbox) for row in multi_cell_rows for bbox in row],
        tolerance=page_width * 0.04,
    )
    return len(repeated_x_clusters) >= 3


def _bbox_row_clusters(bboxes: list[Any], tolerance: float) -> list[list[Any]]:
    rows: list[list[Any]] = []
    row_centers: list[float] = []
    for bbox in sorted(bboxes, key=_bbox_center_y):
        center = _bbox_center_y(bbox)
        matched = False
        for index, row_center in enumerate(row_centers):
            if abs(center - row_center) <= tolerance:
                rows[index].append(bbox)
                row_centers[index] = sum(_bbox_center_y(item) for item in rows[index]) / len(rows[index])
                matched = True
                break
        if not matched:
            rows.append([bbox])
            row_centers.append(center)
    return rows


def _numeric_position_clusters(values: list[float], tolerance: float) -> list[list[float]]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or abs(value - clusters[-1][-1]) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return clusters


def _bbox_center_x(bbox: Any) -> float:
    return (bbox.x0 + bbox.x1) / 2


def _bbox_center_y(bbox: Any) -> float:
    return (bbox.y0 + bbox.y1) / 2


def _page_has_column_geometry(page_width: float, bboxes: list[Any]) -> bool:
    return bool(_page_reading_order_geometry_profile(page_width, bboxes)["text_flow_column_geometry"])


def _bbox_group_vertical_overlap(left: list[Any], right: list[Any]) -> float:
    left_y0 = min(bbox.y0 for bbox in left)
    left_y1 = max(bbox.y1 for bbox in left)
    right_y0 = min(bbox.y0 for bbox in right)
    right_y1 = max(bbox.y1 for bbox in right)
    overlap = max(0.0, min(left_y1, right_y1) - max(left_y0, right_y0))
    denominator = max(1.0, min(left_y1 - left_y0, right_y1 - right_y0))
    return overlap / denominator


def _risk_level(score: float) -> str:
    if score >= 0.35:
        return "high"
    if score >= 0.15:
        return "medium"
    return "low"


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
        "ocr_fallback_counts": _sum_case_values(cases, "ocr_fallback"),
        "total_ocr_fallback_applied_pages": sum(int(case["ocr_fallback_applied_page_count"]) for case in cases),
        "total_ocr_text_elements": sum(int(case["ocr_text_count"]) for case in cases),
        "total_image_only_candidate_pages": sum(int(case["image_only_candidate_page_count"]) for case in cases),
        "total_textless_pages": sum(int(case["textless_page_count"]) for case in cases),
        "html_mode_counts": _sum_case_values(cases, "html_mode"),
        "font_size_scale_counts": _sum_case_values(cases, "font_size_scale"),
        "text_fit_counts": _sum_case_values(cases, "text_fit"),
        "fidelity_background_counts": _sum_case_values(cases, "fidelity_background"),
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
        "mean_reading_order_risk_score": round(
            sum(float(case["reading_order_risk_score"]) for case in cases) / len(cases),
            8,
        ),
        "reading_order_risk_level_counts": _sum_case_values(cases, "reading_order_risk_level"),
        "total_reading_order_column_geometry_pages": sum(
            int(case["reading_order_column_geometry_page_count"]) for case in cases
        ),
        "total_reading_order_visual_yx_column_pages": sum(
            int(case["reading_order_visual_yx_column_page_count"]) for case in cases
        ),
        "total_reading_order_repeated_anchor_pages": sum(
            int(case["reading_order_repeated_anchor_page_count"]) for case in cases
        ),
        "max_reading_order_repeated_anchor_columns": max(
            int(case["reading_order_max_repeated_anchor_columns"]) for case in cases
        ),
        "total_reading_order_table_like_pages": sum(int(case["reading_order_table_like_page_count"]) for case in cases),
        "total_reading_order_table_like_visual_yx_pages": sum(
            int(case["reading_order_table_like_visual_yx_page_count"]) for case in cases
        ),
        "total_reading_order_unlabeled_text_risk_count": sum(
            int(case["reading_order_unlabeled_text_risk_count"]) for case in cases
        ),
        **_summarize_semantic_cases(semantic_cases),
    }


def _write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    fieldnames = [
        "name",
        "max_pages",
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
        "ocr_fallback",
        "ocr_fallback_applied_page_count",
        "ocr_text_count",
        "image_only_candidate_page_count",
        "textless_page_count",
        "html_mode",
        "font_size_scale",
        "text_fit",
        "fidelity_background",
        "structure_evidence_source",
        "structure_evidence_region_count",
        "structure_evidence_matched_element_count",
        "structure_evidence_reordered_page_count",
        "reading_order_risk_score",
        "reading_order_risk_level",
        "reading_order_column_geometry_page_count",
        "reading_order_visual_yx_column_page_count",
        "reading_order_repeated_anchor_page_count",
        "reading_order_max_repeated_anchor_columns",
        "reading_order_table_like_page_count",
        "reading_order_table_like_visual_yx_page_count",
        "reading_order_unlabeled_text_risk_count",
        "reading_order_semantic_ignored_text_ratio",
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


def _calibration_candidates(
    *,
    font_profile: BenchmarkFontProfile,
    html_mode: BenchmarkHtmlMode,
    font_size_scale: BenchmarkFontSizeScale,
    text_fit: BenchmarkTextFit,
    fidelity_background: BenchmarkFidelityBackground,
) -> list[tuple[HtmlMode, FidelityBackgroundChoice, FontProfile, float, HtmlTextFit]]:
    candidates: list[tuple[HtmlMode, FidelityBackgroundChoice, FontProfile, float, HtmlTextFit]] = []
    for mode in _html_mode_candidates(html_mode):
        for background in _fidelity_background_candidates_for_mode(fidelity_background, mode):
            for profile in _font_profile_candidates_for_mode(font_profile, mode):
                for scale in _font_size_scale_candidates_for_mode(font_size_scale, mode):
                    for fit in _text_fit_candidates_for_mode(text_fit, mode):
                        candidates.append((mode, background, profile, scale, fit))
    return candidates


def _candidate_slug(
    html_mode: HtmlMode,
    fidelity_background: FidelityBackgroundChoice,
    font_profile: FontProfile,
    font_size_scale: float,
    text_fit: HtmlTextFit,
) -> str:
    background_part = f"-bg-{fidelity_background}" if html_mode == "fidelity" else ""
    return (
        f"{html_mode}{background_part}-{font_profile}"
        f"-scale-{_font_size_scale_slug(font_size_scale)}-text-fit-{text_fit}"
    )


def _font_profile_candidates_for_mode(
    font_profile: BenchmarkFontProfile,
    html_mode: HtmlMode,
) -> tuple[FontProfile, ...]:
    if font_profile == "auto":
        if html_mode == "fidelity":
            return ("browser-default",)
        return FONT_PROFILE_CANDIDATES
    return (font_profile,)


def _font_size_scale_candidates_for_mode(
    font_size_scale: BenchmarkFontSizeScale,
    html_mode: HtmlMode,
) -> tuple[float, ...]:
    if font_size_scale == "auto" and html_mode == "fidelity":
        return (1.0,)
    return _font_size_scale_candidates(font_size_scale)


def _text_fit_candidates_for_mode(
    text_fit: BenchmarkTextFit,
    html_mode: HtmlMode,
) -> tuple[HtmlTextFit, ...]:
    if text_fit == "auto" and html_mode == "fidelity":
        return ("none",)
    return _text_fit_candidates(text_fit)


def _fidelity_background_candidates_for_mode(
    fidelity_background: BenchmarkFidelityBackground,
    html_mode: HtmlMode,
) -> tuple[FidelityBackgroundChoice, ...]:
    if html_mode != "fidelity":
        return ("none",)
    if fidelity_background == "auto":
        return FIDELITY_BACKGROUND_CANDIDATES
    if fidelity_background not in FIDELITY_BACKGROUND_CANDIDATES:
        raise ValueError(
            "fidelity_background must be one of svg, raster, or auto, "
            f"got {fidelity_background}"
        )
    return (fidelity_background,)


def _case_fidelity_background(fidelity_background: FidelityBackgroundChoice) -> FidelityBackground:
    if fidelity_background == "none":
        return "svg"
    return fidelity_background


def _single_fidelity_background(
    html_mode: BenchmarkHtmlMode,
    fidelity_background: BenchmarkFidelityBackground,
) -> FidelityBackground:
    if html_mode != "fidelity":
        return "svg"
    if fidelity_background == "auto":
        return "svg"
    if fidelity_background not in FIDELITY_BACKGROUND_CANDIDATES:
        raise ValueError(
            "fidelity_background must be one of svg, raster, or auto, "
            f"got {fidelity_background}"
        )
    return fidelity_background


def _fidelity_background_needs_calibration(
    html_mode: BenchmarkHtmlMode,
    fidelity_background: BenchmarkFidelityBackground,
) -> bool:
    return html_mode in {"fidelity", "auto"} and fidelity_background == "auto"


def _html_mode_candidates(html_mode: BenchmarkHtmlMode) -> tuple[HtmlMode, ...]:
    if html_mode == "auto":
        return HTML_MODE_CANDIDATES
    if html_mode not in HTML_MODE_CANDIDATES:
        raise ValueError(f"html_mode must be one of structured, fidelity, or auto, got {html_mode}")
    return (html_mode,)


def _html_mode_request(html_mode: BenchmarkHtmlMode) -> BenchmarkHtmlMode:
    if html_mode == "auto":
        return "auto"
    return _html_mode_candidates(html_mode)[0]


def _max_pages_request(max_pages: int | None) -> int | None:
    if max_pages is None:
        return None
    value = int(max_pages)
    if value <= 0:
        raise ValueError(f"max_pages must be positive, got {max_pages}")
    return value


def _fidelity_background_request(
    fidelity_background: BenchmarkFidelityBackground,
) -> BenchmarkFidelityBackground:
    if fidelity_background == "auto":
        return "auto"
    if fidelity_background not in FIDELITY_BACKGROUND_CANDIDATES:
        raise ValueError(
            "fidelity_background must be one of svg, raster, or auto, "
            f"got {fidelity_background}"
        )
    return fidelity_background


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


def _text_fit_candidates(text_fit: BenchmarkTextFit) -> tuple[HtmlTextFit, ...]:
    if text_fit == "auto":
        return TEXT_FIT_CANDIDATES
    if text_fit not in TEXT_FIT_CANDIDATES:
        raise ValueError(f"text_fit must be one of none, svg, or auto, got {text_fit}")
    return (text_fit,)


def _text_fit_request(text_fit: BenchmarkTextFit) -> BenchmarkTextFit:
    if text_fit == "auto":
        return "auto"
    return _text_fit_candidates(text_fit)[0]


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


def _page_extraction_records(document: DocumentIR) -> list[dict[str, Any]]:
    records = document.metadata.get("page_extraction")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _page_extraction_count(document: DocumentIR, key: str, expected: Any) -> int:
    return sum(1 for record in _page_extraction_records(document) if record.get(key) == expected)


def _ocr_fallback_applied_page_count(document: DocumentIR) -> int:
    return _page_extraction_count(document, "ocr_fallback_status", "applied")


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
