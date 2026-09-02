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

"""ADR-063 Phase 7 follow-up (Codex review, fresh evidence): `augment_report`
must synthesize `run_outcome` for an older report that never carried one,
before `_stamp_schema_version` claims the current schema for it --
`check_report_exit_backfill.backfill_exit_block_fields` already closes this
gap for the `exit` block; `check_report_run_outcome.backfill_run_outcome`
is its `run_outcome` sibling.

Split out as its own module (rather than added to `tests/test_check_report.py`
or `tests/test_run_outcome.py`, both debt-tracked/no-growth) mirroring
`tests/test_check_report_exit_backfill.py`'s own precedent for the identical
reason.
"""

from __future__ import annotations

from abicheck.buildsource.check_report import augment_report


def _augment(report: dict[str, object]) -> dict[str, object]:
    return augment_report(
        report,
        name="libfoo",
        profile_id="p",
        baseline_channel="c",
        requested_depth="headers",
        gate_mode="local",
    )


def test_old_compare_report_gets_backfilled_run_outcome() -> None:
    report = {
        "verdict": "BREAKING",
        "severity": {
            "exit_code": 4,
            "blocking": True,
            "blocking_categories": ["abi_breaking"],
        },
        "library": "libfoo.so",
    }
    out = _augment(report)
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "BREAKING"
    assert run_outcome["gate"] == "abi_breaking"
    assert run_outcome["operational"] == "none"
    # The input report is never mutated (augment_report's own stated contract).
    assert "run_outcome" not in report


def test_old_compare_report_without_severity_derives_gate_from_legacy_verdict() -> None:
    report = {"verdict": "API_BREAK", "library": "libfoo.so"}
    out = _augment(report)
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "API_BREAK"
    # No severity.exit_code available -> falls back to the legacy verdict->exit mapping.
    assert run_outcome["gate"] == "potential_breaking"


