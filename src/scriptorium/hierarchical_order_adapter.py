from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .hierarchical_order import (
    MAX_HIERARCHY_ELEMENTS,
    MAX_HIERARCHY_REGIONS,
    PROVIDER_COARSE_REGION_SOURCE,
)
from .models import BBox, DocumentIR, ElementIR
from .reading_order_sidecar import SIDECAR_SCHEMA_NAME
from .structure_evidence import StructureRegion, normalize_structure_evidence


HIERARCHY_INPUT_ADAPTER_SCHEMA = "scriptorium-document-structure-hierarchy-input/v1"
FINE_ELEMENT_SELECTION_POLICY = "visible-nonempty-document-text-v1"
COARSE_REGION_SELECTION_POLICY = "provider-block-provenance-v1"
DEFAULT_MIN_EXPLICIT_REFERENCE_COVERAGE = 0.5

_COARSE_BLOCK_LIST_KEYS = frozenset(
    {
        "blocks",
        "child_blocks",
        "children",
        "elements",
        "items",
        "layout_det_res.boxes",
        "parsing_res_list",
        "sub_blocks",
        "sub_regions",
    }
)
_SPECIALIZED_BLOCK_LIST_KEYS = frozenset({"formula_res_list"})
_FINE_LIST_KEYS = frozenset(
    {
        "document",
        "overall_ocr_res",
        "seal_res_list",
        "table_res_list.table_cells",
        "text_paragraphs_ocr_res",
    }
)
_FINE_LABELS = frozenset({"table_cell", "table-cell"})
_ANSWER_LIKE_REGION_ORDER_KEYS = frozenset(
    {
        "reading_order",
        "reading_order_id",
        "reading_order_index",
        "order_index",
    }
)
_REGION_REFERENCE_KEYS = (
    "block_id",
    "region_id",
    "layout_region_id",
    "formula_region_id",
    "seal_region_id",
    "id",
    "docling_ref",
    "self_ref",
    "external_structure_table_ref",
)
_ELEMENT_REFERENCE_COMPATIBILITY = {
    "block_id": ("block_id",),
    "region_id": ("region_id", "id"),
    "layout_region_id": ("layout_region_id", "region_id", "id"),
    "formula_region_id": ("formula_region_id", "region_id", "id"),
    "seal_region_id": ("seal_region_id", "region_id", "id"),
    "docling_ref": ("docling_ref", "self_ref"),
    "self_ref": ("self_ref", "docling_ref"),
    "external_structure_table_ref": ("external_structure_table_ref",),
    "parent_id": (
        "id",
        "block_id",
        "region_id",
        "layout_region_id",
        "formula_region_id",
        "seal_region_id",
    ),
    "parent_ref": ("docling_ref", "self_ref", "id"),
}


@dataclass(frozen=True)
class HierarchyInputAdapterResult:
    payload: dict[str, Any]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class _SelectedRegion:
    id: str
    region: StructureRegion
    selection_reason: str
    provider_reference: str | None


def build_fine_hierarchy_input_from_document(
    document: DocumentIR,
    *,
    page_index: int = 0,
    sample_id: str | None = None,
) -> HierarchyInputAdapterResult:
    """Export answer-free fine-line hierarchy input without provider regions.

    Graph heads only consume fine elements, page geometry, and text. This path
    lets real PDF/image pages enter paragraph/successor prediction before any
    structure JSON is available. Regions remain empty by design.
    """

    from .hierarchical_order_benchmark import HIERARCHY_INPUT_SCHEMA

    if page_index < 0:
        raise ValueError("page_index must be non-negative")
    page = next(
        (candidate for candidate in document.pages if candidate.page_index == page_index),
        None,
    )
    if page is None:
        available = sorted(candidate.page_index for candidate in document.pages)
        raise ValueError(
            f"DocumentIR has no page_index {page_index}; available page indices: "
            f"{available}"
        )

    fine_rejected = Counter[str]()
    fine_elements: list[ElementIR] = []
    for element in page.elements:
        reason = _fine_element_rejection_reason(element)
        if reason is None:
            fine_elements.append(element)
        else:
            fine_rejected[reason] += 1
    if not fine_elements:
        raise ValueError(
            "DocumentIR page has no visible non-empty text elements for the fine layer"
        )
    if len(fine_elements) > MAX_HIERARCHY_ELEMENTS:
        raise ValueError(
            f"adapted hierarchy cannot exceed {MAX_HIERARCHY_ELEMENTS} fine elements; "
            f"page {page_index} has {len(fine_elements)}"
        )

    elements = [
        {
            "id": element.id,
            "box": element.bbox_pdf.as_list(),
            "role": _element_role(element),
            "text": element.source_text,
        }
        for element in fine_elements
    ]
    payload = {
        "schema": HIERARCHY_INPUT_SCHEMA,
        "id": sample_id or f"{document.id or 'document'}-page-{page_index}",
        "page_index": page_index,
        "width": float(page.width_pt),
        "height": float(page.height_pt),
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": elements,
        "regions": [],
        "source": {
            "document_id": document.id,
            "source": document.source,
            "source_type": document.source_type,
            "page_index": page_index,
            "adapter": "fine-only-document-ir",
        },
    }
    diagnostics = {
        "schema": HIERARCHY_INPUT_ADAPTER_SCHEMA,
        "adapter": "fine-only-document-ir",
        "page_index": page_index,
        "fine_element_count": len(elements),
        "fine_element_selection_policy": FINE_ELEMENT_SELECTION_POLICY,
        "rejected_fine_element_counts": dict(sorted(fine_rejected.items())),
        "selected_coarse_region_count": 0,
        "rejected_region_count": 0,
        "provider_regions_required": False,
    }
    return HierarchyInputAdapterResult(payload=payload, diagnostics=diagnostics)


