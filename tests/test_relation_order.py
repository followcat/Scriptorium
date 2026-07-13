from scriptorium.relation_order import merge_relation_edge_path_cover


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
