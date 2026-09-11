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

"""``compare --no-baseline``'s own audit document as an ``abicheck
aggregate`` fan-in input -- a sibling of ``tests/test_aggregate.py`` split
out for the same file-size reason as its other axis-specific siblings
(``test_aggregate_scope_completeness.py``, ``test_aggregate_analysis_
assurance.py``).

Codex review, fresh evidence: ``abicheck.workflows.aggregate.load.
_load_report_file`` had no branch for this shape at all -- its always-null
``verdict`` (ADR-068 D2: an audit reports no compatibility verdict, ever)
fell through to the generic "report carried no ABI verdict" path, which
built ``gate=None``. That silently discarded a real AUDIT_GATE finding for
an optional/``on_missing_required: warn`` target (nothing blocked the
aggregate) and, for a required target, folded the completed audit into the
same plain-unavailable shape a report that never arrived gets.
"""

from __future__ import annotations

import json
from pathlib import Path

from abicheck.aggregate import ExpectedTargets, aggregate_reports_dir

LINUX = "linux-x86_64"
MACOS = "macos-arm64"


def _expect(*required: str, optional: tuple[str, ...] = ()) -> ExpectedTargets:
    return ExpectedTargets.from_lists(list(required), list(optional))


def _write_report(
    d: Path,
    target_id: str,
    verdict: str | None,
    *,
    prefix: str = "abi-report-",
    **extra,
) -> Path:
    payload: dict[str, object] = dict(extra)
    if verdict is not None:
        payload["verdict"] = verdict
    path = d / f"{prefix}{target_id}.json"
    path.write_text(json.dumps(payload))
    return path


def _write_no_baseline_report(
    d: Path,
    target_id: str,
    *,
    audit_gate_axis: int = 0,
    evidence_contract_axis: int = 0,
    findings: list | None = None,
    prefix: str = "abi-report-",
    report_target_id: str | None = None,
    operational: str | None = None,
) -> Path:
    """A real `compare --no-baseline` audit document
    (`report/no_baseline.py::_document_json`'s own shape): `verdict` is
    always JSON `null` (ADR-068 D2), and its own gate-worthy axes live
    only in `exit_axes`, not in a `severity` block or a compare-scheme
    top-level `exit_code`.

    *report_target_id*, when given, is written as the report's own
    self-identified `target_id` (a `check_id`-shaped value, for
    `profile_matrix` tests) -- otherwise the report carries none and the
    aggregate falls back to the filename-derived *target_id*.

    *operational*, when given, is written as `run_outcome.operational`
    (e.g. `"evidence_contract_error"`) -- a real operational failure this
    audit's own producer recorded, as opposed to the default clean/absent
    case."""
    exit_axes = {
        "audit_gate": audit_gate_axis,
        "contract_coverage": 0,
        "analysis_assurance": 0,
        "evidence_contract": evidence_contract_axis,
        "incomplete_scope": 0,
        "no_comparison_completed": 0,
    }
    payload: dict[str, object] = {
        "audit_report_schema_version": "1.3",
        "no_baseline": True,
        "library": "libfoo.so",
        "verdict": None,
        "changes": [],
        "findings": findings or [],
        "exit_axes": exit_axes,
        "exit_code": max(exit_axes.values()),
        "contract_coverage_exit_contribution": 0,
        "policy": "strict_abi",
    }
    if operational is not None:
        payload["run_outcome"] = {"operational": operational}
    if report_target_id is not None:
        payload["target_id"] = report_target_id
    path = d / f"{prefix}{target_id}.json"
    path.write_text(json.dumps(payload))
    return path


