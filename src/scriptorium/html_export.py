from __future__ import annotations

import shutil
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .models import DisplayMode, DocumentIR, ElementIR, PageIR


def export_html(document: DocumentIR, out_dir: str | Path, display_mode: DisplayMode = "background") -> Path:
    target = Path(out_dir)
    assets_dir = target / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    include_background = display_mode != "structured"
    pages = [_prepare_page_assets(page, assets_dir, include_background=include_background) for page in document.pages]
    env = Environment(
        loader=PackageLoader("scriptorium", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("document.html.j2")
    html = template.render(
        document=document,
        pages=pages,
        display_mode=display_mode,
        element_text=element_text,
        annotation_attr=annotation_attr,
    )
    index_path = target / "index.html"
    index_path.write_text(html, encoding="utf-8")
    return index_path


def element_text(element: ElementIR, display_mode: DisplayMode) -> str:
    return element.text_for_mode(display_mode)


def annotation_attr(element: ElementIR, key: str, default: str = "") -> str:
    annotation = element.metadata.get("annotation")
    if isinstance(annotation, dict):
        value = annotation.get(key)
        if value is not None:
            return str(value)
    value = element.metadata.get(key)
    return default if value is None else str(value)


def _prepare_page_assets(page: PageIR, assets_dir: Path, include_background: bool = True) -> dict[str, object]:
    background_source = Path(page.background_image)
    page_asset_dir = assets_dir / f"page_{page.page_index + 1:04d}"
    page_asset_dir.mkdir(parents=True, exist_ok=True)

    background_rel: str | None = None
    if include_background:
        background_target = page_asset_dir / background_source.name
        if background_source.resolve() != background_target.resolve():
            shutil.copy2(background_source, background_target)
        background_rel = background_target.relative_to(assets_dir.parent).as_posix()

    elements: list[dict[str, object]] = []
    for element in page.elements:
        crop_rel: str | None = None
        if element.source_crop:
            crop_source = Path(element.source_crop)
            if crop_source.exists():
                crop_target = page_asset_dir / "crops" / crop_source.name
                crop_target.parent.mkdir(parents=True, exist_ok=True)
                if crop_source.resolve() != crop_target.resolve():
                    shutil.copy2(crop_source, crop_target)
                crop_rel = crop_target.relative_to(assets_dir.parent).as_posix()
        elements.append({"ir": element, "crop": crop_rel})

    return {
        "ir": page,
        "background": background_rel,
        "elements": elements,
    }
