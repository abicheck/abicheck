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

from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.type_reachability import (
    _compile_spelling_pattern,
    _stripped_signature_spelling,
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
