from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
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
from .chunkr_benchmark import (
    benchmark_chunkr_reading_order,
    fetch_chunkr_reading_order_annotations,
)
from .chunkr_order_ranker import (
    benchmark_chunkr_order_ranker_roor,
    predict_chunkr_block_order,
    train_chunkr_order_ranker,
)
from .comphrdoc_benchmark import (
    benchmark_comphrdoc_relation_corpus,
    fetch_comphrdoc_benchmark_samples,
    fetch_comphrdoc_provider_calibration_corpus,
    fetch_comphrdoc_provider_test_corpus,
    fetch_comphrdoc_relation_corpus,
)
from .docling_provider import DoclingAdapter
from .fixture import create_fixture
from .floating_ranker import predict_floating_relations, train_floating_relation_ranker
from .html_edits import apply_html_edit_patch
from .html_export import HtmlTextFit, export_html
from .hierarchical_order import build_hierarchical_order_proposal
from .hierarchical_order_adapter import (
    build_fine_hierarchy_input_from_document,
    build_hierarchy_input_from_document,
)
from .hierarchical_order_benchmark import (
    benchmark_hierarchical_order_corpus,
    materialize_comphrdoc_hierarchy_corpus,
)
from .models import DisplayMode, DocumentIR, RevisionIR
from .native_pdf import FontProfile, OcrFallback, RasterPolicy, extract_native_pdf_to_ir
from .ocr import (
    PpStructureAdapter,
    PaddleOcrAdapter,
    SuryaLayoutAdapter,
    load_ocr_json,
    normalize_ocr_to_ir,
    write_ocr_json,
)
from .opendataloader_provider import OpenDataLoaderAdapter
from .paddle_layout_provider import PaddleLayoutAdapter, run_paddle_layout_corpus
from .joint_graph_benchmark import benchmark_joint_graph, propose_joint_graph
from .paragraph_graph_benchmark import (
    benchmark_paragraph_graph,
    predict_paragraph_graph,
)
from .successor_graph_benchmark import (
    benchmark_successor_decoder_ab,
    benchmark_successor_graph,
    predict_successor_graph,
)
from .pdf_export import print_html_to_pdf
from .pdf_render import SourceKind, page_indices_from_ranges, render_pdf, render_source
from .playwright_capture import CaptureMode, capture_pdf
from .provider_anchor_benchmark import (
    PROVIDER_TRANSITION_CANDIDATES,
    benchmark_provider_anchor_suite,
    benchmark_provider_anchors,
    freeze_provider_transition_gate,
    freeze_stratified_provider_transition_gate,
)
from .provider_hierarchy_benchmark import (
    benchmark_provider_hierarchy_corpus,
    materialize_graph_hierarchy_corpus,
    materialize_provider_hierarchy_corpus,
)
from .quality import compare_html_to_rendered_pdf, compare_pdf_renderings
from .reading_order_sidecar import (
    build_provider_consensus_sidecar,
    reading_order_sidecar_summary,
    write_reading_order_sidecar,
)
from .relation_ranker import (
    predict_document_relations,
    predict_structure_relations,
    train_relation_ranker,
)
from .relation_ranker_benchmark import benchmark_relation_rankers_roor
from .roor_benchmark import RoorSplit, fetch_roor_benchmark_samples
from .semantic_successor import BERT_TINY_NSP_PRESET, create_semantic_pair_scorer
from .structure_evidence import apply_structure_evidence, load_structure_json
from .web_fixture import create_web_fixture
from .xml_edit import apply_xml_edits, export_document_xml, set_xml_element_text

app = typer.Typer(help="Scriptorium core conversion tools.")


def _is_document_ir_payload(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("pages"), list)
        and "page_count" in payload
        and "render_dpi" in payload
    )


def _semantic_scorer_from_options(
    preset: str | None,
    *,
    model_path: Path | None,
    cache_path: Path,
    batch_size: int,
    device: str,
):
    if preset is None:
        if model_path is not None:
            raise ValueError("--semantic-model-path requires --semantic-scorer")
        return None
    return create_semantic_pair_scorer(
        preset,
        load_from=model_path,
        cache_path=cache_path,
        batch_size=batch_size,
        device=device,
        local_files_only=model_path is not None,
    )


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


@app.command("benchmark-relation-rankers-roor")
def benchmark_relation_rankers_roor_command(
    corpus_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
    ),
    control_model: Path = typer.Option(
        ...,
        "--control-model",
        exists=True,
        readable=True,
    ),
    candidate_model: Path = typer.Option(
        ...,
        "--candidate-model",
        exists=True,
        readable=True,
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
    semantic_scorer: Optional[str] = typer.Option(
        None,
        "--semantic-scorer",
        help=f"Semantic scorer required by the candidate model ({BERT_TINY_NSP_PRESET}).",
    ),
    semantic_model_path: Optional[Path] = typer.Option(
        None,
        "--semantic-model-path",
        exists=True,
        readable=True,
    ),
    semantic_cache: Path = typer.Option(
        Path("outputs/cache/semantic-successor.sqlite3"),
        "--semantic-cache",
    ),
    semantic_batch_size: int = typer.Option(
        256,
        "--semantic-batch-size",
        min=1,
    ),
    semantic_device: str = typer.Option("cpu", "--semantic-device"),
) -> None:
    """Strict two-phase A/B for local rankers on a fetched ROOR corpus."""

    try:
        scorer = _semantic_scorer_from_options(
            semantic_scorer,
            model_path=semantic_model_path,
            cache_path=semantic_cache,
            batch_size=semantic_batch_size,
            device=semantic_device,
        )
        result = benchmark_relation_rankers_roor(
            corpus_dir,
            control_model,
            candidate_model,
            candidate_semantic_scorer=scorer,
            output=output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="corpus_dir") from exc
    control = result.report["summary"]["control"]
    candidate = result.report["summary"]["candidate"]
    typer.echo(
        "Branch F1 (control/candidate): "
        f"{control['branch']['f1']}/{candidate['branch']['f1']}"
    )
    typer.echo(
        "Path-cover F1 (control/candidate): "
        f"{control['path_cover']['f1']}/{candidate['path_cover']['f1']}"
    )
    typer.echo(f"Report: {result.report_path}")


