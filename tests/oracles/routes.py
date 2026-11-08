"""Invariant C: route equivalence.

Equivalent inputs reached through different routes (live libraries, stored
snapshots, a mixed pair, a reversed or relocated pair, the typed API) must
decide identically: the same exit code, verdict and set of change kinds. The
oracle compares every route against the first one named.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteOutcome:
    """The decision a route reached for one comparison."""

    exit_code: int
    verdict: str
    kinds: frozenset[str]


def route_violations(routes: Mapping[str, RouteOutcome]) -> list[str]:
    """Fields on which a route differs from the first route named."""
    if len(routes) < 2:
        return []
    (reference_name, reference), *others = routes.items()
    problems: list[str] = []
    for name, outcome in others:
        if outcome.exit_code != reference.exit_code:
            problems.append(
                f"{name}: exit {outcome.exit_code} != {reference.exit_code} ({reference_name})"
            )
        if outcome.verdict != reference.verdict:
            problems.append(
                f"{name}: verdict {outcome.verdict} != {reference.verdict} ({reference_name})"
            )
        if outcome.kinds != reference.kinds:
            only = sorted(outcome.kinds - reference.kinds)
            missing = sorted(reference.kinds - outcome.kinds)
            problems.append(
                f"{name}: kinds differ from {reference_name} "
                f"(extra {only}, missing {missing})"
            )
    return problems
