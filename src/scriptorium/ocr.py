from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

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
    "figure": "figure",
    "formula": "formula",
    "image": "image",
    "layout": "layout",
}


def load_ocr_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_ocr_to_ir(
    rendered: RenderedDocument,
    ocr_payload: dict[str, Any] | None = None,
    crop_dir: str | Path | None = None,
) -> DocumentIR:
    ocr_payload = ocr_payload or {}
    by_page = _group_ocr_elements_by_page(ocr_payload)
    crop_root = Path(crop_dir) if crop_dir is not None else None
    if crop_root is not None:
        crop_root.mkdir(parents=True, exist_ok=True)

    pages: list[PageIR] = []
    for rendered_page in rendered.pages:
        raw_elements = by_page.get(rendered_page.page_index, [])
        elements = _normalize_page_elements(rendered_page, raw_elements, crop_root)
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
                elements=elements,
            )
        )

    return DocumentIR(
        source_pdf=str(rendered.source_pdf),
        render_dpi=rendered.render_dpi,
        page_count=len(rendered.pages),
        pages=pages,
        revisions=[
            RevisionIR(
                reason="initial-conversion",
                payload={"ocr_source": ocr_payload.get("source", "json-fallback" if ocr_payload else "empty")},
            )
        ],
    )


def _group_ocr_elements_by_page(payload: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    pages = payload.get("pages")
    if isinstance(pages, list):
        for page in pages:
            page_index = int(page.get("page_index", page.get("index", 0)))
            elements = page.get("elements", page.get("blocks", []))
            if isinstance(elements, list):
                grouped.setdefault(page_index, []).extend([e for e in elements if isinstance(e, dict)])
        return grouped

    elements = payload.get("elements", payload.get("blocks", []))
    if isinstance(elements, list):
        for element in elements:
            if isinstance(element, dict):
                page_index = int(element.get("page_index", element.get("page", 0)))
                grouped.setdefault(page_index, []).append(element)
    return grouped


def _normalize_page_elements(
    page: RenderedPage,
    raw_elements: list[dict[str, Any]],
    crop_root: Path | None,
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
        )
    }

    elements: list[ElementIR] = []
    for visual_index, (_sort_bbox, raw, bbox_px, bbox_pdf) in enumerate(normalized):
        order_assignment = reading_order_assignments[visual_index]
        element_type = _normalize_type(raw.get("type") or raw.get("label") or raw.get("category"))
        source_crop = _write_crop(page, bbox_px, crop_root, visual_index + 1) if crop_root is not None else None
        metadata = {k: v for k, v in raw.items() if k not in {"bbox", "bbox_px", "bbox_pdf", "text"}}
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