@app.command("fetch-chunkr-reading-order")
def fetch_chunkr_reading_order_command(
    out_dir: Path = typer.Option(
        Path("data/external/chunkr-reading-order"),
        help="Directory for the pinned Chunkr COCO reading-order annotations.",
    ),
    annotation_file: Optional[Path] = typer.Option(
        None,
        "--annotation-file",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Optional local annotation file; the pinned SHA-256 is still verified.",
    ),
    refresh: bool = typer.Option(
        False,
        help="Redownload and rewrite the pinned annotation file.",
    ),
) -> None:
    """Fetch the development-only Chunkr reading-order annotation corpus."""

    try:
        result = fetch_chunkr_reading_order_annotations(
            out_dir,
            annotation_file=annotation_file,
            refresh=refresh,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--annotation-file") from exc
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    typer.echo(f"Chunkr pages: {manifest['image_count']}")
    typer.echo(f"Chunkr elements: {manifest['annotation_count']}")
    typer.echo(f"Annotations: {result.annotations_path}")
    typer.echo(f"Manifest: {result.manifest_path}")


@app.command("benchmark-chunkr-reading-order")
def benchmark_chunkr_reading_order_command(
    annotations: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Score answer-free geometry candidates on Chunkr reading-order labels."""

    try:
        result = benchmark_chunkr_reading_order(annotations, output=output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="annotations") from exc
    report = result.report
    for candidate in report["order_candidates"]:
        metrics = report["order_candidates"][candidate]["all"]
        typer.echo(
            f"{candidate}: exact {metrics['exact_match_count']}/"
            f"{metrics['page_count']} = {metrics['exact_match']}; "
            f"pairwise {metrics['pairwise_accuracy']}"
        )
    stable = report["support_curves"]["stable"]["thresholds"]["2"]["all"]
    all_channels = report["support_curves"]["all"]["thresholds"]["2"]["all"]
    typer.echo(
        "Support >= 2 precision/recall (stable/all): "
        f"{stable['precision']}/{stable['recall']} vs "
        f"{all_channels['precision']}/{all_channels['recall']}"
    )
    typer.echo(f"Report: {result.report_path}")


@app.command("train-chunkr-order-ranker")
def train_chunkr_order_ranker_command(
    annotations: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    output: Path = typer.Option(
        Path("outputs/models/chunkr-order-ranker.joblib"),
        "--output",
        "-o",
    ),
    cross_validation_folds: int = typer.Option(
        5,
        "--cross-validation-folds",
        min=2,
        help="Category/complexity-stratified answer-free page folds.",
    ),
    random_seed: int = typer.Option(17, "--random-seed"),
) -> None:
    """Train an isolated role-aware pairwise block-order candidate."""

    try:
        result = train_chunkr_order_ranker(
            annotations,
            output,
            cross_validation_folds=cross_validation_folds,
            random_seed=random_seed,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="annotations") from exc
    learned = result.report["learned_oof_metrics"]["all"]
    selected = result.report["baseline_metrics"]["selected-auto"]["all"]
    visual = result.report["baseline_metrics"]["visual-yx"]["all"]
    typer.echo(
        "OOF exact (learned/selected/visual): "
        f"{learned['exact_match']}/{selected['exact_match']}/"
        f"{visual['exact_match']}"
    )
    typer.echo(
        "OOF pairwise (learned/selected/visual): "
        f"{learned['pairwise_accuracy']}/{selected['pairwise_accuracy']}/"
        f"{visual['pairwise_accuracy']}"
    )
    typer.echo(
        "OOF successor (learned/selected/visual): "
        f"{learned['successor_accuracy']}/{selected['successor_accuracy']}/"
        f"{visual['successor_accuracy']}"
    )
    typer.echo(f"Model: {result.model_path}")
    typer.echo(f"Manifest: {result.manifest_path}")
    typer.echo(f"OOF report: {result.report_path}")


@app.command("predict-chunkr-order")
def predict_chunkr_order_command(
    model: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    input_json: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    output: Path = typer.Option(
        Path("outputs/chunkr-order.prediction.json"),
        "--output",
        "-o",
    ),
) -> None:
    """Predict one review-only block order from answer-free layout JSON."""

    try:
        payload = json.loads(input_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("input JSON root must be an object")
        result = predict_chunkr_block_order(payload, model)
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="input_json") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result.payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    typer.echo(f"Ordered elements: {len(result.ordered_ids)}")
    typer.echo(f"Mean pair margin: {result.diagnostics['mean_pair_margin']}")
    typer.echo(f"Prediction: {output}")


@app.command("benchmark-chunkr-order-ranker-roor")
def benchmark_chunkr_order_ranker_roor_command(
    corpus_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
    ),
    model: Path = typer.Option(
        ...,
        "--model",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Replay one frozen Chunkr order model on answer-separated ROOR pages."""

    try:
        result = benchmark_chunkr_order_ranker_roor(
            corpus_dir,
            model,
            output=output,
        )
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="corpus_dir") from exc
    report = result.report
    learned = report["candidate_metrics"]["learned"]
    selected = report["candidate_metrics"]["selected-auto"]
    visual = report["candidate_metrics"]["visual-yx"]
    typer.echo(
        "Direct recall (learned/selected/visual): "
        f"{learned['direct_recall']}/{selected['direct_recall']}/"
        f"{visual['direct_recall']}"
    )
    typer.echo(
        "Precedence (learned/selected/visual): "
        f"{learned['precedence_accuracy']}/"
        f"{selected['precedence_accuracy']}/"
        f"{visual['precedence_accuracy']}"
    )
    typer.echo(
        "Page-profile in-envelope/outlier: "
        f"{report['page_profile_in_envelope_count']}/"
        f"{report['page_profile_outlier_page_count']}"
    )
    typer.echo(f"Decision: {report['promotion_decision']}")
    typer.echo(f"Report: {result.report_path}")


@app.command("export-hierarchy-input")
def export_hierarchy_input_command(
    ir_json: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="DocumentIR JSON.",
    ),
    output: Path = typer.Option(
        Path("outputs/hierarchy-input.json"),
        "--output",
        "-o",
    ),
    page_index: int = typer.Option(
        0,
        "--page-index",
        min=0,
        help="Zero-based DocumentIR page index to export.",
    ),
    sample_id: Optional[str] = typer.Option(
        None,
        "--sample-id",
        help="Optional hierarchy input id.",
    ),
    structure_json: Optional[Path] = typer.Option(
        None,
        "--structure-json",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Optional provider structure JSON. When omitted, export fine-only input.",
    ),
) -> None:
    """Export answer-free hierarchy input for graph prediction or hierarchy proposals."""

    try:
        document = DocumentIR.load(ir_json)
        if structure_json is None:
            adapter_result = build_fine_hierarchy_input_from_document(
                document,
                page_index=page_index,
                sample_id=sample_id,
            )
        else:
            adapter_result = build_hierarchy_input_from_document(
                document,
                load_structure_json(structure_json),
                page_index=page_index,
            )
            if sample_id:
                adapter_result.payload["id"] = sample_id
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="ir_json") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(adapter_result.payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    diagnostics = adapter_result.diagnostics
    typer.echo(f"Hierarchy input: {output}")
    typer.echo(f"Fine elements: {diagnostics.get('fine_element_count', 0)}")
    typer.echo(
        "Coarse regions: "
        f"{diagnostics.get('selected_coarse_region_count', 0)}"
    )
    typer.echo(f"Adapter: {diagnostics.get('adapter', 'provider-structure')}")


@app.command("build-hierarchical-order")
def build_hierarchical_order_command(
    input_json: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    output: Path = typer.Option(
        Path("outputs/hierarchical-order.proposal.json"),
        "--output",
        "-o",
    ),
    chunkr_model: Path | None = typer.Option(
        None,
        "--chunkr-model",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Optional coarse-block ranker; OOD pages suppress cross-region transitions.",
    ),
    structure_json: Path | None = typer.Option(
        None,
        "--structure-json",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Adapt a DocumentIR input with page-local provider block JSON.",
    ),
    page_index: int = typer.Option(
        0,
        "--page-index",
        min=0,
        help="Zero-based DocumentIR page index used with --structure-json.",
    ),
    min_geometry_coverage: float = typer.Option(
        0.8,
        "--min-geometry-coverage",
        min=0.000001,
        max=1.0,
    ),
    min_geometry_margin: float = typer.Option(
        0.1,
        "--min-geometry-margin",
        min=0.0,
        max=1.0,
    ),
) -> None:
    """Build an isolated coarse-region/line-order proposal."""

    try:
        payload = json.loads(input_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("input JSON root must be an object")
        adapter_result = None
        if structure_json is not None:
            if not _is_document_ir_payload(payload):
                raise ValueError(
                    "--structure-json requires input_json to be a DocumentIR"
                )
            document = DocumentIR.model_validate(payload)
            adapter_result = build_hierarchy_input_from_document(
                document,
                load_structure_json(structure_json),
                page_index=page_index,
            )
            payload = adapter_result.payload
        elif _is_document_ir_payload(payload):
            raise ValueError("DocumentIR input requires --structure-json")
        result = build_hierarchical_order_proposal(
            payload,
            chunkr_model=chunkr_model,
            min_geometry_coverage=min_geometry_coverage,
            min_geometry_margin=min_geometry_margin,
        )
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="input_json") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result.payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    diagnostics = result.diagnostics
    if adapter_result is not None:
        adapter_diagnostics = adapter_result.diagnostics
        typer.echo(
            "Adapter regions (selected/rejected): "
            f"{adapter_diagnostics['selected_coarse_region_count']}/"
            f"{adapter_diagnostics['rejected_region_count']}"
        )
    typer.echo(
        "Membership (assigned/ambiguous/unassigned): "
        f"{diagnostics['assigned_element_count']}/"
        f"{diagnostics['ambiguous_element_count']}/"
        f"{diagnostics['unassigned_element_count']}"
    )
    typer.echo(
        "Transitions (emitted/suppressed): "
        f"{diagnostics['emitted_cross_region_transition_count']}/"
        f"{diagnostics['suppressed_cross_region_transition_count']}"
    )
    typer.echo(f"Decision: {diagnostics['promotion_decision']}")
    typer.echo(f"Proposal: {output}")


@app.command("materialize-comphrdoc-hierarchy")
def materialize_comphrdoc_hierarchy_command(
    source_corpus: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Answer-separated Comp-HRDoc provider calibration/test corpus.",
    ),
    output: Path = typer.Option(
        Path("outputs/comphrdoc-hierarchy-corpus"),
        "--output",
        "-o",
    ),
) -> None:
    """Materialize oracle regions with membership/order labels kept separate."""

    try:
        result = materialize_comphrdoc_hierarchy_corpus(source_corpus, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="source_corpus") from exc
    typer.echo(f"Hierarchy samples: {result.manifest['sample_count']}")
    typer.echo(f"Partitions: {result.manifest['partition_counts']}")
    typer.echo(f"Manifest: {result.manifest_path}")


@app.command("benchmark-hierarchical-order-corpus")
def benchmark_hierarchical_order_corpus_command(
    corpus_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
    ),
    output: Path = typer.Option(
        Path("outputs/hierarchical-order-benchmark.json"),
        "--output",
        "-o",
    ),
    proposals_dir: Path | None = typer.Option(
        None,
        "--proposals-dir",
        help="Optional directory for per-page review-only proposals.",
    ),
    chunkr_model: Path | None = typer.Option(
        None,
        "--chunkr-model",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Optional coarse-block ranker; OOD pages suppress transitions.",
    ),
    relation_model: Path | None = typer.Option(
        None,
        "--relation-model",
        exists=True,
        readable=True,
        help="Optional fine successor ranker used only as review evidence.",
    ),
    semantic_scorer: Optional[str] = typer.Option(
        None,
        "--semantic-scorer",
        help=f"Semantic scorer required by semantic relation models ({BERT_TINY_NSP_PRESET}).",
    ),
    semantic_model_path: Optional[Path] = typer.Option(
        None,
        "--semantic-model-path",
        exists=True,
        readable=True,
    ),
    semantic_cache: Path = typer.Option(
        Path("outputs/cache/semantic-successor.sqlite3"),
        "--semantic-cache",
    ),
    semantic_batch_size: int = typer.Option(
        256,
        "--semantic-batch-size",
        min=1,
    ),
    semantic_device: str = typer.Option("cpu", "--semantic-device"),
) -> None:
    """Score hierarchy membership and within/cross-region relations."""

    try:
        scorer = _semantic_scorer_from_options(
            semantic_scorer,
            model_path=semantic_model_path,
            cache_path=semantic_cache,
            batch_size=semantic_batch_size,
            device=semantic_device,
        )
        result = benchmark_hierarchical_order_corpus(
            corpus_dir,
            output=output,
            proposals_dir=proposals_dir,
            chunkr_model=chunkr_model,
            relation_model_path=relation_model,
            semantic_scorer=scorer,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="corpus_dir") from exc
    summary = result.report["summary"]
    membership = summary["membership"]
    within = summary["hierarchy_within"]
    cross = summary["hierarchy_region_cross"]
    typer.echo(
        "Membership (accuracy/coverage): "
        f"{membership['recall']}/{membership['coverage']}"
    )
    typer.echo(
        "Within-region successor (precision/recall/F1): "
        f"{within['precision']}/{within['recall']}/{within['f1']}"
    )
    typer.echo(
        "Region transition (precision/recall/F1): "
        f"{cross['precision']}/{cross['recall']}/{cross['f1']}"
    )
    typer.echo(f"Decision: {result.report['promotion_decision']}")
    typer.echo(f"Report: {result.report_path}")
    typer.echo(f"Proposals: {result.proposals_dir}")


