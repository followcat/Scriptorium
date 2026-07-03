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
    assert "mean_visual_similarity" in report["summary"]
    assert "mean_diff_ratio" in report["summary"]
    assert "p95_diff_ratio" in report["summary"]
    assert report["summary"]["total_pages"] >= 2
    assert all(0 <= case["visual_similarity"] <= 1 for case in report["cases"])
    assert all("dimension_match" in case for case in report["cases"])
    assert all("worst_page" in case for case in report["cases"])
    assert all("image_count" in case for case in report["cases"])
    assert all("multi_column_element_count" in case for case in report["cases"])
    assert all("recursive_xy_cut_element_count" in case for case in report["cases"])
    assert all("reading_order_strategy_counts" in case for case in report["cases"])
    assert "total_multi_column_elements" in report["summary"]
    assert "total_image_elements" in report["summary"]
    assert "total_recursive_xy_cut_elements" in report["summary"]
    assert "reading_order_strategy_counts" in report["summary"]
    assert report["summary"]["semantic_case_count"] == 2
    assert report["summary"]["mean_semantic_order_pair_accuracy"] == 1
    assert report["summary"]["mean_semantic_sequence_similarity"] == 1
    assert "total_semantic_ignored_text_count" in report["summary"]
    assert all(case["semantic_ground_truth_available"] for case in report["cases"])
    assert all("semantic_ignored_text_count" in case for case in report["cases"])
    assert all(case["element_count"] > 0 for case in report["cases"])
    assert (tmp_path / "benchmark" / "benchmark_report.json").exists()
    assert (tmp_path / "benchmark" / "benchmark_summary.csv").exists()


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
    assert report["summary"]["total_structure_evidence_regions"] == 1
    assert report["summary"]["total_structure_evidence_matched_elements"] == case[
        "structure_evidence_matched_element_count"
    ]
    assert "structure_evidence_matched_element_count" in csv_text
