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
  Reports produced without a ``--severity-*`` policy carry no gate block, so
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

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, TypeGuard

from .aggregate_findings import (
    FINDING_SCOPE_ALL_PROFILES as FINDING_SCOPE_ALL_PROFILES,
    FINDING_SCOPE_PARTIAL as FINDING_SCOPE_PARTIAL,
    FINDING_SCOPE_PROFILE_SPECIFIC as FINDING_SCOPE_PROFILE_SPECIFIC,
    FINDING_SCOPE_UNDETERMINED as FINDING_SCOPE_UNDETERMINED,
    FindingMatrixEntry as FindingMatrixEntry,
    ProfileContractState as ProfileContractState,
    ReportFinding as ReportFinding,
    ReportFindings as ReportFindings,
    build_finding_matrix,
    parse_report_findings,
    render_finding_matrix_lines,
)
from .change_registry_types import Verdict

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
#: ``--contract-evaluation`` for that target, never changes ``scope`` or
#: any existing key, and a ``1.3``-shaped consumer that ignores unknown
#: keys keeps reading a ``1.4`` document correctly.
AGGREGATE_SCHEMA_VERSION = "1.4"

#: Matches a ``check_id``-shaped ``target_id`` — ADR-047 §7's
#: ``target@profile#baseline_channel@requested_depth``, built verbatim by
#: ``buildsource.check_report.build_check_id``. ``profile``/``baseline_channel``
#: are identifiers (``[A-Za-z0-9][A-Za-z0-9._-]*``, see that module's
#: ``_IDENTIFIER_RE`` — never containing ``@``/``#``) and ``requested_depth``
#: is one of the four fixed evidence-depth values, so anchoring on those two
#: known-shaped tail segments is safe even if ``target`` itself were ever to
#: contain a stray ``@``/``#`` (bundle/target ids are also identifiers today,
#: but this regex does not need to assume that to parse correctly).
_CHECK_ID_RE = re.compile(
    r"^(?P<target>.+)@(?P<profile>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"#(?P<channel>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"@(?P<depth>binary|headers|build|source)$"
)


@dataclass(frozen=True)
class CheckIdParts:
    """A parsed ``check_id``-shaped ``target_id`` (ADR-047 §7)."""

    target: str
    profile: str
    baseline_channel: str
    requested_depth: str


