from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest
from typer.testing import CliRunner

from scriptorium import cli
from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.opendataloader_provider import (
    OpenDataLoaderAdapter,
    OpenDataLoaderResult,
    normalize_opendataloader_payload,
)
from scriptorium.structure_evidence import apply_structure_evidence


def _raw_payload(*kids: object, page_count: int = 1) -> dict[str, object]:
    return {
        "file name": "sample.pdf",
        "number of pages": page_count,
        "kids": list(kids),
    }


def _raw_block(
    page_number: int,
    bbox: list[float],
    *,
    block_type: str = "paragraph",
    content: str = "Body text",
    **extra: object,
) -> dict[str, object]:
    return {
        "page number": page_number,
        "bounding box": bbox,
        "type": block_type,
        "content": content,
        **extra,
    }


def _document() -> DocumentIR:
    page = PageIR(
        page_index=0,
        width_pt=100,
        height_pt=100,
        width_px=100,
        height_px=100,
        render_dpi=72,
        scale_x=1,
        scale_y=1,
        background_image="page.png",
        elements=[
            ElementIR(
                id="top",
                page_index=0,
                type="text",
                bbox_pdf=BBox(x0=10, y0=10, x1=90, y1=30),
                bbox_px=BBox(x0=10, y0=10, x1=90, y1=30),
                source_text="Top paragraph",
                reading_order=1,
                metadata={
                    "semantic_role": "paragraph",
                    "reading_order_strategy": "native-fixed",
                    "reading_order_stream_id": "native-main",
                },
            ),
            ElementIR(
                id="bottom",
                page_index=0,
                type="text",
                bbox_pdf=BBox(x0=10, y0=50, x1=90, y1=70),
                bbox_px=BBox(x0=10, y0=50, x1=90, y1=70),
                source_text="Bottom paragraph",
                reading_order=2,
                metadata={
                    "semantic_role": "paragraph",
                    "reading_order_strategy": "native-fixed",
                    "reading_order_stream_id": "native-main",
                },
            ),
        ],
    )
    return DocumentIR(
        source="sample.pdf",
        source_type="pdf",
        render_dpi=72,
        page_count=1,
        pages=[page],
        metadata={"semantic_layer": {"driver": "native-pdf", "payload_kind": "native-pdf"}},
    )


def test_normalizer_converts_bottom_left_pdf_coordinates_and_labels() -> None:
    payload = _raw_payload(
        _raw_block(
            1,
            [-5, 70, 110, 90],
            block_type="heading",
            content="Document title",
            **{"heading level": 1},
        ),
        _raw_block(
            1,
            [10, 40, 90, 60],
            block_type="heading",
            content="Section",
            **{"heading level": 2},
        ),
        _raw_block(1, [10, 10, 40, 30], block_type="image", content=""),
        page_count=1,
    )

    normalized = normalize_opendataloader_payload(payload, {0: (100.0, 100.0)})
    elements = normalized["pages"][0]["elements"]

    assert elements[0]["id"] == "opendataloader-p0001-b0001"
    assert elements[0]["bbox_pdf"] == [0.0, 10.0, 100.0, 30.0]
    assert [element["block_label"] for element in elements] == [
        "doc_title",
        "section_header",
        "figure",
    ]
    assert [element["block_order"] for element in elements] == [1, 2, 3]
    assert normalized["order_policy"] == "review-only"
    assert normalized["relation_policy"] == "review-only"
    assert normalized["semantic_policy"] == "review-only"
    assert normalized["runtime_reorder"] is False


def test_malformed_block_preserves_ids_and_breaks_successor_chain() -> None:
    payload = _raw_payload(
        _raw_block(1, [10, 70, 90, 90], content="First"),
        _raw_block(1, [10, 60, 10, 70], content="Malformed"),
        _raw_block(1, [10, 30, 90, 50], content="Third"),
    )

    normalized = normalize_opendataloader_payload(payload, {0: (100.0, 100.0)})
    page = normalized["pages"][0]

    assert [element["id"] for element in page["elements"]] == [
        "opendataloader-p0001-b0001",
        "opendataloader-p0001-b0003",
    ]
    assert page["successor_edges"] == []
    assert normalized["normalization"]["skipped_reason_counts"] == {
        "invalid-bounding-box": 1
    }


def test_empty_provider_document_is_a_valid_review_payload() -> None:
    normalized = normalize_opendataloader_payload(
        _raw_payload(page_count=1),
        {0: (100.0, 100.0)},
    )

    assert normalized["pages"] == []
    assert normalized["normalization"] == {
        "input_block_count": 0,
        "normalized_block_count": 0,
        "skipped_block_count": 0,
        "skipped_reason_counts": {},
        "review_relation_edge_count": 0,
    }