def build_hierarchy_input_from_document(
    document: DocumentIR,
    structure_payload: Any,
    *,
    page_index: int = 0,
    source: str | None = None,
    min_explicit_reference_coverage: float = DEFAULT_MIN_EXPLICIT_REFERENCE_COVERAGE,
) -> HierarchyInputAdapterResult:
    """Adapt one IR page and provider structure into a granularity-safe input.

    Provider sequence and relation fields are deliberately not copied. The
    adapter consumes only page-local block geometry, role provenance, and
    explicit parent identifiers; the hierarchy builder remains responsible for
    proposing order.
    """

    if page_index < 0:
        raise ValueError("page_index must be non-negative")
    if not 0.0 < min_explicit_reference_coverage <= 1.0:
        raise ValueError("min_explicit_reference_coverage must be in (0, 1]")
    _reject_reading_order_sidecar(structure_payload)
    page = next(
        (candidate for candidate in document.pages if candidate.page_index == page_index),
        None,
    )
    if page is None:
        available = sorted(candidate.page_index for candidate in document.pages)
        raise ValueError(
            f"DocumentIR has no page_index {page_index}; available page indices: "
            f"{available}"
        )

    fine_rejected = Counter[str]()
    fine_elements: list[ElementIR] = []
    for element in page.elements:
        reason = _fine_element_rejection_reason(element)
        if reason is None:
            fine_elements.append(element)
        else:
            fine_rejected[reason] += 1
    if not fine_elements:
        raise ValueError(
            "DocumentIR page has no visible non-empty text elements for the fine layer"
        )
    if len(fine_elements) > MAX_HIERARCHY_ELEMENTS:
        raise ValueError(
            f"adapted hierarchy cannot exceed {MAX_HIERARCHY_ELEMENTS} fine elements; "
            f"page {page_index} has {len(fine_elements)}"
        )

    normalized_regions = [
        region
        for region in normalize_structure_evidence(
            structure_payload,
            document,
            source=source,
        )
        if region.page_index == page_index
    ]
    declared_coarse = _declares_coarse_regions(structure_payload)
    rejected_regions = Counter[str]()
    selected_reason_counts = Counter[str]()
    selected_by_id: dict[str, _SelectedRegion] = {}
    duplicate_count = 0
    for region in normalized_regions:
        selection_reason, rejection_reason = _coarse_region_decision(
            region,
            declared_coarse=declared_coarse,
        )
        if rejection_reason is not None:
            rejected_regions[rejection_reason] += 1
            continue
        assert selection_reason is not None
        region_id = _stable_region_id(region)
        selected = _SelectedRegion(
            id=region_id,
            region=region,
            selection_reason=selection_reason,
            provider_reference=_provider_reference(region.raw),
        )
        if region_id in selected_by_id:
            duplicate_count += 1
            continue
        selected_by_id[region_id] = selected
        selected_reason_counts[selection_reason] += 1
    if duplicate_count:
        rejected_regions["duplicate-coarse-region"] += duplicate_count
    selected_regions = tuple(
        sorted(selected_by_id.values(), key=lambda item: item.id)
    )
    if not selected_regions:
        reason_summary = ", ".join(
            f"{reason}={count}" for reason, count in sorted(rejected_regions.items())
        )
        raise ValueError(
            "structure JSON contains no coarse provider regions after granularity "
            f"filtering ({reason_summary or 'no page-local regions'})"
        )
    if len(selected_regions) > MAX_HIERARCHY_REGIONS:
        raise ValueError(
            f"adapted hierarchy cannot exceed {MAX_HIERARCHY_REGIONS} coarse regions; "
            f"page {page_index} has {len(selected_regions)}"
        )

    explicit_members, reference_diagnostics = _explicit_memberships(
        fine_elements,
        selected_regions,
        min_coverage=min_explicit_reference_coverage,
    )
    provider_source_counts = Counter(
        selected.region.source for selected in selected_regions
    )
    provider_order_source_counts = Counter(
        selected.region.order_source or "none" for selected in selected_regions
    )
    region_provenance = [
        {
            "region_id": selected.id,
            "provider": selected.region.source,
            "provider_reference": selected.provider_reference,
            "provider_order_source": selected.region.order_source,
            "selection_reason": selected.selection_reason,
            "label": selected.region.label,
            "box": selected.region.bbox_pdf.as_list(),
        }
        for selected in selected_regions
    ]
    diagnostics: dict[str, Any] = {
        "fine_element_count": len(fine_elements),
        "fine_rejected_element_count": sum(fine_rejected.values()),
        "fine_rejected_reason_counts": dict(sorted(fine_rejected.items())),
        "normalized_page_region_count": len(normalized_regions),
        "selected_coarse_region_count": len(selected_regions),
        "rejected_region_count": sum(rejected_regions.values()),
        "selected_region_reason_counts": dict(sorted(selected_reason_counts.items())),
        "rejected_region_reason_counts": dict(sorted(rejected_regions.items())),
        "provider_source_counts": dict(sorted(provider_source_counts.items())),
        "provider_order_source_counts": dict(
            sorted(provider_order_source_counts.items())
        ),
        "declared_coarse_region_granularity": declared_coarse,
        "explicit_reference_membership_count": sum(
            len(member_ids) for member_ids in explicit_members.values()
        ),
        "explicit_reference_ambiguous_count": reference_diagnostics[
            "ambiguous_count"
        ],
        "explicit_reference_geometry_conflict_count": reference_diagnostics[
            "geometry_conflict_count"
        ],
        "min_explicit_reference_coverage": round(
            min_explicit_reference_coverage,
            8,
        ),
    }
    adapter_metadata = {
        "schema": HIERARCHY_INPUT_ADAPTER_SCHEMA,
        "coarse_region_source": PROVIDER_COARSE_REGION_SOURCE,
        "fine_element_policy": FINE_ELEMENT_SELECTION_POLICY,
        "coarse_region_policy": COARSE_REGION_SELECTION_POLICY,
        "provider_sequence_policy": "stripped-before-hierarchy-proposal",
        "provider_relation_policy": "ignored-by-hierarchy-input-adapter",
        "source_document_id": document.id,
        "source_document_type": document.source_type,
        "source_document_sha256": _canonical_sha256(
            document.model_dump(mode="json")
        ),
        "source_structure_sha256": _canonical_sha256(structure_payload),
        "page_index": page_index,
        "diagnostics": diagnostics,
        "region_provenance": region_provenance,
    }
    payload = {
        "id": f"{document.id}-page-{page_index + 1:04d}",
        "page_index": page_index,
        "width": float(page.width_pt),
        "height": float(page.height_pt),
        "element_granularity": "fine",
        "region_granularity": "coarse",
        "elements": [
            {
                "id": element.id,
                "box": element.bbox_pdf.as_list(),
                "role": _element_role(element),
                "text": element.source_text,
            }
            for element in fine_elements
        ],
        "regions": [
            {
                "id": selected.id,
                "box": selected.region.bbox_pdf.as_list(),
                "role": selected.region.label,
                "text": selected.region.text,
                **(
                    {"member_ids": sorted(explicit_members[selected.id])}
                    if explicit_members[selected.id]
                    else {}
                ),
            }
            for selected in selected_regions
        ],
        "input_adapter": adapter_metadata,
    }
    return HierarchyInputAdapterResult(payload, diagnostics)


