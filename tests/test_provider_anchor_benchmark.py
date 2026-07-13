from __future__ import annotations

import json

import scriptorium.floating_ranker as floating_ranker
from scriptorium.provider_anchor_benchmark import (
    PROVIDER_TRANSITION_CANDIDATES,
    ProviderAnchor,
    _evaluate_provider_transition_gate,
    _native_candidate_direct_edges,
    _provider_transition_position_audit,
    _serialized_provider_edge_groups,
    _suite_provider_transition_candidate_evidence,
    _suite_transition_records,
    _sum_provider_transition_reviews,
    benchmark_provider_anchor_suite,
    benchmark_provider_anchors,
    freeze_stratified_provider_transition_gate,
    freeze_provider_transition_gate,
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
    assert result.report["relations"]["serialized_within_anchor"] == {
        "correct": 1,
        "predicted": 1,
        "labels": 2,
        "precision": 1.0,
        "recall": 0.5,
        "f1": 0.66666667,
    }
    assert result.report["relations"]["serialized_between_anchors"]["correct"] == 1
    assert (
        result.report["relations"]["serialized_direct_between_anchors"]["correct"]
        == 1
    )
    assert result.report["graphical_relation_audit"]["exact_agreement_count"] == 1
    assert result.report["graphical_relation_audit"]["conflicting_label_count"] == 0
    assert result.report["provider_degradation"]["answer_free_relation_policy"][
        "uses_relation_labels"
    ] is False
    assert result.report["provider_degradation"]["synthetic_profile_comparison"][
        "nearest_profile"
    ] in {"clean", "mild", "stress"}
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
                    {
                        "id": "a",
                        "block_label": "text",
                        "bbox_pdf": [0, 0, 10, 10],
                        "block_content": "A",
                        "confidence": 0.91,
                    }
                ],
                "successor_edges": [],
            }
        ],
    }

    source, anchors, relations = normalize_provider_anchors(payload)

    assert source == "provider"
    assert anchors[0].id == "a"
    assert anchors[0].confidence == 0.91
    assert relations == []


def test_provider_transition_review_combines_native_support_and_endpoint_confidence(
    tmp_path,
) -> None:
    oracle = {
        "uid": "supported-transition",
        "img": {"width": 100, "height": 100},
        "document": [
            {"id": "line-1", "box": [10, 10, 90, 18], "type": "text"},
            {"id": "line-2", "box": [10, 22, 90, 30], "type": "text"},
        ],
    }
    provider = {
        "source": "provider",
        "pages": [
            {
                "page_index": 0,
                "elements": [
                    {
                        "id": "block-1",
                        "type": "text",
                        "box": [10, 10, 90, 18],
                        "provider_order": 0,
                        "confidence": 0.95,
                    },
                    {
                        "id": "block-2",
                        "type": "text",
                        "box": [10, 22, 90, 30],
                        "provider_order": 1,
                        "confidence": 0.88,
                    },
                ],
            }
        ],
    }
    paths = [
        tmp_path / name
        for name in ("oracle.json", "semantic.json", "provider.json")
    ]
    for path, payload in zip(
        paths,
        (oracle, {**oracle, "ro_linkings": [["line-1", "line-2"]]}, provider),
        strict=True,
    ):
        path.write_text(json.dumps(payload))

    report = benchmark_provider_anchors(*paths).report
    review = report["provider_transition_review"]

    assert report["schema"] == "scriptorium-provider-anchor-benchmark/v6"
    assert review["schema"] == "scriptorium-provider-transition-review/v3"
    assert review["policy"]["runtime_reorder"] is False
    assert review["policy"]["selection_uses_semantic_labels"] is False
    assert review["direct_transition_count"] == 1
    assert review["confidence_available_transition_count"] == 1
    assert review["support_histogram"] == {
        "0": 0,
        "1": 0,
        "2": 0,
        "3": 1,
        "4": 0,
    }
    transition = review["transitions"][0]
    assert transition["minimum_provider_confidence"] == 0.88
    assert transition["native_support_count"] == 3
    assert transition["native_supporting_candidates"] == [
        "visual-yx",
        "box-flow",
        "relation-graph",
    ]
    assert review["candidate_edge_semantics"]["relation-graph"] == (
        "selected-edges-from-max-regret-path-cover"
    )
    assert _curve_point(review, support=3, confidence=0.85)["precision"] == 1.0
    assert _curve_point(review, support=3, confidence=0.85)["predicted"] == 1
    assert _curve_point(review, support=3, confidence=0.9)["predicted"] == 0