def test_out_of_scope_pages_are_skipped_but_declared_overflow_is_rejected() -> None:
    payload = _raw_payload(
        _raw_block(1, [10, 70, 90, 90], content="Selected"),
        _raw_block(2, [10, 70, 90, 90], content="Not loaded"),
        page_count=3,
    )

    normalized = normalize_opendataloader_payload(payload, {0: (100.0, 100.0)})

    assert [page["page_index"] for page in normalized["pages"]] == [0]
    assert normalized["normalization"]["skipped_reason_counts"] == {
        "out-of-scope-page": 1
    }

    invalid = _raw_payload(
        _raw_block(4, [10, 70, 90, 90], content="Impossible"),
        page_count=3,
    )
    with pytest.raises(ValueError, match="beyond its declared 3 pages"):
        normalize_opendataloader_payload(invalid, {0: (100.0, 100.0)})


def test_adapter_uses_injected_converter_and_explicit_source_pages(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    document = fitz.open()
    document.new_page(width=100, height=200)
    document.new_page(width=120, height=220)
    document.new_page(width=140, height=240)
    document.save(source)
    document.close()
    calls: dict[str, object] = {}

    def converter(**kwargs: object) -> None:
        calls.update(kwargs)
        output_dir = Path(str(kwargs["output_dir"]))
        payload = _raw_payload(
            _raw_block(1, [10, 150, 90, 180], content="Page one"),
            _raw_block(3, [10, 190, 130, 220], content="Page three"),
            page_count=3,
        )
        (output_dir / "paper.json").write_text(json.dumps(payload), encoding="utf-8")

    result = OpenDataLoaderAdapter(
        converter=converter,
        provider_version="2.4.7",
    ).analyze(
        source,
        tmp_path / "provider",
        page_indices=[0, 2],
        table_method="cluster",
        include_header_footer=True,
        threads=2,
    )

    assert calls["pages"] == "1,3"
    assert calls["reading_order"] == "xycut"
    assert calls["table_method"] == "cluster"
    assert calls["include_header_footer"] is True
    assert calls["threads"] == "2"
    assert [page["page_index"] for page in result.structure_payload["pages"]] == [0, 2]
    assert result.structure_payload["provider_version"] == "opendataloader-pdf-2.4.7"


def test_cli_writes_raw_and_normalized_replay_json(tmp_path: Path, monkeypatch) -> None:
    calls: dict[str, object] = {}
    raw_payload = _raw_payload(
        _raw_block(1, [10, 70, 90, 90], content="Raw"),
        page_count=3,
    )
    normalized_payload = normalize_opendataloader_payload(
        raw_payload,
        {0: (100.0, 100.0)},
        provider_version="2.4.7",
    )

    class FakeAdapter:
        def analyze(self, source: Path, provider_output_dir: Path, **kwargs: object):
            calls["source"] = source
            calls["provider_output_dir"] = provider_output_dir
            calls.update(kwargs)
            return OpenDataLoaderResult(
                raw_payload=raw_payload,
                structure_payload=normalized_payload,
            )

    monkeypatch.setattr(cli, "OpenDataLoaderAdapter", FakeAdapter)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    output = tmp_path / "normalized.json"
    raw_output = tmp_path / "raw.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "run-opendataloader",
            str(source),
            "--output",
            str(output),
            "--raw-output",
            str(raw_output),
            "--page-ranges",
            "1,3",
            "--table-method",
            "cluster",
            "--include-header-footer",
            "--threads",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["page_indices"] == (0, 2)
    assert calls["max_pages"] is None
    assert calls["table_method"] == "cluster"
    assert calls["include_header_footer"] is True
    assert calls["threads"] == 2
    assert json.loads(output.read_text(encoding="utf-8"))["runtime_reorder"] is False
    assert json.loads(raw_output.read_text(encoding="utf-8"))["file name"] == "sample.pdf"
    assert "Runtime reorder: disabled" in result.output


def test_raw_json_fusion_is_review_only_and_keeps_native_runtime_state() -> None:
    document = _document()
    payload = _raw_payload(
        _raw_block(1, [10, 30, 90, 50], content="Bottom paragraph"),
        _raw_block(1, [10, 70, 90, 90], content="Top paragraph"),
    )

    apply_structure_evidence(document, payload)

    page = document.pages[0]
    assert [(element.id, element.reading_order) for element in page.elements] == [
        ("top", 1),
        ("bottom", 2),
    ]
    assert [element.metadata["semantic_role"] for element in page.elements] == [
        "paragraph",
        "paragraph",
    ]
    assert [element.metadata["reading_order_stream_id"] for element in page.elements] == [
        "native-main",
        "native-main",
    ]
    assert all(
        element.metadata["external_structure_semantic_review_only"] is True
        for element in page.elements
    )
    evidence = document.metadata["structure_evidence"]
    assert evidence["review_region_count"] == 2
    assert evidence["review_relation_edge_count"] == 1
    assert evidence["resolved_relation_edge_count"] == 1
    assert evidence["relation_reordered_page_count"] == 0
    assert evidence["order_reordered_page_count"] == 0
    assert evidence["relation_stream_count"] == 0
