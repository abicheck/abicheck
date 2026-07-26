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
    TypeField,
    Visibility,
)
from abicheck.type_reachability import (
    directly_referenced_stdlib_types,
    type_string_references_name,
)


def _fn(
    name: str, return_type: str = "void", params: list[Param] | None = None
) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=return_type,
        params=params or [],
        visibility=Visibility.PUBLIC,
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
