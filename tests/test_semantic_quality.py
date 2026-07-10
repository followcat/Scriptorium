import json
from pathlib import Path

from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.semantic_quality import compare_semantic_reading_order, semantic_ground_truth_candidates


def test_ordered_subsequence_ground_truth_ignores_unlabeled_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    sidecar_dir = tmp_path / "benchmarks" / "semantic-ground-truth"
    sidecar_dir.mkdir(parents=True)
    source_pdf = tmp_path / "external" / "paper.pdf"
    source_pdf.parent.mkdir()
    source_pdf.write_bytes(b"%PDF-1.4\n")
    (sidecar_dir / "paper.semantic-order.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source_pdf": "paper.pdf",
                "pages": [
                    {
                        "page_index": 0,
                        "match_mode": "ordered-subsequence",
                        "text_sequence": ["Section title", "First labeled line", "Second labeled line"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(
        [
            "Running header",
            "Section title",
            "Unlabeled paragraph line",
            "First labeled line",
            "Footer",
            "Second labeled line",
        ]
    )

    report = compare_semantic_reading_order(document, source_pdf, tmp_path / "semantic")
    page = report["pages"][0]

    assert report["ground_truth_available"] is True
    assert report["ground_truth"].endswith("benchmarks/semantic-ground-truth/paper.semantic-order.json")
    assert page["match_mode"] == "ordered-subsequence"
    assert page["matched_text_count"] == 3
    assert page["ignored_text_count"] == 3
    assert [item["text"] for item in page["ignored_texts"]] == [
        "Running header",
        "Unlabeled paragraph line",
        "Footer",
    ]
    assert page["ignored_text_zone_counts"] == {"body": 1, "footer": 1, "header": 1}
    assert page["ignored_text_role_counts"] == {"footer": 1, "paragraph": 1, "running-header": 1}
    assert page["ignored_text_source_counts"] == {"unit-test": 3}
    assert page["extra_text_count"] == 0
    assert page["sequence_similarity"] == 1
    assert page["pairwise_order_accuracy"] == 1
    assert page["successor_order_accuracy"] == 1
    assert report["semantic_ignored_text_count"] == 3
    assert report["semantic_ignored_text_zone_counts"] == {"body": 1, "footer": 1, "header": 1}
    assert report["semantic_ignored_text_role_counts"] == {"footer": 1, "paragraph": 1, "running-header": 1}
    assert report["semantic_ignored_text_source_counts"] == {"unit-test": 3}
    assert report["semantic_order_pair_accuracy"] == 1
    assert report["semantic_successor_accuracy"] == 1
    assert report["semantic_successor_correct_count"] == 2
    assert report["semantic_successor_total_count"] == 2


def test_repo_ground_truth_can_be_scoped_by_parent_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    sidecar_dir = tmp_path / "benchmarks" / "semantic-ground-truth"
    sidecar_dir.mkdir(parents=True)
    source_pdf = tmp_path / "external" / "web-hn" / "input.pdf"
    source_pdf.parent.mkdir(parents=True)
    source_pdf.write_bytes(b"%PDF-1.4\n")
    (sidecar_dir / "web-hn.input.semantic-order.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {
                        "page_index": 0,
                        "match_mode": "ordered-subsequence",
                        "text_sequence": ["Front page", "First item"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["Front page", "First item", "Ignored footer"])

    candidates = semantic_ground_truth_candidates(source_pdf)
    report = compare_semantic_reading_order(document, source_pdf, tmp_path / "semantic")

    assert any(path.name == "web-hn.input.semantic-order.json" for path in candidates)
    assert report["ground_truth"].endswith("benchmarks/semantic-ground-truth/web-hn.input.semantic-order.json")
    assert report["semantic_order_pair_accuracy"] == 1


def test_successor_accuracy_catches_adjacent_order_regression(tmp_path: Path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    semantic_ground_truth = source_pdf.with_suffix(".semantic-order.json")
    semantic_ground_truth.write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {
                        "page_index": 0,
                        "text_sequence": ["A", "B", "C", "D"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["A", "C", "B", "D"])

    report = compare_semantic_reading_order(document, source_pdf, tmp_path / "semantic")
    page = report["pages"][0]

    assert page["pairwise_order_accuracy"] == 0.83333333
    assert page["successor_correct_count"] == 0
    assert page["successor_total_count"] == 3
    assert page["successor_order_accuracy"] == 0
    assert report["semantic_successor_accuracy"] == 0


def test_successor_accuracy_counts_missing_adjacent_text(tmp_path: Path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    semantic_ground_truth = source_pdf.with_suffix(".semantic-order.json")
    semantic_ground_truth.write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {
                        "page_index": 0,
                        "text_sequence": ["A", "B", "C"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["A", "C"])

    report = compare_semantic_reading_order(document, source_pdf, tmp_path / "semantic")
    page = report["pages"][0]

    assert page["missing_texts"] == ["B"]
    assert page["successor_correct_count"] == 0
    assert page["successor_total_count"] == 2
    assert report["semantic_successor_correct_count"] == 0
    assert report["semantic_successor_total_count"] == 2
    assert report["semantic_successor_accuracy"] == 0


def test_candidate_orders_are_scored_against_semantic_ground_truth(tmp_path: Path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    semantic_ground_truth = source_pdf.with_suffix(".semantic-order.json")
    semantic_ground_truth.write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {
                        "page_index": 0,
                        "text_sequence": ["A", "B", "C", "D"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["A", "C", "B", "D"])

    report = compare_semantic_reading_order(
        document,
        source_pdf,
        tmp_path / "semantic",
        candidate_orders={
            "selected_like": {0: ["e0", "e1", "e2", "e3"]},
            "fixed": {0: ["e0", "e2", "e1", "e3"]},
        },
    )
    page_candidates = report["pages"][0]["candidate_orders"]

    assert report["semantic_successor_accuracy"] == 0
    assert page_candidates["selected_like"]["successor_order_accuracy"] == 0
    assert page_candidates["fixed"]["successor_order_accuracy"] == 1
    assert report["semantic_candidate_order_metrics"]["fixed"]["semantic_successor_accuracy"] == 1
    assert report["semantic_best_candidate_by_successor"] == "fixed"
    assert report["semantic_best_candidate_successor_accuracy"] == 1


def test_semantic_ground_truth_matches_sparse_source_page_index(tmp_path: Path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    source_pdf.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {
                        "page_index": 1,
                        "text_sequence": ["A", "B"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["A", "B"], page_index=1)

    report = compare_semantic_reading_order(
        document,
        source_pdf,
        tmp_path / "semantic",
        candidate_orders={"selected_like": {1: ["e0", "e1"]}},
    )
    page = report["pages"][0]

    assert report["ground_truth_available"] is True
    assert page["truth_page_index"] == 1
    assert page["page_index"] == 1
    assert page["candidate_orders"]["selected_like"]["successor_order_accuracy"] == 1
    assert report["semantic_successor_accuracy"] == 1


def test_relation_edges_ground_truth_scores_selected_and_candidate_orders(tmp_path: Path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    semantic_ground_truth = source_pdf.with_suffix(".semantic-order.json")
    semantic_ground_truth.write_text(
        json.dumps(
            {
                "version": 2,
                "pages": [
                    {
                        "page_index": 0,
                        "successor_edges": [["A", "B"], {"source": "C", "target": "D"}],
                        "precedence_edges": [["A", "D"], {"from": "B", "to": "D"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["A", "C", "B", "D"])

    report = compare_semantic_reading_order(
        document,
        source_pdf,
        tmp_path / "semantic",
        candidate_orders={
            "selected_like": {0: ["e0", "e1", "e2", "e3"]},
            "fixed": {0: ["e0", "e2", "e1", "e3"]},
        },
    )
    page = report["pages"][0]
    page_candidates = page["candidate_orders"]

    assert page["match_mode"] == "ordered-subsequence"
    assert page["expected_text_count"] == 0
    assert page["extra_text_count"] == 0
    assert page["ignored_text_count"] == 0
    assert report["semantic_relation_successor_correct_count"] == 0
    assert report["semantic_relation_successor_total_count"] == 2
    assert report["semantic_relation_successor_accuracy"] == 0
    assert report["semantic_relation_precedence_correct_count"] == 2
    assert report["semantic_relation_precedence_total_count"] == 2
    assert report["semantic_relation_precedence_accuracy"] == 1
    assert page_candidates["selected_like"]["relation_successor_accuracy"] == 0
    assert page_candidates["fixed"]["relation_successor_accuracy"] == 1
    assert report["semantic_candidate_order_metrics"]["fixed"]["semantic_relation_successor_accuracy"] == 1
    assert report["semantic_best_candidate_by_relation_successor"] == "fixed"
    assert report["semantic_best_candidate_relation_successor_accuracy"] == 1


def test_relation_edges_report_missing_labels(tmp_path: Path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    source_pdf.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_index": 0,
                        "successor_edges": [["A", "Missing"]],
                        "precedence_edges": [["Missing", "B"]],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["A", "B"])

    report = compare_semantic_reading_order(document, source_pdf, tmp_path / "semantic")

    assert report["semantic_relation_missing_text_count"] == 1
    assert report["semantic_relation_missing_texts"] == ["Missing"]
    assert report["semantic_relation_successor_accuracy"] == 0
    assert report["semantic_relation_precedence_accuracy"] == 0


def test_roor_document_linkings_score_relations_through_segment_ids(tmp_path: Path) -> None:
    source_image = tmp_path / "page.png"
    source_image.write_bytes(b"fake image bytes")
    source_image.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 3,
                "pages": [
                    {
                        "page_index": 0,
                        "document": [
                            {"id": 0, "box": [0, 0, 10, 10], "text": "A"},
                            {"id": 1, "box": [20, 0, 30, 10], "text": "C"},
                            {"id": 2, "box": [0, 20, 10, 30], "text": "B"},
                            {"id": 3, "box": [20, 20, 30, 30], "text": "D"},
                        ],
                        "ro_linkings": [[0, 2], [2, 1], [1, 3]],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["A", "C", "B", "D"])

    report = compare_semantic_reading_order(
        document,
        source_image,
        tmp_path / "semantic",
        candidate_orders={
            "selected_like": {0: ["e0", "e1", "e2", "e3"]},
            "fixed": {0: ["e0", "e2", "e1", "e3"]},
        },
    )
    page = report["pages"][0]
    page_candidates = page["candidate_orders"]

    assert page["match_mode"] == "ordered-subsequence"
    assert page["expected_text_count"] == 0
    assert page["ignored_text_count"] == 0
    assert report["semantic_relation_successor_correct_count"] == 0
    assert report["semantic_relation_successor_total_count"] == 3
    assert report["semantic_relation_successor_accuracy"] == 0
    assert page_candidates["selected_like"]["relation_successor_accuracy"] == 0
    assert page_candidates["fixed"]["relation_successor_accuracy"] == 1
    assert report["semantic_candidate_order_metrics"]["fixed"]["semantic_relation_successor_accuracy"] == 1
    assert report["semantic_best_candidate_by_relation_successor"] == "fixed"


def test_reading_order_relation_aliases_score_through_segment_ids(tmp_path: Path) -> None:
    source_image = tmp_path / "page.png"
    source_image.write_bytes(b"fake image bytes")
    source_image.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 3,
                "pages": [
                    {
                        "page_index": 0,
                        "document": [
                            {"element_id": "title", "text": "Title"},
                            {"element_id": "body", "text": "Body"},
                            {"element_id": "note", "text": "Note"},
                        ],
                        "reading_order_relations": [{"head": "title", "tail": "body"}],
                        "precedence_edges": [{"source_id": "body", "target_id": "note"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["Title", "Body", "Note"])

    report = compare_semantic_reading_order(document, source_image, tmp_path / "semantic")

    assert report["semantic_relation_successor_correct_count"] == 1
    assert report["semantic_relation_successor_total_count"] == 1
    assert report["semantic_relation_successor_accuracy"] == 1
    assert report["semantic_relation_precedence_correct_count"] == 1
    assert report["semantic_relation_precedence_total_count"] == 1
    assert report["semantic_relation_precedence_accuracy"] == 1


def test_typed_relations_score_through_segment_ids(tmp_path: Path) -> None:
    source_image = tmp_path / "page.png"
    source_image.write_bytes(b"fake image bytes")
    source_image.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 3,
                "pages": [
                    {
                        "page_index": 0,
                        "document": [
                            {"id": "title", "text": "Title"},
                            {"id": "body", "text": "Body"},
                            {"id": "note", "text": "Note"},
                        ],
                        "relations": [
                            {"type": "successor", "source": "title", "target": "body"},
                            {"kind": "before", "head": "body", "tail": "note"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["Title", "Body", "Note"])

    report = compare_semantic_reading_order(document, source_image, tmp_path / "semantic")

    assert report["semantic_relation_successor_correct_count"] == 1
    assert report["semantic_relation_successor_total_count"] == 1
    assert report["semantic_relation_successor_accuracy"] == 1
    assert report["semantic_relation_precedence_correct_count"] == 1
    assert report["semantic_relation_precedence_total_count"] == 1
    assert report["semantic_relation_precedence_accuracy"] == 1


def test_structure_region_ids_score_relations_and_streams(tmp_path: Path) -> None:
    source_image = tmp_path / "page.png"
    source_image.write_bytes(b"fake image bytes")
    source_image.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 3,
                "pages": [
                    {
                        "page_index": 0,
                        "formula_res_list": [
                            {
                                "formula_region_id": "formula-1",
                                "rec_formula": "E=mc^2",
                            }
                        ],
                        "seal_res_list": [
                            {
                                "seal_region_id": "seal-1",
                                "rec_texts": ["Seal"],
                            }
                        ],
                        "successor_edges": [["formula-1", "seal-1"]],
                        "reading_streams": [
                            {
                                "id": "stamp-flow",
                                "type": "body",
                                "members": ["formula-1", "seal-1"],
                                "successor_edges": [["formula-1", "seal-1"]],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["E=mc^2", "Seal"])
    for index, element in enumerate(document.pages[0].elements, start=1):
        element.metadata["reading_order_stream_id"] = "stamp-flow"
        element.metadata["reading_order_stream_type"] = "body"
        element.metadata["reading_order_stream_index"] = index

    report = compare_semantic_reading_order(document, source_image, tmp_path / "semantic")

    assert report["semantic_relation_successor_correct_count"] == 1
    assert report["semantic_relation_successor_total_count"] == 1
    assert report["semantic_relation_successor_accuracy"] == 1
    assert report["semantic_stream_successor_correct_count"] == 1
    assert report["semantic_stream_successor_total_count"] == 1
    assert report["semantic_stream_successor_accuracy"] == 1
    assert report["semantic_stream_assignment_label_count"] == 2
    assert report["semantic_stream_assignment_id_accuracy"] == 1
    assert report["semantic_stream_assignment_type_accuracy"] == 1


def test_reading_streams_score_local_successors_independently(tmp_path: Path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    source_pdf.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 3,
                "pages": [
                    {
                        "page_index": 0,
                        "reading_streams": [
                            {
                                "stream_id": "body-main",
                                "stream_type": "body",
                                "text_sequence": ["Body A", "Body B"],
                            },
                            {
                                "stream_id": "sidebar-right",
                                "stream_type": "sidebar-right",
                                "text_sequence": ["Side A", "Side B"],
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["Body A", "Side A", "Body B", "Side B"])

    report = compare_semantic_reading_order(
        document,
        source_pdf,
        tmp_path / "semantic",
        candidate_orders={
            "selected_like": {0: ["e0", "e1", "e2", "e3"]},
            "sidebar_first": {0: ["e1", "e3", "e0", "e2"]},
        },
    )
    page = report["pages"][0]

    assert page["match_mode"] == "ordered-subsequence"
    assert page["stream_count"] == 2
    assert page["stream_successor_correct_count"] == 2
    assert page["stream_successor_total_count"] == 2
    assert page["stream_successor_accuracy"] == 1
    assert page["stream_precedence_accuracy"] == 1
    assert report["semantic_stream_successor_accuracy"] == 1
    assert report["semantic_stream_precedence_accuracy"] == 1
    assert report["semantic_relation_successor_accuracy"] is None
    assert report["semantic_candidate_order_metrics"]["selected_like"]["semantic_stream_successor_accuracy"] == 1
    assert report["semantic_candidate_order_metrics"]["sidebar_first"]["semantic_stream_successor_accuracy"] == 1
    assert report["semantic_best_candidate_by_stream_successor"] in {"selected_like", "sidebar_first"}


def test_reading_stream_member_aliases_and_typed_relations_score_local_streams(tmp_path: Path) -> None:
    source_image = tmp_path / "page.png"
    source_image.write_bytes(b"fake image bytes")
    source_image.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 3,
                "pages": [
                    {
                        "page_index": 0,
                        "document": [
                            {"id": "a", "text": "A"},
                            {"id": "b", "text": "B"},
                            {"id": "c", "text": "C"},
                            {"id": "d", "text": "D"},
                        ],
                        "reading_streams": [
                            {
                                "id": "product-row",
                                "type": "product_grid",
                                "members": ["a", "b"],
                                "reading_order_linkings": [["a", "b"]],
                            },
                            {
                                "id": "right-rail",
                                "type": "right_sidebar",
                                "elements": ["c", "d"],
                                "relations": [
                                    {"type": "successor", "source_id": "c", "target_id": "d"},
                                ],
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["A", "C", "B", "D"])

    report = compare_semantic_reading_order(document, source_image, tmp_path / "semantic")
    page = report["pages"][0]

    assert page["stream_count"] == 2
    assert page["stream_successor_correct_count"] == 2
    assert page["stream_successor_total_count"] == 2
    assert page["stream_successor_accuracy"] == 1
    assert page["stream_missing_text_count"] == 0
    assert [stream["label_count"] for stream in page["reading_streams"]] == [2, 2]
    assert report["semantic_stream_successor_accuracy"] == 1


def test_reading_stream_assignments_score_ir_stream_metadata(tmp_path: Path) -> None:
    source_image = tmp_path / "page.png"
    source_image.write_bytes(b"fake image bytes")
    source_image.with_suffix(".semantic-order.json").write_text(
        json.dumps(
            {
                "version": 3,
                "pages": [
                    {
                        "page_index": 0,
                        "reading_streams": [
                            {
                                "id": "product-row",
                                "type": "product_grid",
                                "members": ["A", "B"],
                            },
                            {
                                "id": "right-rail",
                                "type": "right_sidebar",
                                "members": ["C", "D"],
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document = _document_with_texts(["A", "B", "C", "D"])
    by_text = {element.source_text: element for element in document.pages[0].elements}
    by_text["A"].metadata["reading_order_stream_id"] = "product-row"
    by_text["A"].metadata["reading_order_stream_type"] = "grid-island"
    by_text["B"].metadata["reading_order_stream_id"] = "product-row"
    by_text["B"].metadata["reading_order_stream_type"] = "grid-island"
    by_text["C"].metadata["reading_order_stream_id"] = "wrong"
    by_text["C"].metadata["reading_order_stream_type"] = "sidebar-right"
    by_text["D"].metadata["reading_order_stream_id"] = "right-rail"
    by_text["D"].metadata["reading_order_stream_type"] = "body"

    report = compare_semantic_reading_order(document, source_image, tmp_path / "semantic")
    page = report["pages"][0]

    assert page["stream_assignment_label_count"] == 4
    assert page["stream_assignment_found_count"] == 4
    assert page["stream_assignment_missing_count"] == 0
    assert page["stream_assignment_id_correct_count"] == 3
    assert page["stream_assignment_id_mismatch_count"] == 1
    assert page["stream_assignment_type_correct_count"] == 3
    assert page["stream_assignment_type_total_count"] == 4
    assert page["stream_assignment_type_mismatch_count"] == 1
    assert page["stream_assignment_type_confusion_counts"] == {"sidebar-right=>body": 1}
    assert page["stream_assignment_id_accuracy"] == 0.75
    assert page["stream_assignment_type_accuracy"] == 0.75
    assert page["reading_streams"][0]["assignment_id_accuracy"] == 1
    assert page["reading_streams"][1]["assignment_id_accuracy"] == 0.5
    assert page["reading_streams"][1]["assignment_type_accuracy"] == 0.5
    assert page["reading_streams"][1]["assignment_type_confusion_counts"] == {"sidebar-right=>body": 1}
    assert report["semantic_stream_assignment_label_count"] == 4
    assert report["semantic_stream_assignment_found_count"] == 4
    assert report["semantic_stream_assignment_id_mismatch_count"] == 1
    assert report["semantic_stream_assignment_type_mismatch_count"] == 1
    assert report["semantic_stream_assignment_type_confusion_counts"] == {"sidebar-right=>body": 1}
    assert report["semantic_stream_assignment_id_accuracy"] == 0.75
    assert report["semantic_stream_assignment_type_accuracy"] == 0.75


def _document_with_texts(texts: list[str], page_index: int = 0) -> DocumentIR:
    elements = []
    for index, text in enumerate(texts):
        if text == "Running header":
            y0 = 8
            role = "running-header"
        elif text in {"Footer", "Ignored footer"}:
            y0 = 282
            role = "footer"
        else:
            y0 = 70 + index * 12
            role = "paragraph"
        elements.append(
            ElementIR(
                id=f"e{index}",
                page_index=page_index,
                type="text",
                bbox_pdf=BBox(x0=10, y0=y0, x1=120, y1=y0 + 10),
                bbox_px=BBox(x0=10, y0=y0, x1=120, y1=y0 + 10),
                source_text=text,
                reading_order=index + 1,
                metadata={"annotation": {"role": role, "source_kind": "unit-test"}},
            )
        )
    page = PageIR(
        page_index=page_index,
        width_pt=200,
        height_pt=300,
        width_px=200,
        height_px=300,
        render_dpi=72,
        scale_x=1,
        scale_y=1,
        background_image="",
        elements=elements,
    )
    return DocumentIR(source_pdf="paper.pdf", render_dpi=72, page_count=1, pages=[page])