def parse_check_id(target_id: str) -> CheckIdParts | None:
    """Split *target_id* into its ``check_id`` components, or ``None``.

    ``None`` means *target_id* does not follow the
    ``target@profile#baseline_channel@requested_depth`` shape
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
    )


#: SemVer-style (MAJOR.MINOR) version of the expected-target *manifest* input
#: (``--manifest``). Independent of the report-output schema above. A manifest
#: may carry ``"aggregate_manifest_version"``; a MAJOR component newer than this
#: is rejected (the reader cannot know the newer structure), a matching or older
#: one is accepted (additive-only within a MAJOR).
AGGREGATE_MANIFEST_VERSION = "1.0"

#: Legacy verdict → gate exit code, used only for reports that carry no
#: ``severity`` gate block (i.e. produced without a ``--severity-*`` policy).
#: Mirrors ``compare``'s legacy scheme: NO_CHANGE/COMPATIBLE/COMPATIBLE_WITH_RISK
#: are non-blocking (0), API_BREAK is a source break (2), BREAKING an ABI break
#: (4). A report *with* a gate block uses that block's own ``exit_code`` — the
#: authoritative, policy-aware value — instead.
_LEGACY_SEVERITY: dict[Verdict, int] = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 0,
    Verdict.COMPATIBLE_WITH_RISK: 0,
    Verdict.API_BREAK: 2,
    Verdict.BREAKING: 4,
}

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


def _check_manifest_version(raw: Any) -> None:
    """Validate an optional manifest ``aggregate_manifest_version`` field.

    Absent → accepted (an unversioned manifest is treated as the current
    MAJOR). Present → must be a ``"MAJOR.MINOR"`` string whose MAJOR component
    does not exceed :data:`AGGREGATE_MANIFEST_VERSION`'s (a newer MAJOR carries
    structure this reader cannot interpret; fail loud rather than silently
    mis-read it).
    """
    if raw is None:
        return
    if not isinstance(raw, str) or not raw:
        raise AggregateError("manifest 'aggregate_manifest_version' must be a string")
    try:
        major = int(raw.split(".", 1)[0])
        supported = int(AGGREGATE_MANIFEST_VERSION.split(".", 1)[0])
    except ValueError as exc:
        raise AggregateError(
            f"manifest 'aggregate_manifest_version' is not a MAJOR.MINOR "
            f"version: {raw!r}"
        ) from exc
    if major > supported:
        raise AggregateError(
            f"manifest 'aggregate_manifest_version' {raw!r} is newer than this "
            f"tool supports (max major {supported}); upgrade abicheck"
        )


class CoverageStatus(str, Enum):
    """Was every *required* expected target actually analyzed?"""

    COMPLETE = "complete"  # every required target reported
    PARTIAL = "partial"  # at least one required target is unavailable
    EMPTY = "empty"  # no target could be analyzed at all


class OnMissingRequired(str, Enum):
    """Gate policy for a required target that never reported."""

    FAIL = "fail"  # incomplete required coverage fails the gate (default)
    WARN = "warn"  # report the gap but do not fail on coverage alone


class OnUnexpectedTarget(str, Enum):
    """Gate policy for a report whose target is not in the expected set."""

    INCLUDE = "include"  # count its real findings in the gate, not in coverage
    WARN = "warn"  # surface it and warn, but never fail the gate on it
    FAIL = "fail"  # any unexpected target fails the gate
    IGNORE = "ignore"  # drop it entirely


class AggregateError(ValueError):
    """A malformed input the caller must fix (usage error / exit 64)."""


class _MalformedGate(ValueError):
    """A gate/severity block that is *present but invalid*.

    Distinct from "no gate block at all": a report that never carried a
    ``severity`` block is an old/policy-less report and may legacy-fall-back to
    the verdict mapping, but a report that *does* carry a gate block which is
    corrupt must **fail closed** — the target becomes unavailable rather than
    silently reverting to the (possibly greener) legacy verdict path.
    """


#: The exit codes a report's own gate decision is allowed to declare — the
#: severity-aware scheme ``compare`` emits (0 pass / 1 addition-or-quality /
#: 2 potential-breaking / 4 abi-breaking). Anything else in a ``severity`` block
#: is a corrupt gate, not a value we silently reinterpret.
_VALID_GATE_EXIT = frozenset({0, 1, 2, 4})


@dataclass(frozen=True)
class GateInfo:
    """One target's own CI gate decision, as it recorded it.

    ``exit_code`` is in ``compare``'s severity-aware scheme (0 pass / 1
    addition-or-quality error / 2 potential-breaking error / 4 abi-breaking
    error). ``blocking_categories`` names which severity categories are
    failing. This is read from the report's ``severity`` block when present
    (the policy-aware, authoritative value), or synthesized from the verdict
    via :data:`_LEGACY_SEVERITY` for reports produced without a policy.
    """

    exit_code: int
    blocking: bool
    blocking_categories: tuple[str, ...] = ()
    from_report: bool = True  # False when legacy-derived from the verdict

    @classmethod
    def from_report_data(cls, data: Mapping[str, Any]) -> GateInfo | None:
        """Read the ``severity`` gate block, fail-closed.

        Returns ``None`` only when the report carries **no** ``severity`` key
        (an old/policy-less report the caller may legacy-fall-back). When a
        ``severity`` block is present it is validated strictly and a
        :class:`_MalformedGate` is raised on any inconsistency — a corrupt
        policy-blocked report must never silently revert to the greener legacy
        verdict path.
        """
        if "severity" not in data:
            return None
        sev = data.get("severity")
        if not isinstance(sev, dict):
            raise _MalformedGate("'severity' is not an object")
        exit_code = sev.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise _MalformedGate("'severity.exit_code' is missing or not an integer")
        if exit_code not in _VALID_GATE_EXIT:
            raise _MalformedGate(
                f"'severity.exit_code' {exit_code} is not one of "
                f"{sorted(_VALID_GATE_EXIT)}"
            )
        blocking = sev.get("blocking", exit_code != 0)
        if not isinstance(blocking, bool):
            raise _MalformedGate("'severity.blocking' is not a boolean")
        if blocking != (exit_code != 0):
            raise _MalformedGate(
                f"'severity.blocking'={blocking} contradicts exit_code={exit_code}"
            )
        cats = sev.get("blocking_categories", [])
        if not isinstance(cats, list) or any(not isinstance(c, str) for c in cats):
            raise _MalformedGate(
                "'severity.blocking_categories' is not a list of strings"
            )
        return cls(
            exit_code=exit_code,
            blocking=blocking,
            blocking_categories=tuple(cats),
            from_report=True,
        )

    @classmethod
    def from_scan_report(cls, data: Mapping[str, Any]) -> GateInfo | None:
        """Read a ``scan`` report's gate.

        A legacy-scheme ``scan`` JSON report records its gate only as a
        top-level ``exit_code`` (scheme 0 pass / 2 source break / 4 abi break /
        5 budget overflow / 6 not-comparable) rather than a ``compare``-style
        ``severity`` block. A severity-scheme ``scan --against`` (scan schema
        1.9+) additionally publishes that block at ``diff.severity``, which is
        preferred when present -- see the body. Keyed on
        ``scan_schema_version`` so arbitrary JSON that merely happens to carry
        an ``exit_code`` is not mistaken for a scan gate. Fails closed on a
        scan report whose gate is unusable, by either route.
        """
        if "scan_schema_version" not in data:
            return None
        # A severity-scheme `scan --against` (scan schema 1.9+) publishes a
        # real `compare`-shaped gate at `diff.severity`, whose `exit_code` is
        # this run's *pre-coverage* compatibility contribution. Prefer it, and
        # validate it through the very same strict reader a `compare` report
        # goes through, so the two commands' gates are read identically rather
        # than by two validators that can disagree.
        #
        # This is not an optimization -- it is what keeps the raw-code branch
        # below sound. That branch separates the coverage contribution by
        # arguing scan "has no native 1", which stopped being true once
        # severity reached scan: an error-level addition/quality finding is a
        # native compatibility 1, and folded with a coverage 1 it produced a
        # top-level 1 that the branch below would attribute entirely to
        # coverage and pass (Codex review). A severity-scheme report answers
        # the question directly, so it never reaches that argument.
        nested = _scan_severity_gate(data)
        if nested is not None:
            return nested
        code = data.get("exit_code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise _MalformedGate("scan report 'exit_code' is missing or not an integer")
        # A scan report's `exit_code` is already a *fold* of two orthogonal
        # axes — its own compatibility gate and ADR-049 Phase 7's
        # contract-coverage contribution, which `cli_scan_baseline` folds in
        # with `max`. Reading the folded number as the compatibility gate
        # therefore double-counts a coverage-only failure: it landed the
        # target in `blocking_targets` and made its profile `affected`, so a
        # `NO_CHANGE` scan target read as a compatibility problem where the
        # equivalent `compare` report did not (Codex review, reproduced).
        #
        # Scan's own scheme is what makes this separable rather than a guess:
        # it emits 0/2/4/5/6 and has no native 1, so a *raw* 1 can only be
        # the orthogonal contribution. Discriminating on the raw code matters
        # — 5 (budget overflow) and 6 (NOT_COMPARABLE) both map to 1 below
        # and are real compatibility-gate failures that must keep blocking.
        # The report must also state the contribution itself; a bare 1 with
        # nothing to attribute it to stays blocking, fail-closed.
        if code == COVERAGE_INCOMPLETE_EXIT and _contract_coverage_exit(data) == 1:
            return cls(exit_code=0, blocking=False, from_report=True)
        # Map scan's own scheme onto the aggregate gate scheme: 0/2/4 pass
        # through; any other scan failure (e.g. 5 budget overflow, or a
        # nonsensical value) is a non-ABI failure that still blocks, folded to
        # exit 1 — fail toward blocking, never toward a fake ABI-break 4.
        mapped = code if code in (0, 2, 4) else COVERAGE_INCOMPLETE_EXIT
        return cls(exit_code=mapped, blocking=mapped != 0, from_report=True)

    @classmethod
    def legacy_from_verdict(cls, verdict: Verdict | None) -> GateInfo:
        code = _LEGACY_SEVERITY.get(verdict, 0) if verdict is not None else 0
        return cls(exit_code=code, blocking=code > 0, from_report=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "blocking": self.blocking,
            "blocking_categories": list(self.blocking_categories),
            "from_report": self.from_report,
        }


@dataclass(frozen=True)
class TargetReport:
    """One target's contribution to the aggregate.

    ``compatibility_verdict`` is ``None`` exactly when the target is
    *unavailable* — its report was expected but never arrived or was
    unreadable — in which case ``gate`` is also ``None`` and ``reason``
    explains why. ``unexpected`` marks a report whose target was not in the
    expected set (a new/not-yet-declared matrix target).

    ``reason`` is also populated (with ``compatibility_verdict`` forced to
    ``BREAKING`` and ``gate`` a synthetic blocking one,
    ``blocking_categories=("not_comparable",)``) for an *analyzed* target
    whose report was an ADR-050 D2 ``verdict: null`` not-comparable result —
    see :func:`_load_report_file`. A consumer distinguishing that from a
    genuine break checks ``gate.blocking_categories`` for
    ``"not_comparable"``, the same way an operational-error report
    (``blocking_categories=("operational_error",)``) is already told apart.
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
        }
        if self.unexpected:
            d["unexpected"] = True
        profile_id = self.profile_id
        if profile_id is not None:
            d["profile_id"] = profile_id
        for key in ("report_path", "library", "reason"):
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_target": self.base_target,
            "profiles": list(self.profiles),
            "affected_profiles": list(self.affected_profiles),
            "incomplete_profiles": list(self.incomplete_profiles),
            "unanalyzed_profiles": list(self.unanalyzed_profiles),
            "contract_incomplete_profiles": list(self.contract_incomplete_profiles),
            "verdict_by_profile": dict(self.verdict_by_profile),
        }


