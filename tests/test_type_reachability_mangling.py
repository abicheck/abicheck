# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Nikolay Petrov
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

"""Status-review item 3 (continued): mangled-name-derived reachability --
namespace-suffix spellings, Itanium/Mach-O/MSVC mangled-name scope recovery,
and the record/typedef ambiguity guards those spellings feed into.

Split out of test_type_reachability.py (AI-readiness file-size cap) -- see
that file for the base spelling-index/typedef-resolution test coverage this
one builds on.
"""

from __future__ import annotations

from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    TypeField,
    Variable,
)
from abicheck.type_reachability import (
    _bare_type_name,
    _namespace_suffix_spellings,
    _typedef_spelling_targets,
    directly_referenced_stdlib_types,
)


def _fn(
    name: str,
    return_type: str = "void",
    params: list | None = None,
) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=return_type,
        params=params or [],
    )


class TestNamespaceSuffixSpellings:
    def test_full_identity_and_bare_leaf_are_both_present(self) -> None:
        suffixes = _namespace_suffix_spellings("api::Outer::Inner")
        assert suffixes == ["api::Outer::Inner", "Outer::Inner", "Inner"]

    def test_no_depth_zero_separator_returns_single_element_list(self) -> None:
        assert _namespace_suffix_spellings("Inner") == ["Inner"]

    def test_template_argument_colon_colon_is_not_a_split_point(self) -> None:
        assert _namespace_suffix_spellings("api::Wrapper<dep::Tag>") == [
            "api::Wrapper<dep::Tag>",
            "Wrapper<dep::Tag>",
        ]

    def test_bare_type_name_matches_the_last_suffix(self) -> None:
        assert _bare_type_name("api::Outer::Inner") == "Inner"

    def test_partially_qualified_nested_record_spelling_resolves(self) -> None:
        """Codex review, fresh evidence, confirmed empirically via
        `clang -ast-dump` on `namespace api { struct Outer { struct Inner
        {}; }; Outer::Inner g(); }`: direct-clang spells that function's
        return type as exactly "Outer::Inner" -- dropping the enclosing
        namespace ("api::") while keeping the class-nesting qualifier
        ("Outer::"). Neither the full identity nor the fully-bare leaf
        alone would match this partially-qualified spelling."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("g", return_type="Outer::Inner")],
            types=[
                RecordType(
                    name="Inner",
                    kind="class",
                    qualified_name="api::Outer::Inner",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_ambiguous_partial_suffix_between_distinct_records_is_dropped(self) -> None:
        """Guard: a partial suffix (not just the fully-bare leaf) shared by
        two distinct records must still be dropped as ambiguous."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("g", return_type="Outer::Inner")],
            types=[
                RecordType(
                    name="Inner", kind="class", qualified_name="api::Outer::Inner"
                ),
                RecordType(
                    name="Inner2",
                    kind="class",
                    qualified_name="other::Outer::Inner",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()


class TestStdlibScopeRecoveredFromMangledName:
    def test_bare_named_stdlib_variable_stays_filtered(self) -> None:
        """Codex review, fresh evidence: a namespace-scope variable's own
        `name` can be bare ("touch") while its mangled name reveals it is
        actually inside `std::` -- without recovering the qualified name
        from the mangled symbol, this bypassed the existing
        var.name.startswith(...) guard and marked its stdlib type
        directly referenced."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            variables=[
                Variable(name="touch", mangled="_ZN3std5touchE", type="std::string")
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_bare_named_genuine_public_variable_still_seeds_its_type(self) -> None:
        """Guard against over-correcting: a bare-named variable with no
        recoverable stdlib scope (or none at all) must still be scanned
        normally."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            variables=[
                Variable(
                    name="global_str",
                    mangled="_ZN3Foo10global_strE",
                    type="std::string",
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_bare_named_stdlib_free_function_stays_filtered(self) -> None:
        """Codex review, fresh evidence (same root cause as the variable
        case above, verified to also apply to functions' own return/param
        scan): a free function directly inside namespace std, recorded
        under a bare Function.name by CastXML/direct-clang, must not have
        its return type scanned as if it were part of this library's own
        public API -- it's the standard library's own internals leaking
        into the binary's symbol table."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="touch",
                    mangled="_ZN3std5touchEv",
                    return_type="std::string",
                    params=[],
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_bare_named_genuine_public_free_function_still_scans_return_type(
        self,
    ) -> None:
        """Guard against over-correcting: a bare-named free function with
        no recoverable stdlib scope must still have its return type
        scanned normally."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="touch",
                    mangled="_Z5touchv",
                    return_type="std::string",
                    params=[],
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})


class TestOwnerSeedExactIdentityMatchOnly:
    def test_namespace_function_owner_does_not_match_unrelated_record_bare_suffix(
        self,
    ) -> None:
        """Codex review, fresh evidence: owner_class_of derives its result
        by chopping the trailing "::"-component off any already-qualified
        name, with no way to tell whether what remains is a class or just
        an enclosing namespace -- a public namespace function api::run()
        makes owner_class_of return the bare namespace fragment "api",
        which must not be matched against an unrelated internal record's
        own bare-suffix spelling (other::api) just because they coincide
        textually."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="api::run",
                    mangled="_ZN3api3runEv",
                    return_type="void",
                    params=[],
                )
            ],
            types=[
                RecordType(
                    name="api",
                    kind="class",
                    qualified_name="other::api",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_genuine_class_owner_still_matches_exactly(self) -> None:
        """Guard against over-correcting: a real class owner (an exact
        identity match) must still be seeded normally."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="api::Foo::run",
                    mangled="_ZN3api3Foo3runEv",
                    return_type="void",
                    params=[],
                )
            ],
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    qualified_name="api::Foo",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})


