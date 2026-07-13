from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from PIL import Image


PADDLE_LAYOUT_MODEL = "PP-DocLayoutV3"
PADDLE_LAYOUT_SOURCE = "paddle-pp-doclayoutv3"
PADDLE_LAYOUT_SCHEMA = "scriptorium-paddle-layout-provider/v1"


class PaddleLayoutAdapter:
    """Run PP-DocLayoutV3 without the slower OCR/VLM recognition stages."""

    def __init__(
        self,
        *,
        model_name: str = PADDLE_LAYOUT_MODEL,
        model_dir: str | Path | None = None,
        predict_options: Mapping[str, Any] | None = None,
        predictor_factory: Callable[..., Any] | None = None,
        **options: Any,
    ) -> None:
        self.model_name = model_name
        self.model_dir = Path(model_dir) if model_dir is not None else None
        self.predict_options = dict(predict_options or {})
        self.predictor_factory = predictor_factory
        self.options = options

    def analyze(
        self,
        image_paths: Sequence[str | Path],
        *,
        page_indices: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        paths = [Path(path) for path in image_paths]
        if page_indices is None:
            source_page_indices = list(range(len(paths)))
        else:
            source_page_indices = [int(index) for index in page_indices]
            if len(source_page_indices) != len(paths):
                raise ValueError(
                    "page_indices must have one entry for every Paddle layout input image"
                )
        for path in paths:
            if not path.is_file():
                raise ValueError(f"Paddle layout input image does not exist: {path}")

        predictor = self._create_predictor()
        pages: list[dict[str, Any]] = []
        try:
            for image_path, page_index in zip(
                paths,
                source_page_indices,
                strict=True,
            ):
                results = list(
                    predictor.predict(str(image_path), **self.predict_options)
                )
                raw_results = [_result_payload(result) for result in results]
                raw_boxes = [
                    box
                    for result in raw_results
                    for box in _layout_boxes(result)
                ]
                elements = _normalized_layout_elements(raw_boxes, page_index=page_index)
                ordered = sorted(
                    (
                        element
                        for element in elements
                        if element["provider_order"] is not None
                    ),
                    key=lambda element: (
                        int(element["provider_order"]),
                        int(element["provider_raw_index"]),
                    ),
                )
                with Image.open(image_path) as image:
                    width, height = image.size
                pages.append(
                    {
                        "page_index": page_index,
                        "input_path": str(image_path),
                        "width": width,
                        "height": height,
                        "elements": elements,
                        "successor_edges": [
                            {"source": source["id"], "target": target["id"]}
                            for source, target in zip(ordered, ordered[1:], strict=False)
                        ],
                        "provider_results": raw_results,
                    }
                )
        finally:
            close = getattr(predictor, "close", None)
            if callable(close):
                close()

        return {
            "schema": PADDLE_LAYOUT_SCHEMA,
            "source": PADDLE_LAYOUT_SOURCE,
            "model": self.model_name,
            "provider_version": _installed_version("paddleocr"),
            "semantic_policy": "review-only",
            "order_policy": "review-only",
            "relation_policy": "review-only",
            "runtime_reorder": False,
            "capabilities": {
                "layout": True,
                "reading_order": True,
                "text_recognition": False,
            },
            "provenance": {
                "adapter": type(self).__name__,
                "predictor_factory": (
                    "custom" if self.predictor_factory is not None else "installed-package"
                ),
                "model_options": _json_mapping(
                    {
                        "model_name": self.model_name,
                        "model_dir": self.model_dir,
                        **self.options,
                    }
                ),
                "predict_options": _json_mapping(self.predict_options),
                "package_versions": {
                    distribution: version
                    for distribution in ("paddleocr", "paddlex", "paddlepaddle")
                    if (version := _installed_version(distribution)) != "unavailable"
                },
                "inputs": [
                    {
                        "path": str(path),
                        "source_page_index": page_index,
                        "size_bytes": path.stat().st_size,
                        "sha256": _file_sha256(path),
                    }
                    for path, page_index in zip(paths, source_page_indices, strict=True)
                ],
            },
            "pages": pages,
        }

    def _create_predictor(self) -> Any:
        options = {"model_name": self.model_name, **self.options}
        if self.model_dir is not None:
            options["model_dir"] = str(self.model_dir)
        if self.predictor_factory is not None:
            return self.predictor_factory(**options)
        try:
            from paddleocr import LayoutDetection  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR is not installed. Install requirements-ocr.txt or replay saved layout JSON."
            ) from exc
        return LayoutDetection(**options)


def _result_payload(result: Any) -> dict[str, Any]:
    serialized = getattr(result, "json", None)
    if callable(serialized):
        serialized = serialized()
    if isinstance(serialized, Mapping):
        return dict(serialized)
    if isinstance(result, Mapping):
        return dict(result)
    raise RuntimeError("PP-DocLayoutV3 result does not expose machine-readable JSON")


def _layout_boxes(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    boxes = payload.get("boxes")
    if isinstance(boxes, list):
        return [dict(box) for box in boxes if isinstance(box, Mapping)]
    for key in ("res", "result", "data"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            recovered = _layout_boxes(nested)
            if recovered:
                return recovered
    return []


def _normalized_layout_elements(
    boxes: Sequence[Mapping[str, Any]],
    *,
    page_index: int,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for raw_index, box in enumerate(boxes):
        coordinate = box.get("coordinate", box.get("bbox"))
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 4:
            continue
        try:
            bbox = [float(value) for value in coordinate]
        except (TypeError, ValueError, OverflowError):
            continue
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        raw_order = box.get("order")
        try:
            provider_order = int(raw_order) if raw_order is not None else None
        except (TypeError, ValueError, OverflowError):
            provider_order = None
        element_id = f"paddle-layout-p{page_index + 1:04d}-b{raw_index + 1:04d}"
        elements.append(
            {
                "id": element_id,
                "block_id": element_id,
                "block_label": str(box.get("label") or "unknown"),
                "bbox": bbox,
                "text": "",
                "confidence": _optional_float(box.get("score")),
                "provider_order": provider_order,
                "provider_raw_index": raw_index,
                "polygon_points": box.get("polygon_points"),
                "provider_box": dict(box),
            }
        )
    return elements


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_mapping(options: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _json_value(value)
        for key, value in sorted(options.items(), key=lambda item: str(item[0]))
    }


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return _json_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return repr(value)


def _installed_version(distribution: str) -> str:
    try:
        return package_version(distribution)
    except PackageNotFoundError:
        return "unavailable"
