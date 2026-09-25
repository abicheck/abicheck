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

"""Reconcile one product contract against many binary providers.

The release-level answer to the two contract cross-checks that a
multi-library product cannot answer per member:

* ``public_not_exported`` -- a public declaration is satisfied when **any**
  bundle member exports its symbol. Judged against the union of the side's
  usable exports (:class:`~abicheck.compare.bundle_export_index.
  BundleExportIndex`), so a ScaLAPACK declaration in a shared Intel MKL
  header provided by one MKL library is no longer demanded from each of the
  other 27. When no member provides it the finding is emitted **once**, at
  release level, carrying the declaration and its source header -- and
  deliberately *not* an owning library, because a symbol absent everywhere
  has no provider to attribute it to and inventing one is the defect this
  module exists to remove.
* ``exported_not_public`` -- deliberately **not** moved off the member
  pass, and the asymmetry is the point. An undocumented export is a
  property of the member that exports it: that member *is* the correct
  attribution, and the check already judges it against the one shared
  public declaration index (which every member's snapshot carries), with a
  precise per-symbol reason -- external-dependency leak, internal-namespace
  escape, template instantiation, bare undeclared export. Nothing about
  that answer is a Cartesian product, so re-deriving it at release level
  would mean a second copy of a hundred-line accounting rule for no change
  in conclusion. What *was* wrong is cardinality when several members export
  the same symbol: those identical findings are folded to one, naming every
  provider, by ``report.release_public_surface.
  dedupe_shared_member_findings``. This module adds the release-level
  *accounting* over the same evidence
  (:func:`undocumented_exports_by_member`), so a reader sees one total for
  the product alongside the per-member split.

**Coverage honesty.** A missing export is a conclusion about the *whole*
product, so it rests on having read the whole product. With an expected
member unread (acquisition failed, or no export table), the symbol may
simply live in the member nobody read: the successful evidence is retained,
the obligation is recorded as *unresolved* rather than missing, no
high-confidence ``public_not_exported`` is emitted for it, and the coverage
gap is reported. "Weaker evidence narrows conclusions" -- it never
fabricates a break, and never upgrades to a clean claim either, which is
why an unresolved obligation stays visible in the section instead of
vanishing.

**Evolution, not a snapshot judgement.** Both checks are intra-version
hygiene: each side is reconciled independently and the two results are
folded into one evolution-stated finding set, exactly as
``workflows.cross_source_evolution`` does per member -- a contract gap
present on both real releases must not read as ``INTRODUCED`` because one
side's evidence was thinner.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..buildsource.cross_source_checks_base import _change
from ..checker_types import Change
from ..compare.bundle_export_index import BundleExportIndex
from ..compare.ownership_relations import provider_relations
from ..model.change_catalog.kinds import ChangeKind
from ..model.release_surface import PublicObligation, ReleasePublicSurface
from .evidence_status import Confidence, CrossSourceEvolution


@dataclass(frozen=True)
class SideReconciliation:
    """One release side's contract-versus-providers reconciliation."""

    side: str
    #: The acquisition key of the surface this rests on, so a report can
    #: state *which* public surface produced the numbers.
    acquisition_key: str
    #: Whether the surface itself resolved at all (an unresolved surface
    #: reconciles nothing -- it is not a product that promises nothing).
    surface_resolvable: bool
    #: Declarations with an export obligation.
    obligations_total: int = 0
    #: ``{symbol: providing member(s)}`` for each satisfied obligation.
    satisfied: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Obligations no member provides, with coverage proven complete.
    missing: tuple[PublicObligation, ...] = ()
    #: Obligations no member provides while coverage is **incomplete** --
    #: recorded, never concluded.
    unresolved: tuple[PublicObligation, ...] = ()
    #: Distinct exported symbols across every observed member.
    exports_total: int = 0
    #: How many of those a public header declares.
    exports_declared_in_headers: int = 0
    coverage_complete: bool = True
    coverage_reason: str | None = None

    @property
    def satisfied_total(self) -> int:
        return len(self.satisfied)

    @property
    def exports_not_declared_in_headers(self) -> int:
        return self.exports_total - self.exports_declared_in_headers

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "side": self.side,
            "acquisition_key": self.acquisition_key,
            "surface_resolvable": self.surface_resolvable,
            "public_declarations_with_export_obligation": self.obligations_total,
            "satisfied_by_bundle_exports": self.satisfied_total,
            "missing_from_bundle": [o.to_dict() for o in self.missing],
            "unresolved_under_incomplete_coverage": [
                o.to_dict() for o in self.unresolved
            ],
            "exports_total": self.exports_total,
            "exports_declared_in_headers": self.exports_declared_in_headers,
            "exports_not_declared_in_headers": self.exports_not_declared_in_headers,
            "coverage_complete": self.coverage_complete,
        }
        if self.coverage_reason is not None:
            out["coverage_reason"] = self.coverage_reason
        return out