def test_provider_native_evidence_separates_xy_handoffs_from_relation_edges() -> None:
    oracle_nodes = [
        {"id": "left-top", "box": [60, 70, 240, 80]},
        {"id": "left-bottom", "box": [60, 88, 240, 98]},
        {"id": "right-top", "box": [320, 70, 500, 80]},
        {"id": "right-bottom", "box": [320, 88, 500, 98]},
    ]

    evidence = _native_candidate_direct_edges(
        oracle_nodes,
        width=612,
        height=792,
    )

    handoff = ("left-bottom", "right-top")
    assert handoff in evidence["recursive-xy-cut"]
    assert handoff not in evidence["relation-graph"]
    assert evidence["relation-graph"] == {
        ("left-top", "left-bottom"),
        ("right-top", "right-bottom"),
    }


def test_provider_transition_gate_eligibility_is_relation_label_invariant(tmp_path) -> None:
    oracle = {
        "uid": "label-invariant",
        "img": {"width": 100, "height": 100},
        "document": [
            {"id": "a", "box": [10, 10, 90, 18], "type": "text"},
            {"id": "b", "box": [10, 22, 90, 30], "type": "text"},
        ],
    }
    provider = {
        "source": "provider",
        "pages": [
            {
                "elements": [
                    {"id": "pa", "box": [10, 10, 90, 18], "confidence": 0.9},
                    {"id": "pb", "box": [10, 22, 90, 30], "confidence": 0.9},
                ]
            }
        ],
    }
    oracle_path = tmp_path / "oracle.json"
    provider_path = tmp_path / "provider.json"
    oracle_path.write_text(json.dumps(oracle))
    provider_path.write_text(json.dumps(provider))
    reviews = []
    for index, labels in enumerate(([['a', 'b']], [['b', 'a']])):
        semantic_path = tmp_path / f"semantic-{index}.json"
        semantic_path.write_text(json.dumps({**oracle, "ro_linkings": labels}))
        reviews.append(
            benchmark_provider_anchors(
                oracle_path,
                semantic_path,
                provider_path,
            ).report["provider_transition_review"]
        )

    first = _curve_point(reviews[0], support=1, confidence=0.85)
    second = _curve_point(reviews[1], support=1, confidence=0.85)
    assert first["predicted"] == second["predicted"] == 1
    assert first["eligible_fraction"] == second["eligible_fraction"] == 1.0
    assert first["correct"] == 1
    assert second["correct"] == 0


def test_provider_transition_precision_does_not_treat_unlabelled_edges_as_errors(
    tmp_path,
) -> None:
    oracle = {
        "uid": "partial-labels",
        "img": {"width": 100, "height": 100},
        "document": [
            {"id": "header", "box": [10, 5, 90, 12], "type": "text"},
            {"id": "body-1", "box": [10, 20, 90, 28], "type": "text"},
            {"id": "body-2", "box": [10, 32, 90, 40], "type": "text"},
        ],
    }
    provider = {
        "source": "provider",
        "pages": [
            {
                "elements": [
                    {"id": "p-header", "box": [10, 5, 90, 12]},
                    {"id": "p-body-1", "box": [10, 20, 90, 28]},
                    {"id": "p-body-2", "box": [10, 32, 90, 40]},
                ]
            }
        ],
    }
    paths = [tmp_path / name for name in ("oracle.json", "semantic.json", "provider.json")]
    for path, payload in zip(
        paths,
        (oracle, {**oracle, "ro_linkings": [["body-1", "body-2"]]}, provider),
        strict=True,
    ):
        path.write_text(json.dumps(payload))

    review = benchmark_provider_anchors(*paths).report["provider_transition_review"]
    baseline = _curve_point(review, support=0, confidence=None)

    assert review["scorable_direct_transition_count"] == 1
    assert baseline["eligible"] == 2
    assert baseline["predicted"] == 1
    assert baseline["unscored"] == 1
    assert baseline["correct"] == 1
    assert baseline["precision"] == 1.0
    transitions = {
        (item["source"], item["target"]): item
        for item in review["transitions"]
    }
    assert transitions[("header", "body-1")]["scorable"] is False
    assert transitions[("body-1", "body-2")]["scorable"] is True