def test_old_compare_report_preserves_its_existing_analysis_assurance_block() -> None:
    """Codex review, fresh evidence: a schema 2.38-2.47 compare report
    already carries a completed, already-serialized top-level
    analysis_assurance block (reporter.py's own key) -- hard-coding
    run_outcome.assurance to None during backfill would claim assurance:
    null alongside a contradictory non-null analysis_assurance in the
    same upgraded report."""
    report = {
        "verdict": "COMPATIBLE",
        "analysis_assurance": {"level": "high", "reasons": []},
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["assurance"] == {"level": "high", "reasons": []}


def test_old_scan_report_gets_backfilled_run_outcome() -> None:
    report = {"scan_schema_version": "1.21", "verdict": "BREAKING", "exit_code": 4}
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "BREAKING"
    assert run_outcome["gate"] == "abi_breaking"


def test_old_scan_abort_report_reads_the_nested_diff_exit_shape() -> None:
    """Codex review (P2), fresh evidence: cli_scan._emit_scan_abort_report's
    own persisted JSON (pre-1.24, before that writer carried run_outcome
    itself) nests the abort's preserved exit decision under diff.exit, not
    the top-level exit key service_scan.ScanResult.report uses -- without
    reading that legacy shape too, a pre-existing BUDGET_OVERFLOW report's
    already-found ABI break was silently lost on backfill (gate: none
    instead of abi_breaking)."""
    report = {
        "scan_schema_version": "1.23",
        "verdict": "BUDGET_OVERFLOW",
        "exit_code": 5,
        "diff": {"exit": {"code": 5, "compatibility_contribution": 4}},
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["gate"] == "abi_breaking"
    assert run_outcome["operational"] == "budget_overflow"


def test_old_scan_set_report_recovers_a_completed_member_verdict_from_budget_overflow() -> (
    None
):
    """Codex review, fresh evidence: a pre-1.24 `--artifact-set` scan report
    (before ScanSetResult.to_dict() carried run_outcome itself) has no
    diff/exit block for backfill_run_outcome's report= reader to find a
    compatibility contribution in -- so a set-level BUDGET_OVERFLOW/
    BUNDLE_INCOMPLETE erased a real, already-completed per_artifact member
    result (compatibility: null) unless the member/bundle verdicts are
    recovered from the legacy per_artifact/bundle_verdict envelope, the
    same way ScanSetResult.to_dict()'s own member_verdicts= wiring already
    does for a native writer."""
    report = {
        "scan_schema_version": "1.23",
        "verdict": "BUDGET_OVERFLOW",
        "exit_code": 5,
        "per_artifact": [
            {"artifact": "a.so", "verdict": "COMPATIBLE_WITH_RISK", "exit_code": 0},
            {"artifact": "b.so", "verdict": "BUDGET_OVERFLOW", "exit_code": 5},
        ],
        "bundle_verdict": None,
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "COMPATIBLE_WITH_RISK"
    assert run_outcome["operational"] == "budget_overflow"


def test_old_scan_set_report_carries_bundle_incomplete_beside_a_real_break() -> None:
    """Codex review, fresh evidence: a pre-1.24 artifact-set report whose
    root verdict is a real BREAKING (not an abort sentinel) can still have
    gone through with bundle_incomplete: true -- the independent bundle-
    audit failure must survive backfill beside the stronger compatibility
    gate, the same way ScanSetResult.to_dict()'s own unconditional
    bundle_incomplete= wiring already preserves it for a native writer."""
    report = {
        "scan_schema_version": "1.23",
        "verdict": "BREAKING",
        "exit_code": 4,
        "per_artifact": [{"artifact": "a.so", "verdict": "BREAKING", "exit_code": 4}],
        "bundle_verdict": None,
        "bundle_incomplete": True,
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "BREAKING"
    assert run_outcome["operational"] == "extraction_error"


def test_old_scan_set_report_carries_evidence_contract_error_member() -> None:
    """Sibling of the bundle_incomplete case above: an EVIDENCE_CONTRACT_
    ERROR member's own operational signal must survive backfill beside a
    different, stronger member's real BREAKING verdict."""
    report = {
        "scan_schema_version": "1.23",
        "verdict": "BREAKING",
        "exit_code": 4,
        "per_artifact": [
            {"artifact": "a.so", "verdict": "BREAKING", "exit_code": 4},
            {"artifact": "b.so", "verdict": "EVIDENCE_CONTRACT_ERROR", "exit_code": 1},
        ],
        "bundle_verdict": None,
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "BREAKING"
    assert run_outcome["operational"] == "evidence_contract_error"


def test_old_operational_error_report_preserves_extraction_error() -> None:
    """Codex review, fresh evidence: a pre-2.48 resolve-baseline-failure
    report (build_operational_error_report's own shape) has no severity
    block, so the ordinary-report fallback previously derived gate: none
    from the legacy verdict mapping of "ERROR" (unrecognized -> compatibility
    stays None, exit_code defaults to 0) and discarded the operational axis
    the report actually recorded."""
    report = {
        "verdict": "ERROR",
        "operational_errors": [{"kind": "no_credentials", "message": "boom"}],
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] is None
    assert run_outcome["operational"] == "extraction_error"
    assert run_outcome["gate"] == "none"


def test_old_bootstrap_report_preserves_lifecycle() -> None:
    report = {"verdict": "NO_BASELINE", "baseline_bootstrap": True}
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["lifecycle"] == "bootstrap"
    assert run_outcome["operational"] == "none"


def test_old_new_target_report_preserves_lifecycle() -> None:
    report = {"verdict": "NEW_TARGET", "baseline_new_target": True}
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["lifecycle"] == "new_target"


def test_old_release_report_gets_backfilled_run_outcome() -> None:
    report = {
        "libraries": [{"name": "a", "verdict": "BREAKING"}],
        "old_dir": "/old",
        "new_dir": "/new",
        "verdict": "BREAKING",
        "exit": {},
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "BREAKING"


def test_legacy_release_report_with_no_exit_or_severity_block_gets_a_real_gate() -> (
    None
):
    """CodeRabbit review, fresh evidence: augment_report previously ran
    backfill_exit_block_fields before backfill_run_outcome, so a legacy
    release report with no exit block at all (pre-2.41) had one
    unconditionally synthesized with every *_contribution defaulted to 0
    -- indistinguishable from a real, confirmed-clean release -- before
    backfill_run_outcome ever saw it. A BREAKING release report with
    neither an exit nor a severity block must still get gate: abi_breaking
    (the legacy verdict mapping), not none."""
    report = {
        "libraries": [{"name": "a", "verdict": "BREAKING"}],
        "old_dir": "/old",
        "new_dir": "/new",
        "verdict": "BREAKING",
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "BREAKING"
    assert run_outcome["gate"] == "abi_breaking"


def test_legacy_release_report_recovers_a_completed_library_verdict_from_an_error_sentinel() -> (
    None
):
    """Codex review, fresh evidence: a legacy release whose top-level
    verdict is the "ERROR" operational sentinel (one library failed to
    dump/extract/compare) can still have a DIFFERENT library that
    completed with a real BREAKING result. Passing the sentinel straight
    to run_outcome_dict_for_release would erase that completed result
    (compatibility: null) even though it actually ran -- the backfill must
    recover it from out["libraries"] the same way a native release writer
    does via cli_compare_release_helpers._release_completed_compatibility_
    verdict."""
    report = {
        "libraries": [
            {"name": "a", "verdict": "ERROR"},
            {"name": "b", "verdict": "BREAKING"},
        ],
        "old_dir": "/old",
        "new_dir": "/new",
        "verdict": "ERROR",
        "exit": {"operational_error_contribution": 1},
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "BREAKING"
    assert run_outcome["gate"] == "abi_breaking"
    assert run_outcome["operational"] == "extraction_error"


def test_legacy_release_report_with_only_sentinel_libraries_stays_unknown() -> None:
    """The sibling case: every library is an operational sentinel and no
    bundle/matrix result exists either -- compatibility must stay
    unknown (null), never fall back to the top-level "ERROR" string nor
    a false "NO_CHANGE" floor."""
    report = {
        "libraries": [{"name": "a", "verdict": "ERROR"}],
        "old_dir": "/old",
        "new_dir": "/new",
        "verdict": "ERROR",
        "exit": {"operational_error_contribution": 1},
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] is None
    assert run_outcome["operational"] == "extraction_error"


def test_legacy_release_report_prefers_a_real_severity_exit_code() -> None:
    report = {
        "libraries": [{"name": "a", "verdict": "COMPATIBLE_WITH_RISK"}],
        "old_dir": "/old",
        "new_dir": "/new",
        "verdict": "COMPATIBLE_WITH_RISK",
        "severity": {
            "exit_code": 1,
            "blocking": True,
            "blocking_categories": ["quality_issues"],
        },
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["gate"] == "addition_quality"


def test_legacy_release_report_rejects_a_malformed_compatibility_contribution() -> None:
    """Codex review, fresh evidence: a present-but-malformed exit.
    compatibility_contribution (a string here) was previously trusted as
    authoritative just because the key survived -- run_outcome_dict_for_
    release's own _int_contribution then silently normalized it to 0
    (gate: none), turning a legacy BREAKING report with a corrupted exit
    block into a falsely clean target instead of falling back to the
    legacy verdict mapping."""
    report = {
        "libraries": [{"name": "a", "verdict": "BREAKING"}],
        "old_dir": "/old",
        "new_dir": "/new",
        "verdict": "BREAKING",
        "exit": {"compatibility_contribution": "not-a-number"},
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["gate"] == "abi_breaking"


def test_legacy_release_report_rejects_an_out_of_range_compatibility_contribution() -> (
    None
):
    """Codex review, fresh evidence beyond the malformed-value fix above:
    an integer outside the 0/1/2/4 compatibility-gate scheme (99 here)
    still passed the isinstance(int) check and was forwarded as-is --
    run_outcome_dict_for_release's own scheme-membership check then
    silently normalized it to 0 (gate: none), turning a legacy BREAKING
    report with a corrupted exit block into a falsely clean target."""
    report = {
        "libraries": [{"name": "a", "verdict": "BREAKING"}],
        "old_dir": "/old",
        "new_dir": "/new",
        "verdict": "BREAKING",
        "exit": {"compatibility_contribution": 99},
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["gate"] == "abi_breaking"


def test_report_already_carrying_run_outcome_is_left_untouched() -> None:
    sentinel = {
        "schema_version": "1.0",
        "compatibility": "BREAKING",
        "gate": "abi_breaking",
        "assurance": None,
        "operational": "none",
        "lifecycle": "existing",
    }
    report = {"verdict": "BREAKING", "run_outcome": dict(sentinel)}
    out = _augment(dict(report))
    assert out["run_outcome"] == sentinel
