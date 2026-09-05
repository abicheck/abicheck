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

"""Fan-in aggregation of per-target ABI reports (multi-target CI gate).

A project that ships several ABI-relevant targets (``linux-x86_64``,
``windows-x86_64``, ``macos-arm64``, ...) builds and compares each one on its
own CI matrix leg, and each leg emits a ``compare``/``scan`` JSON report. This
module folds those per-target reports into one gate decision under a single
invariant:

    An expected target with no report is UNAVAILABLE (unknown), never folded
    into the verdict as compatible.

It replaces the hand-written post-matrix "ABI gate" heredoc shown in the
GitHub Action recipes, whose ``for path in glob('*.json')`` loop silently
dropped any target whose build failed before uploading its report — passing
green while a required platform was never analyzed.

Three orthogonal axes, kept separate on purpose (ADR-042):

* **compatibility** — the worst ABI *verdict* over the analyzed targets, for
  reporting. This is *not* the gate: a policy can make a ``COMPATIBLE`` report
  block (``addition=error``) or a ``BREAKING`` report pass (a demoted preset).
* **gate** — whether CI should fail. Each report already carries its own gate
  decision (``severity.{exit_code,blocking,blocking_categories}``, computed by
  ``reporter._build_severity_json`` → ``severity.compute_gate_decision``);
  ``aggregate`` combines those, it never recomputes a gate from the verdict.
  Reports produced without a severity policy carry no gate block, so
  they fall back to the legacy verdict→exit mapping.
* **coverage** — did every *required* target actually report? A required gap is
  a *coverage* failure (exit ``1``), never masqueraded as an ABI break.
* **contract coverage** — ADR-049 Phase 7's orthogonal axis, read off each
  report's own ``contract_coverage_exit_contribution`` and folded with ``max``
  exactly as ``compare`` and ``scan --against`` fold theirs. A target that
  *did* report but could not close its selected ``--contract`` domain
  contributes ``1``. Distinct from the coverage axis above (which is about a
  target reporting at all) and reported separately, so an exit ``1`` always
  names which axis produced it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from abicheck.change_registry_types import Verdict

from .gate import GateInfo as GateInfo
from .matrix import (
    FindingMatrixEntry as FindingMatrixEntry,
    ProfileContractState as ProfileContractState,
)
from .reconcile import (
    FINDING_SCOPE_ALL_PROFILES as FINDING_SCOPE_ALL_PROFILES,
    FINDING_SCOPE_PARTIAL as FINDING_SCOPE_PARTIAL,
    FINDING_SCOPE_PROFILE_SPECIFIC as FINDING_SCOPE_PROFILE_SPECIFIC,
    FINDING_SCOPE_UNDETERMINED as FINDING_SCOPE_UNDETERMINED,
    ReportFinding as ReportFinding,
    ReportFindings as ReportFindings,
)
from .resolve import (
    AGGREGATE_MANIFEST_VERSION as AGGREGATE_MANIFEST_VERSION,
    AggregateError as AggregateError,
    ExpectedTargets as ExpectedTargets,
    OnMissingRequired as OnMissingRequired,
    OnUnexpectedTarget as OnUnexpectedTarget,
    resolve_gate_policy as resolve_gate_policy,
)

#: Machine-readable schema version of the ``to_dict()`` / ``--format json``
#: output. Bump on any incompatible change to that structure.
#:
#: ``1.2`` added the additive ``finding_matrix`` block (G34 Phase D) — every
#: key present at ``1.1`` is unchanged, so a consumer pinned to ``1.1`` keeps
#: reading a ``1.2`` document correctly; the MINOR bump is what tells a
#: consumer the new block is available to read at all.
#:
#: ``1.3`` adds the top-level ``contract_coverage`` block
#: (``exit_contribution`` / ``incomplete_targets``) and a
#: ``contract_coverage_exit`` field on every target entry (ADR-049
#: Phase 7). Additive in shape, but not inert: the contribution folds
#: into ``gate.exit_code``, so a matrix whose targets exited ``1`` for
#: incomplete contract evidence no longer aggregates to ``0``.
#:
#: ``1.4`` adds an optional ``profile_contract`` array to each
#: ``finding_matrix`` entry (CLI-audit P1) -- one ADR-049 contract-decision
#: record per affected profile, so "GCC: IN_CONTRACT and gating" and
#: "Clang: UNKNOWN_UNRESOLVED and not gating" no longer collapse into the
#: same ``affected_profiles`` membership fact for one logical finding.
#: Purely additive and inert: present only when at least one profile ran
#: ``--contract`` for that target, never changes ``scope`` or
#: any existing key, and a ``1.3``-shaped consumer that ignores unknown
#: keys keeps reading a ``1.4`` document correctly.
#:
#: ``1.5`` adds an ``analysis_assurance_exit`` field on every target entry
#: (P0.4's orthogonal analysis-assurance axis, the exact sibling of
#: ``1.3``'s ``contract_coverage_exit`` -- Codex review: without it, a
#: target whose severity gate read 0 but whose analysis-assurance axis
#: independently floored the *real* exit to 1 fed the aggregate a green
#: result for that target). Additive in shape, but not inert the same way
#: ``1.3`` was not: the contribution folds into ``gate.exit_code``, so a
#: matrix whose targets exited 1 for incomplete analysis assurance no
#: longer aggregates to 0.
#:
#: ``1.6`` (CLI cleanup phase two, PR 2) adds ``effective_policy`` -- which of
#: ``missing_required``/``unexpected_target`` this run actually applied, and
#: where each came from (``manifest``/``run-plan``/``default``). Additive and
#: inert the same way: a ``1.5``-shaped consumer ignoring unknown keys still
#: reads a ``1.6`` document correctly. Landed after ``1.5`` merged to
#: ``main`` under the same version number for a different field -- bumped to
#: ``1.6`` on rebase rather than reusing ``1.5`` for two distinct additive
#: shapes, per this codebase's own schema-versioning discipline.
#:
#: ``1.7`` (dedup plan Phase 0 item 6) adds an optional
#: ``effective_config_digest`` per target -- that target's own
#: already-computed digest (:func:`_effective_config_digest`), carried
#: through rather than recomputed. Additive and inert like ``1.4``/``1.6``.
#:
#: ``1.8`` (ADR-065 S2) adds the top-level ``scope_completeness`` block, a
#: ``scope_completeness_exit`` field on every target entry, and
#: ``scope_incomplete_profiles`` on every profile-matrix entry -- the third
#: orthogonal exit-floor axis, the exact sibling of ``1.3``/``1.5``; like
#: ``1.3``, its incomplete lists also name a target that *accepted* the gap
#: (``--on-incomplete-scope warn``, contribution ``0``)
#: (Codex review: a release whose scope was incomplete under
#: ``--on-incomplete-scope block``, or that completed no comparison,
#: published ``run_outcome.scope`` while its canonical consumer folded only
#: ``gate``/``operational``). Additive in shape, not inert: the
#: contribution folds into ``gate.exit_code``.
AGGREGATE_SCHEMA_VERSION = "1.8"

#: Matches a ``check_id``-shaped ``target_id`` — ADR-047 §7's
#: ``target@profile#baseline_channel@requested_depth``, built verbatim by
#: ``buildsource.check_report.build_check_id``. ``profile``/``baseline_channel``
#: are identifiers (``[A-Za-z0-9][A-Za-z0-9._-]*``, see that module's
#: ``_IDENTIFIER_RE`` — never containing ``@``/``#``) and ``requested_depth``
#: is one of the four fixed evidence-depth values, so anchoring on those two
#: known-shaped tail segments is safe even if ``target`` itself were ever to
#: contain a stray ``@``/``#`` (bundle/target ids are also identifiers today,
#: but this regex does not need to assume that to parse correctly).
#:
#: G42 adds two further optional, composable tail segments, in this fixed
#: order: ``!<environment_id>`` (a named-environment qualifier — reserved
#: here, not yet produced by any generator; see the G42 plan's "Named
#: environments" phase) and ``~<explicit_id>`` (a project-author-supplied
#: ``checks[].id``, G42's "Explicit check identifiers" phase). Both are
#: independently omittable and neither collides with the base charset —
#: ``!``/``~`` never appear inside a component (``_IDENTIFIER_RE`` above).
#: Absent both, the generated string and its parse are bit-for-bit
#: unchanged from the pre-G42 shape (``environment_id=None``,
#: ``explicit_id=None``) — this is the backward-compatibility guarantee.
_CHECK_ID_RE = re.compile(
    r"^(?P<target>.+)@(?P<profile>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"#(?P<channel>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"@(?P<depth>binary|headers|build|source)"
    r"(?:!(?P<environment_id>[A-Za-z0-9][A-Za-z0-9._-]*))?"
    # \Z, not a trailing $ -- $ also matches just before a trailing \n
    # (Codex review; see checker_types.CHECK_ID_PATTERN's identical fix,
    # kept in lockstep with this pattern per this module's own docstring).
    r"(?:~(?P<explicit_id>[A-Za-z0-9][A-Za-z0-9._-]*))?\Z"
)


@dataclass(frozen=True)
class CheckIdParts:
    """A parsed ``check_id``-shaped ``target_id`` (ADR-047 §7, extended by G42).

    :attr:`environment_id`/:attr:`explicit_id` are ``None`` for a
    pre-G42-shaped id (no ``!``/``~`` tail) — the common case today.
    """

    target: str
    profile: str
    baseline_channel: str
    requested_depth: str
    environment_id: str | None = None
    explicit_id: str | None = None


def parse_check_id(target_id: str) -> CheckIdParts | None:
    """Split *target_id* into its ``check_id`` components, or ``None``.

    ``None`` means *target_id* does not follow the
    ``target@profile#baseline_channel@requested_depth`` shape (optionally
    followed by a ``!environment_id`` and/or ``~explicit_id`` tail, G42)
    ``buildsource.check_report.build_check_id`` produces — e.g. a bare
    filename-derived id (:func:`target_id_from_path`) from a report that
    never went through the run-plan/``check-target`` pipeline. This is the
    common case for a single-profile setup and is not an error; callers
    that group by profile (:attr:`AggregateResult.profile_matrix`) simply
    have nothing to group such a target under.
    """
    m = _CHECK_ID_RE.match(target_id)
    if m is None:
        return None
    return CheckIdParts(
        target=m.group("target"),
        profile=m.group("profile"),
        baseline_channel=m.group("channel"),
        requested_depth=m.group("depth"),
        environment_id=m.group("environment_id"),
        explicit_id=m.group("explicit_id"),
    )


#: Legacy verdict → gate exit code, used only for reports that carry no
#: ``severity`` gate block (i.e. produced without a severity policy).
#: Mirrors ``compare``'s legacy scheme: NO_CHANGE/COMPATIBLE/COMPATIBLE_WITH_RISK
#: are non-blocking (0), API_BREAK is a source break (2), BREAKING an ABI break
#: (4). A report *with* a gate block uses that block's own ``exit_code`` — the
#: authoritative, policy-aware value — instead.

#: Total ordering over verdicts for *reporting* the worst analyzed
#: compatibility verdict. Unlike the exit scheme (which collapses the three
#: non-blocking verdicts), this keeps ``COMPATIBLE_WITH_RISK`` strictly above
#: ``COMPATIBLE`` so a risk one target flagged is never hidden in the summary.
_VERDICT_RANK: dict[Verdict, int] = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 1,
    Verdict.COMPATIBLE_WITH_RISK: 2,
    Verdict.API_BREAK: 3,
    Verdict.BREAKING: 4,
}

#: Exit code contributed by an incomplete *required* coverage gap. Deliberately
#: NOT 4 (an ABI break) or 2 (a source break) — a missing build is an
#: infrastructure/coverage problem, and an external wrapper reading exit 4 as
#: "ABI break" must never be handed one for a build that simply never ran.
COVERAGE_INCOMPLETE_EXIT = 1

#: Default report-filename prefix the matrix recipe uses
#: (``abi-report-<target>.json``). Stripped when deriving a target id from a
#: report file's stem, if the report does not self-identify a ``target_id``.
DEFAULT_REPORT_PREFIX = "abi-report-"

#: Sentinel top-level ``verdict`` a compare-release report emits for an
#: *operational* failure (a library failed to dump/extract/compare). Not a
#: :class:`Verdict` enum member — the release path floors it to exit 4, and the
#: fan-in preserves it as a blocking gate rather than a verdictless report.
_OPERATIONAL_ERROR_VERDICT = "ERROR"

#: ``scan``'s own four abort verdicts (ADR-064 stage 1b's native-CLI abort
#: report, ``cli_scan._emit_scan_abort_report``, plus the two ADR-050 D2/P2
#: sentinels below) -- none is a :class:`Verdict` enum member, so like
#: ``_OPERATIONAL_ERROR_VERDICT`` above (and unlike
#: ``_BOOTSTRAP_VERDICT``/``_NEW_TARGET_VERDICT`` below, which are
#: legitimately tolerated) all four must force a blocking gate rather than
#: fall through to an unavailable/verdictless report: an unfinished scan is a
#: real failure a required-target policy must not silently treat as "nothing
#: to compare yet" (Codex review, fresh evidence -- the earlier envelope fix
#: made `GateInfo.from_scan_report` accept these payloads in isolation, but
#: `_load_report_file` never reaches it, since it only calls that after
#: `parse_report_verdict` succeeds, and none of these four strings is a
#: `Verdict` member). Unlike `_OPERATIONAL_ERROR_VERDICT`, though, the target
#: stays *unavailable* for the compatibility axis
#: (`compatibility_verdict=None`) rather than a synthetic `Verdict.BREAKING`
#: -- a scan that aborted before comparing never produced an ABI-break
#: finding, so counting it as one, or as an "analyzed" target, fabricates
#: information a reader would reasonably trust (Codex review, fresh evidence:
#: `AggregateResult.to_dict()` reported `compatibility.verdict: "BREAKING"`
#: and a complete `analyzed_targets` count for a comparison that never ran).
#: See `TargetReport`'s own docstring and
#: `AggregateResult._forced_gate_targets` for how the gate still counts
#: without inventing that verdict.
_SCAN_BUDGET_OVERFLOW_VERDICT = "BUDGET_OVERFLOW"
_SCAN_EVIDENCE_CONTRACT_ERROR_VERDICT = "EVIDENCE_CONTRACT_ERROR"
#: ADR-050 D2's comparability refusal (`scan_engine.run_baseline_diff`'s own
#: `ProfileMismatchError`/`ScopeMismatchError` handling) -- scan's legacy
#: exit 6, mirroring `OperationalStatus.NOT_COMPARABLE`.
_SCAN_NOT_COMPARABLE_VERDICT = "NOT_COMPARABLE"
#: `service_scan.run_scan_set`'s P2 sentinel: the cross-library bundle audit
#: itself never ran even though every member scanned clean, so the set's own
#: outcome must still block rather than read as a full pass (Codex review,
#: fresh evidence -- this and `_SCAN_NOT_COMPARABLE_VERDICT` were the two
#: `run_outcome`-blocking sentinels this dict was still missing, discarding
#: `run_outcome.operational` for either one).
_SCAN_BUNDLE_INCOMPLETE_VERDICT = "BUNDLE_INCOMPLETE"

#: ``actions/check-target``'s two advisory, never-a-compatibility-verdict
#: sentinels (``check_report.BOOTSTRAP_VERDICT``/``NEW_TARGET_VERDICT`` —
#: duplicated as bare strings here rather than imported, same as
#: ``_OPERATIONAL_ERROR_VERDICT`` above, to keep this module free of a
#: dependency on ``buildsource``). Both fall through :func:`parse_report_verdict`
#: to ``None`` (neither is a real :class:`Verdict` member), which is correct
#: for the compatibility/coverage axes -- an unavailable ``TargetReport``
#: paired with ``required: false``, per each report builder's own docstring.
#: Read only by :func:`_load_report_file` below, to give each its own
#: human-readable ``reason`` instead of the generic "report carried no ABI
#: verdict" every other verdictless report gets -- otherwise an
#: intentionally-tolerated new-library first release reads identically to a
#: malformed/corrupt report in the aggregate JSON/text output (Codex review).
_BOOTSTRAP_VERDICT = "NO_BASELINE"
_NEW_TARGET_VERDICT = "NEW_TARGET"


class CoverageStatus(str, Enum):
    """Was every *required* expected target actually analyzed?"""

    COMPLETE = "complete"  # every required target reported
    PARTIAL = "partial"  # at least one required target is unavailable
    EMPTY = "empty"  # no target could be analyzed at all


#: The exit codes a report's own gate decision is allowed to declare — the
#: severity-aware scheme ``compare`` emits (0 pass / 1 addition-or-quality /
#: 2 potential-breaking / 4 abi-breaking). Anything else in a ``severity`` block
#: is a corrupt gate, not a value we silently reinterpret.
_VALID_GATE_EXIT = frozenset({0, 1, 2, 4})


@dataclass(frozen=True)
class TargetReport:
    """One target's contribution to the aggregate.

    ``compatibility_verdict`` is ``None`` exactly when the target is
    *unavailable* — its report was expected but never arrived, was
    unreadable, or (see below) ran but never reached a comparison. ``gate``
    is usually also ``None`` in that case, with ``reason`` explaining why.
    ``unexpected`` marks a report whose target was not in the expected set
    (a new/not-yet-declared matrix target).

    ``reason`` is also populated (with ``compatibility_verdict`` forced to
    ``BREAKING`` and ``gate`` a synthetic blocking one,
    ``blocking_categories=("not_comparable",)``) for an *analyzed* target
    whose report was an ADR-050 D2 ``verdict: null`` not-comparable result —
    see :func:`_load_report_file`. A consumer distinguishing that from a
    genuine break checks ``gate.blocking_categories`` for
    ``"not_comparable"``, the same way an operational-error report
    (``blocking_categories=("operational_error",)``) is already told apart.

    A *third* shape exists for ``scan``'s own two abort verdicts
    (``BUDGET_OVERFLOW``/``EVIDENCE_CONTRACT_ERROR``): unlike not_comparable/
    operational-error, these carry a non-``None`` ``gate`` while
    ``compatibility_verdict`` stays ``None`` — the target remains
    *unavailable* for compatibility-reporting purposes (no comparison ever
    ran, so no verdict/analyzed-target count is invented for it), while its
    forced ``gate`` still counts toward :meth:`AggregateResult.exit_code`/
    :attr:`AggregateResult.blocking_targets` regardless of the target's own
    required/optional declaration (Codex review: the earlier synthetic
    ``BREAKING`` verdict made an aborted scan read as an analyzed ABI break).
    See :attr:`AggregateResult._forced_gate_targets`.
    """

    target_id: str
    required: bool
    compatibility_verdict: Verdict | None  # None ⟺ unavailable
    gate: GateInfo | None = None
    report_path: str | None = None
    library: str | None = None
    reason: str | None = None  # unavailable, or not_comparable/operational_error detail
    unexpected: bool = False
    #: The findings this report listed, and whether that list is all of them
    #: (:class:`~abicheck.aggregate_findings.ReportFindings`). ``None`` for an
    #: unavailable report — one that never arrived, was unreadable, or
    #: produced a not-comparable/operational-error result, so it listed
    #: nothing and established nothing.
    findings: ReportFindings | None = None
    #: ADR-049 Phase 7's contract-coverage contribution for this target
    #: (``0``/``1``), carried separately from :attr:`gate` because the two are
    #: orthogonal axes (plan Section 7): a coverage failure may raise a clean
    #: ``0`` to ``1`` and may never lower a real ABI break's ``4``. ``0`` for
    #: an unavailable target -- a report that never arrived is a *coverage*
    #: gap on this aggregate's own axis, already gated as one, and inventing a
    #: contract-coverage failure for it would double-count the same absence.
    #:
    #: Declared after ``findings``, with the two below, so adding them cannot
    #: shift an existing positional construction of this public dataclass: a
    #: caller passing ``TargetReport(..., unexpected, report_findings)`` would
    #: otherwise bind its ``ReportFindings`` here, and ``exit_code()``'s
    #: ``max()`` would compare it against an int (Codex review -- the same
    #: call already made for :class:`ProfileMatrixEntry`).
    contract_coverage_exit: int = 0
    #: Whether the report listed any coverage failure, regardless of whether
    #: it gated. Separate from the contribution above because
    #: ``contract.unresolved=warn`` zeroes the floor and changes nothing else
    #: -- the failures stay listed. Reported, never folded into an exit code.
    contract_coverage_incomplete: bool = False
    #: Whether the report stated a usable contribution at all -- see
    #: :func:`_contract_coverage_declared`. Distinguishes a real
    #: ``contract.unresolved=warn`` acceptance from a silent or malformed
    #: one, so prose about the run cannot claim a policy it never set.
    contract_coverage_declared: bool = False
    #: P0.4's orthogonal analysis-assurance contribution (``0``/``1``), the
    #: exact sibling of :attr:`contract_coverage_exit` above for the other
    #: orthogonal exit-floor axis. Declared last for the same
    #: positional-construction-safety reason as that field.
    analysis_assurance_exit: int = 0
    #: Phase 0 item 6 of docs/contribute/plans/duplication-and-convergence-
    #: assessment.md: this target's own already-computed
    #: ``effective_config_digest``, read straight off its report by
    #: :func:`_load_report_file` -- never recomputed here. ``None`` for an
    #: unavailable target or a report that carried none. Declared last for
    #: the same positional-construction-safety reason as the fields above.
    effective_config_digest: str | None = None
    #: ADR-065's scope-completeness contribution (``0``/``1``), the third
    #: orthogonal exit-floor axis (aggregate schema 1.8); declared
    #: after ``effective_config_digest`` so an existing positional caller of
    #: this compatibility-path type keeps binding the digest (Codex review).
    scope_completeness_exit: int = 0
    #: Whether the report recorded an incomplete scope at all -- true even
    #: when ``--on-incomplete-scope warn`` zeroed the contribution above.
    #: Reported, never folded into an exit code (the contract-coverage rule).
    scope_completeness_incomplete: bool = False

    @property
    def analyzed(self) -> bool:
        return self.compatibility_verdict is not None

    @property
    def profile_id(self) -> str | None:
        """The ``profile`` component of a ``check_id``-shaped ``target_id``
        (status-review item 5: "profile identity in TargetReport"), or
        ``None`` when ``target_id`` doesn't follow that shape at all — see
        :func:`parse_check_id`."""
        parsed = parse_check_id(self.target_id)
        return parsed.profile if parsed is not None else None

    @property
    def base_target(self) -> str:
        """The target/bundle identity with any ``@profile#channel@depth``
        suffix stripped — the key that groups this report with the *same*
        logical target checked under a different profile
        (:attr:`AggregateResult.profile_matrix`). Equals ``target_id``
        verbatim when it isn't ``check_id``-shaped."""
        parsed = parse_check_id(self.target_id)
        return parsed.target if parsed is not None else self.target_id

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "target_id": self.target_id,
            "required": self.required,
            "state": "analyzed" if self.analyzed else "unavailable",
            "compatibility_verdict": (
                self.compatibility_verdict.value
                if self.compatibility_verdict is not None
                else None
            ),
            "gate": self.gate.to_dict() if self.gate is not None else None,
            "contract_coverage_exit": self.contract_coverage_exit,
            "analysis_assurance_exit": self.analysis_assurance_exit,
            "scope_completeness_exit": self.scope_completeness_exit,
        }
        if self.unexpected:
            d["unexpected"] = True
        profile_id = self.profile_id
        if profile_id is not None:
            d["profile_id"] = profile_id
        for key in ("report_path", "library", "reason", "effective_config_digest"):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        return d


