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

"""ADR-066 S1: offline longitudinal compatibility history.

Composes the existing pairwise :func:`abicheck.checker.compare` engine
across a user-supplied, explicitly-ordered chain of stored snapshots and
derives per-entity lifecycle events (``first_observed``/``introduced``/
``deprecated``/``removed``/``reintroduced``, ADR-066 D2) plus a coverage
report naming the gaps and honesty caveats the ADR requires. This module
never runs a second, independently-invented N-way diff: every fact below is
read off a :class:`~abicheck.checker_types.DiffResult` that
:func:`abicheck.checker.compare` already produced for one adjacent pair.

Owner package: ``workflows/`` (ADR-061) — this coordinates a *sequence* of
comparisons the way ``workflows/aggregate`` coordinates a *set* of them, and
``checker.py`` itself is already legacy-classified into this same layer
(``architecture/modules.yaml``), so calling it here is an intra-layer
dependency, not a new cross-layer edge. ``model/`` was considered instead
(the "lifecycle event" shape is a new fact), but a *value shape* that never
changes without the compare/serialization machinery that produces it earns
its keep as a `dataclass` living next to that machinery, and every sibling
"S1 offline over N snapshots" precedent in ``workflows/`` (``aggregate``,
``release_scope``) follows the same pattern: `model/` holds the atoms
(`EntityId`, `Change`, `AbiSnapshot`), `workflows/` holds a *sequence's*
derived facts.

**Deliberate S1 scope (recorded here and in the ADR-066 amendment, S0):**

* **Ordering is explicit, never inferred.** D1's "declared version scheme"
  ordering (``versioning: scheme:``) is S2/D4 work — this slice orders
  entries exactly as the caller lists them (or, with no explicit ``versions``
  given, by each stored snapshot's own ``AbiSnapshot.version`` in the order
  supplied). Passing entries out of the project's real release order silently
  produces a history for *that* order, not the project's true one — S1 does
  not second-guess the caller.
* **Correspondence key, not the full D2 algorithm.** An entity's identity
  across the chain is its existing ``Change.entity_id`` (the identity the
  compare engine already resolved for that pair) when present, or
  ``(entity_kind, Change.symbol)`` otherwise — never a second identity
  scheme invented here. D2's full correspondence step (overload
  signature-discriminator disambiguation, TU-relative provenance
  corroboration for a "possible correspondence") is *not* implemented: a
  function overload whose signature changes carries a different
  ``EntityId`` (its mangled/signature discriminator lives in
  ``EntityId.extra``) and is correctly *not* asserted continuous by this
  slice — exactly D2's own documented fallback ("otherwise history records a
  removed and a first_observed/introduced pair"), so this is a conservative
  narrowing of D2, not a violation of it. Full correspondence-with-
  corroboration is deferred to a later slice (see the ADR-066 amendment).
* **Absence honesty is a bounded heuristic, not ADR-065's full evidence
  ledger.** A ``removed`` event is annotated ``evidence_uncertain=True``
  whenever the pairwise comparison's own ``DiffResult.confidence`` is not
  ``HIGH`` — a real but coarse proxy for "this snapshot's evidence may not
  have been complete enough to prove absence" (ADR-066 D2's 2026-09
  clarification). The full per-provider completeness ledger ADR-065 defines
  for a single pairwise comparison is not reused here; wiring it through is
  future work, tracked in the ADR-066 amendment.
* **Coverage-gap detection is a bounded SemVer heuristic.** With no
  project-declared ``versioning: scheme:`` (D4, not yet implemented), a
  "missing intermediate release" can only be *guessed* from version-label
  shape. Two adjacent, SemVer-parseable labels that are not consecutive
  under the ordinary major/minor/patch increment rule are reported as an
  ``unknown_interval`` gap; anything that does not parse as SemVer reports
  no gap verdict at all (``unknown``), never a false "contiguous".

See ``docs/contribute/adr/066-longitudinal-history-and-versioning-policy.md``
and the amendment recorded there for S0's design record and its full
trade-off discussion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from ..checker import compare
from ..checker_types import Change, DiffResult
from ..model.change_catalog.kinds import ChangeKind
from ..model.identity import EntityId
from ..model.snapshot import AbiSnapshot
from ..policy.evidence_status import Confidence
from ..policy.versioning_policy import VersioningPolicy
from ..serialization import load_snapshot

#: The five D2 lifecycle-event kinds this module can emit. ``changed`` (D2's
#: sixth vocabulary word) is deliberately not emitted by S1 — see the module
#: docstring's "Correspondence key" bullet; a signature/value change that D2
#: would assert as ``changed`` is conservatively read as removed+introduced
#: here instead.
LifecycleEventKind = Literal[
    "first_observed", "introduced", "deprecated", "removed", "reintroduced"
]

ENTITY_KIND_FUNCTION = "function"
ENTITY_KIND_VARIABLE = "variable"
ENTITY_KIND_TYPE = "type"

#: Which entity kind a presence-transition ``ChangeKind`` speaks about.
#: ``TYPE_ADDED``/``TYPE_REMOVED`` cover both ``RecordType`` and ``EnumType``
#: (``diff_types._diff_enums`` reuses the same two kinds — see that module),
#: so both fold onto ``ENTITY_KIND_TYPE``.
_ADDED_KINDS: dict[ChangeKind, str] = {
    ChangeKind.FUNC_ADDED: ENTITY_KIND_FUNCTION,
    ChangeKind.VAR_ADDED: ENTITY_KIND_VARIABLE,
    ChangeKind.TYPE_ADDED: ENTITY_KIND_TYPE,
}
_REMOVED_KINDS: dict[ChangeKind, str] = {
    ChangeKind.FUNC_REMOVED: ENTITY_KIND_FUNCTION,
    ChangeKind.FUNC_REMOVED_ELF_ONLY: ENTITY_KIND_FUNCTION,
    ChangeKind.VAR_REMOVED: ENTITY_KIND_VARIABLE,
    ChangeKind.TYPE_REMOVED: ENTITY_KIND_TYPE,
}
_DEPRECATED_ADDED_KINDS: dict[ChangeKind, str] = {
    ChangeKind.FUNC_DEPRECATED_ADDED: ENTITY_KIND_FUNCTION,
    ChangeKind.VAR_DEPRECATED_ADDED: ENTITY_KIND_VARIABLE,
    ChangeKind.TYPE_DEPRECATED_ADDED: ENTITY_KIND_TYPE,
    ChangeKind.ENUM_DEPRECATED_ADDED: ENTITY_KIND_TYPE,
}
_DEPRECATED_REMOVED_KINDS: dict[ChangeKind, str] = {
    ChangeKind.FUNC_DEPRECATED_REMOVED: ENTITY_KIND_FUNCTION,
    ChangeKind.VAR_DEPRECATED_REMOVED: ENTITY_KIND_VARIABLE,
    ChangeKind.TYPE_DEPRECATED_REMOVED: ENTITY_KIND_TYPE,
    ChangeKind.ENUM_DEPRECATED_REMOVED: ENTITY_KIND_TYPE,
}


class HistoryError(ValueError):
    """Raised for a malformed history request (bad ordering, empty input)."""


@dataclass(frozen=True)
class HistoryEntry:
    """One resolved point in the ordered chain — a stored snapshot plus its
    release label. Index-only ordering; provenance beyond what the loaded
    snapshot itself carries (``git_commit``/``git_tag``/``created_at``) is
    read straight off it, never re-derived."""

    index: int
    version: str
    path: str
    snapshot: AbiSnapshot = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "version": self.version,
            "path": self.path,
            "git_commit": self.snapshot.git_commit,
            "git_tag": self.snapshot.git_tag,
            "created_at": self.snapshot.created_at,
        }


@dataclass(frozen=True)
class LifecycleEvent:
    """One D2 lifecycle event for one entity, observed at one chain step."""

    entity_kind: str
    entity_key: str
    display_name: str
    event: LifecycleEventKind
    version: str
    index: int
    detail: str | None = None
    #: See the module docstring's "Absence honesty" bullet — set only on a
    #: ``removed`` event whose backing comparison had non-HIGH confidence.
    evidence_uncertain: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_kind": self.entity_kind,
            "entity_key": self.entity_key,
            "display_name": self.display_name,
            "event": self.event,
            "version": self.version,
            "index": self.index,
            "detail": self.detail,
            "evidence_uncertain": self.evidence_uncertain,
        }


@dataclass(frozen=True)
class CoverageGap:
    """A suspected missing intermediate release between two supplied entries
    (see the module docstring's "Coverage-gap detection" bullet)."""

    from_version: str
    to_version: str
    kind: Literal["unknown_interval"]
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "from_version": self.from_version,
            "to_version": self.to_version,
            "kind": self.kind,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PairwiseSummary:
    """A thin, transparent record of the underlying ``compare()`` call for
    one adjacent pair — never re-derived from the events; kept so a caller
    can audit exactly which comparison backs a given lifecycle event."""

    from_version: str
    to_version: str
    verdict: str
    change_count: int
    confidence: str

    def to_dict(self) -> dict[str, object]:
        return {
            "from_version": self.from_version,
            "to_version": self.to_version,
            "verdict": self.verdict,
            "change_count": self.change_count,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class LongitudinalHistoryResult:
    """The full ADR-066 S1 output: an ordered chain, its lifecycle events,
    and coverage over the supplied history."""

    library: str
    entries: tuple[HistoryEntry, ...]
    events: tuple[LifecycleEvent, ...]
    gaps: tuple[CoverageGap, ...]
    pairwise: tuple[PairwiseSummary, ...]
    #: ADR-066 S2: the versioning policy's ``deprecation_window`` control,
    #: evaluated over ``events`` (see
    #: :func:`abicheck.policy.versioning_policy.evaluate_deprecation_compliance`).
    #: Empty whenever :func:`build_longitudinal_history`/
    #: :func:`run_history_request` were called with no ``versioning_policy``
    #: (the default) -- a history-report-only, orthogonal fact that never
    #: feeds back into ``events``, ``gaps``, or any pairwise ``compare()``
    #: verdict (ADR-066's "orthogonal axis" requirement).
    deprecation_compliance: tuple[DeprecationComplianceFinding, ...] = ()
    #: Every ``*_DEPRECATED_REMOVED`` transition observed while building
    #: ``events``, as ``(entity_key, index)`` -- plumbing for
    #: :func:`evaluate_deprecation_compliance`, not a sixth D2 lifecycle-
    #: event kind (see :func:`_events_from_pair`'s own comment). Not part
    #: of ``to_dict()``'s published shape: it is an internal correctness
    #: signal for this module's own evaluator, not a new report fact.
    deprecation_resets: tuple[tuple[str, int], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "abicheck.longitudinal-history/v1",
            "library": self.library,
            "entries": [e.to_dict() for e in self.entries],
            "events": [e.to_dict() for e in self.events],
            "coverage": {
                "gaps": [g.to_dict() for g in self.gaps],
            },
            "pairwise": [p.to_dict() for p in self.pairwise],
            "deprecation_compliance": [
                f.to_dict() for f in self.deprecation_compliance
            ],
        }


# ---------------------------------------------------------------------------
# Correspondence key (see module docstring's "Correspondence key" bullet)
# ---------------------------------------------------------------------------


def _correspondence_key(entity_kind: str, change: Change) -> str:
    """The identity a lifecycle event is tracked under across the chain.

    Prefers the already-resolved ``EntityId`` the compare engine attached to
    this finding (ADR-063 Phase 2) — falls back to ``(entity_kind, symbol)``
    only when no header-AST identity was resolved for this pair (a DWARF- or
    symbols-only comparison). Never invents a third identity scheme.
    """
    entity_id: EntityId | None = getattr(change, "entity_id", None)
    if entity_id is not None:
        return f"entity:{entity_kind}:{entity_id.key}"
    return f"symbol:{entity_kind}:{change.symbol}"


def _display_name(change: Change) -> str:
    return change.old_value or change.new_value or change.symbol


# ---------------------------------------------------------------------------
# Coverage-gap detection (bounded SemVer heuristic)
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def _parse_semver(version: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RE.match(version.strip())
    if m is None:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _is_semver_successor(a: tuple[int, int, int], b: tuple[int, int, int]) -> bool:
    """True when *b* is exactly one ordinary SemVer increment after *a*."""
    major, minor, patch = a
    return b in {
        (major + 1, 0, 0),
        (major, minor + 1, 0),
        (major, minor, patch + 1),
    }


def _detect_gap(from_version: str, to_version: str) -> CoverageGap | None:
    a = _parse_semver(from_version)
    b = _parse_semver(to_version)
    if a is None or b is None:
        # Non-SemVer (or unparseable) labels: S1 has no version scheme to
        # check against (D4/S2), so this reports no verdict at all rather
        # than a false "contiguous" or a false "gap".
        return None
    if a == b:
        return None
    if b < a:
        return CoverageGap(
            from_version,
            to_version,
            "unknown_interval",
            f"{to_version} does not sort after {from_version} under ordinary "
            "SemVer ordering -- history order and version-label order "
            "disagree; verify the supplied ordering is really the project's "
            "release order (D1: ordering is never inferred from labels).",
        )
    if not _is_semver_successor(a, b):
        return CoverageGap(
            from_version,
            to_version,
            "unknown_interval",
            f"{from_version} -> {to_version} skips one or more intermediate "
            "SemVer releases that were never supplied to this history -- "
            "any lifecycle event this chain reports for that interval is "
            "only known relative to these two endpoints.",
        )
    return None


# ---------------------------------------------------------------------------
# Core assembly
# ---------------------------------------------------------------------------


def _events_from_pair(
    from_entry: HistoryEntry,
    to_entry: HistoryEntry,
    result: DiffResult,
    *,
    ever_seen: set[str],
    removed: set[str],
    deprecated: set[str],
    deprecation_resets: list[tuple[str, int]],
    display_names: dict[str, str],
    is_initial: bool,
) -> list[LifecycleEvent]:
    """Derive this pair's lifecycle events, mutating the three running
    per-key state sets so later pairs in the chain see a consistent history.

    ``is_initial`` is True only for the synthetic "empty predecessor" pair
    that seeds entry 0 (see :func:`build_longitudinal_history`) -- every
    entity it reports as added is a ``first_observed``, never an
    ``introduced``, per D2's terminology: presence at the very first
    supplied snapshot proves nothing about whether the API existed even
    earlier.
    """
    events: list[LifecycleEvent] = []
    uncertain_absence = result.confidence != Confidence.HIGH

    for change in result.changes:
        entity_kind = _ADDED_KINDS.get(change.kind)
        if entity_kind is not None:
            key = _correspondence_key(entity_kind, change)
            display_names[key] = _display_name(change)
            if is_initial:
                event: LifecycleEventKind = "first_observed"
            elif key in removed:
                event = "reintroduced"
            elif key not in ever_seen:
                event = "introduced"
            else:
                # Already tracked as present -- compare() should never emit
                # ADDED for an entity its own old-side snapshot still
                # carries, but a possible-correspondence edge case (a
                # renamed/re-keyed declaration whose EntityId nonetheless
                # collided) is recorded rather than silently skipped.
                event = "introduced"
            ever_seen.add(key)
            removed.discard(key)
            events.append(
                LifecycleEvent(
                    entity_kind=entity_kind,
                    entity_key=key,
                    display_name=display_names[key],
                    event=event,
                    version=to_entry.version,
                    index=to_entry.index,
                )
            )
            continue

        entity_kind = _REMOVED_KINDS.get(change.kind)
        if entity_kind is not None:
            key = _correspondence_key(entity_kind, change)
            display_names.setdefault(key, _display_name(change))
            ever_seen.add(key)
            removed.add(key)
            deprecated.discard(key)
            events.append(
                LifecycleEvent(
                    entity_kind=entity_kind,
                    entity_key=key,
                    display_name=display_names[key],
                    event="removed",
                    version=to_entry.version,
                    index=to_entry.index,
                    evidence_uncertain=uncertain_absence,
                )
            )
            continue

        entity_kind = _DEPRECATED_ADDED_KINDS.get(change.kind)
        if entity_kind is not None:
            key = _correspondence_key(entity_kind, change)
            display_names.setdefault(key, _display_name(change))
            deprecated.add(key)
            events.append(
                LifecycleEvent(
                    entity_kind=entity_kind,
                    entity_key=key,
                    display_name=display_names[key],
                    event="deprecated",
                    version=to_entry.version,
                    index=to_entry.index,
                    detail=change.new_value,
                )
            )
            continue

        entity_kind = _DEPRECATED_REMOVED_KINDS.get(change.kind)
        if entity_kind is not None:
            # No "un-deprecated" event in D2's vocabulary -- just clear the
            # running flag so a later re-deprecation is reported again.
            # The transition is still recorded (not as a LifecycleEvent, to
            # keep D2's five-word vocabulary intact) so
            # evaluate_deprecation_compliance can tell a plain -> deprecated
            # -> plain -> removed entity apart from one still deprecated at
            # removal, rather than attributing stale deprecation evidence to
            # a later removal (CodeRabbit review).
            key = _correspondence_key(entity_kind, change)
            deprecated.discard(key)
            deprecation_resets.append((key, to_entry.index))

    return events


def build_longitudinal_history(
    entries: list[HistoryEntry],
    *,
    policy: str = "strict_abi",
    versioning_policy: VersioningPolicy | None = None,
) -> LongitudinalHistoryResult:
    """Compose ``checker.compare()`` pairwise across an already-ordered,
    already-loaded chain of snapshots and derive lifecycle events + coverage.

    ``entries`` must already be in the caller's intended release order (D1:
    S1 never infers or reorders — see :func:`run_history_request` for the
    common "load N snapshot files" entry point built on this).

    ``versioning_policy`` (ADR-066 S2) is optional and orthogonal: when
    given, ``deprecation_compliance`` is populated by evaluating the
    policy's ``deprecation_window`` control over the computed ``events`` --
    it never changes ``events``, ``gaps``, ``pairwise``, or which
    comparisons run. Omitted (the default), this function's behavior is
    identical to a build with no versioning-policy support at all.
    """
    if not entries:
        raise HistoryError("a longitudinal history needs at least one snapshot")

    library = entries[0].snapshot.library
    events: list[LifecycleEvent] = []
    gaps: list[CoverageGap] = []
    pairwise: list[PairwiseSummary] = []

    ever_seen: set[str] = set()
    removed: set[str] = set()
    deprecated: set[str] = set()
    deprecation_resets: list[tuple[str, int]] = []
    display_names: dict[str, str] = {}

    # Seed entry 0 via a synthetic empty predecessor, reusing the identical
    # pairwise engine rather than a bespoke "walk snapshot 0's own lists"
    # path -- see the module docstring. Not recorded in `pairwise` (there is
    # no real "from" release), and its confidence is not consulted for the
    # `evidence_uncertain` heuristic (nothing was truly removed).
    empty = AbiSnapshot(library=library, version="")
    initial_result = compare(
        empty, entries[0].snapshot, policy=policy, scope_to_public_surface=True
    )
    events.extend(
        _events_from_pair(
            entries[0],
            entries[0],
            initial_result,
            ever_seen=ever_seen,
            removed=removed,
            deprecated=deprecated,
            deprecation_resets=deprecation_resets,
            display_names=display_names,
            is_initial=True,
        )
    )

    for prev, curr in zip(entries, entries[1:]):
        result = compare(
            prev.snapshot, curr.snapshot, policy=policy, scope_to_public_surface=True
        )
        pairwise.append(
            PairwiseSummary(
                from_version=prev.version,
                to_version=curr.version,
                verdict=result.verdict.value,
                change_count=len(result.changes),
                confidence=result.confidence.value,
            )
        )
        events.extend(
            _events_from_pair(
                prev,
                curr,
                result,
                ever_seen=ever_seen,
                removed=removed,
                deprecated=deprecated,
                deprecation_resets=deprecation_resets,
                display_names=display_names,
                is_initial=False,
            )
        )
        gap = _detect_gap(prev.version, curr.version)
        if gap is not None:
            gaps.append(gap)

    history_result = LongitudinalHistoryResult(
        library=library,
        entries=tuple(entries),
        events=tuple(events),
        gaps=tuple(gaps),
        pairwise=tuple(pairwise),
        deprecation_resets=tuple(deprecation_resets),
    )
    if versioning_policy is not None:
        history_result = replace(
            history_result,
            deprecation_compliance=evaluate_deprecation_compliance(
                history_result, versioning_policy
            ),
        )
    return history_result


def run_history_request(
    snapshot_paths: list[str],
    *,
    versions: list[str] | None = None,
    policy: str = "strict_abi",
    versioning_policy: VersioningPolicy | None = None,
) -> LongitudinalHistoryResult:
    """The typed entry point: N stored-snapshot paths in, one
    :class:`LongitudinalHistoryResult` out.

    ``snapshot_paths`` order IS the release order (D1) -- pass them oldest
    first. Each path is loaded via ``serialization.load_snapshot`` (ADR-059
    storage envelope: plain/gzip/zstd all resolve). ``versions`` optionally
    overrides the release label recorded for each entry (positional,
    same length as ``snapshot_paths``); when omitted, each entry's own
    ``AbiSnapshot.version`` is used. ``versioning_policy`` is forwarded
    verbatim to :func:`build_longitudinal_history` (ADR-066 S2).
    """
    if not snapshot_paths:
        raise HistoryError("a longitudinal history needs at least one snapshot")
    if versions is not None and len(versions) != len(snapshot_paths):
        raise HistoryError(
            f"--version was given {len(versions)} time(s) but "
            f"{len(snapshot_paths)} snapshot(s) were supplied -- pass one "
            "per snapshot, in the same order, or omit it entirely to use "
            "each snapshot's own recorded version"
        )

    entries: list[HistoryEntry] = []
    for index, raw_path in enumerate(snapshot_paths):
        snapshot = load_snapshot(Path(raw_path))
        label = versions[index] if versions is not None else snapshot.version
        entries.append(
            HistoryEntry(
                index=index, version=label, path=str(raw_path), snapshot=snapshot
            )
        )

    return build_longitudinal_history(
        entries, policy=policy, versioning_policy=versioning_policy
    )


# ---------------------------------------------------------------------------
# D2/D4: deprecation-window compliance over a longitudinal history (S2).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeprecationComplianceFinding:
    """One ``removed`` lifecycle event's deprecation-window conformance.

    ``status`` is one of:

    * ``"conforming"`` -- a ``deprecated`` event was observed at least
      ``deprecation_window.min_releases`` release-steps before the removal
      (or no window is required at all).
    * ``"non_conforming"`` -- a ``deprecated`` event was observed, but too
      close to the removal to satisfy the declared window.
    * ``"unknown"`` -- no ``deprecated`` event was observed for this entity
      at all, yet the policy requires one. Per ADR-066 D2's terminology,
      this is *not* asserted as a violation: an unobserved deprecation is
      ``unknown``, not a proven absence of one (the entity could have been
      deprecated before the history's first supplied snapshot).
    """

    entity_kind: str
    entity_key: str
    display_name: str
    removed_at_version: str
    removed_at_index: int
    deprecated_at_version: str | None
    deprecated_at_index: int | None
    observed_releases: int | None
    status: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_kind": self.entity_kind,
            "entity_key": self.entity_key,
            "display_name": self.display_name,
            "removed_at_version": self.removed_at_version,
            "removed_at_index": self.removed_at_index,
            "deprecated_at_version": self.deprecated_at_version,
            "deprecated_at_index": self.deprecated_at_index,
            "observed_releases": self.observed_releases,
            "status": self.status,
            "detail": self.detail,
        }


#: Lifecycle events that discard a previously-tracked deprecation record for
#: the same entity key -- a fresh presence cycle (introduced/reintroduced/
#: first_observed) means any earlier deprecation belongs to a *prior* cycle
#: and must not be attributed to a later removal.
_RESTART_EVENTS = frozenset({"introduced", "reintroduced", "first_observed"})


def evaluate_deprecation_compliance(
    history: LongitudinalHistoryResult, policy: VersioningPolicy
) -> tuple[DeprecationComplianceFinding, ...]:
    """D4's ``deprecation_window`` evaluated over *history*'s lifecycle events.

    Walks ``history.events`` in their already-chronological order (S1's
    :func:`~abicheck.workflows.history.build_longitudinal_history` appends
    strictly in release-chain order), tracking the most recent ``deprecated``
    event per entity key and closing a finding on each ``removed`` event.
    ``history.deprecation_resets`` -- a plain -> deprecated -> plain
    transition, which carries no ``LifecycleEvent`` of its own (see
    ``_events_from_pair``'s comment) -- is also consulted, so a removal that
    follows such a reset with no subsequent re-``deprecated`` event is not
    attributed stale deprecation evidence from before the reset
    (CodeRabbit review).

    This is a pure, read-only projection over already-computed lifecycle
    facts -- it adds a new finding list, never mutates ``history.events``,
    and is never consulted by :func:`abicheck.checker.compare`'s own verdict
    (ADR-066's "orthogonal axis" requirement).
    """
    min_releases = policy.deprecation_window.min_releases
    deprecated_at: dict[str, tuple[int, str]] = {}
    findings: list[DeprecationComplianceFinding] = []

    resets_by_key: dict[str, list[int]] = {}
    for key, index in history.deprecation_resets:
        resets_by_key.setdefault(key, []).append(index)

    for ev in history.events:
        if ev.event == "deprecated":
            deprecated_at[ev.entity_key] = (ev.index, ev.version)
        elif ev.event in _RESTART_EVENTS:
            deprecated_at.pop(ev.entity_key, None)
        elif ev.event == "removed":
            dep = deprecated_at.pop(ev.entity_key, None)
            if dep is not None and any(
                dep[0] < reset_index <= ev.index
                for reset_index in resets_by_key.get(ev.entity_key, ())
            ):
                # A reset strictly between the tracked deprecation and this
                # removal, with no later "deprecated" event overwriting
                # `deprecated_at` in between (dict assignment above already
                # happens in chronological order, so a later re-deprecation
                # would have replaced `dep` before we get here) -- the
                # deprecation in force at removal time is unobserved, not
                # the one recorded before the reset.
                dep = None
            if dep is None:
                if min_releases <= 0:
                    status, observed = "conforming", None
                    detail = (
                        "removed with no observed deprecation event and no "
                        "deprecation window is required (deprecation_window."
                        "min_releases=0)."
                    )
                else:
                    status, observed = "unknown", None
                    detail = (
                        "removed with no deprecation event observed in this "
                        "history, but the policy requires at least "
                        f"{min_releases} release(s) of deprecation -- the "
                        "entity may have been deprecated before this "
                        "history's earliest supplied snapshot (ADR-066 D2: "
                        "an unobserved deprecation is 'unknown', not a "
                        "proven violation)."
                    )
                dep_index: int | None = None
                dep_version: str | None = None
            else:
                dep_index, dep_version = dep
                observed = ev.index - dep_index
                if observed >= min_releases:
                    status = "conforming"
                    detail = (
                        f"deprecated at {dep_version} (release #{dep_index}), "
                        f"removed at {ev.version} (release #{ev.index}): "
                        f"{observed} release(s) of deprecation observed, "
                        f"meeting the required {min_releases}."
                    )
                else:
                    status = "non_conforming"
                    detail = (
                        f"deprecated at {dep_version} (release #{dep_index}), "
                        f"removed at {ev.version} (release #{ev.index}): only "
                        f"{observed} release(s) of deprecation observed, "
                        f"short of the required {min_releases}."
                    )
            findings.append(
                DeprecationComplianceFinding(
                    entity_kind=ev.entity_kind,
                    entity_key=ev.entity_key,
                    display_name=ev.display_name,
                    removed_at_version=ev.version,
                    removed_at_index=ev.index,
                    deprecated_at_version=dep_version,
                    deprecated_at_index=dep_index,
                    observed_releases=observed,
                    status=status,
                    detail=detail,
                )
            )

    return tuple(findings)
