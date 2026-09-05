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
    Anonymous,
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
    index backings differ in sequence and still pass.
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

    def test_the_real_ir_own_order_is_emitted_directly(self) -> None:
        """ADR-063 Track T3: ``typedef_index_pair`` no longer adjudicates
        against a legacy alias map's order -- since both sides carry a real
        ``SemanticIR`` here, it is used directly, in whatever order it
        itself carries, not the legacy map's order. (Before T3, an IR
        holding the right aliases in a *different* order than the legacy
        map fell back to the adapter, which emits in the legacy map's
        order; that fallback no longer exists once both sides have a real
        IR.)
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
        assert emitted == ["C", "A", "B"]


# -- behavior preservation, case by case -----------------------------------


class TestDetectorBehavior:
    def test_a_removed_anonymous_namespace_typedef_is_still_detected(self) -> None:
        """The exact scenario Codex review (PR #1078, fourth round) named:
        a typedef declared in an anonymous namespace, real on the old side
        and genuinely removed on the new side. Before the leaf-name
        fallback, this would have been silently invisible to comparison
        (``_aliases`` skipped it outright); the strict renderer's ``None``
        is not proof the typedef doesn't exist."""
        gone = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(gone): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    )
                }
            )
        )
        (change,) = _run(old_index, SemanticIRIndex(SemanticIR()))
        assert change.kind is ChangeKind.TYPEDEF_REMOVED
        assert change.symbol == "Alias"

    def test_a_whole_colliding_group_disappearing_reports_every_removal(
        self,
    ) -> None:
        """Codex review, PR #1078, twelfth round: two anonymous-scoped
        typedefs collide on ``Alias`` and the whole group vanishes on the
        new side -- not merely shrinks. Both are independent, real
        removals, not just `old_ids[0]`."""
        first = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        second = entity_id_for_typedef((Anonymous("namespace", 1),), "Alias")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                    OccurrenceId(second): CanonicalEntity(
                        canonical_spelling=Fact.present("long")
                    ),
                }
            )
        )
        changes = _run(old_index, SemanticIRIndex(SemanticIR()))
        assert len(changes) == 2
        assert all(c.kind is ChangeKind.TYPEDEF_REMOVED for c in changes)
        assert {c.old_value for c in changes} == {"int", "long"}

    def test_a_value_change_on_one_of_two_colliding_anonymous_typedefs_is_still_caught(
        self,
    ) -> None:
        """Codex review, PR #1078, sixth round: two typedefs declared in two
        distinct anonymous namespaces both render to the bare leaf name
        ``Alias`` (``render_display_name_or_leaf`` cannot distinguish them --
        neither carries a named ancestor). Picking an arbitrary
        representative per side used to mean a real value change on
        whichever occurrence didn't become the representative was silently
        read as "unchanged" whenever the *other* occurrence's value happened
        to match across sides. Here the first occurrence's value (``int``)
        is unchanged and the second's (``long`` -> ``char``) is not -- the
        whole point of comparing by value multiset rather than by picking
        one entity to stand in for the whole group."""
        first_old = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        second_old = entity_id_for_typedef((Anonymous("namespace", 1),), "Alias")
        first_new = entity_id_for_typedef((Anonymous("namespace", 2),), "Alias")
        second_new = entity_id_for_typedef((Anonymous("namespace", 3),), "Alias")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first_old): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                    OccurrenceId(second_old): CanonicalEntity(
                        canonical_spelling=Fact.present("long")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first_new): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                    OccurrenceId(second_new): CanonicalEntity(
                        canonical_spelling=Fact.present("char")
                    ),
                }
            )
        )
        (change,) = _run(old_index, new_index)
        assert change.kind is ChangeKind.TYPEDEF_BASE_CHANGED
        assert change.symbol == "Alias"
        assert change.old_value == "long"
        assert change.new_value == "char"

    def test_a_stable_identitys_own_base_change_is_not_masked_by_a_new_arrival(
        self,
    ) -> None:
        """Codex review, PR #1078, thirteenth round: a stable, real-backend
        identity ``Alias`` changes from ``int`` to ``long`` while a
        *different*, newly-added anonymous-scope ``Alias`` arrives as
        ``int``. Value-only multiset matching cancels the stable entity's
        old ``int`` against the new entity's ``int``, reporting a clean
        comparison (a pure addition, untracked) -- silently masking the
        stable entity's own real, breaking base-type change. Matching by
        shared ``EntityId`` first must catch it directly."""
        stable = entity_id_for_typedef((Namespace("ns"),), "Alias")
        added = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    )
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable): CanonicalEntity(
                        canonical_spelling=Fact.present("long")
                    ),
                    OccurrenceId(added): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                }
            )
        )
        (change,) = _run(old_index, new_index)
        assert change.kind is ChangeKind.TYPEDEF_BASE_CHANGED
        assert change.old_value == "int"
        assert change.new_value == "long"
        assert change.entity_id == stable

    def test_shifted_anonymous_ordinals_are_not_trusted_as_stable_identity(
        self,
    ) -> None:
        """Codex review, PR #1078, fourteenth round: the typedef-family
        sibling of the identical constant-family finding. Two
        anonymous-scoped ``Alias`` declarations (``int``, ``long``) collide
        on the old side; a new, earlier-declared sibling shifts both of
        their ordinals by one on the new side, landing a genuinely new
        ``Alias=char`` at the vacated ordinal 0. Trusting a raw
        `EntityId` intersection would pair old ordinal 0 (``int``) against
        new ordinal 0 (``char``, actually the new declaration) and old
        ordinal 1 (``long``) against new ordinal 1 (``int``, actually the
        original ordinal-0 declaration merely renumbered) -- two fabricated
        ``TYPEDEF_BASE_CHANGED`` findings for declarations that never
        changed. The only provable difference is the addition of
        ``Alias=char`` -- untracked for typedefs, so no finding at all."""
        old_first = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        old_second = entity_id_for_typedef((Anonymous("namespace", 1),), "Alias")
        new_inserted = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        new_first_shifted = entity_id_for_typedef((Anonymous("namespace", 1),), "Alias")
        new_second_shifted = entity_id_for_typedef(
            (Anonymous("namespace", 2),), "Alias"
        )
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(old_first): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                    OccurrenceId(old_second): CanonicalEntity(
                        canonical_spelling=Fact.present("long")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(new_inserted): CanonicalEntity(
                        canonical_spelling=Fact.present("char")
                    ),
                    OccurrenceId(new_first_shifted): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                    OccurrenceId(new_second_shifted): CanonicalEntity(
                        canonical_spelling=Fact.present("long")
                    ),
                }
            )
        )
        assert _run(old_index, new_index) == []

    def test_a_duplicate_value_added_to_a_colliding_group_is_not_a_base_change(
        self,
    ) -> None:
        """Codex review, PR #1078, tenth round: the new side adds a second
        anonymous-namespace ``Alias=int`` alongside an existing
        ``Alias=int`` -- the group grows from one occurrence to two, both
        sharing the identical underlying type. A sorted-list
        multiset-equality check cannot tell this apart from a genuine value
        substitution that happens to leave a coincidentally-equal
        representative pair, and used to report ``TYPEDEF_BASE_CHANGED``
        (breaking) with ``old_value == new_value == "int"`` for what is a
        purely compatible, and for typedefs entirely *untracked*, addition
        -- typedef additions carry no ``ChangeKind`` at all."""
        old_id = entity_id_for_typedef((), "Alias")
        new_first = entity_id_for_typedef((), "Alias")
        new_second = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(old_id): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    )
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(new_first): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                    OccurrenceId(new_second): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                }
            )
        )
        assert _run(old_index, new_index) == []

    def test_a_duplicate_value_removed_from_a_colliding_group_is_a_removal(
        self,
    ) -> None:
        """The mirror image: the group shrinks from two occurrences (both
        ``int``) to one, and must be classified ``TYPEDEF_REMOVED``, not
        ``TYPEDEF_BASE_CHANGED``."""
        old_first = entity_id_for_typedef((), "Alias")
        old_second = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        new_id = entity_id_for_typedef((), "Alias")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(old_first): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                    OccurrenceId(old_second): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(new_id): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    )
                }
            )
        )
        (change,) = _run(old_index, new_index)
        assert change.kind is ChangeKind.TYPEDEF_REMOVED
        assert change.symbol == "Alias"
        assert change.old_value == "int"

    def test_three_colliding_occurrences_shrinking_to_one_reports_two_removals(
        self,
    ) -> None:
        """Codex review, PR #1078, eleventh round (typedef-family sibling
        of the identical constant-family finding): three anonymous-scoped
        occurrences all sharing ``Alias=int`` on the old side, only one on
        the new side -- the loss of *two* occurrences, not one. Converting
        the multiset difference to a ``set`` (an earlier version of this
        fix) collapsed the repeated value to a single entry, silently
        dropping the second removal."""
        first_old = entity_id_for_typedef((), "Alias")
        second_old = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        third_old = entity_id_for_typedef((Anonymous("namespace", 1),), "Alias")
        new_id = entity_id_for_typedef((), "Alias")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first_old): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                    OccurrenceId(second_old): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                    OccurrenceId(third_old): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(new_id): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    )
                }
            )
        )
        changes = _run(old_index, new_index)
        assert len(changes) == 2
        assert all(c.kind is ChangeKind.TYPEDEF_REMOVED for c in changes)
        assert all(c.old_value == "int" for c in changes)

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
            # The evasion through an alias *assigned from* the module
            # attribute, then called bare -- `g = b.getattr; g(snap, ...)`.
            # The call itself is a bare `Name`, and the assignment's own
            # value is an `ast.Attribute`, so neither the plain-alias nor
            # the module-alias call check alone recognizes it (CodeRabbit
            # review on PR #1041).
            "import builtins as b\ng = b.getattr\nx = g(snap, 'typedefs')",
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


