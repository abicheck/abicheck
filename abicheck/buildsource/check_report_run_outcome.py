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

"""ADR-063 Phase 7's ``run_outcome`` block for ``check_report.py``'s three
synthetic report builders, plus the legacy-report backfill ``augment_
report`` needs before it stamps an older report with the current schema
version.

Split out of ``check_report.py`` purely to keep that already-near-the-cap
module from growing (mirrors ``check_report_exit_backfill.py``'s own
split-for-size precedent) -- each of ``build_operational_error_report()``/
``build_bootstrap_report()``/``build_new_target_report()`` represents a run
that never computed a real compatibility verdict at all, so
``compatibility``/``assurance``/``gate`` all stay at their honest "nothing
computed" values and only the one axis the builder actually represents
(``operational`` for a resolve-baseline failure, ``lifecycle`` for a
bootstrap/new-target pass) is populated.
"""

from __future__ import annotations

from typing import Any

from ..policy.outcome import (
    OperationalStatus,
    PolicyGateDecision,
    RunOutcome,
    TargetLifecycle,
    policy_gate_decision_for_exit_code,
    run_outcome_dict_for_release,
    run_outcome_dict_for_scan,
    worst_real_verdict,
)

__all__ = ["backfill_run_outcome", "synthetic_run_outcome"]

#: The shared 0/1/2/4 compatibility-gate exit scheme `policy.outcome.
#: _GATE_EXIT_CODE`'s values are drawn from (not itself importable -- it's
#: a private module constant) -- an `exit.compatibility_contribution` value
#: outside this set is exactly as untrustworthy as a missing/non-int one.
_VALID_COMPAT_CONTRIBUTION = frozenset({0, 1, 2, 4})


