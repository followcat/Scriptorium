from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import fitz
from PIL import Image

from .geometry import clamp_bbox, pdf_to_px_bbox, px_to_pdf_bbox, reading_order_key
from .models import BBox, DocumentIR, ElementIR, PageIR, RevisionIR
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


def load_ocr_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
    include_source_image = rendered.source_type == "image" if include_source_image is None else include_source_image
    crop_root = Path(crop_dir) if crop_dir is not None else None
    if crop_root is not None:
        crop_root.mkdir(parents=True, exist_ok=True)

    pages: list[PageIR] = []
    page_diagnostics: list[dict[str, Any]] = []
    for rendered_page in rendered.pages:
        raw_elements = by_page.get(rendered_page.page_index, [])
        ocr_status = "not-needed" if raw_elements else "not-candidate"
        ocr_language_used: str | None = None
        ocr_error: str | None = None
        if rendered.source_type == "image" and not raw_elements:
            if ocr_fallback == "image-only":
                raw_elements, ocr_language_used, ocr_error = _ocr_image_raw_elements(
                    rendered_page,
                    language=ocr_language,
                    dpi=ocr_dpi,
                )
                if raw_elements:
                    ocr_status = "applied"
                elif ocr_error:
                    ocr_status = "unavailable"
                else:
                    ocr_status = "no-text"
            else:
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
                "ocr_text_line_count": len(raw_elements),
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
        driver = "structure-json"
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
        "ocr_fallback_applied_page_count": sum(
            1 for page in page_diagnostics if page.get("ocr_fallback_status") == "applied"
        ),
        "priority": ["structure-json", "ocr-json", "ocr-fallback", "source-visual-layer"],
    }


def _semantic_payload_kind(payload: dict[str, Any]) -> str:
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
                for key in ("res", "result"):
                    if key in page:
                        visit(page[key], page_index)
            return

        page_index = _extract_page_index(node, fallback_page_index)
        add(page_index, _raw_page_elements(node))
        for key in ("res", "result"):
            if key in node:
                visit(node[key], page_index)
        for key in ("raw_results", "results"):
            value = node.get(key)
            if isinstance(value, list):
                for index, item in enumerate(value):
                    visit(item, index)

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
    return collected


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
            int(round(bbox.x0)),
            int(round(bbox.y0)),
            int(round(bbox.x1)),
            int(round(bbox.y1)),
        )
        image.crop(box).save(crop_path)
    return str(crop_path)


class PaddleOcrAdapter:
    """Lazy PaddleOCR-VL adapter.

    The exact structured JSON shape can vary between PaddleOCR releases and
    pipelines, so this adapter returns raw saved JSON. A model-specific mapper
    should translate that raw payload into the fallback `pages/elements` shape.
    """

    def __init__(self, **options: Any) -> None:
        self.options = options

    def analyze(self, image_paths: list[str | Path]) -> dict[str, Any]:
        try:
            from paddleocr import PaddleOCRVL  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR is not installed. Install requirements-ocr.txt or use --ocr-json fallback."
            ) from exc

        options = {"pipeline_version": "v1.6", **self.options}
        pipeline = PaddleOCRVL(**options)
        raw_results: list[Any] = []

        with tempfile.TemporaryDirectory(prefix="scriptorium-paddle-") as tmp:
            tmp_path = Path(tmp)
            for image_path in image_paths:
                output = pipeline.predict(str(image_path))
                for index, result in enumerate(output):
                    if isinstance(result, dict):
                        raw_results.append(result)
                        continue
                    if hasattr(result, "save_to_json"):
                        before = set(tmp_path.glob("*.json"))
                        result.save_to_json(save_path=str(tmp_path))
                        after = set(tmp_path.glob("*.json"))
                        new_files = sorted(after - before)
                        if not new_files:
                            new_files = sorted(tmp_path.glob("*.json"))
                        for json_path in new_files:
                            raw_results.append(load_ocr_json(json_path))
                    else:
                        raw_results.append({"image": str(image_path), "index": index, "repr": repr(result)})

        return {"source": "paddleocr-vl", "raw_results": raw_results}
