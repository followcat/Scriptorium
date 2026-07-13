from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from scriptorium import cli
from scriptorium.paddle_layout_provider import PaddleLayoutAdapter
from scriptorium.provider_anchor_benchmark import (
    benchmark_provider_anchors,
    normalize_provider_anchors,
)


def test_paddle_layout_adapter_preserves_native_order_as_review_only_edges(tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    class FakeResult:
        json = {
            "res": {
                "page_index": None,
                "boxes": [
                    {
                        "label": "chart",
                        "score": 0.91,
                        "coordinate": [10, 10, 90, 40],
                        "order": None,
                    },
                    {
                        "label": "text",
                        "score": 0.95,
                        "coordinate": [10, 45, 90, 60],
                        "order": 1,
                    },
                    {
                        "label": "text",
                        "score": 0.96,
                        "coordinate": [10, 65, 90, 80],
                        "order": 2,
                    },
                ],
            }
        }

    class FakePredictor:
        def __init__(self, **options: object) -> None:
            calls["options"] = options

        def predict(self, image_path: str, **options: object):
            calls["image_path"] = image_path
            calls["predict_options"] = options
            return [FakeResult()]

        def close(self) -> None:
            calls["closed"] = True

    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 90), "white").save(image_path)
    payload = PaddleLayoutAdapter(
        predictor_factory=FakePredictor,
        device="cpu",
        predict_options={"threshold": 0.5},
    ).analyze([image_path], page_indices=[7])

    assert calls["options"] == {"model_name": "PP-DocLayoutV3", "device": "cpu"}
    assert calls["image_path"] == str(image_path)
    assert calls["predict_options"] == {"threshold": 0.5}
    assert calls["closed"] is True
    assert payload["runtime_reorder"] is False
    assert payload["order_policy"] == "review-only"
    assert payload["capabilities"] == {
        "layout": True,
        "reading_order": True,
        "text_recognition": False,
    }
    assert payload["provenance"]["inputs"][0]["sha256"] == hashlib.sha256(
        image_path.read_bytes()
    ).hexdigest()
    page = payload["pages"][0]
    assert page["page_index"] == 7
    assert [element["provider_order"] for element in page["elements"]] == [None, 1, 2]
    assert page["successor_edges"] == [
        {
            "source": "paddle-layout-p0008-b0002",
            "target": "paddle-layout-p0008-b0003",
        }
    ]

    provider, anchors, explicit = normalize_provider_anchors(payload)
    assert provider == "paddle-pp-doclayoutv3"
    assert [anchor.order for anchor in anchors] == [None, 1, 2]
    assert explicit == [
        ("paddle-layout-p0008-b0002", "paddle-layout-p0008-b0003")
    ]


def test_paddle_layout_benchmark_marks_text_fidelity_not_applicable(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)

    class FakePredictor:
        def predict(self, _image_path: str, **_options: object):
            return [
                {
                    "boxes": [
                        {
                            "label": "text",
                            "coordinate": [10, 10, 90, 20],
                            "order": 1,
                        },
                        {
                            "label": "text",
                            "coordinate": [10, 30, 90, 40],
                            "order": 2,
                        },
                    ]
                }
            ]

    provider = PaddleLayoutAdapter(
        predictor_factory=lambda **_options: FakePredictor()
    ).analyze([image_path])
    oracle = {
        "uid": "layout-only",
        "img": {"width": 100, "height": 100},
        "document": [
            {"id": "line-1", "box": [10, 10, 90, 20], "text": "First"},
            {"id": "line-2", "box": [10, 30, 90, 40], "text": "Second"},
        ],
    }
    semantic = {**oracle, "ro_linkings": [["line-1", "line-2"]]}
    oracle_path = tmp_path / "oracle.json"
    semantic_path = tmp_path / "semantic.json"
    provider_path = tmp_path / "provider.json"
    oracle_path.write_text(json.dumps(oracle), encoding="utf-8")
    semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
    provider_path.write_text(json.dumps(provider), encoding="utf-8")

    report = benchmark_provider_anchors(
        oracle_path,
        semantic_path,
        provider_path,
    ).report

    assert report["provider_capabilities"]["text_recognition"] is False
    assert report["provider_degradation"]["text_fidelity"]["applicable"] is False
    assert (
        "token_error_mean"
        not in report["provider_degradation"]["synthetic_profile_comparison"][
            "feature_names"
        ]
    )


def test_paddle_layout_command_writes_replayable_provider_json(tmp_path: Path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, **options: object) -> None:
            calls["options"] = options

        def analyze(self, image_paths, *, page_indices):
            calls["image_paths"] = [str(path) for path in image_paths]
            calls["page_indices"] = list(page_indices)
            return {
                "source": "paddle-pp-doclayoutv3",
                "model": "PP-DocLayoutV3",
                "runtime_reorder": False,
                "pages": [
                    {
                        "page_index": page_indices[0],
                        "elements": [
                            {
                                "id": "layout-1",
                                "bbox": [1, 2, 30, 20],
                                "block_label": "text",
                                "provider_order": 1,
                            }
                        ],
                    }
                ],
            }

    monkeypatch.setattr(cli, "PaddleLayoutAdapter", FakeAdapter)
    source = tmp_path / "source.png"
    Image.new("RGB", (180, 90), "white").save(source)
    output = tmp_path / "layout.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "run-paddle-layout",
            str(source),
            "--output",
            str(output),
            "--device",
            "cpu",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["options"] == {"model_name": "PP-DocLayoutV3", "device": "cpu"}
    assert calls["page_indices"] == [0]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["runtime_reorder"] is False
    assert payload["pages"][0]["elements"][0]["provider_order"] == 1
