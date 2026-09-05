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

"""`diff_filtering._dedup_exact`'s own key contract, stated directly rather
than only through one caller (root `AGENTS.md`'s "Primitive-level property
tests" guidance).

Split out of `test_cov95_diff_filtering.py` (which is at the test-file line
cap) rather than grown there -- a fresh, focused file for one primitive.

Regression coverage for Codex review, PR #1078, sixteenth round:
`_dedup_exact` used to key only on `(kind, description)`, which collapsed
two genuinely distinct findings whenever a detector legitimately emits
identical description text for two different entities -- exactly what
`compare.typedefs`/`compare.constants`'s occurrence-level collision handling
does when a whole colliding group vanishes or newly appears (each
contributing entity gets its own finding, but two anonymous-scoped entities
sharing a rendered alias produce byte-identical description text by
construction). `tests/test_diff_layout.py`'s own
`test_second_type_still_compared_when_first_shares_its_bare_name` had
already named the identical risk for a bare-name-keyed `RecordType`
detector as a known, then-unaddressed concern.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.diff_filtering import _dedup_exact
from abicheck.model.identity import Namespace, entity_id_for_typedef


def _removed(
    *, symbol: str, old_value: object, entity_id=None, disambiguator=None
) -> Change:
    return Change(
        kind=ChangeKind.TYPEDEF_REMOVED,
        symbol=symbol,
        description=f"Typedef removed: {symbol}",
        old_value=old_value,
        entity_id=entity_id,
        disambiguator=disambiguator,
    )


class TestDedupExactDistinguishesCollidingOccurrences:
    def test_two_distinct_entities_sharing_kind_and_description_both_survive(
        self,
    ) -> None:
        """Same alias, same value -- only `entity_id` can distinguish them
        (the exact case a colliding group's two anonymous-scoped entities
        produce when their values happen to coincide)."""
        eid_a = entity_id_for_typedef((Namespace("a"),), "Alias")
        eid_b = entity_id_for_typedef((Namespace("b"),), "Alias")
        changes = [
            _removed(symbol="Alias", old_value="int", entity_id=eid_a),
            _removed(symbol="Alias", old_value="int", entity_id=eid_b),
        ]
        result = _dedup_exact(changes)
        assert len(result) == 2

    def test_two_distinct_entities_with_different_values_both_survive_even_with_no_entity_id(
        self,
    ) -> None:
        """No `entity_id` at all (a producer that predates entity identity)
        -- the value difference alone must still keep them apart."""
        changes = [
            _removed(symbol="Alias", old_value="int"),
            _removed(symbol="Alias", old_value="long"),
        ]
        result = _dedup_exact(changes)
        assert len(result) == 2

    def test_a_genuine_duplicate_emission_still_collapses(self) -> None:
        """Two byte-identical findings for the very same entity (the
        original purpose of this pass) still collapse to one."""
        eid = entity_id_for_typedef((Namespace("a"),), "Alias")
        changes = [
            _removed(symbol="Alias", old_value="int", entity_id=eid),
            _removed(symbol="Alias", old_value="int", entity_id=eid),
        ]
        result = _dedup_exact(changes)
        assert len(result) == 1

    def test_a_list_valued_old_value_does_not_crash_and_stays_hashable(
        self,
    ) -> None:
        """`Change.old_value` is annotated `str | None` but not enforced
        (`diff_python.py` stores lists there) -- the key must not raise on
        an unhashable value slot."""
        changes = [
            _removed(symbol="X", old_value=["a", "b"]),
            _removed(symbol="X", old_value=["a", "b"]),
            _removed(symbol="X", old_value=["a", "c"]),
        ]
        result = _dedup_exact(changes)
        assert len(result) == 2


class TestDedupExactDistinguishesOccurrenceDisambiguators:
    """Regression coverage for Codex review, PR #1078, seventeenth round:
    two genuine ODR/multi-TU occurrences legitimately share one `entity_id`
    (`OccurrenceId.disambiguator` is what tells them apart) -- the sixteenth
    round's own `entity_id`-based fix alone still collapsed them when both
    carried the same value.
    """

    def test_two_occurrences_sharing_entity_id_with_different_disambiguators_both_survive(
        self,
    ) -> None:
        eid = entity_id_for_typedef((Namespace("a"),), "Alias")
        changes = [
            _removed(
                symbol="Alias", old_value="int", entity_id=eid, disambiguator="tu-a"
            ),
            _removed(
                symbol="Alias", old_value="int", entity_id=eid, disambiguator="tu-b"
            ),
        ]
        result = _dedup_exact(changes)
        assert len(result) == 2

    def test_a_genuine_duplicate_with_no_disambiguator_still_collapses(self) -> None:
        """The common case (no occurrence-level identity at all, both
        `None`) is unaffected -- still a genuine duplicate."""
        eid = entity_id_for_typedef((Namespace("a"),), "Alias")
        changes = [
            _removed(symbol="Alias", old_value="int", entity_id=eid),
            _removed(symbol="Alias", old_value="int", entity_id=eid),
        ]
        result = _dedup_exact(changes)
        assert len(result) == 1


class TestEntityIdKeyingScopedToOccurrenceAwareKinds:
    """Regression coverage for Codex review, PR #1078, twenty-first round:
    `entity_id`/`disambiguator` predate this PR and are already
    asymmetrically populated between evidence tiers for kinds outside
    `compare.typedefs`/`compare.constants` -- e.g. `diff_types.py`'s
    AST-based ``FIELD_RENAMED`` sets a real `entity_id`, but
    `diff_platform.py`'s DWARF-layout-based ``FIELD_RENAMED`` for the
    identical rename does not. Folding `entity_id` into every kind's key
    (rather than scoping it to `_OCCURRENCE_AWARE_KINDS`) would have
    stopped this pair from collapsing across evidence tiers the way it
    always has, reintroducing a duplicate the pre-sixteenth-round key
    never produced.
    """

    def _field_renamed(self, *, entity_id=None) -> Change:
        return Change(
            kind=ChangeKind.FIELD_RENAMED,
            symbol="Foo",
            description="Field renamed: old -> new",
            old_value="old",
            new_value="new",
            entity_id=entity_id,
        )

    def test_an_ast_finding_with_entity_id_still_collapses_a_dwarf_duplicate_without_one(
        self,
    ) -> None:
        eid = entity_id_for_typedef((Namespace("a"),), "Foo")
        changes = [
            self._field_renamed(entity_id=eid),  # AST-based, real entity_id
            self._field_renamed(entity_id=None),  # DWARF-based, no entity_id
        ]
        result = _dedup_exact(changes)
        assert len(result) == 1

    def test_two_typedef_removed_occurrences_are_unaffected_by_the_scoping(
        self,
    ) -> None:
        """`_OCCURRENCE_AWARE_KINDS` itself is unaffected by this scoping --
        confirms the twenty-first round's fix doesn't quietly regress the
        sixteenth/seventeenth rounds' own coverage above."""
        eid_a = entity_id_for_typedef((Namespace("a"),), "Alias")
        eid_b = entity_id_for_typedef((Namespace("b"),), "Alias")
        changes = [
            _removed(symbol="Alias", old_value="int", entity_id=eid_a),
            _removed(symbol="Alias", old_value="int", entity_id=eid_b),
        ]
        result = _dedup_exact(changes)
        assert len(result) == 2
