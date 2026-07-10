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
from .html_export import FidelityBackground, HtmlTextFit, export_html, page_replacement_geometries
from .models import BBox, DocumentIR
from .native_pdf import FontProfile, OcrFallback, RasterPolicy, extract_native_pdf_to_ir
from .ocr import normalize_ocr_to_ir
from .pdf_export import print_html_to_pdf
from .pdf_render import SourceKind, render_source
from .quality import compare_source_to_pdf_rendering
from .reading_order_sidecar import reading_order_sidecar_summary, write_reading_order_sidecar
from .reading_order import (
    infer_box_flow_order,
    infer_relation_graph_order,
    infer_successor_consensus_order,
    pairwise_order_disagreement,
    successor_order_disagreement,
    successor_consensus_diagnostics,
)
from .relation_order import relation_edge_candidate_order
from .semantic_quality import compare_reading_order_sidecar_proposal, compare_semantic_reading_order
from .structure_evidence import (
    apply_structure_evidence,
    external_structure_partial_order_for_elements,
    load_structure_json,
)

BenchmarkFontProfile = Literal["browser-default", "local-urw", "auto"]
HtmlMode = Literal["structured", "fidelity"]
BenchmarkHtmlMode = Literal["structured", "fidelity", "auto"]
BenchmarkFontSizeScale = float | Literal["auto"]
BenchmarkTextFit = Literal["none", "svg", "auto"]
BenchmarkFidelityBackground = Literal["svg", "raster", "auto"]
BenchmarkTranslationStress = Literal["off", "pseudo-expand"]
FidelityBackgroundChoice = Literal["none", "svg", "raster"]
FONT_PROFILE_CANDIDATES: tuple[FontProfile, ...] = ("browser-default", "local-urw")
HTML_MODE_CANDIDATES: tuple[HtmlMode, ...] = ("structured", "fidelity")
FONT_SIZE_SCALE_CANDIDATES: tuple[float, ...] = (0.99, 1.0)
TEXT_FIT_CANDIDATES: tuple[HtmlTextFit, ...] = ("none", "svg")
FIDELITY_BACKGROUND_CANDIDATES: tuple[FidelityBackground, ...] = ("svg", "raster")
TRANSLATION_STRESS_POLICIES: tuple[BenchmarkTranslationStress, ...] = ("off", "pseudo-expand")
SEMANTIC_ORDER_CANDIDATES: tuple[str, ...] = (
    "visual_yx",
    "box_flow",
    "relation_graph",
    "structure_relation",
    "successor_consensus",
    "external_structure",
)


