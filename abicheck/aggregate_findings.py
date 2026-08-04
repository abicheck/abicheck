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

"""Per-finding cross-profile reconciliation for ``aggregate`` (G34 Phase D).

:mod:`abicheck.aggregate`'s ``profile_matrix`` answers "which toolchain
profiles are affected"; this module answers the question underneath it —
*which finding* is the one that differs, and is it the same finding on every
profile or one profile's alone. Without it, a reviewer looking at a
GCC/Clang/MSVC matrix has to cross-reference three reports by hand to tell
"the same removal shows up everywhere" from "each profile broke differently",
which is exactly the call a compiler-matrix gate exists to make.

Split out of ``aggregate.py`` (which sits above the 1500-line soft limit) as
a **leaf**: it imports nothing from ``aggregate`` and knows nothing about
targets, gates, coverage, or exit codes. ``aggregate`` projects its own
``TargetReport`` grouping down to the plain
``base_target -> profile -> [per-check finding set]`` shape
:func:`build_finding_matrix` takes, so the reconciliation rules below are
independently testable without constructing a whole aggregate result.

**The one invariant this module exists to preserve.** ``aggregate``'s
governing rule is that an expected target with no report is *unknown*, never
folded in as compatible. The per-finding form of it is the reason
:attr:`FindingMatrixEntry.undetermined_profiles` exists as a third list
rather than findings being split into just affected/unaffected: a profile
whose findings were never read must never be reported as *proven clean* of a
finding. :class:`ReportFindings` keeps the two facts structurally apart at
the point of reading — *what a report listed* and *whether that list was all
of it* are separate fields — and every rule below propagates the distinction
rather than collapsing it. Only completeness may clear a profile; the
findings themselves may always convict one, since seeing a finding proves it
is there while not seeing one proves nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from .finding_identity import FindingIdentity, resolve_change_identity

if TYPE_CHECKING:
    from .checker_types import Change

#: :attr:`FindingMatrixEntry.scope` values — how one logical finding is
#: distributed across the profiles that checked its target.
FINDING_SCOPE_ALL_PROFILES = "all_profiles"
FINDING_SCOPE_PROFILE_SPECIFIC = "profile_specific"
FINDING_SCOPE_PARTIAL = "partial"
FINDING_SCOPE_UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class _ReportChangeView:
    """Exactly the :class:`~abicheck.checker_types.Change` attributes
    :func:`resolve_change_identity` reads, rebuilt from a report's own JSON.

    A read-back adapter, deliberately *not* a partial ``Change``: a real
    ``Change`` carries ~30 further fields (verdict modulation, reachability,
    impact assessment) that identity resolution never touches, and
    constructing one here would invite a future reader to assume those
    fields mean something on the round-tripped side. Only the eight
    attributes below are consulted, all by plain attribute access
    (``resolve_change_identity`` reads ``kind`` through ``getattr(..., "value",
    kind)``, so a bare kind *slug* string works unchanged and an unknown
    kind from a newer abicheck never has to parse as a ``ChangeKind``).
    """

    kind: str
    symbol: str
    description: str
    old_value: str | None
    new_value: str | None
    source_location: str | None
    affected_symbols: list[str] | None
    qualified_name: str | None


def resolve_report_change_identity(entry: Mapping[str, Any]) -> FindingIdentity:
    """Tiered identity for a finding read back from a **report's** ``changes[]``.

    The same :func:`resolve_change_identity` resolution, over a JSON entry
    (``reporter._change_to_dict``'s output) instead of a live ``Change`` —
    what a cross-report consumer needs, since a report is the only artifact
    that survives a CI matrix leg. Used by
    :attr:`abicheck.aggregate.AggregateResult.finding_matrix` to decide
    whether two profiles' reports describe the *same* logical finding
    (G34 Phase D).

    **Identity is comparable across reports, not with a live ``Change``.**
    ``_change_to_dict`` does not serialize ``qualified_name``, so it reads
    back as ``None`` here and a finding whose live identity was promoted via
    that field resolves one tier lower on the round trip. That is a
    consistent loss — every report on every profile loses the same field, so
    two reports still agree with each other, which is the only comparison
    this function's callers make. Don't mix an id from here with one from
    :func:`resolve_change_identity` on a live diff and expect them to match.

    A non-mapping entry, or one with no ``kind``, yields a REDUCED-tier
    identity over whatever it does carry rather than raising: a report is
    an external artifact, and one malformed finding must not abort a whole
    CI-matrix aggregation.
    """

    def _opt_str(key: str) -> str | None:
        value = entry.get(key)
        return value if isinstance(value, str) and value else None

    affected_raw = entry.get("affected_symbols")
    affected = (
        [str(s) for s in affected_raw] if isinstance(affected_raw, list) else None
    )
    view = _ReportChangeView(
        kind=str(entry.get("kind") or ""),
        symbol=str(entry.get("symbol") or ""),
        description=str(entry.get("description") or ""),
        old_value=_opt_str("old_value"),
        new_value=_opt_str("new_value"),
        source_location=_opt_str("source_location"),
        affected_symbols=affected,
        # Never serialized by `_change_to_dict` — see this function's own
        # docstring for why an absent value here is consistent rather than
        # lossy for the cross-report comparison it exists to serve.
        qualified_name=None,
    )
    return resolve_change_identity(cast("Change", view))


@dataclass(frozen=True)
class ReportFinding:
    """One entry of a report's ``changes[]``, reduced to what cross-profile
    reconciliation needs.

    :attr:`identity` is
    :func:`resolve_report_change_identity`'s
    ``primary_id`` — the key that decides whether two *different profiles'*
    reports describe the same logical finding. It is deliberately the same
    tiered identity model ``diff_filtering.py`` already uses as its
    cross-detector dedup key (ADR-049 Phase 2), not a second scheme: a
    finding one profile reports as ``func_removed`` (rich DWARF evidence)
    and another as ``func_removed_elf_only`` (symbols only) collapses to one
    identity there and must here too, or a differently-provisioned matrix
    leg would look like it found a different problem.

    Not the report's own ``finding_id``, which additionally hashes
    ``description`` and ``source_location`` *unconditionally* — right for
    "is this the same finding across two runs of the same comparison", too
    strict across profiles, where a header path or a rendered size can
    legitimately differ for one logical event.
    """

    identity: str
    identity_tier: str
    kind: str
    symbol: str
    description: str
    severity: str | None = None

    @classmethod
    def from_report_entry(cls, entry: Mapping[str, Any]) -> ReportFinding:
        identity = resolve_report_change_identity(entry)
        severity = entry.get("severity")
        return cls(
            identity=identity.primary_id,
            identity_tier=identity.tier,
            kind=str(entry.get("kind") or ""),
            symbol=str(entry.get("symbol") or ""),
            description=str(entry.get("description") or ""),
            severity=severity if isinstance(severity, str) and severity else None,
        )


@dataclass(frozen=True)
class ReportFindings:
    """One report's findings, plus whether that list is known to be *all* of
    them.

    The two fields are independent on purpose, and conflating them is the
    mistake this class exists to make unrepresentable. ``findings`` is what
    was successfully read; :attr:`complete` is whether reading it accounted
    for everything the comparison found. A report can carry real findings
    and still be incomplete — a ``compare-release`` report enumerates its
    bundle- and matrix-level findings but only *counts* its per-library
    ones, and a report with one unparseable array element yields the rest
    plus a known hole.

    Only :attr:`complete` may clear a profile of a finding. The findings
    themselves can always *convict* a profile, complete or not: seeing a
    finding is proof it is there, whereas not seeing one is proof of
    nothing unless the list was exhaustive.
    """

    findings: tuple[ReportFinding, ...] = ()
    complete: bool = False


#: Finding arrays a ``compare-release`` report carries instead of ``changes``
#: (``cli_compare_release_helpers._format_release_json``) — the shape a
#: ``kind: bundle`` check produces, since a bundle comparison routes through
#: the per-library release fan-out. Both carry the same
#: ``kind``/``symbol``/``description``/``old_value``/``new_value`` fields
#: :class:`ReportFinding` reads, so they need no separate identity path.
_RELEASE_FINDING_KEYS = ("bundle_findings", "matrix_findings")


def _with_bundle_attribution(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    """Fold a bundle finding's library attribution into its ``description``.

    A ``bundle_findings`` entry keeps ``consumer_library``/``provider_library``
    as separate fields, so two findings that differ *only* in which library
    pair they are about are otherwise identical on every field
    :func:`resolve_report_change_identity` reads — they would reconcile into
    one entry that is really two. Prefixing the description restores the
    distinction using ``bundle_models.BundleFinding.to_change``'s own
    established flattening (``"[consumer ← provider] "``) rather than
    inventing a second attribution format for the same fact.
    """
    consumer = entry.get("consumer_library")
    provider = entry.get("provider_library")
    consumer = consumer if isinstance(consumer, str) and consumer else None
    provider = provider if isinstance(provider, str) and provider else None
    if consumer and provider:
        prefix = f"[{consumer} ← {provider}] "
    elif provider:
        prefix = f"[{provider}] "
    elif consumer:
        prefix = f"[{consumer}] "
    else:
        return entry
    return {**entry, "description": prefix + str(entry.get("description") or "")}


def parse_report_findings(data: Mapping[str, Any]) -> ReportFindings:
    """Read every finding array a report carries into a :class:`ReportFindings`.

    Two report shapes are understood. A ``compare``/``scan`` report puts its
    whole finding set in ``changes``; a ``compare-release`` report has no
    ``changes`` at all and instead carries ``bundle_findings`` and
    ``matrix_findings`` (:data:`_RELEASE_FINDING_KEYS`) alongside per-library
    entries that are only *counts*. Both are parsed, so a ``kind: bundle``
    check — which always produces the release shape — participates in the
    matrix instead of being written off as unknown.

    :attr:`~ReportFindings.complete` is granted **only** for a well-formed
    ``changes`` array, because that is the one field that carries a
    comparison's entire finding set. A release report is never complete no
    matter how many bundle/matrix findings it lists: its per-library
    findings are not in the document, so it cannot clear a profile of one.
    Neither is a report with any unparseable array element — the valid
    entries stay usable (they can still convict a profile), but the hole is
    recorded rather than silently rounded down to "found nothing", which is
    the false clean claim :attr:`FindingMatrixEntry.undetermined_profiles`
    exists to prevent.

    A malformed entry is skipped rather than raising: reports are external
    artifacts, and one bad finding must not take a whole CI-matrix
    aggregation down with it.
    """
    findings: list[ReportFinding] = []
    malformed = False

    raw_changes = data.get("changes")
    enumerated_changes = isinstance(raw_changes, list)
    if isinstance(raw_changes, list):
        for entry in raw_changes:
            if isinstance(entry, Mapping):
                findings.append(ReportFinding.from_report_entry(entry))
            else:
                malformed = True

    for key in _RELEASE_FINDING_KEYS:
        raw = data.get(key)
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if isinstance(entry, Mapping):
                findings.append(
                    ReportFinding.from_report_entry(_with_bundle_attribution(entry))
                )
            else:
                malformed = True

    return ReportFindings(
        findings=tuple(findings),
        complete=enumerated_changes and not malformed,
    )


@dataclass(frozen=True)
class FindingMatrixEntry:
    """One logical finding, reconciled across every profile that checked its
    target.

    The three profile lists partition the target's profiles and never
    overlap. :attr:`undetermined_profiles` is the load-bearing one — see this
    module's docstring for why it is not folded into
    :attr:`unaffected_profiles`.
    """

    base_target: str
    #: :func:`resolve_report_change_identity`'s
    #: ``primary_id`` — the reconciliation key. Opaque: compare it for
    #: equality, don't parse it.
    finding_identity: str
    #: That identity's confidence tier (``canonical``/``normalized``/
    #: ``reduced``). A ``reduced`` match is a weaker claim that two profiles'
    #: findings are really the same one.
    identity_tier: str
    #: Every distinct ``ChangeKind`` slug the affected profiles reported this
    #: finding under, sorted. Usually one; two when profiles differ in
    #: available evidence and the identity model collapses the pair. Kept as
    #: a list rather than a single representative so that divergence stays
    #: visible instead of being picked away.
    kinds: tuple[str, ...]
    symbol: str
    description: str
    #: Profiles whose reports carry this finding, sorted.
    affected_profiles: tuple[str, ...]
    #: Profiles whose findings are fully known and do *not* include this one,
    #: sorted — a positive statement that the profile was checked and is
    #: clean of it.
    unaffected_profiles: tuple[str, ...]
    #: Profiles that cannot be placed in either list above, sorted.
    undetermined_profiles: tuple[str, ...]

    @property
    def scope(self) -> str:
        """One of the :data:`FINDING_SCOPE_ALL_PROFILES` constants.

        ``undetermined`` wins over every other answer whenever any profile's
        findings are unknown — a finding cannot be called profile-specific
        while a profile that might also carry it was never read.
        """
        if self.undetermined_profiles:
            return FINDING_SCOPE_UNDETERMINED
        if not self.unaffected_profiles:
            return FINDING_SCOPE_ALL_PROFILES
        if len(self.affected_profiles) == 1:
            return FINDING_SCOPE_PROFILE_SPECIFIC
        return FINDING_SCOPE_PARTIAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_target": self.base_target,
            "finding_identity": self.finding_identity,
            "identity_tier": self.identity_tier,
            "kinds": list(self.kinds),
            "symbol": self.symbol,
            "description": self.description,
            "scope": self.scope,
            "affected_profiles": list(self.affected_profiles),
            "unaffected_profiles": list(self.unaffected_profiles),
            "undetermined_profiles": list(self.undetermined_profiles),
        }


#: One profile's per-check finding sets: one :class:`ReportFindings` per check
#: that profile ran for the target. A check whose report never arrived, was
#: unreadable, or was not comparable contributes a default ``ReportFindings()``
#: — no findings, not complete — so "no report" and "a report that didn't
#: enumerate everything" need no separate representation here: both are simply
#: not complete. A profile with an empty sequence ran no checks at all.
ProfileCheckFindings = Sequence["ReportFindings"]


def build_finding_matrix(
    findings_by_target_and_profile: Mapping[str, Mapping[str, ProfileCheckFindings]],
) -> tuple[FindingMatrixEntry, ...]:
    """Reconcile every profile's findings into one entry per logical finding.

    A profile is *affected* when any of its own checks carries the finding;
    *undetermined* when it is not affected but any of its checks fell short
    of a complete finding set (:attr:`ReportFindings.complete`); *unaffected*
    only when every one of its checks enumerated its findings in full and
    none of them was this one.

    Affected outranks undetermined outranks unaffected, so a profile that
    reported the finding on one check and failed to report at all on another
    is listed as affected — it demonstrably has the finding — rather than
    being softened to "we're not sure". This is why an incomplete check's
    findings are still read: they can only ever add a profile to
    ``affected``, never wrongly clear one.

    Entries are ordered by ``(base_target, kinds, symbol, finding_identity)``
    so the output is stable across runs; the identity itself is last only as
    a tie-break, since it is a hash-like key nobody reads for ordering.
    """
    entries: list[FindingMatrixEntry] = []
    for base_target, checks_by_profile in sorted(
        findings_by_target_and_profile.items()
    ):
        profiles = sorted(checks_by_profile)
        # profile -> the findings it reported, by identity ({} when the
        # profile ran checks but none of them listed a finding).
        known: dict[str, dict[str, ReportFinding]] = {}
        undetermined: set[str] = set()
        for pid in profiles:
            by_identity: dict[str, ReportFinding] = {}
            checks = checks_by_profile[pid]
            if not checks:
                # A profile present in the grouping with no checks at all has
                # nothing known about it either.
                undetermined.add(pid)
            for check in checks:
                if not check.complete:
                    undetermined.add(pid)
                for finding in check.findings:
                    by_identity.setdefault(finding.identity, finding)
            known[pid] = by_identity

        all_identities = sorted(
            {identity for found in known.values() for identity in found}
        )
        for identity in all_identities:
            affected = [pid for pid in profiles if identity in known[pid]]
            affected_set = set(affected)
            rest = [pid for pid in profiles if pid not in affected_set]
            samples = [known[pid][identity] for pid in affected]
            first = samples[0]
            entries.append(
                FindingMatrixEntry(
                    base_target=base_target,
                    finding_identity=identity,
                    identity_tier=first.identity_tier,
                    kinds=tuple(sorted({s.kind for s in samples if s.kind})),
                    symbol=first.symbol,
                    description=first.description,
                    affected_profiles=tuple(affected),
                    unaffected_profiles=tuple(p for p in rest if p not in undetermined),
                    undetermined_profiles=tuple(p for p in rest if p in undetermined),
                )
            )
    entries.sort(key=lambda e: (e.base_target, e.kinds, e.symbol, e.finding_identity))
    return tuple(entries)


#: Render order for the text section: most-ambiguous first, then the findings
#: a reviewer most needs to see the shape of. A finding on every profile is
#: real but the least interesting *cross-profile* signal — it says the matrix
#: agrees with itself.
_SCOPE_RENDER_ORDER = {
    FINDING_SCOPE_UNDETERMINED: 0,
    FINDING_SCOPE_PROFILE_SPECIFIC: 1,
    FINDING_SCOPE_PARTIAL: 2,
    FINDING_SCOPE_ALL_PROFILES: 3,
}


def render_finding_matrix_lines(
    matrix: Sequence[FindingMatrixEntry],
) -> list[str]:
    """The ``Cross-profile findings:`` section of ``aggregate``'s text
    output, or an empty list when there is nothing to reconcile."""
    if not matrix:
        return []
    lines = ["", "Cross-profile findings:"]
    for entry in sorted(
        matrix, key=lambda e: (_SCOPE_RENDER_ORDER[e.scope], e.base_target)
    ):
        kinds = "/".join(entry.kinds) or "(unknown kind)"
        subject = f"{entry.base_target} {kinds}"
        if entry.symbol:
            subject += f" [{entry.symbol}]"
        affected = ", ".join(entry.affected_profiles)
        if entry.scope == FINDING_SCOPE_ALL_PROFILES:
            detail = f"on every checked profile ({affected})"
        elif entry.scope == FINDING_SCOPE_PROFILE_SPECIFIC:
            detail = (
                f"only on {affected}; not on {', '.join(entry.unaffected_profiles)}"
            )
        elif entry.scope == FINDING_SCOPE_PARTIAL:
            detail = f"on {affected}; not on {', '.join(entry.unaffected_profiles)}"
        else:
            # Never say "not on X" for a profile whose findings were never
            # read — that is the one claim this section must not make.
            detail = f"on {affected}; unknown on " + ", ".join(
                entry.undetermined_profiles
            )
            if entry.unaffected_profiles:
                detail += f"; not on {', '.join(entry.unaffected_profiles)}"
        lines.append(f"  {subject}: {detail}")
    return lines
