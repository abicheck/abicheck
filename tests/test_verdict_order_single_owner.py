"""Every verdict ranking agrees with one order.

Before, ``policy.outcome.worst_real_verdict`` ranked by ``Verdict``'s
declaration order and ``workflows.aggregate`` kept its own rank and
exit-code tables, so reordering the enum or editing one table would change
one fold and not the others. Oracle: the order spelled out below.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.change_registry_types import Verdict
from abicheck.policy.outcome import worst_real_verdict
from abicheck.workflows.aggregate import contracts, report_gate

ORDER = ["NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK", "API_BREAK", "BREAKING"]
LEGACY_EXIT = {
    "NO_CHANGE": 0,
    "COMPATIBLE": 0,
    "COMPATIBLE_WITH_RISK": 0,
    "API_BREAK": 2,
    "BREAKING": 4,
}


def test_aggregate_rank_table_follows_the_order() -> None:
    ranked = sorted(Verdict, key=lambda v: contracts._VERDICT_RANK[v])
    assert [v.value for v in ranked] == ORDER


def test_aggregate_legacy_exit_table_matches() -> None:
    assert {v.value: c for v, c in report_gate._LEGACY_SEVERITY.items()} == LEGACY_EXIT


@pytest.mark.parametrize("size", [1, 2, 3])
def test_worst_real_verdict_picks_the_latest_in_the_order(size: int) -> None:
    pool = ORDER + ["ERROR", "not_comparable", None]
    for combo in itertools.product(pool, repeat=size):
        real = [c for c in combo if c in ORDER]
        expected = max(real, key=ORDER.index) if real else None
        got = worst_real_verdict(combo)
        assert (got.value if got else None) == expected, combo
