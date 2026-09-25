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

"""Ownership in the graph: owner, contract and provider relations.

Evidence-entity-model Phase 3 (invariant I5), decided by ADR-075 D5. Three
edge kinds, every entity endpoint an invariant-I1 node id
(``model.graph_entity_identity.snapshot_identities``):

* ``owned_by`` -- declaration/type -> ``owner://<owner>``;
* ``in_contract`` -- declaration/type -> ``contract://<contract>``;
* ``provided_by`` -- an observed export entry
  (``binary_symbol://<platform>/<spelling>``, the Phase 2 node) ->
  ``release_member://<member>``.

**Evidence classes, and why.** ``owned_by``/``in_contract`` are ``derived``:
the classification itself is an extraction-time decision (it needed the
file as the frontend saw it, the project root and castxml's ``artificial``
bit), recorded per entity as ``ownership_fact`` (ADR-075 D2). The edge only
projects that record onto the entity's node id, so by I3 it is recomputed
from the record and never trusted when persisted. ``provided_by`` joins two
observations -- a member's own export table and the export entry -- under a
stated rule, so it is ``resolved_join``. Following the Phase 5 rule
("persist observed, derive the rest") none of the three is ever persisted;
:data:`OWNERSHIP_RELATION_SPECS` states each one's producer, inputs and
recompute rule.

**Unknown stays unknown.** A declaration with no recorded decision gets no
``owned_by``/``in_contract`` edge, and :meth:`OwnershipRelations.contract`
answers ``None`` for it -- never a fabricated ``unresolved``, which is a
positive answer (classified, no root claims the file).

**The one contract model.** :meth:`OwnershipRelations.owes_no_export` is the
one place the obligation half of the contract is read: the per-member
``public_not_exported`` check and the release surface's obligations both ask
it, so a declaration another component owns (its headers declared as a
dependency) is nobody's missing export on the scalar path, in a one-member
package, and across a release alike.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from ..model.extraction_scope import ownership_of
from ..model.graph_entity_identity import SnapshotIdentities, snapshot_identities
from ..model.graph_evidence_class import EdgeEvidenceClass
from ..model.graph_join import JoinSpec, binary_symbol_node_id
from ..model.ownership_rules import CONTRACT_EXTERNAL, CONTRACT_PRIVATE

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot
    from .bundle_export_index import BundleExportIndex

__all__ = [
    "CONTRACT_PREFIX",
    "EDGE_KIND_IN_CONTRACT",
    "EDGE_KIND_OWNED_BY",
    "EDGE_KIND_PROVIDED_BY",
    "IN_CONTRACT",
    "OWNED_BY",
    "OWNERSHIP_RELATION_SPECS",
    "OWNER_PREFIX",
    "PROVIDED_BY",
    "RELEASE_MEMBER_PREFIX",
    "OwnershipRelations",
    "ProviderRelations",
    "contract_node_id",
    "contract_relations",
    "owner_node_id",
    "ownership_relations",
    "provider_relations",
    "release_member_node_id",
]

EDGE_KIND_OWNED_BY = "owned_by"
EDGE_KIND_IN_CONTRACT = "in_contract"
EDGE_KIND_PROVIDED_BY = "provided_by"

OWNER_PREFIX = "owner://"
CONTRACT_PREFIX = "contract://"
RELEASE_MEMBER_PREFIX = "release_member://"

#: Contracts under which a declaration owes this target no export.
_NO_OBLIGATION = frozenset({CONTRACT_PRIVATE, CONTRACT_EXTERNAL})


def owner_node_id(owner: str) -> str:
    return f"{OWNER_PREFIX}{owner}"


def contract_node_id(contract: str) -> str:
    return f"{CONTRACT_PREFIX}{contract}"


def release_member_node_id(member: str) -> str:
    return f"{RELEASE_MEMBER_PREFIX}{member}"


OWNED_BY = JoinSpec(
    edge_kind=EDGE_KIND_OWNED_BY,
    producer="compare.ownership_relations.ownership_relations",
    inputs=(
        "each Function/Variable/RecordType/EnumType's ownership_fact "
        "(stamped once by extract.ownership_stamp, persisted in "
        "AbiSnapshot.extraction_scope)",
        "model.graph_entity_identity.snapshot_identities (I1 node ids)",
    ),
    recompute_rule=(
        "one edge per classified entity to owner://<owner>; an unclassified "
        "entity gets none. Recomputed from the snapshot on demand, never "
        "persisted."
    ),
    evidence_class=EdgeEvidenceClass.DERIVED,
)
IN_CONTRACT = JoinSpec(
    edge_kind=EDGE_KIND_IN_CONTRACT,
    producer="compare.ownership_relations.ownership_relations",
    inputs=OWNED_BY.inputs,
    recompute_rule=(
        "one edge per classified entity to contract://<contract>; an "
        "unclassified entity gets none. Recomputed on demand, never persisted."
    ),
    evidence_class=EdgeEvidenceClass.DERIVED,
)
PROVIDED_BY = JoinSpec(
    edge_kind=EDGE_KIND_PROVIDED_BY,
    producer="compare.ownership_relations.provider_relations",
    inputs=(
        "compare.bundle_export_index.BundleExportIndex (each member's "
        "default-version export set, model.export_index projection)",
    ),
    recompute_rule=(
        "an export entry binary_symbol://<platform>/<spelling> is provided by "
        "every member whose export set contains the spelling; a member with no "
        "export table contributes nothing and makes the relation incomplete. "
        "Recomputed per release comparison, never persisted."
    ),
)
OWNERSHIP_RELATION_SPECS: Mapping[str, JoinSpec] = MappingProxyType(
    {spec.edge_kind: spec for spec in (OWNED_BY, IN_CONTRACT, PROVIDED_BY)}
)


@dataclass(frozen=True)
class OwnershipRelations:
    """The ``owned_by``/``in_contract`` relations of one snapshot."""

    #: ``None`` for a snapshot that recorded no classification at all: every
    #: query then answers unknown without building the identity table.
    identities: SnapshotIdentities | None
    #: I1 node id -> owner / contract, for classified entities only.
    owner_of: Mapping[str, str] = field(default_factory=dict)
    contract_of: Mapping[str, str] = field(default_factory=dict)
    #: Node ids two entities shared with *different* decisions. Such a node
    #: answers unknown: picking one would misclassify the other.
    conflicting: frozenset[str] = frozenset()

    def owner(self, node_id: str) -> str | None:
        return None if node_id in self.conflicting else self.owner_of.get(node_id)

    def contract(self, node_id: str) -> str | None:
        return None if node_id in self.conflicting else self.contract_of.get(node_id)

    def owes_no_export(self, node_id: str) -> bool:
        """Whether the entity at *node_id* is, by its recorded contract, not
        this target's export obligation (``private`` or ``external``).

        ``False`` for ``public``, ``unresolved`` and an unclassified entity:
        those fall back to the caller's existing public-header predicate --
        missing evidence narrows no conclusion here, it only declines to
        lift an obligation.
        """
        return self.contract(node_id) in _NO_OBLIGATION

    def function_owes_no_export(self, index: int) -> bool:
        if self.identities is None:
            return False
        return self.owes_no_export(self.identities.functions[index].node_id)

    def variable_owes_no_export(self, index: int) -> bool:
        if self.identities is None:
            return False
        return self.owes_no_export(self.identities.variables[index].node_id)

    def edges(self) -> Iterator[tuple[str, str, str]]:
        """``(src, dst, edge_kind)`` for every relation, sorted by source."""
        for node_id in sorted(self.owner_of):
            if node_id in self.conflicting:
                continue
            yield node_id, owner_node_id(self.owner_of[node_id]), EDGE_KIND_OWNED_BY
            yield (
                node_id,
                contract_node_id(self.contract_of[node_id]),
                EDGE_KIND_IN_CONTRACT,
            )


def ownership_relations(
    snap: AbiSnapshot, identities: SnapshotIdentities | None = None
) -> OwnershipRelations:
    """Project *snap*'s per-entity ownership onto its I1 node ids."""
    ids = identities if identities is not None else snapshot_identities(snap)
    owner_of: dict[str, str] = {}
    contract_of: dict[str, str] = {}
    conflicting: set[str] = set()
    pairs = (
        (snap.functions, ids.functions),
        (snap.variables, ids.variables),
        (snap.types, ids.records),
        (snap.enums, ids.enums),
    )
    for decls, idents in pairs:
        for decl, ident in zip(decls, idents):
            decision = ownership_of(decl)
            if decision is None:
                continue
            node_id = ident.node_id
            previous = owner_of.get(node_id)
            if previous is not None and (
                previous != decision.owner or contract_of[node_id] != decision.contract
            ):
                conflicting.add(node_id)
                continue
            owner_of[node_id] = decision.owner
            contract_of[node_id] = decision.contract
    return OwnershipRelations(
        identities=ids,
        owner_of=owner_of,
        contract_of=contract_of,
        conflicting=frozenset(conflicting),
    )


