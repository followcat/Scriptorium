import json
import shutil
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from scriptorium.benchmark import (
    _external_structure_candidate_order,
    _fidelity_replacement_stats,
    _page_reading_order_geometry_profile,
    _reading_order_candidate_page_diagnostics,
    _reading_order_candidate_stream_diagnostics,
    _semantic_candidate_arbitration_metrics,
    _semantic_candidate_orders,
    run_benchmark,
    run_structure_ab_benchmark,
)
from scriptorium.benchmark_fixtures import create_benchmark_fixtures
from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.semantic_quality import semantic_ground_truth_path
from scriptorium.structure_evidence import apply_structure_evidence


def _require_tesseract() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is required for image-only OCR fallback benchmark coverage.")


def _readable_test_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size=size)
    return ImageFont.load_default()


def _create_image_only_text_pdf(tmp_path: Path) -> Path:
    image_path = tmp_path / "benchmark_image_only.png"
    image = Image.new("RGB", (1000, 420), "white")
    draw = ImageDraw.Draw(image)
    draw.text((70, 86), "BENCHMARK OCR", font=_readable_test_font(76), fill=(0, 0, 0))
    draw.text((74, 216), "IMAGE ONLY PAGE", font=_readable_test_font(62), fill=(10, 10, 10))
    image.save(image_path)

    pdf_path = tmp_path / "benchmark_image_only.pdf"
    doc = fitz.open()
    page = doc.new_page(width=500, height=210)
    page.insert_image(fitz.Rect(0, 0, 500, 210), filename=image_path)
    doc.save(pdf_path)
    doc.close()
    return pdf_path


def _create_image_source(tmp_path: Path) -> Path:
    image_path = tmp_path / "benchmark_image_source.png"
    image = Image.new("RGB", (480, 260), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 42), "IMAGE SOURCE", font=_readable_test_font(42), fill=(0, 0, 0))
    draw.text((42, 126), "STRUCTURE JSON TEXT", font=_readable_test_font(28), fill=(20, 20, 20))
    image.save(image_path)
    return image_path


