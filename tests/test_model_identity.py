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
    EntityId,
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


def _owner(name: str, *param_types: str) -> EntityId:
    """Build a plausible owning-function ``EntityId`` for ``LocalToFunction``
    tests -- ``owner`` is the owning function's own identity, not a bare
    string (Codex review, PR #941: see ``LocalToFunction``'s docstring)."""
    return entity_id_for_function((), name, param_types=param_types)


class TestLocalToFunctionOwnerIsIdentity:
    @given(owner_a=_names, owner_b=_names)
    def test_distinct_owner_never_equal(self, owner_a: str, owner_b: str) -> None:
        if owner_a == owner_b:
            return
        assert LocalToFunction(
            owner=_owner(owner_a), block_ordinal=0
        ) != LocalToFunction(owner=_owner(owner_b), block_ordinal=0)

    @given(owner=_names, ordinal_a=_ordinals, ordinal_b=_ordinals)
    def test_distinct_block_ordinal_never_equal(
        self, owner: str, ordinal_a: int, ordinal_b: int
    ) -> None:
        # CodeRabbit-flagged gap: owner alone doesn't disambiguate two
        # same-named locals in sibling compound blocks of one function --
        # the same sibling-collision shape Anonymous.ordinal already closes.
        if ordinal_a == ordinal_b:
            return
        o = _owner(owner)
        assert LocalToFunction(owner=o, block_ordinal=ordinal_a) != LocalToFunction(
            owner=o, block_ordinal=ordinal_b
        )

    @given(owner=_names, ordinal=_ordinals)
    def test_same_owner_and_block_ordinal_equal(self, owner: str, ordinal: int) -> None:
        o = _owner(owner)
        assert LocalToFunction(owner=o, block_ordinal=ordinal) == LocalToFunction(
            owner=o, block_ordinal=ordinal
        )

    def test_overloaded_owners_never_collide(self) -> None:
        # The Codex-flagged gap this dimension exists to close: f(int) and
        # f(double) each declaring the same local struct A in their
        # (corresponding) block must not merge just because both owners
        # share the bare function name "f" -- owner carries the *full*
        # overload-disambiguated identity of the enclosing function.
        int_overload = _owner("f", "int")
        double_overload = _owner("f", "double")
        assert LocalToFunction(owner=int_overload, block_ordinal=0) != LocalToFunction(
            owner=double_overload, block_ordinal=0
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
            LocalToFunction(owner=_owner("f"), block_ordinal=0),
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


class TestAnonymousDeclarationSelfIdentity:
    """The Codex-flagged gap: ScopePath names only the *containing* scope,
    never the leaf declaration itself, so two anonymous sibling records/
    enums both passing leaf_name="" would otherwise collide onto one
    EntityId regardless of which one is meant -- distinct from
    Anonymous.ordinal, which disambiguates a *descendant's* containing
    scope, not the anonymous declaration itself."""

    @given(ordinal_a=_ordinals, ordinal_b=_ordinals)
    def test_distinct_anonymous_types_never_collide(
        self, ordinal_a: int, ordinal_b: int
    ) -> None:
        if ordinal_a == ordinal_b:
            return
        scope = (Namespace("ns"),)
        a = entity_id_for_type(scope, "", anonymous_ordinal=ordinal_a)
        b = entity_id_for_type(scope, "", anonymous_ordinal=ordinal_b)
        assert a != b

    @given(ordinal_a=_ordinals, ordinal_b=_ordinals)
    def test_distinct_anonymous_enums_never_collide(
        self, ordinal_a: int, ordinal_b: int
    ) -> None:
        if ordinal_a == ordinal_b:
            return
        scope = (Namespace("ns"),)
        a = entity_id_for_enum(scope, "", anonymous_ordinal=ordinal_a)
        b = entity_id_for_enum(scope, "", anonymous_ordinal=ordinal_b)
        assert a != b

    def test_without_anonymous_ordinal_still_collides_as_before(self) -> None:
        # Documented, deliberate: anonymous_ordinal is opt-in (no wired
        # producer yet, mirroring this module's own scope boundary for
        # LocalToFunction/Anonymous before their producers existed) -- a
        # caller that omits it gets the pre-existing degenerate behavior,
        # not a silent, unrequested change.
        scope = (Namespace("ns"),)
        a = entity_id_for_type(scope, "")
        b = entity_id_for_type(scope, "")
        assert a == b

    def test_anonymous_ordinal_ignored_for_a_named_declaration(self) -> None:
        # Only meaningful when leaf_name is empty -- a named declaration
        # already disambiguates via leaf_name, so a caller that supplies
        # both must not get a spurious extra discriminator.
        scope = (Namespace("ns"),)
        a = entity_id_for_type(scope, "Widget", anonymous_ordinal=0)
        b = entity_id_for_type(scope, "Widget", anonymous_ordinal=1)
        assert a == b
        assert a == entity_id_for_type(scope, "Widget")


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
        plain = entity_id_for_function(scope, "f", is_const=False)
        const = entity_id_for_function(scope, "f", is_const=True)
        assert plain != const

    def test_volatile_vs_non_volatile_overload(self) -> None:
        scope = (Record("C"),)
        plain = entity_id_for_function(scope, "f", is_volatile=False)
        volatile = entity_id_for_function(scope, "f", is_volatile=True)
        assert plain != volatile

    def test_const_and_volatile_are_independent_dimensions(self) -> None:
        # is_const/is_volatile are two independent booleans, not an
        # order-dependent qualifier-token tuple -- "const volatile" and
        # "volatile const" spell the same member-cv qualification and must
        # produce one id regardless of which boolean is set "first" (there
        # is no first/second: this is the whole point of using two
        # booleans instead of a tuple of tokens; Codex review, PR #941).
        scope = (Record("C"),)
        both_a = entity_id_for_function(scope, "f", is_const=True, is_volatile=True)
        both_b = entity_id_for_function(scope, "f", is_volatile=True, is_const=True)
        assert both_a == both_b

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
        # A changed parameter list is a modification of the one function,
        # not a different overload, once a genuine mangled name is known --
        # the mangled name already disambiguates the declaration losslessly.
        # Uses a mangled-shaped name ("_Z1fv"), not a bare name equal to
        # leaf_name -- that degenerate case is exactly what a real
        # extern "C" producer reports (mangled == name is *not* a genuine
        # mangling per this module's own contract) and is covered
        # separately by TestFunctionOverloadDiscrimination's own
        # test_extern_c_ignores_param_types via is_extern_c, not by
        # mangled_name (CodeRabbit review, PR #941).
        scope = (Namespace("ns"),)
        a = entity_id_for_function(
            scope, "f", mangled_name="_Z1fv", param_types=("int",)
        )
        b = entity_id_for_function(
            scope, "f", mangled_name="_Z1fv", param_types=("double", "int")
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
        # name, param_types, is_const, and is_volatile -- only
        # ref_qualifier tells them apart, mirroring
        # resolve_function_identity's own dimension.
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
        # the plain param_types precedence already pinned by
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

    def test_extern_c_identity_is_independent_of_scope(self) -> None:
        # The other Codex-flagged gap: a header/DWARF observation of a
        # namespaced extern "C" function may supply a real ScopePath, while
        # an export-table-only snapshot of the identical binary symbol
        # knows only the bare exported name -- extern "C" linkage means the
        # symbol *is* that bare name at the ABI level, so no namespace is
        # even recoverable from the export table alone. The two must still
        # produce the same EntityId, mirroring resolve_symbol_identity's
        # own choice to base an extern-C identity on the raw export rather
        # than a qualified name for exactly this reason.
        namespaced = entity_id_for_function((Namespace("ns"),), "foo", is_extern_c=True)
        export_only = entity_id_for_function((), "foo", is_extern_c=True)
        assert namespaced == export_only
        assert namespaced.scope == ()

    def test_extern_c_scope_independence_does_not_erase_leaf_name(self) -> None:
        # Scope-independence must not go too far -- two different exported
        # extern "C" names still must not collide just because both were
        # observed with no scope.
        foo = entity_id_for_function((), "foo", is_extern_c=True)
        bar = entity_id_for_function((), "bar", is_extern_c=True)
        assert foo != bar

    def test_mangled_name_identity_is_also_independent_of_scope(self) -> None:
        # The Codex-flagged gap this corrects: a real mangled name already
        # fully and deterministically encodes scope, so folding a caller-
        # supplied scope on top only fragments identity across evidence
        # tiers that differ in whether they can supply one -- the same
        # mechanism the is_extern_c branch guards against, not a
        # different, harmless case (an earlier revision of this test and
        # the code's own docstring both wrongly assumed it was).
        namespaced = entity_id_for_function(
            (Namespace("ns"),), "f", mangled_name="_Z1fv"
        )
        no_scope = entity_id_for_function((), "f", mangled_name="_Z1fv")
        assert namespaced == no_scope
        assert namespaced.scope == ()

    def test_mangled_name_identity_is_independent_of_leaf_name(self) -> None:
        # The confirmed Codex-flagged gap: dumper_elf_fallback.py's
        # ELF-only path constructs Function(name=sym, mangled=sym) --
        # the raw exported symbol reused for *both* fields -- while a
        # header/DWARF observation of the identical symbol supplies the
        # real demangled short name for `name`. A header observation
        # (leaf_name="f") and an export-only observation
        # (leaf_name="_Z1fv", matching that fallback's real behavior)
        # of the same genuinely mangled symbol must produce one EntityId.
        demangled_leaf = entity_id_for_function((), "f", mangled_name="_Z1fv")
        raw_symbol_reused_as_leaf = entity_id_for_function(
            (), "_Z1fv", mangled_name="_Z1fv"
        )
        assert demangled_leaf == raw_symbol_reused_as_leaf
        assert demangled_leaf.leaf_name == ""

    def test_sig_branch_keeps_caller_supplied_scope(self) -> None:
        # Contrast with the mangled/extern-C branches above: a DWARF-only,
        # mangling-free, non-extern-"C" function has no authoritative,
        # scope-independent name to fall back on, so scope is exactly what
        # makes two same-named, same-signature sibling declarations in
        # different scopes distinct.
        sig_a = entity_id_for_function((Namespace("a"),), "f")
        sig_b = entity_id_for_function((Namespace("b"),), "f")
        assert sig_a != sig_b

    def test_param_types_canonicalized_across_producer_spellings(self) -> None:
        # CastXML's "char const*" and Clang's "char const *" spell an
        # otherwise-identical parameter type differently -- without
        # canonicalization the same declaration observed by the two
        # backends would get two different EntityIds.
        scope = (Namespace("ns"),)
        castxml_spelling = entity_id_for_function(
            scope, "f", param_types=("char const*",)
        )
        clang_spelling = entity_id_for_function(
            scope, "f", param_types=("char const *",)
        )
        assert castxml_spelling == clang_spelling

    def test_by_value_top_level_cv_does_not_distinguish_overloads(self) -> None:
        # The Codex-flagged gap: void f(int) and void f(const int) name the
        # same function per the C++ standard -- a top-level BY-VALUE
        # cv-qualifier is dropped from the function's own type for
        # linkage/mangling purposes, so these must not collide as two
        # overloads.
        scope = (Namespace("ns"),)
        plain = entity_id_for_function(scope, "f", param_types=("int",))
        cv_qualified = entity_id_for_function(scope, "f", param_types=("const int",))
        assert plain == cv_qualified

    def test_pointee_cv_still_distinguishes_overloads(self) -> None:
        # Contrast with the by-value case above: a POINTEE cv-qualifier on
        # a pointer/reference parameter is a genuine, standard-mandated
        # overload discriminator -- void f(char*) and void f(const char*)
        # are two simultaneously-declarable, independently-mangled
        # overloads, not one declaration. Collapsing them would silently
        # merge two distinct functions, reintroducing exactly the
        # sibling-overload-collision class this primitive exists to
        # prevent.
        scope = (Namespace("ns"),)
        mutable_ptr = entity_id_for_function(scope, "f", param_types=("char *",))
        const_ptr = entity_id_for_function(scope, "f", param_types=("const char *",))
        assert mutable_ptr != const_ptr

    def test_array_parameter_element_cv_still_distinguishes_overloads(self) -> None:
        # The Codex-flagged gap: a function PARAMETER's array type always
        # decays to a pointer (int[] -> int*), so a cv-qualifier on the
        # element type is pointee-level, exactly like an explicit pointer
        # -- void f(int[]) and void f(const int[]) are two distinct,
        # independently-mangled overloads. Neither spelling contains a
        # "*"/"&" sigil, so this must not be treated as a by-value type.
        scope = (Namespace("ns"),)
        mutable_array = entity_id_for_function(scope, "f", param_types=("int []",))
        const_array = entity_id_for_function(scope, "f", param_types=("const int []",))
        assert mutable_array != const_array

    def test_array_bound_does_not_distinguish_overloads(self) -> None:
        # Fresh Codex finding: int [], int [3], int [4], and int * are all
        # the identical adjusted parameter type -- the bound plays no part
        # in it at all -- so redeclarations spelled with different bounds
        # must produce ONE EntityId, not four.
        scope = (Namespace("ns"),)
        no_bound = entity_id_for_function(scope, "f", param_types=("int []",))
        bound_3 = entity_id_for_function(scope, "f", param_types=("int [3]",))
        bound_4 = entity_id_for_function(scope, "f", param_types=("int [4]",))
        plain_ptr = entity_id_for_function(scope, "f", param_types=("int *",))
        assert no_bound == bound_3 == bound_4 == plain_ptr

    def test_pointers_own_top_level_cv_does_not_distinguish_overloads(self) -> None:
        # Fresh Codex finding: a cv-qualifier trailing the pointer's own
        # outermost sigil qualifies the pointer value itself, not what it
        # points to -- void f(int *) and void f(int * const) name the
        # same function, the identical by-value-cv rule generalized to a
        # pointer parameter instead of a scalar one.
        scope = (Namespace("ns"),)
        plain = entity_id_for_function(scope, "f", param_types=("int *",))
        pointer_const = entity_id_for_function(scope, "f", param_types=("int * const",))
        assert plain == pointer_const

    def test_non_outermost_pointer_cv_still_distinguishes_overloads(self) -> None:
        # Contrast with the case above: a cv-qualifier on an INTERMEDIATE
        # pointer level (not the parameter's own outermost sigil) is
        # genuinely part of the pointee's type -- "pointer to a
        # const-qualified pointer to int" is not the same type as
        # "pointer to pointer to int".
        scope = (Namespace("ns"),)
        plain = entity_id_for_function(scope, "f", param_types=("int **",))
        inner_const = entity_id_for_function(scope, "f", param_types=("int * const *",))
        assert plain != inner_const


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

    def test_mangled_name_identity_is_independent_of_scope(self) -> None:
        # Same Codex-flagged gap as entity_id_for_function's mangled
        # branch: a genuine mangled name already fully encodes scope, so a
        # header/DWARF observation and an export-only observation of the
        # identical symbol must produce the same EntityId.
        namespaced = entity_id_for_variable((Namespace("ns"),), "v", mangled_name="_Zv")
        no_scope = entity_id_for_variable((), "v", mangled_name="_Zv")
        assert namespaced == no_scope
        assert namespaced.scope == ()

    def test_mangled_name_identity_is_independent_of_leaf_name(self) -> None:
        # Same confirmed Codex-flagged gap as entity_id_for_function's
        # mangled branch, for the same reason: dumper_elf_fallback.py's
        # ELF-only path reuses the raw exported symbol for both
        # Variable.name and Variable.mangled.
        demangled_leaf = entity_id_for_variable((), "v", mangled_name="_Zv")
        raw_symbol_reused_as_leaf = entity_id_for_variable(
            (), "_Zv", mangled_name="_Zv"
        )
        assert demangled_leaf == raw_symbol_reused_as_leaf
        assert demangled_leaf.leaf_name == ""

    def test_extern_c_variable_ignores_param_evidence_free_signature(self) -> None:
        # The other Codex-flagged gap: an extern "C" variable caller
        # follows mangled_name's own contract and passes None for it, but
        # entity_id_for_variable had no equivalent linkage signal at all --
        # is_extern_c closes that the same way it does for functions.
        scope = (Namespace("ns"),)
        a = entity_id_for_variable(scope, "v", is_extern_c=True)
        b = entity_id_for_variable((), "v", is_extern_c=True)
        assert a == b
        assert a.scope == ()

    def test_extern_c_variable_tag_never_collides_with_degenerate_case(self) -> None:
        # ("extern_c",) and the bare () degenerate-case tag must occupy
        # disjoint regions of extra's value space.
        scope = (Namespace("ns"),)
        extern_c = entity_id_for_variable(scope, "v", is_extern_c=True)
        degenerate = entity_id_for_variable(scope, "v")
        assert extern_c != degenerate

    def test_mangled_name_wins_over_is_extern_c_for_variables(self) -> None:
        a = entity_id_for_variable(
            (Namespace("ns"),), "v", mangled_name="_Zv", is_extern_c=True
        )
        b = entity_id_for_variable((), "v", mangled_name="_Zv")
        assert a == b


# --------------------------------------------------------------------------
# EntityId.key -- the first real consumer read (ADR-063 Phase 2, closing
# (c2)'s finding_identity.resolve_change_identity consumer step)
# --------------------------------------------------------------------------


class TestEntityIdKey:
    """``EntityId.key``'s own contract: a pure function of the object's
    identity fields, collision-safe the same way ``storage.entity_ids.
    EntityId.key`` is audited to be (this module may not import that one,
    so ``_packed``/``_segment_key`` are a local duplicate of the algorithm,
    not the object)."""

    @given(name_a=_names, name_b=_names)
    def test_distinct_scopes_never_collide(self, name_a: str, name_b: str) -> None:
        if name_a == name_b:
            return
        a = entity_id_for_type((Namespace(name_a),), "Widget")
        b = entity_id_for_type((Namespace(name_b),), "Widget")
        assert a.key != b.key

    def test_record_nested_in_record_vs_namespace(self) -> None:
        # Mirrors TestDistinctScopesNeverCollide's own object-equality case,
        # through .key specifically -- both scopes render to the identical
        # "A::B" qualified-name string, so a naive string-based key would
        # collide them.
        in_record = entity_id_for_type((Record("A"), Record("B")), "C")
        in_namespace = entity_id_for_type((Namespace("A"), Namespace("B")), "C")
        assert in_record.key != in_namespace.key

    def test_sibling_segment_variants_never_collide(self) -> None:
        # Same bare name, different segment kind at the same position --
        # _segment_key's own per-variant tag is what this pins.
        keys = {
            entity_id_for_type((Namespace("a"),), "X").key,
            entity_id_for_type((Record("a"),), "X").key,
            entity_id_for_type((InlineNamespace("a"),), "X").key,
        }
        assert len(keys) == 3

    def test_record_access_does_not_change_key(self) -> None:
        # access is compare=False (payload, not identity) -- .key must
        # honor that exclusion or an alias join could miss a real match
        # between two snapshots that only differ in a member's access.
        public = entity_id_for_type((Record("A", access="public"),), "B")
        private = entity_id_for_type((Record("A", access="private"),), "B")
        assert public == private
        assert public.key == private.key

    def test_extra_tuple_boundary_does_not_collide_with_leaf_name(self) -> None:
        # Adversarial case in the same spirit as the storage module's own
        # audited _packed collision test: two EntityIds whose (leaf_name,
        # extra) split differently but might concatenate to the same raw
        # text must still produce distinct keys.
        a = entity_id_for_function((), "f", mangled_name="ab")
        b = entity_id_for_function((), "fa", mangled_name="b")
        assert a.key != b.key

    def test_local_to_function_recursion_terminates_and_differs(self) -> None:
        owner_f = entity_id_for_function((), "f", param_types=("int",))
        owner_g = entity_id_for_function((), "g", param_types=("int",))
        a = entity_id_for_type((LocalToFunction(owner_f, 0),), "A")
        b = entity_id_for_type((LocalToFunction(owner_g, 0),), "A")
        c = entity_id_for_type((LocalToFunction(owner_f, 1),), "A")
        assert a.key != b.key  # different owning function
        assert a.key != c.key  # same owner, different sibling block

    @given(name=_names, kind=_kinds, ordinal=_ordinals)
    def test_key_never_raises(self, name: str, kind: str, ordinal: int) -> None:
        scope = (Namespace(name), Anonymous(kind, ordinal))
        key = entity_id_for_type(scope, name).key
        assert isinstance(key, str) and key


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
