from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from scriptorium import cli
from scriptorium.docling_provider import (
    DoclingAdapter,
    DoclingResult,
    review_only_docling_payload,
)
from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.semantic_quality import _element_identifier_index
from scriptorium.structure_evidence import (
    apply_structure_evidence,
    normalize_structure_evidence,
    normalize_structure_relations,
    normalize_structure_streams,
)


def _docling_payload() -> dict[str, object]:
    return {
        "schema_name": "DoclingDocument",
        "name": "sample",
        "body": {
            "self_ref": "#/body",
            "children": [
                {"$ref": "#/texts/0"},
                {"$ref": "#/texts/1"},
            ],
        },
        "furniture": {"self_ref": "#/furniture", "children": []},
        "texts": [
            _docling_text("#/texts/0", "First card", 10, 70, 90, 90),
            _docling_text("#/texts/1", "Second card", 10, 30, 90, 50),
        ],
        "tables": [],
        "pictures": [],
        "groups": [],
        "key_value_items": [],
        "pages": {
            "1": {
                "size": {"width": 100, "height": 100},
                "page_no": 1,
            }
        },
    }


def _docling_text(
    ref: str,
    text: str,
    left: float,
    bottom: float,
    right: float,
    top: float,
) -> dict[str, object]:
    return {
        "self_ref": ref,
        "parent": {"$ref": "#/body"},
        "children": [],
        "label": "text",
        "text": text,
        "orig": text,
        "prov": [
            {
                "page_no": 1,
                "bbox": {
                    "l": left,
                    "b": bottom,
                    "r": right,
                    "t": top,
                    "coord_origin": "BOTTOMLEFT",
                },
                "charspan": [0, len(text)],
            }
        ],
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
                id="first",
                page_index=0,
                type="text",
                bbox_pdf=BBox(x0=10, y0=10, x1=90, y1=30),
                bbox_px=BBox(x0=10, y0=10, x1=90, y1=30),
                source_text="First card",
                reading_order=1,
                metadata={
                    "semantic_role": "card-title",
                    "reading_order_stream_id": "grid-001",
                    "reading_order_stream_type": "grid-island",
                    "reading_order_strategy": "native-grid",
                },
            ),
            ElementIR(
                id="second",
                page_index=0,
                type="text",
                bbox_pdf=BBox(x0=10, y0=50, x1=90, y1=70),
                bbox_px=BBox(x0=10, y0=50, x1=90, y1=70),
                source_text="Second card",
                reading_order=2,
                metadata={
                    "semantic_role": "card-title",
                    "reading_order_stream_id": "grid-001",
                    "reading_order_stream_type": "grid-island",
                    "reading_order_strategy": "native-grid",
                },
            ),
        ],
    )
    return DocumentIR(
        source="page.png",
        source_type="image",
        render_dpi=72,
        page_count=1,
        pages=[page],
        metadata={"semantic_layer": {"driver": "ocr-json", "payload_kind": "ocr-json"}},
    )


def test_review_payload_declares_non_executable_provider_contract() -> None:
    payload = review_only_docling_payload(
        _docling_payload(),
        provider_version="2.111.0",
        languages=("eng", "deu"),
        tables=True,
        device="cpu",
    )

    assert payload["source"] == "docling-standard-heron"
    assert payload["provider_version"] == "docling-2.111.0"
    assert payload["provider_code_license"] == "MIT"
    assert payload["layout_model_license"] == "Apache-2.0"
    assert payload["semantic_policy"] == "review-only"
    assert payload["order_policy"] == "review-only"
    assert payload["relation_policy"] == "review-only"
    assert payload["docling_stream_policy"] == "disabled"
    assert payload["candidate_consensus_policy"] == "isolated"
    assert payload["runtime_reorder"] is False
    assert payload["provider_options"]["ocr_languages"] == ["eng", "deu"]
    assert payload["provider_options"]["tables"] is True


