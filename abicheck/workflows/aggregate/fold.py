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
"""Fold loaded target reports into one immutable aggregate result.

This module owns compatibility, coverage, and gate reconciliation. It does not
read report files or translate CLI inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from abicheck.change_registry_types import Verdict

from .contracts import (
    _UNAFFECTED_VERDICTS,
    _VERDICT_RANK,
    AGGREGATE_SCHEMA_VERSION,
    COVERAGE_INCOMPLETE_EXIT,
    CoverageStatus,
    ProfileMatrixEntry,
    TargetReport,
)
from .matrix import (
    FindingMatrixEntry,
    build_finding_matrix,
    render_finding_matrix_lines,
)
from .reconcile import ReportFindings
from .resolve import (
    OnMissingRequired,
    OnUnexpectedTarget,
)


@dataclass(frozen=True)
class AggregateResult:
    """The finalized fan-in view over one commit's per-target reports."""

    #: Every expected target, in stable id order — analyzed or unavailable.
    targets: tuple[TargetReport, ...]
    #: Reports for ids not in the expected set (a matrix leg with no
    #: corresponding expected entry — e.g. a manifest that has not caught up to
    #: a newly-added target). Graded by ``on_unexpected_target``.
    unexpected_targets: tuple[TargetReport, ...] = ()
    on_missing_required: OnMissingRequired = OnMissingRequired.FAIL
    on_unexpected_target: OnUnexpectedTarget = OnUnexpectedTarget.INCLUDE
    #: True when the caller ran in explicit discovered-only mode (no declared
    #: expected set), so coverage is not gated.
    discovered_only: bool = False
    #: Where `on_missing_required`/`on_unexpected_target` (above) actually came
    #: from (CLI cleanup phase two, PR 2) -- ``"manifest"``/``"run-plan"`` when
    #: the expected-target source's own ``gate`` block set at least one of
    #: them, ``"default"`` otherwise (including discovered-only mode, where
    #: neither field is applicable). Purely descriptive, reported in
    #: `to_dict()`'s `effective_policy` block so a reader can tell *why* this
    #: run applied the policy it did, not just what the policy was.
    policy_source: str = "default"

    # --- membership helpers -------------------------------------------------
    @property
    def analyzed(self) -> tuple[TargetReport, ...]:
        return tuple(t for t in self.targets if t.analyzed)

    @property
    def unavailable(self) -> tuple[TargetReport, ...]:
        return tuple(t for t in self.targets if not t.analyzed)

    @property
    def _gated_unexpected(self) -> tuple[TargetReport, ...]:
        """Unexpected targets whose findings count toward the gate."""
        if self.on_unexpected_target in (
            OnUnexpectedTarget.INCLUDE,
            OnUnexpectedTarget.FAIL,
        ):
            return tuple(t for t in self.unexpected_targets if t.analyzed)
        return ()

    @property
    def _forced_gate_targets(self) -> tuple[TargetReport, ...]:
        """Unavailable targets whose report still carries a forced gate.

        A scan that aborted (budget overflow / evidence-contract violation)
        reports a real gate decision without an analyzed compatibility
        verdict (Codex review: the earlier design forced a synthetic
        ``BREAKING`` verdict for these, which invented an ABI-break result
        and an analyzed-target count for a comparison that never ran -- see
        :func:`~abicheck.workflows.aggregate.load._load_report_file`). That
        gate must still count toward the CI decision regardless of the
        target's own required/optional declaration -- the same reason
        operational-error/not-comparable reports force a blocking gate --
        so it is folded in here rather than through :attr:`required_gap`,
        which only fires for *required* targets. Respects
        ``on_unexpected_target`` for the unexpected half exactly like
        :attr:`_gated_unexpected` does, so the two stay consistent about
        which unexpected reports count at all.
        """
        candidates: tuple[TargetReport, ...] = self.targets
        if self.on_unexpected_target in (
            OnUnexpectedTarget.INCLUDE,
            OnUnexpectedTarget.FAIL,
        ):
            candidates = candidates + self.unexpected_targets
        return tuple(t for t in candidates if not t.analyzed and t.gate is not None)

    @property
    def _gated(self) -> tuple[TargetReport, ...]:
        """Every target whose own gate decision counts toward the CI exit
        code -- analyzed targets, gated unexpected targets, and unavailable
        targets carrying a :attr:`_forced_gate_targets` gate. The common
        fold shared by :meth:`exit_code`, :attr:`blocking_targets`,
        :attr:`contract_coverage_exit`, and :attr:`analysis_assurance_exit`.
        """
        return self.analyzed + self._gated_unexpected + self._forced_gate_targets

    # --- compatibility axis (reporting only) --------------------------------
    @property
    def _compat_targets(self) -> tuple[TargetReport, ...]:
        """Analyzed targets whose verdict feeds the compatibility summary.

        This is *not* just the expected analyzed targets: any unexpected
        target whose findings are gated (``--on-unexpected-target
        include``/``fail``) also contributes, so the reported compatibility can
        never say "clean" while a gated unexpected break is driving the exit
        code. Non-gated unexpected targets (``warn``/``ignore``) are excluded,
        matching :attr:`_gated_unexpected`.
        """
        return self.analyzed + self._gated_unexpected

    @property
    def compatibility_verdict(self) -> Verdict | None:
        verdicts = [
            t.compatibility_verdict
            for t in self._compat_targets
            if t.compatibility_verdict is not None
        ]
        if not verdicts:
            return None
        return max(verdicts, key=lambda v: _VERDICT_RANK[v])

    # --- coverage axis ------------------------------------------------------
    @property
    def required_gap(self) -> bool:
        """A *required* target that did not report — the coverage fail signal."""
        return any(not t.analyzed and t.required for t in self.targets)

    @property
    def coverage(self) -> CoverageStatus:
        if not self.required_gap:
            return CoverageStatus.COMPLETE
        return CoverageStatus.EMPTY if not self.analyzed else CoverageStatus.PARTIAL

    @property
    def missing_required(self) -> tuple[str, ...]:
        return tuple(t.target_id for t in self.targets if not t.analyzed and t.required)

    @property
    def coverage_blocking(self) -> bool:
        return (
            self.required_gap
            and self.on_missing_required is OnMissingRequired.FAIL
            and not self.discovered_only
        )

    # --- gate axis (the CI decision) ----------------------------------------
    @property
    def blocking_targets(self) -> tuple[str, ...]:
        gated = list(self._gated)
        return tuple(
            sorted(
                t.target_id
                for t in gated
                if t.gate is not None and (t.gate.exit_code > 0 or t.gate.blocking)
            )
        )

    @property
    def contract_coverage_exit(self) -> int:
        """ADR-049 Phase 7's contract-coverage contribution for the whole set.

        The max of every gated target's own contribution, folded into
        :meth:`exit_code` the same way and for the same reason the two CLIs
        fold theirs (``contract_coverage_exit.fold_coverage_exit``): a ledger
        that gates ``compare`` and ``scan --against`` but not the command that
        aggregates their reports is exactly the cross-command divergence plan
        Section 6.4 forbids — a matrix build could exit ``0`` while a target
        in it exited ``1`` for incomplete contract evidence.

        ``max`` over targets rather than "any", so the axis stays a floor
        rather than a count, matching the per-command fold.
        """
        gated = list(self._gated)
        return max((t.contract_coverage_exit for t in gated), default=0)

    @property
    def contract_coverage_targets(self) -> tuple[str, ...]:
        """Targets whose contract coverage was incomplete -- the *why*
        behind :attr:`contract_coverage_exit`, but also every target that
        accepted incomplete coverage via ``contract.unresolved=warn``
        (contribution ``0``, failures still listed): ADR-049 Section 6.2
        accepts incomplete assurance without hiding it, and deriving this
        from the contribution alone hid it (Codex review). Which of these
        gated stays readable per target from its own ``contract_coverage_exit``.
        """
        gated = list(self._gated)
        return tuple(
            sorted(
                t.target_id
                for t in gated
                if t.contract_coverage_incomplete or t.contract_coverage_exit > 0
            )
        )

    @property
    def analysis_assurance_exit(self) -> int:
        """P0.4's analysis-assurance contribution -- sibling of
        :attr:`contract_coverage_exit`. Without this a target whose
        ``--require-complete-analysis`` gate raised its own exit to ``1``
        fed this aggregate a green ``0`` (Codex review)."""
        gated = list(self._gated)
        return max((t.analysis_assurance_exit for t in gated), default=0)

    @property
    def analysis_assurance_targets(self) -> tuple[str, ...]:
        """Targets short of analysis assurance -- the *why* behind
        :attr:`analysis_assurance_exit`."""
        gated = list(self._gated)
        return tuple(
            sorted(t.target_id for t in gated if t.analysis_assurance_exit > 0)
        )

    @property
    def scope_completeness_exit(self) -> int:
        """ADR-065's scope-completeness contribution -- the third orthogonal
        floor, sibling of the two above. Without it a release that exited
        ``1`` for an incomplete scope under ``--on-incomplete-scope block``
        (or for completing no comparison) fed this aggregate a green ``0``,
        since ``run_outcome.gate``/``operational`` stay ``none`` for that
        axis (Codex review)."""
        return max((t.scope_completeness_exit for t in self._gated), default=0)

    @property
    def scope_completeness_targets(self) -> tuple[str, ...]:
        """Targets whose comparison scope gated -- the *why* behind
        :attr:`scope_completeness_exit`."""
        return tuple(
            sorted(t.target_id for t in self._gated if t.scope_completeness_exit > 0)
        )

    def exit_code(self) -> int:
        """The single CI gate exit code.

        The max of every gated target's own ``severity.exit_code`` (so a
        target's policy-blocked addition contributes ``1``, an API break ``2``,
        an ABI break ``4`` — never recomputed from the verdict), a coverage
        contribution of ``1`` when a required target is missing, ADR-049's
        orthogonal contract-coverage contribution of ``1`` when any target's
        selected contract domain was short of evidence
        (:attr:`contract_coverage_exit`), and P0.4's orthogonal
        analysis-assurance contribution of ``1`` when any target's evidence
        was incomplete under ``--require-complete-analysis``
        (:attr:`analysis_assurance_exit`), and ADR-065's scope-completeness
        contribution (:attr:`scope_completeness_exit`). All fold with
        ``max``, so every ``1``-valued axis can raise a clean ``0`` and none
        can lower a real break's ``2``/``4``. ``64`` / malformed-input errors
        are raised as :class:`AggregateError`, never returned here.
        """
        gated = list(self._gated)
        code = max((t.gate.exit_code for t in gated if t.gate is not None), default=0)
        if self.coverage_blocking:
            code = max(code, COVERAGE_INCOMPLETE_EXIT)
        code = max(code, self.contract_coverage_exit)
        code = max(code, self.analysis_assurance_exit)
        code = max(code, self.scope_completeness_exit)
        # ``fail`` fails the gate on *any* unexpected report — including one that
        # is unreadable/verdictless (so has no gate to contribute above) — since
        # the policy is "no target outside the expected set is tolerated".
        if (
            self.on_unexpected_target is OnUnexpectedTarget.FAIL
            and self.unexpected_targets
        ):
            code = max(code, COVERAGE_INCOMPLETE_EXIT)
        return code

    @property
    def passed(self) -> bool:
        return self.exit_code() == 0

    # --- profile matrix (status-review item 5) ------------------------------
    @property
    def profile_matrix(self) -> tuple[ProfileMatrixEntry, ...]:
        """One entry per :attr:`TargetReport.base_target` (the same logical
        target checked under different profiles); only ``check_id``-shaped
        targets participate, and ``unexpected_targets`` are never grouped.
        A profile's several checks (different channels/depths) combine
        worst-verdict-wins; an unavailable *required* check surfaces in
        ``incomplete_profiles`` rather than being dropped, an optional one
        does not (consistent with :attr:`coverage`); a compatible verdict
        under a still-blocking gate is ``affected``; a profile with zero
        analyzed checks lands in ``unanalyzed_profiles``, never "clean"
        (all Codex review).
        """
        by_target = self._reports_by_target_and_profile()

        entries = []
        for base_target in sorted(by_target):
            reports_by_profile = by_target[base_target]
            profiles = tuple(sorted(reports_by_profile))
            affected = []
            incomplete = []
            unanalyzed = []
            contract_incomplete = []
            analysis_incomplete = []
            scope_incomplete = []
            verdict_by_profile: dict[str, str | None] = {}
            for pid in profiles:
                reports = reports_by_profile[pid]
                if any(r.compatibility_verdict is None and r.required for r in reports):
                    incomplete.append(pid)
                # Same predicate as `contract_coverage_targets`, and checked
                # before the `continue` below: a profile can be short of
                # contract evidence whether or not any of its checks
                # produced a verdict.
                if any(
                    r.contract_coverage_incomplete or r.contract_coverage_exit > 0
                    for r in reports
                ):
                    contract_incomplete.append(pid)
                # Sibling check for P0.4's own orthogonal axis (Codex
                # review): a COMPATIBLE profile short on analysis-assurance
                # evidence still needs profile-level attribution here.
                if any(r.analysis_assurance_exit > 0 for r in reports):
                    analysis_incomplete.append(pid)
                if any(r.scope_completeness_exit > 0 for r in reports):
                    scope_incomplete.append(pid)
                verdicts = [
                    r.compatibility_verdict
                    for r in reports
                    if r.compatibility_verdict is not None
                ]
                if not verdicts:
                    verdict_by_profile[pid] = None
                    unanalyzed.append(pid)
                    continue
                worst = max(verdicts, key=lambda v: _VERDICT_RANK[v])
                verdict_by_profile[pid] = worst.value
                gate_blocking = any(
                    r.gate is not None and r.gate.blocking for r in reports
                )
                if worst not in _UNAFFECTED_VERDICTS or gate_blocking:
                    affected.append(pid)
            entries.append(
                ProfileMatrixEntry(
                    base_target=base_target,
                    profiles=profiles,
                    affected_profiles=tuple(affected),
                    incomplete_profiles=tuple(incomplete),
                    unanalyzed_profiles=tuple(unanalyzed),
                    contract_incomplete_profiles=tuple(contract_incomplete),
                    analysis_incomplete_profiles=tuple(analysis_incomplete),
                    scope_incomplete_profiles=tuple(scope_incomplete),
                    verdict_by_profile=verdict_by_profile,
                )
            )
        return tuple(entries)

    def _reports_by_target_and_profile(
        self,
    ) -> dict[str, dict[str, list[TargetReport]]]:
        """``base_target -> profile_id -> that profile's reports``.

        Shared by :attr:`profile_matrix` and :attr:`finding_matrix` so both
        views group identically — a target present in one and absent from
        the other would be a contradiction, not two opinions.
        """
        grouped: dict[str, dict[str, list[TargetReport]]] = {}
        for t in self.targets:
            pid = t.profile_id
            if pid is None:
                continue
            grouped.setdefault(t.base_target, {}).setdefault(pid, []).append(t)
        return grouped

    # --- finding matrix (G34 Phase D) ---------------------------------------
    @property
    def finding_matrix(self) -> tuple[FindingMatrixEntry, ...]:
        """Every distinct finding across the profiles of each logical target,
        with its own affected/unaffected/undetermined profile lists.

        Where :attr:`profile_matrix` reconciles *verdicts* per profile, this
        reconciles the individual findings behind them, keyed by
        :func:`~abicheck.aggregate_findings.resolve_report_change_identity`.
        Same participation rule as :attr:`profile_matrix`: only targets whose
        ``target_id`` is ``check_id``-shaped (so a profile can be parsed out)
        are grouped, and ``unexpected_targets`` are excluded. Empty in the
        common single-profile case, which is not an error.

        A profile is *affected* when any of its own reports carries the
        finding; *undetermined* when it is not affected but at least one of
        its reports fell short of a complete finding set (:class:`~abicheck.
        aggregate_findings.ReportFindings`); *unaffected* only when every one
        of its reports enumerated its findings in full and none of them was
        this one. The reconciliation rules themselves live in
        :func:`~abicheck.aggregate_findings.build_finding_matrix`; this
        property only projects this result's ``TargetReport`` grouping down
        to the plain per-check finding sets that function takes.

        An *unavailable* report contributes ``None`` (unknown) even if it
        carried a ``changes`` array: a verdictless or synthetic-verdict
        (not-comparable, operational-error) leg describes a comparison that
        never reached a conclusion, so its finding list cannot clear a
        profile of anything.
        """
        return build_finding_matrix(
            {
                base_target: {
                    pid: [
                        report.findings
                        if report.analyzed and report.findings is not None
                        else ReportFindings()
                        for report in reports
                    ]
                    for pid, reports in reports_by_profile.items()
                }
                for base_target, reports_by_profile in (
                    self._reports_by_target_and_profile().items()
                )
            }
        )

    # --- rendering ----------------------------------------------------------
    def render_text(self) -> str:
        required_total = sum(1 for t in self.targets if t.required)
        required_analyzed = sum(1 for t in self.targets if t.required and t.analyzed)

        lines: list[str] = []
        header = "Passed" if self.passed else "Failed"
        cov = {
            CoverageStatus.COMPLETE: "complete",
            CoverageStatus.PARTIAL: "partial",
            CoverageStatus.EMPTY: "no coverage",
        }[self.coverage]
        lines.append(f"ABI aggregate gate: {header} (coverage: {cov})")
        if not self.discovered_only:
            lines.append(
                f"Analyzed {required_analyzed} of {required_total} required targets"
            )
        lines.append("")

        for target in self.targets:
            lines.append("  " + self._render_target_line(target))
        for extra in self.unexpected_targets:
            lines.append("  " + self._render_target_line(extra))

        lines.append("")
        lines.append("Compatibility:")
        lines.append("  " + self._render_compatibility_line())

        lines.extend(self._render_contract_coverage_lines())
        lines.extend(self._render_analysis_assurance_lines())
        lines.extend(self._render_scope_completeness_lines())
        lines.extend(self._render_profile_matrix_lines())
        lines.extend(render_finding_matrix_lines(self.finding_matrix))

        lines.extend(self._render_coverage_and_gate_lines())

        return "\n".join(lines)

    def _render_contract_coverage_lines(self) -> list[str]:
        """The ADR-049 contract-coverage block, empty when every domain closed.

        Named separately from the required-target coverage line for the same
        reason the JSON block is: a bare exit 1 with neither of them explained
        is the failure this axis most easily causes.
        """
        out: list[str] = []
        # Named separately from the required-target coverage line above for
        # the same reason the JSON block is: a bare exit 1 with neither of
        # them explained is the failure this axis most easily causes.
        incomplete = self.contract_coverage_targets
        if incomplete:
            out.append("")
            out.append("Contract coverage:")
            # The contribution is stated separately from the target list
            # rather than folded into one clause: with `contract.unresolved=
            # warn` a listed target contributes 0, so "incomplete on X —
            # contributes 0" would read as a contradiction rather than as the
            # acceptance it is.
            out.append(f"  incomplete on {', '.join(incomplete)}")
            accepted = tuple(
                t.target_id
                for t in list(self.analyzed) + list(self._gated_unexpected)
                if t.contract_coverage_incomplete
                and t.contract_coverage_exit == 0
                and t.contract_coverage_declared
            )
            if accepted:
                # States what is observable, not the policy behind it. A
                # target listing failures while contributing 0 can have got
                # there by `contract.unresolved=warn` *or* by `gate-mode:
                # advisory` neutralization (`buildsource.check_report.
                # _neutralize_gate`), and this side cannot tell them apart --
                # both look like "declared 0 with failures listed". Naming
                # one of them was wrong for the other (Codex review).
                out.append(
                    f"  not gated on {', '.join(sorted(accepted))} "
                    "(that run contributed 0; listed, not gated)"
                )
            out.append(
                f"  contributes {self.contract_coverage_exit} to the exit code "
                "(ADR-049 contract-coverage axis)"
            )

        return out

    @staticmethod
    def _render_floor_axis_lines(
        title: str, incomplete: tuple[str, ...], contribution: int, axis: str
    ) -> list[str]:
        """One plain satisfied/not exit-floor axis (analysis assurance,
        scope completeness) -- unlike contract coverage, neither has an
        "accepted, listed but not gated" state, so only the incomplete
        targets and the contribution are worth stating."""
        if not incomplete:
            return []
        return [
            "",
            f"{title}:",
            f"  incomplete on {', '.join(incomplete)}",
            f"  contributes {contribution} to the exit code ({axis})",
        ]

    def _render_analysis_assurance_lines(self) -> list[str]:
        """P0.4's analysis-assurance block."""
        return self._render_floor_axis_lines(
            "Analysis assurance",
            self.analysis_assurance_targets,
            self.analysis_assurance_exit,
            "P0.4 analysis-assurance axis",
        )

    def _render_scope_completeness_lines(self) -> list[str]:
        """ADR-065's scope-completeness block."""
        return self._render_floor_axis_lines(
            "Comparison scope",
            self.scope_completeness_targets,
            self.scope_completeness_exit,
            "ADR-065 scope-completeness axis",
        )

    def _render_profile_matrix_lines(self) -> list[str]:
        """The per-base-target profile matrix, empty when no profiles ran."""
        out: list[str] = []
        matrix = self.profile_matrix
        if matrix:
            out.append("")
            out.append("Profile matrix:")
            for entry in matrix:
                out.append(self._render_profile_entry_line(entry))

        return out

    def _render_profile_entry_line(self, entry: ProfileMatrixEntry) -> str:
        """One base target's row in the profile matrix.

        Four mutually exclusive shapes -- affected, clean everywhere, partly
        clean with some profile never producing an analyzed result, and
        nothing analyzed at all -- then two independent suffixes that qualify
        whichever shape was chosen.
        """
        unanalyzed = entry.unanalyzed_profiles
        if entry.affected_profiles:
            line = (
                f"  {entry.base_target}: affected on "
                f"{', '.join(entry.affected_profiles)} "
                f"(checked on {', '.join(entry.profiles)})"
            )
            if unanalyzed:
                # An affected profile and an unanalyzed one can
                # coexist on the same target -- don't let "checked
                # on" imply the unanalyzed one produced a result too
                # (Codex review).
                line += f"; no analyzed result on {', '.join(unanalyzed)}"
        elif not unanalyzed:
            line = (
                f"  {entry.base_target}: clean on all checked profiles "
                f"({', '.join(entry.profiles)})"
            )
        elif len(unanalyzed) < len(entry.profiles):
            # Some profiles are clean, others never produced an
            # analyzed result at all -- never call the latter
            # "clean" (Codex review).
            clean = [p for p in entry.profiles if p not in unanalyzed]
            line = (
                f"  {entry.base_target}: clean on {', '.join(clean)} "
                f"(checked on {', '.join(entry.profiles)}); "
                f"no analyzed result on {', '.join(unanalyzed)}"
            )
        else:
            line = (
                f"  {entry.base_target}: no analyzed result on any "
                f"checked profile ({', '.join(entry.profiles)})"
            )
        if entry.incomplete_profiles:
            line += f" [incomplete coverage on {', '.join(entry.incomplete_profiles)}]"
        if entry.contract_incomplete_profiles:
            # Qualifies whatever precedes it, exactly as the
            # incomplete-coverage suffix above does -- including a
            # "clean" line, which stays accurate: clean is a
            # statement about compatibility, and this is the
            # orthogonal evidence axis saying the domain never
            # closed. Without it a profile that raised the exit to 1
            # on contract coverage alone read as flatly clean.
            line += (
                f" [contract evidence incomplete on "
                f"{', '.join(entry.contract_incomplete_profiles)}]"
            )
        if entry.analysis_incomplete_profiles:
            # The exact sibling suffix, for the exact sibling reason (Codex
            # review): a profile that raised the exit to 1 purely on the
            # analysis-assurance axis must not read as flatly clean either.
            line += (
                f" [analysis assurance incomplete on "
                f"{', '.join(entry.analysis_incomplete_profiles)}]"
            )
        if entry.scope_incomplete_profiles:
            line += (
                f" [scope incomplete on {', '.join(entry.scope_incomplete_profiles)}]"
            )
        return line

    def _render_coverage_and_gate_lines(self) -> list[str]:
        """The closing Coverage: and Gate: blocks."""
        out: list[str] = []
        out.append("Coverage:")
        if self.coverage is CoverageStatus.COMPLETE:
            out.append("  Complete — every required target was analyzed.")
        else:
            missing = ", ".join(self.missing_required) or "(none)"
            gated = "" if self.coverage_blocking else " (advisory)"
            out.append(f"  Incomplete — required target(s) unknown: {missing}.{gated}")

        out.append("Gate:")
        if self.passed:
            # Deliberately does NOT claim "coverage complete": under
            # --on-missing-required warn a required gap is reported above but
            # does not fail the gate, so a passing result can still have an
            # (advisory) coverage gap.
            out.append(
                "  Passed — no gate-blocking findings under the configured policies."
            )
        else:
            blockers = ", ".join(self.blocking_targets) or "(none)"
            parts = [f"exit {self.exit_code()}"]
            if self.blocking_targets:
                parts.append(f"blocking: {blockers}")
            if self.coverage_blocking:
                parts.append("required coverage incomplete")
            if (
                self.on_unexpected_target is OnUnexpectedTarget.FAIL
                and self.unexpected_targets
            ):
                ids = ", ".join(t.target_id for t in self.unexpected_targets)
                parts.append(f"unexpected target(s) present: {ids}")
            out.append("  Failed — " + "; ".join(parts) + ".")

        return out

    def _render_target_line(self, t: TargetReport) -> str:
        tag = "" if t.required else " (optional)"
        if t.unexpected:
            tag = " (unexpected)"
        if not t.analyzed:
            forced_gate = ""
            if t.gate is not None and (t.gate.blocking or t.gate.exit_code > 0):
                # A forced-gate-but-unavailable target (a scan abort) still
                # gates the CI decision even though nothing was compared --
                # say so here too, not only in the JSON `gate` block, so the
                # text rendering doesn't read as a plain coverage gap.
                cats = ", ".join(t.gate.blocking_categories)
                forced_gate = f" [gate: blocking{f' ({cats})' if cats else ''}]"
            return (
                f"{t.target_id}{tag}: ⚠ unavailable — "
                f"{t.reason or 'no report'}{forced_gate}"
            )
        assert t.compatibility_verdict is not None
        verdict = t.compatibility_verdict.value
        if t.gate is not None and t.gate.blocking:
            cats = ", ".join(t.gate.blocking_categories)
            gate = f" [gate: blocking{f' ({cats})' if cats else ''}]"
            # An analyzed-but-synthetic verdict (not_comparable/operational_error)
            # carries its own explanatory reason, unlike a genuine break — surface
            # it here so "BREAKING" is never the whole story for one of these.
            if t.reason:
                gate += f" — {t.reason}"
        else:
            gate = ""
        return f"{t.target_id}{tag}: {verdict}{gate}"

    def _render_compatibility_line(self) -> str:
        verdict = self.compatibility_verdict
        if verdict is None:
            return "No targets were analyzed — no compatibility verdict."
        if verdict is Verdict.COMPATIBLE_WITH_RISK:
            risky = sorted(
                t.target_id
                for t in self._compat_targets
                if t.compatibility_verdict is Verdict.COMPATIBLE_WITH_RISK
            )
            return f"No ABI regressions; compatible-with-risk on: {', '.join(risky)}."
        rank = _VERDICT_RANK[verdict]
        if rank <= _VERDICT_RANK[Verdict.COMPATIBLE]:
            return "No ABI regressions in the analyzed targets."
        by_verdict = []
        for v in (Verdict.BREAKING, Verdict.API_BREAK):
            hits = sorted(
                t.target_id
                for t in self._compat_targets
                if t.compatibility_verdict is v
            )
            if hits:
                by_verdict.append(f"{v.value} on: {', '.join(hits)}")
        return "; ".join(by_verdict) + "."

    def to_dict(self) -> dict[str, Any]:
        verdict = self.compatibility_verdict
        required_total = sum(1 for t in self.targets if t.required)
        required_analyzed = sum(1 for t in self.targets if t.required and t.analyzed)
        return {
            "aggregate_schema_version": AGGREGATE_SCHEMA_VERSION,
            "status": "pass" if self.passed else "fail",
            "compatibility": {
                "verdict": verdict.value if verdict is not None else None,
                "analyzed_targets": len(self._compat_targets),
            },
            "coverage": {
                "status": self.coverage.value,
                "required_targets": required_total,
                "analyzed_required_targets": required_analyzed,
                "missing_required_targets": list(self.missing_required),
                "blocking": self.coverage_blocking,
            },
            "gate": {
                "passed": self.passed,
                "exit_code": self.exit_code(),
                "blocking_targets": list(self.blocking_targets),
                "coverage_blocking": self.coverage_blocking,
            },
            # The three orthogonal floors (ADR-049 Phase 7 contract coverage,
            # P0.4 analysis assurance, ADR-065 scope completeness) are their
            # own blocks, not folded into "coverage" above: each answers a
            # different question and each contributes 1, so a consumer must
            # be able to tell which axis raised the exit (plan Section 7).
            "contract_coverage": {
                "exit_contribution": self.contract_coverage_exit,
                "incomplete_targets": list(self.contract_coverage_targets),
            },
            "analysis_assurance": {
                "exit_contribution": self.analysis_assurance_exit,
                "incomplete_targets": list(self.analysis_assurance_targets),
            },
            "scope_completeness": {
                "exit_contribution": self.scope_completeness_exit,
                "incomplete_targets": list(self.scope_completeness_targets),
            },
            # CLI cleanup phase two, PR 2: the resolved gate policy this run
            # actually applied, and where it came from -- expectation and the
            # consequence of breaking it are now one versioned contract
            # (the manifest's/run-plan's own `gate` block) rather than two
            # separately-typed CLI flags, so the report states which policy
            # value was live for this run instead of leaving a reader to
            # infer it from `blocking`/`exit_code` alone.
            "effective_policy": {
                "missing_required": self.on_missing_required.value,
                "unexpected_target": self.on_unexpected_target.value,
                "source": self.policy_source,
            },
            "targets": [t.to_dict() for t in self.targets],
            "unexpected_targets": [t.to_dict() for t in self.unexpected_targets],
            "profile_matrix": [e.to_dict() for e in self.profile_matrix],
            "finding_matrix": [e.to_dict() for e in self.finding_matrix],
        }


# --- parsing / loading ------------------------------------------------------
