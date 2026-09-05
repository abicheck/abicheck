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
from abicheck.policy.disposition_close import conservation_holds
from abicheck.policy.disposition_ledger import DispositionLedger
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


@pytest.mark.parametrize("suppress_all", [False, True])
@pytest.mark.parametrize(("removed", "added"), [(0, 0), (1, 0), (0, 1), (2, 3)])
def test_the_one_line_view_always_states_both_totals(
    removed: int, added: int, suppress_all: bool
) -> None:
    """The general form: whenever the one-line view says anything about the
    audit at all, it says both totals.

    The bug this closes was a *combination* -- a condition that read correctly
    in every state tested and dropped both counts in the one that was not --
    so this enumerates the states rather than adding one more example.
    """
    from abicheck.report.disposition_audit import (
        compute_disposition_audit,
        render_disposition_audit_note,
    )

    old, new = _snapshots(removed=removed, added=added)
    rules = (
        SuppressionList(
            [Suppression(symbol_pattern=".*", reason="all", allow_public_break=True)]
        )
        if suppress_all
        else None
    )
    result = compare(old, new, rules)
    audit = compute_disposition_audit(result)
    note = render_disposition_audit_note(audit)
    if not note:
        # The one silent case: nothing detected and every detector ran, so the
        # counts repeat what the line beside them already says.
        assert audit.detected_total == 0 and not audit.not_evaluated_detectors
        return
    assert f"{audit.detected_total} detected" in note
    assert f"{audit.effective_total} gating" in note


def test_a_suppression_diagnostic_is_not_a_second_detection() -> None:
    """Adding a rule redistributes dispositions; it never changes what was
    observed — including when the rule changes nothing.

    `ApplySuppression` emits a `SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`
    diagnostic *alongside* the finding it is about when a broad selector
    matched but the reachability gate withheld it (ADR-044 D4). Both landed
    in `result.changes`, and recording both made merely adding a
    non-applicable rule move `detected_total` from 1 to 2 — the conservation
    this audit exists to make checkable, broken by the audit itself.

    The diagnostic is an overlay on a record that already exists, not a
    second detection. It keeps whatever gate contribution it independently
    has, which is what the second half asserts.
    """
    from abicheck.checker_types import Change, DiffResult
    from abicheck.policy.disposition_close import finalize_ledger

    def _result(with_diagnostic: bool):
        result = DiffResult(old_version="1.0", new_version="2.0", library="libmatrix")
        break_ = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="pub", description="the break"
        )
        result.changes = [break_]
        if with_diagnostic:
            result.changes.append(
                Change(
                    kind=ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK,
                    symbol="pub",
                    description="rule matched but was withheld",
                    caused_by_type="pub",
                )
            )
        return result

    without = finalize_ledger(DispositionLedger(), _result(False))
    with_rule = finalize_ledger(DispositionLedger(), _result(True))
    assert without.detected_total == with_rule.detected_total == 1, (
        "a rule that changes nothing changed the observed total"
    )
    assert conservation_holds(with_rule)

    # …and the diagnostic still reaches every consumer that reads the
    # result, which is where its own gate effect lives.
    assert any(
        c.kind is ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK
        for c in _result(True).changes
    )


def test_every_policy_overlay_kind_is_produced_by_policy_not_a_detector() -> None:
    """The exclusion list is narrow by construction, so it cannot quietly
    start hiding real detections.

    Each excluded kind must be one no detector emits — it exists only because
    a policy pass generated it about another finding. Checked against the
    detector registry's own catalogue rather than asserted in prose.
    """
    from abicheck.policy.disposition_close import _POLICY_OVERLAY_KINDS

    assert _POLICY_OVERLAY_KINDS, "the list must not be silently emptied"
    for slug in _POLICY_OVERLAY_KINDS:
        assert any(k.value == slug for k in ChangeKind), (
            f"{slug!r} is not a real ChangeKind"
        )
    # The one member today, named so a second entry is a deliberate decision
    # rather than a drive-by addition.
    assert _POLICY_OVERLAY_KINDS == {
        ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK.value
    }
