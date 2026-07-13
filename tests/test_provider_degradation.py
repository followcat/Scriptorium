from __future__ import annotations

from scriptorium.provider_anchor_benchmark import ProviderAnchor
from scriptorium.provider_degradation import (
    characterize_provider_degradation,
    compare_with_synthetic_profiles,
)


def test_exact_grouped_provider_has_zero_degradation_signature() -> None:
    oracle = [
        _oracle("a-1", "a", "text", [10, 10, 90, 18], "First line"),
        _oracle("a-2", "a", "text", [10, 20, 90, 28], "Second line"),
        _oracle("figure-anchor", "figure-block", "figure", [10, 40, 90, 70], ""),
        _oracle("figure-label", "figure-label", "text", [20, 45, 80, 50], "Axis label"),
        _oracle("caption", "caption", "text", [10, 72, 90, 80], "Figure 1. Result"),
    ]
    provider = [
        _provider("pa-1", "a", "text", [10, 10, 90, 18], "First line", 0),
        _provider("pa-2", "a", "text", [10, 20, 90, 28], "Second line", 1),
        _provider("pf", "provider-figure", "figure", [10, 40, 90, 70], "", 2),
        _provider("pfl", "figure-label", "text", [20, 45, 80, 50], "Axis label", 3),
        _provider("nested", "nested", "text", [20, 52, 60, 64], "OCR", 4),
        _provider("pc", "caption", "caption", [10, 72, 90, 80], "Figure 1. Result", 5),
    ]

    report = characterize_provider_degradation(oracle, provider, width=100, height=100)

    assert report["error_taxonomy"]["missing"]["count"] == 0
    assert report["error_taxonomy"]["hallucination"]["count"] == 0
    assert report["nested_graphical_content"]["count"] == 1
    assert report["error_taxonomy"]["split"]["count"] == 0
    assert report["error_taxonomy"]["merge"]["count"] == 0
    assert report["error_taxonomy"]["overlap"]["count"] == 0
    assert report["type_confusion"]["incompatible_count"] == 0
    assert report["geometry"]["center_distance_ratio"]["p90"] == 0
    assert report["text_fidelity"]["token_f1"]["mean"] == 1
    assert report["text_fidelity"]["caption"]["prefix_preservation_rate"] == 1
    assert all(value == 0 for value in report["signature"].values())


def test_structural_degradation_categories_are_separated() -> None:
    oracle = [
        _oracle("a-1", "a", "text", [0, 0, 40, 10], "Alpha one"),
        _oracle("a-2", "a", "text", [0, 12, 40, 22], "Alpha two"),
        _oracle("b", "b", "text", [60, 0, 100, 10], "Beta"),
        _oracle("figure", "figure", "figure", [0, 30, 100, 60], ""),
        _oracle("caption", "caption", "text", [0, 62, 100, 72], "Figure 1. Result"),
        _oracle("size", "size", "text", [60, 80, 100, 90], "Sized"),
        _oracle("missing", "missing", "text", [80, 105, 100, 115], "Missing"),
    ]
    provider = [
        _provider("a-1", "split-1", "text", [0, 0, 40, 10], "Alpha one", 0),
        _provider("a-2", "split-2", "text", [0, 12, 40, 22], "Alpha two", 1),
        _provider("b", "merged", "text", [60, 0, 100, 10], "Beta", 2),
        _provider("caption", "merged", "text", [0, 62, 100, 72], "xxxxxxxxxxxxsult", 3),
        _provider("figure-1", "figure-1", "text", [0, 30, 100, 60], "", 4),
        _provider("figure-2", "figure-2", "text", [0, 30, 100, 60], "", 5),
        _provider("size", "size", "text", [50, 75, 100, 95], "Sized", 6),
        _provider("hallucinated", "hallucinated", "text", [0, 105, 10, 115], "Extra", 7),
    ]

    report = characterize_provider_degradation(oracle, provider, width=100, height=120)

    taxonomy = report["error_taxonomy"]
    assert taxonomy["missing"]["count"] == 1
    assert taxonomy["hallucination"]["count"] == 1
    assert taxonomy["size_error"]["count"] >= 1
    assert taxonomy["split"]["count"] == 1
    assert taxonomy["merge"]["count"] == 1
    assert taxonomy["overlap"]["count"] >= 1
    assert taxonomy["duplicate"]["count"] == 1
    assert taxonomy["misclassification"]["count"] == 1
    assert report["segmentation"]["fragmentation_excess_part_count"] == 1
    assert report["segmentation"]["merge_excess_source_count"] == 1
    assert report["text_fidelity"]["caption"]["prefix_preservation_rate"] == 0


def test_profile_distance_is_descriptive_and_deterministic() -> None:
    oracle = [_oracle("a", "a", "text", [0, 0, 100, 10], "Text")]
    exact = characterize_provider_degradation(
        oracle,
        [_provider("a", "a", "text", [0, 0, 100, 10], "Text", 0)],
        width=100,
        height=100,
    )
    shifted = characterize_provider_degradation(
        oracle,
        [_provider("a", "a", "text", [0, 0, 100, 30], "xxxx", 0)],
        width=100,
        height=100,
    )

    comparison = compare_with_synthetic_profiles(
        exact,
        {"clean": exact, "stress": shifted},
    )

    assert comparison["nearest_profile"] == "clean"
    assert comparison["profiles"]["clean"]["distance"] == 0
    assert comparison["profiles"]["stress"]["distance"] > 0


def _oracle(
    anchor_id: str,
    block_id: str,
    kind: str,
    box: list[float],
    text: str,
) -> dict:
    return {"id": anchor_id, "block_id": block_id, "type": kind, "box": box, "text": text}


def _provider(
    anchor_id: str,
    group_id: str,
    kind: str,
    box: list[float],
    text: str,
    order: int,
) -> ProviderAnchor:
    return ProviderAnchor(
        anchor_id,
        0,
        kind,
        tuple(box),
        text,
        order,
        group_id,
    )
