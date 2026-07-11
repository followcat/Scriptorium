from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .models import BBox, DocumentIR, ElementIR, PageIR
from .reading_order import (
    RelationGraphEdgeDiagnostics,
    infer_box_flow_order,
    infer_relation_graph_order,
    infer_relation_graph_selected_edge_diagnostics,
    successor_consensus_diagnostics,
)


SIDECAR_SCHEMA_NAME = "ScriptoriumReadingOrderSidecar"
SIDECAR_SCHEMA_VERSION = "1.1"
SIDECAR_PROPOSAL_STATUS = "proposal"
SIDECAR_SOURCE = "scriptorium-local-stream-proposal-v2"
EXPLICIT_BLOCK_ORDER_TRANSITION_KIND = "external-structure-explicit-block-order-v1"
REVIEW_EDGE_PROMOTION_CONFIDENCE = 0.82
REVIEW_EDGE_RELATION_GRAPH_SCORE = 0.86
REVIEW_EDGE_GEOMETRY_SCORE = 0.82

# These edge markers are emitted only by the native table/grid island path.
# They are deliberately narrower than generic existing-local streams: a
# benchmark can rely on them as local structure evidence without turning a
# sidecar's full selected order into another page-wide candidate vote.
LOCAL_STRUCTURE_STREAM_EVIDENCE = {
    "table-island": "table-local-order",
    "grid-island": "grid-local-order",
}


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


