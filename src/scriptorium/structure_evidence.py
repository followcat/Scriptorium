from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .geometry import clamp_bbox, pdf_to_px_bbox, px_to_pdf_bbox
from .models import BBox, DocumentIR, ElementIR, PageIR, RevisionIR
from .reading_order_sidecar import is_unaccepted_reading_order_sidecar
from .relation_order import relation_edge_candidate_path_cover


STRUCTURE_REF_KEYS = (
    "id",
    "element_id",
    "block_id",
    "region_id",
    "layout_region_id",
    "table_region_id",
    "table_id",
    "formula_region_id",
    "seal_region_id",
    "cell_id",
    "text_id",
    "line_id",
    "paragraph_id",
    "uid",
    "self_ref",
    "ref",
    "docling_ref",
    "external_structure_table_ref",
)

INDEX_ALIAS_LABEL_KEYS = (
    "document",
    "elements",
    "blocks",
    "parsing_res_list",
    "layout_det_res.boxes",
)


PADDLE_INPUT_PAGE_RE = re.compile(r"(?:^|[_-])page[_-]?0*(\d+)$", re.IGNORECASE)


# A parsing-list position is weaker than an explicit model order.  Keep it for
# blocks that can form a reading stream, but never promote visual/furniture
# regions such as an image, header, or footer into a page-order constraint just
# because the producer serialized them in a list.
IMPLICIT_ORDERABLE_LABELS = frozenset(
    {
        "abstract",
        "algorithm",
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


@dataclass(frozen=True)
class StructureRegion:
    page_index: int
    label: str
    bbox_px: BBox
    bbox_pdf: BBox
    order: int | None
    order_source: str | None
    text: str
    confidence: float | None
    source: str
    raw: dict[str, Any]

    def as_metadata(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "label": self.label,
            "bbox_px": self.bbox_px.as_list(),
            "bbox_pdf": self.bbox_pdf.as_list(),
            "order": self.order,
            "order_source": self.order_source,
            "text": self.text,
            "confidence": self.confidence,
            "source": self.source,
        }


@dataclass(frozen=True)
class StructureRelationEdge:
    page_index: int
    kind: str
    source_ref: str
    target_ref: str
    source: str
    raw: dict[str, Any]

    def as_metadata(self) -> dict[str, Any]:
        metadata = {
            "page_index": self.page_index,
            "kind": self.kind,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "source": self.source,
        }
        source_alias = self.raw.get("_scriptorium_source_alias")
        target_alias = self.raw.get("_scriptorium_target_alias")
        if source_alias:
            metadata["source_alias"] = str(source_alias)
        if target_alias:
            metadata["target_alias"] = str(target_alias)
        if source_alias or target_alias:
            metadata["has_endpoint_alias"] = True
        for key in (
            "source_kind",
            "docling_document_index",
            "docling_parent_ref",
            "docling_parent_scope",
            "docling_run_index",
            "docling_same_page",
            "docling_locality",
            "docling_boundary_policy",
        ):
            if key in self.raw:
                metadata[key] = self.raw[key]
        return metadata


@dataclass(frozen=True)
class StructureReadingStream:
    page_index: int
    stream_id: str
    stream_type: str
    member_refs: tuple[str, ...]
    source: str
    raw: dict[str, Any]

    def as_metadata(self) -> dict[str, Any]:
        metadata = {
            "page_index": self.page_index,
            "stream_id": self.stream_id,
            "stream_type": self.stream_type,
            "member_refs": list(self.member_refs),
            "source": self.source,
        }
        member_aliases = self.raw.get("_scriptorium_member_aliases")
        if isinstance(member_aliases, dict) and member_aliases:
            metadata["member_aliases"] = dict(sorted((str(key), str(value)) for key, value in member_aliases.items()))
        for key in (
            "source_kind",
            "docling_document_index",
            "docling_parent_ref",
            "docling_parent_scope",
            "docling_run_index",
            "docling_same_page",
            "docling_locality",
            "docling_boundary_policy",
        ):
            if key in self.raw:
                metadata[key] = self.raw[key]
        return metadata


@dataclass(frozen=True)
class _EndpointResolution:
    """A relation endpoint mapped to one element or one matched structure group."""

    elements: tuple[ElementIR, ...]
    is_group: bool = False

    @property
    def first(self) -> ElementIR:
        return self.elements[0]

    @property
    def last(self) -> ElementIR:
        return self.elements[-1]

    @property
    def element_ids(self) -> list[str]:
        return [element.id for element in self.elements]


@dataclass
class _RelationApplicationStats:
    resolved_count: int = 0
    alias_resolved_count: int = 0
    resolved_group_edge_count: int = 0
    group_internal_edge_count: int = 0
    unresolved_edge_count: int = 0
    unresolved_endpoint_count: int = 0
    skipped_overlap_edge_count: int = 0
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "resolved_relation_edge_count": self.resolved_count,
            "resolved_relation_alias_edge_count": self.alias_resolved_count,
            "resolved_relation_group_edge_count": self.resolved_group_edge_count,
            "relation_group_internal_edge_count": self.group_internal_edge_count,
            "unresolved_relation_edge_count": self.unresolved_edge_count,
            "unresolved_relation_endpoint_count": self.unresolved_endpoint_count,
            "skipped_overlap_relation_edge_count": self.skipped_overlap_edge_count,
            "edges": self.diagnostics,
        }


@dataclass
class _StreamApplicationStats:
    resolved_count: int = 0
    conflict_count: int = 0
    alias_resolved_count: int = 0
    resolved_group_member_ref_count: int = 0
    unresolved_member_ref_count: int = 0
    duplicate_member_ref_count: int = 0
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "resolved_stream_member_count": self.resolved_count,
            "resolved_stream_alias_member_count": self.alias_resolved_count,
            "resolved_stream_group_member_ref_count": self.resolved_group_member_ref_count,
            "unresolved_stream_member_ref_count": self.unresolved_member_ref_count,
            "duplicate_stream_member_ref_count": self.duplicate_member_ref_count,
            "stream_conflict_count": self.conflict_count,
            "members": self.diagnostics,
        }


@dataclass(frozen=True)
class _ResolvedStreamMember:
    element: ElementIR
    reference: str
    alias: str
    alias_used: bool
    is_group: bool
    group_index: int
    group_size: int


@dataclass(frozen=True)
class _DoclingLocalRun:
    """A contiguous, same-page text run under one Docling body-tree container."""

    page_index: int
    parent_ref: str
    parent_scope: str
    run_index: int
    member_refs: tuple[str, ...]
    locality: str


@dataclass(frozen=True)
class _DoclingTreeLeaf:
    ref: str
    page_index: int
    bbox_pdf: BBox


def load_structure_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_structure_evidence(
    payload: Any,
    document: DocumentIR,
    source: str | None = None,
) -> list[StructureRegion]:
    """Normalize external layout/OCR model JSON into page-local structure regions.

    The parser accepts the common PP-StructureV3/PaddleOCR-VL shapes produced by
    `save_to_json`: nested `res` objects, `raw_results`, `pages`, and
    `parsing_res_list` blocks with `block_bbox`, `block_label`,
    `block_content`, and `block_order`. It also accepts DoclingDocument JSON and
    derives region order from the `body.children` tree.
    """

    regions: list[StructureRegion] = []
    regions.extend(_normalize_docling_evidence(payload, document, source=source))
    for fallback_page_index, page_payload in enumerate(_collect_page_payloads(payload)):
        page_index = _extract_page_index(page_payload, fallback_page_index)
        page = _document_page_by_evidence_index(document, page_index)
        if page is None:
            continue
        for raw_block in _iter_blocks(page_payload):
            block_order = _extract_order(raw_block)
            order_source = _extract_order_source(raw_block) if block_order is not None else None
            if block_order is None:
                block_order = _extract_implicit_order(raw_block)
                order_source = _extract_implicit_order_source(raw_block) if block_order is not None else None
            bbox_info = _extract_bbox(raw_block)
            if bbox_info is None:
                continue
            bbox, coordinate_space = bbox_info
            bbox_px, bbox_pdf = _normalize_region_bbox(bbox, coordinate_space, page)
            if bbox_px.width <= 0 or bbox_px.height <= 0:
                continue
            regions.append(
                StructureRegion(
                    page_index=page.page_index,
                    label=_extract_label(raw_block),
                    bbox_px=bbox_px,
                    bbox_pdf=bbox_pdf,
                    order=block_order,
                    order_source=order_source,
                    text=_extract_text(raw_block),
                    confidence=_extract_confidence(raw_block),
                    source=source or _extract_source(payload, raw_block),
                    raw=dict(raw_block),
                )
            )
    return regions


def normalize_structure_relations(
    payload: Any,
    document: DocumentIR,
    source: str | None = None,
) -> list[StructureRelationEdge]:
    """Normalize external successor/precedence edges from structure JSON.

    These relations are first normalized as evidence. After region matching has
    attached node keys to elements, apply_structure_evidence can resolve the
    endpoints and use safe acyclic relation chains as the selected semantic
    order for the page.
    """

    edges: list[StructureRelationEdge] = []
    seen: set[tuple[int, str, str, str]] = set()
    def append(edge: StructureRelationEdge) -> None:
        key = (
            edge.page_index,
            edge.kind,
            _relation_key(edge.source_ref),
            _relation_key(edge.target_ref),
        )
        if key in seen:
            return
        seen.add(key)
        edges.append(edge)

    for fallback_page_index, page_payload in enumerate(_collect_relation_payloads(payload)):
        page_index = _extract_page_index(page_payload, fallback_page_index)
        page = _document_page_by_evidence_index(document, page_index)
        if page is None:
            continue
        source_name = source or _extract_source(payload, None)
        endpoint_aliases = _page_relation_alias_map(page_payload)
        for kind, source_ref, target_ref, raw in _iter_relation_edges(page_payload):
            raw_edge = dict(raw)
            source_alias = _endpoint_alias(source_ref, endpoint_aliases)
            target_alias = _endpoint_alias(target_ref, endpoint_aliases)
            if source_alias:
                raw_edge["_scriptorium_source_alias"] = source_alias
            if target_alias:
                raw_edge["_scriptorium_target_alias"] = target_alias
            append(
                StructureRelationEdge(
                    page_index=page.page_index,
                    kind=kind,
                    source_ref=source_ref,
                    target_ref=target_ref,
                    source=source_name,
                    raw=raw_edge,
                )
            )

    for document_index, doc in enumerate(_collect_docling_documents(payload), start=1):
        source_name = source or "docling"
        for run in _docling_body_tree_local_runs(doc, document):
            for source_ref, target_ref in zip(run.member_refs, run.member_refs[1:], strict=False):
                append(
                    StructureRelationEdge(
                        page_index=run.page_index,
                        kind="successor",
                        source_ref=source_ref,
                        target_ref=target_ref,
                        source=source_name,
                        raw={
                            "source": source_ref,
                            "target": target_ref,
                            "source_kind": "docling-body-tree",
                            "docling_document_index": document_index,
                            "docling_parent_ref": run.parent_ref,
                            "docling_parent_scope": run.parent_scope,
                            "docling_run_index": run.run_index,
                            "docling_same_page": True,
                            "docling_locality": run.locality,
                            "docling_boundary_policy": "no-cross-container-successor",
                        },
                    )
                )
    return edges


def normalize_structure_streams(
    payload: Any,
    document: DocumentIR,
    source: str | None = None,
) -> list[StructureReadingStream]:
    streams: list[StructureReadingStream] = []
    seen: set[tuple[int, str, tuple[str, ...]]] = set()
    def append(stream: StructureReadingStream) -> None:
        key = (
            stream.page_index,
            stream.stream_id,
            tuple(_relation_key(ref) for ref in stream.member_refs),
        )
        if key in seen:
            return
        seen.add(key)
        streams.append(stream)

    for fallback_page_index, page_payload in enumerate(_collect_relation_payloads(payload)):
        page_index = _extract_page_index(page_payload, fallback_page_index)
        page = _document_page_by_evidence_index(document, page_index)
        if page is None:
            continue
        source_name = source or _extract_source(payload, None)
        endpoint_aliases = _page_relation_alias_map(page_payload)
        for stream_index, raw_stream in enumerate(_iter_structure_streams(page_payload), start=1):
            member_refs = tuple(_stream_member_refs(raw_stream))
            if not member_refs:
                continue
            stream_id = _extract_stream_id(raw_stream, page.page_index, stream_index)
            raw_stream_with_aliases = dict(raw_stream)
            member_aliases = {
                _relation_key(ref): alias
                for ref in member_refs
                if (alias := _endpoint_alias(ref, endpoint_aliases))
            }
            if member_aliases:
                raw_stream_with_aliases["_scriptorium_member_aliases"] = member_aliases
            append(
                StructureReadingStream(
                    page_index=page.page_index,
                    stream_id=stream_id,
                    stream_type=_extract_stream_type(raw_stream),
                    member_refs=member_refs,
                    source=source_name,
                    raw=raw_stream_with_aliases,
                )
            )

    for document_index, doc in enumerate(_collect_docling_documents(payload), start=1):
        source_name = source or "docling"
        for run in _docling_body_tree_local_runs(doc, document):
            stream_id = _docling_local_run_stream_id(run, document_index=document_index)
            append(
                StructureReadingStream(
                    page_index=run.page_index,
                    stream_id=stream_id,
                    stream_type="body",
                    member_refs=run.member_refs,
                    source=source_name,
                    raw={
                        "stream_id": stream_id,
                        "stream_type": "body",
                        "members": list(run.member_refs),
                        "source_kind": "docling-body-tree",
                        "docling_document_index": document_index,
                        "docling_parent_ref": run.parent_ref,
                        "docling_parent_scope": run.parent_scope,
                        "docling_run_index": run.run_index,
                        "docling_same_page": True,
                        "docling_locality": run.locality,
                        "docling_boundary_policy": "no-cross-container-successor",
                    },
                )
            )
    return streams


def _normalize_docling_evidence(
    payload: Any,
    document: DocumentIR,
    *,
    source: str | None,
) -> list[StructureRegion]:
    regions: list[StructureRegion] = []
    for doc in _collect_docling_documents(payload):
        regions.extend(_normalize_docling_document(doc, document, source=source))
    return regions


def _collect_docling_documents(payload: Any) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        if id(value) in seen:
            return
        seen.add(id(value))

        if _is_docling_document(value):
            documents.append(value)
            return

        for child in value.values():
            if isinstance(child, (dict, list)):
                visit(child)

    visit(payload)
    return documents


def _is_docling_document(value: dict[str, Any]) -> bool:
    if value.get("schema_name") == "DoclingDocument":
        return True
    if not isinstance(value.get("body"), dict):
        return False
    return any(isinstance(value.get(key), list) for key in ("texts", "tables", "pictures", "groups", "key_value_items"))


def _normalize_docling_document(
    doc: dict[str, Any],
    document: DocumentIR,
    *,
    source: str | None,
) -> list[StructureRegion]:
    body = doc.get("body")
    furniture = doc.get("furniture")
    if not isinstance(body, dict) and not isinstance(furniture, dict):
        return []

    ref_index = _build_docling_ref_index(doc)
    regions: list[StructureRegion] = []
    emitted: set[tuple[str, int, tuple[float, float, float, float]]] = set()
    order_counter = 0

    def traverse(
        node: Any,
        current_ref: str | None = None,
        *,
        order_source: str,
        orderable: bool,
    ) -> None:
        nonlocal order_counter
        item, ref = _resolve_docling_node(node, doc, ref_index, current_ref)
        if not isinstance(item, dict):
            return

        ref_kind = _docling_ref_kind(ref or item.get("self_ref"))
        if ref_kind != "groups":
            region_order = order_counter + 1 if orderable else None
            item_regions = _docling_item_regions(
                item,
                document,
                order=region_order,
                order_source=order_source,
                source=source or "docling",
                ref=ref or item.get("self_ref"),
            )
            new_regions: list[StructureRegion] = []
            for region in item_regions:
                key = (
                    str(ref or item.get("self_ref") or id(item)),
                    region.page_index,
                    tuple(round(value, 4) for value in region.bbox_pdf.as_list()),
                )
                if key in emitted:
                    continue
                emitted.add(key)
                new_regions.append(region)
            if new_regions:
                regions.extend(new_regions)
                if orderable:
                    order_counter += 1
            table_cell_regions = _docling_table_cell_regions(
                item,
                document,
                order=region_order,
                order_source="docling-table-cell" if orderable else order_source,
                source=source or "docling",
                ref=ref or item.get("self_ref"),
            )
            for region in table_cell_regions:
                key = (
                    str(region.raw.get("docling_ref") or id(region.raw)),
                    region.page_index,
                    tuple(round(value, 4) for value in region.bbox_pdf.as_list()),
                )
                if key in emitted:
                    continue
                emitted.add(key)
                regions.append(region)

        children = item.get("children")
        if isinstance(children, list):
            for child in children:
                traverse(
                    child,
                    order_source=order_source,
                    orderable=orderable,
                )

    if isinstance(body, dict):
        traverse(body, "#/body", order_source="docling-body", orderable=True)
    if isinstance(furniture, dict):
        traverse(furniture, "#/furniture", order_source="docling-furniture", orderable=False)
    return regions


