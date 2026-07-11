from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

from .pdf_render import IMAGE_SOURCE_EXTENSIONS


DOCLING_PACKAGE = "docling"
DOCLING_SOURCE = "docling-standard-heron"
DOCLING_CODE_LICENSE = "MIT"
DOCLING_LAYOUT_MODEL_LICENSE = "Apache-2.0"
DOCLING_SUPPORTED_EXTENSIONS = IMAGE_SOURCE_EXTENSIONS | {".pdf"}


@dataclass(frozen=True)
class DoclingResult:
    raw_payload: dict[str, Any]
    structure_payload: dict[str, Any]


class DoclingAdapter:
    """Run Docling layout/OCR and isolate its reading order as review evidence."""

    def __init__(
        self,
        *,
        converter_factory: Callable[..., Any] | None = None,
        provider_version: str | None = None,
    ) -> None:
        self.converter_factory = converter_factory
        self.provider_version = provider_version

    def analyze(
        self,
        source: str | Path,
        *,
        page_indices: Sequence[int] | None = None,
        max_pages: int | None = None,
        languages: Sequence[str] = ("eng",),
        tables: bool = False,
        force_ocr: bool = False,
        device: Literal["auto", "cpu", "cuda", "mps", "xpu"] = "cpu",
        threads: int = 2,
    ) -> DoclingResult:
        source_path = Path(source)
        if source_path.suffix.lower() not in DOCLING_SUPPORTED_EXTENSIONS:
            raise ValueError("Docling input must be a supported PDF or image source")
        if page_indices is not None and max_pages is not None:
            raise ValueError("page_indices cannot be combined with max_pages")
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if threads < 1:
            raise ValueError("threads must be at least 1")
        normalized_languages = tuple(str(language).strip() for language in languages if str(language).strip())
        if not normalized_languages:
            raise ValueError("at least one OCR language is required")

        page_range = _docling_page_range(page_indices)
        if source_path.suffix.lower() in IMAGE_SOURCE_EXTENSIONS and page_range not in (None, (1, 1)):
            raise ValueError("image sources contain only source page 1")
        converter = self._build_converter(
            languages=normalized_languages,
            tables=tables,
            force_ocr=force_ocr,
            device=device,
            threads=threads,
        )
        convert_options: dict[str, Any] = {}
        if page_range is not None:
            convert_options["page_range"] = page_range
        if max_pages is not None:
            convert_options["max_num_pages"] = max_pages
        try:
            conversion = converter.convert(str(source_path), **convert_options)
            document = conversion.document
            raw_payload = document.export_to_dict()
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"Docling execution failed: {exc}") from exc
        if not isinstance(raw_payload, dict):
            raise RuntimeError("Docling JSON root must be an object")

        provider_version = self.provider_version or _installed_provider_version()
        return DoclingResult(
            raw_payload=raw_payload,
            structure_payload=review_only_docling_payload(
                raw_payload,
                provider_version=provider_version,
                languages=normalized_languages,
                tables=tables,
                force_ocr=force_ocr,
                device=device,
            ),
        )

    def _build_converter(
        self,
        *,
        languages: tuple[str, ...],
        tables: bool,
        force_ocr: bool,
        device: str,
        threads: int,
    ) -> Any:
        options = {
            "languages": languages,
            "tables": tables,
            "force_ocr": force_ocr,
            "device": device,
            "threads": threads,
        }
        if self.converter_factory is not None:
            return self.converter_factory(**options)
        try:
            from docling.datamodel.accelerator_options import AcceleratorOptions
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                TesseractCliOcrOptions,
            )
            from docling.document_converter import (
                DocumentConverter,
                ImageFormatOption,
                PdfFormatOption,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Docling is not installed. Install requirements-docling.txt or replay a saved "
                "--structure-json result."
            ) from exc

        pipeline_options = PdfPipelineOptions(
            accelerator_options=AcceleratorOptions(num_threads=threads, device=device),
            do_table_structure=tables,
            do_ocr=True,
            ocr_options=TesseractCliOcrOptions(
                lang=list(languages),
                force_full_page_ocr=force_ocr,
            ),
        )
        return DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
            },
        )


def review_only_docling_payload(
    payload: Mapping[str, Any],
    *,
    provider_version: str = "unknown",
    languages: Sequence[str] = ("eng",),
    tables: bool = False,
    force_ocr: bool = False,
    device: str = "cpu",
) -> dict[str, Any]:
    """Attach an explicit non-executable provider contract to Docling JSON."""

    normalized = dict(payload)
    normalized.update(
        {
            "source": DOCLING_SOURCE,
            "model": "docling-layout-heron+reading-order-rb",
            "provider_version": f"docling-{provider_version}",
            "provider_code_license": DOCLING_CODE_LICENSE,
            "layout_model_license": DOCLING_LAYOUT_MODEL_LICENSE,
            "layout_model": "docling-project/docling-layout-heron",
            "reading_order_backend": "docling-ibm-models/rule-based",
            "semantic_policy": "review-only",
            "order_policy": "review-only",
            "relation_policy": "review-only",
            "docling_stream_policy": "disabled",
            "candidate_consensus_policy": "isolated",
            "runtime_reorder": False,
            "provider_options": {
                "ocr_engine": "tesseract",
                "ocr_languages": [str(language) for language in languages],
                "tables": bool(tables),
                "force_ocr": bool(force_ocr),
                "device": str(device),
            },
        }
    )
    return normalized


def _docling_page_range(page_indices: Sequence[int] | None) -> tuple[int, int] | None:
    if page_indices is None:
        return None
    selected = tuple(int(index) for index in page_indices)
    if not selected:
        raise ValueError("page_indices must not be empty")
    if any(index < 0 for index in selected):
        raise ValueError("source page index must be non-negative")
    if len(set(selected)) != len(selected):
        raise ValueError("page_indices must not contain duplicates")
    if selected != tuple(range(selected[0], selected[-1] + 1)):
        raise ValueError("Docling currently requires one contiguous page range")
    return selected[0] + 1, selected[-1] + 1


def _installed_provider_version() -> str:
    try:
        return version(DOCLING_PACKAGE)
    except PackageNotFoundError:
        return "unknown"
