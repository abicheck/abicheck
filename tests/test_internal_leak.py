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
    _build_suffix_index,
    _candidate_type_names,
    _name_segments,
    _resolve_type_name,
    _split_top_level_commas,
    _strip_template_args,
    compute_leak_paths,
    detect_internal_leaks,
    is_internal_type,
    select_preferred_path,
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
        assert (
            _strip_template_args("ns::detail::pimpl<Foo<int, char>>")
            == "ns::detail::pimpl"
        )

    def test_splits_segments(self) -> None:
        assert _name_segments("oneapi::dal::detail::pimpl<X>") == [
            "oneapi",
            "dal",
            "detail",
            "pimpl",
        ]

    def test_empty(self) -> None:
        assert _name_segments("") == []


class TestStripSignatureParams:
    def test_strips_params(self) -> None:
        from abicheck.internal_leak import _strip_signature_params

        assert _strip_signature_params("ns::api::foo(ns::detail::T*)") == "ns::api::foo"

    def test_stops_at_top_level_paren_not_nested_one(self) -> None:
        """A function-pointer parameter type has its own nested parens
        (e.g. "void (*)(int)") -- must not be mistaken for the function's
        own parameter-list opening."""
        from abicheck.internal_leak import _strip_signature_params

        assert _strip_signature_params("ns::api::bar(void (*)(int))") == "ns::api::bar"

    def test_no_parens_returned_unchanged(self) -> None:
        from abicheck.internal_leak import _strip_signature_params

        assert _strip_signature_params("ns::detail::helper") == "ns::detail::helper"

    def test_empty_string(self) -> None:
        from abicheck.internal_leak import _strip_signature_params

        assert _strip_signature_params("") == ""


class TestIsInternalType:
    @pytest.mark.parametrize(
        "name",
        [
            "oneapi::dal::detail::pimpl",
            "oneapi::dal::detail::pimpl<X>",
            "ns::impl::handle",
            "ns::internal::core",
            "std::__detail::node",
        ],
    )
    def test_internal_names_are_internal(self, name: str) -> None:
        assert is_internal_type(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "MyClass",
            "ns::Public",
            "Details",  # substring 'detail' — but not a segment
            "DetailView",  # name segment that *contains* 'detail'
            "ns::DetailHelper",  # segment contains 'detail' but isn't exactly 'detail'
            "ns::Public::impl",  # last segment is 'impl' — IS internal (segment match)
        ],
    )
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


def _public_fn(
    name: str, ret: str = "void", params: list[tuple[str, str]] | None = None
) -> Function:
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


class TestResolveTypeNameSuffixIndex:
    """The suffix index is a pure O(1) optimisation of the O(N) map scan; it
    must return byte-for-byte the same resolution the scan did (perf fix for
    large C++ surfaces such as pvxs, where the per-node scan was quadratic)."""

    _MAP = {"ns::detail::Base": None, "other::Base": None, "ns::Public": None}

    def test_index_matches_scan_unique(self) -> None:
        idx = _build_suffix_index(self._MAP)
        # "Public" has exactly one final-segment match -> qualifies.
        assert _resolve_type_name("Public", self._MAP) == "ns::Public"
        assert _resolve_type_name("Public", self._MAP, idx) == "ns::Public"

    def test_index_matches_scan_ambiguous(self) -> None:
        idx = _build_suffix_index(self._MAP)
        # "Base" matches two entries -> ambiguous, keep the literal both ways.
        assert _resolve_type_name("Base", self._MAP) == "Base"
        assert _resolve_type_name("Base", self._MAP, idx) == "Base"

    def test_index_passthrough_and_qualified(self) -> None:
        idx = _build_suffix_index(self._MAP)
        assert _resolve_type_name("ns::Public", self._MAP, idx) == "ns::Public"
        assert _resolve_type_name("", self._MAP, idx) == ""
        assert _resolve_type_name("Missing", self._MAP, idx) == "Missing"

    def test_unqualified_base_resolves_in_leak_walk(self) -> None:
        # DWARF may record the base un-qualified; the BFS must still reach the
        # internal type via the suffix-indexed resolver.
        snap = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", bases=["Base"]),
                RecordType(
                    name="ns::detail::Base",
                    kind="class",
                    fields=[TypeField(name="f", type="int")],
                ),
            ],
        )
        assert "ns::detail::Base" in compute_leak_paths(snap)

    def test_suffix_index_built_once_per_walk(self, monkeypatch) -> None:
        # Regression guard for the O(N^2) hotspot: the suffix index must be
        # built ONCE per BFS walk, not rebuilt (or replaced by a full map scan)
        # per visited node. A snapshot with several public roots and internal
        # types drives multiple BFS steps; if the fix regressed (index moved
        # inside the loop, or the per-call scan returned), this count would be
        # != 1. This is the deterministic complement to the scale test below —
        # it catches the exact regression that unit tests missed originally.
        import abicheck.internal_leak as il

        calls = {"n": 0}
        real = il._build_suffix_index

        def _counting(type_map):
            calls["n"] += 1
            return real(type_map)

        monkeypatch.setattr(il, "_build_suffix_index", _counting)
        snap = _snap(
            functions=[_public_fn(f"make{i}", f"Public{i}*", []) for i in range(8)],
            types=(
                [
                    RecordType(name=f"Public{i}", kind="class", bases=[f"Base{i}"])
                    for i in range(8)
                ]
                + [
                    RecordType(
                        name=f"ns::detail::Base{i}",
                        kind="class",
                        fields=[TypeField(name="f", type="int")],
                    )
                    for i in range(8)
                ]
            ),
        )
        paths = il.compute_leak_paths(snap)
        assert calls["n"] == 1, f"index rebuilt {calls['n']}x (should be once)"
        assert len([k for k in paths if k.startswith("ns::detail::Base")]) == 8

    def test_scale_many_types_resolves_and_terminates(self) -> None:
        # Scale correctness at pvxs-like magnitude (thousands of types, each
        # reached via an unqualified base that needs suffix resolution). With the
        # O(1) index this is sub-second; the pre-fix O(N) per-node scan made the
        # equivalent pvxs walk hang for minutes. We assert correctness (every
        # internal base found) rather than a brittle wall-clock bound.
        n = 3000
        types: list[RecordType] = []
        for i in range(n):
            types.append(
                RecordType(name=f"Public{i}", kind="class", bases=[f"Base{i}"])
            )
            types.append(
                RecordType(
                    name=f"ns::detail::Base{i}",
                    kind="class",
                    fields=[TypeField(name="f", type="int")],
                )
            )
        snap = _snap(
            functions=[_public_fn(f"make{i}", f"Public{i}*", []) for i in range(n)],
            types=types,
        )
        paths = compute_leak_paths(snap)
        assert sum(1 for k in paths if k.startswith("ns::detail::Base")) == n