def test_provider_transition_suite_preserves_unscored_counts() -> None:
    def review(*, correct: int, predicted: int, eligible: int) -> dict:
        return {
            "policy": {"partial_label_policy": "endpoint-universe"},
            "candidate_orders": list(PROVIDER_TRANSITION_CANDIDATES),
            "direct_transition_count": eligible,
            "labelled_node_count": 3,
            "scorable_direct_transition_count": predicted,
            "confidence_available_transition_count": eligible,
            "support_histogram": {"0": 0, "1": eligible, "2": 0, "3": 0},
            "candidate_direct_edge_counts": {
                "visual-yx": eligible,
                "box-flow": eligible,
                "relation-graph": eligible,
            },
            "curve": [
                {
                    "minimum_native_support": 1,
                    "minimum_provider_confidence": 0.85,
                    "eligible": eligible,
                    "predicted": predicted,
                    "unscored": eligible - predicted,
                    "correct": correct,
                    "labels": 4,
                }
            ],
        }

    aggregate = _sum_provider_transition_reviews(
        [
            review(correct=2, predicted=2, eligible=3),
            review(correct=1, predicted=2, eligible=4),
        ]
    )
    point = aggregate["curve"][0]

    assert point["eligible"] == 7
    assert point["predicted"] == 4
    assert point["unscored"] == 3
    assert point["scorable_fraction"] == 0.57142857
    assert point["correct"] == 3
    assert point["incorrect"] == 1
    assert point["precision"] == 0.75


