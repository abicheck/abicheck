# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""T9 (duplication-and-convergence-assessment.md Phase 6 item 4): a
per-``Fact`` ``producer`` attached to a semantic-IR-consumed fact must
survive ``storage/semantic_ir_codec.py``'s encode/decode round trip, not
just the legacy ``storage/fact_codec.py`` one (Codex review, PR #1075:
``_fact_to_dict``/``_fact_from_dict`` originally carried only status,
value, and diagnostics).
"""

from __future__ import annotations

import pytest

from abicheck.model.availability import FactStatus
from abicheck.model.fact import Fact
from abicheck.model.identity import entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.storage.semantic_ir_codec import (
    _fact_from_dict,
    _fact_to_dict,
    semantic_ir_from_document,
    semantic_ir_to_document,
)

FOO = entity_id_for_type((), "Foo")


class TestFactDictProducerRoundTrip:
    """The direct, private-function round trip the fix targets."""

    def test_producer_survives_to_dict_and_from_dict(self) -> None:
        original: Fact[str] = Fact.unsupported(
            "pdb never captures this", producer="pdb"
        )
        encoded = _fact_to_dict(original)
        assert encoded["producer"] == "pdb"
        decoded = _fact_from_dict(
            encoded, as_tuple=False, field_name="canonical_spelling"
        )
        assert decoded == original
        assert decoded.producer == "pdb"

    def test_unset_producer_is_omitted_not_written_as_null(self) -> None:
        """Matches this codec's own established convention (``CanonicalEntity.
        producer``/``_entity_to_dict``): an unset provenance field is
        omitted, not persisted as an explicit ``null``."""
        encoded = _fact_to_dict(Fact.present("x"))
        assert "producer" not in encoded

    def test_a_document_predating_the_field_decodes_producer_as_none(self) -> None:
        raw = {"status": "present", "value": "x", "diagnostics": []}
        decoded = _fact_from_dict(raw, as_tuple=False, field_name="canonical_spelling")
        assert decoded.producer is None

    def test_a_non_string_producer_is_rejected(self) -> None:
        raw = {"status": "present", "value": "x", "diagnostics": [], "producer": 7}
        with pytest.raises(TypeError):
            _fact_from_dict(raw, as_tuple=False, field_name="canonical_spelling")


class TestSemanticIrDocumentRoundTrip:
    """End to end, through the public ``encode_semantic_ir``/
    ``decode_semantic_ir`` entry points a real snapshot save/load uses."""

    def test_producer_on_a_semantic_ir_fact_survives_the_document_round_trip(
        self,
    ) -> None:
        entity = CanonicalEntity(
            canonical_spelling=Fact.present("Foo", producer="dwarf"),
        )
        ir = SemanticIR({OccurrenceId(FOO): entity})
        document = semantic_ir_to_document(ir, {})
        decoded_ir, _ = semantic_ir_from_document(document)
        assert decoded_ir is not None
        decoded_entity = decoded_ir.occurrences[OccurrenceId(FOO)]
        assert decoded_entity.canonical_spelling.status is FactStatus.PRESENT
        assert decoded_entity.canonical_spelling.value == "Foo"
        assert decoded_entity.canonical_spelling.producer == "dwarf"

    def test_no_producer_round_trips_to_none(self) -> None:
        entity = CanonicalEntity(canonical_spelling=Fact.present("Foo"))
        ir = SemanticIR({OccurrenceId(FOO): entity})
        document = semantic_ir_to_document(ir, {})
        decoded_ir, _ = semantic_ir_from_document(document)
        assert decoded_ir is not None
        decoded_entity = decoded_ir.occurrences[OccurrenceId(FOO)]
        assert decoded_entity.canonical_spelling.producer is None
