from __future__ import annotations

import json

import scriptorium.floating_ranker as floating_ranker
from scriptorium.provider_anchor_benchmark import (
    ProviderAnchor,
    benchmark_provider_anchor_suite,
    benchmark_provider_anchors,
    match_provider_anchors,
    normalize_provider_anchors,
)


def test_docling_blocks_match_multiple_oracle_lines_and_explicit_float(tmp_path) -> None:
    oracle = {
        "uid": "sample",
        "img": {"width": 100, "height": 100},
        "document": [
            {"id": "line-2", "box": [10, 20, 90, 28], "text": "Second", "type": "text"},
            {"id": "line-1", "box": [10, 10, 90, 18], "text": "First", "type": "text"},
            {"id": "figure", "box": [10, 40, 90, 70], "text": "[figure]", "type": "figure"},
            {"id": "caption", "box": [10, 72, 90, 80], "text": "Figure 1", "type": "text"},
        ],
    }
    semantic = {**oracle, "ro_linkings": [["line-1", "line-2"], ["figure", "caption"]]}
    provider = _docling_payload()
    oracle_path = tmp_path / "oracle.json"
    semantic_path = tmp_path / "semantic.json"
    provider_path = tmp_path / "docling.json"
    oracle_path.write_text(json.dumps(oracle), encoding="utf-8")
    semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
    provider_path.write_text(json.dumps(provider), encoding="utf-8")

    result = benchmark_provider_anchors(oracle_path, semantic_path, provider_path)

    assert result.report["oracle_anchor_recall"] == 1.0
    assert result.report["provider_anchor_match_rate"] == 1.0
    assert result.report["relations"]["explicit"]["correct"] == 1
    assert result.report["relations"]["serialized"]["correct"] == 2
    assert result.report["graphical_relation_audit"]["exact_agreement_count"] == 1
    assert result.report["graphical_relation_audit"]["conflicting_label_count"] == 0
    assert (
        result.report["graphical_relation_audit"]["provider_geometry_agreement"]["explicit"][
            "correct"
        ]
        == 1
    )
    assert result.report["assignments"]["line-1"]["provider_id"] == "#/texts/0"
    assert result.report["assignments"]["line-2"]["provider_id"] == "#/texts/0"


def test_page_element_provider_schema_is_supported() -> None:
    payload = {
        "source": "provider",
        "pages": [
            {
                "page_index": 0,
                "elements": [
                    {"id": "a", "block_label": "text", "bbox_pdf": [0, 0, 10, 10], "block_content": "A"}
                ],
                "successor_edges": [],
            }
        ],
    }

    source, anchors, relations = normalize_provider_anchors(payload)

    assert source == "provider"
    assert anchors[0].id == "a"
    assert relations == []


def test_paddle_vl_raw_results_are_supported() -> None:
    payload = {
        "source": "paddleocr-vl",
        "raw_results": [
            {
                "page_index": 0,
                "parsing_res_list": [
                    {
                        "block_id": 4,
                        "block_label": "figure_title",
                        "block_bbox": [10, 20, 90, 30],
                        "block_content": "Figure 1",
                    }
                ],
            }
        ],
    }

    source, anchors, relations = normalize_provider_anchors(payload)

    assert source == "paddleocr-vl"
    assert anchors[0].kind == "caption"
    assert anchors[0].bbox == (10.0, 20.0, 90.0, 30.0)
    assert relations == []


def test_provider_anchor_suite_aggregates_matching_prefix(tmp_path) -> None:
    corpus = tmp_path / "corpus"
    providers = tmp_path / "providers"
    (corpus / "structure").mkdir(parents=True)
    (corpus / "images").mkdir()
    providers.mkdir()
    oracle = {
        "uid": "sample_0",
        "img": {"width": 100, "height": 100},
        "document": [
            {"id": "line-1", "box": [10, 10, 90, 18], "text": "First", "type": "text"},
            {"id": "line-2", "box": [10, 20, 90, 28], "text": "Second", "type": "text"},
        ],
    }
    (corpus / "structure" / "sample_0.structure.json").write_text(json.dumps(oracle))
    (corpus / "images" / "sample_0.semantic-order.json").write_text(
        json.dumps({**oracle, "ro_linkings": [["line-1", "line-2"]]})
    )
    (providers / "sample_0.structure.json").write_text(json.dumps(_docling_payload()))
    (corpus / "comphrdoc_benchmark_manifest.json").write_text(
        json.dumps(
            {
                "selection": "fixed-document-page-prefix",
                "samples": [
                    {
                        "id": "sample_0",
                        "structure": "structure/sample_0.structure.json",
                        "semantic_sidecar": "images/sample_0.semantic-order.json",
                    }
                ],
            }
        )
    )

    result = benchmark_provider_anchor_suite(corpus, providers)

    assert result.report["case_count"] == 1
    assert result.report["relations"]["combined"]["correct"] == 1
    assert result.report["relations"]["combined"]["f1"] > 0
    assert result.report["graphical_relation_audit"]["cases_with_conflicts"] == 0


def test_anchor_matcher_does_not_use_oracle_list_order() -> None:
    _, anchors, _ = normalize_provider_anchors(_docling_payload())
    nodes = [
        {"id": "later", "box": [10, 20, 90, 28], "type": "text"},
        {"id": "earlier", "box": [10, 10, 90, 18], "type": "text"},
    ]

    matches = match_provider_anchors(nodes, anchors)

    assert matches["later"]["oracle_box"][1] > matches["earlier"]["oracle_box"][1]