# Model block order is useful as a relation proposal only for primary textual
# regions. Non-linear islands and secondary page furniture are intentional
# boundaries: their local semantics need explicit stream or successor evidence.
PRIMARY_BLOCK_TRANSITION_LABELS = frozenset(
    {
        "abstract",
        "body",
        "body_text",
        "doc_title",
        "list",
        "list_item",
        "paragraph",
        "paragraph_text",
        "paragraph_title",
        "reference",
        "references",
        "section_header",
        "section_title",
        "text",
        "text_block",
        "title",
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
                    "strict_block_transition_count",
                    "review_block_transition_count",
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
            "structure_block_order_relations": "review_only",
            "description": (
                "Local successor edges are inferred from existing selected order and structural metadata. "
                "Explicit consecutive primary block orders may add provenance-rich review transitions, but never "
                "runtime order constraints. Review edge confidence and transitions before changing sidecar_status "
                "to accepted."
            ),
        },
        "summary": {
            "page_count": len(pages),
            "stream_count": int(summary["stream_count"]),
            "member_count": int(summary["member_count"]),
            "successor_edge_count": int(summary["successor_edge_count"]),
            "review_successor_edge_count": int(summary["review_successor_edge_count"]),
            "review_transition_count": int(summary["review_transition_count"]),
            "strict_block_transition_count": int(summary["strict_block_transition_count"]),
            "review_block_transition_count": int(summary["review_block_transition_count"]),
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
        "strict_block_transition_count": _int(summary.get("strict_block_transition_count")),
        "review_block_transition_count": _int(summary.get("review_block_transition_count")),
        "stream_type_counts": _count_mapping(summary.get("stream_type_counts")),
        "stream_origin_counts": _count_mapping(summary.get("stream_origin_counts")),
    }


def local_structure_successor_evidence(payload: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """Return strict native table/grid successor evidence grouped by page.

    A proposal may contain many useful local edges, including inferred body
    streams and external-model relations.  This helper exposes only the
    stricter native table/grid-island edges marked by ``table-local-order`` or
    ``grid-local-order``.  Consumers must keep them page-local: they provide
    evidence for a stream's internal sequence, never a cross-stream handoff
    or an extra full-page candidate order.
    """

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        return {}

    evidence_by_page: dict[int, dict[str, Any]] = {}
    for raw_page in raw_pages:
        if not isinstance(raw_page, Mapping):
            continue
        page_index = _optional_int(raw_page.get("page_index"))
        raw_streams = raw_page.get("reading_streams")
        if page_index is None or not isinstance(raw_streams, list):
            continue

        streams: list[dict[str, Any]] = []
        for raw_stream in raw_streams:
            if not isinstance(raw_stream, Mapping):
                continue
            stream_type = _text(raw_stream.get("type"))
            edge_marker = LOCAL_STRUCTURE_STREAM_EVIDENCE.get(stream_type)
            if edge_marker is None:
                continue
            stream_id = _text(raw_stream.get("id"))
            members = _sidecar_member_ids(raw_stream.get("members"))
            if not stream_id or len(members) < 2:
                continue

            potential_edges = tuple(zip(members, members[1:], strict=False))
            strict_edges = _strict_local_sidecar_edges(
                raw_stream.get("successor_edges"),
                potential_edges,
                edge_marker,
            )
            if not strict_edges:
                continue
            streams.append(
                {
                    "stream_id": stream_id,
                    "stream_type": stream_type,
                    "potential_successor_edges": potential_edges,
                    "successor_edges": strict_edges,
                }
            )

        if streams:
            evidence_by_page[page_index] = {"streams": tuple(streams)}
    return evidence_by_page


def _sidecar_member_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    members: list[str] = []
    seen: set[str] = set()
    for item in value:
        member_id = _text(item)
        if not member_id or member_id in seen:
            continue
        members.append(member_id)
        seen.add(member_id)
    return members


def _strict_local_sidecar_edges(
    value: Any,
    potential_edges: tuple[tuple[str, str], ...],
    marker: str,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        return ()
    potential_edge_set = set(potential_edges)
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping) or bool(item.get("review_required")):
            continue
        edge = (_text(item.get("source")), _text(item.get("target")))
        evidence = item.get("evidence")
        if (
            not all(edge)
            or edge in seen
            or edge not in potential_edge_set
            or not isinstance(evidence, list)
            or marker not in {_text(entry) for entry in evidence}
        ):
            continue
        edges.append(edge)
        seen.add(edge)
    return tuple(edges)


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

    relation_graph_edge_diagnostics = _page_relation_graph_edge_diagnostics(page, elements)
    review_edge_promotions = _review_edge_promotion_support(
        page,
        elements,
        stream_members,
        relation_graph_edge_diagnostics=relation_graph_edge_diagnostics,
    )
    element_order = {element.id: index for index, element in enumerate(elements)}
    streams: list[dict[str, Any]] = []
    summary: Counter[str] = Counter()
    stream_type_counts: Counter[str] = Counter()
    stream_origin_counts: Counter[str] = Counter()
    for stream_id, members in sorted(
        stream_members.items(),
        key=lambda item: (_element_order_key(item[1][0].element), item[0]),
    ):
        details = stream_details[stream_id]
        stream_payload, stream_summary = _propose_stream(
            stream_id,
            details,
            members,
            review_edge_promotions,
            relation_graph_edge_diagnostics,
            elements,
            element_order,
        )
        streams.append(stream_payload)
        summary.update(stream_summary)
        stream_type_counts[details.stream_type] += 1
        stream_origin_counts[details.origin] += 1

    block_transitions = _explicit_block_order_review_transitions(elements, member_by_element_id)
    transitions = _merge_review_transitions(
        _review_transitions(elements, member_by_element_id),
        block_transitions,
    )
    summary["review_transition_count"] = len(transitions)
    # Explicit model block order remains proposal evidence until a relation
    # provider or human review accepts it. Keep the strict count visible so a
    # benchmark can detect an accidental promotion in a later change.
    summary["strict_block_transition_count"] = 0
    summary["review_block_transition_count"] = len(block_transitions)
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
    if (
        stream_type != "body"
        or _has_explicit_structure_stream(metadata, stream_id)
        or _has_secondary_structure_stream(metadata)
    ):
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
    if _has_secondary_structure_stream(metadata):
        return False
    return bool(
        _text(metadata.get("external_structure_stream_id"))
        or _text(metadata.get("external_structure_stream_source"))
        or stream_id.startswith("external-")
    )


def _has_secondary_structure_stream(metadata: Mapping[str, Any]) -> bool:
    value = metadata.get("external_structure_stream_primary")
    if value is False:
        return True
    return _text(value).lower() in {"false", "0", "secondary"}


def _page_relation_graph_edge_diagnostics(
    page: PageIR,
    elements: list[ElementIR],
) -> dict[tuple[str, str], RelationGraphEdgeDiagnostics]:
    if len(elements) < 2:
        return {}
    diagnostics = infer_relation_graph_selected_edge_diagnostics(
        [element.bbox_pdf for element in elements],
        page.width_pt,
        page.height_pt,
    )
    return {
        (elements[source_index].id, elements[target_index].id): diagnostic
        for (source_index, target_index), diagnostic in diagnostics.items()
        if 0 <= source_index < len(elements) and 0 <= target_index < len(elements)
    }


def _review_edge_promotion_support(
    page: PageIR,
    elements: list[ElementIR],
    stream_members: Mapping[str, list["_ProposalMember"]],
    *,
    relation_graph_edge_diagnostics: Mapping[tuple[str, str], RelationGraphEdgeDiagnostics],
) -> dict[tuple[str, str], "_ReviewEdgePromotion"]:
    """Find review edges supported by three independent local signals.

    Page-level reading-order confidence is intentionally not reused here: it
    describes a strategy, not a particular adjacent edge. An upgrade requires
    mutual geometry, an actually selected global relation-graph edge, and a
    three-way local candidate consensus. Cross-stream handoffs never enter
    this function because it only receives edges inside one provisional stream.
    """

    if len(elements) < 2:
        return {}
    promotions: dict[tuple[str, str], _ReviewEdgePromotion] = {}
    for members in stream_members.values():
        promotions.update(
            _stream_review_edge_promotion_support(
                members,
                page_width=page.width_pt,
                page_height=page.height_pt,
                global_relation_edge_diagnostics=relation_graph_edge_diagnostics,
            )
        )
    return promotions


def _stream_review_edge_promotion_support(
    members: list["_ProposalMember"],
    *,
    page_width: float,
    page_height: float,
    global_relation_edge_diagnostics: Mapping[tuple[str, str], RelationGraphEdgeDiagnostics],
) -> dict[tuple[str, str], "_ReviewEdgePromotion"]:
    if len(members) < 2:
        return {}
    bboxes = [member.element.bbox_pdf for member in members]
    geometry_scores = _mutual_forward_geometry_scores(bboxes, page_width, page_height)
    if not geometry_scores:
        return {}

    visual_yx = sorted(range(len(members)), key=lambda index: _bbox_order_key(bboxes[index]))
    candidate_orders = {
        "visual-yx": visual_yx,
        "box-flow": infer_box_flow_order(bboxes, page_width, page_height),
        "relation-graph": infer_relation_graph_order(bboxes, page_width, page_height),
    }
    consensus = successor_consensus_diagnostics(
        candidate_orders,
        item_count=len(members),
        base_order=list(range(len(members))),
    )
    if consensus.agreement_level != "high":
        return {}
    candidate_successors = {
        name: _candidate_successor_edges(order)
        for name, order in candidate_orders.items()
    }

    promotions: dict[tuple[str, str], _ReviewEdgePromotion] = {}
    for source_index in range(len(members) - 1):
        target_index = source_index + 1
        geometry_score = geometry_scores.get((source_index, target_index))
        if geometry_score is None or geometry_score < REVIEW_EDGE_GEOMETRY_SCORE:
            continue
        if not all((source_index, target_index) in edges for edges in candidate_successors.values()):
            continue
        source_id = members[source_index].element.id
        target_id = members[target_index].element.id
        relation_diagnostic = global_relation_edge_diagnostics.get((source_id, target_id))
        if relation_diagnostic is None or relation_diagnostic.score < REVIEW_EDGE_RELATION_GRAPH_SCORE:
            continue
        if relation_diagnostic.has_tied_alternative:
            continue
        promotions[(source_id, target_id)] = _ReviewEdgePromotion(
            geometry_score=geometry_score,
            relation_graph_diagnostic=relation_diagnostic,
        )
    return promotions


def _candidate_successor_edges(order: list[int]) -> set[tuple[int, int]]:
    return {
        (source, target)
        for source, target in zip(order, order[1:], strict=False)
        if source != target
    }


def _mutual_forward_geometry_scores(
    bboxes: list[BBox],
    page_width: float,
    page_height: float,
) -> dict[tuple[int, int], float]:
    heights = [bbox.height for bbox in bboxes if bbox.height > 0]
    median_height = median(heights) if heights else 10.0
    forward: dict[int, list[tuple[float, int]]] = defaultdict(list)
    backward: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for source_index, source_bbox in enumerate(bboxes):
        for target_index, target_bbox in enumerate(bboxes):
            if source_index == target_index:
                continue
            score = _forward_geometry_score(
                source_bbox,
                target_bbox,
                page_width=page_width,
                page_height=page_height,
                median_height=median_height,
            )
            if score is None:
                continue
            forward[source_index].append((score, target_index))
            backward[target_index].append((score, source_index))

    best_forward = {
        source_index: _best_geometry_neighbor(candidates)
        for source_index, candidates in forward.items()
    }
    best_backward = {
        target_index: _best_geometry_neighbor(candidates)
        for target_index, candidates in backward.items()
    }
    scores: dict[tuple[int, int], float] = {}
    for source_index, target_index in best_forward.items():
        if best_backward.get(target_index) != source_index:
            continue
        score = next(
            candidate_score
            for candidate_score, candidate_target in forward[source_index]
            if candidate_target == target_index
        )
        scores[(source_index, target_index)] = round(score, 8)
    return scores


def _best_geometry_neighbor(candidates: list[tuple[float, int]]) -> int:
    return max(candidates, key=lambda item: (item[0], -item[1]))[1]


def _forward_geometry_score(
    source_bbox: BBox,
    target_bbox: BBox,
    *,
    page_width: float,
    page_height: float,
    median_height: float,
) -> float | None:
    vertical_gap = target_bbox.y0 - source_bbox.y1
    max_forward_gap = max(page_height * 0.08, median_height * 7.0)
    if vertical_gap < -median_height * 0.35 or vertical_gap > max_forward_gap:
        return None
    if not _horizontally_related(source_bbox, target_bbox, page_width):
        return None
    horizontal_overlap = _horizontal_overlap_ratio(source_bbox, target_bbox)
    center_delta = abs(_center_x(source_bbox) - _center_x(target_bbox))
    gap_score = 1.0 - min(max(vertical_gap, 0.0) / max(max_forward_gap, 1.0), 1.0)
    center_score = 1.0 - min(center_delta / max(page_width * 0.28, 1.0), 1.0)
    return 0.45 * gap_score + 0.35 * min(horizontal_overlap, 1.0) + 0.2 * center_score


def _horizontally_related(first: BBox, second: BBox, page_width: float) -> bool:
    overlap = _horizontal_overlap_ratio(first, second)
    center_delta = abs(_center_x(first) - _center_x(second))
    max_width = max(first.width, second.width)
    center_limit = max(24.0, min(max_width * 0.42, page_width * 0.12))
    return center_delta <= center_limit or (overlap >= 0.65 and center_delta <= page_width * 0.16)


def _horizontal_overlap_ratio(first: BBox, second: BBox) -> float:
    overlap = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))
    return overlap / max(1.0, min(first.width, second.width))


