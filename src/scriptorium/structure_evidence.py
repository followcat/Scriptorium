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
    `block_content`, and `block_order`.
    """

    regions: list[StructureRegion] = []
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
