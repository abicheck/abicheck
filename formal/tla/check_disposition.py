#!/usr/bin/env python3
"""Explicit-state BFS over DispositionPipeline.tla's transition relation.

An independent transcription of the spec's Init/Step relation (all subsets
D, M, A, B) that checks NothingVanishes. compare_tlc.py requires its
reachable graph to equal TLC's for the same constants. Prints the number of states and, on a violation, the
shortest trace.

    python3 formal/tla/check_disposition.py --ids 4 --steps 2 --adds
    python3 formal/tla/check_disposition.py --ids 5 --steps 3
"""

from __future__ import annotations

import argparse
from collections import deque
from itertools import chain, combinations


def subsets(s: frozenset[int]):
    items = sorted(s)
    return (
        frozenset(c)
        for c in chain.from_iterable(
            combinations(items, r) for r in range(len(items) + 1)
        )
    )


# A state: (changes, buckets, ledger, seen, fresh, steps), the spec's
# variables in its own order (ids are the model values f1.. as 1..).
State = tuple[
    frozenset[int], frozenset[int], frozenset[int], frozenset[int], frozenset[int], int
]


def initial_states(n_ids: int) -> list[State]:
    """The spec's Init: a nonempty initial finding set, everything else empty."""
    ids = frozenset(range(1, n_ids + 1))
    return [(c, frozenset(), frozenset(), c, ids - c, 0) for c in subsets(ids) if c]


def successors(state: State, max_steps: int, adds: bool):
    """The spec's Step, one (D, M, A, B) choice at a time. The stuttering
    disjunct of Next (``steps = MaxSteps /\\ UNCHANGED vars``) only adds
    self-loops and is left out."""
    changes, buckets, ledger, seen, fresh, steps = state
    if steps >= max_steps:
        return
    for D in subsets(changes):
        for M in subsets(changes - D):
            for A in subsets(fresh) if adds else [frozenset()]:
                for B in subsets(fresh - A) if adds else [frozenset()]:
                    after = (changes - (D | M)) | A
                    trigger = len(after) + len(M | B) < len(changes)
                    nxt: State = (
                        after,
                        buckets | M | B,
                        ledger | D if trigger else ledger,
                        seen | A | B,
                        fresh - (A | B),
                        steps + 1,
                    )
                    yield (D, M, A, B), nxt


def nothing_vanishes(state: State) -> bool:
    changes, buckets, ledger, seen, _fresh, _steps = state
    return seen <= changes | buckets | ledger


def check(n_ids: int, max_steps: int, adds: bool):
    init = initial_states(n_ids)
    parent: dict = {s: None for s in init}
    queue = deque(init)
    while queue:
        state = queue.popleft()
        if not nothing_vanishes(state):
            trace = []
            while state is not None:
                trace.append(state)
                state = parent[state][0] if parent[state] else None
            return len(parent), list(reversed(trace))
        for choice, nxt in successors(state, max_steps, adds):
            if nxt not in parent:
                parent[nxt] = (state, choice)
                queue.append(nxt)
    return len(parent), None


def graph(n_ids: int, max_steps: int, adds: bool):
    """The whole reachable graph, not stopping at a violation: (initial
    states, reachable states, transitions as (src, "Step", dst)). Compared
    with TLC's ``-dump dot`` output by compare_tlc.py."""
    init = initial_states(n_ids)
    seen_states = set(init)
    edges: set[tuple[State, str, State]] = set()
    queue = deque(init)
    while queue:
        state = queue.popleft()
        for _choice, nxt in successors(state, max_steps, adds):
            edges.add((state, "Step", nxt))
            if nxt not in seen_states:
                seen_states.add(nxt)
                queue.append(nxt)
    return set(init), seen_states, edges


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=int, default=4)
    ap.add_argument("--steps", type=int, default=2)
    ap.add_argument("--adds", action="store_true")
    a = ap.parse_args()
    n, trace = check(a.ids, a.steps, a.adds)
    print(f"states={n} adds={a.adds} ids={a.ids} steps={a.steps}")
    if trace is None:
        print("NothingVanishes: holds")
    else:
        print("NothingVanishes: VIOLATED; trace (changes, buckets, ledger, seen):")
        for s in trace:
            print("  ", [sorted(x) for x in s[:4]])


if __name__ == "__main__":
    main()
