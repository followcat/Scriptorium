from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import fitz
from PIL import Image

from .geometry import clamp_bbox, pdf_to_px_bbox, px_to_pdf_bbox, reading_order_key
from .models import BBox, DocumentIR, ElementIR, PageIR, RevisionIR
from .paddle_json import normalize_paddleocr_vl_payload
from .pdf_render import RenderedDocument, RenderedPage
from .reading_order import infer_semantic_reading_order


TYPE_ALIASES = {
    "plain text": "text",
    "paragraph": "text",
    "text": "text",
    "title": "title",
    "table": "table",
    "table_cell": "table",
    "table content": "table",
    "table_content": "table",
    "figure": "figure",
    "formula": "formula",
    "image": "image",
    "layout": "layout",
    "seal": "text",
    "stamp": "text",
}


# When a dense OCR layer already exists, generic model text regions are useful
# evidence but not safe extra anchors: they commonly describe page chrome,
# decorative cards, or a merged paragraph. Specific semantic regions can fill
# a genuine OCR hole without multiplying overlapping editable text nodes.
STRUCTURE_COMPLETION_LABELS = frozenset(
    {
        "abstract",
        "algorithm",
        "code",
        "doc_title",
        "equation",
        "formula",
        "list",
        "list_item",
        "paragraph_title",
        "section_header",
        "section_title",
        "table",
        "table_body",
        "table_cell",
        "table_content",
        "title",
    }
)


def load_ocr_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    normalized = normalize_paddleocr_vl_payload(payload)
    result = normalized if isinstance(normalized, dict) else payload
    # The loader is an explicit role boundary. A payload may contain
    # ``document`` anchors and still be used as OCR/layout input rather than as
    # the provider that owns semantic structure.
    result["_scriptorium_payload_kind"] = "ocr-json"
    return result


