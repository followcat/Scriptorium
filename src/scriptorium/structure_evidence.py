from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .geometry import clamp_bbox, pdf_to_px_bbox, px_to_pdf_bbox
from .models import BBox, DocumentIR, ElementIR, PageIR, RevisionIR


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
            order_source = "explicit" if block_order is not None else None
            if block_order is None:
                block_order = _extract_implicit_order(raw_block)
                order_source = "implicit-list" if block_order is not None else None
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
    if not isinstance(body, dict):
        return []

    ref_index = _build_docling_ref_index(doc)
    regions: list[StructureRegion] = []
    emitted: set[tuple[str, int, tuple[float, float, float, float]]] = set()
    order_counter = 0

    def traverse(node: Any, current_ref: str | None = None) -> None:
        nonlocal order_counter
        item, ref = _resolve_docling_node(node, doc, ref_index, current_ref)
        if not isinstance(item, dict):
            return

        ref_kind = _docling_ref_kind(ref or item.get("self_ref"))
        if ref_kind != "groups":
            item_regions = _docling_item_regions(
                item,
                document,
                order=order_counter + 1,
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
                order_counter += 1

        children = item.get("children")
        if isinstance(children, list):
            for child in children:
                traverse(child)

    traverse(body, "#/body")
    return regions


def _build_docling_ref_index(doc: dict[str, Any]) -> dict[str, Any]:
    index: dict[str, Any] = {"#/body": doc.get("body")}
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
    order: int,
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
                order_source="docling-body",
                text=_extract_docling_text(item),
                confidence=_extract_confidence(item) or _extract_confidence(prov),
                source=source,
                raw=raw,
            )
        )
    return regions


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
    regions = normalize_structure_evidence(payload, document, source=source)
    regions_by_page: dict[int, list[StructureRegion]] = {}
    for region in regions:
        regions_by_page.setdefault(region.page_index, []).append(region)

    matched_count = 0
    reordered_pages = 0
    order_source_counts = Counter(str(region.order_source or "none") for region in regions)
    for page in document.pages:
        page_regions = regions_by_page.get(page.page_index, [])
        if not page_regions:
            continue
        page_matches = _apply_page_regions(
            page,
            page_regions,
            min_coverage=min_coverage,
            min_text_similarity=min_text_similarity,
        )
        matched_count += page_matches
        if reorder and _reorder_page_from_regions(page):
            reordered_pages += 1

    document.metadata["structure_evidence"] = {
        "version": "v1",
        "source": source_name,
        "region_count": len(regions),
        "matched_element_count": matched_count,
        "reordered_page_count": reordered_pages,
        "order_source_counts": dict(sorted(order_source_counts.items())),
        "regions_by_page": [
            {
                "page_index": page_index,
                "regions": [region.as_metadata() for region in page_regions],
            }
            for page_index, page_regions in sorted(regions_by_page.items())
        ],
    }
    _update_semantic_layer_metadata(
        document,
        source=source_name,
        region_count=len(regions),
        matched_count=matched_count,
        reordered_pages=reordered_pages,
        order_source_counts=dict(sorted(order_source_counts.items())),
    )
    document.revisions.append(
        RevisionIR(
            reason="structure-evidence-fusion",
            payload={
                "source": source_name,
                "region_count": len(regions),
                "matched_element_count": matched_count,
                "reordered_page_count": reordered_pages,
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
) -> None:
    current = document.metadata.get("semantic_layer")
    semantic_layer = dict(current) if isinstance(current, dict) else {}
    semantic_layer["structure_json"] = {
        "source": source,
        "role": "semantic-driver" if document.source_type == "image" and region_count > 0 else "augmenting-evidence",
        "region_count": region_count,
        "matched_element_count": matched_count,
        "reordered_page_count": reordered_pages,
        "order_source_counts": order_source_counts,
    }
    if document.source_type == "image" and region_count > 0:
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

        if _has_blocks(value):
            page_payload = dict(value)
            if fallback_page_index is not None and page_payload.get("page_index") is None:
                page_payload["page_index"] = fallback_page_index
            collected.append(page_payload)

        for key in ("res", "raw_results", "pages", "results", "page_results", "data"):
            child = value.get(key)
            if child is not None:
                visit(child, fallback_page_index)

    visit(payload)
    return collected


def _has_blocks(value: dict[str, Any]) -> bool:
    if isinstance(value.get("parsing_res_list"), list):
        return True
    if isinstance(value.get("blocks"), list):
        return True
    if isinstance(value.get("elements"), list):
        return True
    layout = value.get("layout_det_res")
    return isinstance(layout, dict) and isinstance(layout.get("boxes"), list)


def _iter_blocks(page_payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    sequence_index = 0

    def add_block(raw_block: dict[str, Any], *, list_key: str, orderable: bool) -> None:
        nonlocal sequence_index
        sequence_index += 1
        normalized_block = dict(raw_block)
        normalized_block.setdefault("_scriptorium_structure_list_key", list_key)
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

    for key in ("parsing_res_list", "blocks", "elements"):
        value = page_payload.get(key)
        if isinstance(value, list):
            for block in value:
                if not isinstance(block, dict):
                    continue
                add_block(block, list_key=key, orderable=True)
    layout = page_payload.get("layout_det_res")
    if isinstance(layout, dict) and isinstance(layout.get("boxes"), list):
        for block in layout["boxes"]:
            if not isinstance(block, dict):
                continue
            add_block(block, list_key="layout_det_res.boxes", orderable=False)
    return blocks


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
    for key in ("page_index", "page", "page_no", "page_num"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            page_index = int(value)
        except (TypeError, ValueError):
            continue
        return max(page_index - 1, 0) if key in {"page", "page_no", "page_num"} and page_index > 0 else page_index
    return fallback


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
    return "unknown"


def _extract_text(raw: dict[str, Any]) -> str:
    for key in ("block_content", "text", "content", "rec_text", "markdown", "html"):
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
    if list_key not in {"parsing_res_list", "blocks", "elements", *_nested_block_keys()}:
        return None
    try:
        return int(raw.get("_scriptorium_structure_list_position"))
    except (TypeError, ValueError):
        return None


def _extract_confidence(raw: dict[str, Any]) -> float | None:
    for key in ("confidence", "score", "layout_score"):
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
        if region.confidence is not None:
            element.metadata["external_structure_confidence"] = region.confidence
        if region.order is not None:
            element.metadata["external_structure_order"] = region.order
        if region.order_source is not None:
            element.metadata["external_structure_order_source"] = region.order_source
        _apply_external_structure_reading_metadata(element, page, region)
        matched_count += 1
    return matched_count


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


def _reorder_page_from_regions(page: PageIR) -> bool:
    text_elements = [element for element in page.elements if element.source_text.strip()]
    ordered_elements = [element for element in text_elements if element.metadata.get("external_structure_order") is not None]
    distinct_orders = {int(element.metadata["external_structure_order"]) for element in ordered_elements}
    if len(distinct_orders) < 2:
        return False

    old_order_by_id = {element.id: element.reading_order for element in text_elements}
    sorted_text = sorted(
        text_elements,
        key=lambda element: (
            int(element.metadata.get("external_structure_order") or 1_000_000),
            old_order_by_id[element.id],
            element.bbox_pdf.y0,
            element.bbox_pdf.x0,
        ),
    )
    for new_order, element in enumerate(sorted_text, start=1):
        previous_order = old_order_by_id[element.id]
        element.metadata.setdefault("native_reading_order", previous_order)
        element.metadata["semantic_order"] = new_order
        if element.metadata.get("external_structure_order") is not None:
            element.metadata.setdefault(
                "native_reading_order_strategy",
                element.metadata.get("reading_order_strategy", "unknown"),
            )
            element.metadata["reading_order_strategy"] = "external-structure-fusion-v1"
            evidence = _reading_order_evidence(element)
            if "external-structure-order" not in evidence:
                evidence.append("external-structure-order")
            element.metadata["reading_order_evidence"] = evidence
            element.metadata["reading_order_evidence_summary"] = ",".join(evidence)
            element.metadata["reading_order_confidence"] = max(
                float(element.metadata.get("reading_order_confidence") or 0.0),
                float(element.metadata.get("external_structure_confidence") or 0.0),
            )
        element.reading_order = new_order
    return True


def _reading_order_evidence(element: ElementIR) -> list[str]:
    evidence = element.metadata.get("reading_order_evidence")
    if not isinstance(evidence, list):
        return []
    return [str(item) for item in evidence if str(item).strip()]
