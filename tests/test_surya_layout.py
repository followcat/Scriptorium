from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from scriptorium import cli
from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.ocr import SuryaLayoutAdapter
from scriptorium.reading_order_sidecar import propose_reading_order_sidecar
from scriptorium.structure_evidence import apply_structure_evidence


class _FakeDetections(list):
    features = "encoder-features"


class _FakeDetector:
    def detect(self, images, *, threshold, batch_size, return_features):
        assert len(images) == 1
        assert threshold == 0.4
        assert batch_size == 8
        assert return_features is True
        return [
            _FakeDetections(
                [
                    {"bbox": [10, 10, 90, 30], "label": "Text", "score": 0.91},
                    {"bbox": [10, 50, 90, 70], "label": "Section-Header", "score": 0.87},
                ]
            )
        ]


class _FakeOrder:
    def order_page(self, features, boxes, labels, width, height):
        assert features == "encoder-features"
        assert boxes == [[10, 10, 90, 30], [10, 50, 90, 70]]
        assert labels == ["Text", "Section-Header"]
        assert (width, height) == (100, 100)
        return [1, 0]


class _FakePredictor:
    def __init__(self, *, learned_order: bool = True) -> None:
        self.model = _FakeDetector()
        self.order = _FakeOrder() if learned_order else None

    def _load_order(self):
        return self.order


class _CapacityLimitedOrder(_FakeOrder):
    max_boxes = 1


class _FractionalOrder(_FakeOrder):
    def order_page(self, *_args):
        return [0.5, 1.5]


def test_surya_adapter_serializes_learned_review_only_order(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    options: dict[str, object] = {}

    def factory(**kwargs):
        options.update(kwargs)
        return _FakePredictor()

    payload = SuryaLayoutAdapter(predictor_factory=factory).analyze(
        [image_path],
        page_indices=[4],
    )

    assert options == {"use_order": True}
    assert payload["source"] == "surya-fast-layout"
    assert payload["relation_policy"] == "review-only"
    assert payload["semantic_policy"] == "review-only"
    assert payload["learned_order_required"] is True
    assert payload["order_model"] == "datalab-to/surya_layout2/order"
    assert payload["learned_order_max_boxes"] is None
    assert payload["model_weights_license"] == "AI-Pubs-OpenRAIL-M-modified"
    page = payload["pages"][0]
    assert page["page_index"] == 4
    assert (page["image_width"], page["image_height"]) == (100, 100)
    assert [block["surya_detection_index"] for block in page["elements"]] == [1, 0]
    assert [block["block_order"] for block in page["elements"]] == [1, 2]
    assert [block["block_label"] for block in page["elements"]] == ["section_header", "text"]
    assert all(block["order_policy"] == "review-only" for block in page["elements"])
    assert all(block["semantic_policy"] == "review-only" for block in page["elements"])
    assert page["successor_edges"] == [
        {
            "source": "surya-p0005-b0001",
            "target": "surya-p0005-b0002",
            "kind": "successor",
            "review_required": True,
            "relation_policy": "review-only",
            "confidence": 0.87,
            "provider": "surya-fast-layout",
        }
    ]


def test_surya_adapter_refuses_missing_learned_order(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    adapter = SuryaLayoutAdapter(
        predictor_factory=lambda **_kwargs: _FakePredictor(learned_order=False)
    )

    with pytest.raises(RuntimeError, match="refusing its raster-order fallback"):
        adapter.analyze([image_path])


def test_surya_adapter_refuses_missing_detector_features(tmp_path: Path) -> None:
    class DetectionsWithoutFeatures(list):
        pass

    class DetectorWithoutFeatures:
        def detect(self, _images, **_kwargs):
            return [
                DetectionsWithoutFeatures(
                    [{"bbox": [10, 10, 90, 30], "label": "Text", "score": 0.91}]
                )
            ]

    predictor = _FakePredictor()
    predictor.model = DetectorWithoutFeatures()
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)

    with pytest.raises(RuntimeError, match="did not return encoder features"):
        SuryaLayoutAdapter(predictor_factory=lambda **_kwargs: predictor).analyze([image_path])


def test_surya_adapter_refuses_order_head_capacity_fallback(tmp_path: Path) -> None:
    predictor = _FakePredictor()
    predictor.order = _CapacityLimitedOrder()
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)

    with pytest.raises(RuntimeError, match="exceeding the learned-order capacity"):
        SuryaLayoutAdapter(predictor_factory=lambda **_kwargs: predictor).analyze([image_path])