def run_benchmark(
    pdfs: list[str | Path] | None,
    out_dir: str | Path,
    dpi: int = 192,
    input_kind: SourceKind = "auto",
    image_dpi: int = 96,
    max_pages: int | None = None,
    page_ranges: str | None = None,
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
    translation_stress: BenchmarkTranslationStress = "off",
) -> dict[str, Any]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    max_pages_request = _max_pages_request(max_pages)
    page_ranges_request = _page_ranges_request(page_ranges)
    page_indices_request = _page_indices_request(page_ranges_request, max_pages_request)
    html_mode_request = _html_mode_request(html_mode)
    font_size_scale_request = _font_size_scale_request(font_size_scale)
    text_fit_request = _text_fit_request(text_fit)
    fidelity_background_request = _fidelity_background_request(fidelity_background)
    translation_stress_request = _translation_stress_request(translation_stress)
    input_sources = [Path(source) for source in pdfs] if pdfs else create_benchmark_fixtures(target / "fixtures")
    structure_json_by_source = _structure_json_by_source(input_sources, structure_jsons or [])

    cases: list[dict[str, Any]] = []
    for source_path in input_sources:
        if (
            font_profile == "auto"
            or html_mode_request == "auto"
            or font_size_scale_request == "auto"
            or text_fit_request == "auto"
            or _fidelity_background_needs_calibration(html_mode_request, fidelity_background_request)
        ):
            cases.append(
                _run_calibrated_case(
                    source_path,
                    target / "cases" / source_path.stem,
                    dpi=dpi,
                    input_kind=input_kind,
                    image_dpi=image_dpi,
                    max_pages=max_pages_request,
                    page_ranges=page_ranges_request,
                    page_indices=page_indices_request,
                    structure_json=structure_json_by_source.get(source_path.resolve()),
                    raster_policy=raster_policy,
                    ocr_fallback=ocr_fallback,
                    ocr_language=ocr_language,
                    ocr_dpi=ocr_dpi,
                    html_mode=html_mode_request,
                    font_size_scale=font_size_scale_request,
                    font_profile=font_profile,
                    text_fit=text_fit_request,
                    fidelity_background=fidelity_background_request,
                    translation_stress=translation_stress_request,
                )
            )
        else:
            case_fidelity_background = _single_fidelity_background(html_mode_request, fidelity_background_request)
            cases.append(
                _run_case(
                    source_path,
                    target / "cases" / source_path.stem,
                    dpi=dpi,
                    input_kind=input_kind,
                    image_dpi=image_dpi,
                    max_pages=max_pages_request,
                    page_ranges=page_ranges_request,
                    page_indices=page_indices_request,
                    structure_json=structure_json_by_source.get(source_path.resolve()),
                    font_profile=font_profile,
                    raster_policy=raster_policy,
                    ocr_fallback=ocr_fallback,
                    ocr_language=ocr_language,
                    ocr_dpi=ocr_dpi,
                    html_mode=html_mode_request,
                    font_size_scale=float(font_size_scale_request),
                    text_fit=text_fit_request,
                    fidelity_background=case_fidelity_background,
                    translation_stress=translation_stress_request,
                )
            )

    summary = _summarize(cases)
    report = {
        "version": 1,
        "dpi": dpi,
        "input_kind": input_kind,
        "image_dpi": image_dpi,
        "max_pages": max_pages_request,
        "page_ranges": page_ranges_request,
        "sampled_page_numbers": [index + 1 for index in page_indices_request] if page_indices_request else None,
        "font_profile": font_profile,
        "raster_policy": raster_policy,
        "ocr_fallback": ocr_fallback,
        "ocr_language": ocr_language,
        "ocr_dpi": ocr_dpi,
        "html_mode": html_mode_request,
        "font_size_scale": font_size_scale_request,
        "text_fit": text_fit_request,
        "fidelity_background": fidelity_background_request,
        "translation_stress": translation_stress_request,
        "case_count": len(cases),
        "summary": summary,
        "cases": cases,
    }
    (target / "benchmark_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_csv(target / "benchmark_summary.csv", cases)
    return report


def run_structure_ab_benchmark(
    pdfs: list[str | Path],
    out_dir: str | Path,
    structure_jsons: list[str | Path],
    dpi: int = 192,
    input_kind: SourceKind = "auto",
    image_dpi: int = 96,
    max_pages: int | None = None,
    page_ranges: str | None = None,
    font_profile: BenchmarkFontProfile = "browser-default",
    raster_policy: RasterPolicy = "dense",
    ocr_fallback: OcrFallback = "image-only",
    ocr_language: str = "eng+chi_sim",
    ocr_dpi: int = 144,
    html_mode: BenchmarkHtmlMode = "structured",
    font_size_scale: BenchmarkFontSizeScale = 1.0,
    text_fit: BenchmarkTextFit = "none",
    fidelity_background: BenchmarkFidelityBackground = "auto",
    translation_stress: BenchmarkTranslationStress = "off",
) -> dict[str, Any]:
    """Run native-only and native-plus-structure benchmarks, then compare them."""

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    native_dir = target / "native-only"
    structure_dir = target / "native-plus-structure"
    native_report = run_benchmark(
        pdfs,
        native_dir,
        dpi=dpi,
        input_kind=input_kind,
        image_dpi=image_dpi,
        max_pages=max_pages,
        page_ranges=page_ranges,
        font_profile=font_profile,
        raster_policy=raster_policy,
        ocr_fallback=ocr_fallback,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
        html_mode=html_mode,
        font_size_scale=font_size_scale,
        text_fit=text_fit,
        fidelity_background=fidelity_background,
        translation_stress=translation_stress,
    )
    structure_report = run_benchmark(
        pdfs,
        structure_dir,
        dpi=dpi,
        input_kind=input_kind,
        image_dpi=image_dpi,
        max_pages=max_pages,
        page_ranges=page_ranges,
        structure_jsons=structure_jsons,
        font_profile=font_profile,
        raster_policy=raster_policy,
        ocr_fallback=ocr_fallback,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
        html_mode=html_mode,
        font_size_scale=font_size_scale,
        text_fit=text_fit,
        fidelity_background=fidelity_background,
        translation_stress=translation_stress,
    )
    comparisons = [
        _structure_ab_case_comparison(native_case, structure_case)
        for native_case, structure_case in zip(native_report["cases"], structure_report["cases"], strict=True)
    ]
    report = {
        "version": 1,
        "dpi": dpi,
        "input_kind": input_kind,
        "image_dpi": image_dpi,
        "max_pages": _max_pages_request(max_pages),
        "page_ranges": _page_ranges_request(page_ranges),
        "sampled_page_numbers": [
            index + 1
            for index in (_page_indices_request(_page_ranges_request(page_ranges), _max_pages_request(max_pages)) or [])
        ]
        or None,
        "font_profile": font_profile,
        "raster_policy": raster_policy,
        "ocr_fallback": ocr_fallback,
        "ocr_language": ocr_language,
        "ocr_dpi": ocr_dpi,
        "html_mode": _html_mode_request(html_mode),
        "font_size_scale": _font_size_scale_request(font_size_scale),
        "text_fit": _text_fit_request(text_fit),
        "fidelity_background": _fidelity_background_request(fidelity_background),
        "translation_stress": _translation_stress_request(translation_stress),
        "case_count": len(comparisons),
        "native_report": str(native_dir / "benchmark_report.json"),
        "native_csv": str(native_dir / "benchmark_summary.csv"),
        "structure_report": str(structure_dir / "benchmark_report.json"),
        "structure_csv": str(structure_dir / "benchmark_summary.csv"),
        "summary": _summarize_structure_ab_comparisons(comparisons),
        "cases": comparisons,
    }
    (target / "structure_ab_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_structure_ab_csv(target / "structure_ab_summary.csv", comparisons)
    return report


def _structure_ab_case_comparison(native_case: dict[str, Any], structure_case: dict[str, Any]) -> dict[str, Any]:
    native_page_needs = _recommendation_count(native_case, "reading_order_candidate_page_recommendation_counts")
    structure_page_needs = _recommendation_count(structure_case, "reading_order_candidate_page_recommendation_counts")
    native_stream_needs = _recommendation_count(native_case, "reading_order_candidate_stream_recommendation_counts")
    structure_stream_needs = _recommendation_count(structure_case, "reading_order_candidate_stream_recommendation_counts")
    native_page_review = _recommendation_count(
        native_case,
        "reading_order_candidate_page_recommendation_counts",
        recommendations=("review-consensus", "review-disagreement"),
    )
    structure_page_review = _recommendation_count(
        structure_case,
        "reading_order_candidate_page_recommendation_counts",
        recommendations=("review-consensus", "review-disagreement"),
    )
    native_stream_review = _recommendation_count(
        native_case,
        "reading_order_candidate_stream_recommendation_counts",
        recommendations=("review-consensus", "review-disagreement"),
    )
    structure_stream_review = _recommendation_count(
        structure_case,
        "reading_order_candidate_stream_recommendation_counts",
        recommendations=("review-consensus", "review-disagreement"),
    )

    return {
        "name": structure_case["name"],
        "source": structure_case.get("source") or structure_case.get("source_path") or structure_case["source_pdf"],
        "source_pdf": structure_case["source_pdf"],
        "native_ir": native_case["ir"],
        "structure_ir": structure_case["ir"],
        "native_visual_similarity": native_case["visual_similarity"],
        "structure_visual_similarity": structure_case["visual_similarity"],
        "visual_similarity_delta": _numeric_delta(structure_case, native_case, "visual_similarity"),
        "native_reading_order_risk_score": native_case["reading_order_risk_score"],
        "structure_reading_order_risk_score": structure_case["reading_order_risk_score"],
        "reading_order_risk_score_delta": _numeric_delta(
            structure_case,
            native_case,
            "reading_order_risk_score",
        ),
        "native_grid_island_element_count": native_case["grid_island_element_count"],
        "structure_grid_island_element_count": structure_case["grid_island_element_count"],
        "grid_island_element_delta": _numeric_delta(structure_case, native_case, "grid_island_element_count"),
        "native_translation_stress_element_count": native_case["translation_stress_element_count"],
        "structure_translation_stress_element_count": structure_case["translation_stress_element_count"],
        "translation_stress_element_delta": _numeric_delta(
            structure_case,
            native_case,
            "translation_stress_element_count",
        ),
        "native_fidelity_replacement_conflict_count": native_case["fidelity_replacement_conflict_count"],
        "structure_fidelity_replacement_conflict_count": structure_case["fidelity_replacement_conflict_count"],
        "fidelity_replacement_conflict_delta": _numeric_delta(
            structure_case,
            native_case,
            "fidelity_replacement_conflict_count",
        ),
        "native_fidelity_replacement_same_stream_conflict_target_count": native_case[
            "fidelity_replacement_same_stream_conflict_target_count"
        ],
        "structure_fidelity_replacement_same_stream_conflict_target_count": structure_case[
            "fidelity_replacement_same_stream_conflict_target_count"
        ],
        "fidelity_replacement_same_stream_conflict_target_delta": _numeric_delta(
            structure_case,
            native_case,
            "fidelity_replacement_same_stream_conflict_target_count",
        ),
        "native_fidelity_replacement_cross_stream_conflict_target_count": native_case[
            "fidelity_replacement_cross_stream_conflict_target_count"
        ],
        "structure_fidelity_replacement_cross_stream_conflict_target_count": structure_case[
            "fidelity_replacement_cross_stream_conflict_target_count"
        ],
        "fidelity_replacement_cross_stream_conflict_target_delta": _numeric_delta(
            structure_case,
            native_case,
            "fidelity_replacement_cross_stream_conflict_target_count",
        ),
        "native_fidelity_replacement_overflow_count": native_case["fidelity_replacement_overflow_count"],
        "structure_fidelity_replacement_overflow_count": structure_case["fidelity_replacement_overflow_count"],
        "fidelity_replacement_overflow_delta": _numeric_delta(
            structure_case,
            native_case,
            "fidelity_replacement_overflow_count",
        ),
        "fidelity_replacement_mean_fit_scale_delta": _numeric_delta(
            structure_case,
            native_case,
            "fidelity_replacement_mean_fit_scale",
        ),
        "structure_evidence_region_count": structure_case["structure_evidence_region_count"],
        "structure_evidence_relation_edge_count": structure_case["structure_evidence_relation_edge_count"],
        "structure_evidence_resolved_relation_edge_count": structure_case[
            "structure_evidence_resolved_relation_edge_count"
        ],
        "structure_evidence_resolved_relation_alias_edge_count": structure_case[
            "structure_evidence_resolved_relation_alias_edge_count"
        ],
        "structure_evidence_resolved_relation_group_edge_count": structure_case[
            "structure_evidence_resolved_relation_group_edge_count"
        ],
        "structure_evidence_relation_group_internal_edge_count": structure_case[
            "structure_evidence_relation_group_internal_edge_count"
        ],
        "structure_evidence_unresolved_relation_edge_count": structure_case[
            "structure_evidence_unresolved_relation_edge_count"
        ],
        "structure_evidence_unresolved_relation_endpoint_count": structure_case[
            "structure_evidence_unresolved_relation_endpoint_count"
        ],
        "structure_evidence_stream_count": structure_case["structure_evidence_stream_count"],
        "structure_evidence_resolved_stream_member_count": structure_case[
            "structure_evidence_resolved_stream_member_count"
        ],
        "structure_evidence_resolved_stream_alias_member_count": structure_case[
            "structure_evidence_resolved_stream_alias_member_count"
        ],
        "structure_evidence_resolved_stream_group_member_ref_count": structure_case[
            "structure_evidence_resolved_stream_group_member_ref_count"
        ],
        "structure_evidence_unresolved_stream_member_ref_count": structure_case[
            "structure_evidence_unresolved_stream_member_ref_count"
        ],
        "structure_evidence_duplicate_stream_member_ref_count": structure_case[
            "structure_evidence_duplicate_stream_member_ref_count"
        ],
        "structure_evidence_stream_conflict_count": structure_case["structure_evidence_stream_conflict_count"],
        "structure_evidence_relation_stream_count": structure_case["structure_evidence_relation_stream_count"],
        "structure_evidence_resolved_relation_stream_member_count": structure_case[
            "structure_evidence_resolved_relation_stream_member_count"
        ],
        "structure_evidence_relation_stream_conflict_count": structure_case[
            "structure_evidence_relation_stream_conflict_count"
        ],
        "structure_evidence_matched_element_count": structure_case["structure_evidence_matched_element_count"],
        "structure_evidence_reordered_page_count": structure_case["structure_evidence_reordered_page_count"],
        "structure_evidence_relation_reordered_page_count": structure_case[
            "structure_evidence_relation_reordered_page_count"
        ],
        "structure_evidence_order_reordered_page_count": structure_case[
            "structure_evidence_order_reordered_page_count"
        ],
        "structure_evidence_order_source_counts": structure_case["structure_evidence_order_source_counts"],
        "native_page_needs_structure_evidence_count": native_page_needs,
        "structure_page_needs_structure_evidence_count": structure_page_needs,
        "page_needs_structure_evidence_delta": structure_page_needs - native_page_needs,
        "native_stream_needs_structure_evidence_count": native_stream_needs,
        "structure_stream_needs_structure_evidence_count": structure_stream_needs,
        "stream_needs_structure_evidence_delta": structure_stream_needs - native_stream_needs,
        "native_page_review_count": native_page_review,
        "structure_page_review_count": structure_page_review,
        "page_review_delta": structure_page_review - native_page_review,
        "native_stream_review_count": native_stream_review,
        "structure_stream_review_count": structure_stream_review,
        "stream_review_delta": structure_stream_review - native_stream_review,
        "box_flow_successor_disagreement_delta": _numeric_delta(
            structure_case,
            native_case,
            "reading_order_box_flow_successor_disagreement_count",
        ),
        "relation_graph_successor_disagreement_delta": _numeric_delta(
            structure_case,
            native_case,
            "reading_order_relation_graph_successor_disagreement_count",
        ),
        "successor_consensus_successor_disagreement_delta": _numeric_delta(
            structure_case,
            native_case,
            "reading_order_successor_consensus_successor_disagreement_count",
        ),
        "semantic_successor_accuracy_delta": _numeric_delta(
            structure_case,
            native_case,
            "semantic_successor_accuracy",
        ),
        "semantic_stream_successor_accuracy_delta": _numeric_delta(
            structure_case,
            native_case,
            "semantic_stream_successor_accuracy",
        ),
        "native_reading_order_proposal_semantic_successor_coverage": native_case[
            "reading_order_proposal_semantic_successor_coverage"
        ],
        "structure_reading_order_proposal_semantic_successor_coverage": structure_case[
            "reading_order_proposal_semantic_successor_coverage"
        ],
        "reading_order_proposal_semantic_successor_coverage_delta": _numeric_delta(
            structure_case,
            native_case,
            "reading_order_proposal_semantic_successor_coverage",
        ),
        "native_reading_order_proposal_semantic_reviewable_successor_coverage": native_case[
            "reading_order_proposal_semantic_reviewable_successor_coverage"
        ],
        "structure_reading_order_proposal_semantic_reviewable_successor_coverage": structure_case[
            "reading_order_proposal_semantic_reviewable_successor_coverage"
        ],
        "reading_order_proposal_semantic_reviewable_successor_coverage_delta": _numeric_delta(
            structure_case,
            native_case,
            "reading_order_proposal_semantic_reviewable_successor_coverage",
        ),
        "native_semantic_relation_missing_text_count": native_case["semantic_relation_missing_text_count"],
        "structure_semantic_relation_missing_text_count": structure_case["semantic_relation_missing_text_count"],
        "semantic_relation_missing_text_delta": _numeric_delta(
            structure_case,
            native_case,
            "semantic_relation_missing_text_count",
        ),
        "native_semantic_stream_missing_text_count": native_case["semantic_stream_missing_text_count"],
        "structure_semantic_stream_missing_text_count": structure_case["semantic_stream_missing_text_count"],
        "semantic_stream_missing_text_delta": _numeric_delta(
            structure_case,
            native_case,
            "semantic_stream_missing_text_count",
        ),
        "native_semantic_stream_assignment_missing_count": native_case[
            "semantic_stream_assignment_missing_count"
        ],
        "structure_semantic_stream_assignment_missing_count": structure_case[
            "semantic_stream_assignment_missing_count"
        ],
        "semantic_stream_assignment_missing_delta": _numeric_delta(
            structure_case,
            native_case,
            "semantic_stream_assignment_missing_count",
        ),
        "semantic_stream_assignment_id_accuracy_delta": _numeric_delta(
            structure_case,
            native_case,
            "semantic_stream_assignment_id_accuracy",
        ),
        "semantic_stream_assignment_type_accuracy_delta": _numeric_delta(
            structure_case,
            native_case,
            "semantic_stream_assignment_type_accuracy",
        ),
        "native_reading_order_stream_type_counts": native_case["reading_order_stream_type_counts"],
        "structure_reading_order_stream_type_counts": structure_case["reading_order_stream_type_counts"],
        "native_reading_order_candidate_page_recommendation_counts": native_case[
            "reading_order_candidate_page_recommendation_counts"
        ],
        "structure_reading_order_candidate_page_recommendation_counts": structure_case[
            "reading_order_candidate_page_recommendation_counts"
        ],
        "native_reading_order_candidate_stream_recommendation_counts": native_case[
            "reading_order_candidate_stream_recommendation_counts"
        ],
        "structure_reading_order_candidate_stream_recommendation_counts": structure_case[
            "reading_order_candidate_stream_recommendation_counts"
        ],
    }


def _summarize_structure_ab_comparisons(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_count": len(comparisons),
        "mean_visual_similarity_delta": _mean_optional(values["visual_similarity_delta"] for values in comparisons),
        "mean_reading_order_risk_score_delta": _mean_optional(
            values["reading_order_risk_score_delta"] for values in comparisons
        ),
        "total_grid_island_element_delta": sum(int(values["grid_island_element_delta"] or 0) for values in comparisons),
        "total_translation_stress_element_delta": sum(
            int(values["translation_stress_element_delta"] or 0) for values in comparisons
        ),
        "total_fidelity_replacement_conflict_delta": sum(
            int(values["fidelity_replacement_conflict_delta"] or 0) for values in comparisons
        ),
        "total_fidelity_replacement_same_stream_conflict_target_delta": sum(
            int(values["fidelity_replacement_same_stream_conflict_target_delta"] or 0) for values in comparisons
        ),
        "total_fidelity_replacement_cross_stream_conflict_target_delta": sum(
            int(values["fidelity_replacement_cross_stream_conflict_target_delta"] or 0) for values in comparisons
        ),
        "total_fidelity_replacement_overflow_delta": sum(
            int(values["fidelity_replacement_overflow_delta"] or 0) for values in comparisons
        ),
        "mean_fidelity_replacement_fit_scale_delta": _mean_optional(
            values["fidelity_replacement_mean_fit_scale_delta"] for values in comparisons
        ),
        "total_structure_evidence_regions": sum(int(values["structure_evidence_region_count"]) for values in comparisons),
        "total_structure_evidence_relation_edges": sum(
            int(values["structure_evidence_relation_edge_count"]) for values in comparisons
        ),
        "total_structure_evidence_resolved_relation_edges": sum(
            int(values["structure_evidence_resolved_relation_edge_count"]) for values in comparisons
        ),
        "total_structure_evidence_resolved_relation_alias_edges": sum(
            int(values["structure_evidence_resolved_relation_alias_edge_count"]) for values in comparisons
        ),
        "total_structure_evidence_resolved_relation_group_edges": sum(
            int(values["structure_evidence_resolved_relation_group_edge_count"]) for values in comparisons
        ),
        "total_structure_evidence_relation_group_internal_edges": sum(
            int(values["structure_evidence_relation_group_internal_edge_count"]) for values in comparisons
        ),
        "total_structure_evidence_unresolved_relation_edges": sum(
            int(values["structure_evidence_unresolved_relation_edge_count"]) for values in comparisons
        ),
        "total_structure_evidence_unresolved_relation_endpoints": sum(
            int(values["structure_evidence_unresolved_relation_endpoint_count"]) for values in comparisons
        ),
        "total_structure_evidence_streams": sum(
            int(values["structure_evidence_stream_count"]) for values in comparisons
        ),
        "total_structure_evidence_resolved_stream_members": sum(
            int(values["structure_evidence_resolved_stream_member_count"]) for values in comparisons
        ),
        "total_structure_evidence_resolved_stream_alias_members": sum(
            int(values["structure_evidence_resolved_stream_alias_member_count"]) for values in comparisons
        ),
        "total_structure_evidence_resolved_stream_group_member_refs": sum(
            int(values["structure_evidence_resolved_stream_group_member_ref_count"]) for values in comparisons
        ),
        "total_structure_evidence_unresolved_stream_member_refs": sum(
            int(values["structure_evidence_unresolved_stream_member_ref_count"]) for values in comparisons
        ),
        "total_structure_evidence_duplicate_stream_member_refs": sum(
            int(values["structure_evidence_duplicate_stream_member_ref_count"]) for values in comparisons
        ),
        "total_structure_evidence_stream_conflicts": sum(
            int(values["structure_evidence_stream_conflict_count"]) for values in comparisons
        ),
        "total_structure_evidence_relation_streams": sum(
            int(values["structure_evidence_relation_stream_count"]) for values in comparisons
        ),
        "total_structure_evidence_resolved_relation_stream_members": sum(
            int(values["structure_evidence_resolved_relation_stream_member_count"]) for values in comparisons
        ),
        "total_structure_evidence_relation_stream_conflicts": sum(
            int(values["structure_evidence_relation_stream_conflict_count"]) for values in comparisons
        ),
        "total_structure_evidence_matched_elements": sum(
            int(values["structure_evidence_matched_element_count"]) for values in comparisons
        ),
        "total_structure_evidence_reordered_pages": sum(
            int(values["structure_evidence_reordered_page_count"]) for values in comparisons
        ),
        "total_structure_evidence_relation_reordered_pages": sum(
            int(values["structure_evidence_relation_reordered_page_count"]) for values in comparisons
        ),
        "total_structure_evidence_order_reordered_pages": sum(
            int(values["structure_evidence_order_reordered_page_count"]) for values in comparisons
        ),
        "structure_evidence_order_source_counts": _sum_count_dicts(
            values["structure_evidence_order_source_counts"] for values in comparisons
        ),
        "total_page_needs_structure_evidence_delta": sum(
            int(values["page_needs_structure_evidence_delta"]) for values in comparisons
        ),
        "total_stream_needs_structure_evidence_delta": sum(
            int(values["stream_needs_structure_evidence_delta"]) for values in comparisons
        ),
        "total_page_review_delta": sum(int(values["page_review_delta"]) for values in comparisons),
        "total_stream_review_delta": sum(int(values["stream_review_delta"]) for values in comparisons),
        "total_semantic_relation_missing_text_delta": sum(
            int(values["semantic_relation_missing_text_delta"] or 0) for values in comparisons
        ),
        "total_semantic_stream_missing_text_delta": sum(
            int(values["semantic_stream_missing_text_delta"] or 0) for values in comparisons
        ),
        "total_semantic_stream_assignment_missing_delta": sum(
            int(values["semantic_stream_assignment_missing_delta"] or 0) for values in comparisons
        ),
        "mean_semantic_stream_assignment_id_accuracy_delta": _mean_optional(
            values["semantic_stream_assignment_id_accuracy_delta"] for values in comparisons
        ),
        "mean_semantic_stream_assignment_type_accuracy_delta": _mean_optional(
            values["semantic_stream_assignment_type_accuracy_delta"] for values in comparisons
        ),
        "mean_reading_order_proposal_semantic_successor_coverage_delta": _mean_optional(
            values["reading_order_proposal_semantic_successor_coverage_delta"] for values in comparisons
        ),
        "mean_reading_order_proposal_semantic_reviewable_successor_coverage_delta": _mean_optional(
            values["reading_order_proposal_semantic_reviewable_successor_coverage_delta"] for values in comparisons
        ),
        "cases_with_stream_assignment_id_improvement": sum(
            1 for values in comparisons if float(values["semantic_stream_assignment_id_accuracy_delta"] or 0.0) > 0
        ),
        "cases_with_stream_assignment_type_improvement": sum(
            1 for values in comparisons if float(values["semantic_stream_assignment_type_accuracy_delta"] or 0.0) > 0
        ),
        "cases_with_relation_missing_text_improvement": sum(
            1 for values in comparisons if int(values["semantic_relation_missing_text_delta"] or 0) < 0
        ),
        "cases_with_stream_missing_text_improvement": sum(
            1 for values in comparisons if int(values["semantic_stream_missing_text_delta"] or 0) < 0
        ),
        "cases_with_stream_assignment_missing_improvement": sum(
            1 for values in comparisons if int(values["semantic_stream_assignment_missing_delta"] or 0) < 0
        ),
        "cases_with_visual_regression": sum(
            1 for values in comparisons if float(values["visual_similarity_delta"] or 0.0) < 0
        ),
        "cases_with_risk_improvement": sum(
            1 for values in comparisons if float(values["reading_order_risk_score_delta"] or 0.0) < 0
        ),
        "cases_with_stream_needs_structure_improvement": sum(
            1 for values in comparisons if int(values["stream_needs_structure_evidence_delta"]) < 0
        ),
    }


def _recommendation_count(
    case: dict[str, Any],
    field: str,
    recommendations: tuple[str, ...] = ("needs-structure-evidence",),
) -> int:
    counts = case.get(field) or {}
    return sum(int(counts.get(recommendation) or 0) for recommendation in recommendations)


def _numeric_delta(left: dict[str, Any], right: dict[str, Any], field: str) -> float | None:
    left_value = left.get(field)
    right_value = right.get(field)
    if left_value is None or right_value is None:
        return None
    return round(float(left_value) - float(right_value), 8)


def _mean_optional(values: Any) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 8)


def _write_structure_ab_csv(path: Path, comparisons: list[dict[str, Any]]) -> None:
    fieldnames = [
        "name",
        "source",
        "source_pdf",
        "native_visual_similarity",
        "structure_visual_similarity",
        "visual_similarity_delta",
        "native_reading_order_risk_score",
        "structure_reading_order_risk_score",
        "reading_order_risk_score_delta",
        "native_grid_island_element_count",
        "structure_grid_island_element_count",
        "grid_island_element_delta",
        "native_translation_stress_element_count",
        "structure_translation_stress_element_count",
        "translation_stress_element_delta",
        "native_fidelity_replacement_conflict_count",
        "structure_fidelity_replacement_conflict_count",
        "fidelity_replacement_conflict_delta",
        "native_fidelity_replacement_same_stream_conflict_target_count",
        "structure_fidelity_replacement_same_stream_conflict_target_count",
        "fidelity_replacement_same_stream_conflict_target_delta",
        "native_fidelity_replacement_cross_stream_conflict_target_count",
        "structure_fidelity_replacement_cross_stream_conflict_target_count",
        "fidelity_replacement_cross_stream_conflict_target_delta",
        "native_fidelity_replacement_overflow_count",
        "structure_fidelity_replacement_overflow_count",
        "fidelity_replacement_overflow_delta",
        "fidelity_replacement_mean_fit_scale_delta",
        "structure_evidence_region_count",
        "structure_evidence_relation_edge_count",
        "structure_evidence_resolved_relation_edge_count",
        "structure_evidence_resolved_relation_alias_edge_count",
        "structure_evidence_resolved_relation_group_edge_count",
        "structure_evidence_relation_group_internal_edge_count",
        "structure_evidence_unresolved_relation_edge_count",
        "structure_evidence_unresolved_relation_endpoint_count",
        "structure_evidence_stream_count",
        "structure_evidence_resolved_stream_member_count",
        "structure_evidence_resolved_stream_alias_member_count",
        "structure_evidence_resolved_stream_group_member_ref_count",
        "structure_evidence_unresolved_stream_member_ref_count",
        "structure_evidence_duplicate_stream_member_ref_count",
        "structure_evidence_stream_conflict_count",
        "structure_evidence_relation_stream_count",
        "structure_evidence_resolved_relation_stream_member_count",
        "structure_evidence_relation_stream_conflict_count",
        "structure_evidence_matched_element_count",
        "structure_evidence_reordered_page_count",
        "structure_evidence_relation_reordered_page_count",
        "structure_evidence_order_reordered_page_count",
        "structure_evidence_order_source_counts",
        "native_page_needs_structure_evidence_count",
        "structure_page_needs_structure_evidence_count",
        "page_needs_structure_evidence_delta",
        "native_stream_needs_structure_evidence_count",
        "structure_stream_needs_structure_evidence_count",
        "stream_needs_structure_evidence_delta",
        "native_page_review_count",
        "structure_page_review_count",
        "page_review_delta",
        "native_stream_review_count",
        "structure_stream_review_count",
        "stream_review_delta",
        "box_flow_successor_disagreement_delta",
        "relation_graph_successor_disagreement_delta",
        "successor_consensus_successor_disagreement_delta",
        "semantic_successor_accuracy_delta",
        "semantic_stream_successor_accuracy_delta",
        "native_reading_order_proposal_semantic_successor_coverage",
        "structure_reading_order_proposal_semantic_successor_coverage",
        "reading_order_proposal_semantic_successor_coverage_delta",
        "native_reading_order_proposal_semantic_reviewable_successor_coverage",
        "structure_reading_order_proposal_semantic_reviewable_successor_coverage",
        "reading_order_proposal_semantic_reviewable_successor_coverage_delta",
        "native_semantic_relation_missing_text_count",
        "structure_semantic_relation_missing_text_count",
        "semantic_relation_missing_text_delta",
        "native_semantic_stream_missing_text_count",
        "structure_semantic_stream_missing_text_count",
        "semantic_stream_missing_text_delta",
        "native_semantic_stream_assignment_missing_count",
        "structure_semantic_stream_assignment_missing_count",
        "semantic_stream_assignment_missing_delta",
        "semantic_stream_assignment_id_accuracy_delta",
        "semantic_stream_assignment_type_accuracy_delta",
        "native_reading_order_stream_type_counts",
        "structure_reading_order_stream_type_counts",
        "native_reading_order_candidate_page_recommendation_counts",
        "structure_reading_order_candidate_page_recommendation_counts",
        "native_reading_order_candidate_stream_recommendation_counts",
        "structure_reading_order_candidate_stream_recommendation_counts",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for comparison in comparisons:
            writer.writerow({field: comparison[field] for field in fieldnames})


def _run_case(
    pdf_path: Path,
    out_dir: Path,
    dpi: int,
    input_kind: SourceKind = "auto",
    image_dpi: int = 96,
    max_pages: int | None = None,
    page_ranges: str | None = None,
    page_indices: tuple[int, ...] | None = None,
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
    translation_stress: BenchmarkTranslationStress = "off",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    start = time.perf_counter()
    rendered = render_source(
        pdf_path,
        out_dir / "pages",
        dpi=dpi,
        include_svg_background=html_mode == "fidelity" and fidelity_background == "svg",
        max_pages=max_pages,
        page_indices=page_indices,
        input_kind=input_kind,
        image_dpi=image_dpi,
    )
    timings["render_seconds"] = _elapsed(start)

    start = time.perf_counter()
    structure_payload = load_structure_json(structure_json) if structure_json is not None else None
    if rendered.source_type == "pdf":
        document = extract_native_pdf_to_ir(
            rendered,
            font_profile=font_profile,
            raster_policy=raster_policy,
            font_size_scale=font_size_scale,
            ocr_fallback=ocr_fallback,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
        )
    else:
        document = normalize_ocr_to_ir(
            rendered,
            structure_payload,
            crop_dir=out_dir / "crops",
            ocr_fallback=ocr_fallback,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
        )
    if structure_json is not None:
        apply_structure_evidence(document, structure_payload or {}, source=_structure_source_name(structure_json))
    annotate_document(document)
    reading_order_sidecar_path = out_dir / "reading-order.sidecar.proposal.json"
    reading_order_sidecar = write_reading_order_sidecar(document, reading_order_sidecar_path)
    reading_order_sidecar_stats = reading_order_sidecar_summary(reading_order_sidecar)
    translation_stress_stats = _apply_translation_stress(document, translation_stress)
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
    quality_report_name = "source_quality_report.json" if rendered.source_type == "image" else "pdf_quality_report.json"
    quality = compare_source_to_pdf_rendering(
        pdf_path,
        exported_pdf,
        out_dir / "quality",
        dpi=dpi,
        max_pages=max_pages,
        expected_page_indices=page_indices,
        expected_input_kind=rendered.source_type,
        image_dpi=image_dpi,
        report_filename=quality_report_name,
    )
    timings["compare_seconds"] = _elapsed(start)

    start = time.perf_counter()
    semantic_quality = compare_semantic_reading_order(
        document,
        pdf_path,
        out_dir / "semantic",
        candidate_orders=_semantic_candidate_orders(document),
    )
    reading_order_proposal_semantic_quality = compare_reading_order_sidecar_proposal(
        document,
        pdf_path,
        out_dir / "semantic",
        reading_order_sidecar,
    )
    timings["semantic_compare_seconds"] = _elapsed(start)

    stats = _document_stats(document)
    replacement_stats = _fidelity_replacement_stats(document, html_mode)
    reading_order_risk = _reading_order_risk_metrics(document, semantic_quality)
    semantic_layer = document.metadata.get("semantic_layer") if isinstance(document.metadata, dict) else {}
    if not isinstance(semantic_layer, dict):
        semantic_layer = {}
    structure_semantic_layer = semantic_layer.get("structure_json")
    if not isinstance(structure_semantic_layer, dict):
        structure_semantic_layer = {}
    max_diff_ratio = float(quality["max_diff_ratio"])
    mean_diff_ratio = float(quality["mean_diff_ratio"])
    p95_diff_ratio = float(quality["p95_diff_ratio"])
    similarity = round(max(0.0, 1.0 - max_diff_ratio), 8)
    total_seconds = round(sum(timings.values()), 6)
    return {
        "name": pdf_path.stem,
        "source": str(pdf_path),
        "source_pdf": str(pdf_path),
        "source_path": str(pdf_path),
        "source_type": rendered.source_type,
        "input_kind": input_kind,
        "image_dpi": image_dpi if rendered.source_type == "image" else None,
        "max_pages": max_pages,
        "page_ranges": page_ranges,
        "sampled_page_numbers": [index + 1 for index in page_indices] if page_indices else None,
        "ir": str(ir_path),
        "html": str(html_path),
        "exported_pdf": str(exported_pdf),
        "quality_report": str(out_dir / "quality" / quality_report_name),
        "semantic_report": str(out_dir / "semantic" / "semantic_quality_report.json"),
        "reading_order_sidecar_proposal": str(reading_order_sidecar_path),
        "reading_order_sidecar_proposal_semantic_report": str(
            out_dir / "semantic" / "reading_order_sidecar_proposal_quality_report.json"
        ),
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
        "mixed_table_column_flow_element_count": stats["mixed_table_column_flow_element_count"],
        "grid_island_element_count": stats["grid_island_element_count"],
        "table_row_major_element_count": stats["table_row_major_element_count"],
        "spatial_graph_element_count": stats["spatial_graph_element_count"],
        "box_flow_element_count": stats["box_flow_element_count"],
        "successor_consensus_arbitration_element_count": stats[
            "successor_consensus_arbitration_element_count"
        ],
        "recursive_xy_cut_element_count": stats["recursive_xy_cut_element_count"],
        "reading_order_artifact_element_count": stats["reading_order_artifact_element_count"],
        "reading_order_artifact_counts": stats["reading_order_artifact_counts"],
        "reading_order_footnote_element_count": stats["reading_order_footnote_element_count"],
        "reading_order_sidebar_element_count": stats["reading_order_sidebar_element_count"],
        "reading_order_sidebar_counts": stats["reading_order_sidebar_counts"],
        "reading_order_stream_element_count": stats["reading_order_stream_element_count"],
        "reading_order_stream_count": stats["reading_order_stream_count"],
        "reading_order_stream_type_counts": stats["reading_order_stream_type_counts"],
        "reading_order_stream_id_counts": stats["reading_order_stream_id_counts"],
        "reading_order_proposal_stream_count": reading_order_sidecar_stats["stream_count"],
        "reading_order_proposal_member_count": reading_order_sidecar_stats["member_count"],
        "reading_order_proposal_successor_edge_count": reading_order_sidecar_stats["successor_edge_count"],
        "reading_order_proposal_review_successor_edge_count": reading_order_sidecar_stats[
            "review_successor_edge_count"
        ],
        "reading_order_proposal_review_transition_count": reading_order_sidecar_stats["review_transition_count"],
        "reading_order_proposal_stream_type_counts": reading_order_sidecar_stats["stream_type_counts"],
        "reading_order_proposal_stream_origin_counts": reading_order_sidecar_stats["stream_origin_counts"],
        "reading_order_caption_element_count": stats["reading_order_caption_element_count"],
        "reading_order_caption_counts": stats["reading_order_caption_counts"],
        "reading_order_caption_targeted_element_count": stats[
            "reading_order_caption_targeted_element_count"
        ],
        "reading_order_caption_orphan_element_count": stats["reading_order_caption_orphan_element_count"],
        "reading_order_caption_target_coverage_ratio": stats[
            "reading_order_caption_target_coverage_ratio"
        ],
        "reading_order_caption_target_counts": stats["reading_order_caption_target_counts"],
        "reading_order_strategy_counts": stats["reading_order_strategy_counts"],
        "reading_order_confidence_element_count": stats["reading_order_confidence_element_count"],
        "reading_order_mean_confidence": stats["reading_order_mean_confidence"],
        "reading_order_low_confidence_element_count": stats["reading_order_low_confidence_element_count"],
        "reading_order_evidence_counts": stats["reading_order_evidence_counts"],
        "reading_order_box_flow_pair_count": stats["reading_order_box_flow_pair_count"],
        "reading_order_box_flow_disagreement_pair_count": stats["reading_order_box_flow_disagreement_pair_count"],
        "reading_order_box_flow_disagreement_ratio": stats["reading_order_box_flow_disagreement_ratio"],
        "reading_order_box_flow_disagreement_page_count": stats["reading_order_box_flow_disagreement_page_count"],
        "reading_order_box_flow_successor_edge_count": stats["reading_order_box_flow_successor_edge_count"],
        "reading_order_box_flow_successor_disagreement_count": stats[
            "reading_order_box_flow_successor_disagreement_count"
        ],
        "reading_order_box_flow_successor_disagreement_ratio": stats[
            "reading_order_box_flow_successor_disagreement_ratio"
        ],
        "reading_order_box_flow_successor_disagreement_page_count": stats[
            "reading_order_box_flow_successor_disagreement_page_count"
        ],
        "reading_order_relation_graph_pair_count": stats["reading_order_relation_graph_pair_count"],
        "reading_order_relation_graph_disagreement_pair_count": stats[
            "reading_order_relation_graph_disagreement_pair_count"
        ],
        "reading_order_relation_graph_disagreement_ratio": stats[
            "reading_order_relation_graph_disagreement_ratio"
        ],
        "reading_order_relation_graph_disagreement_page_count": stats[
            "reading_order_relation_graph_disagreement_page_count"
        ],
        "reading_order_relation_graph_successor_edge_count": stats[
            "reading_order_relation_graph_successor_edge_count"
        ],
        "reading_order_relation_graph_successor_disagreement_count": stats[
            "reading_order_relation_graph_successor_disagreement_count"
        ],
        "reading_order_relation_graph_successor_disagreement_ratio": stats[
            "reading_order_relation_graph_successor_disagreement_ratio"
        ],
        "reading_order_relation_graph_successor_disagreement_page_count": stats[
            "reading_order_relation_graph_successor_disagreement_page_count"
        ],
        "reading_order_successor_consensus_pair_count": stats["reading_order_successor_consensus_pair_count"],
        "reading_order_successor_consensus_disagreement_pair_count": stats[
            "reading_order_successor_consensus_disagreement_pair_count"
        ],
        "reading_order_successor_consensus_disagreement_ratio": stats[
            "reading_order_successor_consensus_disagreement_ratio"
        ],
        "reading_order_successor_consensus_disagreement_page_count": stats[
            "reading_order_successor_consensus_disagreement_page_count"
        ],
        "reading_order_successor_consensus_successor_edge_count": stats[
            "reading_order_successor_consensus_successor_edge_count"
        ],
        "reading_order_successor_consensus_successor_disagreement_count": stats[
            "reading_order_successor_consensus_successor_disagreement_count"
        ],
        "reading_order_successor_consensus_successor_disagreement_ratio": stats[
            "reading_order_successor_consensus_successor_disagreement_ratio"
        ],
        "reading_order_successor_consensus_successor_disagreement_page_count": stats[
            "reading_order_successor_consensus_successor_disagreement_page_count"
        ],
        "reading_order_successor_consensus_candidate_page_count": stats[
            "reading_order_successor_consensus_candidate_page_count"
        ],
        "reading_order_successor_consensus_mean_candidate_count": stats[
            "reading_order_successor_consensus_mean_candidate_count"
        ],
        "reading_order_successor_consensus_candidate_edge_count": stats[
            "reading_order_successor_consensus_candidate_edge_count"
        ],
        "reading_order_successor_consensus_unique_edge_count": stats[
            "reading_order_successor_consensus_unique_edge_count"
        ],
        "reading_order_successor_consensus_selected_edge_count": stats[
            "reading_order_successor_consensus_selected_edge_count"
        ],
        "reading_order_successor_consensus_selected_edge_vote_count": stats[
            "reading_order_successor_consensus_selected_edge_vote_count"
        ],
        "reading_order_successor_consensus_selected_edge_support_ratio": stats[
            "reading_order_successor_consensus_selected_edge_support_ratio"
        ],
        "reading_order_successor_consensus_selected_edge_coverage_ratio": stats[
            "reading_order_successor_consensus_selected_edge_coverage_ratio"
        ],
        "reading_order_successor_consensus_conflicted_edge_count": stats[
            "reading_order_successor_consensus_conflicted_edge_count"
        ],
        "reading_order_successor_consensus_conflicted_edge_ratio": stats[
            "reading_order_successor_consensus_conflicted_edge_ratio"
        ],
        "reading_order_successor_consensus_high_agreement_page_count": stats[
            "reading_order_successor_consensus_high_agreement_page_count"
        ],
        "reading_order_successor_consensus_medium_agreement_page_count": stats[
            "reading_order_successor_consensus_medium_agreement_page_count"
        ],
        "reading_order_successor_consensus_low_agreement_page_count": stats[
            "reading_order_successor_consensus_low_agreement_page_count"
        ],
        "reading_order_successor_consensus_unavailable_page_count": stats[
            "reading_order_successor_consensus_unavailable_page_count"
        ],
        "reading_order_candidate_page_diagnostics": stats["reading_order_candidate_page_diagnostics"],
        "reading_order_candidate_page_recommendation_counts": stats[
            "reading_order_candidate_page_recommendation_counts"
        ],
        "reading_order_candidate_stream_diagnostics": stats["reading_order_candidate_stream_diagnostics"],
        "reading_order_candidate_stream_count": stats["reading_order_candidate_stream_count"],
        "reading_order_candidate_stream_recommendation_counts": stats[
            "reading_order_candidate_stream_recommendation_counts"
        ],
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
        "translation_stress": translation_stress,
        "translation_stress_element_count": translation_stress_stats["translation_stress_element_count"],
        "translation_stress_source_char_count": translation_stress_stats["translation_stress_source_char_count"],
        "translation_stress_translated_char_count": translation_stress_stats[
            "translation_stress_translated_char_count"
        ],
        "translation_stress_char_expansion_ratio": translation_stress_stats[
            "translation_stress_char_expansion_ratio"
        ],
        "fidelity_replacement_element_count": replacement_stats["fidelity_replacement_element_count"],
        "fidelity_replacement_overflow_count": replacement_stats["fidelity_replacement_overflow_count"],
        "fidelity_replacement_conflict_count": replacement_stats["fidelity_replacement_conflict_count"],
        "fidelity_replacement_conflict_target_count": replacement_stats[
            "fidelity_replacement_conflict_target_count"
        ],
        "fidelity_replacement_same_stream_conflict_target_count": replacement_stats[
            "fidelity_replacement_same_stream_conflict_target_count"
        ],
        "fidelity_replacement_cross_stream_conflict_target_count": replacement_stats[
            "fidelity_replacement_cross_stream_conflict_target_count"
        ],
        "fidelity_replacement_min_fit_scale": replacement_stats["fidelity_replacement_min_fit_scale"],
        "fidelity_replacement_mean_fit_scale": replacement_stats["fidelity_replacement_mean_fit_scale"],
        "fidelity_replacement_policy_counts": replacement_stats["fidelity_replacement_policy_counts"],
        "fidelity_replacement_conflict_target_stream_type_counts": replacement_stats[
            "fidelity_replacement_conflict_target_stream_type_counts"
        ],
        "fidelity_replacement_conflict_target_stream_id_counts": replacement_stats[
            "fidelity_replacement_conflict_target_stream_id_counts"
        ],
        "fidelity_replacement_conflict_stream_type_pair_counts": replacement_stats[
            "fidelity_replacement_conflict_stream_type_pair_counts"
        ],
        "fidelity_replacement_conflict_stream_id_pair_counts": replacement_stats[
            "fidelity_replacement_conflict_stream_id_pair_counts"
        ],
        "fidelity_replacement_stream_diagnostics": replacement_stats[
            "fidelity_replacement_stream_diagnostics"
        ],
        "fidelity_replacement_stream_type_counts": replacement_stats[
            "fidelity_replacement_stream_type_counts"
        ],
        "fidelity_replacement_stream_type_overflow_counts": replacement_stats[
            "fidelity_replacement_stream_type_overflow_counts"
        ],
        "fidelity_replacement_stream_type_conflict_counts": replacement_stats[
            "fidelity_replacement_stream_type_conflict_counts"
        ],
        "fidelity_replacement_stream_id_counts": replacement_stats["fidelity_replacement_stream_id_counts"],
        "fidelity_replacement_stream_id_overflow_counts": replacement_stats[
            "fidelity_replacement_stream_id_overflow_counts"
        ],
        "fidelity_replacement_stream_id_conflict_counts": replacement_stats[
            "fidelity_replacement_stream_id_conflict_counts"
        ],
        "structure_evidence_source": stats["structure_evidence_source"],
        "structure_evidence_region_count": stats["structure_evidence_region_count"],
        "structure_evidence_relation_edge_count": stats["structure_evidence_relation_edge_count"],
        "structure_evidence_resolved_relation_edge_count": stats[
            "structure_evidence_resolved_relation_edge_count"
        ],
        "structure_evidence_resolved_relation_alias_edge_count": stats[
            "structure_evidence_resolved_relation_alias_edge_count"
        ],
        "structure_evidence_resolved_relation_group_edge_count": stats[
            "structure_evidence_resolved_relation_group_edge_count"
        ],
        "structure_evidence_relation_group_internal_edge_count": stats[
            "structure_evidence_relation_group_internal_edge_count"
        ],
        "structure_evidence_unresolved_relation_edge_count": stats[
            "structure_evidence_unresolved_relation_edge_count"
        ],
        "structure_evidence_unresolved_relation_endpoint_count": stats[
            "structure_evidence_unresolved_relation_endpoint_count"
        ],
        "structure_evidence_stream_count": stats["structure_evidence_stream_count"],
        "structure_evidence_resolved_stream_member_count": stats[
            "structure_evidence_resolved_stream_member_count"
        ],
        "structure_evidence_resolved_stream_alias_member_count": stats[
            "structure_evidence_resolved_stream_alias_member_count"
        ],
        "structure_evidence_resolved_stream_group_member_ref_count": stats[
            "structure_evidence_resolved_stream_group_member_ref_count"
        ],
        "structure_evidence_unresolved_stream_member_ref_count": stats[
            "structure_evidence_unresolved_stream_member_ref_count"
        ],
        "structure_evidence_duplicate_stream_member_ref_count": stats[
            "structure_evidence_duplicate_stream_member_ref_count"
        ],
        "structure_evidence_stream_conflict_count": stats["structure_evidence_stream_conflict_count"],
        "structure_evidence_relation_stream_count": stats["structure_evidence_relation_stream_count"],
        "structure_evidence_resolved_relation_stream_member_count": stats[
            "structure_evidence_resolved_relation_stream_member_count"
        ],
        "structure_evidence_relation_stream_conflict_count": stats[
            "structure_evidence_relation_stream_conflict_count"
        ],
        "structure_evidence_matched_element_count": stats["structure_evidence_matched_element_count"],
        "structure_evidence_reordered_page_count": stats["structure_evidence_reordered_page_count"],
        "structure_evidence_relation_reordered_page_count": stats[
            "structure_evidence_relation_reordered_page_count"
        ],
        "structure_evidence_order_reordered_page_count": stats[
            "structure_evidence_order_reordered_page_count"
        ],
        "structure_evidence_order_source_counts": stats["structure_evidence_order_source_counts"],
        "semantic_layer_driver": semantic_layer.get("driver"),
        "semantic_layer_payload_kind": semantic_layer.get("payload_kind"),
        "semantic_layer_structure_role": structure_semantic_layer.get("role"),
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
        **_reading_order_proposal_semantic_case_metrics(reading_order_proposal_semantic_quality),
        "total_seconds": total_seconds,
        "timings": timings,
    }


def _run_calibrated_case(
    pdf_path: Path,
    out_dir: Path,
    dpi: int,
    input_kind: SourceKind,
    image_dpi: int,
    max_pages: int | None,
    page_ranges: str | None,
    page_indices: tuple[int, ...] | None,
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
    translation_stress: BenchmarkTranslationStress,
) -> dict[str, Any]:
    start = time.perf_counter()
    candidates = [
        _run_case(
            pdf_path,
            out_dir / _candidate_slug(mode, background, profile, scale, fit),
            dpi=dpi,
            input_kind=input_kind,
            image_dpi=image_dpi,
            max_pages=max_pages,
            page_ranges=page_ranges,
            page_indices=page_indices,
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
            translation_stress=translation_stress,
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
            "translation_stress": candidate["translation_stress"],
            "translation_stress_element_count": candidate["translation_stress_element_count"],
            "fidelity_replacement_element_count": candidate["fidelity_replacement_element_count"],
            "fidelity_replacement_conflict_count": candidate["fidelity_replacement_conflict_count"],
            "fidelity_replacement_mean_fit_scale": candidate["fidelity_replacement_mean_fit_scale"],
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
    reading_order_artifact_counts = Counter(
        str(element.metadata.get("reading_order_artifact_type"))
        for element in text_elements
        if element.metadata.get("reading_order_artifact_type")
    )
    reading_order_sidebar_counts = Counter(
        str(element.metadata.get("reading_order_sidebar_type"))
        for element in text_elements
        if element.metadata.get("reading_order_scope") == "sidebar"
    )
    reading_order_caption_counts = Counter(
        str(element.metadata.get("reading_order_caption_type"))
        for element in text_elements
        if element.metadata.get("reading_order_caption_type")
    )
    reading_order_caption_target_counts = Counter(
        str(element.metadata.get("reading_order_caption_target_kind"))
        for element in text_elements
        if element.metadata.get("reading_order_caption_target_kind")
    )
    reading_order_stream_element_count = 0
    reading_order_stream_count = 0
    reading_order_stream_type_counts: Counter[str] = Counter()
    reading_order_stream_id_counts: Counter[str] = Counter()
    for page in document.pages:
        page_stream_types: dict[str, str] = {}
        for element in page.elements:
            if not element.source_text.strip():
                continue
            stream_id = str(element.metadata.get("reading_order_stream_id") or "").strip()
            if not stream_id:
                continue
            stream_type = str(element.metadata.get("reading_order_stream_type") or "unknown").strip()
            reading_order_stream_element_count += 1
            page_stream_types.setdefault(stream_id, stream_type or "unknown")
        reading_order_stream_count += len(page_stream_types)
        reading_order_stream_type_counts.update(page_stream_types.values())
        reading_order_stream_id_counts.update(page_stream_types.keys())
    reading_order_caption_element_count = sum(
        1 for element in text_elements if element.metadata.get("reading_order_caption_type")
    )
    reading_order_caption_targeted_element_count = sum(
        1
        for element in text_elements
        if element.metadata.get("reading_order_caption_type")
        and element.metadata.get("reading_order_caption_target_id")
    )
    reading_order_confidences = _reading_order_confidences(text_elements)
    reading_order_evidence_counts = Counter(
        evidence
        for element in text_elements
        for evidence in _reading_order_evidence(element)
    )
    box_flow_diagnostics = _reading_order_box_flow_diagnostics(document)
    relation_graph_diagnostics = _reading_order_relation_graph_diagnostics(document)
    successor_consensus_diagnostics = _reading_order_successor_consensus_diagnostics(document)
    candidate_page_diagnostics = _reading_order_candidate_page_diagnostics(document)
    candidate_page_recommendation_counts = Counter(
        str(page_diagnostic.get("recommendation") or "unknown")
        for page_diagnostic in candidate_page_diagnostics
    )
    candidate_stream_diagnostics = _reading_order_candidate_stream_diagnostics(document)
    candidate_stream_recommendation_counts = Counter(
        str(stream_diagnostic.get("recommendation") or "unknown")
        for stream_diagnostic in candidate_stream_diagnostics
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
            if element.metadata.get("reading_order_strategy")
            in {
                "column-flow-v1",
                "marginal-aware-column-flow-v1",
                "sidebar-aware-column-flow-v1",
                "marginal-sidebar-aware-column-flow-v1",
                "footnote-aware-column-flow-v1",
                "marginal-footnote-aware-column-flow-v1",
                "sidebar-footnote-aware-column-flow-v1",
                "marginal-sidebar-footnote-aware-column-flow-v1",
            }
        ),
        "mixed_table_column_flow_element_count": sum(
            1
            for element in text_elements
            if element.metadata.get("reading_order_strategy")
            in {
                "mixed-table-column-flow-v1",
                "marginal-aware-mixed-table-column-flow-v1",
                "sidebar-aware-mixed-table-column-flow-v1",
                "marginal-sidebar-aware-mixed-table-column-flow-v1",
                "footnote-aware-mixed-table-column-flow-v1",
                "marginal-footnote-aware-mixed-table-column-flow-v1",
                "sidebar-footnote-aware-mixed-table-column-flow-v1",
                "marginal-sidebar-footnote-aware-mixed-table-column-flow-v1",
            }
        ),
        "grid_island_element_count": sum(
            1
            for element in text_elements
            if element.metadata.get("reading_order_stream_type") == "grid-island"
        ),
        "table_row_major_element_count": sum(
            1
            for element in text_elements
            if element.metadata.get("reading_order_strategy")
            in {
                "table-row-major-v1",
                "marginal-aware-table-row-major-v1",
            }
        ),
        "spatial_graph_element_count": sum(
            1
            for element in text_elements
            if element.metadata.get("reading_order_strategy")
            in {
                "spatial-graph-v1",
                "marginal-aware-spatial-graph-v1",
            }
        ),
        "box_flow_element_count": sum(
            1
            for element in text_elements
            if element.metadata.get("reading_order_strategy")
            in {
                "box-flow-v1",
                "marginal-aware-box-flow-v1",
            }
        ),
        "successor_consensus_arbitration_element_count": sum(
            1
            for element in text_elements
            if element.metadata.get("reading_order_strategy")
            in {
                "successor-consensus-arbitration-v1",
                "marginal-aware-successor-consensus-arbitration-v1",
            }
        ),
        "recursive_xy_cut_element_count": sum(
            1
            for element in text_elements
            if element.metadata.get("reading_order_strategy") == "recursive-xy-cut-v1"
        ),
        "reading_order_artifact_element_count": sum(
            1 for element in text_elements if element.metadata.get("reading_order_scope") == "page-artifact"
        ),
        "reading_order_artifact_counts": dict(sorted(reading_order_artifact_counts.items())),
        "reading_order_footnote_element_count": sum(
            1 for element in text_elements if element.metadata.get("reading_order_scope") == "footnote"
        ),
        "reading_order_sidebar_element_count": sum(
            1 for element in text_elements if element.metadata.get("reading_order_scope") == "sidebar"
        ),
        "reading_order_sidebar_counts": dict(sorted(reading_order_sidebar_counts.items())),
        "reading_order_stream_element_count": reading_order_stream_element_count,
        "reading_order_stream_count": reading_order_stream_count,
        "reading_order_stream_type_counts": dict(sorted(reading_order_stream_type_counts.items())),
        "reading_order_stream_id_counts": dict(sorted(reading_order_stream_id_counts.items())),
        "reading_order_caption_element_count": reading_order_caption_element_count,
        "reading_order_caption_counts": dict(sorted(reading_order_caption_counts.items())),
        "reading_order_caption_targeted_element_count": reading_order_caption_targeted_element_count,
        "reading_order_caption_orphan_element_count": max(
            0,
            reading_order_caption_element_count - reading_order_caption_targeted_element_count,
        ),
        "reading_order_caption_target_coverage_ratio": round(
            reading_order_caption_targeted_element_count / reading_order_caption_element_count,
            8,
        )
        if reading_order_caption_element_count
        else 0.0,
        "reading_order_caption_target_counts": dict(sorted(reading_order_caption_target_counts.items())),
        "reading_order_strategy_counts": dict(sorted(reading_order_strategy_counts.items())),
        "reading_order_confidence_element_count": len(reading_order_confidences),
        "reading_order_mean_confidence": round(
            sum(reading_order_confidences) / len(reading_order_confidences),
            8,
        )
        if reading_order_confidences
        else 0.0,
        "reading_order_low_confidence_element_count": sum(
            1 for confidence in reading_order_confidences if confidence < 0.65
        ),
        "reading_order_evidence_counts": dict(sorted(reading_order_evidence_counts.items())),
        **box_flow_diagnostics,
        **relation_graph_diagnostics,
        **successor_consensus_diagnostics,
        "reading_order_candidate_page_diagnostics": candidate_page_diagnostics,
        "reading_order_candidate_page_recommendation_counts": dict(
            sorted(candidate_page_recommendation_counts.items())
        ),
        "reading_order_candidate_stream_diagnostics": candidate_stream_diagnostics,
        "reading_order_candidate_stream_count": len(candidate_stream_diagnostics),
        "reading_order_candidate_stream_recommendation_counts": dict(
            sorted(candidate_stream_recommendation_counts.items())
        ),
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
        "structure_evidence_relation_edge_count": int(structure_evidence.get("relation_edge_count") or 0),
        "structure_evidence_resolved_relation_edge_count": int(
            structure_evidence.get("resolved_relation_edge_count") or 0
        ),
        "structure_evidence_resolved_relation_alias_edge_count": int(
            structure_evidence.get("resolved_relation_alias_edge_count") or 0
        ),
        "structure_evidence_resolved_relation_group_edge_count": int(
            structure_evidence.get("resolved_relation_group_edge_count") or 0
        ),
        "structure_evidence_relation_group_internal_edge_count": int(
            structure_evidence.get("relation_group_internal_edge_count") or 0
        ),
        "structure_evidence_unresolved_relation_edge_count": int(
            structure_evidence.get("unresolved_relation_edge_count") or 0
        ),
        "structure_evidence_unresolved_relation_endpoint_count": int(
            structure_evidence.get("unresolved_relation_endpoint_count") or 0
        ),
        "structure_evidence_stream_count": int(structure_evidence.get("stream_count") or 0),
        "structure_evidence_resolved_stream_member_count": int(
            structure_evidence.get("resolved_stream_member_count") or 0
        ),
        "structure_evidence_resolved_stream_alias_member_count": int(
            structure_evidence.get("resolved_stream_alias_member_count") or 0
        ),
        "structure_evidence_resolved_stream_group_member_ref_count": int(
            structure_evidence.get("resolved_stream_group_member_ref_count") or 0
        ),
        "structure_evidence_unresolved_stream_member_ref_count": int(
            structure_evidence.get("unresolved_stream_member_ref_count") or 0
        ),
        "structure_evidence_duplicate_stream_member_ref_count": int(
            structure_evidence.get("duplicate_stream_member_ref_count") or 0
        ),
        "structure_evidence_stream_conflict_count": int(structure_evidence.get("stream_conflict_count") or 0),
        "structure_evidence_relation_stream_count": int(structure_evidence.get("relation_stream_count") or 0),
        "structure_evidence_resolved_relation_stream_member_count": int(
            structure_evidence.get("resolved_relation_stream_member_count") or 0
        ),
        "structure_evidence_relation_stream_conflict_count": int(
            structure_evidence.get("relation_stream_conflict_count") or 0
        ),
        "structure_evidence_matched_element_count": int(structure_evidence.get("matched_element_count") or 0),
        "structure_evidence_reordered_page_count": int(structure_evidence.get("reordered_page_count") or 0),
        "structure_evidence_relation_reordered_page_count": int(
            structure_evidence.get("relation_reordered_page_count") or 0
        ),
        "structure_evidence_order_reordered_page_count": int(
            structure_evidence.get("order_reordered_page_count") or 0
        ),
        "structure_evidence_order_source_counts": dict(
            sorted((structure_evidence.get("order_source_counts") or {}).items())
        ),
    }


def _fidelity_replacement_stats(document: DocumentIR, html_mode: HtmlMode) -> dict[str, Any]:
    if html_mode != "fidelity":
        return _empty_fidelity_replacement_stats()

    geometries: list[dict[str, object]] = []
    stream_groups: dict[tuple[int, str, str], dict[str, Any]] = {}
    stream_type_counts: Counter[str] = Counter()
    stream_type_overflow_counts: Counter[str] = Counter()
    stream_type_conflict_counts: Counter[str] = Counter()
    stream_id_counts: Counter[str] = Counter()
    stream_id_overflow_counts: Counter[str] = Counter()
    stream_id_conflict_counts: Counter[str] = Counter()
    conflict_target_stream_type_counts: Counter[str] = Counter()
    conflict_target_stream_id_counts: Counter[str] = Counter()
    conflict_stream_type_pair_counts: Counter[str] = Counter()
    conflict_stream_id_pair_counts: Counter[str] = Counter()
    same_stream_conflict_target_count = 0
    cross_stream_conflict_target_count = 0
    for page in document.pages:
        page_geometries = page_replacement_geometries(page, "fidelity")
        elements_by_id = {element.id: element for element in page.elements}
        for element_id, geometry in page_geometries.items():
            element = elements_by_id.get(element_id)
            stream_id, stream_type = _replacement_stream_metadata(element)
            overflow = bool(geometry.get("overflow"))
            conflict = bool(geometry.get("conflict"))
            conflict_target_count = _replacement_conflict_target_count(geometry)
            fit_scale = geometry.get("fit_scale")
            fit_scale_float = float(fit_scale) if fit_scale is not None else None

            geometries.append(geometry)
            stream_type_counts[stream_type] += 1
            stream_id_counts[stream_id] += 1
            if overflow:
                stream_type_overflow_counts[stream_type] += 1
                stream_id_overflow_counts[stream_id] += 1
            if conflict:
                stream_type_conflict_counts[stream_type] += 1
                stream_id_conflict_counts[stream_id] += 1

            group = stream_groups.setdefault(
                (page.page_index, stream_id, stream_type),
                {
                    "page_index": page.page_index,
                    "stream_id": stream_id,
                    "stream_type": stream_type,
                    "element_count": 0,
                    "overflow_count": 0,
                    "conflict_count": 0,
                    "conflict_target_count": 0,
                    "same_stream_conflict_target_count": 0,
                    "cross_stream_conflict_target_count": 0,
                    "conflict_target_stream_type_counts": Counter(),
                    "conflict_target_stream_id_counts": Counter(),
                    "conflict_stream_type_pair_counts": Counter(),
                    "conflict_stream_id_pair_counts": Counter(),
                    "fit_scales": [],
                },
            )
            group["element_count"] += 1
            group["overflow_count"] += int(overflow)
            group["conflict_count"] += int(conflict)
            group["conflict_target_count"] += conflict_target_count
            if fit_scale_float is not None:
                group["fit_scales"].append(fit_scale_float)

            for target_id in _replacement_conflict_ids(geometry):
                target = elements_by_id.get(target_id)
                target_stream_id, target_stream_type = _replacement_stream_metadata(target)
                type_pair = f"{stream_type}=>{target_stream_type}"
                id_pair = f"{stream_id}=>{target_stream_id}"
                same_stream = stream_id == target_stream_id and stream_type == target_stream_type
                same_stream_conflict_target_count += int(same_stream)
                cross_stream_conflict_target_count += int(not same_stream)
                conflict_target_stream_type_counts[target_stream_type] += 1
                conflict_target_stream_id_counts[target_stream_id] += 1
                conflict_stream_type_pair_counts[type_pair] += 1
                conflict_stream_id_pair_counts[id_pair] += 1
                group["same_stream_conflict_target_count"] += int(same_stream)
                group["cross_stream_conflict_target_count"] += int(not same_stream)
                group["conflict_target_stream_type_counts"][target_stream_type] += 1
                group["conflict_target_stream_id_counts"][target_stream_id] += 1
                group["conflict_stream_type_pair_counts"][type_pair] += 1
                group["conflict_stream_id_pair_counts"][id_pair] += 1

    fit_scales = [float(geometry["fit_scale"]) for geometry in geometries if geometry.get("fit_scale") is not None]
    policy_counts = Counter(str(geometry.get("policy") or "unknown") for geometry in geometries)
    conflict_target_count = sum(_replacement_conflict_target_count(geometry) for geometry in geometries)
    return {
        "fidelity_replacement_element_count": len(geometries),
        "fidelity_replacement_overflow_count": sum(1 for geometry in geometries if bool(geometry.get("overflow"))),
        "fidelity_replacement_conflict_count": sum(1 for geometry in geometries if bool(geometry.get("conflict"))),
        "fidelity_replacement_conflict_target_count": conflict_target_count,
        "fidelity_replacement_same_stream_conflict_target_count": same_stream_conflict_target_count,
        "fidelity_replacement_cross_stream_conflict_target_count": cross_stream_conflict_target_count,
        "fidelity_replacement_min_fit_scale": round(min(fit_scales), 8) if fit_scales else None,
        "fidelity_replacement_mean_fit_scale": round(sum(fit_scales) / len(fit_scales), 8) if fit_scales else None,
        "fidelity_replacement_policy_counts": dict(sorted(policy_counts.items())),
        "fidelity_replacement_conflict_target_stream_type_counts": dict(
            sorted(conflict_target_stream_type_counts.items())
        ),
        "fidelity_replacement_conflict_target_stream_id_counts": dict(
            sorted(conflict_target_stream_id_counts.items())
        ),
        "fidelity_replacement_conflict_stream_type_pair_counts": dict(
            sorted(conflict_stream_type_pair_counts.items())
        ),
        "fidelity_replacement_conflict_stream_id_pair_counts": dict(sorted(conflict_stream_id_pair_counts.items())),
        "fidelity_replacement_stream_diagnostics": _replacement_stream_diagnostics(stream_groups),
        "fidelity_replacement_stream_type_counts": dict(sorted(stream_type_counts.items())),
        "fidelity_replacement_stream_type_overflow_counts": dict(sorted(stream_type_overflow_counts.items())),
        "fidelity_replacement_stream_type_conflict_counts": dict(sorted(stream_type_conflict_counts.items())),
        "fidelity_replacement_stream_id_counts": dict(sorted(stream_id_counts.items())),
        "fidelity_replacement_stream_id_overflow_counts": dict(sorted(stream_id_overflow_counts.items())),
        "fidelity_replacement_stream_id_conflict_counts": dict(sorted(stream_id_conflict_counts.items())),
    }


def _empty_fidelity_replacement_stats() -> dict[str, Any]:
    return {
        "fidelity_replacement_element_count": 0,
        "fidelity_replacement_overflow_count": 0,
        "fidelity_replacement_conflict_count": 0,
        "fidelity_replacement_conflict_target_count": 0,
        "fidelity_replacement_same_stream_conflict_target_count": 0,
        "fidelity_replacement_cross_stream_conflict_target_count": 0,
        "fidelity_replacement_min_fit_scale": None,
        "fidelity_replacement_mean_fit_scale": None,
        "fidelity_replacement_policy_counts": {},
        "fidelity_replacement_conflict_target_stream_type_counts": {},
        "fidelity_replacement_conflict_target_stream_id_counts": {},
        "fidelity_replacement_conflict_stream_type_pair_counts": {},
        "fidelity_replacement_conflict_stream_id_pair_counts": {},
        "fidelity_replacement_stream_diagnostics": [],
        "fidelity_replacement_stream_type_counts": {},
        "fidelity_replacement_stream_type_overflow_counts": {},
        "fidelity_replacement_stream_type_conflict_counts": {},
        "fidelity_replacement_stream_id_counts": {},
        "fidelity_replacement_stream_id_overflow_counts": {},
        "fidelity_replacement_stream_id_conflict_counts": {},
    }


def _replacement_stream_metadata(element: ElementIR | None) -> tuple[str, str]:
    metadata = element.metadata if element is not None else {}
    stream_id = str(metadata.get("reading_order_stream_id") or "unknown").strip() or "unknown"
    stream_type = str(metadata.get("reading_order_stream_type") or "unknown").strip() or "unknown"
    return stream_id, stream_type


def _replacement_conflict_target_count(geometry: dict[str, object]) -> int:
    return len(_replacement_conflict_ids(geometry))


def _replacement_conflict_ids(geometry: dict[str, object]) -> list[str]:
    conflict_ids = geometry.get("conflict_ids")
    if not isinstance(conflict_ids, list):
        return []
    return [str(conflict_id) for conflict_id in conflict_ids]


def _replacement_stream_diagnostics(stream_groups: dict[tuple[int, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for group in sorted(stream_groups.values(), key=lambda item: (item["page_index"], item["stream_type"], item["stream_id"])):
        fit_scales = [float(value) for value in group["fit_scales"]]
        diagnostics.append(
            {
                "page_index": int(group["page_index"]),
                "stream_id": str(group["stream_id"]),
                "stream_type": str(group["stream_type"]),
                "element_count": int(group["element_count"]),
                "overflow_count": int(group["overflow_count"]),
                "conflict_count": int(group["conflict_count"]),
                "conflict_target_count": int(group["conflict_target_count"]),
                "same_stream_conflict_target_count": int(group["same_stream_conflict_target_count"]),
                "cross_stream_conflict_target_count": int(group["cross_stream_conflict_target_count"]),
                "conflict_target_stream_type_counts": dict(
                    sorted(group["conflict_target_stream_type_counts"].items())
                ),
                "conflict_target_stream_id_counts": dict(sorted(group["conflict_target_stream_id_counts"].items())),
                "conflict_stream_type_pair_counts": dict(sorted(group["conflict_stream_type_pair_counts"].items())),
                "conflict_stream_id_pair_counts": dict(sorted(group["conflict_stream_id_pair_counts"].items())),
                "min_fit_scale": round(min(fit_scales), 8) if fit_scales else None,
                "mean_fit_scale": round(sum(fit_scales) / len(fit_scales), 8) if fit_scales else None,
            }
        )
    return diagnostics


def _apply_translation_stress(document: DocumentIR, policy: BenchmarkTranslationStress) -> dict[str, Any]:
    policy = _translation_stress_request(policy)
    if policy == "off":
        document.metadata["translation_stress"] = _empty_translation_stress_stats(policy)
        return _empty_translation_stress_stats(policy)

    element_count = 0
    source_chars = 0
    translated_chars = 0
    for page in document.pages:
        for element in page.elements:
            if not _is_translation_stress_target(element):
                continue
            source_text = element.source_text.strip()
            translated_text = _pseudo_expanded_translation(source_text)
            element.translated_text = translated_text
            element_count += 1
            source_chars += len(source_text)
            translated_chars += len(translated_text)

    stats = {
        "translation_stress": policy,
        "translation_stress_element_count": element_count,
        "translation_stress_source_char_count": source_chars,
        "translation_stress_translated_char_count": translated_chars,
        "translation_stress_char_expansion_ratio": round(translated_chars / max(source_chars, 1), 8)
        if element_count
        else None,
    }
    document.metadata["translation_stress"] = stats
    return stats


def _empty_translation_stress_stats(policy: BenchmarkTranslationStress) -> dict[str, Any]:
    return {
        "translation_stress": policy,
        "translation_stress_element_count": 0,
        "translation_stress_source_char_count": 0,
        "translation_stress_translated_char_count": 0,
        "translation_stress_char_expansion_ratio": None,
    }


def _is_translation_stress_target(element: ElementIR) -> bool:
    if element.type not in {"text", "title", "table", "figure", "formula"}:
        return False
    text = element.source_text.strip()
    if len(text) < 2:
        return False
    if element.metadata.get("reading_order_artifact_type") == "page_number":
        return False
    return any(character.isalpha() for character in text)


def _pseudo_expanded_translation(text: str) -> str:
    lines = text.splitlines() or [text]
    return "\n".join(_pseudo_expanded_translation_line(line) for line in lines)


def _pseudo_expanded_translation_line(line: str) -> str:
    normalized = " ".join(line.split())
    if not normalized:
        return normalized
    if len(normalized) <= 18:
        return f"{normalized} translated layout expansion"
    prefix = normalized[: max(12, min(48, len(normalized)))]
    return f"{normalized} translated layout expansion {prefix}"


def _reading_order_confidences(elements: list[Any]) -> list[float]:
    confidences: list[float] = []
    for element in elements:
        try:
            confidences.append(float(element.metadata.get("reading_order_confidence") or 0.0))
        except (TypeError, ValueError):
            confidences.append(0.0)
    return confidences


def _reading_order_evidence(element: Any) -> list[str]:
    evidence = element.metadata.get("reading_order_evidence")
    if not isinstance(evidence, list):
        return []
    return [str(item) for item in evidence if str(item).strip()]


def _reading_order_box_flow_diagnostics(document: DocumentIR) -> dict[str, Any]:
    return _reading_order_candidate_diagnostics(
        document,
        prefix="box_flow",
        candidate_order_fn=lambda text_elements, page: infer_box_flow_order(
            [element.bbox_pdf for element in text_elements],
            page_width=page.width_pt,
            page_height=page.height_pt,
            boxes_flow=-0.5,
        ),
    )


def _reading_order_relation_graph_diagnostics(document: DocumentIR) -> dict[str, Any]:
    return _reading_order_candidate_diagnostics(
        document,
        prefix="relation_graph",
        candidate_order_fn=lambda text_elements, page: infer_relation_graph_order(
            [element.bbox_pdf for element in text_elements],
            page_width=page.width_pt,
            page_height=page.height_pt,
        ),
    )


def _reading_order_successor_consensus_diagnostics(document: DocumentIR) -> dict[str, Any]:
    diagnostics = _reading_order_candidate_diagnostics(
        document,
        prefix="successor_consensus",
        candidate_order_fn=lambda text_elements, page: _successor_consensus_candidate_order(text_elements, page),
    )
    diagnostics.update(_successor_consensus_support_diagnostics(document))
    return diagnostics


def _successor_consensus_support_diagnostics(document: DocumentIR) -> dict[str, Any]:
    page_count = 0
    candidate_count_total = 0
    candidate_edge_count = 0
    unique_edge_count = 0
    selected_edge_count = 0
    selected_edge_vote_count = 0
    selected_edge_support_denominator = 0
    selected_edge_coverage_denominator = 0
    conflicted_edge_count = 0
    high_agreement_pages = 0
    medium_agreement_pages = 0
    low_agreement_pages = 0
    unavailable_pages = 0
    for page in document.pages:
        text_elements = [element for element in page.elements if element.source_text.strip()]
        if len(text_elements) < 2:
            continue
        source_candidates = _candidate_index_orders(text_elements, page, include_successor_consensus=False)
        page_diagnostics = successor_consensus_diagnostics(
            source_candidates,
            item_count=len(text_elements),
            base_order=_selected_candidate_order(text_elements),
        )
        page_count += 1
        candidate_count_total += page_diagnostics.candidate_count
        candidate_edge_count += page_diagnostics.candidate_edge_count
        unique_edge_count += page_diagnostics.unique_edge_count
        selected_edge_count += page_diagnostics.selected_edge_count
        selected_edge_vote_count += page_diagnostics.selected_edge_vote_count
        selected_edge_support_denominator += page_diagnostics.selected_edge_count * page_diagnostics.candidate_count
        selected_edge_coverage_denominator += max(page_diagnostics.item_count - 1, 1)
        conflicted_edge_count += page_diagnostics.conflicted_edge_count
        if page_diagnostics.agreement_level == "high":
            high_agreement_pages += 1
        elif page_diagnostics.agreement_level == "medium":
            medium_agreement_pages += 1
        elif page_diagnostics.agreement_level == "low":
            low_agreement_pages += 1
        else:
            unavailable_pages += 1

    return {
        "reading_order_successor_consensus_candidate_page_count": page_count,
        "reading_order_successor_consensus_mean_candidate_count": round(candidate_count_total / page_count, 8)
        if page_count
        else 0.0,
        "reading_order_successor_consensus_candidate_edge_count": candidate_edge_count,
        "reading_order_successor_consensus_unique_edge_count": unique_edge_count,
        "reading_order_successor_consensus_selected_edge_count": selected_edge_count,
        "reading_order_successor_consensus_selected_edge_vote_count": selected_edge_vote_count,
        "reading_order_successor_consensus_selected_edge_support_ratio": round(
            selected_edge_vote_count / max(selected_edge_support_denominator, 1),
            8,
        )
        if selected_edge_count
        else 0.0,
        "reading_order_successor_consensus_selected_edge_coverage_ratio": round(
            selected_edge_count / max(selected_edge_coverage_denominator, 1),
            8,
        ),
        "reading_order_successor_consensus_conflicted_edge_count": conflicted_edge_count,
        "reading_order_successor_consensus_conflicted_edge_ratio": round(
            conflicted_edge_count / max(unique_edge_count, 1),
            8,
        )
        if unique_edge_count
        else 0.0,
        "reading_order_successor_consensus_high_agreement_page_count": high_agreement_pages,
        "reading_order_successor_consensus_medium_agreement_page_count": medium_agreement_pages,
        "reading_order_successor_consensus_low_agreement_page_count": low_agreement_pages,
        "reading_order_successor_consensus_unavailable_page_count": unavailable_pages,
    }


def _reading_order_candidate_page_diagnostics(document: DocumentIR) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for page in document.pages:
        text_elements = [element for element in page.elements if element.source_text.strip()]
        if len(text_elements) < 2:
            continue
        reference_order = _selected_candidate_order(text_elements)
        source_candidates = _candidate_index_orders(text_elements, page, include_successor_consensus=False)
        consensus = successor_consensus_diagnostics(
            source_candidates,
            item_count=len(text_elements),
            base_order=reference_order,
        )
        pairwise = pairwise_order_disagreement(reference_order, consensus.ordered_indices)
        successor = successor_order_disagreement(reference_order, consensus.ordered_indices)
        recommendation, reason = _reading_order_candidate_page_recommendation(consensus, successor)
        diagnostics.append(
            {
                "page_index": page.page_index,
                "text_element_count": len(text_elements),
                "candidate_names": sorted(source_candidates),
                "candidate_count": consensus.candidate_count,
                "agreement_level": consensus.agreement_level,
                "selected_edge_support_ratio": consensus.selected_edge_support_ratio,
                "selected_edge_coverage_ratio": consensus.selected_edge_coverage_ratio,
                "conflicted_edge_ratio": consensus.conflicted_edge_ratio,
                "consensus_pair_count": pairwise.pair_count,
                "consensus_disagreement_pair_count": pairwise.disagreement_count,
                "consensus_disagreement_ratio": pairwise.disagreement_ratio,
                "consensus_successor_edge_count": successor.edge_count,
                "consensus_successor_disagreement_count": successor.disagreement_count,
                "consensus_successor_disagreement_ratio": successor.disagreement_ratio,
                "recommendation": recommendation,
                "reason": reason,
            }
        )
    return diagnostics


def _reading_order_candidate_stream_diagnostics(document: DocumentIR) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for page in document.pages:
        text_elements = [element for element in page.elements if element.source_text.strip()]
        streams: dict[tuple[str, str], list[Any]] = {}
        for element in text_elements:
            stream_id = str(element.metadata.get("reading_order_stream_id") or "").strip()
            if not stream_id:
                continue
            stream_type = str(element.metadata.get("reading_order_stream_type") or "unknown").strip() or "unknown"
            streams.setdefault((stream_id, stream_type), []).append(element)

        for (stream_id, stream_type), stream_elements in sorted(streams.items()):
            if len(stream_elements) < 2:
                continue
            reference_order = _selected_candidate_order(stream_elements)
            source_candidates = _candidate_index_orders(stream_elements, page, include_successor_consensus=False)
            consensus = successor_consensus_diagnostics(
                source_candidates,
                item_count=len(stream_elements),
                base_order=reference_order,
            )
            pairwise = pairwise_order_disagreement(reference_order, consensus.ordered_indices)
            successor = successor_order_disagreement(reference_order, consensus.ordered_indices)
            recommendation, reason = _reading_order_candidate_page_recommendation(consensus, successor)
            diagnostics.append(
                {
                    "page_index": page.page_index,
                    "stream_id": stream_id,
                    "stream_type": stream_type,
                    "text_element_count": len(stream_elements),
                    "candidate_names": sorted(source_candidates),
                    "candidate_count": consensus.candidate_count,
                    "agreement_level": consensus.agreement_level,
                    "selected_edge_support_ratio": consensus.selected_edge_support_ratio,
                    "selected_edge_coverage_ratio": consensus.selected_edge_coverage_ratio,
                    "conflicted_edge_ratio": consensus.conflicted_edge_ratio,
                    "consensus_pair_count": pairwise.pair_count,
                    "consensus_disagreement_pair_count": pairwise.disagreement_count,
                    "consensus_disagreement_ratio": pairwise.disagreement_ratio,
                    "consensus_successor_edge_count": successor.edge_count,
                    "consensus_successor_disagreement_count": successor.disagreement_count,
                    "consensus_successor_disagreement_ratio": successor.disagreement_ratio,
                    "recommendation": recommendation,
                    "reason": reason,
                }
            )
    return diagnostics


def _reading_order_candidate_page_recommendation(
    consensus: Any,
    successor_disagreement: Any,
) -> tuple[str, str]:
    if consensus.agreement_level == "unavailable":
        return "unavailable", "not enough candidate successor evidence"
    if successor_disagreement.disagreement_count == 0:
        if consensus.agreement_level in {"high", "medium"}:
            return "keep-selected-supported", "selected order agrees with candidate successor consensus"
        return "keep-selected-low-consensus", "selected order agrees but candidate consensus is weak"
    if consensus.agreement_level == "high":
        return "review-consensus", "high-support consensus disagrees with selected order"
    if consensus.agreement_level == "medium":
        return "review-disagreement", "medium-support consensus disagrees with selected order"
    return "needs-structure-evidence", "candidate consensus is weak or conflicted"


def _semantic_candidate_orders(document: DocumentIR) -> dict[str, dict[int, list[str]]]:
    orders: dict[str, dict[int, list[str]]] = {candidate_name: {} for candidate_name in SEMANTIC_ORDER_CANDIDATES}
    for page in document.pages:
        text_elements = [element for element in page.elements if element.source_text.strip()]
        if len(text_elements) < 2:
            continue
        candidates = _candidate_index_orders(text_elements, page, include_successor_consensus=True)
        for candidate_name, candidate_order in candidates.items():
            orders[candidate_name][page.page_index] = [str(text_elements[index].id) for index in candidate_order]
    return orders


def _candidate_index_orders(
    text_elements: list[Any],
    page: Any,
    *,
    include_successor_consensus: bool,
) -> dict[str, list[int]]:
    bboxes = [element.bbox_pdf for element in text_elements]
    candidates = {
        "visual_yx": sorted(
            range(len(text_elements)),
            key=lambda index: (bboxes[index].y0, bboxes[index].x0, index),
        ),
        "box_flow": infer_box_flow_order(
            bboxes,
            page_width=page.width_pt,
            page_height=page.height_pt,
            boxes_flow=-0.5,
        ),
        "relation_graph": infer_relation_graph_order(
            bboxes,
            page_width=page.width_pt,
            page_height=page.height_pt,
        ),
    }
    external_structure_order = _external_structure_candidate_order(text_elements)
    if external_structure_order:
        candidates["external_structure"] = external_structure_order
    structure_relation_order = _structure_relation_candidate_order(text_elements, page)
    if structure_relation_order:
        candidates["structure_relation"] = structure_relation_order
    if include_successor_consensus:
        candidates["successor_consensus"] = _successor_consensus_candidate_order(text_elements, page)
    return candidates


def _successor_consensus_candidate_order(text_elements: list[Any], page: Any) -> list[int]:
    source_candidates = _candidate_index_orders(text_elements, page, include_successor_consensus=False)
    return infer_successor_consensus_order(
        source_candidates,
        item_count=len(text_elements),
        base_order=_selected_candidate_order(text_elements),
    )


def _selected_candidate_order(text_elements: list[Any]) -> list[int]:
    return [
        index
        for index, _element in sorted(
            enumerate(text_elements),
            key=lambda item: (
                item[1].reading_order,
                item[1].bbox_pdf.y0,
                item[1].bbox_pdf.x0,
                item[0],
            ),
        )
    ]


def _structure_relation_candidate_order(text_elements: list[Any], page: Any) -> list[int]:
    """Return a structure-aware relation-graph candidate order.

    This candidate is diagnostic-only. It combines page-level scope metadata
    (headers/body/footnotes/sidebars/footers), caption target anchors, and the
    geometry relation graph for the primary body stream.
    """

    if len(text_elements) < 2 or not _has_structure_relation_signal(text_elements):
        return []

    header_indices: list[int] = []
    body_indices: list[int] = []
    footnote_indices: list[int] = []
    sidebar_indices: list[int] = []
    footer_indices: list[int] = []
    for index, element in enumerate(text_elements):
        scope = str(element.metadata.get("reading_order_scope") or "body").strip()
        artifact_type = str(element.metadata.get("reading_order_artifact_type") or "").strip()
        if scope == "page-artifact" and artifact_type == "header":
            header_indices.append(index)
        elif scope == "page-artifact" and artifact_type == "footer":
            footer_indices.append(index)
        elif scope == "footnote":
            footnote_indices.append(index)
        elif scope == "sidebar":
            sidebar_indices.append(index)
        else:
            body_indices.append(index)

    ordered_body = _structure_relation_body_order(text_elements, body_indices, page)
    ordered = [
        *sorted(header_indices, key=lambda index: _visual_candidate_sort_key(text_elements[index], index)),
        *ordered_body,
        *sorted(footnote_indices, key=lambda index: _visual_candidate_sort_key(text_elements[index], index)),
        *sorted(sidebar_indices, key=lambda index: _sidebar_candidate_sort_key(text_elements[index], index)),
        *sorted(footer_indices, key=lambda index: _visual_candidate_sort_key(text_elements[index], index)),
    ]
    if len(ordered) != len(text_elements):
        return []
    return ordered


def _has_structure_relation_signal(text_elements: list[Any]) -> bool:
    for element in text_elements:
        metadata = element.metadata
        scope = str(metadata.get("reading_order_scope") or "body").strip()
        role = _annotation_value(element, "role")
        if scope in {"footnote", "sidebar", "page-artifact"}:
            return True
        if metadata.get("reading_order_caption_target_id"):
            return True
        if metadata.get("reading_order_caption_type"):
            return True
        if role in {"caption", "footnote", "sidebar-text", "running-header", "footer"}:
            return True
    return False


def _structure_relation_body_order(text_elements: list[Any], body_indices: list[int], page: Any) -> list[int]:
    if len(body_indices) < 2:
        return body_indices[:]
    surrogate_bboxes = [
        _structure_relation_bbox(text_elements[index], page_width=page.width_pt, page_height=page.height_pt)
        for index in body_indices
    ]
    local_order = infer_relation_graph_order(
        surrogate_bboxes,
        page_width=page.width_pt,
        page_height=page.height_pt,
    )
    return [body_indices[local_index] for local_index in local_order]


def _structure_relation_bbox(element: Any, *, page_width: float, page_height: float) -> BBox:
    target_bbox = _caption_target_bbox(element)
    if target_bbox is None:
        return element.bbox_pdf

    position = str(element.metadata.get("reading_order_caption_target_position") or "").strip()
    caption_box = element.bbox_pdf
    height = max(caption_box.height, min(max(page_height * 0.018, 6.0), 16.0))
    if position == "caption-below-target":
        y0 = min(page_height - height, target_bbox.y1 + 0.5)
        y1 = min(page_height, y0 + height)
    elif position == "caption-above-target":
        y1 = max(height, target_bbox.y0 - 0.5)
        y0 = max(0.0, y1 - height)
    else:
        y0 = max(0.0, min(page_height - height, caption_box.y0))
        y1 = min(page_height, y0 + height)

    x0 = max(0.0, min(page_width, min(caption_box.x0, target_bbox.x0)))
    x1 = max(x0 + 1.0, min(page_width, max(caption_box.x1, target_bbox.x1)))
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _caption_target_bbox(element: Any) -> BBox | None:
    value = element.metadata.get("reading_order_caption_target_bbox_pdf")
    if value is None:
        return None
    try:
        return BBox.from_any(value)
    except (TypeError, ValueError):
        return None


def _annotation_value(element: Any, key: str) -> str:
    annotation = element.metadata.get("annotation")
    if isinstance(annotation, dict) and annotation.get(key) is not None:
        return str(annotation.get(key) or "").strip()
    return str(element.metadata.get(key) or "").strip()


def _visual_candidate_sort_key(element: Any, index: int) -> tuple[float, float, int]:
    return (element.bbox_pdf.y0, element.bbox_pdf.x0, index)


def _sidebar_candidate_sort_key(element: Any, index: int) -> tuple[str, float, float, int]:
    return (
        str(element.metadata.get("reading_order_sidebar_type") or ""),
        element.bbox_pdf.y0,
        element.bbox_pdf.x0,
        index,
    )


def _external_structure_candidate_order(elements: list[Any]) -> list[int]:
    relation_order = _external_structure_relation_candidate_order(elements)
    if relation_order:
        return relation_order
    return external_structure_partial_order_for_elements(elements)


def _external_structure_relation_candidate_order(elements: list[Any]) -> list[int]:
    id_to_index = {str(element.id): index for index, element in enumerate(elements)}
    successor_edges: list[tuple[int, int]] = []
    precedence_edges: list[tuple[int, int]] = []
    for source_index, element in enumerate(elements):
        for target_id in _metadata_id_list(element, "external_structure_successor_ids"):
            target_index = id_to_index.get(target_id)
            if target_index is not None:
                successor_edges.append((source_index, target_index))
        for target_id in _metadata_id_list(element, "external_structure_precedence_target_ids"):
            target_index = id_to_index.get(target_id)
            if target_index is not None:
                precedence_edges.append((source_index, target_index))

    if not successor_edges and not precedence_edges:
        return []
    return relation_edge_candidate_order(
        item_count=len(elements),
        successor_edges=successor_edges,
        precedence_edges=precedence_edges,
        base_order=_selected_candidate_order(elements),
    )


def _metadata_id_list(element: Any, key: str) -> list[str]:
    value = element.metadata.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _reading_order_candidate_diagnostics(
    document: DocumentIR,
    prefix: str,
    candidate_order_fn: Any,
) -> dict[str, Any]:
    pair_count = 0
    disagreement_pair_count = 0
    disagreement_page_count = 0
    successor_edge_count = 0
    successor_disagreement_count = 0
    successor_disagreement_page_count = 0
    for page in document.pages:
        text_elements = [element for element in page.elements if element.source_text.strip()]
        if len(text_elements) < 2:
            continue
        reference_order = _selected_candidate_order(text_elements)
        candidate_order = candidate_order_fn(text_elements, page)
        disagreement = pairwise_order_disagreement(reference_order, candidate_order)
        successor_disagreement = successor_order_disagreement(reference_order, candidate_order)
        pair_count += disagreement.pair_count
        disagreement_pair_count += disagreement.disagreement_count
        successor_edge_count += successor_disagreement.edge_count
        successor_disagreement_count += successor_disagreement.disagreement_count
        if disagreement.disagreement_count:
            disagreement_page_count += 1
        if successor_disagreement.disagreement_count:
            successor_disagreement_page_count += 1
    return {
        f"reading_order_{prefix}_pair_count": pair_count,
        f"reading_order_{prefix}_disagreement_pair_count": disagreement_pair_count,
        f"reading_order_{prefix}_disagreement_ratio": round(disagreement_pair_count / pair_count, 8)
        if pair_count
        else 0.0,
        f"reading_order_{prefix}_disagreement_page_count": disagreement_page_count,
        f"reading_order_{prefix}_successor_edge_count": successor_edge_count,
        f"reading_order_{prefix}_successor_disagreement_count": successor_disagreement_count,
        f"reading_order_{prefix}_successor_disagreement_ratio": round(
            successor_disagreement_count / successor_edge_count,
            8,
        )
        if successor_edge_count
        else 0.0,
        f"reading_order_{prefix}_successor_disagreement_page_count": successor_disagreement_page_count,
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
    proposal_semantic_cases = [
        case
        for case in cases
        if bool(case["reading_order_proposal_semantic_ground_truth_available"])
    ]
    return {
        "mean_visual_similarity": round(sum(similarities) / len(similarities), 8),
        "min_visual_similarity": round(min(similarities), 8),
        "max_diff_ratio": round(max(diff_ratios), 8),
        "mean_diff_ratio": round(sum(mean_diff_ratios) / len(mean_diff_ratios), 8),
        "p95_diff_ratio": round(_percentile(diff_ratios, 95.0), 8),
        "worst_case": worst_case["name"],
        "worst_page": worst_case["worst_page"],
        "source_type_counts": dict(Counter(str(case.get("source_type") or "pdf") for case in cases)),
        "semantic_layer_driver_counts": _sum_case_values(cases, "semantic_layer_driver"),
        "semantic_layer_payload_kind_counts": _sum_case_values(cases, "semantic_layer_payload_kind"),
        "semantic_layer_structure_role_counts": _sum_case_values(cases, "semantic_layer_structure_role"),
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
        "total_mixed_table_column_flow_elements": sum(
            int(case["mixed_table_column_flow_element_count"]) for case in cases
        ),
        "total_grid_island_elements": sum(int(case["grid_island_element_count"]) for case in cases),
        "total_table_row_major_elements": sum(int(case["table_row_major_element_count"]) for case in cases),
        "total_spatial_graph_elements": sum(int(case["spatial_graph_element_count"]) for case in cases),
        "total_box_flow_elements": sum(int(case["box_flow_element_count"]) for case in cases),
        "total_successor_consensus_arbitration_elements": sum(
            int(case["successor_consensus_arbitration_element_count"]) for case in cases
        ),
        "total_recursive_xy_cut_elements": sum(int(case["recursive_xy_cut_element_count"]) for case in cases),
        "total_reading_order_artifact_elements": sum(int(case["reading_order_artifact_element_count"]) for case in cases),
        "reading_order_artifact_counts": _sum_case_count_dicts(cases, "reading_order_artifact_counts"),
        "total_reading_order_footnote_elements": sum(int(case["reading_order_footnote_element_count"]) for case in cases),
        "total_reading_order_sidebar_elements": sum(int(case["reading_order_sidebar_element_count"]) for case in cases),
        "reading_order_sidebar_counts": _sum_case_count_dicts(cases, "reading_order_sidebar_counts"),
        "total_reading_order_stream_elements": sum(
            int(case["reading_order_stream_element_count"]) for case in cases
        ),
        "total_reading_order_streams": sum(int(case["reading_order_stream_count"]) for case in cases),
        "reading_order_stream_type_counts": _sum_case_count_dicts(cases, "reading_order_stream_type_counts"),
        "reading_order_stream_id_counts": _sum_case_count_dicts(cases, "reading_order_stream_id_counts"),
        "total_reading_order_proposal_streams": sum(
            int(case["reading_order_proposal_stream_count"]) for case in cases
        ),
        "total_reading_order_proposal_members": sum(
            int(case["reading_order_proposal_member_count"]) for case in cases
        ),
        "total_reading_order_proposal_successor_edges": sum(
            int(case["reading_order_proposal_successor_edge_count"]) for case in cases
        ),
        "total_reading_order_proposal_review_successor_edges": sum(
            int(case["reading_order_proposal_review_successor_edge_count"]) for case in cases
        ),
        "total_reading_order_proposal_review_transitions": sum(
            int(case["reading_order_proposal_review_transition_count"]) for case in cases
        ),
        "reading_order_proposal_stream_type_counts": _sum_case_count_dicts(
            cases,
            "reading_order_proposal_stream_type_counts",
        ),
        "reading_order_proposal_stream_origin_counts": _sum_case_count_dicts(
            cases,
            "reading_order_proposal_stream_origin_counts",
        ),
        "reading_order_proposal_semantic_case_count": len(proposal_semantic_cases),
        "total_reading_order_proposal_semantic_expected_successor_edges": sum(
            int(case["reading_order_proposal_semantic_expected_successor_edge_count"])
            for case in proposal_semantic_cases
        ),
        "total_reading_order_proposal_semantic_successor_candidate_edges": sum(
            int(case["reading_order_proposal_semantic_successor_candidate_edge_count"])
            for case in proposal_semantic_cases
        ),
        "total_reading_order_proposal_semantic_successor_labelled_edges": sum(
            int(case["reading_order_proposal_semantic_successor_labelled_edge_count"])
            for case in proposal_semantic_cases
        ),
        "total_reading_order_proposal_semantic_successor_correct_edges": sum(
            int(case["reading_order_proposal_semantic_successor_correct_count"])
            for case in proposal_semantic_cases
        ),
        "reading_order_proposal_semantic_successor_precision": _optional_case_ratio(
            sum(int(case["reading_order_proposal_semantic_successor_correct_count"]) for case in proposal_semantic_cases),
            sum(int(case["reading_order_proposal_semantic_successor_labelled_edge_count"]) for case in proposal_semantic_cases),
        ),
        "reading_order_proposal_semantic_successor_coverage": _optional_case_ratio(
            sum(int(case["reading_order_proposal_semantic_successor_correct_count"]) for case in proposal_semantic_cases),
            sum(int(case["reading_order_proposal_semantic_expected_successor_edge_count"]) for case in proposal_semantic_cases),
        ),
        "total_reading_order_proposal_semantic_review_successor_candidate_edges": sum(
            int(case["reading_order_proposal_semantic_review_successor_candidate_edge_count"])
            for case in proposal_semantic_cases
        ),
        "total_reading_order_proposal_semantic_review_successor_labelled_edges": sum(
            int(case["reading_order_proposal_semantic_review_successor_labelled_edge_count"])
            for case in proposal_semantic_cases
        ),
        "total_reading_order_proposal_semantic_review_successor_correct_edges": sum(
            int(case["reading_order_proposal_semantic_review_successor_correct_count"])
            for case in proposal_semantic_cases
        ),
        "reading_order_proposal_semantic_review_successor_precision": _optional_case_ratio(
            sum(
                int(case["reading_order_proposal_semantic_review_successor_correct_count"])
                for case in proposal_semantic_cases
            ),
            sum(
                int(case["reading_order_proposal_semantic_review_successor_labelled_edge_count"])
                for case in proposal_semantic_cases
            ),
        ),
        "reading_order_proposal_semantic_review_successor_coverage": _optional_case_ratio(
            sum(
                int(case["reading_order_proposal_semantic_review_successor_correct_count"])
                for case in proposal_semantic_cases
            ),
            sum(
                int(case["reading_order_proposal_semantic_expected_successor_edge_count"])
                for case in proposal_semantic_cases
            ),
        ),
        "total_reading_order_proposal_semantic_reviewable_successor_correct_edges": sum(
            int(case["reading_order_proposal_semantic_reviewable_successor_correct_count"])
            for case in proposal_semantic_cases
        ),
        "reading_order_proposal_semantic_reviewable_successor_coverage": _optional_case_ratio(
            sum(
                int(case["reading_order_proposal_semantic_reviewable_successor_correct_count"])
                for case in proposal_semantic_cases
            ),
            sum(
                int(case["reading_order_proposal_semantic_expected_successor_edge_count"])
                for case in proposal_semantic_cases
            ),
        ),
        "total_reading_order_caption_elements": sum(int(case["reading_order_caption_element_count"]) for case in cases),
        "reading_order_caption_counts": _sum_case_count_dicts(cases, "reading_order_caption_counts"),
        "total_reading_order_caption_targeted_elements": sum(
            int(case["reading_order_caption_targeted_element_count"]) for case in cases
        ),
        "total_reading_order_caption_orphan_elements": sum(
            int(case["reading_order_caption_orphan_element_count"]) for case in cases
        ),
        "mean_reading_order_caption_target_coverage_ratio": _ratio_from_case_sums(
            cases,
            numerator_key="reading_order_caption_targeted_element_count",
            denominator_key="reading_order_caption_element_count",
        ),
        "reading_order_caption_target_counts": _sum_case_count_dicts(
            cases,
            "reading_order_caption_target_counts",
        ),
        "reading_order_strategy_counts": _sum_strategy_counts(cases),
        "mean_reading_order_confidence": _weighted_case_mean(
            cases,
            value_key="reading_order_mean_confidence",
            weight_key="reading_order_confidence_element_count",
        ),
        "total_reading_order_low_confidence_elements": sum(
            int(case["reading_order_low_confidence_element_count"]) for case in cases
        ),
        "reading_order_evidence_counts": _sum_case_count_dicts(cases, "reading_order_evidence_counts"),
        "total_reading_order_box_flow_pairs": sum(int(case["reading_order_box_flow_pair_count"]) for case in cases),
        "total_reading_order_box_flow_disagreement_pairs": sum(
            int(case["reading_order_box_flow_disagreement_pair_count"]) for case in cases
        ),
        "mean_reading_order_box_flow_disagreement_ratio": _ratio_from_case_sums(
            cases,
            numerator_key="reading_order_box_flow_disagreement_pair_count",
            denominator_key="reading_order_box_flow_pair_count",
        ),
        "total_reading_order_box_flow_disagreement_pages": sum(
            int(case["reading_order_box_flow_disagreement_page_count"]) for case in cases
        ),
        "total_reading_order_box_flow_successor_edges": sum(
            int(case["reading_order_box_flow_successor_edge_count"]) for case in cases
        ),
        "total_reading_order_box_flow_successor_disagreements": sum(
            int(case["reading_order_box_flow_successor_disagreement_count"]) for case in cases
        ),
        "mean_reading_order_box_flow_successor_disagreement_ratio": _ratio_from_case_sums(
            cases,
            numerator_key="reading_order_box_flow_successor_disagreement_count",
            denominator_key="reading_order_box_flow_successor_edge_count",
        ),
        "total_reading_order_box_flow_successor_disagreement_pages": sum(
            int(case["reading_order_box_flow_successor_disagreement_page_count"]) for case in cases
        ),
        "total_reading_order_relation_graph_pairs": sum(
            int(case["reading_order_relation_graph_pair_count"]) for case in cases
        ),
        "total_reading_order_relation_graph_disagreement_pairs": sum(
            int(case["reading_order_relation_graph_disagreement_pair_count"]) for case in cases
        ),
        "mean_reading_order_relation_graph_disagreement_ratio": _ratio_from_case_sums(
            cases,
            numerator_key="reading_order_relation_graph_disagreement_pair_count",
            denominator_key="reading_order_relation_graph_pair_count",
        ),
        "total_reading_order_relation_graph_disagreement_pages": sum(
            int(case["reading_order_relation_graph_disagreement_page_count"]) for case in cases
        ),
        "total_reading_order_relation_graph_successor_edges": sum(
            int(case["reading_order_relation_graph_successor_edge_count"]) for case in cases
        ),
        "total_reading_order_relation_graph_successor_disagreements": sum(
            int(case["reading_order_relation_graph_successor_disagreement_count"]) for case in cases
        ),
        "mean_reading_order_relation_graph_successor_disagreement_ratio": _ratio_from_case_sums(
            cases,
            numerator_key="reading_order_relation_graph_successor_disagreement_count",
            denominator_key="reading_order_relation_graph_successor_edge_count",
        ),
        "total_reading_order_relation_graph_successor_disagreement_pages": sum(
            int(case["reading_order_relation_graph_successor_disagreement_page_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_pairs": sum(
            int(case["reading_order_successor_consensus_pair_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_disagreement_pairs": sum(
            int(case["reading_order_successor_consensus_disagreement_pair_count"]) for case in cases
        ),
        "mean_reading_order_successor_consensus_disagreement_ratio": _ratio_from_case_sums(
            cases,
            numerator_key="reading_order_successor_consensus_disagreement_pair_count",
            denominator_key="reading_order_successor_consensus_pair_count",
        ),
        "total_reading_order_successor_consensus_disagreement_pages": sum(
            int(case["reading_order_successor_consensus_disagreement_page_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_successor_edges": sum(
            int(case["reading_order_successor_consensus_successor_edge_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_successor_disagreements": sum(
            int(case["reading_order_successor_consensus_successor_disagreement_count"]) for case in cases
        ),
        "mean_reading_order_successor_consensus_successor_disagreement_ratio": _ratio_from_case_sums(
            cases,
            numerator_key="reading_order_successor_consensus_successor_disagreement_count",
            denominator_key="reading_order_successor_consensus_successor_edge_count",
        ),
        "total_reading_order_successor_consensus_successor_disagreement_pages": sum(
            int(case["reading_order_successor_consensus_successor_disagreement_page_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_candidate_pages": sum(
            int(case["reading_order_successor_consensus_candidate_page_count"]) for case in cases
        ),
        "mean_reading_order_successor_consensus_candidate_count": _weighted_case_mean(
            cases,
            value_key="reading_order_successor_consensus_mean_candidate_count",
            weight_key="reading_order_successor_consensus_candidate_page_count",
        ),
        "total_reading_order_successor_consensus_candidate_edges": sum(
            int(case["reading_order_successor_consensus_candidate_edge_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_unique_edges": sum(
            int(case["reading_order_successor_consensus_unique_edge_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_selected_edges": sum(
            int(case["reading_order_successor_consensus_selected_edge_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_selected_edge_votes": sum(
            int(case["reading_order_successor_consensus_selected_edge_vote_count"]) for case in cases
        ),
        "mean_reading_order_successor_consensus_selected_edge_support_ratio": _weighted_case_mean(
            cases,
            value_key="reading_order_successor_consensus_selected_edge_support_ratio",
            weight_key="reading_order_successor_consensus_selected_edge_count",
        ),
        "mean_reading_order_successor_consensus_selected_edge_coverage_ratio": _weighted_case_mean(
            cases,
            value_key="reading_order_successor_consensus_selected_edge_coverage_ratio",
            weight_key="reading_order_successor_consensus_candidate_page_count",
        ),
        "total_reading_order_successor_consensus_conflicted_edges": sum(
            int(case["reading_order_successor_consensus_conflicted_edge_count"]) for case in cases
        ),
        "mean_reading_order_successor_consensus_conflicted_edge_ratio": _weighted_case_mean(
            cases,
            value_key="reading_order_successor_consensus_conflicted_edge_ratio",
            weight_key="reading_order_successor_consensus_unique_edge_count",
        ),
        "total_reading_order_successor_consensus_high_agreement_pages": sum(
            int(case["reading_order_successor_consensus_high_agreement_page_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_medium_agreement_pages": sum(
            int(case["reading_order_successor_consensus_medium_agreement_page_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_low_agreement_pages": sum(
            int(case["reading_order_successor_consensus_low_agreement_page_count"]) for case in cases
        ),
        "total_reading_order_successor_consensus_unavailable_pages": sum(
            int(case["reading_order_successor_consensus_unavailable_page_count"]) for case in cases
        ),
        "reading_order_candidate_page_recommendation_counts": _sum_case_count_dicts(
            cases,
            "reading_order_candidate_page_recommendation_counts",
        ),
        "total_reading_order_candidate_streams": sum(
            int(case["reading_order_candidate_stream_count"]) for case in cases
        ),
        "reading_order_candidate_stream_recommendation_counts": _sum_case_count_dicts(
            cases,
            "reading_order_candidate_stream_recommendation_counts",
        ),
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
        "translation_stress_counts": _sum_case_values(cases, "translation_stress"),
        "total_translation_stress_elements": sum(int(case["translation_stress_element_count"]) for case in cases),
        "total_translation_stress_source_chars": sum(
            int(case["translation_stress_source_char_count"]) for case in cases
        ),
        "total_translation_stress_translated_chars": sum(
            int(case["translation_stress_translated_char_count"]) for case in cases
        ),
        "mean_translation_stress_char_expansion_ratio": _weighted_optional_case_mean(
            cases,
            value_key="translation_stress_char_expansion_ratio",
            weight_key="translation_stress_source_char_count",
        ),
        "total_fidelity_replacement_elements": sum(
            int(case["fidelity_replacement_element_count"]) for case in cases
        ),
        "total_fidelity_replacement_overflows": sum(
            int(case["fidelity_replacement_overflow_count"]) for case in cases
        ),
        "total_fidelity_replacement_conflicts": sum(
            int(case["fidelity_replacement_conflict_count"]) for case in cases
        ),
        "total_fidelity_replacement_conflict_targets": sum(
            int(case["fidelity_replacement_conflict_target_count"]) for case in cases
        ),
        "total_fidelity_replacement_same_stream_conflict_targets": sum(
            int(case["fidelity_replacement_same_stream_conflict_target_count"]) for case in cases
        ),
        "total_fidelity_replacement_cross_stream_conflict_targets": sum(
            int(case["fidelity_replacement_cross_stream_conflict_target_count"]) for case in cases
        ),
        "min_fidelity_replacement_fit_scale": _min_optional_case_float(cases, "fidelity_replacement_min_fit_scale"),
        "mean_fidelity_replacement_fit_scale": _weighted_optional_case_mean(
            cases,
            value_key="fidelity_replacement_mean_fit_scale",
            weight_key="fidelity_replacement_element_count",
        ),
        "fidelity_replacement_policy_counts": _sum_case_count_dicts(cases, "fidelity_replacement_policy_counts"),
        "fidelity_replacement_conflict_target_stream_type_counts": _sum_case_count_dicts(
            cases, "fidelity_replacement_conflict_target_stream_type_counts"
        ),
        "fidelity_replacement_conflict_target_stream_id_counts": _sum_case_count_dicts(
            cases, "fidelity_replacement_conflict_target_stream_id_counts"
        ),
        "fidelity_replacement_conflict_stream_type_pair_counts": _sum_case_count_dicts(
            cases, "fidelity_replacement_conflict_stream_type_pair_counts"
        ),
        "fidelity_replacement_conflict_stream_id_pair_counts": _sum_case_count_dicts(
            cases, "fidelity_replacement_conflict_stream_id_pair_counts"
        ),
        "fidelity_replacement_stream_type_counts": _sum_case_count_dicts(
            cases, "fidelity_replacement_stream_type_counts"
        ),
        "fidelity_replacement_stream_type_overflow_counts": _sum_case_count_dicts(
            cases, "fidelity_replacement_stream_type_overflow_counts"
        ),
        "fidelity_replacement_stream_type_conflict_counts": _sum_case_count_dicts(
            cases, "fidelity_replacement_stream_type_conflict_counts"
        ),
        "fidelity_replacement_stream_id_counts": _sum_case_count_dicts(
            cases, "fidelity_replacement_stream_id_counts"
        ),
        "fidelity_replacement_stream_id_overflow_counts": _sum_case_count_dicts(
            cases, "fidelity_replacement_stream_id_overflow_counts"
        ),
        "fidelity_replacement_stream_id_conflict_counts": _sum_case_count_dicts(
            cases, "fidelity_replacement_stream_id_conflict_counts"
        ),
        "layout_region_counts": _sum_case_count_dicts(cases, "layout_region_counts"),
        "total_table_regions": sum(int(case["table_region_count"]) for case in cases),
        "total_figure_regions": sum(int(case["figure_region_count"]) for case in cases),
        "total_raster_fallbacks": sum(int(case["raster_fallback_count"]) for case in cases),
        "total_rasterized_text_elements": sum(int(case["rasterized_text_count"]) for case in cases),
        "total_rasterized_image_elements": sum(int(case["rasterized_image_count"]) for case in cases),
        "total_rasterized_shape_elements": sum(int(case["rasterized_shape_count"]) for case in cases),
        "total_vector_background_pages": sum(int(case["vector_background_page_count"]) for case in cases),
        "total_structure_evidence_regions": sum(int(case["structure_evidence_region_count"]) for case in cases),
        "total_structure_evidence_relation_edges": sum(
            int(case["structure_evidence_relation_edge_count"]) for case in cases
        ),
        "total_structure_evidence_resolved_relation_edges": sum(
            int(case["structure_evidence_resolved_relation_edge_count"]) for case in cases
        ),
        "total_structure_evidence_resolved_relation_alias_edges": sum(
            int(case["structure_evidence_resolved_relation_alias_edge_count"]) for case in cases
        ),
        "total_structure_evidence_resolved_relation_group_edges": sum(
            int(case["structure_evidence_resolved_relation_group_edge_count"]) for case in cases
        ),
        "total_structure_evidence_relation_group_internal_edges": sum(
            int(case["structure_evidence_relation_group_internal_edge_count"]) for case in cases
        ),
        "total_structure_evidence_unresolved_relation_edges": sum(
            int(case["structure_evidence_unresolved_relation_edge_count"]) for case in cases
        ),
        "total_structure_evidence_unresolved_relation_endpoints": sum(
            int(case["structure_evidence_unresolved_relation_endpoint_count"]) for case in cases
        ),
        "total_structure_evidence_streams": sum(int(case["structure_evidence_stream_count"]) for case in cases),
        "total_structure_evidence_resolved_stream_members": sum(
            int(case["structure_evidence_resolved_stream_member_count"]) for case in cases
        ),
        "total_structure_evidence_resolved_stream_alias_members": sum(
            int(case["structure_evidence_resolved_stream_alias_member_count"]) for case in cases
        ),
        "total_structure_evidence_resolved_stream_group_member_refs": sum(
            int(case["structure_evidence_resolved_stream_group_member_ref_count"]) for case in cases
        ),
        "total_structure_evidence_unresolved_stream_member_refs": sum(
            int(case["structure_evidence_unresolved_stream_member_ref_count"]) for case in cases
        ),
        "total_structure_evidence_duplicate_stream_member_refs": sum(
            int(case["structure_evidence_duplicate_stream_member_ref_count"]) for case in cases
        ),
        "total_structure_evidence_stream_conflicts": sum(
            int(case["structure_evidence_stream_conflict_count"]) for case in cases
        ),
        "total_structure_evidence_relation_streams": sum(
            int(case["structure_evidence_relation_stream_count"]) for case in cases
        ),
        "total_structure_evidence_resolved_relation_stream_members": sum(
            int(case["structure_evidence_resolved_relation_stream_member_count"]) for case in cases
        ),
        "total_structure_evidence_relation_stream_conflicts": sum(
            int(case["structure_evidence_relation_stream_conflict_count"]) for case in cases
        ),
        "total_structure_evidence_matched_elements": sum(
            int(case["structure_evidence_matched_element_count"]) for case in cases
        ),
        "total_structure_evidence_reordered_pages": sum(
            int(case["structure_evidence_reordered_page_count"]) for case in cases
        ),
        "total_structure_evidence_relation_reordered_pages": sum(
            int(case["structure_evidence_relation_reordered_page_count"]) for case in cases
        ),
        "total_structure_evidence_order_reordered_pages": sum(
            int(case["structure_evidence_order_reordered_page_count"]) for case in cases
        ),
        "structure_evidence_order_source_counts": _sum_case_count_dicts(
            cases,
            "structure_evidence_order_source_counts",
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
        "source",
        "source_pdf",
        "source_path",
        "source_type",
        "input_kind",
        "image_dpi",
        "max_pages",
        "page_ranges",
        "sampled_page_numbers",
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
        "mixed_table_column_flow_element_count",
        "grid_island_element_count",
        "table_row_major_element_count",
        "spatial_graph_element_count",
        "box_flow_element_count",
        "successor_consensus_arbitration_element_count",
        "recursive_xy_cut_element_count",
        "reading_order_artifact_element_count",
        "reading_order_footnote_element_count",
        "reading_order_sidebar_element_count",
        "reading_order_sidebar_counts",
        "reading_order_stream_element_count",
        "reading_order_stream_count",
        "reading_order_stream_type_counts",
        "reading_order_stream_id_counts",
        "reading_order_proposal_stream_count",
        "reading_order_proposal_member_count",
        "reading_order_proposal_successor_edge_count",
        "reading_order_proposal_review_successor_edge_count",
        "reading_order_proposal_review_transition_count",
        "reading_order_proposal_stream_type_counts",
        "reading_order_proposal_stream_origin_counts",
        "reading_order_proposal_semantic_ground_truth_available",
        "reading_order_proposal_semantic_expected_successor_edge_count",
        "reading_order_proposal_semantic_successor_candidate_edge_count",
        "reading_order_proposal_semantic_successor_labelled_edge_count",
        "reading_order_proposal_semantic_successor_unlabelled_edge_count",
        "reading_order_proposal_semantic_successor_correct_count",
        "reading_order_proposal_semantic_successor_precision",
        "reading_order_proposal_semantic_successor_coverage",
        "reading_order_proposal_semantic_review_successor_candidate_edge_count",
        "reading_order_proposal_semantic_review_successor_labelled_edge_count",
        "reading_order_proposal_semantic_review_successor_unlabelled_edge_count",
        "reading_order_proposal_semantic_review_successor_correct_count",
        "reading_order_proposal_semantic_review_successor_precision",
        "reading_order_proposal_semantic_review_successor_coverage",
        "reading_order_proposal_semantic_reviewable_successor_correct_count",
        "reading_order_proposal_semantic_reviewable_successor_coverage",
        "reading_order_caption_element_count",
        "reading_order_caption_counts",
        "reading_order_caption_targeted_element_count",
        "reading_order_caption_orphan_element_count",
        "reading_order_caption_target_coverage_ratio",
        "reading_order_caption_target_counts",
        "reading_order_confidence_element_count",
        "reading_order_mean_confidence",
        "reading_order_low_confidence_element_count",
        "reading_order_evidence_counts",
        "reading_order_box_flow_pair_count",
        "reading_order_box_flow_disagreement_pair_count",
        "reading_order_box_flow_disagreement_ratio",
        "reading_order_box_flow_disagreement_page_count",
        "reading_order_box_flow_successor_edge_count",
        "reading_order_box_flow_successor_disagreement_count",
        "reading_order_box_flow_successor_disagreement_ratio",
        "reading_order_box_flow_successor_disagreement_page_count",
        "reading_order_relation_graph_pair_count",
        "reading_order_relation_graph_disagreement_pair_count",
        "reading_order_relation_graph_disagreement_ratio",
        "reading_order_relation_graph_disagreement_page_count",
        "reading_order_relation_graph_successor_edge_count",
        "reading_order_relation_graph_successor_disagreement_count",
        "reading_order_relation_graph_successor_disagreement_ratio",
        "reading_order_relation_graph_successor_disagreement_page_count",
        "reading_order_successor_consensus_pair_count",
        "reading_order_successor_consensus_disagreement_pair_count",
        "reading_order_successor_consensus_disagreement_ratio",
        "reading_order_successor_consensus_disagreement_page_count",
        "reading_order_successor_consensus_successor_edge_count",
        "reading_order_successor_consensus_successor_disagreement_count",
        "reading_order_successor_consensus_successor_disagreement_ratio",
        "reading_order_successor_consensus_successor_disagreement_page_count",
        "reading_order_successor_consensus_candidate_page_count",
        "reading_order_successor_consensus_mean_candidate_count",
        "reading_order_successor_consensus_candidate_edge_count",
        "reading_order_successor_consensus_unique_edge_count",
        "reading_order_successor_consensus_selected_edge_count",
        "reading_order_successor_consensus_selected_edge_vote_count",
        "reading_order_successor_consensus_selected_edge_support_ratio",
        "reading_order_successor_consensus_selected_edge_coverage_ratio",
        "reading_order_successor_consensus_conflicted_edge_count",
        "reading_order_successor_consensus_conflicted_edge_ratio",
        "reading_order_successor_consensus_high_agreement_page_count",
        "reading_order_successor_consensus_medium_agreement_page_count",
        "reading_order_successor_consensus_low_agreement_page_count",
        "reading_order_successor_consensus_unavailable_page_count",
        "reading_order_candidate_page_recommendation_counts",
        "reading_order_candidate_stream_count",
        "reading_order_candidate_stream_recommendation_counts",
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
        "translation_stress",
        "translation_stress_element_count",
        "translation_stress_source_char_count",
        "translation_stress_translated_char_count",
        "translation_stress_char_expansion_ratio",
        "fidelity_replacement_element_count",
        "fidelity_replacement_overflow_count",
        "fidelity_replacement_conflict_count",
        "fidelity_replacement_conflict_target_count",
        "fidelity_replacement_same_stream_conflict_target_count",
        "fidelity_replacement_cross_stream_conflict_target_count",
        "fidelity_replacement_min_fit_scale",
        "fidelity_replacement_mean_fit_scale",
        "fidelity_replacement_policy_counts",
        "fidelity_replacement_conflict_target_stream_type_counts",
        "fidelity_replacement_conflict_target_stream_id_counts",
        "fidelity_replacement_conflict_stream_type_pair_counts",
        "fidelity_replacement_conflict_stream_id_pair_counts",
        "fidelity_replacement_stream_diagnostics",
        "fidelity_replacement_stream_type_counts",
        "fidelity_replacement_stream_type_overflow_counts",
        "fidelity_replacement_stream_type_conflict_counts",
        "fidelity_replacement_stream_id_counts",
        "fidelity_replacement_stream_id_overflow_counts",
        "fidelity_replacement_stream_id_conflict_counts",
        "structure_evidence_source",
        "structure_evidence_region_count",
        "structure_evidence_relation_edge_count",
        "structure_evidence_resolved_relation_edge_count",
        "structure_evidence_resolved_relation_alias_edge_count",
        "structure_evidence_resolved_relation_group_edge_count",
        "structure_evidence_relation_group_internal_edge_count",
        "structure_evidence_unresolved_relation_edge_count",
        "structure_evidence_unresolved_relation_endpoint_count",
        "structure_evidence_stream_count",
        "structure_evidence_resolved_stream_member_count",
        "structure_evidence_resolved_stream_alias_member_count",
        "structure_evidence_resolved_stream_group_member_ref_count",
        "structure_evidence_unresolved_stream_member_ref_count",
        "structure_evidence_duplicate_stream_member_ref_count",
        "structure_evidence_stream_conflict_count",
        "structure_evidence_relation_stream_count",
        "structure_evidence_resolved_relation_stream_member_count",
        "structure_evidence_relation_stream_conflict_count",
        "structure_evidence_matched_element_count",
        "structure_evidence_reordered_page_count",
        "structure_evidence_relation_reordered_page_count",
        "structure_evidence_order_reordered_page_count",
        "structure_evidence_order_source_counts",
        "semantic_layer_driver",
        "semantic_layer_payload_kind",
        "semantic_layer_structure_role",
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
        "semantic_successor_accuracy",
        "semantic_successor_correct_count",
        "semantic_successor_total_count",
        "semantic_relation_successor_accuracy",
        "semantic_relation_successor_correct_count",
        "semantic_relation_successor_total_count",
        "semantic_relation_precedence_accuracy",
        "semantic_relation_precedence_correct_count",
        "semantic_relation_precedence_total_count",
        "semantic_relation_missing_text_count",
        "semantic_stream_count",
        "semantic_stream_successor_accuracy",
        "semantic_stream_successor_correct_count",
        "semantic_stream_successor_total_count",
        "semantic_stream_precedence_accuracy",
        "semantic_stream_precedence_correct_count",
        "semantic_stream_precedence_total_count",
        "semantic_stream_missing_text_count",
        "semantic_stream_assignment_label_count",
        "semantic_stream_assignment_found_count",
        "semantic_stream_assignment_missing_count",
        "semantic_stream_assignment_id_correct_count",
        "semantic_stream_assignment_id_mismatch_count",
        "semantic_stream_assignment_type_correct_count",
        "semantic_stream_assignment_type_total_count",
        "semantic_stream_assignment_type_mismatch_count",
        "semantic_stream_assignment_type_confusion_counts",
        "semantic_stream_assignment_id_accuracy",
        "semantic_stream_assignment_type_accuracy",
        "semantic_candidate_order_metrics",
        "semantic_best_candidate_by_successor",
        "semantic_best_candidate_successor_accuracy",
        "semantic_best_candidate_by_relation_successor",
        "semantic_best_candidate_relation_successor_accuracy",
        "semantic_best_candidate_by_stream_successor",
        "semantic_best_candidate_stream_successor_accuracy",
        "semantic_candidate_arbitration_recommendation",
        "semantic_candidate_arbitration_candidate",
        "semantic_candidate_arbitration_reason",
        "semantic_candidate_successor_delta",
        "semantic_candidate_pairwise_delta",
        "semantic_candidate_relation_successor_delta",
        "semantic_candidate_stream_successor_delta",
        "semantic_visual_yx_order_pair_accuracy",
        "semantic_visual_yx_successor_accuracy",
        "semantic_visual_yx_relation_successor_accuracy",
        "semantic_visual_yx_relation_precedence_accuracy",
        "semantic_visual_yx_stream_successor_accuracy",
        "semantic_visual_yx_stream_precedence_accuracy",
        "semantic_box_flow_order_pair_accuracy",
        "semantic_box_flow_successor_accuracy",
        "semantic_box_flow_relation_successor_accuracy",
        "semantic_box_flow_relation_precedence_accuracy",
        "semantic_box_flow_stream_successor_accuracy",
        "semantic_box_flow_stream_precedence_accuracy",
        "semantic_relation_graph_order_pair_accuracy",
        "semantic_relation_graph_successor_accuracy",
        "semantic_relation_graph_relation_successor_accuracy",
        "semantic_relation_graph_relation_precedence_accuracy",
        "semantic_relation_graph_stream_successor_accuracy",
        "semantic_relation_graph_stream_precedence_accuracy",
        "semantic_structure_relation_order_pair_accuracy",
        "semantic_structure_relation_successor_accuracy",
        "semantic_structure_relation_relation_successor_accuracy",
        "semantic_structure_relation_relation_precedence_accuracy",
        "semantic_structure_relation_stream_successor_accuracy",
        "semantic_structure_relation_stream_precedence_accuracy",
        "semantic_successor_consensus_order_pair_accuracy",
        "semantic_successor_consensus_successor_accuracy",
        "semantic_successor_consensus_relation_successor_accuracy",
        "semantic_successor_consensus_relation_precedence_accuracy",
        "semantic_successor_consensus_stream_successor_accuracy",
        "semantic_successor_consensus_stream_precedence_accuracy",
        "semantic_external_structure_order_pair_accuracy",
        "semantic_external_structure_successor_accuracy",
        "semantic_external_structure_relation_successor_accuracy",
        "semantic_external_structure_relation_precedence_accuracy",
        "semantic_external_structure_stream_successor_accuracy",
        "semantic_external_structure_stream_precedence_accuracy",
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


def _weighted_case_mean(cases: list[dict[str, Any]], value_key: str, weight_key: str) -> float:
    weighted_sum = sum(float(case[value_key]) * int(case[weight_key]) for case in cases)
    weight = sum(int(case[weight_key]) for case in cases)
    return round(weighted_sum / max(weight, 1), 8)


def _weighted_optional_case_mean(cases: list[dict[str, Any]], value_key: str, weight_key: str) -> float | None:
    weighted_sum = 0.0
    weight = 0
    for case in cases:
        value = case.get(value_key)
        if value is None:
            continue
        case_weight = int(case.get(weight_key) or 0)
        if case_weight <= 0:
            continue
        weighted_sum += float(value) * case_weight
        weight += case_weight
    if weight <= 0:
        return None
    return round(weighted_sum / weight, 8)


def _min_optional_case_float(cases: list[dict[str, Any]], key: str) -> float | None:
    values: list[float] = []
    for case in cases:
        value = case.get(key)
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return round(min(values), 8)


def _ratio_from_case_sums(
    cases: list[dict[str, Any]],
    numerator_key: str,
    denominator_key: str,
) -> float:
    numerator = sum(int(case[numerator_key]) for case in cases)
    denominator = sum(int(case[denominator_key]) for case in cases)
    return round(numerator / max(denominator, 1), 8)


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


def _page_ranges_request(page_ranges: str | None) -> str | None:
    if page_ranges is None:
        return None
    value = ",".join(part.strip() for part in str(page_ranges).split(",") if part.strip())
    return value or None


def _page_indices_request(page_ranges: str | None, max_pages: int | None) -> tuple[int, ...] | None:
    if page_ranges is None:
        return None
    if max_pages is not None:
        raise ValueError("page_ranges cannot be combined with max_pages")

    indices: list[int] = []
    seen: set[int] = set()
    for part in page_ranges.split(","):
        if "-" in part:
            start_text, end_text = (item.strip() for item in part.split("-", 1))
        else:
            start_text = end_text = part.strip()
        try:
            start = int(start_text)
            end = int(end_text)
        except ValueError as exc:
            raise ValueError(f"Invalid page range segment: {part!r}") from exc
        if start <= 0 or end <= 0:
            raise ValueError(f"Page ranges are 1-based and must be positive, got {part!r}")
        if end < start:
            raise ValueError(f"Page range end must be >= start, got {part!r}")
        for page_number in range(start, end + 1):
            index = page_number - 1
            if index in seen:
                continue
            indices.append(index)
            seen.add(index)
    if not indices:
        raise ValueError("page_ranges did not contain any pages")
    return tuple(indices)


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


def _translation_stress_request(translation_stress: BenchmarkTranslationStress) -> BenchmarkTranslationStress:
    if translation_stress not in TRANSLATION_STRESS_POLICIES:
        raise ValueError(
            "translation_stress must be one of off or pseudo-expand, "
            f"got {translation_stress}"
        )
    return translation_stress


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


def _structure_json_by_source(input_sources: list[Path], structure_jsons: list[str | Path]) -> dict[Path, Path]:
    if not structure_jsons:
        return {}

    sources = [source.resolve() for source in input_sources]
    paths = [Path(path).resolve() for path in structure_jsons]
    if len(paths) == 1 and len(sources) == 1:
        return {sources[0]: paths[0]}
    if len(paths) == len(sources):
        return dict(zip(sources, paths))

    source_keys: dict[str, Path] = {}
    for source in sources:
        source_keys[source.stem] = source
        if source.parent.name:
            source_keys[f"{source.parent.name}.{source.stem}"] = source

    mapping: dict[Path, Path] = {}
    unmatched: list[Path] = []
    for path in paths:
        source = source_keys.get(_structure_match_key(path))
        if source is None:
            unmatched.append(path)
            continue
        mapping[source] = path

    if unmatched or len(mapping) != len(paths):
        names = ", ".join(str(path) for path in unmatched or paths)
        raise ValueError(
            "Could not match structure JSON files to sources. "
            "Pass one JSON for one source, pass the same number of sources and JSON files, "
            f"or use matching names such as <source-stem>.structure.json. Unmatched: {names}"
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
    candidate_metrics = report.get("semantic_candidate_order_metrics") if available else {}
    if not isinstance(candidate_metrics, dict):
        candidate_metrics = {}
    selected_pairwise = report.get("semantic_order_pair_accuracy") if available else None
    selected_successor = report.get("semantic_successor_accuracy") if available else None
    return {
        "semantic_ground_truth_available": available,
        "semantic_order_pair_accuracy": selected_pairwise,
        "semantic_sequence_similarity": report.get("semantic_sequence_similarity") if available else None,
        "semantic_exact_page_match_rate": report.get("semantic_exact_page_match_rate") if available else None,
        "semantic_expected_text_count": report.get("semantic_expected_text_count") if available else 0,
        "semantic_actual_text_count": report.get("semantic_actual_text_count") if available else 0,
        "semantic_sequence_edit_distance": report.get("semantic_sequence_edit_distance") if available else 0,
        "semantic_pairwise_correct_count": report.get("semantic_pairwise_correct_count") if available else 0,
        "semantic_pairwise_total_count": report.get("semantic_pairwise_total_count") if available else 0,
        "semantic_successor_accuracy": selected_successor,
        "semantic_successor_correct_count": report.get("semantic_successor_correct_count") if available else 0,
        "semantic_successor_total_count": report.get("semantic_successor_total_count") if available else 0,
        "semantic_relation_successor_accuracy": report.get("semantic_relation_successor_accuracy")
        if available
        else None,
        "semantic_relation_successor_correct_count": report.get("semantic_relation_successor_correct_count")
        if available
        else 0,
        "semantic_relation_successor_total_count": report.get("semantic_relation_successor_total_count")
        if available
        else 0,
        "semantic_relation_precedence_accuracy": report.get("semantic_relation_precedence_accuracy")
        if available
        else None,
        "semantic_relation_precedence_correct_count": report.get("semantic_relation_precedence_correct_count")
        if available
        else 0,
        "semantic_relation_precedence_total_count": report.get("semantic_relation_precedence_total_count")
        if available
        else 0,
        "semantic_relation_missing_text_count": report.get("semantic_relation_missing_text_count") if available else 0,
        "semantic_stream_count": report.get("semantic_stream_count") if available else 0,
        "semantic_stream_successor_accuracy": report.get("semantic_stream_successor_accuracy")
        if available
        else None,
        "semantic_stream_successor_correct_count": report.get("semantic_stream_successor_correct_count")
        if available
        else 0,
        "semantic_stream_successor_total_count": report.get("semantic_stream_successor_total_count")
        if available
        else 0,
        "semantic_stream_precedence_accuracy": report.get("semantic_stream_precedence_accuracy")
        if available
        else None,
        "semantic_stream_precedence_correct_count": report.get("semantic_stream_precedence_correct_count")
        if available
        else 0,
        "semantic_stream_precedence_total_count": report.get("semantic_stream_precedence_total_count")
        if available
        else 0,
        "semantic_stream_missing_text_count": report.get("semantic_stream_missing_text_count") if available else 0,
        "semantic_stream_assignment_label_count": report.get("semantic_stream_assignment_label_count")
        if available
        else 0,
        "semantic_stream_assignment_found_count": report.get("semantic_stream_assignment_found_count")
        if available
        else 0,
        "semantic_stream_assignment_missing_count": report.get("semantic_stream_assignment_missing_count")
        if available
        else 0,
        "semantic_stream_assignment_id_correct_count": report.get(
            "semantic_stream_assignment_id_correct_count"
        )
        if available
        else 0,
        "semantic_stream_assignment_id_mismatch_count": report.get(
            "semantic_stream_assignment_id_mismatch_count"
        )
        if available
        else 0,
        "semantic_stream_assignment_type_correct_count": report.get(
            "semantic_stream_assignment_type_correct_count"
        )
        if available
        else 0,
        "semantic_stream_assignment_type_total_count": report.get(
            "semantic_stream_assignment_type_total_count"
        )
        if available
        else 0,
        "semantic_stream_assignment_type_mismatch_count": report.get(
            "semantic_stream_assignment_type_mismatch_count"
        )
        if available
        else 0,
        "semantic_stream_assignment_type_confusion_counts": report.get(
            "semantic_stream_assignment_type_confusion_counts"
        )
        if available
        else {},
        "semantic_stream_assignment_id_accuracy": report.get("semantic_stream_assignment_id_accuracy")
        if available
        else None,
        "semantic_stream_assignment_type_accuracy": report.get("semantic_stream_assignment_type_accuracy")
        if available
        else None,
        "semantic_ignored_text_count": report.get("semantic_ignored_text_count") if available else 0,
        "semantic_ignored_text_zone_counts": report.get("semantic_ignored_text_zone_counts") if available else {},
        "semantic_ignored_text_role_counts": report.get("semantic_ignored_text_role_counts") if available else {},
        "semantic_ignored_text_source_counts": report.get("semantic_ignored_text_source_counts") if available else {},
        "semantic_missing_text_count": report.get("semantic_missing_text_count") if available else 0,
        "semantic_extra_text_count": report.get("semantic_extra_text_count") if available else 0,
        "semantic_candidate_order_metrics": candidate_metrics,
        "semantic_best_candidate_by_successor": report.get("semantic_best_candidate_by_successor") if available else None,
        "semantic_best_candidate_successor_accuracy": report.get("semantic_best_candidate_successor_accuracy")
        if available
        else None,
        "semantic_best_candidate_by_relation_successor": report.get("semantic_best_candidate_by_relation_successor")
        if available
        else None,
        "semantic_best_candidate_relation_successor_accuracy": report.get(
            "semantic_best_candidate_relation_successor_accuracy"
        )
        if available
        else None,
        "semantic_best_candidate_by_stream_successor": report.get("semantic_best_candidate_by_stream_successor")
        if available
        else None,
        "semantic_best_candidate_stream_successor_accuracy": report.get(
            "semantic_best_candidate_stream_successor_accuracy"
        )
        if available
        else None,
        **_semantic_candidate_arbitration_metrics(
            selected_pairwise=selected_pairwise,
            selected_successor=selected_successor,
            selected_relation_successor=report.get("semantic_relation_successor_accuracy") if available else None,
            selected_stream_successor=report.get("semantic_stream_successor_accuracy") if available else None,
            candidate_metrics=candidate_metrics,
        ),
        **_semantic_candidate_case_metrics(candidate_metrics),
    }


def _reading_order_proposal_semantic_case_metrics(report: dict[str, Any]) -> dict[str, Any]:
    available = bool(report.get("ground_truth_available"))

    def count(name: str) -> int:
        return int(report.get(name) or 0) if available else 0

    def ratio(name: str) -> float | None:
        return report.get(name) if available else None

    return {
        "reading_order_proposal_semantic_ground_truth_available": available,
        "reading_order_proposal_semantic_expected_successor_edge_count": count("expected_successor_edge_count"),
        "reading_order_proposal_semantic_successor_candidate_edge_count": count("successor_candidate_edge_count"),
        "reading_order_proposal_semantic_successor_labelled_edge_count": count("successor_labelled_edge_count"),
        "reading_order_proposal_semantic_successor_unlabelled_edge_count": count("successor_unlabelled_edge_count"),
        "reading_order_proposal_semantic_successor_correct_count": count("successor_correct_count"),
        "reading_order_proposal_semantic_successor_precision": ratio("successor_precision"),
        "reading_order_proposal_semantic_successor_coverage": ratio("successor_coverage"),
        "reading_order_proposal_semantic_review_successor_candidate_edge_count": count(
            "review_successor_candidate_edge_count"
        ),
        "reading_order_proposal_semantic_review_successor_labelled_edge_count": count(
            "review_successor_labelled_edge_count"
        ),
        "reading_order_proposal_semantic_review_successor_unlabelled_edge_count": count(
            "review_successor_unlabelled_edge_count"
        ),
        "reading_order_proposal_semantic_review_successor_correct_count": count("review_successor_correct_count"),
        "reading_order_proposal_semantic_review_successor_precision": ratio("review_successor_precision"),
        "reading_order_proposal_semantic_review_successor_coverage": ratio("review_successor_coverage"),
        "reading_order_proposal_semantic_reviewable_successor_correct_count": count(
            "reviewable_successor_correct_count"
        ),
        "reading_order_proposal_semantic_reviewable_successor_coverage": ratio("reviewable_successor_coverage"),
    }


def _semantic_candidate_arbitration_metrics(
    *,
    selected_pairwise: Any,
    selected_successor: Any,
    selected_relation_successor: Any,
    selected_stream_successor: Any,
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    default = {
        "semantic_candidate_arbitration_recommendation": "unavailable",
        "semantic_candidate_arbitration_candidate": None,
        "semantic_candidate_arbitration_reason": "no semantic candidate scores",
        "semantic_candidate_successor_delta": None,
        "semantic_candidate_pairwise_delta": None,
        "semantic_candidate_relation_successor_delta": None,
        "semantic_candidate_stream_successor_delta": None,
    }
    if selected_pairwise is None or selected_successor is None or not candidate_metrics:
        return default

    valid_candidates: list[tuple[str, float, float]] = []
    valid_relation_candidates: list[tuple[str, float, float]] = []
    valid_stream_candidates: list[tuple[str, float, float]] = []
    for candidate_name, metrics in candidate_metrics.items():
        if not isinstance(metrics, dict):
            continue
        try:
            successor_accuracy = float(metrics["semantic_successor_accuracy"])
            pairwise_accuracy = float(metrics["semantic_order_pair_accuracy"])
        except (KeyError, TypeError, ValueError):
            continue
        valid_candidates.append((str(candidate_name), successor_accuracy, pairwise_accuracy))
        relation_successor_accuracy = metrics.get("semantic_relation_successor_accuracy")
        relation_precedence_accuracy = metrics.get("semantic_relation_precedence_accuracy")
        if relation_successor_accuracy is not None:
            try:
                valid_relation_candidates.append(
                    (
                        str(candidate_name),
                        float(relation_successor_accuracy),
                        float(relation_precedence_accuracy if relation_precedence_accuracy is not None else 0.0),
                    )
                )
            except (TypeError, ValueError):
                pass
        stream_successor_accuracy = metrics.get("semantic_stream_successor_accuracy")
        stream_precedence_accuracy = metrics.get("semantic_stream_precedence_accuracy")
        if stream_successor_accuracy is not None:
            try:
                valid_stream_candidates.append(
                    (
                        str(candidate_name),
                        float(stream_successor_accuracy),
                        float(stream_precedence_accuracy if stream_precedence_accuracy is not None else 0.0),
                    )
                )
            except (TypeError, ValueError):
                pass
    if not valid_candidates:
        return default

    selected_successor_value = float(selected_successor)
    selected_pairwise_value = float(selected_pairwise)
    best_name, best_successor, best_pairwise = max(
        valid_candidates,
        key=lambda item: (item[1], item[2], item[0]),
    )
    successor_delta = round(best_successor - selected_successor_value, 8)
    pairwise_delta = round(best_pairwise - selected_pairwise_value, 8)

    relation_delta: float | None = None
    relation_recommendation: tuple[str, str] | None = None
    if selected_relation_successor is not None and valid_relation_candidates:
        selected_relation_value = float(selected_relation_successor)
        relation_name, relation_successor, _relation_precedence = max(
            valid_relation_candidates,
            key=lambda item: (item[1], item[2], item[0]),
        )
        relation_delta = round(relation_successor - selected_relation_value, 8)
        if relation_delta > 0:
            relation_recommendation = (
                relation_name,
                "best candidate improves labelled relation successor edges",
            )

    stream_delta: float | None = None
    stream_recommendation: tuple[str, str] | None = None
    if selected_stream_successor is not None and valid_stream_candidates:
        selected_stream_value = float(selected_stream_successor)
        stream_name, stream_successor, _stream_precedence = max(
            valid_stream_candidates,
            key=lambda item: (item[1], item[2], item[0]),
        )
        stream_delta = round(stream_successor - selected_stream_value, 8)
        if stream_delta > 0:
            stream_recommendation = (
                stream_name,
                "best candidate improves labelled reading-stream successor edges",
            )

    if relation_recommendation is not None:
        recommendation = f"consider-{relation_recommendation[0]}"
        reason = relation_recommendation[1]
        best_name = relation_recommendation[0]
    elif stream_recommendation is not None:
        recommendation = f"consider-{stream_recommendation[0]}"
        reason = stream_recommendation[1]
        best_name = stream_recommendation[0]
    elif successor_delta > 0 or (successor_delta == 0 and pairwise_delta > 0):
        recommendation = f"consider-{best_name}"
        reason = "best candidate improves labelled semantic order"
    else:
        recommendation = "keep-selected"
        reason = "selected order is at least as good as scored candidates"
    return {
        "semantic_candidate_arbitration_recommendation": recommendation,
        "semantic_candidate_arbitration_candidate": best_name,
        "semantic_candidate_arbitration_reason": reason,
        "semantic_candidate_successor_delta": successor_delta,
        "semantic_candidate_pairwise_delta": pairwise_delta,
        "semantic_candidate_relation_successor_delta": relation_delta,
        "semantic_candidate_stream_successor_delta": stream_delta,
    }


def _semantic_candidate_case_metrics(candidate_metrics: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for candidate_name in SEMANTIC_ORDER_CANDIDATES:
        candidate = candidate_metrics.get(candidate_name)
        if not isinstance(candidate, dict):
            candidate = {}
        metrics.update(
            {
                f"semantic_{candidate_name}_order_pair_accuracy": candidate.get("semantic_order_pair_accuracy"),
                f"semantic_{candidate_name}_pairwise_correct_count": int(
                    candidate.get("semantic_pairwise_correct_count") or 0
                ),
                f"semantic_{candidate_name}_pairwise_total_count": int(
                    candidate.get("semantic_pairwise_total_count") or 0
                ),
                f"semantic_{candidate_name}_successor_accuracy": candidate.get("semantic_successor_accuracy"),
                f"semantic_{candidate_name}_successor_correct_count": int(
                    candidate.get("semantic_successor_correct_count") or 0
                ),
                f"semantic_{candidate_name}_successor_total_count": int(
                    candidate.get("semantic_successor_total_count") or 0
                ),
                f"semantic_{candidate_name}_relation_successor_accuracy": candidate.get(
                    "semantic_relation_successor_accuracy"
                ),
                f"semantic_{candidate_name}_relation_successor_correct_count": int(
                    candidate.get("semantic_relation_successor_correct_count") or 0
                ),
                f"semantic_{candidate_name}_relation_successor_total_count": int(
                    candidate.get("semantic_relation_successor_total_count") or 0
                ),
                f"semantic_{candidate_name}_relation_precedence_accuracy": candidate.get(
                    "semantic_relation_precedence_accuracy"
                ),
                f"semantic_{candidate_name}_relation_precedence_correct_count": int(
                    candidate.get("semantic_relation_precedence_correct_count") or 0
                ),
                f"semantic_{candidate_name}_relation_precedence_total_count": int(
                    candidate.get("semantic_relation_precedence_total_count") or 0
                ),
                f"semantic_{candidate_name}_stream_successor_accuracy": candidate.get(
                    "semantic_stream_successor_accuracy"
                ),
                f"semantic_{candidate_name}_stream_successor_correct_count": int(
                    candidate.get("semantic_stream_successor_correct_count") or 0
                ),
                f"semantic_{candidate_name}_stream_successor_total_count": int(
                    candidate.get("semantic_stream_successor_total_count") or 0
                ),
                f"semantic_{candidate_name}_stream_precedence_accuracy": candidate.get(
                    "semantic_stream_precedence_accuracy"
                ),
                f"semantic_{candidate_name}_stream_precedence_correct_count": int(
                    candidate.get("semantic_stream_precedence_correct_count") or 0
                ),
                f"semantic_{candidate_name}_stream_precedence_total_count": int(
                    candidate.get("semantic_stream_precedence_total_count") or 0
                ),
            }
        )
    return metrics


def _summarize_semantic_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not cases:
        summary = {
            "semantic_case_count": 0,
            "mean_semantic_order_pair_accuracy": None,
            "mean_semantic_sequence_similarity": None,
            "mean_semantic_exact_page_match_rate": None,
            "mean_semantic_successor_accuracy": None,
            "mean_semantic_relation_successor_accuracy": None,
            "mean_semantic_relation_precedence_accuracy": None,
            "mean_semantic_stream_successor_accuracy": None,
            "mean_semantic_stream_precedence_accuracy": None,
            "mean_semantic_stream_assignment_id_accuracy": None,
            "mean_semantic_stream_assignment_type_accuracy": None,
            "total_semantic_expected_text_count": 0,
            "total_semantic_successor_correct_count": 0,
            "total_semantic_successor_count": 0,
            "total_semantic_relation_successor_correct_count": 0,
            "total_semantic_relation_successor_count": 0,
            "total_semantic_relation_precedence_correct_count": 0,
            "total_semantic_relation_precedence_count": 0,
            "total_semantic_relation_missing_text_count": 0,
            "total_semantic_stream_count": 0,
            "total_semantic_stream_successor_correct_count": 0,
            "total_semantic_stream_successor_count": 0,
            "total_semantic_stream_precedence_correct_count": 0,
            "total_semantic_stream_precedence_count": 0,
            "total_semantic_stream_missing_text_count": 0,
            "total_semantic_stream_assignment_label_count": 0,
            "total_semantic_stream_assignment_found_count": 0,
            "total_semantic_stream_assignment_missing_count": 0,
            "total_semantic_stream_assignment_id_correct_count": 0,
            "total_semantic_stream_assignment_id_mismatch_count": 0,
            "total_semantic_stream_assignment_type_correct_count": 0,
            "total_semantic_stream_assignment_type_count": 0,
            "total_semantic_stream_assignment_type_mismatch_count": 0,
            "semantic_stream_assignment_type_confusion_counts": {},
            "total_semantic_ignored_text_count": 0,
            "total_semantic_ignored_text_zone_counts": {},
            "total_semantic_ignored_text_role_counts": {},
            "total_semantic_ignored_text_source_counts": {},
            "total_semantic_missing_text_count": 0,
            "total_semantic_extra_text_count": 0,
            "semantic_best_candidate_by_successor_counts": {},
            "semantic_best_candidate_by_relation_successor_counts": {},
            "semantic_best_candidate_by_stream_successor_counts": {},
            "semantic_candidate_arbitration_recommendation_counts": {},
            "semantic_candidate_arbitration_candidate_counts": {},
            "mean_semantic_candidate_successor_delta": None,
            "mean_semantic_candidate_pairwise_delta": None,
            "mean_semantic_candidate_relation_successor_delta": None,
            "mean_semantic_candidate_stream_successor_delta": None,
        }
        summary.update(_empty_semantic_candidate_summary())
        return summary

    expected_count = sum(int(case["semantic_expected_text_count"]) for case in cases)
    actual_count = sum(int(case["semantic_actual_text_count"]) for case in cases)
    edit_distance = sum(int(case["semantic_sequence_edit_distance"]) for case in cases)
    pairwise_correct = sum(int(case["semantic_pairwise_correct_count"]) for case in cases)
    pairwise_total = sum(int(case["semantic_pairwise_total_count"]) for case in cases)
    successor_correct = sum(int(case["semantic_successor_correct_count"]) for case in cases)
    successor_total = sum(int(case["semantic_successor_total_count"]) for case in cases)
    relation_successor_correct = sum(int(case["semantic_relation_successor_correct_count"]) for case in cases)
    relation_successor_total = sum(int(case["semantic_relation_successor_total_count"]) for case in cases)
    relation_precedence_correct = sum(int(case["semantic_relation_precedence_correct_count"]) for case in cases)
    relation_precedence_total = sum(int(case["semantic_relation_precedence_total_count"]) for case in cases)
    stream_successor_correct = sum(int(case["semantic_stream_successor_correct_count"]) for case in cases)
    stream_successor_total = sum(int(case["semantic_stream_successor_total_count"]) for case in cases)
    stream_precedence_correct = sum(int(case["semantic_stream_precedence_correct_count"]) for case in cases)
    stream_precedence_total = sum(int(case["semantic_stream_precedence_total_count"]) for case in cases)
    stream_assignment_found = sum(int(case["semantic_stream_assignment_found_count"]) for case in cases)
    stream_assignment_id_correct = sum(int(case["semantic_stream_assignment_id_correct_count"]) for case in cases)
    stream_assignment_type_correct = sum(
        int(case["semantic_stream_assignment_type_correct_count"]) for case in cases
    )
    stream_assignment_type_total = sum(
        int(case["semantic_stream_assignment_type_total_count"]) for case in cases
    )
    summary = {
        "semantic_case_count": len(cases),
        "mean_semantic_order_pair_accuracy": round(pairwise_correct / pairwise_total if pairwise_total else 1.0, 8),
        "mean_semantic_successor_accuracy": round(
            successor_correct / successor_total if successor_total else 1.0,
            8,
        ),
        "mean_semantic_relation_successor_accuracy": _optional_case_ratio(
            relation_successor_correct,
            relation_successor_total,
        ),
        "mean_semantic_relation_precedence_accuracy": _optional_case_ratio(
            relation_precedence_correct,
            relation_precedence_total,
        ),
        "mean_semantic_stream_successor_accuracy": _optional_case_ratio(
            stream_successor_correct,
            stream_successor_total,
        ),
        "mean_semantic_stream_precedence_accuracy": _optional_case_ratio(
            stream_precedence_correct,
            stream_precedence_total,
        ),
        "mean_semantic_stream_assignment_id_accuracy": _optional_case_ratio(
            stream_assignment_id_correct,
            stream_assignment_found,
        ),
        "mean_semantic_stream_assignment_type_accuracy": _optional_case_ratio(
            stream_assignment_type_correct,
            stream_assignment_type_total,
        ),
        "mean_semantic_sequence_similarity": round(
            1.0 - edit_distance / max(expected_count, actual_count, 1),
            8,
        ),
        "mean_semantic_exact_page_match_rate": round(
            sum(float(case["semantic_exact_page_match_rate"]) for case in cases) / len(cases),
            8,
        ),
        "total_semantic_expected_text_count": expected_count,
        "total_semantic_successor_correct_count": successor_correct,
        "total_semantic_successor_count": successor_total,
        "total_semantic_relation_successor_correct_count": relation_successor_correct,
        "total_semantic_relation_successor_count": relation_successor_total,
        "total_semantic_relation_precedence_correct_count": relation_precedence_correct,
        "total_semantic_relation_precedence_count": relation_precedence_total,
        "total_semantic_relation_missing_text_count": sum(
            int(case["semantic_relation_missing_text_count"]) for case in cases
        ),
        "total_semantic_stream_count": sum(int(case["semantic_stream_count"]) for case in cases),
        "total_semantic_stream_successor_correct_count": stream_successor_correct,
        "total_semantic_stream_successor_count": stream_successor_total,
        "total_semantic_stream_precedence_correct_count": stream_precedence_correct,
        "total_semantic_stream_precedence_count": stream_precedence_total,
        "total_semantic_stream_missing_text_count": sum(
            int(case["semantic_stream_missing_text_count"]) for case in cases
        ),
        "total_semantic_stream_assignment_label_count": sum(
            int(case["semantic_stream_assignment_label_count"]) for case in cases
        ),
        "total_semantic_stream_assignment_found_count": stream_assignment_found,
        "total_semantic_stream_assignment_missing_count": sum(
            int(case["semantic_stream_assignment_missing_count"]) for case in cases
        ),
        "total_semantic_stream_assignment_id_correct_count": stream_assignment_id_correct,
        "total_semantic_stream_assignment_id_mismatch_count": sum(
            int(case["semantic_stream_assignment_id_mismatch_count"]) for case in cases
        ),
        "total_semantic_stream_assignment_type_correct_count": stream_assignment_type_correct,
        "total_semantic_stream_assignment_type_count": stream_assignment_type_total,
        "total_semantic_stream_assignment_type_mismatch_count": sum(
            int(case["semantic_stream_assignment_type_mismatch_count"]) for case in cases
        ),
        "semantic_stream_assignment_type_confusion_counts": _sum_case_count_dicts(
            cases,
            "semantic_stream_assignment_type_confusion_counts",
        ),
        "total_semantic_ignored_text_count": sum(int(case["semantic_ignored_text_count"]) for case in cases),
        "total_semantic_ignored_text_zone_counts": _sum_case_count_dicts(cases, "semantic_ignored_text_zone_counts"),
        "total_semantic_ignored_text_role_counts": _sum_case_count_dicts(cases, "semantic_ignored_text_role_counts"),
        "total_semantic_ignored_text_source_counts": _sum_case_count_dicts(cases, "semantic_ignored_text_source_counts"),
        "total_semantic_missing_text_count": sum(int(case["semantic_missing_text_count"]) for case in cases),
        "total_semantic_extra_text_count": sum(int(case["semantic_extra_text_count"]) for case in cases),
        "semantic_best_candidate_by_successor_counts": _sum_case_values(cases, "semantic_best_candidate_by_successor"),
        "semantic_best_candidate_by_relation_successor_counts": _sum_case_values(
            cases,
            "semantic_best_candidate_by_relation_successor",
        ),
        "semantic_best_candidate_by_stream_successor_counts": _sum_case_values(
            cases,
            "semantic_best_candidate_by_stream_successor",
        ),
        "semantic_candidate_arbitration_recommendation_counts": _sum_case_values(
            cases,
            "semantic_candidate_arbitration_recommendation",
        ),
        "semantic_candidate_arbitration_candidate_counts": _sum_case_values(
            cases,
            "semantic_candidate_arbitration_candidate",
        ),
        "mean_semantic_candidate_successor_delta": _mean_optional_case_float(
            cases,
            "semantic_candidate_successor_delta",
        ),
        "mean_semantic_candidate_pairwise_delta": _mean_optional_case_float(
            cases,
            "semantic_candidate_pairwise_delta",
        ),
        "mean_semantic_candidate_relation_successor_delta": _mean_optional_case_float(
            cases,
            "semantic_candidate_relation_successor_delta",
        ),
        "mean_semantic_candidate_stream_successor_delta": _mean_optional_case_float(
            cases,
            "semantic_candidate_stream_successor_delta",
        ),
    }
    summary.update(_semantic_candidate_summary(cases))
    return summary


def _empty_semantic_candidate_summary() -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for candidate_name in SEMANTIC_ORDER_CANDIDATES:
        summary[f"mean_semantic_{candidate_name}_order_pair_accuracy"] = None
        summary[f"mean_semantic_{candidate_name}_successor_accuracy"] = None
        summary[f"total_semantic_{candidate_name}_successor_correct_count"] = 0
        summary[f"total_semantic_{candidate_name}_successor_count"] = 0
        summary[f"mean_semantic_{candidate_name}_relation_successor_accuracy"] = None
        summary[f"total_semantic_{candidate_name}_relation_successor_correct_count"] = 0
        summary[f"total_semantic_{candidate_name}_relation_successor_count"] = 0
        summary[f"mean_semantic_{candidate_name}_relation_precedence_accuracy"] = None
        summary[f"total_semantic_{candidate_name}_relation_precedence_correct_count"] = 0
        summary[f"total_semantic_{candidate_name}_relation_precedence_count"] = 0
        summary[f"mean_semantic_{candidate_name}_stream_successor_accuracy"] = None
        summary[f"total_semantic_{candidate_name}_stream_successor_correct_count"] = 0
        summary[f"total_semantic_{candidate_name}_stream_successor_count"] = 0
        summary[f"mean_semantic_{candidate_name}_stream_precedence_accuracy"] = None
        summary[f"total_semantic_{candidate_name}_stream_precedence_correct_count"] = 0
        summary[f"total_semantic_{candidate_name}_stream_precedence_count"] = 0
    return summary


def _semantic_candidate_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for candidate_name in SEMANTIC_ORDER_CANDIDATES:
        pairwise_correct = sum(int(case[f"semantic_{candidate_name}_pairwise_correct_count"]) for case in cases)
        pairwise_total = sum(int(case[f"semantic_{candidate_name}_pairwise_total_count"]) for case in cases)
        successor_correct = sum(int(case[f"semantic_{candidate_name}_successor_correct_count"]) for case in cases)
        successor_total = sum(int(case[f"semantic_{candidate_name}_successor_total_count"]) for case in cases)
        relation_successor_correct = sum(
            int(case[f"semantic_{candidate_name}_relation_successor_correct_count"]) for case in cases
        )
        relation_successor_total = sum(
            int(case[f"semantic_{candidate_name}_relation_successor_total_count"]) for case in cases
        )
        relation_precedence_correct = sum(
            int(case[f"semantic_{candidate_name}_relation_precedence_correct_count"]) for case in cases
        )
        relation_precedence_total = sum(
            int(case[f"semantic_{candidate_name}_relation_precedence_total_count"]) for case in cases
        )
        stream_successor_correct = sum(
            int(case[f"semantic_{candidate_name}_stream_successor_correct_count"]) for case in cases
        )
        stream_successor_total = sum(
            int(case[f"semantic_{candidate_name}_stream_successor_total_count"]) for case in cases
        )
        stream_precedence_correct = sum(
            int(case[f"semantic_{candidate_name}_stream_precedence_correct_count"]) for case in cases
        )
        stream_precedence_total = sum(
            int(case[f"semantic_{candidate_name}_stream_precedence_total_count"]) for case in cases
        )
        summary[f"mean_semantic_{candidate_name}_order_pair_accuracy"] = round(
            pairwise_correct / pairwise_total if pairwise_total else 1.0,
            8,
        )
        summary[f"mean_semantic_{candidate_name}_successor_accuracy"] = round(
            successor_correct / successor_total if successor_total else 1.0,
            8,
        )
        summary[f"total_semantic_{candidate_name}_successor_correct_count"] = successor_correct
        summary[f"total_semantic_{candidate_name}_successor_count"] = successor_total
        summary[f"mean_semantic_{candidate_name}_relation_successor_accuracy"] = _optional_case_ratio(
            relation_successor_correct,
            relation_successor_total,
        )
        summary[f"total_semantic_{candidate_name}_relation_successor_correct_count"] = relation_successor_correct
        summary[f"total_semantic_{candidate_name}_relation_successor_count"] = relation_successor_total
        summary[f"mean_semantic_{candidate_name}_relation_precedence_accuracy"] = _optional_case_ratio(
            relation_precedence_correct,
            relation_precedence_total,
        )
        summary[f"total_semantic_{candidate_name}_relation_precedence_correct_count"] = relation_precedence_correct
        summary[f"total_semantic_{candidate_name}_relation_precedence_count"] = relation_precedence_total
        summary[f"mean_semantic_{candidate_name}_stream_successor_accuracy"] = _optional_case_ratio(
            stream_successor_correct,
            stream_successor_total,
        )
        summary[f"total_semantic_{candidate_name}_stream_successor_correct_count"] = stream_successor_correct
        summary[f"total_semantic_{candidate_name}_stream_successor_count"] = stream_successor_total
        summary[f"mean_semantic_{candidate_name}_stream_precedence_accuracy"] = _optional_case_ratio(
            stream_precedence_correct,
            stream_precedence_total,
        )
        summary[f"total_semantic_{candidate_name}_stream_precedence_correct_count"] = stream_precedence_correct
        summary[f"total_semantic_{candidate_name}_stream_precedence_count"] = stream_precedence_total
    return summary


def _sum_case_count_dicts(cases: list[dict[str, Any]], key: str) -> dict[str, int]:
    return _sum_count_dicts(case.get(key) for case in cases)


def _sum_count_dicts(values: Any) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values:
        if isinstance(value, dict):
            counts.update({str(item_key): int(item_value) for item_key, item_value in value.items()})
    return dict(sorted(counts.items()))


def _mean_optional_case_float(cases: list[dict[str, Any]], key: str) -> float | None:
    values: list[float] = []
    for case in cases:
        value = case.get(key)
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return round(sum(values) / len(values), 8)


def _optional_case_ratio(correct: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(correct / total, 8)


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
