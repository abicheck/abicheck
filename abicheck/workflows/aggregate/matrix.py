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
"""Build the cross-profile matrix from parsed aggregate findings.

This module owns profile-level reconciliation, not report JSON parsing or gate
folding. Its public names are re-exported by :mod:`.contracts`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .reconcile import (
    FINDING_SCOPE_ALL_PROFILES,
    FINDING_SCOPE_PARTIAL,
    FINDING_SCOPE_PROFILE_SPECIFIC,
    FINDING_SCOPE_UNDETERMINED,
    MANGLING_ITANIUM,
    ReportFinding,
    ReportFindings,
)


@dataclass(frozen=True)
class ProfileContractState:
    """One affected profile's own ADR-049 contract decision for one
    :class:`FindingMatrixEntry` (CLI-audit P1: the aggregate semantic
    matrix).

    A single ``finding_matrix`` entry today only says a profile *observed*
    a finding (``affected_profiles``) -- it collapses "GCC: IN_CONTRACT and
    gating" and "Clang: UNKNOWN_UNRESOLVED and not gating" into the same
    membership fact, even though under ``--contract`` those are
    different outcomes for the *same* logical finding. This is the
    per-profile answer that distinction needs, read verbatim off that
    profile's own report entry -- never re-derived, so it can't disagree
    with what the profile's own run actually decided.
    """

    profile: str
    contract_relevance: str | None
    compatibility_evaluation_status: str | None
    compatibility_decision: str | None
    gate_contribution: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "contract_relevance": self.contract_relevance,
            "compatibility_evaluation_status": self.compatibility_evaluation_status,
            "compatibility_decision": self.compatibility_decision,
            "gate_contribution": self.gate_contribution,
        }


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
    #: The scheme-independent declaration this finding is about
    #: (:func:`cross_abi_declaration`), or ``None`` when none could be
    #: recovered. Two entries sharing it concern the same declaration under
    #: different C++ mangling schemes — they are deliberately *not* merged,
    #: since nothing in a report proves they are the same overload, but a
    #: consumer that wants to present them together has the link here.
    cross_abi_declaration: str | None = None
    #: One :class:`ProfileContractState` per profile in
    #: :attr:`affected_profiles` (same order), each that profile's own
    #: ADR-049 contract decision for this finding. Empty when no affected
    #: profile's report carried a ``contract_relevance`` at all -- i.e. no
    #: profile checking this target ran ``--contract`` -- so a
    #: matrix built from pre-ADR-049 reports serializes identically to
    #: before this field existed. A profile that *did* opt in but whose own
    #: entry for this finding somehow carries none still gets a state
    #: record here (every field ``None``), so "this profile ran without
    #: --contract" and "this profile's own report was missing
    #: the field" are never conflated with each other by omission.
    profile_contract: tuple[ProfileContractState, ...] = ()

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
        d: dict[str, Any] = {
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
            "cross_abi_declaration": self.cross_abi_declaration,
        }
        # CLI-audit P1: present only when at least one affected profile's
        # report actually carried a contract decision -- i.e. some profile
        # checking this target ran --contract. Omitted rather
        # than emitted empty so a matrix built entirely from pre-ADR-049
        # reports serializes byte-for-byte as it always did.
        if self.profile_contract:
            d["profile_contract"] = [p.to_dict() for p in self.profile_contract]
        return d


#: One profile's per-check finding sets: one :class:`ReportFindings` per check
#: that profile ran for the target. A check whose report never arrived, was
#: unreadable, or was not comparable contributes a default ``ReportFindings()``
#: — no findings, not complete — so "no report" and "a report that didn't
#: enumerate everything" need no separate representation here: both are simply
#: not complete. A profile with an empty sequence ran no checks at all.
ProfileCheckFindings = Sequence["ReportFindings"]


def _merge_equivalent_spellings(
    known: dict[str, dict[str, list[ReportFinding]]],
    profiles: Sequence[str],
) -> dict[str, dict[str, list[ReportFinding]]]:
    """Re-key findings whose spellings are *provably* the same entity.

    One declaration is spelled ``_ZN3lib3addEii`` by a Linux toolchain and
    ``__ZN3lib3addEii`` by a Mach-O one — the platform's extra leading
    underscore, and nothing else. Normalized
    (:func:`comparable_mangled_symbol`) they are byte-identical *complete*
    Itanium encodings, parameter types included, so they name one entity;
    only the primary identity, which keys on the raw symbol, failed to see
    it. Left split, a Linux and a macOS profile reporting one removal
    produced two entries where one ``all_profiles`` finding is the truth
    (Codex review).

    **This is the merge the cross-ABI key may not do, and the difference is
    the evidence.** ``cross_identity`` only strips to a qualified name, so
    two profiles matching on it may hold different overloads —
    :func:`resolve_cross_abi_identity` explains why merging there would
    assert a pairing nothing establishes. Here the *whole* mangling matches
    after one platform prefix is removed, which leaves nothing to guess at.
    Merging is keyed on that equality, never on the qualified name alone.
    """
    identities_by_spelling: dict[tuple[str, str], set[str]] = {}
    for pid in profiles:
        for identity, findings in known[pid].items():
            for f in findings:
                if f.cross_identity and f.comparable_symbol:
                    identities_by_spelling.setdefault(
                        (f.cross_identity, f.comparable_symbol), set()
                    ).add(identity)

    remap: dict[str, str] = {}
    for identities in identities_by_spelling.values():
        if len(identities) < 2:
            continue
        # Deterministic and opaque: the schema documents the id as a key to
        # compare, never to parse, so the smallest of the group serves.
        canonical = min(identities)
        for identity in identities:
            remap[identity] = canonical
    if not remap:
        return known

    merged: dict[str, dict[str, list[ReportFinding]]] = {}
    for pid in profiles:
        by_identity: dict[str, list[ReportFinding]] = {}
        for identity, findings in known[pid].items():
            by_identity.setdefault(remap.get(identity, identity), []).extend(findings)
        merged[pid] = by_identity
    return merged


def _withholds_clean_verdict(
    own: set[tuple[str | None, str]],
    others: set[tuple[str | None, str]],
) -> bool:
    """Whether another profile's findings on this declaration make a clean
    verdict unsafe to state.

    *own* and *others* are ``(mangling scheme, comparable symbol)`` pairs for
    findings that already share a cross-ABI declaration key — same qualified
    name, same discriminator. What separates "provably a different overload"
    from "cannot tell" is whether the two spellings are comparable at all:

    - **Different schemes** (Itanium vs MSVC) are not comparable without a
      type-encoding translator this module does not have, so nothing can be
      concluded and the clean verdict is withheld.
    - **Both Itanium, different symbol** is proof of distinctness —
      ``_ZN3lib3addEii`` and ``_ZN3lib3addEd`` encode different parameter
      types, and Itanium puts nothing else in the mangling — so the other
      profile really is clean of *this* finding and must be reported as such
      (Codex review: withholding here cost real precision on the commonest
      configuration of all, a GCC and a Clang profile that both mangle
      Itanium).
    - **Both MSVC, different symbol** proves nothing, unlike its Itanium
      counterpart, because an MSVC decoration can encode the *target ABI*
      rather than the declaration: ARM64EC inserts a ``$$h`` tag, so one
      declaration is spelled ``?add@lib@@YAHHH@Z`` on x64 and
      ``?add@lib@@$$hYAHHH@Z`` on ARM64EC — verified: both reduce to
      ``lib::add`` through :func:`msvc_qualified_name`. Two Windows profiles
      on different targets would otherwise be reported clean of each other's
      identical removal (Codex review). Withheld rather than normalized,
      since ``$$h`` is the decoration this module can *name*, not
      demonstrably the only one — and withholding needs no such proof.
    - **Same scheme, same symbol** is the same entity spelled differently
      across platforms (a Mach-O toolchain prefixes an extra underscore,
      normalized by :func:`comparable_mangled_symbol`). Withheld, because
      the profile demonstrably has this very finding — it is only the
      *primary* identity, which keys on the raw symbol, that failed to
      recognize it.

    Returns ``True`` when any of *others* is not *provably* a different
    entity from everything in *own*.
    """
    own_by_scheme: dict[str | None, set[str]] = {}
    for scheme, symbol in own:
        own_by_scheme.setdefault(scheme, set()).add(symbol)
    return any(
        scheme not in own_by_scheme
        or scheme != MANGLING_ITANIUM
        or symbol in own_by_scheme[scheme]
        for scheme, symbol in others
    )


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

    Identities are **never merged** across mangling schemes — see
    :func:`resolve_cross_abi_identity` for why that key cannot prove two
    findings are the same one. It is used only to decide whether a profile
    may be called *clean*: when another profile reported on the same
    declaration under a spelling this one cannot be compared with
    (:func:`_withholds_clean_verdict`), that profile is ``undetermined``
    rather than ``unaffected``.

    Entries are ordered by ``(base_target, kinds, symbol, finding_identity)``
    so the output is stable across runs; the identity itself is last only as
    a tie-break, since it is a hash-like key nobody reads for ordering.
    """
    entries: list[FindingMatrixEntry] = []
    for base_target, checks_by_profile in sorted(
        findings_by_target_and_profile.items()
    ):
        profiles = sorted(checks_by_profile)
        # profile -> identity -> every finding that resolved to it. A list,
        # not one representative: a profile can run several checks for one
        # target (different baseline channels/depths), and two of them can
        # legitimately report the same identity under equivalent rich and L0
        # kinds -- keeping only the first would drop the other from `kinds`,
        # which the schema promises carries every kind the affected profiles
        # reported (Codex review).
        known: dict[str, dict[str, list[ReportFinding]]] = {}
        undetermined: set[str] = set()
        for pid in profiles:
            by_identity: dict[str, list[ReportFinding]] = {}
            checks = checks_by_profile[pid]
            if not checks:
                # A profile present in the grouping with no checks at all has
                # nothing known about it either.
                undetermined.add(pid)
            for check in checks:
                if not check.complete:
                    undetermined.add(pid)
                for finding in check.findings:
                    by_identity.setdefault(finding.identity, []).append(finding)
            known[pid] = by_identity

        # Two spellings of one entity become one identity before anything is
        # counted, so a merged finding is affected/unaffected like any other.
        known = _merge_equivalent_spellings(known, profiles)

        # profile -> cross-ABI declaration key -> the (scheme, normalized
        # symbol) pairs it reported under. Both halves are needed to decide
        # whether that profile's finding is *provably* a different one.
        cross_by_profile: dict[str, dict[str, set[tuple[str | None, str]]]] = {
            pid: {} for pid in profiles
        }
        for pid in profiles:
            for found in known[pid].values():
                for f in found:
                    if f.cross_identity:
                        cross_by_profile[pid].setdefault(f.cross_identity, set()).add(
                            (f.mangling, f.comparable_symbol)
                        )

        all_identities = sorted(
            {identity for found in known.values() for identity in found}
        )
        for identity in all_identities:
            affected = [pid for pid in profiles if identity in known[pid]]
            affected_set = set(affected)
            rest = [pid for pid in profiles if pid not in affected_set]
            samples = [f for pid in affected for f in known[pid][identity]]
            first = samples[0]
            # A profile carrying a different identity that shares this one's
            # cross-ABI key has a finding on the same declaration. Whether
            # that leaves it clean or merely undetermined turns on whether
            # the two spellings can be compared at all.
            cross = first.cross_identity
            sibling = (
                {
                    pid
                    for pid in rest
                    if _withholds_clean_verdict(
                        {(s.mangling, s.comparable_symbol) for s in samples},
                        cross_by_profile[pid].get(cross, set()),
                    )
                }
                if cross
                else set()
            )
            # CLI-audit P1: one profile's own contract decision for this
            # finding, per affected profile -- the first sample per profile,
            # mirroring `first = samples[0]` above's "representative, not
            # merged" precedent (two checks for one profile reporting the
            # same identity should already agree on its contract decision;
            # this does not attempt to reconcile a disagreement). Built only
            # when at least one profile's sample actually carries a
            # contract_relevance, so a matrix with no --contract
            # profile anywhere never allocates the field at all.
            profile_contract: tuple[ProfileContractState, ...] = ()
            if any(s.contract_relevance is not None for s in samples):
                profile_contract = tuple(
                    ProfileContractState(
                        profile=pid,
                        contract_relevance=known[pid][identity][0].contract_relevance,
                        compatibility_evaluation_status=known[pid][identity][
                            0
                        ].compatibility_evaluation_status,
                        compatibility_decision=known[pid][identity][
                            0
                        ].compatibility_decision,
                        gate_contribution=known[pid][identity][0].gate_contribution,
                    )
                    for pid in affected
                )
            entries.append(
                FindingMatrixEntry(
                    base_target=base_target,
                    finding_identity=identity,
                    identity_tier=first.identity_tier,
                    kinds=tuple(sorted({s.kind for s in samples if s.kind})),
                    symbol=first.symbol,
                    description=first.description,
                    affected_profiles=tuple(affected),
                    unaffected_profiles=tuple(
                        p for p in rest if p not in undetermined and p not in sibling
                    ),
                    undetermined_profiles=tuple(
                        p for p in rest if p in undetermined or p in sibling
                    ),
                    cross_abi_declaration=first.cross_declaration,
                    profile_contract=profile_contract,
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