class TestComputeLeakPaths:
    def test_no_internal_types_no_paths(self) -> None:
        snap = _snap(
            functions=[_public_fn("foo", "Public", [])],
            types=[
                RecordType(
                    name="Public",
                    kind="class",
                    fields=[TypeField(name="x", type="int")],
                ),
            ],
        )
        paths = compute_leak_paths(snap)
        assert paths == {}

    def test_inheritance_path(self) -> None:
        # Public class inherits from detail::Base
        snap = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", bases=["ns::detail::Base"]),
                RecordType(
                    name="ns::detail::Base",
                    kind="class",
                    fields=[TypeField(name="f", type="int")],
                ),
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
                RecordType(
                    name="Public",
                    kind="class",
                    fields=[
                        TypeField(name="impl_", type="ns::detail::Impl"),
                    ],
                ),
                RecordType(name="ns::detail::Impl", kind="struct"),
            ],
        )
        paths = compute_leak_paths(snap)
        assert "ns::detail::Impl" in paths

    def test_via_pointer_field_still_reachable(self) -> None:
        snap = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(
                    name="Public",
                    kind="class",
                    fields=[
                        TypeField(name="impl_", type="ns::detail::Impl*"),
                    ],
                ),
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
                RecordType(
                    name="ns::detail::A",
                    kind="class",
                    fields=[TypeField(name="h", type="ns::detail::Hidden")],
                ),
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
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="Public",
                description="size changed",
            )
        ]
        leaks = detect_internal_leaks(changes, old, new)
        assert leaks == []

    def test_internal_type_change_reachable_via_base(self) -> None:
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=32),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=64),
            ],
        )
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="ns::detail::Base",
                description="size changed",
            )
        ]
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
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="ns::detail::Hidden",
                description="size changed",
            )
        ]
        leaks = detect_internal_leaks(changes, old, new)
        assert leaks == []

    def test_embedded_by_value_severity_hint(self) -> None:
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(
                    name="Public",
                    kind="class",
                    fields=[
                        TypeField(name="impl_", type="ns::detail::Impl"),
                    ],
                ),
                RecordType(name="ns::detail::Impl", kind="struct", size_bits=32),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(
                    name="Public",
                    kind="class",
                    fields=[
                        TypeField(name="impl_", type="ns::detail::Impl"),
                    ],
                ),
                RecordType(name="ns::detail::Impl", kind="struct", size_bits=64),
            ],
        )
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="ns::detail::Impl",
                description="size changed",
            )
        ]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1
        assert "embedded-by-value or via inheritance" in leaks[0].description

    def test_pointer_field_severity_hint(self) -> None:
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(
                    name="Public",
                    kind="class",
                    fields=[
                        TypeField(name="impl_", type="ns::detail::Impl*"),
                    ],
                ),
                RecordType(name="ns::detail::Impl", kind="struct", size_bits=32),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(
                    name="Public",
                    kind="class",
                    fields=[
                        TypeField(name="impl_", type="ns::detail::Impl*"),
                    ],
                ),
                RecordType(
                    name="ns::detail::Impl", kind="struct", vtable=["fn1", "fn2"]
                ),
            ],
        )
        changes = [
            Change(
                kind=ChangeKind.TYPE_VTABLE_CHANGED,
                symbol="ns::detail::Impl",
                description="vtable changed",
            )
        ]
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
                RecordType(name="Public", kind="class", bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=32),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=64),
            ],
        )
        # NB: TYPE_FIELD_* (from diff_types) and STRUCT_FIELD_* (from
        # diff_platform) use different symbol conventions. TYPE_FIELD_*
        # puts the type name in symbol (the field name lives in
        # `description`). STRUCT_FIELD_* puts "Type::field" in symbol.
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="ns::detail::Base",
                description="size",
            ),
            # TYPE_FIELD_ADDED uses symbol=type-name (matches diff_types).
            Change(
                kind=ChangeKind.TYPE_FIELD_ADDED,
                symbol="ns::detail::Base",
                description="field added: ns::detail::Base::y",
            ),
            # STRUCT_FIELD_OFFSET_CHANGED uses symbol="Type::field" (matches diff_platform).
            Change(
                kind=ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
                symbol="ns::detail::Base::y",
                description="offset changed: ns::detail::Base::y",
            ),
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
                RecordType(
                    name="Public",
                    kind="class",
                    fields=[
                        TypeField(name="impl_", type="ns::detail::Impl"),
                    ],
                ),
                RecordType(
                    name="ns::detail::Impl",
                    kind="struct",
                    fields=[TypeField(name="row", type="int", offset_bits=0)],
                ),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(
                    name="Public",
                    kind="class",
                    fields=[
                        TypeField(name="impl_", type="ns::detail::Impl"),
                    ],
                ),
                RecordType(
                    name="ns::detail::Impl",
                    kind="struct",
                    fields=[
                        TypeField(name="row", type="int", offset_bits=0),
                        TypeField(name="col", type="int", offset_bits=32),
                    ],
                ),
            ],
        )
        # Mimic diff_types: TYPE_FIELD_ADDED with symbol = containing type.
        changes = [
            Change(
                kind=ChangeKind.TYPE_FIELD_ADDED,
                symbol="ns::detail::Impl",
                description="Field added: ns::detail::Impl::col",
            )
        ]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1
        assert leaks[0].symbol == "ns::detail::Impl"

    def test_custom_namespace_patterns(self) -> None:
        # Use a project-specific internal namespace name like "priv".
        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", bases=["ns::priv::Base"]),
                RecordType(name="ns::priv::Base", kind="class", size_bits=32),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(name="Public", kind="class", bases=["ns::priv::Base"]),
                RecordType(name="ns::priv::Base", kind="class", size_bits=64),
            ],
        )
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="ns::priv::Base",
                description="size",
            )
        ]
        # Default namespaces: no detection (priv isn't in defaults).
        assert detect_internal_leaks(changes, old, new) == []
        # Custom: detection fires.
        leaks = detect_internal_leaks(
            changes,
            old,
            new,
            internal_namespaces=("priv",),
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
                RecordType(
                    name="Public",
                    kind="class",
                    bases=["ns::detail::Base"],
                    size_bits=32,
                ),
                RecordType(
                    name="ns::detail::Base",
                    kind="class",
                    size_bits=32,
                    fields=[TypeField(name="x", type="int", offset_bits=0)],
                ),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                RecordType(
                    name="Public",
                    kind="class",
                    bases=["ns::detail::Base"],
                    size_bits=64,
                ),
                RecordType(
                    name="ns::detail::Base",
                    kind="class",
                    size_bits=64,
                    fields=[
                        TypeField(name="x", type="int", offset_bits=0),
                        TypeField(name="y", type="int", offset_bits=32),
                    ],
                ),
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
            functions=[
                _public_fn(
                    "mylib_make_descriptor",
                    "mylib::knn_descriptor*",
                )
            ],
            types=[
                RecordType(
                    name="mylib::detail::descriptor_base",
                    kind="class",
                    size_bits=32,
                    fields=[TypeField(name="class_count_", type="int", offset_bits=0)],
                ),
                RecordType(
                    name="mylib::knn_descriptor",
                    kind="class",
                    size_bits=64,
                    bases=["mylib::detail::descriptor_base"],
                    fields=[
                        TypeField(name="neighbor_count_", type="int", offset_bits=32)
                    ],
                ),
            ],
        )
        new = _snap(
            functions=[
                _public_fn(
                    "mylib_make_descriptor",
                    "mylib::knn_descriptor*",
                )
            ],
            types=[
                RecordType(
                    name="mylib::detail::descriptor_base",
                    kind="class",
                    size_bits=64,
                    fields=[
                        TypeField(name="class_count_", type="int", offset_bits=0),
                        TypeField(name="max_iter_", type="int", offset_bits=32),
                    ],
                ),
                RecordType(
                    name="mylib::knn_descriptor",
                    kind="class",
                    size_bits=96,
                    bases=["mylib::detail::descriptor_base"],
                    fields=[
                        TypeField(name="neighbor_count_", type="int", offset_bits=64)
                    ],
                ),
            ],
        )
        result = compare(old, new)
        leaks = [
            c
            for c in result.changes
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
            functions=[
                _public_fn(
                    "mylib_make_table",
                    "mylib::table*",
                )
            ],
            types=[
                RecordType(
                    name="mylib::detail::table_impl",
                    kind="struct",
                    size_bits=128,
                    fields=[
                        TypeField(name="row_count", type="size_t", offset_bits=0),
                        TypeField(name="column_count", type="size_t", offset_bits=64),
                    ],
                ),
                RecordType(
                    name="mylib::table",
                    kind="class",
                    size_bits=128,
                    fields=[
                        TypeField(
                            name="impl_",
                            type="mylib::detail::table_impl",
                            offset_bits=0,
                        ),
                    ],
                ),
            ],
        )
        new = _snap(
            functions=[
                _public_fn(
                    "mylib_make_table",
                    "mylib::table*",
                )
            ],
            types=[
                RecordType(
                    name="mylib::detail::table_impl",
                    kind="struct",
                    size_bits=192,
                    fields=[
                        TypeField(name="row_count", type="size_t", offset_bits=0),
                        TypeField(name="column_count", type="size_t", offset_bits=64),
                        TypeField(name="layout_kind", type="size_t", offset_bits=128),
                    ],
                ),
                RecordType(
                    name="mylib::table",
                    kind="class",
                    size_bits=192,
                    fields=[
                        TypeField(
                            name="impl_",
                            type="mylib::detail::table_impl",
                            offset_bits=0,
                        ),
                    ],
                ),
            ],
        )
        result = compare(old, new)
        leaks = [
            c
            for c in result.changes
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
            functions=[
                _public_fn(
                    "mylib_make_svm",
                    "mylib::detail::algorithm_iface*",
                )
            ],
            types=[
                RecordType(
                    name="mylib::detail::algorithm_iface",
                    kind="class",
                    size_bits=64,
                    vtable=["~algorithm_iface", "run", "status"],
                ),
                RecordType(
                    name="mylib::svm_algorithm",
                    kind="class",
                    size_bits=96,
                    bases=["mylib::detail::algorithm_iface"],
                    vtable=["~svm_algorithm", "run", "status"],
                ),
            ],
        )
        new = _snap(
            functions=[
                _public_fn(
                    "mylib_make_svm",
                    "mylib::detail::algorithm_iface*",
                )
            ],
            types=[
                RecordType(
                    name="mylib::detail::algorithm_iface",
                    kind="class",
                    size_bits=64,
                    vtable=["~algorithm_iface", "run", "progress", "status"],
                ),
                RecordType(
                    name="mylib::svm_algorithm",
                    kind="class",
                    size_bits=96,
                    bases=["mylib::detail::algorithm_iface"],
                    vtable=["~svm_algorithm", "run", "progress", "status"],
                ),
            ],
        )
        result = compare(old, new)
        leaks = [
            c
            for c in result.changes
            if c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        ]
        assert leaks, "case76 pattern: leak must be detected"
        assert leaks[0].symbol == "mylib::detail::algorithm_iface"
        assert "mylib::svm_algorithm" in leaks[0].description


