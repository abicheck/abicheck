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

"""Status-review item 3: direct vs transitive stdlib type reachability."""

from __future__ import annotations

from abicheck.diff_cxx_rules import owner_class_of
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.type_reachability import (
    _bare_type_name,
    _compile_spelling_pattern,
    _non_stdlib_signature_spellings,
    _stripped_signature_spelling,
    _typedef_spelling_targets,
    directly_referenced_stdlib_types,
    type_string_references_name,
)


def _fn(
    name: str,
    return_type: str = "void",
    params: list[Param] | None = None,
    visibility: Visibility = Visibility.PUBLIC,
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN,
) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=return_type,
        params=params or [],
        visibility=visibility,
        origin=origin,
    )


class TestTypeStringReferencesName:
    def test_matches_bare_reference(self) -> None:
        assert type_string_references_name("const std::string &", "std::string")

    def test_does_not_match_longer_name_prefix(self) -> None:
        assert not type_string_references_name("std::stringstream", "std::string")

    def test_does_not_match_when_preceded_by_identifier_char(self) -> None:
        assert not type_string_references_name("xstd::string", "std::string")

    def test_matches_inside_template_args(self) -> None:
        assert type_string_references_name("std::vector<std::string>", "std::string")

    def test_no_match_at_all(self) -> None:
        assert not type_string_references_name("int", "std::string")


