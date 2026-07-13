from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class WeightedMatch:
    left_index: int
    right_index: int
    score: float


def maximum_weight_bipartite_matching(
    scores: Sequence[Sequence[float | None]],
    *,
    minimum_score: float = 0.0,
) -> tuple[WeightedMatch, ...]:
    """Return a deterministic one-to-one matching for a rectangular score matrix.

    Eligible pairs first maximize cardinality, then total score. ``None`` and
    scores below ``minimum_score`` are ineligible. Dummy columns let either side
    remain unmatched without requiring SciPy as a core dependency.
    """

    if not math.isfinite(minimum_score):
        raise ValueError("minimum_score must be finite")
    left_count = len(scores)
    if left_count == 0:
        return ()
    right_count = len(scores[0])
    if any(len(row) != right_count for row in scores):
        raise ValueError("score matrix must be rectangular")
    if right_count == 0:
        return ()

    normalized: list[list[float | None]] = []
    eligible_scores: list[float] = []
    for row in scores:
        normalized_row: list[float | None] = []
        for raw_score in row:
            if raw_score is None:
                normalized_row.append(None)
                continue
            score = float(raw_score)
            if not math.isfinite(score):
                raise ValueError("matching scores must be finite")
            if score < minimum_score:
                normalized_row.append(None)
                continue
            normalized_row.append(score)
            eligible_scores.append(score)
        normalized.append(normalized_row)
    if not eligible_scores:
        return ()

    low = min(eligible_scores)
    span = max(eligible_scores) - low
    maximum_pairs = min(left_count, right_count)
    cardinality_bonus = float(maximum_pairs + 1)
    augmented: list[list[float]] = []
    for row in normalized:
        real_columns = [
            -1.0
            if score is None
            else cardinality_bonus + ((score - low) / span if span else 0.0)
            for score in row
        ]
        augmented.append(real_columns + [0.0] * left_count)

    assigned_columns = _hungarian_minimize(
        [[-weight for weight in row] for row in augmented]
    )
    matches = []
    for left_index, right_index in enumerate(assigned_columns):
        if right_index >= right_count:
            continue
        score = normalized[left_index][right_index]
        if score is not None:
            matches.append(WeightedMatch(left_index, right_index, score))
    return tuple(matches)


def _hungarian_minimize(costs: Sequence[Sequence[float]]) -> list[int]:
    """Solve a rectangular assignment where rows do not outnumber columns."""

    row_count = len(costs)
    column_count = len(costs[0])
    if row_count > column_count:
        raise ValueError("Hungarian assignment requires rows <= columns")
    row_potential = [0.0] * (row_count + 1)
    column_potential = [0.0] * (column_count + 1)
    column_row = [0] * (column_count + 1)
    previous_column = [0] * (column_count + 1)
    epsilon = 1e-12

    for row in range(1, row_count + 1):
        column_row[0] = row
        current_column = 0
        minimum_slack = [math.inf] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[current_column] = True
            current_row = column_row[current_column]
            delta = math.inf
            next_column = 0
            for column in range(1, column_count + 1):
                if used[column]:
                    continue
                reduced_cost = (
                    costs[current_row - 1][column - 1]
                    - row_potential[current_row]
                    - column_potential[column]
                )
                if reduced_cost < minimum_slack[column] - epsilon:
                    minimum_slack[column] = reduced_cost
                    previous_column[column] = current_column
                if minimum_slack[column] < delta - epsilon:
                    delta = minimum_slack[column]
                    next_column = column
            for column in range(column_count + 1):
                if used[column]:
                    row_potential[column_row[column]] += delta
                    column_potential[column] -= delta
                else:
                    minimum_slack[column] -= delta
            current_column = next_column
            if column_row[current_column] == 0:
                break
        while True:
            next_column = previous_column[current_column]
            column_row[current_column] = column_row[next_column]
            current_column = next_column
            if current_column == 0:
                break

    assignments = [column_count] * row_count
    for column in range(1, column_count + 1):
        if column_row[column]:
            assignments[column_row[column] - 1] = column - 1
    return assignments