# ---------------------------------------------------------------------------
# compute_call_graph_leak_paths / detect_call_graph_leaks (ADR-044 P1 items 1-2)
# ---------------------------------------------------------------------------


def _decl_node(node_id: str, label: str, visibility: str):
    from abicheck.buildsource.source_graph import GraphNode

    return GraphNode(
        id=node_id, kind="source_decl", label=label, attrs={"visibility": visibility}
    )


def _graph_snap(nodes: list, edges: list) -> AbiSnapshot:
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import SourceGraphSummary

    graph = SourceGraphSummary(nodes=list(nodes), edges=list(edges))
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        build_source=BuildSourcePack(root="", source_graph=graph),
    )


class TestComputeCallGraphLeakPaths:
    def test_no_build_source_returns_empty(self) -> None:
        from abicheck.internal_leak import compute_call_graph_leak_paths

        assert compute_call_graph_leak_paths(_snap()) == {}

    def test_no_relevant_edges_returns_empty(self) -> None:
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import GraphEdge, SourceGraphSummary
        from abicheck.internal_leak import compute_call_graph_leak_paths

        graph = SourceGraphSummary(
            nodes=[_decl_node("decl://pub", "pubFn", "public_header")],
            edges=[GraphEdge(src="decl://pub", dst="decl://x", kind="DECL_HAS_TYPE")],
        )
        snap = AbiSnapshot(
            library="libtest.so",
            version="1.0",
            build_source=BuildSourcePack(root="", source_graph=graph),
        )
        assert compute_call_graph_leak_paths(snap) == {}

    def test_public_entry_calling_internal_decl_is_a_leak_path(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        paths = compute_call_graph_leak_paths(snap)
        assert "ns::detail::helper" in paths
        assert "pubFn" in paths["ns::detail::helper"][0]
        assert "DECL_CALLS_DECL" in paths["ns::detail::helper"][0]

    def test_ordinary_out_of_line_exported_entry_is_not_a_leak_path(self) -> None:
        """Codex review: an ordinary, out-of-line exported function's own body
        is compiled into the *library's* binary only, never into any
        consumer's -- its internal calls (e.g. to ns::detail::helper) are not
        public-reachable the way an inline/template entry's calls are.
        consumer_compiled_body=False (the build-integrated source_graph.py
        signal for "not inline, not a template") must exclude it from the
        walk's entry set entirely."""
        from abicheck.buildsource.source_graph import GraphEdge, GraphNode
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                GraphNode(
                    id="decl://pub",
                    kind="source_decl",
                    label="api",
                    attrs={
                        "visibility": "public_header",
                        "consumer_compiled_body": False,
                    },
                ),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        assert compute_call_graph_leak_paths(snap) == {}

    def test_inline_entry_with_explicit_flag_is_still_a_leak_path(self) -> None:
        """The positive counterpart: consumer_compiled_body=True (an inline
        method/template entry) still seeds the walk, same as before this
        signal existed -- this ADR's own headline scenario."""
        from abicheck.buildsource.source_graph import GraphEdge, GraphNode
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                GraphNode(
                    id="decl://pub",
                    kind="source_decl",
                    label="pubInline",
                    attrs={
                        "visibility": "public_header",
                        "consumer_compiled_body": True,
                    },
                ),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        paths = compute_call_graph_leak_paths(snap)
        assert "ns::detail::helper" in paths

    def test_walk_stops_expanding_past_non_consumer_compiled_intermediate(
        self,
    ) -> None:
        """Codex review, fresh evidence: restricting the *entry set* to
        consumer-compiled nodes is not enough on its own -- the walk itself
        must also stop *expanding past* a non-consumer-compiled intermediate.
        A public inline wrap() calls an ordinary out-of-line exported api()
        (consumer_compiled_body=False), which in turn calls an internal
        helper(). api() itself IS reachable from wrap() (a consumer really
        does link against api()'s exported symbol), but helper() is not --
        that call happens entirely inside the library's binary, in a
        function whose own body no consumer ever compiles. Before this fix,
        the walk expanded transitively past every intermediate regardless of
        its own consumer_compiled_body, so helper()'s removal would have
        been (wrongly) reported reachable through wrap()."""
        from abicheck.buildsource.source_graph import GraphEdge, GraphNode
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                GraphNode(
                    id="decl://wrap",
                    kind="source_decl",
                    label="demo::wrap",
                    attrs={
                        "visibility": "public_header",
                        "consumer_compiled_body": True,
                    },
                ),
                GraphNode(
                    id="decl://api",
                    kind="source_decl",
                    label="demo::api",
                    attrs={
                        "visibility": "public_header",
                        "consumer_compiled_body": False,
                    },
                ),
                _decl_node("decl://helper", "demo::detail::helper", "source"),
            ],
            [
                GraphEdge(src="decl://wrap", dst="decl://api", kind="DECL_CALLS_DECL"),
                GraphEdge(
                    src="decl://api", dst="decl://helper", kind="DECL_CALLS_DECL"
                ),
            ],
        )
        paths = compute_call_graph_leak_paths(snap)
        assert "demo::detail::helper" not in paths
        # api() itself is a real, direct dependency of wrap() and is not in an
        # internal namespace, so it never becomes a leak-path key either way --
        # this asserts the walk didn't silently drop it from the graph, only
        # that it correctly stopped expanding past it.
        assert paths == {}

    def test_walk_stops_at_call_graph_fallback_node_with_no_signal(self) -> None:
        """Codex review, fresh evidence: the previous fix's default
        (permissive when consumer_compiled_body is absent) covered the
        explicit-False case above, but a real call_graph.py fallback node
        (augment_graph_with_calls, created for a caller/callee identity with
        no other declaration node backing it) has NO consumer_compiled_body
        attr at all -- neither True nor False -- while still being a real,
        build-integrated project function whose out-of-line body is not
        necessarily consumer-compiled. A public inline wrap() calling such a
        fallback-shaped intermediate (demo::helper_a, provenance="call_graph",
        no consumer_compiled_body key) which itself calls an internal
        helper() must stop expanding at helper_a() -- treating "no signal"
        as "safe" for this one provenance would silently reintroduce the
        exact over-reach the previous fix closed for the explicit-False
        shape."""
        from abicheck.buildsource.source_graph import GraphEdge, GraphNode
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                GraphNode(
                    id="decl://wrap",
                    kind="source_decl",
                    label="demo::wrap",
                    attrs={
                        "visibility": "public_header",
                        "consumer_compiled_body": True,
                    },
                ),
                GraphNode(
                    id="decl://helper_a",
                    kind="source_decl",
                    label="demo::helper_a",
                    provenance="call_graph",
                    attrs={"defined_in_project": True},
                ),
                _decl_node("decl://helper", "demo::detail::helper", "source"),
            ],
            [
                GraphEdge(
                    src="decl://wrap", dst="decl://helper_a", kind="DECL_CALLS_DECL"
                ),
                GraphEdge(
                    src="decl://helper_a", dst="decl://helper", kind="DECL_CALLS_DECL"
                ),
            ],
        )
        paths = compute_call_graph_leak_paths(snap)
        assert paths == {}

    @pytest.mark.parametrize("backend_provenance", ["kythe", "codeql"])
    def test_walk_stops_at_kythe_codeql_node_with_no_signal(
        self,
        backend_provenance: str,
    ) -> None:
        """Codex review, fresh evidence: the call_graph.py fallback-node fix
        above only excused that one provenance from the permissive
        "no consumer_compiled_body attr -> treat as reachable" default --
        graph_backends.py's ingest_kythe_entries/ingest_codeql_call_results
        create the identical shape (a bare source_decl node with no
        consumer_compiled_body attr at all) for an imported external-indexer
        edge, tagged provenance="kythe"/"codeql" instead of "call_graph".
        Neither export format says whether the referenced declaration's body
        is inline/template, so a public inline wrap() calling such a
        Kythe/CodeQL-sourced intermediate (demo::helper_a) which itself calls
        an internal helper() must stop expanding at helper_a() for the same
        reason as the call_graph fallback shape."""
        from abicheck.buildsource.source_graph import GraphEdge, GraphNode
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                GraphNode(
                    id="decl://wrap",
                    kind="source_decl",
                    label="demo::wrap",
                    attrs={
                        "visibility": "public_header",
                        "consumer_compiled_body": True,
                    },
                ),
                GraphNode(
                    id="decl://helper_a",
                    kind="source_decl",
                    label="demo::helper_a",
                    provenance=backend_provenance,
                    attrs={"defined_in_project": True},
                ),
                _decl_node("decl://helper", "demo::detail::helper", "source"),
            ],
            [
                GraphEdge(
                    src="decl://wrap", dst="decl://helper_a", kind="DECL_CALLS_DECL"
                ),
                GraphEdge(
                    src="decl://helper_a", dst="decl://helper", kind="DECL_CALLS_DECL"
                ),
            ],
        )
        paths = compute_call_graph_leak_paths(snap)
        assert paths == {}

    def test_reference_edge_also_counts(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::Const", "source"),
            ],
            [
                GraphEdge(
                    src="decl://pub", dst="decl://int", kind="DECL_REFERENCES_DECL"
                )
            ],
        )
        paths = compute_call_graph_leak_paths(snap)
        assert "ns::detail::Const" in paths

    def test_non_internal_target_not_recorded(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::otherPublicFn", "public_header"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
        )
        assert compute_call_graph_leak_paths(snap) == {}

    def test_no_public_entry_returns_empty(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                _decl_node("decl://a", "ns::detail::a", "source"),
                _decl_node("decl://b", "ns::detail::b", "source"),
            ],
            [GraphEdge(src="decl://a", dst="decl://b", kind="DECL_CALLS_DECL")],
        )
        assert compute_call_graph_leak_paths(snap) == {}

    def test_custom_namespaces_recognizes_convention(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::priv::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        assert compute_call_graph_leak_paths(snap) == {}
        assert "ns::priv::helper" in compute_call_graph_leak_paths(snap, ("priv",))

    def test_result_also_keyed_by_mangled_exported_symbol(self) -> None:
        """Codex review, fresh evidence: a real ``FUNC_REMOVED`` Change's
        ``symbol`` is the mangled linker name (diff_symbols.py), not the
        node's demangled qualified-name label — keying the result only by
        ``node.label`` would silently never match a real compiled C++
        removal. A target node's own SOURCE_DECL_MAPS_TO_SYMBOL edge (the
        same binary_symbol:// identity localize_symbol() already uses) must
        also produce a lookup key."""
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::train_ops_dispatcher", "source"),
            ],
            [
                GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL"),
                GraphEdge(
                    src="decl://int",
                    dst="binary_symbol://_ZN2ns6detail19train_ops_dispatcherEv",
                    kind="SOURCE_DECL_MAPS_TO_SYMBOL",
                ),
            ],
        )
        paths = compute_call_graph_leak_paths(snap)
        # Both the demangled qualified-name key (compute_leak_paths's own
        # convention, and what a hand-authored Change might use) ...
        assert "ns::detail::train_ops_dispatcher" in paths
        # ... and the mangled key a real diff_symbols.py FUNC_REMOVED
        # Change.symbol actually holds must resolve to the same proof paths.
        assert "_ZN2ns6detail19train_ops_dispatcherEv" in paths
        assert (
            paths["_ZN2ns6detail19train_ops_dispatcherEv"]
            == paths["ns::detail::train_ops_dispatcher"]
        )

    def test_no_symbol_mapping_keys_only_by_label(self) -> None:
        """A call-graph-only target with no exported symbol at all (e.g.
        fully inlined, no linkage) gets no mangled key — there is no
        FUNC_REMOVED-shaped Change that could ever look one up anyway."""
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        paths = compute_call_graph_leak_paths(snap)
        assert list(paths.keys()) == ["ns::detail::helper"]

    def test_mangled_only_label_demangled_for_classification(self, monkeypatch) -> None:
        """Codex review, fresh evidence: augment_graph_with_calls's
        call-graph-only fallback node (added when a callee has no
        SOURCE_DECLARES-backed node elsewhere) gets label=ident from
        function_decl_identity, which is the *mangled* name for any
        ordinary C++ function -- no "::" at all, so is_internal_type would
        reject it before classification without demangling first. The
        stored key stays the original mangled label (matching a real
        FUNC_REMOVED's Change.symbol directly)."""
        import abicheck.demangle as demangle_mod
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        mangled = "_ZN2ns6detail6helperEv"
        monkeypatch.setattr(demangle_mod, "demangle", lambda s: "ns::detail::helper()")
        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", mangled, "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        paths = compute_call_graph_leak_paths(snap)
        assert mangled in paths
        assert "pubFn" in paths[mangled][0]

    def test_mangled_label_not_internal_after_demangling_stays_dropped(
        self, monkeypatch
    ) -> None:
        """A mangled label that demangles to a non-internal qualified name
        must still be excluded -- demangling only changes what's classified
        as internal, not a blanket "keep everything mangled" allowance."""
        import abicheck.demangle as demangle_mod
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        mangled = "_ZN2ns9PublicFooEv"
        monkeypatch.setattr(demangle_mod, "demangle", lambda s: "ns::PublicFoo()")
        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", mangled, "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        assert compute_call_graph_leak_paths(snap) == {}

    def test_public_fn_with_internal_param_type_not_misclassified(
        self,
        monkeypatch,
    ) -> None:
        """Codex review, fresh evidence: demangle() returns the *full
        signature* ("ns::api::foo(ns::detail::T*)"), not just the qualified
        name. is_internal_type's segment scan would otherwise find "detail"
        inside the *parameter* type and misclassify an ordinary public
        function as an internal leak target merely because it takes an
        internal-namespaced type -- even though the function itself lives in
        the public ns::api namespace. The signature's own parameter list
        must be stripped before classification."""
        import abicheck.demangle as demangle_mod
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        mangled = "_ZN2ns3api3fooEPNS_6detail1TE"
        monkeypatch.setattr(
            demangle_mod, "demangle", lambda s: "ns::api::foo(ns::detail::T*)"
        )
        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://target", mangled, "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://target", kind="DECL_CALLS_DECL")],
        )
        assert compute_call_graph_leak_paths(snap) == {}

    def test_hash_suffixed_label_also_keyed_by_stripped_name(self) -> None:
        """Codex review, fresh evidence: function_decl_identity's third node
        shape -- a declaration with no distinct mangled name (e.g. extern
        "C") gets label="{qualified_name}#sha256:{digest}", not the bare
        qualified name a real Change.symbol/qualified_name would ever carry.
        The hash-stripped name must also be a lookup key."""
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        label = "ns::detail::c_helper#sha256:abcdef1234567890"
        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", label, "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        paths = compute_call_graph_leak_paths(snap)
        assert "ns::detail::c_helper" in paths
        assert paths["ns::detail::c_helper"] == paths[label]