class TestPrivateHelpers:
    """Direct coverage for two internal helpers: real call sites only ever
    reach ``_has_version_family_successor`` after the same regex has
    already matched, so its other branches need direct tests. ``_aliases``'
    unrenderable-identity skip is, since ADR-063 Track T3, the real
    load-bearing mechanism on the ``SemanticIR`` path (not a floor behind a
    gate that used to fall back first) -- see its own docstring."""

    def test_has_version_family_successor_false_when_name_does_not_match(
        self,
    ) -> None:
        from abicheck.compare.typedefs import _has_version_family_successor

        assert not _has_version_family_successor("plain_alias", frozenset())

    def test_has_version_family_successor_false_on_empty_prefix(self) -> None:
        """A sentinel-shaped name with no family prefix (starts directly
        with ``_version_``) must not match itself as its own family."""
        from abicheck.compare.typedefs import _has_version_family_successor

        assert not _has_version_family_successor(
            "_version_1_0_0", frozenset({"_version_2_0_0"})
        )

    def test_aliases_falls_back_to_the_bare_leaf_name_for_an_unrenderable_identity(
        self,
    ) -> None:
        """An ``Anonymous``-scoped typedef identity still contributes an
        entry, keyed by its bare leaf name -- Codex review, PR #1078,
        fourth round: the legacy adapter's own synthetic identity for the
        identical declaration always renders (an empty scope), so skipping
        it here would make an anonymous-namespace typedef disappear the
        moment a real ``SemanticIR`` is used directly, visible on the
        legacy-adapted path only by accident."""
        from abicheck.compare.typedefs import _aliases

        anon = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(anon): CanonicalEntity(
                    canonical_spelling=Fact.present("int")
                )
            }
        )
        assert _aliases(SemanticIRIndex(ir)) == {"Alias": [OccurrenceId(anon)]}