class TestDirectlyReferencedStdlibTypes:
    def test_empty_snapshot_has_no_stdlib_types(self) -> None:
        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_no_stdlib_types_in_snapshot_returns_empty(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="int")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_stdlib_type_used_as_public_param_is_directly_referenced(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "foo",
                    params=[Param(name="s", type="std::string")],
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_stdlib_type_used_as_public_return_type_is_directly_referenced(
        self,
    ) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="std::string")],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_stdlib_type_used_as_public_field_is_directly_referenced(self) -> None:
        """MyPublicType must itself be reachable from a public function
        before its field counts as a reachability root (Codex review: a
        record's fields are only consulted once the record itself is
        confirmed reachable, not for every record in the snapshot)."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="MyPublicType")],
            types=[
                RecordType(
                    name="MyPublicType",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_unreachable_record_field_is_not_a_reachability_root(self) -> None:
        """Codex review, fresh evidence: the previous version scanned every
        non-stdlib record's fields unconditionally -- a purely internal
        record that nothing public actually reaches (the default
        ScopeOrigin.UNKNOWN a DWARF-only snapshot retains for such a type)
        must not make its field's stdlib type look directly referenced."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api")],
            types=[
                RecordType(
                    name="InternalCache",
                    kind="class",
                    fields=[TypeField(name="v", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_transitively_reachable_record_field_is_a_reachability_root(
        self,
    ) -> None:
        """A record reached only *transitively* -- via another already-
        reachable record's field, not directly from a function signature --
        must still have its own fields walked."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Outer")],
            types=[
                RecordType(
                    name="Outer",
                    kind="class",
                    fields=[TypeField(name="inner", type="Inner")],
                ),
                RecordType(
                    name="Inner",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_stdlib_type_only_reachable_via_internal_instantiation_is_not_referenced(
        self,
    ) -> None:
        """A stdlib type like std::string::_Alloc_hider that never appears in
        any non-stdlib function signature or field type -- only nested
        inside another stdlib type's own template internals -- is NOT
        directly referenced (the "transitive/implementation detail" case
        the status review distinguishes from "direct public signature")."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="int")],
            types=[
                RecordType(name="std::string", kind="class"),
                RecordType(
                    name="std::string::_Alloc_hider",
                    kind="class",
                    fields=[TypeField(name="_M_p", type="char *")],
                ),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_stdlib_functions_are_never_treated_as_the_referencing_side(self) -> None:
        """A function that is ITSELF stdlib-namespaced (e.g. libstdc++'s own
        std::foo) must not count as a "non-stdlib declaration" referencing
        another stdlib type -- that's the standard library's own internals
        referencing each other, not the inspected library's public surface."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "std::foo",
                    params=[Param(name="s", type="std::string")],
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_mixed_direct_and_transitive_types_are_distinguished(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn("foo", params=[Param(name="s", type="const std::string &")])
            ],
            types=[
                RecordType(name="std::string", kind="class"),
                RecordType(name="std::_Rb_tree_node_base", kind="struct"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_scan_short_circuits_once_everything_is_found(self) -> None:
        """Once every stdlib name has been matched, later scan calls (a
        second param, a later function, a later type's fields) must not
        keep re-scanning -- covers the early-exit path when a single
        return type mentions every remaining stdlib name at once."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "foo",
                    return_type="std::pair<std::string, std::string>",
                    params=[Param(name="v", type="std::string")],
                )
            ],
            types=[
                RecordType(name="std::pair<std::string, std::string>", kind="class")
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {"std::pair<std::string, std::string>"}
        )

    def test_field_scan_continues_across_multiple_non_stdlib_types(self) -> None:
        """A stdlib type not found in any function signature, and not in
        the first *reachable* non-stdlib type's fields, must still be found
        via a *later* reachable non-stdlib type's fields -- covers the
        worklist actually processing more than one queued record."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn("first", return_type="FirstPublicType"),
                _fn("second", return_type="SecondPublicType"),
            ],
            types=[
                RecordType(
                    name="FirstPublicType",
                    kind="class",
                    fields=[TypeField(name="n", type="int")],
                ),
                RecordType(
                    name="SecondPublicType",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_hidden_function_signature_does_not_count_as_direct_reference(
        self,
    ) -> None:
        """Codex review: a Visibility.HIDDEN function is retained in real
        snapshots for cross-reference purposes but is not part of the
        public ABI surface -- a stdlib type mentioned only in its
        signature must not be treated as directly referenced, or wiring
        this helper into a live detector would turn an internal
        implementation signature into a stdlib ABI dependency."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "internal_helper",
                    params=[Param(name="s", type="std::string")],
                    visibility=Visibility.HIDDEN,
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_private_header_origin_does_not_count_as_direct_reference(
        self,
    ) -> None:
        """Codex review, fresh evidence: public-header scoping can retain a
        function whose visibility is still PUBLIC but whose origin is
        PRIVATE_HEADER -- linkage and origin are independent axes, so this
        must be excluded even though the hidden-visibility fix alone does
        not catch it."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "internal_helper",
                    params=[Param(name="s", type="std::string")],
                    origin=ScopeOrigin.PRIVATE_HEADER,
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_system_header_origin_does_not_count_as_direct_reference(
        self,
    ) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "libc_wrapper",
                    return_type="std::string",
                    origin=ScopeOrigin.SYSTEM_HEADER,
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_generated_header_origin_does_not_count_as_direct_reference(
        self,
    ) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "moc_generated_fn",
                    params=[Param(name="s", type="std::string")],
                    origin=ScopeOrigin.GENERATED,
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_private_header_origin_record_field_does_not_count_as_direct_reference(
        self,
    ) -> None:
        """Codex review, fresh evidence beyond the function-origin fix:
        RecordType carries the same origin provenance axis, and the
        separate record-field scan loop bypassed the origin check
        entirely -- a non-stdlib record retained from a private header
        must not make its field types count as public reachability
        roots either. The record is made reachable via a public function
        (matching the reachability-closure fix) so this test isolates the
        origin check specifically, rather than passing vacuously because
        nothing reaches the record at all."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="InternalImplDetail")],
            types=[
                RecordType(
                    name="InternalImplDetail",
                    kind="struct",
                    fields=[TypeField(name="s", type="std::string")],
                    origin=ScopeOrigin.PRIVATE_HEADER,
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_system_header_origin_record_field_does_not_count_as_direct_reference(
        self,
    ) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="LibcWrapperDetail")],
            types=[
                RecordType(
                    name="LibcWrapperDetail",
                    kind="struct",
                    fields=[TypeField(name="s", type="std::string")],
                    origin=ScopeOrigin.SYSTEM_HEADER,
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_public_header_origin_record_field_still_counts_as_direct_reference(
        self,
    ) -> None:
        """Guard against over-excluding the record-field path too."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="MyPublicType")],
            types=[
                RecordType(
                    name="MyPublicType",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_public_header_origin_still_counts_as_direct_reference(self) -> None:
        """Guard against over-excluding: PUBLIC_HEADER (and the UNKNOWN
        default used when no --public-header set is supplied) must still be
        treated as a valid reachability root."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "foo",
                    params=[Param(name="s", type="std::string")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_elf_only_function_signature_does_not_count_as_direct_reference(
        self,
    ) -> None:
        """Same reasoning as the hidden-function case: ELF_ONLY means
        exported-but-undeclared-in-headers, not part of the public header
        API surface this helper models."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "undeclared_export",
                    return_type="std::string",
                    visibility=Visibility.ELF_ONLY,
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_castxml_style_bare_name_with_separate_qualified_name_is_detected(
        self,
    ) -> None:
        """Codex review, fresh evidence: castxml/direct-clang store the bare
        leaf in RecordType.name and the "std::"-prefixed spelling separately
        in qualified_name (model.py:384-392, dumper_clang.py:865-878) --
        matching only against `t.name` (as the pre-fix code did) never finds
        a real castxml/clang-produced stdlib record at all, since `name`
        alone never carries the "std::" prefix for those two backends.
        Reproduces the real shape empirically confirmed via `abicheck dump`
        on a compiled std::vector<int> parameter: Function.return_type/
        Param.type spell it bare ("vector<int, std::allocator<int> >"),
        matching RecordType.name, not RecordType.qualified_name."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "foo",
                    params=[Param(name="v", type="vector<int, std::allocator<int> >")],
                )
            ],
            types=[
                RecordType(
                    name="vector<int, std::allocator<int> >",
                    kind="class",
                    qualified_name="std::vector<int, std::allocator<int> >",
                )
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {"std::vector<int, std::allocator<int> >"}
        )

    def test_dwarf_style_prequalified_name_matches_bare_signature_spelling(
        self,
    ) -> None:
        """Codex review, fresh evidence: DWARF has no separate
        qualified_name field and instead bakes the namespace straight into
        `name` (dwarf_snapshot.py:606,725), so `name` is already
        "std::vector<...>" with qualified_name=None -- but
        Function.return_type/Param.type are still spelled bare (empirically
        confirmed via a real `abicheck dump` DWARF-only snapshot). The
        prefix-stripped fallback must connect the two."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "bar",
                    return_type="vector<int, std::allocator<int> >",
                )
            ],
            types=[
                RecordType(
                    name="std::vector<int, std::allocator<int> >",
                    kind="class",
                )
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {"std::vector<int, std::allocator<int> >"}
        )

    def test_stripped_spelling_colliding_with_unrelated_user_type_is_not_attributed(
        self,
    ) -> None:
        """Codex review, fresh evidence: a library can define its own public
        type whose bare spelling happens to collide with a stdlib
        candidate's namespace-prefix-stripped fallback spelling. A signature
        naming the unrelated user type must NOT be misread as a direct
        stdlib reference -- silently missing the stdlib candidate here is
        far safer than wrongly attributing an unrelated type's own layout
        churn to it."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "foo",
                    params=[Param(name="v", type="vector<int, std::allocator<int> >")],
                )
            ],
            types=[
                # An unrelated, genuinely non-stdlib user type that happens
                # to share the exact bare spelling the stdlib candidate
                # below reduces to after stripping "std::".
                RecordType(name="vector<int, std::allocator<int> >", kind="class"),
                RecordType(
                    name="vector<int, std::allocator<int> >",
                    kind="class",
                    qualified_name="std::vector<int, std::allocator<int> >",
                ),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_stripped_spelling_without_collision_still_matches(self) -> None:
        """Guard against over-correcting: when there is no colliding
        non-stdlib type, the stripped-spelling fallback must still work
        (this is the same scenario as the castxml-style test above, kept
        here as a direct sibling of the collision-guard test)."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "foo",
                    params=[Param(name="v", type="vector<int, std::allocator<int> >")],
                )
            ],
            types=[
                RecordType(
                    name="vector<int, std::allocator<int> >",
                    kind="class",
                    qualified_name="std::vector<int, std::allocator<int> >",
                )
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {"std::vector<int, std::allocator<int> >"}
        )

    def test_libcxx_inline_namespace_stripped_to_match_bare_signature(self) -> None:
        """Codex review, fresh evidence: libc++ wraps the whole standard
        library in an inline namespace (std::__1::) invisible to real C++
        code but present in the debug-info-derived qualified name --
        stripping only "std::" leaves "__1::vector<int>", which still can't
        match the bare backend spelling "vector<int>". The inline-namespace
        marker must be stripped too."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", params=[Param(name="v", type="vector<int>")])],
            types=[
                RecordType(
                    name="vector<int>",
                    kind="class",
                    qualified_name="std::__1::vector<int>",
                )
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {"std::__1::vector<int>"}
        )

    def test_android_libcxx_ndk_inline_namespace_stripped(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", params=[Param(name="v", type="vector<int>")])],
            types=[
                RecordType(
                    name="vector<int>",
                    kind="class",
                    qualified_name="std::__ndk1::vector<int>",
                )
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {"std::__ndk1::vector<int>"}
        )

    def test_many_unreferenced_stdlib_candidates_scan_efficiently(self) -> None:
        """Codex review, fresh evidence: the pre-fix per-candidate substring
        scan was O(candidates x declarations) -- a synthetic snapshot with
        1,000 functions and 1,000 unreferenced stdlib records took over a
        second in a single call before the single-pass regex rewrite. This
        is a correctness-adjacent regression guard, not a micro-benchmark:
        it asserts the call completes well under a generous ceiling rather
        than pinning an exact duration (flake-prone on shared CI runners)."""
        import time

        functions = [
            _fn(f"fn{i}", params=[Param(name="a", type="int")]) for i in range(1000)
        ]
        types = [
            RecordType(
                name=f"vector<T{i}, std::allocator<T{i}> >",
                kind="class",
                qualified_name=f"std::vector<T{i}, std::allocator<T{i}> >",
            )
            for i in range(1000)
        ]
        snap = AbiSnapshot(
            library="libfoo.so", version="1.0", functions=functions, types=types
        )
        start = time.monotonic()
        result = directly_referenced_stdlib_types(snap)
        elapsed = time.monotonic() - start
        assert result == frozenset()
        assert elapsed < 5.0


class TestStrippedSignatureSpelling:
    def test_no_stdlib_prefix_returns_none(self) -> None:
        assert _stripped_signature_spelling("MyNamespace::Widget") is None

    def test_plain_std_prefix_stripped(self) -> None:
        assert _stripped_signature_spelling("std::vector<int>") == "vector<int>"

    def test_libcxx_inline_namespace_stripped(self) -> None:
        assert _stripped_signature_spelling("std::__1::vector<int>") == "vector<int>"


class TestCompileSpellingPattern:
    def test_empty_spellings_returns_none(self) -> None:
        assert _compile_spelling_pattern([]) is None

    def test_nonempty_spellings_compiles_a_pattern(self) -> None:
        pattern = _compile_spelling_pattern(["std::string"])
        assert pattern is not None
        assert pattern.search("const std::string &")


class TestDirectlyReferencedStdlibTypesEdgeCases:
    def test_empty_param_type_string_is_skipped(self) -> None:
        """Covers _scan's empty-string guard directly -- an empty type
        string (e.g. a malformed/partial snapshot) must not be scanned."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "foo",
                    params=[
                        Param(name="unused", type=""),
                        Param(name="s", type="std::string"),
                    ],
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_scan_breaks_once_everything_found_before_later_functions(self) -> None:
        """Once every stdlib candidate is found, later functions in
        iteration order must not be scanned at all -- covers the top-of-loop
        early-exit break."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn("first", params=[Param(name="s", type="std::string")]),
                _fn("second", params=[Param(name="s", type="std::string")]),
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_record_reached_twice_is_not_requeued(self) -> None:
        """A non-stdlib record reached via two independent paths (directly
        from a function signature, and as a field of another reachable
        record) must only be queued once -- covers the "already reached,
        skip" branch."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn("first", return_type="Shared"),
                _fn("second", return_type="Outer"),
            ],
            types=[
                RecordType(
                    name="Shared",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(
                    name="Outer",
                    kind="class",
                    fields=[TypeField(name="shared", type="Shared")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})


class TestOwnerClassSeedingAndAmbiguousAliases:
    def test_public_member_function_seeds_its_owner_class(self) -> None:
        """Codex review, fresh evidence: a public method like void
        Foo::run() never repeats "Foo" in its own return/parameter types,
        so Foo itself must be seeded as reachable via its owner class --
        otherwise a genuine layout break in one of Foo's fields would be
        silently missed."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="Foo::run",
                    mangled="_ZN3Foo3runEv",
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

    def test_owner_class_of_conversion_operator_with_qualified_target(self) -> None:
        """Codex review, fresh evidence: a real compiled+demangled
        conversion operator to a namespace-qualified type renders as
        "Foo::operator ns::Bar" -- naively splitting at the *last* "::"
        would treat the target type's own qualification as the owner
        boundary, producing "Foo::operator ns" instead of "Foo"."""
        fn = Function(
            name="Foo::operator ns::Bar",
            mangled="_ZNK3FoocvN2ns3BarEEv",
            return_type="ns::Bar",
            params=[],
        )
        assert owner_class_of(fn) == "Foo"

    def test_owner_class_of_conversion_operator_with_nested_owner(self) -> None:
        """Guard against over-correcting: the owner itself can also be
        namespace-nested, and the fix must still find the right boundary
        (the "::" immediately before the "operator" keyword)."""
        fn = Function(
            name="ns::Foo::operator ns2::Bar",
            mangled="_ZNK2ns3FoocvN3ns23BarEEv",
            return_type="ns2::Bar",
            params=[],
        )
        assert owner_class_of(fn) == "ns::Foo"

    def test_public_conversion_operator_to_qualified_type_seeds_its_owner_class(
        self,
    ) -> None:
        """End-to-end: a public conversion operator whose own target type
        is namespace-qualified must still seed its owner class as
        reachable, so a genuine layout break in one of the owner's fields
        is not silently missed."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="Foo::operator ns::Bar",
                    mangled="_ZNK3FoocvN2ns3BarEEv",
                    return_type="ns::Bar",
                    params=[],
                )
            ],
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="ns::Bar", kind="class"),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_free_function_has_no_owner_to_seed(self) -> None:
        """A free function's owner_class_of is None -- must not error and
        must not spuriously seed anything."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("free_function")],
            types=[
                RecordType(
                    name="Unrelated",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_ambiguous_bare_alias_between_distinct_records_is_not_queued(
        self,
    ) -> None:
        """Codex review, fresh evidence: two distinct non-stdlib records
        (api::Inner and detail::Inner) both reducing to the bare alias
        "Inner" must not let a signature naming one of them wrongly queue
        the other -- unrelated implementation-only churn (here, the
        detail::Inner sibling's std::string field) must not be reported as
        publicly reachable just because of a name collision."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Inner")],
            types=[
                RecordType(
                    name="Inner",
                    kind="class",
                    qualified_name="api::Inner",
                    fields=[TypeField(name="n", type="int")],
                ),
                RecordType(
                    name="Inner",
                    kind="class",
                    qualified_name="detail::Inner",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_unambiguous_bare_alias_still_matches(self) -> None:
        """Guard against over-correcting: a single record's own trailing
        bare-segment alias must still work when there is no collision."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Inner")],
            types=[
                RecordType(
                    name="Inner",
                    kind="class",
                    qualified_name="api::Inner",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})


class TestPublicVariablesAndTypedefResolution:
    def test_public_variable_seeds_its_record_type(self) -> None:
        """Codex review, fresh evidence: an exported public variable (e.g.
        Foo global) is a reachability root the same way a public function
        is -- the scan originally only walked snapshot.functions."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api")],
            variables=[
                Variable(
                    name="global_foo",
                    mangled="global_foo",
                    type="Foo",
                    visibility=Visibility.PUBLIC,
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

    def test_hidden_variable_does_not_seed_its_record_type(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api")],
            variables=[
                Variable(
                    name="internal_foo",
                    mangled="internal_foo",
                    type="Foo",
                    visibility=Visibility.HIDDEN,
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

    def test_private_origin_variable_does_not_seed_its_record_type(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api")],
            variables=[
                Variable(
                    name="internal_foo",
                    mangled="internal_foo",
                    type="Foo",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PRIVATE_HEADER,
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

    def test_typedef_alias_in_signature_resolves_to_its_record_target(
        self,
    ) -> None:
        """Codex review, fresh evidence: a public function returning
        "Alias" where snapshot.typedefs maps "Alias" -> "Foo" must resolve
        to Foo, mirroring surface.py's own reachability closure."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Alias")],
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        snap.typedefs = {"Alias": "Foo"}
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_no_typedefs_means_no_typedef_pattern_compiled(self) -> None:
        """Guard against a regression in the typedef_pattern-is-None short
        circuit when a snapshot has no typedefs at all."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", params=[Param(name="s", type="std::string")])],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_typedef_not_matched_is_not_resolved_twice(self) -> None:
        """A typedef alias reached from two different declarations must
        only be resolved once -- covers the resolved_typedefs de-dup set."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn("first", return_type="Alias"),
                _fn("second", return_type="Alias"),
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
        snap.typedefs = {"Alias": "Foo"}
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_variable_scan_breaks_once_everything_already_found(self) -> None:
        """Once every stdlib candidate is found via functions, the variable
        loop must not scan any variables at all -- covers its top-of-loop
        early-exit break."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", params=[Param(name="s", type="std::string")])],
            variables=[
                Variable(
                    name="global_foo",
                    mangled="global_foo",
                    type="Foo",
                    visibility=Visibility.PUBLIC,
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

    def test_stdlib_namespaced_variable_is_never_the_referencing_side(self) -> None:
        """Mirrors the stdlib-function exclusion: a variable whose own name
        is stdlib-namespaced must not count as a referencing declaration."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api")],
            variables=[
                Variable(
                    name="std::internal_global",
                    mangled="std_internal_global",
                    type="Foo",
                    visibility=Visibility.PUBLIC,
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

    def test_direct_base_class_field_is_a_reachability_root(self) -> None:
        """Codex review, fresh evidence: a public Derived inheriting a
        non-stdlib Base whose own field is a stdlib record must reach that
        field -- only rec.fields was followed, not rec.bases."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Derived")],
            types=[
                RecordType(name="Derived", kind="class", bases=["Base"]),
                RecordType(
                    name="Base",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_virtual_base_class_field_is_a_reachability_root(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Derived")],
            types=[
                RecordType(name="Derived", kind="class", virtual_bases=["Base"]),
                RecordType(
                    name="Base",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})


class TestStdlibTypedefAliasResolution:
    def test_std_string_typedef_alias_resolves_to_real_record(self) -> None:
        """The concrete gap this closes: a public function spelled with the
        bare DWARF signature form ("string") must resolve through
        snapshot.typedefs["std::string"] (qualified key, bare target) to
        the real std::__cxx11::basic_string<...> RecordType -- verified
        empirically against a real DWARF-dumped std::string parameter."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="string")],
            types=[
                RecordType(
                    name="std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >",
                    kind="class",
                )
            ],
        )
        snap.typedefs = {
            "std::string": "basic_string<char, std::char_traits<char>, std::allocator<char> >"
        }
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {
                "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >"
            }
        )

    def test_qualified_typedef_key_spelling_also_matches(self) -> None:
        """A signature spelled with the fully-qualified alias itself
        ("std::string", not the bare DWARF form) must still resolve --
        the literal typedefs key is always kept in the index."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="std::string")],
            types=[
                RecordType(
                    name="std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >",
                    kind="class",
                )
            ],
        )
        snap.typedefs = {
            "std::string": "basic_string<char, std::char_traits<char>, std::allocator<char> >"
        }
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {
                "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >"
            }
        )

    def test_stripped_typedef_key_colliding_with_non_stdlib_record_is_dropped(
        self,
    ) -> None:
        """The stripped alias spelling must not be registered if it
        collides with a real, unrelated non-stdlib record's own identity
        -- same false-negative-over-false-positive principle as the
        RecordType-level collision guard."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "foo",
                    params=[Param(name="s", type="string")],
                )
            ],
            types=[
                # An unrelated user type whose bare spelling collides with
                # the stripped form of the "std::string" typedef key.
                RecordType(name="string", kind="class"),
                RecordType(
                    name="std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >",
                    kind="class",
                ),
            ],
        )
        snap.typedefs = {
            "std::string": "basic_string<char, std::char_traits<char>, std::allocator<char> >"
        }
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_two_typedefs_disagreeing_on_stripped_target_are_dropped(self) -> None:
        """Two distinct stdlib-namespaced typedef keys reducing to the same
        stripped spelling but pointing at different targets must not
        register that ambiguous spelling at all -- an ambiguous resolution
        is worse than none. Unit-tested directly against
        _typedef_spelling_targets: std::Alias and __gnu_cxx::Alias both
        strip to bare "Alias", but point at different targets."""
        index = _typedef_spelling_targets(
            {"std::Alias": "detail::Real", "__gnu_cxx::Alias": "other::Real"},
            frozenset(),
        )
        assert "Alias" not in index
        assert index["std::Alias"] == "detail::Real"
        assert index["__gnu_cxx::Alias"] == "other::Real"

    def test_agreeing_stripped_typedef_targets_are_kept(self) -> None:
        """Guard against over-correcting: two typedef keys reducing to the
        same stripped spelling that agree on the target must still
        register it (no real ambiguity in the outcome)."""
        index = _typedef_spelling_targets(
            {"std::Alias": "Real", "__gnu_cxx::Alias": "Real"},
            frozenset(),
        )
        assert index["Alias"] == "Real"


