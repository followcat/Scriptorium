from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from .models import DocumentIR, RevisionIR


HTML_EDIT_PATCH_FORMAT = "scriptorium-html-edits/v1"
HtmlEditTarget = Literal["edited_text", "translated_text"]
_EDIT_TARGETS = frozenset({"edited_text", "translated_text"})


def load_html_edit_patch(path: str | Path) -> dict[str, Any]:
    """Load and validate a browser patch emitted by an exported HTML document."""

    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid HTML edit patch JSON: {source}") from error
    return normalize_html_edit_patch(payload)


def normalize_html_edit_patch(payload: Mapping[str, Any] | object) -> dict[str, Any]:
    """Return a validated, JSON-safe edit patch without applying it to an IR."""

    if not isinstance(payload, Mapping):
        raise ValueError("HTML edit patch must be a JSON object.")
    if payload.get("format") != HTML_EDIT_PATCH_FORMAT:
        raise ValueError(f"Unsupported HTML edit patch format: {payload.get('format')!r}")

    document_id = payload.get("document_id")
    if document_id is not None and not isinstance(document_id, str):
        raise ValueError("HTML edit patch document_id must be a string.")

    edits = payload.get("edits")
    if not isinstance(edits, Sequence) or isinstance(edits, (str, bytes, bytearray)):
        raise ValueError("HTML edit patch edits must be a list.")

    normalized_edits: list[dict[str, str]] = []
    for index, raw_edit in enumerate(edits):
        if not isinstance(raw_edit, Mapping):
            raise ValueError(f"HTML edit patch entry {index} must be an object.")
        element_id = raw_edit.get("element_id")
        target = raw_edit.get("target")
        text = raw_edit.get("text")
        source_text = raw_edit.get("source_text")
        if not isinstance(element_id, str) or not element_id:
            raise ValueError(f"HTML edit patch entry {index} is missing element_id.")
        if target not in _EDIT_TARGETS:
            raise ValueError(f"HTML edit patch entry {index} has unsupported target: {target!r}")
        if not isinstance(text, str):
            raise ValueError(f"HTML edit patch entry {index} text must be a string.")
        if source_text is not None and not isinstance(source_text, str):
            raise ValueError(f"HTML edit patch entry {index} source_text must be a string.")

        entry = {"element_id": element_id, "target": str(target), "text": text}
        if source_text is not None:
            entry["source_text"] = source_text
        normalized_edits.append(entry)

    return {
        "format": HTML_EDIT_PATCH_FORMAT,
        "document_id": document_id or "",
        "edits": normalized_edits,
    }


def apply_html_edit_patch(
    document: DocumentIR,
    patch: Mapping[str, Any] | str | Path,
    *,
    require_document_id: bool = True,
    require_source_match: bool = True,
) -> int:
    """Apply browser edits to declared IR fields and record one revision."""

    payload = load_html_edit_patch(patch) if isinstance(patch, (str, Path)) else normalize_html_edit_patch(patch)
    patch_document_id = payload["document_id"]
    if require_document_id and patch_document_id != document.id:
        raise ValueError(
            "HTML edit patch document_id does not match the target DocumentIR. "
            "Use require_document_id=False only after an explicit identity review."
        )

    resolved: list[tuple[HtmlEditTarget, str, str]] = []
    for index, entry in enumerate(payload["edits"]):
        element_id = entry["element_id"]
        try:
            element = document.find_element(element_id)
        except KeyError as error:
            raise ValueError(f"HTML edit patch entry {index} references unknown element {element_id!r}.") from error
        source_text = entry.get("source_text")
        if require_source_match and source_text is not None and source_text != element.source_text:
            raise ValueError(
                f"HTML edit patch entry {index} source text no longer matches element {element_id!r}."
            )
        resolved.append((entry["target"], element_id, entry["text"]))

    changed = 0
    target_counts: Counter[str] = Counter()
    for target, element_id, text in resolved:
        element = document.find_element(element_id)
        if getattr(element, target) == text:
            continue
        setattr(element, target, text)
        changed += 1
        target_counts[target] += 1

    if changed:
        document.revisions.append(
            RevisionIR(
                reason="apply-html-edits",
                payload={
                    "changed": changed,
                    "format": HTML_EDIT_PATCH_FORMAT,
                    "target_counts": dict(sorted(target_counts.items())),
                },
            )
        )
    return changed
