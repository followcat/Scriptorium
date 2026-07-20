from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class RelationEdgeMergeResult:
    selected_edges: tuple[tuple[Hashable, Hashable], ...]
    protected_selected_edges: tuple[tuple[Hashable, Hashable], ...]
    rejected_outgoing_conflict_count: int
    rejected_incoming_conflict_count: int
    rejected_cycle_count: int
    rejected_self_loop_count: int


@dataclass(frozen=True)
class MaxRegretRelationEdgeMergeResult:
    selected_edges: tuple[tuple[Hashable, Hashable], ...]
    protected_selected_edges: tuple[tuple[Hashable, Hashable], ...]
    candidate_edge_count: int
    decision_count: int
    positive_regret_decision_count: int
    single_feasible_candidate_decision_count: int
    exhausted_source_count: int


def merge_relation_edge_path_cover(
    candidate_edges: Iterable[tuple[Hashable, Hashable]],
    *,
    protected_edges: Iterable[tuple[Hashable, Hashable]] = (),
) -> RelationEdgeMergeResult:
    """Merge ordered relation evidence into an acyclic degree-one path cover."""

    successor: dict[Hashable, Hashable] = {}
    predecessor: dict[Hashable, Hashable] = {}
    selected: list[tuple[Hashable, Hashable]] = []
    protected_selected: list[tuple[Hashable, Hashable]] = []
    seen: set[tuple[Hashable, Hashable]] = set()
    outgoing_rejections = 0
    incoming_rejections = 0
    cycle_rejections = 0
    self_loop_rejections = 0

    def consider(edge: tuple[Hashable, Hashable], *, protected: bool) -> None:
        nonlocal outgoing_rejections, incoming_rejections, cycle_rejections, self_loop_rejections
        source, target = edge
        if edge in seen:
            return
        seen.add(edge)
        if source == target:
            self_loop_rejections += 1
            return
        if source in successor:
            outgoing_rejections += 1
            return
        if target in predecessor:
            incoming_rejections += 1
            return
        if _generic_successor_path_reaches(successor, start=target, target=source):
            cycle_rejections += 1
            return
        successor[source] = target
        predecessor[target] = source
        selected.append(edge)
        if protected:
            protected_selected.append(edge)

    for edge in protected_edges:
        consider(edge, protected=True)
    for edge in candidate_edges:
        consider(edge, protected=False)
    return RelationEdgeMergeResult(
        tuple(selected),
        tuple(protected_selected),
        outgoing_rejections,
        incoming_rejections,
        cycle_rejections,
        self_loop_rejections,
    )


