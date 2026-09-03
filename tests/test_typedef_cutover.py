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

"""The typedef detector family after its cutover onto ``SemanticIRIndex``
(ADR-063 Phase 6B's first full vertical slice), plus the architecture gate
that keeps it migrated.

The bug *class* under test is "a migrated detector produces different
findings depending on which index backed it". That is checked as an
equivalence over generated typedef map pairs — the same comparison run once
through a real ``SemanticIR`` and once through the legacy adapter must
produce identical findings — against an oracle (the adapter path, which is
the pre-migration behavior) that is not the mechanism under test.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.checker_policy import ChangeKind
from abicheck.compare.typedefs import (
    diff_typedefs,
    is_version_stamped_typedef,
    typedef_index_pair,
)
from abicheck.model import AbiSnapshot
from abicheck.model.fact import Fact
from abicheck.model.identity import (
    EntityKind,
    Namespace,
    entity_id_for_typedef,
)
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_index import SemanticIRIndex
from abicheck.model.semantic_ir_legacy_adapter import legacy_typedef_ir


def _snap(**kw) -> AbiSnapshot:
    base = dict(library="libfoo.so.1", version="1.0.0")
    base.update(kw)
    return AbiSnapshot(**base)


def _never_filtered(name: str, *, exclude_stdlib_namespaces: bool) -> bool:
    return False


def _run(old_index, new_index, **kw):
    return diff_typedefs(
        old_index,
        new_index,
        exclude_stdlib_namespaces=kw.get("excl", False),
        suppress_removed=kw.get("suppress_removed", False),
        is_non_abi_surface_type=kw.get("surface", _never_filtered),
    )


def _adapted(typedefs: dict[str, str], snapshot: AbiSnapshot | None = None):
    return SemanticIRIndex(legacy_typedef_ir(snapshot or _snap(), typedefs))


def _ir_backed(typedefs: dict[str, str]) -> SemanticIRIndex:
    """A *real* ``SemanticIR``, built the way
    ``extract/semantic_normalizer.py`` builds one: a resolved ``EntityId``
    per alias, payload in ``canonical_spelling``."""
    occurrences = {}
    for alias, underlying in typedefs.items():
        *scope, leaf = alias.split("::")
        eid = entity_id_for_typedef(tuple(Namespace(s) for s in scope), leaf)
        occurrences[OccurrenceId(eid)] = CanonicalEntity(
            canonical_spelling=Fact.present(underlying)
        )
    return SemanticIRIndex(SemanticIR(occurrences=occurrences))


def _summary(changes):
    """Findings in *emission order*, deliberately not sorted.

    Nothing between a detector and a report re-sorts findings, so emission
    order is output order -- comparing sorted summaries would let the two
    index backings differ in sequence and still pass. The selector's own
    fidelity gate compares ordered alias sequences for the same reason.
    """
    return [
        (c.kind, c.symbol, c.old_value, c.new_value, c.description) for c in changes
    ]


# -- the equivalence that makes the cutover safe ---------------------------

_alias = st.sampled_from(["A", "B", "ns::A", "ns::B", "ns::inner::C", "D"])
_underlying = st.sampled_from(["int", "long", "char *", "?", "struct S"])
_maps = st.dictionaries(_alias, _underlying, max_size=5)


class TestBackendEquivalence:
    @given(old=_maps, new=_maps, suppress=st.booleans())
    @settings(max_examples=120, deadline=None)
    def test_ir_backed_and_adapted_indexes_produce_identical_findings(
        self, old: dict[str, str], new: dict[str, str], suppress: bool
    ) -> None:
        """The property the whole cutover rests on. Generated over add/
        remove/change/unchanged combinations across five aliases (bare,
        singly and doubly qualified) and five underlying spellings including
        the unresolved ``"?"`` placeholder -- not a single hand-picked pair.
        """
        via_ir = _run(_ir_backed(old), _ir_backed(new), suppress_removed=suppress)
        via_adapter = _run(_adapted(old), _adapted(new), suppress_removed=suppress)
        assert _summary(via_ir) == _summary(via_adapter)

    @given(old=_maps, new=_maps)
    @settings(max_examples=60, deadline=None)
    def test_only_the_ir_path_stamps_a_producer_identity(
        self, old: dict[str, str], new: dict[str, str]
    ) -> None:
        """The one deliberate difference between the two paths, pinned so it
        cannot invert: a real backend identity reaches ``Change.entity_id``;
        the adapter's synthesized one never does, because
        ``resolve_change_identity`` folds that field into an ``entity:``
        alias real stored suppression rules match against."""
        for change in _run(_adapted(old), _adapted(new)):
            assert change.entity_id is None
        for change in _run(_ir_backed(old), _ir_backed(new)):
            assert change.entity_id is not None
            assert change.entity_id.kind is EntityKind.TYPEDEF

    def test_a_reordered_ir_is_refused_rather_than_emitted_out_of_order(
        self,
    ) -> None:
        """The concrete reason the gate compares ordered sequences. An IR
        holding exactly the right aliases in a different order would produce
        exactly the right findings in the wrong sequence -- an unordered
        check would wave that through. The selector falls back to the
        adapter instead, which is the pre-migration order by construction.
        """
        maps = {"A": "int", "B": "int", "C": "int"}
        reordered = {"C": "int", "A": "int", "B": "int"}
        snap = _snap(
            typedefs_qualified=maps,
            semantic_ir=_ir_backed(reordered).ir,
        )
        old_index, _ = typedef_index_pair(
            snap, snap, old_typedefs=maps, new_typedefs=maps
        )
        emitted = [c.symbol for c in _run(old_index, SemanticIRIndex(SemanticIR()))]
        assert emitted == ["A", "B", "C"]


# -- behavior preservation, case by case -----------------------------------


class TestDetectorBehavior:
    def test_removal_change_and_no_op(self) -> None:
        changes = _run(
            _ir_backed({"gone": "int", "moved": "int", "same": "int"}),
            _ir_backed({"moved": "long", "same": "int"}),
        )
        by_kind = {c.kind: c for c in changes}
        assert set(by_kind) == {
            ChangeKind.TYPEDEF_REMOVED,
            ChangeKind.TYPEDEF_BASE_CHANGED,
        }
        assert by_kind[ChangeKind.TYPEDEF_REMOVED].symbol == "gone"
        assert by_kind[ChangeKind.TYPEDEF_BASE_CHANGED].old_value == "int"
        assert by_kind[ChangeKind.TYPEDEF_BASE_CHANGED].new_value == "long"

    def test_symbol_stays_bare_while_the_description_disambiguates(self) -> None:
        """Both spellings matter and for different reasons -- see the
        detector's own docstring."""
        (change,) = _run(_ir_backed({"ns::Alias": "int"}), _ir_backed({}))
        assert change.symbol == "Alias"
        assert "(ns::Alias)" in change.description

    def test_suppress_removed_drops_removals_only(self) -> None:
        changes = _run(
            _ir_backed({"gone": "int", "moved": "int"}),
            _ir_backed({"moved": "long"}),
            suppress_removed=True,
        )
        assert [c.kind for c in changes] == [ChangeKind.TYPEDEF_BASE_CHANGED]

    def test_a_surface_filtered_alias_is_skipped_entirely(self) -> None:
        def only_ns(name: str, *, exclude_stdlib_namespaces: bool) -> bool:
            return name.startswith("ns::")

        changes = _run(
            _ir_backed({"ns::Hidden": "int", "Shown": "int"}),
            _ir_backed({}),
            surface=only_ns,
        )
        assert [c.symbol for c in changes] == ["Shown"]

    def test_version_sentinel_rotation_is_not_a_removal(self) -> None:
        changes = _run(
            _ir_backed({"png_libpng_version_1_6_46": "char *"}),
            _ir_backed({"png_libpng_version_1_6_47": "char *"}),
        )
        assert [c.kind for c in changes] == [ChangeKind.TYPEDEF_VERSION_SENTINEL]

    def test_a_version_shaped_name_with_no_successor_is_a_real_removal(
        self,
    ) -> None:
        changes = _run(
            _ir_backed({"png_libpng_version_1_6_46": "char *"}), _ir_backed({})
        )
        assert [c.kind for c in changes] == [ChangeKind.TYPEDEF_REMOVED]

    def test_the_version_pattern_itself_is_unchanged_by_the_move(self) -> None:
        assert is_version_stamped_typedef("png_libpng_version_1_6_46")
        assert not is_version_stamped_typedef("png_libpng_version_1")
        assert not is_version_stamped_typedef("plain_alias")

    def test_an_unresolved_spelling_compares_as_the_legacy_placeholder(
        self,
    ) -> None:
        """``extract/semantic_normalizer.py`` records an unfollowable chain
        as ``Fact.failed``, where the legacy path carried the literal
        ``"?"``. Unresolved-vs-unresolved must stay "unchanged", and
        unresolved-vs-resolved must stay a base-type change."""
        failed = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(entity_id_for_typedef((), "A")): CanonicalEntity(
                        canonical_spelling=Fact.failed("not resolved")
                    )
                }
            )
        )
        assert _run(failed, failed) == []
        (change,) = _run(failed, _ir_backed({"A": "int"}))
        assert change.kind is ChangeKind.TYPEDEF_BASE_CHANGED
        assert change.old_value == "?"
        assert change.new_value == "int"