@dataclass(frozen=True)
class ReleaseReconciliation:
    """The folded, evolution-stated release-level contract reconciliation."""

    old: SideReconciliation | None
    new: SideReconciliation
    findings: tuple[Change, ...] = ()
    #: ``{member: count}`` of exports that member provides which no public
    #: header declares. Counts, not a symbol-keyed map: a real product can
    #: carry hundreds of thousands of such exports, and a per-symbol map
    #: would put the release report straight back on the
    #: O(surface x members) growth curve this workstream removes. The
    #: symbols themselves remain reachable where they are already
    #: attributed -- each member's own accounted ``exported_not_public``
    #: findings.
    undocumented_exports_by_member: Mapping[str, int] = field(default_factory=dict)
    coverage_warnings: tuple[str, ...] = ()

    @property
    def evaluated(self) -> bool:
        """Whether any side's surface resolved, i.e. anything was judged."""
        return self.new.surface_resolvable or bool(
            self.old is not None and self.old.surface_resolvable
        )

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "new": self.new.to_dict(),
            "findings": len(self.findings),
            "undocumented_exports_by_member": dict(
                sorted(self.undocumented_exports_by_member.items())
            ),
        }
        if self.old is not None:
            out["old"] = self.old.to_dict()
        if self.coverage_warnings:
            out["coverage_warnings"] = list(self.coverage_warnings)
        return out


def reconcile_side(
    surface: ReleasePublicSurface, index: BundleExportIndex
) -> SideReconciliation:
    """Reconcile one side's public surface against its bundle export index."""
    if not surface.resolvable:
        return SideReconciliation(
            side=surface.side,
            acquisition_key=surface.acquisition_key,
            surface_resolvable=False,
            exports_total=len(index.symbols),
            coverage_complete=index.complete,
            coverage_reason=surface.unresolved_reason or index.incompleteness_reason(),
        )
    # ADR-075 D7: attribution reads the release's `provided_by` relation
    # (export -> providing member, over this same index), the one provider
    # model the graph exposes -- never a second symbol -> member lookup.
    provided_by = provider_relations(index)
    complete = provided_by.complete
    satisfied: dict[str, tuple[str, ...]] = {}
    absent: list[PublicObligation] = []
    for obligation in surface.obligations:
        providers = provided_by.providers(obligation.symbol)
        if providers:
            satisfied[obligation.symbol] = providers
        else:
            absent.append(obligation)
    declared = surface.declared_symbols
    return SideReconciliation(
        side=surface.side,
        acquisition_key=surface.acquisition_key,
        surface_resolvable=True,
        obligations_total=len(surface.obligations),
        satisfied=satisfied,
        # The whole coverage rule, in one place: with an unread member the
        # absent set is *recorded* (`unresolved`) and never *concluded*
        # (`missing`), so no finding below can rest on unread evidence.
        missing=tuple(absent) if complete else (),
        unresolved=() if complete else tuple(absent),
        exports_total=len(index.symbols),
        exports_declared_in_headers=len(index.symbols & declared),
        coverage_complete=complete,
        coverage_reason=index.incompleteness_reason(),
    )


def _missing_export_finding(obligation: PublicObligation) -> Change:
    entity = "extern variable" if obligation.entity == "variable" else "function"
    return _change(
        ChangeKind.PUBLIC_NOT_EXPORTED,
        obligation.symbol,
        f"The release's public headers declare {entity} {obligation.name!r} "
        f"(expected symbol {obligation.symbol!r}) but no library in the bundle "
        "exports it. Code that compiles against the headers gets an "
        "undefined-symbol link error against the whole product, not against "
        "one member.",
        old_value=obligation.symbol,
        confidence=Confidence.HIGH,
        source_location=obligation.source_location,
    )


