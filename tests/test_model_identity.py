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

"""ADR-063 Phase 2 (first slice): ``ScopePath``/``EntityId`` primitive.

Note this file is deliberately named ``test_model_identity.py``, not
``test_entity_identity.py`` as the plan doc's Phase 2 "Tests" section names
it -- ``tests/test_entity_identity.py`` already exists, for the unrelated
ADR-048/G31 ``buildsource.entity_identity`` (L5 source-graph, USR-based)
primitive. The plan doc is corrected to match in the same commit that adds
this file.

Scope of this slice (see ``abicheck/model/identity.py``'s own module
docstring for the two open design questions deliberately left for a later
slice): pins the identity-vs-payload contract for each ``ScopePath``
segment type, and the "no bare ``(scope, kind)``" collision contract for
``EntityId`` -- both are correctness properties, not example-shaped
behavior, so most of this file is Hypothesis-driven per AGENTS.md's
"Primitive-level property tests" convention rather than fixed examples.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.model import identity as model_identity
from abicheck.model.identity import (
    Anonymous,
    EntityKind,
    InlineNamespace,
    LocalToFunction,
    Namespace,
    ObservationKind,
    Record,
    entity_id_for_constant,
    entity_id_for_enum,
    entity_id_for_function,
    entity_id_for_type,
    entity_id_for_typedef,
    entity_id_for_variable,
)
from abicheck.storage import entity_ids as storage_entity_ids

# --------------------------------------------------------------------------
# EntityKind/ObservationKind relocation
# --------------------------------------------------------------------------


class TestRelocatedVocabulary:
    """Exactly one ``EntityKind``/``ObservationKind`` exists in the
    repository after this slice -- ``storage.entity_ids`` imports rather
    than redefines them (acceptance criterion from the plan's Phase 2
    section, testable now even though the rest of that phase is not yet
    landed)."""

    def test_storage_entity_kind_is_the_same_object(self) -> None:
        assert storage_entity_ids.EntityKind is EntityKind

    def test_storage_observation_kind_is_the_same_object(self) -> None:
        assert storage_entity_ids.ObservationKind is ObservationKind

    def test_storage_wire_entity_id_still_works_with_relocated_kind(self) -> None:
        # storage.entity_ids.EntityId is the pre-existing wire DTO, unrelated
        # to model.identity.EntityId (this slice does not bridge the two —
        # see identity.py's module docstring). Confirms the relocation is
        # purely additive from the wire type's point of view.
        wire_id = storage_entity_ids.EntityId(
            kind=EntityKind.FUNCTION, qualified_name="ns::f", discriminator="_Z1fv"
        )
        assert wire_id.kind is EntityKind.FUNCTION
        round_tripped = storage_entity_ids.EntityId.from_dict(wire_id.to_dict())
        assert round_tripped == wire_id


# --------------------------------------------------------------------------
# Segment identity-vs-payload contracts
# --------------------------------------------------------------------------

_names = st.text(
    min_size=1, max_size=12, alphabet=st.characters(min_codepoint=97, max_codepoint=122)
)
_accesses = st.sampled_from(["", "public", "protected", "private"])
_kinds = st.sampled_from(["struct", "union", "enum", "namespace"])
_ordinals = st.integers(min_value=0, max_value=1000)


class TestRecordAccessIsNotIdentity:
    """``Record.access`` is payload, not identity -- an access-level change
    alone must never look like a different containing scope."""

    @given(name=_names, access_a=_accesses, access_b=_accesses)
    def test_equal_regardless_of_access(
        self, name: str, access_a: str, access_b: str
    ) -> None:
        a = Record(name=name, access=access_a)
        b = Record(name=name, access=access_b)
        assert a == b
        assert hash(a) == hash(b)

    @given(name_a=_names, name_b=_names, access=_accesses)
    def test_distinct_names_never_equal(
        self, name_a: str, name_b: str, access: str
    ) -> None:
        if name_a == name_b:
            return
        assert Record(name=name_a, access=access) != Record(name=name_b, access=access)

    def test_access_is_preserved_on_the_instance(self) -> None:
        # Payload, not identity, but not discarded either — a consumer that
        # wants to know the access level of a nested scope still can.
        rec = Record(name="Inner", access="private")
        assert rec.access == "private"


class TestAnonymousOrdinalIsIdentity:
    """Both fields of ``Anonymous`` are identity -- nothing else
    disambiguates two sibling anonymous scopes."""

    @given(kind=_kinds, ordinal_a=_ordinals, ordinal_b=_ordinals)
    def test_distinct_ordinal_never_equal(
        self, kind: str, ordinal_a: int, ordinal_b: int
    ) -> None:
        if ordinal_a == ordinal_b:
            return
        assert Anonymous(kind=kind, ordinal=ordinal_a) != Anonymous(
            kind=kind, ordinal=ordinal_b
        )

    @given(kind=_kinds, ordinal=_ordinals)
    def test_same_kind_and_ordinal_equal(self, kind: str, ordinal: int) -> None:
        assert Anonymous(kind=kind, ordinal=ordinal) == Anonymous(
            kind=kind, ordinal=ordinal
        )


class TestInlineNamespaceVersionTagIsIdentity:
    """ADR-025's own versioned-inline-namespace-alias handling keys on
    ``version_tag`` -- it must stay part of identity here."""

    @given(name=_names, tag_a=_names, tag_b=_names)
    def test_distinct_version_tag_never_equal(
        self, name: str, tag_a: str, tag_b: str
    ) -> None:
        if tag_a == tag_b:
            return
        assert InlineNamespace(name=name, version_tag=tag_a) != InlineNamespace(
            name=name, version_tag=tag_b
        )


class TestLocalToFunctionOwnerIsIdentity:
    @given(owner_a=_names, owner_b=_names)
    def test_distinct_owner_never_equal(self, owner_a: str, owner_b: str) -> None:
        if owner_a == owner_b:
            return
        assert LocalToFunction(owner=owner_a, block_ordinal=0) != LocalToFunction(
            owner=owner_b, block_ordinal=0
        )

    @given(owner=_names, ordinal_a=_ordinals, ordinal_b=_ordinals)
    def test_distinct_block_ordinal_never_equal(
        self, owner: str, ordinal_a: int, ordinal_b: int
    ) -> None:
        # CodeRabbit-flagged gap: owner alone doesn't disambiguate two
        # same-named locals in sibling compound blocks of one function --
        # the same sibling-collision shape Anonymous.ordinal already closes.
        if ordinal_a == ordinal_b:
            return
        assert LocalToFunction(owner=owner, block_ordinal=ordinal_a) != LocalToFunction(
            owner=owner, block_ordinal=ordinal_b
        )

    @given(owner=_names, ordinal=_ordinals)
    def test_same_owner_and_block_ordinal_equal(self, owner: str, ordinal: int) -> None:
        assert LocalToFunction(owner=owner, block_ordinal=ordinal) == LocalToFunction(
            owner=owner, block_ordinal=ordinal
        )


class TestSegmentsAreHashable:
    """Every segment type is usable as a dict key / set member -- ``scope``
    lives inside a frozen, hashable ``EntityId``, so every segment it can
    hold must itself be hashable."""

    def test_all_segment_kinds_hashable(self) -> None:
        segments = [
            Namespace(name="ns"),
            Record(name="C", access="private"),
            InlineNamespace(name="v1", version_tag="v1"),
            Anonymous(kind="struct", ordinal=0),
            LocalToFunction(owner="f", block_ordinal=0),
        ]
        as_set = set(segments)
        assert len(as_set) == len(segments)


# --------------------------------------------------------------------------
# EntityId: no bare (scope, kind) collision
# --------------------------------------------------------------------------


class TestDistinctScopesNeverCollide:
    """Two distinct declarations in different namespaces never collide
    regardless of bare-name overlap."""

    def test_same_leaf_name_different_namespace(self) -> None:
        a = entity_id_for_type((Namespace("a"),), "Widget")
        b = entity_id_for_type((Namespace("b"),), "Widget")
        assert a != b

    def test_record_nested_in_record_vs_namespace(self) -> None:
        # A record nested in a record vs. the same bare names nested in a
        # namespace — the exact collision `qualified_name`-only identity
        # (a `"::".join`) cannot distinguish, since both render to "A::B".
        in_record = entity_id_for_type((Record("A"), Record("B")), "C")
        in_namespace = entity_id_for_type((Namespace("A"), Namespace("B")), "C")
        assert in_record != in_namespace


class TestSiblingKindsNeverCollide:
    """A bare ``(scope, kind)`` id would collide any two sibling
    declarations of the same kind and scope -- pinned per kind."""

    @given(name_a=_names, name_b=_names)
    def test_two_enums_same_scope(self, name_a: str, name_b: str) -> None:
        if name_a == name_b:
            return
        scope = (Namespace("ns"),)
        assert entity_id_for_enum(scope, name_a) != entity_id_for_enum(scope, name_b)

    @given(name_a=_names, name_b=_names)
    def test_two_typedefs_same_scope(self, name_a: str, name_b: str) -> None:
        if name_a == name_b:
            return
        scope = (Namespace("ns"),)
        assert entity_id_for_typedef(scope, name_a) != entity_id_for_typedef(
            scope, name_b
        )

    @given(name_a=_names, name_b=_names)
    def test_two_constants_same_scope(self, name_a: str, name_b: str) -> None:
        if name_a == name_b:
            return
        scope = (Namespace("ns"),)
        assert entity_id_for_constant(scope, name_a) != entity_id_for_constant(
            scope, name_b
        )

    def test_different_kinds_same_scope_and_name_never_collide(self) -> None:
        # ns::A the type vs. ns::A the constant — legal to coexist in some
        # producer's raw output even if not in valid C++ (a robust identity
        # scheme should not rely on the source language to keep this safe).
        scope = (Namespace("ns"),)
        assert entity_id_for_type(scope, "A") != entity_id_for_constant(scope, "A")


# --------------------------------------------------------------------------
# Function overload discrimination
# --------------------------------------------------------------------------


class TestFunctionOverloadDiscrimination:
    """The counterexample this design was built to close: two overloads
    sharing one ``ScopePath`` must always produce distinct ``EntityId``s."""

    def test_f_int_vs_f_double(self) -> None:
        scope = (Namespace("ns"),)
        f_int = entity_id_for_function(scope, "f", param_types=("int",))
        f_double = entity_id_for_function(scope, "f", param_types=("double",))
        assert f_int != f_double

    def test_const_vs_non_const_overload(self) -> None:
        scope = (Record("C"),)
        plain = entity_id_for_function(scope, "f", cv_qualifiers=())
        const = entity_id_for_function(scope, "f", cv_qualifiers=("const",))
        assert plain != const

    def test_different_scope_same_name_never_collides_with_mangled_sig_tag(
        self,
    ) -> None:
        # The ("mangled", ...) / ("sig", ...) tag makes the two branches
        # occupy disjoint regions of `extra`'s value space — a mangled name
        # that happens to equal some other function's literal param-type
        # spelling must not collide with it.
        scope = (Namespace("ns"),)
        mangled = entity_id_for_function(scope, "f", mangled_name="int")
        sig = entity_id_for_function(scope, "f", param_types=("int",))
        assert mangled != sig

    def test_mangled_name_present_ignores_param_types(self) -> None:
        # extern "C": a changed parameter list is a modification of the one
        # function, not a different overload, once a genuine mangled name is
        # known.
        scope = (Namespace("ns"),)
        a = entity_id_for_function(scope, "f", mangled_name="f", param_types=("int",))
        b = entity_id_for_function(
            scope, "f", mangled_name="f", param_types=("double", "int")
        )
        assert a == b

    def test_distinct_mangled_names_never_collide(self) -> None:
        scope = (Namespace("ns"),)
        a = entity_id_for_function(scope, "f", mangled_name="_Z1fi")
        b = entity_id_for_function(scope, "f", mangled_name="_Z1fd")
        assert a != b

    def test_extern_c_ignores_param_types(self) -> None:
        # The Codex-flagged gap: an extern "C" caller follows mangled_name's
        # own contract and passes None for it (the raw export spelling is
        # not a genuine mangling) -- without is_extern_c, that falls
        # through to the signature branch and a parameter-type change would
        # wrongly look like a different overload. C has no overload
        # resolution, so this must collapse to one id, mirroring
        # resolve_function_identity's func.is_extern_c gate.
        scope = (Namespace("ns"),)
        a = entity_id_for_function(scope, "f", is_extern_c=True, param_types=("int",))
        b = entity_id_for_function(
            scope, "f", is_extern_c=True, param_types=("double", "int")
        )
        assert a == b

    def test_extern_c_tag_never_collides_with_sig_tag(self) -> None:
        # ("extern_c",) and ("sig", ...) must occupy disjoint regions of
        # extra's value space, same discipline as ("mangled", ...) vs.
        # ("sig", ...).
        scope = (Namespace("ns"),)
        extern_c = entity_id_for_function(scope, "f", is_extern_c=True)
        sig = entity_id_for_function(scope, "f")
        assert extern_c != sig

    def test_ref_qualifier_distinguishes_overloads(self) -> None:
        # The other Codex-flagged gap: C::f() & vs. C::f() && share scope,
        # name, param_types, and cv_qualifiers -- only ref_qualifier tells
        # them apart, mirroring resolve_function_identity's own dimension.
        scope = (Record("C"),)
        lvalue = entity_id_for_function(scope, "f", ref_qualifier="&")
        rvalue = entity_id_for_function(scope, "f", ref_qualifier="&&")
        assert lvalue != rvalue

    def test_variadic_distinguishes_overloads(self) -> None:
        # void f(int) vs. void f(int, ...) share identical fixed
        # parameters -- only is_variadic tells them apart.
        scope = (Namespace("ns"),)
        fixed = entity_id_for_function(
            scope, "f", param_types=("int",), is_variadic=False
        )
        variadic = entity_id_for_function(
            scope, "f", param_types=("int",), is_variadic=True
        )
        assert fixed != variadic

    def test_variadic_none_is_distinct_from_confirmed_states(self) -> None:
        # bool | None: a producer that doesn't know must not silently
        # collapse onto "confirmed non-variadic", the same tri-state
        # resolve_function_identity's own f"variadic:{func.is_variadic}"
        # preserves.
        scope = (Namespace("ns"),)
        unknown = entity_id_for_function(scope, "f", is_variadic=None)
        confirmed_false = entity_id_for_function(scope, "f", is_variadic=False)
        confirmed_true = entity_id_for_function(scope, "f", is_variadic=True)
        assert unknown != confirmed_false
        assert unknown != confirmed_true
        assert confirmed_false != confirmed_true

    def test_mangled_name_wins_over_extern_c_and_ignores_signature_dims(self) -> None:
        # When a genuine mangled name is present, it wins outright --
        # is_extern_c/ref_qualifier/is_variadic are all ignored, matching
        # the plain param_types/cv_qualifiers precedence already pinned by
        # test_mangled_name_present_ignores_param_types.
        scope = (Namespace("ns"),)
        a = entity_id_for_function(
            scope,
            "f",
            mangled_name="_Z1fv",
            is_extern_c=True,
            ref_qualifier="&",
            is_variadic=True,
        )
        b = entity_id_for_function(scope, "f", mangled_name="_Z1fv")
        assert a == b


class TestVariableMangledDiscriminator:
    def test_distinct_mangled_names_never_collide(self) -> None:
        scope = (Namespace("ns"),)
        a = entity_id_for_variable(scope, "v", mangled_name="_ZN2ns1vE_v1")
        b = entity_id_for_variable(scope, "v", mangled_name="_ZN2ns1vE_v2")
        assert a != b

    def test_absent_mangled_name_is_the_documented_degenerate_case(self) -> None:
        # Two variables sharing scope+leaf name with no mangled evidence at
        # all collapse to one EntityId — documented, deliberate (mirrors
        # AGENTS.md's own SymbolIdentityIndex note: "variables enable no
        # alias tier at all"), not silently accidental.
        scope = (Namespace("ns"),)
        a = entity_id_for_variable(scope, "v")
        b = entity_id_for_variable(scope, "v")
        assert a == b


# --------------------------------------------------------------------------
# "Computed once" == one algorithm, not cached identity
# --------------------------------------------------------------------------


class TestPureFunctionOfInputs:
    """Calling a constructor twice with value-equal-but-not-identical
    inputs produces an equal ``EntityId`` -- "computed once" means one
    algorithm called the same way everywhere, not a value cached on any
    object (per this module's own docstring and the plan's framing note)."""

    def test_list_vs_tuple_scope_argument(self) -> None:
        as_list = [Namespace("a"), Record("B")]
        as_tuple = (Namespace("a"), Record("B"))
        assert entity_id_for_type(as_list, "C") == entity_id_for_type(as_tuple, "C")

    def test_two_freshly_constructed_scopes_compare_equal(self) -> None:
        a = entity_id_for_function(
            (Namespace("ns"), Record("C")), "f", param_types=("int",)
        )
        b = entity_id_for_function(
            (Namespace("ns"), Record("C")), "f", param_types=("int",)
        )
        assert a == b
        assert hash(a) == hash(b)


def test_entity_id_is_hashable() -> None:
    eid = entity_id_for_function((Namespace("ns"),), "f", param_types=("int",))
    hash(eid)  # must not raise
    assert eid in {eid}


def test_module_declares_no_dependency_above_model() -> None:
    """Leaf-module contract (ADR-063 D10): ``model.identity`` imports
    nothing from ``checker_types``/``diff_*``/anything above ``model`` —
    checked against the module's real ``import``/``from ... import`` AST
    nodes, not a substring scan of its source text (which would also match
    the module's own explanatory prose about what it deliberately avoids)."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(model_identity))
    banned_prefixes = (
        "checker_types",
        "diff_",
        "checker",
        "compare",
        "finding_identity",
    )
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
    for name in imported_names:
        bare = name.lstrip(".")
        assert not bare.startswith(banned_prefixes), name
