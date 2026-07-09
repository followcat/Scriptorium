from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import fitz
from PIL import Image, ImageOps


SourceKind = Literal["auto", "pdf", "image"]
RenderedSourceType = Literal["pdf", "image"]
IMAGE_SOURCE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}


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
    source_path: Path
    render_dpi: int
    pages: list[RenderedPage]
    source_type: RenderedSourceType = "pdf"
    source_pdf: Path | None = None

    @property
    def source(self) -> Path:
        return self.source_path

    @property
    def pdf_source(self) -> Path:
        if self.source_pdf is None:
            raise ValueError("PDF source is only available for PDF-rendered documents")
        return self.source_pdf


def render_source(
    source_path: str | Path,
    out_dir: str | Path,
    dpi: int = 192,
    include_svg_background: bool = False,
    max_pages: int | None = None,
    page_indices: Sequence[int] | None = None,
    input_kind: SourceKind = "auto",
    image_dpi: int = 96,
) -> RenderedDocument:
    source = Path(source_path)
    source_type = _detect_source_type(source, input_kind)
    if source_type == "image":
        return render_image(
            source,
            out_dir,
            image_dpi=image_dpi,
            max_pages=max_pages,
            page_indices=page_indices,
        )
    return render_pdf(
        source,
        out_dir,
        dpi=dpi,
        include_svg_background=include_svg_background,
        max_pages=max_pages,
        page_indices=page_indices,
    )


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

    return RenderedDocument(source_path=source, source_pdf=source, source_type="pdf", render_dpi=dpi, pages=pages)


def render_image(
    image_path: str | Path,
    out_dir: str | Path,
    image_dpi: int = 96,
    max_pages: int | None = None,
    page_indices: Sequence[int] | None = None,
) -> RenderedDocument:
    if image_dpi <= 0:
        raise ValueError(f"image_dpi must be positive, got {image_dpi}")
    if max_pages is not None and max_pages <= 0:
        raise ValueError(f"max_pages must be positive, got {max_pages}")
    if page_indices is not None and max_pages is not None:
        raise ValueError("page_indices and max_pages cannot be used together")

    source = Path(image_path).resolve()
    selected_indices = _selected_page_indices(1, page_indices)
    if max_pages is not None:
        selected_indices = selected_indices[:max_pages]

    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    pages: list[RenderedPage] = []
    if not selected_indices:
        return RenderedDocument(
            source_path=source,
            source_type="image",
            render_dpi=image_dpi,
            pages=[],
        )

    with Image.open(source) as raw_image:
        image = ImageOps.exif_transpose(raw_image)
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")
        width_px, height_px = image.size
        image_out = target_dir / "page_0001.png"
        image.save(image_out)

    width_pt = width_px * 72.0 / image_dpi
    height_pt = height_px * 72.0 / image_dpi
    pages.append(
        RenderedPage(
            page_index=0,
            width_pt=float(width_pt),
            height_pt=float(height_pt),
            width_px=int(width_px),
            height_px=int(height_px),
            render_dpi=image_dpi,
            scale_x=float(width_px / width_pt),
            scale_y=float(height_px / height_pt),
            background_image=image_out,
            background_svg=None,
        )
    )
    return RenderedDocument(
        source_path=source,
        source_type="image",
        render_dpi=image_dpi,
        pages=pages,
    )


def _detect_source_type(source_path: Path, input_kind: SourceKind) -> RenderedSourceType:
    if input_kind == "pdf":
        return "pdf"
    if input_kind == "image":
        return "image"
    if input_kind != "auto":
        raise ValueError(f"input_kind must be one of auto, pdf, or image, got {input_kind}")
    if source_path.suffix.lower() in IMAGE_SOURCE_EXTENSIONS:
        return "image"
    return "pdf"


def _selected_page_indices(page_count: int, page_indices: Sequence[int] | None) -> list[int]:
    if page_indices is None:
        return list(range(page_count))

    selected: list[int] = []
    seen: set[int] = set()
    for raw_index in page_indices:
        index = int(raw_index)
        if index < 0 or index >= page_count:
            raise ValueError(f"page index {index} is outside source page range 0-{page_count - 1}")
        if index in seen:
            continue
        selected.append(index)
        seen.add(index)
    return selected
