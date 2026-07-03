from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import typer

from .annotations import annotate_document
from .benchmark import BenchmarkFontProfile, BenchmarkHtmlMode, run_benchmark
from .fixture import create_fixture
from .html_export import export_html
from .models import DisplayMode, DocumentIR, RevisionIR
from .native_pdf import FontProfile, RasterPolicy, extract_native_pdf_to_ir
from .ocr import load_ocr_json, normalize_ocr_to_ir
from .pdf_export import print_html_to_pdf
from .pdf_render import render_pdf
from .playwright_capture import CaptureMode, capture_pdf
from .quality import compare_html_to_rendered_pdf, compare_pdf_renderings
from .structure_evidence import apply_structure_evidence, load_structure_json
from .web_fixture import create_web_fixture
from .xml_edit import apply_xml_edits, export_document_xml, set_xml_element_text

app = typer.Typer(help="Scriptorium PDF core conversion tools.")


@app.command()
def make_fixture(out_dir: Path = typer.Option(Path("data/fixture"), help="Directory for sample PDF and OCR JSON.")) -> None:
    pdf_path, ocr_path = create_fixture(out_dir)
    typer.echo(f"PDF: {pdf_path}")
    typer.echo(f"OCR JSON: {ocr_path}")


@app.command("make-web-fixture")
def make_web_fixture(out_dir: Path = typer.Option(Path("data/web-fixture"), help="Directory for structured HTML fixture.")) -> None:
    html_path = create_web_fixture(out_dir)
    typer.echo(f"HTML: {html_path}")


@app.command("benchmark")
def benchmark_command(
    pdf: Optional[list[Path]] = typer.Argument(None, help="Optional PDF files. If omitted, built-in fixtures are generated."),
    out_dir: Path = typer.Option(Path("outputs/benchmark"), help="Benchmark output directory."),
    dpi: int = typer.Option(192, min=72, max=600, help="Render DPI for visual comparison."),
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
    html_mode: BenchmarkHtmlMode = typer.Option(
        "structured",
        help=(
            "HTML mode to score. structured redraws editable PDF elements; "
            "fidelity keeps the page raster as the visible layer and overlays editable coordinates."
        ),
    ),
    font_size_scale: str = typer.Option(
        "1.0",
        help="Global CSS font-size multiplier for visual calibration experiments, or auto.",
    ),
    structure_json: Optional[list[Path]] = typer.Option(
        None,
        "--structure-json",
        exists=True,
        readable=True,
        help=(
            "Optional PaddleOCR-VL/PP-StructureV3 style JSON evidence. "
            "For multiple PDFs, pass files in PDF order or use matching names."
        ),
    ),
) -> None:
    report = run_benchmark(
        pdf,
        out_dir,
        dpi=dpi,
        structure_jsons=structure_json,
        font_profile=font_profile,
        raster_policy=raster_policy,
        html_mode=html_mode,
        font_size_scale=font_size_scale,
    )
    typer.echo(f"Benchmark report: {out_dir / 'benchmark_report.json'}")
    typer.echo(f"Benchmark CSV: {out_dir / 'benchmark_summary.csv'}")
    typer.echo(f"Cases: {report['case_count']}")
    typer.echo(f"Mean visual similarity: {report['summary'].get('mean_visual_similarity')}")
    typer.echo(f"Max diff ratio: {report['summary'].get('max_diff_ratio')}")
    typer.echo(f"Mean diff ratio: {report['summary'].get('mean_diff_ratio')}")
    typer.echo(f"Font profile: {report.get('font_profile')}")
    typer.echo(f"Raster policy: {report.get('raster_policy')}")
    typer.echo(f"HTML mode: {report.get('html_mode')}")
    typer.echo(f"Font size scale: {report.get('font_size_scale')}")
    typer.echo(f"Mismatched cases: {report['summary'].get('mismatched_case_count')}")
    typer.echo(f"Semantic cases: {report['summary'].get('semantic_case_count')}")
    typer.echo(f"Mean semantic order accuracy: {report['summary'].get('mean_semantic_order_pair_accuracy')}")
    typer.echo(f"Structure evidence regions: {report['summary'].get('total_structure_evidence_regions')}")
    typer.echo(f"Structure evidence matched elements: {report['summary'].get('total_structure_evidence_matched_elements')}")


@app.command("capture-pdf")
def capture_pdf_command(
    source: str = typer.Argument(..., help="URL, file path, or direct PDF URL."),
    pdf: Path = typer.Option(Path("outputs/captured.pdf"), help="Captured PDF path."),
    mode: CaptureMode = typer.Option("print", help="Use print for HTML pages or download for direct PDF URLs."),
    chrome: Optional[str] = typer.Option(None, help="Optional Chrome/Chromium executable path."),
) -> None:
    pdf_path = capture_pdf(source, pdf, mode=mode, chrome_executable=chrome)
    typer.echo(f"PDF: {pdf_path}")


@app.command()
def convert(
    pdf: Path = typer.Argument(..., exists=True, readable=True, help="Input PDF."),
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
        help="Extraction mode. auto uses OCR JSON when provided, otherwise native PDF text extraction.",
    ),
    font_profile: FontProfile = typer.Option(
        "browser-default",
        help="CSS font fallback profile for native PDF text. Use local-urw for local Nimbus/DejaVu experiments.",
    ),
    raster_policy: RasterPolicy = typer.Option(
        "dense",
        help="Native raster fallback policy: none, dense, or tables for experimental complex table regions.",
    ),
    svg_background: bool = typer.Option(
        False,
        "--svg-background",
        help="Also export per-page SVG backgrounds for fidelity overlay HTML.",
    ),
    font_size_scale: float = typer.Option(
        1.0,
        min=0.9,
        max=1.1,
        help="Global CSS font-size multiplier for native PDF text extraction.",
    ),
    dpi: int = typer.Option(192, min=72, max=600, help="PDF render DPI."),
) -> None:
    pages_dir = out_dir / "pages"
    crops_dir = out_dir / "crops"
    rendered = render_pdf(pdf, pages_dir, dpi=dpi, include_svg_background=svg_background)
    if extract_mode == "native" or (extract_mode == "auto" and ocr_json is None):
        document = extract_native_pdf_to_ir(
            rendered,
            font_profile=font_profile,
            raster_policy=raster_policy,
            font_size_scale=font_size_scale,
        )
    else:
        ocr_payload = load_ocr_json(ocr_json) if ocr_json else None
        document = normalize_ocr_to_ir(rendered, ocr_payload, crop_dir=crops_dir)
    if structure_json:
        apply_structure_evidence(document, load_structure_json(structure_json))
    annotate_document(document)
    ir_path = out_dir / "document.ir.json"
    document.save(ir_path)
    typer.echo(f"IR: {ir_path}")
    typer.echo(f"Pages: {len(document.pages)}")


@app.command("export-html")
def export_html_command(
    ir_json: Path = typer.Argument(..., exists=True, readable=True, help="DocumentIR JSON."),
    out_dir: Path = typer.Option(Path("outputs/html"), help="HTML output directory."),
    display_mode: DisplayMode = typer.Option("background", help="HTML display mode."),
) -> None:
    document = DocumentIR.load(ir_json)
    html_path = export_html(document, out_dir, display_mode=display_mode)
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
