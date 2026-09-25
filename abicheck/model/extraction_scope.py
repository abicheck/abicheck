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

"""What a snapshot's declarations were classified under, and each one's answer.

ADR-075 D1/D2/D3. Two shapes and one rule:

* :class:`ExtractionScope` -- the ownership rules a snapshot was classified
  under (roots already normalized against the project root), what was kept
  of dependency declarations, and the reserved prefilter. Stored as
  ``AbiSnapshot.extraction_scope``; ``None`` there means *unrecorded*, which
  no reader may read as ``full``.
* :class:`EntityOwnership` -- one declaration's (owner, contract, rule id),
  carried on ``Function``/``Variable``/``RecordType``/``EnumType`` as
  ``ownership_fact``. :func:`ownership_of` is the one reader.
* :func:`compare_extraction_scopes` -- ADR-075 D3's table, the one decision
  ``comparability`` refuses on and ``confidence`` warns from.

Classification itself (paths -> decision) is ``extract.ownership``'s job;
this module only holds the result.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .availability import FactStatus
from .fact import Fact
from .ownership_rules import DependencyRoots, OwnershipRules

__all__ = [
    "DEPENDENCY_EVIDENCE_FULL",
    "DEPENDENCY_EVIDENCE_REFERENCED",
    "EXTRACTION_SCOPE_NOTE_MARKER",
    "SUPPORTED_DEPENDENCY_EVIDENCE",
    "EntityOwnership",
    "ExtractionScope",
    "OwnershipBearing",
    "ScopeComparison",
    "compare_extraction_scopes",
    "extraction_scope_identity",
    "extraction_scope_notes",
    "extraction_scope_refusal",
    "moved_declarations",
    "ownership_of",
    "snapshot_scope_identity",
]

#: Keep every dependency declaration the parse produced (today's behaviour).
DEPENDENCY_EVIDENCE_FULL = "full"
#: Keep only dependency types an owned declaration references. Recognised so
#: a snapshot from a later build compares correctly; not yet producible
#: (the target-ownership plan's Phase 3).
DEPENDENCY_EVIDENCE_REFERENCED = "referenced"
#: What ``.abicheck.yml``'s ``scope.dependency_evidence`` accepts today.
SUPPORTED_DEPENDENCY_EVIDENCE: frozenset[str] = frozenset({DEPENDENCY_EVIDENCE_FULL})


@dataclass(frozen=True)
class EntityOwnership:
    """One declaration's classification (ADR-075 D2).

    ``owner`` is ``target`` / ``dependency:<name>`` / ``toolchain`` /
    ``unresolved``; ``contract`` is ``public`` / ``private`` / ``external`` /
    ``unresolved``; ``rule_id`` names the rule that decided. ``unresolved``
    is a positive answer (classified, no root claims the file) and is not
    the same as an unclassified declaration, which has no decision at all.
    """

    owner: str
    contract: str
    rule_id: str


class OwnershipBearing(Protocol):
    ownership_fact: Fact[EntityOwnership] | None


def ownership_of(decl: object) -> EntityOwnership | None:
    """*decl*'s classification, or ``None`` when it was never classified.

    The one reader of ``ownership_fact``: a missing field (a model object
    that predates it), an unset one (a pre-v52 snapshot, a hand-built
    declaration) and a non-``PRESENT`` fact all answer ``None`` -- unknown,
    never a guessed owner.
    """
    fact = getattr(decl, "ownership_fact", None)
    if not isinstance(fact, Fact) or fact.status is not FactStatus.PRESENT:
        return None
    value = fact.value
    return value if isinstance(value, EntityOwnership) else None


def _canonical_rules(rules: OwnershipRules) -> dict[str, Any]:
    return {
        "target_roots": sorted(set(rules.target_roots)),
        "dependencies": [
            {"name": d.name, "header_roots": sorted(set(d.header_roots))}
            for d in sorted(rules.dependencies, key=lambda d: d.name)
        ],
        "private_headers": sorted(set(rules.private_headers)),
        "private_namespaces": sorted(set(rules.private_namespaces)),
    }


@dataclass(frozen=True)
class ExtractionScope:
    """``AbiSnapshot.extraction_scope`` (ADR-075 D1).

    *ownership_rules*' roots are already normalized (relative to the project
    root where they lie under it, POSIX spelling); the canonical form sorts
    and de-duplicates them, so two runs stating the same rules in another
    order share one :attr:`fingerprint`.
    """

    ownership_rules: OwnershipRules = field(default_factory=OwnershipRules)
    dependency_evidence: str = DEPENDENCY_EVIDENCE_FULL
    #: Reserved for the plan's Phase 4 prefilter; always ``None`` today.
    prefilter: Mapping[str, Any] | None = None
    #: The classifier's rule-7 diagnostics, sorted and unique.
    diagnostics: tuple[str, ...] = ()

    def canonical(self) -> dict[str, Any]:
        """The fingerprinted part: rules, evidence mode and prefilter."""
        return {
            "ownership_rules": _canonical_rules(self.ownership_rules),
            "dependency_evidence": self.dependency_evidence,
            "prefilter": dict(self.prefilter) if self.prefilter is not None else None,
        }

    @property
    def fingerprint(self) -> str:
        blob = json.dumps(
            self.canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        out = self.canonical()
        out["fingerprint"] = self.fingerprint
        if self.diagnostics:
            out["diagnostics"] = list(self.diagnostics)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExtractionScope:
        raw = data.get("ownership_rules")
        rules_map: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}

        def strs(value: object) -> tuple[str, ...]:
            if isinstance(value, list):
                return tuple(str(v) for v in value)
            return ()

        deps: list[DependencyRoots] = []
        for entry in rules_map.get("dependencies") or ():
            if isinstance(entry, Mapping) and isinstance(entry.get("name"), str):
                deps.append(
                    DependencyRoots(str(entry["name"]), strs(entry.get("header_roots")))
                )
        prefilter = data.get("prefilter")
        return cls(
            ownership_rules=OwnershipRules(
                target_roots=strs(rules_map.get("target_roots")),
                dependencies=tuple(deps),
                private_headers=strs(rules_map.get("private_headers")),
                private_namespaces=strs(rules_map.get("private_namespaces")),
            ),
            # Kept verbatim, even a value this build cannot produce: a
            # snapshot a later build narrowed under `referenced` must be
            # refused against a `full` one, not relabelled `full`.
            dependency_evidence=str(
                data.get("dependency_evidence") or DEPENDENCY_EVIDENCE_FULL
            ),
            prefilter=dict(prefilter) if isinstance(prefilter, Mapping) else None,
            diagnostics=strs(data.get("diagnostics")),
        )


@dataclass(frozen=True)
class ScopeComparison:
    """ADR-075 D3's answer for one pair."""

    #: Set when the pair must be refused (``ScopeMismatchError``).
    refuse_reason: str | None = None
    #: Comparable, but a reader must be told (report line / warning).
    notes: tuple[str, ...] = ()
    #: Both sides recorded, both ``full``, rules differ: findings may have
    #: moved owner or contract, so the report enumerates them.
    rules_differ: bool = False