def test_graphical_anchor_matcher_is_one_to_one() -> None:
    nodes = [
        {"id": "broad", "box": [0, 0, 100, 100], "type": "figure"},
        {"id": "exact", "box": [10, 10, 90, 90], "type": "figure"},
    ]
    anchors = [
        ProviderAnchor(
            "provider-figure",
            0,
            "figure",
            (10, 10, 90, 90),
            "",
            0,
        )
    ]

    matches = match_provider_anchors(nodes, anchors)

    assert set(matches) == {"exact"}
    assert matches["exact"]["provider_id"] == "provider-figure"


def test_graphical_audit_reports_crossed_oracle_without_replacing_raw_score(tmp_path) -> None:
    oracle = {
        "uid": "crossed",
        "img": {"width": 200, "height": 200},
        "document": [
            {"id": "figure-1", "box": [10, 10, 90, 60], "type": "figure"},
            {"id": "caption-1", "box": [10, 63, 90, 72], "text": "Figure 1. First"},
            {"id": "figure-2", "box": [110, 10, 190, 60], "type": "figure"},
            {"id": "caption-2", "box": [110, 63, 190, 72], "text": "Figure 2. Second"},
        ],
    }
    semantic = {
        **oracle,
        "ro_linkings": [["figure-1", "caption-2"], ["figure-2", "caption-1"]],
    }
    provider = {
        "source": "provider",
        "pages": [
            {
                "page_index": 0,
                "elements": [
                    {"id": "pf1", "type": "figure", "box": [10, 10, 90, 60]},
                    {"id": "pc1", "type": "caption", "box": [10, 63, 90, 72]},
                    {"id": "pf2", "type": "figure", "box": [110, 10, 190, 60]},
                    {"id": "pc2", "type": "caption", "box": [110, 63, 190, 72]},
                ],
                "successor_edges": [["pf1", "pc1"], ["pf2", "pc2"]],
            }
        ],
    }
    paths = [tmp_path / name for name in ("oracle.json", "semantic.json", "provider.json")]
    for path, payload in zip(paths, (oracle, semantic, provider), strict=True):
        path.write_text(json.dumps(payload))

    report = benchmark_provider_anchors(*paths).report

    assert report["relations"]["explicit"]["correct"] == 0
    audit = report["graphical_relation_audit"]
    assert audit["exact_agreement_count"] == 0
    assert audit["conflicting_label_count"] == 2
    assert audit["oracle_geometry_conflict_rate"] == 1.0
    assert audit["provider_geometry_agreement"]["explicit"]["correct"] == 2


def test_provider_benchmark_can_map_trained_floating_relations(tmp_path, monkeypatch) -> None:
    oracle = {
        "uid": "float",
        "img": {"width": 100, "height": 100},
        "document": [
            {"id": "figure", "box": [10, 40, 90, 70], "text": "[figure]", "type": "figure"},
            {"id": "caption", "box": [10, 72, 90, 80], "text": "Figure 1", "type": "text"},
        ],
    }
    paths = [tmp_path / name for name in ("oracle.json", "semantic.json", "provider.json")]
    paths[0].write_text(json.dumps(oracle))
    paths[1].write_text(json.dumps({**oracle, "ro_linkings": [["figure", "caption"]]}))
    paths[2].write_text(json.dumps(_docling_payload()))
    monkeypatch.setattr(
        floating_ranker,
        "load_floating_relation_ranker",
        lambda _: ({}, {"model_sha256": "floating"}),
    )
    monkeypatch.setattr(
        floating_ranker,
        "_predict_floating_relations",
        lambda *args, **kwargs: floating_ranker.FloatingRankerPredictionResult(
            [
                {
                    "source": "#/pictures/0",
                    "target": "#/texts/1",
                    "reliability_tier": "high-precision-review",
                    "strict_gate_passed": True,
                    "feature_outlier_count": 0,
                }
            ],
            1,
            1,
            {},
        ),
    )

    result = benchmark_provider_anchors(
        paths[0],
        paths[1],
        paths[2],
        floating_model_path=tmp_path / "floating.joblib",
    )

    assert result.report["relations"]["trained_floating"]["correct"] == 1
    assert result.report["relations"]["reliable_trained_floating"]["correct"] == 1
    assert result.report["relations"]["strict_trained_floating"]["correct"] == 1
    assert (
        result.report["relations"]["strict_in_envelope_trained_floating"]["correct"]
        == 1
    )
    assert result.report["floating_model_sha256"] == "floating"


def _docling_payload() -> dict:
    def prov(x0, y0, x1, y1):
        return [
            {
                "page_no": 1,
                "bbox": {
                    "l": x0,
                    "t": 100 - y0,
                    "r": x1,
                    "b": 100 - y1,
                    "coord_origin": "BOTTOMLEFT",
                },
            }
        ]

    return {
        "schema_name": "DoclingDocument",
        "source": "docling-test",
        "pages": {"1": {"page_no": 1, "size": {"width": 100, "height": 100}}},
        "body": {
            "self_ref": "#/body",
            "children": [{"$ref": "#/texts/0"}, {"$ref": "#/pictures/0"}],
        },
        "texts": [
            {
                "self_ref": "#/texts/0",
                "label": "text",
                "text": "First Second",
                "prov": prov(10, 10, 90, 30),
            },
            {
                "self_ref": "#/texts/1",
                "label": "caption",
                "text": "Figure 1",
                "prov": prov(10, 72, 90, 80),
            },
        ],
        "pictures": [
            {
                "self_ref": "#/pictures/0",
                "label": "picture",
                "prov": prov(10, 40, 90, 70),
                "children": [{"$ref": "#/texts/1"}],
                "captions": [{"$ref": "#/texts/1"}],
            }
        ],
        "tables": [],
        "groups": [],
    }