def _center_x(bbox: BBox) -> float:
    return (bbox.x0 + bbox.x1) / 2


def _bbox_order_key(bbox: BBox) -> tuple[float, float, float, float]:
    return (bbox.y0, bbox.x0, bbox.y1, bbox.x1)


def _propose_stream(
    stream_id: str,
    details: "_StreamDetails",
    members: list["_ProposalMember"],
    review_edge_promotions: Mapping[tuple[str, str], "_ReviewEdgePromotion"],
    relation_graph_edge_diagnostics: Mapping[tuple[str, str], RelationGraphEdgeDiagnostics],
    ordered_elements: list[ElementIR],
    element_order: Mapping[str, int],
) -> tuple[dict[str, Any], Counter[str]]:
    successor_edges: list[dict[str, Any]] = []
    review_successor_edges: list[dict[str, Any]] = []
    review_edge_count = 0
    for source, target in zip(members, members[1:], strict=False):
        interleaved_structure_boundary = _has_interleaved_explicit_structure_stream(
            source.element,
            target.element,
            ordered_elements,
            element_order,
        )
        edge = _successor_edge(
            source,
            target,
            review_edge_promotions.get((source.element.id, target.element.id)),
            relation_graph_edge_diagnostics.get((source.element.id, target.element.id)),
            interleaved_structure_boundary=interleaved_structure_boundary,
        )
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


