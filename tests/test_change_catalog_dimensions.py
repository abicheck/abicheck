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

"""The change catalog's declared display dimensions are *correct*.

Sibling of `test_view_internal_grammar.py`, which owns the `--view` grammar
itself (what tokens exist, what they parse to, what is retired). This file
owns the other half plan slice 7o created: 407 hand-seeded `entity`/
`operation` declarations, and whether each one says what its own entry
means.

They are separated because the failure modes are different. A grammar bug
is a wrong token; a dimension bug is a *silently* wrong classification that
fails nothing anywhere -- three consecutive review rounds each found more of
them by reading entries, which is why
`TestADeclaredEntityAgreesWithItsOwnRegistration` makes the contradiction
mechanical instead.
"""

from __future__ import annotations

import pytest

from abicheck.change_registry import REGISTRY
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import ChangeEntity, ChangeOperation
from abicheck.reporter_markdown import (
    ShowOnlyFilter,
    entity_for_change,
    entity_for_kind,
)


class TestBaseClassLayoutFindingsAreTypeEntities:
    """A base subobject moving within a record is a *type* layout break.

    `surface.py` already routes this family through type-level
    reachability because the symbol is the owning type; classifying it as
    a function meant `--view show=types` hid it and `show=functions`
    wrongly included it.
    """

    KINDS = (
        "base_class_offset_changed",
        "base_class_position_changed",
        "base_class_virtual_changed",
    )

    @pytest.mark.parametrize("kind", KINDS)
    def test_declared_as_a_type_entity(self, kind):
        assert entity_for_kind(kind) == ChangeEntity.TYPE.value

    @pytest.mark.parametrize("kind", KINDS)
    def test_shown_by_the_types_token_and_not_the_functions_token(self, kind):
        types_filter = ShowOnlyFilter(frozenset(), frozenset({"types"}), frozenset())
        functions_filter = ShowOnlyFilter(
            frozenset(), frozenset({"functions"}), frozenset()
        )
        assert types_filter._check_element(None, kind)
        assert not functions_filter._check_element(None, kind)

    def test_it_agrees_with_the_type_level_surface_routing(self):
        """The independent oracle: `surface.py`'s own type-level family."""
        from abicheck.surface import _TYPE_LEVEL_KIND_NAMES

        for kind in _TYPE_LEVEL_KIND_NAMES:
            if entity_for_kind(kind) is None:
                continue
            assert entity_for_kind(kind) in {
                ChangeEntity.TYPE.value,
                ChangeEntity.ENUM.value,
                ChangeEntity.ANALYSIS.value,
            }, kind


class TestAnonymousFieldChangesAreTypeFindings:
    """`anon_field_changed` compares anonymous members of a matched record
    and carries the containing record's identity, so `--view show=types`
    must show it and `show=functions` must not."""

    def test_declared_as_a_type_entity(self):
        assert entity_for_kind("anon_field_changed") == ChangeEntity.TYPE.value

    def test_shown_by_the_types_token_and_not_the_functions_token(self):
        types_filter = ShowOnlyFilter(frozenset(), frozenset({"types"}), frozenset())
        functions_filter = ShowOnlyFilter(
            frozenset(), frozenset({"functions"}), frozenset()
        )
        assert types_filter._check_element(None, "anon_field_changed")
        assert not functions_filter._check_element(None, "anon_field_changed")


