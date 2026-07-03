import json
from pathlib import Path

from scriptorium.benchmark import run_benchmark
from scriptorium.benchmark_fixtures import create_benchmark_fixtures
from scriptorium.semantic_quality import semantic_ground_truth_path


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
    assert report["raster_policy"] == "dense"
    assert report["html_mode"] == "structured"
    assert report["font_size_scale"] == 1.0
    assert "mean_visual_similarity" in report["summary"]
    assert "mean_diff_ratio" in report["summary"]
    assert "p95_diff_ratio" in report["summary"]
    assert report["summary"]["total_pages"] >= 2
    assert all(0 <= case["visual_similarity"] <= 1 for case in report["cases"])
    assert all("dimension_match" in case for case in report["cases"])
    assert all("worst_page" in case for case in report["cases"])
    assert all("image_count" in case for case in report["cases"])
    assert all("text_run_count" in case for case in report["cases"])
    assert all("mixed_inline_style_element_count" in case for case in report["cases"])
    assert all("multi_column_element_count" in case for case in report["cases"])
    assert all("recursive_xy_cut_element_count" in case for case in report["cases"])
    assert all("reading_order_strategy_counts" in case for case in report["cases"])
    assert all("layout_region_counts" in case for case in report["cases"])
    assert all("table_region_count" in case for case in report["cases"])
    assert all("raster_fallback_count" in case for case in report["cases"])
    assert all("vector_background_page_count" in case for case in report["cases"])
    assert all("reading_order_risk_score" in case for case in report["cases"])
    assert all("reading_order_risk_level" in case for case in report["cases"])
    assert all("reading_order_column_geometry_page_count" in case for case in report["cases"])
    assert all("reading_order_unlabeled_text_risk_count" in case for case in report["cases"])
    assert all(case["font_profile"] == "browser-default" for case in report["cases"])
    assert all(case["raster_policy"] == "dense" for case in report["cases"])
    assert all(case["html_mode"] == "structured" for case in report["cases"])
    assert all(case["font_size_scale"] == 1.0 for case in report["cases"])
    assert all(case["vector_background_page_count"] == 0 for case in report["cases"])
    assert "total_text_runs" in report["summary"]
    assert "total_mixed_inline_style_elements" in report["summary"]
    assert "total_multi_column_elements" in report["summary"]
    assert "total_image_elements" in report["summary"]
    assert "total_recursive_xy_cut_elements" in report["summary"]
    assert "reading_order_strategy_counts" in report["summary"]
    assert "font_profile_counts" in report["summary"]
    assert report["summary"]["html_mode_counts"] == {"structured": 2}
    assert report["summary"]["font_size_scale_counts"] == {"1.0": 2}
    assert "layout_region_counts" in report["summary"]
    assert "total_table_regions" in report["summary"]
    assert "total_raster_fallbacks" in report["summary"]
    assert report["summary"]["total_vector_background_pages"] == 0
    assert "mean_reading_order_risk_score" in report["summary"]
    assert "reading_order_risk_level_counts" in report["summary"]
    assert "total_reading_order_column_geometry_pages" in report["summary"]
    assert "total_reading_order_unlabeled_text_risk_count" in report["summary"]
    assert report["summary"]["semantic_case_count"] == 2
    assert report["summary"]["mean_semantic_order_pair_accuracy"] == 1
    assert report["summary"]["mean_semantic_sequence_similarity"] == 1
    assert "total_semantic_ignored_text_count" in report["summary"]
    assert all(case["semantic_ground_truth_available"] for case in report["cases"])
    assert all("semantic_ignored_text_count" in case for case in report["cases"])
    assert all(case["element_count"] > 0 for case in report["cases"])
    assert (tmp_path / "benchmark" / "benchmark_report.json").exists()
    assert (tmp_path / "benchmark" / "benchmark_summary.csv").exists()


def test_benchmark_can_score_fidelity_overlay_mode(tmp_path: Path) -> None:
    pdfs = create_benchmark_fixtures(tmp_path / "fixtures")[:1]
    report = run_benchmark(pdfs, tmp_path / "benchmark-fidelity", dpi=96, html_mode="fidelity")
    case = report["cases"][0]
    csv_text = (tmp_path / "benchmark-fidelity" / "benchmark_summary.csv").read_text(encoding="utf-8")

    assert report["html_mode"] == "fidelity"
    assert case["html_mode"] == "fidelity"
    assert case["font_size_scale"] == 1.0
    assert case["vector_background_page_count"] == case["page_count"]
    assert "html_mode" in csv_text
    assert "font_size_scale" in csv_text
    assert "vector_background_page_count" in csv_text
    assert "reading_order_risk_score" in csv_text
    assert (tmp_path / "benchmark-fidelity" / "cases" / pdfs[0].stem / "fidelity-export.pdf").exists()
    assert report["summary"]["html_mode_counts"] == {"fidelity": 1}


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
    assert case["structure_evidence_matched_element_count"] > 0
    assert "text_run_count" in csv_text
    assert "raster_fallback_count" in csv_text
    assert report["summary"]["total_structure_evidence_regions"] == 1
    assert report["summary"]["total_structure_evidence_matched_elements"] == case[
        "structure_evidence_matched_element_count"
    ]
    assert "structure_evidence_matched_element_count" in csv_text


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
