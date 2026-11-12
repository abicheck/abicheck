#!/usr/bin/env python3
"""Explicit-state BFS over ScopeCompleteness.tla's transition relation (E8).

A stand-in for TLC: it enumerates the same Init / Acquire / Classify relation
and checks I1-I3 on every reachable state with no member left `absent`. The
exit formula and the state vocabulary are a line-by-line transcription of the
.tla file (see its header for the mapping to abicheck's code).

    python3 formal/tla/check_scope.py --members 3
    python3 formal/tla/check_scope.py --members 2 --mutant warn-clean-zero

`--mutant` seeds one known-wrong variant of the spec so the invariants can be
seen to fail (a checker that never fails proves nothing):
    classify-unguarded  Classify retires a member without NewProven   -> I2
    warn-clean-zero     D7 only under `block` (zero comparisons pass warn) -> I1
    failed-ops-dropped  a failed member contributes nothing to the exit -> I3
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass

ABSENT = "absent"
ACQUIRE_STATES = (
    "unselected",
    "expected_not_produced",
    "failed",
    "not_supplied",
    "compared_ok",
    "compared_break",
)
POLICIES = ("warn", "block")
MUTANTS = ("classify-unguarded", "warn-clean-zero", "failed-ops-dropped")


@dataclass(frozen=True)
class Spec:
    """The spec variant under check. ``mutant`` None is the faithful spec."""

    mutant: str | None = None

    def classify_needs_proof(self) -> bool:
        return self.mutant != "classify-unguarded"

    def d7_policy_independent(self) -> bool:
        return self.mutant != "warn-clean-zero"

    def failed_contributes(self) -> bool:
        return self.mutant != "failed-ops-dropped"


# A state: (member states tuple, new_proven, policy)
State = tuple[tuple[str, ...], bool, str]


def completed(states: tuple[str, ...]) -> list[int]:
    return [i for i, s in enumerate(states) if s in ("compared_ok", "compared_break")]


def exit_code(spec: Spec, states: tuple[str, ...], policy: str) -> int:
    """The spec's Exit formula (ScopeCompleteness.tla, 'Exit')."""
    no_completed = not completed(states)
    incomplete = no_completed or any(
        s in ("expected_not_produced", "failed", "not_supplied") for s in states
    )
    compat = 4 if "compared_break" in states else 0
    removal = 8 if "retired" in states else 0
    ops = 4 if (spec.failed_contributes() and "failed" in states) else 0
    d6 = 1 if (policy == "block" and incomplete) else 0
    if spec.d7_policy_independent():
        d7 = 1 if no_completed else 0
    else:
        d7 = 1 if (no_completed and policy == "block") else 0
    return max(compat, removal, ops, d6, d7)


def successors(spec: Spec, st: State):
    states, proven, policy = st
    # Acquire: one absent member takes any acquisition state.
    for i, s in enumerate(states):
        if s == ABSENT:
            for new in ACQUIRE_STATES:
                yield (states[:i] + (new,) + states[i + 1 :], proven, policy)
    # Classify: not_supplied -> retired, guarded by NewProven (the faithful
    # spec); the mutant drops the guard.
    if (proven or not spec.classify_needs_proof()) and "not_supplied" in states:
        yield (
            tuple("retired" if s == "not_supplied" else s for s in states),
            proven,
            policy,
        )


def invariant_failures(states: tuple[str, ...], proven: bool, exit_: int) -> list[str]:
    """I1-I3 as predicates over one run: its member states, whether NEW's
    inventory is proven, and its exit code. Shared by the BFS (which feeds
    the spec's own exit) and the conformance harness (which feeds the real
    exit), so both check the same definitions."""
    if ABSENT in states:
        return []
    out: list[str] = []
    # I1: zero completed comparisons never yields a clean pass.
    if not completed(states) and exit_ == 0:
        out.append("I1 NoCleanZeroComparison")
    # I2: a removal (some member retired, Removal = 8) needs NEW's inventory
    # proven. Absence alone is never a removal.
    if "retired" in states and not proven:
        out.append("I2 RemovalNeedsProof")
    # I3: a failed member never reads as clean.
    if "failed" in states and exit_ < 1:
        out.append("I3 FailedNeverClean")
    return out


def invariant_violations(spec: Spec, st: State) -> list[str]:
    states, proven, policy = st
    return invariant_failures(states, proven, exit_code(spec, states, policy))


def check(n_members: int, spec: Spec = Spec()):
    """BFS from every Init. Returns (states_explored, first_violation|None)."""
    init: list[State] = [
        ((ABSENT,) * n_members, proven, policy)
        for proven in (False, True)
        for policy in POLICIES
    ]
    parent: dict[State, State | None] = {s: None for s in init}
    queue = deque(init)
    while queue:
        st = queue.popleft()
        bad = invariant_violations(spec, st)
        if bad:
            trace: list[State] = []
            cur: State | None = st
            while cur is not None:
                trace.append(cur)
                cur = parent[cur]
            return len(parent), (bad, list(reversed(trace)))
        for nxt in successors(spec, st):
            if nxt not in parent:
                parent[nxt] = st
                queue.append(nxt)
    return len(parent), None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--members", type=int, default=3)
    ap.add_argument("--mutant", choices=MUTANTS, default=None)
    a = ap.parse_args()
    n, found = check(a.members, Spec(a.mutant))
    print(f"states={n} members={a.members} mutant={a.mutant or 'none'}")
    if found is None:
        print("I1, I2, I3: hold on every reachable state")
        return
    bad, trace = found
    print(f"VIOLATED: {', '.join(bad)}; shortest trace (members, NewProven, policy):")
    for states, proven, policy in trace:
        print(f"   {list(states)} newProven={proven} policy={policy}")


if __name__ == "__main__":
    main()