class TestItaniumStdSubstitution:
    def test_standalone_st_substitution_recovers_std_scope(self) -> None:
        """Codex review, fresh evidence: GCC/Clang use the mandatory
        2-character "St" substitution for the first occurrence of the
        std:: scope prefix. namespace std { void touch() {} } compiles to
        the bare _ZSt5touchv (confirmed against a real GCC build) -- no
        "N...E" nested-name wrapper at all, which the parser previously
        did not recognize as scoped under std at all."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="touch",
                    mangled="_ZSt5touchv",
                    return_type="std::string",
                    params=[],
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_standalone_st_substitution_variable(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            variables=[Variable(name="ping", mangled="_ZSt4ping", type="std::string")],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_nested_st_substitution_recovers_std_scope(self) -> None:
        """namespace std { namespace detail { void foo() {} } } compiles
        to _ZNSt6detail3fooEv (confirmed against a real GCC build) -- "St"
        right after the "N" nested-name marker, with further components
        following before "E"."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="foo",
                    mangled="_ZNSt6detail3fooEv",
                    return_type="std::string",
                    params=[],
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()


class TestBareIdentityCollisionWithDerivedSuffix:
    def test_bare_record_identity_collision_with_qualified_records_suffix(
        self,
    ) -> None:
        """Codex review, fresh evidence: when records have identities
        "Inner" and "api::Inner", the derived-suffix collection previously
        saw only one candidate contributor for the derived suffix "Inner"
        (from api::Inner) and merged it straight into the *existing*
        full-identity entry for the unrelated global Inner record, so a
        public signature spelling the global type as bare "Inner" also
        queued the unrelated api::Inner and its std::string field."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Inner")],
            types=[
                RecordType(name="Inner", kind="class"),
                RecordType(
                    name="Inner",
                    kind="class",
                    qualified_name="api::Inner",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_typedef_exact_key_disagreeing_with_derived_suffix_is_dropped(
        self,
    ) -> None:
        """Codex review, fresh evidence: when snapshot.typedefs holds both
        a global "Alias" -> "std::..." and a qualified "api::Alias" ->
        "Foo", a declaration inside api can legitimately spell the latter
        as bare "Alias" too -- the bare spelling is genuinely ambiguous
        between the two, and silently preferring the pre-existing exact
        key (as an earlier version did) could resolve it to the wrong
        one. Both must be treated as an ambiguous collision and dropped."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Alias")],
            types=[
                RecordType(name="Foo", kind="class", qualified_name="api::Foo"),
                RecordType(name="std::string", kind="class"),
            ],
        )
        snap.typedefs = {"Alias": "std::string", "api::Alias": "api::Foo"}
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_typedef_exact_key_agreeing_with_derived_suffix_is_kept(self) -> None:
        """Guard against over-correcting: when the exact key and a derived
        suffix happen to agree on the same target, there is no real
        ambiguity in the outcome and the spelling must still resolve."""
        index = _typedef_spelling_targets(
            {"Alias": "Foo", "api::Alias": "Foo"},
            frozenset(),
        )
        assert index["Alias"] == "Foo"

    def test_typedef_exact_key_colliding_with_non_stdlib_record_is_dropped(
        self,
    ) -> None:
        """Codex review, fresh evidence: direct-clang's own typedef-scope
        loss can make an exact typedef key (e.g. "Alias", really
        "api::Alias" with the namespace dropped) collide with an unrelated
        non-stdlib record's own bare signature spelling (a global `struct
        Alias {};`). Registering the exact key unconditionally -- unlike
        every derived/stripped candidate, which already goes through this
        same collision guard -- let a public function taking that unrelated
        record by value resolve through the typedef instead, incorrectly
        marking the typedef's stdlib target reachable."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("use_alias", return_type="Alias")],
            types=[
                RecordType(name="Alias", kind="class"),
                RecordType(name="std::string", kind="class"),
            ],
        )
        snap.typedefs = {"Alias": "std::string"}
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_typedef_exact_key_without_record_collision_still_resolves(
        self,
    ) -> None:
        """Guard against over-correcting: when the exact typedef key does
        not collide with any non-stdlib record's own spelling, resolution
        through the typedef must still work as before."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("use_alias", return_type="NoCollision")],
            types=[RecordType(name="std::string", kind="class")],
        )
        snap.typedefs = {"NoCollision": "std::string"}
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_within_namespace_bare_signature_does_not_misattribute_to_global_record(
        self,
    ) -> None:
        """Codex review, fresh evidence: direct-clang's own "drop the
        enclosing namespace" convention means a signature declared inside
        namespace api can spell api::Inner bare as "Inner" too (not just
        the earlier Outer::Inner-shaped partial qualification) -- merely
        refusing to merge api::Inner's candidates into the pre-existing
        record_index["Inner"] entry isn't enough, since that entry still
        pointed at the unrelated global Inner record. A public api::f()
        returning (bare-spelled) api::Inner must not have its std::
        field misattributed to the unrelated global Inner's own field."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="api::f",
                    mangled="_ZN3api1fEv",
                    return_type="Inner",
                    params=[],
                )
            ],
            types=[
                RecordType(
                    name="Inner",
                    kind="class",
                    fields=[TypeField(name="v", type="std::vector<int>")],
                ),
                RecordType(name="Inner", kind="class", qualified_name="api::Inner"),
                RecordType(name="std::vector<int>", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()


class TestMachODoubleUnderscorePrefixRecognized:
    def test_bare_named_stdlib_function_stays_filtered_with_macho_mangling(
        self,
    ) -> None:
        """Codex review, fresh evidence: on macOS, clang's mangledName
        carries an extra platform leading underscore ("__ZSt5touchv",
        confirmed via dumper_clang.py's own _visibility() docstring),
        which itanium_qualified_name's bare "_Z" check previously
        rejected outright -- silently disabling the mangled-scope-
        recovery guard for every symbol on that platform."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="touch",
                    mangled="__ZSt5touchv",
                    return_type="std::string",
                    params=[],
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_macho_owner_seeding_still_works_for_a_genuine_class(self) -> None:
        """Guard against over-correcting: a Mach-O-mangled method on a
        genuine non-stdlib owner must still seed that owner normally."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="Foo::touch",
                    mangled="__ZN3Foo5touchEv",
                    return_type="void",
                    params=[],
                )
            ],
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})


class TestMsvcOwnerSeedingRecognized:
    """Codex review, fresh evidence: a clang-cl (``--target=*-windows-msvc``)
    direct-clang snapshot records a method's bare AST name (the same
    unqualified-leaf convention as CastXML) while ``mangledName`` is MSVC-
    mangled, not Itanium -- confirmed via real ``clang --target=x86_64-pc-
    windows-msvc -Xclang -ast-dump=json`` output (``?run@Foo@@QEAAXXZ`` for
    ``Foo::run()``). ``owner_class_of``'s Itanium-only mangled-name fallback
    left this owner unresolved, so an embedded stdlib record's fields were
    never walked when the method was the only public root for its owner."""

    def test_bare_named_method_seeds_owner_from_msvc_mangling(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="run",
                    mangled="?run@Foo@@QEAAXXZ",
                    return_type="void",
                    params=[],
                )
            ],
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_bare_named_stdlib_function_stays_filtered_with_msvc_mangling(
        self,
    ) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="touch",
                    mangled="?touch@std@@YAXXZ",
                    return_type="std::string",
                    params=[],
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_msvc_constructor_does_not_seed_owner(self) -> None:
        """A constructor's MSVC mangling is not modelled by
        ``msvc_scope_components`` (special-member operator code, not a
        plain leaf/scope split) -- must fall back to None, not crash or
        mis-seed an unrelated owner."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="Foo",
                    mangled="??0Foo@@QEAA@XZ",
                    return_type="void",
                    params=[],
                )
            ],
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()