def reconcile_release(
    new_surface: ReleasePublicSurface,
    new_index: BundleExportIndex,
    *,
    old_surface: ReleasePublicSurface | None = None,
    old_index: BundleExportIndex | None = None,
    undocumented_exports_by_member: Mapping[str, int] | None = None,
) -> ReleaseReconciliation:
    """Fold both sides' reconciliations into one evolution-stated result.

    The pairing rules are ``workflows.cross_source_evolution``'s, applied to
    one obligation symbol as its identity: flagged on both sides ->
    ``PERSISTENT``; only NEW -> ``INTRODUCED``; only OLD -> ``RESOLVED``;
    flagged on a side whose sibling could not be evaluated ->
    ``NOT_EVALUATED``. A side is *evaluable* when its surface resolved and
    its export coverage is complete -- incomplete coverage is exactly the
    "could not confirm or deny" case, so an obligation absent under it never
    reads as introduced.
    """
    new_side = reconcile_side(new_surface, new_index)
    old_side = (
        reconcile_side(old_surface, old_index)
        if old_surface is not None and old_index is not None
        else None
    )

    new_evaluable = new_side.surface_resolvable and new_side.coverage_complete
    old_evaluable = bool(
        old_side is not None
        and old_side.surface_resolvable
        and old_side.coverage_complete
    )
    new_absent = {o.symbol: o for o in (new_side.missing or new_side.unresolved)}
    old_absent = (
        {o.symbol: o for o in (old_side.missing or old_side.unresolved)}
        if old_side is not None
        else {}
    )

    findings: list[Change] = []
    for symbol in sorted(set(new_absent) | set(old_absent)):
        new_hit = symbol in new_absent
        old_hit = symbol in old_absent
        if old_side is None:
            # No baseline exists (``--no-baseline``-shaped release): the
            # observation about the candidate is real, the history is not.
            if not new_hit or not new_evaluable:
                continue
            evolution = CrossSourceEvolution.NOT_EVALUATED
            obligation = new_absent[symbol]
        elif old_evaluable and new_evaluable:
            if old_hit and new_hit:
                evolution = CrossSourceEvolution.PERSISTENT
                obligation = new_absent[symbol]
            elif new_hit:
                evolution = CrossSourceEvolution.INTRODUCED
                obligation = new_absent[symbol]
            else:
                evolution = CrossSourceEvolution.RESOLVED
                obligation = old_absent[symbol]
        elif new_hit and new_evaluable and not old_evaluable:
            evolution = CrossSourceEvolution.NOT_EVALUATED
            obligation = new_absent[symbol]
        elif old_hit and old_evaluable and not new_evaluable:
            evolution = CrossSourceEvolution.NOT_EVALUATED
            obligation = old_absent[symbol]
        else:
            # Neither side could be evaluated: nothing was established, so
            # nothing is claimed. The obligations stay listed as unresolved
            # in each side's own record, which is where a reader looks.
            continue
        change = _missing_export_finding(obligation)
        change.cross_source_evolution = evolution
        findings.append(change)

    warnings: list[str] = []
    for side in (old_side, new_side):
        if side is None:
            continue
        if side.coverage_reason and not side.coverage_complete:
            warnings.append(
                f"release contract reconciliation on the {side.side} side is "
                f"incomplete: {side.coverage_reason}. "
                f"{len(side.unresolved)} public declaration(s) could not be "
                "proven missing from the whole bundle and are reported as "
                "unresolved rather than as missing exports."
            )
        if not side.surface_resolvable and side.coverage_reason:
            warnings.append(
                f"the {side.side} side's public surface could not be acquired: "
                f"{side.coverage_reason}"
            )

    return ReleaseReconciliation(
        old=old_side,
        new=new_side,
        findings=tuple(findings),
        undocumented_exports_by_member=dict(
            sorted((undocumented_exports_by_member or {}).items())
        ),
        coverage_warnings=tuple(warnings),
    )


def release_owned_checks() -> frozenset[str]:
    """The cross-source checks this module owns at release level.

    Exactly one check: ``public_not_exported``. It is the only one of the
    eleven whose *answer* changes when the whole product is considered -- a
    declaration this member does not export may be exported by a sibling,
    so the per-member answer is structurally a Cartesian-product artifact
    (787,833 of them on a 28-library Intel MKL release). Every other check,
    ``exported_not_public`` included, judges one binary's own evidence and
    stays per member; see this module's docstring for why moving that one
    would change nothing but the amount of duplicated code.

    Named through ``ChangeKind`` rather than
    ``buildsource.cross_source_checks``'s ``CHECK_*`` constant, which is
    the same slug: that module is ``workflows``-classified and
    ``policy -> workflows`` is a forbidden edge. This is not a second
    spelling of the vocabulary -- a check's name *is* its kind's value
    there too (``ALL_CHECKS`` and the ``ChangeKind`` values agree by
    construction, asserted by this module's own test), so deriving it from
    the model-owned enum shares the one source rather than copying a
    literal inward.
    """
    return frozenset({ChangeKind.PUBLIC_NOT_EXPORTED.value})


def undocumented_exports_by_member(
    surface: ReleasePublicSurface,
    index: BundleExportIndex,
    member_exports: Mapping[str, frozenset[str]],
) -> dict[str, int]:
    """How many of each member's exports no public header declares.

    Judged against *surface*'s one shared declaration index -- the release's
    public contract -- rather than against a per-member copy of it, which is
    what makes this one accounting over the product instead of N repetitions
    of the same comparison.

    Deliberately a *count* per member and not a classification: the precise
    per-symbol reason (external-dependency leak, internal-namespace escape,
    template instantiation, bare undeclared export) is
    ``buildsource.cross_source_checks``'s own accounting, which still runs
    per member against the same shared surface and still emits the finding
    where it belongs. Re-deriving that classification here would be a second
    copy of a 100-line accounting rule, which is exactly the duplication
    this workstream is removing elsewhere.

    An unresolvable surface yields an empty mapping: with no declaration
    index there is nothing to judge an export against, and reporting every
    export as undocumented would be the "failed extractor read as an empty
    surface" inversion.
    """
    if not surface.resolvable:
        return {}
    declared = surface.declared_symbols
    counts: dict[str, int] = {}
    for member in index.members:
        exports = member_exports.get(member)
        if exports is None:
            continue
        counts[member] = len(exports - declared)
    return dict(sorted(counts.items()))