def contract_relations(snap: AbiSnapshot) -> OwnershipRelations:
    """:func:`ownership_relations`, or an all-unknown relation without the
    identity table for a snapshot that recorded no extraction scope (every
    pre-v52 snapshot, every binary-only one) -- the cheap path the obligation
    readers take on every comparison."""
    if getattr(snap, "extraction_scope", None) is None:
        return OwnershipRelations(identities=None)
    return ownership_relations(snap)


@dataclass(frozen=True)
class ProviderRelations:
    """The ``provided_by`` relation of one release side."""

    index: BundleExportIndex
    platform: str

    @property
    def complete(self) -> bool:
        return self.index.complete

    def providers(self, spelling: str) -> tuple[str, ...]:
        """The members providing export *spelling* (empty when none does)."""
        return self.index.providers(spelling)

    def edges(self) -> Iterator[tuple[str, str, str]]:
        for spelling, members in self.index.providers_by_symbol.items():
            src = binary_symbol_node_id(self.platform, spelling)
            for member in members:
                yield src, release_member_node_id(member), EDGE_KIND_PROVIDED_BY


def provider_relations(index: BundleExportIndex) -> ProviderRelations:
    """The ``provided_by`` relation over *index* -- the release model's own
    ``symbol -> member`` map, never a second derivation of it."""
    return ProviderRelations(index=index, platform=index.platform)
