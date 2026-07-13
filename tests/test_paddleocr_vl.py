from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from scriptorium import cli
from scriptorium.models import DocumentIR
from scriptorium.ocr import PpStructureAdapter, PaddleOcrAdapter
from scriptorium.paddle_json import normalize_paddleocr_vl_payload
from scriptorium.structure_evidence import load_structure_json


def test_paddle_adapter_preserves_source_page_index_through_result_wrappers(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeResult:
        json = {
            "res": {
                "page_index": 0,
                "input_path": "model-page.png",
                "parsing_res_list": [
                    {
                        "block_label": "text",
                        "block_content": "A model paragraph.",
                        "block_bbox": [10, 20, 180, 42],
                        "block_order": 1,
                    }
                ],
            }
        }

    class FakePipeline:
        def __init__(self, **options: object) -> None:
            captured["options"] = options

        def predict(self, image_path: str, **options: object) -> list[FakeResult]:
            captured["image_path"] = image_path
            captured["predict_options"] = options
            return [FakeResult()]

    page_image = tmp_path / "page_0136.png"
    page_image.write_bytes(b"not-used-by-fake-pipeline")
    adapter = PaddleOcrAdapter(
        pipeline_factory=FakePipeline,
        device="cpu",
        predict_options={"max_new_tokens": 512},
    )

    payload = adapter.analyze([page_image], page_indices=[135])

    assert captured["options"] == {"pipeline_version": "v1.6", "device": "cpu"}
    assert captured["image_path"] == str(page_image)
    assert captured["predict_options"] == {"max_new_tokens": 512}
    assert payload["source"] == "paddleocr-vl"
    assert payload["model"] == "PaddleOCR-VL-1.6"
    assert payload["provenance"]["adapter"] == "PaddleOcrAdapter"
    assert payload["provenance"]["pipeline_factory"] == "custom"
    assert payload["provenance"]["pipeline_options"] == {"device": "cpu"}
    assert payload["provenance"]["predict_options"] == {"max_new_tokens": 512}
    assert payload["provenance"]["inputs"] == [
        {
            "path": str(page_image),
            "source_page_index": 135,
            "size_bytes": page_image.stat().st_size,
            "sha256": hashlib.sha256(page_image.read_bytes()).hexdigest(),
        }
    ]
    result = payload["raw_results"][0]
    assert result["page_index"] == 135
    assert result["input_path"] == str(page_image)
    assert result["res"]["page_index"] == 135
    assert result["res"]["input_path"] == str(page_image)
    assert result["res"]["scriptorium_source_page_index"] == 135


def test_paddle_adapter_prefers_saved_machine_json_over_display_json(tmp_path: Path) -> None:
    class FakeResult:
        json = {"parsing_res_list": ["display-oriented block"]}

        def save_to_json(self, *, save_path: str) -> None:
            Path(save_path, "result.json").write_text(
                json.dumps(
                    {
                        "res": {
                            "page_index": 0,
                            "layout_det_res": {
                                "boxes": [
                                    {
                                        "label": "text",
                                        "coordinate": [1, 2, 30, 20],
                                        "order": 1,
                                        "score": 0.94,
                                    }
                                ]
                            },
                            "parsing_res_list": [
                                "\n\n#################\nlabel:\ttext\nbbox:\t[1, 2, 30, 20]\n"
                                "content:\tMachine-readable block\n#################"
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )

    class FakePipeline:
        def predict(self, _image_path: str, **_options: object) -> list[FakeResult]:
            return [FakeResult()]

    page_image = tmp_path / "page_0001.png"
    page_image.write_bytes(b"not-used-by-fake-pipeline")
    payload = PaddleOcrAdapter(pipeline_factory=lambda **_options: FakePipeline()).analyze([page_image])

    result = payload["raw_results"][0]
    assert result["res"]["parsing_res_list"][0]["block_content"] == "Machine-readable block"
    assert result["res"]["parsing_res_list"][0]["block_order"] == 1
    assert result["res"]["parsing_res_list"][0]["confidence"] == 0.94


def test_paddle_adapter_prefers_save_to_json_for_mapping_like_result_objects(tmp_path: Path) -> None:
    class FakeResult(dict):
        def __init__(self) -> None:
            super().__init__({"parsing_res_list": ["display-oriented block"]})

        def save_to_json(self, *, save_path: str) -> None:
            Path(save_path, "result.json").write_text(
                json.dumps(
                    {
                        "res": {
                            "parsing_res_list": [
                                {
                                    "block_label": "text",
                                    "block_content": "Saved from mapping result",
                                    "block_bbox": [1, 2, 30, 20],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

    class FakePipeline:
        def predict(self, _image_path: str, **_options: object) -> list[FakeResult]:
            return [FakeResult()]

    page_image = tmp_path / "page_0001.png"
    page_image.write_bytes(b"not-used-by-fake-pipeline")
    payload = PaddleOcrAdapter(pipeline_factory=lambda **_options: FakePipeline()).analyze([page_image])

    result = payload["raw_results"][0]
    assert result["res"]["parsing_res_list"][0]["block_content"] == "Saved from mapping result"


def test_pp_structure_adapter_preserves_source_page_index_and_cpu_compatibility(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeResult:
        def save_to_json(self, *, save_path: str) -> None:
            Path(save_path, "result.json").write_text(
                json.dumps(
                    {
                        "page_index": None,
                        "input_path": "model-page.png",
                        "parsing_res_list": [
                            {
                                "block_label": "paragraph_title",
                                "block_content": "Model heading",
                                "block_bbox": [8, 10, 132, 32],
                                "block_order": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

    class FakePipeline:
        def __init__(self, **options: object) -> None:
            captured["options"] = options

        def predict(self, image_path: str, **options: object) -> list[FakeResult]:
            captured["image_path"] = image_path
            captured["predict_options"] = options
            return [FakeResult()]

    page_image = tmp_path / "page_0136.png"
    page_image.write_bytes(b"not-used-by-fake-pipeline")
    payload = PpStructureAdapter(
        pipeline_factory=FakePipeline,
        device="cpu",
        use_table_recognition=False,
    ).analyze([page_image], page_indices=[135])

    assert captured["options"] == {
        "device": "cpu",
        "use_table_recognition": False,
        "enable_mkldnn": False,
    }
    assert captured["image_path"] == str(page_image)
    assert captured["predict_options"] == {}
    assert payload["source"] == "pp-structurev3"
    assert payload["model"] == "PP-StructureV3"
    assert payload["provenance"]["adapter_options"] == {
        "cpu_compatibility_mode": True,
        "cpu_compatibility_environment": {
            "FLAGS_enable_pir_api": "0",
            "PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT": "0",
        },
    }
    result = payload["raw_results"][0]
    assert result["page_index"] == 135
    assert result["input_path"] == str(page_image)
    assert result["parsing_res_list"][0]["block_order"] == 1


def test_structure_loader_recovers_paddle_display_blocks(tmp_path: Path) -> None:
    raw_json = tmp_path / "paddle-display.json"
    raw_json.write_text(
        json.dumps(
            {
                "source": "paddleocr-vl",
                "raw_results": [
                    {
                        "page_index": 0,
                        "layout_det_res": {
                            "boxes": [
                                {
                                    "label": "paragraph_title",
                                    "coordinate": [10, 20, 110, 42],
                                    "order": 1,
                                }
                            ]
                        },
                        "parsing_res_list": [
                            "\n\n#################\nlabel:\tparagraph_title\nbbox:\t[10, 20, 110, 42]\n"
                            "content:\tRecovered heading\n#################"
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = load_structure_json(raw_json)
    block = payload["raw_results"][0]["parsing_res_list"][0]

    assert block["block_label"] == "paragraph_title"
    assert block["block_bbox"] == [10.0, 20.0, 110.0, 42.0]
    assert block["block_content"] == "Recovered heading"
    assert block["block_order"] == 1


def test_paddle_normalizer_enriches_native_parsing_blocks_from_layout_companions() -> None:
    payload = {
        "parsing_res_list": [
            {
                "block_label": "text",
                "block_bbox": [10, 20, 110, 42],
                "block_content": "Native parser order stays authoritative.",
                "block_order": 8,
            }
        ],
        "layout_det_res": {
            "boxes": [
                {
                    "label": "text",
                    "coordinate": [10, 20, 110, 42],
                    "order": 3,
                    "score": 0.97,
                    "polygon_points": [[10, 20], [110, 20], [110, 42], [10, 42]],
                }
            ]
        },
    }

    normalized = normalize_paddleocr_vl_payload(payload)
    block = normalized["parsing_res_list"][0]

    assert block["block_order"] == 8
    assert block["confidence"] == 0.97
    assert block["block_polygon_points"] == [[10, 20], [110, 20], [110, 42], [10, 42]]


def test_paddleocr_vl_command_writes_replayable_structure_json_and_image_convert_uses_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, **options: object) -> None:
            calls["options"] = options

        def analyze(self, image_paths, *, page_indices):
            calls["image_paths"] = [str(path) for path in image_paths]
            calls["page_indices"] = list(page_indices)
            return {
                "source": "paddleocr-vl",
                "model": "PaddleOCR-VL-1.6",
                "pipeline_version": "v1.6",
                "raw_results": [
                    {
                        "page_index": page_indices[0],
                        "input_path": str(image_paths[0]),
                        "res": {
                            "page_index": page_indices[0],
                            "input_path": str(image_paths[0]),
                            "parsing_res_list": [
                                {
                                    "block_label": "text",
                                    "block_content": "Paddle semantic anchor",
                                    "block_bbox": [8, 10, 132, 32],
                                    "block_order": 1,
                                }
                            ],
                        },
                    }
                ],
            }

    monkeypatch.setattr(cli, "PaddleOcrAdapter", FakeAdapter)
    source = tmp_path / "source.png"
    Image.new("RGB", (180, 90), "white").save(source)
    structure_json = tmp_path / "paddle.json"
    runner = CliRunner()

    run_result = runner.invoke(
        cli.app,
        [
            "run-paddleocr-vl",
            str(source),
            "--output",
            str(structure_json),
            "--device",
            "cpu",
            "--max-new-tokens",
            "768",
            "--synchronous",
        ],
    )

    assert run_result.exit_code == 0, run_result.output
    assert calls["options"] == {
        "device": "cpu",
        "predict_options": {"max_new_tokens": 768, "use_queues": False},
    }
    assert calls["page_indices"] == [0]
    payload = json.loads(structure_json.read_text(encoding="utf-8"))
    assert payload["source"] == "paddleocr-vl"
    assert payload["raw_results"][0]["res"]["parsing_res_list"][0]["block_order"] == 1

    out_dir = tmp_path / "converted"
    convert_result = runner.invoke(
        cli.app,
        [
            "convert",
            str(source),
            "--out-dir",
            str(out_dir),
            "--structure-json",
            str(structure_json),
            "--ocr-fallback",
            "off",
        ],
    )

    assert convert_result.exit_code == 0, convert_result.output
    document = DocumentIR.load(out_dir / "document.ir.json")
    text_elements = [element for element in document.pages[0].elements if element.source_text]
    assert [element.source_text for element in text_elements] == ["Paddle semantic anchor"]
    assert document.metadata["semantic_layer"]["driver"] == "structure-json"
    assert document.metadata["semantic_layer"]["structure_json"]["role"] == "semantic-driver"


def test_pp_structure_command_writes_replayable_structure_json(tmp_path: Path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, **options: object) -> None:
            calls["options"] = options

        def analyze(self, image_paths, *, page_indices):
            calls["image_paths"] = [str(path) for path in image_paths]
            calls["page_indices"] = list(page_indices)
            return {
                "source": "pp-structurev3",
                "model": "PP-StructureV3",
                "pipeline_version": "v3",
                "raw_results": [
                    {
                        "page_index": page_indices[0],
                        "input_path": str(image_paths[0]),
                        "parsing_res_list": [
                            {
                                "block_label": "text",
                                "block_content": "PP-Structure anchor",
                                "block_bbox": [8, 10, 132, 32],
                                "block_order": 1,
                            }
                        ],
                    }
                ],
            }

    monkeypatch.setattr(cli, "PpStructureAdapter", FakeAdapter)
    source = tmp_path / "source.png"
    Image.new("RGB", (180, 90), "white").save(source)
    structure_json = tmp_path / "pp-structure.json"
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "run-pp-structure",
            str(source),
            "--output",
            str(structure_json),
            "--device",
            "cpu",
            "--table-recognition",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["options"] == {
        "cpu_compatibility_mode": True,
        "use_table_recognition": True,
        "use_formula_recognition": False,
        "use_region_detection": False,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "device": "cpu",
    }
    assert calls["page_indices"] == [0]
    payload = json.loads(structure_json.read_text(encoding="utf-8"))
    assert payload["source"] == "pp-structurev3"
    assert payload["raw_results"][0]["parsing_res_list"][0]["block_order"] == 1
