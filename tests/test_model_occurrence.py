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

"""``OccurrenceId``/``canonical_key`` (ADR-063 D3/Phase 3)."""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.model.identity import Namespace, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId, canonical_key

_names = st.text(
    min_size=1, max_size=12, alphabet=st.characters(min_codepoint=97, max_codepoint=122)
)
_disambiguators = st.text(max_size=16)


class TestCanonicalKeyReducesToEntityIdKey:
    """The invariant Phase 3's own design text states explicitly: an
    ``OccurrenceId`` with an empty disambiguator must produce *exactly* the
    same string as its bare ``EntityId``, so a public-surface graph node
    (built with no disambiguator -- L0/L2 evidence carries no TU context)
    collides correctly with an L5-sourced node for the identical
    declaration, wherever the two encodings otherwise happen to agree."""

    def test_bare_entity_id(self) -> None:
        eid = entity_id_for_type((), "Foo")
        assert canonical_key(eid) == eid.key

    def test_occurrence_with_no_disambiguator(self) -> None:
        eid = entity_id_for_type((Namespace("ns"),), "Foo")
        assert canonical_key(OccurrenceId(eid)) == eid.key

    def test_occurrence_with_explicit_empty_disambiguator(self) -> None:
        eid = entity_id_for_type((), "Foo")
        assert canonical_key(OccurrenceId(eid, disambiguator="")) == eid.key

    @given(name=_names)
    def test_holds_for_any_entity(self, name: str) -> None:
        eid = entity_id_for_type((), name)
        assert canonical_key(OccurrenceId(eid)) == canonical_key(eid) == eid.key


class TestCanonicalKeyDisambiguatorChangesKey:
    """A genuinely populated disambiguator is the whole point of the type --
    it must produce a *different* key from the bare entity, and two
    different disambiguators for the same entity must not collide with
    each other either (the internal-linkage same-TU-signal-missing case
    this type exists to resolve, D5's own two-file-local-``static``
    example)."""

    def test_differs_from_bare_entity_key(self) -> None:
        eid = entity_id_for_type((), "helper")
        occ = OccurrenceId(eid, disambiguator="tu:a.cpp")
        assert canonical_key(occ) != eid.key

    def test_distinct_disambiguators_never_collide(self) -> None:
        eid = entity_id_for_type((), "helper")
        a = OccurrenceId(eid, disambiguator="tu:a.cpp")
        b = OccurrenceId(eid, disambiguator="tu:b.cpp")
        assert canonical_key(a) != canonical_key(b)

    @given(name=_names, disambiguator=_disambiguators)
    def test_never_raises_and_agrees_with_empty_iff_disambiguator_empty(
        self, name: str, disambiguator: str
    ) -> None:
        eid = entity_id_for_type((), name)
        key = canonical_key(OccurrenceId(eid, disambiguator=disambiguator))
        assert isinstance(key, str) and key
        assert (key == eid.key) == (disambiguator == "")


class TestOccurrenceIdEquality:
    """Ordinary frozen-dataclass equality -- not the identity contract
    ``canonical_key`` itself provides, but the object still needs to behave
    as a real value type for anything that holds one in a set/dict key
    before reducing it to a string."""

    def test_equal_fields_are_equal(self) -> None:
        eid = entity_id_for_type((), "Foo")
        assert OccurrenceId(eid, "x") == OccurrenceId(eid, "x")

    def test_differing_disambiguator_is_not_equal(self) -> None:
        eid = entity_id_for_type((), "Foo")
        assert OccurrenceId(eid, "x") != OccurrenceId(eid, "y")

    def test_hashable(self) -> None:
        eid = entity_id_for_type((), "Foo")
        s = {OccurrenceId(eid, "x"), OccurrenceId(eid, "y")}
        assert len(s) == 2
