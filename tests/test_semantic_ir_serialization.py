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

from hypothesis import given, strategies as st

from abicheck.model.fact import Fact
from abicheck.model.identity import Namespace, Record, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
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


class TestAbsence:
    def test_a_snapshot_without_an_ir_writes_no_key(self) -> None:
        assert "semantic_ir" not in snapshot_to_dict(_snapshot(None))

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