class TestDetectCallGraphLeaks:
    def test_func_removed_on_internal_decl_reachable_via_call_graph(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import detect_call_graph_leaks

        old = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::train_ops_dispatcher", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        new = _graph_snap(
            [_decl_node("decl://pub", "pubFn", "public_header")],
            [],
        )
        triggering = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="ns::detail::train_ops_dispatcher",
            description="removed",
        )
        extra = detect_call_graph_leaks([triggering], old, new)
        assert len(extra) == 1
        overlay = extra[0]
        assert overlay.kind == ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API
        assert overlay.symbol == "ns::detail::train_ops_dispatcher"
        assert overlay.public_reachable is True
        assert overlay.reachability_kind == "symbol_availability"
        assert "pubFn" in (overlay.reachability_proof_path or "")

    def test_func_removed_matches_via_mangled_symbol_not_label(self) -> None:
        """Codex review, fresh evidence: the real-world shape. A real
        FUNC_REMOVED Change's symbol is the mangled linker name, which does
        NOT equal the graph node's demangled qualified-name label — the
        overlay finding must still be produced via the node's own
        SOURCE_DECL_MAPS_TO_SYMBOL edge, not only when a test hand-picks a
        matching label/symbol pair."""
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import detect_call_graph_leaks

        mangled = "_ZN2ns6detail19train_ops_dispatcherEv"
        old = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::train_ops_dispatcher", "source"),
            ],
            [
                GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL"),
                GraphEdge(
                    src="decl://int",
                    dst=f"binary_symbol://{mangled}",
                    kind="SOURCE_DECL_MAPS_TO_SYMBOL",
                ),
            ],
        )
        new = _graph_snap([_decl_node("decl://pub", "pubFn", "public_header")], [])
        # symbol is the mangled name, exactly as diff_symbols.py builds it —
        # NOT "ns::detail::train_ops_dispatcher" (the node's label).
        triggering = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol=mangled, description="removed"
        )
        extra = detect_call_graph_leaks([triggering], old, new)
        assert len(extra) == 1
        overlay = extra[0]
        assert overlay.kind == ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API
        assert overlay.symbol == mangled
        assert overlay.public_reachable is True

    def test_no_op_without_triggering_change(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import detect_call_graph_leaks

        old = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        new = _graph_snap([], [])
        assert detect_call_graph_leaks([], old, new) == []

    def test_non_breaking_kind_is_not_a_trigger(self) -> None:
        """A COMPATIBLE finding on an internal decl must not compose an
        overlay finding — the authority rule (only an already artifact-proven
        BREAKING change may be explained/correlated, never manufactured)."""
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import detect_call_graph_leaks

        old = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        new = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        compatible_change = Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="ns::detail::helper",
            description="added an overload",
        )
        assert detect_call_graph_leaks([compatible_change], old, new) == []

    def test_api_break_kind_is_not_a_trigger(self) -> None:
        """Codex review, fresh evidence: API_BREAK_KINDS is the
        SOURCE_CONTRACT tier, not artifact-proven — most of its members
        (e.g. inline_function_removed) have no removed linker symbol at all,
        so composing one into this overlay's "fails to resolve at load
        time" claim would be a false binary-load-time claim. Only
        BREAKING_KINDS may trigger, even with real call-graph evidence."""
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.checker_policy import BREAKING_KINDS
        from abicheck.internal_leak import detect_call_graph_leaks

        old = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        new = _graph_snap([_decl_node("decl://pub", "pubFn", "public_header")], [])
        assert ChangeKind.INLINE_FUNCTION_REMOVED not in BREAKING_KINDS
        api_break_change = Change(
            kind=ChangeKind.INLINE_FUNCTION_REMOVED,
            symbol="ns::detail::helper",
            description="inline function removed",
        )
        assert detect_call_graph_leaks([api_break_change], old, new) == []

    def test_header_graph_mode_matches_via_qualified_name(self) -> None:
        """Codex review, fresh evidence: header_graph.py (--header-graph, no
        real build) never creates a SOURCE_DECL_MAPS_TO_SYMBOL edge, so the
        mangled-symbol-key fix above is a no-op in that mode. A real
        FUNC_REMOVED's Change.qualified_name (set by EnrichSourceLocations
        from Function.name, independent of graph provenance) must serve as
        a fallback lookup key against the label-keyed result."""
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import detect_call_graph_leaks

        mangled = "_ZN2ns6detail6helperEv"
        # No SOURCE_DECL_MAPS_TO_SYMBOL edge at all -- the header-graph shape.
        old = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        new = _graph_snap([_decl_node("decl://pub", "pubFn", "public_header")], [])
        triggering = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=mangled,
            qualified_name="ns::detail::helper",
            description="removed",
        )
        extra = detect_call_graph_leaks([triggering], old, new)
        assert len(extra) == 1
        overlay = extra[0]
        assert overlay.kind == ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API
        assert overlay.public_reachable is True

    def test_no_call_graph_evidence_no_overlay(self) -> None:
        from abicheck.internal_leak import detect_call_graph_leaks

        triggering = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="ns::detail::train_ops_dispatcher",
            description="removed",
        )
        assert detect_call_graph_leaks([triggering], _snap(), _snap()) == []


