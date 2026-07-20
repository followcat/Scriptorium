from scriptorium.relation_order import (
    merge_relation_edge_path_cover,
    merge_scored_relation_edge_path_cover_max_regret,
)


def test_merge_relation_edges_prioritizes_protected_float_chain() -> None:
    result = merge_relation_edge_path_cover(
        [("body-a", "caption"), ("caption", "body-b"), ("body-b", "body-a")],
        protected_edges=[("figure", "caption")],
    )

    assert result.protected_selected_edges == (("figure", "caption"),)
    assert result.selected_edges == (
        ("figure", "caption"),
        ("caption", "body-b"),
        ("body-b", "body-a"),
    )
    assert result.rejected_incoming_conflict_count == 1
    assert result.rejected_cycle_count == 0


def test_merge_relation_edges_rejects_cycles_and_degree_conflicts() -> None:
    result = merge_relation_edge_path_cover(
        [("a", "b"), ("a", "c"), ("c", "b"), ("b", "a"), ("a", "a")]
    )

    assert result.selected_edges == (("a", "b"),)
    assert result.rejected_outgoing_conflict_count == 1
    assert result.rejected_incoming_conflict_count == 1
    assert result.rejected_cycle_count == 1
    assert result.rejected_self_loop_count == 1


def test_max_regret_path_cover_prevents_flexible_source_edge_theft() -> None:
    result = merge_scored_relation_edge_path_cover_max_regret(
        [
            ("flexible", "shared", 0.95),
            ("flexible", "fallback", 0.94),
            ("constrained", "shared", 0.90),
            ("constrained", "poor", 0.10),
        ]
    )

    assert result.selected_edges == (
        ("constrained", "shared"),
        ("flexible", "fallback"),
    )
    assert result.positive_regret_decision_count == 1
    assert result.decision_count == 2


def test_max_regret_path_cover_keeps_protected_edges_and_avoids_cycles() -> None:
    result = merge_scored_relation_edge_path_cover_max_regret(
        [
            ("b", "c", 0.9),
            ("b", "e", 0.1),
            ("c", "a", 0.8),
            ("c", "d", 0.7),
        ],
        protected_edges=[("a", "b")],
    )

    assert result.protected_selected_edges == (("a", "b"),)
    assert result.selected_edges == (("a", "b"), ("b", "c"), ("c", "d"))
