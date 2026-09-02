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

"""``renumber_anonymous_closure_identities``'s ``SemanticIR``/
``semantic_ir_conflicts`` reach paths (ADR-063 Phase 6, second slice, Codex
review, PR #1001).

Split out of ``test_lambda_identity_ordinal.py`` (mechanical extraction,
unchanged test bodies) once that file crossed the AI-readiness
``new-test-size`` gate's 1200-line cap -- same "move responsibility instead
of raising the baseline" discipline this codebase applies to production
modules, applied to a test file (see e.g. ``test_mutation_per_module_
scoping.py``'s own split-out precedent). These four tests were the
addition that tipped it over.

``SemanticIR.occurrences`` is keyed by ``OccurrenceId`` (wrapping an
``EntityId``), which is a different reach path than a dataclass field's
plain VALUE (``RecordType.entity_id``, covered in the sibling file's own
``TestFrozenDataclassesReachableFromTheWalkAreRebuilt.
test_entity_id_carrier_on_a_real_snapshot_is_renumbered``) -- the generic
walk's dict-handling used to only rewrite a **string** dict key, leaving a
dataclass-typed one (this one) untouched. ``AbiSnapshot.
semantic_ir_conflicts`` has a related but distinct gap: its own keys are
*packed* strings (``model.semantic_ir.semantic_ir_conflict_key``), so an
in-place text rewrite of an embedded marker corrupts the packed length
prefix rather than fixing anything -- it needs a dedicated re-key function
(``model.semantic_ir.renumber_conflict_keys``) instead of the generic walk.
"""

from __future__ import annotations

from abicheck.model import AbiSnapshot, RecordType
from abicheck.model.fact import Fact
from abicheck.model.identity import Namespace, Record, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import (
    CanonicalEntity,
    SemanticIR,
    semantic_ir_conflict_key,
)
from abicheck.name_classification import strip_anonymous_type_location
from abicheck.qualified_name_segments import renumber_anonymous_closure_identities


def _closure(header: str, line: int, col: int) -> str:
    return strip_anonymous_type_location(f"(lambda at /src/x/{header}:{line}:{col})")


def _record(name: str, qualified: str | None = None) -> RecordType:
    return RecordType(name=name, kind="class", qualified_name=qualified, size_bits=8)


