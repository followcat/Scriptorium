from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen
from zipfile import ZipFile

import fitz


CompHrDocDownloader = Callable[[str], bytes]

COMPHRDOC_REPOSITORY = "https://github.com/microsoft/CompHRDoc"
COMPHRDOC_REVISION = "ca0fb394bdf01918f49ce2b61ef1564adb8e8b04"
COMPHRDOC_LICENSE = "MIT"
COMPHRDOC_ARCHIVE_SHA256 = "530f482b75523a80fe1b0a7480fd8273c44f9239e0189650a4841c0aae61d03d"
COMPHRDOC_ARCHIVE_URL = (
    "https://media.githubusercontent.com/media/microsoft/CompHRDoc/"
    f"{COMPHRDOC_REVISION}/CompHRDoc.zip"
)
COMPHRDOC_ANNOTATION_MEMBER = (
    "datasets/Comp-HRDoc/HRDH_MSRA_POD_TEST/unified_layout_analysis_test.json"
)
COMPHRDOC_TRAIN_ANNOTATION_MEMBER = (
    "datasets/Comp-HRDoc/HRDH_MSRA_POD_TRAIN/unified_layout_analysis_train.json"
)
COMPHRDOC_FETCH_SCHEMA = "scriptorium-comphrdoc-benchmark/v1"
COMPHRDOC_PROVIDER_CALIBRATION_SCHEMA = (
    "scriptorium-comphrdoc-provider-calibration/v1"
)
COMPHRDOC_PROVIDER_TEST_SCHEMA = "scriptorium-comphrdoc-provider-test/v1"
DEFAULT_COMPHRDOC_DOCUMENT_ID = "1401.3699"
SOURCE_PAGE_ALIGNMENT_MIN_ANNOTATION_TOKENS = 40
SOURCE_PAGE_ALIGNMENT_MIN_F1 = 0.6
SOURCE_PAGE_ALIGNMENT_MIN_MARGIN = 0.15
SOURCE_PAGE_ALIGNMENT_MIN_IMPROVEMENT = 0.15
SOURCE_PAGE_ALIGNMENT_MIN_OVERLAP_TOKENS = 20


@dataclass(frozen=True)
class CompHrDocBenchmarkSample:
    sample_id: str
    page_index: int
    image_path: Path
    structure_path: Path
    semantic_sidecar_path: Path


@dataclass(frozen=True)
class CompHrDocBenchmarkFetchResult:
    out_dir: Path
    manifest_path: Path
    source_pdf_path: Path
    samples: tuple[CompHrDocBenchmarkSample, ...]


@dataclass(frozen=True)
class CompHrDocProviderCalibrationFetchResult:
    out_dir: Path
    manifest_path: Path
    source_pdf_paths: tuple[Path, ...]
    samples: tuple[CompHrDocBenchmarkSample, ...]


@dataclass(frozen=True)
class CompHrDocProviderTestFetchResult:
    out_dir: Path
    manifest_path: Path
    source_pdf_paths: tuple[Path, ...]
    samples: tuple[CompHrDocBenchmarkSample, ...]


@dataclass(frozen=True)
class _CompHrDocProviderMaterialization:
    samples: tuple[CompHrDocBenchmarkSample, ...]
    source_pdf_paths: tuple[Path, ...]
    manifest_samples: tuple[dict[str, Any], ...]
    manifest_documents: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class CompHrDocRelationCorpusResult:
    out_dir: Path
    manifest_path: Path
    sample_count: int


@dataclass(frozen=True)
class CompHrDocRelationBenchmarkResult:
    report_path: Path
    report: dict[str, Any]