def _fine_element_rejection_reason(element: ElementIR) -> str | None:
    if not element.visibility:
        return "hidden-element"
    if element.metadata.get("image_source_visual_layer") is True:
        return "source-visual-layer"
    if element.bbox_pdf.width <= 0 or element.bbox_pdf.height <= 0:
        return "empty-geometry"
    if not element.source_text.strip():
        return "empty-text"
    return None


def _coarse_region_decision(
    region: StructureRegion,
    *,
    declared_coarse: bool,
) -> tuple[str | None, str | None]:
    raw = region.raw
    list_key = str(raw.get("_scriptorium_structure_list_key") or "").strip()
    label = str(region.label or "").strip().lower().replace(" ", "_")
    if raw.get("_scriptorium_sidecar_reference") is True:
        return None, "sidecar-reference"
    if label in _FINE_LABELS or list_key == "table_res_list.table_cells":
        return None, "fine-table-cell"
    if "paddle_text_index" in raw or list_key in _FINE_LIST_KEYS:
        return None, "fine-ocr-or-document-element"
    if _ANSWER_LIKE_REGION_ORDER_KEYS & set(raw):
        return None, "answer-like-region-order-field"
    if region.order_source in {"docling-body", "docling-furniture"}:
        return "docling-document-block", None
    if list_key == "layout_det_res.boxes":
        return "layout-detector-block", None
    if list_key in _COARSE_BLOCK_LIST_KEYS:
        return "provider-block-list", None
    if list_key in _SPECIALIZED_BLOCK_LIST_KEYS:
        return "provider-specialized-block", None
    if "block_order" in raw:
        return "explicit-provider-block", None
    if declared_coarse:
        return "declared-coarse-region", None
    return None, "missing-coarse-provenance"