def test_transition_gate_freezes_fit_curve_and_evaluates_without_reselection(
    tmp_path,
) -> None:
    review = {
        "candidate_orders": list(PROVIDER_TRANSITION_CANDIDATES),
        "curve": [
            {
                "minimum_native_support": 1,
                "minimum_provider_confidence": 0.8,
                "predicted": 100,
                "correct": 94,
                "labels": 500,
                "precision": 0.94,
                "precision_wilson_lower_95": 0.88,
            },
            {
                "minimum_native_support": 1,
                "minimum_provider_confidence": 0.85,
                "predicted": 80,
                "correct": 77,
                "labels": 500,
                "precision": 0.9625,
                "precision_wilson_lower_95": 0.91,
            },
            {
                "minimum_native_support": 2,
                "minimum_provider_confidence": 0.85,
                "predicted": 55,
                "correct": 54,
                "labels": 500,
                "precision": 0.98181818,
                "precision_wilson_lower_95": 0.91,
            },
        ],
    }
    report_path = tmp_path / "suite.json"
    report_path.write_text(
        json.dumps(
            {
                "corpus_manifest_sha256": "fit-corpus",
                "corpus": {"dataset": "Comp-HRDoc train"},
                "partitions": {"fit": {"provider_transition_review": review}},
            }
        )
    )

    frozen = freeze_provider_transition_gate(report_path)

    assert frozen.gate["runtime_reorder"] is False
    assert frozen.gate["minimum_native_support"] == 1
    assert frozen.gate["minimum_provider_confidence"] == 0.85
    assert frozen.gate["fit_metrics"]["predicted"] == 80
    test_review = {
        "candidate_orders": review["candidate_orders"],
        "curve": [
            {
                "minimum_native_support": 1,
                "minimum_provider_confidence": 0.85,
                "predicted": 75,
                "correct": 72,
                "labels": 480,
                "precision": 0.96,
                "precision_wilson_lower_95": 0.905,
            }
        ],
    }
    evaluation = _evaluate_provider_transition_gate(test_review, frozen.gate)
    assert evaluation["meets_frozen_acceptance_criteria"] is True
    assert evaluation["metrics"]["predicted"] == 75

    position_audit = _provider_transition_position_audit(
        [
            {
                "provider_transition_review": {
                    "transitions": [
                        {
                            "transition_index": 0,
                            "page_transition_count": 3,
                            "native_support_count": 1,
                            "minimum_provider_confidence": 0.85,
                            "correct": False,
                        },
                        {
                            "transition_index": 0,
                            "page_transition_count": 3,
                            "native_support_count": 1,
                            "minimum_provider_confidence": 0.85,
                            "scorable": False,
                            "correct": False,
                        },
                        {
                            "transition_index": 1,
                            "page_transition_count": 3,
                            "native_support_count": 2,
                            "minimum_provider_confidence": 0.9,
                            "correct": True,
                        },
                        {
                            "transition_index": 2,
                            "page_transition_count": 3,
                            "native_support_count": 0,
                            "minimum_provider_confidence": 0.99,
                            "correct": True,
                        },
                    ]
                }
            }
        ],
        frozen.gate,
    )
    assert position_audit["start"]["eligible"] == 2
    assert position_audit["start"]["predicted"] == 1
    assert position_audit["start"]["unscored"] == 1
    assert position_audit["start"]["correct"] == 0
    assert position_audit["middle"]["precision"] == 1.0
    assert position_audit["end"]["predicted"] == 0


def test_stratified_gate_abstains_unqualified_buckets_and_freezes_on_calibration(
    tmp_path,
) -> None:
    fit_good = _transition_case(
        "fit-good",
        partition="fit",
        layout_stratum="multicolumn",
        correct=[True, True, True, True, False],
    )
    fit_bad = _transition_case(
        "fit-bad",
        partition="fit",
        layout_stratum="graphical-multicolumn",
        correct=[False, False, False, False, True],
    )
    fit_middle = _transition_case(
        "fit-middle",
        partition="fit",
        layout_stratum="multicolumn",
        correct=[True, True, True],
        transition_index=1,
    )
    calibration = _transition_case(
        "calibration-good",
        partition="calibration",
        layout_stratum="multicolumn",
        correct=[True, True, True],
    )
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "corpus_manifest_sha256": "train-corpus",
                "corpus": {"dataset": "Comp-HRDoc train"},
                "cases": [fit_good, fit_bad, fit_middle, calibration],
            }
        )
    )

    result = freeze_stratified_provider_transition_gate(
        suite_path,
        minimum_native_support=1,
        cross_validation_folds=0,
        fit_minimum_precision=0.8,
        fit_minimum_wilson_lower_95=0.0,
        fit_minimum_predicted=3,
        calibration_minimum_precision=0.8,
        calibration_minimum_wilson_lower_95=0.0,
        calibration_minimum_predicted=2,
        test_minimum_precision=0.8,
        test_minimum_wilson_lower_95=0.0,
        test_minimum_predicted=2,
        test_bucket_minimum_precision=0.8,
        test_bucket_minimum_wilson_lower_95=0.0,
        test_bucket_minimum_predicted=2,
        allowed_layout_strata=["multicolumn"],
        allowed_position_bands=["start"],
    )

    gate = result.gate
    assert gate["schema"] == "scriptorium-provider-transition-gate/v3"
    assert gate["calibration_accepted"] is True
    assert gate["calibration_can_modify_rules"] is False
    assert gate["bucket_definition"]["allowed_layout_strata"] == ["multicolumn"]
    assert gate["bucket_definition"]["allowed_position_bands"] == ["start"]
    assert [
        (rule["layout_stratum"], rule["position_band"])
        for rule in gate["rules"]
    ] == [("multicolumn", "start")]
    assert gate["inactive_buckets"] == [
        {
            "layout_stratum": "graphical-multicolumn",
            "position_band": "start",
            "fit_transition_count": 5,
            "reason": "excluded-by-predeclared-layout-policy",
        },
        {
            "layout_stratum": "multicolumn",
            "position_band": "middle",
            "fit_transition_count": 3,
            "reason": "excluded-by-predeclared-position-policy",
        },
    ]
    held_out_cases = [
        _transition_case(
            "test-good",
            partition="test",
            layout_stratum="multicolumn",
            correct=[True, True, True],
        ),
        _transition_case(
            "test-abstain",
            partition="test",
            layout_stratum="graphical-multicolumn",
            correct=[False, False],
        ),
    ]
    evaluation = _evaluate_provider_transition_gate(
        {
            "candidate_orders": list(PROVIDER_TRANSITION_CANDIDATES),
        },
        gate,
        cases=held_out_cases,
    )
    assert evaluation["meets_frozen_acceptance_criteria"] is True
    assert evaluation["aggregate_metrics"]["correct"] == 3
    assert evaluation["unruled_transition_count"] == 2
    assert evaluation["abstained_transition_count"] == 2