class TestAnAttributeTransitionIsAModification:
    """A kind reporting that a *persisting* declaration gained or lost an
    attribute is a modification of that declaration, not an addition or a
    removal of it.

    The class, not the one reported kind: Codex flagged
    `func_deprecated_added`, and the same mistake was in every
    `[[deprecated]]` sibling, the `override`-specifier pair and the field
    default-initializer kind -- each one naming a declaration present on
    both sides. `ChangeOperation`'s own docstring already stated the rule
    (`func_noexcept_added` is a "trait gained by a persisting entity"); the
    seeding pass did not apply it consistently.

    Deliberately *not* included, and the boundary is the point:
    `func_export_added`/`var_export_added` stay `ADDED` because a symbol
    genuinely appears in the binary's export table -- a new thing becomes
    bindable, rather than an existing one being annotated.
    """

    #: Every kind whose name says an attribute of a persisting declaration
    #: moved. Derived from the catalog by shape, then asserted -- so a kind
    #: added tomorrow in this shape is caught rather than assumed.
    ATTRIBUTE_MARKERS = (
        "_deprecated_",
        "_override_specifier_",
        "_default_initializer_",
    )

    def _attribute_transition_kinds(self):
        return sorted(
            k.value
            for k in ChangeKind
            if any(m in k.value for m in self.ATTRIBUTE_MARKERS)
            and REGISTRY.operation_for(k.value) is not None
        )

    def test_the_corpus_is_not_empty(self):
        """Vacuity guard: an empty derivation would pass every assertion."""
        assert len(self._attribute_transition_kinds()) >= 10

    def test_none_of_them_is_an_addition_or_a_removal(self):
        wrong = {
            k: REGISTRY.operation_for(k).value
            for k in self._attribute_transition_kinds()
            if REGISTRY.operation_for(k) is not ChangeOperation.MODIFIED
        }
        assert not wrong, wrong

    def test_they_are_shown_by_changed_and_hidden_by_added_and_removed(self):
        changed = ShowOnlyFilter(frozenset(), frozenset(), frozenset({"changed"}))
        added = ShowOnlyFilter(frozenset(), frozenset(), frozenset({"added"}))
        removed = ShowOnlyFilter(frozenset(), frozenset(), frozenset({"removed"}))
        for kind in self._attribute_transition_kinds():
            assert changed._check_action(kind, changed.actions), kind
            assert not added._check_action(kind, added.actions), kind
            assert not removed._check_action(kind, removed.actions), kind

    def test_a_genuinely_new_export_is_still_an_addition(self):
        """The boundary, asserted so a later sweep cannot widen the rule
        into every kind whose subject persists."""
        assert REGISTRY.operation_for("func_export_added") is ChangeOperation.ADDED
        assert REGISTRY.operation_for("var_export_added") is ChangeOperation.ADDED


