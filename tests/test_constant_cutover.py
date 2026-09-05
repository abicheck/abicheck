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

"""The constant detector family after its cutover onto ``SemanticIRIndex``
(ADR-063 Phase 6B's second detector cohort), plus the architecture gate that
keeps it migrated.

Mirrors ``tests/test_typedef_cutover.py`` for cohort 1: the bug *class*
under test is "a migrated detector produces different findings depending on
which index backed it", checked as an equivalence over generated constant
map pairs -- the same comparison run once through a real ``SemanticIR`` and
once through the legacy adapter must produce identical findings, against an
oracle (the adapter path, the pre-migration behavior) that is not the
mechanism under test.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.checker_policy import ChangeKind
from abicheck.compare.constants import constant_index_pair, diff_constants
from abicheck.model import AbiSnapshot
from abicheck.model.fact import Fact
from abicheck.model.identity import (
    Anonymous,
    EntityKind,
    Namespace,
    entity_id_for_constant,
)
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_index import SemanticIRIndex
from abicheck.model.semantic_ir_legacy_adapter import legacy_constant_ir


def _snap(**kw) -> AbiSnapshot:
    base = dict(
        library="libfoo.so.1",
        version="1.0.0",
        from_headers=True,
        from_headers_inferred=False,
    )
    base.update(kw)
    return AbiSnapshot(**base)


def _never_unreliable(old_value: str, new_value: str) -> bool:
    return False


def _run(old_index, new_index, **kw):
    return diff_constants(
        old_index,
        new_index,
        is_fingerprint_comparison_unreliable=kw.get("unreliable", _never_unreliable),
    )


def _adapted(constants: dict[str, str], snapshot: AbiSnapshot | None = None):
    return SemanticIRIndex(legacy_constant_ir(snapshot or _snap(), constants))


def _ir_backed(constants: dict[str, str]) -> SemanticIRIndex:
    """A *real* ``SemanticIR``, built the way ``extract/
    semantic_normalizer.py`` builds one: a resolved ``EntityId`` per
    qualified name, payload in ``canonical_spelling``."""
    occurrences = {}
    for name, value in constants.items():
        *scope, leaf = name.split("::")
        eid = entity_id_for_constant(tuple(Namespace(s) for s in scope), leaf)
        occurrences[OccurrenceId(eid)] = CanonicalEntity(
            canonical_spelling=Fact.present(value)
        )
    return SemanticIRIndex(SemanticIR(occurrences=occurrences))


def _summary(changes):
    """Findings in *emission order*, deliberately not sorted -- see
    ``test_typedef_cutover.py``'s identical helper for why."""
    return [
        (c.kind, c.symbol, c.old_value, c.new_value, c.description) for c in changes
    ]


# -- the equivalence that makes the cutover safe ---------------------------

_name = st.sampled_from(["A", "B", "ns::A", "ns::B", "ns::inner::C", "D"])
_value = st.sampled_from(["42", "0x1", '"hello"', "3.14", "-1"])
_maps = st.dictionaries(_name, _value, max_size=5)


class TestBackendEquivalence:
    @given(old=_maps, new=_maps)
    @settings(max_examples=120, deadline=None)
    def test_ir_backed_and_adapted_indexes_produce_identical_findings(
        self, old: dict[str, str], new: dict[str, str]
    ) -> None:
        """The property the whole cutover rests on. Generated over add/
        remove/change/unchanged combinations across five names (bare,
        singly and doubly qualified) and five value spellings."""
        via_ir = _run(_ir_backed(old), _ir_backed(new))
        via_adapter = _run(_adapted(old), _adapted(new))
        assert _summary(via_ir) == _summary(via_adapter)

    @given(old=_maps, new=_maps)
    @settings(max_examples=60, deadline=None)
    def test_only_the_ir_path_stamps_a_producer_identity(
        self, old: dict[str, str], new: dict[str, str]
    ) -> None:
        """The one deliberate difference between the two paths, pinned so it
        cannot invert: a real backend identity reaches ``Change.entity_id``;
        the adapter's synthesized one never does."""
        for change in _run(_adapted(old), _adapted(new)):
            assert change.entity_id is None
        for change in _run(_ir_backed(old), _ir_backed(new)):
            assert change.entity_id is not None
            assert change.entity_id.kind is EntityKind.CONSTANT

    def test_the_real_ir_own_order_is_emitted_directly(self) -> None:
        """ADR-063 Track T3 -- see ``test_typedef_cutover.py``'s identical
        test for the full reasoning: no more comparison-time adjudication
        against a legacy map's order once both sides carry a real IR."""
        maps = {"A": "1", "B": "1", "C": "1"}
        reordered = {"C": "1", "A": "1", "B": "1"}
        snap = _snap(constants=maps, semantic_ir=_ir_backed(reordered).ir)
        old_index, _ = constant_index_pair(
            snap, snap, old_constants=maps, new_constants=maps
        )
        emitted = [c.symbol for c in _run(old_index, SemanticIRIndex(SemanticIR()))]
        assert emitted == ["C", "A", "B"]


