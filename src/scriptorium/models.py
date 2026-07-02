from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


ElementType = Literal[
    "text",
    "title",
    "table",
    "figure",
    "formula",
    "image",
    "layout",
    "shape",
    "unknown",
]

DisplayMode = Literal["background", "debug", "source", "edited", "translated", "bilingual", "structured"]


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_any(cls, value: Any) -> "BBox":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(**value)
        if isinstance(value, (list, tuple)) and len(value) == 4:
            return cls(x0=float(value[0]), y0=float(value[1]), x1=float(value[2]), y1=float(value[3]))
        raise ValueError(f"Unsupported bbox value: {value!r}")

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


class ElementIR(BaseModel):
    id: str
    page_index: int
    type: ElementType = "unknown"
    bbox_pdf: BBox
    bbox_px: BBox
    source_text: str = ""
    edited_text: str | None = None
    translated_text: str | None = None
    markdown: str | None = None
    html: str | None = None
    confidence: float | None = None
    reading_order: int = 0
    style_hint: dict[str, Any] = Field(default_factory=dict)
    source_crop: str | None = None
    visibility: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    def text_for_mode(self, mode: DisplayMode) -> str:
        if mode == "structured":
            return self.edited_text or self.source_text
        if mode == "translated":
            return self.translated_text or self.edited_text or self.source_text
        if mode == "edited":
            return self.edited_text or self.source_text
        if mode == "bilingual":
            translated = self.translated_text or ""
            if translated and self.source_text:
                return f"{self.source_text}\n{translated}"
            return translated or self.source_text
        return self.source_text


class PageIR(BaseModel):
    page_index: int
    width_pt: float
    height_pt: float
    width_px: int
    height_px: int
    render_dpi: int
    scale_x: float
    scale_y: float
    background_image: str
    elements: list[ElementIR] = Field(default_factory=list)


class RevisionIR(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DocumentIR(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    source_pdf: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    render_dpi: int
    page_count: int
    pages: list[PageIR]
    revisions: list[RevisionIR] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "DocumentIR":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def find_element(self, element_id: str) -> ElementIR:
        for page in self.pages:
            for element in page.elements:
                if element.id == element_id:
                    return element
        raise KeyError(element_id)