# -- end to end, through the real detector entry point ---------------------


class TestThroughCompare:
    def test_a_dwarf_only_pair_still_reports_typedef_findings(self) -> None:
        """The regression the adapter exists to prevent: before it, a
        detector reading only through the index would see nothing at all on
        a snapshot with no ``SemanticIR``, silently losing the family."""
        from abicheck.diff_types import _diff_typedefs

        old = _snap(typedefs={"Alias": "int"}, ast_producer="")
        new = _snap(typedefs={"Alias": "long"}, ast_producer="")
        changes = _diff_typedefs(old, new)
        assert [c.kind for c in changes] == [ChangeKind.TYPEDEF_BASE_CHANGED]

    def test_the_real_entry_point_uses_the_ir_when_it_is_faithful(self) -> None:
        from abicheck.diff_types import _diff_typedefs

        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        old = _snap(
            typedefs_qualified={"ns::Alias": "int"},
            typedef_entity_ids={"ns::Alias": eid},
            semantic_ir=_ir_backed({"ns::Alias": "int"}).ir,
        )
        new = _snap(
            typedefs_qualified={"ns::Alias": "long"},
            typedef_entity_ids={"ns::Alias": eid},
            semantic_ir=_ir_backed({"ns::Alias": "long"}).ir,
        )
        (change,) = _diff_typedefs(old, new)
        assert change.kind is ChangeKind.TYPEDEF_BASE_CHANGED
        # Proof the IR path ran: the identity reached the finding.
        assert change.entity_id == eid

    def test_selector_and_detector_agree_on_a_real_pair(self) -> None:
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        maps = {"ns::Alias": "int"}
        snap = _snap(
            typedefs_qualified=maps,
            typedef_entity_ids={"ns::Alias": eid},
            semantic_ir=_ir_backed(maps).ir,
        )
        old_index, new_index = typedef_index_pair(
            snap, snap, old_typedefs=maps, new_typedefs=maps
        )
        assert _run(old_index, new_index) == []