class TestOdrDuplicateOccurrencesSurviveReduction:
    """Regression coverage for Codex review, PR #1078, fifteenth round:
    ``_aliases``/``_underlying`` used to read through ``SemanticIRIndex``'s
    *reduced*, one-entry-per-``EntityId`` view (``entities_of_kind()``/
    ``.fact()``), which silently collapsed two genuine occurrences sharing
    one identity -- distinguished only by ``OccurrenceId.disambiguator`` --
    onto a single "most facts present" winner. A real value change on
    whichever occurrence did not win that reduction was then invisible to
    ``diff_typedefs`` even though ``SemanticIR.occurrences`` never actually
    merged the two: it is keyed by ``OccurrenceId``, not bare ``EntityId``,
    precisely to keep this pair distinct.
    """

    def _ir_with_two_occurrences(
        self, eid, *, value_a: str, value_b: str
    ) -> SemanticIR:
        return SemanticIR(
            occurrences={
                OccurrenceId(eid, "tu-a"): CanonicalEntity(
                    canonical_spelling=Fact.present(value_a)
                ),
                OccurrenceId(eid, "tu-b"): CanonicalEntity(
                    canonical_spelling=Fact.present(value_b)
                ),
            }
        )

    def test_aliases_keeps_both_odr_duplicate_occurrences_distinct(self) -> None:
        from abicheck.compare.typedefs import _aliases

        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        ir = self._ir_with_two_occurrences(eid, value_a="int", value_b="int")
        grouped = _aliases(SemanticIRIndex(ir))
        assert set(grouped) == {"ns::Alias"}
        assert set(grouped["ns::Alias"]) == {
            OccurrenceId(eid, "tu-a"),
            OccurrenceId(eid, "tu-b"),
        }

    def test_a_value_change_on_one_odr_duplicate_occurrence_is_detected(
        self,
    ) -> None:
        """Two occurrences share one ``EntityId`` (an ODR-duplicate pair,
        e.g. two internal-linkage typedefs in different TUs). Only one of
        them changes value between snapshots -- the reduced-view bug would
        have let the ``canonical_entities()`` "most facts present" tie-break
        pick the same, *unchanged* occurrence as the representative on both
        sides, reporting no change at all despite a real base-type change on
        the sibling occurrence.
        """
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        old_index = SemanticIRIndex(
            self._ir_with_two_occurrences(eid, value_a="int", value_b="int")
        )
        new_index = SemanticIRIndex(
            self._ir_with_two_occurrences(eid, value_a="int", value_b="long")
        )
        changes = _run(old_index, new_index)
        assert len(changes) == 1
        change = changes[0]
        assert change.kind is ChangeKind.TYPEDEF_BASE_CHANGED
        assert change.symbol == "Alias"
        assert change.old_value == "int"
        assert change.new_value == "long"