def test_stratified_gate_rejects_low_partial_label_scorability(tmp_path) -> None:
    fit = _transition_case(
        "fit",
        partition="fit",
        layout_stratum="multicolumn",
        correct=[True, True, True, True],
    )
    calibration = _transition_case(
        "calibration",
        partition="calibration",
        layout_stratum="multicolumn",
        correct=[True, True, False, False],
    )
    calibration_transitions = calibration["provider_transition_review"]["transitions"]
    for transition in calibration_transitions[2:]:
        transition["scorable"] = False
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "corpus_manifest_sha256": "train-corpus",
                "corpus": {"dataset": "Comp-HRDoc train"},
                "cases": [fit, calibration],
            }
        )
    )

    result = freeze_stratified_provider_transition_gate(
        suite_path,
        minimum_native_support=1,
        cross_validation_folds=0,
        fit_minimum_precision=1.0,
        fit_minimum_wilson_lower_95=0.0,
        fit_minimum_predicted=2,
        calibration_minimum_precision=1.0,
        calibration_minimum_wilson_lower_95=0.0,
        calibration_minimum_predicted=2,
        calibration_minimum_scorable_fraction=0.8,
        test_minimum_precision=1.0,
        test_minimum_wilson_lower_95=0.0,
        test_minimum_predicted=2,
        test_bucket_minimum_precision=1.0,
        test_bucket_minimum_wilson_lower_95=0.0,
        test_bucket_minimum_predicted=2,
    )

    metrics = result.gate["calibration_aggregate_metrics"]
    assert metrics["eligible"] == 4
    assert metrics["predicted"] == 2
    assert metrics["precision"] == 1.0
    assert metrics["scorable_fraction"] == 0.5
    assert result.gate["calibration_accepted"] is False