def fetch_comphrdoc_benchmark_samples(
    out_dir: str | Path,
    *,
    document_id: str = DEFAULT_COMPHRDOC_DOCUMENT_ID,
    max_pages: int = 5,
    refresh: bool = False,
    downloader: CompHrDocDownloader | None = None,
) -> CompHrDocBenchmarkFetchResult:
    """Fetch one fixed Comp-HRDoc test document with answer-separated pages."""

    if not document_id or any(character not in "0123456789." for character in document_id):
        raise ValueError("Comp-HRDoc document_id must be an arXiv-style numeric identifier")
    if max_pages < 1:
        raise ValueError("Comp-HRDoc max_pages must be at least 1")
    download = downloader or _download_bytes
    archive_bytes = download(COMPHRDOC_ARCHIVE_URL)
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    if archive_sha256 != COMPHRDOC_ARCHIVE_SHA256:
        raise ValueError("Comp-HRDoc annotation archive SHA-256 mismatch")
    annotations = _load_annotation_archive(archive_bytes)
    page_records = _document_page_records(annotations, document_id)
    if not page_records:
        raise ValueError(f"Comp-HRDoc test annotations do not contain document {document_id}")

    pdf_url = f"https://arxiv.org/pdf/{document_id}"
    pdf_bytes = download(pdf_url)
    target = Path(out_dir)
    images_dir = target / "images"
    structure_dir = target / "structure"
    images_dir.mkdir(parents=True, exist_ok=True)
    structure_dir.mkdir(parents=True, exist_ok=True)
    source_pdf_path = target / f"{document_id}.pdf"
    if refresh or not source_pdf_path.exists():
        source_pdf_path.write_bytes(pdf_bytes)

    samples: list[CompHrDocBenchmarkSample] = []
    manifest_samples: list[dict[str, Any]] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        selected_pages = min(max_pages, len(page_records), len(pdf))
        for page_index in range(selected_pages):
            image_record, page_annotations = page_records[page_index]
            sample_id = f"{document_id}_{page_index}"
            image_path = images_dir / f"{sample_id}.png"
            structure_path = structure_dir / f"{sample_id}.structure.json"
            semantic_path = image_path.with_suffix(".semantic-order.json")
            width = int(image_record["width"])
            height = int(image_record["height"])
            if refresh or not image_path.exists():
                _render_annotated_page(pdf[page_index], image_path, width=width, height=height)
            structure_payload, semantic_payload = _page_payloads(
                document_id,
                page_index,
                width,
                height,
                page_annotations,
            )
            if refresh or not structure_path.exists():
                structure_path.write_text(
                    json.dumps(structure_payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if refresh or not semantic_path.exists():
                semantic_path.write_text(
                    json.dumps(semantic_payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            samples.append(
                CompHrDocBenchmarkSample(
                    sample_id,
                    page_index,
                    image_path,
                    structure_path,
                    semantic_path,
                )
            )
            manifest_samples.append(
                {
                    "id": sample_id,
                    "page_index": page_index,
                    "image": str(image_path.relative_to(target)),
                    "structure": str(structure_path.relative_to(target)),
                    "semantic_sidecar": str(semantic_path.relative_to(target)),
                    "relation_count": len(semantic_payload["ro_linkings"]),
                }
            )

    manifest_path = target / "comphrdoc_benchmark_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": COMPHRDOC_FETCH_SCHEMA,
                "dataset": "Comp-HRDoc test",
                "repository": COMPHRDOC_REPOSITORY,
                "revision": COMPHRDOC_REVISION,
                "annotation_license": COMPHRDOC_LICENSE,
                "annotation_archive_sha256": archive_sha256,
                "document_id": document_id,
                "source_pdf_url": pdf_url,
                "source_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "selection": "fixed-document-page-prefix",
                "sample_count": len(samples),
                "evaluation_scope": "oracle-layout reading-order relations; source PDF rendered locally",
                "structure_input": {
                    "kind": "line-layout-anchor-only",
                    "relations_removed": True,
                },
                "samples": manifest_samples,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return CompHrDocBenchmarkFetchResult(target, manifest_path, source_pdf_path, tuple(samples))


def fetch_comphrdoc_provider_calibration_corpus(
    out_dir: str | Path,
    *,
    sample_count: int = 8,
    document_count: int = 4,
    calibration_fraction: float = 0.2,
    arxiv_version: str | None = None,
    annotation_archive: str | Path | None = None,
    refresh: bool = False,
    downloader: CompHrDocDownloader | None = None,
) -> CompHrDocProviderCalibrationFetchResult:
    """Rebuild a deterministic real-provider corpus from official train annotations."""

    if sample_count < 2:
        raise ValueError("Comp-HRDoc provider calibration sample_count must be at least 2")
    if document_count < 2 or document_count > sample_count:
        raise ValueError("document_count must be between 2 and sample_count")
    if not 0.05 <= calibration_fraction <= 0.5:
        raise ValueError("calibration_fraction must be between 0.05 and 0.5")
    normalized_arxiv_version = _normalized_arxiv_version(arxiv_version)
    download = downloader or _download_bytes
    archive_bytes, archive_sha256 = _comphrdoc_archive_bytes(
        download,
        annotation_archive=annotation_archive,
    )
    annotations = _load_annotation_archive(
        archive_bytes,
        member=COMPHRDOC_TRAIN_ANNOTATION_MEMBER,
    )
    document_pages = _provider_calibration_document_pages(annotations)
    selected_documents = _select_provider_calibration_documents(
        document_pages,
        document_count=document_count,
        calibration_fraction=calibration_fraction,
    )
    page_quotas = _balanced_quotas(sample_count, len(selected_documents))

    selected_documents = [
        {
            **document,
            "pages": _select_provider_calibration_pages(
                document["pages"],
                quota=page_quotas[document_position],
            ),
        }
        for document_position, document in enumerate(selected_documents)
    ]
    target = Path(out_dir)
    materialized = _materialize_comphrdoc_provider_corpus(
        target,
        selected_documents,
        arxiv_version=normalized_arxiv_version,
        refresh=refresh,
        downloader=download,
    )

    manifest_path = target / "comphrdoc_benchmark_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": COMPHRDOC_PROVIDER_CALIBRATION_SCHEMA,
                "dataset": "Comp-HRDoc train",
                "repository": COMPHRDOC_REPOSITORY,
                "revision": COMPHRDOC_REVISION,
                "annotation_license": COMPHRDOC_LICENSE,
                "annotation_archive_sha256": archive_sha256,
                "annotation_member": COMPHRDOC_TRAIN_ANNOTATION_MEMBER,
                "annotation_images_present_in_archive": False,
                "source_pdf_policy": (
                    "download original arXiv submissions for local reconstruction; "
                    "paper licenses remain those of their arXiv records"
                ),
                "arxiv_version": normalized_arxiv_version or "latest",
                "source_pdfs_redistributed_by_project": False,
                "selection": (
                    "document-hash-partition-layout-stratified-documents-and-pages-v1"
                ),
                "selection_uses_relation_labels": False,
                "selection_uses_oracle_layout": True,
                "selection_allowed_annotation_fields": [
                    "bbox",
                    "category_id",
                    "textline_polys",
                ],
                "selection_excluded_annotation_fields": [
                    "reading_order_id",
                    "reading_order_label",
                    "ro_linkings",
                ],
                "split_policy": "document-id-sha256-fit-calibration-v1",
                "split_unit": "document",
                "calibration_fraction": calibration_fraction,
                "sample_count": len(materialized.samples),
                "requested_sample_count": sample_count,
                "document_count": len(selected_documents),
                "inference_inputs_are_answer_free": True,
                "answer_separation": {
                    "provider_input": "rendered-image-only",
                    "oracle_structure_role": "evaluation-anchor-matching-only",
                    "semantic_sidecar_role": "evaluation-labels-only",
                    "provider_reads_oracle_structure": False,
                    "provider_reads_semantic_sidecar": False,
                },
                "source_page_alignment": _source_page_alignment_policy(),
                "structure_input": {
                    "kind": "line-layout-anchor-only",
                    "relations_removed": True,
                },
                "documents": materialized.manifest_documents,
                "samples": materialized.manifest_samples,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return CompHrDocProviderCalibrationFetchResult(
        target,
        manifest_path,
        materialized.source_pdf_paths,
        materialized.samples,
    )


def fetch_comphrdoc_provider_test_corpus(
    out_dir: str | Path,
    *,
    sample_count: int = 32,
    document_count: int = 16,
    document_offset: int = 0,
    arxiv_version: str | None = None,
    annotation_archive: str | Path | None = None,
    refresh: bool = False,
    downloader: CompHrDocDownloader | None = None,
) -> CompHrDocProviderTestFetchResult:
    """Rebuild a deterministic real-provider corpus from official test annotations."""

    if sample_count < 1:
        raise ValueError("Comp-HRDoc provider test sample_count must be at least 1")
    if document_count < 1 or document_count > sample_count:
        raise ValueError("document_count must be between 1 and sample_count")
    if document_offset < 0:
        raise ValueError("document_offset must be non-negative")
    normalized_arxiv_version = _normalized_arxiv_version(arxiv_version)
    download = downloader or _download_bytes
    archive_bytes, archive_sha256 = _comphrdoc_archive_bytes(
        download,
        annotation_archive=annotation_archive,
    )
    annotations = _load_annotation_archive(
        archive_bytes,
        member=COMPHRDOC_ANNOTATION_MEMBER,
    )
    document_pages = _provider_calibration_document_pages(annotations)
    selected_documents = _select_provider_test_documents(
        document_pages,
        document_count=document_count,
        document_offset=document_offset,
    )
    page_quotas = _balanced_quotas(sample_count, len(selected_documents))
    selected_documents = [
        {
            **document,
            "pages": _select_provider_test_pages(
                document["pages"],
                quota=page_quotas[document_position],
            ),
        }
        for document_position, document in enumerate(selected_documents)
    ]

    target = Path(out_dir)
    materialized = _materialize_comphrdoc_provider_corpus(
        target,
        selected_documents,
        arxiv_version=normalized_arxiv_version,
        refresh=refresh,
        downloader=download,
    )
    manifest_path = target / "comphrdoc_benchmark_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": COMPHRDOC_PROVIDER_TEST_SCHEMA,
                "dataset": "Comp-HRDoc test",
                "repository": COMPHRDOC_REPOSITORY,
                "revision": COMPHRDOC_REVISION,
                "annotation_license": COMPHRDOC_LICENSE,
                "annotation_archive_sha256": archive_sha256,
                "annotation_member": COMPHRDOC_ANNOTATION_MEMBER,
                "annotation_images_present_in_archive": False,
                "source_pdf_policy": (
                    "download original arXiv submissions for local reconstruction; "
                    "paper licenses remain those of their arXiv records"
                ),
                "arxiv_version": normalized_arxiv_version or "latest",
                "source_pdfs_redistributed_by_project": False,
                "selection": "document-hash-layout-stratified-test-documents-and-pages-v1",
                "selection_uses_relation_labels": False,
                "selection_uses_oracle_layout": True,
                "selection_allowed_annotation_fields": [
                    "bbox",
                    "category_id",
                    "textline_polys",
                ],
                "selection_excluded_annotation_fields": [
                    "reading_order_id",
                    "reading_order_label",
                    "ro_linkings",
                ],
                "split_policy": "official-test-only",
                "split_unit": "document",
                "partition": "test",
                "sample_count": len(materialized.samples),
                "requested_sample_count": sample_count,
                "document_count": len(selected_documents),
                "document_offset": document_offset,
                "selection_window": {
                    "document_offset": document_offset,
                    "document_count": len(selected_documents),
                },
                "inference_inputs_are_answer_free": True,
                "answer_separation": {
                    "provider_input": "rendered-image-only",
                    "oracle_structure_role": "evaluation-anchor-matching-only",
                    "semantic_sidecar_role": "evaluation-labels-only",
                    "provider_reads_oracle_structure": False,
                    "provider_reads_semantic_sidecar": False,
                },
                "source_page_alignment": _source_page_alignment_policy(),
                "structure_input": {
                    "kind": "line-layout-anchor-only",
                    "relations_removed": True,
                },
                "documents": materialized.manifest_documents,
                "samples": materialized.manifest_samples,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return CompHrDocProviderTestFetchResult(
        target,
        manifest_path,
        materialized.source_pdf_paths,
        materialized.samples,
    )


def _materialize_comphrdoc_provider_corpus(
    target: Path,
    selected_documents: list[dict[str, Any]],
    *,
    arxiv_version: str | None,
    refresh: bool,
    downloader: CompHrDocDownloader,
) -> _CompHrDocProviderMaterialization:
    images_dir = target / "images"
    structure_dir = target / "structure"
    semantic_dir = target / "semantic"
    sources_dir = target / "sources"
    for directory in (images_dir, structure_dir, semantic_dir, sources_dir):
        directory.mkdir(parents=True, exist_ok=True)

    samples: list[CompHrDocBenchmarkSample] = []
    manifest_samples: list[dict[str, Any]] = []
    manifest_documents: list[dict[str, Any]] = []
    source_pdf_paths: list[Path] = []
    for document in selected_documents:
        document_id = str(document["document_id"])
        partition = str(document["partition"])
        selected_pages = document["pages"]
        versioned_document_id = f"{document_id}{arxiv_version or ''}"
        pdf_url = f"https://arxiv.org/pdf/{versioned_document_id}"
        source_pdf_path = sources_dir / f"{versioned_document_id}.pdf"
        if source_pdf_path.exists() and not refresh:
            pdf_bytes = source_pdf_path.read_bytes()
        else:
            pdf_bytes = downloader(pdf_url)
            source_pdf_path.write_bytes(pdf_bytes)
        source_pdf_paths.append(source_pdf_path)
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            if not selected_pages:
                raise ValueError(f"Comp-HRDoc document {document_id} selected no pages")
            source_page_remap_count = 0
            for page_record in selected_pages:
                image_record = page_record["image"]
                page_annotations = page_record["annotations"]
                page_index = int(page_record["page_index"])
                source_page_index, source_page_alignment = _align_source_page(
                    pdf,
                    annotation_page_index=page_index,
                    annotations=page_annotations,
                )
                source_page_remap_count += int(source_page_index != page_index)
                sample_id = f"{document_id}_{page_index}"
                image_path = images_dir / f"{sample_id}.png"
                structure_path = structure_dir / f"{sample_id}.structure.json"
                semantic_path = semantic_dir / f"{sample_id}.semantic-order.json"
                width = int(image_record["width"])
                height = int(image_record["height"])
                if refresh or not image_path.exists():
                    _render_annotated_page(
                        pdf[source_page_index],
                        image_path,
                        width=width,
                        height=height,
                    )
                structure_payload, semantic_payload = _page_payloads(
                    document_id,
                    page_index,
                    width,
                    height,
                    page_annotations,
                )
                if refresh or not structure_path.exists():
                    structure_path.write_text(
                        json.dumps(structure_payload, ensure_ascii=False, indent=2)
                        + "\n",
                        encoding="utf-8",
                    )
                if refresh or not semantic_path.exists():
                    semantic_path.write_text(
                        json.dumps(semantic_payload, ensure_ascii=False, indent=2)
                        + "\n",
                        encoding="utf-8",
                    )
                alignment = source_page_alignment["selected_alignment"]
                samples.append(
                    CompHrDocBenchmarkSample(
                        sample_id,
                        page_index,
                        image_path,
                        structure_path,
                        semantic_path,
                    )
                )
                manifest_samples.append(
                    {
                        "id": sample_id,
                        "document_id": document_id,
                        "page_index": page_index,
                        "annotation_page_index": page_index,
                        "source_page_index": source_page_index,
                        "source_page_alignment": source_page_alignment,
                        "partition": partition,
                        "layout_stratum": page_record["layout_stratum"],
                        "layout_features": page_record["layout_features"],
                        "source_text_alignment": alignment,
                        "image": str(image_path.relative_to(target)),
                        "structure": str(structure_path.relative_to(target)),
                        "semantic_sidecar": str(semantic_path.relative_to(target)),
                        "relation_count": len(semantic_payload["ro_linkings"]),
                        "graphical_relation_count": _graphical_relation_count(
                            semantic_payload
                        ),
                    }
                )
            manifest_documents.append(
                {
                    "id": document_id,
                    "partition": partition,
                    "source_pdf": str(source_pdf_path.relative_to(target)),
                    "source_pdf_url": pdf_url,
                    "source_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                    "source_pdf_page_count": len(pdf),
                    "selected_page_count": len(selected_pages),
                    "source_page_remap_count": source_page_remap_count,
                }
            )
    return _CompHrDocProviderMaterialization(
        tuple(samples),
        tuple(source_pdf_paths),
        tuple(manifest_samples),
        tuple(manifest_documents),
    )


def _normalized_arxiv_version(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if re.fullmatch(r"v[1-9]\d*", normalized) is None:
        raise ValueError("arxiv_version must look like v1, v2, or another positive revision")
    return normalized


def _comphrdoc_archive_bytes(
    downloader: CompHrDocDownloader,
    *,
    annotation_archive: str | Path | None,
) -> tuple[bytes, str]:
    archive_bytes = (
        Path(annotation_archive).read_bytes()
        if annotation_archive is not None
        else downloader(COMPHRDOC_ARCHIVE_URL)
    )
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    if archive_sha256 != COMPHRDOC_ARCHIVE_SHA256:
        raise ValueError("Comp-HRDoc annotation archive SHA-256 mismatch")
    return archive_bytes, archive_sha256


def _provider_calibration_document_pages(
    payload: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    annotations_by_image: dict[int, list[dict[str, Any]]] = {}
    for annotation in payload.get("annotations", []):
        if isinstance(annotation, dict):
            annotations_by_image.setdefault(
                int(annotation.get("image_id", -1)),
                [],
            ).append(annotation)
    documents: dict[str, list[dict[str, Any]]] = {}
    for image in payload.get("images", []):
        if not isinstance(image, dict):
            continue
        stem = Path(str(image.get("file_name") or "")).stem
        try:
            document_id, raw_page_index = stem.rsplit("_", 1)
            page_index = int(raw_page_index)
        except (ValueError, TypeError):
            continue
        if re.fullmatch(r"\d{4}\.\d{4,5}", document_id) is None:
            continue
        annotations = annotations_by_image.get(int(image.get("id", -1)), [])
        layout_stratum, layout_features = _provider_page_layout_features(
            image,
            annotations,
        )
        documents.setdefault(document_id, []).append(
            {
                "image": image,
                "annotations": sorted(
                    annotations,
                    key=lambda annotation: (
                        int(annotation.get("reading_order_id", 0)),
                        int(annotation.get("in_page_id", 0)),
                    ),
                ),
                "page_index": page_index,
                "layout_stratum": layout_stratum,
                "layout_features": layout_features,
            }
        )
    for pages in documents.values():
        pages.sort(key=lambda page: int(page["page_index"]))
    return documents


def _select_provider_calibration_documents(
    document_pages: Mapping[str, list[dict[str, Any]]],
    *,
    document_count: int,
    calibration_fraction: float,
) -> list[dict[str, Any]]:
    calibration_count = min(
        document_count - 1,
        max(1, math.ceil(document_count * calibration_fraction)),
    )
    fit_count = document_count - calibration_count
    by_partition: dict[
        str,
        list[tuple[tuple[int, int, int], str, str, list[dict[str, Any]]]],
    ] = {
        "fit": [],
        "calibration": [],
    }
    for document_id, pages in document_pages.items():
        if not pages:
            continue
        partition = _provider_calibration_partition(
            document_id,
            calibration_fraction=calibration_fraction,
        )
        rank = hashlib.sha256(
            f"scriptorium-provider-calibration-document-v1:{document_id}".encode(
                "utf-8"
            )
        ).hexdigest()
        strata = {str(page["layout_stratum"]) for page in pages}
        layout_rank = (
            int(not any("multicolumn" in stratum for stratum in strata)),
            int(not any("graphical" in stratum for stratum in strata)),
            int("graphical-multicolumn" not in strata),
        )
        by_partition[partition].append(
            (layout_rank, rank, document_id, pages)
        )
    for candidates in by_partition.values():
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    if len(by_partition["fit"]) < fit_count or len(by_partition["calibration"]) < calibration_count:
        raise ValueError("Comp-HRDoc train split lacks enough documents for requested partitions")
    selected: list[dict[str, Any]] = []
    for partition, count in (("fit", fit_count), ("calibration", calibration_count)):
        selected.extend(
            {
                "document_id": document_id,
                "partition": partition,
                "pages": pages,
            }
            for _, _, document_id, pages in by_partition[partition][:count]
        )
    return selected


def _select_provider_test_documents(
    document_pages: Mapping[str, list[dict[str, Any]]],
    *,
    document_count: int,
    document_offset: int = 0,
) -> list[dict[str, Any]]:
    candidates: list[
        tuple[tuple[int, int, int], str, str, list[dict[str, Any]]]
    ] = []
    for document_id, pages in document_pages.items():
        if not pages:
            continue
        rank = hashlib.sha256(
            f"scriptorium-provider-test-document-v1:{document_id}".encode("utf-8")
        ).hexdigest()
        strata = {str(page["layout_stratum"]) for page in pages}
        layout_rank = (
            int(not any("multicolumn" in stratum for stratum in strata)),
            int(not any("graphical" in stratum for stratum in strata)),
            int("graphical-multicolumn" not in strata),
        )
        candidates.append((layout_rank, rank, document_id, pages))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    if document_offset < 0:
        raise ValueError("Comp-HRDoc test document_offset must be non-negative")
    if len(candidates) < document_offset + document_count:
        raise ValueError("Comp-HRDoc test split lacks enough documents for selection window")
    return [
        {
            "document_id": document_id,
            "partition": "test",
            "pages": pages,
        }
        for _, _, document_id, pages in candidates[
            document_offset : document_offset + document_count
        ]
    ]


def _provider_calibration_partition(
    document_id: str,
    *,
    calibration_fraction: float,
) -> str:
    boundary = int(calibration_fraction * 10_000)
    bucket = int.from_bytes(
        hashlib.sha256(document_id.encode("utf-8")).digest()[:4],
        "big",
    ) % 10_000
    return "calibration" if bucket < boundary else "fit"


def _balanced_quotas(total: int, groups: int) -> list[int]:
    base, remainder = divmod(total, groups)
    return [base + int(index < remainder) for index in range(groups)]


def _select_provider_calibration_pages(
    pages: list[dict[str, Any]],
    *,
    quota: int,
) -> list[dict[str, Any]]:
    return _select_provider_pages(
        pages,
        quota=quota,
        hash_namespace="scriptorium-provider-calibration-page-v1",
    )


def _select_provider_test_pages(
    pages: list[dict[str, Any]],
    *,
    quota: int,
) -> list[dict[str, Any]]:
    return _select_provider_pages(
        pages,
        quota=quota,
        hash_namespace="scriptorium-provider-test-page-v1",
    )


def _select_provider_pages(
    pages: list[dict[str, Any]],
    *,
    quota: int,
    hash_namespace: str,
) -> list[dict[str, Any]]:
    if len(pages) < quota:
        raise ValueError("selected Comp-HRDoc document has fewer pages than its quota")
    stratum_order = (
        "graphical-multicolumn",
        "multicolumn",
        "graphical",
        "plain",
    )
    queues: dict[str, list[dict[str, Any]]] = {name: [] for name in stratum_order}
    for page in pages:
        queues[str(page["layout_stratum"])].append(page)
    for stratum, candidates in queues.items():
        candidates.sort(
            key=lambda page: (
                hashlib.sha256(
                    f"{hash_namespace}:{stratum}:{page['image']['file_name']}".encode(
                        "utf-8"
                    )
                ).hexdigest(),
                int(page["page_index"]),
            )
        )
    selected: list[dict[str, Any]] = []
    while len(selected) < quota:
        progress = False
        for stratum in stratum_order:
            if queues[stratum] and len(selected) < quota:
                selected.append(queues[stratum].pop(0))
                progress = True
        if not progress:
            break
    return selected


def _provider_page_layout_features(
    image: Mapping[str, Any],
    annotations: list[dict[str, Any]],
) -> tuple[str, dict[str, int | float | bool]]:
    width = float(image.get("width") or 0)
    height = float(image.get("height") or 0)
    text_boxes: list[list[float]] = []
    figure_count = 0
    table_count = 0
    text_line_count = 0
    for annotation in annotations:
        kind = _graphical_kind(annotation)
        figure_count += int(kind == "figure")
        table_count += int(kind == "table")
        polygons = annotation.get("textline_polys")
        if isinstance(polygons, list):
            for polygon in polygons:
                box = _polygon_bbox(polygon)
                if box is not None:
                    text_boxes.append(box)
                    text_line_count += 1
    narrow = [box for box in text_boxes if width > 0 and box[2] - box[0] <= width * 0.62]
    left = [box for box in narrow if box[2] <= width * 0.52]
    right = [box for box in narrow if box[0] >= width * 0.48]
    vertical_overlap = 0.0
    if left and right and height > 0:
        vertical_overlap = max(
            0.0,
            min(max(box[3] for box in left), max(box[3] for box in right))
            - max(min(box[1] for box in left), min(box[1] for box in right)),
        ) / height
    multicolumn = len(left) >= 3 and len(right) >= 3 and vertical_overlap >= 0.18
    graphical_count = figure_count + table_count
    if graphical_count and multicolumn:
        stratum = "graphical-multicolumn"
    elif multicolumn:
        stratum = "multicolumn"
    elif graphical_count:
        stratum = "graphical"
    else:
        stratum = "plain"
    return stratum, {
        "annotation_count": len(annotations),
        "text_line_count": text_line_count,
        "narrow_text_line_count": len(narrow),
        "left_column_line_count": len(left),
        "right_column_line_count": len(right),
        "column_vertical_overlap_ratio": round(vertical_overlap, 8),
        "multicolumn": multicolumn,
        "figure_count": figure_count,
        "table_count": table_count,
        "graphical_count": graphical_count,
    }


def _source_text_alignment(
    page: fitz.Page,
    annotations: list[dict[str, Any]],
) -> dict[str, int | float]:
    reference = " ".join(
        str(content or "")
        for annotation in annotations
        for content in (
            annotation.get("textline_contents")
            if isinstance(annotation.get("textline_contents"), list)
            else []
        )
    )
    candidate = page.get_text("text")
    reference_tokens = Counter(re.findall(r"\w+", reference.casefold()))
    candidate_tokens = Counter(re.findall(r"\w+", candidate.casefold()))
    overlap = sum((reference_tokens & candidate_tokens).values())
    precision = overlap / sum(candidate_tokens.values()) if candidate_tokens else 0.0
    recall = overlap / sum(reference_tokens.values()) if reference_tokens else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "annotation_token_count": sum(reference_tokens.values()),
        "source_pdf_token_count": sum(candidate_tokens.values()),
        "overlap_token_count": overlap,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
    }


def _align_source_page(
    pdf: fitz.Document,
    *,
    annotation_page_index: int,
    annotations: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    """Resolve source revision page shifts without reading order labels."""

    if len(pdf) < 1:
        raise ValueError("source PDF contains no pages")
    candidates = [
        {
            "source_page_index": source_page_index,
            "alignment": _source_text_alignment(pdf[source_page_index], annotations),
        }
        for source_page_index in range(len(pdf))
    ]
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            float(candidate["alignment"]["f1"]),
            float(candidate["alignment"]["recall"]),
            int(candidate["alignment"]["overlap_token_count"]),
            -int(candidate["source_page_index"]),
        ),
        reverse=True,
    )
    best = ranked[0]
    second_best_f1 = (
        float(ranked[1]["alignment"]["f1"])
        if len(ranked) > 1
        else 0.0
    )
    same_index = next(
        (
            candidate
            for candidate in candidates
            if int(candidate["source_page_index"]) == annotation_page_index
        ),
        None,
    )
    annotation_token_count = int(best["alignment"]["annotation_token_count"])
    best_f1 = float(best["alignment"]["f1"])
    best_margin = best_f1 - second_best_f1
    best_has_evidence = (
        annotation_token_count >= SOURCE_PAGE_ALIGNMENT_MIN_ANNOTATION_TOKENS
        and int(best["alignment"]["overlap_token_count"])
        >= SOURCE_PAGE_ALIGNMENT_MIN_OVERLAP_TOKENS
        and best_f1 >= SOURCE_PAGE_ALIGNMENT_MIN_F1
        and best_margin >= SOURCE_PAGE_ALIGNMENT_MIN_MARGIN
    )
    if same_index is None:
        if not best_has_evidence:
            raise ValueError(
                "annotation page is outside the source PDF and text alignment is ambiguous"
            )
        selected = best
        policy = "remapped-out-of-range-by-text-alignment"
    else:
        same_f1 = float(same_index["alignment"]["f1"])
        best_improvement = best_f1 - same_f1
        if (
            int(best["source_page_index"]) != annotation_page_index
            and best_has_evidence
            and best_improvement >= SOURCE_PAGE_ALIGNMENT_MIN_IMPROVEMENT
        ):
            selected = best
            policy = "remapped-by-text-alignment"
        else:
            selected = same_index
            if annotation_token_count < SOURCE_PAGE_ALIGNMENT_MIN_ANNOTATION_TOKENS:
                policy = "same-index-sparse-annotation"
            elif same_f1 < SOURCE_PAGE_ALIGNMENT_MIN_F1:
                policy = "same-index-low-confidence-no-safe-remap"
            else:
                policy = "same-index"
    selected_page_index = int(selected["source_page_index"])
    return selected_page_index, {
        "policy": policy,
        "selection_uses_relation_labels": False,
        "alignment_field": "textline_contents",
        "annotation_page_index": annotation_page_index,
        "selected_source_page_index": selected_page_index,
        "remapped": selected_page_index != annotation_page_index,
        "candidate_page_count": len(candidates),
        "same_index_alignment": (
            same_index["alignment"] if same_index is not None else None
        ),
        "best_candidate_source_page_index": int(best["source_page_index"]),
        "best_candidate_alignment": best["alignment"],
        "second_best_f1": round(second_best_f1, 8),
        "best_margin": round(best_margin, 8),
        "selected_alignment": selected["alignment"],
        "thresholds": {
            "minimum_annotation_tokens": SOURCE_PAGE_ALIGNMENT_MIN_ANNOTATION_TOKENS,
            "minimum_overlap_tokens": SOURCE_PAGE_ALIGNMENT_MIN_OVERLAP_TOKENS,
            "minimum_f1": SOURCE_PAGE_ALIGNMENT_MIN_F1,
            "minimum_best_vs_second_margin": SOURCE_PAGE_ALIGNMENT_MIN_MARGIN,
            "minimum_best_vs_same_improvement": (
                SOURCE_PAGE_ALIGNMENT_MIN_IMPROVEMENT
            ),
        },
    }


def _source_page_alignment_policy() -> dict[str, Any]:
    return {
        "policy": "same-index-unless-unique-high-confidence-text-remap-v1",
        "selection_uses_relation_labels": False,
        "alignment_field": "textline_contents",
        "minimum_annotation_tokens": SOURCE_PAGE_ALIGNMENT_MIN_ANNOTATION_TOKENS,
        "minimum_overlap_tokens": SOURCE_PAGE_ALIGNMENT_MIN_OVERLAP_TOKENS,
        "minimum_f1": SOURCE_PAGE_ALIGNMENT_MIN_F1,
        "minimum_best_vs_second_margin": SOURCE_PAGE_ALIGNMENT_MIN_MARGIN,
        "minimum_best_vs_same_improvement": SOURCE_PAGE_ALIGNMENT_MIN_IMPROVEMENT,
        "sparse_or_ambiguous_policy": "keep-same-index-and-record-low-confidence",
        "out_of_range_ambiguous_policy": "fail",
    }


def fetch_comphrdoc_relation_corpus(
    out_dir: str | Path,
    *,
    sample_count: int = 250,
    refresh: bool = False,
    downloader: CompHrDocDownloader | None = None,
) -> CompHrDocRelationCorpusResult:
    """Build a fixed cross-document floating relation corpus without PDF images."""

    if sample_count < 1:
        raise ValueError("Comp-HRDoc relation corpus sample_count must be at least 1")
    archive_bytes = (downloader or _download_bytes)(COMPHRDOC_ARCHIVE_URL)
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    if archive_sha256 != COMPHRDOC_ARCHIVE_SHA256:
        raise ValueError("Comp-HRDoc annotation archive SHA-256 mismatch")
    payload = _load_annotation_archive(archive_bytes)
    annotations_by_image: dict[int, list[dict[str, Any]]] = {}
    for annotation in payload["annotations"]:
        if isinstance(annotation, dict):
            annotations_by_image.setdefault(int(annotation.get("image_id", -1)), []).append(annotation)
    images = sorted(
        (image for image in payload["images"] if isinstance(image, dict)),
        key=lambda image: str(image.get("file_name") or ""),
    )
    selected = []
    for image in images:
        annotations = annotations_by_image.get(int(image.get("id", -1)), [])
        if not _has_graphical_floating_group(annotations):
            continue
        selected.append((image, annotations))
        if len(selected) >= sample_count:
            break

    target = Path(out_dir)
    structure_dir = target / "structure"
    semantic_dir = target / "semantic"
    structure_dir.mkdir(parents=True, exist_ok=True)
    semantic_dir.mkdir(parents=True, exist_ok=True)
    manifest_samples: list[dict[str, Any]] = []
    for image, annotations in selected:
        file_name = str(image["file_name"])
        document_id, raw_page_index = Path(file_name).stem.rsplit("_", 1)
        page_index = int(raw_page_index)
        ordered_annotations = sorted(
            annotations,
            key=lambda annotation: (
                int(annotation.get("reading_order_id", 0)),
                int(annotation.get("in_page_id", 0)),
            ),
        )
        structure, semantic = _page_payloads(
            document_id,
            page_index,
            int(image["width"]),
            int(image["height"]),
            ordered_annotations,
        )
        sample_id = f"{document_id}_{page_index}"
        structure_path = structure_dir / f"{sample_id}.structure.json"
        semantic_path = semantic_dir / f"{sample_id}.semantic-order.json"
        if refresh or not structure_path.exists():
            structure_path.write_text(json.dumps(structure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if refresh or not semantic_path.exists():
            semantic_path.write_text(json.dumps(semantic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        graphical_relation_count = _graphical_relation_count(semantic)
        manifest_samples.append(
            {
                "id": sample_id,
                "source_annotation_image": file_name,
                "structure": str(structure_path.relative_to(target)),
                "semantic_sidecar": str(semantic_path.relative_to(target)),
                "relation_count": len(semantic["ro_linkings"]),
                "graphical_relation_count": graphical_relation_count,
            }
        )

    manifest_path = target / "comphrdoc_relation_corpus_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "scriptorium-comphrdoc-relation-corpus/v1",
                "dataset": "Comp-HRDoc test",
                "repository": COMPHRDOC_REPOSITORY,
                "revision": COMPHRDOC_REVISION,
                "annotation_license": COMPHRDOC_LICENSE,
                "annotation_archive_sha256": archive_sha256,
                "selection": "published-image-name-order-floating-page-prefix",
                "selection_uses_labels": True,
                "inference_inputs_are_answer_free": True,
                "sample_count": len(manifest_samples),
                "requested_sample_count": sample_count,
                "source_images_redistributed": False,
                "samples": manifest_samples,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return CompHrDocRelationCorpusResult(target, manifest_path, len(manifest_samples))


def benchmark_comphrdoc_relation_corpus(
    corpus_dir: str | Path,
    model_path: str | Path,
    *,
    floating_model_path: str | Path | None = None,
    noise_profile: str = "clean",
    output: str | Path | None = None,
    semantic_scorer: Any | None = None,
) -> CompHrDocRelationBenchmarkResult:
    """Score relation-role fusion on an answer-separated Comp-HRDoc corpus."""

    from . import relation_ranker
    from .provider_anchor_benchmark import _graphical_relation_audit
    from .relation_order import merge_relation_edge_path_cover
    from .relation_noise import perturb_relation_structure

    corpus = Path(corpus_dir)
    manifest_path = corpus / "comphrdoc_relation_corpus_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Comp-HRDoc relation corpus manifest is required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "scriptorium-comphrdoc-relation-corpus/v1":
        raise ValueError("unsupported Comp-HRDoc relation corpus schema")
    bundle, model_manifest = relation_ranker.load_relation_ranker(model_path)
    modes = {"native-ranker": False, "native-plus-structure-role": True}
    floating_bundle = None
    floating_manifest = None
    if floating_model_path is not None:
        from . import floating_ranker

        floating_bundle, floating_manifest = floating_ranker.load_floating_relation_ranker(
            floating_model_path
        )
        modes["native-plus-trained-floating"] = False
    totals = {name: _empty_relation_totals() for name in modes}
    noise_totals: Counter[str] = Counter()
    graphical_audit_totals: Counter[str] = Counter()
    page_results: list[dict[str, Any]] = []
    pending: list[
        tuple[
            Mapping[str, Any],
            dict[str, Any],
            dict[str, list[dict[str, Any]]],
        ]
    ] = []
    # Phase one finishes every provider prediction before any semantic sidecar
    # path is resolved or opened.
    for sample in manifest.get("samples", []):
        structure_path = corpus / str(sample["structure"])
        structure = json.loads(structure_path.read_text(encoding="utf-8"))
        structure, noise_diagnostics = perturb_relation_structure(
            structure,
            profile=noise_profile,
        )
        for key in (
            "source_element_count",
            "retained_element_count",
            "jittered_element",
            "fragmented_element",
            "type_dropout",
            "element_dropout",
            "prefix_corruption",
        ):
            noise_totals[key] += int(noise_diagnostics.get(key, 0))
        prediction_cache: dict[bool, dict[str, Any]] = {}
        prediction_edges_by_mode: dict[str, list[dict[str, Any]]] = {}
        for mode, enabled in modes.items():
            if enabled not in prediction_cache:
                prediction_cache[enabled] = relation_ranker._predict_roor_page_relations(
                    structure,
                    bundle=bundle,
                    manifest=model_manifest,
                    structure_role_fusion=enabled,
                    semantic_scorer=semantic_scorer,
                ).structure_payload
            prediction_edges = list(prediction_cache[enabled].get("successor_edges", []))
            if mode == "native-plus-trained-floating":
                assert floating_bundle is not None and floating_manifest is not None
                learned = floating_ranker._predict_floating_relations(
                    structure,
                    bundle=floating_bundle,
                    manifest=floating_manifest,
                )
                learned_sources = {edge["source"] for edge in learned.successor_edges}
                prediction_edges = [
                    edge for edge in prediction_edges if edge["source"] not in learned_sources
                ] + learned.successor_edges
            prediction_edges_by_mode[mode] = prediction_edges
        pending.append((sample, structure, prediction_edges_by_mode))

    # Phase two opens immutable labels only after phase one is complete.
    for sample, structure, prediction_edges_by_mode in pending:
        semantic_path = corpus / str(sample["semantic_sidecar"])
        semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
        truth = {tuple(edge) for edge in semantic.get("ro_linkings", [])}
        retained_ids = {
            element.get("id")
            for element in structure.get("document", [])
            if isinstance(element, Mapping)
        }
        noise_totals["label_count"] += len(truth)
        noise_totals["resolvable_label_count"] += sum(
            int(source in retained_ids and target in retained_ids)
            for source, target in truth
        )
        nodes = {
            node.get("id"): node
            for node in semantic.get("document", [])
            if isinstance(node, Mapping)
        }
        graphical_truth = {
            edge
            for edge in truth
            if nodes.get(edge[0], {}).get("type") in {"figure", "table"}
            or nodes.get(edge[1], {}).get("type") in {"figure", "table"}
        }
        graphical_audit = _graphical_relation_audit(
            semantic,
            [node for node in semantic.get("document", []) if isinstance(node, Mapping)],
            truth,
            {},
        )
        conflict_graphical_ids = {
            str(conflict["graphical_id"])
            for conflict in graphical_audit["conflicts"]
        }
        for key in (
            "oracle_graphical_label_count",
            "geometry_proposal_count",
            "exact_agreement_count",
            "conflicting_label_count",
            "oracle_without_geometry_count",
            "geometry_without_oracle_count",
        ):
            graphical_audit_totals[key] += int(graphical_audit[key])
        graphical_audit_totals["cases_with_conflicts"] += int(
            bool(graphical_audit["conflicting_label_count"])
        )
        page_result: dict[str, Any] = {
            "id": sample["id"],
            "label_count": len(truth),
            "graphical_label_audit": {
                key: graphical_audit[key]
                for key in (
                    "reference_policy",
                    "oracle_graphical_label_count",
                    "geometry_proposal_count",
                    "exact_agreement_count",
                    "conflicting_label_count",
                    "oracle_without_geometry_count",
                    "geometry_without_oracle_count",
                    "conflicts",
                )
            },
        }
        for mode in modes:
            prediction_edges = prediction_edges_by_mode[mode]
            if mode == "native-plus-trained-floating":
                role_origin = "trained-floating-pair"
            else:
                role_origin = "structure-role-geometry"
            predicted = {(edge["source"], edge["target"]) for edge in prediction_edges}
            role_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("relation_origin") == role_origin
            }
            high_reliability_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("reliability_tier") == "high-precision-review"
            }
            high_reliability_in_envelope_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("reliability_tier") == "high-precision-review"
                and int(edge.get("feature_outlier_count", 0)) == 0
            }
            strict_gate_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("strict_gate_passed") is True
            }
            strict_gate_in_envelope_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("strict_gate_passed") is True
                and int(edge.get("feature_outlier_count", 0)) == 0
            }
            noise_aware_review_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("noise_aware_reliability_tier")
                == "robust-high-precision-review"
            }
            noise_aware_strict_predicted = {
                (edge["source"], edge["target"])
                for edge in prediction_edges
                if edge.get("noise_aware_strict_gate_passed") is True
            }
            metrics = _relation_counts(predicted, truth)
            role_metrics = _relation_counts(role_predicted, graphical_truth)
            high_reliability_metrics = _relation_counts(
                high_reliability_predicted,
                graphical_truth,
            )
            high_reliability_in_envelope_metrics = _relation_counts(
                high_reliability_in_envelope_predicted,
                graphical_truth,
            )
            strict_gate_metrics = _relation_counts(
                strict_gate_predicted,
                graphical_truth,
            )
            strict_gate_in_envelope_metrics = _relation_counts(
                strict_gate_in_envelope_predicted,
                graphical_truth,
            )
            noise_aware_review_metrics = _relation_counts(
                noise_aware_review_predicted,
                graphical_truth,
            )
            noise_aware_strict_metrics = _relation_counts(
                noise_aware_strict_predicted,
                graphical_truth,
            )
            strict_conflict_predictions = _graphical_conflict_prediction_count(
                strict_gate_predicted,
                conflict_graphical_ids,
            )
            strict_conflict_incorrect = _graphical_conflict_prediction_count(
                strict_gate_predicted - graphical_truth,
                conflict_graphical_ids,
            )
            strict_in_envelope_conflict_predictions = (
                _graphical_conflict_prediction_count(
                    strict_gate_in_envelope_predicted,
                    conflict_graphical_ids,
                )
            )
            strict_in_envelope_conflict_incorrect = (
                _graphical_conflict_prediction_count(
                    strict_gate_in_envelope_predicted - graphical_truth,
                    conflict_graphical_ids,
                )
            )
            noise_aware_strict_conflict_predictions = (
                _graphical_conflict_prediction_count(
                    noise_aware_strict_predicted,
                    conflict_graphical_ids,
                )
            )
            noise_aware_strict_conflict_incorrect = (
                _graphical_conflict_prediction_count(
                    noise_aware_strict_predicted - graphical_truth,
                    conflict_graphical_ids,
                )
            )
            if mode == "native-plus-trained-floating":
                graphical_audit_totals["strict_gate_conflict_prediction_count"] += (
                    strict_conflict_predictions
                )
                graphical_audit_totals["strict_gate_conflict_incorrect_count"] += (
                    strict_conflict_incorrect
                )
                graphical_audit_totals[
                    "strict_gate_in_envelope_conflict_prediction_count"
                ] += strict_in_envelope_conflict_predictions
                graphical_audit_totals[
                    "strict_gate_in_envelope_conflict_incorrect_count"
                ] += strict_in_envelope_conflict_incorrect
                graphical_audit_totals[
                    "noise_aware_strict_conflict_prediction_count"
                ] += noise_aware_strict_conflict_predictions
                graphical_audit_totals[
                    "noise_aware_strict_conflict_incorrect_count"
                ] += noise_aware_strict_conflict_incorrect
            ordered_edges = sorted(
                prediction_edges,
                key=lambda edge: float(edge.get("confidence", 0.0)),
                reverse=True,
            )
            protected_path_edges = [
                (edge["source"], edge["target"])
                for edge in ordered_edges
                if edge.get("reliability_tier") == "high-precision-review"
                and int(edge.get("feature_outlier_count", 0)) == 0
            ]
            merged_path = merge_relation_edge_path_cover(
                ((edge["source"], edge["target"]) for edge in ordered_edges),
                protected_edges=protected_path_edges,
            )
            joint_path_metrics = _relation_counts(set(merged_path.selected_edges), truth)
            noise_aware_protected_path_edges = [
                (edge["source"], edge["target"])
                for edge in ordered_edges
                if edge.get("noise_aware_reliability_tier")
                == "robust-high-precision-review"
            ]
            noise_aware_merged_path = merge_relation_edge_path_cover(
                ((edge["source"], edge["target"]) for edge in ordered_edges),
                protected_edges=noise_aware_protected_path_edges,
            )
            noise_aware_joint_path_metrics = _relation_counts(
                set(noise_aware_merged_path.selected_edges),
                truth,
            )
            _accumulate_relation_totals(
                totals[mode],
                metrics,
                role_metrics,
                high_reliability_metrics,
                high_reliability_in_envelope_metrics,
                strict_gate_metrics,
                strict_gate_in_envelope_metrics,
                noise_aware_review_metrics,
                noise_aware_strict_metrics,
                joint_path_metrics,
                merged_path,
                noise_aware_joint_path_metrics,
                noise_aware_merged_path,
            )
            page_result[mode] = {
                **metrics,
                "graphical": role_metrics,
                "high_reliability_graphical": high_reliability_metrics,
                "high_reliability_in_envelope_graphical": high_reliability_in_envelope_metrics,
                "strict_gate_graphical": strict_gate_metrics,
                "strict_gate_in_envelope_graphical": strict_gate_in_envelope_metrics,
                "noise_aware_review_graphical": noise_aware_review_metrics,
                "noise_aware_strict_graphical": noise_aware_strict_metrics,
                "strict_gate_conflict_prediction_count": strict_conflict_predictions,
                "strict_gate_conflict_incorrect_count": strict_conflict_incorrect,
                "strict_gate_in_envelope_conflict_prediction_count": (
                    strict_in_envelope_conflict_predictions
                ),
                "strict_gate_in_envelope_conflict_incorrect_count": (
                    strict_in_envelope_conflict_incorrect
                ),
                "noise_aware_strict_conflict_prediction_count": (
                    noise_aware_strict_conflict_predictions
                ),
                "noise_aware_strict_conflict_incorrect_count": (
                    noise_aware_strict_conflict_incorrect
                ),
                "joint_path_cover": {
                    **joint_path_metrics,
                    "protected_selected": len(merged_path.protected_selected_edges),
                    "rejected_outgoing_conflict": merged_path.rejected_outgoing_conflict_count,
                    "rejected_incoming_conflict": merged_path.rejected_incoming_conflict_count,
                    "rejected_cycle": merged_path.rejected_cycle_count,
                },
                "noise_aware_joint_path_cover": {
                    **noise_aware_joint_path_metrics,
                    "protected_selected": len(
                        noise_aware_merged_path.protected_selected_edges
                    ),
                    "rejected_outgoing_conflict": (
                        noise_aware_merged_path.rejected_outgoing_conflict_count
                    ),
                    "rejected_incoming_conflict": (
                        noise_aware_merged_path.rejected_incoming_conflict_count
                    ),
                    "rejected_cycle": noise_aware_merged_path.rejected_cycle_count,
                },
            }
        page_results.append(page_result)
    summarized = {name: _summarize_relation_totals(value) for name, value in totals.items()}
    baseline_f1 = summarized["native-ranker"]["f1"]
    fused_f1 = summarized["native-plus-structure-role"]["f1"]
    report = {
        "schema": "scriptorium-comphrdoc-relation-benchmark/v1",
        "corpus_manifest": str(manifest_path),
        "corpus_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "model_sha256": model_manifest.get("model_sha256"),
        "semantic_scorer": model_manifest.get("semantic_scorer"),
        "semantic_fusion": model_manifest.get("semantic_fusion"),
        "semantic_top_k": model_manifest.get("semantic_top_k"),
        "floating_model_sha256": (
            floating_manifest.get("model_sha256") if floating_manifest is not None else None
        ),
        "sample_count": len(page_results),
        "selection": manifest.get("selection"),
        "inference_inputs_are_answer_free": manifest.get("inference_inputs_are_answer_free"),
        "labels_opened_after_all_predictions": True,
        "noise": {
            "profile": noise_profile,
            **dict(noise_totals),
            "element_retention_ratio": round(
                noise_totals["retained_element_count"]
                / noise_totals["source_element_count"],
                8,
            )
            if noise_totals["source_element_count"]
            else 0.0,
            "resolvable_label_ratio": round(
                noise_totals["resolvable_label_count"] / noise_totals["label_count"],
                8,
            )
            if noise_totals["label_count"]
            else 0.0,
        },
        "graphical_label_audit": {
            "reference_policy": "answer-free-local-geometry-diagnostic-not-ground-truth",
            **dict(graphical_audit_totals),
            "oracle_geometry_exact_agreement": round(
                graphical_audit_totals["exact_agreement_count"]
                / graphical_audit_totals["oracle_graphical_label_count"],
                8,
            )
            if graphical_audit_totals["oracle_graphical_label_count"]
            else 0.0,
            "oracle_geometry_conflict_rate": round(
                graphical_audit_totals["conflicting_label_count"]
                / graphical_audit_totals["oracle_graphical_label_count"],
                8,
            )
            if graphical_audit_totals["oracle_graphical_label_count"]
            else 0.0,
        },
        "summary": summarized,
        "f1_delta": round(fused_f1 - baseline_f1, 8),
        "pages": page_results,
    }
    if "native-plus-trained-floating" in summarized:
        report["trained_floating_f1_delta"] = round(
            summarized["native-plus-trained-floating"]["f1"] - baseline_f1,
            8,
        )
    report_path = Path(output) if output is not None else corpus / "relation_benchmark_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CompHrDocRelationBenchmarkResult(report_path, report)


def _empty_relation_totals() -> dict[str, int]:
    return {
        "correct": 0,
        "predicted": 0,
        "labels": 0,
        "graphical_correct": 0,
        "graphical_predicted": 0,
        "graphical_labels": 0,
        "high_reliability_correct": 0,
        "high_reliability_predicted": 0,
        "high_reliability_labels": 0,
        "high_reliability_in_envelope_correct": 0,
        "high_reliability_in_envelope_predicted": 0,
        "high_reliability_in_envelope_labels": 0,
        "strict_gate_correct": 0,
        "strict_gate_predicted": 0,
        "strict_gate_labels": 0,
        "strict_gate_in_envelope_correct": 0,
        "strict_gate_in_envelope_predicted": 0,
        "strict_gate_in_envelope_labels": 0,
        "noise_aware_review_correct": 0,
        "noise_aware_review_predicted": 0,
        "noise_aware_review_labels": 0,
        "noise_aware_strict_correct": 0,
        "noise_aware_strict_predicted": 0,
        "noise_aware_strict_labels": 0,
        "joint_path_correct": 0,
        "joint_path_predicted": 0,
        "joint_path_labels": 0,
        "joint_path_protected_selected": 0,
        "joint_path_rejected_outgoing_conflict": 0,
        "joint_path_rejected_incoming_conflict": 0,
        "joint_path_rejected_cycle": 0,
        "noise_aware_joint_path_correct": 0,
        "noise_aware_joint_path_predicted": 0,
        "noise_aware_joint_path_labels": 0,
        "noise_aware_joint_path_protected_selected": 0,
        "noise_aware_joint_path_rejected_outgoing_conflict": 0,
        "noise_aware_joint_path_rejected_incoming_conflict": 0,
        "noise_aware_joint_path_rejected_cycle": 0,
    }


def _relation_counts(predicted: set[tuple[Any, Any]], truth: set[tuple[Any, Any]]) -> dict[str, int]:
    return {"correct": len(predicted & truth), "predicted": len(predicted), "labels": len(truth)}


def _graphical_conflict_prediction_count(
    predicted: set[tuple[Any, Any]],
    conflict_graphical_ids: set[str],
) -> int:
    return sum(
        int(str(source) in conflict_graphical_ids or str(target) in conflict_graphical_ids)
        for source, target in predicted
    )


def _accumulate_relation_totals(
    totals: dict[str, int],
    metrics: Mapping[str, int],
    graphical: Mapping[str, int],
    high_reliability: Mapping[str, int],
    high_reliability_in_envelope: Mapping[str, int],
    strict_gate: Mapping[str, int],
    strict_gate_in_envelope: Mapping[str, int],
    noise_aware_review: Mapping[str, int],
    noise_aware_strict: Mapping[str, int],
    joint_path: Mapping[str, int],
    merged_path: Any,
    noise_aware_joint_path: Mapping[str, int],
    noise_aware_merged_path: Any,
) -> None:
    for key in ("correct", "predicted", "labels"):
        totals[key] += int(metrics[key])
        totals[f"graphical_{key}"] += int(graphical[key])
        totals[f"high_reliability_{key}"] += int(high_reliability[key])
        totals[f"high_reliability_in_envelope_{key}"] += int(
            high_reliability_in_envelope[key]
        )
        totals[f"strict_gate_{key}"] += int(strict_gate[key])
        totals[f"strict_gate_in_envelope_{key}"] += int(
            strict_gate_in_envelope[key]
        )
        totals[f"noise_aware_review_{key}"] += int(noise_aware_review[key])
        totals[f"noise_aware_strict_{key}"] += int(noise_aware_strict[key])
        totals[f"joint_path_{key}"] += int(joint_path[key])
        totals[f"noise_aware_joint_path_{key}"] += int(
            noise_aware_joint_path[key]
        )
    totals["joint_path_protected_selected"] += len(merged_path.protected_selected_edges)
    totals["joint_path_rejected_outgoing_conflict"] += merged_path.rejected_outgoing_conflict_count
    totals["joint_path_rejected_incoming_conflict"] += merged_path.rejected_incoming_conflict_count
    totals["joint_path_rejected_cycle"] += merged_path.rejected_cycle_count
    totals["noise_aware_joint_path_protected_selected"] += len(
        noise_aware_merged_path.protected_selected_edges
    )
    totals["noise_aware_joint_path_rejected_outgoing_conflict"] += (
        noise_aware_merged_path.rejected_outgoing_conflict_count
    )
    totals["noise_aware_joint_path_rejected_incoming_conflict"] += (
        noise_aware_merged_path.rejected_incoming_conflict_count
    )
    totals["noise_aware_joint_path_rejected_cycle"] += (
        noise_aware_merged_path.rejected_cycle_count
    )


def _summarize_relation_totals(totals: Mapping[str, int]) -> dict[str, Any]:
    result: dict[str, Any] = dict(totals)
    result.update(_precision_recall_f1(totals["correct"], totals["predicted"], totals["labels"]))
    result["graphical"] = {
        "correct": totals["graphical_correct"],
        "predicted": totals["graphical_predicted"],
        "labels": totals["graphical_labels"],
        **_precision_recall_f1(
            totals["graphical_correct"],
            totals["graphical_predicted"],
            totals["graphical_labels"],
        ),
    }
    result["high_reliability_graphical"] = {
        "correct": totals["high_reliability_correct"],
        "predicted": totals["high_reliability_predicted"],
        "labels": totals["high_reliability_labels"],
        **_precision_recall_f1(
            totals["high_reliability_correct"],
            totals["high_reliability_predicted"],
            totals["high_reliability_labels"],
        ),
    }
    result["high_reliability_in_envelope_graphical"] = {
        "correct": totals["high_reliability_in_envelope_correct"],
        "predicted": totals["high_reliability_in_envelope_predicted"],
        "labels": totals["high_reliability_in_envelope_labels"],
        **_precision_recall_f1(
            totals["high_reliability_in_envelope_correct"],
            totals["high_reliability_in_envelope_predicted"],
            totals["high_reliability_in_envelope_labels"],
        ),
    }
    result["strict_gate_graphical"] = {
        "correct": totals["strict_gate_correct"],
        "predicted": totals["strict_gate_predicted"],
        "labels": totals["strict_gate_labels"],
        **_precision_recall_f1(
            totals["strict_gate_correct"],
            totals["strict_gate_predicted"],
            totals["strict_gate_labels"],
        ),
    }
    result["strict_gate_in_envelope_graphical"] = {
        "correct": totals["strict_gate_in_envelope_correct"],
        "predicted": totals["strict_gate_in_envelope_predicted"],
        "labels": totals["strict_gate_in_envelope_labels"],
        **_precision_recall_f1(
            totals["strict_gate_in_envelope_correct"],
            totals["strict_gate_in_envelope_predicted"],
            totals["strict_gate_in_envelope_labels"],
        ),
    }
    result["noise_aware_review_graphical"] = {
        "correct": totals["noise_aware_review_correct"],
        "predicted": totals["noise_aware_review_predicted"],
        "labels": totals["noise_aware_review_labels"],
        **_precision_recall_f1(
            totals["noise_aware_review_correct"],
            totals["noise_aware_review_predicted"],
            totals["noise_aware_review_labels"],
        ),
    }
    result["noise_aware_strict_graphical"] = {
        "correct": totals["noise_aware_strict_correct"],
        "predicted": totals["noise_aware_strict_predicted"],
        "labels": totals["noise_aware_strict_labels"],
        **_precision_recall_f1(
            totals["noise_aware_strict_correct"],
            totals["noise_aware_strict_predicted"],
            totals["noise_aware_strict_labels"],
        ),
    }
    result["joint_path_cover"] = {
        "correct": totals["joint_path_correct"],
        "predicted": totals["joint_path_predicted"],
        "labels": totals["joint_path_labels"],
        **_precision_recall_f1(
            totals["joint_path_correct"],
            totals["joint_path_predicted"],
            totals["joint_path_labels"],
        ),
        "protected_selected": totals["joint_path_protected_selected"],
        "rejected_outgoing_conflict": totals["joint_path_rejected_outgoing_conflict"],
        "rejected_incoming_conflict": totals["joint_path_rejected_incoming_conflict"],
        "rejected_cycle": totals["joint_path_rejected_cycle"],
    }
    result["noise_aware_joint_path_cover"] = {
        "correct": totals["noise_aware_joint_path_correct"],
        "predicted": totals["noise_aware_joint_path_predicted"],
        "labels": totals["noise_aware_joint_path_labels"],
        **_precision_recall_f1(
            totals["noise_aware_joint_path_correct"],
            totals["noise_aware_joint_path_predicted"],
            totals["noise_aware_joint_path_labels"],
        ),
        "protected_selected": totals[
            "noise_aware_joint_path_protected_selected"
        ],
        "rejected_outgoing_conflict": totals[
            "noise_aware_joint_path_rejected_outgoing_conflict"
        ],
        "rejected_incoming_conflict": totals[
            "noise_aware_joint_path_rejected_incoming_conflict"
        ],
        "rejected_cycle": totals["noise_aware_joint_path_rejected_cycle"],
    }
    for key in ("graphical_correct", "graphical_predicted", "graphical_labels"):
        result.pop(key)
    for key in (
        "high_reliability_correct",
        "high_reliability_predicted",
        "high_reliability_labels",
    ):
        result.pop(key)
    for key in (
        "joint_path_correct",
        "joint_path_predicted",
        "joint_path_labels",
        "joint_path_protected_selected",
        "joint_path_rejected_outgoing_conflict",
        "joint_path_rejected_incoming_conflict",
        "joint_path_rejected_cycle",
    ):
        result.pop(key)
    for key in (
        "high_reliability_in_envelope_correct",
        "high_reliability_in_envelope_predicted",
        "high_reliability_in_envelope_labels",
    ):
        result.pop(key)
    for key in (
        "strict_gate_correct",
        "strict_gate_predicted",
        "strict_gate_labels",
        "strict_gate_in_envelope_correct",
        "strict_gate_in_envelope_predicted",
        "strict_gate_in_envelope_labels",
        "noise_aware_review_correct",
        "noise_aware_review_predicted",
        "noise_aware_review_labels",
        "noise_aware_strict_correct",
        "noise_aware_strict_predicted",
        "noise_aware_strict_labels",
    ):
        result.pop(key)
    for key in (
        "noise_aware_joint_path_correct",
        "noise_aware_joint_path_predicted",
        "noise_aware_joint_path_labels",
        "noise_aware_joint_path_protected_selected",
        "noise_aware_joint_path_rejected_outgoing_conflict",
        "noise_aware_joint_path_rejected_incoming_conflict",
        "noise_aware_joint_path_rejected_cycle",
    ):
        result.pop(key)
    return result


def _precision_recall_f1(correct: int, predicted: int, labels: int) -> dict[str, float]:
    precision = correct / predicted if predicted else 0.0
    recall = correct / labels if labels else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 8), "recall": round(recall, 8), "f1": round(f1, 8)}


