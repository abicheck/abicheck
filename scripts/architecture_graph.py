"""Cycle search over the ADR-061 layer graph and module import graph.

A pure leaf with no I/O, split out of ``check_architecture.py`` to keep that
gate under its own grandfathered file-size record.
"""

from __future__ import annotations

from collections.abc import Mapping


def find_cycle(graph: Mapping[str, set[str]]) -> list[str] | None:
    active: list[str] = []
    done: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in active:
            start = active.index(node)
            return [*active[start:], node]
        if node in done:
            return None
        active.append(node)
        for target in sorted(graph.get(node, ())):
            cycle = visit(target)
            if cycle:
                return cycle
        active.pop()
        done.add(node)
        return None

    for node in sorted(graph):
        cycle = visit(node)
        if cycle:
            return cycle
    return None
