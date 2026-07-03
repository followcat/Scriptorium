from __future__ import annotations

import json
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
        if page_index < 0 or page_index >= len(document.pages):
            continue
        page = document.pages[page_index]
        for raw_block in _iter_blocks(page_payload):
            bbox_info = _extract_bbox(raw_block)
            if bbox_info is None:
                continue
            bbox, coordinate_space = bbox_info
            bbox_px, bbox_pdf = _normalize_region_bbox(bbox, coordinate_space, page)
            if bbox_px.width <= 0 or bbox_px.height <= 0:
                continue
            regions.append(
                StructureRegion(
                    page_index=page_index,
                    label=_extract_label(raw_block),
                    bbox_px=bbox_px,
                    bbox_pdf=bbox_pdf,
                    order=_extract_order(raw_block),
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
        if page_index < 0 or page_index >= len(document.pages):
            continue
        page = document.pages[page_index]
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
                page_index=page_index,
                label=_extract_label(item),
                bbox_px=bbox_px,
                bbox_pdf=normalized_bbox_pdf,
                order=order,
                text=_extract_docling_text(item),
                confidence=_extract_confidence(item) or _extract_confidence(prov),
                source=source,
                raw=raw,
            )
        )
    return regions


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
    regions = normalize_structure_evidence(payload, document, source=source)
    regions_by_page: dict[int, list[StructureRegion]] = {}
    for region in regions:
        regions_by_page.setdefault(region.page_index, []).append(region)

    matched_count = 0
    reordered_pages = 0
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
        "source": source or _extract_source(payload, None),
        "region_count": len(regions),
        "matched_element_count": matched_count,
        "reordered_page_count": reordered_pages,
        "regions_by_page": [
            {
                "page_index": page_index,
                "regions": [region.as_metadata() for region in page_regions],
            }
            for page_index, page_regions in sorted(regions_by_page.items())
        ],
    }
    document.revisions.append(
        RevisionIR(
            reason="structure-evidence-fusion",
            payload={
                "source": source or _extract_source(payload, None),
                "region_count": len(regions),
                "matched_element_count": matched_count,
                "reordered_page_count": reordered_pages,
            },
        )
    )
    return document


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
    for key in ("parsing_res_list", "blocks", "elements"):
        value = page_payload.get(key)
        if isinstance(value, list):
            blocks.extend(block for block in value if isinstance(block, dict))
    layout = page_payload.get("layout_det_res")
    if isinstance(layout, dict) and isinstance(layout.get("boxes"), list):
        blocks.extend(block for block in layout["boxes"] if isinstance(block, dict))
    return blocks


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
    for key in ("block_order", "order", "reading_order"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
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
        matched_count += 1
    return matched_count


def _best_region_match(element: ElementIR, regions: list[StructureRegion]) -> tuple[StructureRegion, float, float] | None:
    best: tuple[float, StructureRegion, float, float] | None = None
    for region in regions:
        coverage = _bbox_coverage(element.bbox_pdf, region.bbox_pdf)
        text_similarity = _text_similarity(element.source_text, region.text)
        score = coverage * 0.75 + text_similarity * 0.25
        if best is None or score > best[0]:
            best = (score, region, coverage, text_similarity)
    if best is None:
        return None
    _score, region, coverage, text_similarity = best
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