# -- behavior preservation, case by case -----------------------------------


class TestDetectorBehavior:
    def test_removal_change_and_no_op(self) -> None:
        changes = _run(
            _ir_backed({"gone": "1", "moved": "1", "same": "1"}),
            _ir_backed({"moved": "2", "same": "1"}),
        )
        by_kind = {c.kind: c for c in changes}
        assert set(by_kind) == {
            ChangeKind.CONSTANT_REMOVED,
            ChangeKind.CONSTANT_CHANGED,
        }
        assert by_kind[ChangeKind.CONSTANT_REMOVED].symbol == "gone"
        assert by_kind[ChangeKind.CONSTANT_CHANGED].old_value == "1"
        assert by_kind[ChangeKind.CONSTANT_CHANGED].new_value == "2"

    def test_addition_is_reported(self) -> None:
        (change,) = _run(_ir_backed({}), _ir_backed({"new_const": "1"}))
        assert change.kind is ChangeKind.CONSTANT_ADDED
        assert change.symbol == "new_const"
        assert change.new_value == "1"

    def test_an_unreliable_fingerprint_comparison_is_not_a_reported_change(
        self,
    ) -> None:
        def always_unreliable(old_value: str, new_value: str) -> bool:
            return True

        changes = _run(
            _ir_backed({"x": "expr:aaaaaaaaaaaaaaaa"}),
            _ir_backed({"x": "expr:bbbbbbbbbbbbbbbb"}),
            unreliable=always_unreliable,
        )
        assert changes == []

    def test_unsupported_fact_yields_no_comparable_value(self) -> None:
        """A ``Fact.unsupported()`` occurrence (the clang compound-
        initializer-fingerprint/bool-literal case, see ``extract/
        semantic_normalizer.py``) carries no value text -- defensively
        skipped rather than compared against ``None``. Unreachable from the
        real ``constant_index_pair`` gate in practice (see that function's
        own docstring), exercised directly here as the defensive floor."""
        unsupported = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(entity_id_for_constant((), "X")): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    )
                }
            )
        )
        assert _run(unsupported, unsupported) == []
        assert _run(unsupported, _ir_backed({"X": "1"})) == []
        assert _run(_ir_backed({"X": "1"}), unsupported) == []

    def test_a_newly_added_unsupported_fact_is_still_reported_as_an_addition(
        self,
    ) -> None:
        """A membership change is real regardless of whether the constant's
        own value is comparable (Codex review, PR #1078): a constant that
        only exists on the *new* side and carries an unsupported fact is
        still a genuine addition -- it must not be silently dropped just
        because ``new_value`` cannot be rendered. Distinct code path from
        ``test_unsupported_fact_yields_no_comparable_value``'s cases, none
        of which exercise a name absent from ``old_values`` entirely."""
        unsupported = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(entity_id_for_constant((), "X")): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    )
                }
            )
        )
        (change,) = _run(_ir_backed({}), unsupported)
        assert change.kind is ChangeKind.CONSTANT_ADDED
        assert change.symbol == "X"
        assert change.new_value is None

    def test_a_removed_unsupported_fact_is_still_reported_as_a_removal(
        self,
    ) -> None:
        """The removal-side mirror of the addition test above: a constant
        that only exists on the *old* side and carries an unsupported fact
        is still a genuine removal."""
        unsupported = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(entity_id_for_constant((), "X")): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    )
                }
            )
        )
        (change,) = _run(unsupported, _ir_backed({}))
        assert change.kind is ChangeKind.CONSTANT_REMOVED
        assert change.symbol == "X"
        assert change.old_value is None

    def test_an_unrenderable_scope_segment_is_skipped_by_every_projection(
        self,
    ) -> None:
        """An entity whose scope contains an ``Anonymous`` segment has no
        faithful flat spelling (``render_display_name`` returns ``None``,
        see ``semantic_ir_legacy_adapter.py``'s own docstring) -- exercised
        directly against ``_values`` (via ``diff_constants``) and against
        ``constant_index_pair`` itself, since Track T3 made ``SemanticIR``
        the sole comparison-time source: an anonymous-scoped entity is still
        present in the raw index either way, it simply renders no name."""
        unrenderable_id = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        unrenderable = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(unrenderable_id): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    )
                }
            )
        )
        assert _run(unrenderable, unrenderable) == []
        (change,) = _run(unrenderable, _ir_backed({"X": "1"}))
        assert change.kind is ChangeKind.CONSTANT_ADDED

        old = _snap(constants={}, semantic_ir=unrenderable.ir)
        new = _snap(constants={}, semantic_ir=unrenderable.ir)
        old_index, new_index = constant_index_pair(
            old, new, old_constants={}, new_constants={}
        )
        # Both sides carry a real SemanticIR, so it is used directly --
        # the anonymous entity is still present in the raw index (it is
        # not projected away here), it simply has no rendered name for a
        # detector's own `_values` projection to key it under.
        assert isinstance(old_index, SemanticIRIndex)
        assert isinstance(new_index, SemanticIRIndex)
        assert unrenderable_id in old_index.entities_of_kind(EntityKind.CONSTANT)


