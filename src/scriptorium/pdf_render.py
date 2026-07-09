from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass(frozen=True)
class RenderedPage:
    page_index: int
    width_pt: float
    height_pt: float
    width_px: int
    height_px: int
    render_dpi: int
    scale_x: float
    scale_y: float
    background_image: Path
    background_svg: Path | None = None


@dataclass(frozen=True)
class RenderedDocument:
    source_pdf: Path
    render_dpi: int
    pages: list[RenderedPage]


def render_pdf(
    pdf_path: str | Path,
    out_dir: str | Path,
    dpi: int = 192,
    include_svg_background: bool = False,
    max_pages: int | None = None,
    page_indices: Sequence[int] | None = None,
) -> RenderedDocument:
    source = Path(pdf_path).resolve()
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    if max_pages is not None and max_pages <= 0:
        raise ValueError(f"max_pages must be positive, got {max_pages}")
    if page_indices is not None and max_pages is not None:
        raise ValueError("page_indices and max_pages cannot be used together")

    pages: list[RenderedPage] = []
    with fitz.open(source) as doc:
        selected_indices = _selected_page_indices(doc.page_count, page_indices)
        for page_index in selected_indices:
            if max_pages is not None and len(pages) >= max_pages:
                break
            page = doc[page_index]
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            image_path = target_dir / f"page_{page_index + 1:04d}.png"
            pixmap.save(image_path)
            svg_path = None
            if include_svg_background:
                svg_path = target_dir / f"page_{page_index + 1:04d}.svg"
                svg_path.write_text(page.get_svg_image(text_as_path=True), encoding="utf-8")

            rect = page.rect
            scale_x = pixmap.width / rect.width
            scale_y = pixmap.height / rect.height
            pages.append(
                RenderedPage(
                    page_index=page_index,
                    width_pt=float(rect.width),
                    height_pt=float(rect.height),
                    width_px=int(pixmap.width),
                    height_px=int(pixmap.height),
                    render_dpi=dpi,
                    scale_x=float(scale_x),
                    scale_y=float(scale_y),
                    background_image=image_path,
                    background_svg=svg_path,
                )
            )

    return RenderedDocument(source_pdf=source, render_dpi=dpi, pages=pages)


def _selected_page_indices(page_count: int, page_indices: Sequence[int] | None) -> list[int]:
    if page_indices is None:
        return list(range(page_count))

    selected: list[int] = []
    seen: set[int] = set()
    for raw_index in page_indices:
        index = int(raw_index)
        if index < 0 or index >= page_count:
            raise ValueError(f"page index {index} is outside PDF page range 0-{page_count - 1}")
        if index in seen:
            continue
        selected.append(index)
        seen.add(index)
    return selected
