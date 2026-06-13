# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Unit tests for the internal-namespace leak detector.

These tests build synthetic ``AbiSnapshot`` objects, so they do not
need a C/C++ compiler, libabigail, abi-compliance-checker, or castxml.
They are part of the default fast test suite.
"""
from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.internal_leak import (
    _candidate_type_names,
    _name_segments,
    _split_top_level_commas,
    _strip_template_args,
    compute_leak_paths,
    detect_internal_leaks,
    is_internal_type,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)

# ---------------------------------------------------------------------------
# is_internal_type / segment helpers
# ---------------------------------------------------------------------------


class TestNameSegments:
    def test_strips_template_args(self) -> None:
        assert _strip_template_args("ns::detail::pimpl<X>") == "ns::detail::pimpl"

    def test_strips_nested_template_args(self) -> None:
        assert _strip_template_args("ns::detail::pimpl<Foo<int, char>>") == "ns::detail::pimpl"

    def test_splits_segments(self) -> None:
        assert _name_segments("oneapi::dal::detail::pimpl<X>") == [
            "oneapi", "dal", "detail", "pimpl",
        ]

    def test_empty(self) -> None:
        assert _name_segments("") == []


class TestIsInternalType:
    @pytest.mark.parametrize("name", [
        "oneapi::dal::detail::pimpl",
        "oneapi::dal::detail::pimpl<X>",
        "ns::impl::handle",
        "ns::internal::core",
        "std::__detail::node",
    ])
    def test_internal_names_are_internal(self, name: str) -> None:
        assert is_internal_type(name) is True

    @pytest.mark.parametrize("name", [
        "MyClass",
        "ns::Public",
        "Details",                # substring 'detail' — but not a segment
        "DetailView",             # name segment that *contains* 'detail'
        "ns::DetailHelper",       # segment contains 'detail' but isn't exactly 'detail'
        "ns::Public::impl",       # last segment is 'impl' — IS internal (segment match)
    ])
    def test_non_segment_substring_is_not_internal(self, name: str) -> None:
        # The last case ("ns::Public::impl") *is* internal because the last
        # segment is exactly "impl". Adjust the parametrise list:
        if name == "ns::Public::impl":
            assert is_internal_type(name) is True
        else:
            assert is_internal_type(name) is False

    def test_custom_namespace_list(self) -> None:
        assert is_internal_type("ns::priv::x", internal_namespaces=("priv",)) is True
        assert is_internal_type("ns::detail::x", internal_namespaces=("priv",)) is False

    def test_empty_namespace_list(self) -> None:
        assert is_internal_type("ns::detail::x", internal_namespaces=()) is False


class TestCandidateTypeNames:
    def test_plain_type(self) -> None:
        cands = _candidate_type_names("int")
        assert "int" in cands

    def test_pointer_decorator_stripped(self) -> None:
        cands = _candidate_type_names("const ns::detail::Impl*")
        # const + * stripped — strip leaves "ns::detail::Impl"
        assert any("ns::detail::Impl" in c for c in cands)

    def test_template_inner_extracted(self) -> None:
        cands = _candidate_type_names("std::unique_ptr<ns::detail::Impl>")
        # Outer template AND the inner type both surface
        joined = ",".join(cands)
        assert "std::unique_ptr" in joined
        assert "ns::detail::Impl" in joined

    def test_split_top_level_commas(self) -> None:
        assert _split_top_level_commas("A, B, C") == ["A", " B", " C"]

    def test_split_respects_nesting(self) -> None:
        assert _split_top_level_commas("A, B<X, Y>, C") == ["A", " B<X, Y>", " C"]


# ---------------------------------------------------------------------------
# Synthetic snapshot helpers
# ---------------------------------------------------------------------------


def _snap(
    library: str = "libtest.so",
    version: str = "1.0",
    *,
    functions: list[Function] | None = None,
    variables: list[Variable] | None = None,
    types: list[RecordType] | None = None,
    typedefs: dict[str, str] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version=version,
        functions=list(functions or []),
        variables=list(variables or []),
        types=list(types or []),
        typedefs=dict(typedefs or {}),
    )


def _public_fn(name: str, ret: str = "void", params: list[tuple[str, str]] | None = None) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[Param(name=n, type=t) for n, t in (params or [])],
        visibility=Visibility.PUBLIC,
    )


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


class TestComputeLeakPaths:
    def test_no_internal_types_no_paths(self) -> None:
        snap = _snap(
            functions=[_public_fn("foo", "Public", [])],
            types=[
                RecordType(name="Public", kind="class",
                           fields=[TypeField(name="x", type="int")]),
            ],
        )
        paths = compute_leak_paths(snap)
        assert paths == {}

    def test_inheritance_path(self) -> None:
        # Public class inherits from detail::Base
        snap = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class",
                           bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class",
                           fields=[TypeField(name="f", type="int")]),
            ],
        )
        paths = compute_leak_paths(snap)
        assert "ns::detail::Base" in paths
        # Path should mention the public class and the base step
        joined = " ".join(" ".join(p) for p in paths["ns::detail::Base"])
        assert "Public" in joined

    def test_embedded_by_value_path(self) -> None:
        snap = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", fields=[
                    TypeField(name="impl_", type="ns::detail::Impl"),
                ]),
                RecordType(name="ns::detail::Impl", kind="struct"),
            ],
        )
        paths = compute_leak_paths(snap)
        assert "ns::detail::Impl" in paths

    def test_via_pointer_field_still_reachable(self) -> None:
        snap = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", fields=[
                    TypeField(name="impl_", type="ns::detail::Impl*"),
                ]),
                RecordType(name="ns::detail::Impl", kind="struct"),
            ],
        )
        paths = compute_leak_paths(snap)
        # Pointer fields still produce a path — identity/vtable changes
        # still leak. Severity downgrade happens via the value-embedding
        # heuristic, not here.
        assert "ns::detail::Impl" in paths

    def test_via_function_return_type(self) -> None:
        snap = _snap(
            functions=[_public_fn("get_impl", "ns::detail::Helper*", [])],
        )
        paths = compute_leak_paths(snap)
        assert "ns::detail::Helper" in paths

    def test_via_public_typedef_return_type(self) -> None:
        snap = _snap(
            functions=[_public_fn("make", "PublicImpl", [])],
            types=[RecordType(name="ns::detail::Impl", kind="struct")],
            typedefs={"PublicImpl": "ns::detail::Impl"},
        )
        paths = compute_leak_paths(snap)
        assert "ns::detail::Impl" in paths
        joined = " ".join(" ".join(p) for p in paths["ns::detail::Impl"])
        assert "typedef:PublicImpl" in joined

    def test_via_chained_public_typedef_return_type(self) -> None:
        snap = _snap(
            functions=[_public_fn("make", "PublicImpl", [])],
            types=[RecordType(name="ns::detail::Impl", kind="struct")],
            typedefs={"PublicImpl": "ImplAlias", "ImplAlias": "ns::detail::Impl"},
        )
        paths = compute_leak_paths(snap)
        assert "ns::detail::Impl" in paths

    def test_via_template_argument_in_return(self) -> None:
        snap = _snap(
            functions=[_public_fn("get", "std::unique_ptr<ns::detail::Impl>", [])],
        )
        paths = compute_leak_paths(snap)
        assert "ns::detail::Impl" in paths

    def test_truly_private_not_reachable(self) -> None:
        # detail::Hidden is only referenced from another detail:: type.
        snap = _snap(
            functions=[_public_fn("foo", "int", [])],
            types=[
                RecordType(name="ns::detail::A", kind="class",
                           fields=[TypeField(name="h", type="ns::detail::Hidden")]),
                RecordType(name="ns::detail::Hidden", kind="class"),
            ],
        )
        paths = compute_leak_paths(snap)
        # ns::detail::A and ns::detail::Hidden are both internal AND only
        # reachable from each other (foo returns int, no public types).
        # They should NOT appear since the BFS starts from public surface.
        # But — public RecordTypes also seed; here both are internal, so
        # they won't seed. Result: empty.
        assert paths == {}


# ---------------------------------------------------------------------------
# detect_internal_leaks
# ---------------------------------------------------------------------------


class TestDetectInternalLeaks:
    def test_no_internal_changes(self) -> None:
        old = _snap(functions=[_public_fn("foo", "int", [])])
        new = _snap(functions=[_public_fn("foo", "int", [])])
        leaks = detect_internal_leaks([], old, new)
        assert leaks == []

    def test_unrelated_change_no_leak(self) -> None:
        # type_size_changed on a *public* type — no leak should be emitted.
        old = _snap(
            functions=[_public_fn("foo", "Public*", [])],
            types=[RecordType(name="Public", kind="class", size_bits=32)],
        )
        new = _snap(
            functions=[_public_fn("foo", "Public*", [])],
            types=[RecordType(name="Public", kind="class", size_bits=64)],
        )
        changes = [Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Public",
            description="size changed",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert leaks == []

    def test_internal_type_change_reachable_via_base(self) -> None:
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class",
                           bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=32),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class",
                           bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=64),
            ],
        )
        changes = [Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Base",
            description="size changed",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1
        leak = leaks[0]
        assert leak.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        assert leak.symbol == "ns::detail::Base"
        # Description must mention the public class
        assert "Public" in leak.description
        # And the leak kind being reported
        assert "type_size_changed" in leak.description

    def test_internal_type_not_reachable_no_leak(self) -> None:
        # detail::Hidden changes but is not in the public reachability graph.
        old = _snap(
            functions=[_public_fn("foo", "int", [])],
            types=[RecordType(name="ns::detail::Hidden", kind="class", size_bits=32)],
        )
        new = _snap(
            functions=[_public_fn("foo", "int", [])],
            types=[RecordType(name="ns::detail::Hidden", kind="class", size_bits=64)],
        )
        changes = [Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Hidden",
            description="size changed",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert leaks == []

    def test_embedded_by_value_severity_hint(self) -> None:
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", fields=[
                    TypeField(name="impl_", type="ns::detail::Impl"),
                ]),
                RecordType(name="ns::detail::Impl", kind="struct", size_bits=32),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", fields=[
                    TypeField(name="impl_", type="ns::detail::Impl"),
                ]),
                RecordType(name="ns::detail::Impl", kind="struct", size_bits=64),
            ],
        )
        changes = [Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Impl",
            description="size changed",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1
        assert "embedded-by-value or via inheritance" in leaks[0].description

    def test_pointer_field_severity_hint(self) -> None:
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", fields=[
                    TypeField(name="impl_", type="ns::detail::Impl*"),
                ]),
                RecordType(name="ns::detail::Impl", kind="struct", size_bits=32),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", fields=[
                    TypeField(name="impl_", type="ns::detail::Impl*"),
                ]),
                RecordType(name="ns::detail::Impl", kind="struct",
                           vtable=["fn1", "fn2"]),
            ],
        )
        changes = [Change(
            kind=ChangeKind.TYPE_VTABLE_CHANGED,
            symbol="ns::detail::Impl",
            description="vtable changed",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1
        # Pointer-only embedding — not "embedded-by-value"
        assert "embedded-by-value" not in leaks[0].description
        assert "reachable via pointer / template" in leaks[0].description

    def test_multiple_changes_collapse_to_single_leak(self) -> None:
        # Two distinct change kinds on the same detail:: type produce
        # one leak finding (so users don't see redundant noise).
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class",
                           bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=32),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class",
                           bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=64),
            ],
        )
        # NB: TYPE_FIELD_* (from diff_types) and STRUCT_FIELD_* (from
        # diff_platform) use different symbol conventions. TYPE_FIELD_*
        # puts the type name in symbol (the field name lives in
        # `description`). STRUCT_FIELD_* puts "Type::field" in symbol.
        changes = [
            Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                   symbol="ns::detail::Base", description="size"),
            # TYPE_FIELD_ADDED uses symbol=type-name (matches diff_types).
            Change(kind=ChangeKind.TYPE_FIELD_ADDED,
                   symbol="ns::detail::Base", description="field added: ns::detail::Base::y"),
            # STRUCT_FIELD_OFFSET_CHANGED uses symbol="Type::field" (matches diff_platform).
            Change(kind=ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
                   symbol="ns::detail::Base::y",
                   description="offset changed: ns::detail::Base::y"),
        ]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1
        # All three source kinds should appear in the description
        assert "type_size_changed" in leaks[0].description
        assert "type_field_added" in leaks[0].description
        assert "struct_field_offset_changed" in leaks[0].description

    def test_namespaced_internal_type_with_type_field_change_not_truncated(
        self,
    ) -> None:
        """Regression: a TYPE_FIELD_* change on a namespaced internal type
        must not be misclassified as "Type::field" and have its last
        segment stripped.

        ``diff_types`` emits ``TYPE_FIELD_*`` with ``symbol=<type_name>``
        (field name only in the description). If our root-type helper
        treats the last segment as a field, ``ns::detail::Impl`` would
        get truncated to ``ns::detail`` and the reachability lookup
        would fail.
        """
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", fields=[
                    TypeField(name="impl_", type="ns::detail::Impl"),
                ]),
                RecordType(name="ns::detail::Impl", kind="struct",
                           fields=[TypeField(name="row", type="int",
                                             offset_bits=0)]),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", fields=[
                    TypeField(name="impl_", type="ns::detail::Impl"),
                ]),
                RecordType(name="ns::detail::Impl", kind="struct", fields=[
                    TypeField(name="row", type="int", offset_bits=0),
                    TypeField(name="col", type="int", offset_bits=32),
                ]),
            ],
        )
        # Mimic diff_types: TYPE_FIELD_ADDED with symbol = containing type.
        changes = [Change(
            kind=ChangeKind.TYPE_FIELD_ADDED,
            symbol="ns::detail::Impl",
            description="Field added: ns::detail::Impl::col",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1
        assert leaks[0].symbol == "ns::detail::Impl"

    def test_custom_namespace_patterns(self) -> None:
        # Use a project-specific internal namespace name like "priv".
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class",
                           bases=["ns::priv::Base"]),
                RecordType(name="ns::priv::Base", kind="class", size_bits=32),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class",
                           bases=["ns::priv::Base"]),
                RecordType(name="ns::priv::Base", kind="class", size_bits=64),
            ],
        )
        changes = [Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::priv::Base",
            description="size",
        )]
        # Default namespaces: no detection (priv isn't in defaults).
        assert detect_internal_leaks(changes, old, new) == []
        # Custom: detection fires.
        leaks = detect_internal_leaks(
            changes, old, new, internal_namespaces=("priv",),
        )
        assert len(leaks) == 1


# ---------------------------------------------------------------------------
# Integration with the full compare() pipeline
# ---------------------------------------------------------------------------


class TestComparePipelineIntegration:
    """Verify the new ChangeKind appears via the full compare() pipeline."""

    def test_detail_base_size_change_produces_leak_finding(self) -> None:
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class",
                           bases=["ns::detail::Base"], size_bits=32),
                RecordType(name="ns::detail::Base", kind="class", size_bits=32,
                           fields=[TypeField(name="x", type="int", offset_bits=0)]),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class",
                           bases=["ns::detail::Base"], size_bits=64),
                RecordType(name="ns::detail::Base", kind="class", size_bits=64,
                           fields=[
                               TypeField(name="x", type="int", offset_bits=0),
                               TypeField(name="y", type="int", offset_bits=32),
                           ]),
            ],
        )
        result = compare(old, new)
        # Some flavour of layout-affecting change on the detail base must
        # have fired (size or field-added), and the leak overlay must be
        # present too.
        leak_kinds = {c.kind for c in result.changes}
        assert ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API in leak_kinds, (
            f"expected leak overlay, got kinds={sorted(k.value for k in leak_kinds)}"
        )

    def test_only_detail_change_with_no_public_consumer_no_leak(self) -> None:
        old = _snap(
            types=[RecordType(name="ns::detail::Orphan", kind="class", size_bits=32)],
        )
        new = _snap(
            types=[RecordType(name="ns::detail::Orphan", kind="class", size_bits=64)],
        )
        result = compare(old, new)
        leak_kinds = {c.kind for c in result.changes}
        assert ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API not in leak_kinds

    def test_public_only_change_no_false_leak(self) -> None:
        # Pure public-API change — no detail:: involvement. The leak
        # detector must NOT emit anything.
        old = _snap(
            functions=[_public_fn("foo", "int", [])],
            types=[RecordType(name="MyClass", kind="class", size_bits=32)],
        )
        new = _snap(
            functions=[_public_fn("foo", "int", [])],
            types=[RecordType(name="MyClass", kind="class", size_bits=64)],
        )
        result = compare(old, new)
        leak_kinds = {c.kind for c in result.changes}
        assert ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API not in leak_kinds


# ---------------------------------------------------------------------------
# Example-case parity tests — synthetic snapshots that mirror examples/case74,
# case75, case76 so the leak detection has fast-test coverage even when the
# castxml / compiler toolchain required by the integration tests is absent.
# ---------------------------------------------------------------------------


class TestExampleCaseParity:
    """Reproduce the structural pattern of each new example case as a
    synthetic snapshot and assert the detector fires.
    """

    def test_case74_detail_base_class_changed(self) -> None:
        # mylib::knn_descriptor : public mylib::detail::descriptor_base
        # detail::descriptor_base gains a field; public derived size shifts.
        old = _snap(
            functions=[_public_fn(
                "mylib_make_descriptor", "mylib::knn_descriptor*",
            )],
            types=[
                RecordType(
                    name="mylib::detail::descriptor_base", kind="class",
                    size_bits=32,
                    fields=[TypeField(name="class_count_", type="int",
                                      offset_bits=0)],
                ),
                RecordType(
                    name="mylib::knn_descriptor", kind="class",
                    size_bits=64,
                    bases=["mylib::detail::descriptor_base"],
                    fields=[TypeField(name="neighbor_count_", type="int",
                                      offset_bits=32)],
                ),
            ],
        )
        new = _snap(
            functions=[_public_fn(
                "mylib_make_descriptor", "mylib::knn_descriptor*",
            )],
            types=[
                RecordType(
                    name="mylib::detail::descriptor_base", kind="class",
                    size_bits=64,
                    fields=[
                        TypeField(name="class_count_", type="int",
                                  offset_bits=0),
                        TypeField(name="max_iter_", type="int",
                                  offset_bits=32),
                    ],
                ),
                RecordType(
                    name="mylib::knn_descriptor", kind="class",
                    size_bits=96,
                    bases=["mylib::detail::descriptor_base"],
                    fields=[TypeField(name="neighbor_count_", type="int",
                                      offset_bits=64)],
                ),
            ],
        )
        result = compare(old, new)
        leaks = [
            c for c in result.changes
            if c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        ]
        assert leaks, "case74 pattern: leak must be detected"
        assert leaks[0].symbol == "mylib::detail::descriptor_base"
        # The path should mention the public derived class.
        assert "mylib::knn_descriptor" in leaks[0].description

    def test_case75_detail_embedded_by_value(self) -> None:
        # mylib::table embeds mylib::detail::table_impl by value.
        # detail::table_impl gains a field; public table size grows.
        old = _snap(
            functions=[_public_fn(
                "mylib_make_table", "mylib::table*",
            )],
            types=[
                RecordType(
                    name="mylib::detail::table_impl", kind="struct",
                    size_bits=128,
                    fields=[
                        TypeField(name="row_count", type="size_t",
                                  offset_bits=0),
                        TypeField(name="column_count", type="size_t",
                                  offset_bits=64),
                    ],
                ),
                RecordType(
                    name="mylib::table", kind="class", size_bits=128,
                    fields=[
                        TypeField(name="impl_",
                                  type="mylib::detail::table_impl",
                                  offset_bits=0),
                    ],
                ),
            ],
        )
        new = _snap(
            functions=[_public_fn(
                "mylib_make_table", "mylib::table*",
            )],
            types=[
                RecordType(
                    name="mylib::detail::table_impl", kind="struct",
                    size_bits=192,
                    fields=[
                        TypeField(name="row_count", type="size_t",
                                  offset_bits=0),
                        TypeField(name="column_count", type="size_t",
                                  offset_bits=64),
                        TypeField(name="layout_kind", type="size_t",
                                  offset_bits=128),
                    ],
                ),
                RecordType(
                    name="mylib::table", kind="class", size_bits=192,
                    fields=[
                        TypeField(name="impl_",
                                  type="mylib::detail::table_impl",
                                  offset_bits=0),
                    ],
                ),
            ],
        )
        result = compare(old, new)
        leaks = [
            c for c in result.changes
            if c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        ]
        assert leaks, "case75 pattern: leak must be detected"
        assert leaks[0].symbol == "mylib::detail::table_impl"
        # Embedded-by-value severity hint should appear.
        assert "embedded-by-value" in leaks[0].description

    def test_case76_detail_pimpl_vtable_changed(self) -> None:
        # mylib::svm_algorithm : public mylib::detail::algorithm_iface
        # detail::algorithm_iface gets a new virtual method inserted
        # mid-vtable; vtable layout shifts for all consumers.
        old = _snap(
            functions=[_public_fn(
                "mylib_make_svm", "mylib::detail::algorithm_iface*",
            )],
            types=[
                RecordType(
                    name="mylib::detail::algorithm_iface", kind="class",
                    size_bits=64,
                    vtable=["~algorithm_iface", "run", "status"],
                ),
                RecordType(
                    name="mylib::svm_algorithm", kind="class",
                    size_bits=96,
                    bases=["mylib::detail::algorithm_iface"],
                    vtable=["~svm_algorithm", "run", "status"],
                ),
            ],
        )
        new = _snap(
            functions=[_public_fn(
                "mylib_make_svm", "mylib::detail::algorithm_iface*",
            )],
            types=[
                RecordType(
                    name="mylib::detail::algorithm_iface", kind="class",
                    size_bits=64,
                    vtable=["~algorithm_iface", "run", "progress", "status"],
                ),
                RecordType(
                    name="mylib::svm_algorithm", kind="class",
                    size_bits=96,
                    bases=["mylib::detail::algorithm_iface"],
                    vtable=["~svm_algorithm", "run", "progress", "status"],
                ),
            ],
        )
        result = compare(old, new)
        leaks = [
            c for c in result.changes
            if c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        ]
        assert leaks, "case76 pattern: leak must be detected"
        assert leaks[0].symbol == "mylib::detail::algorithm_iface"
        assert "mylib::svm_algorithm" in leaks[0].description