def merge_scored_relation_edge_path_cover_max_regret(
    candidate_edges: Iterable[tuple[Hashable, Hashable, float]],
    *,
    protected_edges: Iterable[tuple[Hashable, Hashable]] = (),
) -> MaxRegretRelationEdgeMergeResult:
    """Select a degree-one acyclic path cover by dynamic source regret.

    Every iteration recomputes each unresolved source's feasible outgoing
    alternatives. Sources with a large best-vs-second-best score gap commit
    first, which prevents a flexible source from taking a target needed by a
    source with no comparable alternative. Input order is the deterministic
    tie-breaker. A source with fewer than two feasible choices has zero regret.
    """

    protected = merge_relation_edge_path_cover((), protected_edges=protected_edges)
    successor = dict(protected.selected_edges)
    predecessor = {target: source for source, target in protected.selected_edges}
    selected = list(protected.selected_edges)

    alternatives_by_source: dict[
        Hashable,
        list[tuple[float, int, Hashable]],
    ] = {}
    seen_edges: set[tuple[Hashable, Hashable]] = set()
    candidate_count = 0
    for input_rank, (source, target, raw_score) in enumerate(candidate_edges):
        edge = (source, target)
        if edge in seen_edges or source == target:
            continue
        seen_edges.add(edge)
        candidate_count += 1
        alternatives_by_source.setdefault(source, []).append(
            (float(raw_score), input_rank, target)
        )
    for alternatives in alternatives_by_source.values():
        alternatives.sort(key=lambda item: (-item[0], item[1]))

    source_rank = {
        source: rank for rank, source in enumerate(alternatives_by_source)
    }
    unresolved = {
        source for source in alternatives_by_source if source not in successor
    }
    decision_count = 0
    positive_regret_count = 0
    single_candidate_count = 0
    exhausted_source_count = 0

    while unresolved:
        decisions: list[
            tuple[float, float, int, int, Hashable, Hashable]
        ] = []
        exhausted: list[Hashable] = []
        for source in sorted(unresolved, key=source_rank.__getitem__):
            feasible = [
                alternative
                for alternative in alternatives_by_source[source]
                if alternative[2] not in predecessor
                and not _generic_successor_path_reaches(
                    successor,
                    start=alternative[2],
                    target=source,
                )
            ]
            if not feasible:
                exhausted.append(source)
                continue
            best_score, best_rank, best_target = feasible[0]
            regret = best_score - feasible[1][0] if len(feasible) >= 2 else 0.0
            decisions.append(
                (
                    regret,
                    best_score,
                    -source_rank[source],
                    -best_rank,
                    source,
                    best_target,
                )
            )

        for source in exhausted:
            unresolved.remove(source)
        exhausted_source_count += len(exhausted)
        if not decisions:
            break

        regret, _score, _source_priority, _edge_priority, source, target = max(
            decisions,
            key=lambda item: item[:4],
        )
        feasible_count = sum(
            1
            for _score, _rank, alternative_target in alternatives_by_source[source]
            if alternative_target not in predecessor
            and not _generic_successor_path_reaches(
                successor,
                start=alternative_target,
                target=source,
            )
        )
        successor[source] = target
        predecessor[target] = source
        selected.append((source, target))
        unresolved.remove(source)
        decision_count += 1
        positive_regret_count += regret > 0.0
        single_candidate_count += feasible_count == 1

    return MaxRegretRelationEdgeMergeResult(
        selected_edges=tuple(selected),
        protected_selected_edges=protected.protected_selected_edges,
        candidate_edge_count=candidate_count,
        decision_count=decision_count,
        positive_regret_decision_count=positive_regret_count,
        single_feasible_candidate_decision_count=single_candidate_count,
        exhausted_source_count=exhausted_source_count,
    )


def _generic_successor_path_reaches(
    successor: dict[Hashable, Hashable],
    *,
    start: Hashable,
    target: Hashable,
) -> bool:
    current = start
    seen: set[Hashable] = set()
    while current in successor:
        if current in seen:
            return True
        seen.add(current)
        current = successor[current]
        if current == target:
            return True
    return False


def relation_edge_candidate_order(
    *,
    item_count: int,
    successor_edges: list[tuple[int, int]],
    precedence_edges: list[tuple[int, int]],
    base_order: list[int],
) -> list[int]:
    ordered, _chains = relation_edge_candidate_path_cover(
        item_count=item_count,
        successor_edges=successor_edges,
        precedence_edges=precedence_edges,
        base_order=base_order,
    )
    return ordered


