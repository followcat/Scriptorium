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


def _document_with_texts(texts: list[str]) -> DocumentIR:
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
                page_index=0,
                type="text",
                bbox_pdf=BBox(x0=10, y0=y0, x1=120, y1=y0 + 10),
                bbox_px=BBox(x0=10, y0=y0, x1=120, y1=y0 + 10),
                source_text=text,
                reading_order=index + 1,
                metadata={"annotation": {"role": role, "source_kind": "unit-test"}},
            )
        )
    page = PageIR(
        page_index=0,
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
