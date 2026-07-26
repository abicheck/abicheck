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
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
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
        the first non-stdlib type's fields, must still be found via a
        *later* non-stdlib type's fields -- covers the multi-type field
        scan loop actually iterating past its first entry."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
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
