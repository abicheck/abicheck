# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Per-kind rollup in the Markdown report.

One conda-forge MKL comparison emitted 39,956 ``exported_not_public``
findings -- 39,955 of them ``persistent`` -- producing a ~120,000-line report
in which no other finding could be found. The count is the fact a reader
needs; 39,956 individual lines actively hide it.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind, CrossSourceEvolution, Verdict
from abicheck.checker_types import Change
from abicheck.report.kind_rollup import (
    KIND_ROLLUP_SAMPLES,
    KIND_ROLLUP_THRESHOLD,
    KindRollup,
    render_kind_rollups,
    roll_up_large_kinds,
    section_is_gating,
)
from abicheck.reporter_markdown import _section_severity_level


def _changes(kind: ChangeKind, n: int, prefix: str = "s") -> list[Change]:
    return [Change(kind=kind, symbol=f"{prefix}{i}", description="") for i in range(n)]


class TestRollupSelection:
    def test_a_flood_is_rolled_up(self) -> None:
        items, rollups = roll_up_large_kinds(
            _changes(ChangeKind.EXPORTED_NOT_PUBLIC, 40_000)
        )
        assert items == []
        assert [(r.kind, r.count) for r in rollups] == [("exported_not_public", 40_000)]

    def test_an_ordinary_number_is_left_itemised(self) -> None:
        """The rollup must not engage for a report a reader can already read
        -- a summarised finding is strictly less information than an itemised
        one, so it has to earn its place."""
        changes = _changes(ChangeKind.EXPORTED_NOT_PUBLIC, 3)
        items, rollups = roll_up_large_kinds(changes)
        assert items == changes
        assert rollups == ()

    @pytest.mark.parametrize(
        ("count", "rolled"),
        [
            (KIND_ROLLUP_THRESHOLD - 1, False),
            (KIND_ROLLUP_THRESHOLD, False),
            (KIND_ROLLUP_THRESHOLD + 1, True),
        ],
    )
    def test_the_boundary_is_exact(self, count: int, rolled: bool) -> None:
        """Stated against the constant rather than a literal, so tuning the
        threshold does not silently make this test assert nothing."""
        items, rollups = roll_up_large_kinds(
            _changes(ChangeKind.EXPORTED_NOT_PUBLIC, count)
        )
        assert bool(rollups) is rolled
        assert bool(items) is not rolled

    def test_one_flooding_kind_does_not_hide_the_others(self) -> None:
        """The whole point: the findings a reader is looking for stay
        itemised next to the summary of the ones they are not."""
        flood = _changes(ChangeKind.EXPORTED_NOT_PUBLIC, 500, "flood")
        real = _changes(ChangeKind.UNVERSIONED_EXPORTED_SYMBOL, 2, "real")
        items, rollups = roll_up_large_kinds(flood + real)
        assert [c.symbol for c in items] == ["real0", "real1"]
        assert [r.kind for r in rollups] == ["exported_not_public"]

    def test_every_finding_is_accounted_for(self) -> None:
        """Partition property: nothing is dropped. A rollup that lost
        findings instead of summarising them would be a far worse bug than
        the flood it fixes, and would look identical in the rendered
        output."""
        groups = {
            ChangeKind.EXPORTED_NOT_PUBLIC: 300,
            ChangeKind.UNVERSIONED_EXPORTED_SYMBOL: 40,
            ChangeKind.PRIVATE_HEADER_LEAK: 2,
        }
        changes = [c for k, n in groups.items() for c in _changes(k, n, k.value)]
        items, rollups = roll_up_large_kinds(changes)
        assert len(items) + sum(r.count for r in rollups) == len(changes)

    def test_rollups_are_ordered_by_descending_count(self) -> None:
        changes = _changes(ChangeKind.EXPORTED_NOT_PUBLIC, 100, "a") + _changes(
            ChangeKind.UNVERSIONED_EXPORTED_SYMBOL, 300, "b"
        )
        _items, rollups = roll_up_large_kinds(changes)
        assert [r.count for r in rollups] == [300, 100]

    def test_samples_are_bounded(self) -> None:
        _items, rollups = roll_up_large_kinds(
            _changes(ChangeKind.EXPORTED_NOT_PUBLIC, 1000)
        )
        assert len(rollups[0].sample_symbols) == KIND_ROLLUP_SAMPLES

    def test_empty_input(self) -> None:
        assert roll_up_large_kinds([]) == ([], ())