def _narrowing(scope: ExtractionScope) -> str | None:
    """Why *scope* changed which declarations exist, or ``None`` under ``full``."""
    if scope.prefilter is not None:
        return f"a {scope.prefilter.get('kind', 'frontend')!s} prefilter"
    if scope.dependency_evidence != DEPENDENCY_EVIDENCE_FULL:
        return f"dependency_evidence: {scope.dependency_evidence}"
    return None


def compare_extraction_scopes(
    old: ExtractionScope | None, new: ExtractionScope | None
) -> ScopeComparison:
    """ADR-075 D3: refuse what changed presence, note what changed labels.

    The caller applies this only when both sides carry header-derived
    declarations; a binary-only side has nothing to have classified.
    """
    if old is None and new is None:
        return ScopeComparison()
    if old is None or new is None:
        recorded, side = (new, "old") if old is None else (old, "new")
        assert recorded is not None
        narrowing = _narrowing(recorded)
        if narrowing is not None:
            return ScopeComparison(
                refuse_reason=(
                    f"the {side} snapshot does not record the extraction scope "
                    f"it was dumped under, while the other side was narrowed by "
                    f"{narrowing}; nothing proves the unrecorded side kept the "
                    "same declarations. Re-dump it with this abicheck version "
                    "under the same scope configuration."
                )
            )
        return ScopeComparison(
            notes=(
                f"the {side} snapshot predates extraction-scope recording "
                "(snapshot schema v52), so its declarations carry no recorded "
                "owner or contract; the comparison proceeds because no earlier "
                "build could narrow a dump by ownership rules.",
            )
        )
    if old.dependency_evidence != new.dependency_evidence:
        return ScopeComparison(
            refuse_reason=(
                "old and new snapshots keep dependency declarations differently "
                f"(old: dependency_evidence={old.dependency_evidence!r}, new: "
                f"{new.dependency_evidence!r}), so a dependency declaration "
                "present on one side only would read as an addition or removal. "
                "Re-dump both sides under the same scope.dependency_evidence."
            )
        )
    if (old.prefilter or None) != (new.prefilter or None):
        return ScopeComparison(
            refuse_reason=(
                "old and new snapshots were narrowed by different frontend "
                "prefilters, so they do not cover the same declarations. "
                "Re-dump both sides with the same prefilter."
            )
        )
    if old.fingerprint == new.fingerprint:
        return ScopeComparison()
    if _narrowing(old) is not None or _narrowing(new) is not None:
        return ScopeComparison(
            refuse_reason=(
                "old and new snapshots were classified under different ownership "
                "rules while dependency declarations were narrowed by "
                "ownership, so the rules changed which declarations exist. "
                "Re-scoping such a project requires a new baseline."
            )
        )
    return ScopeComparison(
        notes=(
            "old and new snapshots were classified under different ownership "
            "rules (scope.public_header_dirs / dependencies / private_*); "
            "declarations are the same, but some may carry a different owner "
            "or contract on each side.",
        ),
        rules_differ=True,
    )