def write_ocr_json(payload: Mapping[str, Any], path: str | Path) -> Path:
    """Persist an OCR/structure payload without losing non-ASCII text."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return target


def normalize_ocr_to_ir(
    rendered: RenderedDocument,
    ocr_payload: dict[str, Any] | None = None,
    crop_dir: str | Path | None = None,
    include_source_image: bool | None = None,
    ocr_fallback: str = "off",
    ocr_language: str = "eng+chi_sim",
    ocr_dpi: int = 144,
) -> DocumentIR:
    ocr_payload = ocr_payload or {}
    by_page = _group_ocr_elements_by_page(ocr_payload)
    payload_kind = _semantic_payload_kind(ocr_payload) if ocr_payload else None
    include_source_image = rendered.source_type == "image" if include_source_image is None else include_source_image
    crop_root = Path(crop_dir) if crop_dir is not None else None
    if crop_root is not None:
        crop_root.mkdir(parents=True, exist_ok=True)

    pages: list[PageIR] = []
    page_diagnostics: list[dict[str, Any]] = []
    for rendered_page in rendered.pages:
        raw_elements = list(by_page.get(rendered_page.page_index, []))
        payload_text_anchor_count = _raw_text_anchor_count(raw_elements)
        structure_anchor_count = payload_text_anchor_count if payload_kind == "structure-json" else 0
        completion_stats = _empty_anchor_completion_stats()
        ocr_status = "not-needed" if payload_text_anchor_count else "not-candidate"
        ocr_language_used: str | None = None
        ocr_error: str | None = None
        if rendered.source_type == "image":
            if ocr_fallback == "image-only":
                should_complete_structure = payload_kind == "structure-json" and bool(raw_elements)
                if not payload_text_anchor_count or should_complete_structure:
                    fallback_elements, ocr_language_used, ocr_error = _ocr_image_raw_elements(
                        rendered_page,
                        language=ocr_language,
                        dpi=ocr_dpi,
                    )
                    if fallback_elements:
                        if should_complete_structure:
                            raw_elements, completion_stats = _fuse_structure_anchors_with_fallback(
                                raw_elements,
                                fallback_elements,
                                rendered_page,
                            )
                        else:
                            raw_elements.extend(fallback_elements)
                            completion_stats = {
                                **_empty_anchor_completion_stats(),
                                "fallback_anchor_count": _raw_text_anchor_count(fallback_elements),
                                "added_anchor_count": _raw_text_anchor_count(fallback_elements),
                            }
                        ocr_status = "applied"
                    elif ocr_error:
                        ocr_status = "unavailable"
                    else:
                        ocr_status = "no-text"
            elif not payload_text_anchor_count:
                ocr_status = "disabled"
        page_diagnostics.append(
            {
                "page_index": rendered_page.page_index,
                "source_type": rendered.source_type,
                "ocr_fallback": ocr_fallback,
                "ocr_fallback_status": ocr_status,
                "ocr_language_requested": ocr_language,
                "ocr_language_used": ocr_language_used,
                "ocr_dpi": ocr_dpi,
                "ocr_error": ocr_error,
                "ocr_text_line_count": _raw_text_anchor_count(raw_elements),
                "structure_text_anchor_count": structure_anchor_count,
                "ocr_fallback_anchor_count": completion_stats["fallback_anchor_count"],
                "ocr_fallback_added_anchor_count": completion_stats["added_anchor_count"],
                "ocr_fallback_suppressed_duplicate_count": completion_stats["suppressed_duplicate_count"],
                "ocr_fallback_conflict_count": completion_stats["conflict_count"],
                "structure_anchor_overlap_count": completion_stats["structure_overlap_anchor_count"],
                "structure_anchor_completion_count": completion_stats["structure_completion_anchor_count"],
                "structure_generic_completion_suppressed_count": completion_stats[
                    "structure_generic_completion_suppressed_count"
                ],
            }
        )
        default_source = "native-ocr" if rendered.source_type == "image" else "json-fallback"
        elements = _normalize_page_elements(rendered_page, raw_elements, crop_root, default_source=default_source)
        if include_source_image:
            elements.insert(0, _source_image_element(rendered_page))
        pages.append(
            PageIR(
                page_index=rendered_page.page_index,
                width_pt=rendered_page.width_pt,
                height_pt=rendered_page.height_pt,
                width_px=rendered_page.width_px,
                height_px=rendered_page.height_px,
                render_dpi=rendered_page.render_dpi,
                scale_x=rendered_page.scale_x,
                scale_y=rendered_page.scale_y,
                background_image=str(rendered_page.background_image),
                background_svg=str(rendered_page.background_svg) if rendered_page.background_svg else None,
                elements=elements,
            )
        )

    semantic_layer = _semantic_layer_metadata(
        rendered,
        ocr_payload,
        page_diagnostics,
        include_source_image=include_source_image,
    )
    return DocumentIR(
        source=str(rendered.source),
        source_pdf=str(rendered.source_pdf) if rendered.source_pdf is not None else None,
        source_path=str(rendered.source),
        source_type=rendered.source_type,
        render_dpi=rendered.render_dpi,
        page_count=len(rendered.pages),
        pages=pages,
        revisions=[
            RevisionIR(
                reason="initial-conversion",
                payload={
                    "ocr_source": ocr_payload.get("source", "json-fallback" if ocr_payload else "empty"),
                    "source_type": rendered.source_type,
                    "include_source_image": include_source_image,
                    "ocr_fallback": ocr_fallback,
                    "ocr_language": ocr_language,
                    "ocr_dpi": ocr_dpi,
                },
            )
        ],
        metadata={
            "extraction_mode": "ocr-json",
            "source": str(rendered.source),
            "source_type": rendered.source_type,
            "source_path": str(rendered.source),
            "ocr_source": ocr_payload.get("source", "json-fallback" if ocr_payload else "empty"),
            "image_source_visual_layer": bool(include_source_image),
            "semantic_layer": semantic_layer,
            "ocr_fallback": ocr_fallback,
            "ocr_language": ocr_language,
            "ocr_dpi": ocr_dpi,
            "page_extraction": page_diagnostics,
        },
    )


def _semantic_layer_metadata(
    rendered: RenderedDocument,
    ocr_payload: dict[str, Any],
    page_diagnostics: list[dict[str, Any]],
    *,
    include_source_image: bool,
) -> dict[str, Any]:
    payload_source = str(ocr_payload.get("source") or ocr_payload.get("model") or "").strip() if ocr_payload else ""
    has_payload = bool(ocr_payload)
    has_payload_text = any(int(page.get("ocr_text_line_count") or 0) > 0 for page in page_diagnostics)
    has_ocr_fallback = any(page.get("ocr_fallback_status") == "applied" for page in page_diagnostics)
    payload_kind = _semantic_payload_kind(ocr_payload) if has_payload else None

    if payload_kind == "structure-json" and has_payload_text:
        completion_anchor_count = sum(
            int(page.get("ocr_fallback_added_anchor_count") or 0) for page in page_diagnostics
        )
        driver = "structure-plus-ocr-fallback" if completion_anchor_count else "structure-json"
    elif has_payload and has_payload_text:
        driver = "ocr-json"
    elif has_ocr_fallback:
        driver = "ocr-fallback"
    elif rendered.source_type == "image":
        driver = "visual-only"
    else:
        driver = "json-fallback"

    return {
        "driver": driver,
        "payload_kind": payload_kind,
        "payload_source": payload_source or None,
        "source_type": rendered.source_type,
        "source_visual_layer": bool(include_source_image),
        "text_anchor_count": sum(int(page.get("ocr_text_line_count") or 0) for page in page_diagnostics),
        "structure_text_anchor_count": sum(
            int(page.get("structure_text_anchor_count") or 0) for page in page_diagnostics
        ),
        "ocr_fallback_anchor_count": sum(
            int(page.get("ocr_fallback_anchor_count") or 0) for page in page_diagnostics
        ),
        "ocr_fallback_completion_anchor_count": sum(
            int(page.get("ocr_fallback_added_anchor_count") or 0) for page in page_diagnostics
        ),
        "ocr_fallback_conflict_count": sum(
            int(page.get("ocr_fallback_conflict_count") or 0) for page in page_diagnostics
        ),
        "structure_anchor_overlap_count": sum(
            int(page.get("structure_anchor_overlap_count") or 0) for page in page_diagnostics
        ),
        "structure_anchor_completion_count": sum(
            int(page.get("structure_anchor_completion_count") or 0) for page in page_diagnostics
        ),
        "structure_generic_completion_suppressed_count": sum(
            int(page.get("structure_generic_completion_suppressed_count") or 0)
            for page in page_diagnostics
        ),
        "ocr_fallback_applied_page_count": sum(
            1 for page in page_diagnostics if page.get("ocr_fallback_status") == "applied"
        ),
        "priority": [
            "structure-json",
            "ocr-fallback-completion",
            "ocr-json",
            "source-visual-layer",
        ],
    }


def _raw_text_anchor_count(raw_elements: list[dict[str, Any]]) -> int:
    return sum(1 for raw in raw_elements if _extract_text(raw).strip())


def _empty_anchor_completion_stats() -> dict[str, int]:
    return {
        "fallback_anchor_count": 0,
        "added_anchor_count": 0,
        "suppressed_duplicate_count": 0,
        "conflict_count": 0,
        "structure_overlap_anchor_count": 0,
        "structure_completion_anchor_count": 0,
        "structure_generic_completion_suppressed_count": 0,
    }


def _fuse_structure_anchors_with_fallback(
    structure_elements: list[dict[str, Any]],
    fallback_elements: list[dict[str, Any]],
    page: RenderedPage,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep fine OCR anchors, then add only structure text with no OCR peer.

    Image documents often combine a layout-aware parser that recognizes fewer,
    larger blocks with OCR that recognizes small card, menu, or product labels.
    Feeding both sets into one geometry inference step duplicates overlapping
    text and can erase stable grid/card islands. Use OCR as the fine-grained
    anchor base when it is available. The model remains the semantic authority
    because its regions, labels, and order are applied afterwards; it only adds
    text anchors for geometry that OCR did not see at all.
    """

    completed: list[dict[str, Any]] = []
    stats = _empty_anchor_completion_stats()
    stats["fallback_anchor_count"] = _raw_text_anchor_count(fallback_elements)
    for fallback in fallback_elements:
        if not _extract_text(fallback).strip():
            continue
        if _raw_duplicate_index(completed, fallback) is not None:
            stats["suppressed_duplicate_count"] += 1
            continue
        completion = dict(fallback)
        completion["ocr_anchor_origin"] = "ocr-fallback-completion"
        completion["structure_anchor_completion"] = True
        completed.append(completion)
        stats["added_anchor_count"] += 1

    for structure in structure_elements:
        if not _extract_text(structure).strip():
            continue
        if _structure_anchor_has_fallback_peer(structure, completed, page):
            stats["structure_overlap_anchor_count"] += 1
            continue
        if not _structure_anchor_is_completion_eligible(structure):
            stats["structure_generic_completion_suppressed_count"] += 1
            continue
        if _raw_duplicate_index(completed, structure) is not None:
            stats["suppressed_duplicate_count"] += 1
            continue
        completion = dict(structure)
        completion["ocr_anchor_origin"] = "structure-anchor-completion"
        completion["structure_anchor_completion"] = True
        completed.append(completion)
        stats["structure_completion_anchor_count"] += 1
    return completed, stats


