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

"""Each finding's display dimension must match the detector that emits it.

Split out of ``test_change_catalog_dimensions.py`` (at the architecture
gate's 1200-line test-file cap) rather than trimmed. These classes share one
subject distinct from that file's: not "is every kind classified" but "does
the classification agree with the *producer*" -- the data-only symbol
detectors, the per-function value-ABI loop, the shared type-slot iterator
that yields both function-owned and record-owned slots, an overlay kind that
inherits its entity from whatever triggered it, and JUnit's classname
grouping, which kept a fourth name-prefix taxonomy after every other
projection moved to the catalog.

Every one of these came from reading the emitting code, not the kind's name
-- which is the whole point of the catalog dimensions (plan slice 7o).
"""

from __future__ import annotations

from abicheck.change_registry import REGISTRY
from abicheck.reporter_markdown import ShowOnlyFilter


class TestASymbolTableFactIsNotClaimedAsAFunction:
    """`consumer_required_symbol_removed` rests on a consumer binary's
    dynamic-symbol table, and that evidence carries no function/variable
    discriminator: `AppRequirements.undefined_symbols` is a bare `set[str]`,
    and the PE/Mach-O collectors retain no symbol type either. Declaring
    FUNCTION therefore hid a required *data* symbol from
    `--view show=variables` and serialized an entity the evidence cannot
    support (Codex review, PR #1284).

    Stated as a rule over the evidence rather than as one kind's expected
    value: any finding whose only operand is an undiscriminated symbol name
    belongs to the BINARY dimension, which is what its exact structural
    sibling `imported_symbol_removed` already declares.
    """

    def _entity(self, kind_name):
        from abicheck.change_registry import REGISTRY
        from abicheck.checker_policy import ChangeKind

        return REGISTRY.entity_for(ChangeKind(kind_name))

    def test_consumer_required_symbol_removed_is_a_binary_finding(self):
        from abicheck.model.change_catalog.dimensions import ChangeEntity

        assert self._entity("consumer_required_symbol_removed") is ChangeEntity.BINARY

    def test_it_agrees_with_its_structural_sibling(self):
        assert self._entity("consumer_required_symbol_removed") == self._entity(
            "imported_symbol_removed"
        )

    def test_the_evidence_really_discards_the_symbol_type(self):
        """The premise, asserted rather than assumed -- if `undefined_symbols`
        ever grows a type, BINARY stops being the honest answer and this test
        is where that should be noticed."""
        from dataclasses import fields

        from abicheck.appcompat import AppRequirements

        undefined = {f.name: f for f in fields(AppRequirements)}["undefined_symbols"]
        assert "set[str]" in str(undefined.type).replace(" ", "")


class TestAPersistingCallableWithAChangedSignatureIsModified:
    """A parameter is not an entity in this vocabulary, so a parameter-level
    change is a *modification of the function that persists*, whatever the
    kind's name ends in. `python_api_parameter_removed` was the one
    parameter-level kind declared REMOVED, which put it in
    `--view show=removed` and hid it from `show=changed` (Codex review,
    PR #1284).

    Stated over the whole parameter-level family, so the next one added
    cannot drift the same way -- and this is exactly the case the retired
    name-suffix derivation got wrong by construction.
    """

    def _operation(self, kind_name):
        from abicheck.change_registry import REGISTRY
        from abicheck.checker_policy import ChangeKind

        return REGISTRY.operation_for(ChangeKind(kind_name))

    def test_every_parameter_level_kind_is_a_modification(self):
        from abicheck.model.change_catalog.dimensions import ChangeOperation

        family = [
            "python_api_parameter_added",
            "python_api_parameter_removed",
            "python_api_parameter_renamed",
            "python_api_parameter_kind_changed",
            "python_api_parameter_type_changed",
            "param_default_value_removed",
        ]
        disagreeing = {
            k: self._operation(k)
            for k in family
            if self._operation(k) is not ChangeOperation.MODIFIED
        }
        assert not disagreeing, disagreeing

    def test_the_family_is_not_empty(self):
        """Vacuity guard: an oracle that silently selects nothing passes the
        sweep above while asserting nothing."""
        from abicheck.checker_policy import ChangeKind

        assert ChangeKind("python_api_parameter_removed")

    def test_the_view_filter_follows(self):
        """The consequence a user actually sees, not just the declared
        value: `show=changed` lists it and `show=removed` does not."""
        changed = ShowOnlyFilter(frozenset(), frozenset(), frozenset({"changed"}))
        removed = ShowOnlyFilter(frozenset(), frozenset(), frozenset({"removed"}))
        assert changed._check_action("python_api_parameter_removed", changed.actions)
        assert not removed._check_action(
            "python_api_parameter_removed", removed.actions
        )


