from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import typer

from .annotations import annotate_document
from .benchmark import (
    BenchmarkFidelityBackground,
    BenchmarkFontProfile,
    BenchmarkHtmlMode,
    BenchmarkTextFit,
    BenchmarkTranslationStress,
    run_benchmark,
    run_structure_ab_benchmark,
)
from .fixture import create_fixture
from .html_edits import apply_html_edit_patch
from .html_export import HtmlTextFit, export_html
from .models import DisplayMode, DocumentIR, RevisionIR
from .native_pdf import FontProfile, OcrFallback, RasterPolicy, extract_native_pdf_to_ir
from .ocr import PpStructureAdapter, PaddleOcrAdapter, load_ocr_json, normalize_ocr_to_ir, write_ocr_json
from .pdf_export import print_html_to_pdf
from .pdf_render import SourceKind, page_indices_from_ranges, render_pdf, render_source
from .playwright_capture import CaptureMode, capture_pdf
from .quality import compare_html_to_rendered_pdf, compare_pdf_renderings
from .reading_order_sidecar import reading_order_sidecar_summary, write_reading_order_sidecar
from .roor_benchmark import RoorSplit, fetch_roor_benchmark_samples
from .structure_evidence import apply_structure_evidence, load_structure_json
from .web_fixture import create_web_fixture
from .xml_edit import apply_xml_edits, export_document_xml, set_xml_element_text

app = typer.Typer(help="Scriptorium core conversion tools.")


@app.command()
def make_fixture(out_dir: Path = typer.Option(Path("data/fixture"), help="Directory for sample PDF and OCR JSON.")) -> None:
    pdf_path, ocr_path = create_fixture(out_dir)
    typer.echo(f"PDF: {pdf_path}")
    typer.echo(f"OCR JSON: {ocr_path}")


@app.command("make-web-fixture")
def make_web_fixture(out_dir: Path = typer.Option(Path("data/web-fixture"), help="Directory for structured HTML fixture.")) -> None:
    html_path = create_web_fixture(out_dir)
    typer.echo(f"HTML: {html_path}")


@app.command("fetch-roor")
def fetch_roor_command(
    out_dir: Path = typer.Option(
        Path("data/external/roor-validation"),
        help="Directory for the official ROOR image, layout-anchor, and relation-label files.",
    ),
    split: RoorSplit = typer.Option("val", help="Official ROOR split to fetch."),
    sample_count: int = typer.Option(
        5,
        min=1,
        help="Use the published split's first N samples; this is independent of benchmark outcomes.",
    ),
    refresh: bool = typer.Option(False, help="Download images and rewrite derived files even when they exist."),
) -> None:
    result = fetch_roor_benchmark_samples(
        out_dir,
        split=split,
        sample_count=sample_count,
        refresh=refresh,
    )
    typer.echo(f"ROOR split: {result.split}")
    typer.echo(f"Samples: {len(result.samples)}")
    typer.echo(f"Manifest: {result.manifest_path}")
    typer.echo(f"Images: {result.out_dir / 'images'}")
    typer.echo(f"Structure anchors: {result.out_dir / 'structure'}")