@app.command("materialize-provider-hierarchy")
def materialize_provider_hierarchy_command(
    source_hierarchy_corpus: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Answer-separated hierarchy corpus supplying fine lines and labels.",
    ),
    provider_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Directory containing one provider structure JSON per sample.",
    ),
    output: Path = typer.Option(
        Path("outputs/provider-hierarchy-corpus"),
        "--output",
        "-o",
    ),
    provider_manifest: Optional[Path] = typer.Option(
        None,
        "--provider-manifest",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Optional provider run manifest; Paddle layout runs are auto-detected.",
    ),
) -> None:
    """Replace oracle coarse regions with answer-free provider blocks."""

    try:
        result = materialize_provider_hierarchy_corpus(
            source_hierarchy_corpus,
            provider_dir,
            output,
            provider_manifest=provider_manifest,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(
            str(exc),
            param_hint="source_hierarchy_corpus",
        ) from exc
    typer.echo(f"Provider hierarchy samples: {result.manifest['sample_count']}")
    typer.echo(f"Provider: {result.manifest['provider']}")
    typer.echo(f"Partitions: {result.manifest['partition_counts']}")
    typer.echo(f"Manifest: {result.manifest_path}")


@app.command("materialize-graph-hierarchy")
def materialize_graph_hierarchy_command(
    source_hierarchy_corpus: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Answer-separated hierarchy corpus supplying fine lines and labels.",
    ),
    output: Path = typer.Option(
        Path("outputs/graph-hierarchy-corpus"),
        "--output",
        "-o",
    ),
) -> None:
    """Convert a hierarchy corpus into graph-benchmark provider-hierarchy format."""

    try:
        result = materialize_graph_hierarchy_corpus(source_hierarchy_corpus, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(
            str(exc),
            param_hint="source_hierarchy_corpus",
        ) from exc
    typer.echo(f"Graph hierarchy samples: {result.manifest['sample_count']}")
    typer.echo(f"Provider: {result.manifest['provider']}")
    typer.echo(f"Partitions: {result.manifest['partition_counts']}")
    typer.echo(f"Manifest: {result.manifest_path}")


@app.command("benchmark-provider-hierarchy")
def benchmark_provider_hierarchy_command(
    corpus_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
    ),
    output: Path = typer.Option(
        Path("outputs/provider-hierarchy-benchmark.json"),
        "--output",
        "-o",
    ),
    proposals_dir: Path | None = typer.Option(
        None,
        "--proposals-dir",
        help="Optional directory for per-page review-only proposals.",
    ),
    relation_model: Path | None = typer.Option(
        None,
        "--relation-model",
        exists=True,
        readable=True,
        help="Optional fine successor ranker used only as review evidence.",
    ),
    semantic_scorer: Optional[str] = typer.Option(
        None,
        "--semantic-scorer",
        help=f"Semantic scorer required by semantic relation models ({BERT_TINY_NSP_PRESET}).",
    ),
    semantic_model_path: Optional[Path] = typer.Option(
        None,
        "--semantic-model-path",
        exists=True,
        readable=True,
    ),
    semantic_cache: Path = typer.Option(
        Path("outputs/cache/semantic-successor.sqlite3"),
        "--semantic-cache",
    ),
    semantic_batch_size: int = typer.Option(
        256,
        "--semantic-batch-size",
        min=1,
    ),
    semantic_device: str = typer.Option("cpu", "--semantic-device"),
    partition: Optional[str] = typer.Option(
        None,
        "--partition",
        help="Optional corpus partition to predict and score in isolation.",
    ),
    min_geometry_coverage: float = typer.Option(
        0.1,
        "--min-geometry-coverage",
        min=0.01,
        max=1.0,
        help="Minimum fine-line coverage by a provider detector block.",
    ),
    min_geometry_margin: float = typer.Option(
        0.1,
        "--min-geometry-margin",
        min=0.0,
        max=1.0,
        help="Minimum coverage margin over the next provider block.",
    ),
) -> None:
    """Score line successors after provider block segmentation."""

    try:
        scorer = _semantic_scorer_from_options(
            semantic_scorer,
            model_path=semantic_model_path,
            cache_path=semantic_cache,
            batch_size=semantic_batch_size,
            device=semantic_device,
        )
        result = benchmark_provider_hierarchy_corpus(
            corpus_dir,
            output=output,
            proposals_dir=proposals_dir,
            relation_model_path=relation_model,
            semantic_scorer=scorer,
            partition=partition,
            min_geometry_coverage=min_geometry_coverage,
            min_geometry_margin=min_geometry_margin,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="corpus_dir") from exc
    relation = result.report["summary"]["provider_hierarchy_relation"]
    segmentation = result.report["summary"]["segmentation_pairwise"]
    stream_segmentation = result.report["summary"][
        "assigned_stream_segmentation_pairwise"
    ]
    assignment = result.report["summary"]["assignment_coverage"]
    typer.echo(
        "Provider hierarchy relation (precision/recall/F1): "
        f"{relation['precision']}/{relation['recall']}/{relation['f1']}"
    )
    typer.echo(f"Segmentation pair F1: {segmentation['f1']}")
    typer.echo(
        "Assigned-stream segmentation pair F1: "
        f"{stream_segmentation['f1']}"
    )
    typer.echo(f"Assignment coverage: {assignment['coverage']}")
    typer.echo(f"Decision: {result.report['promotion_decision']}")
    typer.echo(f"Report: {result.report_path}")
    typer.echo(f"Proposals: {result.proposals_dir}")


@app.command("benchmark-paragraph-graph")
def benchmark_paragraph_graph_command(
    train_corpus: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Provider hierarchy corpus with document-disjoint fit/calibration labels.",
    ),
    output: Path = typer.Option(
        Path("outputs/paragraph-graph-benchmark.json"),
        "--output",
        "-o",
    ),
    test_corpus: Optional[Path] = typer.Option(
        None,
        "--test-corpus",
        exists=True,
        file_okay=False,
        readable=True,
        help="Optional independent provider hierarchy test corpus.",
    ),
    proposals_dir: Optional[Path] = typer.Option(
        None,
        "--proposals-dir",
        help="Directory for review-only line paragraph streams.",
    ),
    model_output: Optional[Path] = typer.Option(
        None,
        "--model-output",
        help="Optional .joblib path for the review-only paragraph graph model.",
    ),
    cross_validation_folds: int = typer.Option(
        5,
        "--cross-validation-folds",
        min=2,
    ),
    minimum_edge_precision: float = typer.Option(
        0.97,
        "--minimum-edge-precision",
        min=0.5,
        max=1.0,
    ),
    minimum_selected_edges: int = typer.Option(
        100,
        "--minimum-selected-edges",
        min=1,
    ),
) -> None:
    """Evaluate a source-neutral fine-line paragraph graph without runtime reorder."""

    try:
        result = benchmark_paragraph_graph(
            train_corpus,
            output=output,
            proposals_dir=proposals_dir,
            test_corpus_dir=test_corpus,
            model_output=model_output,
            cross_validation_folds=cross_validation_folds,
            minimum_edge_precision=minimum_edge_precision,
            minimum_selected_edges=minimum_selected_edges,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="train_corpus") from exc
    typer.echo(f"Frozen threshold: {result.report['frozen_threshold']}")
    for split, summary in result.report["summary"].items():
        edge = summary["selected_edge"]
        pair = summary["segmentation_pairwise"]
        typer.echo(
            f"{split} edge precision / pair F1: "
            f"{edge['precision']} / {pair['f1']}"
        )
    typer.echo(f"Decision: {result.report['promotion_decision']}")
    typer.echo(f"Report: {result.report_path}")
    typer.echo(f"Proposals: {result.proposals_dir}")
    if result.model_path is not None:
        typer.echo(f"Model: {result.model_path}")
        typer.echo(f"Model manifest: {result.model_manifest_path}")


@app.command("predict-paragraph-graph")
def predict_paragraph_graph_command(
    hierarchy_input: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Answer-free hierarchy input JSON for one page.",
    ),
    model: Path = typer.Option(
        ...,
        "--model",
        exists=True,
        readable=True,
        help="Serialized paragraph-graph .joblib model with adjacent manifest.",
    ),
    output: Path = typer.Option(
        Path("outputs/paragraph-graph.proposal.json"),
        "--output",
        "-o",
    ),
    sample_id: Optional[str] = typer.Option(
        None,
        "--sample-id",
        help="Optional proposal id; defaults to hierarchy input id.",
    ),
) -> None:
    """Predict a review-only paragraph graph proposal from a serialized model."""

    try:
        result = predict_paragraph_graph(
            hierarchy_input,
            model,
            output=output,
            sample_id=sample_id,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="hierarchy_input") from exc
    streams = result.proposal.get("reading_streams") or []
    typer.echo(f"Paragraph streams: {len(streams)}")
    typer.echo(f"Proposal: {result.proposal_path}")
    typer.echo(f"Model: {result.model_path}")


@app.command("benchmark-successor-graph")
def benchmark_successor_graph_command(
    train_corpus: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Provider hierarchy corpus with document-disjoint fit/calibration labels.",
    ),
    output: Path = typer.Option(
        Path("outputs/successor-graph-benchmark.json"),
        "--output",
        "-o",
    ),
    test_corpus: Optional[Path] = typer.Option(
        None,
        "--test-corpus",
        exists=True,
        file_okay=False,
        readable=True,
        help="Optional independent provider hierarchy test corpus.",
    ),
    proposals_dir: Optional[Path] = typer.Option(
        None,
        "--proposals-dir",
        help="Directory for review-only directed successor proposals.",
    ),
    model_output: Optional[Path] = typer.Option(
        None,
        "--model-output",
        help="Optional .joblib path for the review-only successor graph model.",
    ),
    cross_validation_folds: int = typer.Option(
        5,
        "--cross-validation-folds",
        min=2,
    ),
    nearest_candidates: int = typer.Option(
        20,
        "--nearest-candidates",
        min=1,
        help="Directed nearest-geometry candidates added for every source.",
    ),
    minimum_edge_precision: float = typer.Option(
        0.97,
        "--minimum-edge-precision",
        min=0.5,
        max=1.0,
    ),
    minimum_selected_edges: int = typer.Option(
        1000,
        "--minimum-selected-edges",
        min=1,
    ),
) -> None:
    """Evaluate a source-neutral directed successor graph without runtime reorder."""

    try:
        result = benchmark_successor_graph(
            train_corpus,
            output=output,
            proposals_dir=proposals_dir,
            test_corpus_dir=test_corpus,
            model_output=model_output,
            cross_validation_folds=cross_validation_folds,
            nearest_candidates=nearest_candidates,
            minimum_edge_precision=minimum_edge_precision,
            minimum_selected_edges=minimum_selected_edges,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="train_corpus") from exc
    typer.echo(f"Frozen threshold: {result.report['frozen_threshold']}")
    for split, summary in result.report["summary"].items():
        relation = summary["selected_relation"]
        typer.echo(
            f"{split} precision / recall / F1: "
            f"{relation['precision']} / {relation['recall']} / {relation['f1']}"
        )
    typer.echo(f"Decision: {result.report['promotion_decision']}")
    typer.echo(f"Report: {result.report_path}")
    typer.echo(f"Proposals: {result.proposals_dir}")
    if result.model_path is not None:
        typer.echo(f"Model: {result.model_path}")
        typer.echo(f"Model manifest: {result.model_manifest_path}")