def _is_declared_positive_flag(value: object) -> bool:
    """Whether *value* is a genuinely-declared ``1`` for one of the ``exit``
    block's 0/1 orthogonal-axis contribution fields (e.g. ``not_comparable_
    contribution``/``operational_error_contribution``) -- ``False`` for a
    missing key, a malformed/non-int value, a `bool`, or a declared ``0``.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


#: `scan`'s own legacy top-level exit scheme (AGENTS.md's exit-code table:
#: 0 compatible, 2 API break, 4 ABI break, 5 budget overflow, 6 not
#: comparable) -- the exact `int` domain a pre-severity-scheme scan report's
#: root `exit_code` may legitimately hold. A value outside this set is
#: exactly as untrustworthy as a missing/non-int one (Codex review, fresh
#: evidence): `run_outcome_for_scan_fields` itself already silently floors
#: an out-of-scheme `compat_exit_code` to 0 (folding it into `operational`
#: instead) -- correct for a code that genuinely IS operational-only (5/6),
#: but for a bogus code like 99 it discarded a real BREAKING/API_BREAK
#: verdict's own gate contribution, backfilling `run_outcome.gate: "none"`
#: for a report whose `verdict` string says otherwise.
_VALID_LEGACY_SCAN_EXIT = frozenset({0, 2, 4, 5, 6})


def synthetic_run_outcome(
    *,
    operational: OperationalStatus = OperationalStatus.NONE,
    lifecycle: TargetLifecycle = TargetLifecycle.EXISTING,
) -> dict[str, Any]:
    return RunOutcome(
        compatibility=None,
        assurance=None,
        gate=PolicyGateDecision.NONE,
        operational=operational,
        lifecycle=lifecycle,
    ).to_dict()


def backfill_run_outcome(out: dict[str, Any]) -> None:
    """Synthesize ``run_outcome`` in place for an older report that never
    carried one, before ``check_report._stamp_schema_version`` upgrades its
    marker to the current schema (Codex review, fresh evidence) -- the
    sibling gap ``check_report_exit_backfill.backfill_exit_block_fields``
    already closes for the ``exit`` block itself. A no-op when ``out``
    already carries ``run_outcome`` (a report produced by an already-
    upgraded writer).

    Dispatches on the same report-shape markers ``check_report.
    _stamp_schema_version`` itself keys on: a scan report (``scan_schema_
    version``) reuses :func:`~abicheck.policy.outcome.run_outcome_dict_for_
    scan` exactly the way ``GateInfo.from_scan_report`` would read it back;
    a release/bundle report (``libraries``+``old_dir``, or ``libraries``+
    ``unmatched_old`` for a ``--output-dir`` summary) reuses
    :func:`~abicheck.policy.outcome.run_outcome_dict_for_release`, with its
    own ``compatibility_contribution`` fallback to ``severity.exit_code``
    or the legacy verdict mapping when the original ``exit`` block never
    carried that key (this function must run *before* ``check_report.
    augment_report``'s own call to ``backfill_exit_block_fields``, which
    would otherwise default it to 0 -- indistinguishable from a real
    confirmed-clean report -- before this function ever sees it). Its
    ``compatibility`` verdict is recovered from the worst REAL per-library
    result via :func:`~abicheck.policy.outcome.worst_real_verdict` -- the
    same sentinel-excluding precision ``cli_compare_release_helpers.
    _release_completed_compatibility_verdict`` gives a native release
    writer, reimplemented here since that helper lives in a `frontends`-
    classified module this leaf may not import -- rather than the raw
    top-level ``verdict`` alone, so a legacy release whose top-level
    verdict is the ``"ERROR"``/``"not_comparable"`` sentinel does not lose
    a real completed ``BREAKING``/``API_BREAK`` library result on upgrade
    (Codex review, fresh evidence). A pre-2.48 synthetic report (marked by
    ``operational_errors``/``baseline_bootstrap``/``baseline_new_target`` --
    ``check_report.py``'s own builders' sentinel keys, mirrored here since
    this leaf may not import that module) reuses :func:`synthetic_run_
    outcome` so its one real axis (``operational``/``lifecycle``) survives
    upgrade instead of defaulting away (Codex review, fresh evidence). A
    pre-2.48 standalone comparability refusal (``verdict: null`` plus a
    ``reason.kind`` marker -- ``report.not_comparable``'s own shape, before
    that writer carried ``run_outcome`` itself) similarly reuses
    :func:`synthetic_run_outcome` with ``operational=NOT_COMPARABLE``
    (Codex review, fresh evidence). A native compare-shaped report (none of
    the above) derives ``gate`` from whichever exit code is available
    (``severity.exit_code`` if present, else the legacy verdict mapping)
    and ``compatibility`` from ``verdict`` directly.
    """
    if "run_outcome" in out:
        return
    from ..change_registry_types import Verdict
    from ..policy.severity import legacy_exit_code

    raw_verdict = out.get("verdict")
    if "scan_schema_version" in out:
        exit_code = out.get("exit_code")
        if (
            not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or exit_code not in _VALID_LEGACY_SCAN_EXIT
        ):
            # A missing/malformed top-level `exit_code` previously defaulted
            # to 0 unconditionally -- for a legacy report whose `verdict` is
            # a real BREAKING/API_BREAK, that read as a false gate: none
            # once forwarded (`report=`'s own severity/abort readers find
            # nothing either, on a report this corrupted). Derive the
            # fallback from the real verdict instead, the same legacy-
            # verdict mapping the release/compare branches already use
            # (Codex review, fresh evidence). An out-of-scheme-but-still-int
            # code (e.g. `99`) is exactly as untrustworthy as a missing one
            # (Codex review, fresh evidence, second round): without this,
            # `run_outcome_for_scan_fields` itself floors it to a `compat_
            # exit_code` of 0 (`_GATE_EXIT_CODE.values()` membership check),
            # backfilling `run_outcome.gate: "none"` for a report whose real
            # `verdict` string still says BREAKING/API_BREAK.
            try:
                scan_verdict = (
                    Verdict(raw_verdict) if isinstance(raw_verdict, str) else None
                )
            except ValueError:
                scan_verdict = None
            exit_code = (
                legacy_exit_code(scan_verdict) if scan_verdict is not None else 0
            )
        report: dict[str, Any] = out
        if "exit" not in out:
            # `cli_scan._emit_scan_abort_report`'s own persisted JSON shape
            # (pre-1.24, before that writer carried `run_outcome` itself)
            # nests the abort's preserved exit decision under `diff.exit`,
            # not the top-level `exit` key `scan_report_abort_compatibility_
            # contribution` reads -- that top-level shape is only
            # `service_scan.ScanResult.report`'s own, typed-API-only
            # envelope (Codex review, fresh evidence: without this, a
            # legacy BUDGET_OVERFLOW/EVIDENCE_CONTRACT_ERROR report's
            # already-found ABI break was silently lost on backfill, since
            # the reader found nothing at the top-level key it expected).
            diff = out.get("diff")
            nested_exit = diff.get("exit") if isinstance(diff, dict) else None
            if isinstance(nested_exit, dict):
                report = {**out, "exit": nested_exit}
        # A pre-1.24 artifact-set (`--artifact-set`) report -- before
        # `ScanSetResult.to_dict()` carried `run_outcome` itself -- has no
        # `diff`/`exit` block at all for `report=` to read a compatibility
        # contribution out of, so a set-level BUDGET_OVERFLOW/BUNDLE_
        # INCOMPLETE would otherwise backfill to `compatibility: null` even
        # though an earlier `per_artifact` member (or the bundle audit
        # itself) already completed with a real result (Codex review, fresh
        # evidence -- the legacy-backfill sibling of the fix `ScanSetResult.
        # to_dict()`'s own `member_verdicts=` wiring already applies to a
        # *native* writer's report). `bundle_incomplete`/an `EVIDENCE_
        # CONTRACT_ERROR` member are independent operational signals that
        # survive even beside a *stronger* member's real verdict (a set
        # whose root verdict is `BREAKING` can still have gone through with
        # `bundle_incomplete: true`) -- derived the same unconditional way
        # `ScanSetResult.to_dict()`'s own call already does, not gated on
        # `verdict` naming an abort sentinel (Codex review, fresh evidence).
        member_verdicts: list[object] | None = None
        member_evidence_contract_error = False
        per_artifact = out.get("per_artifact")
        if isinstance(per_artifact, list):
            member_verdicts = [
                entry.get("verdict")
                for entry in per_artifact
                if isinstance(entry, dict)
            ]
            member_verdicts.append(out.get("bundle_verdict"))
            member_evidence_contract_error = any(
                entry.get("verdict") == "EVIDENCE_CONTRACT_ERROR"
                for entry in per_artifact
                if isinstance(entry, dict)
            )
        out["run_outcome"] = run_outcome_dict_for_scan(
            str(raw_verdict) if raw_verdict is not None else "",
            exit_code,
            report=report,
            member_verdicts=member_verdicts,
            member_evidence_contract_error=member_evidence_contract_error,
            bundle_incomplete=bool(out.get("bundle_incomplete")),
        )
        return

    compatibility: Verdict | None
    try:
        compatibility = Verdict(raw_verdict) if isinstance(raw_verdict, str) else None
    except ValueError:
        compatibility = None

    if "libraries" in out and ("old_dir" in out or "unmatched_old" in out):
        # `old_dir` alone would miss a `compare-release --output-dir`
        # summary.json (`cli_compare_release_matrix._write_release_summary_
        # file`'s own shape): it carries `libraries`/`unmatched_old` but no
        # `old_dir`/`new_dir` at all, so it previously fell through to the
        # single-compare fallback below -- discarding a completed member
        # result and the release's own operational failure alike (Codex
        # review, fresh evidence). `unmatched_old` is present in both
        # release-shaped writers' output, so the two markers together match
        # either shape without also matching an unrelated `libraries`-
        # bearing report.
        #
        # Recover the worst REAL per-library verdict (Codex review, fresh
        # evidence) rather than trusting the raw top-level `verdict` alone:
        # a legacy release whose top-level verdict is the "ERROR"/
        # "not_comparable" operational sentinel can still have a library
        # entry that completed with a real BREAKING/API_BREAK result --
        # `worst_real_verdict` silently drops any non-`Verdict` candidate
        # (the two sentinels included), so it never needs to know their
        # exact spellings. Used for both `compatibility` (below) and the
        # `compatibility_contribution` fallback (next), so the two axes
        # can't independently disagree about whether a completed result
        # exists.
        libraries = out.get("libraries")
        # `bundle_verdict`/`matrix_verdict` (Codex review, fresh evidence):
        # a completed GLOBAL bundle-audit or cross-profile matrix comparison
        # can carry a real verdict even when every per-library entry above
        # only carries the top-level ERROR/not_comparable sentinel -- omitting
        # these left `run_outcome.compatibility: null` for a report whose
        # bundle/matrix comparison genuinely completed, which then denied the
        # aggregate findings-preservation fix (see `load.py`) any non-null
        # verdict to key off.
        member_candidates: list[object] = [
            raw_verdict,
            out.get("bundle_verdict"),
            out.get("matrix_verdict"),
        ]
        if isinstance(libraries, list):
            member_candidates.extend(
                entry.get("verdict") for entry in libraries if isinstance(entry, dict)
            )
        worst_member = worst_real_verdict(member_candidates)
        release_verdict = (
            worst_member.value
            if worst_member is not None
            else (str(raw_verdict) if raw_verdict is not None else None)
        )
        # `exit`, when present, is only trusted for `compatibility_
        # contribution` if that specific key survived *and* holds a real,
        # in-scheme int -- this runs before `backfill_exit_block_fields`
        # (Codex review, fresh evidence: that backfill unconditionally
        # defaults every missing `*_contribution` key, including this one,
        # to 0, indistinguishable from a real confirmed-clean report;
        # calling this first sees the true original shape). A present-but-
        # malformed value (a string, `None`, a bool) OR an out-of-scheme
        # int (e.g. `99`) is rejected the same way a missing key is --
        # trusting either as-is would let `run_outcome_dict_for_release`'s
        # own `_int_contribution`/scheme-membership check silently
        # normalize it to `0` (gate: none), turning a legacy `BREAKING`
        # report with a corrupted `exit` block into a falsely clean target
        # instead of falling back (Codex review, fresh evidence, two
        # rounds). Falls back to `severity.exit_code`, then the legacy
        # verdict mapping over the recovered `worst_member` -- never the
        # backfilled 0 -- so a legacy BREAKING release report with no
        # exit/severity block still gets gate: abi_breaking instead of
        # silently passing aggregation as clean.
        exit_decision = out.get("exit")
        raw_contribution = (
            exit_decision.get("compatibility_contribution")
            if isinstance(exit_decision, dict)
            else None
        )
        if (
            not isinstance(exit_decision, dict)
            or not isinstance(raw_contribution, int)
            or isinstance(raw_contribution, bool)
            or raw_contribution not in _VALID_COMPAT_CONTRIBUTION
        ):
            severity = out.get("severity")
            sev_exit = severity.get("exit_code") if isinstance(severity, dict) else None
            if (
                not isinstance(sev_exit, int)
                or isinstance(sev_exit, bool)
                or sev_exit not in _VALID_COMPAT_CONTRIBUTION
            ):
                # `severity.exit_code` is just as untrustworthy as `exit.
                # compatibility_contribution` above when it's out-of-scheme
                # (Codex review, fresh evidence): forwarding e.g. `99`
                # unchecked would let `run_outcome_dict_for_release`'s own
                # scheme-membership check silently normalize it to `0`
                # (gate: none) instead of falling through to the legacy
                # verdict mapping.
                sev_exit = (
                    legacy_exit_code(worst_member) if worst_member is not None else 0
                )
            exit_decision = {
                **(exit_decision if isinstance(exit_decision, dict) else {}),
                "compatibility_contribution": sev_exit,
            }
        # Infer the operational axis from the legacy top-level/member
        # sentinels when the newer contribution keys are absent from `exit`
        # entirely (Codex review, fresh evidence): a release produced before
        # `not_comparable_contribution`/`operational_error_contribution`
        # existed can still have a top-level or member verdict of
        # `"not_comparable"`/`"ERROR"` (`_RELEASE_OPERATIONAL_SENTINELS`),
        # and forwarding that legacy `exit` block unchanged left `run_
        # outcome.operational: none` despite a library having failed or
        # refused comparison -- unlike the native writer, which always
        # derives both contributions itself. `not_comparable` takes
        # precedence, mirroring `run_outcome_dict_for_release`'s own
        # `not_comparable_contribution`-preferred-over-`operational_error_
        # contribution` order.
        # Codex review, fresh evidence, second round: key *presence* alone is
        # not enough -- a legacy/malformed `exit` block can carry `not_
        # comparable_contribution: 0` (or any other non-`1` value) even
        # though a top-level/member verdict genuinely is the sentinel, and
        # the old `"... not in exit_decision"` check skipped inference
        # entirely for that shape, letting `run_outcome_dict_for_release`
        # normalize the refusal to `operational: none`.
        if not _is_declared_positive_flag(
            exit_decision.get("not_comparable_contribution")
        ):
            if "not_comparable" in member_candidates:
                exit_decision = {**exit_decision, "not_comparable_contribution": 1}
            elif (
                not _is_declared_positive_flag(
                    exit_decision.get("operational_error_contribution")
                )
                and "ERROR" in member_candidates
            ):
                exit_decision = {**exit_decision, "operational_error_contribution": 1}
        out["run_outcome"] = run_outcome_dict_for_release(
            release_verdict,
            exit_decision,
        )
        return

    # A pre-2.48 synthetic Action report -- before `build_operational_
    # error_report`/`build_bootstrap_report`/`build_new_target_report`
    # carried `run_outcome` themselves -- never ran a real comparison at
    # all, so the ordinary-report fallback below (which derives `gate` from
    # `verdict`/`severity.exit_code`) would misread each one: a resolve-
    # baseline failure's `verdict: "ERROR"` collapses onto the identical
    # generic-error handling `libraries`-shaped reports use, and neither
    # bootstrap/new-target sentinel carries a `severity` block at all, so
    # both silently defaulted to `gate: none`/`operational: none`/
    # `lifecycle: existing` -- discarding the one axis (`operational`/
    # `lifecycle`) each report actually recorded (Codex review, fresh
    # evidence). Recognized by the same marker keys each builder writes
    # (`operational_errors`/`baseline_bootstrap`/`baseline_new_target`),
    # never by `verdict` alone, since `check_report_run_outcome.py` may not
    # import `check_report.py`'s own sentinel constants (that direction
    # would be circular -- `check_report.py` already imports this module).
    if out.get("operational_errors"):
        out["run_outcome"] = synthetic_run_outcome(
            operational=OperationalStatus.EXTRACTION_ERROR
        )
        return
    if out.get("baseline_bootstrap") is True:
        out["run_outcome"] = synthetic_run_outcome(lifecycle=TargetLifecycle.BOOTSTRAP)
        return
    if out.get("baseline_new_target") is True:
        out["run_outcome"] = synthetic_run_outcome(lifecycle=TargetLifecycle.NEW_TARGET)
        return
    # A pre-2.48 standalone comparability refusal (`report.not_comparable.
    # not_comparable_document`'s own `{"verdict": null, "reason": {"kind":
    # ..., "message": ...}}` shape, before that writer carried `run_outcome`
    # itself) reaches here too -- the generic fallback below would hard-code
    # `operational: none`, contradicting the current native writer, which
    # always records `NOT_COMPARABLE` for this exact shape (Codex review,
    # fresh evidence). Recognized by the `reason.kind` marker, since
    # `raw_verdict` is `None` for this shape either way.
    reason = out.get("reason")
    if isinstance(reason, dict) and "kind" in reason:
        out["run_outcome"] = synthetic_run_outcome(
            operational=OperationalStatus.NOT_COMPARABLE
        )
        return

    severity = out.get("severity")
    exit_code = severity.get("exit_code") if isinstance(severity, dict) else None
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        exit_code = legacy_exit_code(compatibility) if compatibility is not None else 0
    out["run_outcome"] = RunOutcome(
        compatibility=compatibility,
        # A schema 2.38-2.47 compare report already carries a completed,
        # already-serialized `analysis_assurance` block (`reporter.py`'s own
        # top-level key) -- passed through as-is rather than hard-coded to
        # `None`, so the upgraded schema-2.48 report doesn't claim `run_
        # outcome.assurance: null` alongside a contradictory non-null
        # `analysis_assurance` (Codex review, fresh evidence).
        assurance=out.get("analysis_assurance"),
        gate=policy_gate_decision_for_exit_code(exit_code),
        operational=OperationalStatus.NONE,
        lifecycle=TargetLifecycle.EXISTING,
    ).to_dict()