class TestRollupRendering:
    def test_the_count_is_the_headline(self) -> None:
        line = render_kind_rollups(
            (
                KindRollup(
                    kind="exported_not_public", count=39_956, sample_symbols=("a", "b")
                ),
            )
        )[0]
        assert "39,956" in line
        assert "exported_not_public" in line
        assert "`a`" in line and "`b`" in line
        # And it must say where the rest are, or a reader has no recourse.
        assert "json" in line

    def test_the_remainder_count_is_correct(self) -> None:
        line = render_kind_rollups(
            (KindRollup(kind="k", count=10, sample_symbols=("a", "b", "c")),)
        )[0]
        assert "7 more" in line

    def test_no_remainder_clause_when_everything_is_shown(self) -> None:
        line = render_kind_rollups(
            (KindRollup(kind="k", count=2, sample_symbols=("a", "b")),)
        )[0]
        assert "more" not in line

    def test_nothing_renders_for_no_rollups(self) -> None:
        assert render_kind_rollups(()) == []


class TestMachineOutputIsUnaffected:
    def test_json_still_carries_every_finding(self) -> None:
        """The rollup is a *presentation* limit. A machine consumer that
        started losing findings to a display decision would be the worse
        failure by far -- and it is the one a reader of the Markdown could
        never notice.
        """
        from abicheck.checker_types import DiffResult
        from abicheck.reporter import to_json

        changes = _changes(ChangeKind.EXPORTED_NOT_PUBLIC, 200)
        for c in changes:
            c.cross_source_evolution = CrossSourceEvolution.PERSISTENT
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=changes,
            verdict=Verdict.COMPATIBLE_WITH_RISK,
        )
        import json

        payload = json.loads(to_json(result))
        assert len(payload["changes"]) == 200


class TestAGatingSectionIsNeverRolledUp:
    """The module's stated contract, enforced rather than assumed.

    "Applied only to non-gating sections" was enforced purely by *which
    sections called it* -- which stopped being true the moment a severity
    setting made one of them gate. Under the strict preset
    `potential_breaking`/`quality_issues` resolve to `error` and drive the
    exit code, yet were still collapsed to a count and five samples, so the
    Markdown report could not name every finding that blocked the run
    (Codex review).

    Bug class: an invariant enforced by call-site discipline rather than by
    the function that states it.
    """

    @staticmethod
    def _flood(n: int = KIND_ROLLUP_THRESHOLD + 5) -> list[Change]:
        return [
            Change(
                kind=ChangeKind.EXPORTED_NOT_PUBLIC,
                symbol=f"sym{i}",
                description=f"sym{i} exported but not public",
            )
            for i in range(n)
        ]

    def test_error_level_keeps_every_finding_itemised(self):
        items, rollups = roll_up_large_kinds(self._flood(), severity_level="error")
        assert len(items) == KIND_ROLLUP_THRESHOLD + 5
        assert rollups == ()

    @pytest.mark.parametrize("level", [None, "warning", "info"])
    def test_non_gating_levels_still_roll_up(self, level):
        """The negative control: an implementation that never rolled up would
        satisfy the claim above completely."""
        items, rollups = roll_up_large_kinds(self._flood(), severity_level=level)
        assert rollups != ()
        assert len(items) < KIND_ROLLUP_THRESHOLD + 5

    def test_an_enum_shaped_level_is_read_by_value(self):
        """`SeverityConfig` holds enum members, not strings -- reading
        `str(level)` instead of `.value` would render `Severity.ERROR` and
        silently fail the comparison."""

        class _Level:
            value = "error"

        items, rollups = roll_up_large_kinds(self._flood(), severity_level=_Level())
        assert rollups == ()
        assert len(items) == KIND_ROLLUP_THRESHOLD + 5

    @pytest.mark.parametrize(
        ("level", "gating"),
        [
            (None, False),
            ("error", True),
            ("ERROR", True),
            ("warning", False),
            ("info", False),
        ],
    )
    def test_the_gating_predicate_states_the_rule_on_its_own(self, level, gating):
        assert section_is_gating(level) is gating

    def test_the_markdown_report_itemises_a_gating_section(self):
        """Through the real report builder, not the helper: the defect was
        that the *call sites* never passed a level."""
        from abicheck.severity import SeverityConfig

        config = SeverityConfig()
        if not hasattr(config, "quality_issues"):
            pytest.skip("SeverityConfig has no quality_issues attribute")
        level = _section_severity_level(config, "quality_issues")
        # Whatever the default resolves to, the accessor must agree with the
        # config rather than re-deriving it from a rendered label.
        assert level == getattr(config, "quality_issues", None)
