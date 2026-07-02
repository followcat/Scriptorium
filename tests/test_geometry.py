from scriptorium.geometry import clamp_bbox, pdf_to_px_bbox, px_to_pdf_bbox
from scriptorium.models import BBox


def test_bbox_roundtrip_between_pdf_and_pixels() -> None:
    pdf_bbox = BBox(x0=10, y0=20, x1=110, y1=70)
    px_bbox = pdf_to_px_bbox(pdf_bbox, scale_x=2.0, scale_y=3.0)
    assert px_bbox.as_list() == [20, 60, 220, 210]
    assert px_to_pdf_bbox(px_bbox, scale_x=2.0, scale_y=3.0).as_list() == pdf_bbox.as_list()


def test_clamp_bbox_normalizes_bounds() -> None:
    bbox = clamp_bbox(BBox(x0=-10, y0=80, x1=120, y1=-20), width=100, height=60)
    assert bbox.as_list() == [0.0, 0.0, 100.0, 60.0]