class TestAPolymorphicKindTakesItsEntityFromTheFinding:
    """A kind a detector emits for more than one entity type resolves its
    entity per *finding*, not per kind.

    `diff_namespaces` emits both experimental-namespace kinds for functions
    and for types; declaring either statically excluded a graduated
    *function* from `--view show=functions` and serialized a wrong `entity`
    (Codex review, PR #1284). The polymorphism is declared on the kind's own
    single catalog registration (`ChangeKindMeta.entity_from_field`), so
    this is not a second table the reporter maintains.

    **Every case below runs the real detector.** The first version of this
    class built a synthetic object carrying a `detail` attribute and passed
    in full, while the production path resolved nothing at all: the
    mechanism pointed at `make_change`'s `detail` *argument*, which `Change`
    does not store, so every real finding fell back to the declared entity
    (Codex review round 4 — the exact "test written to confirm the fix"
    failure AGENTS.md names). A synthetic finding cannot state this claim.
    """

    POLYMORPHIC = (
        "experimental_graduated",
        "experimental_removed_without_replacement",
    )

    @staticmethod
    def _real_findings():
        """One real finding per (kind, entity) the detector can produce."""
        from abicheck.diff_namespaces import detect_experimental_namespace_changes
        from tests.test_diff_namespaces import _fn, _rec, _snap

        graduated_fn = detect_experimental_namespace_changes(
            _snap(funcs=[_fn("ns::experimental::sort")]),
            _snap(funcs=[_fn("ns::experimental::sort"), _fn("ns::sort")]),
        )
        graduated_type = detect_experimental_namespace_changes(
            _snap(types=[_rec("ns::experimental::queue")]),
            _snap(types=[_rec("ns::experimental::queue"), _rec("ns::queue")]),
        )
        removed_fn = detect_experimental_namespace_changes(
            _snap(funcs=[_fn("ns::experimental::sort")]), _snap(funcs=[])
        )
        removed_type = detect_experimental_namespace_changes(
            _snap(types=[_rec("ns::experimental::queue")]), _snap(types=[])
        )
        return {
            ("experimental_graduated", "function"): graduated_fn,
            ("experimental_graduated", "type"): graduated_type,
            ("experimental_removed_without_replacement", "function"): removed_fn,
            ("experimental_removed_without_replacement", "type"): removed_type,
        }

    def test_each_polymorphic_kind_declares_where_its_entity_comes_from(self):
        for kind in self.POLYMORPHIC:
            assert REGISTRY.entity_from_field_for(kind) == "entity_discriminator", kind

    def test_the_named_field_is_one_a_real_change_actually_carries(self):
        """The bug that made the first version of this vacuous: a field name
        no `Change` has resolves to `None` on every production finding."""
        import dataclasses

        from abicheck.checker_types import Change

        fields = {f.name for f in dataclasses.fields(Change)}
        for kind in self.POLYMORPHIC:
            assert REGISTRY.entity_from_field_for(kind) in fields, kind

    def test_the_detector_states_the_entity_on_every_real_finding(self):
        produced = self._real_findings()
        # Vacuity guard: all four shapes must actually produce a finding, or
        # the assertions below check nothing.
        for key, changes in produced.items():
            assert changes, key
        for (kind, expected), changes in produced.items():
            for change in changes:
                if change.kind.value != kind:
                    continue
                assert change.entity_discriminator == expected, (kind, expected)
                assert entity_for_change(change, change.kind.value) == expected

    def test_a_graduated_function_reaches_the_functions_token(self):
        functions = ShowOnlyFilter(frozenset(), frozenset({"functions"}), frozenset())
        types = ShowOnlyFilter(frozenset(), frozenset({"types"}), frozenset())
        produced = self._real_findings()
        for (kind, expected), changes in produced.items():
            for change in changes:
                if change.kind.value != kind:
                    continue
                wanted = functions if expected == "function" else types
                other = types if expected == "function" else functions
                assert wanted._check_element(change, change.kind.value), (
                    kind,
                    expected,
                )
                assert not other._check_element(change, change.kind.value), (
                    kind,
                    expected,
                )

    def test_the_json_projection_carries_the_concrete_entity(self):
        """Through the real serializer, not the resolver alone."""
        import json

        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.reporter import to_json

        for (kind, expected), changes in self._real_findings().items():
            relevant = [c for c in changes if c.kind.value == kind]
            if not relevant:
                continue
            payload = json.loads(
                to_json(
                    DiffResult(
                        old_version="1.0",
                        new_version="2.0",
                        library="libfoo.so",
                        changes=relevant,
                        verdict=Verdict.COMPATIBLE,
                    )
                )
            )
            entities = {c["entity"] for c in payload["changes"] if c["kind"] == kind}
            assert entities == {expected}, (kind, expected, entities)

    def test_an_unstated_discriminator_falls_back_rather_than_vanishing(self):
        """A hand-built `Change` states none; an unresolvable dimension would
        drop it from every element filter, which is worse than a coarse
        one."""
        from abicheck.checker_types import Change
        from abicheck.model.change_catalog.kinds import ChangeKind as CK

        for kind in self.POLYMORPHIC:
            change = Change(
                kind=CK(kind), symbol="ns::experimental::x", description="d"
            )
            assert change.entity_discriminator is None
            assert entity_for_change(change, kind) == entity_for_kind(kind)

    def test_a_monomorphic_kind_declares_no_discriminator(self):
        """The escape hatch is opt-in per kind."""
        unaffected = [
            k.value
            for k in ChangeKind
            if k.value not in self.POLYMORPHIC and REGISTRY.entity_for(k.value)
        ]
        assert len(unaffected) > 100, "vacuity guard"
        for kind in unaffected:
            assert REGISTRY.entity_from_field_for(kind) is None, kind