class TestBuildLeakChangePreferredPath:
    """CodeRabbit review on PR #620: the INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
    finding's own displayed proof path must also prefer value-propagating
    evidence over a shorter indirect one -- select_preferred_path (ADR-046
    D6) was wired into MarkReachability's separate layout walk but not into
    this, the leak detector's own synthetic Change builder."""

    def test_reachability_proof_path_prefers_value_propagating(self) -> None:
        from abicheck.internal_leak import _build_leak_change, _format_path

        indirect_short = ["Public", "indirect:ptr", "Internal"]
        value_longer = ["Public", "field:x", "Mid", "field:y", "Internal"]
        change = _build_leak_change(
            "ns::detail::Internal",
            [
                Change(
                    kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Internal",
                    description="size changed",
                )
            ],
            [indirect_short, value_longer],
            _snap(),
            embedded_by_value=True,
        )
        assert change.reachability_proof_path == _format_path(value_longer)

    def test_more_paths_count_still_reflects_the_full_collection(self) -> None:
        from abicheck.internal_leak import _build_leak_change

        paths = [
            ["Public", "field:x", "Internal"],
            ["Public", "field:y", "Internal"],
            ["Public", "field:z", "Internal"],
            ["Public", "indirect:ptr", "Internal"],
        ]
        change = _build_leak_change(
            "ns::detail::Internal",
            [
                Change(
                    kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Internal",
                    description="size changed",
                )
            ],
            paths,
            _snap(),
            embedded_by_value=True,
        )
        assert "+1 more paths" in (change.description or "")


