from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import fitz


OPEN_DATALOADER_PACKAGE = "opendataloader-pdf"
OPEN_DATALOADER_SOURCE = "opendataloader-pdf-xycut"
OPEN_DATALOADER_LICENSE = "Apache-2.0"


@dataclass(frozen=True)
class OpenDataLoaderResult:
    raw_payload: dict[str, Any]
    structure_payload: dict[str, Any]


class OpenDataLoaderAdapter:
    """Run the optional local OpenDataLoader PDF XY-Cut provider."""

    def __init__(
        self,
        *,
        converter: Callable[..., None] | None = None,
        provider_version: str | None = None,
    ) -> None:
        self.converter = converter
        self.provider_version = provider_version

    def analyze(
        self,
        source: str | Path,
        provider_output_dir: str | Path,
        *,
        page_indices: Sequence[int] | None = None,
        max_pages: int | None = None,
        table_method: str = "default",
        include_header_footer: bool = False,
        threads: int = 1,
    ) -> OpenDataLoaderResult:
        source_path = Path(source)
        if source_path.suffix.lower() != ".pdf":
            raise ValueError("OpenDataLoader currently accepts PDF sources only")
        if page_indices is not None and max_pages is not None:
            raise ValueError("page_indices cannot be combined with max_pages")
        if table_method not in {"default", "cluster"}:
            raise ValueError("table_method must be default or cluster")
        if threads < 1:
            raise ValueError("threads must be at least 1")

        selected_indices, page_sizes = _pdf_page_sizes(
            source_path,
            page_indices=page_indices,
            max_pages=max_pages,
        )
        output_dir = Path(provider_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        converter = self.converter or self._load_converter()
        try:
            converter(
                input_path=str(source_path),
                output_dir=str(output_dir),
                format="json",
                quiet=True,
                reading_order="xycut",
                image_output="off",
                pages=_provider_page_selection(selected_indices),
                table_method=table_method,
                include_header_footer=include_header_footer,
                threads=str(threads),
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"OpenDataLoader execution failed: {exc}") from exc

        raw_path = _provider_json_path(output_dir, source_path)
        try:
            raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to read OpenDataLoader JSON: {raw_path}") from exc
        if not isinstance(raw_payload, dict):
            raise RuntimeError("OpenDataLoader JSON root must be an object")

        provider_version = self.provider_version or _installed_provider_version()
        return OpenDataLoaderResult(
            raw_payload=raw_payload,
            structure_payload=normalize_opendataloader_payload(
                raw_payload,
                page_sizes,
                provider_version=provider_version,
            ),
        )

    @staticmethod
    def _load_converter() -> Callable[..., None]:
        try:
            from opendataloader_pdf import convert  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "OpenDataLoader PDF is not installed. Install requirements-opendataloader.txt "
                "and provide Java 11+, or replay a saved --structure-json result."
            ) from exc
        return convert


