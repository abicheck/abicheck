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
"""Execute aggregate report fan-in from a resolved target contract.

The canonical entry point is :func:`aggregate_reports_dir`. Resolution and
folding remain separately owned so frontends do not reproduce either decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .contracts import DEFAULT_REPORT_PREFIX, AggregateError, TargetReport
from .fold import AggregateResult
from .load import _load_report_file, _LoadedReport
from .resolve import (
    ExpectedTargets,
    OnMissingRequired,
    OnUnexpectedTarget,
    resolve_gate_policy,
)


def aggregate(
    expected: ExpectedTargets | None,
    found: Mapping[str, _LoadedReport],
    *,
    on_missing_required: OnMissingRequired | None = None,
    on_unexpected_target: OnUnexpectedTarget | None = None,
    policy_source_hint: str = "manifest",
) -> AggregateResult:
    """Reconcile an expected-target set against the reports found.

    *expected* is ``None`` only in discovered-only mode, where the reports
    present *are* the expected set and coverage is not gated.

    *on_missing_required*/*on_unexpected_target* default to ``None``, meaning
    "resolve via :func:`resolve_gate_policy`" (the manifest's own ``gate``
    block, falling back to the hard-coded default) rather than to a fixed
    enum value -- a caller that passes an explicit value here still forces
    it, same as before this function grew manifest-awareness.
    """
    discovered_only = expected is None
    on_missing_required, on_unexpected_target, policy_source = resolve_gate_policy(
        expected,
        explicit_missing_required=on_missing_required,
        explicit_unexpected_target=on_unexpected_target,
        source_hint=policy_source_hint,
    )
    if expected is None:
        expected = ExpectedTargets(targets={tid: True for tid in found}, head_sha=None)

    def _target(tid: str, required: bool, unexpected: bool) -> TargetReport:
        report = found.get(tid)
        if report is None:
            return TargetReport(
                target_id=tid,
                required=required,
                compatibility_verdict=None,
                reason="no report was produced for this expected target",
                unexpected=unexpected,
            )
        # Commit-identity guard, opt-in via the manifest's own ``head_sha``.
        # When the manifest pins a commit, a report is only current if it
        # carries a *matching* head_sha: a mismatch is a superseded run, and a
        # *missing* head_sha is unverifiable (a delayed artifact from an older
        # run without identity metadata must not slip through). Both are
        # unavailable — fail closed.
        if expected.head_sha is not None and report.head_sha != expected.head_sha:
            why = (
                f"report is for a different commit ({report.head_sha})"
                if report.head_sha is not None
                else "report carries no head_sha; cannot confirm it is for commit "
                f"{expected.head_sha}"
            )
            return TargetReport(
                target_id=tid,
                required=required,
                compatibility_verdict=None,
                report_path=str(report.path),
                library=report.library,
                reason=why,
                unexpected=unexpected,
            )
        return TargetReport(
            target_id=tid,
            required=required,
            compatibility_verdict=report.verdict,
            gate=report.gate,
            report_path=str(report.path),
            library=report.library,
            reason=report.reason,
            unexpected=unexpected,
            contract_coverage_exit=report.contract_coverage_exit,
            contract_coverage_incomplete=report.contract_coverage_incomplete,
            contract_coverage_declared=report.contract_coverage_declared,
            analysis_assurance_exit=report.analysis_assurance_exit,
            findings=report.findings,
            effective_config_digest=report.effective_config_digest,
        )

    targets = tuple(
        _target(tid, expected.targets[tid], unexpected=False)
        for tid in sorted(expected.targets)
    )

    unexpected_targets: tuple[TargetReport, ...] = ()
    if on_unexpected_target is not OnUnexpectedTarget.IGNORE:
        unexpected_targets = tuple(
            _target(tid, required=False, unexpected=True)
            for tid in sorted(set(found) - set(expected.targets))
        )

    return AggregateResult(
        targets=targets,
        unexpected_targets=unexpected_targets,
        on_missing_required=on_missing_required,
        on_unexpected_target=on_unexpected_target,
        discovered_only=discovered_only,
        # `resolve_gate_policy` was called above against the ORIGINAL
        # `expected` (before the discovered-only substitution just below its
        # call), so it already reads "default" here -- discovered-only mode
        # has no expected-target source to carry a `gate` block at all.
        policy_source=policy_source,
    )


def collect_reports(
    reports_dir: Path, *, prefix: str = DEFAULT_REPORT_PREFIX
) -> dict[str, _LoadedReport]:
    """Load every ``*.json`` report in *reports_dir*, keyed by target id.

    A missing directory is treated as zero reports (a full build outage must
    still produce a coverage result, not a usage error). Two reports resolving
    to the *same* target id are a hard :class:`AggregateError` — silently
    dropping one on a CI gate is unacceptable.
    """
    found: dict[str, _LoadedReport] = {}
    if not reports_dir.is_dir():
        return found
    for path in sorted(reports_dir.glob("*.json")):
        report = _load_report_file(path, prefix=prefix)
        if report.target_id in found:
            raise AggregateError(
                f"duplicate target id {report.target_id!r}: both "
                f"{found[report.target_id].path.name} and {path.name} resolve to "
                "it — give each target a unique report/artifact name"
            )
        found[report.target_id] = report
    return found


# --- the aggregation itself -------------------------------------------------
#
# ExpectedTargets (the manifest input) and resolve_gate_policy live in
# resolve.py. The root aggregate_manifest.py path is a compatibility facade,
# never an internal dependency.


def aggregate_reports_dir(
    reports_dir: Path,
    *,
    expected: ExpectedTargets | None = None,
    discovered_only: bool = False,
    on_missing_required: OnMissingRequired | None = None,
    on_unexpected_target: OnUnexpectedTarget | None = None,
    policy_source_hint: str = "manifest",
    prefix: str = DEFAULT_REPORT_PREFIX,
) -> AggregateResult:
    """Load a reports dir and aggregate against an expected set.

    Exactly one of *expected* or *discovered_only* selects the mode. In
    discovered-only mode the reports present become the expected set and
    coverage is not gated — the caller must opt into that explicitly, since it
    cannot detect a missing target. Raises :class:`AggregateError` for
    malformed input (a usage error, exit 64).

    *on_missing_required*/*on_unexpected_target* default to ``None`` (resolve
    via *expected*'s own manifest ``gate`` block, falling back to the
    hard-coded default -- see :func:`resolve_gate_policy`); an explicit value
    here still forces it. *policy_source_hint* names which expected-target
    source *expected* came from (``"manifest"``/``"run-plan"``), reported
    back in the result's ``effective_policy.source`` when that source's
    ``gate`` block actually supplied a value.
    """
    if discovered_only and expected is not None:
        # Exactly one mode — never silently drop the expected set (which would
        # disable its required-coverage and commit-identity checks).
        raise AggregateError(
            "expected targets and discovered-only mode are mutually exclusive"
        )
    if not discovered_only and expected is None:
        raise AggregateError(
            "no expected-target set: pass a manifest / expected targets, or "
            "opt into discovered-only mode explicitly"
        )
    found = collect_reports(reports_dir, prefix=prefix)
    return aggregate(
        None if discovered_only else expected,
        found,
        on_missing_required=on_missing_required,
        on_unexpected_target=on_unexpected_target,
        policy_source_hint=policy_source_hint,
    )
