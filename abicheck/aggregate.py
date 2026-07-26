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
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .change_registry_types import Verdict

#: Machine-readable schema version of the ``to_dict()`` / ``--format json``
#: output. Bump on any incompatible change to that structure.
AGGREGATE_SCHEMA_VERSION = "1.1"

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
        """Read a ``scan`` report's own top-level ``exit_code`` as the gate.

        A ``scan`` JSON report records its gate as a top-level ``exit_code``
        (scheme 0 pass / 2 source break / 4 abi break / 5 budget overflow)
        rather than a ``compare``-style ``severity`` block. Keyed on
        ``scan_schema_version`` so arbitrary JSON that merely happens to carry
        an ``exit_code`` is not mistaken for a scan gate. Fails closed on a
        scan report whose ``exit_code`` is unusable.
        """
        if "scan_schema_version" not in data:
            return None
        code = data.get("exit_code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise _MalformedGate("scan report 'exit_code' is missing or not an integer")
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
    #: ``NO_CHANGE`` nor ``COMPATIBLE`` (i.e. contributed a break, source
    #: break, or risk finding). A profile with *zero* analyzed checks (every
    #: check for it is unavailable) is never "affected" here — no verdict at
    #: all is a coverage gap, not a break — but see ``incomplete_profiles``:
    #: a profile can be BOTH affected (one check broke) AND incomplete
    #: (another of its checks never reported) at once.
    affected_profiles: tuple[str, ...]
    #: Subset of ``profiles`` where at least one of that profile's checks
    #: (this target can have more than one, at different baseline
    #: channels/requested depths) is unavailable — no report arrived for it.
    #: ``verdict_by_profile``/``affected_profiles`` are still computed from
    #: whichever of that profile's checks DID report (Codex review: an
    #: unavailable check must never be silently dropped from the picture,
    #: since a clean *completed* check plus a missing required one is a
    #: coverage gap, not "this profile is clean").
    incomplete_profiles: tuple[str, ...]
    #: profile -> worst analyzed verdict value, or ``None`` when *every*
    #: check for that profile is unavailable (no analyzed check at all).
    verdict_by_profile: dict[str, str | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_target": self.base_target,
            "profiles": list(self.profiles),
            "affected_profiles": list(self.affected_profiles),
            "incomplete_profiles": list(self.incomplete_profiles),
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

    def exit_code(self) -> int:
        """The single CI gate exit code.

        The max of every gated target's own ``severity.exit_code`` (so a
        target's policy-blocked addition contributes ``1``, an API break ``2``,
        an ABI break ``4`` — never recomputed from the verdict) and a coverage
        contribution of ``1`` when a required target is missing. ``64`` /
        malformed-input errors are raised as :class:`AggregateError`, never
        returned here.
        """
        gated = list(self.analyzed) + list(self._gated_unexpected)
        code = max((t.gate.exit_code for t in gated if t.gate is not None), default=0)
        if self.coverage_blocking:
            code = max(code, COVERAGE_INCOMPLETE_EXIT)
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
        a profile's checks is unavailable while another did report, the
        unavailable one is never silently dropped either (Codex review) — it
        surfaces in ``incomplete_profiles`` alongside whatever verdict the
        completed check(s) contributed, rather than letting an incomplete
        profile read as flatly "clean".
        """
        by_target: dict[str, dict[str, list[TargetReport]]] = {}
        for t in self.targets:
            pid = t.profile_id
            if pid is None:
                continue
            by_target.setdefault(t.base_target, {}).setdefault(pid, []).append(t)

        entries = []
        for base_target in sorted(by_target):
            reports_by_profile = by_target[base_target]
            profiles = tuple(sorted(reports_by_profile))
            affected = []
            incomplete = []
            verdict_by_profile: dict[str, str | None] = {}
            for pid in profiles:
                reports = reports_by_profile[pid]
                if any(r.compatibility_verdict is None for r in reports):
                    incomplete.append(pid)
                verdicts = [
                    r.compatibility_verdict
                    for r in reports
                    if r.compatibility_verdict is not None
                ]
                if not verdicts:
                    verdict_by_profile[pid] = None
                    continue
                worst = max(verdicts, key=lambda v: _VERDICT_RANK[v])
                verdict_by_profile[pid] = worst.value
                if worst not in _UNAFFECTED_VERDICTS:
                    affected.append(pid)
            entries.append(
                ProfileMatrixEntry(
                    base_target=base_target,
                    profiles=profiles,
                    affected_profiles=tuple(affected),
                    incomplete_profiles=tuple(incomplete),
                    verdict_by_profile=verdict_by_profile,
                )
            )
        return tuple(entries)

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

        matrix = self.profile_matrix
        if matrix:
            lines.append("")
            lines.append("Profile matrix:")
            for entry in matrix:
                if entry.affected_profiles:
                    line = (
                        f"  {entry.base_target}: affected on "
                        f"{', '.join(entry.affected_profiles)} "
                        f"(checked on {', '.join(entry.profiles)})"
                    )
                else:
                    line = (
                        f"  {entry.base_target}: clean on all checked profiles "
                        f"({', '.join(entry.profiles)})"
                    )
                if entry.incomplete_profiles:
                    line += (
                        f" [incomplete coverage on "
                        f"{', '.join(entry.incomplete_profiles)}]"
                    )
                lines.append(line)

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
            "targets": [t.to_dict() for t in self.targets],
            "unexpected_targets": [t.to_dict() for t in self.unexpected_targets],
            "profile_matrix": [e.to_dict() for e in self.profile_matrix],
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