class TestSemanticIrReachableFromTheWalkIsRebuilt:
    def test_semantic_ir_occurrence_key_is_renumbered_in_step_with_its_value(
        self,
    ) -> None:
        """ADR-063 Phase 6 (second slice, Codex review, PR #1001):
        ``SemanticIR.occurrences`` is keyed by ``OccurrenceId``, which wraps
        an ``EntityId`` -- itself reachable, and renumbered, when it hangs
        off ``RecordType.entity_id`` (a plain field VALUE). But a dict's own
        KEY is a different reach path than a dataclass field's value, and
        the walk's dict-handling used to only rewrite a **string** key,
        leaving a dataclass-typed key (this one) completely untouched --
        so the record's flat ``name`` spelling renumbered to ``#N`` while
        its own ``SemanticIR`` occurrence stayed keyed on the raw,
        line-tainted marker, and the entity's own ``canonical_spelling``
        (a plain field VALUE, always correctly reached) renumbered too --
        leaving the KEY the only stale part of the whole structure.
        """
        marker = _closure("task_group.h", 20, 4)
        rec = _record("R", qualified=f"ns::{marker}::R")
        eid = entity_id_for_type((Namespace("ns"), Record(marker)), "R")
        rec.entity_id = eid

        occ_id = OccurrenceId(eid)
        semantic_ir = SemanticIR(
            occurrences={
                occ_id: CanonicalEntity(
                    canonical_spelling=Fact.present(f"ns::{marker}::R")
                )
            }
        )
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[rec, _record("Other", qualified=_closure("task_group.h", 10, 2))],
            semantic_ir=semantic_ir,
        )
        renumber_anonymous_closure_identities(snap)

        assert snap.semantic_ir is not None
        (renumbered_occ_id,) = snap.semantic_ir.occurrences.keys()
        (renumbered_entity,) = snap.semantic_ir.occurrences.values()

        scope_names = [
            getattr(seg, "name", "") for seg in renumbered_occ_id.entity_id.scope
        ]
        assert any("#2" in name for name in scope_names), scope_names
        assert not any(":20:4" in name for name in scope_names)
        # Key and value agree with each other and with the flat `types`
        # spelling this occurrence describes -- the whole point of
        # renumbering all three reach paths together rather than some.
        assert renumbered_occ_id.entity_id == snap.types[0].entity_id
        assert (
            renumbered_entity.canonical_spelling.value == snap.types[0].qualified_name
        )

    def test_semantic_ir_conflicts_key_is_renumbered_to_match_a_fresh_lookup(
        self,
    ) -> None:
        """ADR-063 Phase 6 (second slice, Codex review, PR #1001):
        ``AbiSnapshot.semantic_ir_conflicts`` is keyed by
        ``semantic_ir_conflict_key(occurrence_id, fact_name)`` -- a
        length-prefixed PACKED string (``model.identity._packed``). Naively
        text-rewriting that string in place (the way every other reachable
        string is rewritten) corrupts it: the outer length prefix no longer
        matches the rewritten marker text's real (different) length, so the
        stored key ends up equal to neither its own old form nor a freshly
        recomputed one -- silently making the stored conflict diagnostic
        unreachable via the one lookup path (``semantic_ir_conflict_key``)
        any real consumer would use. Confirmed to fail (the key stays a
        stale/corrupted mismatch) against a version of this function that
        added ``"semantic_ir_conflicts"`` straight to `_LAMBDA_IDENTITY_
        FIELDS` instead of recomputing it via
        ``model.semantic_ir.renumber_conflict_keys``.
        """
        marker = _closure("task_group.h", 20, 4)
        eid = entity_id_for_type((Namespace("ns"),), f"Wrapper<{marker}>")
        occ_id = OccurrenceId(eid)
        old_key = semantic_ir_conflict_key(occ_id, "canonical_spelling")
        semantic_ir = SemanticIR(
            occurrences={
                occ_id: CanonicalEntity(
                    canonical_spelling=Fact.present(f"ns::Wrapper<{marker}>")
                )
            }
        )
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            semantic_ir=semantic_ir,
            semantic_ir_conflicts={old_key: repr("clang's discarded value")},
        )
        renumber_anonymous_closure_identities(snap)

        assert snap.semantic_ir is not None
        (renumbered_occ_id,) = snap.semantic_ir.occurrences.keys()
        fresh_key = semantic_ir_conflict_key(renumbered_occ_id, "canonical_spelling")

        assert fresh_key in snap.semantic_ir_conflicts
        assert old_key not in snap.semantic_ir_conflicts
        assert snap.semantic_ir_conflicts[fresh_key] == repr("clang's discarded value")

    def test_semantic_ir_conflict_value_is_renumbered_too(self) -> None:
        """ADR-063 Phase 6 (second slice, Codex review, PR #1001, third
        round): the discarded backend's own spelling -- the conflict
        dict's VALUE -- can embed the identical closure marker the
        retained (winning) spelling does. Unlike the key, a value carries
        no packed-length encoding to corrupt, so it is rewritten the
        ordinary way rather than left stale."""
        marker = _closure("task_group.h", 20, 4)
        eid = entity_id_for_type((Namespace("ns"),), f"Wrapper<{marker}>")
        occ_id = OccurrenceId(eid)
        old_key = semantic_ir_conflict_key(occ_id, "canonical_spelling")
        semantic_ir = SemanticIR(
            occurrences={
                occ_id: CanonicalEntity(
                    canonical_spelling=Fact.present(f"ns::Wrapper<{marker}>")
                )
            }
        )
        discarded_value = repr(f"clang::Wrapper<{marker}>")
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            semantic_ir=semantic_ir,
            semantic_ir_conflicts={old_key: discarded_value},
        )
        renumber_anonymous_closure_identities(snap)

        assert snap.semantic_ir is not None
        (renumbered_occ_id,) = snap.semantic_ir.occurrences.keys()
        fresh_key = semantic_ir_conflict_key(renumbered_occ_id, "canonical_spelling")

        assert ":20:4" not in snap.semantic_ir_conflicts[fresh_key]
        assert "#1" in snap.semantic_ir_conflicts[fresh_key]
        assert snap.semantic_ir_conflicts[fresh_key] == repr(
            "clang::Wrapper<(lambda:task_group.h#1)>"
        )

    def test_semantic_ir_conflicts_untouched_when_nothing_needs_renumbering(
        self,
    ) -> None:
        eid = entity_id_for_type((), "Plain")
        occ_id = OccurrenceId(eid)
        key = semantic_ir_conflict_key(occ_id, "canonical_spelling")
        semantic_ir = SemanticIR(
            occurrences={
                occ_id: CanonicalEntity(canonical_spelling=Fact.present("Plain"))
            }
        )
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            # Still needs a closure marker SOMEWHERE for the cheap
            # "anything to renumber at all" check to actually run the walk.
            types=[_record("Other", qualified=_closure("task_group.h", 10, 2))],
            semantic_ir=semantic_ir,
            semantic_ir_conflicts={key: repr("value")},
        )
        renumber_anonymous_closure_identities(snap)
        assert snap.semantic_ir_conflicts == {key: repr("value")}