def _structure_anchor_has_fallback_peer(
    structure: dict[str, Any],
    fallback_elements: list[dict[str, Any]],
    page: RenderedPage,
) -> bool:
    """Return whether a model text block intersects an existing OCR anchor."""

    try:
        structure_bbox, _structure_pdf = _extract_bboxes(structure, page)
    except (TypeError, ValueError):
        return False
    for fallback in fallback_elements:
        if not _extract_text(fallback).strip():
            continue
        try:
            fallback_bbox, _fallback_pdf = _extract_bboxes(fallback, page)
        except (TypeError, ValueError):
            continue
        if max(
            _bbox_inner_coverage(structure_bbox, fallback_bbox),
            _bbox_inner_coverage(fallback_bbox, structure_bbox),
        ) >= 0.6:
            return True
    return False


def _structure_anchor_is_completion_eligible(raw: dict[str, Any]) -> bool:
    label = str(raw.get("type") or raw.get("label") or raw.get("block_label") or "")
    normalized = label.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in STRUCTURE_COMPLETION_LABELS


def _bbox_inner_coverage(inner: BBox, outer: BBox) -> float:
    intersection_width = max(0.0, min(inner.x1, outer.x1) - max(inner.x0, outer.x0))
    intersection_height = max(0.0, min(inner.y1, outer.y1) - max(inner.y0, outer.y0))
    intersection = intersection_width * intersection_height
    area = max(inner.width * inner.height, 1.0)
    return max(0.0, min(1.0, intersection / area))


def _semantic_payload_kind(payload: dict[str, Any]) -> str:
    explicit_kind = str(payload.get("_scriptorium_payload_kind") or "").strip()
    if explicit_kind in {"ocr-json", "structure-json"}:
        return explicit_kind
    if _payload_contains_any_key(
        payload,
        {
            "parsing_res_list",
            "layout_det_res",
            "table_res_list",
            "cell_box_list",
            "table_ocr_pred",
            "overall_ocr_res",
            "text_paragraphs_ocr_res",
            "formula_res_list",
            "seal_res_list",
            "block_bbox",
            "block_content",
            "block_label",
            "block_order",
            "document",
            "ro_linkings",
            "successor_edges",
            "successor_relations",
            "reading_order_edges",
            "reading_order_relations",
            "reading_order_linkings",
            "precedence_edges",
            "order_edges",
            "reading_streams",
            "streams",
            "schema_name",
            "body",
            "prov",
        },
    ):
        return "structure-json"
    return "ocr-json"


def _payload_contains_any_key(value: Any, keys: set[str]) -> bool:
    if isinstance(value, list):
        return any(_payload_contains_any_key(item, keys) for item in value)
    if not isinstance(value, dict):
        return False
    if any(key in value for key in keys):
        return True
    return any(_payload_contains_any_key(child, keys) for child in value.values() if isinstance(child, (dict, list)))


def _group_ocr_elements_by_page(payload: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}

    def add(page_index: int, raw_elements: Any) -> None:
        if not isinstance(raw_elements, list):
            return
        for raw in raw_elements:
            if not isinstance(raw, dict):
                continue
            normalized = _normalize_raw_ocr_block(raw)
            if normalized is not None:
                grouped.setdefault(page_index, []).append(normalized)

    def visit(node: Any, fallback_page_index: int = 0) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, index)
            return
        if not isinstance(node, dict):
            return

        pages = node.get("pages")
        if isinstance(pages, list):
            for index, page in enumerate(pages):
                if not isinstance(page, dict):
                    continue
                page_index = _extract_page_index(page, index)
                add(page_index, _raw_page_elements(page))
                for key in ("res", "result", "data"):
                    if key in page:
                        visit(page[key], page_index)
            return

        node_has_page_index = any(key in node for key in ("page_index", "index", "page", "page_no", "page_num"))
        page_index = _extract_page_index(node, fallback_page_index)
        add(page_index, _raw_page_elements(node))
        for key in ("res", "result", "data"):
            if key in node:
                visit(node[key], page_index)
        for key in ("raw_results", "results", "page_results"):
            value = node.get(key)
            if isinstance(value, list):
                for index, item in enumerate(value):
                    visit(item, page_index if node_has_page_index else index)

    visit(payload)
    return grouped