def test_benchmark_fixtures_create_multiple_pdfs(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")
    assert len(pdfs) == 5
    assert all(path.exists() for path in pdfs)
    assert all(semantic_ground_truth_path(path).exists() for path in pdfs)


def test_benchmark_outputs_similarity_metrics(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:2]
    report = run_benchmark(pdfs, tmp_path / "benchmark", dpi=96)

    assert report["case_count"] == 2
    assert report["font_profile"] == "browser-default"
    assert report["input_kind"] == "auto"
    assert report["image_dpi"] == 96
    assert report["raster_policy"] == "dense"
    assert report["html_mode"] == "structured"
    assert report["font_size_scale"] == 1.0
    assert report["text_fit"] == "none"
    assert report["fidelity_background"] == "auto"
    assert report["translation_stress"] == "off"
    assert "mean_visual_similarity" in report["summary"]
    assert "mean_diff_ratio" in report["summary"]
    assert "p95_diff_ratio" in report["summary"]
    assert report["summary"]["total_pages"] >= 2
    assert report["summary"]["ocr_fallback_counts"] == {"image-only": 2}
    assert report["summary"]["source_type_counts"] == {"pdf": 2}
    assert "total_ocr_fallback_applied_pages" in report["summary"]
    assert "total_ocr_text_elements" in report["summary"]
    assert "total_image_only_candidate_pages" in report["summary"]
    assert "total_textless_pages" in report["summary"]
    assert report["summary"]["translation_stress_counts"] == {"off": 2}
    assert report["summary"]["total_translation_stress_elements"] == 0
    assert "total_fidelity_replacement_elements" in report["summary"]
    assert "total_fidelity_replacement_conflicts" in report["summary"]
    assert all(0 <= case["visual_similarity"] <= 1 for case in report["cases"])
    assert all("dimension_match" in case for case in report["cases"])
    assert all(case["source_type"] == "pdf" for case in report["cases"])
    assert all(case["input_kind"] == "auto" for case in report["cases"])
    assert all("worst_page" in case for case in report["cases"])
    assert all("image_count" in case for case in report["cases"])
    assert all("text_run_count" in case for case in report["cases"])
    assert all("mixed_inline_style_element_count" in case for case in report["cases"])
    assert all("multi_column_element_count" in case for case in report["cases"])
    assert all("recursive_xy_cut_element_count" in case for case in report["cases"])
    assert all("mixed_table_column_flow_element_count" in case for case in report["cases"])
    assert all("grid_island_element_count" in case for case in report["cases"])
    assert all("table_row_major_element_count" in case for case in report["cases"])
    assert all("spatial_graph_element_count" in case for case in report["cases"])
    assert all("box_flow_element_count" in case for case in report["cases"])
    assert all("successor_consensus_arbitration_element_count" in case for case in report["cases"])
    assert all("fidelity_replacement_element_count" in case for case in report["cases"])
    assert all("fidelity_replacement_policy_counts" in case for case in report["cases"])
    assert all("reading_order_artifact_element_count" in case for case in report["cases"])
    assert all("reading_order_artifact_counts" in case for case in report["cases"])
    assert all("reading_order_footnote_element_count" in case for case in report["cases"])
    assert all("reading_order_sidebar_element_count" in case for case in report["cases"])
    assert all("reading_order_sidebar_counts" in case for case in report["cases"])
    assert all("reading_order_stream_element_count" in case for case in report["cases"])
    assert all("reading_order_stream_count" in case for case in report["cases"])
    assert all("reading_order_stream_type_counts" in case for case in report["cases"])
    assert all("reading_order_stream_id_counts" in case for case in report["cases"])
    assert all("reading_order_sidecar_proposal" in case for case in report["cases"])
    assert all(Path(case["reading_order_sidecar_proposal"]).exists() for case in report["cases"])
    assert all("reading_order_sidecar_proposal_semantic_report" in case for case in report["cases"])
    assert all(Path(case["reading_order_sidecar_proposal_semantic_report"]).exists() for case in report["cases"])
    assert all("reading_order_proposal_stream_count" in case for case in report["cases"])
    assert all("reading_order_proposal_successor_edge_count" in case for case in report["cases"])
    assert all("reading_order_proposal_semantic_successor_precision" in case for case in report["cases"])
    assert all("reading_order_proposal_semantic_strict_anchor_path_coverage" in case for case in report["cases"])
    assert all("reading_order_proposal_semantic_reviewable_anchor_path_coverage" in case for case in report["cases"])
    assert all("reading_order_caption_element_count" in case for case in report["cases"])
    assert all("reading_order_caption_counts" in case for case in report["cases"])
    assert all("reading_order_caption_targeted_element_count" in case for case in report["cases"])
    assert all("reading_order_caption_orphan_element_count" in case for case in report["cases"])
    assert all("reading_order_caption_target_coverage_ratio" in case for case in report["cases"])
    assert all("reading_order_caption_target_counts" in case for case in report["cases"])
    assert all("reading_order_strategy_counts" in case for case in report["cases"])
    assert all("reading_order_confidence_element_count" in case for case in report["cases"])
    assert all("reading_order_mean_confidence" in case for case in report["cases"])
    assert all("reading_order_low_confidence_element_count" in case for case in report["cases"])
    assert all("reading_order_evidence_counts" in case for case in report["cases"])
    assert all("reading_order_box_flow_pair_count" in case for case in report["cases"])
    assert all("reading_order_box_flow_disagreement_pair_count" in case for case in report["cases"])
    assert all("reading_order_box_flow_disagreement_ratio" in case for case in report["cases"])
    assert all("reading_order_box_flow_disagreement_page_count" in case for case in report["cases"])
    assert all("reading_order_box_flow_successor_edge_count" in case for case in report["cases"])
    assert all("reading_order_box_flow_successor_disagreement_count" in case for case in report["cases"])
    assert all("reading_order_box_flow_successor_disagreement_ratio" in case for case in report["cases"])
    assert all("reading_order_box_flow_successor_disagreement_page_count" in case for case in report["cases"])
    assert all("reading_order_relation_graph_pair_count" in case for case in report["cases"])
    assert all("reading_order_relation_graph_disagreement_pair_count" in case for case in report["cases"])
    assert all("reading_order_relation_graph_disagreement_ratio" in case for case in report["cases"])
    assert all("reading_order_relation_graph_disagreement_page_count" in case for case in report["cases"])
    assert all("reading_order_relation_graph_successor_edge_count" in case for case in report["cases"])
    assert all("reading_order_relation_graph_successor_disagreement_count" in case for case in report["cases"])
    assert all("reading_order_relation_graph_successor_disagreement_ratio" in case for case in report["cases"])
    assert all("reading_order_relation_graph_successor_disagreement_page_count" in case for case in report["cases"])
    assert all("reading_order_relation_graph_path_cover_edge_count" in case for case in report["cases"])
    assert all("reading_order_relation_graph_tied_edge_count" in case for case in report["cases"])
    assert all("reading_order_relation_graph_tied_edge_ratio" in case for case in report["cases"])
    assert all("reading_order_relation_graph_mean_minimum_margin" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_pair_count" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_disagreement_pair_count" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_disagreement_ratio" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_disagreement_page_count" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_successor_edge_count" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_successor_disagreement_count" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_successor_disagreement_ratio" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_successor_disagreement_page_count" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_candidate_page_count" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_mean_candidate_count" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_selected_edge_support_ratio" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_selected_edge_coverage_ratio" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_conflicted_edge_ratio" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_high_agreement_page_count" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_medium_agreement_page_count" in case for case in report["cases"])
    assert all("reading_order_successor_consensus_low_agreement_page_count" in case for case in report["cases"])
    assert all("reading_order_candidate_page_diagnostics" in case for case in report["cases"])
    assert all("reading_order_candidate_page_recommendation_counts" in case for case in report["cases"])
    assert all("reading_order_candidate_stream_diagnostics" in case for case in report["cases"])
    assert all("reading_order_candidate_stream_count" in case for case in report["cases"])
    assert all("reading_order_candidate_stream_recommendation_counts" in case for case in report["cases"])
    assert all("layout_region_counts" in case for case in report["cases"])
    assert all("table_region_count" in case for case in report["cases"])
    assert all("raster_fallback_count" in case for case in report["cases"])
    assert all("vector_background_page_count" in case for case in report["cases"])
    assert all("ocr_fallback" in case for case in report["cases"])
    assert all("ocr_fallback_applied_page_count" in case for case in report["cases"])
    assert all("ocr_text_count" in case for case in report["cases"])
    assert all("image_only_candidate_page_count" in case for case in report["cases"])
    assert all("textless_page_count" in case for case in report["cases"])
    assert all("reading_order_risk_score" in case for case in report["cases"])
    assert all("reading_order_risk_level" in case for case in report["cases"])
    assert all("reading_order_column_geometry_page_count" in case for case in report["cases"])
    assert all("reading_order_unlabeled_text_risk_count" in case for case in report["cases"])
    assert all("semantic_candidate_order_metrics" in case for case in report["cases"])
    assert all("semantic_best_candidate_by_successor" in case for case in report["cases"])
    assert all("semantic_best_candidate_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_best_candidate_by_relation_successor" in case for case in report["cases"])
    assert all("semantic_best_candidate_relation_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_best_candidate_by_stream_successor" in case for case in report["cases"])
    assert all("semantic_best_candidate_stream_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_candidate_arbitration_recommendation" in case for case in report["cases"])
    assert all("semantic_candidate_arbitration_candidate" in case for case in report["cases"])
    assert all("semantic_candidate_successor_delta" in case for case in report["cases"])
    assert all("semantic_candidate_relation_successor_delta" in case for case in report["cases"])
    assert all("semantic_candidate_stream_successor_delta" in case for case in report["cases"])
    assert all("semantic_stream_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_stream_successor_correct_count" in case for case in report["cases"])
    assert all("semantic_stream_successor_total_count" in case for case in report["cases"])
    assert all("semantic_stream_precedence_accuracy" in case for case in report["cases"])
    assert all("semantic_stream_precedence_correct_count" in case for case in report["cases"])
    assert all("semantic_stream_precedence_total_count" in case for case in report["cases"])
    assert all("semantic_stream_assignment_label_count" in case for case in report["cases"])
    assert all("semantic_stream_assignment_found_count" in case for case in report["cases"])
    assert all("semantic_stream_assignment_id_mismatch_count" in case for case in report["cases"])
    assert all("semantic_stream_assignment_type_mismatch_count" in case for case in report["cases"])
    assert all("semantic_stream_assignment_type_confusion_counts" in case for case in report["cases"])
    assert all("semantic_stream_assignment_id_accuracy" in case for case in report["cases"])
    assert all("semantic_stream_assignment_type_accuracy" in case for case in report["cases"])
    assert all("semantic_visual_yx_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_box_flow_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_relation_graph_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_relation_graph_relation_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_relation_graph_stream_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_structure_relation_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_structure_relation_relation_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_structure_relation_stream_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_successor_consensus_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_external_structure_successor_accuracy" in case for case in report["cases"])
    assert all(case["font_profile"] == "browser-default" for case in report["cases"])
    assert all(case["raster_policy"] == "dense" for case in report["cases"])
    assert all(case["html_mode"] == "structured" for case in report["cases"])
    assert all(case["font_size_scale"] == 1.0 for case in report["cases"])
    assert all(case["text_fit"] == "none" for case in report["cases"])
    assert all(case["fidelity_background"] == "none" for case in report["cases"])
    assert all(case["vector_background_page_count"] == 0 for case in report["cases"])
    assert "total_text_runs" in report["summary"]
    assert "total_mixed_inline_style_elements" in report["summary"]
    assert "total_multi_column_elements" in report["summary"]
    assert "total_image_elements" in report["summary"]
    assert "total_recursive_xy_cut_elements" in report["summary"]
    assert "total_mixed_table_column_flow_elements" in report["summary"]
    assert "total_grid_island_elements" in report["summary"]
    assert "total_table_row_major_elements" in report["summary"]
    assert "total_spatial_graph_elements" in report["summary"]
    assert "total_box_flow_elements" in report["summary"]
    assert "total_successor_consensus_arbitration_elements" in report["summary"]
    assert "total_reading_order_artifact_elements" in report["summary"]
    assert "reading_order_artifact_counts" in report["summary"]
    assert "total_reading_order_footnote_elements" in report["summary"]
    assert "total_reading_order_sidebar_elements" in report["summary"]
    assert "reading_order_sidebar_counts" in report["summary"]
    assert "total_reading_order_stream_elements" in report["summary"]
    assert "total_reading_order_streams" in report["summary"]
    assert "reading_order_stream_type_counts" in report["summary"]
    assert "reading_order_stream_id_counts" in report["summary"]
    assert "total_reading_order_proposal_streams" in report["summary"]
    assert "total_reading_order_proposal_successor_edges" in report["summary"]
    assert "total_reading_order_proposal_review_transitions" in report["summary"]
    assert "reading_order_proposal_stream_type_counts" in report["summary"]
    assert "total_reading_order_proposal_semantic_successor_correct_edges" in report["summary"]
    assert "reading_order_proposal_semantic_successor_coverage" in report["summary"]
    assert "total_reading_order_proposal_semantic_anchor_transitions" in report["summary"]
    assert "reading_order_proposal_semantic_strict_anchor_path_coverage" in report["summary"]
    assert "reading_order_proposal_semantic_reviewable_anchor_path_coverage" in report["summary"]
    assert "total_reading_order_caption_elements" in report["summary"]
    assert "reading_order_caption_counts" in report["summary"]
    assert "total_reading_order_caption_targeted_elements" in report["summary"]
    assert "total_reading_order_caption_orphan_elements" in report["summary"]
    assert "mean_reading_order_caption_target_coverage_ratio" in report["summary"]
    assert "reading_order_caption_target_counts" in report["summary"]
    assert "reading_order_strategy_counts" in report["summary"]
    assert "mean_reading_order_confidence" in report["summary"]
    assert "total_reading_order_low_confidence_elements" in report["summary"]
    assert "reading_order_evidence_counts" in report["summary"]
    assert "total_reading_order_box_flow_pairs" in report["summary"]
    assert "total_reading_order_box_flow_disagreement_pairs" in report["summary"]
    assert "mean_reading_order_box_flow_disagreement_ratio" in report["summary"]
    assert "total_reading_order_box_flow_disagreement_pages" in report["summary"]
    assert "total_reading_order_box_flow_successor_edges" in report["summary"]
    assert "total_reading_order_box_flow_successor_disagreements" in report["summary"]
    assert "mean_reading_order_box_flow_successor_disagreement_ratio" in report["summary"]
    assert "total_reading_order_box_flow_successor_disagreement_pages" in report["summary"]
    assert "total_reading_order_relation_graph_pairs" in report["summary"]
    assert "total_reading_order_relation_graph_disagreement_pairs" in report["summary"]
    assert "mean_reading_order_relation_graph_disagreement_ratio" in report["summary"]
    assert "total_reading_order_relation_graph_disagreement_pages" in report["summary"]
    assert "total_reading_order_relation_graph_successor_edges" in report["summary"]
    assert "total_reading_order_relation_graph_successor_disagreements" in report["summary"]
    assert "mean_reading_order_relation_graph_successor_disagreement_ratio" in report["summary"]
    assert "total_reading_order_relation_graph_successor_disagreement_pages" in report["summary"]
    assert "total_reading_order_relation_graph_path_cover_edges" in report["summary"]
    assert "total_reading_order_relation_graph_tied_edges" in report["summary"]
    assert "mean_reading_order_relation_graph_tied_edge_ratio" in report["summary"]
    assert "mean_reading_order_relation_graph_minimum_margin" in report["summary"]
    assert "total_reading_order_successor_consensus_pairs" in report["summary"]
    assert "total_reading_order_successor_consensus_disagreement_pairs" in report["summary"]
    assert "mean_reading_order_successor_consensus_disagreement_ratio" in report["summary"]
    assert "total_reading_order_successor_consensus_disagreement_pages" in report["summary"]
    assert "total_reading_order_successor_consensus_successor_edges" in report["summary"]
    assert "total_reading_order_successor_consensus_successor_disagreements" in report["summary"]
    assert "mean_reading_order_successor_consensus_successor_disagreement_ratio" in report["summary"]
    assert "total_reading_order_successor_consensus_successor_disagreement_pages" in report["summary"]
    assert "total_reading_order_successor_consensus_candidate_pages" in report["summary"]
    assert "mean_reading_order_successor_consensus_candidate_count" in report["summary"]
    assert "total_reading_order_successor_consensus_candidate_edges" in report["summary"]
    assert "total_reading_order_successor_consensus_selected_edges" in report["summary"]
    assert "mean_reading_order_successor_consensus_selected_edge_support_ratio" in report["summary"]
    assert "mean_reading_order_successor_consensus_selected_edge_coverage_ratio" in report["summary"]
    assert "mean_reading_order_successor_consensus_conflicted_edge_ratio" in report["summary"]
    assert "total_reading_order_successor_consensus_high_agreement_pages" in report["summary"]
    assert "total_reading_order_successor_consensus_medium_agreement_pages" in report["summary"]
    assert "total_reading_order_successor_consensus_low_agreement_pages" in report["summary"]
    assert "reading_order_candidate_page_recommendation_counts" in report["summary"]
    assert "total_reading_order_candidate_streams" in report["summary"]
    assert "reading_order_candidate_stream_recommendation_counts" in report["summary"]
    assert "font_profile_counts" in report["summary"]
    assert report["summary"]["html_mode_counts"] == {"structured": 2}
    assert report["summary"]["font_size_scale_counts"] == {"1.0": 2}
    assert report["summary"]["text_fit_counts"] == {"none": 2}
    assert report["summary"]["fidelity_background_counts"] == {"none": 2}
    assert report["summary"]["translation_stress_counts"] == {"off": 2}
    assert report["summary"]["mean_translation_stress_char_expansion_ratio"] is None
    assert report["summary"]["total_fidelity_replacement_elements"] == 0
    assert report["summary"]["total_fidelity_replacement_overflows"] == 0
    assert report["summary"]["total_fidelity_replacement_conflicts"] == 0
    assert report["summary"]["total_fidelity_replacement_conflict_targets"] == 0
    assert report["summary"]["total_fidelity_replacement_same_stream_conflict_targets"] == 0
    assert report["summary"]["total_fidelity_replacement_cross_stream_conflict_targets"] == 0
    assert report["summary"]["min_fidelity_replacement_fit_scale"] is None
    assert report["summary"]["mean_fidelity_replacement_fit_scale"] is None
    assert report["summary"]["fidelity_replacement_policy_counts"] == {}
    assert report["summary"]["fidelity_replacement_conflict_stream_type_pair_counts"] == {}
    assert "layout_region_counts" in report["summary"]
    assert "total_table_regions" in report["summary"]
    assert "total_raster_fallbacks" in report["summary"]
    assert report["summary"]["total_vector_background_pages"] == 0
    assert "mean_reading_order_risk_score" in report["summary"]
    assert "reading_order_risk_level_counts" in report["summary"]
    assert "total_reading_order_column_geometry_pages" in report["summary"]
    assert "total_reading_order_repeated_anchor_pages" in report["summary"]
    assert "max_reading_order_repeated_anchor_columns" in report["summary"]
    assert "total_reading_order_table_like_pages" in report["summary"]
    assert "total_reading_order_table_like_visual_yx_pages" in report["summary"]
    assert "total_reading_order_unlabeled_text_risk_count" in report["summary"]
    assert report["summary"]["semantic_case_count"] == 2
    assert report["summary"]["mean_semantic_order_pair_accuracy"] == 1
    assert report["summary"]["mean_semantic_successor_accuracy"] == 1
    assert report["summary"]["mean_semantic_relation_successor_accuracy"] is None
    assert report["summary"]["total_semantic_relation_successor_count"] == 0
    assert report["summary"]["mean_semantic_stream_successor_accuracy"] is None
    assert report["summary"]["total_semantic_stream_successor_count"] == 0
    assert report["summary"]["mean_semantic_stream_assignment_id_accuracy"] is None
    assert report["summary"]["mean_semantic_stream_assignment_type_accuracy"] is None
    assert report["summary"]["total_semantic_stream_assignment_label_count"] == 0
    assert report["summary"]["total_semantic_stream_assignment_found_count"] == 0
    assert report["summary"]["total_semantic_stream_assignment_id_mismatch_count"] == 0
    assert report["summary"]["total_semantic_stream_assignment_type_mismatch_count"] == 0
    assert report["summary"]["semantic_stream_assignment_type_confusion_counts"] == {}
    assert report["summary"]["mean_semantic_sequence_similarity"] == 1
    assert "total_semantic_successor_correct_count" in report["summary"]
    assert "total_semantic_successor_count" in report["summary"]
    assert "semantic_best_candidate_by_successor_counts" in report["summary"]
    assert "semantic_best_candidate_by_relation_successor_counts" in report["summary"]
    assert "semantic_best_candidate_by_stream_successor_counts" in report["summary"]
    assert "semantic_candidate_arbitration_recommendation_counts" in report["summary"]
    assert "semantic_candidate_arbitration_candidate_counts" in report["summary"]
    assert "mean_semantic_candidate_successor_delta" in report["summary"]
    assert "mean_semantic_candidate_relation_successor_delta" in report["summary"]
    assert "mean_semantic_candidate_stream_successor_delta" in report["summary"]
    assert "mean_semantic_visual_yx_successor_accuracy" in report["summary"]
    assert "mean_semantic_box_flow_successor_accuracy" in report["summary"]
    assert "mean_semantic_relation_graph_successor_accuracy" in report["summary"]
    assert "mean_semantic_relation_graph_relation_successor_accuracy" in report["summary"]
    assert "mean_semantic_relation_graph_stream_successor_accuracy" in report["summary"]
    assert "mean_semantic_structure_relation_successor_accuracy" in report["summary"]
    assert "mean_semantic_structure_relation_relation_successor_accuracy" in report["summary"]
    assert "mean_semantic_structure_relation_stream_successor_accuracy" in report["summary"]
    assert "mean_semantic_successor_consensus_successor_accuracy" in report["summary"]
    assert "mean_semantic_external_structure_successor_accuracy" in report["summary"]
    assert "total_semantic_ignored_text_count" in report["summary"]
    assert all(case["semantic_ground_truth_available"] for case in report["cases"])
    assert all("semantic_successor_accuracy" in case for case in report["cases"])
    assert all("semantic_successor_correct_count" in case for case in report["cases"])
    assert all("semantic_successor_total_count" in case for case in report["cases"])
    assert all("semantic_ignored_text_count" in case for case in report["cases"])
    assert all(case["element_count"] > 0 for case in report["cases"])
    assert (tmp_path / "benchmark" / "benchmark_report.json").exists()
    assert (tmp_path / "benchmark" / "benchmark_summary.csv").exists()


def test_benchmark_can_limit_large_documents_to_first_pages(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")
    multipage_pdf = next(pdf for pdf in pdfs if pdf.name == "multipage_mixed.pdf")
    report = run_benchmark([multipage_pdf], tmp_path / "benchmark-max-pages", dpi=96, max_pages=1)
    case = report["cases"][0]
    csv_text = (tmp_path / "benchmark-max-pages" / "benchmark_summary.csv").read_text(encoding="utf-8")
    quality = json.loads(Path(case["quality_report"]).read_text(encoding="utf-8"))

    assert report["max_pages"] == 1
    assert case["max_pages"] == 1
    assert case["page_count"] == 1
    assert quality["max_pages"] == 1
    assert quality["expected_page_count"] == 1
    assert quality["actual_page_count"] == 1
    assert case["semantic_ground_truth_available"] is True
    assert case["semantic_expected_text_count"] == 4
    assert "max_pages" in csv_text


def test_benchmark_can_score_explicit_source_page_ranges(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")
    multipage_pdf = next(pdf for pdf in pdfs if pdf.name == "multipage_mixed.pdf")
    report = run_benchmark([multipage_pdf], tmp_path / "benchmark-page-ranges", dpi=96, page_ranges="2")
    case = report["cases"][0]
    csv_text = (tmp_path / "benchmark-page-ranges" / "benchmark_summary.csv").read_text(encoding="utf-8")
    quality = json.loads(Path(case["quality_report"]).read_text(encoding="utf-8"))
    semantic = json.loads(Path(case["semantic_report"]).read_text(encoding="utf-8"))

    assert report["max_pages"] is None
    assert report["page_ranges"] == "2"
    assert report["sampled_page_numbers"] == [2]
    assert case["max_pages"] is None
    assert case["page_ranges"] == "2"
    assert case["sampled_page_numbers"] == [2]
    assert case["page_count"] == 1
    assert quality["max_pages"] is None
    assert quality["expected_page_indices"] == [1]
    assert quality["actual_page_indices"] is None
    assert quality["expected_page_count"] == 1
    assert quality["actual_page_count"] == 1
    assert quality["pages"][0]["expected_source_page_index"] == 1
    assert quality["pages"][0]["expected_source_page_number"] == 2
    assert quality["pages"][0]["actual_source_page_index"] == 0
    assert quality["pages"][0]["actual_source_page_number"] == 1
    assert semantic["ground_truth_available"] is True
    assert semantic["pages"][0]["truth_page_index"] == 1
    assert semantic["pages"][0]["page_index"] == 1
    assert case["semantic_ground_truth_available"] is True
    assert case["semantic_expected_text_count"] == 4
    assert "page_ranges" in csv_text
    assert "sampled_page_numbers" in csv_text


def test_benchmark_rejects_page_ranges_with_max_pages(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")
    multipage_pdf = next(pdf for pdf in pdfs if pdf.name == "multipage_mixed.pdf")

    with pytest.raises(ValueError, match="page_ranges cannot be combined with max_pages"):
        run_benchmark([multipage_pdf], tmp_path / "benchmark-invalid-page-ranges", dpi=96, max_pages=1, page_ranges="2")


def test_benchmark_can_score_image_source_with_structure_json(tmp_path: Path) -> None:
    image_path = _create_image_source(tmp_path)
    structure_json = tmp_path / "benchmark_image_source.structure.json"
    structure_json.write_text(
        json.dumps(
            {
                "source": "pp-structurev3",
                "res": {
                    "page_index": 0,
                    "parsing_res_list": [
                        {
                            "block_label": "title",
                            "block_bbox": [40, 42, 360, 88],
                            "block_order": 1,
                            "block_content": "IMAGE SOURCE",
                            "confidence": 0.94,
                        },
                        {
                            "block_label": "text",
                            "block_bbox": [42, 126, 420, 160],
                            "block_order": 2,
                            "block_content": "STRUCTURE JSON TEXT",
                            "confidence": 0.92,
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    report = run_benchmark(
        [image_path],
        tmp_path / "benchmark-image-source",
        dpi=96,
        input_kind="image",
        image_dpi=96,
        structure_jsons=[structure_json],
        html_mode="structured",
        fidelity_background="raster",
    )
    case = report["cases"][0]
    quality = json.loads(Path(case["quality_report"]).read_text(encoding="utf-8"))
    csv_text = (tmp_path / "benchmark-image-source" / "benchmark_summary.csv").read_text(encoding="utf-8")

    assert report["input_kind"] == "image"
    assert report["image_dpi"] == 96
    assert report["summary"]["source_type_counts"] == {"image": 1}
    assert report["summary"]["semantic_layer_driver_counts"] == {"structure-json": 1}
    assert case["source_type"] == "image"
    assert case["source"] == str(image_path)
    assert case["semantic_layer_driver"] == "structure-json"
    assert case["semantic_layer_payload_kind"] == "structure-json"
    assert case["semantic_layer_structure_role"] == "semantic-driver"
    assert case["image_dpi"] == 96
    assert case["page_count"] == 1
    assert case["image_count"] == 1
    assert case["editable_element_count"] == 2
    assert case["structure_evidence_matched_element_count"] == 2
    assert quality["expected_source_type"] == "image"
    assert quality["image_dpi"] == 96
    assert quality["expected_page_count"] == 1
    assert quality["actual_page_count"] == 1
    assert quality["dimension_match"] is True
    assert "source" in csv_text
    assert "source_type" in csv_text
    assert "semantic_layer_driver" in csv_text
    assert "image_dpi" in csv_text


def test_reading_order_geometry_profile_separates_text_flow_columns_from_tables() -> None:
    text_flow_boxes: list[BBox] = []
    table_boxes: list[BBox] = []
    for row in range(10):
        y0 = 70 + row * 13
        text_flow_boxes.extend(
            [
                BBox(x0=52, y0=y0, x1=176, y1=y0 + 10),
                BBox(x0=224, y0=y0, x1=348, y1=y0 + 10),
                BBox(x0=396, y0=y0, x1=520, y1=y0 + 10),
            ]
        )
        table_boxes.extend(
            [
                BBox(x0=80, y0=y0, x1=106, y1=y0 + 10),
                BBox(x0=218, y0=y0, x1=247, y1=y0 + 10),
                BBox(x0=398, y0=y0, x1=427, y1=y0 + 10),
                BBox(x0=518, y0=y0, x1=545, y1=y0 + 10),
            ]
        )

    text_flow = _page_reading_order_geometry_profile(576, text_flow_boxes)
    table = _page_reading_order_geometry_profile(576, table_boxes)

    assert text_flow["repeated_anchor_column_count"] == 3
    assert text_flow["table_like"] is True
    assert text_flow["text_flow_column_geometry"] is True
    assert table["repeated_anchor_column_count"] == 3
    assert table["table_like"] is True
    assert table["text_flow_column_geometry"] is False


def test_semantic_candidate_orders_include_external_structure_order() -> None:
    document = _document_with_candidate_text_boxes(
        [
            ("left-one", "Left column one.", BBox(x0=10, y0=10, x1=70, y1=20), 1, 1),
            ("right-one", "Right column one.", BBox(x0=110, y0=10, x1=170, y1=20), 2, 2),
            ("left-two", "Left column two.", BBox(x0=10, y0=30, x1=70, y1=40), 3, 1),
            ("right-two", "Right column two.", BBox(x0=110, y0=30, x1=170, y1=40), 4, 2),
        ]
    )

    candidates = _semantic_candidate_orders(document)

    assert candidates["external_structure"][0] == ["left-one", "left-two", "right-one", "right-two"]
    assert candidates["successor_consensus"][0] == ["left-one", "left-two", "right-one", "right-two"]


def test_external_structure_candidate_order_fuses_partial_orders_stably() -> None:
    document = _document_with_candidate_text_boxes(
        [
            ("title", "Document title", BBox(x0=10, y0=5, x1=118, y1=16), 1, None),
            ("right-one", "Right one.", BBox(x0=110, y0=30, x1=176, y1=40), 2, 2),
            ("left-one", "Left one.", BBox(x0=10, y0=30, x1=76, y1=40), 3, 1),
            ("aside", "Unordered note", BBox(x0=80, y0=45, x1=102, y1=55), 4, None),
            ("right-two", "Right two.", BBox(x0=110, y0=60, x1=176, y1=70), 5, 2),
            ("left-two", "Left two.", BBox(x0=10, y0=60, x1=76, y1=70), 6, 1),
            ("footer", "Page 1", BBox(x0=10, y0=180, x1=52, y1=190), 7, None),
        ]
    )

    order = _external_structure_candidate_order(document.pages[0].elements)

    assert [document.pages[0].elements[index].id for index in order] == [
        "title",
        "left-one",
        "aside",
        "left-two",
        "right-one",
        "right-two",
        "footer",
    ]


def test_semantic_candidate_orders_include_external_structure_relations() -> None:
    document = _document_with_candidate_text_boxes(
        [
            ("a", "A", BBox(x0=10, y0=10, x1=70, y1=20), 1, 0),
            ("c", "C", BBox(x0=110, y0=10, x1=170, y1=20), 2, 0),
            ("b", "B", BBox(x0=10, y0=30, x1=70, y1=40), 3, 0),
            ("d", "D", BBox(x0=110, y0=30, x1=170, y1=40), 4, 0),
        ]
    )
    boxes = [
        {"id": element.id, "label": "text", "bbox": element.bbox_px.as_list(), "text": element.source_text}
        for element in document.pages[0].elements
    ]
    payload = {
        "source": "relation-model",
        "res": {
            "page_index": 0,
            "layout_det_res": {"boxes": boxes},
            "successor_edges": [["a", "b"], ["c", "d"]],
            "precedence_edges": [["b", "d"]],
        },
    }
    apply_structure_evidence(document, payload)

    candidates = _semantic_candidate_orders(document)

    assert candidates["external_structure"][0] == ["a", "b", "c", "d"]
    assert candidates["successor_consensus"][0] == ["a", "b", "c", "d"]


def test_semantic_candidate_orders_include_structure_relation_order() -> None:
    elements = [
        ElementIR(
            id="header",
            page_index=0,
            type="text",
            bbox_pdf=BBox(x0=20, y0=8, x1=180, y1=18),
            bbox_px=BBox(x0=20, y0=8, x1=180, y1=18),
            source_text="Running header",
            reading_order=1,
            metadata={
                "source": "unit-test",
                "reading_order_scope": "page-artifact",
                "reading_order_artifact_type": "header",
            },
        ),
        ElementIR(
            id="body",
            page_index=0,
            type="text",
            bbox_pdf=BBox(x0=40, y0=42, x1=145, y1=54),
            bbox_px=BBox(x0=40, y0=42, x1=145, y1=54),
            source_text="Body text before figure.",
            reading_order=2,
            metadata={"source": "unit-test"},
        ),
        ElementIR(
            id="sidebar",
            page_index=0,
            type="text",
            bbox_pdf=BBox(x0=4, y0=46, x1=28, y1=88),
            bbox_px=BBox(x0=4, y0=46, x1=28, y1=88),
            source_text="Side note",
            reading_order=3,
            metadata={
                "source": "unit-test",
                "reading_order_scope": "sidebar",
                "reading_order_sidebar_type": "left",
            },
        ),
        ElementIR(
            id="caption",
            page_index=0,
            type="text",
            bbox_pdf=BBox(x0=52, y0=112, x1=160, y1=124),
            bbox_px=BBox(x0=52, y0=112, x1=160, y1=124),
            source_text="Figure 1: A diagram.",
            reading_order=4,
            metadata={
                "source": "unit-test",
                "reading_order_caption_type": "figure",
                "reading_order_caption_target_id": "figure-001",
                "reading_order_caption_target_kind": "figure",
                "reading_order_caption_target_position": "caption-below-target",
                "reading_order_caption_target_bbox_pdf": [48, 68, 162, 104],
            },
        ),
        ElementIR(
            id="footnote",
            page_index=0,
            type="text",
            bbox_pdf=BBox(x0=40, y0=174, x1=150, y1=185),
            bbox_px=BBox(x0=40, y0=174, x1=150, y1=185),
            source_text="1. Footnote",
            reading_order=5,
            metadata={"source": "unit-test", "reading_order_scope": "footnote"},
        ),
        ElementIR(
            id="footer",
            page_index=0,
            type="text",
            bbox_pdf=BBox(x0=82, y0=192, x1=118, y1=198),
            bbox_px=BBox(x0=82, y0=192, x1=118, y1=198),
            source_text="Page 1",
            reading_order=6,
            metadata={
                "source": "unit-test",
                "reading_order_scope": "page-artifact",
                "reading_order_artifact_type": "footer",
            },
        ),
    ]
    page = PageIR(
        page_index=0,
        width_pt=200,
        height_pt=220,
        width_px=200,
        height_px=220,
        render_dpi=72,
        scale_x=1.0,
        scale_y=1.0,
        background_image="",
        elements=elements,
    )
    document = DocumentIR(
        source_pdf="unit.pdf",
        render_dpi=72,
        page_count=1,
        pages=[page],
    )

    candidates = _semantic_candidate_orders(document)

    assert candidates["structure_relation"][0] == [
        "header",
        "body",
        "caption",
        "footnote",
        "sidebar",
        "footer",
    ]
    assert "successor_consensus" in candidates
    assert sorted(candidates["successor_consensus"][0]) == sorted(element.id for element in elements)


def test_candidate_page_diagnostics_recommend_review_for_supported_disagreement() -> None:
    document = _document_with_candidate_text_boxes(
        [
            ("candidate-one", "Candidate one.", BBox(x0=10, y0=10, x1=70, y1=20), 1, 1),
            ("selected-second", "Selected second.", BBox(x0=10, y0=50, x1=70, y1=60), 2, 3),
            ("candidate-second", "Candidate second.", BBox(x0=10, y0=30, x1=70, y1=40), 3, 2),
            ("candidate-fourth", "Candidate fourth.", BBox(x0=10, y0=70, x1=70, y1=80), 4, 4),
        ]
    )

    diagnostics = _reading_order_candidate_page_diagnostics(document)

    assert len(diagnostics) == 1
    assert diagnostics[0]["recommendation"] == "review-consensus"
    assert diagnostics[0]["agreement_level"] == "high"
    assert diagnostics[0]["consensus_successor_disagreement_count"] > 0


def test_candidate_stream_diagnostics_are_local_to_reading_streams() -> None:
    document = _document_with_candidate_text_boxes(
        [
            ("body-one", "Body one.", BBox(x0=10, y0=10, x1=70, y1=20), 1, 1),
            ("body-third", "Body third.", BBox(x0=10, y0=50, x1=70, y1=60), 2, 3),
            ("body-two", "Body two.", BBox(x0=10, y0=30, x1=70, y1=40), 3, 2),
            ("side-one", "Side one.", BBox(x0=130, y0=10, x1=180, y1=20), 4, 10),
            ("side-two", "Side two.", BBox(x0=130, y0=30, x1=180, y1=40), 5, 11),
        ]
    )
    for element in document.pages[0].elements[:3]:
        element.metadata["reading_order_stream_id"] = "body-main"
        element.metadata["reading_order_stream_type"] = "body"
    for element in document.pages[0].elements[3:]:
        element.metadata["reading_order_stream_id"] = "sidebar-right"
        element.metadata["reading_order_stream_type"] = "sidebar-right"

    diagnostics = _reading_order_candidate_stream_diagnostics(document)
    by_stream = {diagnostic["stream_id"]: diagnostic for diagnostic in diagnostics}

    assert set(by_stream) == {"body-main", "sidebar-right"}
    assert by_stream["body-main"]["recommendation"] == "review-consensus"
    assert by_stream["body-main"]["consensus_successor_disagreement_count"] > 0
    assert by_stream["sidebar-right"]["recommendation"] == "keep-selected-supported"
    assert by_stream["sidebar-right"]["consensus_successor_disagreement_count"] == 0


def test_candidate_stream_diagnostics_trust_complete_explicit_successors() -> None:
    document = _document_with_candidate_text_boxes(
        [
            ("first", "First.", BBox(x0=10, y0=10, x1=70, y1=20), 1, None),
            ("second", "Second.", BBox(x0=10, y0=50, x1=70, y1=60), 2, None),
            ("third", "Third.", BBox(x0=10, y0=30, x1=70, y1=40), 3, None),
        ]
    )
    elements = document.pages[0].elements
    for element in elements:
        element.metadata["reading_order_stream_id"] = "docling-body-page-001-run-001"
        element.metadata["reading_order_stream_type"] = "body"
    elements[0].metadata["external_structure_successor_ids"] = ["second"]
    elements[1].metadata["external_structure_successor_ids"] = ["third"]

    diagnostics = _reading_order_candidate_stream_diagnostics(document)

    assert diagnostics[0]["explicit_successor_edge_count"] == 2
    assert diagnostics[0]["explicit_successor_coverage"] == 1.0
    assert diagnostics[0]["recommendation"] == "keep-selected-external-successors"


def test_semantic_candidate_arbitration_recommends_better_candidate() -> None:
    metrics = _semantic_candidate_arbitration_metrics(
        selected_pairwise=0.75,
        selected_successor=0.5,
        selected_relation_successor=None,
        selected_stream_successor=None,
        candidate_metrics={
            "visual_yx": {
                "semantic_order_pair_accuracy": 0.75,
                "semantic_successor_accuracy": 0.5,
            },
            "relation_graph": {
                "semantic_order_pair_accuracy": 0.9,
                "semantic_successor_accuracy": 0.75,
            },
        },
    )

    assert metrics["semantic_candidate_arbitration_recommendation"] == "consider-relation_graph"
    assert metrics["semantic_candidate_arbitration_candidate"] == "relation_graph"
    assert metrics["semantic_candidate_successor_delta"] == 0.25
    assert metrics["semantic_candidate_pairwise_delta"] == 0.15


def test_semantic_candidate_arbitration_uses_relation_edges_when_available() -> None:
    metrics = _semantic_candidate_arbitration_metrics(
        selected_pairwise=1.0,
        selected_successor=1.0,
        selected_relation_successor=0.5,
        selected_stream_successor=None,
        candidate_metrics={
            "visual_yx": {
                "semantic_order_pair_accuracy": 1.0,
                "semantic_successor_accuracy": 1.0,
                "semantic_relation_successor_accuracy": 0.5,
                "semantic_relation_precedence_accuracy": 1.0,
            },
            "structure_relation": {
                "semantic_order_pair_accuracy": 1.0,
                "semantic_successor_accuracy": 1.0,
                "semantic_relation_successor_accuracy": 1.0,
                "semantic_relation_precedence_accuracy": 1.0,
            },
        },
    )

    assert metrics["semantic_candidate_arbitration_recommendation"] == "consider-structure_relation"
    assert metrics["semantic_candidate_arbitration_candidate"] == "structure_relation"
    assert metrics["semantic_candidate_relation_successor_delta"] == 0.5
    assert metrics["semantic_candidate_successor_delta"] == 0


def test_semantic_candidate_arbitration_uses_stream_edges_when_available() -> None:
    metrics = _semantic_candidate_arbitration_metrics(
        selected_pairwise=1.0,
        selected_successor=1.0,
        selected_relation_successor=None,
        selected_stream_successor=0.5,
        candidate_metrics={
            "selected": {
                "semantic_order_pair_accuracy": 1.0,
                "semantic_successor_accuracy": 1.0,
                "semantic_stream_successor_accuracy": 0.5,
                "semantic_stream_precedence_accuracy": 1.0,
            },
            "stream_candidate": {
                "semantic_order_pair_accuracy": 1.0,
                "semantic_successor_accuracy": 1.0,
                "semantic_stream_successor_accuracy": 1.0,
                "semantic_stream_precedence_accuracy": 1.0,
            },
        },
    )

    assert metrics["semantic_candidate_arbitration_recommendation"] == "consider-stream_candidate"
    assert metrics["semantic_candidate_arbitration_candidate"] == "stream_candidate"
    assert metrics["semantic_candidate_stream_successor_delta"] == 0.5
    assert metrics["semantic_candidate_successor_delta"] == 0


def test_benchmark_can_score_fidelity_overlay_mode(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:1]
    report = run_benchmark(
        pdfs,
        tmp_path / "benchmark-fidelity",
        dpi=96,
        html_mode="fidelity",
        fidelity_background="svg",
    )
    case = report["cases"][0]
    csv_text = (tmp_path / "benchmark-fidelity" / "benchmark_summary.csv").read_text(encoding="utf-8")

    assert report["html_mode"] == "fidelity"
    assert report["fidelity_background"] == "svg"
    assert case["html_mode"] == "fidelity"
    assert case["fidelity_background"] == "svg"
    assert case["translation_stress"] == "off"
    assert case["font_size_scale"] == 1.0
    assert case["vector_background_page_count"] == case["page_count"]
    assert case["fidelity_replacement_element_count"] == 0
    assert case["fidelity_replacement_overflow_count"] == 0
    assert case["fidelity_replacement_conflict_count"] == 0
    assert case["fidelity_replacement_mean_fit_scale"] is None
    assert "html_mode" in csv_text
    assert "font_size_scale" in csv_text
    assert "text_fit" in csv_text
    assert "fidelity_background" in csv_text
    assert "translation_stress" in csv_text
    assert "translation_stress_element_count" in csv_text
    assert "fidelity_replacement_element_count" in csv_text
    assert "fidelity_replacement_overflow_count" in csv_text
    assert "fidelity_replacement_conflict_count" in csv_text
    assert "fidelity_replacement_mean_fit_scale" in csv_text
    assert "fidelity_replacement_policy_counts" in csv_text
    assert "vector_background_page_count" in csv_text
    assert "ocr_fallback_applied_page_count" in csv_text
    assert "ocr_text_count" in csv_text
    assert "mixed_table_column_flow_element_count" in csv_text
    assert "grid_island_element_count" in csv_text
    assert "table_row_major_element_count" in csv_text
    assert "spatial_graph_element_count" in csv_text
    assert "box_flow_element_count" in csv_text
    assert "reading_order_artifact_element_count" in csv_text
    assert "reading_order_footnote_element_count" in csv_text
    assert "reading_order_sidebar_element_count" in csv_text
    assert "reading_order_sidebar_counts" in csv_text
    assert "reading_order_stream_element_count" in csv_text
    assert "reading_order_stream_count" in csv_text
    assert "reading_order_stream_type_counts" in csv_text
    assert "reading_order_stream_id_counts" in csv_text
    assert "reading_order_proposal_stream_count" in csv_text
    assert "reading_order_proposal_successor_edge_count" in csv_text
    assert "reading_order_proposal_review_transition_count" in csv_text
    assert "reading_order_proposal_semantic_successor_precision" in csv_text
    assert "reading_order_proposal_semantic_review_successor_coverage" in csv_text
    assert "reading_order_proposal_semantic_strict_anchor_path_coverage" in csv_text
    assert "reading_order_proposal_semantic_reviewable_anchor_path_coverage" in csv_text
    assert "reading_order_caption_element_count" in csv_text
    assert "reading_order_caption_counts" in csv_text
    assert "reading_order_caption_targeted_element_count" in csv_text
    assert "reading_order_caption_target_coverage_ratio" in csv_text
    assert "reading_order_caption_target_counts" in csv_text
    assert "reading_order_mean_confidence" in csv_text
    assert "reading_order_low_confidence_element_count" in csv_text
    assert "reading_order_evidence_counts" in csv_text
    assert "reading_order_box_flow_disagreement_ratio" in csv_text
    assert "reading_order_box_flow_successor_disagreement_ratio" in csv_text
    assert "reading_order_relation_graph_successor_disagreement_ratio" in csv_text
    assert "reading_order_relation_graph_tied_edge_ratio" in csv_text
    assert "reading_order_relation_graph_mean_minimum_margin" in csv_text
    assert "reading_order_successor_consensus_successor_disagreement_ratio" in csv_text
    assert "reading_order_successor_consensus_selected_edge_support_ratio" in csv_text
    assert "reading_order_successor_consensus_conflicted_edge_ratio" in csv_text
    assert "successor_consensus_arbitration_element_count" in csv_text
    assert "reading_order_candidate_page_recommendation_counts" in csv_text
    assert "reading_order_candidate_stream_count" in csv_text
    assert "reading_order_candidate_stream_recommendation_counts" in csv_text
    assert "semantic_candidate_order_metrics" in csv_text
    assert "semantic_candidate_arbitration_recommendation" in csv_text
    assert "semantic_candidate_successor_delta" in csv_text
    assert "semantic_candidate_relation_successor_delta" in csv_text
    assert "semantic_candidate_stream_successor_delta" in csv_text
    assert "semantic_stream_successor_accuracy" in csv_text
    assert "semantic_stream_precedence_accuracy" in csv_text
    assert "semantic_stream_assignment_id_mismatch_count" in csv_text
    assert "semantic_stream_assignment_type_mismatch_count" in csv_text
    assert "semantic_stream_assignment_type_confusion_counts" in csv_text
    assert "semantic_stream_assignment_id_accuracy" in csv_text
    assert "semantic_stream_assignment_type_accuracy" in csv_text
    assert "semantic_relation_graph_stream_successor_accuracy" in csv_text
    assert "semantic_structure_relation_stream_successor_accuracy" in csv_text
    assert "semantic_relation_successor_accuracy" in csv_text
    assert "semantic_relation_graph_successor_accuracy" in csv_text
    assert "semantic_relation_graph_relation_successor_accuracy" in csv_text
    assert "semantic_structure_relation_successor_accuracy" in csv_text
    assert "semantic_structure_relation_relation_successor_accuracy" in csv_text
    assert "semantic_successor_consensus_successor_accuracy" in csv_text
    assert "semantic_external_structure_successor_accuracy" in csv_text
    assert "reading_order_repeated_anchor_page_count" in csv_text
    assert "reading_order_table_like_page_count" in csv_text
    assert "reading_order_risk_score" in csv_text
    assert (tmp_path / "benchmark-fidelity" / "cases" / pdfs[0].stem / "fidelity-svg-export.pdf").exists()
    assert report["summary"]["html_mode_counts"] == {"fidelity": 1}
    assert report["summary"]["fidelity_background_counts"] == {"svg": 1}
    assert report["summary"]["total_fidelity_replacement_elements"] == 0


def test_benchmark_translation_stress_populates_translated_text_and_replacement_metrics(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:1]
    report = run_benchmark(
        pdfs,
        tmp_path / "benchmark-translation-stress",
        dpi=96,
        html_mode="fidelity",
        fidelity_background="svg",
        translation_stress="pseudo-expand",
    )
    case = report["cases"][0]
    ir = DocumentIR.load(case["ir"])
    csv_text = (tmp_path / "benchmark-translation-stress" / "benchmark_summary.csv").read_text(encoding="utf-8")

    assert report["translation_stress"] == "pseudo-expand"
    assert case["translation_stress"] == "pseudo-expand"
    assert case["translation_stress_element_count"] > 0
    assert case["translation_stress_char_expansion_ratio"] > 1
    assert case["fidelity_replacement_element_count"] == case["translation_stress_element_count"]
    assert case["fidelity_replacement_mean_fit_scale"] is not None
    assert "fidelity_replacement_same_stream_conflict_target_count" in case
    assert "fidelity_replacement_cross_stream_conflict_target_count" in case
    assert "fidelity_replacement_conflict_stream_type_pair_counts" in case
    assert case["fidelity_replacement_stream_diagnostics"]
    assert case["fidelity_replacement_stream_type_counts"]
    assert case["fidelity_replacement_stream_id_counts"]
    assert report["summary"]["translation_stress_counts"] == {"pseudo-expand": 1}
    assert report["summary"]["total_translation_stress_elements"] == case["translation_stress_element_count"]
    assert report["summary"]["mean_translation_stress_char_expansion_ratio"] == case[
        "translation_stress_char_expansion_ratio"
    ]
    assert report["summary"]["fidelity_replacement_stream_type_counts"]
    assert report["summary"]["fidelity_replacement_stream_id_counts"]
    assert "fidelity_replacement_conflict_stream_type_pair_counts" in report["summary"]
    assert any(element.translated_text for page in ir.pages for element in page.elements)
    assert "translation_stress_char_expansion_ratio" in csv_text
    assert "fidelity_replacement_conflict_count" in csv_text
    assert "fidelity_replacement_same_stream_conflict_target_count" in csv_text
    assert "fidelity_replacement_cross_stream_conflict_target_count" in csv_text
    assert "fidelity_replacement_conflict_stream_type_pair_counts" in csv_text
    assert "fidelity_replacement_stream_type_conflict_counts" in csv_text


def test_fidelity_replacement_stats_measure_translation_fit_and_conflicts() -> None:
    replacement = ElementIR(
        id="replace",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=10, y0=10, x1=90, y1=24),
        bbox_px=BBox(x0=10, y0=10, x1=90, y1=24),
        source_text="Buy now",
        translated_text="A much longer translated replacement line",
        style_hint={"font_size_px": 14, "line_height": 1.1, "font_family": "Arial"},
        metadata={"reading_order_stream_id": "grid-island-001", "reading_order_stream_type": "grid-island"},
    )
    neighbor = ElementIR(
        id="neighbor",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=91, y0=10, x1=130, y1=24),
        bbox_px=BBox(x0=91, y0=10, x1=130, y1=24),
        source_text="Next",
        style_hint={"font_size_px": 14, "line_height": 1.1, "font_family": "Arial"},
        metadata={"reading_order_stream_id": "body-main", "reading_order_stream_type": "body"},
    )
    same_stream_neighbor = ElementIR(
        id="same_neighbor",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=75, y0=10, x1=88, y1=24),
        bbox_px=BBox(x0=75, y0=10, x1=88, y1=24),
        source_text="Same",
        style_hint={"font_size_px": 14, "line_height": 1.1, "font_family": "Arial"},
        metadata={"reading_order_stream_id": "grid-island-001", "reading_order_stream_type": "grid-island"},
    )
    body_replacement = ElementIR(
        id="body_replace",
        page_index=0,
        type="text",
        bbox_pdf=BBox(x0=10, y0=50, x1=90, y1=64),
        bbox_px=BBox(x0=10, y0=50, x1=90, y1=64),
        source_text="Body",
        translated_text="Ok",
        style_hint={"font_size_px": 12, "line_height": 1.1, "font_family": "Arial"},
        metadata={"reading_order_stream_id": "body-main", "reading_order_stream_type": "body"},
    )
    document = DocumentIR(
        source_pdf="synthetic.pdf",
        render_dpi=72,
        page_count=1,
        pages=[
            PageIR(
                page_index=0,
                width_pt=160,
                height_pt=80,
                width_px=160,
                height_px=80,
                render_dpi=72,
                scale_x=1,
                scale_y=1,
                background_image="page.png",
                elements=[replacement, same_stream_neighbor, neighbor, body_replacement],
            )
        ],
    )

    stats = _fidelity_replacement_stats(document, "fidelity")
    structured_stats = _fidelity_replacement_stats(document, "structured")

    assert stats["fidelity_replacement_element_count"] == 2
    assert stats["fidelity_replacement_conflict_count"] == 1
    assert stats["fidelity_replacement_conflict_target_count"] == 2
    assert stats["fidelity_replacement_same_stream_conflict_target_count"] == 1
    assert stats["fidelity_replacement_cross_stream_conflict_target_count"] == 1
    assert stats["fidelity_replacement_min_fit_scale"] < 1
    assert stats["fidelity_replacement_mean_fit_scale"] > stats["fidelity_replacement_min_fit_scale"]
    assert stats["fidelity_replacement_policy_counts"] == {"fidelity-replacement-fit-v1": 2}
    assert stats["fidelity_replacement_conflict_target_stream_type_counts"] == {"body": 1, "grid-island": 1}
    assert stats["fidelity_replacement_conflict_target_stream_id_counts"] == {
        "body-main": 1,
        "grid-island-001": 1,
    }
    assert stats["fidelity_replacement_conflict_stream_type_pair_counts"] == {
        "grid-island=>body": 1,
        "grid-island=>grid-island": 1,
    }
    assert stats["fidelity_replacement_conflict_stream_id_pair_counts"] == {
        "grid-island-001=>body-main": 1,
        "grid-island-001=>grid-island-001": 1,
    }
    assert stats["fidelity_replacement_stream_type_counts"] == {"body": 1, "grid-island": 1}
    assert stats["fidelity_replacement_stream_type_conflict_counts"] == {"grid-island": 1}
    assert stats["fidelity_replacement_stream_id_counts"] == {"body-main": 1, "grid-island-001": 1}
    assert stats["fidelity_replacement_stream_id_conflict_counts"] == {"grid-island-001": 1}
    diagnostics = {
        item["stream_id"]: item
        for item in stats["fidelity_replacement_stream_diagnostics"]
    }
    assert diagnostics["body-main"]["conflict_count"] == 0
    assert diagnostics["grid-island-001"]["conflict_count"] == 1
    assert diagnostics["grid-island-001"]["conflict_target_count"] == 2
    assert diagnostics["grid-island-001"]["same_stream_conflict_target_count"] == 1
    assert diagnostics["grid-island-001"]["cross_stream_conflict_target_count"] == 1
    assert diagnostics["grid-island-001"]["conflict_target_stream_type_counts"] == {"body": 1, "grid-island": 1}
    assert diagnostics["grid-island-001"]["conflict_stream_type_pair_counts"] == {
        "grid-island=>body": 1,
        "grid-island=>grid-island": 1,
    }
    assert structured_stats["fidelity_replacement_element_count"] == 0
    assert structured_stats["fidelity_replacement_stream_diagnostics"] == []


def test_benchmark_can_auto_select_fidelity_background(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:1]
    report = run_benchmark(
        pdfs,
        tmp_path / "benchmark-auto-fidelity-background",
        dpi=96,
        html_mode="fidelity",
        fidelity_background="auto",
    )
    case = report["cases"][0]

    assert report["html_mode"] == "fidelity"
    assert report["fidelity_background"] == "auto"
    assert case["fidelity_background_request"] == "auto"
    assert case["fidelity_background_selected"] == case["fidelity_background"]
    assert case["fidelity_background_auto_total_seconds"] >= case["fidelity_background_selected_total_seconds"]
    assert case["total_seconds"] == case["fidelity_background_auto_total_seconds"]
    assert {candidate["fidelity_background"] for candidate in case["fidelity_background_candidates"]} == {
        "svg",
        "raster",
    }
    assert all("visual_similarity" in candidate for candidate in case["fidelity_background_candidates"])
    assert report["summary"]["fidelity_background_counts"][case["fidelity_background"]] == 1


def test_benchmark_can_auto_select_html_mode(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:1]
    report = run_benchmark(pdfs, tmp_path / "benchmark-auto-html-mode", dpi=96, html_mode="auto")
    case = report["cases"][0]

    assert report["html_mode"] == "auto"
    assert case["html_mode_request"] == "auto"
    assert case["html_mode_selected"] == case["html_mode"]
    assert case["html_mode_auto_total_seconds"] >= case["html_mode_selected_total_seconds"]
    assert case["total_seconds"] == case["html_mode_auto_total_seconds"]
    assert {candidate["html_mode"] for candidate in case["html_mode_candidates"]} == {"structured", "fidelity"}
    assert all("visual_similarity" in candidate for candidate in case["html_mode_candidates"])
    assert report["summary"]["html_mode_counts"][case["html_mode"]] == 1


def test_benchmark_can_score_structure_evidence_fusion(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:1]
    structure_json = tmp_path / f"{pdfs[0].stem}.structure.json"
    structure_json.write_text(
        json.dumps(
            {
                "source": "unit-pp-structure",
                "pages": [
                    {
                        "page_index": 0,
                        "parsing_res_list": [
                            {
                                "block_label": "text",
                                "block_bbox": [0, 0, 10000, 10000],
                                "block_order": 1,
                                "block_content": "",
                                "confidence": 0.9,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = run_benchmark(pdfs, tmp_path / "benchmark-structure", dpi=96, structure_jsons=[structure_json])
    case = report["cases"][0]
    csv_text = (tmp_path / "benchmark-structure" / "benchmark_summary.csv").read_text(encoding="utf-8")

    assert case["structure_evidence_source"] == f"structure-json:{structure_json.name}"
    assert case["structure_evidence_region_count"] == 1
    assert case["structure_evidence_relation_edge_count"] == 0
    assert case["structure_evidence_resolved_relation_edge_count"] == 0
    assert case["structure_evidence_resolved_relation_alias_edge_count"] == 0
    assert case["structure_evidence_stream_count"] == 0
    assert case["structure_evidence_resolved_stream_member_count"] == 0
    assert case["structure_evidence_resolved_stream_alias_member_count"] == 0
    assert case["structure_evidence_stream_conflict_count"] == 0
    assert case["structure_evidence_relation_stream_count"] == 0
    assert case["structure_evidence_resolved_relation_stream_member_count"] == 0
    assert case["structure_evidence_relation_stream_conflict_count"] == 0
    assert case["structure_evidence_matched_element_count"] > 0
    assert case["structure_evidence_relation_reordered_page_count"] == 0
    assert case["structure_evidence_order_reordered_page_count"] >= 0
    assert case["structure_evidence_order_source_counts"] == {"explicit": 1}
    assert "text_run_count" in csv_text
    assert "raster_fallback_count" in csv_text
    assert report["summary"]["total_structure_evidence_regions"] == 1
    assert report["summary"]["total_structure_evidence_resolved_relation_alias_edges"] == 0
    assert report["summary"]["total_structure_evidence_streams"] == 0
    assert report["summary"]["total_structure_evidence_resolved_stream_members"] == 0
    assert report["summary"]["total_structure_evidence_resolved_stream_alias_members"] == 0
    assert report["summary"]["total_structure_evidence_stream_conflicts"] == 0
    assert report["summary"]["total_structure_evidence_relation_streams"] == 0
    assert report["summary"]["total_structure_evidence_resolved_relation_stream_members"] == 0
    assert report["summary"]["total_structure_evidence_relation_stream_conflicts"] == 0
    assert report["summary"]["total_structure_evidence_matched_elements"] == case[
        "structure_evidence_matched_element_count"
    ]
    assert report["summary"]["total_structure_evidence_relation_reordered_pages"] == 0
    assert report["summary"]["total_structure_evidence_order_reordered_pages"] >= 0
    assert report["summary"]["structure_evidence_order_source_counts"] == {"explicit": 1}
    assert "structure_evidence_matched_element_count" in csv_text
    assert "structure_evidence_relation_edge_count" in csv_text
    assert "structure_evidence_resolved_relation_alias_edge_count" in csv_text
    assert "structure_evidence_stream_count" in csv_text
    assert "structure_evidence_resolved_stream_alias_member_count" in csv_text
    assert "structure_evidence_relation_stream_count" in csv_text
    assert "structure_evidence_relation_reordered_page_count" in csv_text
    assert "structure_evidence_order_source_counts" in csv_text
    assert "semantic_external_structure_successor_accuracy" in csv_text


def test_structure_ab_benchmark_compares_native_and_structure_runs(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:1]
    expected_text = [
        "Quarterly Engineering Note",
        "This page is mostly flowing native PDF text.",
        "The benchmark checks whether text nodes, style marks, and coordinates survive.",
        "Key points",
        "1. Extract text spans without using a page screenshot.",
        "2. Emit editable HTML nodes with stable IDs.",
        "3. Preserve source text while allowing edited text.",
    ]
    pdfs[0].with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 3,
                "pages": [
                    {
                        "page_index": 0,
                        "reading_streams": [
                            {
                                "id": "grid-island-external-001",
                                "type": "product_grid",
                                "members": expected_text,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    structure_json = tmp_path / f"{pdfs[0].stem}.structure.json"
    structure_json.write_text(
        json.dumps(
            {
                "source": "unit-pp-structure",
                "pages": [
                    {
                        "page_index": 0,
                        "parsing_res_list": [
                            {
                                "block_label": "product_grid",
                                "block_bbox": [0, 0, 10000, 10000],
                                "block_order": 1,
                                "block_content": "",
                                "confidence": 0.9,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = run_structure_ab_benchmark(
        pdfs,
        tmp_path / "structure-ab",
        [structure_json],
        dpi=96,
    )
    comparison = report["cases"][0]
    csv_text = (tmp_path / "structure-ab" / "structure_ab_summary.csv").read_text(encoding="utf-8")

    assert report["case_count"] == 1
    assert (tmp_path / "structure-ab" / "native-only" / "benchmark_report.json").exists()
    assert (tmp_path / "structure-ab" / "native-plus-structure" / "benchmark_report.json").exists()
    assert report["native_report"].endswith("native-only/benchmark_report.json")
    assert report["structure_report"].endswith("native-plus-structure/benchmark_report.json")
    assert comparison["structure_evidence_region_count"] == 1
    assert comparison["structure_evidence_relation_edge_count"] == 0
    assert comparison["structure_evidence_resolved_relation_edge_count"] == 0
    assert comparison["structure_evidence_resolved_relation_alias_edge_count"] == 0
    assert comparison["structure_evidence_stream_count"] == 0
    assert comparison["structure_evidence_resolved_stream_member_count"] == 0
    assert comparison["structure_evidence_resolved_stream_alias_member_count"] == 0
    assert comparison["structure_evidence_stream_conflict_count"] == 0
    assert comparison["structure_evidence_relation_stream_count"] == 0
    assert comparison["structure_evidence_resolved_relation_stream_member_count"] == 0
    assert comparison["structure_evidence_relation_stream_conflict_count"] == 0
    assert comparison["structure_evidence_matched_element_count"] > 0
    assert comparison["structure_evidence_relation_reordered_page_count"] == 0
    assert comparison["structure_evidence_order_reordered_page_count"] >= 0
    assert comparison["structure_evidence_order_source_counts"] == {"explicit": 1}
    assert comparison["structure_grid_island_element_count"] >= comparison["native_grid_island_element_count"]
    assert "visual_similarity_delta" in comparison
    assert "stream_needs_structure_evidence_delta" in comparison
    assert "semantic_relation_missing_text_delta" in comparison
    assert "semantic_stream_missing_text_delta" in comparison
    assert "reading_order_proposal_semantic_successor_coverage_delta" in comparison
    assert "reading_order_proposal_semantic_reviewable_successor_coverage_delta" in comparison
    assert "reading_order_proposal_semantic_strict_anchor_path_coverage_delta" in comparison
    assert "reading_order_proposal_semantic_reviewable_anchor_path_coverage_delta" in comparison
    assert "semantic_stream_assignment_missing_delta" in comparison
    assert comparison["semantic_stream_assignment_id_accuracy_delta"] > 0
    assert comparison["semantic_stream_assignment_type_accuracy_delta"] > 0
    assert report["summary"]["total_semantic_relation_missing_text_delta"] == 0
    assert report["summary"]["total_semantic_stream_missing_text_delta"] == 0
    assert report["summary"]["total_semantic_stream_assignment_missing_delta"] == 0
    assert report["summary"]["mean_semantic_stream_assignment_id_accuracy_delta"] > 0
    assert report["summary"]["mean_semantic_stream_assignment_type_accuracy_delta"] > 0
    assert report["summary"]["cases_with_stream_assignment_id_improvement"] == 1
    assert report["summary"]["cases_with_stream_assignment_type_improvement"] == 1
    assert report["summary"]["cases_with_relation_missing_text_improvement"] == 0
    assert report["summary"]["cases_with_stream_missing_text_improvement"] == 0
    assert report["summary"]["cases_with_stream_assignment_missing_improvement"] == 0
    assert "semantic_external_structure_successor_accuracy" not in csv_text
    assert "translation_stress_element_delta" in csv_text
    assert "fidelity_replacement_conflict_delta" in csv_text
    assert "fidelity_replacement_same_stream_conflict_target_delta" in csv_text
    assert "fidelity_replacement_cross_stream_conflict_target_delta" in csv_text
    assert "semantic_relation_missing_text_delta" in csv_text
    assert "reading_order_proposal_semantic_successor_coverage_delta" in csv_text
    assert "reading_order_proposal_semantic_strict_anchor_path_coverage_delta" in csv_text
    assert "reading_order_proposal_semantic_reviewable_anchor_path_coverage_delta" in csv_text
    assert "semantic_stream_missing_text_delta" in csv_text
    assert "semantic_stream_assignment_missing_delta" in csv_text
    assert "semantic_stream_assignment_id_accuracy_delta" in csv_text
    assert "semantic_stream_assignment_type_accuracy_delta" in csv_text
    assert "structure_evidence_matched_element_count" in csv_text
    assert "structure_evidence_relation_edge_count" in csv_text
    assert "structure_evidence_resolved_relation_alias_edge_count" in csv_text
    assert "structure_evidence_stream_count" in csv_text
    assert "structure_evidence_resolved_stream_alias_member_count" in csv_text
    assert "structure_evidence_relation_stream_count" in csv_text
    assert "structure_evidence_relation_reordered_page_count" in csv_text
    assert "structure_evidence_order_source_counts" in csv_text
    assert "grid_island_element_delta" in csv_text
    assert report["summary"]["total_structure_evidence_regions"] == 1
    assert report["summary"]["total_structure_evidence_relation_edges"] == 0
    assert report["summary"]["total_structure_evidence_resolved_relation_edges"] == 0
    assert report["summary"]["total_structure_evidence_resolved_relation_alias_edges"] == 0
    assert report["summary"]["total_structure_evidence_streams"] == 0
    assert report["summary"]["total_structure_evidence_resolved_stream_members"] == 0
    assert report["summary"]["total_structure_evidence_resolved_stream_alias_members"] == 0
    assert report["summary"]["total_structure_evidence_stream_conflicts"] == 0
    assert report["summary"]["total_structure_evidence_relation_streams"] == 0
    assert report["summary"]["total_structure_evidence_resolved_relation_stream_members"] == 0
    assert report["summary"]["total_structure_evidence_relation_stream_conflicts"] == 0
    assert report["summary"]["total_structure_evidence_relation_reordered_pages"] == 0
    assert report["summary"]["total_structure_evidence_order_reordered_pages"] >= 0
    assert "total_fidelity_replacement_same_stream_conflict_target_delta" in report["summary"]
    assert "total_fidelity_replacement_cross_stream_conflict_target_delta" in report["summary"]
    assert report["summary"]["total_structure_evidence_matched_elements"] == comparison[
        "structure_evidence_matched_element_count"
    ]
    assert report["summary"]["structure_evidence_order_source_counts"] == {"explicit": 1}


def test_structure_ab_benchmark_reports_block_group_relation_metrics(tmp_path: Path) -> None:
    pdf_path = next(
        path for path in create_benchmark_fixtures(tmp_path / "fixtures") if path.name == "two_column_notes.pdf"
    )
    structure_json = tmp_path / "two_column_notes.structure.json"
    structure_json.write_text(
        json.dumps(
            {
                "source": "unit-pp-block-relations",
                "pages": [
                    {
                        "page_index": 0,
                        "parsing_res_list": [
                            {
                                "block_id": "left-column",
                                "block_label": "text",
                                "bbox_pdf": [60, 120, 280, 250],
                                "block_content": "Left column one. Native extraction keeps text spans. The annotation layer records role. Coordinates remain in PDF points.",
                            },
                            {
                                "block_id": "right-column",
                                "block_label": "text",
                                "bbox_pdf": [300, 120, 510, 250],
                                "block_content": "Right column paragraph one. This stresses reading order. The HTML should avoid page images. Benchmarks track similarity.",
                            },
                        ],
                        "successor_edges": [["left-column", "right-column"]],
                        "reading_streams": [
                            {
                                "id": "article-columns",
                                "type": "body",
                                "members": ["left-column", "right-column"],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = run_structure_ab_benchmark(
        [pdf_path],
        tmp_path / "structure-ab-block-groups",
        [structure_json],
        dpi=96,
    )
    comparison = report["cases"][0]
    csv_text = (tmp_path / "structure-ab-block-groups" / "structure_ab_summary.csv").read_text(encoding="utf-8")

    assert comparison["structure_evidence_relation_edge_count"] == 1
    assert comparison["structure_evidence_resolved_relation_edge_count"] == 1
    assert comparison["structure_evidence_resolved_relation_group_edge_count"] == 1
    assert comparison["structure_evidence_relation_group_internal_edge_count"] == 6
    assert comparison["structure_evidence_unresolved_relation_edge_count"] == 0
    assert comparison["structure_evidence_resolved_stream_group_member_ref_count"] == 2
    assert comparison["structure_evidence_resolved_stream_member_count"] == 8
    assert comparison["structure_evidence_unresolved_stream_member_ref_count"] == 0
    assert report["summary"]["total_structure_evidence_resolved_relation_group_edges"] == 1
    assert report["summary"]["total_structure_evidence_relation_group_internal_edges"] == 6
    assert report["summary"]["total_structure_evidence_resolved_stream_group_member_refs"] == 2
    assert report["summary"]["total_structure_evidence_unresolved_relation_edges"] == 0
    assert "structure_evidence_resolved_relation_group_edge_count" in csv_text
    assert "structure_evidence_unresolved_stream_member_ref_count" in csv_text


def _document_with_candidate_text_boxes(items: list[tuple[str, str, BBox, int, int | None]]) -> DocumentIR:
    elements = [
        ElementIR(
            id=element_id,
            page_index=0,
            type="text",
            bbox_pdf=bbox,
            bbox_px=bbox,
            source_text=text,
            reading_order=reading_order,
            metadata={
                "source": "unit-test",
                "reading_order_strategy": "visual-yx",
                "external_structure_order": external_order,
            },
        )
        for element_id, text, bbox, reading_order, external_order in items
    ]
    page = PageIR(
        page_index=0,
        width_pt=200,
        height_pt=200,
        width_px=200,
        height_px=200,
        render_dpi=72,
        scale_x=1.0,
        scale_y=1.0,
        background_image="",
        elements=elements,
    )
    return DocumentIR(source_pdf="candidate.pdf", render_dpi=72, page_count=1, pages=[page])


def test_benchmark_can_auto_select_font_profile(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:1]
    report = run_benchmark(pdfs, tmp_path / "benchmark-auto-font", dpi=96, font_profile="auto")
    case = report["cases"][0]

    assert report["font_profile"] == "auto"
    assert case["font_profile_request"] == "auto"
    assert case["font_profile_selected"] == case["font_profile"]
    assert case["font_profile_auto_total_seconds"] >= case["font_profile_selected_total_seconds"]
    assert case["total_seconds"] == case["font_profile_auto_total_seconds"]
    assert {candidate["font_profile"] for candidate in case["font_profile_candidates"]} == {
        "browser-default",
        "local-urw",
    }
    assert all("total_seconds" in candidate for candidate in case["font_profile_candidates"])
    assert report["summary"]["font_profile_counts"][case["font_profile"]] == 1


def test_benchmark_can_auto_select_font_size_scale(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:1]
    report = run_benchmark(pdfs, tmp_path / "benchmark-auto-font-scale", dpi=96, font_size_scale="auto")
    case = report["cases"][0]

    assert report["font_size_scale"] == "auto"
    assert case["font_size_scale_request"] == "auto"
    assert case["font_size_scale_selected"] == case["font_size_scale"]
    assert case["font_size_scale_auto_total_seconds"] >= case["font_size_scale_selected_total_seconds"]
    assert case["total_seconds"] == case["font_size_scale_auto_total_seconds"]
    assert {candidate["font_size_scale"] for candidate in case["font_size_scale_candidates"]} == {0.99, 1.0}
    assert all("total_seconds" in candidate for candidate in case["font_size_scale_candidates"])
    assert report["summary"]["font_size_scale_counts"][str(case["font_size_scale"])] == 1


def test_benchmark_reports_image_only_ocr_fallback_metrics(tmp_path: Path) -> None:
    _require_tesseract()
    pdf_path = _create_image_only_text_pdf(tmp_path)
    report = run_benchmark(
        [pdf_path],
        tmp_path / "benchmark-image-only-ocr",
        dpi=96,
        ocr_language="eng",
        ocr_dpi=200,
    )
    case = report["cases"][0]
    csv_text = (tmp_path / "benchmark-image-only-ocr" / "benchmark_summary.csv").read_text(encoding="utf-8")
    ir = json.loads(Path(case["ir"]).read_text(encoding="utf-8"))
    ocr_text = " ".join(
        element["source_text"].upper()
        for page in ir["pages"]
        for element in page["elements"]
        if element["metadata"].get("source") == "native-ocr"
    )

    assert report["ocr_fallback"] == "image-only"
    assert report["ocr_language"] == "eng"
    assert report["ocr_dpi"] == 200
    assert case["ocr_fallback_applied_page_count"] == 1
    assert case["ocr_text_count"] > 0
    assert case["image_only_candidate_page_count"] == 1
    assert case["textless_page_count"] == 0
    assert case["editable_element_count"] >= case["ocr_text_count"]
    assert report["summary"]["total_ocr_fallback_applied_pages"] == 1
    assert report["summary"]["total_ocr_text_elements"] == case["ocr_text_count"]
    assert "BENCHMARK" in ocr_text
    assert "OCR" in ocr_text
    assert "ocr_fallback_applied_page_count" in csv_text
    assert "ocr_text_count" in csv_text


def test_benchmark_can_auto_select_text_fit(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:1]
    report = run_benchmark(pdfs, tmp_path / "benchmark-auto-text-fit", dpi=96, text_fit="auto")
    case = report["cases"][0]

    assert report["text_fit"] == "auto"
    assert case["text_fit_request"] == "auto"
    assert case["text_fit_selected"] == case["text_fit"]
    assert case["text_fit_auto_total_seconds"] >= case["text_fit_selected_total_seconds"]
    assert case["total_seconds"] == case["text_fit_auto_total_seconds"]
    assert {candidate["text_fit"] for candidate in case["text_fit_candidates"]} == {"none", "svg"}
    assert all("total_seconds" in candidate for candidate in case["text_fit_candidates"])
    assert report["summary"]["text_fit_counts"][case["text_fit"]] == 1
