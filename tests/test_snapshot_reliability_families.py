"""`model.snapshot_reliability`: the one accessor over
`AbiSnapshot.stale_fact_families` (ADR-063 Phase 0/10)."""

from __future__ import annotations

import pytest

from abicheck.model import AbiSnapshot
from abicheck.model.snapshot_reliability import (
    FACT_FAMILIES,
    RELIABILITY_FLAG_NAMES,
    family_reliable,
    flag_name,
    raw_unreliable_facts,
)


@pytest.mark.parametrize("family", FACT_FAMILIES)
def test_each_family_is_reliable_until_recorded_stale(family: str) -> None:
    fresh = AbiSnapshot(library="l", version="1")
    stale = AbiSnapshot(
        library="l", version="1", stale_fact_families=frozenset({family})
    )
    assert family_reliable(fresh, family)
    assert not family_reliable(stale, family)
    assert raw_unreliable_facts(stale) == [flag_name(family)]
    others = [f for f in FACT_FAMILIES if f != family]
    assert all(family_reliable(stale, f) for f in others)


def test_an_unknown_family_is_an_error_not_a_silent_true() -> None:
    with pytest.raises(ValueError, match="unknown fact family"):
        family_reliable(AbiSnapshot(library="l", version="1"), "clang_vtables")


def test_flag_names_follow_family_order() -> None:
    assert RELIABILITY_FLAG_NAMES == tuple(f"{f}_facts_reliable" for f in FACT_FAMILIES)