#: Verdicts that do NOT make a profile "affected" in the profile matrix.
_UNAFFECTED_VERDICTS = frozenset({Verdict.NO_CHANGE, Verdict.COMPATIBLE})


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
        gated = list(self.analyzed) + list(self._gated_unexpected)
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
        gated = list(self.analyzed) + list(self._gated_unexpected)
        return max((t.contract_coverage_exit for t in gated), default=0)

    @property
    def contract_coverage_targets(self) -> tuple[str, ...]:
        """Targets whose contract coverage was incomplete.

        Chiefly the *why* behind :attr:`contract_coverage_exit`, so a reader
        is not left with a bare exit ``1`` and no target to look at — but not
        *only* that. A target that accepted incomplete coverage via
        ``contract.unresolved=warn`` contributes ``0`` while still listing
        real failures, and deriving this from the contribution alone dropped
        it from the aggregate entirely: ``incomplete_targets: []`` and no
        diagnostic, for a matrix in which a contract domain never closed
        (Codex review, fresh evidence). ``warn`` accepts incomplete assurance;
        ADR-049 Section 6.2 is explicit that it does not hide it, and an
        aggregate that omits the target hides it.

        Which of these gated is still readable per target, from each entry's
        own ``contract_coverage_exit`` — so no reader loses the narrower
        question by this answering the broader one.
        """
        gated = list(self.analyzed) + list(self._gated_unexpected)
        return tuple(
            sorted(
                t.target_id
                for t in gated
                if t.contract_coverage_incomplete or t.contract_coverage_exit > 0
            )
        )

    def exit_code(self) -> int:
        """The single CI gate exit code.

        The max of every gated target's own ``severity.exit_code`` (so a
        target's policy-blocked addition contributes ``1``, an API break ``2``,
        an ABI break ``4`` — never recomputed from the verdict), a coverage
        contribution of ``1`` when a required target is missing, and ADR-049's
        orthogonal contract-coverage contribution of ``1`` when any target's
        selected contract domain was short of evidence
        (:attr:`contract_coverage_exit`). All three fold with ``max``, so the
        two ``1``-valued axes can raise a clean ``0`` and neither can lower a
        real break's ``2``/``4``. ``64`` / malformed-input errors are raised as
        :class:`AggregateError`, never returned here.
        """
        gated = list(self.analyzed) + list(self._gated_unexpected)
        code = max((t.gate.exit_code for t in gated if t.gate is not None), default=0)
        if self.coverage_blocking:
            code = max(code, COVERAGE_INCOMPLETE_EXIT)
        code = max(code, self.contract_coverage_exit)
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
        """Group every expected target sharing a :attr:`TargetReport.base_target`
        (the same logical target checked under different profiles) into one
        entry each. Only targets whose ``target_id`` is ``check_id``-shaped
        (carries a parseable ``profile_id``) participate — a target with no
        profile encoding has nothing to group by, so it is silently excluded
        here (it is still fully represented in :attr:`targets`). Empty when
        no target in this result carries a ``profile_id`` (the common
        single-profile case), not an error. ``unexpected_targets`` are not
        grouped — a not-in-manifest report is a different axis entirely.

        A run plan can carry *more than one* check for the same
        (base_target, profile) pair — the same target checked under one
        profile at two different baseline channels or requested depths
        (e.g. ``libfoo@linux-gcc14#release@headers`` and
        ``libfoo@linux-gcc14#release@build``). All of a profile's checks are
        combined worst-verdict-wins (Codex review) rather than keying by
        profile_id alone, which would let a later, cleaner check silently
        overwrite an earlier, breaking one for the same profile. When one of
        a profile's *required* checks is unavailable while another did
        report, the unavailable one is never silently dropped either (Codex
        review) — it surfaces in ``incomplete_profiles`` alongside whatever
        verdict the completed check(s) contributed, rather than letting an
        incomplete profile read as flatly "clean"; an unavailable *optional*
        check does not, so this stays consistent with
        :attr:`AggregateResult.coverage` (also required-only). A profile
        whose analyzed verdict is compatible but whose gate is still
        blocking (e.g. an ``addition: error`` policy) is not "affected" by
        the verdict but is by the gate (Codex review) — either makes it
        ``affected``. A profile with *zero* analyzed checks at all (every
        check unavailable, whether required or optional) lands in
        ``unanalyzed_profiles`` instead of being silently folded into
        "clean" (Codex review) — an optional-only profile that never
        reported is not a coverage failure, but it was never actually
        checked either, so calling it "clean" would claim confidence this
        result does not have.
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

        # Named separately from the required-target coverage line above for
        # the same reason the JSON block is: a bare exit 1 with neither of
        # them explained is the failure this axis most easily causes.
        incomplete = self.contract_coverage_targets
        if incomplete:
            lines.append("")
            lines.append("Contract coverage:")
            # The contribution is stated separately from the target list
            # rather than folded into one clause: with `contract.unresolved=
            # warn` a listed target contributes 0, so "incomplete on X —
            # contributes 0" would read as a contradiction rather than as the
            # acceptance it is.
            lines.append(f"  incomplete on {', '.join(incomplete)}")
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
                lines.append(
                    f"  not gated on {', '.join(sorted(accepted))} "
                    "(that run contributed 0; listed, not gated)"
                )
            lines.append(
                f"  contributes {self.contract_coverage_exit} to the exit code "
                "(ADR-049 contract-coverage axis)"
            )

        matrix = self.profile_matrix
        if matrix:
            lines.append("")
            lines.append("Profile matrix:")
            for entry in matrix:
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
                    line += (
                        f" [incomplete coverage on "
                        f"{', '.join(entry.incomplete_profiles)}]"
                    )
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
                lines.append(line)

        lines.extend(render_finding_matrix_lines(self.finding_matrix))

        lines.append("Coverage:")
        if self.coverage is CoverageStatus.COMPLETE:
            lines.append("  Complete — every required target was analyzed.")
        else:
            missing = ", ".join(self.missing_required) or "(none)"
            gated = "" if self.coverage_blocking else " (advisory)"
            lines.append(
                f"  Incomplete — required target(s) unknown: {missing}.{gated}"
            )

        lines.append("Gate:")
        if self.passed:
            # Deliberately does NOT claim "coverage complete": under
            # --on-missing-required warn a required gap is reported above but
            # does not fail the gate, so a passing result can still have an
            # (advisory) coverage gap.
            lines.append(
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
            lines.append("  Failed — " + "; ".join(parts) + ".")

        return "\n".join(lines)

    def _render_target_line(self, t: TargetReport) -> str:
        tag = "" if t.required else " (optional)"
        if t.unexpected:
            tag = " (unexpected)"
        if not t.analyzed:
            return f"{t.target_id}{tag}: ⚠ unavailable — {t.reason or 'no report'}"
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
            # ADR-049 Phase 7's orthogonal axis, reported as its own block
            # rather than folded into "coverage" above: that one is about
            # whether every required *target reported*, this one about whether
            # a target that did report had the evidence to close its selected
            # contract domain. Both contribute 1 and they are not the same
            # question, so a consumer must be able to tell which raised the
            # exit ("Reports identify whether exit 1 comes from contract
            # coverage, gate severity, or aggregate required-target
            # coverage" -- plan Section 7).
            "contract_coverage": {
                "exit_contribution": self.contract_coverage_exit,
                "incomplete_targets": list(self.contract_coverage_targets),
            },
            "targets": [t.to_dict() for t in self.targets],
            "unexpected_targets": [t.to_dict() for t in self.unexpected_targets],
            "profile_matrix": [e.to_dict() for e in self.profile_matrix],
            "finding_matrix": [e.to_dict() for e in self.finding_matrix],
        }


# --- parsing / loading ------------------------------------------------------


def parse_report_verdict(data: Mapping[str, Any]) -> Verdict | None:
    """Extract the compatibility verdict from a parsed report."""
    raw = data.get("verdict")
    if not isinstance(raw, str):
        return None
    try:
        return Verdict(raw)
    except ValueError:
        return None


def target_id_from_path(path: Path, *, prefix: str = DEFAULT_REPORT_PREFIX) -> str:
    """Derive a target id from a report file's stem (convenience fallback)."""
    stem = path.stem
    if prefix and stem.startswith(prefix):
        stem = stem[len(prefix) :]
    return stem