class TestWholeGroupRemovalSurvivesPostProcessingDedup:
    """Regression coverage for Codex review, PR #1078, sixteenth round:
    ``diff_typedefs`` emits one ``TYPEDEF_REMOVED`` per contributing entity
    when a whole colliding group vanishes (twelfth round), but the public
    ``checker.compare()`` pipeline's own post-processing used to run
    ``diff_filtering._dedup_exact`` keyed only on ``(kind, description)`` --
    identical text for every entity in a colliding group by construction --
    silently collapsing these independently-provable removals back down to
    one before a caller ever saw them. This is checked at the seam between
    the two modules directly, not only within ``diff_typedefs`` in
    isolation, since a test calling ``diff_typedefs`` alone cannot see this
    downstream loss.
    """

    def test_two_colliding_removals_both_survive_dedup_exact(self) -> None:
        from abicheck.diff_filtering import _dedup_exact

        first = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        second = entity_id_for_typedef((Anonymous("namespace", 1),), "Alias")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                    OccurrenceId(second): CanonicalEntity(
                        canonical_spelling=Fact.present("int")
                    ),
                }
            )
        )
        changes = _run(old_index, SemanticIRIndex(SemanticIR()))
        assert len(changes) == 2
        # Same value on both sides of the collision -- the case the old
        # (kind, description) key alone could not distinguish even with
        # this PR's own `entity_id` improvement, since these are also the
        # findings' own identical `symbol`/`old_value`; only `entity_id`
        # tells them apart.
        assert {c.old_value for c in changes} == {"int"}
        deduped = _dedup_exact(changes)
        assert len(deduped) == 2