class TestADeclaredEntityAgreesWithItsOwnRegistration:
    """A kind's declared `entity` may not contradict the evidence in its own
    catalog entry.

    This class exists because three separate review rounds each found entity
    misclassifications by *reading* entries — `anon_field_changed` and the
    three `base_class_*` kinds (round 2), then `source_level_kind_changed`
    and `used_reserved_field` (round 3) — which is exactly the failure mode
    AGENTS.md names: 407 entries were seeded by hand, and a wrong one failed
    nothing anywhere, so a 47k-test suite passed against every one of them.
    The judgement stays manual; only the *contradiction* is now mechanical,
    and it immediately found a third round-3 error no reviewer had flagged
    (`ctor_overload_ambiguity_risk`, whose template names a class).

    Two oracles, both derived from `description_template` — the entry's own
    human-facing sentence, written independently of the `entity` field this
    slice added, so neither restates what it checks.
    """

    #: Leading noun of a `description_template` -> the entity it names.
    #: Deliberately partial: a noun that does not settle the question
    #: ("Symbol", "Member") is absent rather than guessed at.
    LEADING_NOUN_ENTITIES = {
        "function": ChangeEntity.FUNCTION,
        "method": ChangeEntity.FUNCTION,
        "constructor": ChangeEntity.FUNCTION,
        "destructor": ChangeEntity.FUNCTION,
        "operator": ChangeEntity.FUNCTION,
        "overload": ChangeEntity.FUNCTION,
        "variable": ChangeEntity.VARIABLE,
        "struct": ChangeEntity.TYPE,
        "class": ChangeEntity.TYPE,
        "union": ChangeEntity.TYPE,
        "type": ChangeEntity.TYPE,
        "typedef": ChangeEntity.TYPE,
        "field": ChangeEntity.TYPE,
        "aggregate": ChangeEntity.TYPE,
        "base": ChangeEntity.TYPE,
        "vtable": ChangeEntity.TYPE,
        "enum": ChangeEntity.ENUM,
    }

    #: A kind whose declared entity legitimately disagrees with an oracle
    #: below. Empty, and that is the point: an entry here is a reviewed
    #: decision with a reason, never a way to silence the check.
    ACCEPTED_DISAGREEMENTS: dict[str, str] = {}

    def _templated_kinds(self):
        return [
            (k.value, REGISTRY.description_template_for(k.value))
            for k in ChangeKind
            if REGISTRY.description_template_for(k.value)
        ]

    def test_the_oracles_have_something_to_check(self):
        """Vacuity guard: both oracles must match a substantial share of the
        catalog, or every assertion below is trivially true."""
        import re

        leading = [
            k
            for k, t in self._templated_kinds()
            if re.split(r"[^A-Za-z]+", t.strip())[0].lower()
            in self.LEADING_NOUN_ENTITIES
        ]
        member = [k for k, t in self._templated_kinds() if "{name}::" in t]
        assert len(leading) > 50, len(leading)
        assert len(member) > 20, len(member)

    def test_the_leading_noun_of_a_template_agrees_with_the_declared_entity(self):
        """ "Function ..." is a function finding, "Class ..." a type one."""
        import re

        disagreeing = {}
        for kind, template in self._templated_kinds():
            if kind in self.ACCEPTED_DISAGREEMENTS:
                continue
            noun = re.split(r"[^A-Za-z]+", template.strip())[0].lower()
            expected = self.LEADING_NOUN_ENTITIES.get(noun)
            if expected is None:
                continue
            declared = REGISTRY.entity_for(kind)
            if declared is not expected:
                disagreeing[kind] = (
                    template,
                    declared.value if declared else None,
                    expected.value,
                )
        assert not disagreeing, disagreeing

    def test_a_member_shaped_template_is_never_a_function_or_variable(self):
        """`{name}::{...}` names a member of an aggregate, so the finding is
        about the aggregate (TYPE) or the enumeration (ENUM) -- never about a
        function or a variable. `used_reserved_field` was the lone
        `FUNCTION` outlier among 30 kinds sharing this exact shape."""
        wrong = {
            kind: REGISTRY.entity_for(kind).value
            for kind, template in self._templated_kinds()
            if "{name}::" in template
            and kind not in self.ACCEPTED_DISAGREEMENTS
            and REGISTRY.entity_for(kind) not in (ChangeEntity.TYPE, ChangeEntity.ENUM)
        }
        assert not wrong, wrong

    def test_every_accepted_disagreement_states_a_reason(self):
        """The allowlist cannot become a silent one."""
        for kind, reason in self.ACCEPTED_DISAGREEMENTS.items():
            assert REGISTRY.entity_for(kind) is not None, kind
            assert len(reason) > 40, kind