@dataclass(frozen=True)
class _LoadedReport:
    target_id: str
    verdict: Verdict | None
    gate: GateInfo | None
    library: str | None
    head_sha: str | None
    reason: str | None
    path: Path
    #: ADR-049 Phase 7's orthogonal contract-coverage contribution, read off
    #: the report's own ``contract_coverage_exit_contribution`` (schema 2.26).
    #: ``0`` for every report that carries none -- a run without
    #: ``--contract-evaluation`` selected no contract domain, so it cannot be
    #: short of evidence for one.
    contract_coverage_exit: int = 0
    #: Whether the report listed any coverage failure at all -- true even
    #: when the contribution above is ``0`` because ``contract.unresolved=
    #: warn`` accepted it. Reported, never folded into an exit code.
    contract_coverage_incomplete: bool = False
    #: Whether the report stated a usable contribution at all -- see
    #: :func:`_contract_coverage_declared`. Distinguishes a real
    #: ``contract.unresolved=warn`` acceptance from a silent or malformed
    #: one, so prose about the run cannot claim a policy it never set.
    contract_coverage_declared: bool = False
    #: ``None`` on every failure branch below (unreadable, malformed gate,
    #: operational error, not comparable), since none of those establish what
    #: the comparison did or did not find. Otherwise the report's own
    #: :func:`~abicheck.aggregate_findings.parse_report_findings` result.
    findings: ReportFindings | None = None


