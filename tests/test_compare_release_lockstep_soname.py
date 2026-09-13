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

"""Tests for `_suppress_lockstep_soname_findings` (the coordinated-release
`SONAME_BUMP_UNNECESSARY` suppression) and its disposition-audit/suppression-
trail bookkeeping.

Split out of `test_compare_release.py` (a debt.yaml `no_growth`-tracked
module) purely to keep that file under the AI-readiness `file-size` gate's
2000-line hard cap -- this pair of classes is fully self-contained (their
own `_entry`/`_entry_with_real_ledger` helpers, no shared module-level
fixtures from the parent file beyond stdlib `json`) and was the newest/most
independently-addable block at the time of the split, mirroring
`test_compare_release_parallel_dedup.py`'s own identical split rationale.
"""

from __future__ import annotations

import json


class TestLockstepSonameCoupling:
    """A coordinated lockstep SONAME bump across a multi-library release should
    not be flagged 'unnecessary' on members that had no break of their own when
    a sibling/dependency genuinely broke (real-world: oneDAL bumps every
    libonedal* SONAME together because libonedal_core breaks)."""

    @staticmethod
    def _entry(lib, changes, verdict):
        from abicheck.checker import DiffResult

        result = DiffResult(
            old_version="1",
            new_version="2",
            library=lib,
            changes=changes,
            verdict=verdict,
        )
        return {
            "library": lib,
            "verdict": verdict.value,
            "breaking": len(result.breaking),
            "source_breaks": len(result.source_breaks),
            "risk_changes": len(result.risk),
            "compatible_additions": len(result.compatible),
            "_diff_result": result,
        }

    def test_suppressed_when_sibling_breaks(self):
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry(
            "libonedal.so",
            [
                Change(ChangeKind.SONAME_BUMP_UNNECESSARY, "DT_SONAME", "bump"),
            ],
            Verdict.COMPATIBLE,
        )
        core = self._entry(
            "libonedal_core.so",
            [Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            Verdict.BREAKING,
        )
        n = _suppress_lockstep_soname_findings([umbrella, core], "BREAKING", None)
        assert n == 1
        kinds = [c.kind for c in umbrella["_diff_result"].changes]
        assert ChangeKind.SONAME_BUMP_UNNECESSARY not in kinds
        assert umbrella["compatible_additions"] == 0

    def test_kept_when_no_real_break_in_release(self):
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry(
            "libonedal.so",
            [
                Change(ChangeKind.SONAME_BUMP_UNNECESSARY, "DT_SONAME", "bump"),
            ],
            Verdict.COMPATIBLE,
        )
        n = _suppress_lockstep_soname_findings([umbrella], "COMPATIBLE", None)
        assert n == 0
        kinds = [c.kind for c in umbrella["_diff_result"].changes]
        assert ChangeKind.SONAME_BUMP_UNNECESSARY in kinds

    def test_kept_when_release_worst_is_source_only_api_break(self):
        # A SONAME bump is only justified by a *binary* ABI break; a source-only
        # API_BREAK elsewhere must NOT suppress the relink-forcing warning.
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry(
            "libonedal.so",
            [
                Change(ChangeKind.SONAME_BUMP_UNNECESSARY, "DT_SONAME", "bump"),
            ],
            Verdict.COMPATIBLE,
        )
        n = _suppress_lockstep_soname_findings([umbrella], "API_BREAK", None)
        assert n == 0
        kinds = [c.kind for c in umbrella["_diff_result"].changes]
        assert ChangeKind.SONAME_BUMP_UNNECESSARY in kinds

    def test_rewrites_per_library_json(self, tmp_path):
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry(
            "libonedal.so.3",
            [
                Change(ChangeKind.SONAME_BUMP_UNNECESSARY, "DT_SONAME", "bump"),
            ],
            Verdict.COMPATIBLE,
        )
        core = self._entry(
            "libonedal_core.so.3",
            [Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            Verdict.BREAKING,
        )
        _suppress_lockstep_soname_findings([umbrella, core], "BREAKING", tmp_path)
        data = json.loads((tmp_path / "libonedal.so.json").read_text())
        assert all(c["kind"] != "soname_bump_unnecessary" for c in data["changes"])


class TestLockstepSonameSuppressionUpdatesDispositionAudit:
    """Codex review, fresh evidence ("Record lockstep SONAME suppression
    before folding audits"): the umbrella library's own ``disposition_audit``
    is stamped (by ``_compare_one_library``) before the release-wide
    ``worst_verdict`` this suppression depends on is even known -- without
    also updating that audit here, the report hides the ``SONAME_BUMP_
    UNNECESSARY`` finding while the audit keeps classifying it ``non_gating``
    with no suppression rule recorded at all."""

    @staticmethod
    def _entry_with_real_ledger(lib: str, verdict: object) -> dict[str, object]:
        """Like ``TestLockstepSonameCoupling._entry``, but with a real,
        finalized ``disposition_ledger`` attached (mirroring what a real
        ``checker.compare()`` run produces) -- the synthetic ledger-less
        fixture that class uses can't exercise this fix at all, since
        ``supersede_as_suppressed`` is a no-op without a real ledger to
        supersede."""
        from abicheck.checker import Change, ChangeKind, DiffResult
        from abicheck.policy.disposition_close import finalize_ledger
        from abicheck.policy.disposition_ledger import DispositionLedger
        from abicheck.report.disposition_audit import compute_disposition_audit

        change = Change(ChangeKind.SONAME_BUMP_UNNECESSARY, "DT_SONAME", "bump")
        result = DiffResult(
            old_version="1",
            new_version="2",
            library=lib,
            changes=[change],
            verdict=verdict,
        )
        ledger = DispositionLedger()
        finalize_ledger(ledger, result)
        result.disposition_ledger = ledger
        return {
            "library": lib,
            "verdict": verdict.value,
            "breaking": len(result.breaking),
            "source_breaks": len(result.source_breaks),
            "risk_changes": len(result.risk),
            "compatible_additions": len(result.compatible),
            "_diff_result": result,
            # Stamped ahead of the suppression, exactly as
            # `_compare_one_library` does in the real pipeline.
            "disposition_audit": compute_disposition_audit(result).to_dict(),
        }

    def test_suppressed_finding_moves_to_the_suppressed_disposition(self) -> None:
        from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry_with_real_ledger("libonedal.so", Verdict.COMPATIBLE)
        # Before suppression: the finding is detected and labelled
        # non_gating, the disposition a compatible/quality-kind finding
        # gets by default.
        before = umbrella["disposition_audit"]
        assert before["detected_total"] == 1
        assert before["counts"]["non_gating"] == 1
        assert before["counts"]["suppressed"] == 0

        core_result = DiffResult(
            old_version="1",
            new_version="2",
            library="libonedal_core.so",
            changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            verdict=Verdict.BREAKING,
        )
        core = {
            "library": "libonedal_core.so",
            "verdict": "BREAKING",
            "breaking": 1,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
            "_diff_result": core_result,
        }
        n = _suppress_lockstep_soname_findings([umbrella, core], "BREAKING", None)
        assert n == 1

        after = umbrella["disposition_audit"]
        # detected_total is unchanged -- the finding is still an observed
        # detection, D3's "record before disposing" rule -- but it moved
        # from non_gating to suppressed, and a rule now names why.
        assert after["detected_total"] == 1
        assert after["counts"]["non_gating"] == 0
        assert after["counts"]["suppressed"] == 1
        assert len(after["rules"]) == 1
        assert after["rules"][0]["matched_count"] == 1

    def test_suppressed_finding_survives_in_the_result_suppression_fields(
        self,
    ) -> None:
        """Codex review, fresh evidence, follow-up ("Preserve lockstep
        findings in the suppression trail"): the disposition ledger/audit
        is one view of a suppression -- `DiffResult.suppressed_changes`/
        `suppressed_count` (what `to_json()`'s own `suppression` block
        reads) is a second, independent one, and the two must agree.
        Without this, `--output-dir`'s rewritten per-library JSON reported
        `suppression.suppressed_count: 0` and an empty `suppressed_changes`
        list for a library whose `disposition_audit` said one finding was
        suppressed."""
        from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry_with_real_ledger("libonedal.so", Verdict.COMPATIBLE)
        umbrella_result = umbrella["_diff_result"]
        assert isinstance(umbrella_result, DiffResult)
        assert umbrella_result.suppressed_count == 0
        assert umbrella_result.suppressed_changes == []

        core_result = DiffResult(
            old_version="1",
            new_version="2",
            library="libonedal_core.so",
            changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            verdict=Verdict.BREAKING,
        )
        core = {
            "library": "libonedal_core.so",
            "verdict": "BREAKING",
            "breaking": 1,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
            "_diff_result": core_result,
        }
        n = _suppress_lockstep_soname_findings([umbrella, core], "BREAKING", None)
        assert n == 1

        assert umbrella_result.suppressed_count == 1
        assert len(umbrella_result.suppressed_changes) == 1
        assert (
            umbrella_result.suppressed_changes[0].kind
            == ChangeKind.SONAME_BUMP_UNNECESSARY
        )
        # The finding left `result.changes` (so it no longer double-counts
        # as an active finding) but is preserved in the audit trail, not
        # dropped outright.
        assert umbrella_result.changes == []

    def test_release_level_fold_reflects_the_updated_audit(self) -> None:
        """The release-wide `release_disposition_audit_block` fold reads
        each library's already-stamped `disposition_audit` -- if this
        library's own entry weren't updated, the release-level total would
        still show the finding as non_gating too."""
        from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
        from abicheck.cli_compare_receipt import release_disposition_audit_block
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry_with_real_ledger("libonedal.so", Verdict.COMPATIBLE)
        core_result = DiffResult(
            old_version="1",
            new_version="2",
            library="libonedal_core.so",
            changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            verdict=Verdict.BREAKING,
        )
        core = {
            "library": "libonedal_core.so",
            "verdict": "BREAKING",
            "breaking": 1,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
            "_diff_result": core_result,
            "disposition_audit": {
                "detected_total": 1,
                "effective_total": 1,
                "counts": {"gating": 1},
            },
        }
        _suppress_lockstep_soname_findings([umbrella, core], "BREAKING", None)
        folded = release_disposition_audit_block([umbrella, core])
        assert folded["counts"]["non_gating"] == 0
        assert folded["counts"]["suppressed"] == 1
        assert folded["counts"]["gating"] == 1
        assert folded["detected_total"] == 2
