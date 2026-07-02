import json
from pathlib import Path

from scriptorium.models import BBox, DocumentIR, ElementIR, PageIR
from scriptorium.semantic_quality import compare_semantic_reading_order


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
    assert page["extra_text_count"] == 0
    assert page["sequence_similarity"] == 1
    assert page["pairwise_order_accuracy"] == 1
    assert report["semantic_ignored_text_count"] == 3
    assert report["semantic_order_pair_accuracy"] == 1


def _document_with_texts(texts: list[str]) -> DocumentIR:
    elements = [
        ElementIR(
            id=f"e{index}",
            page_index=0,
            type="text",
            bbox_pdf=BBox(x0=10, y0=10 + index * 12, x1=120, y1=20 + index * 12),
            bbox_px=BBox(x0=10, y0=10 + index * 12, x1=120, y1=20 + index * 12),
            source_text=text,
            reading_order=index + 1,
        )
        for index, text in enumerate(texts)
    ]
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