def is_opendataloader_payload(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    kids = payload.get("kids")
    return (
        isinstance(kids, list)
        and "file name" in payload
        and "number of pages" in payload
    )


def normalize_opendataloader_payload(
    payload: Mapping[str, Any],
    page_sizes: Mapping[int, tuple[float, float]],
    *,
    provider_version: str = "unknown",
) -> dict[str, Any]:
    """Convert OpenDataLoader's bottom-left PDF JSON into review-only evidence."""

    if not is_opendataloader_payload(payload):
        raise ValueError("payload is not OpenDataLoader PDF JSON")
    raw_kids = payload.get("kids")
    assert isinstance(raw_kids, list)
    declared_page_count = _provider_page_count(payload)

    slots_by_page: dict[int, list[dict[str, Any] | None]] = defaultdict(list)
    skipped_reasons: Counter[str] = Counter()
    for raw_item in raw_kids:
        page_index = _provider_page_index(raw_item)
        if page_index is None:
            skipped_reasons["missing-page-number"] += 1
            continue
        if declared_page_count is not None and page_index >= declared_page_count:
            raise ValueError(
                f"OpenDataLoader emitted source page {page_index + 1} "
                f"beyond its declared {declared_page_count} pages"
            )
        if page_index not in page_sizes:
            skipped_reasons["out-of-scope-page"] += 1
            continue
        page_slots = slots_by_page[page_index]
        block_order = len(page_slots) + 1
        page_slots.append(
            _normalize_provider_block(
                raw_item,
                page_index=page_index,
                block_order=block_order,
                page_size=page_sizes[page_index],
                skipped_reasons=skipped_reasons,
            )
        )

    pages: list[dict[str, Any]] = []
    relation_count = 0
    normalized_count = 0
    for page_index, slots in sorted(slots_by_page.items()):
        elements = [block for block in slots if block is not None]
        successor_edges: list[dict[str, Any]] = []
        for source_block, target_block in zip(slots, slots[1:], strict=False):
            if source_block is None or target_block is None:
                continue
            successor_edges.append(
                {
                    "source": source_block["id"],
                    "target": target_block["id"],
                    "kind": "successor",
                    "review_required": True,
                    "relation_policy": "review-only",
                    "provider": OPEN_DATALOADER_SOURCE,
                }
            )
        relation_count += len(successor_edges)
        normalized_count += len(elements)
        pages.append(
            {
                "page_index": page_index,
                "source_page_number": page_index + 1,
                "coordinate_origin": "TOPLEFT",
                "relation_policy": "review-only",
                "elements": elements,
                "successor_edges": successor_edges,
            }
        )

    return {
        "source": OPEN_DATALOADER_SOURCE,
        "model": "opendataloader-pdf/xycut",
        "provider_version": f"opendataloader-pdf-{provider_version}",
        "backend": "deterministic-xycut",
        "order_policy": "review-only",
        "relation_policy": "review-only",
        "semantic_policy": "review-only",
        "runtime_reorder": False,
        "provider_code_license": OPEN_DATALOADER_LICENSE,
        "input_file_name": str(payload.get("file name") or ""),
        "normalization": {
            "input_block_count": len(raw_kids),
            "normalized_block_count": normalized_count,
            "skipped_block_count": sum(skipped_reasons.values()),
            "skipped_reason_counts": dict(sorted(skipped_reasons.items())),
            "review_relation_edge_count": relation_count,
        },
        "pages": pages,
    }


def _normalize_provider_block(
    raw_item: Any,
    *,
    page_index: int,
    block_order: int,
    page_size: tuple[float, float],
    skipped_reasons: Counter[str],
) -> dict[str, Any] | None:
    if not isinstance(raw_item, Mapping):
        skipped_reasons["non-object-block"] += 1
        return None
    bbox = _provider_bbox(raw_item.get("bounding box"), page_size=page_size)
    if bbox is None:
        skipped_reasons["invalid-bounding-box"] += 1
        return None
    page_number = page_index + 1
    block_id = f"opendataloader-p{page_number:04d}-b{block_order:04d}"
    block: dict[str, Any] = {
        "id": block_id,
        "block_label": _provider_label(raw_item),
        "block_content": str(raw_item.get("content") or "").strip(),
        "block_order": block_order,
        "bbox_pdf": bbox,
        "coordinate_space": "pdf",
        "order_policy": "review-only",
        "semantic_policy": "review-only",
        "provider_type": str(raw_item.get("type") or "unknown"),
    }
    for source_key, target_key in (
        ("id", "provider_id"),
        ("pdfua_tag", "provider_pdfua_tag"),
        ("heading level", "provider_heading_level"),
        ("level", "provider_level"),
        ("source", "provider_source"),
    ):
        value = raw_item.get(source_key)
        if value is not None:
            block[target_key] = value
    return block


def _provider_page_index(raw_item: Any) -> int | None:
    if not isinstance(raw_item, Mapping):
        return None
    try:
        page_number = int(raw_item.get("page number"))
    except (TypeError, ValueError):
        return None
    return page_number - 1 if page_number > 0 else None


def _provider_page_count(payload: Mapping[str, Any]) -> int | None:
    try:
        page_count = int(payload.get("number of pages"))
    except (TypeError, ValueError):
        return None
    return page_count if page_count >= 0 else None


def _provider_bbox(
    value: Any,
    *,
    page_size: tuple[float, float],
) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, bottom_y0, x1, bottom_y1 = (float(item) for item in value)
    except (TypeError, ValueError, OverflowError):
        return None
    width, height = page_size
    if x1 <= x0 or bottom_y1 <= bottom_y0:
        return None
    left = min(max(x0, 0.0), width)
    right = min(max(x1, 0.0), width)
    top = min(max(height - bottom_y1, 0.0), height)
    bottom = min(max(height - bottom_y0, 0.0), height)
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _provider_label(raw_item: Mapping[str, Any]) -> str:
    raw_type = str(raw_item.get("type") or "unknown").strip().lower().replace("-", " ")
    normalized_type = "_".join(raw_type.split())
    if normalized_type == "heading":
        level = str(raw_item.get("level") or "").strip().lower()
        try:
            heading_level = int(raw_item.get("heading level"))
        except (TypeError, ValueError):
            heading_level = 0
        return "doc_title" if level == "doctitle" or heading_level == 1 else "section_header"
    return {
        "image": "figure",
        "table_cell": "table_cell",
        "table_row": "table",
    }.get(normalized_type, normalized_type or "unknown")


def _pdf_page_sizes(
    source: Path,
    *,
    page_indices: Sequence[int] | None,
    max_pages: int | None,
) -> tuple[tuple[int, ...] | None, dict[int, tuple[float, float]]]:
    with fitz.open(source) as document:
        if page_indices is not None:
            selected = tuple(int(index) for index in page_indices)
            invalid = [index for index in selected if index < 0 or index >= document.page_count]
            if invalid:
                raise ValueError(f"source page index is out of range: {invalid[0]}")
        elif max_pages is not None:
            selected = tuple(range(min(max_pages, document.page_count)))
        else:
            selected = None
        indices = selected if selected is not None else tuple(range(document.page_count))
        page_sizes = {
            index: (float(document[index].rect.width), float(document[index].rect.height))
            for index in indices
        }
    return selected, page_sizes


def _provider_page_selection(page_indices: Sequence[int] | None) -> str | None:
    if page_indices is None:
        return None
    return ",".join(str(int(index) + 1) for index in page_indices)


def _provider_json_path(output_dir: Path, source: Path) -> Path:
    expected = output_dir / f"{source.stem}.json"
    if expected.is_file():
        return expected
    candidates = sorted(output_dir.glob("*.json"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"OpenDataLoader produced {len(candidates)} JSON files in {output_dir}; expected one"
        )
    return candidates[0]


def _installed_provider_version() -> str:
    try:
        return version(OPEN_DATALOADER_PACKAGE)
    except PackageNotFoundError:
        return "unknown"
