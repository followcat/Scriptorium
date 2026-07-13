from __future__ import annotations

import pytest

from scriptorium.bipartite_matching import maximum_weight_bipartite_matching


def test_matching_maximizes_cardinality_before_total_score() -> None:
    matches = maximum_weight_bipartite_matching(
        [
            [0.90, 0.80],
            [0.89, None],
        ],
        minimum_score=0.5,
    )

    assert [(match.left_index, match.right_index) for match in matches] == [
        (0, 1),
        (1, 0),
    ]


def test_matching_leaves_ineligible_rows_unmatched() -> None:
    matches = maximum_weight_bipartite_matching(
        [[0.4], [0.9]],
        minimum_score=0.5,
    )

    assert [(match.left_index, match.right_index, match.score) for match in matches] == [
        (1, 0, 0.9)
    ]


def test_matching_rejects_ragged_or_non_finite_scores() -> None:
    with pytest.raises(ValueError, match="rectangular"):
        maximum_weight_bipartite_matching([[0.5], [0.5, 0.6]])
    with pytest.raises(ValueError, match="finite"):
        maximum_weight_bipartite_matching([[float("nan")]])
