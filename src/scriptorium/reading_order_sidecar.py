from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .models import DocumentIR, ElementIR, PageIR


SIDECAR_SCHEMA_NAME = "ScriptoriumReadingOrderSidecar"
SIDECAR_SCHEMA_VERSION = "1.0"
SIDECAR_PROPOSAL_STATUS = "proposal"
SIDECAR_SOURCE = "scriptorium-local-stream-proposal-v1"


STREAMABLE_EXTERNAL_BLOCK_LABELS = frozenset(
    {
        "abstract",
        "body",
        "body_text",
        "card",
        "card_grid",
        "code",
        "content_card",
        "content_grid",
        "doc_title",
        "equation",
        "formula",
        "grid",
        "grid_area",
        "grid_block",
        "list",
        "list_item",
        "menu_grid",
        "nav_grid",
        "paragraph",
        "paragraph_text",
        "paragraph_title",
        "product",
        "product_card",
        "product_grid",
        "reference",
        "references",
        "section_header",
        "section_title",
        "table",
        "table_body",
        "table_cell",
        "table_content",
        "text",
        "text_block",
        "tile",
        "tile_grid",
        "title",
    }
)

GENERIC_EXTERNAL_TEXT_BLOCK_LABELS = frozenset(
    {
        "abstract",
        "body",
        "body_text",
        "paragraph",
        "paragraph_text",
        "reference",
        "references",
        "text",
        "text_block",
    }
)


def propose_reading_order_sidecar(document: DocumentIR) -> dict[str, Any]:
    """Build a reviewable local reading-stream sidecar from an annotated IR.

    The result intentionally models only confident *local* successor chains.
    It does not invent a page-wide sequence across columns, captions, tables,
    and sidebars. Cross-stream handoffs are emitted as review records instead
    of executable precedence constraints, so a generated proposal cannot be
    mistaken for human or model-confirmed semantic ground truth.
    """

    pages: list[dict[str, Any]] = []
    summary: Counter[str] = Counter()
    stream_type_counts: Counter[str] = Counter()
    stream_origin_counts: Counter[str] = Counter()
    for page in sorted(document.pages, key=lambda item: item.page_index):
        page_payload, page_summary = _propose_page(page)
        pages.append(page_payload)
        summary.update(
            {
                key: _int(page_summary.get(key))
                for key in (
                    "stream_count",
                    "member_count",
                    "successor_edge_count",
                    "review_successor_edge_count",
                    "review_transition_count",
                )
            }
        )
        stream_type_counts.update(_count_mapping(page_summary.get("stream_type_counts")))
        stream_origin_counts.update(_count_mapping(page_summary.get("stream_origin_counts")))

    return {
        "schema_name": SIDECAR_SCHEMA_NAME,
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "sidecar_status": SIDECAR_PROPOSAL_STATUS,
        "source": SIDECAR_SOURCE,
        "source_document": {
            "id": document.id,
            "source": document.source,
            "source_type": document.source_type,
            "page_count": document.page_count,
        },
        "review_policy": {
            "acceptance_required": True,
            "accepted_status": "accepted",
            "cross_stream_relations": "review_only",
            "description": (
                "Local successor edges are inferred from existing selected order and structural metadata. "
                "Review edge confidence and cross-stream transitions before changing sidecar_status to accepted."
            ),
        },
        "summary": {
            "page_count": len(pages),
            "stream_count": int(summary["stream_count"]),
            "member_count": int(summary["member_count"]),
            "successor_edge_count": int(summary["successor_edge_count"]),
            "review_successor_edge_count": int(summary["review_successor_edge_count"]),
            "review_transition_count": int(summary["review_transition_count"]),
            "stream_type_counts": dict(sorted(stream_type_counts.items())),
            "stream_origin_counts": dict(sorted(stream_origin_counts.items())),
        },
        "pages": pages,
    }