def _contract_coverage_declared(data: Mapping[str, Any]) -> bool:
    """Whether the report actually *stated* a usable coverage contribution.

    :func:`_contract_coverage_exit` fails open, so its ``0`` covers three
    different situations: the run declared ``0``, the run declared nothing,
    and the run declared something unusable. That is the right answer for
    the gate -- none of them may block -- but not for prose *about* the run:
    only the first is a ``contract.unresolved=warn`` acceptance, and saying
    so about the other two attributes a policy decision the report never
    made (CodeRabbit review).
    """
    return any(
        _is_valid_contribution(block.get("contract_coverage_exit_contribution"))
        for block in contract_coverage_blocks(data)
    )


def _scan_severity_gate(data: Mapping[str, Any]) -> GateInfo | None:
    """A severity-scheme scan report's own ``diff.severity`` gate, if it has one.

    ``None`` for a legacy-scheme scan (which runs no severity gate and so
    publishes no block), leaving :meth:`GateInfo.from_scan_report`'s raw
    top-level ``exit_code`` path to answer.

    Delegates to :meth:`GateInfo.from_report_data` rather than re-validating:
    ``cli_scan_baseline`` builds this block with the same
    ``reporter._build_severity_json`` that writes ``compare``'s, so a second
    validator here could only ever disagree with the first. That also means a
    *corrupt* scan gate block fails closed (``_MalformedGate``) exactly as a
    corrupt ``compare`` one does, instead of silently falling through to the
    greener raw-code path -- the same fail-closed principle the surrounding
    reader already applies.
    """
    diff = data.get("diff")
    if not isinstance(diff, Mapping) or "severity" not in diff:
        return None
    return GateInfo.from_report_data(diff)


