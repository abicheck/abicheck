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

"""``merge_snapshots()``'s ``semantic_ir`` reconciliation (ADR-063 Phase 6).

The merge *algorithm* is tested at primitive level in
``test_semantic_ir_merge.py``; this file covers the wiring, i.e. that the
hybrid path actually reconciles the field rather than carrying the castxml
sub-snapshot's IR through unchanged beside legacy fields that did get
reconciled. Its own file rather than a class inside ``test_dumper_hybrid.py``,
which is already at its ``architecture/debt.yaml`` no-growth baseline.
"""

from __future__ import annotations

from abicheck.dumper_hybrid import merge_snapshots
from abicheck.model import AbiSnapshot, Function
from abicheck.model.fact import Fact
from abicheck.model.identity import entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import (
    CanonicalEntity,
    SemanticIR,
    semantic_ir_conflict_key,
)

FOO = entity_id_for_type((), "Foo")
BAR = entity_id_for_type((), "Bar")


def _snap(ir: SemanticIR | None, producer: str, **kwargs: object) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        from_headers=True,
        ast_producer=producer,
        semantic_ir=ir,
        **kwargs,  # type: ignore[arg-type]
    )


def _entity(
    spelling: str, *, template: tuple[str, ...] | None = None
) -> CanonicalEntity:
    return CanonicalEntity(
        canonical_spelling=Fact.present(spelling),
        template_arguments=(
            Fact.present(template) if template is not None else Fact.not_collected()
        ),
    )


class TestMergeSnapshotsReconcilesSemanticIr:
    def test_clang_only_entity_reaches_the_merged_ir(self) -> None:
        """The exact drift this step exists to close: a clang-only entity is
        already unioned into the merged ``functions``/``types``, so leaving it
        out of ``semantic_ir`` would make one snapshot's two representations
        disagree."""
        castxml_occ = OccurrenceId(FOO)
        clang_occ = OccurrenceId(BAR)
        merged = merge_snapshots(
            _snap(SemanticIR({castxml_occ: _entity("Foo")}), "castxml"),
            _snap(SemanticIR({clang_occ: _entity("Bar")}), "clang"),
        )
        assert merged.semantic_ir is not None
        assert set(merged.semantic_ir.occurrences) == {castxml_occ, clang_occ}

    def test_clang_backfills_an_unresolved_castxml_fact(self) -> None:
        occ = OccurrenceId(FOO)
        merged = merge_snapshots(
            _snap(SemanticIR({occ: _entity("Foo")}), "castxml"),
            _snap(SemanticIR({occ: _entity("Foo", template=("int",))}), "clang"),
        )
        assert merged.semantic_ir is not None
        assert merged.semantic_ir.occurrences[occ].template_arguments.value == ("int",)

    def test_a_two_sided_disagreement_is_recorded_not_dropped(self) -> None:
        occ = OccurrenceId(FOO)
        merged = merge_snapshots(
            _snap(SemanticIR({occ: _entity("castxml::Foo")}), "castxml"),
            _snap(SemanticIR({occ: _entity("clang::Foo")}), "clang"),
        )
        assert merged.semantic_ir is not None
        assert merged.semantic_ir.occurrences[occ].canonical_spelling.value == (
            "castxml::Foo"
        )
        assert merged.semantic_ir_conflicts == {
            semantic_ir_conflict_key(occ, "canonical_spelling"): repr("clang::Foo")
        }

    def test_no_ir_on_either_side_leaves_the_field_none(self) -> None:
        merged = merge_snapshots(_snap(None, "castxml"), _snap(None, "clang"))
        assert merged.semantic_ir is None
        assert merged.semantic_ir_conflicts == {}

    def test_one_sided_ir_survives(self) -> None:
        occ = OccurrenceId(FOO)
        merged = merge_snapshots(
            _snap(None, "castxml"), _snap(SemanticIR({occ: _entity("Foo")}), "clang")
        )
        assert merged.semantic_ir is not None
        assert set(merged.semantic_ir.occurrences) == {occ}

    def test_pre_existing_conflicts_are_preserved(self) -> None:
        """A castxml leg that is itself a merge result (a nested hybrid dump)
        carries conflicts of its own; this merge adds to them rather than
        replacing the map."""
        occ = OccurrenceId(FOO)
        merged = merge_snapshots(
            _snap(
                SemanticIR({occ: _entity("Foo")}),
                "castxml",
                semantic_ir_conflicts={"earlier": "'value'"},
            ),
            _snap(SemanticIR({occ: _entity("Foo", template=("int",))}), "clang"),
        )
        assert merged.semantic_ir_conflicts == {"earlier": "'value'"}

    def test_a_one_sided_fallback_returns_castxml_untouched(self) -> None:
        """The existing early return (one side never got header evidence) is
        not weakened: the castxml snapshot, IR included, comes back as-is."""
        castxml = _snap(SemanticIR({OccurrenceId(FOO): _entity("Foo")}), "castxml")
        clang = AbiSnapshot(
            library="libtest.so.1",
            version="1.0",
            from_headers=False,
            ast_producer="clang",
            semantic_ir=SemanticIR({OccurrenceId(BAR): _entity("Bar")}),
        )
        assert merge_snapshots(castxml, clang) is castxml


class TestLegacyFieldsAreUnaffected:
    def test_merging_snapshots_without_an_ir_is_unchanged(self) -> None:
        """Parity: every snapshot produced today carries no ``semantic_ir``
        (no backend is narrowed onto the normalizer yet), so the hybrid
        merge's existing output must be bit-identical."""
        func = Function(name="f", mangled="_Z1fv", return_type="void", params=[])
        merged = merge_snapshots(
            _snap(None, "castxml", functions=[func]),
            _snap(None, "clang", functions=[func]),
        )
        assert [f.mangled for f in merged.functions] == ["_Z1fv"]
        assert merged.semantic_ir is None