def _has_graphical_floating_group(annotations: list[dict[str, Any]]) -> bool:
    groups: dict[int, list[dict[str, Any]]] = {}
    for annotation in annotations:
        groups.setdefault(int(annotation.get("reading_order_id", -1)), []).append(annotation)
    return any(
        any(int(item.get("reading_order_label", 0)) == 2 for item in group)
        and any(_graphical_kind(item) is not None for item in group)
        for group in groups.values()
    )


def _graphical_relation_count(semantic: Mapping[str, Any]) -> int:
    nodes = {
        node.get("id"): node
        for node in semantic.get("document", [])
        if isinstance(node, Mapping)
    }
    return sum(
        1
        for source, target in semantic.get("ro_linkings", [])
        if nodes.get(source, {}).get("type") in {"figure", "table"}
        or nodes.get(target, {}).get("type") in {"figure", "table"}
    )


def _download_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Scriptorium/0.1"})
    with urlopen(request, timeout=180) as response:
        return response.read()


def _load_annotation_archive(
    payload: bytes,
    *,
    member: str = COMPHRDOC_ANNOTATION_MEMBER,
) -> dict[str, Any]:
    try:
        with ZipFile(BytesIO(payload)) as archive:
            raw = json.loads(archive.read(member))
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Comp-HRDoc annotation archive") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("images"), list) or not isinstance(raw.get("annotations"), list):
        raise ValueError("invalid Comp-HRDoc unified layout annotation")
    return raw