class TestNoBaselineAuditGate:
    def test_gating_audit_blocks_at_exit_1(self, tmp_path: Path):
        # AUDIT_GATE floors onto COVERAGE_INCOMPLETE_EXIT (1), not the
        # audit's own raw exit 3 -- GateInfo's exit_code is documented as
        # compare's 0/1/2/4 scheme, and 3 is not a member of it.
        _write_no_baseline_report(tmp_path, LINUX, audit_gate_axis=3)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.exit_code() == 1
        assert LINUX in r.blocking_targets
        assert r.targets[0].gate is not None
        assert r.targets[0].gate.blocking is True
        assert r.targets[0].gate.blocking_categories == ("audit_gate",)

    def test_gating_audit_on_optional_target_still_blocks(self, tmp_path: Path):
        # The bug's sharpest case: an optional (or on_missing_required:
        # warn) target's real gate must not be silently swallowed just
        # because the report carries no compatibility verdict.
        _write_no_baseline_report(tmp_path, MACOS, audit_gate_axis=3)
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, optional=(MACOS,)))
        assert r.exit_code() == 1
        assert MACOS in r.blocking_targets

    def test_evidence_contract_error_axis_also_blocks(self, tmp_path: Path):
        _write_no_baseline_report(tmp_path, LINUX, evidence_contract_axis=7)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.exit_code() == 1
        assert r.targets[0].gate is not None
        assert r.targets[0].gate.blocking_categories == ("evidence_contract_error",)

    def test_clean_audit_with_no_gating_axis_does_not_block(self, tmp_path: Path):
        _write_no_baseline_report(tmp_path, LINUX)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].gate is not None
        assert r.targets[0].gate.blocking is False
        assert r.targets[0].gate.exit_code == 0

    def test_audit_never_carries_a_compatibility_verdict(self, tmp_path: Path):
        # ADR-068 D2: an audit reports no additions, removals, or
        # compatibility verdict at all, gating or not -- ``verdict`` must
        # stay ``None``, never a fabricated ``Verdict`` member.
        _write_no_baseline_report(tmp_path, LINUX, audit_gate_axis=3)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].compatibility_verdict is None

    def test_audit_findings_are_parsed_as_a_complete_empty_change_set(
        self, tmp_path: Path
    ):
        # `changes` is always `[]` for this shape -- a real, complete fact
        # (an audit has no compatibility changes to report), not an
        # unknown one.
        _write_no_baseline_report(tmp_path, LINUX)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].findings is not None
        assert r.targets[0].findings.complete is True
        assert r.targets[0].findings.findings == ()

    def test_a_clean_required_audit_is_analyzed_not_empty_coverage(
        self, tmp_path: Path
    ):
        """Codex review, fresh evidence: `TargetReport.analyzed` used to be
        `compatibility_verdict is not None` alone, and an audit's
        verdict is *always* `None` by design (ADR-068 D2) -- indistinguishable
        from a report that never arrived. A clean, completed, required audit
        must read as analyzed and the aggregate's coverage must be
        `complete`, not `CoverageStatus.EMPTY`."""
        _write_no_baseline_report(tmp_path, LINUX)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].analyzed is True
        assert r.targets[0].compatibility_verdict is None  # still no fabricated verdict
        assert r.exit_code() == 0
        coverage = r.to_dict()["coverage"]
        assert coverage["status"] == "complete"
        assert coverage["analyzed_required_targets"] == 1
        assert coverage["missing_required_targets"] == []

    def test_audit_candidate_side_findings_reach_the_target_report(
        self, tmp_path: Path
    ):
        """Codex review, fresh evidence: the audit's real candidate-side
        findings (the ones that produced AUDIT_GATE) live in a root-level
        `findings` array, an entirely different shape from the always-empty
        `changes`. Discarding them broke cross-profile reconciliation and
        display -- a reader could never see what actually gated."""
        _write_no_baseline_report(
            tmp_path,
            LINUX,
            audit_gate_axis=3,
            findings=[
                {
                    "kind": "symbol_removed",
                    "symbol": "libfoo_free",
                    "description": "removed",
                    "severity": "breaking",
                }
            ],
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].findings is not None
        assert len(r.targets[0].findings.findings) == 1
        finding = r.targets[0].findings.findings[0]
        assert finding.kind == "symbol_removed"
        assert finding.symbol == "libfoo_free"