def _raw_page_elements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for key in ("document", "elements", "blocks", "parsing_res_list", "boxes"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                if key == "document":
                    normalized = dict(item)
                    normalized.setdefault("type", "text")
                    collected.append(normalized)
                else:
                    collected.append(item)
    layout = payload.get("layout_det_res")
    if isinstance(layout, dict):
        boxes = layout.get("boxes")
        if isinstance(boxes, list):
            collected.extend(item for item in boxes if isinstance(item, dict))
    collected.extend(_paddle_ocr_result_blocks(payload))
    collected.extend(_paddle_table_ocr_blocks(payload))
    return _dedupe_raw_page_elements(collected)


def _dedupe_raw_page_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for raw in elements:
        duplicate_index = _raw_duplicate_index(deduped, raw)
        if duplicate_index is None:
            deduped.append(raw)
            continue
        if _raw_element_dedupe_rank(raw) > _raw_element_dedupe_rank(deduped[duplicate_index]):
            deduped[duplicate_index] = raw
    return deduped


def _raw_duplicate_index(elements: list[dict[str, Any]], raw: dict[str, Any]) -> int | None:
    for index, existing in enumerate(elements):
        if _raw_elements_are_near_duplicates(existing, raw):
            return index
    return None


def _raw_elements_are_near_duplicates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_text = _dedupe_text_key(_extract_text(left))
    right_text = _dedupe_text_key(_extract_text(right))
    if not left_text or left_text != right_text:
        return False
    left_bbox = _raw_bbox_for_dedupe(left)
    right_bbox = _raw_bbox_for_dedupe(right)
    if left_bbox is None or right_bbox is None:
        return False
    intersection = _bbox_intersection_area(left_bbox, right_bbox)
    if intersection <= 0:
        return False
    left_area = max(_bbox_area(left_bbox), 1.0)
    right_area = max(_bbox_area(right_bbox), 1.0)
    min_coverage = intersection / min(left_area, right_area)
    area_ratio = min(left_area, right_area) / max(left_area, right_area)
    return min_coverage >= 0.9 and area_ratio >= 0.25


def _raw_element_dedupe_rank(raw: dict[str, Any]) -> tuple[int, int, float, int, float]:
    label = _dedupe_label_key(raw.get("type") or raw.get("label") or raw.get("category") or raw.get("block_label"))
    bbox = _raw_bbox_for_dedupe(raw)
    area = _bbox_area(bbox) if bbox is not None else 1_000_000.0
    structured = 1 if raw.get("paddle_result_key") is None and any(key in raw for key in ("block_label", "block_order")) else 0
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
    result_priority = {
        "formula_res_list": 4,
        "seal_res_list": 4,
        "table_res_list": 3,
        "table_ocr_pred": 3,
        "text_paragraphs_ocr_res": 2,
        "overall_ocr_res": 1,
    }.get(str(raw.get("paddle_result_key") or ""), 0)
    confidence = _extract_confidence(raw) or 0.0
    return (label_priority, structured, -area, result_priority, confidence)


def _dedupe_text_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _dedupe_label_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _raw_bbox_for_dedupe(raw: dict[str, Any]) -> list[float] | None:
    for key in ("bbox_px", "bbox", "block_bbox", "coordinate", "box", "layout_bbox", "poly", "points"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            return _bbox_from_any(value)
        except (TypeError, ValueError):
            continue
    return None


def _bbox_area(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_intersection_area(left: list[float], right: list[float]) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    return width * height


def _paddle_ocr_result_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for key in ("overall_ocr_res", "text_paragraphs_ocr_res"):
        result = payload.get(key)
        if isinstance(result, dict):
            blocks.extend(_paddle_rec_result_blocks(result, result_key=key, type_label="text"))

    formula_results = payload.get("formula_res_list")
    if isinstance(formula_results, list):
        for result_index, result in enumerate(formula_results):
            if isinstance(result, dict):
                block = _paddle_formula_result_block(result, result_index)
                if block is not None:
                    blocks.append(block)

    seal_results = payload.get("seal_res_list")
    if isinstance(seal_results, list):
        for result_index, result in enumerate(seal_results):
            if isinstance(result, dict):
                blocks.extend(
                    _paddle_rec_result_blocks(
                        result,
                        result_key="seal_res_list",
                        type_label="seal",
                        result_index=result_index,
                    )
                )
    return blocks


def _paddle_rec_result_blocks(
    result: dict[str, Any],
    *,
    result_key: str,
    type_label: str,
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
            "bbox": bbox,
            "bbox_unit": "px",
            "text": text.strip(),
            "type": type_label,
            "source": "native-ocr",
            "paddle_result_key": result_key,
            "paddle_text_index": text_index,
        }
        if result_index is not None:
            block["paddle_result_index"] = result_index
        if text_index < len(scores):
            block["confidence"] = scores[text_index]
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
        "bbox": bbox,
        "bbox_unit": "px",
        "text": text,
        "type": "formula",
        "source": "native-ocr",
        "paddle_result_key": "formula_res_list",
        "paddle_result_index": result_index,
    }
    for key in ("formula_region_id", "region_id", "layout_region_id", "block_id", "id"):
        value = result.get(key)
        if value is not None:
            block[key] = value
    confidence = _first_float(result.get("rec_score"), result.get("score"), result.get("confidence"))
    if confidence is not None:
        block["confidence"] = confidence
    return block


def _paddle_result_boxes(result: dict[str, Any]) -> list[list[float]]:
    for value in (
        result.get("rec_boxes"),
        result.get("rec_polys"),
        result.get("dt_polys"),
        result.get("boxes"),
        result.get("polys"),
    ):
        boxes = _bbox_candidates_from_any(value)
        if boxes:
            return boxes
    return []


def _paddle_table_ocr_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    table_results = payload.get("table_res_list")
    if not isinstance(table_results, list):
        return []

    blocks: list[dict[str, Any]] = []
    for table_index, table in enumerate(table_results):
        if not isinstance(table, dict):
            continue
        ocr_pred = table.get("table_ocr_pred")
        if not isinstance(ocr_pred, dict):
            continue
        boxes = _paddle_table_cell_boxes(table, ocr_pred)
        texts = _string_values(ocr_pred.get("rec_texts"))
        scores = _float_values(ocr_pred.get("rec_scores"))
        for cell_index, bbox in enumerate(boxes):
            text = texts[cell_index] if cell_index < len(texts) else ""
            if not text.strip():
                continue
            block: dict[str, Any] = {
                "bbox": bbox,
                "bbox_unit": "px",
                "text": text.strip(),
                "type": "table_cell",
                "source": "native-ocr",
                "table_ref": _paddle_table_ref(table, table_index),
            }
            if cell_index < len(scores):
                block["confidence"] = scores[cell_index]
            blocks.append(block)
    return blocks


def _paddle_table_cell_boxes(table: dict[str, Any], ocr_pred: dict[str, Any]) -> list[list[float]]:
    for value in (
        table.get("cell_box_list"),
        ocr_pred.get("rec_boxes"),
        ocr_pred.get("rec_polys"),
        ocr_pred.get("dt_polys"),
    ):
        boxes = _bbox_list_from_any(value)
        if boxes:
            return boxes
    return []


def _bbox_list_from_any(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    boxes: list[list[float]] = []
    for item in value:
        try:
            boxes.append(_bbox_from_any(item))
        except (TypeError, ValueError):
            continue
    return boxes


def _bbox_candidates_from_any(value: Any) -> list[list[float]]:
    boxes = _bbox_list_from_any(value)
    if boxes:
        return boxes
    try:
        bbox = _bbox_from_any(value)
    except (TypeError, ValueError):
        return []
    return [bbox]


def _first_bbox_from_any(*values: Any) -> list[float] | None:
    for value in values:
        boxes = _bbox_candidates_from_any(value)
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


def _paddle_table_ref(table: dict[str, Any], table_index: int) -> str:
    for key in ("table_region_id", "region_id", "layout_region_id", "table_id", "block_id", "id"):
        value = table.get(key)
        if value is not None:
            return str(value)
    return f"table_res_list:{table_index}"


def _normalize_raw_ocr_block(raw: dict[str, Any]) -> dict[str, Any] | None:
    normalized = dict(raw)
    if "bbox" not in normalized and "bbox_px" not in normalized and "bbox_pdf" not in normalized:
        for key in ("block_bbox", "coordinate", "box", "layout_bbox", "poly", "points"):
            if key in raw:
                normalized["bbox"] = _bbox_from_any(raw[key])
                normalized.setdefault("bbox_unit", "px")
                break
    if "bbox" not in normalized and "bbox_px" not in normalized and "bbox_pdf" not in normalized:
        return None
    if "text" not in normalized:
        for key in ("block_content", "content", "transcription", "rec_text", "rec_formula"):
            value = raw.get(key)
            if isinstance(value, str):
                normalized["text"] = value
                break
    if "type" not in normalized and "label" not in normalized:
        for key in ("block_label", "category", "class_name"):
            value = raw.get(key)
            if isinstance(value, str):
                normalized["type"] = value
                break
    normalized.setdefault("source", "native-ocr")
    return normalized


def _bbox_from_any(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)) and len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
        return [float(item) for item in value]
    if isinstance(value, (list, tuple)):
        points: list[tuple[float, float]] = []
        for point in value:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                try:
                    points.append((float(point[0]), float(point[1])))
                except (TypeError, ValueError):
                    continue
        if points:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            return [min(xs), min(ys), max(xs), max(ys)]
    return BBox.from_any(value).as_list()


def _extract_page_index(payload: dict[str, Any], fallback: int) -> int:
    for key in ("page_index", "index"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    for key in ("page", "page_no", "page_num"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return max(int(value) - 1, 0)
        except (TypeError, ValueError):
            continue
    return fallback


def _ocr_image_raw_elements(
    page: RenderedPage,
    *,
    language: str,
    dpi: int,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    errors: list[str] = []
    for candidate in _ocr_language_candidates(language):
        try:
            raw_elements = _ocr_image_raw_elements_for_language(page, language=candidate, dpi=dpi)
        except Exception as exc:  # pragma: no cover - depends on local Tesseract installation.
            errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
            continue
        if raw_elements:
            return raw_elements, candidate, None
    return [], None, "; ".join(errors) if errors else None


def _ocr_image_raw_elements_for_language(
    rendered_page: RenderedPage,
    *,
    language: str,
    dpi: int,
) -> list[dict[str, Any]]:
    doc = fitz.open()
    try:
        page = doc.new_page(width=rendered_page.width_pt, height=rendered_page.height_pt)
        page.insert_image(page.rect, filename=str(rendered_page.background_image))
        textpage = page.get_textpage_ocr(language=language, dpi=dpi)
        text_dict = page.get_text("dict", textpage=textpage)
    finally:
        doc.close()
    return _ocr_elements_from_text_dict(text_dict, language=language, dpi=dpi)


def _ocr_elements_from_text_dict(text_dict: dict[str, Any], *, language: str, dpi: int) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text", "")]
            if not spans:
                continue
            text = "".join(str(span.get("text", "")) for span in spans).strip()
            if not text:
                continue
            try:
                bbox = BBox.from_any(line.get("bbox"))
            except (TypeError, ValueError):
                continue
            elements.append(
                {
                    "type": "text",
                    "bbox_pdf": bbox.as_list(),
                    "text": text,
                    "confidence": 0.72,
                    "source": "native-ocr",
                    "ocr_fallback": True,
                    "ocr_language": language,
                    "ocr_dpi": dpi,
                }
            )
    return elements


def _ocr_language_candidates(language: str) -> tuple[str, ...]:
    requested = language.strip() or "eng"
    candidates = [requested]
    if requested != "eng":
        candidates.append("eng")
    return tuple(dict.fromkeys(candidates))


def _normalize_page_elements(
    page: RenderedPage,
    raw_elements: list[dict[str, Any]],
    crop_root: Path | None,
    *,
    default_source: str = "json-fallback",
) -> list[ElementIR]:
    normalized: list[tuple[BBox, dict[str, Any], BBox, BBox]] = []
    for raw in raw_elements:
        bbox_px, bbox_pdf = _extract_bboxes(raw, page)
        bbox_px = clamp_bbox(bbox_px, page.width_px, page.height_px)
        bbox_pdf = clamp_bbox(bbox_pdf, page.width_pt, page.height_pt)
        if bbox_px.width <= 0 or bbox_px.height <= 0:
            continue
        normalized.append((bbox_px, raw, bbox_px, bbox_pdf))

    normalized.sort(key=lambda item: reading_order_key(item[0]))
    reading_order_assignments = {
        assignment.item_index: assignment
        for assignment in infer_semantic_reading_order(
            [item[3] for item in normalized],
            page.width_pt,
            page.height_pt,
            texts=[_extract_text(item[1]) for item in normalized],
        )
    }

    elements: list[ElementIR] = []
    for visual_index, (_sort_bbox, raw, bbox_px, bbox_pdf) in enumerate(normalized):
        order_assignment = reading_order_assignments[visual_index]
        element_type = _normalize_type(raw.get("type") or raw.get("label") or raw.get("category"))
        source_crop = _write_crop(page, bbox_px, crop_root, visual_index + 1) if crop_root is not None else None
        metadata = {k: v for k, v in raw.items() if k not in {"bbox", "bbox_px", "bbox_pdf", "text"}}
        metadata.setdefault("source", default_source)
        metadata.update(order_assignment.as_metadata())
        elements.append(
            ElementIR(
                id=f"p{page.page_index + 1:04d}-e{visual_index + 1:04d}",
                page_index=page.page_index,
                type=element_type,
                bbox_pdf=bbox_pdf,
                bbox_px=bbox_px,
                source_text=_extract_text(raw),
                markdown=raw.get("markdown"),
                html=raw.get("html"),
                confidence=_extract_confidence(raw),
                reading_order=order_assignment.semantic_order,
                style_hint=_style_hint_from_bbox(bbox_px, raw),
                source_crop=source_crop,
                metadata=metadata,
            )
        )
    return elements


def _source_image_element(page: RenderedPage) -> ElementIR:
    bbox_px = BBox(x0=0, y0=0, x1=page.width_px, y1=page.height_px)
    bbox_pdf = BBox(x0=0, y0=0, x1=page.width_pt, y1=page.height_pt)
    return ElementIR(
        id=f"p{page.page_index + 1:04d}-source-image",
        page_index=page.page_index,
        type="image",
        bbox_pdf=bbox_pdf,
        bbox_px=bbox_px,
        source_text="",
        reading_order=0,
        source_crop=str(page.background_image),
        metadata={
            "source": "image-source",
            "source_kind": "image-source",
            "image_source_visual_layer": True,
            "semantic_order": 0,
            "visual_order": 0,
            "reading_order_strategy": "source-image-background",
            "reading_order_confidence": 1.0,
            "reading_order_evidence": ["source-image-visual-layer"],
        },
    )


def _extract_bboxes(raw: dict[str, Any], page: RenderedPage) -> tuple[BBox, BBox]:
    if "bbox_px" in raw:
        bbox_px = BBox.from_any(raw["bbox_px"])
        return bbox_px, px_to_pdf_bbox(bbox_px, page.scale_x, page.scale_y)
    if "bbox_pdf" in raw:
        bbox_pdf = BBox.from_any(raw["bbox_pdf"])
        return pdf_to_px_bbox(bbox_pdf, page.scale_x, page.scale_y), bbox_pdf
    if "bbox" in raw:
        bbox = BBox.from_any(raw["bbox"])
        unit = str(raw.get("bbox_unit", "px")).lower()
        if unit in {"pdf", "pt", "point", "points"}:
            return pdf_to_px_bbox(bbox, page.scale_x, page.scale_y), bbox
        return bbox, px_to_pdf_bbox(bbox, page.scale_x, page.scale_y)
    raise ValueError(f"OCR element missing bbox: {raw!r}")


def _extract_text(raw: dict[str, Any]) -> str:
    for key in ("text", "source_text", "content", "transcription", "markdown"):
        value = raw.get(key)
        if isinstance(value, str):
            return value
    return ""


def _extract_confidence(raw: dict[str, Any]) -> float | None:
    for key in ("confidence", "score", "prob"):
        value = raw.get(key)
        if value is not None:
            return float(value)
    return None


def _normalize_type(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    return TYPE_ALIASES.get(value.strip().lower(), "unknown")


def _style_hint_from_bbox(bbox: BBox, raw: dict[str, Any]) -> dict[str, Any]:
    font_size = raw.get("font_size")
    if font_size is None:
        font_size = max(7.0, min(48.0, bbox.height * 0.72))
    return {
        "font_size_px": round(float(font_size), 2),
        "line_height": round(float(raw.get("line_height", 1.15)), 2),
        "font_family": raw.get("font_family", "serif"),
    }


def _write_crop(page: RenderedPage, bbox: BBox, crop_root: Path, order: int) -> str:
    page_dir = crop_root / f"page_{page.page_index + 1:04d}"
    page_dir.mkdir(parents=True, exist_ok=True)
    crop_path = page_dir / f"element_{order:04d}.png"
    with Image.open(page.background_image) as image:
        box = (
            max(0, min(image.width - 1, math.floor(bbox.x0))),
            max(0, min(image.height - 1, math.floor(bbox.y0))),
            max(1, min(image.width, math.ceil(bbox.x1))),
            max(1, min(image.height, math.ceil(bbox.y1))),
        )
        image.crop(box).save(crop_path)
    return str(crop_path)


class PaddleOcrAdapter:
    """Run the complete PaddleOCR-VL pipeline and retain raw structure JSON.

    The adapter treats rendered source pages as independent model inputs, then
    overwrites Paddle's per-input page index with the original source page
    index. This is essential for sampled long PDFs: raw model results often
    start each single-image invocation at page zero.
    """

    def __init__(
        self,
        *,
        predict_options: Mapping[str, Any] | None = None,
        pipeline_factory: Callable[..., Any] | None = None,
        **options: Any,
    ) -> None:
        self.options = options
        self.predict_options = dict(predict_options or {})
        self.pipeline_factory = pipeline_factory

    def analyze(
        self,
        image_paths: Sequence[str | Path],
        *,
        page_indices: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        return self._analyze_pages(
            image_paths,
            page_indices=page_indices,
            source="paddleocr-vl",
            model="PaddleOCR-VL-1.6",
            pipeline_version=str(self.options.get("pipeline_version") or "v1.6"),
        )

    def _analyze_pages(
        self,
        image_paths: Sequence[str | Path],
        *,
        page_indices: Sequence[int] | None,
        source: str,
        model: str,
        pipeline_version: str,
    ) -> dict[str, Any]:
        paths = [Path(image_path) for image_path in image_paths]
        if page_indices is None:
            source_page_indices = list(range(len(paths)))
        else:
            source_page_indices = [int(page_index) for page_index in page_indices]
            if len(source_page_indices) != len(paths):
                raise ValueError("page_indices must have one entry for every Paddle input image")

        pipeline = self._create_pipeline()
        raw_results: list[Any] = []

        with tempfile.TemporaryDirectory(prefix="scriptorium-paddle-") as tmp:
            tmp_path = Path(tmp)
            for image_path, source_page_index in zip(paths, source_page_indices, strict=True):
                output = pipeline.predict(str(image_path), **self.predict_options)
                for result_index, result in enumerate(output):
                    payloads = self._result_payloads(result, tmp_path)
                    if not payloads:
                        payloads = [{"result_index": result_index, "repr": repr(result)}]
                    for payload in payloads:
                        raw_results.append(
                            _with_paddle_source_page_context(
                                payload,
                                image_path=image_path,
                                source_page_index=source_page_index,
                            )
                        )

        return {
            "source": source,
            "model": model,
            "pipeline_version": pipeline_version,
            "raw_results": raw_results,
        }

    def _create_pipeline(self) -> Any:
        options = {"pipeline_version": "v1.6", **self.options}
        if self.pipeline_factory is not None:
            return self.pipeline_factory(**options)
        try:
            from paddleocr import PaddleOCRVL  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR is not installed. Install requirements-ocr.txt or use --ocr-json fallback."
            ) from exc
        return PaddleOCRVL(**options)

    def _result_payloads(self, result: Any, tmp_path: Path) -> list[dict[str, Any]]:
        if hasattr(result, "save_to_json"):
            before = set(tmp_path.glob("*.json"))
            result.save_to_json(save_path=str(tmp_path))
            after = set(tmp_path.glob("*.json"))
            saved_files = sorted(after - before)
            if not saved_files:
                saved_files = sorted(after)
            payloads: list[dict[str, Any]] = []
            for json_path in saved_files:
                payload = load_ocr_json(json_path)
                if isinstance(payload, dict):
                    payloads.append(payload)
            if payloads:
                return payloads

        if isinstance(result, Mapping):
            normalized = normalize_paddleocr_vl_payload(dict(result))
            return [normalized] if isinstance(normalized, dict) else []

        # Some third-party wrappers expose only a mapping-like ``json``
        # property. PaddleOCR-VL itself is intentionally handled above because
        # its display-oriented property can stringify parsing blocks.
        serialized = getattr(result, "json", None)
        if isinstance(serialized, Mapping):
            normalized = normalize_paddleocr_vl_payload(dict(serialized))
            return [normalized] if isinstance(normalized, dict) else []
        return []


class PpStructureAdapter(PaddleOcrAdapter):
    """Run PP-StructureV3 and persist its replayable layout/OCR JSON.

    PP-StructureV3 is a useful lower-latency structure provider for ordinary
    papers and reports. It shares Paddle's ``save_to_json`` output path with
    PaddleOCR-VL, so the existing structure-evidence fusion code can consume
    either provider without a second IR format.
    """

    def __init__(
        self,
        *,
        cpu_compatibility_mode: bool = True,
        predict_options: Mapping[str, Any] | None = None,
        pipeline_factory: Callable[..., Any] | None = None,
        **options: Any,
    ) -> None:
        super().__init__(
            predict_options=predict_options,
            pipeline_factory=pipeline_factory,
            **options,
        )
        self.cpu_compatibility_mode = cpu_compatibility_mode

    def analyze(
        self,
        image_paths: Sequence[str | Path],
        *,
        page_indices: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        return self._analyze_pages(
            image_paths,
            page_indices=page_indices,
            source="pp-structurev3",
            model="PP-StructureV3",
            pipeline_version="v3",
        )

    def _create_pipeline(self) -> Any:
        options = dict(self.options)
        if self.cpu_compatibility_mode:
            # Paddle 3.3's PP-StructureV3 path can otherwise hit a PIR/oneDNN
            # compatibility failure before the pipeline initializes.
            os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")
            os.environ.setdefault("FLAGS_enable_pir_api", "0")
            options.setdefault("enable_mkldnn", False)
        if self.pipeline_factory is not None:
            return self.pipeline_factory(**options)
        try:
            from paddleocr import PPStructureV3  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR is not installed. Install requirements-ocr.txt or use --structure-json replay."
            ) from exc
        return PPStructureV3(**options)


class SuryaLayoutAdapter:
    """Run Surya's fast layout detector with its learned reading-order head.

    Surya silently falls back to raster order when the optional order head or
    detector feature map is unavailable. That behavior is unsuitable for a
    semantic-order benchmark, so this adapter uses the detector and order head
    directly and fails closed instead of serializing fallback geometry as
    learned evidence.
    """

    def __init__(
        self,
        *,
        predictor_factory: Callable[..., Any] | None = None,
        checkpoint: str | None = None,
        order_checkpoint: str | None = None,
        device: str | None = "cpu",
        num_threads: int | None = None,
        confidence_threshold: float = 0.4,
        batch_size: int = 8,
    ) -> None:
        self.predictor_factory = predictor_factory
        self.checkpoint = checkpoint
        self.order_checkpoint = order_checkpoint
        self.device = device
        self.num_threads = num_threads
        self.confidence_threshold = confidence_threshold
        self.batch_size = batch_size

    def analyze(
        self,
        image_paths: Sequence[str | Path],
        *,
        page_indices: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        paths = [Path(image_path) for image_path in image_paths]
        source_page_indices = _source_page_indices(paths, page_indices, provider="Surya")
        predictor = self._create_predictor()
        order_predictor = self._load_order_predictor(predictor)
        order_max_boxes = _surya_order_max_boxes(order_predictor)
        if self.predictor_factory is None and order_max_boxes is None:
            raise RuntimeError(
                "Unsupported Surya learned-order API: model capacity is unavailable, so raster fallback cannot be detected"
            )

        images: list[Image.Image] = []
        try:
            for image_path in paths:
                with Image.open(image_path) as image:
                    images.append(image.convert("RGB"))
            detections = predictor.model.detect(
                images,
                threshold=self.confidence_threshold,
                batch_size=self.batch_size,
                return_features=True,
            )
            if len(detections) != len(images):
                raise RuntimeError("Surya returned a different number of layout pages than it received")
            pages = [
                _surya_layout_page_payload(
                    detections=page_detections,
                    order_predictor=order_predictor,
                    image=image,
                    image_path=image_path,
                    page_index=page_index,
                )
                for page_detections, image, image_path, page_index in zip(
                    detections,
                    images,
                    paths,
                    source_page_indices,
                    strict=True,
                )
            ]
        finally:
            for image in images:
                image.close()

        return {
            "source": "surya-fast-layout",
            "model": self.checkpoint or "datalab-to/surya_layout2",
            "order_model": self.order_checkpoint or "datalab-to/surya_layout2/order",
            "provider_version": "surya-ocr-0.21.1",
            "backend": "fast-layout-learned-order",
            "relation_policy": "review-only",
            "semantic_policy": "review-only",
            "learned_order_required": True,
            "learned_order_max_boxes": order_max_boxes,
            "model_code_license": "Apache-2.0",
            "model_weights_license": "AI-Pubs-OpenRAIL-M-modified",
            "model_weights_license_url": "https://huggingface.co/datalab-to/surya_layout2/blob/main/LICENSE",
            "pages": pages,
        }

    def _create_predictor(self) -> Any:
        if self.device:
            os.environ["FAST_DETECTOR_DEVICE"] = self.device
        if self.order_checkpoint:
            os.environ["FAST_ORDER_MODEL_CHECKPOINT"] = self.order_checkpoint
        options: dict[str, Any] = {"use_order": True}
        if self.checkpoint:
            options["checkpoint"] = self.checkpoint
        if self.num_threads is not None:
            options["num_threads"] = self.num_threads
        if self.predictor_factory is not None:
            return self.predictor_factory(**options)
        try:
            from surya.fast_layout import FastLayoutPredictor  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Surya is not installed. Use a dedicated environment with requirements-surya.txt "
                "or replay a saved --structure-json result."
            ) from exc
        return FastLayoutPredictor(**options)

    @staticmethod
    def _load_order_predictor(predictor: Any) -> Any:
        loader = getattr(predictor, "_load_order", None)
        order_predictor = loader() if callable(loader) else getattr(predictor, "order", None)
        if order_predictor is None:
            raise RuntimeError(
                "Surya's learned reading-order head did not load; refusing its raster-order fallback"
            )
        if not hasattr(predictor, "model") or not hasattr(predictor.model, "detect"):
            raise RuntimeError("Unsupported Surya FastLayoutPredictor API: detector model is unavailable")
        return order_predictor


def _source_page_indices(
    paths: Sequence[Path],
    page_indices: Sequence[int] | None,
    *,
    provider: str,
) -> list[int]:
    if page_indices is None:
        return list(range(len(paths)))
    normalized = [int(page_index) for page_index in page_indices]
    if len(normalized) != len(paths):
        raise ValueError(f"page_indices must have one entry for every {provider} input image")
    return normalized


def _surya_layout_page_payload(
    *,
    detections: Any,
    order_predictor: Any,
    image: Image.Image,
    image_path: Path,
    page_index: int,
) -> dict[str, Any]:
    raw_detections = list(detections)
    if raw_detections:
        max_boxes = _surya_order_max_boxes(order_predictor)
        if max_boxes is not None and len(raw_detections) > max_boxes:
            raise RuntimeError(
                f"Surya detected {len(raw_detections)} layout boxes, exceeding the learned-order "
                f"capacity of {max_boxes}; refusing its raster-order fallback"
            )
        features = getattr(detections, "features", None)
        if features is None:
            raise RuntimeError(
                "Surya's detector did not return encoder features; refusing raster-order fallback"
            )
        positions = list(
            order_predictor.order_page(
                features,
                [detection["bbox"] for detection in raw_detections],
                [detection["label"] for detection in raw_detections],
                image.width,
                image.height,
            )
        )
    else:
        positions = []
    normalized_positions: list[int] = []
    for position in positions:
        try:
            normalized_position = int(position)
            exact_position = float(position)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("Surya's learned reading-order head returned an invalid position") from exc
        if isinstance(position, bool) or exact_position != normalized_position:
            raise RuntimeError("Surya's learned reading-order head returned a non-integer position")
        normalized_positions.append(normalized_position)
    if sorted(normalized_positions) != list(range(len(raw_detections))):
        raise RuntimeError("Surya's learned reading-order head returned a non-permutation")

    ordered: list[tuple[int, int, Mapping[str, Any]]] = sorted(
        (
            (int(position), detection_index, detection)
            for detection_index, (detection, position) in enumerate(
                zip(raw_detections, normalized_positions, strict=True)
            )
        ),
        key=lambda item: (item[0], item[1]),
    )
    blocks: list[dict[str, Any]] = []
    for position, detection_index, detection in ordered:
        bbox = [float(value) for value in detection["bbox"]]
        raw_label = str(detection.get("label") or "unknown")
        block_id = f"surya-p{page_index + 1:04d}-b{position + 1:04d}"
        blocks.append(
            {
                "id": block_id,
                "type": "layout",
                "bbox_px": bbox,
                "block_label": _surya_layout_label(raw_label),
                "block_order": position + 1,
                "order_policy": "review-only",
                "semantic_policy": "review-only",
                "confidence": float(detection.get("score") or 0.0),
                "surya_position": position,
                "surya_detection_index": detection_index,
                "surya_raw_label": raw_label,
            }
        )
    successor_edges = [
        {
            "source": source_block["id"],
            "target": target_block["id"],
            "kind": "successor",
            "review_required": True,
            "relation_policy": "review-only",
            "confidence": min(source_block["confidence"], target_block["confidence"]),
            "provider": "surya-fast-layout",
        }
        for source_block, target_block in zip(blocks, blocks[1:], strict=False)
    ]
    return {
        "page_index": page_index,
        "input_path": str(image_path),
        "image_width": image.width,
        "image_height": image.height,
        "relation_policy": "review-only",
        "elements": blocks,
        "successor_edges": successor_edges,
    }


def _surya_order_max_boxes(order_predictor: Any) -> int | None:
    for owner in (order_predictor, getattr(order_predictor, "model", None)):
        value = getattr(owner, "max_boxes", None)
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized > 0:
            return normalized

    module_name = getattr(order_predictor.__class__, "__module__", "")
    if not module_name:
        return None
    try:
        module = __import__(module_name, fromlist=["MAX_BOXES"])
        normalized = int(getattr(module, "MAX_BOXES", 0))
    except (ImportError, TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _surya_layout_label(value: str) -> str:
    aliases = {
        "blankpage": "blank_page",
        "chemicalblock": "chemical_block",
        "listgroup": "list",
        "pagefooter": "footer",
        "pageheader": "header",
        "sectionheader": "section_header",
        "tableofcontents": "table_of_contents",
    }
    normalized = "".join(char.lower() for char in value if char.isalnum())
    if normalized in aliases:
        return aliases[normalized]
    words: list[str] = []
    current = ""
    for char in value.replace("-", "_").replace(" ", "_"):
        if char == "_":
            if current:
                words.append(current.lower())
                current = ""
            continue
        if char.isupper() and current:
            words.append(current.lower())
            current = char
        else:
            current += char
    if current:
        words.append(current.lower())
    return "_".join(words) or "unknown"


_PADDLE_PAGE_CONTEXT_WRAPPER_KEYS = frozenset(
    {"res", "result", "data", "raw_results", "results", "page_results", "pages"}
)
_PADDLE_PAGE_CONTEXT_BLOCK_KEYS = frozenset(
    {
        "parsing_res_list",
        "layout_det_res",
        "overall_ocr_res",
        "text_paragraphs_ocr_res",
        "table_res_list",
        "formula_res_list",
        "seal_res_list",
        "document",
        "elements",
        "blocks",
    }
)


def _with_paddle_source_page_context(
    value: Any,
    *,
    image_path: Path,
    source_page_index: int,
) -> Any:
    """Set original source-page context through common Paddle result wrappers."""

    if isinstance(value, list):
        return [
            _with_paddle_source_page_context(
                item,
                image_path=image_path,
                source_page_index=source_page_index,
            )
            for item in value
        ]
    if not isinstance(value, Mapping):
        return value
    payload = {
        key: _with_paddle_source_page_context(
            item,
            image_path=image_path,
            source_page_index=source_page_index,
        )
        if key in _PADDLE_PAGE_CONTEXT_WRAPPER_KEYS
        else item
        for key, item in value.items()
    }
    is_page_payload = bool(
        set(payload).intersection(_PADDLE_PAGE_CONTEXT_WRAPPER_KEYS)
        or set(payload).intersection(_PADDLE_PAGE_CONTEXT_BLOCK_KEYS)
        or "page_index" in payload
        or "input_path" in payload
    )
    if is_page_payload:
        payload["page_index"] = source_page_index
        payload["input_path"] = str(image_path)
        payload["scriptorium_source_page_index"] = source_page_index
    return payload
