#!/usr/bin/env python3
"""Explicit-state BFS over DispositionPipeline.tla's transition relation.

A stand-in for TLC in environments where the TLA+ tools cannot run; it
enumerates exactly the same Init/Step relation (all subsets D, M, A, B) and
checks NothingVanishes. Prints the number of states and, on a violation, the
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


def check(n_ids: int, max_steps: int, adds: bool):
    ids = frozenset(range(1, n_ids + 1))
    init = [(c, frozenset(), frozenset(), c, ids - c, 0) for c in subsets(ids) if c]
    parent: dict = {s: None for s in init}
    queue = deque(init)
    while queue:
        state = queue.popleft()
        changes, buckets, ledger, seen, fresh, steps = state
        if not seen <= changes | buckets | ledger:
            trace = []
            while state is not None:
                trace.append(state)
                state = parent[state][0] if parent[state] else None
            return len(parent), list(reversed(trace))
        if steps >= max_steps:
            continue
        for D in subsets(changes):
            for M in subsets(changes - D):
                for A in subsets(fresh) if adds else [frozenset()]:
                    for B in subsets(fresh - A) if adds else [frozenset()]:
                        after = (changes - (D | M)) | A
                        trigger = len(after) + len(M | B) < len(changes)
                        nxt = (
                            after,
                            buckets | M | B,
                            ledger | D if trigger else ledger,
                            seen | A | B,
                            fresh - (A | B),
                            steps + 1,
                        )
                        if nxt not in parent:
                            parent[nxt] = (state, (D, M, A, B))
                            queue.append(nxt)
    return len(parent), None


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
