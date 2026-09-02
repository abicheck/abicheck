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

"""``AbiSnapshot.semantic_ir`` persistence (ADR-063 Phase 6, schema v38).

Goes through the real ``snapshot_to_dict``/``snapshot_from_dict``
chokepoints and through ``json.dumps`` — not the codec's own internals —
per AGENTS.md's third-party-boundary rule: the two defects this encoding
exists to avoid (``dataclasses.asdict()`` raising on a dataclass dict key,
and a string-flattened key losing the typed ``ScopePath`` inside it) are
both invisible to a test that calls the codec directly with an
already-flat fixture.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, strategies as st

from abicheck.model.fact import Fact
from abicheck.model.identity import Namespace, Record, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId, canonical_key
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

_tags = st.text(
    min_size=1, max_size=8, alphabet=st.characters(min_codepoint=97, max_codepoint=122)
)


def _snapshot(ir: SemanticIR | None, **kwargs: object) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so.1", version="1.0.0", semantic_ir=ir, **kwargs)  # type: ignore[arg-type]


def _round_trip(snap: AbiSnapshot) -> AbiSnapshot:
    # json.dumps/loads, not just the dict: a tuple that survives the dict
    # round trip but comes back as a list from JSON would otherwise pass.
    return snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))


class TestRoundTrip:
    def test_odr_duplicate_pair_survives_with_both_occurrences(self) -> None:
        """Two occurrences sharing one ``EntityId`` — the shape a string-keyed
        or ``canonical_entities()``-reduced encoding would collapse."""
        eid = entity_id_for_type((Namespace("ns"), Record("Outer")), "Inner")
        complete = OccurrenceId(eid, disambiguator="tu-a")
        incomplete = OccurrenceId(eid, disambiguator="tu-b")
        ir = SemanticIR(
            occurrences={
                complete: CanonicalEntity(
                    canonical_spelling=Fact.present("ns::Outer::Inner"),
                    template_arguments=Fact.present(("int", "char")),
                    cv_qualification=Fact.present(("const", "volatile")),
                    producer="castxml",
                ),
                incomplete: CanonicalEntity(
                    canonical_spelling=Fact.present("ns::Outer::Inner"),
                    template_arguments=Fact.not_collected(),
                    cv_qualification=Fact.unsupported("no cv evidence"),
                    producer="clang",
                ),
            }
        )
        reloaded = _round_trip(_snapshot(ir))
        assert reloaded.semantic_ir is not None
        assert dict(reloaded.semantic_ir.occurrences) == dict(ir.occurrences)

    def test_typed_scope_segments_survive(self) -> None:
        """A rendered-string key could not tell these two apart: identical
        leaf names, identical rendering, different typed scope."""
        nested_in_record = entity_id_for_type((Record("Outer"),), "Inner")
        nested_in_namespace = entity_id_for_type((Namespace("Outer"),), "Inner")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(nested_in_record): CanonicalEntity(
                    canonical_spelling=Fact.present("Outer::Inner"),
                ),
                OccurrenceId(nested_in_namespace): CanonicalEntity(
                    canonical_spelling=Fact.present("Outer::Inner"),
                ),
            }
        )
        reloaded = _round_trip(_snapshot(ir))
        assert reloaded.semantic_ir is not None
        assert len(reloaded.semantic_ir.occurrences) == 2
        scopes = {occ.entity_id.scope for occ in reloaded.semantic_ir.occurrences}
        assert scopes == {(Record("Outer"),), (Namespace("Outer"),)}

    @given(tags=st.lists(_tags, min_size=1, max_size=4, unique=True))
    def test_every_occurrence_round_trips(self, tags: list[str]) -> None:
        eid = entity_id_for_type((), "Foo")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(eid, disambiguator=tag): CanonicalEntity(
                    canonical_spelling=Fact.present(f"Foo<{tag}>"),
                    template_arguments=Fact.present((tag,)),
                )
                for tag in tags
            }
        )
        reloaded = _round_trip(_snapshot(ir))
        assert reloaded.semantic_ir is not None
        assert dict(reloaded.semantic_ir.occurrences) == dict(ir.occurrences)

    def test_conflicts_round_trip(self) -> None:
        snap = _snapshot(None, semantic_ir_conflicts={"key": "'clang::Foo'"})
        assert _round_trip(snap).semantic_ir_conflicts == {"key": "'clang::Foo'"}


class TestDeterministicEncoding:
    """`storage/AGENTS.md` invariant 4: a semantic digest must never depend on
    incidental order. Two equal `SemanticIR` values built in opposite
    insertion orders describe identical state, so they must encode to an
    identical document — otherwise a content hash over the snapshot reports a
    difference that does not exist."""

    @given(tags=st.lists(_tags, min_size=2, max_size=5, unique=True))
    def test_insertion_order_does_not_change_the_document(
        self, tags: list[str]
    ) -> None:
        eid = entity_id_for_type((), "Foo")
        entries = [
            (
                OccurrenceId(eid, disambiguator=tag),
                CanonicalEntity(canonical_spelling=Fact.present(f"Foo<{tag}>")),
            )
            for tag in tags
        ]
        forward = snapshot_to_dict(_snapshot(SemanticIR(occurrences=dict(entries))))
        backward = snapshot_to_dict(
            _snapshot(SemanticIR(occurrences=dict(reversed(entries))))
        )
        assert json.dumps(forward, sort_keys=True) == json.dumps(
            backward, sort_keys=True
        )

    def test_entries_are_sorted_by_canonical_key(self) -> None:
        eid = entity_id_for_type((), "Foo")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(eid, disambiguator=tag): CanonicalEntity(
                    canonical_spelling=Fact.present("Foo")
                )
                for tag in ("zzz", "aaa", "mmm")
            }
        )
        document = snapshot_to_dict(_snapshot(ir))
        written = [
            entry["occurrence"]["disambiguator"]
            for entry in document["semantic_ir"]["occurrences"]
        ]
        assert written == sorted(
            written,
            key=lambda tag: canonical_key(OccurrenceId(eid, disambiguator=tag)),
        )


class TestMalformedDocumentsAreRefused:
    """`storage/AGENTS.md` invariant 6: never coerce a value a decision reads.
    A coerced disambiguator would make a JSON `1` and `"1"` one `OccurrenceId`
    and silently drop one of the two occurrences on load."""

    def _document_with_disambiguators(self, first: object, second: object) -> dict:
        eid = entity_id_for_type((), "Foo")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(eid, disambiguator="1"): CanonicalEntity(
                    canonical_spelling=Fact.present("first")
                ),
                OccurrenceId(eid, disambiguator="2"): CanonicalEntity(
                    canonical_spelling=Fact.present("second")
                ),
            }
        )
        document = snapshot_to_dict(_snapshot(ir))
        entries = document["semantic_ir"]["occurrences"]
        entries[0]["occurrence"]["disambiguator"] = first
        entries[1]["occurrence"]["disambiguator"] = second
        return document

    @pytest.mark.parametrize("bad", [1, 1.0, True, ["1"], {"v": "1"}])
    def test_a_non_string_disambiguator_is_rejected(self, bad: object) -> None:
        document = self._document_with_disambiguators(bad, "2")
        with pytest.raises(TypeError, match="disambiguator"):
            snapshot_from_dict(document)

    def test_coercion_would_have_lost_an_occurrence(self) -> None:
        # The failure the rejection above prevents, stated directly: a
        # coercing decoder maps JSON 1 and "1" onto one key.
        document = self._document_with_disambiguators(1, "1")
        with pytest.raises(TypeError):
            snapshot_from_dict(document)

    def test_a_non_string_producer_is_rejected(self) -> None:
        eid = entity_id_for_type((), "Foo")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(eid): CanonicalEntity(
                    canonical_spelling=Fact.present("Foo"), producer="castxml"
                )
            }
        )
        document = snapshot_to_dict(_snapshot(ir))
        document["semantic_ir"]["occurrences"][0]["entity"]["producer"] = 7
        with pytest.raises(TypeError, match="producer"):
            snapshot_from_dict(document)

    def test_a_duplicate_occurrence_entry_is_rejected(self) -> None:
        """The list encoding can spell a duplicate the mapping cannot hold —
        a load that silently kept the last one would report success having
        discarded occurrence evidence."""
        eid = entity_id_for_type((), "Foo")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(eid, disambiguator="tu-a"): CanonicalEntity(
                    canonical_spelling=Fact.present("first")
                )
            }
        )
        document = snapshot_to_dict(_snapshot(ir))
        entries = document["semantic_ir"]["occurrences"]
        duplicate = json.loads(json.dumps(entries[0]))
        duplicate["entity"]["canonical_spelling"]["value"] = "second"
        entries.append(duplicate)
        with pytest.raises(ValueError, match="same occurrence twice"):
            snapshot_from_dict(document)

    @pytest.mark.parametrize(
        "bad", ["parse error", [1], [None], ["ok", 2], {"a": "b"}]
    )
    def test_malformed_fact_diagnostics_are_rejected(self, bad: object) -> None:
        """A bare `tuple()` would split a string into one diagnostic per
        character and coerce a non-string member into a manufactured one —
        both written back as if a producer had reported them."""
        eid = entity_id_for_type((), "Foo")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(eid): CanonicalEntity(
                    canonical_spelling=Fact.failed("real diagnostic")
                )
            }
        )
        document = snapshot_to_dict(_snapshot(ir))
        entity = document["semantic_ir"]["occurrences"][0]["entity"]
        entity["canonical_spelling"]["diagnostics"] = bad
        with pytest.raises(TypeError):
            snapshot_from_dict(document)

    def test_well_formed_diagnostics_survive(self) -> None:
        eid = entity_id_for_type((), "Foo")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(eid): CanonicalEntity(
                    canonical_spelling=Fact.failed("castxml exited 1"),
                    template_arguments=Fact.unsupported("no template evidence"),
                )
            }
        )
        reloaded = _round_trip(_snapshot(ir))
        assert reloaded.semantic_ir is not None
        assert dict(reloaded.semantic_ir.occurrences) == dict(ir.occurrences)

    @pytest.mark.parametrize("bad", [False, 0, {}, "", []])
    def test_a_falsey_malformed_diagnostics_value_is_rejected(
        self, bad: object
    ) -> None:
        """The class the earlier `or ()` fix left open: a *falsey* malformed
        value skipped the guard entirely and was rewritten as "no
        diagnostics". Absence and a present-but-corrupt value are different
        documents."""
        eid = entity_id_for_type((), "Foo")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(eid): CanonicalEntity(
                    canonical_spelling=Fact.failed("real diagnostic")
                )
            }
        )
        document = snapshot_to_dict(_snapshot(ir))
        entity = document["semantic_ir"]["occurrences"][0]["entity"]
        entity["canonical_spelling"]["diagnostics"] = bad
        if bad == []:
            # The one falsey value that is genuinely well-formed: an empty
            # list is a real, empty diagnostics collection.
            assert snapshot_from_dict(document).semantic_ir is not None
        else:
            with pytest.raises(TypeError):
                snapshot_from_dict(document)

    @pytest.mark.parametrize("bad", [[], "", 0, False, ["occurrences"]])
    def test_a_falsey_malformed_semantic_ir_container_is_rejected(
        self, bad: object
    ) -> None:
        """Same class, one level up: a present-but-malformed `semantic_ir`
        must not decode as "no backend produced one"."""
        document = snapshot_to_dict(_snapshot(None))
        document["semantic_ir"] = bad
        with pytest.raises(TypeError, match="semantic_ir"):
            snapshot_from_dict(document)

    @pytest.mark.parametrize("bad", [{}, "", 0, False, "occurrences"])
    def test_a_malformed_occurrences_list_is_rejected(self, bad: object) -> None:
        document = snapshot_to_dict(_snapshot(SemanticIR()))
        document["semantic_ir"]["occurrences"] = bad
        with pytest.raises(TypeError):
            snapshot_from_dict(document)

    @pytest.mark.parametrize("bad", [[], "", 0, False])
    def test_a_falsey_malformed_conflict_map_is_rejected(self, bad: object) -> None:
        document = snapshot_to_dict(_snapshot(None))
        document["semantic_ir_conflicts"] = bad
        with pytest.raises(TypeError, match="semantic_ir_conflicts"):
            snapshot_from_dict(document)

    def test_a_non_string_conflict_entry_is_rejected(self) -> None:
        document = snapshot_to_dict(_snapshot(None, semantic_ir_conflicts={"k": "v"}))
        document["semantic_ir_conflicts"] = {"k": 3}
        with pytest.raises(TypeError, match="semantic_ir_conflicts"):
            snapshot_from_dict(document)


class TestAbsence:
    def test_a_snapshot_without_an_ir_writes_no_key(self) -> None:
        document = snapshot_to_dict(_snapshot(None))
        assert "semantic_ir" not in document
        # Nor an empty conflict map: a snapshot no hybrid merge touched must
        # serialize exactly as it did before this field existed, or every
        # document written by every backend grows a key that says nothing.
        assert "semantic_ir_conflicts" not in document

    def test_a_document_without_the_key_loads_as_none(self) -> None:
        document = snapshot_to_dict(_snapshot(None))
        document.pop("semantic_ir", None)
        assert snapshot_from_dict(document).semantic_ir is None

    def test_empty_ir_round_trips_as_empty_not_none(self) -> None:
        document = snapshot_to_dict(_snapshot(SemanticIR()))
        assert document["semantic_ir"] == {"occurrences": []}
        # An IR that observed nothing is not the same as no IR at all — the
        # first says a narrowed backend ran and found nothing, the second
        # that no backend produced one — so the two must not both decode to
        # ``None``.
        reloaded = snapshot_from_dict(document)
        assert reloaded.semantic_ir == SemanticIR(occurrences={})