def _contract_coverage_exit(data: Mapping[str, Any]) -> int:
    """The report's own ADR-049 contract-coverage contribution (``0``/``1``).

    Read rather than recomputed: ``contract_coverage_exit_contribution``
    (report schema 2.26) is the number that actually gated the run that wrote
    it -- already reflecting the selected ``--contract`` domain and any
    ``contract.unresolved=warn`` acceptance -- and this aggregate holds none
    of the evidence needed to answer it again. A per-target run that accepted
    incomplete coverage must not have the aggregate re-impose it.

    Fails *open*, unlike the ``severity`` gate's ``_MalformedGate``: an absent
    or unusable value means "this report says nothing about a contract
    domain", which is the honest reading for every pre-2.26 report and every
    run without ``--contract-evaluation``. Treating it as a failure would make
    the aggregate block on reports that never asked the question.

    A ``scan --against`` report carries the field one level down, inside its
    ``diff`` block (``cli_scan_baseline`` writes it into the summary that
    becomes ``ScanOutcome.to_dict()['diff']``), so that block is consulted for
    a scan report too. Without it the aggregate still *failed* -- the scan's
    own top-level ``exit_code`` already folds the contribution -- but reported
    ``contract_coverage.exit_contribution: 0`` and an empty
    ``incomplete_targets`` beside it, hiding which axis caused the failure
    (Codex review). That is exactly what plan Section 7 requires a report to
    make identifiable. Keyed on ``scan_schema_version``, mirroring
    :meth:`GateInfo.from_scan_report`, so arbitrary JSON that merely happens
    to carry a ``diff`` key is never mined for one. Consulted whenever the
    root value is *unusable*, not merely absent, so a malformed root key
    cannot shadow a valid nested one.
    """
    for block in contract_coverage_blocks(data):
        raw = block.get("contract_coverage_exit_contribution")
        if _is_valid_contribution(raw):
            return raw
    return 0


def contract_coverage_block_paths(data: Mapping[str, Any]) -> list[tuple[str, ...]]:
    """Key paths within *data* that may carry a contract-coverage block.

    The single definition of *where these fields live*. Two consumers need
    it and they need it to agree exactly: this module reads the blocks, and
    ``buildsource.check_report._neutralize_gate`` zeroes them for
    ``gate-mode: advisory``. A hand-written copy of the traversal there
    already missed the scan-shaped nested block once (Codex review), so the
    shape is stated once and both sides derive from it.

    Paths rather than the blocks themselves, because a writer additionally
    has to *rebind* each container it touches -- ``augment_report`` copies
    only the top level, so mutating a nested block in place would reach back
    into the caller's own report (Codex review, again).
    """
    paths: list[tuple[str, ...]] = [()]
    # A `compare` report may carry an unrelated `diff` key; only a scan
    # report nests its coverage fields, so the marker gates the descent.
    if "scan_schema_version" not in data:
        return paths
    if isinstance(data.get("diff"), Mapping):
        paths.append(("diff",))
    report = data.get("report")
    if isinstance(report, Mapping) and isinstance(report.get("diff"), Mapping):
        paths.append(("report", "diff"))
    return paths


