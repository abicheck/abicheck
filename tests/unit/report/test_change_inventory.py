# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The change-versus-inventory split and the headline that reads it.

The bug class (``tests/regressions/manifest.py``): a *population* a
comparison never observed -- standing cross-source hygiene inventory
present identically on both sides -- being counted, and headlined, as
something the comparison did observe. The invariant below is stated over
the whole ``CrossSourceEvolution`` domain and every ``Verdict``, not over
the one ``32 persistent exported_not_public`` case that reported it.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker_policy import ChangeKind, CrossSourceEvolution, Verdict
from abicheck.checker_types import Change
from abicheck.report.change_inventory import (
    compute_change_inventory,
    render_change_inventory_json,
)
from abicheck.report.document import ReportDocument
from abicheck.report.render_text import (
    format_hygiene_note,
    format_stat_line,
    render_stat_document,
)

_VERDICTS = (
    Verdict.BREAKING,
    Verdict.API_BREAK,
    Verdict.COMPATIBLE_WITH_RISK,
    Verdict.COMPATIBLE,
)
_STATES = (None, *CrossSourceEvolution)


def _change(state: CrossSourceEvolution | None) -> Change:
    c = Change(ChangeKind.EXPORTED_NOT_PUBLIC, "sym", "sym")
    c.cross_source_evolution = state
    return c


@pytest.mark.parametrize(
    ("state", "verdict"), list(itertools.product(_STATES, _VERDICTS))
)
def test_a_stamped_finding_is_never_a_compatibility_change(
    state: CrossSourceEvolution | None, verdict: Verdict
) -> None:
    """Exhaustive over the (evolution state x verdict) domain.

    A finding counts toward ``compatibility_*`` if and only if it carries
    no evolution stamp -- independently of which verdict it resolves to,
    which is the half the reported defect got wrong (a ``PERSISTENT``
    finding resolving to ``COMPATIBLE_WITH_RISK`` was headlined as risk).
    """
    change = _change(state)
    split = compute_change_inventory([change], [change], lambda _c: verdict)
    stamped = state is not None
    assert split.compatibility_changes == (0 if stamped else 1)
    assert split.hygiene_total == (1 if stamped else 0)
    assert split.has_hygiene is stamped
    scored = (
        split.compatibility_breaking
        + split.compatibility_source_breaks
        + split.compatibility_risk
        + split.compatibility_compatible
    )
    assert scored == (0 if stamped else 1)


def test_the_populations_partition_every_finding() -> None:
    """``compatibility_changes`` + the four hygiene states == the total.

    Generated over every multiset of states up to length three rather than
    asserted on one hand-built list, so a state added to
    ``CrossSourceEvolution`` without a counter here fails immediately.
    """
    for length in (1, 2, 3):
        for combo in itertools.product(_STATES, repeat=length):
            changes = [_change(state) for state in combo]
            split = compute_change_inventory(
                changes, changes, lambda _c: Verdict.COMPATIBLE_WITH_RISK
            )
            assert split.compatibility_changes + split.hygiene_total == len(changes)
            rendered = render_change_inventory_json(split)
            assert (
                sum(rendered[key] for key in rendered if key.startswith("hygiene_"))
                == split.hygiene_total
            )


def test_an_unevaluated_compatibility_change_is_counted_but_not_scored() -> None:
    """ADR-049 D1: the verdict counters run over the evaluated subset only."""
    change = _change(None)
    split = compute_change_inventory([change], [], lambda _c: Verdict.BREAKING)
    assert split.compatibility_changes == 1
    assert split.compatibility_breaking == 0


def test_headline_states_persistent_inventory_separately() -> None:
    split = compute_change_inventory(
        [_change(CrossSourceEvolution.PERSISTENT) for _ in range(32)],
        [],
        lambda _c: Verdict.COMPATIBLE_WITH_RISK,
    )
    line = format_stat_line(
        "NO_CHANGE",
        breaking=0,
        source_breaks=0,
        risk_count=32,
        compatible_additions=0,
        total_changes=32,
        change_inventory=render_change_inventory_json(split),
    )
    assert (
        line == "NO_CHANGE: no compatibility changes (0 total); hygiene: 32 persistent"
    )
    assert "32 risk" not in line


def test_headline_is_byte_identical_without_hygiene_findings() -> None:
    """Every pre-existing report's one-line summary is unchanged."""
    split = compute_change_inventory(
        [_change(None)], [_change(None)], lambda _c: Verdict.BREAKING
    )
    kwargs = dict(
        breaking=2,
        source_breaks=1,
        risk_count=3,
        compatible_additions=4,
        total_changes=10,
    )
    assert format_stat_line("BREAKING", **kwargs) == format_stat_line(
        "BREAKING", **kwargs, change_inventory=render_change_inventory_json(split)
    )


def test_hygiene_note_orders_what_changed_before_what_did_not() -> None:
    note = format_hygiene_note(
        {
            "hygiene_introduced": 1,
            "hygiene_resolved": 2,
            "hygiene_persistent": 3,
            "hygiene_not_evaluated": 4,
        }
    )
    assert note == "; hygiene: 1 introduced, 2 resolved, 3 persistent, 4 not evaluated"
    assert format_hygiene_note(None) == ""
    assert format_hygiene_note({"hygiene_persistent": 0}) == ""


_BASE_SUMMARY = {
    "breaking": 0,
    "source_breaks": 0,
    "risk_changes": 32,
    "compatible_additions": 0,
    "total_changes": 32,
}


def _stat_document(summary: dict[str, object]) -> ReportDocument:
    return ReportDocument.from_mapping(
        {"verdict_label": "NO_CHANGE", "summary": summary}
    )


class TestStatDocumentProjection:
    """``render_stat_document`` reads the block, and tolerates its absence.

    The projection is generic over any ``ReportDocument``, and
    ``format_stat_line``'s ``change_inventory`` is optional -- so a document
    built by a caller that carries no ``summary.change_inventory`` (every
    pre-5.3 one) must render exactly the line it always did. Asserting that
    through the projection, rather than through ``format_stat_line`` alone,
    is what makes it a statement about the report rather than about the
    formatter.
    """

    def test_a_document_carrying_the_block_states_the_inventory(self) -> None:
        split = compute_change_inventory(
            [_change(CrossSourceEvolution.PERSISTENT) for _ in range(32)],
            [],
            lambda _c: Verdict.COMPATIBLE_WITH_RISK,
        )
        summary = {
            **_BASE_SUMMARY,
            "change_inventory": render_change_inventory_json(split),
        }
        assert render_stat_document(_stat_document(summary)) == (
            "NO_CHANGE: no compatibility changes (0 total); hygiene: 32 persistent"
        )

    @pytest.mark.parametrize("absent", [None, "not-an-object", 7, []])
    def test_a_document_without_the_block_renders_the_pre_5_3_line(
        self, absent: object
    ) -> None:
        """Absent, null, and a non-object value all fall back identically.

        Parametrized over the shapes a hand-built or older document can
        actually carry, because the guard is an ``isinstance`` check: a
        single ``None`` case would leave the other three asserting nothing.
        """
        expected = render_stat_document(_stat_document(dict(_BASE_SUMMARY)))
        assert expected == "NO_CHANGE: 32 risk (32 total)"
        summary = {**_BASE_SUMMARY, "change_inventory": absent}
        assert render_stat_document(_stat_document(summary)) == expected
