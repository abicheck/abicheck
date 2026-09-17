# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Change-versus-inventory reporting at release cardinality.

The bug class this closes, and why it escaped: the scalar ``compare``
report gained ``summary.change_inventory`` from a *shared* computation
(``report_summary.build_summary``), and the release fan-out already called
that identical function -- then hand-projected two of its fields into each
member entry and dropped the inventory. Every test of the scalar module
passed, because none of them reaches that projection boundary. So the
first test here is the invariant that would have failed immediately:

    a one-member release must state what a scalar report over the
    identical pair states.

It is asserted over the *whole* inventory block by field-wise comparison,
not over the one counter this fix happened to notice, so the next counter
added to ``ChangeInventorySplit`` is covered without editing this test --
and a member block that silently stops carrying a counter fails here
rather than in a reader's dashboard.

Registry: see ``tests/regressions/manifest.py`` for this bug class
(``report.scalar_release_projection_drift``).
"""

from __future__ import annotations

import json

import pytest

from abicheck.checker_policy import ChangeKind, CrossSourceEvolution, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.report.change_inventory import (
    ChangeInventorySplit,
    render_change_inventory_json,
)
from abicheck.report.release_change_inventory import (
    RELEASE_INVENTORY_COUNTERS,
    RELEASE_OPERATIONAL_SENTINELS,
    fold_release_change_inventory,
    release_change_inventory,
    release_inventory_counters,
)
from abicheck.report.release_member_summary import add_member_review_summary
from abicheck.report_summary import build_summary

#: Read off the owner rather than restated here, so a sentinel added to the
#: release vocabulary is covered by the parametrized test below without
#: editing this file -- the same "enumerate from the registry" discipline
#: the cross-source hygiene tests already use.
_SENTINELS = RELEASE_OPERATIONAL_SENTINELS


def test_the_sentinel_vocabulary_is_what_the_release_actually_uses() -> None:
    """The CLI helper's historical private name must still be this set.

    Two definitions of "this member completed no comparison" -- one in the
    report layer and one in the front end -- is how the fold and the
    verdict rollup would come to disagree about which members count.
    """
    from abicheck.cli_compare_release_helpers import _RELEASE_OPERATIONAL_SENTINELS

    assert _RELEASE_OPERATIONAL_SENTINELS is RELEASE_OPERATIONAL_SENTINELS
    assert {"ERROR", "not_comparable", "unsupported", "failed"} <= set(_SENTINELS)


def _hygiene(kind: ChangeKind, state: CrossSourceEvolution, symbol: str) -> Change:
    return Change(
        kind=kind,
        symbol=symbol,
        description="",
        cross_source_evolution=state,
    )


def _plain(kind: ChangeKind, symbol: str) -> Change:
    return Change(kind=kind, symbol=symbol, description="")


def _result(*changes: Change) -> DiffResult:
    return DiffResult(
        library="libx.so",
        old_version="1.0",
        new_version="1.1",
        verdict=Verdict.COMPATIBLE_WITH_RISK,
        changes=list(changes),
    )


#: One pair carrying every population the split distinguishes at once, so a
#: fix that propagated only the counter it noticed cannot pass.
def _mixed_result() -> DiffResult:
    return _result(
        _plain(ChangeKind.FUNC_REMOVED, "gone"),
        _plain(ChangeKind.FUNC_ADDED, "fresh"),
        _hygiene(
            ChangeKind.EXPORTED_NOT_PUBLIC, CrossSourceEvolution.PERSISTENT, "old_a"
        ),
        _hygiene(
            ChangeKind.EXPORTED_NOT_PUBLIC, CrossSourceEvolution.PERSISTENT, "old_b"
        ),
        _hygiene(
            ChangeKind.EXPORTED_NOT_PUBLIC, CrossSourceEvolution.INTRODUCED, "new_a"
        ),
        _hygiene(
            ChangeKind.EXPORTED_NOT_PUBLIC, CrossSourceEvolution.RESOLVED, "fixed_a"
        ),
        _hygiene(
            ChangeKind.EXPORTED_NOT_PUBLIC,
            CrossSourceEvolution.NOT_EVALUATED,
            "unknown_a",
        ),
    )


class TestScalarAndMemberAgree:
    """The projection-boundary invariant the omission escaped through."""

    def test_member_block_equals_the_scalar_report_block(self) -> None:
        result = _mixed_result()
        entry: dict[str, object] = {"library": "libx.so", "verdict": "NO_CHANGE"}
        add_member_review_summary(entry, result, None)
        assert entry["change_inventory"] == render_change_inventory_json(
            build_summary(result).inventory
        )

    def test_member_block_is_field_wise_complete(self) -> None:
        """Every counter the scalar block has, the member block has.

        Asserted over the scalar block's own key set rather than a list
        written here, so a counter added to ``ChangeInventorySplit`` is
        covered without editing this file -- the omission this closes was
        precisely a *missing* thing that failed nothing anywhere.
        """
        result = _mixed_result()
        scalar = render_change_inventory_json(build_summary(result).inventory)
        entry: dict[str, object] = {"library": "libx.so", "verdict": "NO_CHANGE"}
        add_member_review_summary(entry, result, None)
        member = entry["change_inventory"]
        assert isinstance(member, dict)
        assert set(member) == set(scalar)
        for key, value in scalar.items():
            assert member[key] == value, key

    def test_the_fixture_actually_populates_every_population(self) -> None:
        """Vacuity guard on the two tests above.

        A fixture whose findings were all one population would let a
        partial propagation pass both of them.
        """
        block = render_change_inventory_json(build_summary(_mixed_result()).inventory)
        for key in (
            "compatibility_changes",
            "hygiene_introduced",
            "hygiene_resolved",
            "hygiene_persistent",
            "hygiene_not_evaluated",
        ):
            assert block[key] > 0, key

    def test_member_block_reflects_the_finalized_findings(self) -> None:
        """Release postprocessing can still change findings, so the block is
        stamped from whatever ``result`` holds when it runs -- not cached
        from an earlier pass.
        """
        result = _mixed_result()
        entry: dict[str, object] = {"library": "libx.so", "verdict": "NO_CHANGE"}
        add_member_review_summary(entry, result, None)
        before = dict(entry["change_inventory"])  # type: ignore[arg-type]
        result.changes.append(
            _hygiene(
                ChangeKind.EXPORTED_NOT_PUBLIC,
                CrossSourceEvolution.PERSISTENT,
                "old_c",
            )
        )
        add_member_review_summary(entry, result, None)
        after = entry["change_inventory"]
        assert isinstance(after, dict)
        assert after["hygiene_persistent"] == before["hygiene_persistent"] + 1
        assert after == render_change_inventory_json(build_summary(result).inventory)


class TestReleaseFold:
    """The aggregate's four honesty rules."""

    #: Exactly the keys `render_change_inventory_json` emits, read off a real
    #: split rather than restated -- the release counter tuple is NOT the
    #: right source here. An earlier version of this fixture built the block
    #: from `RELEASE_INVENTORY_COUNTERS`, which includes `hygiene_total`;
    #: production member blocks never carry that key (it is a *property* of
    #: `ChangeInventorySplit`, not a rendered field), so the fixture invented
    #: it and hid a real bug: the fold summed `hygiene_total` from members
    #: and therefore always reported 0 beside a nonzero `hygiene_persistent`.
    #: A fixture unfaithful to the real emitter is the exact failure mode
    #: `tests/CLAUDE.md` warns about.
    _MEMBER_KEYS = tuple(
        render_change_inventory_json(ChangeInventorySplit(0, 0, 0, 0, 0, 0, 0, 0, 0))
    )

    @classmethod
    def _member(cls, verdict: str, **counters: int) -> dict[str, object]:
        unknown = set(counters) - set(cls._MEMBER_KEYS)
        assert not unknown, f"production member blocks carry no {sorted(unknown)}"
        block = dict.fromkeys(cls._MEMBER_KEYS, 0)
        block.update(counters)
        return {"library": "l.so", "verdict": verdict, "change_inventory": block}

    def test_counters_are_the_plain_sum_of_the_members(self) -> None:
        folded = fold_release_change_inventory(
            [
                self._member("NO_CHANGE", hygiene_persistent=32),
                self._member(
                    "BREAKING",
                    compatibility_changes=3,
                    compatibility_breaking=2,
                    compatibility_compatible=1,
                ),
            ],
            operational_sentinels=_SENTINELS,
        )
        assert folded is not None
        assert folded["hygiene_persistent"] == 32
        # Derived by the fold from the four state counters, not summed from
        # the members (which never carry it) -- asserted against the folded
        # value itself, so a fold that dropped the derivation fails here.
        assert folded["hygiene_total"] == 32
        assert folded["compatibility_changes"] == 3
        assert folded["compatibility_breaking"] == 2
        assert folded["members_contributing"] == 2

    def test_conservation_holds_across_the_fold(self) -> None:
        """``compatibility_changes`` + the four ``hygiene_*`` counters equals
        the members' combined ``total_changes`` -- the same conservation rule
        the single-pair split documents, which a fold that double-counted or
        dropped a population would break.
        """
        members = []
        expected_total = 0
        for i in range(4):
            result = _mixed_result()
            for _ in range(i):
                result.changes.append(_plain(ChangeKind.FUNC_ADDED, f"extra{i}"))
            entry: dict[str, object] = {"library": f"l{i}.so", "verdict": "NO_CHANGE"}
            add_member_review_summary(entry, result, None)
            members.append(entry)
            expected_total += len(result.changes)
        folded = fold_release_change_inventory(
            members, operational_sentinels=_SENTINELS
        )
        assert folded is not None
        assert (
            folded["compatibility_changes"]
            + folded["hygiene_introduced"]
            + folded["hygiene_resolved"]
            + folded["hygiene_persistent"]
            + folded["hygiene_not_evaluated"]
            == expected_total
        )

    @pytest.mark.parametrize("sentinel", sorted(_SENTINELS))
    def test_a_member_with_no_comparison_is_counted_not_summed(
        self, sentinel: str
    ) -> None:
        folded = fold_release_change_inventory(
            [
                self._member("BREAKING", compatibility_changes=2),
                # A failed member could carry a stale or zeroed block; either
                # way it must not reach the sums, and must be visible.
                self._member(sentinel, compatibility_changes=999),
            ],
            operational_sentinels=_SENTINELS,
        )
        assert folded is not None
        assert folded["compatibility_changes"] == 2
        assert folded["members_contributing"] == 1
        assert folded["members_no_comparison_completed"] == 1

    def test_a_member_without_a_block_is_counted_not_assumed_zero(self) -> None:
        folded = fold_release_change_inventory(
            [
                self._member("BREAKING", compatibility_changes=2),
                {"library": "legacy.so", "verdict": "NO_CHANGE"},
            ],
            operational_sentinels=_SENTINELS,
        )
        assert folded is not None
        assert folded["members_contributing"] == 1
        assert folded["members_without_inventory"] == 1

    def test_no_member_block_yields_no_section_rather_than_zeros(self) -> None:
        assert (
            fold_release_change_inventory([], operational_sentinels=_SENTINELS) is None
        )
        assert (
            fold_release_change_inventory(
                [{"library": "l.so", "verdict": "NO_CHANGE"}],
                operational_sentinels=_SENTINELS,
            )
            is None
        )
        assert (
            fold_release_change_inventory(
                [{"library": "l.so", "verdict": "failed"}],
                operational_sentinels=_SENTINELS,
            )
            is None
        )

    def test_a_missing_counter_reads_as_zero_for_that_member_only(self) -> None:
        folded = fold_release_change_inventory(
            [
                {
                    "library": "partial.so",
                    "verdict": "NO_CHANGE",
                    "change_inventory": {"hygiene_persistent": 5},
                },
                self._member("NO_CHANGE", compatibility_changes=7),
            ],
            operational_sentinels=_SENTINELS,
        )
        assert folded is not None
        assert folded["hygiene_persistent"] == 5
        assert folded["compatibility_changes"] == 7
        assert set(RELEASE_INVENTORY_COUNTERS) <= set(folded)

    def test_non_integer_and_boolean_counters_are_ignored(self) -> None:
        folded = fold_release_change_inventory(
            [
                {
                    "library": "junk.so",
                    "verdict": "NO_CHANGE",
                    "change_inventory": {
                        "compatibility_changes": "3",
                        "hygiene_persistent": True,
                    },
                }
            ],
            operational_sentinels=_SENTINELS,
        )
        assert folded is not None
        assert folded["compatibility_changes"] == 0
        assert folded["hygiene_persistent"] == 0

    def test_a_non_mapping_block_is_treated_as_absent(self) -> None:
        folded = fold_release_change_inventory(
            [
                {"library": "junk.so", "verdict": "NO_CHANGE", "change_inventory": []},
                self._member("NO_CHANGE", compatibility_changes=1),
            ],
            operational_sentinels=_SENTINELS,
        )
        assert folded is not None
        assert folded["members_without_inventory"] == 1
        assert folded["compatibility_changes"] == 1


