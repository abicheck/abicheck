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

"""ADR-067 C-S1: the audit's *detector-state* and *release-advice* halves.

Split out of ``tests/test_disposition_audit.py`` when that file passed the
architecture gate's 1200-line test-file cap. The seam is real rather than
arbitrary: this file covers what the audit says about capability that was
never exercised (``not_evaluated``) and the one behaviour change the slice
makes (``semver.recommend_release`` reading the conserved delta); the
sibling file covers the ledger's own conservation and disposition contract.
Both are registered as seed tests of the ``policy.disposition_conservation``
bug class.
"""

from __future__ import annotations

import json

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, Variable, Visibility
from abicheck.semver import ReleaseRecommendationState, SemverBump
from abicheck.suppression import Suppression, SuppressionList


def _snapshots(
    removed: int = 0,
    *,
    kept: int = 0,
    added: int = 0,
    variables_removed: int = 0,
    prefix: str = "foo",
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Old/new pair with *removed* public functions gone in the new side."""
    old = AbiSnapshot(library="libfoo", version="1.0")
    new = AbiSnapshot(library="libfoo", version="2.0")

    def _fn(name: str) -> Function:
        return Function(
            name=f"{prefix}::{name}",
            mangled=f"_ZN3{prefix}{len(name)}{name}Ev",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )

    for i in range(removed):
        old.functions.append(_fn(f"gone{i}"))
    for i in range(kept):
        fn = _fn(f"stay{i}")
        old.functions.append(fn)
        new.functions.append(_fn(f"stay{i}"))
    for i in range(added):
        new.functions.append(_fn(f"new{i}"))
    for i in range(variables_removed):
        old.variables.append(
            Variable(
                name=f"{prefix}::var{i}",
                mangled=f"_ZN3{prefix}3var{i}E",
                type="int",
                visibility=Visibility.PUBLIC,
            )
        )
    return old, new


# ---------------------------------------------------------------------------
# not_evaluated detectors
# ---------------------------------------------------------------------------


class TestNotEvaluatedDetectors:
    def test_a_detector_that_did_not_run_is_not_a_zero(self) -> None:
        old, new = _snapshots(removed=1)
        result = compare(old, new)
        by_name = {d.name: d for d in result.detector_results}
        dwarf = by_name["dwarf"]
        assert dwarf.changes_count == 0
        assert dwarf.not_evaluated is True
        assert dwarf.enabled is False
        assert dwarf.coverage_gap

    def test_every_not_evaluated_detector_states_a_reason(self) -> None:
        old, new = _snapshots(removed=1)
        result = compare(old, new)
        not_run = [d for d in result.detector_results if d.not_evaluated]
        assert not_run, "this evidence-free pair must leave detectors unevaluated"
        assert all(d.coverage_gap for d in not_run)
        assert all(d.changes_count == 0 and not d.enabled for d in not_run)

    def test_a_new_only_dwarf_comparison_is_not_evaluated(self) -> None:
        """The old side carrying no debug info means there is no baseline to
        compare a new-side layout against — the detector's own documented
        skip. Reporting that as `enabled=True, changes_count=0` presents an
        unperformed comparison as a performed one that found nothing."""
        from abicheck.model.dwarf_facts import DwarfMetadata

        old, new = _snapshots(removed=1)
        new.dwarf = DwarfMetadata(has_dwarf=True)
        result = compare(old, new)
        dwarf = {d.name: d for d in result.detector_results}["dwarf"]
        assert dwarf.not_evaluated is True
        assert dwarf.changes_count == 0
        assert "baseline" in (dwarf.coverage_gap or "")

    def test_an_old_only_dwarf_comparison_is_evaluated(self) -> None:
        """The mirror case is *not* the same claim: the old side has layout
        evidence and the new side lost it, which the detector reports as a
        real `DWARF_INFO_MISSING` finding. That is an evaluated comparison
        disclosing a loss of evidence, so the gate must stay open for it."""
        from abicheck.model.dwarf_facts import DwarfMetadata

        old, new = _snapshots()
        old.dwarf = DwarfMetadata(has_dwarf=True)
        result = compare(old, new)
        dwarf = {d.name: d for d in result.detector_results}["dwarf"]
        assert dwarf.not_evaluated is False
        assert dwarf.enabled is True
        assert any(
            c.kind is ChangeKind.DWARF_INFO_MISSING
            for c in result.changes + result.suppressed_changes
        )

    def test_a_detector_that_ran_is_never_marked_not_evaluated(self) -> None:
        old, new = _snapshots(removed=1)
        result = compare(old, new)
        ran = [d for d in result.detector_results if d.enabled]
        assert ran
        assert not any(d.not_evaluated for d in ran)

    def test_the_state_reaches_the_report(self) -> None:
        from abicheck import reporter

        old, new = _snapshots(removed=1)
        report = json.loads(reporter.to_json(compare(old, new)))
        detectors = {d["name"]: d for d in report["detectors"]}
        assert detectors["dwarf"]["not_evaluated"] is True
        assert "dwarf" in {
            d["name"] for d in report["disposition_audit"]["not_evaluated_detectors"]
        }


# ---------------------------------------------------------------------------
# semver.recommend_release reads the conserved delta
# ---------------------------------------------------------------------------


class TestRecommendReleaseReadsTheConservedDelta:
    """The reported bug: a suppressed break became "no bump needed".

    Exercised over several sibling shapes rather than the one reported input
    — a wildcard waiver, an exact-symbol rule, a kind rule, and a variable
    removal — because the defect was in *what the recommendation reads*, not
    in any one rule spelling.
    """

    @pytest.mark.parametrize(
        "rule",
        [
            Suppression(symbol_pattern=".*", reason="w", allow_public_break=True),
            Suppression(symbol="_ZN3foo5gone0Ev", reason="w", allow_public_break=True),
            Suppression(
                symbol_pattern="_ZN3foo.*",
                change_kind="func_removed",
                reason="w",
                allow_public_break=True,
            ),
        ],
    )
    def test_a_suppressed_break_is_not_no_bump_needed(self, rule) -> None:
        from abicheck.semver import recommend_release

        old, new = _snapshots(removed=1)
        result = compare(old, new, SuppressionList([rule]))
        assert result.changes == []  # the rule really did hide it

        rec = recommend_release(result)
        assert rec.bump is SemverBump.MAJOR
        assert rec.state is ReleaseRecommendationState.REVIEW
        assert "suppressed" in rec.rationale
        assert "intent: unspecified" in rec.rationale
        assert "no version bump required" not in rec.rationale

    def test_an_unsuppressed_run_is_unchanged(self) -> None:
        from abicheck.semver import recommend_release

        old, new = _snapshots(removed=1)
        assert recommend_release(compare(old, new)).bump is SemverBump.MAJOR
        clean_old, clean_new = _snapshots(removed=0, kept=2)
        clean = recommend_release(compare(clean_old, clean_new))
        assert clean.bump is SemverBump.NONE
        assert clean.state is ReleaseRecommendationState.ACTIONABLE

    def test_a_suppressed_compatible_addition_does_not_force_a_major(self) -> None:
        """Only a *major-class* suppressed finding changes the advice — a
        suppressed addition is not a hidden break."""
        from abicheck.semver import recommend_release

        old, new = _snapshots(added=2)
        result = compare(
            old, new, SuppressionList([Suppression(symbol_pattern=".*", reason="w")])
        )
        rec = recommend_release(result)
        assert rec.bump is not SemverBump.MAJOR
        assert rec.state is ReleaseRecommendationState.ACTIONABLE

    def test_the_rule_that_hid_the_break_is_named_in_the_rationale(self) -> None:
        from abicheck.semver import recommend_release

        old, new = _snapshots(removed=1)
        result = compare(
            old,
            new,
            SuppressionList(
                [
                    Suppression(
                        symbol_pattern=".*gone.*",
                        reason="tracked in ticket 42",
                        allow_public_break=True,
                    )
                ]
            ),
        )
        rationale = recommend_release(result).rationale
        assert "symbol_pattern" in rationale
        assert "func_removed" in rationale