class TestBareTypeNameTemplateArguments:
    def test_strips_only_outer_namespace_not_template_argument_qualifier(
        self,
    ) -> None:
        """Codex review, fresh evidence: a naive rsplit("::", 1) splits
        inside a template argument's own qualified name, since
        "dep::Tag"'s "::" is lexically the last one in the whole string
        even though it belongs to the template argument, not the outer
        namespace path -- producing the corrupted bare form "Tag>" instead
        of "Wrapper<dep::Tag>"."""
        assert _bare_type_name("api::Wrapper<dep::Tag>") == "Wrapper<dep::Tag>"

    def test_no_outer_namespace_qualifier_returns_identity_unchanged(self) -> None:
        """A template argument's own "::" at bracket depth > 0 is not an
        outer-namespace separator -- with nothing at depth zero to split
        on, the identity is already bare."""
        assert _bare_type_name("Wrapper<dep::Tag>") == "Wrapper<dep::Tag>"

    def test_plain_qualified_name_without_templates_still_works(self) -> None:
        """Guard against over-correcting the simple (pre-existing) case."""
        assert _bare_type_name("api::Inner") == "Inner"

    def test_nested_namespace_qualifier_strips_up_to_last_depth_zero_separator(
        self,
    ) -> None:
        assert _bare_type_name("ns::Outer::Inner<T>") == "Inner<T>"

    def test_end_to_end_bare_alias_resolves_through_qualified_template_argument(
        self,
    ) -> None:
        """A record whose qualified identity is a template instantiation
        with its own qualified template argument (api::Wrapper<dep::Tag>)
        must still be reachable via the bare spelling a real dumper
        backend actually uses in a signature (Wrapper<dep::Tag>) -- the
        template-nesting-aware bare alias, not a naive rsplit, is what
        makes this resolve."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Wrapper<dep::Tag>")],
            types=[
                RecordType(
                    name="Wrapper<dep::Tag>",
                    kind="class",
                    qualified_name="api::Wrapper<dep::Tag>",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})


class TestNestedStdlibSpellingWithinNonStdlibWrapperIdentity:
    def test_stdlib_type_embedded_directly_in_public_signature_via_wrapper_identity(
        self,
    ) -> None:
        """Codex review, fresh evidence: when a non-stdlib record's own
        full identity spelling embeds a stdlib type's spelling verbatim
        (a template instantiation like "Wrapper<std::string>"), and a
        public function's signature names that wrapper identity exactly,
        the nested "std::string" substring must be independently detected
        as directly referenced -- a single non-overlapping regex pass over
        one combined pattern would match the longer non-stdlib alternative
        first and never separately notice the nested stdlib substring in
        the same span. Wrapper itself has no fields, so this can only pass
        via the direct-spelling match, not the field-walk fallback."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Wrapper<std::string>")],
            types=[
                RecordType(name="Wrapper<std::string>", kind="class"),
                RecordType(name="std::string", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_stdlib_type_already_found_is_not_reprocessed_on_a_second_mention(
        self,
    ) -> None:
        """A stdlib identity already moved from ``remaining`` into
        ``referenced`` by an earlier declaration must not error or
        duplicate work when a later declaration mentions it again."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "foo",
                    return_type="std::string",
                    params=[
                        Param(name="a", type="std::string"),
                        Param(name="b", type="std::string"),
                    ],
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})


class TestNonStdlibBareAliasCollisionWithStdlibStrippedSpelling:
    def test_non_stdlib_signature_spellings_includes_bare_alias(self) -> None:
        """Codex review, fresh evidence: a non-stdlib record's own full
        identity is namespace-qualified, but a real dumper backend spells
        it bare in a signature -- the collision set must include that bare
        form too, not just the full identity, or a stdlib candidate
        stripping to the same bare spelling slips through undetected."""
        spellings = _non_stdlib_signature_spellings(frozenset({"api::vector<int>"}))
        assert spellings == frozenset({"api::vector<int>", "vector<int>"})

    def test_non_stdlib_signature_spellings_keeps_ambiguous_bare_alias(self) -> None:
        """Unlike record_index (which drops an ambiguous bare alias
        shared by 2+ distinct records), the collision set must still
        include it -- it is a real spelling some non-stdlib record can be
        named by, regardless of which one a matching signature means."""
        spellings = _non_stdlib_signature_spellings(
            frozenset({"api::Inner", "detail::Inner"})
        )
        assert "Inner" in spellings

    def test_bare_aliased_non_stdlib_type_does_not_unfilter_unrelated_stdlib_vector(
        self,
    ) -> None:
        """Codex review, fresh evidence: api::vector<int> is spelled bare
        as "vector<int>" in a real signature -- the same bare spelling
        std::vector<int> reduces to after namespace-stripping. A public
        function taking api::vector<int> by value must not incorrectly
        mark the unrelated std::vector<int> as directly referenced just
        because both share that bare spelling."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="vector<int>")],
            types=[
                RecordType(
                    name="vector<int>",
                    kind="class",
                    qualified_name="api::vector<int>",
                ),
                RecordType(name="std::vector<int>", kind="class"),
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_bare_aliased_non_stdlib_type_does_not_resolve_through_typedef_collision(
        self,
    ) -> None:
        """Codex review, fresh evidence: the same collision, but through
        the typedef-key stripping path -- api::string is spelled bare as
        "string", the same bare spelling the "std::string" typedef key
        strips to. A public function taking api::string by value must not
        incorrectly resolve through that typedef target to std::string's
        real backing record."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="string")],
            types=[
                RecordType(
                    name="string",
                    kind="class",
                    qualified_name="api::string",
                ),
                RecordType(
                    name="std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >",
                    kind="class",
                ),
            ],
        )
        snap.typedefs = {
            "std::string": "basic_string<char, std::char_traits<char>, std::allocator<char> >"
        }
        assert directly_referenced_stdlib_types(snap) == frozenset()


class TestNonStdlibTypedefBareAlias:
    def test_non_stdlib_qualified_typedef_key_resolves_via_bare_alias(self) -> None:
        """Codex review, fresh evidence: the DWARF backend stores a
        namespace-qualified typedef key ("api::Alias") while a public
        declaration's own type string spells the bare "Alias" -- without
        also deriving a bare alias for a non-stdlib typedef key (not just
        a stdlib one), a signature using that bare spelling never resolves
        through the typedef to its target, silently missing a stdlib field
        reachable through it."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", return_type="Alias")],
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
                RecordType(name="std::string", kind="class"),
            ],
        )
        snap.typedefs = {"api::Alias": "Foo"}
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_non_stdlib_typedef_bare_alias_colliding_with_real_record_is_dropped(
        self,
    ) -> None:
        """The new non-stdlib bare-alias derivation must still respect the
        existing collision guard: a bare alias that coincides with a real,
        unrelated non-stdlib record's own bare spelling must not be
        registered."""
        index = _typedef_spelling_targets(
            {"api::Alias": "Foo"},
            frozenset({"unrelated::Alias"}),
        )
        assert "Alias" not in index

    def test_non_stdlib_typedef_key_without_namespace_is_unaffected(self) -> None:
        """Guard against over-correcting: an already-bare typedef key must
        not gain a spurious duplicate entry (_bare_type_name returns it
        unchanged, which the `c != key` filter must exclude)."""
        index = _typedef_spelling_targets({"Alias": "Foo"}, frozenset())
        assert index == {"Alias": "Foo"}


class TestStdlibOwnerNotSeededAsReferenced:
    def test_bare_named_method_on_stdlib_internal_owner_stays_filtered(self) -> None:
        """Codex review, fresh evidence: CastXML/direct-clang record a
        method's own Function.name bare ("touch", never
        "__gnu_cxx::Node::touch"), so the existing fn.name.startswith(...)
        guard cannot catch a retained, seemingly-public method whose owner
        is itself a stdlib-internal class -- owner_class_of correctly
        recovers the qualified "__gnu_cxx::Node" owner via the mangled
        fallback, and without also checking that recovered owner against
        the stdlib prefixes, it would be scanned and marked directly
        referenced, unfiltering purely internal toolchain churn."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="touch",
                    mangled="_ZN9__gnu_cxx4Node5touchEv",
                    return_type="void",
                    params=[],
                )
            ],
            types=[RecordType(name="__gnu_cxx::Node", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset()

    def test_bare_named_method_on_genuine_non_stdlib_owner_still_seeds_it(
        self,
    ) -> None:
        """Guard against over-correcting: a bare-named method whose real
        owner is a genuine non-stdlib class must still seed that owner as
        reachable."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="touch",
                    mangled="_ZN3Foo5touchEv",
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
