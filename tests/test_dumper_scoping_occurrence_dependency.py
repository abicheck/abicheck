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
"""Direct unit tests for :func:`~abicheck.extract.occurrence_dependency_scope.
scoped_occurrences_excluding_dependencies` -- split out of
``tests/test_dumper_scoping.py`` to keep that file at its
``architecture/debt.yaml``-implied line budget (it has no adoption-debt
entry of its own, so the AI-readiness ``new-test-size`` gate's 1200-line
cap applies directly).

This is the primitive ``dumper_scoping._scoped_semantic_ir`` builds on;
tested here at the primitive level, decoupled from the full
snapshot-scoping pipeline `test_dumper_scoping.py` exercises, per this
repository's own "Primitive-level property tests" convention (AGENTS.md).
"""

from __future__ import annotations

from abicheck.extract.occurrence_dependency_scope import (
    scoped_occurrences_excluding_dependencies,
)
from abicheck.model.fact import Fact
from abicheck.model.identity import entity_id_for_type, entity_id_for_typedef
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity

_SYSTEM_HEADER = "/usr/include/c++/11/string"
_OWN_HEADER = "/src/myproject/include/api.h"


def test_non_scoped_kind_survives_regardless_of_kept_entity_ids():
    """A typedef (a non-scoped ``EntityKind``) is never checked against
    *kept_entity_ids* or dependency-header origin at all."""
    typedef_id = entity_id_for_typedef((), "MyAlias")
    occ_id = OccurrenceId(typedef_id, disambiguator=_SYSTEM_HEADER)
    entity = CanonicalEntity(canonical_spelling=Fact.present("int"))

    kept, excluded = scoped_occurrences_excluding_dependencies(
        {occ_id: entity}, kept_entity_ids=set(), header_roots=None
    )

    assert kept == {occ_id: entity}
    assert excluded == []


def test_excluded_entity_id_drops_every_occurrence():
    """An ``EntityId`` not in *kept_entity_ids* is excluded outright --
    matching the flat-field filter exactly, regardless of where its own
    occurrence's disambiguator points."""
    dropped = entity_id_for_type((), "Dropped")
    occ_id = OccurrenceId(dropped, disambiguator=_OWN_HEADER)
    entity = CanonicalEntity(canonical_spelling=Fact.present("Dropped"))

    kept, excluded = scoped_occurrences_excluding_dependencies(
        {occ_id: entity}, kept_entity_ids=set(), header_roots=None
    )

    assert kept == {}
    assert excluded == [occ_id]


def test_kept_identity_with_only_dependency_occurrences_survives_whole():
    """Direct-reference retention (Codex review, second round, fresh
    evidence): a kept identity every one of whose own occurrences lives
    under a dependency header must keep ALL of them -- dropping any would
    leave the still-retained flat entity with zero SemanticIR evidence,
    defeating `scope_snapshot_excluding_dependencies`'s own deliberate
    retention of a directly-referenced dependency type."""
    dep = entity_id_for_type((), "std::string")
    occ_a = OccurrenceId(dep, disambiguator=_SYSTEM_HEADER)
    occ_b = OccurrenceId(dep, disambiguator=f"{_SYSTEM_HEADER}:99")
    entity_a = CanonicalEntity(canonical_spelling=Fact.present("std::string"))
    entity_b = CanonicalEntity(canonical_spelling=Fact.present("std::string"))

    kept, excluded = scoped_occurrences_excluding_dependencies(
        {occ_a: entity_a, occ_b: entity_b},
        kept_entity_ids={dep},
        header_roots=None,
    )

    assert kept == {occ_a: entity_a, occ_b: entity_b}
    assert excluded == []


def test_kept_identity_drops_only_the_dependency_occurrence():
    """The ordinary case this function exists for: a kept identity with
    BOTH a project-header occurrence and a dependency-header occurrence
    drops only the dependency one, since the project occurrence already
    stands in for that identity's SemanticIR evidence."""
    entity_id = entity_id_for_type((), "Widget")
    own_occ = OccurrenceId(entity_id, disambiguator=_OWN_HEADER)
    dep_occ = OccurrenceId(entity_id, disambiguator=_SYSTEM_HEADER)
    own_entity = CanonicalEntity(canonical_spelling=Fact.present("Widget"))
    dep_entity = CanonicalEntity(canonical_spelling=Fact.present("Widget"))

    kept, excluded = scoped_occurrences_excluding_dependencies(
        {own_occ: own_entity, dep_occ: dep_entity},
        kept_entity_ids={entity_id},
        header_roots=None,
    )

    assert kept == {own_occ: own_entity}
    assert excluded == [dep_occ]
