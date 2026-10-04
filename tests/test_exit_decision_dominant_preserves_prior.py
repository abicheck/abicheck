"""A dominant ADR-064 axis must not erase the ordinary decision under it.

Bug class: ``_dominant_decision(prior=...)`` copied the prior decision's
contributions from a hand-written field list, so every axis added after the
list was written (the ADR-067 D6 additions-review axis first) was silently
reset to ``0`` whenever a budget overflow or other dominant axis fired on
top of it -- the report then claimed the axis never contributed.

Invariant, over every contribution field of ``ExitDecision`` (read off the
dataclass, so a future axis is covered without editing this file) and every
dominant reason: the field's prior value survives, except the dominant
field itself, which carries the dominant code.
"""

from __future__ import annotations

import dataclasses

import pytest

from abicheck.policy.exit_decision import ExitDecision, ExitReason
from abicheck.policy.exit_decision_precedence import (
    _DOMINANT_FIELD,
    _dominant_decision,
)

_CONTRIBUTIONS = [
    f.name for f in dataclasses.fields(ExitDecision) if f.name.endswith("_contribution")
]


def _prior_with(field: str) -> ExitDecision:
    values = dict.fromkeys(_CONTRIBUTIONS, 0)
    values[field] = 1
    return ExitDecision(code=1, reasons=(ExitReason.CLEAN,), **values)


@pytest.mark.parametrize("reason", list(_DOMINANT_FIELD))
@pytest.mark.parametrize("field", _CONTRIBUTIONS)
def test_a_prior_contribution_survives_a_dominant_axis(reason, field) -> None:
    dominant_field = _DOMINANT_FIELD[reason]
    decision = _dominant_decision(64, reason, prior=_prior_with(field))
    assert decision.code == 64
    assert decision.reasons == (reason,)
    assert getattr(decision, dominant_field) == 64
    if field != dominant_field:
        assert getattr(decision, field) == 1


def test_the_field_list_is_not_empty() -> None:
    assert "additions_review_contribution" in _CONTRIBUTIONS
    assert len(_CONTRIBUTIONS) >= 10