# -- end to end, through the real detector entry point ---------------------


class TestThroughCompare:
    def test_a_dwarf_only_pair_reports_nothing_rather_than_manufacturing_removals(
        self,
    ) -> None:
        """Constants are header-tier only: a DWARF-only pair has no
        confirmed header evidence, so the header-aware gate short-circuits
        before either index is ever built -- unlike typedefs, which have no
        such gate and instead rely on the adapter."""
        from abicheck.diff_symbols import _diff_constants

        old = _snap(constants={"X": "1"}, from_headers=False)
        new = _snap(constants={"X": "2"}, from_headers=False)
        assert _diff_constants(old, new) == []

    def test_the_real_entry_point_uses_the_ir_when_it_is_faithful(self) -> None:
        from abicheck.diff_symbols import _diff_constants

        eid = entity_id_for_constant((Namespace("ns"),), "X")
        old = _snap(
            constants={"ns::X": "1"},
            constant_entity_ids={"ns::X": eid},
            semantic_ir=_ir_backed({"ns::X": "1"}).ir,
        )
        new = _snap(
            constants={"ns::X": "2"},
            constant_entity_ids={"ns::X": eid},
            semantic_ir=_ir_backed({"ns::X": "2"}).ir,
        )
        (change,) = _diff_constants(old, new)
        assert change.kind is ChangeKind.CONSTANT_CHANGED
        # Proof the IR path ran: the identity reached the finding.
        assert change.entity_id == eid

    def test_selector_and_detector_agree_on_a_real_pair(self) -> None:
        eid = entity_id_for_constant((Namespace("ns"),), "X")
        maps = {"ns::X": "1"}
        snap = _snap(
            constants=maps,
            constant_entity_ids={"ns::X": eid},
            semantic_ir=_ir_backed(maps).ir,
        )
        old_index, new_index = constant_index_pair(
            snap, snap, old_constants=maps, new_constants=maps
        )
        assert _run(old_index, new_index) == []


# -- the architecture gate -------------------------------------------------


class TestSemanticIrCutoverGate:
    def test_the_migrated_module_reads_no_legacy_constant_collection(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from findings_report import Findings
        from semantic_ir_cutover import check_semantic_ir_cutover

        findings = Findings()
        check_semantic_ir_cutover(findings)
        assert findings.errors == []

    def test_the_gate_actually_fires_on_every_forbidden_read_shape(self) -> None:
        """Executable, not prose -- see ``test_typedef_cutover.py``'s
        identical test for why."""
        import ast
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from semantic_ir_cutover import legacy_collection_reads

        forbidden = frozenset({"constants", "constant_entity_ids"})
        for source in (
            "x = snap.constants",
            "x = old.constant_entity_ids",
            "x = self.snapshot.constants",
            'x = getattr(snap, "constants")',
            'x = getattr(snap, "constant_entity_ids", {})',
            "from builtins import getattr as g\nx = g(snap, 'constants')",
            "g = getattr\nx = g(snap, 'constant_entity_ids')",
            "import builtins as b\nx = b.getattr(snap, 'constants')",
            "import builtins\nx = builtins.getattr(snap, 'constant_entity_ids')",
            "import builtins as b\ng = b.getattr\nx = g(snap, 'constants')",
        ):
            found = legacy_collection_reads(ast.parse(source), forbidden)
            assert found, f"gate missed: {source!r}"

    def test_the_gate_does_not_fire_on_a_local_or_a_keyword(self) -> None:
        import ast
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from semantic_ir_cutover import legacy_collection_reads

        forbidden = frozenset({"constants", "constant_entity_ids"})
        for source in (
            "constants = {}\nx = constants",
            "build(snapshot, constants=value_map)",
            'x = getattr(snap, "something_else")',
            'x = some_object.getattr(snap, "constants")',
        ):
            assert legacy_collection_reads(ast.parse(source), forbidden) == []