def _docling_body_tree_local_runs(
    doc: dict[str, Any],
    document: DocumentIR,
) -> list[_DoclingLocalRun]:
    """Collect only local Docling text runs that are safe as executable edges.

    Docling's body tree carries document order, but a parent containing two
    nested groups does not establish a direct text-to-text successor across
    those groups.  Restricting runs to adjacent text siblings keeps group,
    table, picture, unresolved-ref, and page transitions as boundaries.
    """

    body = doc.get("body")
    if not isinstance(body, dict):
        return []

    ref_index = _build_docling_ref_index(doc)
    runs: list[_DoclingLocalRun] = []
    visited_containers: set[str] = set()

    def visit_container(node: Any, current_ref: str | None, parent_scope: str) -> None:
        item, ref = _resolve_docling_node(node, doc, ref_index, current_ref)
        if not isinstance(item, dict):
            return
        parent_ref = str(ref or item.get("self_ref") or current_ref or "").strip()
        if not parent_ref:
            return
        if parent_ref in visited_containers:
            return
        visited_containers.add(parent_ref)

        children = item.get("children")
        if not isinstance(children, list):
            return

        run_index = 0
        pending_leaves: list[_DoclingTreeLeaf] = []

        def flush() -> None:
            nonlocal run_index, pending_leaves
            if len(pending_leaves) >= 2:
                run_index += 1
                runs.append(
                    _DoclingLocalRun(
                        page_index=pending_leaves[0].page_index,
                        parent_ref=parent_ref,
                        parent_scope=parent_scope,
                        run_index=run_index,
                        member_refs=tuple(leaf.ref for leaf in pending_leaves),
                        locality=(
                            "same-container-geometry"
                            if parent_scope == "body"
                            else "same-container-tree"
                        ),
                    )
                )
            pending_leaves = []

        for child in children:
            child_item, child_ref = _resolve_docling_node(child, doc, ref_index)
            if not isinstance(child_item, dict):
                flush()
                continue
            if _docling_tree_container_scope(child_item, child_ref):
                flush()
                visit_container(child, child_ref, "group")
                continue

            leaf = _docling_relation_leaf(child_item, child_ref, document)
            if leaf is None:
                flush()
                continue
            if pending_leaves:
                previous = pending_leaves[-1]
                if previous.page_index != leaf.page_index:
                    flush()
                elif parent_scope == "body":
                    page = _document_page_by_evidence_index(document, leaf.page_index)
                    if page is None or not _docling_body_siblings_are_locally_continuous(previous, leaf, page):
                        flush()
            pending_leaves.append(leaf)
        flush()

    visit_container(body, "#/body", "body")
    return runs


def _docling_tree_container_scope(item: dict[str, Any], ref: str | None) -> str | None:
    if _docling_ref_kind(ref or item.get("self_ref")) == "groups":
        return "group"
    return None


def _docling_relation_leaf(
    item: dict[str, Any],
    ref: str | None,
    document: DocumentIR,
) -> _DoclingTreeLeaf | None:
    """Return one bounded, single-page textual Docling leaf."""

    ref_kind = _docling_ref_kind(ref or item.get("self_ref"))
    label = _normalize_structure_label(_extract_label(item))
    if ref_kind in {"groups", "pictures", "tables"}:
        return None
    if label in {"picture", "image", "figure", "chart", "table", "table_body", "table_content"}:
        return None
    if not _extract_docling_text(item):
        return None

    prov_items = [prov for prov in item.get("prov", []) if isinstance(prov, dict)]
    if not prov_items:
        return None
    evidence_page_indices = {_docling_page_index(prov) for prov in prov_items}
    if len(evidence_page_indices) != 1:
        return None
    evidence_page_index = next(iter(evidence_page_indices))
    page = _document_page_by_evidence_index(document, evidence_page_index)
    if page is None:
        return None
    boxes = [bbox for prov in prov_items if (bbox := _docling_bbox_from_prov(prov, page)) is not None]
    bbox_pdf = _union_bboxes(boxes)
    if bbox_pdf is None:
        return None
    resolved_ref = str(ref or item.get("self_ref") or "").strip()
    if not resolved_ref:
        return None
    return _DoclingTreeLeaf(ref=resolved_ref, page_index=page.page_index, bbox_pdf=bbox_pdf)


def _docling_body_siblings_are_locally_continuous(
    previous: _DoclingTreeLeaf,
    current: _DoclingTreeLeaf,
    page: PageIR,
) -> bool:
    """Guard root-body edges against multi-column or long-range transitions."""

    if previous.page_index != current.page_index:
        return False
    previous_box = previous.bbox_pdf
    current_box = current.bbox_pdf
    vertical_tolerance = max(1.0, min(previous_box.height, current_box.height) * 0.35)
    if current_box.y0 < previous_box.y0 - vertical_tolerance:
        return False
    maximum_gap = max(page.height_pt * 0.16, max(previous_box.height, current_box.height) * 14.0)
    if current_box.y0 - previous_box.y1 > maximum_gap:
        return False

    horizontal_overlap = max(
        0.0,
        min(previous_box.x1, current_box.x1) - max(previous_box.x0, current_box.x0),
    )
    minimum_width = max(min(previous_box.width, current_box.width), 1.0)
    if horizontal_overlap / minimum_width >= 0.35:
        return True

    center_offset = abs(_center_x(previous_box) - _center_x(current_box))
    return center_offset <= max(page.width_pt * 0.10, minimum_width * 0.60)


def _docling_local_run_stream_id(run: _DoclingLocalRun, *, document_index: int) -> str:
    parent = _slug_text(run.parent_ref)
    if not parent or parent == "unknown":
        parent = run.parent_scope
    return (
        f"docling-{parent}-document-{document_index:03d}"
        f"-page-{run.page_index + 1:03d}-run-{run.run_index:03d}"
    )


