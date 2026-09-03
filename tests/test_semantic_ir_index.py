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

"""``SemanticIRIndex`` (ADR-063 Phase 6B, "PR 2" first slice) — primitive-level
tests over a synthetic :class:`~abicheck.model.semantic_ir.SemanticIR`, not a
real extraction. This mirrors how ``ResolvedExecutionContext`` ("PR 1") was
tested: the query facade is proven correct in isolation, with no live
detector caller yet (see the module's own docstring for why)."""

from __future__ import annotations

import pytest

from abicheck.model.fact import Fact
from abicheck.model.identity import (
    EntityKind,
    entity_id_for_function,
    entity_id_for_type,
    entity_id_for_variable,
)
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_index import SemanticIRIndex

_FUNC_ID = entity_id_for_function((), "compute", param_types=("int",))
_VAR_ID = entity_id_for_variable((), "kMaxPoints")
_TYPE_ID = entity_id_for_type((), "Point")


def _entity(
    spelling: str = "int compute(int)",
    *,
    template_arguments: Fact[tuple[str, ...]] | None = None,
    cv_qualification: Fact[tuple[str, ...]] | None = None,
    producer: str = "castxml",
) -> CanonicalEntity:
    return CanonicalEntity(
        canonical_spelling=Fact.present(spelling),
        template_arguments=template_arguments
        if template_arguments is not None
        else Fact.present(()),
        cv_qualification=cv_qualification
        if cv_qualification is not None
        else Fact.present(()),
        producer=producer,
    )


class TestEntityLookup:
    def test_finds_a_present_entity(self) -> None:
        entity = _entity()
        index = SemanticIRIndex(
            SemanticIR(occurrences={OccurrenceId(_FUNC_ID): entity})
        )
        assert index.entity(_FUNC_ID) is entity

    def test_missing_entity_returns_none(self) -> None:
        index = SemanticIRIndex(SemanticIR(occurrences={}))
        assert index.entity(_FUNC_ID) is None

    def test_reduces_to_the_entity_with_more_present_facts(self) -> None:
        # Two occurrences sharing one EntityId (an ODR-duplicate/incomplete
        # declaration pair) -- `entity()` must return the more complete one,
        # matching `SemanticIR.canonical_entities()`'s own contract.
        incomplete = _entity(template_arguments=Fact.not_collected())
        complete = _entity()
        index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(_FUNC_ID, "tu-a"): incomplete,
                    OccurrenceId(_FUNC_ID, "tu-b"): complete,
                }
            )
        )
        assert index.entity(_FUNC_ID) is complete


class TestOccurrencesFor:
    def test_returns_every_occurrence_sharing_an_identity(self) -> None:
        occ_a = OccurrenceId(_FUNC_ID, "tu-a")
        occ_b = OccurrenceId(_FUNC_ID, "tu-b")
        index = SemanticIRIndex(
            SemanticIR(occurrences={occ_a: _entity(), occ_b: _entity()})
        )
        assert set(index.occurrences_for(_FUNC_ID)) == {occ_a, occ_b}

    def test_unknown_identity_returns_empty(self) -> None:
        index = SemanticIRIndex(SemanticIR(occurrences={}))
        assert index.occurrences_for(_FUNC_ID) == ()


class TestKindFiltering:
    def _mixed_index(self) -> SemanticIRIndex:
        return SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(_FUNC_ID): _entity("int compute(int)"),
                    OccurrenceId(_VAR_ID): _entity("int kMaxPoints"),
                    OccurrenceId(_TYPE_ID): _entity("Point"),
                }
            )
        )

    def test_functions_returns_only_function_entities(self) -> None:
        index = self._mixed_index()
        assert set(index.functions()) == {_FUNC_ID}

    def test_variables_returns_only_variable_entities(self) -> None:
        index = self._mixed_index()
        assert set(index.variables()) == {_VAR_ID}

    def test_records_returns_only_type_entities(self) -> None:
        index = self._mixed_index()
        assert set(index.records()) == {_TYPE_ID}

    def test_entities_of_kind_matches_the_named_accessors(self) -> None:
        index = self._mixed_index()
        assert index.entities_of_kind(EntityKind.FUNCTION) == index.functions()
        assert index.entities_of_kind(EntityKind.VARIABLE) == index.variables()
        assert index.entities_of_kind(EntityKind.TYPE) == index.records()

    def test_empty_index_has_no_entities_of_any_kind(self) -> None:
        index = SemanticIRIndex(SemanticIR(occurrences={}))
        assert index.functions() == {}
        assert index.variables() == {}
        assert index.records() == {}


class TestFactLookup:
    def test_returns_the_named_fact(self) -> None:
        entity = _entity(cv_qualification=Fact.present(("const",)))
        index = SemanticIRIndex(
            SemanticIR(occurrences={OccurrenceId(_FUNC_ID): entity})
        )
        assert index.fact(_FUNC_ID, "cv_qualification") == Fact.present(("const",))
        assert index.fact(_FUNC_ID, "canonical_spelling") == entity.canonical_spelling

    def test_missing_entity_returns_none_not_a_raise(self) -> None:
        index = SemanticIRIndex(SemanticIR(occurrences={}))
        assert index.fact(_FUNC_ID, "canonical_spelling") is None

    def test_unknown_fact_name_returns_none_not_a_raise(self) -> None:
        index = SemanticIRIndex(
            SemanticIR(occurrences={OccurrenceId(_FUNC_ID): _entity()})
        )
        assert index.fact(_FUNC_ID, "no_such_fact") is None


class TestImmutability:
    def test_index_is_frozen(self) -> None:
        index = SemanticIRIndex(SemanticIR(occurrences={}))
        with pytest.raises(Exception):
            index.ir = SemanticIR(occurrences={})  # type: ignore[misc]

    def test_canonical_view_is_computed_once(self) -> None:
        ir = SemanticIR(occurrences={OccurrenceId(_FUNC_ID): _entity()})
        index = SemanticIRIndex(ir)
        first = index.entity(_FUNC_ID)
        second = index.entity(_FUNC_ID)
        assert first is second