def _successor_edge(
    source: "_ProposalMember",
    target: "_ProposalMember",
    review_edge_promotion: "_ReviewEdgePromotion | None" = None,
    relation_graph_diagnostic: RelationGraphEdgeDiagnostics | None = None,
    *,
    interleaved_structure_boundary: bool = False,
) -> dict[str, Any]:
    source_metadata = source.element.metadata
    target_metadata = target.element.metadata
    confidence = min(_confidence(source.element), _confidence(target.element))
    evidence = [*source.details.evidence]
    promotion: dict[str, Any] | None = None
    if _has_explicit_successor(source.element, target.element.id):
        confidence = max(confidence, 0.99)
        evidence.append("external-successor-evidence")
    elif interleaved_structure_boundary:
        confidence = min(confidence, 0.76)
        evidence.append("interleaved-external-stream-boundary")
    elif source.details.origin == "external-structure-block":
        confidence = min(confidence, 0.76)
        evidence.append("structure-block-membership")
    elif source.details.origin == "existing-structure":
        confidence = min(confidence, 0.76)
        evidence.append("external-stream-membership")
    elif source.details.stream_type in {"table-island", "table-grid"}:
        confidence = max(confidence, 0.88)
        evidence.append("table-local-order")
    elif source.details.stream_type == "grid-island":
        confidence = max(confidence, 0.84)
        evidence.append("grid-local-order")
    elif source.details.origin == "existing-local":
        confidence = max(confidence, 0.76)
        evidence.append("existing-local-stream")
    if source.details.origin == "column-partition" and not interleaved_structure_boundary:
        confidence = max(confidence, 0.8)
        evidence.append("same-column")
    if _int(source_metadata.get("flow_segment_index"), default=0) == _int(
        target_metadata.get("flow_segment_index"), default=-1
    ):
        evidence.append("same-flow-segment")
    if (
        confidence < 0.78
        and review_edge_promotion is not None
        and not interleaved_structure_boundary
    ):
        confidence = max(confidence, REVIEW_EDGE_PROMOTION_CONFIDENCE)
        evidence.extend(review_edge_promotion.evidence)
        promotion = review_edge_promotion.as_payload()
    confidence = min(round(confidence, 8), 1.0)
    edge = {
        "source": source.element.id,
        "target": target.element.id,
        "confidence": confidence,
        "review_required": confidence < 0.78,
        "evidence": _dedupe_texts(evidence),
    }
    if promotion is not None:
        edge["promotion"] = promotion
    # Stable strict edges retain the compact v1 payload.  Selection diagnostics
    # are attached when an edge was promoted or when a score tie itself is the
    # reason that a reviewer needs to inspect the local relation.
    if relation_graph_diagnostic is not None and (
        promotion is not None or relation_graph_diagnostic.has_tied_alternative
    ):
        edge["relation_graph"] = relation_graph_diagnostic.as_payload()
    return edge