class TestAnOverlayKindTakesItsEntityFromWhatTriggeredIt:
    """`internal_symbol_required_by_public_api` is emitted for whatever the
    triggering breaking change was about — `func_removed` on an internal
    function, but equally `var_removed` on an internal global that a public
    inline function references. A fixed FUNCTION entity hid the latter from
    `--view show=variables` and serialized it as a function (Codex review,
    PR #1284).

    The rule is unanimous-or-fall-back, not first-wins: a decl that changed
    several ways at once has no single honest entity, and the declared
    fallback is a coarse answer rather than a wrong one.
    """

    def _entity(self, kinds):
        from abicheck.change_registry import unanimous_entity_for

        return unanimous_entity_for(kinds)

    def test_a_variable_trigger_yields_a_variable_finding(self):
        assert self._entity(["var_removed"]) == "variable"

    def test_a_function_trigger_yields_a_function_finding(self):
        assert self._entity(["func_removed"]) == "function"

    def test_a_mixed_trigger_falls_back_rather_than_guessing(self):
        assert self._entity(["func_removed", "var_removed"]) is None
        assert self._entity([]) is None

    def test_it_agrees_with_the_registry_for_every_breaking_kind(self):
        """The oracle is the catalog itself, swept over every kind that can
        actually trigger this detector -- so the helper cannot drift from
        what the registry says about those same kinds."""
        from abicheck.change_registry import REGISTRY
        from abicheck.policy.classification import BREAKING_KINDS

        disagreeing = {}
        for kind in BREAKING_KINDS:
            declared = REGISTRY.entity_for(kind.value)
            expected = None if declared is None else declared.value
            got = self._entity([kind.value])
            if got != expected:
                disagreeing[kind.value] = (got, expected)
        assert not disagreeing, disagreeing

    def test_the_sweep_is_not_vacuous(self):
        from abicheck.policy.classification import BREAKING_KINDS

        assert len(BREAKING_KINDS) > 50

    def test_the_catalog_declares_the_kind_polymorphic(self):
        """Without this the discriminator the detector sets is ignored."""
        from abicheck.change_registry import REGISTRY

        assert (
            REGISTRY.entity_from_field_for("internal_symbol_required_by_public_api")
            == "entity_discriminator"
        )


class TestJUnitClassnamesComeFromTheCatalog:
    """JUnit kept a fourth name-prefix taxonomy (`func_`/`var_`/`type_`/
    `union_`/`enum_`) after every other projection moved to the catalog, so
    it answered `metadata` for kinds the catalog declares as real elements
    and could not classify a polymorphic kind at all — JUnit disagreed with
    JSON and `--view show=` about the same finding (Codex review, PR #1284).
    """

    def _classname(self, kind_value, **kw):
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change
        from abicheck.junit_report import _classname_for

        return _classname_for(
            Change(kind=ChangeKind(kind_value), symbol="s", description="d", **kw)
        )

    def test_kinds_the_prefix_table_called_metadata_are_classified_now(self):
        assert self._classname("constant_added") == "variables"
        assert self._classname("calling_convention_changed") == "functions"

    def test_the_obvious_cases_are_unchanged(self):
        assert self._classname("func_removed") == "functions"
        assert self._classname("var_removed") == "variables"
        assert self._classname("type_size_changed") == "types"

    def test_a_non_element_entity_keeps_the_metadata_bucket(self):
        """Existing consumers' grouping is unchanged for everything the old
        table already got right."""
        assert self._classname("soname_changed") == "metadata"

    def test_junit_agrees_with_the_view_filter_for_every_kind(self):
        """The claim worth pinning: one taxonomy, not two that happen to
        agree today. Swept over the whole catalog against the same resolver
        `--view show=` uses."""
        from abicheck.change_registry import REGISTRY
        from abicheck.checker_policy import ChangeKind

        mapping = {
            "function": "functions",
            "variable": "variables",
            "type": "types",
            "enum": "enums",
        }
        disagreeing = {}
        for kind in ChangeKind:
            entity = REGISTRY.entity_for(kind.value)
            expected = mapping.get(getattr(entity, "value", ""), "metadata")
            got = self._classname(kind.value)
            if got != expected:
                disagreeing[kind.value] = (got, expected)
        assert not disagreeing, disagreeing

    def test_that_sweep_reaches_every_bucket(self):
        """Vacuity guard: a mapping reduced to a constant would satisfy the
        sweep above while asserting nothing."""
        from abicheck.checker_policy import ChangeKind

        seen = {self._classname(k.value) for k in ChangeKind}
        assert seen == {"functions", "variables", "types", "enums", "metadata"}