class TestSelectPreferredPath:
    """ADR-046 D6 (partial): prefer a value-propagating path over a shorter
    indirect-only one instead of plain ``min(paths, key=len)``."""

    def test_prefers_value_propagating_over_shorter_indirect(self) -> None:
        indirect_short = ["Public", "indirect:ptr", "Internal"]
        value_longer = ["Public", "field:x", "Mid", "field:y", "Internal"]
        assert select_preferred_path([indirect_short, value_longer]) == value_longer

    def test_shortest_wins_within_the_same_tier(self) -> None:
        short = ["Public", "field:x", "Internal"]
        long_ = ["Public", "field:x", "Mid", "field:y", "Internal"]
        assert select_preferred_path([long_, short]) == short

    def test_pointer_or_signature_beats_pure_indirect(self) -> None:
        # Neither value-propagating nor indirection-marked (e.g. a bare
        # signature-seed path) ranks above an indirect-marked one.
        plain = ["Public", "signature", "Internal"]
        indirect = ["Public", "indirect:ptr", "Internal"]
        assert select_preferred_path([indirect, plain]) == plain

    def test_single_path_returned_unchanged(self) -> None:
        only = ["Public", "field:x", "Internal"]
        assert select_preferred_path([only]) == only


# ---------------------------------------------------------------------------
# TraversalPolicy (ADR-046 D5, partial)
# ---------------------------------------------------------------------------


