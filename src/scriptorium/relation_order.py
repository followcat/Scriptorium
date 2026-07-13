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