class TestRenderersAgree:
    """One release, three renders, one set of numbers."""

    @staticmethod
    def _library_results() -> list[dict[str, object]]:
        entry: dict[str, object] = {
            "library": "libx.so",
            "verdict": "NO_CHANGE",
            "breaking": 0,
            "source_breaks": 0,
            "risk_changes": 5,
            "compatible_additions": 0,
            "quality_issues": 0,
        }
        add_member_review_summary(entry, _mixed_result(), None)
        return [entry]

    def test_oneline_states_hygiene_separately_from_observed_changes(self) -> None:
        from abicheck.report.release_oneline import format_release_oneline

        libs = self._library_results()
        inventory = release_inventory_counters(libs)
        assert inventory is not None
        line = format_release_oneline("NO_CHANGE", libs, change_inventory=inventory)
        # The contradiction this closes: a rebuild whose displayed "risk" is
        # entirely standing inventory must not print it as observed change.
        # Asserted on the rendered *values*, not just the labels: a renderer
        # emitting the wrong counts passed the label-only version of this
        # (found in review), which is the same "asserts nothing" failure the
        # inventory itself was introduced to fix.
        observed = inventory["compatibility_changes"]
        assert f"{inventory['hygiene_persistent']} persistent" in line
        assert f"{observed} total" in line
        assert f"{inventory['hygiene_introduced']} introduced" in line
        assert f"{inventory['hygiene_resolved']} resolved" in line
        # ...and the standing inventory is not counted as observed risk: the
        # members' raw `risk_changes` is 5, which must not appear as risk.
        assert "5 risk" not in line
        without = format_release_oneline("NO_CHANGE", libs)
        assert without != line

    def test_a_release_global_break_survives_a_members_only_inventory(self) -> None:
        """A bundle/matrix break must not vanish behind the inventory.

        `format_stat_line` *replaces* its headline counts with the
        inventory's `compatibility_*` values whenever standing hygiene is
        present, so passing a members-only inventory discarded the
        release-global findings already folded into those counts. A release
        with hygiene whose only break was a bundle finding then printed
        "no compatibility changes" beside a BREAKING verdict -- the exact
        contradiction the inventory exists to remove, one level up.

        Release-global findings carry no cross-source evolution stamp, so
        they are observed changes, never standing inventory.
        """
        from abicheck.report.release_oneline import format_release_oneline

        libs = self._library_results()
        inventory = release_inventory_counters(libs)
        assert inventory is not None
        release_global = {
            "breaking": 1,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
            "total": 1,
        }
        line = format_release_oneline(
            "BREAKING",
            libs,
            change_inventory=inventory,
            release_global=release_global,
        )
        # The member's own observed break PLUS the release-global one.
        expected_breaking = inventory["compatibility_breaking"] + 1
        assert f"{expected_breaking} breaking" in line, line
        # The hygiene clause is still stated separately, unchanged.
        assert f"{inventory['hygiene_persistent']} persistent" in line
        # And the observed total accounts for the release-global finding.
        expected_total = inventory["compatibility_changes"] + 1
        assert f"{expected_total} total" in line, line
        # Vacuity guard: the merge must actually be adding something, i.e.
        # the fixture's own member inventory is not already the total.
        assert expected_breaking > inventory["compatibility_breaking"]

    def test_a_release_global_break_is_reported_without_any_inventory(self) -> None:
        """Control: the pre-existing path is unchanged.

        Without an inventory there is no override, so this would pass even
        with the bug -- which is why it is a control rather than the
        regression test above.
        """
        from abicheck.report.release_oneline import format_release_oneline

        line = format_release_oneline(
            "BREAKING",
            [],
            release_global={
                "breaking": 1,
                "source_breaks": 0,
                "risk_changes": 0,
                "compatible_additions": 0,
                "total": 1,
            },
        )
        assert "1 breaking" in line, line

    def test_markdown_table_states_the_split_without_changing_its_columns(
        self,
    ) -> None:
        from abicheck.report.render_release_markdown import _release_md_libraries_table

        libs = self._library_results()
        plain = _release_md_libraries_table(libs, {"NO_CHANGE": "="})
        annotated = _release_md_libraries_table(libs, {"NO_CHANGE": "="}, libs)
        # Additive: the table rows a consumer already parses are byte-identical.
        assert annotated[: len(plain)] == plain
        assert len(annotated) > len(plain)
        assert "standing inventory" in "\n".join(annotated)

    def test_json_release_summary_carries_the_aggregate(self) -> None:
        from abicheck.cli_compare_release_helpers import _format_release_json

        doc = json.loads(
            _format_release_json(
                "NO_CHANGE",
                __import__("pathlib").Path("old"),
                __import__("pathlib").Path("new"),
                self._library_results(),
                [],
                [],
                {},
                {},
                [],
                None,
                None,
            )
        )
        aggregate = doc["change_inventory"]
        member = doc["libraries"][0]["change_inventory"]
        assert aggregate["hygiene_persistent"] == member["hygiene_persistent"]
        assert aggregate["compatibility_changes"] == member["compatibility_changes"]
        assert aggregate["members_contributing"] == 1
        assert aggregate["members_no_comparison_completed"] == 0

    def test_release_schema_version_records_the_addition(self) -> None:
        from abicheck.schemas import RELEASE_SCHEMA_VERSION

        assert tuple(int(p) for p in RELEASE_SCHEMA_VERSION.split(".")) >= (1, 7)