class TestNoBaselineAuditTextRenderingAndProfileMatrix:
    """Codex review, third round, fresh evidence: `TargetReport.analyzed`
    is widened for a completed no-baseline audit, but several *consumers*
    of `analyzed` still assumed it implies a non-null `compatibility_
    verdict` -- `_render_target_line()`'s `assert t.compatibility_verdict
    is not None` crashed `abicheck aggregate --format text` outright for
    any such target (including the text invocation `check-project.yml`
    itself runs), and `profile_matrix` labelled the same completed audit
    both `incomplete` and `unanalyzed`, indistinguishable from a report
    that never arrived."""

    def test_format_text_does_not_crash_on_a_completed_audit(self, tmp_path: Path):
        _write_no_baseline_report(tmp_path, LINUX)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        text = r.render_text()
        assert "no compatibility verdict" in text
        assert LINUX in text

    def test_format_text_shows_a_gating_audits_blocking_gate(self, tmp_path: Path):
        _write_no_baseline_report(tmp_path, LINUX, audit_gate_axis=3)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        text = r.render_text()
        assert "gate: blocking" in text
        assert "audit_gate" in text

    def test_profile_matrix_does_not_mark_a_completed_audit_incomplete_or_unanalyzed(
        self, tmp_path: Path
    ):
        tid = f"{LINUX}@profileA#release@headers"
        _write_no_baseline_report(
            tmp_path, LINUX, prefix=f"abi-report-{LINUX}-", report_target_id=tid
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(tid))
        assert len(r.profile_matrix) == 1
        entry = r.profile_matrix[0]
        assert entry.incomplete_profiles == ()
        assert entry.unanalyzed_profiles == ()
        assert entry.verdict_by_profile == {"profileA": None}

    def test_profile_matrix_marks_a_gating_audit_affected(self, tmp_path: Path):
        tid = f"{LINUX}@profileA#release@headers"
        _write_no_baseline_report(
            tmp_path,
            LINUX,
            prefix=f"abi-report-{LINUX}-",
            report_target_id=tid,
            audit_gate_axis=3,
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(tid))
        entry = r.profile_matrix[0]
        assert entry.affected_profiles == ("profileA",)
        assert entry.incomplete_profiles == ()
        assert entry.unanalyzed_profiles == ()

    def test_profile_matrix_still_marks_a_genuinely_missing_required_report_incomplete(
        self, tmp_path: Path
    ):
        """Regression guard for the fix's own scope: a required profile
        whose report genuinely never arrived -- not a completed audit --
        must still land in `incomplete_profiles`, unaffected by the
        `completed_without_compatibility_verdict` carve-out."""
        tid = f"{LINUX}@profileA#release@headers"
        r = aggregate_reports_dir(tmp_path, expected=_expect(tid))
        entry = r.profile_matrix[0]
        assert entry.incomplete_profiles == ("profileA",)
        assert entry.unanalyzed_profiles == ("profileA",)


