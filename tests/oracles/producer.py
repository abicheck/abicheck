"""Invariant B: producer equivalence.

Where castxml and clang both produce a fact family, the normalized kinds must
agree. A difference is legal only when it is named in an explicit allowlist.
The allowlist may only shrink: an entry added after its baseline is a
violation even if it explains a real difference.

The pairwise check reuses :func:`tests.catalog_kind_parity.frontend_parity`,
the same relation the nightly cross-cell gate applies, so there is one
definition of "undeclared" and "stale".
"""

from __future__ import annotations

from collections.abc import Iterable

from tests.catalog_kind_parity import frontend_parity


def producer_violations(
    castxml: Iterable[str],
    clang: Iterable[str],
    allowlist: Iterable[tuple[str, str]] = (),
) -> list[str]:
    """Kinds only one producer reports that the allowlist does not name,
    plus allowlist entries that no longer match an observed difference."""
    result = frontend_parity(castxml, clang, declared=allowlist)
    return [
        *(f"undeclared: {k}" for k in result.undeclared),
        *(f"stale: {k}" for k in result.stale),
    ]


def allowlist_growth(
    allowlist: Iterable[tuple[str, str]],
    baseline: Iterable[tuple[str, str]],
) -> list[str]:
    """Allowlist entries that are not in the recorded baseline."""
    grown = set(allowlist) - set(baseline)
    return [f"allowlist grew: {kind} (only {side})" for kind, side in sorted(grown)]