def test_stratified_gate_cross_validates_consensus_by_document(tmp_path) -> None:
    fit_cases = [
        _transition_case(
            f"document-{index}_0",
            document_id=f"document-{index}",
            partition="fit",
            layout_stratum="multicolumn",
            correct=[True, True],
            native_support_count=2,
        )
        for index in range(4)
    ]
    calibration = _transition_case(
        "calibration-document_0",
        document_id="calibration-document",
        partition="calibration",
        layout_stratum="multicolumn",
        correct=[True, True],
        native_support_count=2,
    )
    for case in [*fit_cases, calibration]:
        case["provider_transition_review"]["transitions"].append(
            {
                "transition_index": 0,
                "page_transition_count": 3,
                "native_support_count": 1,
                "minimum_provider_confidence": 0.99,
                "correct": False,
            }
        )
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "corpus_manifest_sha256": "train-corpus",
                "corpus": {"dataset": "Comp-HRDoc train"},
                "cases": [*fit_cases, calibration],
            }
        )
    )

    result = freeze_stratified_provider_transition_gate(
        suite_path,
        minimum_native_support=2,
        cross_validation_folds=2,
        fit_minimum_precision=1.0,
        fit_minimum_wilson_lower_95=0.0,
        fit_minimum_predicted=2,
        calibration_minimum_precision=1.0,
        calibration_minimum_wilson_lower_95=0.0,
        calibration_minimum_predicted=2,
        test_minimum_precision=1.0,
        test_minimum_wilson_lower_95=0.0,
        test_minimum_predicted=2,
        test_bucket_minimum_precision=1.0,
        test_bucket_minimum_wilson_lower_95=0.0,
        test_bucket_minimum_predicted=2,
    )

    gate = result.gate
    cross_validation = gate["document_cross_validation"]
    assert gate["schema"] == "scriptorium-provider-transition-gate/v3"
    assert gate["minimum_native_support"] == 2
    assert all(rule["minimum_native_support"] >= 2 for rule in gate["rules"])
    assert cross_validation["accepted"] is True
    assert cross_validation["out_of_fold_metrics"]["eligible"] == 8
    assert cross_validation["out_of_fold_metrics"]["predicted"] == 8
    assert cross_validation["out_of_fold_metrics"]["precision"] == 1.0
    assert cross_validation["document_count"] == 4
    assert len(cross_validation["bucket_evaluations"]) == 1
    assert cross_validation["bucket_evaluations"][0][
        "meets_acceptance_criteria"
    ] is True
    for fold in cross_validation["folds"]:
        assert set(fold["training_document_ids"]).isdisjoint(
            fold["validation_document_ids"]
        )
    assert gate["calibration_quality_accepted"] is True
    assert all(gate["calibration_criterion_results"].values())
    assert gate["calibration_accepted"] is True


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
                        "document_id": "document-0",
                        "partition": "calibration",
                        "layout_stratum": "multicolumn",
                        "structure": "structure/sample_0.structure.json",
                        "semantic_sidecar": "images/sample_0.semantic-order.json",
                    }
                ],
            }
        )
    )

    result = benchmark_provider_anchor_suite(corpus, providers)

    assert result.report["schema"] == "scriptorium-provider-anchor-suite/v8"
    assert result.report["case_count"] == 1
    assert result.report["cases"][0]["document_id"] == "document-0"
    assert result.report["cases"][0]["sample_id"] == "sample_0"
    assert result.report["relations"]["combined"]["correct"] == 1
    assert result.report["relations"]["combined"]["f1"] > 0
    assert result.report["graphical_relation_audit"]["cases_with_conflicts"] == 0
    assert result.report["provider_degradation"]["case_count"] == 1
    assert result.report["provider_degradation"]["error_taxonomy"]["missing"][
        "denominator"
    ] == 2
    assert result.report["cases"][0]["partition"] == "calibration"
    assert result.report["partitions"]["calibration"]["sample_ids"] == ["sample_0"]
    assert result.report["partitions"]["calibration"]["layout_strata"] == {
        "multicolumn": 1
    }
    assert result.report["layout_strata"]["multicolumn"]["sample_ids"] == [
        "sample_0"
    ]
    assert result.report["partitions"]["calibration"]["relations"]["combined"] == (
        result.report["relations"]["combined"]
    )
    transition_review = result.report["provider_transition_review"]
    assert transition_review["schema"] == (
        "scriptorium-provider-transition-review-suite/v3"
    )
    assert transition_review["case_count"] == 1
    assert transition_review["direct_transition_count"] == 0
    assert (
        result.report["partitions"]["calibration"]["provider_transition_review"]
        == transition_review
    )


def test_suite_transition_records_preserve_document_groups() -> None:
    records = _suite_transition_records(
        {
            "cases": [
                {
                    "sample_id": "paper_3",
                    "document_id": "paper",
                    "partition": "fit",
                    "layout_stratum": "multicolumn",
                    "provider_transition_review": {
                        "transitions": [
                            {
                                "page_transition_count": 1,
                                "transition_index": 0,
                            }
                        ]
                    },
                }
            ]
        },
        partition="fit",
    )

    assert len(records) == 1
    assert records[0]["sample_id"] == "paper_3"
    assert records[0]["document_id"] == "paper"