def _has_interleaved_explicit_structure_stream(
    source: ElementIR,
    target: ElementIR,
    ordered_elements: list[ElementIR],
    element_order: Mapping[str, int],
) -> bool:
    """Keep inferred native edges from jumping across an external local stream."""

    source_index = element_order.get(source.id)
    target_index = element_order.get(target.id)
    if source_index is None or target_index is None or target_index <= source_index + 1:
        return False
    for element in ordered_elements[source_index + 1 : target_index]:
        metadata = element.metadata
        stream_id = _text(metadata.get("reading_order_stream_id")) or "body-main"
        if _has_explicit_structure_stream(metadata, stream_id):
            return True
    return False


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


def _explicit_block_order_review_transitions(
    elements: list[ElementIR],
    member_by_element_id: Mapping[str, "_ProposalMember"],
) -> list[dict[str, Any]]:
    """Propose review-only relations between unambiguous consecutive blocks.

    The numeric adjacency guard is deliberate. If order 2 is unmatched or is a
    table/sidebar boundary, order 1 must not jump directly to order 3. Tied
    block orders are also ambiguous and therefore produce no relation.
    """

    grouped: dict[_ExplicitStructureBlockKey, list[ElementIR]] = defaultdict(list)
    for element in elements:
        key = _explicit_structure_block_key(element)
        if key is not None:
            grouped[key].append(element)

    by_source_and_order: dict[str, dict[int, list[_ExplicitStructureBlock]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for key, members in grouped.items():
        ordered_members = tuple(sorted(members, key=_element_order_key))
        by_source_and_order[key.source][key.order].append(
            _ExplicitStructureBlock(key=key, members=ordered_members)
        )

    element_rank = {element.id: index for index, element in enumerate(elements)}
    transitions: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    for structure_source, blocks_by_order in sorted(by_source_and_order.items()):
        for order in sorted(blocks_by_order):
            next_order = order + 1
            source_blocks = blocks_by_order[order]
            target_blocks = blocks_by_order.get(next_order, [])
            if len(source_blocks) != 1 or len(target_blocks) != 1:
                continue
            source_block = source_blocks[0]
            target_block = target_blocks[0]
            if not _block_is_primary_transition_candidate(source_block):
                continue
            if not _block_is_primary_transition_candidate(target_block):
                continue

            source = source_block.members[-1]
            target = target_block.members[0]
            edge = (source.id, target.id)
            if source.id == target.id or edge in seen_edges:
                continue
            source_member = member_by_element_id[source.id]
            target_member = member_by_element_id[target.id]
            source_rank = element_rank[source.id]
            target_rank = element_rank[target.id]
            confidence = min(
                _confidence(source),
                _confidence(target),
                _structure_block_confidence(source_block),
                _structure_block_confidence(target_block),
                0.74,
            )
            transitions.append(
                {
                    "source": source.id,
                    "target": target.id,
                    "source_stream_id": source_member.stream_id,
                    "target_stream_id": target_member.stream_id,
                    "reason": "explicit-structure-block-order",
                    "confidence": round(confidence, 8),
                    "review_required": True,
                    "evidence": [
                        "external-structure-block-order",
                        "explicit-block-order",
                        "consecutive-block-order",
                        "primary-text-blocks",
                    ],
                    "provenance": {
                        "kind": EXPLICIT_BLOCK_ORDER_TRANSITION_KIND,
                        "structure_source": structure_source,
                        "order_source": "explicit",
                        "order_delta": 1,
                        "selected_order_direction": "forward" if source_rank < target_rank else "reverse",
                        "source_block": source_block.as_payload(),
                        "target_block": target_block.as_payload(),
                    },
                }
            )
            seen_edges.add(edge)
    return transitions


def _explicit_structure_block_key(element: ElementIR) -> "_ExplicitStructureBlockKey | None":
    metadata = element.metadata
    structure = metadata.get("structure_evidence")
    if not isinstance(structure, Mapping):
        return None
    order = _optional_int(structure.get("order"))
    if order is None or _text(structure.get("order_source")).lower() != "explicit":
        return None
    if _text(metadata.get("external_structure_order_source")).lower() not in {"", "explicit"}:
        return None
    source = _text(structure.get("source"))
    label = _normalize_external_label(structure.get("label") or metadata.get("external_structure_label"))
    bbox = structure.get("bbox_pdf")
    if not source or not label or not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        normalized_bbox = tuple(round(float(value), 6) for value in bbox)
    except (TypeError, ValueError):
        return None
    return _ExplicitStructureBlockKey(
        source=source,
        order=order,
        label=label,
        bbox_pdf=normalized_bbox,
    )


def _block_is_primary_transition_candidate(block: "_ExplicitStructureBlock") -> bool:
    if block.key.label not in PRIMARY_BLOCK_TRANSITION_LABELS:
        return False
    for element in block.members:
        metadata = element.metadata
        if metadata.get("external_structure_order_diagnostic_only") is True:
            return False
        if _text(metadata.get("reading_order_scope") or "body") != "body":
            return False
        if _text(metadata.get("reading_order_stream_type") or "body") != "body":
            return False
        if _has_secondary_structure_stream(metadata):
            return False
        if metadata.get("reading_order_caption_type"):
            return False
        column_span = _text(metadata.get("column_span"))
        if column_span.startswith(("grid", "table", "caption", "artifact", "footnote", "sidebar")):
            return False
        structure = metadata.get("structure_evidence")
        if not isinstance(structure, Mapping):
            return False
        coverage = _safe_float(structure.get("coverage"))
        if coverage is None or coverage < 0.5:
            return False
    return True


def _structure_block_confidence(block: "_ExplicitStructureBlock") -> float:
    values: list[float] = []
    for element in block.members:
        structure = element.metadata.get("structure_evidence")
        if not isinstance(structure, Mapping):
            continue
        confidence = _safe_float(structure.get("confidence"))
        if confidence is not None:
            values.append(confidence)
    return min(values) if values else 1.0


def _merge_review_transitions(
    selected_order_transitions: list[dict[str, Any]],
    block_order_transitions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge duplicate endpoints while retaining independent provenance."""

    merged = [dict(transition) for transition in selected_order_transitions]
    by_edge = {
        (_text(transition.get("source")), _text(transition.get("target"))): transition
        for transition in merged
    }
    for transition in block_order_transitions:
        edge = (_text(transition.get("source")), _text(transition.get("target")))
        existing = by_edge.get(edge)
        if existing is None:
            item = dict(transition)
            merged.append(item)
            by_edge[edge] = item
            continue
        existing["confidence"] = max(
            _safe_float(existing.get("confidence")) or 0.0,
            _safe_float(transition.get("confidence")) or 0.0,
        )
        existing["evidence"] = _dedupe_texts(
            [
                *(_text(item) for item in existing.get("evidence", []) if _text(item)),
                *(_text(item) for item in transition.get("evidence", []) if _text(item)),
            ]
        )
        existing["selected_order_transition"] = True
        existing["provenance"] = transition["provenance"]
    return merged


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


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _optional_float(value: float | None) -> float | None:
    return round(value, 8) if value is not None else None


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


class _ReviewEdgePromotion:
    def __init__(
        self,
        *,
        geometry_score: float,
        relation_graph_diagnostic: RelationGraphEdgeDiagnostics,
    ) -> None:
        self.geometry_score = geometry_score
        self.relation_graph_diagnostic = relation_graph_diagnostic
        self.evidence = (
            "geometry-mutual-neighbor",
            "relation-graph-selected",
            "stream-consensus-3-of-3",
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": "independent-local-evidence",
            "geometry_score": round(self.geometry_score, 8),
            "relation_graph_score": round(self.relation_graph_diagnostic.score, 8),
            "relation_graph_minimum_margin": _optional_float(
                self.relation_graph_diagnostic.minimum_margin
            ),
            "relation_graph_has_tied_alternative": self.relation_graph_diagnostic.has_tied_alternative,
            "candidate_consensus": "3-of-3",
        }


@dataclass(frozen=True)
class _ExplicitStructureBlockKey:
    source: str
    order: int
    label: str
    bbox_pdf: tuple[float, float, float, float]


@dataclass(frozen=True)
class _ExplicitStructureBlock:
    key: _ExplicitStructureBlockKey
    members: tuple[ElementIR, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "order": self.key.order,
            "label": self.key.label,
            "bbox_pdf": list(self.key.bbox_pdf),
            "member_ids": [element.id for element in self.members],
        }
