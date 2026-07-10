from __future__ import annotations

import ast
from collections.abc import Mapping
import re
from typing import Any


_WRAPPER_KEYS = frozenset({"res", "result", "data", "raw_results", "results", "page_results", "pages"})
_DISPLAY_BLOCK_RE = re.compile(
    r"^\s*#{5,}\s*\n"
    r"label:\t(?P<label>[^\n]+)\n"
    r"bbox:\t(?P<bbox>\[[^\n]+\])\n"
    r"content:\t(?P<content>.*?)\n"
    r"#{5,}\s*$",
    flags=re.DOTALL,
)


def normalize_paddleocr_vl_payload(payload: Any) -> Any:
    """Recover and canonicalize PaddleOCR-VL's paired block representations.

    PaddleOCR 3.7's local result writer can serialize ``parsing_res_list`` as
    human-readable block strings even though the same result also retains
    layout boxes, labels, scores, and layout order.  Convert only strings that
    match that documented display form, retaining every other third-party JSON
    shape unchanged.  Paddle also emits the same layout block twice: once in
    ``parsing_res_list`` with recognized content and once in
    ``layout_det_res.boxes`` with detector confidence.  The parsing block is
    the semantic source of truth, so enrich it with its layout companion rather
    than allowing downstream consumers to choose between competing copies.
    This keeps persisted runner output replayable by both OCR anchor seeding
    and structure-evidence fusion.
    """

    if isinstance(payload, list):
        return [normalize_paddleocr_vl_payload(item) for item in payload]
    if not isinstance(payload, Mapping):
        return payload

    normalized = {
        key: normalize_paddleocr_vl_payload(value) if key in _WRAPPER_KEYS else value
        for key, value in payload.items()
    }
    parsing_blocks = normalized.get("parsing_res_list")
    if not isinstance(parsing_blocks, list):
        return normalized

    recovered_blocks = _recover_display_blocks(normalized, parsing_blocks)
    if recovered_blocks is not None:
        normalized["parsing_res_list"] = recovered_blocks
    return normalized


def _recover_display_blocks(
    page_payload: Mapping[str, Any],
    parsing_blocks: list[Any],
) -> list[dict[str, Any]] | None:
    recovered: list[dict[str, Any]] = []
    layout_boxes = _layout_boxes(page_payload.get("layout_det_res"))
    for block_index, raw_block in enumerate(parsing_blocks):
        if isinstance(raw_block, Mapping):
            block = dict(raw_block)
        else:
            if not isinstance(raw_block, str):
                return None
            parsed = _parse_display_block(raw_block)
            if parsed is None:
                return None
            label, bbox, content = parsed
            block = {
                "block_id": block_index,
                "block_label": label,
                "block_bbox": bbox,
                "block_content": content,
                "paddle_display_block_recovered": True,
            }

        label = str(block.get("block_label") or block.get("label") or "").strip()
        bbox = _numeric_bbox(block.get("block_bbox", block.get("bbox")))
        layout_box = _matching_layout_box(layout_boxes, label=label, bbox=bbox) if label and bbox else None
        if layout_box is not None:
            _merge_layout_companion(block, layout_box)
        recovered.append(block)
    return recovered


def _merge_layout_companion(block: dict[str, Any], layout_box: Mapping[str, Any]) -> None:
    """Fill missing parser fields from a geometrically identical layout box.

    ``parsing_res_list.block_order`` is intentionally retained when present:
    it is the parser's reading order and can differ from the detector's local
    detection enumeration.  Confidence and polygon geometry are complementary
    detector evidence, so they are safe to inherit when absent.
    """

    if block.get("block_order") is None and layout_box.get("order") is not None:
        block["block_order"] = layout_box["order"]
    if block.get("confidence") is None and layout_box.get("score") is not None:
        block["confidence"] = layout_box["score"]
    if block.get("block_polygon_points") is None and layout_box.get("polygon_points") is not None:
        block["block_polygon_points"] = layout_box["polygon_points"]


def _parse_display_block(value: str) -> tuple[str, list[float], str] | None:
    match = _DISPLAY_BLOCK_RE.match(value)
    if match is None:
        return None
    label = match.group("label").strip()
    content = match.group("content")
    if not label:
        return None
    try:
        raw_bbox = ast.literal_eval(match.group("bbox"))
    except (SyntaxError, ValueError):
        return None
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return None
    try:
        bbox = [float(item) for item in raw_bbox]
    except (TypeError, ValueError):
        return None
    return label, bbox, content


def _layout_boxes(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        boxes = value.get("boxes")
        if isinstance(boxes, list):
            return [dict(box) for box in boxes if isinstance(box, Mapping)]
        nested = value.get("res")
        if isinstance(nested, Mapping):
            return _layout_boxes(nested)
    return []


def _matching_layout_box(
    layout_boxes: list[dict[str, Any]],
    *,
    label: str,
    bbox: list[float],
) -> dict[str, Any] | None:
    for layout_box in layout_boxes:
        if str(layout_box.get("label") or "").strip() != label:
            continue
        candidate = _numeric_bbox(layout_box.get("coordinate", layout_box.get("bbox")))
        if candidate is None:
            continue
        if max(abs(left - right) for left, right in zip(candidate, bbox, strict=True)) <= 1.0:
            return layout_box
    return None


def _numeric_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None