def _document_page_records(
    payload: dict[str, Any],
    document_id: str,
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    images = [
        image
        for image in payload["images"]
        if isinstance(image, dict) and str(image.get("file_name") or "").startswith(f"{document_id}_")
    ]
    images.sort(key=lambda image: _page_number_from_name(str(image["file_name"]), document_id))
    annotations_by_image: dict[int, list[dict[str, Any]]] = {}
    for annotation in payload["annotations"]:
        if isinstance(annotation, dict):
            annotations_by_image.setdefault(int(annotation.get("image_id", -1)), []).append(annotation)
    return [
        (
            image,
            sorted(
                annotations_by_image.get(int(image["id"]), []),
                key=lambda annotation: (
                    int(annotation.get("reading_order_id", 0)),
                    int(annotation.get("in_page_id", 0)),
                ),
            ),
        )
        for image in images
    ]


def _page_number_from_name(file_name: str, document_id: str) -> int:
    stem = Path(file_name).stem
    try:
        return int(stem.removeprefix(f"{document_id}_"))
    except ValueError as exc:
        raise ValueError(f"invalid Comp-HRDoc page name: {file_name}") from exc


def _render_annotated_page(page: fitz.Page, path: Path, *, width: int, height: int) -> None:
    matrix = fitz.Matrix(width / page.rect.width, height / page.rect.height)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    if pixmap.width != width or pixmap.height != height:
        raise ValueError(
            f"rendered page dimensions {pixmap.width}x{pixmap.height} do not match annotations {width}x{height}"
        )
    pixmap.save(path)


def _page_payloads(
    document_id: str,
    page_index: int,
    width: int,
    height: int,
    annotations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    annotation_node_ids: list[list[str]] = []
    edges: list[list[str]] = []
    for annotation in annotations:
        contents = annotation.get("textline_contents")
        polygons = annotation.get("textline_polys")
        block_node_ids: list[str] = []
        block_id = f"comphrdoc-p{page_index + 1:04d}-b{int(annotation.get('in_page_id', 0)) + 1:04d}"
        if isinstance(contents, list) and isinstance(polygons, list):
            for content, polygon in zip(contents, polygons, strict=False):
                bbox = _polygon_bbox(polygon)
                text = str(content or "").strip()
                if bbox is None or not text:
                    continue
                node_id = f"comphrdoc-p{page_index + 1:04d}-l{len(nodes) + 1:04d}"
                nodes.append(
                    {
                        "id": node_id,
                        "box": bbox,
                        "text": text,
                        "words": [],
                        "type": "text",
                        "block_id": block_id,
                    }
                )
                block_node_ids.append(node_id)
        graphical_kind = _graphical_kind(annotation)
        if not block_node_ids and graphical_kind is not None:
            bbox = _annotation_bbox(annotation.get("bbox"))
            if bbox is not None:
                node_id = f"comphrdoc-p{page_index + 1:04d}-l{len(nodes) + 1:04d}"
                text = f"[{graphical_kind} p{page_index + 1:04d} g{int(annotation.get('in_page_id', 0)) + 1:04d}]"
                nodes.append(
                    {
                        "id": node_id,
                        "box": bbox,
                        "text": text,
                        "words": [],
                        "type": graphical_kind,
                        "block_id": block_id,
                    }
                )
                block_node_ids.append(node_id)
        for source, target in zip(block_node_ids, block_node_ids[1:], strict=False):
            edges.append([source, target])
        annotation_node_ids.append(block_node_ids)
    floating_order_ids = {
        int(annotation.get("reading_order_id", -1))
        for annotation in annotations
        if int(annotation.get("reading_order_label", 0)) == 2
    }
    body_indices = [
        index
        for index, annotation in enumerate(annotations)
        if int(annotation.get("reading_order_id", -1)) not in floating_order_ids
    ]
    for body_position, index in enumerate(body_indices[:-1]):
        annotation = annotations[index]
        if int(annotation.get("reading_order_label", 0)) != 1:
            continue
        following_index = body_indices[body_position + 1]
        current = annotation_node_ids[index] if index < len(annotation_node_ids) else []
        following = annotation_node_ids[following_index]
        if current and following:
            edges.append([current[-1], following[0]])
    floating_groups: dict[int, list[int]] = {}
    for index, annotation in enumerate(annotations):
        floating_groups.setdefault(int(annotation.get("reading_order_id", -1)), []).append(index)
    for group_indices in floating_groups.values():
        if not any(int(annotations[index].get("reading_order_label", 0)) == 2 for index in group_indices):
            continue
        graphical_indices = [index for index in group_indices if _graphical_kind(annotations[index]) is not None]
        text_indices = [index for index in group_indices if _graphical_kind(annotations[index]) is None]
        for graphical_index in graphical_indices:
            graphical_kind = _graphical_kind(annotations[graphical_index])
            graphical = annotation_node_ids[graphical_index]
            caption_nodes = [
                node_id
                for text_index in text_indices
                for node_id in annotation_node_ids[text_index]
            ]
            if not graphical or not caption_nodes:
                continue
            if graphical_kind == "figure":
                edges.append([graphical[-1], caption_nodes[0]])
            else:
                edges.append([caption_nodes[-1], graphical[0]])
    base = {
        "uid": f"{document_id}_{page_index}",
        "img": {
            "fname": f"images/{document_id}_{page_index}.png",
            "width": width,
            "height": height,
        },
        "document": nodes,
    }
    return (
        {
            "schema": "scriptorium-comphrdoc-layout-anchor-only/v1",
            **base,
            "relations_removed": True,
        },
        {
            "schema": "scriptorium-comphrdoc-reading-order/v1",
            **base,
            "ro_linkings": edges,
            "match_mode": "ordered-subsequence",
        },
    )


def _polygon_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 8 or len(value) % 2:
        return None
    try:
        xs = [float(value[index]) for index in range(0, len(value), 2)]
        ys = [float(value[index]) for index in range(1, len(value), 2)]
    except (TypeError, ValueError, OverflowError):
        return None
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    return bbox if bbox[2] > bbox[0] and bbox[3] > bbox[1] else None


def _graphical_kind(annotation: Mapping[str, Any]) -> str | None:
    try:
        category_id = int(annotation.get("category_id"))
    except (TypeError, ValueError):
        return None
    return {1: "figure", 2: "table"}.get(category_id)


def _annotation_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 4:
        return None
    try:
        x, y, width, height = (float(item) for item in value[:4])
    except (TypeError, ValueError, OverflowError):
        return None
    if width <= 0 or height <= 0:
        return None
    return [x, y, x + width, y + height]
