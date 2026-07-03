import json
import shutil
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from scriptorium.benchmark import _page_reading_order_geometry_profile, run_benchmark
from scriptorium.benchmark_fixtures import create_benchmark_fixtures
from scriptorium.models import BBox
from scriptorium.semantic_quality import semantic_ground_truth_path


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
    assert report["text_fit"] == "none"
    assert report["fidelity_background"] == "auto"
    assert "mean_visual_similarity" in report["summary"]
    assert "mean_diff_ratio" in report["summary"]
    assert "p95_diff_ratio" in report["summary"]
    assert report["summary"]["total_pages"] >= 2
    assert report["summary"]["ocr_fallback_counts"] == {"image-only": 2}
    assert "total_ocr_fallback_applied_pages" in report["summary"]
    assert "total_ocr_text_elements" in report["summary"]
    assert "total_image_only_candidate_pages" in report["summary"]
    assert "total_textless_pages" in report["summary"]
    assert all(0 <= case["visual_similarity"] <= 1 for case in report["cases"])
    assert all("dimension_match" in case for case in report["cases"])
    assert all("worst_page" in case for case in report["cases"])
    assert all("image_count" in case for case in report["cases"])
    assert all("text_run_count" in case for case in report["cases"])
    assert all("mixed_inline_style_element_count" in case for case in report["cases"])
    assert all("multi_column_element_count" in case for case in report["cases"])
    assert all("recursive_xy_cut_element_count" in case for case in report["cases"])
    assert all("mixed_table_column_flow_element_count" in case for case in report["cases"])
    assert all("reading_order_artifact_element_count" in case for case in report["cases"])
    assert all("reading_order_artifact_counts" in case for case in report["cases"])
    assert all("reading_order_footnote_element_count" in case for case in report["cases"])
    assert all("reading_order_sidebar_element_count" in case for case in report["cases"])
    assert all("reading_order_sidebar_counts" in case for case in report["cases"])
    assert all("reading_order_strategy_counts" in case for case in report["cases"])
    assert all("reading_order_confidence_element_count" in case for case in report["cases"])
    assert all("reading_order_mean_confidence" in case for case in report["cases"])
    assert all("reading_order_low_confidence_element_count" in case for case in report["cases"])
    assert all("reading_order_evidence_counts" in case for case in report["cases"])
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
    assert "total_reading_order_artifact_elements" in report["summary"]
    assert "reading_order_artifact_counts" in report["summary"]
    assert "total_reading_order_footnote_elements" in report["summary"]
    assert "total_reading_order_sidebar_elements" in report["summary"]
    assert "reading_order_sidebar_counts" in report["summary"]
    assert "reading_order_strategy_counts" in report["summary"]
    assert "mean_reading_order_confidence" in report["summary"]
    assert "total_reading_order_low_confidence_elements" in report["summary"]
    assert "reading_order_evidence_counts" in report["summary"]
    assert "font_profile_counts" in report["summary"]
    assert report["summary"]["html_mode_counts"] == {"structured": 2}
    assert report["summary"]["font_size_scale_counts"] == {"1.0": 2}
    assert report["summary"]["text_fit_counts"] == {"none": 2}
    assert report["summary"]["fidelity_background_counts"] == {"none": 2}
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
    assert report["summary"]["mean_semantic_sequence_similarity"] == 1
    assert "total_semantic_ignored_text_count" in report["summary"]
    assert all(case["semantic_ground_truth_available"] for case in report["cases"])
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
    assert case["font_size_scale"] == 1.0
    assert case["vector_background_page_count"] == case["page_count"]
    assert "html_mode" in csv_text
    assert "font_size_scale" in csv_text
    assert "text_fit" in csv_text
    assert "fidelity_background" in csv_text
    assert "vector_background_page_count" in csv_text
    assert "ocr_fallback_applied_page_count" in csv_text
    assert "ocr_text_count" in csv_text
    assert "mixed_table_column_flow_element_count" in csv_text
    assert "reading_order_artifact_element_count" in csv_text
    assert "reading_order_footnote_element_count" in csv_text
    assert "reading_order_sidebar_element_count" in csv_text
    assert "reading_order_sidebar_counts" in csv_text
    assert "reading_order_mean_confidence" in csv_text
    assert "reading_order_low_confidence_element_count" in csv_text
    assert "reading_order_evidence_counts" in csv_text
    assert "reading_order_repeated_anchor_page_count" in csv_text
    assert "reading_order_table_like_page_count" in csv_text
    assert "reading_order_risk_score" in csv_text
    assert (tmp_path / "benchmark-fidelity" / "cases" / pdfs[0].stem / "fidelity-svg-export.pdf").exists()
    assert report["summary"]["html_mode_counts"] == {"fidelity": 1}
    assert report["summary"]["fidelity_background_counts"] == {"svg": 1}


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