@app.command("benchmark-successor-decoder-ab")
def benchmark_successor_decoder_ab_command(
    baseline_report: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Frozen successor graph report with its serialized model.",
    ),
    output: Path = typer.Option(
        Path("outputs/successor-decoder-ab.json"),
        "--output",
        "-o",
    ),
    proposals_dir: Optional[Path] = typer.Option(
        None,
        "--proposals-dir",
        help="Directory for review-only max-regret successor proposals.",
    ),
) -> None:
    """Replay a frozen successor head with greedy and max-regret decoders."""

    try:
        result = benchmark_successor_decoder_ab(
            baseline_report,
            output=output,
            proposals_dir=proposals_dir,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="baseline_report") from exc
    for split, delta in result.report["delta"].items():
        typer.echo(
            f"{split} max-regret delta precision / recall / F1: "
            f"{delta['precision']} / {delta['recall']} / {delta['f1']}"
        )
    typer.echo(f"Decision: {result.report['promotion_decision']}")
    typer.echo(f"Report: {result.report_path}")
    typer.echo(f"Proposals: {result.proposals_dir}")


@app.command("predict-successor-graph")
def predict_successor_graph_command(
    hierarchy_input: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Answer-free hierarchy input JSON for one page.",
    ),
    model: Path = typer.Option(
        ...,
        "--model",
        exists=True,
        readable=True,
        help="Serialized successor-graph .joblib model with adjacent manifest.",
    ),
    output: Path = typer.Option(
        Path("outputs/successor-graph.proposal.json"),
        "--output",
        "-o",
    ),
    sample_id: Optional[str] = typer.Option(
        None,
        "--sample-id",
        help="Optional proposal id; defaults to hierarchy input id.",
    ),
) -> None:
    """Predict a review-only successor graph proposal from a serialized model."""

    try:
        result = predict_successor_graph(
            hierarchy_input,
            model,
            output=output,
            sample_id=sample_id,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="hierarchy_input") from exc
    edges = result.proposal.get("successor_edges") or []
    streams = result.proposal.get("reading_streams") or []
    typer.echo(f"Successor edges: {len(edges)}")
    typer.echo(f"Reading streams: {len(streams)}")
    typer.echo(f"Proposal: {result.proposal_path}")
    typer.echo(f"Model: {result.model_path}")


@app.command("propose-joint-graph")
def propose_joint_graph_command(
    hierarchy_input: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Answer-free hierarchy input JSON, or DocumentIR when --page-index is used.",
    ),
    paragraph_model: Path = typer.Option(
        ...,
        "--paragraph-model",
        exists=True,
        readable=True,
        help="Serialized paragraph-graph .joblib model with adjacent manifest.",
    ),
    successor_model: Path = typer.Option(
        ...,
        "--successor-model",
        exists=True,
        readable=True,
        help="Serialized successor-graph .joblib model with adjacent manifest.",
    ),
    output: Path = typer.Option(
        Path("outputs/joint-graph.proposal.json"),
        "--output",
        "-o",
    ),
    sample_id: Optional[str] = typer.Option(
        None,
        "--sample-id",
        help="Optional proposal id; defaults to hierarchy/document id.",
    ),
    page_index: Optional[int] = typer.Option(
        None,
        "--page-index",
        min=0,
        help="When set, treat hierarchy_input as DocumentIR and export fine-only hierarchy first.",
    ),
    work_dir: Optional[Path] = typer.Option(
        None,
        "--work-dir",
        help="Optional directory for intermediate hierarchy/paragraph/successor artifacts.",
    ),
) -> None:
    """Predict and package a review-only joint graph proposal for one page."""

    try:
        source: Path | dict = hierarchy_input
        if page_index is not None:
            document = DocumentIR.load(hierarchy_input)
            adapter = build_fine_hierarchy_input_from_document(
                document,
                page_index=page_index,
                sample_id=sample_id,
            )
            source = adapter.payload
        result = propose_joint_graph(
            source,
            paragraph_model=paragraph_model,
            successor_model=successor_model,
            output=output,
            sample_id=sample_id,
            work_dir=work_dir,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="hierarchy_input") from exc
    edges = result.proposal.get("successor_edges") or []
    streams = result.proposal.get("reading_streams") or []
    components = result.proposal.get("paragraph_streams") or []
    typer.echo(f"Decoder mode: {result.decoder_mode}")
    typer.echo(f"Successor edges: {len(edges)}")
    typer.echo(f"Reading streams: {len(streams)}")
    typer.echo(f"Paragraph components: {len(components)}")
    typer.echo(f"Proposal: {result.proposal_path}")
    if result.hierarchy_input_path is not None:
        typer.echo(f"Hierarchy input: {result.hierarchy_input_path}")


@app.command("benchmark-joint-graph")
def benchmark_joint_graph_command(
    train_corpus: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Provider hierarchy corpus with document-disjoint fit/calibration labels.",
    ),
    paragraph_proposals_dir: Path = typer.Option(
        ...,
        "--paragraph-proposals-dir",
        exists=True,
        file_okay=False,
        readable=True,
        help="Directory of review-only paragraph-graph proposals.",
    ),
    successor_proposals_dir: Path = typer.Option(
        ...,
        "--successor-proposals-dir",
        exists=True,
        file_okay=False,
        readable=True,
        help="Directory of review-only successor-graph proposals.",
    ),
    output: Path = typer.Option(
        Path("outputs/joint-graph-benchmark.json"),
        "--output",
        "-o",
    ),
    test_corpus: Optional[Path] = typer.Option(
        None,
        "--test-corpus",
        exists=True,
        file_okay=False,
        readable=True,
        help="Optional independent provider hierarchy test corpus.",
    ),
    proposals_dir: Optional[Path] = typer.Option(
        None,
        "--proposals-dir",
        help="Directory for review-only joint hierarchical proposals.",
    ),
) -> None:
    """Jointly decode paragraph and successor graph proposals without runtime reorder."""

    try:
        result = benchmark_joint_graph(
            train_corpus,
            paragraph_proposals_dir=paragraph_proposals_dir,
            successor_proposals_dir=successor_proposals_dir,
            output=output,
            proposals_dir=proposals_dir,
            test_corpus_dir=test_corpus,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="train_corpus") from exc
    for split, summary in result.report["summary"].items():
        relation = summary["selected_relation"]
        segmentation = summary["segmentation_pairwise"]
        typer.echo(
            f"{split} relation F1 / segmentation F1: "
            f"{relation['f1']} / {segmentation['f1']}"
        )
    typer.echo(f"Decision: {result.report['promotion_decision']}")
    typer.echo(f"Report: {result.report_path}")
    typer.echo(f"Proposals: {result.proposals_dir}")