def extraction_scope_identity(
    old: ExtractionScope | None, new: ExtractionScope | None, *, has_old: bool = True
) -> str:
    """ADR-075 D4: the comparison's ``surface.ownership`` digest value.

    One fingerprint when both sides agree, ``old=…|new=…`` when they do not,
    ``""`` for an unrecorded side. *has_old* is ``False`` for a no-baseline
    comparison, which has only the candidate's scope.
    """
    old_id = old.fingerprint if old is not None else ""
    new_id = new.fingerprint if new is not None else ""
    if old_id == new_id or not has_old:
        return new_id
    return f"old={old_id}|new={new_id}"


def _both_from_headers(old: object, new: object) -> bool:
    return bool(getattr(old, "from_headers", False)) and bool(
        getattr(new, "from_headers", False)
    )


def extraction_scope_refusal(old: object, new: object) -> str | None:
    """Why two snapshots may not be compared on this axis, or ``None``.

    Only a pair whose two sides both carry header-derived declarations is
    judged: a binary- or debug-only side classified nothing.
    """
    if old is None or not _both_from_headers(old, new):
        return None
    return compare_extraction_scopes(
        getattr(old, "extraction_scope", None), getattr(new, "extraction_scope", None)
    ).refuse_reason


#: How many moved declarations a note names before summarizing the rest.
_MOVED_NAMED = 10


def _ownership_index(snapshot: object) -> dict[tuple[str, str], EntityOwnership]:
    index: dict[tuple[str, str], EntityOwnership] = {}
    for kind in ("functions", "variables", "types", "enums"):
        for decl in getattr(snapshot, kind, ()) or ():
            decision = ownership_of(decl)
            if decision is None:
                continue
            # A type's `name` is its unqualified leaf: `a::Impl` and `b::Impl`
            # must not share a key.
            key = str(
                getattr(decl, "mangled", "")
                or getattr(decl, "qualified_name", "")
                or getattr(decl, "name", "")
            )
            index.setdefault((kind, key), decision)
    return index


def moved_declarations(old: object, new: object) -> list[tuple[str, str, str]]:
    """``(name, old owner/contract, new owner/contract)`` for each declaration
    present on both sides whose classification differs, sorted by name.

    A declaration is matched by its linker name, else its qualified name, else
    its name, within its kind. Only the owner and contract count: a changed rule id alone (a root
    spelled differently) moves nothing a reader sees.
    """
    old_index, new_index = _ownership_index(old), _ownership_index(new)
    moved: list[tuple[str, str, str]] = []
    for key in sorted(old_index.keys() & new_index.keys(), key=lambda k: k[1]):
        a, b = old_index[key], new_index[key]
        if (a.owner, a.contract) != (b.owner, b.contract):
            moved.append((key[1], f"{a.owner}/{a.contract}", f"{b.owner}/{b.contract}"))
    return moved


#: Substring every note below carries, so a consumer can filter them.
EXTRACTION_SCOPE_NOTE_MARKER = "extraction scope"


def extraction_scope_notes(old: object, new: object) -> list[str]:
    """ADR-075 D3's report lines for a comparable pair (coverage warnings)."""
    if old is None or not _both_from_headers(old, new):
        return []
    comparison = compare_extraction_scopes(
        getattr(old, "extraction_scope", None), getattr(new, "extraction_scope", None)
    )
    if comparison.refuse_reason is not None:
        return []
    notes = [f"{EXTRACTION_SCOPE_NOTE_MARKER}: {n}" for n in comparison.notes]
    if comparison.rules_differ:
        moved = moved_declarations(old, new)
        if moved:
            named = "; ".join(f"{n} ({a} -> {b})" for n, a, b in moved[:_MOVED_NAMED])
            more = len(moved) - _MOVED_NAMED
            notes.append(
                f"{EXTRACTION_SCOPE_NOTE_MARKER}: {len(moved)} declaration(s) "
                f"moved owner or contract between the two sides, so a finding "
                f"about one of them was classified differently on each: {named}"
                + (f"; and {more} more" if more > 0 else "")
            )
    return notes


def snapshot_scope_identity(old: object, new: object) -> str:
    """:func:`extraction_scope_identity` read off two snapshots (*old* may be
    ``None`` for a no-baseline comparison)."""
    return extraction_scope_identity(
        getattr(old, "extraction_scope", None),
        getattr(new, "extraction_scope", None),
        has_old=old is not None,
    )
