# Copyright 2026 Nikolay Petrov
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

"""ADR-084: observed header or source evidence that a declaration is contract.

A source-level break (``API_BREAK``) claims that a header or source tree
promised something a consumer compiles against. A binary-level observation
cannot prove that: an exported template instantiation, a DWARF record or an
export-table stub shows that a consumer *could link* against an entity, not
that a declaration was ever part of the promised source contract.

This module answers the one question the binary-depth gate asks of a
declaration: did a producer that *read a header or source* observe it? It is
a leaf of ``model`` so that ``compare`` detectors (which may not import
``policy``) and ``policy`` steps can share one answer.

Two producer classes count, neither of them an export lookup:

* fact (a) ``declared_in_headers`` confirmed -- a header parse of this run
  declared the entity (``header_ast_surface_facts``, every header backend);
* fact (b) ``in_public_contract`` confirmed by a non-binary producer -- a
  supplied public-header set placed it in the contract, or a source-level
  extractor judged it (``provenance.tag_provenance``, the L4 source extractor).

A confirmed (b) whose producer is a debug-info or PDB reader is **not**
counted: ``extract/surface_fact_producers.debug_info_surface_facts`` records
(b) from a confirmed export there (``producer="dwarf"``), which is a true
statement about the binary and carries no header evidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .availability import FactStatus
from .evidence_status import ReachabilityState
from .surface_facts import (
    SurfaceFactBearing,
    declared_in_headers,
    in_public_contract,
    is_confirmed_true,
    is_legacy_derived,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .entities import EnumType, RecordType

__all__ = [
    "BINARY_SIDE_CONTRACT_PRODUCERS",
    "ContractEvidenceByName",
    "PUBLIC_SOURCE_VISIBILITIES",
    "has_observed_source_contract_evidence",
    "observed_source_contract_names",
    "public_reachability",
    "reachability_of",
]

#: Producers whose ``in_public_contract`` fact is read off a binary or debug
#: artifact rather than a header or source tree. Their (b) answer is a
#: confirmed export, not a contract observation.
BINARY_SIDE_CONTRACT_PRODUCERS: frozenset[str] = frozenset({"dwarf", "pdb"})

#: ``SourceEntity.visibility`` values an L4 source pack records for a
#: declaration in a public header. Mirrors ``buildsource/source_link.py``'s
#: ``_PUBLIC_VISIBILITY``, which routes the same entities to the public
#: surface; ``tests/test_binary_gated_contract.py`` pins the two equal.
PUBLIC_SOURCE_VISIBILITIES: frozenset[str] = frozenset({"public_header", "generated"})

#: The ``SourceAbiSurface`` buckets an entity is routed into once it is public.
_SOURCE_ABI_BUCKETS = (
    "reachable_declarations",
    "reachable_types",
    "reachable_macros",
    "reachable_templates",
    "reachable_inline_bodies",
)


def observed_source_contract_names(snapshot: object) -> frozenset[str]:
    """Every name the snapshot's embedded L4 source pack observed in a public header.

    A source producer that parsed the public headers records each declaration
    it found there as a ``SourceEntity`` with a public visibility. Its
    qualified name, mangled name and producer-specific names are the spellings
    a change's ``symbol`` may use. The pack is read through attributes rather
    than an import so ``model`` stays a leaf of ``buildsource``.

    Returns an empty set when the snapshot carries no source pack, or a pack
    without a source ABI surface.
    """
    pack = getattr(snapshot, "build_source", None)
    surface = getattr(pack, "source_abi", None)
    if surface is None:
        return frozenset()
    names: set[str] = set()
    for bucket in _SOURCE_ABI_BUCKETS:
        for entity in getattr(surface, bucket, ()):
            if not entity.api_relevant:
                continue
            if entity.visibility not in PUBLIC_SOURCE_VISIBILITIES:
                continue
            names.update(
                value
                for value in (
                    entity.qualified_name,
                    entity.mangled_name,
                    *entity.names.values(),
                )
                if value
            )
    return frozenset(names)


def has_observed_source_contract_evidence(
    decl: SurfaceFactBearing | RecordType | EnumType, *, header_parsed: bool = False
) -> bool:
    """Whether a header or source producer observed *decl* as contract.

    Stricter than :func:`~abicheck.model.surface_facts.has_observed_contract_evidence`,
    which also accepts a debug-info or export-derived (b). That predicate
    answers "is this entity known to be public", which is the right question
    for narrowing an ELF export set. This one answers "is there header or
    source evidence for a source-level claim", which a binary-only run cannot
    supply, however many of its exports are confirmed.

    *header_parsed* is the owning snapshot's ``from_headers``. Records and
    enums carry no (a)/(b) facts, only ``source_header_fact``, and debug info
    sets that too, so for them the header observation is the pair: the
    snapshot was parsed from headers *and* the declaration has a confirmed
    header location. Functions and variables answer from their facts alone.

    Legacy-derived facts (a pre-v46 ``PUBLIC`` bridge, a hand-built record
    with no producer) never count, for the same reason the narrower predicate
    excludes them.
    """
    # `is_confirmed_true` admits PARTIAL, and a legacy-derived PARTIAL is
    # exactly what a record's source location bridges to, so both clauses
    # exclude legacy-derived facts explicitly. Records and enums do not
    # declare the (a)/(b) fields, so the accessors answer them from the
    # legacy bridge alone (never confirmed) and the header clause below takes
    # over.
    surface = cast("SurfaceFactBearing", decl)
    declared = declared_in_headers(surface)
    if is_confirmed_true(declared) and not is_legacy_derived(declared):
        return True
    contract = in_public_contract(surface)
    if (
        is_confirmed_true(contract)
        and not is_legacy_derived(contract)
        and contract.producer not in BINARY_SIDE_CONTRACT_PRODUCERS
    ):
        return True
    if not header_parsed:
        return False
    located = getattr(decl, "source_header_fact", None)
    return (
        located is not None
        and located.status is FactStatus.PRESENT
        and bool(located.value)
    )


class ContractEvidenceByName:
    """Per name: whether any declaration filed under it has observed
    header/source contract evidence (:func:`has_observed_source_contract_evidence`).

    A detector indexes every declaration but tags reachability only for the
    names it reports, so the question is asked lazily, once per reported name,
    rather than of every indexed declaration. :meth:`confirm` records a name
    another signal already proved public.
    """

    __slots__ = ("_answers", "_decls")

    def __init__(self) -> None:
        self._decls: dict[str, list[tuple[Any, bool]]] = {}
        self._answers: dict[str, bool] = {}

    def add(self, name: str, decl: Any, *, header_parsed: bool) -> None:
        self._decls.setdefault(name, []).append((decl, header_parsed))

    def confirm(self, name: str) -> None:
        self._answers[name] = True

    def has(self, name: str) -> bool:
        answer = self._answers.get(name)
        if answer is None:
            answer = self._answers[name] = any(
                has_observed_source_contract_evidence(decl, header_parsed=parsed)
                for decl, parsed in self._decls.get(name, ())
            )
        return answer


def reachability_of(
    decl: SurfaceFactBearing | RecordType | EnumType, *, header_parsed: bool = False
) -> dict[str, Any]:
    """:func:`public_reachability` for the declaration *decl* itself."""
    return public_reachability(
        has_observed_source_contract_evidence(decl, header_parsed=header_parsed)
    )


def public_reachability(public_evidence: bool) -> dict[str, Any]:
    """The reachability tags a finding carries, read off its own evidence.

    Used in place of a hard-coded ``public_reachable=True``: a proven public
    claim needs the observed evidence, and without it reachability is
    recorded as unknown rather than asserted (ADR-084).
    """
    if public_evidence:
        return {
            "public_reachable": True,
            "reachability_state": ReachabilityState.PROVEN_REACHABLE,
            "reachability_kind": "direct_public_symbol",
        }
    return {
        "public_reachable": False,
        "reachability_state": ReachabilityState.UNKNOWN,
        "reachability_kind": None,
    }
