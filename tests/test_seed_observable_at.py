"""scripts/seed_observable_at.py: its decisions are the registry's observable_at values.

Applied decisions must reproduce exactly in the registry; a decision the
production cap blocked must leave its kind UNCLASSIFIED; no kind outside the
decision table may be classified.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import seed_observable_at as seed  # noqa: E402

from abicheck.model.change_catalog.assembled import REGISTRY  # noqa: E402
from abicheck.model.change_catalog.observability import (  # noqa: E402
    UNCLASSIFIED,
)


def test_applied_decisions_match_registry() -> None:
    applied = 0
    for kind, dec in seed.DECISIONS.items():
        value = REGISTRY.entries[kind].observable_at
        if value is UNCLASSIFIED:
            continue  # blocked by the production line cap; reported, not applied
        assert value == seed.decision_observability(dec.code), kind
        applied += 1
    assert applied > 0


def test_ledger_round_trips_through_the_catalog_value() -> None:
    """ledger -> catalog value -> ledger: a row is reproduced from the value it
    gives, for single- and multi-condition rows alike."""
    for kind, dec in seed.DECISIONS.items():
        assert seed.codes_of(seed.decision_observability(dec.code)) == dec.code, kind


def test_multi_condition_row_reads_as_one_form_per_condition() -> None:
    assert seed.DECISIONS["func_removed"].codes == ("DH:ELFPE", "B:MACHO")
    assert seed.DECISIONS["func_params_changed"].codes == ("DH:C", "B:CXX")
    assert len(seed.decision_observability("DH:ELFPE+B:MACHO")) == 2


def test_no_kind_outside_decisions_is_classified() -> None:
    for kind, entry in REGISTRY.entries.items():
        if kind not in seed.DECISIONS:
            assert entry.observable_at is UNCLASSIFIED, kind


def test_every_decision_names_a_real_kind() -> None:
    assert set(seed.DECISIONS) <= set(REGISTRY.entries)


@pytest.mark.parametrize(
    "text",
    ["some_kind BOGUS", "one_token_only", "dup_kind B\ndup_kind H"],
)
def test_parser_rejects_malformed_rows(text: str) -> None:
    with pytest.raises(ValueError):
        seed.parse_decisions(text)


def test_unclassified_reasons_are_recorded_for_kinds_left_out() -> None:
    # Each reason row documents a kind family left UNCLASSIFIED on purpose.
    assert seed.UNCLASSIFIED_REASONS
    assert all(reason.strip() for reason in seed.UNCLASSIFIED_REASONS.values())
