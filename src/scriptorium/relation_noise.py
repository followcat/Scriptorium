from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal


RelationNoiseProfile = Literal["clean", "mild", "stress"]

_PROFILE_CONFIG = {
    "clean": {
        "jitter": 0.0,
        "fragmentation": 0.0,
        "type_dropout": 0.0,
        "element_dropout": 0.0,
        "prefix_corruption": 0.0,
    },
    "mild": {
        "jitter": 0.005,
        "fragmentation": 0.10,
        "type_dropout": 0.03,
        "element_dropout": 0.01,
        "prefix_corruption": 0.05,
    },
    "stress": {
        "jitter": 0.015,
        "fragmentation": 0.25,
        "type_dropout": 0.10,
        "element_dropout": 0.03,
        "prefix_corruption": 0.15,
    },
}


def perturb_relation_structure(
    payload: Mapping[str, Any],
    *,
    profile: RelationNoiseProfile,
) -> tuple[dict[str, Any], dict[str, int | float | str]]:
    """Apply deterministic source-neutral layout/OCR noise to answer-free anchors."""

    if profile not in _PROFILE_CONFIG:
        raise ValueError(f"unsupported relation noise profile: {profile}")
    normalized = deepcopy(dict(payload))
    document = normalized.get("document")
    image = normalized.get("img")
    if not isinstance(document, list) or not isinstance(image, Mapping):
        raise ValueError("relation noise input must contain document and img")
    width = float(image.get("width", 0))
    height = float(image.get("height", 0))
    if width <= 0 or height <= 0:
        raise ValueError("relation noise image dimensions must be positive")
    config = _PROFILE_CONFIG[profile]
    uid = str(normalized.get("uid") or image.get("fname") or "page")
    counts: Counter[str] = Counter()
    perturbed: list[dict[str, Any]] = []
    for index, raw_segment in enumerate(document):
        if not isinstance(raw_segment, Mapping):
            continue
        segment = dict(raw_segment)
        segment_id = str(segment.get("id", index))
        block_id = str(segment.get("block_id", segment_id))
        if _selected(config["element_dropout"], uid, segment_id, "element-dropout"):
            counts["element_dropout"] += 1
            continue
        kind = str(segment.get("type") or "text").lower()
        if kind in {"figure", "table"} and _selected(
            config["type_dropout"], uid, block_id, "type-dropout"
        ):
            segment["type"] = "text"
            counts["type_dropout"] += 1
        if kind == "text" and _selected(
            config["fragmentation"], uid, block_id, "fragmentation"
        ):
            segment["block_id"] = f"{block_id}:fragment:{segment_id}"
            counts["fragmented_element"] += 1
        text = str(segment.get("text") or "")
        if kind == "text" and text and _selected(
            config["prefix_corruption"], uid, block_id, "prefix-corruption"
        ):
            segment["text"] = _corrupt_prefix(text)
            counts["prefix_corruption"] += 1
        if config["jitter"] > 0:
            jittered = _jitter_bbox(
                segment.get("box"),
                width=width,
                height=height,
                amplitude=config["jitter"],
                key=f"{uid}:{segment_id}",
            )
            if jittered is not None:
                segment["box"] = jittered
                counts["jittered_element"] += 1
        perturbed.append(segment)
    normalized["document"] = perturbed
    diagnostics: dict[str, int | float | str] = {
        "profile": profile,
        "source_element_count": len(document),
        "retained_element_count": len(perturbed),
        "jitter_amplitude_ratio": config["jitter"],
        "fragmentation_rate": config["fragmentation"],
        "type_dropout_rate": config["type_dropout"],
        "element_dropout_rate": config["element_dropout"],
        "prefix_corruption_rate": config["prefix_corruption"],
        **counts,
    }
    return normalized, diagnostics


def _selected(rate: float, uid: str, item_id: str, action: str) -> bool:
    if rate <= 0:
        return False
    digest = hashlib.sha256(f"{action}:{uid}:{item_id}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") / (2**32 - 1)
    return bucket < rate


def _jitter_bbox(
    value: Any,
    *,
    width: float,
    height: float,
    amplitude: float,
    key: str,
) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    x0, y0, x1, y1 = map(float, value)
    digest = hashlib.sha256(f"bbox-jitter:{key}".encode("utf-8")).digest()

    def signed(offset: int) -> float:
        return int.from_bytes(digest[offset : offset + 2], "big") / 32767.5 - 1.0

    dx0 = signed(0) * amplitude * width
    dy0 = signed(2) * amplitude * height
    dx1 = signed(4) * amplitude * width
    dy1 = signed(6) * amplitude * height
    nx0 = min(max(0.0, x0 + dx0), width - 0.01)
    ny0 = min(max(0.0, y0 + dy0), height - 0.01)
    nx1 = min(max(nx0 + 0.01, x1 + dx1), width)
    ny1 = min(max(ny0 + 0.01, y1 + dy1), height)
    return [round(nx0, 4), round(ny0, 4), round(nx1, 4), round(ny1, 4)]


def _corrupt_prefix(text: str) -> str:
    limit = min(len(text), 12)
    prefix = "".join("x" if character.isalnum() else character for character in text[:limit])
    return prefix + text[limit:]