def contract_coverage_blocks(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every block of *data* that may carry a contract-coverage contribution.

    The read-side view of :func:`contract_coverage_block_paths`.
    """
    blocks: list[Mapping[str, Any]] = []
    for path in contract_coverage_block_paths(data):
        node: Any = data
        for key in path:
            node = node[key]
        blocks.append(node)
    return blocks


def _is_valid_contribution(raw: object) -> TypeGuard[int]:
    """Whether *raw* is a usable ``0``/``1`` contract-coverage contribution.

    Exactly ``0`` or ``1``, never merely "a non-negative integer": the axis is
    defined as a ``0``/``1`` floor, and letting a malformed ``2`` through would
    make this aggregate exit ``2`` -- indistinguishable from a source/API
    break -- and emit a ``contract_coverage.exit_contribution`` its own schema
    rejects (Codex review).

    The ``bool`` rejection has to come *first*, and both the root check and the
    final check must route through this one predicate rather than spelling the
    test twice. Testing a root value with a bare ``raw not in (0, 1)`` looks
    equivalent but is not: ``True == 1`` in Python, so a scan report whose root
    key is the malformed ``true`` satisfied that membership test, won against a
    perfectly valid nested ``diff`` value, and was only then rejected by the
    type check -- reporting ``0`` for a scan whose own exit code had already
    folded a ``1`` (Codex review, fresh evidence). A *string* root fell through
    correctly, which is what made the gap easy to miss.
    """
    return not isinstance(raw, bool) and isinstance(raw, int) and raw in (0, 1)


def _contract_coverage_incomplete(data: Mapping[str, Any]) -> bool:
    """Whether the report listed any contract-coverage failure at all.

    Tracked *separately* from :func:`_contract_coverage_exit` because the two
    genuinely differ: ``contract.unresolved=warn`` zeroes the exit floor and
    changes nothing else, so an accepting target's report still carries a
    populated ``contract_coverage_failures`` ledger beside a contribution of
    ``0`` (ADR-049 Section 6.2 -- accepting incomplete assurance is not
    hiding it). Deriving incompleteness from the contribution alone therefore
    reported ``incomplete_targets: []`` and printed no diagnostic for a
    matrix in which a target's contract domain never closed, which is the
    hiding that mode is explicitly not supposed to do (Codex review, fresh
    evidence).

    Searches the same blocks as the contribution, through the same shared
    :func:`contract_coverage_blocks` -- the two answer different questions
    off the same keys in the same places, so a nesting one knows and the other
    does not is a guaranteed divergence. A non-list, or an empty list, is
    "nothing to report" -- the same fail-open reading as the contribution, and
    correct for every pre-2.26 report and every run without
    ``--contract-evaluation``.
    """
    for block in contract_coverage_blocks(data):
        failures = block.get("contract_coverage_failures")
        if isinstance(failures, list):
            return bool(failures)
    return False


def _incomplete_findings(data: dict[str, Any]) -> ReportFindings:
    """Whatever findings *data* lists, explicitly not accounted as exhaustive.

    For a report whose run did not finish cleanly. `parse_report_findings`
    already refuses completeness to every release-shaped document, so this
    is belt-and-braces rather than the only guard — but it states the
    intent at the call site, where the reason (the run errored) actually
    lives, instead of relying on a property of the shape it happens to have.
    """
    return replace(parse_report_findings(data), complete=False)


def _load_report_file(path: Path, *, prefix: str) -> _LoadedReport:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return _LoadedReport(
            target_id_from_path(path, prefix=prefix),
            None,
            None,
            None,
            None,
            f"unreadable report ({type(exc).__name__})",
            path,
        )
    if not isinstance(data, dict):
        return _LoadedReport(
            target_id_from_path(path, prefix=prefix),
            None,
            None,
            None,
            None,
            "report is not a JSON object",
            path,
        )
    # Prefer the report's own self-identified target id; fall back to filename.
    own_id = data.get("target_id")
    target_id = (
        str(own_id)
        if isinstance(own_id, str) and own_id
        else target_id_from_path(path, prefix=prefix)
    )
    head_sha_raw = data.get("head_sha")
    head_sha = str(head_sha_raw) if isinstance(head_sha_raw, str) else None
    # A compare-release *operational* failure carries top-level ``verdict:
    # "ERROR"`` (a library failed to dump/extract/compare) — the release path
    # ranks it above BREAKING and floors its exit to 4. "ERROR" is not a
    # ``Verdict`` enum member, so preserve it here as a blocking gate: otherwise
    # it falls through as a verdictless (unavailable) report that a warn /
    # optional / unexpected policy could let pass, silently downgrading a hard
    # operational failure to a coverage gap.
    if data.get("verdict") == _OPERATIONAL_ERROR_VERDICT:
        return _LoadedReport(
            target_id=target_id,
            verdict=Verdict.BREAKING,
            gate=GateInfo(
                exit_code=4,
                blocking=True,
                blocking_categories=("operational_error",),
                from_report=True,
            ),
            library=data.get("library"),
            head_sha=head_sha,
            reason=None,
            path=path,
            contract_coverage_exit=_contract_coverage_exit(data),
            contract_coverage_incomplete=_contract_coverage_incomplete(data),
            contract_coverage_declared=_contract_coverage_declared(data),
            # An operational ERROR means *a* library failed, not that nothing
            # was compared: `_format_release_json` emits `bundle_findings`/
            # `matrix_findings` from whatever did complete, regardless of the
            # top-level verdict (Codex review). Dropping them would lose real
            # evidence from the one profile most likely to differ. Never
            # complete, though — a run that errored cannot account for
            # everything, so these findings can convict their own profile and
            # clear no other.
            findings=_incomplete_findings(data),
        )
    # ADR-050 D2: a native compare/compare-release not_comparable report
    # carries a real ``verdict: null`` (JSON null, not a missing key) plus a
    # structured ``reason: {kind, message}`` (schema 2.17) -- distinct from
    # an ordinary verdictless/malformed report (which falls through to the
    # generic "carried no ABI verdict" reason below), and must never be
    # silently folded into the same "unavailable" bucket a report that
    # simply never arrived gets: unavailable is a *coverage* gap, gated only
    # for required targets and skipped entirely under
    # --on-missing-required warn / discovered-only mode, while a genuine
    # not_comparable result is exactly the "we don't actually know if this
    # is safe" case this whole ADR exists to never treat as silently OK.
    # Mapped to the same forced-blocking shape the operational-error branch
    # above already uses -- a synthetic BREAKING verdict with a gate that
    # bypasses the legacy/report gate lookup entirely -- so it is folded
    # into exit_code() unconditionally, coverage settings notwithstanding.
    if "verdict" in data and data.get("verdict") is None:
        reason_obj = data.get("reason")
        if isinstance(reason_obj, dict) and isinstance(reason_obj.get("kind"), str):
            kind = reason_obj["kind"]
            message = reason_obj.get("message")
            detail = f": {message}" if isinstance(message, str) and message else ""
            return _LoadedReport(
                target_id=target_id,
                verdict=Verdict.BREAKING,
                gate=GateInfo(
                    exit_code=4,
                    blocking=True,
                    blocking_categories=("not_comparable",),
                    from_report=True,
                ),
                library=data.get("library"),
                head_sha=head_sha,
                reason=f"not comparable ({kind}){detail}",
                path=path,
                contract_coverage_exit=_contract_coverage_exit(data),
                contract_coverage_incomplete=_contract_coverage_incomplete(data),
            )
    verdict = parse_report_verdict(data)
    gate: GateInfo | None = None
    if verdict is not None:
        try:
            # A ``compare`` severity block wins; else a ``scan`` top-level gate;
            # else the legacy verdict mapping. Only an *absent* gate block
            # legacy-falls-back — a *malformed* one fails closed below.
            gate = GateInfo.from_report_data(data)
            if gate is None:
                gate = GateInfo.from_scan_report(data)
        except _MalformedGate as exc:
            # Fail closed: a present-but-corrupt gate makes the target
            # unavailable (unknown), never silently the greener legacy path.
            return _LoadedReport(
                target_id=target_id,
                verdict=None,
                gate=None,
                library=data.get("library"),
                head_sha=head_sha,
                reason=f"report gate decision is malformed: {exc}",
                path=path,
            )
        if gate is None:
            gate = GateInfo.legacy_from_verdict(verdict)
    return _LoadedReport(
        target_id=target_id,
        verdict=verdict,
        gate=gate,
        library=data.get("library"),
        head_sha=head_sha,
        reason=None if verdict is not None else "report carried no ABI verdict",
        path=path,
        contract_coverage_exit=_contract_coverage_exit(data),
        contract_coverage_incomplete=_contract_coverage_incomplete(data),
        contract_coverage_declared=_contract_coverage_declared(data),
        # Only a report that produced a real verdict has a finding set worth
        # reading: a verdictless one is unavailable, and its `changes` array
        # (if any) describes a comparison that never reached a conclusion.
        findings=parse_report_findings(data) if verdict is not None else None,
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


# --- expected-set specification --------------------------------------------


@dataclass(frozen=True)
class ExpectedTargets:
    """The declared expected-target set (from a manifest or CLI flags)."""

    #: target_id → required
    targets: Mapping[str, bool]
    head_sha: str | None = None

    @classmethod
    def from_manifest_file(cls, path: Path) -> ExpectedTargets:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise AggregateError(f"cannot read manifest {path}: {exc}") from exc
        return cls.from_manifest_data(data)

    @classmethod
    def from_manifest_data(cls, data: Any) -> ExpectedTargets:
        if not isinstance(data, dict):
            raise AggregateError("manifest must be a JSON object")
        _check_manifest_version(data.get("aggregate_manifest_version"))
        raw = data.get("targets")
        if not isinstance(raw, list) or not raw:
            raise AggregateError("manifest 'targets' must be a non-empty list")
        targets: dict[str, bool] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                raise AggregateError(f"manifest target must be an object: {entry!r}")
            tid = entry.get("id")
            if not isinstance(tid, str) or not tid:
                raise AggregateError(f"manifest target needs a string 'id': {entry!r}")
            if tid in targets:
                raise AggregateError(f"duplicate manifest target id: {tid!r}")
            required = entry.get("required", True)
            if not isinstance(required, bool):
                raise AggregateError(
                    f"manifest target 'required' must be a boolean: {entry!r}"
                )
            targets[tid] = required
        head_sha = data.get("head_sha")
        if "head_sha" in data and (not isinstance(head_sha, str) or not head_sha):
            # A present-but-malformed head_sha must not silently become None —
            # that would disable the commit-identity guard the manifest asked
            # for. Fail loud instead.
            raise AggregateError("manifest 'head_sha' must be a non-empty string")
        return cls(targets=targets, head_sha=head_sha)

    @classmethod
    def from_lists(
        cls, required: Iterable[str], optional: Iterable[str] = ()
    ) -> ExpectedTargets:
        targets: dict[str, bool] = {tid: False for tid in optional}
        for tid in required:
            targets[tid] = True
        if not targets:
            raise AggregateError("no expected targets given")
        return cls(targets=targets)


# --- the aggregation itself -------------------------------------------------


def aggregate(
    expected: ExpectedTargets | None,
    found: Mapping[str, _LoadedReport],
    *,
    on_missing_required: OnMissingRequired = OnMissingRequired.FAIL,
    on_unexpected_target: OnUnexpectedTarget = OnUnexpectedTarget.INCLUDE,
) -> AggregateResult:
    """Reconcile an expected-target set against the reports found.

    *expected* is ``None`` only in discovered-only mode, where the reports
    present *are* the expected set and coverage is not gated.
    """
    discovered_only = expected is None
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
            findings=report.findings,
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
    )


def aggregate_reports_dir(
    reports_dir: Path,
    *,
    expected: ExpectedTargets | None = None,
    discovered_only: bool = False,
    on_missing_required: OnMissingRequired = OnMissingRequired.FAIL,
    on_unexpected_target: OnUnexpectedTarget = OnUnexpectedTarget.INCLUDE,
    prefix: str = DEFAULT_REPORT_PREFIX,
) -> AggregateResult:
    """Load a reports dir and aggregate against an expected set.

    Exactly one of *expected* or *discovered_only* selects the mode. In
    discovered-only mode the reports present become the expected set and
    coverage is not gated — the caller must opt into that explicitly, since it
    cannot detect a missing target. Raises :class:`AggregateError` for
    malformed input (a usage error, exit 64).
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
    )