@app.command("fetch-comphrdoc")
def fetch_comphrdoc_command(
    out_dir: Path = typer.Option(
        Path("data/external/comphrdoc-test"),
        help="Directory for rendered test pages, answer-free anchors, and relation sidecars.",
    ),
    document_id: str = typer.Option("1401.3699", help="Fixed Comp-HRDoc test arXiv document id."),
    max_pages: int = typer.Option(5, min=1, help="First N pages, selected independently of results."),
    refresh: bool = typer.Option(False, help="Rewrite downloaded and derived benchmark files."),
) -> None:
    """Fetch a fixed Comp-HRDoc test prefix for cross-domain order scoring."""

    try:
        result = fetch_comphrdoc_benchmark_samples(
            out_dir,
            document_id=document_id,
            max_pages=max_pages,
            refresh=refresh,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--document-id") from exc
    typer.echo(f"Comp-HRDoc document: {document_id}")
    typer.echo(f"Samples: {len(result.samples)}")
    typer.echo(f"Manifest: {result.manifest_path}")
    typer.echo(f"Source PDF: {result.source_pdf_path}")
    typer.echo(f"Images: {result.out_dir / 'images'}")
    typer.echo(f"Structure anchors: {result.out_dir / 'structure'}")


@app.command("fetch-comphrdoc-provider-calibration")
def fetch_comphrdoc_provider_calibration_command(
    out_dir: Path = typer.Option(
        Path("data/external/comphrdoc-provider-calibration"),
        help="Directory for locally reconstructed Comp-HRDoc train pages.",
    ),
    sample_count: int = typer.Option(8, min=2, help="Total train pages to reconstruct."),
    document_count: int = typer.Option(
        4,
        min=2,
        help="Train documents to sample; pages are balanced across documents.",
    ),
    calibration_fraction: float = typer.Option(
        0.2,
        min=0.05,
        max=0.5,
        help="Document-hash partition reserved for provider calibration.",
    ),
    arxiv_version: Optional[str] = typer.Option(
        None,
        help="Optional pinned source revision such as v1; latest is used when omitted.",
    ),
    annotation_archive: Optional[Path] = typer.Option(
        None,
        "--annotation-archive",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Optional local Comp-HRDoc.zip; its pinned SHA-256 is still verified.",
    ),
    skip_unaligned_documents: bool = typer.Option(
        False,
        "--skip-unaligned-documents",
        help=(
            "Skip an entire source document after an audited page-alignment "
            "failure and replenish it from the same hash partition."
        ),
    ),
    refresh: bool = typer.Option(False, help="Redownload PDFs and rewrite derived files."),
) -> None:
    """Rebuild a train-only real-provider calibration corpus from arXiv PDFs."""

    try:
        result = fetch_comphrdoc_provider_calibration_corpus(
            out_dir,
            sample_count=sample_count,
            document_count=document_count,
            calibration_fraction=calibration_fraction,
            arxiv_version=arxiv_version,
            annotation_archive=annotation_archive,
            skip_unaligned_documents=skip_unaligned_documents,
            refresh=refresh,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--sample-count") from exc
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    partitions: dict[str, int] = {}
    for sample in manifest["samples"]:
        partition = str(sample["partition"])
        partitions[partition] = partitions.get(partition, 0) + 1
    typer.echo(f"Comp-HRDoc train samples: {len(result.samples)}")
    typer.echo(f"Documents: {len(result.source_pdf_paths)}")
    typer.echo(f"Skipped unaligned documents: {manifest['skipped_document_count']}")
    typer.echo(f"Partitions: {json.dumps(partitions, sort_keys=True)}")
    typer.echo(f"Manifest: {result.manifest_path}")
    typer.echo(f"Images: {result.out_dir / 'images'}")
    typer.echo(f"Structure anchors: {result.out_dir / 'structure'}")
    typer.echo(f"Semantic sidecars: {result.out_dir / 'semantic'}")


@app.command("fetch-comphrdoc-provider-test")
def fetch_comphrdoc_provider_test_command(
    out_dir: Path = typer.Option(
        Path("data/external/comphrdoc-provider-test"),
        help="Directory for locally reconstructed Comp-HRDoc test pages.",
    ),
    sample_count: int = typer.Option(32, min=1, help="Total test pages to reconstruct."),
    document_count: int = typer.Option(
        16,
        min=1,
        help="Test documents to sample; pages are balanced across documents.",
    ),
    document_offset: int = typer.Option(
        0,
        min=0,
        help="Skip this many documents in the fixed hash-ranked test selection.",
    ),
    arxiv_version: Optional[str] = typer.Option(
        None,
        help="Optional pinned source revision such as v1; latest is used when omitted.",
    ),
    annotation_archive: Optional[Path] = typer.Option(
        None,
        "--annotation-archive",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Optional local Comp-HRDoc.zip; its pinned SHA-256 is still verified.",
    ),
    refresh: bool = typer.Option(False, help="Redownload PDFs and rewrite derived files."),
) -> None:
    """Rebuild an independently selected official test corpus from arXiv PDFs."""

    try:
        result = fetch_comphrdoc_provider_test_corpus(
            out_dir,
            sample_count=sample_count,
            document_count=document_count,
            document_offset=document_offset,
            arxiv_version=arxiv_version,
            annotation_archive=annotation_archive,
            refresh=refresh,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--sample-count") from exc
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    strata: dict[str, int] = {}
    for sample in manifest["samples"]:
        stratum = str(sample["layout_stratum"])
        strata[stratum] = strata.get(stratum, 0) + 1
    typer.echo(f"Comp-HRDoc test samples: {len(result.samples)}")
    typer.echo(f"Documents: {len(result.source_pdf_paths)}")
    typer.echo(f"Document offset: {manifest['document_offset']}")
    typer.echo(f"Layout strata: {json.dumps(strata, sort_keys=True)}")
    typer.echo(f"Manifest: {result.manifest_path}")
    typer.echo(f"Images: {result.out_dir / 'images'}")
    typer.echo(f"Structure anchors: {result.out_dir / 'structure'}")
    typer.echo(f"Semantic sidecars: {result.out_dir / 'semantic'}")


@app.command("fetch-comphrdoc-relations")
def fetch_comphrdoc_relations_command(
    out_dir: Path = typer.Option(
        Path("data/external/comphrdoc-relations"),
        help="Directory for answer-free floating relation anchors and semantic sidecars.",
    ),
    sample_count: int = typer.Option(
        250,
        min=1,
        help="First N floating test pages in published image-name order.",
    ),
    refresh: bool = typer.Option(False, help="Rewrite derived corpus files."),
) -> None:
    """Build a cross-document Comp-HRDoc relation corpus without PDF images."""

    try:
        result = fetch_comphrdoc_relation_corpus(
            out_dir,
            sample_count=sample_count,
            refresh=refresh,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--sample-count") from exc
    typer.echo(f"Comp-HRDoc relation samples: {result.sample_count}")
    typer.echo(f"Manifest: {result.manifest_path}")
    typer.echo(f"Structure anchors: {result.out_dir / 'structure'}")
    typer.echo(f"Semantic sidecars: {result.out_dir / 'semantic'}")


@app.command("benchmark-comphrdoc-relations")
def benchmark_comphrdoc_relations_command(
    corpus_dir: Path = typer.Argument(..., exists=True, file_okay=False, readable=True),
    model: Path = typer.Option(..., "--model", exists=True, readable=True),
    floating_model: Path | None = typer.Option(
        None,
        "--floating-model",
        exists=True,
        readable=True,
    ),
    noise_profile: Literal["clean", "mild", "stress"] = typer.Option(
        "clean",
        "--noise-profile",
        help="Deterministic synthetic layout/OCR perturbation profile.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
    semantic_scorer: Optional[str] = typer.Option(
        None,
        "--semantic-scorer",
        help=f"Semantic scorer required by semantic ranker models ({BERT_TINY_NSP_PRESET}).",
    ),
    semantic_model_path: Optional[Path] = typer.Option(
        None,
        "--semantic-model-path",
        exists=True,
        readable=True,
    ),
    semantic_cache: Path = typer.Option(
        Path("outputs/cache/semantic-successor.sqlite3"),
        "--semantic-cache",
    ),
    semantic_batch_size: int = typer.Option(
        256,
        "--semantic-batch-size",
        min=1,
    ),
    semantic_device: str = typer.Option("cpu", "--semantic-device"),
) -> None:
    """A/B score structure-role fusion on a Comp-HRDoc relation corpus."""

    try:
        scorer = _semantic_scorer_from_options(
            semantic_scorer,
            model_path=semantic_model_path,
            cache_path=semantic_cache,
            batch_size=semantic_batch_size,
            device=semantic_device,
        )
        result = benchmark_comphrdoc_relation_corpus(
            corpus_dir,
            model,
            floating_model_path=floating_model,
            noise_profile=noise_profile,
            output=output,
            semantic_scorer=scorer,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="corpus_dir") from exc
    summary = result.report["summary"]
    typer.echo(f"Samples: {result.report['sample_count']}")
    typer.echo(f"Noise profile: {result.report['noise']['profile']}")
    typer.echo(f"Native ranker F1: {summary['native-ranker']['f1']}")
    typer.echo(f"Native plus structure-role F1: {summary['native-plus-structure-role']['f1']}")
    if "native-plus-trained-floating" in summary:
        typer.echo(
            "Native plus trained floating F1: "
            f"{summary['native-plus-trained-floating']['f1']}"
        )
    typer.echo(f"F1 delta: {result.report['f1_delta']}")
    typer.echo(f"Report: {result.report_path}")


@app.command("benchmark-provider-anchors")
def benchmark_provider_anchors_command(
    oracle_structure: Path = typer.Argument(..., exists=True, readable=True),
    semantic_sidecar: Path = typer.Argument(..., exists=True, readable=True),
    provider_json: Path = typer.Argument(..., exists=True, readable=True),
    floating_model: Path | None = typer.Option(
        None,
        "--floating-model",
        exists=True,
        readable=True,
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Match real provider blocks to oracle anchors and score relations."""

    try:
        result = benchmark_provider_anchors(
            oracle_structure,
            semantic_sidecar,
            provider_json,
            floating_model_path=floating_model,
            output=output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="provider_json") from exc
    report = result.report
    typer.echo(f"Provider: {report['provider']}")
    typer.echo(f"Oracle anchor recall: {report['oracle_anchor_recall']}")
    typer.echo(f"Provider anchor match rate: {report['provider_anchor_match_rate']}")
    typer.echo(f"Combined relation F1: {report['relations']['combined']['f1']}")
    degradation = report["provider_degradation"]
    typer.echo(
        "Provider missing/hallucination: "
        f"{degradation['error_taxonomy']['missing']['rate']}/"
        f"{degradation['error_taxonomy']['hallucination']['rate']}"
    )
    typer.echo(
        "Provider split/merge: "
        f"{degradation['error_taxonomy']['split']['rate']}/"
        f"{degradation['error_taxonomy']['merge']['rate']}"
    )
    typer.echo(
        "Nearest synthetic profile: "
        f"{degradation['synthetic_profile_comparison']['nearest_profile']}"
    )
    typer.echo(f"Report: {result.report_path}")


@app.command("benchmark-provider-anchor-suite")
def benchmark_provider_anchor_suite_command(
    corpus_dir: Path = typer.Argument(..., exists=True, file_okay=False, readable=True),
    provider_dir: Path = typer.Argument(..., exists=True, file_okay=False, readable=True),
    floating_model: Path | None = typer.Option(
        None,
        "--floating-model",
        exists=True,
        readable=True,
    ),
    transition_gate: Path | None = typer.Option(
        None,
        "--transition-gate",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Frozen review-only support/confidence gate to evaluate.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Score a real provider over a rendered Comp-HRDoc prefix."""

    try:
        result = benchmark_provider_anchor_suite(
            corpus_dir,
            provider_dir,
            floating_model_path=floating_model,
            transition_gate_path=transition_gate,
            output=output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="provider_dir") from exc
    report = result.report
    typer.echo(f"Provider: {report['provider']}")
    typer.echo(f"Cases: {report['case_count']}")
    typer.echo(f"Oracle anchor recall: {report['oracle_anchor_recall']}")
    typer.echo(f"Provider anchor match rate: {report['provider_anchor_match_rate']}")
    typer.echo(f"Combined relation F1: {report['relations']['combined']['f1']}")
    degradation = report["provider_degradation"]
    typer.echo(
        "Provider missing/hallucination: "
        f"{degradation['error_taxonomy']['missing']['rate']}/"
        f"{degradation['error_taxonomy']['hallucination']['rate']}"
    )
    typer.echo(
        "Provider split/merge: "
        f"{degradation['error_taxonomy']['split']['rate']}/"
        f"{degradation['error_taxonomy']['merge']['rate']}"
    )
    typer.echo(
        "Nearest synthetic profile: "
        f"{degradation['synthetic_profile_comparison']['nearest_profile']}"
    )
    gate_evaluation = report.get("provider_transition_gate_evaluation")
    if gate_evaluation is not None:
        typer.echo(
            "Frozen transition gate accepted: "
            f"{gate_evaluation['meets_frozen_acceptance_criteria']}"
        )
    typer.echo(f"Report: {result.report_path}")


@app.command("freeze-provider-transition-gate")
def freeze_provider_transition_gate_command(
    suite_report: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    partition: str = typer.Option("fit", help="Named train-only partition used to freeze thresholds."),
    minimum_precision: float = typer.Option(0.95, min=0.0, max=1.0),
    minimum_wilson_lower_95: float = typer.Option(
        0.9,
        min=0.0,
        max=1.0,
        help="Minimum 95% Wilson lower bound for transition precision.",
    ),
    minimum_predicted: int = typer.Option(
        50,
        min=1,
        help="Minimum eligible transitions required on the freeze partition.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Freeze one fit-selected Provider transition gate for independent review."""

    try:
        result = freeze_provider_transition_gate(
            suite_report,
            partition=partition,
            minimum_precision=minimum_precision,
            minimum_wilson_lower_95=minimum_wilson_lower_95,
            minimum_predicted=minimum_predicted,
            output=output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="suite_report") from exc
    gate = result.gate
    typer.echo(f"Partition: {gate['selection_partition']}")
    typer.echo(
        "Threshold: native support >= "
        f"{gate['minimum_native_support']}, provider confidence >= "
        f"{gate['minimum_provider_confidence']}"
    )
    typer.echo(
        "Fit precision/Wilson lower/predicted: "
        f"{gate['fit_metrics']['precision']}/"
        f"{gate['fit_metrics']['precision_wilson_lower_95']}/"
        f"{gate['fit_metrics']['predicted']}"
    )
    typer.echo(f"Gate: {result.gate_path}")


@app.command("freeze-stratified-provider-transition-gate")
def freeze_stratified_provider_transition_gate_command(
    suite_report: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    minimum_native_support: int = typer.Option(
        2,
        min=1,
        max=len(PROVIDER_TRANSITION_CANDIDATES),
        help="Minimum answer-free native candidate votes for every active rule.",
    ),
    support_candidate_names: Optional[list[str]] = typer.Option(
        None,
        "--support-candidate",
        help=(
            "Repeat to select independently calibrated native evidence channels; "
            "the stable three-channel set is used by default."
        ),
    ),
    cross_validation_folds: int = typer.Option(
        5,
        min=0,
        help="Document-grouped fit folds; use 0 only for legacy diagnostics.",
    ),
    fit_minimum_precision: float = typer.Option(0.95, min=0.0, max=1.0),
    fit_minimum_wilson_lower_95: float = typer.Option(
        0.8,
        min=0.0,
        max=1.0,
    ),
    fit_minimum_predicted: int = typer.Option(20, min=1),
    fit_minimum_scorable_fraction: float = typer.Option(
        0.8,
        min=0.0,
        max=1.0,
    ),
    calibration_minimum_precision: float = typer.Option(
        0.95,
        min=0.0,
        max=1.0,
    ),
    calibration_minimum_wilson_lower_95: float = typer.Option(
        0.85,
        min=0.0,
        max=1.0,
    ),
    calibration_minimum_predicted: int = typer.Option(30, min=1),
    calibration_minimum_scorable_fraction: float = typer.Option(
        0.8,
        min=0.0,
        max=1.0,
    ),
    allowed_layout_strata: Optional[list[str]] = typer.Option(
        None,
        "--allowed-layout-stratum",
        help="Repeat to predeclare layout families that may receive active rules.",
    ),
    allowed_position_bands: Optional[list[str]] = typer.Option(
        None,
        "--allowed-position-band",
        help="Repeat to predeclare start/middle/end/single bands that may receive rules.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Freeze consensus bucket rules with document CV and calibration."""

    try:
        result = freeze_stratified_provider_transition_gate(
            suite_report,
            minimum_native_support=minimum_native_support,
            support_candidate_names=support_candidate_names,
            cross_validation_folds=cross_validation_folds,
            fit_minimum_precision=fit_minimum_precision,
            fit_minimum_wilson_lower_95=fit_minimum_wilson_lower_95,
            fit_minimum_predicted=fit_minimum_predicted,
            fit_minimum_scorable_fraction=fit_minimum_scorable_fraction,
            calibration_minimum_precision=calibration_minimum_precision,
            calibration_minimum_wilson_lower_95=(
                calibration_minimum_wilson_lower_95
            ),
            calibration_minimum_predicted=calibration_minimum_predicted,
            calibration_minimum_scorable_fraction=(
                calibration_minimum_scorable_fraction
            ),
            allowed_layout_strata=allowed_layout_strata,
            allowed_position_bands=allowed_position_bands,
            output=output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="suite_report") from exc
    gate = result.gate
    typer.echo(
        "Support candidates: " + ", ".join(gate["support_candidate_names"])
    )
    typer.echo(f"Active bucket rules: {len(gate['rules'])}")
    typer.echo(f"Inactive buckets: {len(gate['inactive_buckets'])}")
    cross_validation = gate["document_cross_validation"]
    cross_validation_metrics = cross_validation["out_of_fold_metrics"]
    typer.echo(
        "Document CV precision/Wilson/predicted: "
        f"{cross_validation_metrics['precision']}/"
        f"{cross_validation_metrics['precision_wilson_lower_95']}/"
        f"{cross_validation_metrics['predicted']}"
    )
    typer.echo(f"Document CV accepted: {cross_validation['accepted']}")
    typer.echo(
        "Fit precision/Wilson/predicted: "
        f"{gate['fit_aggregate_metrics']['precision']}/"
        f"{gate['fit_aggregate_metrics']['precision_wilson_lower_95']}/"
        f"{gate['fit_aggregate_metrics']['predicted']}"
    )
    typer.echo(
        "Calibration precision/Wilson/predicted: "
        f"{gate['calibration_aggregate_metrics']['precision']}/"
        f"{gate['calibration_aggregate_metrics']['precision_wilson_lower_95']}/"
        f"{gate['calibration_aggregate_metrics']['predicted']}"
    )
    typer.echo(f"Calibration accepted: {gate['calibration_accepted']}")
    typer.echo(f"Gate: {result.gate_path}")


@app.command("consensus-reading-sidecars")
def consensus_reading_sidecars_command(
    base_sidecar: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Native/base proposal that owns stable elements and local reading streams.",
    ),
    provider_sidecars: list[Path] = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Two or more independent provider proposal sidecars.",
    ),
    output: Path = typer.Option(
        Path("outputs/reading-order.consensus.proposal.json"),
        "--output",
        "-o",
        help="Review-only provider-consensus sidecar.",
    ),
    min_providers: int = typer.Option(
        2,
        min=2,
        help="Minimum number of distinct provider sidecars that must propose the same edge.",
    ),
) -> None:
    """Intersect independent model block-order proposals over stable element IDs."""

    try:
        base_payload = json.loads(base_sidecar.read_text(encoding="utf-8"))
        providers = [
            (str(path), json.loads(path.read_text(encoding="utf-8")))
            for path in provider_sidecars
        ]
        payload = build_provider_consensus_sidecar(
            base_payload,
            providers,
            min_provider_count=min_providers,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    consensus = payload["provider_consensus"]
    typer.echo(f"Provider-consensus sidecar: {output}")
    typer.echo(f"Providers: {consensus['provider_count']}")
    typer.echo(f"Consensus review edges: {consensus['consensus_edge_count']}")
    typer.echo("Runtime reorder: disabled")


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
    ocr_json: Optional[list[Path]] = typer.Option(
        None,
        "--ocr-json",
        exists=True,
        readable=True,
        help=(
            "Optional OCR/layout-anchor JSON used to create text nodes before structure fusion. "
            "For multiple sources, pass files in source order or use matching names."
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
        ocr_jsons=ocr_json,
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
    typer.echo(f"OCR JSON files: {report.get('ocr_json_count')}")
    typer.echo(f"Structure JSON files: {report.get('structure_json_count')}")
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
    ocr_json: Optional[list[Path]] = typer.Option(
        None,
        "--ocr-json",
        exists=True,
        readable=True,
        help=(
            "Optional OCR/layout-anchor JSON shared by both A/B branches. "
            "Structure JSON remains exclusive to the native-plus-structure branch."
        ),
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
        ocr_jsons=ocr_json,
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
    typer.echo(f"OCR JSON files: {report.get('ocr_json_count')}")
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
    max_new_tokens: Optional[int] = typer.Option(
        None,
        min=1,
        help="Maximum VLM output tokens per page; omitted delegates to Paddle's default.",
    ),
    queued: bool = typer.Option(
        False,
        "--queued/--synchronous",
        help="Use Paddle queue workers; synchronous mode is deterministic for local runs.",
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
    predict_options: dict[str, object] = {"use_queues": queued}
    if max_new_tokens is not None:
        predict_options["max_new_tokens"] = max_new_tokens
    payload = PaddleOcrAdapter(
        predict_options=predict_options,
        **options,
    ).analyze(
        [page.background_image for page in rendered.pages],
        page_indices=[page.page_index for page in rendered.pages],
    )
    output_path = write_ocr_json(payload, output)
    typer.echo(f"PaddleOCR-VL JSON: {output_path}")
    typer.echo(f"Pages: {len(rendered.pages)}")
    typer.echo(f"Source type: {rendered.source_type}")
    typer.echo(f"Model: {payload.get('model')}")


@app.command("run-paddle-layout")
def run_paddle_layout_command(
    source: Path = typer.Argument(..., exists=True, readable=True, help="Input PDF or image source."),
    output: Path = typer.Option(
        Path("outputs/pp-doclayoutv3.structure.json"),
        "--output",
        "-o",
        help="Review-only PP-DocLayoutV3 blocks and reading-order evidence.",
    ),
    dpi: int = typer.Option(192, min=72, max=600, help="Render DPI for PDF source pages."),
    input_kind: SourceKind = typer.Option("auto", help="Source type: auto, pdf, or image."),
    image_dpi: int = typer.Option(
        96,
        min=1,
        max=1200,
        help="Pixel density used to map image pixels into PDF points for image sources.",
    ),
    max_pages: Optional[int] = typer.Option(None, min=1, help="Limit inference to the first N pages."),
    page_ranges: Optional[str] = typer.Option(
        None,
        help="Explicit 1-based page ranges, for example 1-3,136. Cannot combine with --max-pages.",
    ),
    device: Optional[str] = typer.Option(
        None,
        help="Optional Paddle device, for example gpu:0 or cpu.",
    ),
    model_name: str = typer.Option(
        "PP-DocLayoutV3",
        help="Paddle layout model with native reading-order prediction.",
    ),
    model_dir: Optional[Path] = typer.Option(
        None,
        help="Optional local Paddle layout model directory.",
    ),
) -> None:
    """Run fast layout and order inference without OCR or VLM recognition."""

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
    options: dict[str, object] = {"model_name": model_name}
    if device:
        options["device"] = device
    if model_dir is not None:
        options["model_dir"] = model_dir
    payload = PaddleLayoutAdapter(**options).analyze(
        [page.background_image for page in rendered.pages],
        page_indices=[page.page_index for page in rendered.pages],
    )
    output_path = write_ocr_json(payload, output)
    typer.echo(f"PP-DocLayoutV3 JSON: {output_path}")
    typer.echo(f"Pages: {len(rendered.pages)}")
    typer.echo(f"Layout blocks: {sum(len(page['elements']) for page in payload['pages'])}")
    typer.echo("Runtime reorder: disabled")


@app.command("run-paddle-layout-corpus")
def run_paddle_layout_corpus_command(
    corpus_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Answer-separated Comp-HRDoc corpus directory.",
    ),
    out_dir: Path = typer.Option(
        Path("outputs/pp-doclayoutv3-corpus"),
        "--out-dir",
        help="Directory for one review-only provider JSON per corpus sample.",
    ),
    partition: str = typer.Option(
        "all",
        help="Corpus partition to run: all, fit, or calibration.",
    ),
    max_samples: Optional[int] = typer.Option(
        None,
        min=1,
        help="Optional deterministic prefix limit after partition filtering.",
    ),
    refresh: bool = typer.Option(
        False,
        help="Re-run samples whose provider JSON already exists.",
    ),
    device: Optional[str] = typer.Option(
        None,
        help="Optional Paddle device, for example gpu:0 or cpu.",
    ),
    model_name: str = typer.Option(
        "PP-DocLayoutV3",
        help="Paddle layout model with native reading-order prediction.",
    ),
    model_dir: Optional[Path] = typer.Option(
        None,
        help="Optional local Paddle layout model directory.",
    ),
) -> None:
    """Run one reusable PP-DocLayoutV3 predictor over a corpus manifest."""

    normalized_partition = partition.strip().lower()
    if normalized_partition not in {"all", "fit", "calibration"}:
        raise typer.BadParameter(
            "partition must be all, fit, or calibration",
            param_hint="--partition",
        )
    options: dict[str, object] = {"model_name": model_name}
    if device:
        options["device"] = device
    if model_dir is not None:
        options["model_dir"] = model_dir
    try:
        result = run_paddle_layout_corpus(
            corpus_dir,
            out_dir,
            adapter=PaddleLayoutAdapter(**options),
            partition=None if normalized_partition == "all" else normalized_partition,
            max_samples=max_samples,
            refresh=refresh,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="corpus_dir") from exc
    typer.echo(f"Corpus run report: {result.report_path}")
    typer.echo(f"Provider JSON files: {len(result.output_paths)}")
    typer.echo(f"Generated: {len(result.generated_sample_ids)}")
    typer.echo(f"Skipped existing: {len(result.skipped_sample_ids)}")
    typer.echo("Runtime reorder: disabled")


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
    document_orientation: bool = typer.Option(
        False,
        "--document-orientation/--no-document-orientation",
        help="Classify and correct whole-page rotation before PP-Structure inference.",
    ),
    document_unwarping: bool = typer.Option(
        False,
        "--document-unwarping/--no-document-unwarping",
        help="Rectify photographed or curved pages before PP-Structure inference.",
    ),
    textline_orientation: bool = typer.Option(
        False,
        "--textline-orientation/--no-textline-orientation",
        help="Classify text-line direction; unnecessary for upright rendered PDF pages.",
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
        "use_doc_orientation_classify": document_orientation,
        "use_doc_unwarping": document_unwarping,
        "use_textline_orientation": textline_orientation,
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


@app.command("run-opendataloader")
def run_opendataloader_command(
    source: Path = typer.Argument(..., exists=True, readable=True, help="Input PDF source."),
    output: Path = typer.Option(
        Path("outputs/opendataloader.structure.json"),
        "--output",
        "-o",
        help="Normalized review-only XY-Cut structure JSON for replay or A/B benchmarking.",
    ),
    raw_output: Optional[Path] = typer.Option(
        None,
        "--raw-output",
        help="Optional path for the original provider JSON; defaults beside --output.",
    ),
    max_pages: Optional[int] = typer.Option(None, min=1, help="Limit extraction to the first N pages."),
    page_ranges: Optional[str] = typer.Option(
        None,
        help="Explicit 1-based source page ranges, for example 1-3,136. Cannot be combined with --max-pages.",
    ),
    table_method: Literal["default", "cluster"] = typer.Option(
        "default",
        help="OpenDataLoader table method: default border-based or border-plus-cluster.",
    ),
    include_header_footer: bool = typer.Option(
        False,
        "--include-header-footer/--exclude-header-footer",
        help="Retain provider-classified page headers and footers in the review proposal.",
    ),
    threads: int = typer.Option(
        1,
        min=1,
        help="Provider page threads; one is the deterministic default.",
    ),
) -> None:
    """Run deterministic OpenDataLoader XY-Cut and emit review-only evidence."""

    try:
        page_indices = page_indices_from_ranges(page_ranges, max_pages=max_pages)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--page-ranges") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_path = raw_output or output.with_name(f"{output.stem}.raw.json")
    if raw_path.resolve() == output.resolve():
        raise typer.BadParameter("--raw-output must differ from --output", param_hint="--raw-output")
    try:
        with TemporaryDirectory(prefix="scriptorium-opendataloader-", dir=output.parent) as temp_dir:
            result = OpenDataLoaderAdapter().analyze(
                source,
                temp_dir,
                page_indices=page_indices,
                max_pages=max_pages,
                table_method=table_method,
                include_header_footer=include_header_footer,
                threads=threads,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="source") from exc
    normalized_path = write_ocr_json(result.structure_payload, output)
    provider_raw_path = write_ocr_json(result.raw_payload, raw_path)
    normalization = result.structure_payload["normalization"]
    typer.echo(f"OpenDataLoader structure JSON: {normalized_path}")
    typer.echo(f"OpenDataLoader raw JSON: {provider_raw_path}")
    typer.echo(f"Pages: {len(result.structure_payload['pages'])}")
    typer.echo(f"Review blocks: {normalization['normalized_block_count']}")
    typer.echo(f"Review relations: {normalization['review_relation_edge_count']}")
    typer.echo("Runtime reorder: disabled")


@app.command("run-docling")
def run_docling_command(
    source: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Input PDF or image source.",
    ),
    output: Path = typer.Option(
        Path("outputs/docling.structure.json"),
        "--output",
        "-o",
        help="Review-only Docling structure JSON for replay or A/B benchmarking.",
    ),
    raw_output: Optional[Path] = typer.Option(
        None,
        "--raw-output",
        help="Optional original Docling JSON path; defaults beside --output.",
    ),
    max_pages: Optional[int] = typer.Option(None, min=1, help="Limit extraction to the first N pages."),
    page_ranges: Optional[str] = typer.Option(
        None,
        help="One contiguous 1-based source page range, for example 1-3. Cannot combine with --max-pages.",
    ),
    ocr_languages: str = typer.Option(
        "eng",
        help="Comma-separated Tesseract language codes.",
    ),
    tables: bool = typer.Option(
        False,
        "--tables/--no-tables",
        help="Run Docling table structure recognition; disabled for layout/order benchmarks.",
    ),
    force_ocr: bool = typer.Option(
        False,
        "--force-ocr/--no-force-ocr",
        help="Force full-page OCR even when native PDF text is available.",
    ),
    device: Literal["auto", "cpu", "cuda", "mps", "xpu"] = typer.Option(
        "cpu",
        help="Docling accelerator device.",
    ),
    threads: int = typer.Option(2, min=1, help="Docling CPU worker threads."),
) -> None:
    """Run Docling Heron layout with isolated review-only reading order."""

    try:
        page_indices = page_indices_from_ranges(page_ranges, max_pages=max_pages)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--page-ranges") from exc
    languages = tuple(language.strip() for language in ocr_languages.split(",") if language.strip())
    if not languages:
        raise typer.BadParameter("at least one OCR language is required", param_hint="--ocr-languages")
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_path = raw_output or output.with_name(f"{output.stem}.raw.json")
    if raw_path.resolve() == output.resolve():
        raise typer.BadParameter("--raw-output must differ from --output", param_hint="--raw-output")
    try:
        result = DoclingAdapter().analyze(
            source,
            page_indices=page_indices,
            max_pages=max_pages,
            languages=languages,
            tables=tables,
            force_ocr=force_ocr,
            device=device,
            threads=threads,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="source") from exc
    normalized_path = write_ocr_json(result.structure_payload, output)
    provider_raw_path = write_ocr_json(result.raw_payload, raw_path)
    typer.echo(f"Docling structure JSON: {normalized_path}")
    typer.echo(f"Docling raw JSON: {provider_raw_path}")
    typer.echo(f"Text items: {len(result.raw_payload.get('texts', []))}")
    typer.echo(f"Table items: {len(result.raw_payload.get('tables', []))}")
    typer.echo("Runtime reorder: disabled")


@app.command("train-relation-ranker")
def train_relation_ranker_command(
    dataset_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="ROOR data directory containing data.train.txt and jsons/.",
    ),
    output: Path = typer.Option(
        Path("outputs/models/relation-ranker.joblib"),
        "--output",
        "-o",
        help="Locally generated joblib model path.",
    ),
    calibration_fraction: float = typer.Option(
        0.2,
        min=0.05,
        max=0.5,
        help="UID-hash holdout fraction taken only from the official train split.",
    ),
    negative_candidates: int = typer.Option(
        20,
        min=1,
        help="Nearest negative targets retained per source during training.",
    ),
    seed: int = typer.Option(17, help="Deterministic estimator seed."),
    semantic_scorer: Optional[str] = typer.Option(
        None,
        "--semantic-scorer",
        help=f"Optional semantic pair scorer preset ({BERT_TINY_NSP_PRESET}).",
    ),
    semantic_model_path: Optional[Path] = typer.Option(
        None,
        "--semantic-model-path",
        exists=True,
        readable=True,
        help="Optional local snapshot for the pinned semantic model.",
    ),
    semantic_cache: Path = typer.Option(
        Path("outputs/cache/semantic-successor.sqlite3"),
        "--semantic-cache",
        help="Content-addressed semantic pair score cache.",
    ),
    semantic_batch_size: int = typer.Option(
        256,
        "--semantic-batch-size",
        min=1,
        help="Semantic model inference batch size.",
    ),
    semantic_device: str = typer.Option(
        "cpu",
        "--semantic-device",
        help="Torch device used only by the optional semantic scorer.",
    ),
    semantic_fusion: str = typer.Option(
        "top-k-rerank",
        "--semantic-fusion",
        help="Semantic fusion mode: top-k-rerank or direct.",
    ),
    semantic_top_k: int = typer.Option(
        5,
        "--semantic-top-k",
        min=2,
        help="Base-ranker candidates scored per source by the semantic reranker.",
    ),
) -> None:
    """Train a review-only successor ranker without reading validation labels."""

    try:
        scorer = _semantic_scorer_from_options(
            semantic_scorer,
            model_path=semantic_model_path,
            cache_path=semantic_cache,
            batch_size=semantic_batch_size,
            device=semantic_device,
        )
        result = train_relation_ranker(
            dataset_dir,
            output,
            calibration_fraction=calibration_fraction,
            random_seed=seed,
            negative_candidates=negative_candidates,
            semantic_scorer=scorer,
            semantic_fusion=semantic_fusion,
            semantic_top_k=semantic_top_k,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="dataset_dir") from exc
    calibration = result.manifest["calibration"]
    typer.echo(f"Relation ranker model: {result.model_path}")
    typer.echo(f"Relation ranker manifest: {result.manifest_path}")
    typer.echo(f"Calibration documents: {calibration['document_count']}")
    typer.echo(f"Calibration F1: {calibration['f1']}")
    typer.echo(f"Successor threshold: {result.manifest['successor_threshold']}")
    typer.echo(f"Branch calibration F1: {result.manifest['branch_calibration']['f1']}")
    typer.echo(f"Branch threshold: {result.manifest['branch_threshold']}")
    typer.echo(f"Semantic scorer: {semantic_scorer or 'disabled'}")
    if semantic_scorer:
        typer.echo(f"Semantic fusion: {semantic_fusion}")


@app.command("train-floating-ranker")
def train_floating_ranker_command(
    annotation_archive: Path = typer.Argument(..., exists=True, readable=True),
    output: Path = typer.Option(Path("outputs/models/floating-ranker.joblib"), "--output", "-o"),
    calibration_fraction: float = typer.Option(0.2, min=0.05, max=0.5),
    negative_candidates: int = typer.Option(12, min=1),
    seed: int = typer.Option(29),
) -> None:
    """Train a review-only float/caption pair gate from Comp-HRDoc train."""

    try:
        result = train_floating_relation_ranker(
            annotation_archive,
            output,
            calibration_fraction=calibration_fraction,
            negative_candidates=negative_candidates,
            random_seed=seed,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="annotation_archive") from exc
    typer.echo(f"Floating ranker model: {result.model_path}")
    typer.echo(f"Floating ranker manifest: {result.manifest_path}")
    typer.echo(f"Assignment policy: {result.manifest['assignment_policy']}")
    typer.echo(f"Calibration F1: {result.manifest['calibration']['f1']}")
    typer.echo(f"Pair threshold: {result.manifest['threshold']}")
    review_gate = result.manifest["reliability_gate"]
    promotion_gate = result.manifest["promotion_gate"]
    typer.echo(
        "High-precision review gate: "
        f"available={review_gate['available']} precision={review_gate['precision']} "
        f"recall={review_gate['recall']}"
    )
    typer.echo(f"Strict promotion gate available: {promotion_gate['available']}")
    noise_review_gate = result.manifest["noise_aware_reliability_gate"]
    noise_promotion_gate = result.manifest["noise_aware_promotion_gate"]
    typer.echo(
        "Noise-aware review gate: "
        f"available={noise_review_gate['available']} "
        f"threshold={noise_review_gate['confidence_threshold']} "
        f"worst_precision={noise_review_gate.get('worst_profile_precision')}"
    )
    typer.echo(
        "Noise-aware strict gate: "
        f"available={noise_promotion_gate['available']} "
        f"threshold={noise_promotion_gate['confidence_threshold']} "
        f"worst_precision={noise_promotion_gate.get('worst_profile_precision')}"
    )


@app.command("run-floating-ranker")
def run_floating_ranker_command(
    structure_json: Path = typer.Argument(..., exists=True, readable=True),
    model: Path = typer.Option(..., "--model", exists=True, readable=True),
    output: Path = typer.Option(Path("outputs/floating-ranker.structure.json"), "--output", "-o"),
) -> None:
    """Predict isolated review-only float/caption relations."""

    try:
        payload = load_structure_json(structure_json)
        result = predict_floating_relations(payload, model)
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="structure_json") from exc
    normalized = dict(payload)
    normalized.update(
        {
            "source": "scriptorium-trained-floating-ranker",
            "relation_policy": "review-only",
            "runtime_reorder": False,
            "successor_edges": result.successor_edges,
            "floating_ranker": result.diagnostics,
        }
    )
    output_path = write_ocr_json(normalized, output)
    typer.echo(f"Floating relation JSON: {output_path}")
    typer.echo(f"Graphical sources: {result.graphical_source_count}")
    typer.echo(f"Candidate pairs: {result.candidate_pair_count}")
    typer.echo(f"Predicted edges: {len(result.successor_edges)}")


@app.command("run-relation-ranker")
def run_relation_ranker_command(
    structure_json: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Answer-free ROOR-style structure JSON or a DocumentIR JSON file.",
    ),
    model: Path = typer.Option(
        ...,
        "--model",
        exists=True,
        readable=True,
        help="Locally generated relation-ranker joblib model.",
    ),
    output: Path = typer.Option(
        Path("outputs/relation-ranker.structure.json"),
        "--output",
        "-o",
        help="Isolated review-only successor relation JSON.",
    ),
    structure_role_fusion: bool = typer.Option(
        True,
        "--structure-role-fusion/--no-structure-role-fusion",
        help="Fuse explicit figure/table roles with local caption geometry.",
    ),
    semantic_scorer: Optional[str] = typer.Option(
        None,
        "--semantic-scorer",
        help=f"Semantic scorer required by semantic ranker models ({BERT_TINY_NSP_PRESET}).",
    ),
    semantic_model_path: Optional[Path] = typer.Option(
        None,
        "--semantic-model-path",
        exists=True,
        readable=True,
        help="Optional local snapshot for the pinned semantic model.",
    ),
    semantic_cache: Path = typer.Option(
        Path("outputs/cache/semantic-successor.sqlite3"),
        "--semantic-cache",
        help="Content-addressed semantic pair score cache.",
    ),
    semantic_batch_size: int = typer.Option(
        256,
        "--semantic-batch-size",
        min=1,
        help="Semantic model inference batch size.",
    ),
    semantic_device: str = typer.Option(
        "cpu",
        "--semantic-device",
        help="Torch device used only by the optional semantic scorer.",
    ),
) -> None:
    """Predict review-only relations from answer-free structure anchors."""

    try:
        scorer = _semantic_scorer_from_options(
            semantic_scorer,
            model_path=semantic_model_path,
            cache_path=semantic_cache,
            batch_size=semantic_batch_size,
            device=semantic_device,
        )
        raw_payload = json.loads(structure_json.read_text(encoding="utf-8"))
        if _is_document_ir_payload(raw_payload):
            result = predict_document_relations(
                DocumentIR.model_validate(raw_payload),
                model,
                structure_role_fusion=structure_role_fusion,
                semantic_scorer=scorer,
            )
        else:
            payload = load_structure_json(structure_json)
            result = predict_structure_relations(
                payload,
                model,
                structure_role_fusion=structure_role_fusion,
                semantic_scorer=scorer,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="structure_json") from exc
    output_path = write_ocr_json(result.structure_payload, output)
    typer.echo(f"Relation structure JSON: {output_path}")
    typer.echo(f"Source segments: {result.source_count}")
    typer.echo(f"Predicted successor edges: {result.predicted_edge_count}")
    typer.echo(f"Predicted branch edges: {result.predicted_branch_edge_count}")
    typer.echo(f"Semantic scorer: {semantic_scorer or 'disabled'}")
    typer.echo("Runtime reorder: disabled")


@app.command("run-surya-layout")
def run_surya_layout_command(
    source: Path = typer.Argument(..., exists=True, readable=True, help="Input PDF or image source."),
    output: Path = typer.Option(
        Path("outputs/surya-layout.raw.json"),
        "--output",
        "-o",
        help="Review-only Surya layout/order JSON for replay or A/B benchmarking.",
    ),
    dpi: int = typer.Option(192, min=72, max=600, help="Render DPI for PDF source pages."),
    input_kind: SourceKind = typer.Option("auto", help="Source type: auto, pdf, or image."),
    image_dpi: int = typer.Option(
        96,
        min=1,
        max=1200,
        help="Pixel density used to map image pixels into PDF points for image sources.",
    ),
    max_pages: Optional[int] = typer.Option(None, min=1, help="Limit model execution to the first N pages."),
    page_ranges: Optional[str] = typer.Option(
        None,
        help="Explicit 1-based source page ranges, for example 1-3,136. Cannot be combined with --max-pages.",
    ),
    device: str = typer.Option("cpu", help="Surya fast detector/order device: cpu, cuda, or mps."),
    num_threads: Optional[int] = typer.Option(None, min=1, help="Optional CPU thread count for Surya."),
    confidence_threshold: float = typer.Option(
        0.4,
        min=0.0,
        max=1.0,
        help="Fast layout detector confidence threshold.",
    ),
    batch_size: int = typer.Option(8, min=1, help="Fast layout detector batch size."),
    checkpoint: Optional[str] = typer.Option(None, help="Optional Surya detector checkpoint or hf:// reference."),
    order_checkpoint: Optional[str] = typer.Option(
        None,
        help="Optional Surya learned-order checkpoint or hf:// reference.",
    ),
    accept_model_license: bool = typer.Option(
        False,
        "--accept-model-license",
        help=(
            "Confirm acceptance of the modified AI Pubs OpenRAIL-M model-weight license. "
            "Surya code is Apache-2.0, but its weights and outputs have additional terms."
        ),
    ),
) -> None:
    """Run Surya FastLayout with learned order; fail rather than use raster fallback."""

    if not accept_model_license:
        raise typer.BadParameter(
            "Surya model weights require explicit license acceptance; pass --accept-model-license after review",
            param_hint="--accept-model-license",
        )
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
    payload = SuryaLayoutAdapter(
        checkpoint=checkpoint,
        order_checkpoint=order_checkpoint,
        device=device,
        num_threads=num_threads,
        confidence_threshold=confidence_threshold,
        batch_size=batch_size,
    ).analyze(
        [page.background_image for page in rendered.pages],
        page_indices=[page.page_index for page in rendered.pages],
    )
    output_path = write_ocr_json(payload, output)
    typer.echo(f"Surya layout/order JSON: {output_path}")
    typer.echo(f"Pages: {len(rendered.pages)}")
    typer.echo(f"Source type: {rendered.source_type}")
    typer.echo(f"Model: {payload.get('model')}")
    typer.echo(f"Relation policy: {payload.get('relation_policy')}")


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
    typer.echo(f"Strict explicit block transitions: {summary['strict_block_transition_count']}")
    typer.echo(f"Review explicit block transitions: {summary['review_block_transition_count']}")


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