class TestNoBaselineAuditOperationalFailure:
    """Codex review, fourth round, fresh evidence: an audit that pinned an
    evidence contract it could not satisfy (`run_outcome.operational:
    evidence_contract_error`, exit 7) produced no valid analysis at all --
    it must never read as a completed audit just because a gate axis
    happened to be clean. `check_report._classify_verdict` already checks
    the identical `run_outcome.operational` signal before exempting a
    no-baseline report from operational-error status; `_load_report_file`'s
    own no-baseline branch had no matching check at all."""

    def test_evidence_contract_operational_failure_is_not_analyzed(
        self, tmp_path: Path
    ):
        _write_no_baseline_report(
            tmp_path, LINUX, operational="evidence_contract_error"
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].analyzed is False
        assert r.targets[0].completed_without_compatibility_verdict is False
        assert r.targets[0].reason == "audit did not complete: evidence_contract_error"

    def test_evidence_contract_operational_failure_leaves_coverage_incomplete(
        self, tmp_path: Path
    ):
        _write_no_baseline_report(
            tmp_path, LINUX, operational="evidence_contract_error"
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        coverage = r.to_dict()["coverage"]
        assert coverage["status"] != "complete"
        assert LINUX in coverage["missing_required_targets"]

    def test_a_clean_operational_status_still_counts_as_a_completed_audit(
        self, tmp_path: Path
    ):
        # "none" (and the field's own absence, covered by every other test
        # in this file) must not be mistaken for a real failure.
        _write_no_baseline_report(tmp_path, LINUX, operational="none")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].analyzed is True
        assert r.targets[0].completed_without_compatibility_verdict is True
        assert r.targets[0].reason is None

    def test_operational_failure_still_reports_a_gating_axis(self, tmp_path: Path):
        # An operational failure and a real gating finding are different,
        # non-exclusive facts -- the AUDIT_GATE axis must still gate even
        # though the audit itself never completed.
        _write_no_baseline_report(
            tmp_path,
            LINUX,
            audit_gate_axis=3,
            operational="evidence_contract_error",
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.exit_code() == 1
        assert r.targets[0].analyzed is False


class TestNoBaselineAuditOnlyProfileMatrix:
    """Codex review, fourth round, fresh evidence: `profile_matrix` folded a
    profile whose only reports were completed-but-verdict-less audits into
    "clean on all checked profiles" -- a compatibility claim this shape
    never actually makes (ADR-068 D2). `ProfileMatrixEntry.audit_only_
    profiles` and `_render_profile_entry_line` now keep it distinct from
    both a genuinely clean profile and an unanalyzed one."""

    def test_audit_only_clean_profile_is_not_folded_into_clean(self, tmp_path: Path):
        tid = f"{LINUX}@profileA#release@headers"
        _write_no_baseline_report(
            tmp_path, LINUX, prefix=f"abi-report-{LINUX}-", report_target_id=tid
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(tid))
        entry = r.profile_matrix[0]
        assert entry.audit_only_profiles == ("profileA",)
        assert entry.unanalyzed_profiles == ()
        assert entry.incomplete_profiles == ()

    def test_audit_only_clean_profile_renders_as_audit_only_not_clean(
        self, tmp_path: Path
    ):
        tid = f"{LINUX}@profileA#release@headers"
        _write_no_baseline_report(
            tmp_path, LINUX, prefix=f"abi-report-{LINUX}-", report_target_id=tid
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(tid))
        text = r.render_text()
        assert "audit-only (no compatibility verdict) on all checked profiles" in text
        assert "clean on all checked profiles" not in text

    def test_mixed_clean_and_audit_only_profiles_keeps_clean_narrow(
        self, tmp_path: Path
    ):
        tid_a = f"{LINUX}@profileA#release@headers"
        tid_b = f"{LINUX}@profileB#release@headers"
        _write_no_baseline_report(
            tmp_path, LINUX, prefix=f"abi-report-{LINUX}-a-", report_target_id=tid_a
        )
        _write_report(tmp_path, tid_b, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(tid_a, tid_b))
        entry = r.profile_matrix[0]
        assert entry.audit_only_profiles == ("profileA",)
        assert "profileB" not in entry.audit_only_profiles
        text = r.render_text()
        assert "clean on profileB" in text
        assert "audit-only (no compatibility verdict) on profileA" in text

    def test_audit_only_profile_still_marked_affected_when_gate_blocks(
        self, tmp_path: Path
    ):
        # audit_only and affected are orthogonal, non-exclusive facts: a
        # gating audit is both "affected" (it blocked) and made no
        # compatibility claim at all.
        tid = f"{LINUX}@profileA#release@headers"
        _write_no_baseline_report(
            tmp_path,
            LINUX,
            prefix=f"abi-report-{LINUX}-",
            report_target_id=tid,
            audit_gate_axis=3,
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(tid))
        entry = r.profile_matrix[0]
        assert entry.affected_profiles == ("profileA",)
        assert entry.audit_only_profiles == ("profileA",)

    def test_audit_only_profiles_in_to_dict(self, tmp_path: Path):
        tid = f"{LINUX}@profileA#release@headers"
        _write_no_baseline_report(
            tmp_path, LINUX, prefix=f"abi-report-{LINUX}-", report_target_id=tid
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(tid))
        entry_dict = r.profile_matrix[0].to_dict()
        assert entry_dict["audit_only_profiles"] == ["profileA"]


class TestNoBaselineAuditCompatibilityAnalyzedCount:
    """Codex review, fifth round, fresh evidence: `AggregateResult.to_dict()`
    counted a completed-but-verdict-less audit toward
    `compatibility.analyzed_targets`, via `len(self._compat_targets)` --
    that set is scoped for *gate* participation and widened to include such
    audits when `TargetReport.analyzed` was widened. The public JSON then
    read `{"verdict": null, "analyzed_targets": 1}`, a completed audit
    dressed up as an analyzed-but-verdictless compatibility result. Only a
    target that actually produced a `compatibility_verdict` belongs in this
    axis's own count."""

    def test_a_clean_audit_alone_reports_zero_analyzed_compatibility_targets(
        self, tmp_path: Path
    ):
        _write_no_baseline_report(tmp_path, LINUX)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        d = r.to_dict()
        assert d["compatibility"]["verdict"] is None
        assert d["compatibility"]["analyzed_targets"] == 0

    def test_a_real_verdict_still_counts_alongside_an_audit(self, tmp_path: Path):
        macos_tid = f"{MACOS}@profileA#release@headers"
        linux_tid = f"{LINUX}@profileA#release@headers"
        _write_no_baseline_report(
            tmp_path,
            MACOS,
            prefix=f"abi-report-{MACOS}-",
            report_target_id=macos_tid,
        )
        _write_report(tmp_path, linux_tid, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(macos_tid, linux_tid))
        d = r.to_dict()
        # The one real verdict counts; the audit-only completion does not.
        assert d["compatibility"]["analyzed_targets"] == 1