# -- the architecture gate -------------------------------------------------


class TestSemanticIrCutoverGate:
    def test_the_migrated_module_reads_no_legacy_typedef_collection(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from findings_report import Findings
        from semantic_ir_cutover import check_semantic_ir_cutover

        findings = Findings()
        check_semantic_ir_cutover(findings)
        assert findings.errors == []

    def test_the_gate_actually_fires_on_every_forbidden_read_shape(self) -> None:
        """Executable, not prose: the gate is exercised against real source
        for each spelling an un-migration could take, rather than asserting
        that its own allowlist happens to be empty. A gate that cannot be
        shown to fail proves nothing about the code it guards.
        """
        import ast
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from semantic_ir_cutover import legacy_collection_reads

        forbidden = frozenset({"typedefs", "typedefs_qualified"})
        for source in (
            "x = snap.typedefs",
            "x = old.typedefs_qualified",
            "x = self.snapshot.typedefs",
            'x = getattr(snap, "typedefs")',
            'x = getattr(snap, "typedefs", {})',
            "from builtins import getattr as g\nx = g(snap, 'typedefs')",
            "g = getattr\nx = g(snap, 'typedefs_qualified')",
            # The evasion through an aliased `builtins` module rather than
            # an aliased `getattr` name -- `func` is an `ast.Attribute`
            # (`b.getattr`), not an `ast.Name`, so it needs its own check.
            "import builtins as b\nx = b.getattr(snap, 'typedefs')",
            "import builtins\nx = builtins.getattr(snap, 'typedefs_qualified')",
        ):
            found = legacy_collection_reads(ast.parse(source), forbidden)
            assert found, f"gate missed: {source!r}"

    def test_the_gate_does_not_fire_on_a_local_or_a_keyword(self) -> None:
        """The complement: a gate that flags everything is as useless as one
        that flags nothing. A local variable and the adapter's own inbound
        parameter share the name and are not reads of the field."""
        import ast
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from semantic_ir_cutover import legacy_collection_reads

        forbidden = frozenset({"typedefs", "typedefs_qualified"})
        for source in (
            "typedefs = {}\nx = typedefs",
            "build(snapshot, typedefs=alias_map)",
            'x = getattr(snap, "something_else")',
            # A `.getattr(...)` method call on something that is not the
            # `builtins` module must not be mistaken for the builtin.
            'x = some_object.getattr(snap, "typedefs")',
        ):
            assert legacy_collection_reads(ast.parse(source), forbidden) == []