def test_legacy_suite_keeps_its_original_candidate_semantics() -> None:
    candidates, semantics = _suite_provider_transition_candidate_evidence(
        {
            "provider_transition_review": {
                "candidate_orders": [
                    "visual-yx",
                    "box-flow",
                    "relation-graph",
                ]
            }
        }
    )

    assert candidates == ["visual-yx", "box-flow", "relation-graph"]
    assert set(semantics.values()) == {"legacy-report-unspecified"}


def test_anchor_matcher_does_not_use_oracle_list_order() -> None:
    _, anchors, _ = normalize_provider_anchors(_docling_payload())
    nodes = [
        {"id": "later", "box": [10, 20, 90, 28], "type": "text"},
        {"id": "earlier", "box": [10, 10, 90, 18], "type": "text"},
    ]

    matches = match_provider_anchors(nodes, anchors)

    assert matches["later"]["oracle_box"][1] > matches["earlier"]["oracle_box"][1]


def test_serialized_provider_edges_separate_anchor_scope_and_page_boundaries() -> None:
    anchors = [
        ProviderAnchor("page-1-a", 0, "text", (0, 0, 10, 20), "", 0),
        ProviderAnchor("page-1-unmatched", 0, "text", (0, 30, 10, 40), "", 1),
        ProviderAnchor("page-1-c", 0, "text", (0, 50, 10, 60), "", 2),
        ProviderAnchor("page-2-a", 1, "text", (0, 0, 10, 10), "", 0),
    ]
    assignments = {
        "line-1": {"provider_id": "page-1-a", "oracle_box": [0, 0, 10, 8]},
        "line-2": {"provider_id": "page-1-a", "oracle_box": [0, 10, 10, 18]},
        "line-3": {"provider_id": "page-1-c", "oracle_box": [0, 50, 10, 58]},
        "line-4": {"provider_id": "page-2-a", "oracle_box": [0, 0, 10, 8]},
    }

    groups = _serialized_provider_edge_groups(anchors, assignments)

    assert groups["within_anchor"] == {("line-1", "line-2")}
    assert groups["between_anchors"] == {("line-2", "line-3")}
    assert groups["direct_between_anchors"] == set()
    assert groups["all"] == {("line-1", "line-2"), ("line-2", "line-3")}
    assert ("line-3", "line-4") not in groups["all"]


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
                    "noise_aware_reliability_tier": "robust-high-precision-review",
                    "noise_aware_strict_gate_passed": True,
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
    assert (
        result.report["relations"]["noise_aware_reliable_trained_floating"][
            "correct"
        ]
        == 1
    )
    assert (
        result.report["relations"]["noise_aware_strict_trained_floating"]["correct"]
        == 1
    )
    assert result.report["floating_model_sha256"] == "floating"


def _transition_case(
    sample_id: str,
    *,
    document_id: str | None = None,
    partition: str,
    layout_stratum: str,
    correct: list[bool],
    transition_index: int = 0,
    native_support_count: int = 1,
) -> dict:
    return {
        "sample_id": sample_id,
        "document_id": document_id or sample_id,
        "partition": partition,
        "layout_stratum": layout_stratum,
        "provider_transition_review": {
            "transitions": [
                {
                    "transition_index": transition_index,
                    "page_transition_count": 3,
                    "native_support_count": native_support_count,
                    "minimum_provider_confidence": 0.9,
                    "correct": value,
                }
                for value in correct
            ]
        },
    }


def _curve_point(
    review: dict,
    *,
    support: int,
    confidence: float | None,
) -> dict:
    return next(
        point
        for point in review["curve"]
        if point["minimum_native_support"] == support
        and point["minimum_provider_confidence"] == confidence
    )


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