@app.command("benchmark")
def benchmark_command(
    sources: Optional[list[Path]] = typer.Argument(
        None,
        help="Optional source PDF/image files. If omitted, built-in PDF fixtures are generated.",
    ),
    out_dir: Path = typer.Option(Path("outputs/benchmark"), help="Benchmark output directory."),
    dpi: int = typer.Option(192, min=72, max=600, help="Render DPI for visual comparison."),
    input_kind: SourceKind = typer.Option(
        "auto",
        help="Source type for explicit inputs: auto, pdf, or image.",
    ),
    image_dpi: int = typer.Option(
        96,
        min=1,
        max=1200,
        help="Pixel density used to map image source pixels into PDF points.",
    ),
    max_pages: Optional[int] = typer.Option(
        None,
        min=1,
        help="Limit each benchmark source to the first N pages for large external documents.",
    ),
    page_ranges: Optional[str] = typer.Option(
        None,
        help="Score explicit 1-based source page ranges, for example 1-12,136-160. Cannot be combined with --max-pages.",
    ),
    font_profile: BenchmarkFontProfile = typer.Option(
        "browser-default",
        help=(
            "CSS font fallback profile for native PDF text. "
            "Use auto to benchmark browser-default and local-urw, then keep the better case."
        ),
    ),
    raster_policy: RasterPolicy = typer.Option(
        "dense",
        help="Native raster fallback policy: none, dense, or tables for experimental complex table regions.",
    ),
    ocr_fallback: OcrFallback = typer.Option(
        "image-only",
        help="OCR fallback policy: off or image-only for textless raster pages and image sources.",
    ),
    ocr_language: str = typer.Option(
        "eng+chi_sim",
        help="Tesseract language list for image-only OCR fallback, for example eng or eng+chi_sim.",
    ),
    ocr_dpi: int = typer.Option(
        144,
        min=72,
        max=600,
        help="OCR render DPI used only by the image-only fallback.",
    ),
    html_mode: BenchmarkHtmlMode = typer.Option(
        "structured",
        help=(
            "HTML mode to score. structured redraws editable document elements; "
            "fidelity keeps a source page background as the visible layer and overlays editable coordinates; "
            "auto benchmarks both and keeps the higher visual-similarity result."
        ),
    ),
    font_size_scale: str = typer.Option(
        "1.0",
        help="Global CSS font-size multiplier for visual calibration experiments, or auto.",
    ),
    text_fit: BenchmarkTextFit = typer.Option(
        "none",
        help="Structured text fitting strategy: none, svg, or auto to benchmark both and keep the better case.",
    ),
    fidelity_background: BenchmarkFidelityBackground = typer.Option(
        "auto",
        help=(
            "Fidelity background source: svg keeps vector PDF background when available; "
            "raster uses the rendered page image; auto benchmarks both for fidelity mode."
        ),
    ),
    structure_json: Optional[list[Path]] = typer.Option(
        None,
        "--structure-json",
        exists=True,
        readable=True,
        help=(
            "Optional PaddleOCR-VL/PP-StructureV3 style JSON evidence. "
            "For multiple sources, pass files in source order or use matching names."
        ),
    ),
    translation_stress: BenchmarkTranslationStress = typer.Option(
        "off",
        help="Deterministic pseudo-translation stress for replacement metrics: off or pseudo-expand.",
    ),
) -> None:
    report = run_benchmark(
        sources,
        out_dir,
        dpi=dpi,
        input_kind=input_kind,
        image_dpi=image_dpi,
        max_pages=max_pages,
        page_ranges=page_ranges,
        structure_jsons=structure_json,
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
    typer.echo(f"Benchmark report: {out_dir / 'benchmark_report.json'}")
    typer.echo(f"Benchmark CSV: {out_dir / 'benchmark_summary.csv'}")
    typer.echo(f"Cases: {report['case_count']}")
    typer.echo(f"Mean visual similarity: {report['summary'].get('mean_visual_similarity')}")
    typer.echo(f"Max diff ratio: {report['summary'].get('max_diff_ratio')}")
    typer.echo(f"Mean diff ratio: {report['summary'].get('mean_diff_ratio')}")
    typer.echo(f"Input kind: {report.get('input_kind')}")
    typer.echo(f"Image DPI: {report.get('image_dpi')}")
    typer.echo(f"Max pages: {report.get('max_pages')}")
    typer.echo(f"Page ranges: {report.get('page_ranges')}")
    typer.echo(f"Font profile: {report.get('font_profile')}")
    typer.echo(f"Raster policy: {report.get('raster_policy')}")
    typer.echo(f"OCR fallback: {report.get('ocr_fallback')}")
    typer.echo(f"OCR language: {report.get('ocr_language')}")
    typer.echo(f"OCR DPI: {report.get('ocr_dpi')}")
    typer.echo(f"HTML mode: {report.get('html_mode')}")
    typer.echo(f"Font size scale: {report.get('font_size_scale')}")
    typer.echo(f"Text fit: {report.get('text_fit')}")
    typer.echo(f"Fidelity background: {report.get('fidelity_background')}")
    typer.echo(f"Translation stress: {report.get('translation_stress')}")
    typer.echo(f"Mismatched cases: {report['summary'].get('mismatched_case_count')}")
    typer.echo(f"Semantic cases: {report['summary'].get('semantic_case_count')}")
    typer.echo(f"Mean semantic order accuracy: {report['summary'].get('mean_semantic_order_pair_accuracy')}")
    typer.echo(f"OCR fallback pages: {report['summary'].get('total_ocr_fallback_applied_pages')}")
    typer.echo(f"OCR text elements: {report['summary'].get('total_ocr_text_elements')}")
    typer.echo(f"Structure evidence regions: {report['summary'].get('total_structure_evidence_regions')}")
    typer.echo(f"Structure evidence matched elements: {report['summary'].get('total_structure_evidence_matched_elements')}")
    typer.echo(f"Translation stress elements: {report['summary'].get('total_translation_stress_elements')}")
    typer.echo(f"Fidelity replacement conflicts: {report['summary'].get('total_fidelity_replacement_conflicts')}")


@app.command("benchmark-structure-ab")
def benchmark_structure_ab_command(
    sources: list[Path] = typer.Argument(
        ...,
        help="Source PDF/image files to compare with and without structure evidence.",
    ),
    out_dir: Path = typer.Option(Path("outputs/structure-ab"), help="A/B benchmark output directory."),
    structure_json: list[Path] = typer.Option(
        ...,
        "--structure-json",
        exists=True,
        readable=True,
        help="PaddleOCR-VL/PP-StructureV3/Docling JSON evidence. Pass files in PDF order or use matching names.",
    ),
    dpi: int = typer.Option(192, min=72, max=600, help="Render DPI for visual comparison."),
    input_kind: SourceKind = typer.Option(
        "auto",
        help="Source type for explicit inputs: auto, pdf, or image.",
    ),
    image_dpi: int = typer.Option(
        96,
        min=1,
        max=1200,
        help="Pixel density used to map image source pixels into PDF points.",
    ),
    max_pages: Optional[int] = typer.Option(
        None,
        min=1,
        help="Limit each benchmark source to the first N pages for large external documents.",
    ),
    page_ranges: Optional[str] = typer.Option(
        None,
        help="Score explicit 1-based source page ranges, for example 1-12,136-160. Cannot be combined with --max-pages.",
    ),
    font_profile: BenchmarkFontProfile = typer.Option(
        "browser-default",
        help="CSS font fallback profile for native PDF text, or auto.",
    ),
    raster_policy: RasterPolicy = typer.Option(
        "dense",
        help="Native raster fallback policy: none, dense, or tables.",
    ),
    ocr_fallback: OcrFallback = typer.Option(
        "image-only",
        help="OCR fallback policy: off or image-only for textless raster pages and image sources.",
    ),
    ocr_language: str = typer.Option(
        "eng+chi_sim",
        help="Tesseract language list for image-only OCR fallback.",
    ),
    ocr_dpi: int = typer.Option(
        144,
        min=72,
        max=600,
        help="OCR render DPI used only by the image-only fallback.",
    ),
    html_mode: BenchmarkHtmlMode = typer.Option(
        "structured",
        help="HTML mode to score: structured, fidelity, or auto.",
    ),
    font_size_scale: str = typer.Option(
        "1.0",
        help="Global CSS font-size multiplier for visual calibration experiments, or auto.",
    ),
    text_fit: BenchmarkTextFit = typer.Option(
        "none",
        help="Structured text fitting strategy: none, svg, or auto.",
    ),
    fidelity_background: BenchmarkFidelityBackground = typer.Option(
        "auto",
        help="Fidelity background source: svg, raster, or auto.",
    ),
    translation_stress: BenchmarkTranslationStress = typer.Option(
        "off",
        help="Deterministic pseudo-translation stress for replacement metrics: off or pseudo-expand.",
    ),
) -> None:
    report = run_structure_ab_benchmark(
        sources,
        out_dir,
        structure_json,
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
    typer.echo(f"Structure A/B report: {out_dir / 'structure_ab_report.json'}")
    typer.echo(f"Structure A/B CSV: {out_dir / 'structure_ab_summary.csv'}")
    typer.echo(f"Native report: {report['native_report']}")
    typer.echo(f"Native-plus-structure report: {report['structure_report']}")
    typer.echo(f"Cases: {report['case_count']}")
    typer.echo(f"Input kind: {report.get('input_kind')}")
    typer.echo(f"Image DPI: {report.get('image_dpi')}")
    typer.echo(f"Page ranges: {report.get('page_ranges')}")
    typer.echo(f"Mean visual similarity delta: {report['summary'].get('mean_visual_similarity_delta')}")
    typer.echo(f"Mean reading-order risk delta: {report['summary'].get('mean_reading_order_risk_score_delta')}")
    typer.echo(f"Grid-island element delta: {report['summary'].get('total_grid_island_element_delta')}")
    typer.echo(f"Translation stress element delta: {report['summary'].get('total_translation_stress_element_delta')}")
    typer.echo(
        "Fidelity replacement conflict delta: "
        f"{report['summary'].get('total_fidelity_replacement_conflict_delta')}"
    )
    typer.echo(
        "Stream needs-structure-evidence delta: "
        f"{report['summary'].get('total_stream_needs_structure_evidence_delta')}"
    )
    typer.echo(
        "Mean semantic stream assignment id delta: "
        f"{report['summary'].get('mean_semantic_stream_assignment_id_accuracy_delta')}"
    )
    typer.echo(
        "Mean semantic stream assignment type delta: "
        f"{report['summary'].get('mean_semantic_stream_assignment_type_accuracy_delta')}"
    )
    typer.echo(
        "Structure evidence matched elements: "
        f"{report['summary'].get('total_structure_evidence_matched_elements')}"
    )


@app.command("capture-pdf")
def capture_pdf_command(
    source: str = typer.Argument(..., help="URL, file path, or direct PDF URL."),
    pdf: Path = typer.Option(Path("outputs/captured.pdf"), help="Captured PDF path."),
    mode: CaptureMode = typer.Option("print", help="Use print for HTML pages or download for direct PDF URLs."),
    chrome: Optional[str] = typer.Option(None, help="Optional Chrome/Chromium executable path."),
) -> None:
    pdf_path = capture_pdf(source, pdf, mode=mode, chrome_executable=chrome)
    typer.echo(f"PDF: {pdf_path}")


@app.command("run-paddleocr-vl")
def run_paddleocr_vl_command(
    source: Path = typer.Argument(..., exists=True, readable=True, help="Input PDF or image source."),
    output: Path = typer.Option(
        Path("outputs/paddleocr-vl.raw.json"),
        "--output",
        "-o",
        help="Raw PaddleOCR-VL structure JSON to persist for replay or A/B benchmarking.",
    ),
    dpi: int = typer.Option(192, min=72, max=600, help="Render DPI for PDF source pages."),
    input_kind: SourceKind = typer.Option(
        "auto",
        help="Source type: auto, pdf, or image.",
    ),
    image_dpi: int = typer.Option(
        96,
        min=1,
        max=1200,
        help="Pixel density used to map image pixels into PDF points for image sources.",
    ),
    max_pages: Optional[int] = typer.Option(
        None,
        min=1,
        help="Limit model execution to the first N source pages.",
    ),
    page_ranges: Optional[str] = typer.Option(
        None,
        help="Explicit 1-based source page ranges, for example 1-3,136. Cannot be combined with --max-pages.",
    ),
    device: Optional[str] = typer.Option(
        None,
        help="Optional Paddle device, for example gpu:0 or cpu.",
    ),
    vl_rec_model_dir: Optional[Path] = typer.Option(
        None,
        help="Optional local PaddleOCR-VL recognition model directory.",
    ),
) -> None:
    """Run PaddleOCR-VL 1.6 on rendered source pages and persist its raw JSON."""

    try:
        page_indices = page_indices_from_ranges(page_ranges, max_pages=max_pages)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--page-ranges") from exc
    rendered = render_source(
        source,
        output.parent / f"{output.stem}.pages",
        dpi=dpi,
        max_pages=max_pages,
        page_indices=page_indices,
        input_kind=input_kind,
        image_dpi=image_dpi,
    )
    options: dict[str, object] = {}
    if device:
        options["device"] = device
    if vl_rec_model_dir is not None:
        options["vl_rec_model_dir"] = str(vl_rec_model_dir)
    payload = PaddleOcrAdapter(**options).analyze(
        [page.background_image for page in rendered.pages],
        page_indices=[page.page_index for page in rendered.pages],
    )
    output_path = write_ocr_json(payload, output)
    typer.echo(f"PaddleOCR-VL JSON: {output_path}")
    typer.echo(f"Pages: {len(rendered.pages)}")
    typer.echo(f"Source type: {rendered.source_type}")
    typer.echo(f"Model: {payload.get('model')}")


@app.command("run-pp-structure")
def run_pp_structure_command(
    source: Path = typer.Argument(..., exists=True, readable=True, help="Input PDF or image source."),
    output: Path = typer.Option(
        Path("outputs/pp-structure.raw.json"),
        "--output",
        "-o",
        help="Raw PP-StructureV3 JSON to persist for replay or A/B benchmarking.",
    ),
    dpi: int = typer.Option(192, min=72, max=600, help="Render DPI for PDF source pages."),
    input_kind: SourceKind = typer.Option(
        "auto",
        help="Source type: auto, pdf, or image.",
    ),
    image_dpi: int = typer.Option(
        96,
        min=1,
        max=1200,
        help="Pixel density used to map image pixels into PDF points for image sources.",
    ),
    max_pages: Optional[int] = typer.Option(
        None,
        min=1,
        help="Limit model execution to the first N source pages.",
    ),
    page_ranges: Optional[str] = typer.Option(
        None,
        help="Explicit 1-based source page ranges, for example 1-3,136. Cannot be combined with --max-pages.",
    ),
    device: Optional[str] = typer.Option(
        None,
        help="Optional Paddle device, for example gpu:0 or cpu.",
    ),
    table_recognition: bool = typer.Option(
        False,
        "--table-recognition/--no-table-recognition",
        help="Enable PP-Structure table recognition and cell evidence; disabled for lightweight layout-only runs.",
    ),
    formula_recognition: bool = typer.Option(
        False,
        "--formula-recognition/--no-formula-recognition",
        help="Enable PP-Structure formula recognition.",
    ),
    region_detection: bool = typer.Option(
        False,
        "--region-detection/--no-region-detection",
        help="Enable PP-Structure document-region detection.",
    ),
    cpu_compatibility_mode: bool = typer.Option(
        True,
        "--cpu-compatibility-mode/--no-cpu-compatibility-mode",
        help="Disable Paddle 3.3 PIR/oneDNN defaults before PP-StructureV3 imports on CPU.",
    ),
) -> None:
    """Run PP-StructureV3 on rendered source pages and persist raw JSON."""

    try:
        page_indices = page_indices_from_ranges(page_ranges, max_pages=max_pages)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--page-ranges") from exc
    rendered = render_source(
        source,
        output.parent / f"{output.stem}.pages",
        dpi=dpi,
        max_pages=max_pages,
        page_indices=page_indices,
        input_kind=input_kind,
        image_dpi=image_dpi,
    )
    options: dict[str, object] = {
        "use_table_recognition": table_recognition,
        "use_formula_recognition": formula_recognition,
        "use_region_detection": region_detection,
    }
    if device:
        options["device"] = device
    payload = PpStructureAdapter(
        cpu_compatibility_mode=cpu_compatibility_mode,
        **options,
    ).analyze(
        [page.background_image for page in rendered.pages],
        page_indices=[page.page_index for page in rendered.pages],
    )
    output_path = write_ocr_json(payload, output)
    typer.echo(f"PP-StructureV3 JSON: {output_path}")
    typer.echo(f"Pages: {len(rendered.pages)}")
    typer.echo(f"Source type: {rendered.source_type}")
    typer.echo(f"Model: {payload.get('model')}")


@app.command()
def convert(
    source: Path = typer.Argument(..., exists=True, readable=True, help="Input source PDF or image."),
    out_dir: Path = typer.Option(Path("outputs/document"), help="Conversion output directory."),
    ocr_json: Optional[Path] = typer.Option(None, exists=True, readable=True, help="Fallback OCR JSON."),
    structure_json: Optional[Path] = typer.Option(
        None,
        exists=True,
        readable=True,
        help="Optional PaddleOCR-VL/PP-StructureV3 style structure evidence JSON.",
    ),
    extract_mode: Literal["auto", "ocr-json", "native"] = typer.Option(
        "auto",
        help="Extraction mode. auto uses OCR/structure JSON when provided, native extraction for PDFs, and OCR fallback for image sources.",
    ),
    font_profile: FontProfile = typer.Option(
        "browser-default",
        help="CSS font fallback profile for native PDF text. Use local-urw for local Nimbus/DejaVu experiments.",
    ),
    raster_policy: RasterPolicy = typer.Option(
        "dense",
        help="Native raster fallback policy: none, dense, or tables for experimental complex table regions.",
    ),
    ocr_fallback: OcrFallback = typer.Option(
        "image-only",
        help="OCR fallback policy: off or image-only for textless raster pages and image sources.",
    ),
    ocr_language: str = typer.Option(
        "eng+chi_sim",
        help="Tesseract language list for image-only OCR fallback, for example eng or eng+chi_sim.",
    ),
    ocr_dpi: int = typer.Option(
        144,
        min=72,
        max=600,
        help="OCR render DPI used only by the image-only fallback.",
    ),
    svg_background: bool = typer.Option(
        False,
        "--svg-background",
        help="Also export per-page SVG backgrounds for fidelity overlay HTML.",
    ),
    input_kind: SourceKind = typer.Option(
        "auto",
        help="Source type: auto, pdf, or image. Images are rendered as one-page sources.",
    ),
    image_dpi: int = typer.Option(
        96,
        min=1,
        max=1200,
        help="Pixel density used to map image pixels into PDF points for image sources.",
    ),
    font_size_scale: float = typer.Option(
        1.0,
        min=0.9,
        max=1.1,
        help="Global CSS font-size multiplier for native PDF text extraction.",
    ),
    dpi: int = typer.Option(192, min=72, max=600, help="PDF render DPI. Image sources use --image-dpi."),
) -> None:
    pages_dir = out_dir / "pages"
    crops_dir = out_dir / "crops"
    rendered = render_source(
        source,
        pages_dir,
        dpi=dpi,
        include_svg_background=svg_background,
        input_kind=input_kind,
        image_dpi=image_dpi,
    )
    structure_payload = load_structure_json(structure_json) if structure_json else None
    if rendered.source_type == "pdf" and (extract_mode == "native" or (extract_mode == "auto" and ocr_json is None)):
        document = extract_native_pdf_to_ir(
            rendered,
            font_profile=font_profile,
            raster_policy=raster_policy,
            font_size_scale=font_size_scale,
            ocr_fallback=ocr_fallback,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
        )
    elif extract_mode == "native":
        raise typer.BadParameter("native extraction only supports PDF sources; use auto or ocr-json for image sources")
    else:
        ocr_payload = load_ocr_json(ocr_json) if ocr_json else structure_payload
        document = normalize_ocr_to_ir(
            rendered,
            ocr_payload,
            crop_dir=crops_dir,
            ocr_fallback=ocr_fallback,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
        )
    if structure_payload:
        apply_structure_evidence(document, structure_payload)
    annotate_document(document)
    ir_path = out_dir / "document.ir.json"
    document.save(ir_path)
    typer.echo(f"IR: {ir_path}")
    typer.echo(f"Pages: {len(document.pages)}")
    typer.echo(f"Source type: {document.source_type}")


@app.command("propose-reading-sidecar")
def propose_reading_sidecar(
    ir_json: Path = typer.Argument(..., exists=True, readable=True, help="Annotated DocumentIR JSON."),
    sidecar: Path = typer.Option(
        Path("outputs/reading-order.sidecar.proposal.json"),
        help="Reviewable local successor-edge and reading-stream sidecar JSON.",
    ),
) -> None:
    """Generate a reviewable local reading-order sidecar without modifying the IR."""

    payload = write_reading_order_sidecar(DocumentIR.load(ir_json), sidecar)
    summary = reading_order_sidecar_summary(payload)
    typer.echo(f"Reading-order sidecar proposal: {sidecar}")
    typer.echo(f"Streams: {summary['stream_count']}")
    typer.echo(f"Successor edges: {summary['successor_edge_count']}")
    typer.echo(f"Review successor edges: {summary['review_successor_edge_count']}")
    typer.echo(f"Review transitions: {summary['review_transition_count']}")


@app.command("export-html")
def export_html_command(
    ir_json: Path = typer.Argument(..., exists=True, readable=True, help="DocumentIR JSON."),
    out_dir: Path = typer.Option(Path("outputs/html"), help="HTML output directory."),
    display_mode: DisplayMode = typer.Option("background", help="HTML display mode."),
    text_fit: HtmlTextFit = typer.Option(
        "none",
        help="Structured text fitting strategy. svg emits a fitted SVG text layer plus editable proxy.",
    ),
) -> None:
    document = DocumentIR.load(ir_json)
    html_path = export_html(document, out_dir, display_mode=display_mode, text_fit=text_fit)
    typer.echo(f"HTML: {html_path}")


@app.command("export-xml")
def export_xml_command(
    ir_json: Path = typer.Argument(..., exists=True, readable=True, help="DocumentIR JSON."),
    xml: Path = typer.Option(Path("outputs/document.xml"), help="Editable XML output path."),
    text_mode: DisplayMode = typer.Option("structured", help="Text mode to write into XML nodes."),
) -> None:
    document = DocumentIR.load(ir_json)
    xml_path = export_document_xml(document, xml, text_mode=text_mode)
    typer.echo(f"XML: {xml_path}")


@app.command("set-xml-node")
def set_xml_node_command(
    xml: Path = typer.Argument(..., exists=True, readable=True, help="Editable XML file."),
    element_id: str = typer.Argument(..., help="Element id."),
    text: str = typer.Argument(..., help="New XML node text."),
) -> None:
    set_xml_element_text(xml, element_id, text)
    typer.echo(f"Updated XML node {element_id}")


@app.command("apply-xml-edits")
def apply_xml_edits_command(
    ir_json: Path = typer.Argument(..., exists=True, readable=True, help="DocumentIR JSON to update."),
    xml: Path = typer.Argument(..., exists=True, readable=True, help="Edited XML file."),
    target_field: Literal["edited_text", "translated_text"] = typer.Option("edited_text", help="IR field to update."),
) -> None:
    document = DocumentIR.load(ir_json)
    changed = apply_xml_edits(document, xml, target_field=target_field)
    document.save(ir_json)
    typer.echo(f"Changed elements: {changed}")


@app.command("apply-html-edits")
def apply_html_edits_command(
    ir_json: Path = typer.Argument(..., exists=True, readable=True, help="DocumentIR JSON to update."),
    patch: Path = typer.Argument(..., exists=True, readable=True, help="Browser edit patch JSON."),
    allow_document_mismatch: bool = typer.Option(
        False,
        help="Allow a patch created from a different DocumentIR id after manual identity review.",
    ),
    allow_source_mismatch: bool = typer.Option(
        False,
        help="Allow edits whose exported source text no longer matches the target element.",
    ),
) -> None:
    """Apply a Scriptorium HTML edit patch to edited_text or translated_text."""

    document = DocumentIR.load(ir_json)
    changed = apply_html_edit_patch(
        document,
        patch,
        require_document_id=not allow_document_mismatch,
        require_source_match=not allow_source_mismatch,
    )
    document.save(ir_json)
    typer.echo(f"Changed elements: {changed}")


@app.command("quality-check")
def quality_check(
    ir_json: Path = typer.Argument(..., exists=True, readable=True, help="DocumentIR JSON."),
    html: Path = typer.Argument(..., exists=True, readable=True, help="Exported HTML file."),
    out_dir: Path = typer.Option(Path("outputs/quality"), help="Quality report directory."),
    chrome: Optional[str] = typer.Option(None, help="Optional Chrome/Chromium executable path."),
) -> None:
    document = DocumentIR.load(ir_json)
    report = compare_html_to_rendered_pdf(document, html, out_dir, chrome_executable=chrome)
    typer.echo(f"Quality report: {out_dir / 'quality_report.json'}")
    typer.echo(f"Max diff ratio: {report['max_diff_ratio']}")
    typer.echo(f"Mean diff ratio: {report['mean_diff_ratio']}")
    typer.echo(f"Dimension match: {report['dimension_match']}")


@app.command("print-pdf")
def print_pdf(
    html: Path = typer.Argument(..., exists=True, readable=True, help="Exported HTML file."),
    pdf: Path = typer.Option(Path("outputs/export.pdf"), help="Output PDF path."),
    chrome: Optional[str] = typer.Option(None, help="Optional Chrome/Chromium executable path."),
) -> None:
    pdf_path = print_html_to_pdf(html, pdf, chrome_executable=chrome)
    typer.echo(f"PDF: {pdf_path}")


@app.command("compare-pdf")
def compare_pdf(
    expected_pdf: Path = typer.Argument(..., exists=True, readable=True, help="Original PDF."),
    actual_pdf: Path = typer.Argument(..., exists=True, readable=True, help="Generated PDF."),
    out_dir: Path = typer.Option(Path("outputs/pdf-quality"), help="PDF render comparison output directory."),
    dpi: int = typer.Option(192, min=72, max=600, help="Render DPI for both PDFs."),
) -> None:
    report = compare_pdf_renderings(expected_pdf, actual_pdf, out_dir, dpi=dpi)
    typer.echo(f"PDF quality report: {out_dir / 'pdf_quality_report.json'}")
    typer.echo(f"Max diff ratio: {report['max_diff_ratio']}")
    typer.echo(f"Mean diff ratio: {report['mean_diff_ratio']}")
    typer.echo(f"Page count match: {report['page_count_match']}")
    typer.echo(f"Dimension match: {report['dimension_match']}")


@app.command("set-text")
def set_text(
    ir_json: Path = typer.Argument(..., exists=True, readable=True, help="DocumentIR JSON to update."),
    element_id: str = typer.Argument(..., help="Element id."),
    text: str = typer.Argument(..., help="New edited text."),
) -> None:
    document = DocumentIR.load(ir_json)
    element = document.find_element(element_id)
    element.edited_text = text
    document.revisions.append(RevisionIR(reason="edit-text", payload={"element_id": element_id}))
    document.save(ir_json)
    typer.echo(f"Updated {element_id}")


@app.command("set-translation")
def set_translation(
    ir_json: Path = typer.Argument(..., exists=True, readable=True, help="DocumentIR JSON to update."),
    element_id: str = typer.Argument(..., help="Element id."),
    text: str = typer.Argument(..., help="Translated text."),
) -> None:
    document = DocumentIR.load(ir_json)
    element = document.find_element(element_id)
    element.translated_text = text
    document.revisions.append(RevisionIR(reason="set-translation", payload={"element_id": element_id}))
    document.save(ir_json)
    typer.echo(f"Updated translation for {element_id}")


if __name__ == "__main__":
    app()