def _explicit_memberships(
    elements: Sequence[ElementIR],
    regions: Sequence[_SelectedRegion],
    *,
    min_coverage: float,
) -> tuple[dict[str, set[str]], dict[str, int]]:
    reference_index: dict[tuple[str, str], set[str]] = defaultdict(set)
    region_by_id = {region.id: region for region in regions}
    for selected in regions:
        for key in _REGION_REFERENCE_KEYS:
            value = _scalar_reference(selected.region.raw.get(key))
            if value is not None:
                reference_index[(key, value)].add(selected.id)

    members: dict[str, set[str]] = {region.id: set() for region in regions}
    ambiguous_count = 0
    geometry_conflict_count = 0
    for element in elements:
        candidates: set[str] = set()
        for element_key, region_keys in _ELEMENT_REFERENCE_COMPATIBILITY.items():
            value = _scalar_reference(element.metadata.get(element_key))
            if value is None:
                continue
            for region_key in region_keys:
                candidates.update(reference_index.get((region_key, value), set()))
        if len(candidates) > 1:
            ambiguous_count += 1
            continue
        if not candidates:
            continue
        region_id = next(iter(candidates))
        coverage = _bbox_coverage(
            element.bbox_pdf,
            region_by_id[region_id].region.bbox_pdf,
        )
        if coverage < min_coverage:
            geometry_conflict_count += 1
            continue
        members[region_id].add(element.id)
    return members, {
        "ambiguous_count": ambiguous_count,
        "geometry_conflict_count": geometry_conflict_count,
    }


def _declares_coarse_regions(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    values = (
        payload.get("region_granularity"),
        payload.get("structure_granularity"),
        payload.get("element_granularity"),
    )
    return any(str(value or "").strip().lower() == "coarse" for value in values)


def _reject_reading_order_sidecar(payload: Any) -> None:
    pending: list[Any] = [payload]
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            if id(value) in seen:
                continue
            seen.add(id(value))
            if str(value.get("schema_name") or "").strip() == SIDECAR_SCHEMA_NAME:
                raise ValueError(
                    "hierarchy input adapter does not accept reading-order sidecars"
                )
            pending.extend(value.values())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if id(value) in seen:
                continue
            seen.add(id(value))
            pending.extend(value)


def _stable_region_id(region: StructureRegion) -> str:
    fingerprint = {
        "page_index": region.page_index,
        "provider": region.source,
        "provider_reference": _provider_reference(region.raw),
        "label": region.label,
        "box": [round(value, 6) for value in region.bbox_pdf.as_list()],
    }
    digest = _canonical_sha256(fingerprint)[:16]
    provider = re.sub(r"[^a-z0-9]+", "-", region.source.lower()).strip("-")
    provider = provider[:28] or "provider"
    return f"region-p{region.page_index + 1:04d}-{provider}-{digest}"


def _provider_reference(raw: Mapping[str, Any]) -> str | None:
    for key in _REGION_REFERENCE_KEYS:
        value = _scalar_reference(raw.get(key))
        if value is not None:
            return f"{key}:{value}"
    return None


def _scalar_reference(value: Any) -> str | None:
    if value is None or isinstance(value, (Mapping, list, tuple, set)):
        return None
    normalized = str(value).strip()
    return normalized or None


def _element_role(element: ElementIR) -> str:
    return str(
        element.metadata.get("role")
        or element.metadata.get("block_label")
        or element.type
        or "unknown"
    )


def _bbox_coverage(subject: BBox, container: BBox) -> float:
    intersection_width = max(
        0.0,
        min(subject.x1, container.x1) - max(subject.x0, container.x0),
    )
    intersection_height = max(
        0.0,
        min(subject.y1, container.y1) - max(subject.y0, container.y0),
    )
    area = subject.width * subject.height
    return (intersection_width * intersection_height) / area if area > 0 else 0.0


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