class TestTraversalPolicy:
    """CALL_GRAPH_TRAVERSAL_POLICY reifies compute_call_graph_leak_paths's
    own edge-kind/stop rules; a caller-supplied policy with a stricter
    minimum_confidence or a custom stop predicate must actually change the
    walk, not just be accepted and ignored."""

    def test_call_graph_policy_allows_call_and_reference_edges(self) -> None:
        from abicheck.internal_leak import CALL_GRAPH_TRAVERSAL_POLICY

        assert CALL_GRAPH_TRAVERSAL_POLICY.allowed_edges == frozenset(
            {"DECL_CALLS_DECL", "DECL_REFERENCES_DECL"}
        )

    def test_minimum_confidence_excludes_low_confidence_edges(self) -> None:
        from abicheck.buildsource.graph_facts import CONF_HIGH, CONF_REDUCED
        from abicheck.buildsource.source_graph import GraphEdge, SourceGraphSummary
        from abicheck.internal_leak import (
            TraversalPolicy,
            _consumer_compiled_reachability,
        )

        graph = SourceGraphSummary(
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://mid", "ns::detail::mid", "source"),
            ],
            edges=[
                GraphEdge(
                    src="decl://pub",
                    dst="decl://mid",
                    kind="DECL_CALLS_DECL",
                    confidence=CONF_REDUCED,
                )
            ],
        )
        node_by_id = {n.id: n for n in graph.nodes}
        permissive = TraversalPolicy(
            allowed_edges=frozenset({"DECL_CALLS_DECL"}),
            stop_conditions=lambda node_id, node_by_id: False,
        )
        strict = TraversalPolicy(
            allowed_edges=frozenset({"DECL_CALLS_DECL"}),
            stop_conditions=lambda node_id, node_by_id: False,
            minimum_confidence=CONF_HIGH,
        )
        permissive_reach = _consumer_compiled_reachability(
            graph, permissive, ["decl://pub"], node_by_id
        )
        strict_reach = _consumer_compiled_reachability(
            graph, strict, ["decl://pub"], node_by_id
        )
        assert "decl://mid" in permissive_reach["decl://pub"][0]
        assert "decl://mid" not in strict_reach["decl://pub"][0]

    def test_stop_conditions_halts_expansion_but_keeps_node_reachable(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge, SourceGraphSummary
        from abicheck.internal_leak import (
            TraversalPolicy,
            _consumer_compiled_reachability,
        )

        graph = SourceGraphSummary(
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://mid", "ns::detail::mid", "source"),
                _decl_node("decl://leaf", "ns::detail::leaf", "source"),
            ],
            edges=[
                GraphEdge(src="decl://pub", dst="decl://mid", kind="DECL_CALLS_DECL"),
                GraphEdge(src="decl://mid", dst="decl://leaf", kind="DECL_CALLS_DECL"),
            ],
        )
        node_by_id = {n.id: n for n in graph.nodes}
        stop_at_mid = TraversalPolicy(
            allowed_edges=frozenset({"DECL_CALLS_DECL"}),
            stop_conditions=lambda node_id, node_by_id: node_id == "decl://mid",
        )
        reachable, _ = _consumer_compiled_reachability(
            graph, stop_at_mid, ["decl://pub"], node_by_id
        )["decl://pub"]
        assert "decl://mid" in reachable
        assert "decl://leaf" not in reachable

    def test_compute_call_graph_leak_paths_uses_the_shared_policy(self) -> None:
        """End-to-end: the public entry point still routes through
        CALL_GRAPH_TRAVERSAL_POLICY, not a re-derived edge set."""
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_REFERENCES_DECL")],
        )
        paths = compute_call_graph_leak_paths(snap)
        assert "ns::detail::helper" in paths