class TestADataOnlyProducerYieldsAVariableFinding:
    """Five kinds were declared `BINARY` whose producers emit them *only* for
    data symbols or *only* per function, so `--view show=variables` /
    `show=functions` omitted them and the JSON `entity` was wrong (Codex
    review, PR #1284).

    The oracle here is the producer's own gate, not a name: each of these is
    guarded on `SymbolType.OBJECT/COMMON/TLS` (or, for the return-convention
    kind, emitted once per public function name). `struct_return_convention_
    changed` additionally had a *reviewed* allowlist entry asserting it was
    "a target calling-convention trait, not one function's attribute" -- true
    about its cause, but the finding is keyed to the function whose callers
    break, which is what the display dimension answers.
    """

    DATA_ONLY = (
        "symbol_size_changed",
        "symbol_size_changed_const_object",
        "symbol_size_changed_internal",
        "exported_object_alignment_reduced",
        "protected_visibility_changed",
    )

    def test_every_data_only_kind_is_a_variable(self):
        from abicheck.model.change_catalog.dimensions import ChangeEntity

        wrong = {
            k: REGISTRY.entity_for(k)
            for k in self.DATA_ONLY
            if REGISTRY.entity_for(k) is not ChangeEntity.VARIABLE
        }
        assert not wrong, wrong

    def test_they_are_shown_by_the_variables_filter(self):
        f = ShowOnlyFilter(frozenset(), frozenset({"variables"}), frozenset())
        for kind in self.DATA_ONLY:
            assert f._check_element(None, kind), (
                f"{kind} is hidden from --view show=variables"
            )

    def test_a_per_function_finding_is_a_function(self):
        from abicheck.model.change_catalog.dimensions import ChangeEntity

        assert (
            REGISTRY.entity_for("struct_return_convention_changed")
            is ChangeEntity.FUNCTION
        )

    def test_a_disappeared_instantiation_is_a_removal(self):
        """`instantiation_missing_from_binary` fires when an old exported
        instantiation is absent from the new export set while its siblings
        survive -- a disappearance, so `show=removed` must list it."""
        from abicheck.model.change_catalog.dimensions import ChangeOperation

        assert (
            REGISTRY.operation_for("instantiation_missing_from_binary")
            is ChangeOperation.REMOVED
        )
        removed = ShowOnlyFilter(frozenset(), frozenset(), frozenset({"removed"}))
        assert removed._check_action(
            "instantiation_missing_from_binary", removed.actions
        )


class TestASharedSlotIteratorKeepsEachFindingsOwner:
    """`iter_type_slot_changes` yields function-owned slots (a parameter or a
    return type) and record-owned slots (a field) through one iterator, and
    three kinds are emitted from it: `atomic_qualifier_changed`,
    `bit_int_width_changed`, `char8t_migration`. A single fixed entity is
    therefore wrong for one of the two owners by construction — an `_Atomic`
    change on a function parameter was declared a *type* finding and dropped
    by `--view show=functions` (Codex review, PR #1284).

    The discriminator is stated by the branch that yields the slot, which
    knows structurally which side it walked; it is deliberately *not*
    re-derived from `slot`'s human text ("parameter 'n'" / "field 'buf'"),
    which would be a second interpretation of a display string — the exact
    shape this slice exists to delete.
    """

    SHARED_KINDS = (
        "atomic_qualifier_changed",
        "bit_int_width_changed",
        "char8t_migration",
    )

    def test_each_shared_kind_declares_a_discriminator(self):
        missing = [
            k for k in self.SHARED_KINDS if REGISTRY.entity_from_field_for(k) is None
        ]
        assert not missing, missing

    def test_the_producer_states_the_owner_for_both_branches(self):
        """Asserted against the real iterator, not a hand-built slot."""
        from abicheck.diff_type_spellings import iter_type_slot_changes
        from abicheck.model import (
            AbiSnapshot,
            Function,
            Param,
            RecordType,
            TypeField,
            Visibility,
        )

        old = AbiSnapshot(
            library="l",
            version="1",
            functions=[
                Function(
                    name="f",
                    mangled="_Z1fi",
                    return_type="int",
                    params=[Param(name="p", type="int")],
                    visibility=Visibility.PUBLIC,
                )
            ],
            types=[
                RecordType(
                    name="R",
                    kind="struct",
                    size_bits=32,
                    fields=[TypeField(name="x", type="int")],
                )
            ],
        )
        new = AbiSnapshot(
            library="l",
            version="2",
            functions=[
                Function(
                    name="f",
                    mangled="_Z1fi",
                    return_type="long",
                    params=[Param(name="p", type="_Atomic int")],
                    visibility=Visibility.PUBLIC,
                )
            ],
            types=[
                RecordType(
                    name="R",
                    kind="struct",
                    size_bits=32,
                    fields=[TypeField(name="x", type="_Atomic int")],
                )
            ],
        )
        owners = {
            (c.slot.split()[0], c.owner) for c in iter_type_slot_changes(old, new)
        }
        assert ("parameter", "function") in owners, owners
        assert ("return", "function") in owners, owners
        assert ("field", "type") in owners, owners

    def test_a_function_slot_finding_reaches_the_functions_filter(self):
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        finding = Change(
            kind=ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="f",
            description="d",
            entity_discriminator="function",
        )
        functions = ShowOnlyFilter(frozenset(), frozenset({"functions"}), frozenset())
        assert functions._check_element(finding, "atomic_qualifier_changed")

    def test_a_record_slot_finding_still_reaches_the_types_filter(self):
        """The other half: the fix must not move the record case."""
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        finding = Change(
            kind=ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="R",
            description="d",
            entity_discriminator="type",
        )
        types = ShowOnlyFilter(frozenset(), frozenset({"types"}), frozenset())
        assert types._check_element(finding, "atomic_qualifier_changed")