@dataclass(frozen=True)
class ProfileMatrixEntry:
    """One logical target's outcome across every toolchain profile that
    checked it (status-review item 5: "affected_profiles for one logical
    finding", "profile-level finding deduplication").

    Before this, a project running the same target under several
    ``profiles:`` (e.g. ``linux-gcc14``, ``linux-clang20``, ``windows-msvc``)
    saw three unrelated-looking ``target_id`` rows in
    :attr:`AggregateResult.targets` — nothing tied them back together as the
    *same* logical target, so a consumer had to already know the naming
    convention to notice "this one target broke everywhere except
    windows-msvc". This groups by :attr:`TargetReport.base_target` instead.
    """

    base_target: str
    #: Every profile that reported for this target, sorted.
    profiles: tuple[str, ...]
    #: Subset of ``profiles`` whose worst analyzed verdict was neither
    #: ``NO_CHANGE`` nor ``COMPATIBLE``, OR whose gate is blocking even
    #: though the verdict itself is compatible (Codex review: a
    #: ``COMPATIBLE`` report can still carry a policy-blocking gate, e.g.
    #: an ``addition: error`` policy — a profile in that state is not
    #: "clean" just because nothing broke). A profile with *zero* analyzed
    #: checks (every check for it is unavailable) is never "affected" here
    #: — no verdict at all is a coverage gap, not a break — but see
    #: ``incomplete_profiles``: a profile can be BOTH affected (one check
    #: broke) AND incomplete (another of its checks never reported) at once.
    affected_profiles: tuple[str, ...]
    #: Subset of ``profiles`` where at least one of that profile's
    #: *required* checks (this target can have more than one, at different
    #: baseline channels/requested depths) is unavailable — no report
    #: arrived for it. An unavailable *optional* check does not set this
    #: (Codex review: it must agree with ``AggregateResult.coverage``, which
    #: also only gates on required targets — otherwise a required-complete,
    #: optional-missing profile would read as both "coverage complete" and
    #: "incomplete" at once, contradicting itself).
    #: ``verdict_by_profile``/``affected_profiles`` are still computed from
    #: whichever of that profile's checks DID report (Codex review: an
    #: unavailable check must never be silently dropped from the picture,
    #: since a clean *completed* check plus a missing required one is a
    #: coverage gap, not "this profile is clean").
    incomplete_profiles: tuple[str, ...]
    #: Subset of ``profiles`` with *zero* analyzed checks at all — every
    #: check for that profile is unavailable (``verdict_by_profile[pid] is
    #: None``). Distinct from ``incomplete_profiles`` (which can include a
    #: profile that has SOME analyzed result): a profile here has none, so
    #: it must never be described as "clean" (Codex review) — there is
    #: nothing to be clean *about*. An optional-only gap here (no required
    #: check for this profile at all) still lands here, since the ambiguity
    #: is about the *verdict*, not the coverage gate.
    unanalyzed_profiles: tuple[str, ...]
    #: Subset of ``profiles`` with at least one check whose own ADR-049
    #: contract-coverage axis is short of evidence -- the profile-level view
    #: of :attr:`AggregateResult.contract_coverage_targets`, using the same
    #: predicate so the two can never name different targets.
    #:
    #: Deliberately its own field rather than folding into
    #: ``affected_profiles`` (Codex review). The complaint that prompted it
    #: was real: a profile whose only problem was contract coverage raised
    #: the aggregate exit to ``1`` while every list here stayed empty, so
    #: nothing named the profile responsible. But ``affected`` is defined in
    #: terms of verdict and gate, both compatibility concepts, and ADR-049
    #: §7 makes the coverage axis orthogonal to those -- it "never rewrites
    #: a finding's compatibility decision or gate contribution", and §7 asks
    #: reports to identify *which* axis produced an exit of 1. Folding it in
    #: would answer "which profile" by destroying "which axis", and it would
    #: contradict the same call made one level up, where ``contract_coverage``
    #: is a separate block from ``coverage`` for exactly this reason.
    #:
    #: A profile can be in this list and ``affected`` at once (a real break
    #: plus incomplete evidence), or in this one alone.
    #: profile -> worst analyzed verdict value, or ``None`` when *every*
    #: check for that profile is unavailable (no analyzed check at all).
    verdict_by_profile: dict[str, str | None]
    #: Declared last, with a default, so adding it cannot break a positional
    #: construction of this public dataclass (CodeRabbit review).
    contract_incomplete_profiles: tuple[str, ...] = ()
    #: Sibling of ``contract_incomplete_profiles`` for P0.4's own
    #: analysis-assurance axis; same predicate, same positional-safety
    #: reason for being declared last.
    analysis_incomplete_profiles: tuple[str, ...] = ()
    #: ADR-065's own axis (schema 1.8): profiles with at least one check
    #: whose scope-completeness contribution is nonzero.
    scope_incomplete_profiles: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_target": self.base_target,
            "profiles": list(self.profiles),
            "affected_profiles": list(self.affected_profiles),
            "incomplete_profiles": list(self.incomplete_profiles),
            "unanalyzed_profiles": list(self.unanalyzed_profiles),
            "contract_incomplete_profiles": list(self.contract_incomplete_profiles),
            "analysis_incomplete_profiles": list(self.analysis_incomplete_profiles),
            "scope_incomplete_profiles": list(self.scope_incomplete_profiles),
            "verdict_by_profile": dict(self.verdict_by_profile),
        }


#: Verdicts that do NOT make a profile "affected" in the profile matrix.
_UNAFFECTED_VERDICTS = frozenset({Verdict.NO_CHANGE, Verdict.COMPATIBLE})
