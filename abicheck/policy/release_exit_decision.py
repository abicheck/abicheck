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

"""Turning a release's *reported* values into ADR-064's resolved contributions.

:mod:`abicheck.policy.exit_decision_precedence` owns the precedence
arithmetic -- which axis dominates which, given contributions that are
already resolved. This module owns the other half: a directory/package
``compare`` does not *have* resolved contributions, it has the values it
reports (a ``worst_verdict`` string that may be an operational sentinel, a
per-member ``library_results`` list, an optional aggregated severity code),
and something has to derive one from the other.

Split out of that module (PR #1195) when this PR's own additions took it
past `architecture/modules.yaml`'s 800-line production maximum. The split is
along a real seam rather than a convenient line number: everything here
reads *release-shaped reported input*, and everything left there is pure
arithmetic over already-resolved integers. It is also the half both
consumers now share -- ``frontends.cli.runtime._exit_compare_release`` for
the process status (via :func:`resolve_release_exit_decision_with_assurance`)
and ``report.release_document.release_json_document`` for the persisted
``exit`` block -- so giving it its own name makes "one resolution, two
readers" legible instead of implied.

Re-exported from :mod:`abicheck.policy.exit_decision_precedence` and
:mod:`abicheck.workflows.gate`, so every existing importer is unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .exit_decision import ExitDecision
from .exit_decision_precedence import (
    resolve_release_exit_decision,
)

if TYPE_CHECKING:
    from .release_assurance import ReleaseAssuranceDecision

__all__ = [
    "release_analysis_assurance_contribution",
    "release_evidence_contract_contribution",
    "resolve_release_exit_decision_for_report",
    "resolve_release_exit_decision_with_assurance",
]


def release_analysis_assurance_contribution(
    library_results: list[dict[str, object]],
    *,
    require_complete: bool,
) -> int:
    """``.abicheck.yml``'s ``assurance.require_complete``, aggregated across
    a release's members.

    ``max()`` over each member's own ``0``/``1``, computed from the
    ``analysis_assurance_status`` the fan-out stamps from that member's
    ``DiffResult`` -- the identical rule
    :func:`~abicheck.analysis_assurance.analysis_assurance_exit_contribution`
    applies to a single pair, so **a release of one library and that library
    compared on its own agree**. Cardinality is an input here, not a
    different product: one member short of complete analysis floors the
    release, exactly as it would floor its own single-pair comparison.

    ``0`` unconditionally when *require_complete* is false, which is what
    keeps the setting purely additive -- a release that never opted in sees
    no exit-code change whatever any member's status says.

    Derived here rather than passed in, for the same reason
    :func:`release_evidence_contract_contribution` is: a value every
    reporter must supply identically is not an argument, and that axis
    shipped with one of three call sites missed.
    """
    if not require_complete:
        return 0
    return max(
        (
            0 if entry.get("analysis_assurance_status") == "complete" else 1
            for entry in library_results
            if isinstance(entry, dict) and "analysis_assurance_status" in entry
        ),
        default=0,
    )


def release_additions_review_contribution(
    library_results: list[dict[str, object]],
) -> int:
    """ADR-067 D6's additions-review floor across a release's members:
    ``max()`` over each member's ``additions_review_exit_contribution``.

    Derived here, like the assurance and evidence-contract axes, so every
    reporter of the release decision reads the same value.
    """
    return max(
        (
            value
            for entry in library_results
            if isinstance(entry, dict)
            and type(value := entry.get("additions_review_exit_contribution", 0)) is int
        ),
        default=0,
    )


def release_unused_rules_contribution(
    library_results: list[dict[str, object]],
) -> int:
    """ADR-067 P1: the ``unused_rules`` gate across a release's members.

    A rule is unused for the *release* when every member that could evaluate it
    left it unused, and at least one member could. A member could evaluate a
    rule when its status is anything but ``not_evaluated`` (its
    ``evaluable_rule_keys``); a member that could not evaluate it is ignored.
    This is the rule :func:`~abicheck.report.disposition_audit.fold_rule_status`
    applies to the release audit row, so the gate and the row always agree. The
    strictest member mode decides whether the gate is active at all.
    """
    from .unused_rules_gate import strictest_mode

    def _keys(entry: dict[str, object], field: str) -> set[str]:
        value = entry.get(field)
        return {str(k) for k in value} if isinstance(value, list) else set()

    modes = [
        str(entry["unused_rules_mode"])
        for entry in library_results
        if isinstance(entry, dict) and entry.get("unused_rules_mode")
    ]
    if strictest_mode(modes) != "block":
        return 0
    carriers = [
        entry
        for entry in library_results
        if isinstance(entry, dict) and "configured_rule_keys" in entry
    ]

    def _evaluable(entry: dict[str, object]) -> set[str]:
        # A member written before this key existed: every configured rule
        # counts as evaluable, which is the old all-members rule.
        if "evaluable_rule_keys" in entry:
            return _keys(entry, "evaluable_rule_keys")
        return _keys(entry, "configured_rule_keys")

    configured: set[str] = set()
    for entry in carriers:
        configured |= _keys(entry, "configured_rule_keys")
    for key in configured:
        evaluating = [e for e in carriers if key in _evaluable(e)]
        if evaluating and all(key in _keys(e, "unused_rule_keys") for e in evaluating):
            return 1
    return 0


def release_evidence_contract_contribution(
    library_results: list[dict[str, object]],
) -> int:
    """ADR-064's exit-7 axis, aggregated across a release's members.

    ``max()`` over each member's own ``evidence_contract_error_contribution``
    (stamped by the fan-out from its ``DiffResult``): one member whose pinned
    ``--depth build``/``--depth source`` its own live evidence never reached
    makes the release report the axis. ``0`` for every run without a
    ``--depth`` pin.

    Derived here, and called by :func:`resolve_release_exit_decision_for_report`
    itself rather than passed in, on purpose: this axis first shipped as a
    caller-supplied argument and one of the three call sites (the
    ``--output-dir`` sidecar) was missed, so that file reported ``exit.code:
    0`` for a run that exited ``7`` (Codex review). A value every reporter
    must supply identically is not an argument.
    """
    return max(
        (
            contribution
            for entry in library_results
            if isinstance(entry, dict)
            and isinstance(
                contribution := entry.get("evidence_contract_error_contribution", 0),
                int,
            )
        ),
        default=0,
    )


def _compute_release_legacy_exit_code(
    worst_verdict: str,
    library_results: list[dict[str, object]],
    release_global_verdict: str = "NO_CHANGE",
) -> int:
    """Worst legacy-scheme exit code across libraries *and* release-global
    (bundle/probe-matrix) findings.

    ADR-064 stage 1b. Mirrors ``workflows.release_exit_fold.release_severity_exit_code``'s per-library independence for
    the *legacy* scheme: ``release_outcome.RELEASE_VERDICT_ORDER``'s collapsed
    ``worst_verdict`` ranks ``"ERROR"``/``"not_comparable"`` above every
    real :class:`~abicheck.checker_policy.Verdict`, so using it directly as
    the legacy compatibility-gate contribution would let an unrelated
    library's operational failure hide a real ``BREAKING``/``API_BREAK``
    verdict from a *different* library in the same release -- exactly the
    gap :func:`resolve_release_exit_decision`'s own docstring calls out as
    "real, additional scope for stage 1b's wiring work." Scanning
    *library_results* alone, though, misses the opposite case (Codex
    review, fresh evidence): a bundle or probe-matrix break with every
    library itself ``NO_CHANGE`` raises the *aggregate* ``worst_verdict``
    (``workflows.release_bundle.collect_bundle_result``/``_collect_matrix_
    result``) without ever setting any library's own ``"verdict"`` key, so
    the per-library scan alone would find ``0``. Folding in *worst_verdict*
    itself (when it names a real ``Verdict``, i.e. not ``"ERROR"``/
    ``"not_comparable"``) via ``max()`` catches both: an aggregate real
    verdict at least as bad as any library's own, and a library's own real
    verdict the aggregate's ``"ERROR"`` collapse would otherwise hide.

    *release_global_verdict* (Codex review, fresh evidence, second round)
    is the caller's own uncollapsed bundle/probe-matrix verdict --
    independent of *worst_verdict*, which is *already* the max of every
    library's verdict, every release-global verdict, **and** the ``ERROR``/
    ``not_comparable`` sentinels together, so once an unrelated library's
    ``ERROR`` outranks a real release-global ``BREAKING`` in that same
    collapse, *worst_verdict* alone can no longer tell the two apart --
    unlike a library-level break, a release-global one never appears in
    *library_results* either, so there is nothing left to scan it out of.
    Folded in via the same ``max()`` treatment as a library's own verdict,
    so a real release-global break is never silently dropped just because
    some other library's operational failure happens to rank higher.
    """
    # `policy.classification`, not the legacy `checker_policy` facade:
    # origin/main moved this import to the canonical owner in the same
    # function, in the commits this branch merged (the conflict git
    # mis-aligned onto the re-export shim next door). Adopted here rather
    # than dropped, since the function moved but the change is main's.
    from .classification import Verdict
    from .severity import legacy_exit_code

    worst = 0
    for entry in library_results:
        if not isinstance(entry, dict):
            continue
        verdict_str = entry.get("verdict")
        if isinstance(verdict_str, str) and verdict_str in Verdict.__members__:
            worst = max(worst, legacy_exit_code(Verdict[verdict_str]))
    if worst_verdict in Verdict.__members__:
        worst = max(worst, legacy_exit_code(Verdict[worst_verdict]))
    if release_global_verdict in Verdict.__members__:
        worst = max(worst, legacy_exit_code(Verdict[release_global_verdict]))
    return worst


def resolve_release_exit_decision_for_report(
    worst_verdict: str,
    fail_on_removed: bool,
    removed_keys: list[str],
    severity_exit_code: int | None,
    contract_coverage_exit_contribution: int,
    library_results: list[dict[str, object]],
    release_global_verdict: str = "NO_CHANGE",
    *,
    incomplete_scope_contribution: int = 0,
    no_comparison_completed_contribution: int = 0,
    require_complete_analysis: bool = False,
) -> ExitDecision:
    """ADR-064 stage 1b: the release fan-out's persisted, explainable
    ``exit`` block.

    *removed_keys* is, since ADR-065 S2, the release's **proven** removal
    set (`ScopeAcquisitionRecord.proven_removed_members`), never the raw
    old-minus-new set difference `_match_release_keys` still reports under
    the JSON key ``unmatched_old`` -- an unmatched member whose absence
    the new side's inventory cannot prove is the scope axis's business
    (*incomplete_scope_contribution*), not exit ``8``'s.
    *no_comparison_completed_contribution* is D7's own ``0``/``1``.
    *analysis_assurance_contribution* (ADR-071) is the release's
    ``assurance.require_complete`` floor, already folded with ``max`` across
    every compared member by ``policy.release_assurance``; ``0`` whenever the
    setting is off, which is every pre-existing invocation.

    The one release resolution: the persisted ``exit`` block reads it, and
    the process status reads it through
    :func:`resolve_release_exit_decision_with_assurance`, so the two agree by
    construction. A release with one ``BREAKING`` library and a second,
    unrelated library that failed to compare (an ``"ERROR"`` verdict)
    collapses to ``worst_verdict == "ERROR"`` in
    ``release_outcome.RELEASE_VERDICT_ORDER``; this function still finds the
    ``BREAKING`` member via :func:`_compute_release_legacy_exit_code` and
    names both ``COMPATIBILITY_GATE`` and ``OPERATIONAL_ERROR`` in
    ``reasons``, tied at ``4``.

    *library_results* alone does not capture a bundle/probe-matrix-only
    break (no library's own verdict changes) -- see
    :func:`_compute_release_legacy_exit_code`'s own docstring for how the
    legacy branch also folds in *worst_verdict* itself to cover that case,
    and *release_global_verdict* (Codex review, fresh evidence, second
    round) for the sibling case that fix alone still missed: an unrelated
    library's ``"ERROR"`` outranking a real release-global ``BREAKING`` in
    the very same ``worst_verdict`` collapse.

    *severity_exit_code* being not ``None`` is what "severity scheme
    active" means.
    *operational_error_contribution* is the **union** of the aggregate
    ``worst_verdict == "ERROR"`` and a direct scan of *library_results* --
    either alone loses a real operational failure. The scan was added
    (Codex review, fresh evidence) rather than checking ``worst_verdict ==
    "ERROR"`` -- an earlier revision did the latter, which reads ``0``
    whenever a *different* library's ``"not_comparable"`` verdict outranks
    ``"ERROR"`` in ``release_outcome.RELEASE_VERDICT_ORDER`` and becomes the aggregate
    ``worst_verdict``, even though a real operational failure still
    happened elsewhere in the release and `resolve_release_exit_decision`'s
    own ``not_comparable`` branch already preserves this value for exactly
    that explainability case. The aggregate check is kept alongside it
    because a caller with no per-member list at all -- ``compare
    --bundle-facts``, whose whole release is one already-folded result --
    carries the ``"ERROR"`` sentinel *only* in *worst_verdict*, and
    scanning an empty *library_results* would silently drop its exit ``4``
    to ``0`` (found by the reachable-state parity harness).
    """
    not_comparable = worst_verdict == "not_comparable"
    severity_scheme_active = severity_exit_code is not None
    removed_required_library = fail_on_removed and bool(removed_keys)
    operational_error_contribution = (
        4
        if (
            worst_verdict == "ERROR"
            or any(
                isinstance(e, dict) and e.get("verdict") == "ERROR"
                for e in library_results
            )
        )
        else 0
    )
    verdict_or_severity_contribution = (
        (severity_exit_code or 0)
        if severity_scheme_active
        else _compute_release_legacy_exit_code(
            worst_verdict, library_results, release_global_verdict
        )
    )
    return resolve_release_exit_decision(
        not_comparable=not_comparable,
        severity_scheme_active=severity_scheme_active,
        verdict_or_severity_contribution=verdict_or_severity_contribution,
        removed_required_library=removed_required_library,
        contract_coverage_contribution=contract_coverage_exit_contribution,
        analysis_assurance_contribution=release_analysis_assurance_contribution(
            library_results, require_complete=require_complete_analysis
        ),
        evidence_contract_error_contribution=release_evidence_contract_contribution(
            library_results
        ),
        operational_error_contribution=operational_error_contribution,
        incomplete_scope_contribution=incomplete_scope_contribution,
        no_comparison_completed_contribution=no_comparison_completed_contribution,
        additions_review_contribution=release_additions_review_contribution(
            library_results
        ),
        unused_rules_contribution=release_unused_rules_contribution(library_results),
    )


def resolve_release_exit_decision_with_assurance(
    worst_verdict: str,
    fail_on_removed: bool,
    removed_keys: list[str],
    severity_exit_code: int | None = None,
    *,
    contract_coverage_exit_contribution: int = 0,
    library_results: list[dict[str, object]] | None = None,
    release_global_verdict: str = "NO_CHANGE",
    incomplete_scope_exit_contribution: int = 0,
    no_comparison_completed_exit_contribution: int = 0,
    assurance_decision: ReleaseAssuranceDecision | None = None,
) -> ExitDecision:
    """The release decision a directory/package ``compare`` exits on.

    :func:`resolve_release_exit_decision_for_report` over the run's own
    inputs, with ADR-071's assurance axis taken from *assurance_decision*
    (``.abicheck.yml``'s ``assurance.require_complete`` plus the per-member
    statuses behind it) rather than a bare flag, so a caller cannot state a
    ``require_complete`` its members contradict.

    ``compare --bundle-facts`` has no per-member *library_results* (its
    release arrives as one folded document) but does have a real
    ``AnalysisAssurance`` per member (ADR-071 D8). The resolver reads that
    axis off per-member rows, so rows carrying only the one key it reads are
    synthesized here; every other per-member axis reads its own keys with a
    defaulting ``.get``, so such a row contributes ``0`` to each of them. Its
    ``"ERROR"`` sentinel reaches the resolver through *worst_verdict*.
    """
    members = list(library_results or [])
    if assurance_decision is not None and not members:
        members = [
            {"library": m.name, "analysis_assurance_status": m.status}
            for m in assurance_decision.members
        ]
    return resolve_release_exit_decision_for_report(
        worst_verdict,
        fail_on_removed,
        removed_keys,
        severity_exit_code,
        contract_coverage_exit_contribution,
        members,
        release_global_verdict,
        incomplete_scope_contribution=incomplete_scope_exit_contribution,
        no_comparison_completed_contribution=no_comparison_completed_exit_contribution,
        require_complete_analysis=bool(
            assurance_decision is not None and assurance_decision.require_complete
        ),
    )