def test_surya_adapter_refuses_non_integer_order_positions(tmp_path: Path) -> None:
    predictor = _FakePredictor()
    predictor.order = _FractionalOrder()
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)

    with pytest.raises(RuntimeError, match="non-integer position"):
        SuryaLayoutAdapter(predictor_factory=lambda **_kwargs: predictor).analyze([image_path])


def test_surya_review_relations_are_scored_without_runtime_reorder(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    payload = SuryaLayoutAdapter(
        predictor_factory=lambda **_kwargs: _FakePredictor()
    ).analyze([image_path])
    page = PageIR(
        page_index=0,
        width_pt=100,
        height_pt=100,
        width_px=100,
        height_px=100,
        render_dpi=72,
        scale_x=1,
        scale_y=1,
        background_image=str(image_path),
        elements=[
            ElementIR(
                id="top",
                page_index=0,
                type="text",
                bbox_pdf=BBox(x0=10, y0=10, x1=90, y1=30),
                bbox_px=BBox(x0=10, y0=10, x1=90, y1=30),
                source_text="Top line",
                reading_order=1,
                metadata={"source": "native-ocr", "reading_order_strategy": "visual-yx"},
            ),
            ElementIR(
                id="bottom",
                page_index=0,
                type="text",
                bbox_pdf=BBox(x0=10, y0=50, x1=90, y1=70),
                bbox_px=BBox(x0=10, y0=50, x1=90, y1=70),
                source_text="Bottom heading",
                reading_order=2,
                metadata={"source": "native-ocr", "reading_order_strategy": "visual-yx"},
            ),
        ],
    )
    document = DocumentIR(
        source=str(image_path),
        source_type="image",
        render_dpi=72,
        page_count=1,
        pages=[page],
        metadata={"semantic_layer": {"driver": "ocr-json", "payload_kind": "ocr-json"}},
    )

    apply_structure_evidence(document, payload)

    assert [element.id for element in sorted(page.elements, key=lambda item: item.reading_order)] == [
        "top",
        "bottom",
    ]
    evidence = document.metadata["structure_evidence"]
    assert evidence["review_region_count"] == 2
    assert evidence["relation_edge_count"] == 1
    assert evidence["review_relation_edge_count"] == 1
    assert evidence["resolved_relation_edge_count"] == 1
    assert evidence["relation_reordered_page_count"] == 0
    assert evidence["order_reordered_page_count"] == 0
    assert evidence["relation_stream_count"] == 0
    assert document.metadata["semantic_layer"]["driver"] == "ocr-json"
    assert document.metadata["semantic_layer"]["structure_json"]["role"] == "augmenting-evidence"
    assert document.metadata["semantic_layer"]["structure_json"]["review_region_count"] == 2
    assert all(
        element.metadata["external_structure_semantic_review_only"] is True
        for element in page.elements
    )
    assert page.elements[0].metadata["external_structure_order_review_only"] is True
    assert page.elements[1].metadata["external_structure_order_review_only"] is True
    relation_record = page.elements[1].metadata["external_structure_relation_edges"][0]
    assert relation_record["target_id"] == "top"
    assert relation_record["review_only"] is True
    proposal = propose_reading_order_sidecar(document)
    assert proposal["summary"]["strict_block_transition_count"] == 0
    assert proposal["summary"]["review_block_transition_count"] == 1


def test_surya_cli_requires_license_acceptance_and_writes_json(tmp_path: Path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, **options: object) -> None:
            calls["options"] = options

        def analyze(self, image_paths, *, page_indices):
            calls["page_indices"] = list(page_indices)
            return {
                "source": "surya-fast-layout",
                "model": "datalab-to/surya_layout2",
                "relation_policy": "review-only",
                "pages": [],
            }

    monkeypatch.setattr(cli, "SuryaLayoutAdapter", FakeAdapter)
    source = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(source)
    output = tmp_path / "surya.json"
    runner = CliRunner()

    rejected = runner.invoke(cli.app, ["run-surya-layout", str(source), "--output", str(output)])
    assert rejected.exit_code != 0
    assert "explicit license acceptance" in rejected.output

    accepted = runner.invoke(
        cli.app,
        [
            "run-surya-layout",
            str(source),
            "--output",
            str(output),
            "--accept-model-license",
            "--num-threads",
            "3",
        ],
    )
    assert accepted.exit_code == 0, accepted.output
    assert calls["options"] == {
        "checkpoint": None,
        "order_checkpoint": None,
        "device": "cpu",
        "num_threads": 3,
        "confidence_threshold": 0.4,
        "batch_size": 8,
    }
    assert calls["page_indices"] == [0]
    assert json.loads(output.read_text(encoding="utf-8"))["relation_policy"] == "review-only"