class TestCounterProjection:
    """``release_inventory_counters``: the renderers' reduced view."""

    def test_absent_inventory_projects_to_none_not_an_empty_mapping(self) -> None:
        """A release with nothing to state must give a renderer ``None``.

        ``format_hygiene_note`` treats an empty mapping and ``None`` alike
        today, but the distinction is the one this whole change rests on:
        "no member carried an inventory" is not "the inventory is all
        zeroes", and a renderer that received ``{}`` could legitimately
        print a zeroed split for a release that never inventoried anything.
        """
        assert (
            release_inventory_counters([{"library": "l.so", "verdict": "NO_CHANGE"}])
            is None
        )
        assert release_inventory_counters([]) is None

    def test_projection_keeps_only_the_integer_counters(self) -> None:
        entry: dict[str, object] = {"library": "libx.so", "verdict": "NO_CHANGE"}
        add_member_review_summary(entry, _mixed_result(), None)
        counters = release_inventory_counters([entry])
        assert counters is not None
        assert set(counters) == set(RELEASE_INVENTORY_COUNTERS)
        assert all(isinstance(v, int) for v in counters.values())
        # The scope-accounting keys are for a document reader, not a
        # one-line clause, so they must not leak into this view.
        assert "members_contributing" not in counters

    def test_projection_agrees_with_the_document_block(self) -> None:
        """Both views come from one fold, so their counters cannot differ.

        This is the same "one computation, many renders" property the
        member-versus-scalar invariant states, applied to the release's own
        two consumers.
        """
        entry: dict[str, object] = {"library": "libx.so", "verdict": "NO_CHANGE"}
        add_member_review_summary(entry, _mixed_result(), None)
        folded = release_change_inventory([entry])
        counters = release_inventory_counters([entry])
        assert folded is not None and counters is not None
        for key in RELEASE_INVENTORY_COUNTERS:
            assert counters[key] == folded[key], key
