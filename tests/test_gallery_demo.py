from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GALLERY_ROOT = REPOSITORY_ROOT / "docs" / "showcase"


class _GalleryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tabs: list[dict[str, str]] = []
        self.panels: list[dict[str, str]] = []
        self.frames: list[dict[str, str]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "a" and values.get("role") == "tab":
            self.tabs.append(values)
        elif tag == "section" and "sample" in values.get("class", "").split():
            self.panels.append(values)
        elif tag == "iframe":
            self.frames.append(values)


def test_gallery_tabs_panels_and_lazy_frames_stay_in_sync() -> None:
    parser = _GalleryParser()
    parser.feed((GALLERY_ROOT / "index.html").read_text(encoding="utf-8"))
    parser.close()

    assert len(parser.tabs) == 11
    assert len(parser.panels) == 11
    assert len(parser.frames) == 11

    tabs_by_panel = {tab["aria-controls"]: tab for tab in parser.tabs}
    panels_by_id = {panel["id"]: panel for panel in parser.panels}
    assert tabs_by_panel.keys() == panels_by_id.keys()

    selected_tabs = [tab for tab in parser.tabs if tab["aria-selected"] == "true"]
    visible_panels = [panel for panel in parser.panels if "hidden" not in panel]
    assert len(selected_tabs) == 1
    assert len(visible_panels) == 1
    assert selected_tabs[0]["aria-controls"] == visible_panels[0]["id"]

    for panel_id, panel in panels_by_id.items():
        assert panel["role"] == "tabpanel"
        assert panel["aria-labelledby"] == tabs_by_panel[panel_id]["id"]

    lazy_frames = [frame for frame in parser.frames if frame.get("data-src")]
    assert not [frame for frame in parser.frames if frame.get("src")]
    assert len(lazy_frames) == 11

    for frame in parser.frames:
        assert not (frame.get("src") and frame.get("data-src"))
        frame_path = frame.get("src") or frame.get("data-src")
        assert frame_path
        assert (GALLERY_ROOT / frame_path).is_file()