def _build_docling_ref_index(doc: dict[str, Any]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for root_key in ("body", "furniture"):
        root = doc.get(root_key)
        if isinstance(root, dict):
            index[f"#/{root_key}"] = root
    for key, value in doc.items():
        if not isinstance(value, list):
            continue
        for item_index, item in enumerate(value):
            if not isinstance(item, dict):
                continue
            pointer = f"#/{key}/{item_index}"
            index[pointer] = item
            self_ref = item.get("self_ref")
            if isinstance(self_ref, str) and self_ref:
                index[self_ref] = item
    return index


def _resolve_docling_node(
    node: Any,
    doc: dict[str, Any],
    ref_index: dict[str, Any],
    current_ref: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(node, str):
        resolved = ref_index.get(node) or _resolve_json_pointer(doc, node)
        return (resolved, node) if isinstance(resolved, dict) else (None, node)
    if isinstance(node, dict):
        ref = node.get("$ref") or node.get("ref")
        if isinstance(ref, str):
            resolved = ref_index.get(ref) or _resolve_json_pointer(doc, ref)
            return (resolved, ref) if isinstance(resolved, dict) else (None, ref)
        self_ref = node.get("self_ref")
        return node, str(self_ref) if self_ref else current_ref
    return None, current_ref


def _resolve_json_pointer(doc: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    current: Any = doc
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _docling_ref_kind(ref: Any) -> str | None:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    return parts[0] if parts else None


def _docling_item_regions(
    item: dict[str, Any],
    document: DocumentIR,
    *,
    order: int | None,
    order_source: str,
    source: str,
    ref: Any,
) -> list[StructureRegion]:
    prov_items = item.get("prov")
    if not isinstance(prov_items, list):
        return []

    regions: list[StructureRegion] = []
    for prov in prov_items:
        if not isinstance(prov, dict):
            continue
        page_index = _docling_page_index(prov)
        page = _document_page_by_evidence_index(document, page_index)
        if page is None:
            continue
        bbox_pdf = _docling_bbox_from_prov(prov, page)
        if bbox_pdf is None:
            continue
        bbox_px, normalized_bbox_pdf = _normalize_region_bbox(bbox_pdf, "pdf", page)
        if bbox_px.width <= 0 or bbox_px.height <= 0:
            continue
        raw = dict(item)
        raw["docling_ref"] = ref
        raw["docling_prov"] = dict(prov)
        regions.append(
            StructureRegion(
                page_index=page.page_index,
                label=_extract_label(item),
                bbox_px=bbox_px,
                bbox_pdf=normalized_bbox_pdf,
                order=order,
                order_source=order_source,
                text=_extract_docling_text(item),
                confidence=_extract_confidence(item) or _extract_confidence(prov),
                source=source,
                raw=raw,
            )
        )
    return regions


def _docling_table_cell_regions(
    item: dict[str, Any],
    document: DocumentIR,
    *,
    order: int | None,
    order_source: str,
    source: str,
    ref: Any,
) -> list[StructureRegion]:
    data = item.get("data")
    if not isinstance(data, dict):
        return []

    cells = _docling_table_cells(data)
    if not cells:
        return []

    parent_prov_items = [prov for prov in item.get("prov", []) if isinstance(prov, dict)]
    regions: list[StructureRegion] = []
    for cell_index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        text = str(cell.get("text") or cell.get("content") or "").strip()
        if not text:
            continue
        page_index = _docling_cell_page_index(cell, parent_prov_items)
        page = _document_page_by_evidence_index(document, page_index)
        if page is None:
            continue
        bbox_pdf = _docling_cell_bbox(cell, page, parent_prov_items)
        if bbox_pdf is None:
            continue
        bbox_px, normalized_bbox_pdf = _normalize_region_bbox(bbox_pdf, "pdf", page)
        if bbox_px.width <= 0 or bbox_px.height <= 0:
            continue
        row_index = _optional_int(cell.get("start_row_offset_idx"))
        col_index = _optional_int(cell.get("start_col_offset_idx"))
        raw = dict(cell)
        raw["docling_table_ref"] = ref
        raw["docling_ref"] = str(cell.get("ref") or f"{ref}/data/table_cells/{cell_index}")
        raw["_scriptorium_structure_order_subindex"] = _docling_cell_order_subindex(cell, cell_index, data)
        if row_index is not None:
            raw["external_structure_table_cell_row"] = row_index
        if col_index is not None:
            raw["external_structure_table_cell_col"] = col_index
        for key in ("row_span", "col_span", "column_header", "row_header", "row_section"):
            if key in cell:
                raw[f"external_structure_table_cell_{key}"] = cell[key]
        regions.append(
            StructureRegion(
                page_index=page.page_index,
                label="table_cell",
                bbox_px=bbox_px,
                bbox_pdf=normalized_bbox_pdf,
                order=order,
                order_source=order_source,
                text=text,
                confidence=_extract_confidence(cell) or _extract_confidence(item),
                source=source,
                raw=raw,
            )
        )
    return regions


def _docling_table_cells(data: dict[str, Any]) -> list[dict[str, Any]]:
    table_cells = data.get("table_cells")
    if isinstance(table_cells, list) and table_cells:
        return [cell for cell in table_cells if isinstance(cell, dict)]

    grid = data.get("grid")
    cells: list[dict[str, Any]] = []
    if isinstance(grid, list):
        for row in grid:
            if isinstance(row, list):
                cells.extend(cell for cell in row if isinstance(cell, dict))
            elif isinstance(row, dict):
                cells.append(row)
    return cells


def _docling_cell_page_index(cell: dict[str, Any], parent_prov_items: list[dict[str, Any]]) -> int:
    prov_items = cell.get("prov")
    if isinstance(prov_items, list):
        for prov in prov_items:
            if isinstance(prov, dict):
                return _docling_page_index(prov)
    if parent_prov_items:
        return _docling_page_index(parent_prov_items[0])
    value = cell.get("page_index")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    for key in ("page_no", "page", "page_num"):
        value = cell.get(key)
        if value is None:
            continue
        try:
            return max(int(value) - 1, 0)
        except (TypeError, ValueError):
            continue
    return 0


def _docling_cell_bbox(
    cell: dict[str, Any],
    page: PageIR,
    parent_prov_items: list[dict[str, Any]],
) -> BBox | None:
    prov_items = cell.get("prov")
    if isinstance(prov_items, list):
        for prov in prov_items:
            if isinstance(prov, dict):
                bbox = _docling_bbox_from_prov(prov, page)
                if bbox is not None:
                    return bbox

    raw_bbox = cell.get("bbox") or cell.get("box") or cell.get("bbox_pdf")
    bbox = _docling_bbox_from_any(raw_bbox)
    if bbox is None:
        return None

    origin = ""
    if isinstance(raw_bbox, dict):
        origin = str(raw_bbox.get("coord_origin") or "").upper()
    if not origin:
        origin = str(cell.get("coord_origin") or "").upper()
    if not origin and parent_prov_items:
        parent_bbox = parent_prov_items[0].get("bbox")
        if isinstance(parent_bbox, dict):
            origin = str(parent_bbox.get("coord_origin") or "").upper()
        if not origin:
            origin = str(parent_prov_items[0].get("coord_origin") or "").upper()
    if origin == "BOTTOMLEFT" or (not origin and bbox.y0 > bbox.y1):
        return BBox(
            x0=bbox.x0,
            y0=page.height_pt - bbox.y0,
            x1=bbox.x1,
            y1=page.height_pt - bbox.y1,
        )
    return bbox


def _docling_cell_order_subindex(cell: dict[str, Any], cell_index: int, data: dict[str, Any]) -> int:
    row = _optional_int(cell.get("start_row_offset_idx"))
    col = _optional_int(cell.get("start_col_offset_idx"))
    num_cols = _optional_int(data.get("num_cols")) or 1000
    if row is None or col is None:
        return cell_index + 1
    return row * max(num_cols, 1) + col + 1


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _document_page_by_evidence_index(document: DocumentIR, page_index: int) -> PageIR | None:
    for page in document.pages:
        if page.page_index == page_index:
            return page
    if _document_uses_positional_page_indices(document) and 0 <= page_index < len(document.pages):
        return document.pages[page_index]
    return None


def _document_uses_positional_page_indices(document: DocumentIR) -> bool:
    return all(page.page_index == index for index, page in enumerate(document.pages))


def _docling_page_index(prov: dict[str, Any]) -> int:
    for key in ("page_no", "page", "page_num"):
        value = prov.get(key)
        if value is None:
            continue
        try:
            return max(int(value) - 1, 0)
        except (TypeError, ValueError):
            continue
    value = prov.get("page_index")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _docling_bbox_from_prov(prov: dict[str, Any], page: PageIR) -> BBox | None:
    raw_bbox = prov.get("bbox")
    bbox = _docling_bbox_from_any(raw_bbox)
    if bbox is None:
        return None

    origin = ""
    if isinstance(raw_bbox, dict):
        origin = str(raw_bbox.get("coord_origin") or "").upper()
    if not origin:
        origin = str(prov.get("coord_origin") or "").upper()
    if origin == "BOTTOMLEFT" or (not origin and bbox.y0 > bbox.y1):
        return BBox(
            x0=bbox.x0,
            y0=page.height_pt - bbox.y0,
            x1=bbox.x1,
            y1=page.height_pt - bbox.y1,
        )
    return bbox


def _docling_bbox_from_any(value: Any) -> BBox | None:
    if isinstance(value, dict) and {"l", "t", "r", "b"}.issubset(value):
        try:
            return BBox(
                x0=float(value["l"]),
                y0=float(value["t"]),
                x1=float(value["r"]),
                y1=float(value["b"]),
            )
        except (TypeError, ValueError):
            return None
    return _bbox_from_any(value)


def _extract_docling_text(item: dict[str, Any]) -> str:
    text = _extract_text(item)
    if text:
        return text

    data = item.get("data")
    if not isinstance(data, dict):
        return ""
    cell_texts: list[str] = []
    for key in ("table_cells", "cells", "grid"):
        value = data.get(key)
        if isinstance(value, list):
            _collect_docling_cell_texts(value, cell_texts)
    return " ".join(text for text in cell_texts if text).strip()


def _collect_docling_cell_texts(value: Any, texts: list[str]) -> None:
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        if text:
            texts.append(str(text).strip())
        for child in value.values():
            if isinstance(child, (dict, list)):
                _collect_docling_cell_texts(child, texts)
    elif isinstance(value, list):
        for child in value:
            _collect_docling_cell_texts(child, texts)


def apply_structure_evidence(
    document: DocumentIR,
    payload: Any,
    *,
    source: str | None = None,
    min_coverage: float = 0.5,
    min_text_similarity: float = 0.45,
    reorder: bool = True,
) -> DocumentIR:
    source_name = source or _extract_source(payload, None)
    if is_unaccepted_reading_order_sidecar(payload):
        document.metadata["structure_evidence_proposal"] = {
            "source": source_name,
            "status": "proposal-skipped",
            "schema_name": str(payload.get("schema_name") or ""),
            "instruction": "Review local edges, then set sidecar_status to accepted before applying it.",
        }
        document.revisions.append(
            RevisionIR(
                reason="structure-evidence-proposal-skipped",
                payload={"source": source_name},
            )
        )
        return document
    regions = normalize_structure_evidence(payload, document, source=source)
    relations = normalize_structure_relations(payload, document, source=source)
    streams = normalize_structure_streams(payload, document, source=source)
    regions_by_page: dict[int, list[StructureRegion]] = {}
    for region in regions:
        regions_by_page.setdefault(region.page_index, []).append(region)
    relations_by_page: dict[int, list[StructureRelationEdge]] = {}
    for relation in relations:
        relations_by_page.setdefault(relation.page_index, []).append(relation)
    streams_by_page: dict[int, list[StructureReadingStream]] = {}
    for stream in streams:
        streams_by_page.setdefault(stream.page_index, []).append(stream)

    matched_count = 0
    resolved_relation_count = 0
    resolved_relation_alias_count = 0
    resolved_relation_group_count = 0
    relation_group_internal_edge_count = 0
    unresolved_relation_edge_count = 0
    unresolved_relation_endpoint_count = 0
    skipped_overlap_relation_edge_count = 0
    resolved_stream_member_count = 0
    resolved_stream_alias_member_count = 0
    resolved_stream_group_member_ref_count = 0
    unresolved_stream_member_ref_count = 0
    duplicate_stream_member_ref_count = 0
    stream_conflict_count = 0
    relation_stream_count = 0
    resolved_relation_stream_member_count = 0
    relation_stream_conflict_count = 0
    reordered_pages = 0
    relation_reordered_pages = 0
    order_reordered_pages = 0
    order_source_counts = Counter(str(region.order_source or "none") for region in regions)
    relation_resolution_by_page: list[dict[str, Any]] = []
    stream_resolution_by_page: list[dict[str, Any]] = []
    for page in document.pages:
        page_regions = regions_by_page.get(page.page_index, [])
        if page_regions:
            page_matches = _apply_page_regions(
                page,
                page_regions,
                min_coverage=min_coverage,
                min_text_similarity=min_text_similarity,
            )
            matched_count += page_matches
        page_relation_stats = _apply_page_relation_edges(
            page,
            relations_by_page.get(page.page_index, []),
        )
        resolved_relation_count += page_relation_stats.resolved_count
        resolved_relation_alias_count += page_relation_stats.alias_resolved_count
        resolved_relation_group_count += page_relation_stats.resolved_group_edge_count
        relation_group_internal_edge_count += page_relation_stats.group_internal_edge_count
        unresolved_relation_edge_count += page_relation_stats.unresolved_edge_count
        unresolved_relation_endpoint_count += page_relation_stats.unresolved_endpoint_count
        skipped_overlap_relation_edge_count += page_relation_stats.skipped_overlap_edge_count
        if relations_by_page.get(page.page_index):
            relation_resolution_by_page.append(
                {
                    "page_index": page.page_index,
                    **page_relation_stats.as_metadata(),
                }
            )
        page_stream_stats = _apply_page_streams(
            page,
            streams_by_page.get(page.page_index, []),
        )
        resolved_stream_member_count += page_stream_stats.resolved_count
        resolved_stream_alias_member_count += page_stream_stats.alias_resolved_count
        resolved_stream_group_member_ref_count += page_stream_stats.resolved_group_member_ref_count
        unresolved_stream_member_ref_count += page_stream_stats.unresolved_member_ref_count
        duplicate_stream_member_ref_count += page_stream_stats.duplicate_member_ref_count
        stream_conflict_count += page_stream_stats.conflict_count
        if streams_by_page.get(page.page_index):
            stream_resolution_by_page.append(
                {
                    "page_index": page.page_index,
                    **page_stream_stats.as_metadata(),
                }
            )
        page_relation_streams, page_relation_stream_members, page_relation_stream_conflicts = _apply_relation_derived_streams(
            page,
            source=source_name,
        )
        relation_stream_count += page_relation_streams
        resolved_relation_stream_member_count += page_relation_stream_members
        relation_stream_conflict_count += page_relation_stream_conflicts
        if reorder:
            reorder_source = _reorder_page_from_regions(page)
            if reorder_source:
                reordered_pages += 1
                if reorder_source == "relation":
                    relation_reordered_pages += 1
                elif reorder_source == "order":
                    order_reordered_pages += 1

    document.metadata["structure_evidence"] = {
        "version": "v1",
        "source": source_name,
        "region_count": len(regions),
        "relation_edge_count": len(relations),
        "resolved_relation_edge_count": resolved_relation_count,
        "resolved_relation_alias_edge_count": resolved_relation_alias_count,
        "resolved_relation_group_edge_count": resolved_relation_group_count,
        "relation_group_internal_edge_count": relation_group_internal_edge_count,
        "unresolved_relation_edge_count": unresolved_relation_edge_count,
        "unresolved_relation_endpoint_count": unresolved_relation_endpoint_count,
        "skipped_overlap_relation_edge_count": skipped_overlap_relation_edge_count,
        "stream_count": len(streams),
        "resolved_stream_member_count": resolved_stream_member_count,
        "resolved_stream_alias_member_count": resolved_stream_alias_member_count,
        "resolved_stream_group_member_ref_count": resolved_stream_group_member_ref_count,
        "unresolved_stream_member_ref_count": unresolved_stream_member_ref_count,
        "duplicate_stream_member_ref_count": duplicate_stream_member_ref_count,
        "stream_conflict_count": stream_conflict_count,
        "relation_stream_count": relation_stream_count,
        "resolved_relation_stream_member_count": resolved_relation_stream_member_count,
        "relation_stream_conflict_count": relation_stream_conflict_count,
        "matched_element_count": matched_count,
        "reordered_page_count": reordered_pages,
        "relation_reordered_page_count": relation_reordered_pages,
        "order_reordered_page_count": order_reordered_pages,
        "order_source_counts": dict(sorted(order_source_counts.items())),
        "regions_by_page": [
            {
                "page_index": page_index,
                "regions": [region.as_metadata() for region in page_regions],
            }
            for page_index, page_regions in sorted(regions_by_page.items())
        ],
        "relations_by_page": [
            {
                "page_index": page_index,
                "relations": [relation.as_metadata() for relation in page_relations],
            }
            for page_index, page_relations in sorted(relations_by_page.items())
        ],
        "relation_resolution_by_page": relation_resolution_by_page,
        "streams_by_page": [
            {
                "page_index": page_index,
                "streams": [stream.as_metadata() for stream in page_streams],
            }
            for page_index, page_streams in sorted(streams_by_page.items())
        ],
        "stream_resolution_by_page": stream_resolution_by_page,
    }
    _update_semantic_layer_metadata(
        document,
        source=source_name,
        region_count=len(regions),
        matched_count=matched_count,
        reordered_pages=reordered_pages,
        order_source_counts=dict(sorted(order_source_counts.items())),
        relation_count=len(relations),
        resolved_relation_count=resolved_relation_count,
        resolved_relation_alias_count=resolved_relation_alias_count,
        resolved_relation_group_count=resolved_relation_group_count,
        relation_group_internal_edge_count=relation_group_internal_edge_count,
        unresolved_relation_edge_count=unresolved_relation_edge_count,
        unresolved_relation_endpoint_count=unresolved_relation_endpoint_count,
        skipped_overlap_relation_edge_count=skipped_overlap_relation_edge_count,
        stream_count=len(streams),
        resolved_stream_member_count=resolved_stream_member_count,
        resolved_stream_alias_member_count=resolved_stream_alias_member_count,
        resolved_stream_group_member_ref_count=resolved_stream_group_member_ref_count,
        unresolved_stream_member_ref_count=unresolved_stream_member_ref_count,
        duplicate_stream_member_ref_count=duplicate_stream_member_ref_count,
        stream_conflict_count=stream_conflict_count,
        relation_stream_count=relation_stream_count,
        resolved_relation_stream_member_count=resolved_relation_stream_member_count,
        relation_stream_conflict_count=relation_stream_conflict_count,
        relation_reordered_pages=relation_reordered_pages,
        order_reordered_pages=order_reordered_pages,
    )
    document.revisions.append(
        RevisionIR(
            reason="structure-evidence-fusion",
            payload={
                "source": source_name,
                "region_count": len(regions),
                "relation_edge_count": len(relations),
                "resolved_relation_edge_count": resolved_relation_count,
                "resolved_relation_alias_edge_count": resolved_relation_alias_count,
                "resolved_relation_group_edge_count": resolved_relation_group_count,
                "relation_group_internal_edge_count": relation_group_internal_edge_count,
                "unresolved_relation_edge_count": unresolved_relation_edge_count,
                "unresolved_relation_endpoint_count": unresolved_relation_endpoint_count,
                "skipped_overlap_relation_edge_count": skipped_overlap_relation_edge_count,
                "stream_count": len(streams),
                "resolved_stream_member_count": resolved_stream_member_count,
                "resolved_stream_alias_member_count": resolved_stream_alias_member_count,
                "resolved_stream_group_member_ref_count": resolved_stream_group_member_ref_count,
                "unresolved_stream_member_ref_count": unresolved_stream_member_ref_count,
                "duplicate_stream_member_ref_count": duplicate_stream_member_ref_count,
                "stream_conflict_count": stream_conflict_count,
                "relation_stream_count": relation_stream_count,
                "resolved_relation_stream_member_count": resolved_relation_stream_member_count,
                "relation_stream_conflict_count": relation_stream_conflict_count,
                "matched_element_count": matched_count,
                "reordered_page_count": reordered_pages,
                "relation_reordered_page_count": relation_reordered_pages,
                "order_reordered_page_count": order_reordered_pages,
                "order_source_counts": dict(sorted(order_source_counts.items())),
            },
        )
    )
    return document


def _update_semantic_layer_metadata(
    document: DocumentIR,
    *,
    source: str,
    region_count: int,
    matched_count: int,
    reordered_pages: int,
    order_source_counts: dict[str, int],
    relation_count: int,
    resolved_relation_count: int,
    resolved_relation_alias_count: int,
    resolved_relation_group_count: int,
    relation_group_internal_edge_count: int,
    unresolved_relation_edge_count: int,
    unresolved_relation_endpoint_count: int,
    skipped_overlap_relation_edge_count: int,
    stream_count: int,
    resolved_stream_member_count: int,
    resolved_stream_alias_member_count: int,
    resolved_stream_group_member_ref_count: int,
    unresolved_stream_member_ref_count: int,
    duplicate_stream_member_ref_count: int,
    stream_conflict_count: int,
    relation_stream_count: int,
    resolved_relation_stream_member_count: int,
    relation_stream_conflict_count: int,
    relation_reordered_pages: int,
    order_reordered_pages: int,
) -> None:
    current = document.metadata.get("semantic_layer")
    semantic_layer = dict(current) if isinstance(current, dict) else {}
    structure_drives_image_semantics = document.source_type == "image" and (
        region_count > 0 or relation_count > 0 or stream_count > 0
    )
    semantic_layer["structure_json"] = {
        "source": source,
        "role": "semantic-driver" if structure_drives_image_semantics else "augmenting-evidence",
        "region_count": region_count,
        "matched_element_count": matched_count,
        "reordered_page_count": reordered_pages,
        "order_source_counts": order_source_counts,
        "relation_edge_count": relation_count,
        "resolved_relation_edge_count": resolved_relation_count,
        "resolved_relation_alias_edge_count": resolved_relation_alias_count,
        "resolved_relation_group_edge_count": resolved_relation_group_count,
        "relation_group_internal_edge_count": relation_group_internal_edge_count,
        "unresolved_relation_edge_count": unresolved_relation_edge_count,
        "unresolved_relation_endpoint_count": unresolved_relation_endpoint_count,
        "skipped_overlap_relation_edge_count": skipped_overlap_relation_edge_count,
        "stream_count": stream_count,
        "resolved_stream_member_count": resolved_stream_member_count,
        "resolved_stream_alias_member_count": resolved_stream_alias_member_count,
        "resolved_stream_group_member_ref_count": resolved_stream_group_member_ref_count,
        "unresolved_stream_member_ref_count": unresolved_stream_member_ref_count,
        "duplicate_stream_member_ref_count": duplicate_stream_member_ref_count,
        "stream_conflict_count": stream_conflict_count,
        "relation_stream_count": relation_stream_count,
        "resolved_relation_stream_member_count": resolved_relation_stream_member_count,
        "relation_stream_conflict_count": relation_stream_conflict_count,
        "relation_reordered_page_count": relation_reordered_pages,
        "order_reordered_page_count": order_reordered_pages,
    }
    if structure_drives_image_semantics:
        semantic_layer["driver"] = "structure-json"
        semantic_layer["payload_kind"] = "structure-json"
        semantic_layer["source_visual_layer_role"] = "visual-fidelity-only"
    else:
        semantic_layer.setdefault("driver", "native-pdf" if document.source_type == "pdf" else "ocr-json")
        semantic_layer.setdefault("source_visual_layer_role", "visual-fidelity")
    document.metadata["semantic_layer"] = semantic_layer


def _collect_page_payloads(payload: Any) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []

    def visit(value: Any, fallback_page_index: int | None = None) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, index if fallback_page_index is None else fallback_page_index)
            return
        if not isinstance(value, dict):
            return

        child_fallback_page_index = _payload_page_index(value, fallback_page_index)
        if _has_blocks(value):
            page_payload = dict(value)
            if child_fallback_page_index is not None and page_payload.get("page_index") is None:
                page_payload["page_index"] = child_fallback_page_index
            collected.append(page_payload)

        for key in ("res", "raw_results", "pages", "results", "page_results", "data"):
            child = value.get(key)
            if child is not None:
                visit(child, child_fallback_page_index)

    visit(payload)
    return collected


def _collect_relation_payloads(payload: Any) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(value: Any, fallback_page_index: int | None = None) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, index if fallback_page_index is None else fallback_page_index)
            return
        if not isinstance(value, dict):
            return
        if id(value) in seen:
            return
        seen.add(id(value))

        child_fallback_page_index = _payload_page_index(value, fallback_page_index)
        if _has_relation_edges(value):
            page_payload = dict(value)
            if child_fallback_page_index is not None and page_payload.get("page_index") is None:
                page_payload["page_index"] = child_fallback_page_index
            collected.append(page_payload)

        for key in ("res", "raw_results", "pages", "results", "page_results", "data"):
            child = value.get(key)
            if child is not None:
                visit(child, child_fallback_page_index)

    visit(payload)
    return collected


def _payload_page_index(value: dict[str, Any], fallback_page_index: int | None) -> int | None:
    explicit_page_index = _explicit_page_index(value)
    if explicit_page_index is not None:
        return explicit_page_index
    paddle_input_page_index = _paddle_input_page_index(value)
    if paddle_input_page_index is not None:
        return paddle_input_page_index
    return fallback_page_index


def _has_relation_edges(value: dict[str, Any]) -> bool:
    for key in (
        "successor_edges",
        "successor_relations",
        "ro_linkings",
        "reading_order_edges",
        "reading_order_relations",
        "reading_order_linkings",
        "precedence_edges",
        "order_edges",
        "relations",
        "reading_streams",
        "streams",
    ):
        if isinstance(value.get(key), list):
            return True
    return False


def _iter_relation_edges(payload: dict[str, Any]) -> list[tuple[str, str, str, dict[str, Any]]]:
    edges: list[tuple[str, str, str, dict[str, Any]]] = []
    edges.extend(
        ("successor", source, target, raw)
        for source, target, raw in _relation_edges_from_any(
            _combined_relation_values(
                payload.get("successor_edges"),
                payload.get("successor_relations"),
                payload.get("ro_linkings"),
                payload.get("reading_order_edges"),
                payload.get("reading_order_relations"),
                payload.get("reading_order_linkings"),
            )
        )
    )
    edges.extend(
        ("precedence", source, target, raw)
        for source, target, raw in _relation_edges_from_any(
            _combined_relation_values(payload.get("precedence_edges"), payload.get("order_edges"))
        )
    )
    edges.extend(_typed_relation_edges_from_any(payload.get("relations")))

    for stream in _combined_relation_values(payload.get("reading_streams"), payload.get("streams")):
        if not isinstance(stream, dict):
            continue
        sequence = _texts_from_any(stream.get("text_sequence", stream.get("sequence", stream.get("texts", []))))
        for source, target in zip(sequence, sequence[1:], strict=False):
            edges.append(("successor", source, target, {"source": source, "target": target, "stream": True}))
        edges.extend(
            ("successor", source, target, raw)
            for source, target, raw in _relation_edges_from_any(
                _combined_relation_values(
                    stream.get("successor_edges"),
                    stream.get("successor_relations"),
                    stream.get("ro_linkings"),
                    stream.get("reading_order_edges"),
                    stream.get("reading_order_relations"),
                    stream.get("reading_order_linkings"),
                )
            )
        )
        edges.extend(
            ("precedence", source, target, raw)
            for source, target, raw in _relation_edges_from_any(
                _combined_relation_values(stream.get("precedence_edges"), stream.get("order_edges"))
            )
        )
        edges.extend(_typed_relation_edges_from_any(stream.get("relations")))
    return edges


def _iter_structure_streams(payload: dict[str, Any]) -> list[dict[str, Any]]:
    streams: list[dict[str, Any]] = []
    for value in _combined_relation_values(payload.get("reading_streams"), payload.get("streams")):
        if isinstance(value, dict):
            streams.append(value)
    return streams


def _page_relation_alias_map(page_payload: dict[str, Any]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key in ("document", "elements", "blocks", "parsing_res_list"):
        value = page_payload.get(key)
        if isinstance(value, list):
            _collect_relation_aliases(value, aliases, allow_index_alias=True)
    for key in ("table_res_list", "formula_res_list", "seal_res_list"):
        value = page_payload.get(key)
        if isinstance(value, list):
            _collect_relation_aliases(value, aliases)
    for key in ("overall_ocr_res", "text_paragraphs_ocr_res"):
        value = page_payload.get(key)
        if isinstance(value, dict):
            _collect_relation_aliases(value, aliases)
    layout = page_payload.get("layout_det_res")
    if isinstance(layout, dict) and isinstance(layout.get("boxes"), list):
        _collect_relation_aliases(layout["boxes"], aliases, allow_index_alias=True)
    return aliases


def _collect_relation_aliases(
    value: Any,
    aliases: dict[str, str],
    *,
    allow_index_alias: bool = False,
    list_index: int | None = None,
) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _collect_relation_aliases(
                item,
                aliases,
                list_index=index if allow_index_alias else None,
            )
        return
    if not isinstance(value, dict):
        return
    text = _relation_alias_text(value)
    if text:
        if list_index is not None:
            _add_relation_alias(aliases, list_index, text)
        for key in STRUCTURE_REF_KEYS:
            raw_id = value.get(key)
            if raw_id is not None:
                _add_relation_alias(aliases, raw_id, text)
    for key in (
        "children",
        "child_blocks",
        "sub_blocks",
        "sub_regions",
        "items",
        "cells",
        "blocks",
        "elements",
        "data",
        "table_ocr_pred",
        "overall_ocr_res",
        "text_paragraphs_ocr_res",
        "formula_res_list",
        "seal_res_list",
        "table_res_list",
    ):
        child = value.get(key)
        if isinstance(child, (dict, list)):
            _collect_relation_aliases(child, aliases)


def _relation_alias_text(value: dict[str, Any]) -> str:
    text = _extract_text(value)
    if text:
        return text
    rec_texts = value.get("rec_texts")
    if isinstance(rec_texts, list):
        return " ".join(str(text).strip() for text in rec_texts if str(text).strip())
    return ""


def _add_relation_alias(aliases: dict[str, str], raw_alias: Any, text: str) -> None:
    alias_key = _relation_key(raw_alias)
    if alias_key and text.strip():
        aliases.setdefault(alias_key, text.strip())


def _endpoint_alias(endpoint: Any, aliases: dict[str, str]) -> str:
    alias = aliases.get(_relation_key(endpoint), "")
    return alias if alias and _relation_key(alias) != _relation_key(endpoint) else ""


def _combined_relation_values(*values: Any) -> list[Any]:
    combined: list[Any] = []
    for value in values:
        if isinstance(value, list):
            combined.extend(value)
    return combined


def _relation_edges_from_any(values: list[Any]) -> list[tuple[str, str, dict[str, Any]]]:
    edges: list[tuple[str, str, dict[str, Any]]] = []
    for value in values:
        edge = _relation_edge_from_any(value)
        if edge is not None:
            edges.append(edge)
    return edges


def _relation_edge_from_any(value: Any) -> tuple[str, str, dict[str, Any]] | None:
    if isinstance(value, dict):
        source = _relation_endpoint(
            _first_present(
                value,
                ("source", "from", "src", "before", "head", "source_id", "from_id"),
            )
        )
        target = _relation_endpoint(
            _first_present(
                value,
                ("target", "to", "dst", "after", "tail", "target_id", "to_id"),
            )
        )
        if source and target:
            return source, target, dict(value)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        source = _relation_endpoint(value[0])
        target = _relation_endpoint(value[1])
        if source and target:
            return source, target, {"source": source, "target": target}
    return None


def _first_present(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def _typed_relation_edges_from_any(value: Any) -> list[tuple[str, str, str, dict[str, Any]]]:
    edges: list[tuple[str, str, str, dict[str, Any]]] = []
    if not isinstance(value, list):
        return edges
    for raw in value:
        edge = _relation_edge_from_any(raw)
        if edge is None:
            continue
        source, target, raw_edge = edge
        relation_type = ""
        if isinstance(raw, dict):
            relation_type = str(
                raw.get("relation")
                or raw.get("type")
                or raw.get("kind")
                or raw.get("edge_type")
                or raw.get("label")
                or ""
            ).strip().lower()
        if relation_type in {"successor", "successor_edge", "next", "adjacent", "follows"}:
            edges.append(("successor", source, target, raw_edge))
        elif relation_type in {"precedence", "precedence_edge", "before", "order", "ordering", "precedes"}:
            edges.append(("precedence", source, target, raw_edge))
    return edges


def _texts_from_any(value: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(value, list):
        for item in value:
            text = _relation_endpoint(item)
            if text:
                texts.append(text)
    return texts


def _stream_member_refs(stream: dict[str, Any]) -> list[str]:
    members: list[str] = []
    for key in ("text_sequence", "sequence", "texts", "elements", "items", "members", "children"):
        members.extend(_texts_from_any(stream.get(key)))
    for source, target, _raw in _relation_edges_from_any(
        _combined_relation_values(
            stream.get("successor_edges"),
            stream.get("successor_relations"),
            stream.get("ro_linkings"),
            stream.get("reading_order_edges"),
            stream.get("reading_order_relations"),
            stream.get("reading_order_linkings"),
        )
    ):
        members.extend([source, target])
    for source, target, _raw in _relation_edges_from_any(
        _combined_relation_values(stream.get("precedence_edges"), stream.get("order_edges"))
    ):
        members.extend([source, target])
    for _kind, source, target, _raw in _typed_relation_edges_from_any(stream.get("relations")):
        members.extend([source, target])
    return _dedupe_texts(members)


def _extract_stream_id(stream: dict[str, Any], page_index: int, stream_index: int) -> str:
    for key in ("stream_id", "id", "uid", "name", "ref", "self_ref"):
        value = stream.get(key)
        if value:
            return _slug_text(value)
    return f"external-{_extract_stream_type(stream)}-{page_index + 1:03d}-{stream_index:03d}"


def _extract_stream_type(stream: dict[str, Any]) -> str:
    for key in ("stream_type", "type", "role", "label", "kind"):
        value = stream.get(key)
        if value:
            return _normalize_external_stream_type(value)
    return "body"


def _normalize_external_stream_type(value: Any) -> str:
    token = _slug_text(value).replace("_", "-")
    if token in {"main", "body", "text", "paragraph", "content", "list", "article"}:
        return "body"
    if token in {"grid", "grid-island", "card", "card-grid", "content-grid", "product", "product-card", "product-grid", "tile", "tile-grid"}:
        return "grid-island"
    if token in {"table", "table-island", "table-body", "table-content", "table-grid"}:
        return "table-island"
    if token in {"footnote", "footnotes", "note", "notes"}:
        return "footnote"
    if token in {"figure-caption", "figure-title", "image-caption"}:
        return "caption-figure"
    if token in {"table-caption", "table-title"}:
        return "caption-table"
    if token in {"chart-caption", "chart-title"}:
        return "caption-chart"
    if token in {"algorithm-caption", "algorithm-title"}:
        return "caption-algorithm"
    if token in {"header", "page-header", "running-header"}:
        return "page-artifact-header"
    if token in {"footer", "page-footer", "page-number"}:
        return "page-artifact-footer"
    if "sidebar" in token or "side-bar" in token or token in {"marginalia", "margin-note"}:
        if "left" in token:
            return "sidebar-left"
        if "right" in token:
            return "sidebar-right"
        return "sidebar"
    return token or "body"


def _relation_endpoint(value: Any) -> str:
    if isinstance(value, dict):
        for key in (
            *STRUCTURE_REF_KEYS,
            "text",
            "source_text",
            "block_content",
            "content",
        ):
            endpoint = value.get(key)
            if endpoint is not None:
                return str(endpoint).strip()
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _relation_key(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def _slug_text(value: Any) -> str:
    text = "-".join(str(value or "").strip().lower().replace("_", "-").split())
    return "".join(char for char in text if char.isalnum() or char in {"-", "."}).strip("-") or "unknown"


def _has_blocks(value: dict[str, Any]) -> bool:
    if isinstance(value.get("document"), list):
        return True
    if isinstance(value.get("parsing_res_list"), list):
        return True
    if isinstance(value.get("blocks"), list):
        return True
    if isinstance(value.get("elements"), list):
        return True
    if isinstance(value.get("table_res_list"), list):
        return True
    if _has_paddle_ocr_results(value):
        return True
    layout = value.get("layout_det_res")
    return isinstance(layout, dict) and isinstance(layout.get("boxes"), list)


def _iter_blocks(page_payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    sequence_index = 0

    def add_block(
        raw_block: dict[str, Any],
        *,
        list_key: str,
        orderable: bool,
        list_index: int | None = None,
    ) -> None:
        nonlocal sequence_index
        if raw_block.get("_scriptorium_sidecar_reference") is True:
            return
        sequence_index += 1
        normalized_block = dict(raw_block)
        normalized_block.setdefault("_scriptorium_structure_list_key", list_key)
        if list_index is not None:
            normalized_block.setdefault("_scriptorium_structure_list_index", list_index)
        if orderable:
            normalized_block.setdefault("_scriptorium_structure_list_position", sequence_index)
        blocks.append(normalized_block)
        for child_key in _nested_block_keys():
            child_value = raw_block.get(child_key)
            if isinstance(child_value, list):
                for child_block in child_value:
                    if isinstance(child_block, dict):
                        add_block(child_block, list_key=child_key, orderable=orderable)
            elif isinstance(child_value, dict):
                add_block(child_value, list_key=child_key, orderable=orderable)

    for key in ("document", "parsing_res_list", "blocks", "elements"):
        value = page_payload.get(key)
        if isinstance(value, list):
            for block_index, block in enumerate(value):
                if not isinstance(block, dict):
                    continue
                add_block(
                    block,
                    list_key=key,
                    orderable=key != "document",
                    list_index=block_index if key in INDEX_ALIAS_LABEL_KEYS else None,
                )
    layout = page_payload.get("layout_det_res")
    if isinstance(layout, dict) and isinstance(layout.get("boxes"), list):
        for block_index, block in enumerate(layout["boxes"]):
            if not isinstance(block, dict):
                continue
            add_block(
                block,
                list_key="layout_det_res.boxes",
                orderable=False,
                list_index=block_index,
            )
    blocks.extend(_paddle_ocr_result_blocks(page_payload))
    blocks.extend(_paddle_table_cell_blocks(page_payload))
    return _dedupe_structure_blocks(blocks)


def _dedupe_structure_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for block in blocks:
        duplicate_index = _structure_duplicate_index(deduped, block)
        if duplicate_index is None:
            deduped.append(block)
            continue
        if _structure_block_dedupe_rank(block) > _structure_block_dedupe_rank(deduped[duplicate_index]):
            deduped[duplicate_index] = block
    return deduped


def _structure_duplicate_index(blocks: list[dict[str, Any]], block: dict[str, Any]) -> int | None:
    for index, existing in enumerate(blocks):
        if _structure_blocks_are_near_duplicates(existing, block):
            return index
    return None


def _structure_blocks_are_near_duplicates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_text = _dedupe_text_key(_extract_text(left))
    right_text = _dedupe_text_key(_extract_text(right))
    if not left_text or left_text != right_text:
        return False
    left_bbox_info = _extract_bbox(left)
    right_bbox_info = _extract_bbox(right)
    if left_bbox_info is None or right_bbox_info is None:
        return False
    left_bbox, left_space = left_bbox_info
    right_bbox, right_space = right_bbox_info
    if left_space != right_space:
        return False
    intersection = _bbox_intersection_area(left_bbox, right_bbox)
    if intersection <= 0:
        return False
    left_area = max(left_bbox.width * left_bbox.height, 1.0)
    right_area = max(right_bbox.width * right_bbox.height, 1.0)
    min_coverage = intersection / min(left_area, right_area)
    area_ratio = min(left_area, right_area) / max(left_area, right_area)
    return min_coverage >= 0.9 and area_ratio >= 0.25


def _structure_block_dedupe_rank(block: dict[str, Any]) -> tuple[int, int, float, int, float]:
    label = _normalize_structure_label(_extract_label(block))
    bbox_info = _extract_bbox(block)
    area = bbox_info[0].width * bbox_info[0].height if bbox_info is not None else 1_000_000.0
    list_key = str(block.get("_scriptorium_structure_list_key") or "")
    structured = 1 if list_key in {"parsing_res_list", "blocks", "elements", *_nested_block_keys()} else 0
    label_priority = {
        "formula": 6,
        "seal": 5,
        "stamp": 5,
        "table_cell": 5,
        "table": 4,
        "title": 3,
        "figure": 3,
        "image": 2,
        "text": 1,
        "paragraph": 1,
    }.get(label, 0)
    list_priority = {
        "formula_res_list": 4,
        "seal_res_list": 4,
        "table_res_list.table_cells": 3,
        "text_paragraphs_ocr_res": 2,
        "overall_ocr_res": 1,
    }.get(list_key, 0)
    confidence = _extract_confidence(block) or 0.0
    return (label_priority, structured, -area, list_priority, confidence)


def _dedupe_text_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _bbox_intersection_area(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
    return width * height


def _has_paddle_ocr_results(value: dict[str, Any]) -> bool:
    for key in ("overall_ocr_res", "text_paragraphs_ocr_res"):
        if isinstance(value.get(key), dict):
            return True
    for key in ("formula_res_list", "seal_res_list"):
        if isinstance(value.get(key), list):
            return True
    return False


def _paddle_ocr_result_blocks(page_payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for key in ("overall_ocr_res", "text_paragraphs_ocr_res"):
        result = page_payload.get(key)
        if isinstance(result, dict):
            blocks.extend(_paddle_rec_result_blocks(result, list_key=key, label="text"))

    formula_results = page_payload.get("formula_res_list")
    if isinstance(formula_results, list):
        for result_index, result in enumerate(formula_results):
            if isinstance(result, dict):
                block = _paddle_formula_result_block(result, result_index)
                if block is not None:
                    blocks.append(block)

    seal_results = page_payload.get("seal_res_list")
    if isinstance(seal_results, list):
        for result_index, result in enumerate(seal_results):
            if isinstance(result, dict):
                blocks.extend(
                    _paddle_rec_result_blocks(
                        result,
                        list_key="seal_res_list",
                        label="seal",
                        result_index=result_index,
                    )
                )
    return blocks


def _paddle_rec_result_blocks(
    result: dict[str, Any],
    *,
    list_key: str,
    label: str,
    result_index: int | None = None,
) -> list[dict[str, Any]]:
    boxes = _paddle_result_boxes(result)
    texts = _string_values(result.get("rec_texts"))
    scores = _float_values(result.get("rec_scores"))
    blocks: list[dict[str, Any]] = []
    for text_index, bbox in enumerate(boxes):
        text = texts[text_index] if text_index < len(texts) else ""
        if not text.strip():
            continue
        block: dict[str, Any] = {
            "block_label": label,
            "block_bbox": bbox.as_list(),
            "block_content": text.strip(),
            "confidence": scores[text_index] if text_index < len(scores) else None,
            "_scriptorium_structure_list_key": list_key,
            "paddle_text_index": text_index,
        }
        if result_index is not None:
            block["paddle_result_index"] = result_index
        for key in ("region_id", "seal_region_id", "layout_region_id", "block_id", "id"):
            value = result.get(key)
            if value is not None:
                block[key] = value
        blocks.append(block)
    return blocks


def _paddle_formula_result_block(result: dict[str, Any], result_index: int) -> dict[str, Any] | None:
    text = str(result.get("rec_formula") or result.get("formula") or "").strip()
    if not text:
        return None
    bbox = _first_bbox_from_any(
        result.get("rec_boxes"),
        result.get("rec_polys"),
        result.get("dt_polys"),
        result.get("bbox"),
        result.get("box"),
    )
    if bbox is None:
        return None
    block: dict[str, Any] = {
        "block_label": "formula",
        "block_bbox": bbox.as_list(),
        "block_content": text,
        "confidence": _first_float(result.get("rec_score"), result.get("score"), result.get("confidence")),
        "_scriptorium_structure_list_key": "formula_res_list",
        "paddle_result_index": result_index,
    }
    for key in ("formula_region_id", "region_id", "layout_region_id", "block_id", "id"):
        value = result.get(key)
        if value is not None:
            block[key] = value
    return block


def _paddle_result_boxes(result: dict[str, Any]) -> list[BBox]:
    for value in (
        result.get("rec_boxes"),
        result.get("rec_polys"),
        result.get("dt_polys"),
        result.get("boxes"),
        result.get("polys"),
    ):
        boxes = _bboxes_from_any_or_single(value)
        if boxes:
            return boxes
    return []


def _paddle_table_cell_blocks(page_payload: dict[str, Any]) -> list[dict[str, Any]]:
    table_results = page_payload.get("table_res_list")
    if not isinstance(table_results, list):
        return []

    blocks: list[dict[str, Any]] = []
    for table_index, table in enumerate(table_results):
        if not isinstance(table, dict):
            continue
        cells = _paddle_table_cells(table)
        if not cells:
            continue
        parent_order, parent_order_source = _paddle_table_parent_order(page_payload, table, table_index, cells)
        cell_positions = _table_cell_positions([bbox for bbox, _text, _score in cells])
        for cell_index, (bbox, text, score) in enumerate(cells):
            row_index, col_index, order_subindex = cell_positions[cell_index]
            block: dict[str, Any] = {
                "block_label": "table_cell",
                "block_bbox": bbox.as_list(),
                "block_content": text,
                "confidence": score,
                "_scriptorium_structure_list_key": "table_res_list.table_cells",
                "_scriptorium_structure_list_position": cell_index + 1,
                "_scriptorium_structure_order_subindex": order_subindex,
                "external_structure_table_ref": _paddle_table_ref(table, table_index),
                "external_structure_table_cell_row": row_index,
                "external_structure_table_cell_col": col_index,
                "external_structure_table_cell_index": cell_index,
            }
            if parent_order is not None:
                block["block_order"] = parent_order
                block["_scriptorium_structure_order_source"] = parent_order_source or "paddle-table-cell"
            blocks.append(block)
    return blocks


def _paddle_table_cells(table: dict[str, Any]) -> list[tuple[BBox, str, float | None]]:
    ocr_pred = table.get("table_ocr_pred")
    ocr_pred = ocr_pred if isinstance(ocr_pred, dict) else {}
    boxes = _paddle_table_cell_boxes(table, ocr_pred)
    texts = _string_values(ocr_pred.get("rec_texts"))
    scores = _float_values(ocr_pred.get("rec_scores"))

    cells: list[tuple[BBox, str, float | None]] = []
    for index, bbox in enumerate(boxes):
        text = texts[index] if index < len(texts) else ""
        score = scores[index] if index < len(scores) else None
        if not text.strip():
            continue
        cells.append((bbox, text.strip(), score))
    return cells


def _paddle_table_cell_boxes(table: dict[str, Any], ocr_pred: dict[str, Any]) -> list[BBox]:
    for value in (
        table.get("cell_box_list"),
        ocr_pred.get("rec_boxes"),
        ocr_pred.get("rec_polys"),
        ocr_pred.get("dt_polys"),
    ):
        boxes = _bboxes_from_list(value)
        if boxes:
            return boxes
    return []


def _bboxes_from_list(value: Any) -> list[BBox]:
    if not isinstance(value, list):
        return []
    boxes: list[BBox] = []
    for item in value:
        bbox = _bbox_from_any(item)
        if bbox is not None and bbox.width > 0 and bbox.height > 0:
            boxes.append(bbox)
    return boxes


def _bboxes_from_any_or_single(value: Any) -> list[BBox]:
    boxes = _bboxes_from_list(value)
    if boxes:
        return boxes
    bbox = _bbox_from_any(value)
    return [bbox] if bbox is not None and bbox.width > 0 and bbox.height > 0 else []


def _first_bbox_from_any(*values: Any) -> BBox | None:
    for value in values:
        boxes = _bboxes_from_any_or_single(value)
        if boxes:
            return boxes[0]
    return None


def _string_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _float_values(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    floats: list[float] = []
    for item in value:
        try:
            floats.append(float(item))
        except (TypeError, ValueError):
            continue
    return floats


def _first_float(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _paddle_table_parent_order(
    page_payload: dict[str, Any],
    table: dict[str, Any],
    table_index: int,
    cells: list[tuple[BBox, str, float | None]],
) -> tuple[int | None, str | None]:
    table_blocks = _paddle_page_table_blocks(page_payload)
    if not table_blocks:
        return None, None

    table_ref = _paddle_table_ref(table, table_index)
    for raw_block in table_blocks:
        block_keys = {_relation_key(value) for value in _paddle_block_refs(raw_block)}
        if _relation_key(table_ref) in block_keys:
            order = _paddle_parent_order(raw_block)
            return order, "paddle-table-cell" if order is not None else None

    if len(table_blocks) == 1 and len(_table_res_list(page_payload)) == 1:
        order = _paddle_parent_order(table_blocks[0])
        return order, "paddle-table-cell" if order is not None else None

    table_bbox = _paddle_table_bbox(table, cells)
    if table_bbox is None:
        return None, None
    best: tuple[float, dict[str, Any]] | None = None
    for raw_block in table_blocks:
        block_bbox_info = _extract_bbox(raw_block)
        if block_bbox_info is None:
            continue
        block_bbox, _coordinate_space = block_bbox_info
        coverage = _bbox_coverage(table_bbox, block_bbox)
        if best is None or coverage > best[0]:
            best = (coverage, raw_block)
    if best is None or best[0] < 0.5:
        return None, None
    order = _paddle_parent_order(best[1])
    return order, "paddle-table-cell" if order is not None else None


def _paddle_page_table_blocks(page_payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    sequence_index = 0
    for key in ("parsing_res_list", "blocks", "elements"):
        value = page_payload.get(key)
        if not isinstance(value, list):
            continue
        for raw_block in value:
            sequence_index += 1
            if isinstance(raw_block, dict) and _normalize_structure_label(_extract_label(raw_block)) in {
                "table",
                "table_body",
                "table_content",
            }:
                normalized_block = dict(raw_block)
                normalized_block.setdefault("_scriptorium_structure_list_key", key)
                normalized_block.setdefault("_scriptorium_structure_list_position", sequence_index)
                blocks.append(normalized_block)
    return blocks


def _paddle_parent_order(raw_block: dict[str, Any]) -> int | None:
    order = _extract_order(raw_block)
    return order if order is not None else _extract_implicit_order(raw_block)


def _table_res_list(page_payload: dict[str, Any]) -> list[Any]:
    value = page_payload.get("table_res_list")
    return value if isinstance(value, list) else []


def _paddle_table_ref(table: dict[str, Any], table_index: int) -> str:
    for key in ("table_region_id", "region_id", "layout_region_id", "table_id", "block_id", "id"):
        value = table.get(key)
        if value is not None:
            return str(value)
    return f"table_res_list:{table_index}"


def _paddle_block_refs(raw_block: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("table_region_id", "region_id", "layout_region_id", "table_id", "block_id", "id"):
        value = raw_block.get(key)
        if value is not None:
            refs.append(str(value))
    return refs


def _paddle_table_bbox(table: dict[str, Any], cells: list[tuple[BBox, str, float | None]]) -> BBox | None:
    for key in ("table_bbox", "bbox", "box", "block_bbox", "layout_bbox"):
        bbox = _bbox_from_any(table.get(key))
        if bbox is not None:
            return bbox
    return _union_bboxes([bbox for bbox, _text, _score in cells])


def _union_bboxes(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    return BBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
    )


def _table_cell_positions(boxes: list[BBox]) -> list[tuple[int, int, int]]:
    indexed = list(enumerate(boxes))
    indexed.sort(key=lambda item: (item[1].y0, item[1].x0, item[0]))
    heights = sorted(box.height for box in boxes if box.height > 0)
    median_height = heights[len(heights) // 2] if heights else 1.0
    row_tolerance = max(1.0, median_height * 0.65)
    rows: list[list[tuple[int, BBox]]] = []
    row_centers: list[float] = []
    for index, bbox in indexed:
        center_y = _center_y(bbox)
        target_row: int | None = None
        for row_index, row_center in enumerate(row_centers):
            if abs(center_y - row_center) <= row_tolerance:
                target_row = row_index
                break
        if target_row is None:
            rows.append([(index, bbox)])
            row_centers.append(center_y)
        else:
            rows[target_row].append((index, bbox))
            row_centers[target_row] = sum(_center_y(box) for _index, box in rows[target_row]) / len(rows[target_row])

    positions: list[tuple[int, int, int]] = [(0, 0, index + 1) for index in range(len(boxes))]
    max_cols = max((len(row) for row in rows), default=1)
    for row_index, row in enumerate(rows):
        for col_index, (original_index, _bbox) in enumerate(sorted(row, key=lambda item: (item[1].x0, item[0]))):
            positions[original_index] = (row_index, col_index, row_index * max_cols + col_index + 1)
    return positions


def _nested_block_keys() -> tuple[str, ...]:
    return (
        "children",
        "child_blocks",
        "sub_blocks",
        "sub_regions",
        "items",
        "cells",
        "blocks",
        "elements",
        "parsing_res_list",
    )


def _extract_page_index(payload: dict[str, Any], fallback: int) -> int:
    explicit_page_index = _explicit_page_index(payload)
    return explicit_page_index if explicit_page_index is not None else fallback


def _explicit_page_index(payload: dict[str, Any]) -> int | None:
    for key in ("page_index", "page", "page_no", "page_num"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            page_index = int(value)
        except (TypeError, ValueError):
            continue
        return max(page_index - 1, 0) if key in {"page", "page_no", "page_num"} and page_index > 0 else page_index
    return None


def _paddle_input_page_index(payload: dict[str, Any]) -> int | None:
    value = payload.get("input_path")
    if not isinstance(value, str) or not value.strip():
        return None
    filename = value.replace("\\", "/").rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0]
    match = PADDLE_INPUT_PAGE_RE.search(stem)
    if match is None:
        return None
    try:
        return max(int(match.group(1)) - 1, 0)
    except (TypeError, ValueError):
        return None


def _extract_bbox(raw: dict[str, Any]) -> tuple[BBox, str] | None:
    for key in ("block_bbox", "bbox_px", "coordinate", "bbox", "box", "layout_bbox"):
        value = raw.get(key)
        if value is None:
            continue
        bbox = _bbox_from_any(value)
        if bbox is None:
            continue
        coordinate_space = "pdf" if key == "bbox_pdf" or raw.get("coordinate_space") == "pdf" else "px"
        return bbox, coordinate_space
    value = raw.get("bbox_pdf")
    bbox = _bbox_from_any(value)
    return (bbox, "pdf") if bbox else None


def _bbox_from_any(value: Any) -> BBox | None:
    if value is None:
        return None
    try:
        return BBox.from_any(value)
    except (TypeError, ValueError):
        pass
    if isinstance(value, (list, tuple)):
        points: list[tuple[float, float]] = []
        if len(value) == 8 and all(isinstance(item, (int, float)) for item in value):
            points = [(float(value[index]), float(value[index + 1])) for index in range(0, 8, 2)]
        elif value and all(isinstance(item, (list, tuple)) and len(item) >= 2 for item in value):
            points = [(float(item[0]), float(item[1])) for item in value]
        if points:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))
    return None


def _normalize_region_bbox(bbox: BBox, coordinate_space: str, page: PageIR) -> tuple[BBox, BBox]:
    if coordinate_space == "pdf":
        bbox_pdf = clamp_bbox(bbox, page.width_pt, page.height_pt)
        bbox_px = clamp_bbox(pdf_to_px_bbox(bbox_pdf, page.scale_x, page.scale_y), page.width_px, page.height_px)
        return bbox_px, bbox_pdf
    bbox_px = clamp_bbox(bbox, page.width_px, page.height_px)
    bbox_pdf = clamp_bbox(px_to_pdf_bbox(bbox_px, page.scale_x, page.scale_y), page.width_pt, page.height_pt)
    return bbox_px, bbox_pdf


def _extract_label(raw: dict[str, Any]) -> str:
    for key in ("block_label", "label", "type", "category", "cls_name"):
        value = raw.get(key)
        if value:
            return str(value)
    if raw.get("_scriptorium_structure_list_key") == "document":
        return "text"
    return "unknown"


def _extract_text(raw: dict[str, Any]) -> str:
    for key in ("block_content", "text", "content", "rec_text", "rec_formula", "markdown", "html"):
        value = raw.get(key)
        if value:
            return str(value).strip()
    return ""


def _extract_order(raw: dict[str, Any]) -> int | None:
    for key in ("block_order", "order", "reading_order", "reading_order_index", "order_index"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_implicit_order(raw: dict[str, Any]) -> int | None:
    list_key = str(raw.get("_scriptorium_structure_list_key") or "")
    if list_key not in {"parsing_res_list", "blocks", "elements", "table_res_list.table_cells", *_nested_block_keys()}:
        return None
    if not _implicit_orderable_structure_block(raw):
        return None
    try:
        return int(raw.get("_scriptorium_structure_list_position"))
    except (TypeError, ValueError):
        return None


def _implicit_orderable_structure_block(raw: dict[str, Any]) -> bool:
    label = _normalize_structure_label(_extract_label(raw))
    return label in IMPLICIT_ORDERABLE_LABELS


def _extract_order_source(raw: dict[str, Any]) -> str:
    value = raw.get("_scriptorium_structure_order_source")
    return str(value) if value else "explicit"


def _extract_implicit_order_source(raw: dict[str, Any]) -> str:
    if str(raw.get("_scriptorium_structure_list_key") or "") == "table_res_list.table_cells":
        return "implicit-table-cell"
    return "implicit-list"


def _extract_confidence(raw: dict[str, Any]) -> float | None:
    for key in ("confidence", "score", "layout_score", "rec_score"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_source(payload: Any, raw_block: dict[str, Any] | None) -> str:
    for value in (
        raw_block.get("source") if isinstance(raw_block, dict) else None,
        payload.get("source") if isinstance(payload, dict) else None,
        payload.get("model") if isinstance(payload, dict) else None,
    ):
        if value:
            return str(value)
    return "external-structure"


def _apply_page_regions(
    page: PageIR,
    regions: list[StructureRegion],
    *,
    min_coverage: float,
    min_text_similarity: float,
) -> int:
    matched_count = 0
    for element in page.elements:
        if not element.source_text.strip():
            continue
        match = _best_region_match(element, regions)
        if match is None:
            continue
        region, coverage, text_similarity = match
        if coverage < min_coverage and text_similarity < min_text_similarity:
            continue
        element.metadata["structure_evidence"] = {
            "source": region.source,
            "label": region.label,
            "order": region.order,
            "order_source": region.order_source,
            "confidence": region.confidence,
            "bbox_pdf": region.bbox_pdf.as_list(),
            "bbox_px": region.bbox_px.as_list(),
            "coverage": round(coverage, 6),
            "text_similarity": round(text_similarity, 6),
        }
        element.metadata["external_structure_label"] = region.label
        element.metadata["external_structure_node_keys"] = _region_node_keys(region)
        if region.confidence is not None:
            element.metadata["external_structure_confidence"] = region.confidence
        if region.order is not None:
            element.metadata["external_structure_order"] = region.order
        if region.order_source is not None:
            element.metadata["external_structure_order_source"] = region.order_source
        _apply_external_structure_detail_metadata(element, region)
        _apply_external_structure_reading_metadata(element, page, region)
        matched_count += 1
    return matched_count


def _apply_page_relation_edges(page: PageIR, relations: list[StructureRelationEdge]) -> _RelationApplicationStats:
    stats = _RelationApplicationStats()
    if not relations:
        return stats
    text_elements = [element for element in page.elements if element.source_text.strip()]
    if len(text_elements) < 2:
        for relation in relations:
            stats.unresolved_edge_count += 1
            stats.unresolved_endpoint_count += 2
            stats.diagnostics.append(
                {
                    "kind": relation.kind,
                    "source_ref": relation.source_ref,
                    "target_ref": relation.target_ref,
                    "status": "unresolved-no-text-elements",
                }
            )
        return stats

    for relation in relations:
        source_resolution, source_alias_used, source_alias = _resolve_endpoint_with_alias(
            relation.source_ref,
            relation.raw.get("_scriptorium_source_alias"),
            text_elements,
        )
        target_resolution, target_alias_used, target_alias = _resolve_endpoint_with_alias(
            relation.target_ref,
            relation.raw.get("_scriptorium_target_alias"),
            text_elements,
        )
        diagnostic: dict[str, Any] = {
            "kind": relation.kind,
            "source_ref": relation.source_ref,
            "target_ref": relation.target_ref,
            "source_resolution": _endpoint_resolution_label(source_resolution, source_alias_used),
            "target_resolution": _endpoint_resolution_label(target_resolution, target_alias_used),
        }
        if source_resolution is not None:
            diagnostic["source_element_ids"] = source_resolution.element_ids
        else:
            stats.unresolved_endpoint_count += 1
        if target_resolution is not None:
            diagnostic["target_element_ids"] = target_resolution.element_ids
        else:
            stats.unresolved_endpoint_count += 1
        if source_resolution is None or target_resolution is None:
            stats.unresolved_edge_count += 1
            diagnostic["status"] = "unresolved"
            stats.diagnostics.append(diagnostic)
            continue

        source_ids = set(source_resolution.element_ids)
        target_ids = set(target_resolution.element_ids)
        if source_ids & target_ids:
            stats.skipped_overlap_edge_count += 1
            diagnostic["status"] = "skipped-overlapping-endpoints"
            stats.diagnostics.append(diagnostic)
            continue
        if _docling_body_relation_hits_specific_native_stream(
            relation,
            source_resolution,
            target_resolution,
        ):
            diagnostic["status"] = "skipped-specific-native-stream"
            stats.diagnostics.append(diagnostic)
            continue

        secondary_native_column_flow = _docling_body_relation_preserves_native_column_flow(
            relation,
            source_resolution,
            target_resolution,
        )

        if source_resolution.is_group or target_resolution.is_group:
            stats.resolved_group_edge_count += 1
        if source_resolution.is_group and not secondary_native_column_flow:
            stats.group_internal_edge_count += _apply_structure_group_internal_successors(
                source_resolution,
                reference=relation.source_ref,
            )
        if target_resolution.is_group and not secondary_native_column_flow:
            stats.group_internal_edge_count += _apply_structure_group_internal_successors(
                target_resolution,
                reference=relation.target_ref,
            )

        source_element = source_resolution.last
        target_element = target_resolution.first
        metadata_key = (
            "external_structure_successor_ids"
            if relation.kind == "successor"
            else "external_structure_precedence_target_ids"
        )
        _append_external_relation_target(source_element, target_element, metadata_key)

        relation_records = source_element.metadata.get("external_structure_relation_edges")
        if not isinstance(relation_records, list):
            relation_records = []
        record = {
            "kind": relation.kind,
            "target_id": target_element.id,
            "source_ref": relation.source_ref,
            "target_ref": relation.target_ref,
            "source": relation.source,
        }
        if source_resolution.is_group:
            record["source_group_element_ids"] = source_resolution.element_ids
        if target_resolution.is_group:
            record["target_group_element_ids"] = target_resolution.element_ids
        if source_alias_used:
            record["source_alias"] = source_alias
        if target_alias_used:
            record["target_alias"] = target_alias
        if source_alias_used or target_alias_used:
            record["resolved_via_alias"] = True
        if secondary_native_column_flow:
            record["secondary_native_column_flow"] = True
        if record not in relation_records:
            relation_records.append(record)
        source_element.metadata["external_structure_relation_edges"] = relation_records
        stats.resolved_count += 1
        if source_alias_used or target_alias_used:
            stats.alias_resolved_count += 1
        diagnostic["status"] = (
            "resolved-secondary-native-column-flow" if secondary_native_column_flow else "resolved"
        )
        diagnostic["source_boundary_element_id"] = source_element.id
        diagnostic["target_boundary_element_id"] = target_element.id
        stats.diagnostics.append(diagnostic)
    return stats


def _apply_page_streams(page: PageIR, streams: list[StructureReadingStream]) -> _StreamApplicationStats:
    stats = _StreamApplicationStats()
    if not streams:
        return stats
    text_elements = [element for element in page.elements if element.source_text.strip()]
    if not text_elements:
        for stream in streams:
            for ref in stream.member_refs:
                stats.unresolved_member_ref_count += 1
                stats.diagnostics.append(
                    {
                        "stream_id": stream.stream_id,
                        "member_ref": str(ref),
                        "status": "unresolved-no-text-elements",
                    }
                )
        return stats

    for stream in streams:
        stream_members: list[_ResolvedStreamMember] = []
        seen_member_ids: set[str] = set()
        member_aliases = stream.raw.get("_scriptorium_member_aliases")
        if not isinstance(member_aliases, dict):
            member_aliases = {}
        for ref in stream.member_refs:
            ref_text = str(ref)
            resolution, alias_used, alias_text = _resolve_endpoint_with_alias(
                ref,
                member_aliases.get(_relation_key(ref)),
                text_elements,
            )
            diagnostic: dict[str, Any] = {
                "stream_id": stream.stream_id,
                "member_ref": ref_text,
                "resolution": _endpoint_resolution_label(resolution, alias_used),
            }
            if resolution is None:
                stats.unresolved_member_ref_count += 1
                diagnostic["status"] = "unresolved"
                stats.diagnostics.append(diagnostic)
                continue
            if resolution.is_group:
                stats.resolved_group_member_ref_count += 1
            diagnostic["element_ids"] = resolution.element_ids
            duplicate_ids: list[str] = []
            resolved_ids: list[str] = []
            for group_index, element in enumerate(resolution.elements, start=1):
                if element.id in seen_member_ids:
                    duplicate_ids.append(element.id)
                    continue
                seen_member_ids.add(element.id)
                resolved_ids.append(element.id)
                stream_members.append(
                    _ResolvedStreamMember(
                        element=element,
                        reference=ref_text,
                        alias=alias_text,
                        alias_used=alias_used,
                        is_group=resolution.is_group,
                        group_index=group_index,
                        group_size=len(resolution.elements),
                    )
                )
            if duplicate_ids:
                stats.duplicate_member_ref_count += 1
                diagnostic["duplicate_element_ids"] = duplicate_ids
            diagnostic["resolved_element_ids"] = resolved_ids
            diagnostic["status"] = "resolved" if resolved_ids else "duplicate"
            stats.diagnostics.append(diagnostic)
        if not stream_members:
            continue
        for applied_stream, applied_members in _split_docling_body_stream_at_native_boundaries(
            stream,
            stream_members,
        ):
            for stream_index, member in enumerate(applied_members, start=1):
                element = member.element
                existing_stream_id = str(element.metadata.get("external_structure_stream_id") or "").strip()
                if existing_stream_id and existing_stream_id != applied_stream.stream_id:
                    conflicts = element.metadata.get("external_structure_stream_conflicts")
                    if not isinstance(conflicts, list):
                        conflicts = []
                    conflicts.append(
                        {
                            "existing_stream_id": existing_stream_id,
                            "stream_id": applied_stream.stream_id,
                            "stream_type": applied_stream.stream_type,
                            "source": applied_stream.source,
                        }
                    )
                    element.metadata["external_structure_stream_conflicts"] = conflicts
                    stats.conflict_count += 1
                    continue
                _apply_external_stream_metadata(
                    element,
                    applied_stream,
                    stream_index,
                    preserve_native_primary=_docling_body_stream_preserves_native_column_flow(
                        applied_stream,
                        element,
                    ),
                )
                element.metadata["external_structure_stream_member_ref"] = member.reference
                element.metadata["external_structure_stream_resolved_via_alias"] = member.alias_used
                element.metadata["external_structure_stream_member_resolution"] = (
                    "group" if member.is_group else "element"
                )
                if member.is_group:
                    element.metadata["external_structure_stream_member_group_index"] = member.group_index
                    element.metadata["external_structure_stream_member_group_size"] = member.group_size
                if member.alias_used:
                    element.metadata["external_structure_stream_member_alias"] = member.alias
                    stats.alias_resolved_count += 1
                stats.resolved_count += 1
    return stats


def _resolve_endpoint_with_alias(
    reference: Any,
    alias: Any,
    elements: list[ElementIR],
) -> tuple[_EndpointResolution | None, bool, str]:
    resolution = _resolve_relation_endpoint(reference, elements)
    if resolution is not None:
        return resolution, False, ""
    alias_text = str(alias or "").strip()
    if not alias_text:
        return None, False, ""
    resolution = _resolve_relation_endpoint(alias_text, elements)
    return resolution, resolution is not None, alias_text


def _endpoint_resolution_label(
    resolution: _EndpointResolution | None,
    alias_used: bool,
) -> str:
    if resolution is None:
        return "unresolved"
    if resolution.is_group:
        return "alias-group" if alias_used else "group"
    return "alias-element" if alias_used else "element"


def _apply_structure_group_internal_successors(
    resolution: _EndpointResolution,
    *,
    reference: str,
) -> int:
    if not resolution.is_group:
        return 0
    added_count = 0
    reference_text = str(reference)
    for group_index, element in enumerate(resolution.elements, start=1):
        memberships = element.metadata.get("external_structure_relation_groups")
        if not isinstance(memberships, list):
            memberships = []
        membership = {
            "ref": reference_text,
            "member_index": group_index,
            "member_count": len(resolution.elements),
        }
        if membership not in memberships:
            memberships.append(membership)
        element.metadata["external_structure_relation_groups"] = memberships
    for source_element, target_element in zip(resolution.elements, resolution.elements[1:], strict=False):
        if _append_external_relation_target(
            source_element,
            target_element,
            "external_structure_successor_ids",
        ):
            added_count += 1
        group_target_ids = _string_list(source_element.metadata.get("external_structure_group_successor_ids"))
        if target_element.id not in group_target_ids:
            group_target_ids.append(target_element.id)
        source_element.metadata["external_structure_group_successor_ids"] = group_target_ids
    return added_count


def _append_external_relation_target(
    source_element: ElementIR,
    target_element: ElementIR,
    metadata_key: str,
) -> bool:
    target_ids = _string_list(source_element.metadata.get(metadata_key))
    if target_element.id in target_ids:
        return False
    target_ids.append(target_element.id)
    source_element.metadata[metadata_key] = target_ids
    return True


def _apply_relation_derived_streams(page: PageIR, *, source: str) -> tuple[int, int, int]:
    text_elements = [element for element in page.elements if element.source_text.strip()]
    _ordered_ids, _participant_ids, relation_chains = _relation_order_for_elements(text_elements)
    if not relation_chains:
        return 0, 0, 0

    stream_count = 0
    resolved_count = 0
    conflict_count = 0
    for chain in relation_chains:
        stream_members = [
            element
            for element in (_element_by_id(text_elements, element_id) for element_id in chain)
            if element is not None
        ]
        if len(stream_members) < 2:
            continue
        if any(str(element.metadata.get("external_structure_stream_id") or "").strip() for element in stream_members):
            continue
        if any(_has_skipped_docling_body_stream(element) for element in stream_members):
            continue
        stream_count += 1
        stream_type = _relation_derived_stream_type(stream_members)
        stream = StructureReadingStream(
            page_index=page.page_index,
            stream_id=f"external-relation-{stream_type}-{page.page_index + 1:03d}-{stream_count:03d}",
            stream_type=stream_type,
            member_refs=tuple(element.id for element in stream_members),
            source=source,
            raw={"source": source, "relation_derived": True},
        )
        for stream_index, element in enumerate(stream_members, start=1):
            _apply_external_stream_metadata(element, stream, stream_index)
            element.metadata["external_structure_stream_relation_derived"] = True
            evidence = _reading_order_evidence(element)
            if "external-structure-relation-stream" not in evidence:
                evidence.append("external-structure-relation-stream")
            element.metadata["reading_order_evidence"] = evidence
            element.metadata["reading_order_evidence_summary"] = ",".join(evidence)
            resolved_count += 1
    return stream_count, resolved_count, conflict_count


def _element_by_id(elements: list[ElementIR], element_id: str) -> ElementIR | None:
    for element in elements:
        if element.id == element_id:
            return element
    return None


def _docling_body_relation_hits_specific_native_stream(
    relation: StructureRelationEdge,
    source: _EndpointResolution,
    target: _EndpointResolution,
) -> bool:
    if str(relation.raw.get("source_kind") or "") != "docling-body-tree":
        return False
    if str(relation.raw.get("docling_parent_scope") or "") != "body":
        return False
    return any(
        _has_specific_native_local_stream(element)
        for element in (*source.elements, *target.elements)
    )


def _docling_body_relation_preserves_native_column_flow(
    relation: StructureRelationEdge,
    source: _EndpointResolution,
    target: _EndpointResolution,
) -> bool:
    """Keep root-body relations as diagnostics when native columns are concrete.

    A root Docling body sequence is a useful local relation signal, but it is
    not strong enough to overwrite native column flow. The relation is retained
    on the element metadata and sidecar evidence, while the global path-cover
    reorder ignores it. Nested Docling groups remain executable.
    """

    if str(relation.raw.get("source_kind") or "") != "docling-body-tree":
        return False
    if str(relation.raw.get("docling_parent_scope") or "") != "body":
        return False
    return any(
        _has_concrete_native_column_flow(element)
        for element in (*source.elements, *target.elements)
    )


def _docling_body_stream_hits_specific_native_stream(
    stream: StructureReadingStream,
    element: ElementIR,
) -> bool:
    return (
        str(stream.raw.get("source_kind") or "") == "docling-body-tree"
        and str(stream.raw.get("docling_parent_scope") or "") == "body"
        and stream.stream_type == "body"
        and _has_specific_native_local_stream(element)
    )


def _docling_body_stream_preserves_native_column_flow(
    stream: StructureReadingStream,
    element: ElementIR,
) -> bool:
    """Keep root-body evidence secondary when native extraction has a column flow.

    A root Docling body run is useful evidence for local successors, but its
    serialization is weaker than a native stream that has already been assigned
    to a concrete column. Keep the model membership for diagnostics and relation
    evidence while leaving the native column as the primary translation stream.
    Nested Docling groups remain strong enough to own their local stream.
    """

    if (
        str(stream.raw.get("source_kind") or "") != "docling-body-tree"
        or str(stream.raw.get("docling_parent_scope") or "") != "body"
        or stream.stream_type != "body"
    ):
        return False
    return _has_concrete_native_column_flow(element)


def _has_concrete_native_column_flow(element: ElementIR) -> bool:
    metadata = element.metadata
    if str(metadata.get("reading_order_stream_type") or "body").strip() != "body":
        return False
    if (_optional_int(metadata.get("column_count")) or 1) < 2:
        return False
    if _optional_int(metadata.get("column_index")) is None:
        return False
    return str(metadata.get("column_span") or "").strip() not in {"", "full"}


def _split_docling_body_stream_at_native_boundaries(
    stream: StructureReadingStream,
    members: list[_ResolvedStreamMember],
) -> list[tuple[StructureReadingStream, list[_ResolvedStreamMember]]]:
    """Keep generic root-body streams from bridging stronger native islands.

    A root Docling ``body`` run may geometrically span a grid, table, caption,
    sidebar, or artifact that native extraction already identified.  Such a run
    is only generic evidence, so split it at the stronger local boundary rather
    than assigning one external stream id to disjoint text on both sides.
    """

    if not any(_docling_body_stream_hits_specific_native_stream(stream, member.element) for member in members):
        return [(stream, members)]

    segments: list[tuple[StructureReadingStream, list[_ResolvedStreamMember]]] = []
    pending: list[_ResolvedStreamMember] = []
    segment_index = 0

    def flush() -> None:
        nonlocal pending, segment_index
        if len(pending) >= 2:
            segment_index += 1
            segments.append((_docling_native_boundary_segment(stream, segment_index, pending), pending))
        elif pending:
            _record_skipped_docling_body_stream(
                pending[0].element,
                stream,
                pending[0],
                reason="native-boundary-singleton",
            )
        pending = []

    for member in members:
        if _docling_body_stream_hits_specific_native_stream(stream, member.element):
            flush()
            _record_skipped_docling_body_stream(
                member.element,
                stream,
                member,
                reason="specific-native-local-stream",
            )
            continue
        pending.append(member)
    flush()
    return segments


def _docling_native_boundary_segment(
    stream: StructureReadingStream,
    segment_index: int,
    members: list[_ResolvedStreamMember],
) -> StructureReadingStream:
    raw = dict(stream.raw)
    member_refs = tuple(member.reference for member in members)
    stream_id = f"{stream.stream_id}-native-segment-{segment_index:03d}"
    raw["docling_original_stream_id"] = stream.stream_id
    raw["docling_native_boundary_segment_index"] = segment_index
    raw["docling_native_boundary_policy"] = "split-at-specific-native-local-stream"
    raw["stream_id"] = stream_id
    raw["members"] = list(member_refs)
    return StructureReadingStream(
        page_index=stream.page_index,
        stream_id=stream_id,
        stream_type=stream.stream_type,
        member_refs=member_refs,
        source=stream.source,
        raw=raw,
    )


def _has_specific_native_local_stream(element: ElementIR) -> bool:
    metadata = element.metadata
    stream_type = str(metadata.get("reading_order_stream_type") or "").strip()
    if stream_type and stream_type != "body":
        return True
    scope = str(metadata.get("reading_order_scope") or "body").strip()
    if scope in {"page-artifact", "footnote", "sidebar"}:
        return True
    column_span = str(metadata.get("column_span") or "").strip()
    return column_span.startswith(("grid", "table", "caption", "artifact", "footnote", "sidebar"))


def _record_skipped_docling_body_stream(
    element: ElementIR,
    stream: StructureReadingStream,
    member: _ResolvedStreamMember,
    *,
    reason: str,
) -> None:
    skipped = element.metadata.get("external_structure_skipped_streams")
    if not isinstance(skipped, list):
        skipped = []
    record = {
        "stream_id": stream.stream_id,
        "stream_type": stream.stream_type,
        "source": stream.source,
        "member_ref": member.reference,
        "reason": reason,
    }
    if record not in skipped:
        skipped.append(record)
    element.metadata["external_structure_skipped_streams"] = skipped
    evidence = _reading_order_evidence(element)
    if "external-structure-stream-preserved-native" not in evidence:
        evidence.append("external-structure-stream-preserved-native")
    element.metadata["reading_order_evidence"] = evidence
    element.metadata["reading_order_evidence_summary"] = ",".join(evidence)


def _has_skipped_docling_body_stream(element: ElementIR) -> bool:
    skipped = element.metadata.get("external_structure_skipped_streams")
    return isinstance(skipped, list) and bool(skipped)


def _relation_derived_stream_type(elements: list[ElementIR]) -> str:
    scopes = {str(element.metadata.get("reading_order_scope") or "").strip() for element in elements}
    scopes.discard("")
    if scopes == {"footnote"}:
        return "footnote"
    if scopes == {"sidebar"}:
        sidebar_types = {
            str(element.metadata.get("reading_order_sidebar_type") or "").strip()
            for element in elements
        }
        sidebar_types.discard("")
        if len(sidebar_types) == 1:
            return f"sidebar-{next(iter(sidebar_types))}"
        return "sidebar"
    if scopes == {"page-artifact"}:
        artifact_types = {
            str(element.metadata.get("reading_order_artifact_type") or "").strip()
            for element in elements
        }
        artifact_types.discard("")
        if len(artifact_types) == 1:
            return f"page-artifact-{next(iter(artifact_types))}"
        return "page-artifact"

    spans = {str(element.metadata.get("column_span") or "").strip() for element in elements}
    spans.discard("")
    if spans and all("table" in span for span in spans):
        return "table-island"
    if spans and all("grid" in span or "card" in span for span in spans):
        return "grid-island"

    caption_types = {
        str(element.metadata.get("reading_order_caption_type") or "").strip()
        for element in elements
    }
    caption_types.discard("")
    if len(caption_types) == 1:
        return f"caption-{next(iter(caption_types))}"
    return "body"


def _apply_external_stream_metadata(
    element: ElementIR,
    stream: StructureReadingStream,
    stream_index: int,
    *,
    preserve_native_primary: bool = False,
) -> None:
    for key in ("reading_order_stream_id", "reading_order_stream_type", "reading_order_stream_index"):
        if key in element.metadata:
            element.metadata.setdefault(f"native_{key}", element.metadata.get(key))
    element.metadata["external_structure_stream_id"] = stream.stream_id
    element.metadata["external_structure_stream_type"] = stream.stream_type
    element.metadata["external_structure_stream_index"] = stream_index
    element.metadata["external_structure_stream_source"] = stream.source
    element.metadata["external_structure_stream_primary"] = not preserve_native_primary
    if not preserve_native_primary:
        element.metadata["reading_order_stream_id"] = stream.stream_id
        element.metadata["reading_order_stream_type"] = stream.stream_type
        element.metadata["reading_order_stream_index"] = stream_index
        _apply_external_stream_scope_metadata(element, stream.stream_type)
    evidence = _reading_order_evidence(element)
    if "external-structure-stream" not in evidence:
        evidence.append("external-structure-stream")
    if preserve_native_primary and "external-structure-stream-secondary" not in evidence:
        evidence.append("external-structure-stream-secondary")
    element.metadata["reading_order_evidence"] = evidence
    element.metadata["reading_order_evidence_summary"] = ",".join(evidence)


def _apply_external_stream_scope_metadata(element: ElementIR, stream_type: str) -> None:
    if stream_type == "footnote":
        element.metadata["reading_order_scope"] = "footnote"
        element.metadata["column_span"] = "footnote"
    elif stream_type.startswith("sidebar-"):
        element.metadata["reading_order_scope"] = "sidebar"
        element.metadata["reading_order_sidebar_type"] = stream_type.removeprefix("sidebar-")
        element.metadata["column_span"] = stream_type
    elif stream_type.startswith("page-artifact-"):
        element.metadata["reading_order_scope"] = "page-artifact"
        element.metadata["reading_order_artifact_type"] = stream_type.removeprefix("page-artifact-")
        element.metadata["column_span"] = stream_type
    elif stream_type.startswith("caption-"):
        element.metadata["reading_order_caption_type"] = stream_type.removeprefix("caption-")
        element.metadata["column_span"] = stream_type
    elif stream_type == "table-island":
        element.metadata["column_index"] = None
        element.metadata["column_span"] = "table-external"
    elif stream_type == "grid-island":
        element.metadata["column_index"] = None
        element.metadata["column_span"] = "grid-external"


def _resolve_relation_endpoint(
    endpoint: Any,
    elements: list[ElementIR],
) -> _EndpointResolution | None:
    key = _relation_key(endpoint)
    if not key:
        return None

    key_matches = [
        element
        for element in elements
        if key in _element_relation_keys(element)
    ]
    if len(key_matches) == 1:
        return _EndpointResolution(elements=(key_matches[0],))
    if len(key_matches) > 1:
        group_resolution = _shared_structure_group_resolution(key, key_matches)
        if group_resolution is not None:
            return group_resolution
        return None

    scored: list[tuple[float, ElementIR]] = []
    for element in elements:
        score = _relation_endpoint_text_similarity(endpoint, element.source_text)
        if score >= 0.92:
            scored.append((score, element))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return None
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 0.02:
        return None
    return _EndpointResolution(elements=(scored[0][1],))


def _relation_endpoint_text_similarity(endpoint: Any, source_text: str) -> float:
    """Score fallback endpoint text without letting one-character labels match prose.

    Exact ids/text keys are handled before this fallback. For non-exact values,
    a one-character OCR token such as ``A`` or ``B`` is too weak: ordinary
    prose often contains that character as a substring. Treating it as a fuzzy
    match silently turns unmapped structure references into wrong relations.
    """

    endpoint_key = _relation_key(endpoint)
    source_key = _relation_key(source_text)
    if not endpoint_key or not source_key:
        return 0.0
    if endpoint_key == source_key:
        return 1.0
    if min(len(endpoint_key), len(source_key)) < 4:
        return 0.0
    return _text_similarity(endpoint_key, source_key)


def _resolve_relation_endpoint_to_element(
    endpoint: str,
    elements: list[ElementIR],
) -> ElementIR | None:
    """Compatibility helper for callers that intentionally reject group endpoints."""

    resolution = _resolve_relation_endpoint(endpoint, elements)
    if resolution is None or resolution.is_group:
        return None
    return resolution.first


def _shared_structure_group_resolution(
    key: str,
    matches: list[ElementIR],
) -> _EndpointResolution | None:
    """Expand a model block only when every match belongs to one structure region.

    A raw text label such as ``Price`` may occur many times on a page and must
    remain ambiguous. A model block id/index, on the other hand, can validly
    cover several native text lines. The common region signature is the guard
    that keeps those two cases distinct.
    """

    if len(matches) < 2:
        return None
    if not all(key in _external_structure_node_keys(element) for element in matches):
        return None
    signatures = {_structure_group_signature(element) for element in matches}
    if None in signatures or len(signatures) != 1:
        return None
    ordered = tuple(sorted(matches, key=_structure_group_element_key))
    return _EndpointResolution(elements=ordered, is_group=True)


def _external_structure_node_keys(element: ElementIR) -> set[str]:
    node_keys = element.metadata.get("external_structure_node_keys")
    if not isinstance(node_keys, list):
        return set()
    return {_relation_key(item) for item in node_keys if _relation_key(item)}


def _structure_group_signature(element: ElementIR) -> tuple[Any, ...] | None:
    structure = element.metadata.get("structure_evidence")
    if not isinstance(structure, dict):
        return None
    bbox = structure.get("bbox_pdf")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        normalized_bbox = tuple(round(float(value), 6) for value in bbox)
    except (TypeError, ValueError):
        return None
    return (
        _relation_key(structure.get("source")),
        _relation_key(structure.get("label")),
        normalized_bbox,
        _relation_key(structure.get("text")),
        _relation_key(structure.get("order")),
    )


def _structure_group_element_key(element: ElementIR) -> tuple[int, int, int, float, float, str]:
    subindex = _optional_int(element.metadata.get("external_structure_order_subindex"))
    return (
        0 if subindex is not None else 1,
        subindex if subindex is not None else 0,
        int(element.reading_order),
        element.bbox_pdf.y0,
        element.bbox_pdf.x0,
        element.id,
    )


def _element_relation_keys(element: ElementIR) -> set[str]:
    keys = {
        _relation_key(element.id),
        _relation_key(element.source_text),
    }
    for key in STRUCTURE_REF_KEYS:
        value = element.metadata.get(key)
        if value is not None:
            keys.add(_relation_key(value))
    node_keys = element.metadata.get("external_structure_node_keys")
    if isinstance(node_keys, list):
        keys.update(_relation_key(item) for item in node_keys)
    structure = element.metadata.get("structure_evidence")
    if isinstance(structure, dict):
        for key in ("text", "label", "order"):
            if structure.get(key) is not None:
                keys.add(_relation_key(structure[key]))
    return {key for key in keys if key}


def _region_node_keys(region: StructureRegion) -> list[str]:
    keys: list[str] = []
    for key in STRUCTURE_REF_KEYS:
        value = region.raw.get(key)
        if value is not None:
            keys.append(str(value).strip())
    list_key = str(region.raw.get("_scriptorium_structure_list_key") or "")
    list_index = region.raw.get("_scriptorium_structure_list_index")
    if list_key in INDEX_ALIAS_LABEL_KEYS and list_index is not None:
        keys.append(str(list_index).strip())
    if region.order is not None:
        keys.extend([str(region.order), f"order:{region.order}"])
    if region.text:
        keys.append(region.text)
    return _dedupe_texts(keys)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _dedupe_texts(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = _relation_key(text)
        if not text or key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped


def _apply_external_structure_detail_metadata(element: ElementIR, region: StructureRegion) -> None:
    subindex = _optional_int(region.raw.get("_scriptorium_structure_order_subindex"))
    if subindex is not None:
        element.metadata["external_structure_order_subindex"] = subindex

    for raw_key, metadata_key in (
        ("docling_table_ref", "external_structure_table_ref"),
        ("external_structure_table_ref", "external_structure_table_ref"),
        ("external_structure_table_cell_index", "external_structure_table_cell_index"),
        ("external_structure_table_cell_row", "external_structure_table_cell_row"),
        ("external_structure_table_cell_col", "external_structure_table_cell_col"),
        ("external_structure_table_cell_row_span", "external_structure_table_cell_row_span"),
        ("external_structure_table_cell_col_span", "external_structure_table_cell_col_span"),
        ("external_structure_table_cell_column_header", "external_structure_table_cell_column_header"),
        ("external_structure_table_cell_row_header", "external_structure_table_cell_row_header"),
        ("external_structure_table_cell_row_section", "external_structure_table_cell_row_section"),
    ):
        if raw_key in region.raw:
            element.metadata[metadata_key] = region.raw[raw_key]


def _apply_external_structure_reading_metadata(
    element: ElementIR,
    page: PageIR,
    region: StructureRegion,
) -> None:
    normalized_label = _normalize_structure_label(region.label)
    if not normalized_label:
        return

    element.metadata.setdefault("reading_order_region_path", _external_region_path(page, region))
    evidence = _reading_order_evidence(element)
    for item in ("external-structure-label", f"external-structure-{normalized_label}"):
        if item not in evidence:
            evidence.append(item)
    element.metadata["reading_order_evidence"] = evidence
    element.metadata["reading_order_evidence_summary"] = ",".join(evidence)

    artifact_type = _external_artifact_type(normalized_label, element.bbox_pdf, page)
    if artifact_type:
        element.metadata["reading_order_scope"] = "page-artifact"
        element.metadata["reading_order_artifact_type"] = artifact_type
        element.metadata["column_index"] = None
        element.metadata["column_span"] = f"artifact-{artifact_type}"
        return

    if normalized_label in {"footnote", "footnotes"}:
        element.metadata["reading_order_scope"] = "footnote"
        element.metadata["column_index"] = None
        element.metadata["column_span"] = "footnote"
        return

    if normalized_label in {"sidebar", "sidebar_text", "side_bar", "marginalia", "margin_note"}:
        sidebar_type = "right" if _center_x(element.bbox_pdf) >= page.width_pt / 2 else "left"
        element.metadata["reading_order_scope"] = "sidebar"
        element.metadata["reading_order_sidebar_type"] = sidebar_type
        element.metadata["column_index"] = None
        element.metadata["column_span"] = f"sidebar-{sidebar_type}"
        return

    caption_type = _external_caption_type(normalized_label)
    if caption_type:
        element.metadata["reading_order_caption_type"] = caption_type
        element.metadata["column_span"] = (
            "caption-full" if element.bbox_pdf.width >= page.width_pt * 0.62 else "caption-column"
        )
        return

    if normalized_label in {"table", "table_body", "table_cell", "table_content"}:
        element.metadata["column_index"] = None
        element.metadata["column_span"] = "table-external"
        element.metadata["reading_order_region_path"] = _external_table_region_path(page, region)
        return

    if _external_grid_island_type(normalized_label):
        element.metadata["column_index"] = None
        element.metadata["column_span"] = "grid-external"
        element.metadata["reading_order_region_path"] = _external_grid_region_path(page, region)
        if "external-structure-grid-island" not in evidence:
            evidence.append("external-structure-grid-island")
            element.metadata["reading_order_evidence"] = evidence
            element.metadata["reading_order_evidence_summary"] = ",".join(evidence)


def _normalize_structure_label(label: str) -> str:
    return str(label or "").strip().lower().replace("-", "_").replace(" ", "_")


def _external_artifact_type(label: str, bbox: BBox, page: PageIR) -> str | None:
    if label in {"header", "running_header", "page_header", "header_text"}:
        return "header"
    if label in {"footer", "page_footer", "footer_text"}:
        return "footer"
    if label in {"page_number", "number"}:
        return "header" if _center_y(bbox) <= page.height_pt * 0.18 else "footer"
    return None


def _external_caption_type(label: str) -> str | None:
    if label in {"figure_caption", "figure_title", "figure_table_title", "image_caption"}:
        return "figure"
    if label in {"table_caption", "table_title"}:
        return "table"
    if label in {"chart_caption", "chart_title"}:
        return "chart"
    if label in {"algorithm_caption", "algorithm_title"}:
        return "algorithm"
    return None


def _external_grid_island_type(label: str) -> str | None:
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
        return "grid"
    return None


def _external_region_path(page: PageIR, region: StructureRegion) -> str:
    return f"external-structure/page-{page.page_index + 1:03d}/region-{_external_region_suffix(region)}"


def _external_table_region_path(page: PageIR, region: StructureRegion) -> str:
    return f"external-structure/page-{page.page_index + 1:03d}/table-island-external-{_external_region_suffix(region)}"


def _external_grid_region_path(page: PageIR, region: StructureRegion) -> str:
    return f"external-structure/page-{page.page_index + 1:03d}/grid-island-external-{_external_region_suffix(region)}"


def _external_region_suffix(region: StructureRegion) -> str:
    if region.order is not None:
        return f"{region.order:03d}"
    bbox_values = "-".join(str(round(value, 1)).replace(".", "_") for value in region.bbox_pdf.as_list())
    return bbox_values or "unknown"


def _center_x(bbox: BBox) -> float:
    return (bbox.x0 + bbox.x1) / 2


def _center_y(bbox: BBox) -> float:
    return (bbox.y0 + bbox.y1) / 2


def _best_region_match(element: ElementIR, regions: list[StructureRegion]) -> tuple[StructureRegion, float, float] | None:
    best: tuple[float, float, float, StructureRegion, float, float] | None = None
    for region in regions:
        coverage = _bbox_coverage(element.bbox_pdf, region.bbox_pdf)
        text_similarity = _text_similarity(element.source_text, region.text)
        score = coverage * 0.75 + text_similarity * 0.25
        specificity = -max(region.bbox_pdf.width * region.bbox_pdf.height, 1.0)
        ranking = (score, text_similarity, specificity, region, coverage, text_similarity)
        if best is None or ranking[:3] > best[:3]:
            best = ranking
    if best is None:
        return None
    _score, _similarity_rank, _specificity, region, coverage, text_similarity = best
    return region, coverage, text_similarity


def _bbox_coverage(inner: BBox, outer: BBox) -> float:
    intersection_width = max(0.0, min(inner.x1, outer.x1) - max(inner.x0, outer.x0))
    intersection_height = max(0.0, min(inner.y1, outer.y1) - max(inner.y0, outer.y0))
    intersection = intersection_width * intersection_height
    area = max(inner.width * inner.height, 1.0)
    return max(0.0, min(1.0, intersection / area))


def _text_similarity(left: str, right: str) -> float:
    left_text = " ".join(left.split()).lower()
    right_text = " ".join(right.split()).lower()
    if not left_text or not right_text:
        return 0.0
    if left_text in right_text or right_text in left_text:
        return 1.0
    return SequenceMatcher(None, left_text, right_text).ratio()


def external_structure_partial_order_for_elements(elements: list[Any]) -> list[int]:
    """Fuse sparse external block orders without treating them as a page permutation.

    Structure engines commonly omit page furniture, decorations, or unresolved
    text while still assigning an order to the blocks they recognize.  Sorting
    every element by that field turns an incomplete annotation into a full-page
    claim and moves all unmatched text to the end.  Instead, retain the native
    order as the topological tie-breaker and add precedence constraints only
    from one explicit order tier to the next.

    Multiple native/OCR lines matched to one structure region stay in their
    native local order.  Likewise, unmatched elements remain available at their
    native positions unless an explicit structure constraint requires a move.
    """

    if len(elements) < 2:
        return []

    base_order = _external_structure_base_order(elements)
    base_rank = {index: rank for rank, index in enumerate(base_order)}
    groups: dict[tuple[int, int, tuple[Any, ...]], list[int]] = {}
    for index, element in enumerate(elements):
        metadata = getattr(element, "metadata", None)
        if not isinstance(metadata, dict):
            continue
        order = _optional_int(metadata.get("external_structure_order"))
        if order is None or not _external_structure_order_participates(element):
            continue
        subindex = _optional_int(metadata.get("external_structure_order_subindex")) or 0
        identity = _external_structure_order_group_identity(element, order=order, subindex=subindex)
        groups.setdefault((order, subindex, identity), []).append(index)

    if len(groups) < 2:
        return []

    tiers: dict[tuple[int, int], list[list[int]]] = {}
    for (order, subindex, _identity), members in groups.items():
        members.sort(key=lambda index: base_rank[index])
        tiers.setdefault((order, subindex), []).append(members)
    ordered_tiers = sorted(tiers)
    if len(ordered_tiers) < 2:
        return []

    precedence_edges: list[tuple[int, int]] = []
    for previous_tier, next_tier in zip(ordered_tiers, ordered_tiers[1:]):
        previous_members = [index for group in tiers[previous_tier] for index in group]
        next_members = [index for group in tiers[next_tier] for index in group]
        precedence_edges.extend(
            (source, target)
            for source in previous_members
            for target in next_members
            if source != target
        )
    if not precedence_edges:
        return []

    ordered_indices, _path_cover_chains = relation_edge_candidate_path_cover(
        item_count=len(elements),
        successor_edges=[],
        precedence_edges=precedence_edges,
        base_order=base_order,
    )
    return ordered_indices


def _external_structure_base_order(elements: list[Any]) -> list[int]:
    return [
        index
        for index, _element in sorted(
            enumerate(elements),
            key=lambda item: (
                int(getattr(item[1], "reading_order", 0)),
                getattr(getattr(item[1], "bbox_pdf", None), "y0", 0.0),
                getattr(getattr(item[1], "bbox_pdf", None), "x0", 0.0),
                item[0],
            ),
        )
    ]


def _external_structure_order_group_identity(
    element: ElementIR,
    *,
    order: int,
    subindex: int,
) -> tuple[Any, ...]:
    """Return a stable identity for all native lines matched to one model block."""

    signature = _structure_group_signature(element)
    if signature is not None:
        return ("region", signature)
    node_keys = tuple(
        key
        for key in sorted(_external_structure_node_keys(element))
        if key not in {str(order), f"order:{order}"}
    )
    if node_keys:
        return ("nodes", *node_keys)
    return ("order-tier", order, subindex)


def _external_structure_order_participates(element: ElementIR) -> bool:
    """Keep generic model text order out of stronger native local structures.

    A PP layout model frequently labels every text line in a card grid or table
    as generic ``text``.  Native table/grid/caption routing is more specific in
    that case, so generic block order must not flatten those local streams.
    Model labels that explicitly identify a table or grid still participate.
    """

    metadata = element.metadata
    if _optional_int(metadata.get("external_structure_order")) is None:
        return False
    # Docling's full body traversal is useful region evidence, but only its
    # same-container local runs become executable relation edges.  Do not turn
    # a depth-first sequence across groups or pages into a global fallback.
    if str(metadata.get("external_structure_order_source") or "").strip() == "docling-body":
        return False
    label = _normalize_structure_label(str(metadata.get("external_structure_label") or ""))
    if label in {"table", "table_body", "table_cell", "table_content"}:
        return True
    if _external_grid_island_type(label):
        return True

    scope = str(metadata.get("reading_order_scope") or "").strip()
    if scope in {"page-artifact", "footnote", "sidebar"}:
        return False
    if metadata.get("reading_order_caption_type") or _external_caption_type(label):
        return False
    column_span = str(metadata.get("column_span") or "").strip()
    if column_span.startswith(("grid", "table", "artifact", "footnote", "sidebar")):
        return False
    return True


def _reorder_page_from_regions(page: PageIR) -> str | None:
    text_elements = [element for element in page.elements if element.source_text.strip()]
    relation_order, relation_participant_ids, _relation_chains = _relation_order_for_elements(text_elements)
    if relation_order:
        _apply_reordered_text_order(
            relation_order,
            text_elements,
            strategy="external-structure-relation-fusion-v1",
            evidence_label="external-structure-relation",
            participant_ids=relation_participant_ids,
        )
        return "relation"

    ordered_elements = [
        element
        for element in text_elements
        if _external_structure_order_participates(element)
    ]
    ordered_indices = external_structure_partial_order_for_elements(text_elements)
    if not ordered_indices:
        return None

    base_order = _external_structure_base_order(text_elements)
    if ordered_indices == base_order:
        return None
    ordered_ids = [text_elements[index].id for index in ordered_indices]
    _apply_reordered_text_order(
        ordered_ids,
        text_elements,
        strategy="external-structure-partial-order-fusion-v2",
        evidence_label="external-structure-partial-order",
        participant_ids={element.id for element in ordered_elements},
    )
    return "order"


def _relation_order_for_elements(text_elements: list[ElementIR]) -> tuple[list[str], set[str], list[list[str]]]:
    if len(text_elements) < 2:
        return [], set(), []
    id_to_index = {element.id: index for index, element in enumerate(text_elements)}
    successor_edges: list[tuple[int, int]] = []
    precedence_edges: list[tuple[int, int]] = []
    participant_ids: set[str] = set()
    for source_index, element in enumerate(text_elements):
        for target_id in _string_list(element.metadata.get("external_structure_successor_ids")):
            if _relation_target_is_secondary_native_column_flow(element, target_id, kind="successor"):
                continue
            target_index = id_to_index.get(target_id)
            if target_index is None:
                continue
            successor_edges.append((source_index, target_index))
            participant_ids.update({element.id, target_id})
        for target_id in _string_list(element.metadata.get("external_structure_precedence_target_ids")):
            if _relation_target_is_secondary_native_column_flow(element, target_id, kind="precedence"):
                continue
            target_index = id_to_index.get(target_id)
            if target_index is None:
                continue
            precedence_edges.append((source_index, target_index))
            participant_ids.update({element.id, target_id})
    if not successor_edges and not precedence_edges:
        return [], set(), []

    base_order = [
        index
        for index, _element in sorted(
            enumerate(text_elements),
            key=lambda item: (
                item[1].reading_order,
                item[1].bbox_pdf.y0,
                item[1].bbox_pdf.x0,
                item[0],
            ),
        )
    ]
    ordered_indices, path_cover_chains = relation_edge_candidate_path_cover(
        item_count=len(text_elements),
        successor_edges=successor_edges,
        precedence_edges=precedence_edges,
        base_order=base_order,
    )
    if not ordered_indices:
        return [], set(), []
    stream_chains = [
        [text_elements[index].id for index in chain]
        for chain in path_cover_chains
        if len(chain) >= 2 and any((source, target) in successor_edges for source in chain for target in chain)
    ]
    return [text_elements[index].id for index in ordered_indices], participant_ids, stream_chains


def _relation_target_is_secondary_native_column_flow(
    source: ElementIR,
    target_id: str,
    *,
    kind: str,
) -> bool:
    records = source.metadata.get("external_structure_relation_edges")
    if not isinstance(records, list):
        return False
    matching_records = [
        record
        for record in records
        if isinstance(record, dict)
        and str(record.get("kind") or "") == kind
        and str(record.get("target_id") or "") == target_id
    ]
    return bool(matching_records) and all(
        record.get("secondary_native_column_flow") is True
        for record in matching_records
    )


def _apply_reordered_text_order(
    ordered_ids: list[str],
    text_elements: list[ElementIR],
    *,
    strategy: str,
    evidence_label: str,
    participant_ids: set[str],
) -> None:
    element_by_id = {element.id: element for element in text_elements}
    ordered_text = [element_by_id[element_id] for element_id in ordered_ids if element_id in element_by_id]
    if len(ordered_text) != len(text_elements):
        return

    old_order_by_id = {element.id: element.reading_order for element in text_elements}
    for new_order, element in enumerate(ordered_text, start=1):
        previous_order = old_order_by_id[element.id]
        element.metadata.setdefault("native_reading_order", previous_order)
        element.metadata["semantic_order"] = new_order
        if element.id in participant_ids:
            element.metadata.setdefault(
                "native_reading_order_strategy",
                element.metadata.get("reading_order_strategy", "unknown"),
            )
            element.metadata["reading_order_strategy"] = strategy
            evidence = _reading_order_evidence(element)
            if evidence_label not in evidence:
                evidence.append(evidence_label)
            element.metadata["reading_order_evidence"] = evidence
            element.metadata["reading_order_evidence_summary"] = ",".join(evidence)
            element.metadata["reading_order_confidence"] = max(
                float(element.metadata.get("reading_order_confidence") or 0.0),
                float(element.metadata.get("external_structure_confidence") or 0.0),
            )
        element.reading_order = new_order


def _reading_order_evidence(element: ElementIR) -> list[str]:
    evidence = element.metadata.get("reading_order_evidence")
    if not isinstance(evidence, list):
        return []
    return [str(item) for item in evidence if str(item).strip()]