def write_reading_order_sidecar(document: DocumentIR, path: str | Path) -> dict[str, Any]:
    """Write a reviewable reading-order proposal and return its payload."""

    payload = propose_reading_order_sidecar(document)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def reading_order_sidecar_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return normalized proposal counters for benchmark reporting."""

    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        summary = {}
    return {
        "stream_count": _int(summary.get("stream_count")),
        "member_count": _int(summary.get("member_count")),
        "successor_edge_count": _int(summary.get("successor_edge_count")),
        "review_successor_edge_count": _int(summary.get("review_successor_edge_count")),
        "review_transition_count": _int(summary.get("review_transition_count")),
        "stream_type_counts": _count_mapping(summary.get("stream_type_counts")),
        "stream_origin_counts": _count_mapping(summary.get("stream_origin_counts")),
    }


def is_unaccepted_reading_order_sidecar(payload: Any) -> bool:
    """Return whether a Scriptorium-generated sidecar is still a proposal."""

    if not isinstance(payload, Mapping):
        return False
    if str(payload.get("schema_name") or "").strip() != SIDECAR_SCHEMA_NAME:
        return False
    return str(payload.get("sidecar_status") or "").strip().lower() == SIDECAR_PROPOSAL_STATUS


def _propose_page(page: PageIR) -> tuple[dict[str, Any], dict[str, Any]]:
    elements = _ordered_text_elements(page.elements)
    structure_block_details = _external_structure_block_details(elements)
    flow_segment_stream_ids = _flow_segment_partition_stream_ids(elements, structure_block_details)
    stream_members: dict[str, list[_ProposalMember]] = defaultdict(list)
    stream_details: dict[str, _StreamDetails] = {}
    member_by_element_id: dict[str, _ProposalMember] = {}

    for element in elements:
        details = structure_block_details.get(element.id) or _stream_details(
            element,
            flow_segment_stream_ids=flow_segment_stream_ids,
        )
        stream_id = details.stream_id
        member = _ProposalMember(element=element, stream_id=stream_id, details=details)
        stream_members[stream_id].append(member)
        stream_details.setdefault(stream_id, details)
        member_by_element_id[element.id] = member

    streams: list[dict[str, Any]] = []
    summary: Counter[str] = Counter()
    stream_type_counts: Counter[str] = Counter()
    stream_origin_counts: Counter[str] = Counter()
    for stream_id, members in sorted(
        stream_members.items(),
        key=lambda item: (_element_order_key(item[1][0].element), item[0]),
    ):
        details = stream_details[stream_id]
        stream_payload, stream_summary = _propose_stream(stream_id, details, members)
        streams.append(stream_payload)
        summary.update(stream_summary)
        stream_type_counts[details.stream_type] += 1
        stream_origin_counts[details.origin] += 1

    transitions = _review_transitions(elements, member_by_element_id)
    summary["review_transition_count"] = len(transitions)
    page_summary: dict[str, Any] = dict(summary)
    page_summary["stream_type_counts"] = dict(stream_type_counts)
    page_summary["stream_origin_counts"] = dict(stream_origin_counts)
    return (
        {
            "page_index": page.page_index,
            "document": [_sidecar_element(element) for element in elements],
            "reading_streams": streams,
            "review_transitions": transitions,
        },
        page_summary,
    )


def _ordered_text_elements(elements: Iterable[ElementIR]) -> list[ElementIR]:
    return sorted(
        (element for element in elements if element.source_text.strip()),
        key=_element_order_key,
    )


def _element_order_key(element: ElementIR) -> tuple[int, float, float, str]:
    return (int(element.reading_order), element.bbox_pdf.y0, element.bbox_pdf.x0, element.id)


def _sidecar_element(element: ElementIR) -> dict[str, Any]:
    metadata = element.metadata
    return {
        "id": element.id,
        "text": element.source_text,
        "type": "text",
        "bbox_pdf": element.bbox_pdf.as_list(),
        "_scriptorium_sidecar_reference": True,
        "review": {
            "bbox_pdf": element.bbox_pdf.as_list(),
            "reading_order": element.reading_order,
            "stream_id": _text(metadata.get("reading_order_stream_id")) or "body-main",
            "stream_type": _text(metadata.get("reading_order_stream_type")) or "body",
            "reading_order_confidence": _confidence(element),
            "reading_order_strategy": _text(metadata.get("reading_order_strategy")) or "unknown",
        },
    }


def _stream_details(
    element: ElementIR,
    *,
    flow_segment_stream_ids: set[str],
) -> "_StreamDetails":
    metadata = element.metadata
    stream_type = _text(metadata.get("reading_order_stream_type")) or "body"
    stream_id = _text(metadata.get("reading_order_stream_id")) or "body-main"
    explicit_structure_stream = _has_explicit_structure_stream(metadata, stream_id)
    if stream_type != "body" or explicit_structure_stream:
        return _StreamDetails(
            stream_id=stream_id,
            stream_type=stream_type,
            origin="existing-structure" if explicit_structure_stream else "existing-local",
            evidence=("preserve-existing-stream",),
        )

    column_count = _int(metadata.get("column_count"), default=1)
    column_index = _optional_int(metadata.get("column_index"))
    flow_segment = _int(metadata.get("flow_segment_index"), default=1)
    column_span = _text(metadata.get("column_span"))
    if column_count >= 2 and column_index is not None and not _is_full_width_span(column_span):
        return _StreamDetails(
            stream_id=_partition_stream_id("column-partition", flow_segment, column_index),
            stream_type="body",
            origin="column-partition",
            evidence=("column-local-stream", f"flow-segment-{flow_segment:03d}"),
            flow_segment=flow_segment,
            column_index=column_index,
        )
    if column_count >= 2 and _is_full_width_span(column_span):
        return _StreamDetails(
            stream_id=_partition_stream_id("full-width-partition", flow_segment, None),
            stream_type="body",
            origin="full-width-partition",
            evidence=("full-width-local-stream", f"flow-segment-{flow_segment:03d}"),
            flow_segment=flow_segment,
        )
    if stream_id in flow_segment_stream_ids:
        return _StreamDetails(
            stream_id=_flow_segment_stream_id(stream_id, flow_segment),
            stream_type="body",
            origin="flow-segment-partition",
            evidence=("flow-segment-local-stream", f"flow-segment-{flow_segment:03d}"),
            flow_segment=flow_segment,
        )
    return _StreamDetails(
        stream_id=stream_id,
        stream_type="body",
        origin="existing-body",
        evidence=("preserve-body-stream",),
    )


def _partition_stream_id(origin: str, flow_segment: int, column_index: int | None) -> str:
    segment_prefix = "body" if flow_segment == 1 else f"body-segment-{flow_segment:03d}"
    if origin == "column-partition":
        return f"{segment_prefix}-column-{(column_index or 0) + 1:03d}"
    return f"{segment_prefix}-full-width"


def _flow_segment_partition_stream_ids(
    elements: list[ElementIR],
    structure_block_details: Mapping[str, "_StreamDetails"],
) -> set[str]:
    by_stream: dict[str, list[ElementIR]] = defaultdict(list)
    for element in elements:
        if element.id in structure_block_details:
            continue
        metadata = element.metadata
        stream_type = _text(metadata.get("reading_order_stream_type")) or "body"
        stream_id = _text(metadata.get("reading_order_stream_id")) or "body-main"
        if stream_type != "body" or _has_explicit_structure_stream(metadata, stream_id):
            continue
        if _int(metadata.get("column_count"), default=1) >= 2:
            continue
        by_stream[stream_id].append(element)

    return {
        stream_id
        for stream_id, members in by_stream.items()
        if len({_int(member.metadata.get("flow_segment_index"), default=1) for member in members}) >= 2
        and any(_has_structural_flow_segment_signal(member) for member in members)
    }


def _flow_segment_stream_id(base_stream_id: str, flow_segment: int) -> str:
    if base_stream_id == "body-main":
        return f"body-segment-{flow_segment:03d}"
    return f"{base_stream_id}-flow-{flow_segment:03d}"


def _has_structural_flow_segment_signal(element: ElementIR) -> bool:
    metadata = element.metadata
    strategy = _text(metadata.get("reading_order_strategy"))
    if "mixed-" in strategy or "recursive-xy-cut" in strategy or "external-structure" in strategy:
        return True
    evidence = metadata.get("reading_order_evidence")
    if isinstance(evidence, list):
        return any(
            _text(value)
            in {
                "full-width-flow-break",
                "recursive-xy-cut",
                "table-island-row-major",
                "grid-island-row-major",
            }
            for value in evidence
        )
    return False


def _external_structure_block_details(elements: list[ElementIR]) -> dict[str, "_StreamDetails"]:
    groups: dict[tuple[str, str, tuple[float, float, float, float]], list[ElementIR]] = defaultdict(list)
    for element in elements:
        signature = _external_structure_block_signature(element)
        if signature is not None:
            groups[signature].append(element)

    grouped_items = [
        (signature, members)
        for signature, members in groups.items()
        if len(members) >= 2
    ]
    grouped_items.sort(
        key=lambda item: (
            _element_order_key(min(item[1], key=_element_order_key)),
            item[0][0],
            item[0][1],
            item[0][2],
        )
    )
    stream_numbers: Counter[str] = Counter()
    details_by_element_id: dict[str, _StreamDetails] = {}
    for (_source, label, _bbox), members in grouped_items:
        stream_type = _external_block_stream_type(label)
        stream_numbers[stream_type] += 1
        details = _StreamDetails(
            stream_id=f"external-block-{stream_type}-{stream_numbers[stream_type]:03d}",
            stream_type=stream_type,
            origin="external-structure-block",
            evidence=("external-structure-block", f"external-structure-{label}"),
        )
        for element in members:
            details_by_element_id[element.id] = details
    return details_by_element_id


def _external_structure_block_signature(
    element: ElementIR,
) -> tuple[str, str, tuple[float, float, float, float]] | None:
    metadata = element.metadata
    stream_type = _text(metadata.get("reading_order_stream_type")) or "body"
    stream_id = _text(metadata.get("reading_order_stream_id")) or "body-main"
    if stream_type != "body" or _has_explicit_structure_stream(metadata, stream_id):
        return None
    structure = metadata.get("structure_evidence")
    if not isinstance(structure, Mapping):
        return None
    label = _normalize_external_label(structure.get("label") or metadata.get("external_structure_label"))
    if label not in STREAMABLE_EXTERNAL_BLOCK_LABELS:
        return None
    if not _should_partition_external_structure_block(element, label):
        return None
    source = _text(structure.get("source"))
    bbox = structure.get("bbox_pdf")
    if not source or not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        normalized_bbox = tuple(round(float(value), 6) for value in bbox)
    except (TypeError, ValueError):
        return None
    return source, label, normalized_bbox


def _should_partition_external_structure_block(element: ElementIR, label: str) -> bool:
    """Avoid replacing a stable native column flow with generic model text blocks."""

    if label not in GENERIC_EXTERNAL_TEXT_BLOCK_LABELS:
        return True
    if _int(element.metadata.get("column_count"), default=1) >= 2:
        return False
    return _has_structural_flow_segment_signal(element)


def _external_block_stream_type(label: str) -> str:
    if label in {"table", "table_body", "table_cell", "table_content"}:
        return "table-island"
    if label in {
        "card",
        "card_grid",
        "content_card",
        "content_grid",
        "grid",
        "grid_area",
        "grid_block",
        "menu_grid",
        "nav_grid",
        "product",
        "product_card",
        "product_grid",
        "tile",
        "tile_grid",
    }:
        return "grid-island"
    return "body"


def _normalize_external_label(value: Any) -> str:
    return "_".join(_text(value).lower().replace("-", " ").split())


def _has_explicit_structure_stream(metadata: Mapping[str, Any], stream_id: str) -> bool:
    return bool(
        _text(metadata.get("external_structure_stream_id"))
        or _text(metadata.get("external_structure_stream_source"))
        or stream_id.startswith("external-")
    )


def _propose_stream(
    stream_id: str,
    details: "_StreamDetails",
    members: list["_ProposalMember"],
) -> tuple[dict[str, Any], Counter[str]]:
    successor_edges: list[dict[str, Any]] = []
    review_successor_edges: list[dict[str, Any]] = []
    review_edge_count = 0
    for source, target in zip(members, members[1:], strict=False):
        edge = _successor_edge(source, target)
        if edge["review_required"]:
            review_edge_count += 1
            review_successor_edges.append(edge)
        else:
            successor_edges.append(edge)
    confidences = [_confidence(member.element) for member in members]
    payload = {
        "id": stream_id,
        "type": details.stream_type,
        "members": [member.element.id for member in members],
        "successor_edges": successor_edges,
        "review_successor_edges": review_successor_edges,
        "proposal": {
            "origin": details.origin,
            "confidence": round(sum(confidences) / len(confidences), 8) if confidences else 0.0,
            "evidence": list(details.evidence),
        },
    }
    return (
        payload,
        Counter(
            {
                "stream_count": 1,
                "member_count": len(members),
                "successor_edge_count": len(successor_edges),
                "review_successor_edge_count": review_edge_count,
            }
        ),
    )


def _successor_edge(source: "_ProposalMember", target: "_ProposalMember") -> dict[str, Any]:
    source_metadata = source.element.metadata
    target_metadata = target.element.metadata
    confidence = min(_confidence(source.element), _confidence(target.element))
    evidence = [*source.details.evidence]
    if _has_explicit_successor(source.element, target.element.id):
        confidence = max(confidence, 0.99)
        evidence.append("external-successor-evidence")
    elif source.details.origin == "external-structure-block":
        confidence = min(confidence, 0.76)
        evidence.append("structure-block-membership")
    elif source.details.stream_type in {"table-island", "table-grid"}:
        confidence = max(confidence, 0.88)
        evidence.append("table-local-order")
    elif source.details.stream_type == "grid-island":
        confidence = max(confidence, 0.84)
        evidence.append("grid-local-order")
    elif source.details.origin == "column-partition":
        confidence = max(confidence, 0.8)
        evidence.append("same-column")
    elif source.details.origin == "existing-local":
        confidence = max(confidence, 0.76)
        evidence.append("existing-local-stream")
    if _int(source_metadata.get("flow_segment_index"), default=0) == _int(
        target_metadata.get("flow_segment_index"), default=-1
    ):
        evidence.append("same-flow-segment")
    confidence = min(round(confidence, 8), 1.0)
    return {
        "source": source.element.id,
        "target": target.element.id,
        "confidence": confidence,
        "review_required": confidence < 0.78,
        "evidence": _dedupe_texts(evidence),
    }


def _review_transitions(
    elements: list[ElementIR],
    member_by_element_id: Mapping[str, "_ProposalMember"],
) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    for source, target in zip(elements, elements[1:], strict=False):
        source_member = member_by_element_id[source.id]
        target_member = member_by_element_id[target.id]
        if source_member.stream_id == target_member.stream_id:
            continue
        reason = _transition_reason(source_member, target_member)
        confidence = min(_confidence(source), _confidence(target), 0.72)
        transitions.append(
            {
                "source": source.id,
                "target": target.id,
                "source_stream_id": source_member.stream_id,
                "target_stream_id": target_member.stream_id,
                "reason": reason,
                "confidence": round(confidence, 8),
                "review_required": True,
            }
        )
    return transitions


def _transition_reason(source: "_ProposalMember", target: "_ProposalMember") -> str:
    if source.details.origin == target.details.origin == "column-partition":
        if source.details.flow_segment == target.details.flow_segment:
            return "column-handoff"
        return "flow-segment-column-handoff"
    if source.details.origin == "full-width-partition" or target.details.origin == "full-width-partition":
        return "full-width-flow-boundary"
    if source.details.stream_type != target.details.stream_type:
        return "stream-type-boundary"
    return "local-stream-boundary"


def _has_explicit_successor(element: ElementIR, target_id: str) -> bool:
    targets = element.metadata.get("external_structure_successor_ids")
    return isinstance(targets, list) and target_id in {str(item) for item in targets}


def _is_full_width_span(column_span: str) -> bool:
    normalized = column_span.lower()
    return normalized == "full" or normalized.endswith("-full")


def _confidence(element: ElementIR) -> float:
    value = element.metadata.get("reading_order_confidence")
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.5


def _count_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    counts: dict[str, int] = {}
    for key, count in value.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        counts[normalized_key] = _int(count)
    return dict(sorted(counts.items()))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, *, default: int = 0) -> int:
    parsed = _optional_int(value)
    return default if parsed is None else parsed


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dedupe_texts(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


class _StreamDetails:
    def __init__(
        self,
        *,
        stream_id: str,
        stream_type: str,
        origin: str,
        evidence: tuple[str, ...],
        flow_segment: int = 1,
        column_index: int | None = None,
    ) -> None:
        self.stream_id = stream_id
        self.stream_type = stream_type
        self.origin = origin
        self.evidence = evidence
        self.flow_segment = flow_segment
        self.column_index = column_index


class _ProposalMember:
    def __init__(self, *, element: ElementIR, stream_id: str, details: _StreamDetails) -> None:
        self.element = element
        self.stream_id = stream_id
        self.details = details
