# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the pattern/preprocessor scan coverage-completeness
fix (CodeRabbit review of PR #1141, fresh evidence).

Bug: ``compute_pattern_preprocessor_scan`` folded a side as "evaluated" from
``files_scanned > 0`` (pattern) or ``ran and not all_failed`` (preprocessor)
alone -- both hold even when the scan only *partially* covered its inputs
(some pattern files skipped as unreadable; some preprocessor probes failed
or were truncated by the probe cap). A pattern kind or macro divergence
absent from a partial result could simply be hiding in the unscanned/failed
portion, not genuinely absent -- so folding it as fully evaluated let
``pattern_escalation_evolution``/``macro_divergence_evolution``/
``header_leak_evolution`` report ``introduced``/``resolved`` from incomplete
evidence instead of the honest ``not_evaluated``.

General invariant, not just the reported shape: both the pattern-scan and
preprocessor-scan sibling primitives get the same completeness treatment
(``_pattern_sufficiency``/``_macro_divergence_sufficiency``/
``_header_leak_sufficiency``), each exercised at the unit level directly
against constructed result objects (every combination of "some work done, but
not all of it"), plus one end-to-end ``compute_pattern_preprocessor_scan``
case per primitive proving the fold itself changes to ``not_evaluated``.

**Two contract refinements this module was updated for** (see
``tests/test_stored_snapshot_source_licence.py``, which owns the two
registered bug classes):

- Sufficiency is now computed from the scan's *expected-input set* rather than
  from the ``files_scanned``/``files_skipped`` tallies, because a declared root
  that did not exist contributed to neither tally. A hand-built
  ``PatternFactsResult`` that carries only tallies therefore has no input
  account and is never sufficient -- deny-by-default -- so these unit cases now
  construct the account they mean.
- Sufficiency is answered **per check**, and establishment is answered **per
  identity**: presence is established by observation (an incomplete scan
  cannot un-see a hit), absence only by sufficiency. So ``persistent`` no
  longer requires both sides to be complete -- both sides *observed* the
  construct, which is the strongest premise this fold has -- while
  ``introduced``/``resolved``, which each assert an absence, still do. See
  ``_fold_evolution``'s own docstring.
"""

from __future__ import annotations

from unittest.mock import patch

from abicheck.buildsource.pattern_facts import (
    PatternCategory,
    PatternFact,
    PatternFactsResult,
    PatternKind,
)
from abicheck.buildsource.preprocessor_facts import PreprocessorFactsResult
from abicheck.buildsource.preprocessor_probe_families import ProbeTallies
from abicheck.buildsource.source_inputs import (
    SourceInput,
    SourceInputDisposition,
    SourceInputSet,
    SourceReadLicence,
)
from abicheck.model import AbiSnapshot
from abicheck.workflows.pattern_preprocessor_scan import (
    Sufficiency,
    _fold_evolution,
    _header_leak_sufficiency,
    _macro_divergence_sufficiency,
    _pattern_sufficiency,
    compute_pattern_preprocessor_scan,
)


def _account(scanned: int = 0, skipped: int = 0) -> SourceInputSet:
    """The expected-input account a real scan of *scanned*+*skipped* files has.

    The unit cases below used to hand ``PatternFactsResult`` bare tallies; the
    tallies are no longer the sufficiency signal (that was the bug), so each
    case now states the account those tallies came from.
    """
    return SourceInputSet(
        inputs=tuple(
            [
                SourceInput(path=f"s{i}", disposition=SourceInputDisposition.SCANNED)
                for i in range(scanned)
            ]
            + [
                SourceInput(path=f"u{i}", disposition=SourceInputDisposition.UNREADABLE)
                for i in range(skipped)
            ]
        ),
        licence=SourceReadLicence.live_extraction(),
    )


def _pattern_result(
    scanned: int = 0, skipped: int = 0, **kw: object
) -> PatternFactsResult:
    return PatternFactsResult(
        files_scanned=scanned,
        files_skipped=skipped,
        inputs=_account(scanned, skipped),
        **kw,  # type: ignore[arg-type]
    )


def _covered() -> Sufficiency:
    return Sufficiency(established=True)


def _partial(reason: str = "one unreadable file") -> Sufficiency:
    return Sufficiency(established=False, reason=reason)


class TestPatternScanFullyCovered:
    def test_no_files_scanned_is_not_covered(self) -> None:
        assert _pattern_sufficiency(PatternFactsResult()).established is False

    def test_all_scanned_no_skips_is_covered(self) -> None:
        assert _pattern_sufficiency(_pattern_result(scanned=3)).established is True

    def test_any_skipped_file_is_not_covered(self) -> None:
        """Even a single unreadable file alongside otherwise-successful
        scans means the result could be hiding a real hit -- not full
        coverage, per the CodeRabbit finding."""
        result = _pattern_result(scanned=3, skipped=1)
        assert _pattern_sufficiency(result).established is False

    def test_all_skipped_is_not_covered(self) -> None:
        result = _pattern_result(scanned=0, skipped=2)
        assert _pattern_sufficiency(result).established is False

    def test_missing_root_is_not_covered_even_with_clean_tallies(self) -> None:
        """The P1 completeness defect, at the unit level: the tallies say
        "one file scanned, nothing skipped" -- exactly what the old predicate
        called complete -- while a declared root is simply gone."""
        result = PatternFactsResult(
            files_scanned=1,
            files_skipped=0,
            inputs=SourceInputSet(
                inputs=(
                    SourceInput(path="a.h", disposition=SourceInputDisposition.SCANNED),
                    SourceInput(path="b.h", disposition=SourceInputDisposition.MISSING),
                ),
                licence=SourceReadLicence.live_extraction(),
            ),
        )
        assert _pattern_sufficiency(result).established is False
        assert "missing" in _pattern_sufficiency(result).reason


class TestPreprocessorScanFullyCovered:
    """Both preprocessor-derived checks share these probe-level gaps; where
    they diverge is covered by
    ``test_stored_snapshot_source_licence.py::...macro_and_leak_sufficiency_can_disagree``."""

    def test_did_not_run_is_not_covered(self) -> None:
        assert (
            _macro_divergence_sufficiency(PreprocessorFactsResult()).established
            is False
        )
        assert _header_leak_sufficiency(PreprocessorFactsResult()).established is False

    def test_all_succeeded_is_covered(self) -> None:
        # Sufficiency now reads each probe family's own tallies, not the run-wide
        # aggregates -- so a fully-covered run has to state both families (see
        # ``test_stored_snapshot_source_licence.py::
        # TestProbeFamilySufficiencyIsIndependent`` for why).
        result = PreprocessorFactsResult(
            ran=True,
            attempted=5,
            succeeded=5,
            probe_tallies=ProbeTallies(
                attempted={"macro": 3, "header": 2}, succeeded={"macro": 3, "header": 2}
            ),
        )
        assert _macro_divergence_sufficiency(result).established is True
        assert _header_leak_sufficiency(result).established is True

    def test_partial_failure_is_not_covered(self) -> None:
        """Some (not all) clang -E invocations failing means ``all_failed``
        is False, but coverage is still incomplete -- the CodeRabbit gap."""
        result = PreprocessorFactsResult(
            ran=True,
            attempted=6,
            succeeded=4,
            probe_tallies=ProbeTallies(
                attempted={"macro": 3, "header": 3}, succeeded={"macro": 2, "header": 2}
            ),
        )
        assert _macro_divergence_sufficiency(result).established is False
        assert _header_leak_sufficiency(result).established is False

    def test_all_failed_is_not_covered(self) -> None:
        result = PreprocessorFactsResult(
            ran=True,
            attempted=3,
            succeeded=0,
            probe_tallies=ProbeTallies(attempted={"macro": 3}, succeeded={}),
        )
        assert _macro_divergence_sufficiency(result).established is False

    def test_truncated_probes_is_not_covered(self) -> None:
        """The probe-count cap silently dropping units is exactly the same
        "real coverage gap" shape as a partial failure, even when every
        attempted probe itself succeeded."""
        result = PreprocessorFactsResult(
            ran=True,
            attempted=5,
            succeeded=5,
            probes_truncated=2,
            probe_tallies=ProbeTallies(
                attempted={"macro": 3, "header": 2},
                succeeded={"macro": 3, "header": 2},
                truncated={"macro": 1, "header": 1},
            ),
        )
        assert _macro_divergence_sufficiency(result).established is False
        assert _header_leak_sufficiency(result).established is False


class TestFoldEvolutionIncompleteSideReverseOrientation:
    """CodeRabbit review, fresh evidence: an earlier revision only folded
    ``not_evaluated`` when the *hit itself* was on the evaluated side
    (``new_hit and not old_evaluated`` / its symmetric case) -- the reverse
    orientation, a hit only on the *incomplete* side with the evaluated
    side silent, fell through to a bare ``continue`` and the identity
    vanished from the result entirely instead of reading ``not_evaluated``.
    Incompleteness on either side means neither PERSISTENT/INTRODUCED/
    RESOLVED can be trusted for *any* identity in the union, regardless of
    which side actually flagged it -- these cases exercise exactly the
    orientation the earlier revision dropped."""

    def test_hit_only_on_incomplete_old_side_is_not_evaluated(self) -> None:
        """OLD is incomplete and flags `k`; NEW is fully evaluated and does
        NOT flag it. The naive fold reads this as neither `new_hit` (false)
        nor `old_hit and not new_evaluated` (new_evaluated is True) --
        falling through to `continue` and silently dropping `k` instead of
        reporting `not_evaluated`.

        Under per-identity establishment this is still `not_evaluated`, but
        for a sharper reason: `resolved` asserts *absence in NEW*, and NEW's
        own coverage is what would have to establish that. Here it does, so
        what blocks the claim is OLD -- whose hit establishes presence but
        whose incompleteness is irrelevant to a claim about NEW. The state
        that actually decides it is therefore NEW's: see
        ``test_resolved_is_allowed_when_new_alone_is_established`` below."""
        result = _fold_evolution(
            old=_partial(),
            new=_covered(),
            old_keys={"k"},
            new_keys=set(),
        )
        assert result == {"k": "resolved"}

    def test_resolved_needs_new_side_coverage_not_old_side_coverage(self) -> None:
        """The refinement, stated directly: `resolved` is a claim about NEW
        not containing the construct, so NEW's coverage is what must be
        established. OLD's incompleteness cannot un-see OLD's own hit."""
        assert _fold_evolution(
            old=_partial(), new=_partial(), old_keys={"k"}, new_keys=set()
        ) == {"k": "not_evaluated"}
        assert _fold_evolution(
            old=_partial(), new=_covered(), old_keys={"k"}, new_keys=set()
        ) == {"k": "resolved"}

    def test_hit_only_on_incomplete_new_side_is_not_evaluated(self) -> None:
        """Symmetric case: NEW is incomplete and flags `k`; OLD is fully
        evaluated and does not. `introduced` asserts absence in OLD, which
        OLD's own established coverage supports."""
        result = _fold_evolution(
            old=_covered(),
            new=_partial(),
            old_keys=set(),
            new_keys={"k"},
        )
        assert result == {"k": "introduced"}

    def test_introduced_is_refused_when_old_coverage_is_not_established(
        self,
    ) -> None:
        """The P1 fold defect: NEW flags `k`, OLD does not, and OLD's
        coverage is *not* established -- so "newly introduced" is a claim
        about OLD that nothing supports."""
        result = _fold_evolution(
            old=_partial("declared root is missing"),
            new=_covered(),
            old_keys=set(),
            new_keys={"k"},
        )
        assert result == {"k": "not_evaluated"}

    def test_hit_on_both_sides_one_incomplete_is_persistent(self) -> None:
        """Both sides *observed* `k`. An incomplete scan cannot un-see a hit,
        so `persistent` rests on two observations and needs no sufficiency --
        the per-identity refinement of the earlier revision's global rule,
        which folded this to `not_evaluated` and discarded a fact both sides
        had actually established."""
        result = _fold_evolution(
            old=_partial(),
            new=_covered(),
            old_keys={"k"},
            new_keys={"k"},
        )
        assert result == {"k": "persistent"}


def _empty_snapshot(library: str, version: str) -> AbiSnapshot:
    return AbiSnapshot(library=library, version=version)


class TestComputePatternPreprocessorScanCoverageFold:
    """End-to-end: when the fully-covered side has a hit its incomplete
    sibling doesn't, the fold must read ``not_evaluated``, never
    ``resolved`` -- the incomplete side's silence proves nothing."""

    def test_partial_pattern_scan_folds_not_evaluated_instead_of_resolved(
        self,
    ) -> None:
        """OLD fully scans and finds the construct; NEW skips a file and its
        (necessarily partial) scan of the rest finds nothing. Before this
        fix, ``files_scanned > 0`` alone marked NEW "evaluated" too, so the
        fold read the construct as ``resolved`` -- a false claim, since the
        construct could simply be sitting in NEW's one skipped file."""
        old = _empty_snapshot("libfoo.so", "1.0")
        new = _empty_snapshot("libfoo.so", "2.0")

        old_pattern = PatternFactsResult(
            facts=[
                PatternFact(
                    kind=PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION,
                    category=PatternCategory.TEMPLATE,
                    path="old.hpp",
                    line=1,
                    snippet="template class Widget<int>;",
                    escalates=True,
                    detail="explicit template instantiation",
                )
            ],
            files_scanned=1,
            files_skipped=0,
            inputs=_account(scanned=1),
        )
        new_pattern = PatternFactsResult(
            files_scanned=1,
            files_skipped=1,  # partial: one unreadable file alongside it
            inputs=_account(scanned=1, skipped=1),
        )

        with (
            patch(
                "abicheck.workflows.pattern_preprocessor_scan._run_pattern_scan",
                side_effect=[old_pattern, new_pattern],
            ),
            patch(
                "abicheck.workflows.pattern_preprocessor_scan._run_preprocessor_scan_for",
                return_value=PreprocessorFactsResult(),
            ),
        ):
            result = compute_pattern_preprocessor_scan(old, new)

        # A naive files_scanned>0 fold would report "resolved" here (the
        # kind is old-only); full coverage requires zero skips on NEW.
        assert (
            result.pattern_escalation_evolution.get("explicit_template_instantiation")
            == "not_evaluated"
        )

    def test_partial_preprocessor_scan_folds_not_evaluated_instead_of_resolved(
        self,
    ) -> None:
        from abicheck.buildsource.preprocessor_facts import MacroDivergence

        old = _empty_snapshot("libfoo.so", "1.0")
        new = _empty_snapshot("libfoo.so", "2.0")

        old_preproc = PreprocessorFactsResult(
            ran=True,
            attempted=2,
            succeeded=2,
            tus_scanned=2,
            divergences=[
                MacroDivergence(macro="FOO_VERSION", values={"1": ["tu1"]}),
            ],
        )
        new_preproc = PreprocessorFactsResult(
            ran=True,
            attempted=2,
            succeeded=1,  # partial: one clang -E invocation failed
        )

        with (
            patch(
                "abicheck.workflows.pattern_preprocessor_scan._run_pattern_scan",
                return_value=PatternFactsResult(),
            ),
            patch(
                "abicheck.workflows.pattern_preprocessor_scan._run_preprocessor_scan_for",
                side_effect=[old_preproc, new_preproc],
            ),
        ):
            result = compute_pattern_preprocessor_scan(old, new)

        # A naive "ran and not all_failed" fold would report "resolved"
        # here; full coverage requires succeeded == attempted on NEW.
        assert result.macro_divergence_evolution.get("FOO_VERSION") == "not_evaluated"

    def test_hit_only_on_incomplete_side_is_never_silently_dropped(
        self,
    ) -> None:
        """CodeRabbit review, fresh evidence: the reverse orientation of the
        two cases above -- the hit is on the INCOMPLETE side (NEW skips a file
        and, in the portion it did scan, finds the construct), while the
        fully-covered OLD side finds nothing. The pre-fix `_fold_evolution`
        fell through to a bare `continue` for this orientation, silently
        dropping the identity from the report entirely.

        Under per-identity establishment the identity is not merely present in
        the map, it is *decidable*: `introduced` asserts absence in OLD, and
        OLD's coverage is established, so the claim is supported. The second
        half of this test takes OLD's coverage away and shows the same input
        then folds to `not_evaluated` -- which is the actual P1 fold fix, and
        the reason the key must never be dropped in either case.
        """
        old = _empty_snapshot("libfoo.so", "1.0")
        new = _empty_snapshot("libfoo.so", "2.0")

        new_pattern = PatternFactsResult(
            facts=[
                PatternFact(
                    kind=PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION,
                    category=PatternCategory.TEMPLATE,
                    path="new.hpp",
                    line=1,
                    snippet="template class Widget<int>;",
                    escalates=True,
                    detail="explicit template instantiation",
                )
            ],
            files_scanned=1,
            files_skipped=1,  # partial: one unreadable file alongside it
            inputs=_account(scanned=1, skipped=1),
        )

        def _run(old_pattern: PatternFactsResult) -> str:
            with (
                patch(
                    "abicheck.workflows.pattern_preprocessor_scan._run_pattern_scan",
                    side_effect=[old_pattern, new_pattern],
                ),
                patch(
                    "abicheck.workflows.pattern_preprocessor_scan._run_preprocessor_scan_for",
                    return_value=PreprocessorFactsResult(),
                ),
            ):
                result = compute_pattern_preprocessor_scan(old, new)
            evolution = result.pattern_escalation_evolution
            # Never dropped, whatever the state (the bare-`continue` bug).
            assert "explicit_template_instantiation" in evolution
            return evolution["explicit_template_instantiation"]

        # OLD is fully covered: its silence establishes absence -> introduced.
        assert (
            _run(
                PatternFactsResult(
                    files_scanned=1, files_skipped=0, inputs=_account(scanned=1)
                )
            )
            == "introduced"
        )
        # OLD has a gap of its own: nothing establishes absence in OLD, so
        # "newly introduced" is refused.
        assert (
            _run(
                PatternFactsResult(
                    files_scanned=1,
                    files_skipped=1,
                    inputs=_account(scanned=1, skipped=1),
                )
            )
            == "not_evaluated"
        )