def test_review_policy_propagates_to_docling_regions_relations_and_streams() -> None:
    document = _document()
    payload = review_only_docling_payload(_docling_payload())
    document.pages[0].elements[0].metadata["id"] = 11
    document.pages[0].elements[1].metadata["id"] = 12
    document.pages[0].elements[0].metadata["structure_evidence"] = {"id": 11, "source": "ocr-json"}
    document.pages[0].elements[1].metadata["structure_evidence"] = {"id": 12, "source": "ocr-json"}
    payload["texts"][0]["id"] = 12

    regions = normalize_structure_evidence(payload, document)
    relations = normalize_structure_relations(payload, document)
    streams = normalize_structure_streams(payload, document)

    assert len(regions) == 2
    assert all(region.raw["_scriptorium_semantic_review_only"] is True for region in regions)
    assert all(region.raw["order_policy"] == "review-only" for region in regions)
    assert len(relations) == 1
    assert relations[0].raw["_scriptorium_relation_review_only"] is True
    assert streams == []

    apply_structure_evidence(document, payload)

    page = document.pages[0]
    assert [(element.id, element.reading_order) for element in page.elements] == [
        ("first", 1),
        ("second", 2),
    ]
    assert [element.metadata["semantic_role"] for element in page.elements] == [
        "card-title",
        "card-title",
    ]
    assert [element.metadata["reading_order_stream_id"] for element in page.elements] == [
        "grid-001",
        "grid-001",
    ]
    assert [element.metadata["reading_order_stream_type"] for element in page.elements] == [
        "grid-island",
        "grid-island",
    ]
    assert all(
        element.metadata["external_structure_candidate_consensus_isolated"] is True
        for element in page.elements
    )
    assert [element.metadata["structure_evidence"]["source"] for element in page.elements] == [
        "ocr-json",
        "ocr-json",
    ]
    assert all(
        element.metadata["external_structure_review_evidence"]["semantic_review_only"] is True
        for element in page.elements
    )
    evidence = document.metadata["structure_evidence"]
    assert evidence["review_region_count"] == 2
    assert evidence["review_relation_edge_count"] == 1
    assert evidence["stream_count"] == 0
    assert evidence["relation_reordered_page_count"] == 0
    assert evidence["order_reordered_page_count"] == 0
    identifier_index = _element_identifier_index(page.elements)
    assert identifier_index["11"] == "first"
    assert identifier_index["12"] == "second"


def test_adapter_uses_injected_converter_and_contiguous_page_range(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    calls: dict[str, object] = {}

    class FakeDocument:
        def export_to_dict(self) -> dict[str, object]:
            return _docling_payload()

    class FakeConversion:
        document = FakeDocument()

    class FakeConverter:
        def convert(self, source_path: str, **kwargs: object) -> FakeConversion:
            calls["source"] = source_path
            calls["convert_options"] = kwargs
            return FakeConversion()

    def factory(**kwargs: object) -> FakeConverter:
        calls["factory_options"] = kwargs
        return FakeConverter()

    result = DoclingAdapter(
        converter_factory=factory,
        provider_version="2.111.0",
    ).analyze(
        source,
        page_indices=[2, 3, 4],
        languages=("eng",),
        tables=False,
        force_ocr=True,
        device="cpu",
        threads=3,
    )

    assert calls["factory_options"] == {
        "languages": ("eng",),
        "tables": False,
        "force_ocr": True,
        "device": "cpu",
        "threads": 3,
    }
    assert calls["convert_options"] == {"page_range": (3, 5)}
    assert result.raw_payload["schema_name"] == "DoclingDocument"
    assert result.structure_payload["runtime_reorder"] is False

    with pytest.raises(ValueError, match="contiguous page range"):
        DoclingAdapter(converter_factory=factory).analyze(source, page_indices=[0, 2])


def test_cli_writes_raw_and_review_only_json(tmp_path: Path, monkeypatch) -> None:
    calls: dict[str, object] = {}
    raw_payload = _docling_payload()
    structure_payload = review_only_docling_payload(raw_payload, provider_version="2.111.0")

    class FakeAdapter:
        def analyze(self, source: Path, **kwargs: object) -> DoclingResult:
            calls["source"] = source
            calls.update(kwargs)
            return DoclingResult(raw_payload=raw_payload, structure_payload=structure_payload)

    monkeypatch.setattr(cli, "DoclingAdapter", FakeAdapter)
    source = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(source)
    output = tmp_path / "docling.structure.json"
    raw_output = tmp_path / "docling.raw.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "run-docling",
            str(source),
            "--output",
            str(output),
            "--raw-output",
            str(raw_output),
            "--page-ranges",
            "1",
            "--ocr-languages",
            "eng,deu",
            "--tables",
            "--force-ocr",
            "--device",
            "cpu",
            "--threads",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["page_indices"] == (0,)
    assert calls["languages"] == ("eng", "deu")
    assert calls["tables"] is True
    assert calls["force_ocr"] is True
    assert calls["threads"] == 3
    assert json.loads(output.read_text(encoding="utf-8"))["runtime_reorder"] is False
    assert json.loads(raw_output.read_text(encoding="utf-8"))["schema_name"] == "DoclingDocument"
    assert "Runtime reorder: disabled" in result.output