def relation_edge_candidate_path_cover(
    *,
    item_count: int,
    successor_edges: list[tuple[int, int]],
    precedence_edges: list[tuple[int, int]],
    base_order: list[int],
) -> tuple[list[int], list[list[int]]]:
    if item_count < 2:
        return [], []
    normalized_base_order = _normalized_base_order(base_order, item_count)
    base_rank = {index: rank for rank, index in enumerate(normalized_base_order)}
    successor_by_source = _degree_constrained_successors(successor_edges, item_count)
    chains = _successor_chains(item_count, successor_by_source, base_rank)
    if not chains:
        return [], []

    chain_by_item = {
        item: chain_index
        for chain_index, chain in enumerate(chains)
        for item in chain
    }
    chain_edges: set[tuple[int, int]] = set()
    for source, target in [*successor_by_source.items(), *precedence_edges]:
        if source < 0 or target < 0 or source >= item_count or target >= item_count or source == target:
            continue
        source_chain = chain_by_item.get(source)
        target_chain = chain_by_item.get(target)
        if source_chain is None or target_chain is None or source_chain == target_chain:
            continue
        chain_edges.add((source_chain, target_chain))

    ordered_chain_indices = _topological_chain_order(chains, chain_edges, base_rank)
    if not ordered_chain_indices:
        return [], []
    ordered_chains = [chains[chain_index] for chain_index in ordered_chain_indices]
    ordered = [item for chain_index in ordered_chain_indices for item in chains[chain_index]]
    if len(ordered) != item_count or len(set(ordered)) != item_count:
        return [], []
    return ordered, ordered_chains


def _normalized_base_order(base_order: list[int], item_count: int) -> list[int]:
    ordered: list[int] = []
    seen: set[int] = set()
    for index in base_order:
        if index < 0 or index >= item_count or index in seen:
            continue
        seen.add(index)
        ordered.append(index)
    ordered.extend(index for index in range(item_count) if index not in seen)
    return ordered


def _degree_constrained_successors(
    edges: list[tuple[int, int]],
    item_count: int,
) -> dict[int, int]:
    successor_by_source: dict[int, int] = {}
    predecessor_by_target: dict[int, int] = {}
    for source, target in edges:
        if source < 0 or target < 0 or source >= item_count or target >= item_count or source == target:
            continue
        if source in successor_by_source or target in predecessor_by_target:
            continue
        if _successor_path_reaches(successor_by_source, start=target, target=source):
            continue
        successor_by_source[source] = target
        predecessor_by_target[target] = source
    return successor_by_source


def _successor_path_reaches(successor_by_source: dict[int, int], *, start: int, target: int) -> bool:
    current = start
    seen: set[int] = set()
    while current in successor_by_source:
        if current in seen:
            return True
        seen.add(current)
        current = successor_by_source[current]
        if current == target:
            return True
    return False


def _successor_chains(
    item_count: int,
    successor_by_source: dict[int, int],
    base_rank: dict[int, int],
) -> list[list[int]]:
    predecessor_targets = set(successor_by_source.values())
    chain_starts = [index for index in range(item_count) if index not in predecessor_targets]
    chain_starts.sort(key=lambda index: base_rank.get(index, index))
    chains: list[list[int]] = []
    seen: set[int] = set()
    for start in chain_starts:
        if start in seen:
            continue
        chain: list[int] = []
        current = start
        while current not in seen:
            chain.append(current)
            seen.add(current)
            if current not in successor_by_source:
                break
            current = successor_by_source[current]
        if chain:
            chains.append(chain)
    for index in range(item_count):
        if index not in seen:
            chains.append([index])
    return chains


def _topological_chain_order(
    chains: list[list[int]],
    chain_edges: set[tuple[int, int]],
    base_rank: dict[int, int],
) -> list[int]:
    indegree = {index: 0 for index in range(len(chains))}
    outgoing: dict[int, set[int]] = {index: set() for index in range(len(chains))}
    for source, target in chain_edges:
        if source == target or source not in outgoing or target not in indegree:
            continue
        if target in outgoing[source]:
            continue
        outgoing[source].add(target)
        indegree[target] += 1

    def chain_rank(chain_index: int) -> tuple[int, int]:
        return (
            min(base_rank.get(item, item) for item in chains[chain_index]),
            chain_index,
        )

    ready = sorted((index for index, degree in indegree.items() if degree == 0), key=chain_rank)
    ordered: list[int] = []
    while ready:
        chain_index = ready.pop(0)
        ordered.append(chain_index)
        for target in sorted(outgoing[chain_index], key=chain_rank):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
        ready.sort(key=chain_rank)
    return ordered if len(ordered) == len(chains) else []
