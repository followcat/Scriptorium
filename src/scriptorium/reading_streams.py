from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from typing import Any


def assign_reading_streams_to_metadata(
    metadata_items: Iterable[MutableMapping[str, Any]],
    *,
    order_key: Callable[[Mapping[str, Any]], object],
) -> None:
    """Assign page-local reading stream metadata to ordered text elements."""

    ordered_items = sorted(list(metadata_items), key=order_key)
    stream_counts: Counter[str] = Counter()
    for metadata in ordered_items:
        stream_type = reading_order_stream_type(metadata)
        stream_id = reading_order_stream_id(metadata, stream_type=stream_type)
        stream_counts[stream_id] += 1
        metadata["reading_order_stream_type"] = stream_type
        metadata["reading_order_stream_id"] = stream_id
        metadata["reading_order_stream_index"] = stream_counts[stream_id]


def reading_order_stream_type(metadata: Mapping[str, Any]) -> str:
    """Return the page-local stream type implied by reading-order metadata."""

    scope = reading_order_scope(metadata)
    if scope == "page-artifact":
        artifact_type = _token(metadata.get("reading_order_artifact_type")) or "unknown"
        return f"page-artifact-{artifact_type}"
    if scope == "sidebar":
        sidebar_type = _token(metadata.get("reading_order_sidebar_type")) or "unknown"
        return f"sidebar-{sidebar_type}"
    if scope == "footnote":
        return "footnote"

    caption_type = _token(metadata.get("reading_order_caption_type"))
    if caption_type:
        return f"caption-{caption_type}"

    evidence = set(_evidence_items(metadata))
    region_path = _text(metadata.get("reading_order_region_path"))
    column_span = _text(metadata.get("column_span"))
    strategy = _text(metadata.get("reading_order_strategy"))
    if (
        "table-island" in region_path
        or column_span.startswith("table")
        or "table-island-row-major" in evidence
    ):
        return "table-island"
    if "table-row-major" in evidence or strategy.endswith("table-row-major-v1"):
        return "table-grid"
    return "body"


def reading_order_stream_id(metadata: Mapping[str, Any], *, stream_type: str | None = None) -> str:
    """Return a stable page-local stream id for DOM and benchmark reporting."""

    resolved_type = stream_type or reading_order_stream_type(metadata)
    if resolved_type == "body":
        return "body-main"
    if resolved_type in {"footnote", "table-grid"}:
        return resolved_type
    if resolved_type.startswith("page-artifact-") or resolved_type.startswith("sidebar-"):
        return resolved_type
    if resolved_type == "table-island":
        region_path = _text(metadata.get("reading_order_region_path"))
        if "table-island-" in region_path:
            return region_path.rsplit("/", maxsplit=1)[-1]
        return f"table-island-{_segment_suffix(metadata)}"
    if resolved_type.startswith("caption-"):
        target_id = _slug(metadata.get("reading_order_caption_target_id"))
        if target_id:
            return f"{resolved_type}-target-{target_id}"
        return f"{resolved_type}-{_segment_suffix(metadata)}"
    return resolved_type


def reading_order_scope(metadata: Mapping[str, Any]) -> str:
    scope = _token(metadata.get("reading_order_scope")) or "body"
    if scope == "body":
        if _token(metadata.get("reading_order_artifact_type")):
            return "page-artifact"
        if _token(metadata.get("reading_order_sidebar_type")):
            return "sidebar"
    return scope


def _segment_suffix(metadata: Mapping[str, Any]) -> str:
    segment = _int_or_none(metadata.get("flow_segment_index"))
    if segment is None:
        return "segment-000"
    return f"segment-{segment:03d}"


def _evidence_items(metadata: Mapping[str, Any]) -> list[str]:
    evidence = metadata.get("reading_order_evidence")
    if isinstance(evidence, list):
        return [_token(item) for item in evidence if _token(item)]
    if isinstance(evidence, str):
        return [_token(item) for item in evidence.split(",") if _token(item)]
    return []


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slug(value: Any) -> str:
    return "-".join(_text(value).split())


def _token(value: Any) -> str:
    return _slug(value).lower()


def _text(value: Any) -> str:
    return str(value or "").strip()
