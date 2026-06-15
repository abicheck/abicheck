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

"""Regression tests for the DWARF-only fallback path in the leak detector.

CodeRabbit PR #256 finding: on the DWARF-only fallback path (snap.types is
empty, snap.dwarf.structs provides the type map) _seed_queue_from_public_types
was unconditionally seeding every non-internal type as a BFS root, including
private implementation types that have no real public entry point.  That
produced spurious INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API findings.

The fix: _build_type_map() returns is_dwarf_fallback=True, and
_seed_queue_from_public_types() exits early in that case.  Function- and
variable-based seeding still runs, so a genuine leak (where a public
function's signature leads to an internal type) is still detected.
"""
from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.dwarf_metadata import DwarfMetadata, FieldInfo, StructLayout
from abicheck.internal_leak import (
    _build_type_map,
    _seed_queue_from_public_types,
    compute_leak_paths,
    detect_internal_leaks,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dwarf_snap(
    *,
    structs: dict[str, StructLayout] | None = None,
    functions: list[Function] | None = None,
) -> AbiSnapshot:
    """Return a snapshot with empty snap.types but a populated snap.dwarf."""
    dwarf = DwarfMetadata(
        structs=dict(structs or {}),
        has_dwarf=True,
    )
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        types=[],  # <-- deliberately empty so DWARF fallback activates
        functions=list(functions or []),
        dwarf=dwarf,
    )


def _pub_fn(name: str, ret: str = "void") -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[],
        visibility=Visibility.PUBLIC,
    )


def _struct_layout(
    name: str,
    *,
    byte_size: int = 8,
    fields: list[tuple[str, str, int]] | None = None,
) -> StructLayout:
    """Build a StructLayout; fields = list of (name, type_name, byte_offset)."""
    fi_list = [
        FieldInfo(name=n, type_name=t, byte_offset=o, byte_size=8)
        for n, t, o in (fields or [])
    ]
    return StructLayout(name=name, byte_size=byte_size, fields=fi_list)


# ---------------------------------------------------------------------------
# _build_type_map flag
# ---------------------------------------------------------------------------


class TestBuildTypeMapFlag:
    """Unit-test that _build_type_map correctly signals the fallback path."""

    def test_header_path_returns_false(self) -> None:
        snap = AbiSnapshot(
            library="l.so", version="1",
            types=[RecordType(name="Public", kind="class")],
        )
        _, is_fallback = _build_type_map(snap)
        assert is_fallback is False

    def test_dwarf_only_returns_true(self) -> None:
        snap = _dwarf_snap(
            structs={"ns::detail::Impl": _struct_layout("ns::detail::Impl")},
        )
        _, is_fallback = _build_type_map(snap)
        assert is_fallback is True

    def test_empty_snap_returns_false(self) -> None:
        snap = AbiSnapshot(library="l.so", version="1", types=[])
        _, is_fallback = _build_type_map(snap)
        assert is_fallback is False

    def test_dwarf_none_returns_false(self) -> None:
        snap = AbiSnapshot(library="l.so", version="1", types=[], dwarf=None)
        _, is_fallback = _build_type_map(snap)
        assert is_fallback is False


# ---------------------------------------------------------------------------
# _seed_queue_from_public_types skips on DWARF fallback
# ---------------------------------------------------------------------------


class TestSeedQueueSkipsOnDwarfFallback:
    """Unit-test the early-return guard in _seed_queue_from_public_types."""

    def test_skips_when_is_dwarf_fallback_true(self) -> None:
        import collections
        snap = _dwarf_snap(
            structs={"PublicLooking": _struct_layout("PublicLooking")},
        )
        type_map, _ = _build_type_map(snap)
        queue: collections.deque[tuple[str, list[str]]] = collections.deque()
        _seed_queue_from_public_types(
            type_map,
            {"detail", "impl", "internal"},
            queue,
            is_dwarf_fallback=True,
        )
        assert len(queue) == 0, "DWARF-fallback seeding must be suppressed"

    def test_seeds_when_is_dwarf_fallback_false(self) -> None:
        import collections
        snap = AbiSnapshot(
            library="l.so", version="1",
            types=[RecordType(name="PublicType", kind="class")],
        )
        type_map, _ = _build_type_map(snap)
        queue: collections.deque[tuple[str, list[str]]] = collections.deque()
        _seed_queue_from_public_types(
            type_map,
            {"detail", "impl", "internal"},
            queue,
            is_dwarf_fallback=False,
        )
        assert len(queue) == 1
        assert queue[0][0] == "PublicType"


# ---------------------------------------------------------------------------
# Core regression: no spurious finding when DWARF-only fallback is active
# and the private impl type has no real public entry point
# ---------------------------------------------------------------------------


class TestDwarfFallbackNoSpuriousLeak:
    """Regression scenario from the CodeRabbit finding.

    snap.types is empty; snap.dwarf.structs contains a private
    ``ns::detail::PrivateImpl`` type.  A public function returns ``int``
    (not the internal type).  Before the fix, _seed_queue_from_public_types
    would enqueue every DWARF-synthesised non-internal record as a BFS root
    — but ``ns::detail::PrivateImpl`` is internal and never reachable from
    the real public surface.  No spurious finding must be emitted.
    """

    def _make_snap(self, size_bits: int) -> AbiSnapshot:
        return _dwarf_snap(
            structs={
                "ns::detail::PrivateImpl": _struct_layout(
                    "ns::detail::PrivateImpl",
                    byte_size=size_bits // 8,
                ),
            },
            functions=[_pub_fn("public_api", "int")],
        )

    def test_compute_leak_paths_no_spurious_paths(self) -> None:
        snap = self._make_snap(32)
        paths = compute_leak_paths(snap)
        # ns::detail::PrivateImpl is not reachable from any public surface
        # anchor — it must NOT appear in the reachability map.
        assert "ns::detail::PrivateImpl" not in paths, (
            f"Spurious path found: {paths.get('ns::detail::PrivateImpl')}"
        )

    def test_detect_internal_leaks_no_spurious_finding(self) -> None:
        old = self._make_snap(32)
        new = self._make_snap(64)
        # Simulate a layout change on the private impl type.
        changes = [Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::PrivateImpl",
            description="size changed from 32 to 64 bits",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert leaks == [], (
            "INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API must NOT fire when the "
            "internal type is not reachable from the public ABI surface "
            f"(got: {leaks})"
        )


# ---------------------------------------------------------------------------
# Positive case: genuine leak via function signature on DWARF-only path
# ---------------------------------------------------------------------------


class TestDwarfFallbackGenuineLeakDetected:
    """On the DWARF-only fallback path, a real leak (where a public function's
    return type leads to an internal type via a field) must still be detected.

    The function-based seeding (_seed_queue_from_functions) is NOT suppressed,
    so this should work even when public-type seeding is skipped.
    """

    def _make_snap(self, impl_byte_size: int) -> AbiSnapshot:
        # A public function returns "ns::PublicHandle" which is a DWARF-only
        # type that embeds "ns::detail::Impl" by value via a field.
        return _dwarf_snap(
            structs={
                "ns::detail::Impl": _struct_layout(
                    "ns::detail::Impl",
                    byte_size=impl_byte_size,
                ),
                "ns::PublicHandle": _struct_layout(
                    "ns::PublicHandle",
                    byte_size=impl_byte_size + 8,
                    fields=[("impl_", "ns::detail::Impl", 0)],
                ),
            },
            functions=[_pub_fn("get_handle", "ns::PublicHandle")],
        )

    def test_compute_leak_paths_finds_genuine_path(self) -> None:
        snap = self._make_snap(32)
        paths = compute_leak_paths(snap)
        assert "ns::detail::Impl" in paths, (
            "Genuine leak via function signature must be detected on "
            f"DWARF-only path; got paths={paths}"
        )
        # The path must be anchored to the public function.
        path_strs = [" ".join(p) for p in paths["ns::detail::Impl"]]
        assert any("fn:get_handle" in s for s in path_strs), (
            f"Path must start from the public function; got: {path_strs}"
        )

    def test_detect_internal_leaks_genuine_finding_emitted(self) -> None:
        old = self._make_snap(32)
        new = self._make_snap(64)
        changes = [Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Impl",
            description="size changed",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1
        assert leaks[0].kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        assert leaks[0].symbol == "ns::detail::Impl"
        # The path description must mention the public handle type.
        assert "ns::PublicHandle" in leaks[0].description


# ---------------------------------------------------------------------------
# P2 (UXL field run): pointer-mediated internal layout leak is suppressed
# ---------------------------------------------------------------------------


class TestPointerMediatedLayoutLeakSuppressed:
    """oneTBB ``thread_request_serializer`` shape: a public type holds a
    ``unique_ptr`` to an internal proxy, the proxy embeds an internal type by
    value, and that internal type's *layout* changes. The change sits behind a
    pointer, so it does not propagate to the public holder — the leak must be
    suppressed. An identity/vtable change on the same type still fires.
    """

    def _snap(self, *, serializer_field_type: str, serializer_vtable=None) -> AbiSnapshot:
        return AbiSnapshot(
            library="libtbb.so",
            version="1.0",
            functions=[
                Function(
                    name="make", mangled="make", return_type="Public*",
                    params=[], visibility=Visibility.PUBLIC,
                )
            ],
            types=[
                RecordType(name="Public", kind="class", fields=[
                    # held through a smart pointer -> indirection
                    TypeField(name="impl_", type="std::unique_ptr<ns::detail::Proxy>"),
                ]),
                RecordType(name="ns::detail::Proxy", kind="class", fields=[
                    # embedded by value below the pointer
                    TypeField(name="ser", type="ns::detail::Serializer"),
                ]),
                RecordType(
                    name="ns::detail::Serializer", kind="class",
                    fields=[TypeField(name="count", type=serializer_field_type)],
                    vtable=serializer_vtable,
                ),
            ],
        )

    def test_nested_value_below_pointer_is_suppressed(self) -> None:
        # Per-hop path model: the changed type sits by value inside a proxy held
        # through a unique_ptr. The edge into the proxy is marked indirect, so the
        # whole sub-path is behind a pointer — a layout-only change is demoted.
        old = self._snap(serializer_field_type="int")
        new = self._snap(serializer_field_type="std::atomic<int>")
        changes = [Change(
            kind=ChangeKind.TYPE_FIELD_TYPE_CHANGED,
            symbol="ns::detail::Serializer",
            description="count: int -> std::atomic<int>",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert leaks == [], (
            "a layout change to a type embedded by value below a unique_ptr does "
            f"not reach the public holder — must be demoted (got: {leaks})"
        )

    def test_decomposed_unique_ptr_is_suppressed(self) -> None:
        # The real oneTBB shape: libstdc++ decomposes unique_ptr<Proxy> into
        # _Tuple_impl<0, Proxy*, Deleter> / _Head_base<0, Proxy*, false>; the
        # pointer is a NESTED template arg. Per-hop indirection attributes it to
        # Proxy, so the Serializer embedded by value in Proxy is behind a pointer.
        def _snap(count_type: str) -> AbiSnapshot:
            return AbiSnapshot(
                library="libtbb.so", version="1.0",
                functions=[Function(
                    name="make", mangled="make", return_type="Public*",
                    params=[], visibility=Visibility.PUBLIC,
                )],
                types=[
                    RecordType(name="Public", kind="class", fields=[
                        TypeField(name="t", type="std::_Tuple_impl<0, ns::detail::Proxy*, ns::detail::Deleter>"),
                    ]),
                    RecordType(name="ns::detail::Proxy", kind="class", fields=[
                        TypeField(name="ser", type="ns::detail::Serializer"),
                    ]),
                    RecordType(name="ns::detail::Serializer", kind="class",
                               fields=[TypeField(name="count", type=count_type)]),
                ],
            )
        leaks = detect_internal_leaks(
            [Change(kind=ChangeKind.TYPE_FIELD_TYPE_CHANGED,
                    symbol="ns::detail::Serializer", description="count")],
            _snap("int"), _snap("std::atomic<int>"),
        )
        assert leaks == [], (
            "a decomposed-unique_ptr (nested pointer template arg) path is behind "
            f"a pointer — the layout change must be demoted (got: {leaks})"
        )

    def test_vtable_change_behind_pointer_still_fires(self) -> None:
        old = self._snap(serializer_field_type="int")
        new = self._snap(serializer_field_type="int", serializer_vtable=["f1", "f2"])
        changes = [Change(
            kind=ChangeKind.TYPE_VTABLE_CHANGED,
            symbol="ns::detail::Serializer",
            description="vtable changed",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1, (
            "a vtable change propagates through a pointer (virtual dispatch) and "
            "must still be flagged"
        )
        assert "pointer / template" in leaks[0].description

    def _pimpl_snap(self, *, impl_field_type: str, impl_size: int) -> AbiSnapshot:
        return AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[Function(
                name="make", mangled="make", return_type="Public*",
                params=[], visibility=Visibility.PUBLIC,
            )],
            types=[
                RecordType(name="Public", kind="class", fields=[
                    TypeField(name="impl_", type=impl_field_type),
                ]),
                RecordType(name="ns::detail::Impl", kind="struct", size_bits=impl_size),
            ],
        )

    def test_pointer_to_value_embed_still_fires(self) -> None:
        # Codex review: old reaches Impl through a pointer, new embeds it BY VALUE
        # (pimpl -> by-value) and Impl's layout changes. _merge_leak_paths dedups
        # the identical `field:impl_` chain, so the suppression must evaluate each
        # path against its OWN snapshot — the new by-value layout now propagates
        # to Public, so the leak must NOT be suppressed.
        old = self._pimpl_snap(impl_field_type="ns::detail::Impl*", impl_size=64)
        new = self._pimpl_snap(impl_field_type="ns::detail::Impl", impl_size=128)
        changes = [Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Impl",
            description="size 64 -> 128",
        )]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1, (
            "an internal type newly embedded by value carries its layout into the "
            f"public holder — the leak must still fire (got: {leaks})"
        )
        assert "embedded-by-value" in leaks[0].description

    def test_pimpl_named_public_type_by_value_still_fires(self) -> None:
        # Codex review: a public type/function name containing "pimpl" must NOT be
        # treated as indirection. `PimplHandle` embeds the internal type BY VALUE,
        # so a layout change propagates and the leak must fire.
        def _snap(impl_size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="lib.so", version="1.0",
                functions=[Function(
                    name="make_pimpl", mangled="make_pimpl", return_type="PimplHandle*",
                    params=[], visibility=Visibility.PUBLIC,
                )],
                types=[
                    RecordType(name="PimplHandle", kind="class", fields=[
                        TypeField(name="state", type="ns::detail::Impl"),  # by value
                    ]),
                    RecordType(name="ns::detail::Impl", kind="struct", size_bits=impl_size),
                ],
            )
        leaks = detect_internal_leaks(
            [Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Impl", description="size")],
            _snap(32), _snap(64),
        )
        assert len(leaks) == 1, (
            "a 'Pimpl'-named public type embedding an internal type by value is a "
            f"real layout leak — name must not be read as indirection (got: {leaks})"
        )
        assert "embedded-by-value" in leaks[0].description

    def test_value_embedded_pimpl_named_internal_type_still_fires(self) -> None:
        # Codex review: a by-value FIELD whose type is a record literally named
        # `Pimpl` (no `<...>`) embeds its layout — must fire, not be read as a
        # smart-pointer alias.
        def _snap(size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="lib.so", version="1.0",
                functions=[Function(
                    name="make", mangled="make", return_type="Public*",
                    params=[], visibility=Visibility.PUBLIC,
                )],
                types=[
                    RecordType(name="Public", kind="class", fields=[
                        TypeField(name="state", type="ns::detail::Pimpl"),
                    ]),
                    RecordType(name="ns::detail::Pimpl", kind="struct", size_bits=size),
                ],
            )
        leaks = detect_internal_leaks(
            [Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Pimpl", description="size")],
            _snap(32), _snap(64),
        )
        assert len(leaks) == 1, (
            f"a by-value field of a record named 'Pimpl' must still leak (got: {leaks})"
        )
        assert "embedded-by-value" in leaks[0].description

    def test_pointer_typedef_field_is_suppressed(self) -> None:
        # Codex review: a field declared through a pointer alias
        # (`using Handle = ns::detail::Impl*;`) is indirection — the typedef step
        # must be resolved, so a layout-only change is demoted (suppressed).
        def _snap(size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="lib.so", version="1.0",
                functions=[Function(
                    name="make", mangled="make", return_type="Public*",
                    params=[], visibility=Visibility.PUBLIC,
                )],
                types=[
                    RecordType(name="Public", kind="class", fields=[
                        TypeField(name="impl", type="Handle"),
                    ]),
                    RecordType(name="ns::detail::Impl", kind="struct", size_bits=size),
                ],
                typedefs={"Handle": "ns::detail::Impl*"},
            )
        leaks = detect_internal_leaks(
            [Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Impl", description="size")],
            _snap(32), _snap(64),
        )
        assert leaks == [], (
            "a layout change behind a pointer typedef must be demoted, not leaked "
            f"(got: {leaks})"
        )

    def test_by_value_template_arg_with_unrelated_pointer_still_fires(self) -> None:
        # Codex review: a field type that mixes a by-value internal template arg
        # with an unrelated pointer (std::pair<ns::detail::Impl, int*>) embeds
        # Impl BY VALUE — the nested `int*` must not mark the whole field as
        # indirection, so a layout change to Impl still leaks.
        def _snap(size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="lib.so", version="1.0",
                functions=[Function(
                    name="make", mangled="make", return_type="Public*",
                    params=[], visibility=Visibility.PUBLIC,
                )],
                types=[
                    RecordType(name="Public", kind="class", fields=[
                        TypeField(name="p", type="std::pair<ns::detail::Impl, int*>"),
                    ]),
                    RecordType(name="ns::detail::Impl", kind="struct", size_bits=size),
                ],
            )
        leaks = detect_internal_leaks(
            [Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Impl", description="size")],
            _snap(32), _snap(64),
        )
        assert len(leaks) == 1, (
            "a by-value template arg must leak; an unrelated nested pointer must "
            f"not be read as indirection (got: {leaks})"
        )
        assert "embedded-by-value" in leaks[0].description

    def test_top_level_pointer_to_template_suppresses_args(self) -> None:
        # Codex review: a top-level pointer on the enclosing template
        # (`std::pair<ns::detail::Impl, int>*`) puts the by-value `Impl` behind a
        # pointer too — a layout change to Impl must be demoted.
        def _snap(size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="lib.so", version="1.0",
                functions=[Function(
                    name="make", mangled="make", return_type="Public*",
                    params=[], visibility=Visibility.PUBLIC,
                )],
                types=[
                    RecordType(name="Public", kind="class", fields=[
                        TypeField(name="p", type="std::pair<ns::detail::Impl, int>*"),
                    ]),
                    RecordType(name="ns::detail::Impl", kind="struct", size_bits=size),
                ],
            )
        leaks = detect_internal_leaks(
            [Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Impl", description="size")],
            _snap(32), _snap(64),
        )
        assert leaks == [], (
            "a by-value template arg behind a top-level pointer must be demoted "
            f"(got: {leaks})"
        )

    def test_opaque_handle_pointer_param_is_suppressed(self) -> None:
        # Codex review: an internal type reached only through a pointer PARAM in a
        # public signature (`void use(ns::detail::Impl*)`) does not embed its
        # layout — a layout-only change must be demoted, not leaked.
        def _snap(size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="lib.so", version="1.0",
                functions=[Function(
                    name="use", mangled="use", return_type="void",
                    params=[Param(name="h", type="ns::detail::Impl*", pointer_depth=1)],
                    visibility=Visibility.PUBLIC,
                )],
                types=[RecordType(name="ns::detail::Impl", kind="struct", size_bits=size)],
            )
        leaks = detect_internal_leaks(
            [Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Impl", description="size")],
            _snap(32), _snap(64),
        )
        assert leaks == [], (
            f"opaque-handle pointer param must not leak a layout change (got: {leaks})"
        )

    def test_pimpl_alias_template_field_is_suppressed(self) -> None:
        # oneDAL pimpl<T> alias = a smart-pointer; a layout change to the pointee
        # must be demoted even though `pimpl` is not std::*_ptr.
        def _snap(size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="lib.so", version="1.0",
                functions=[Function(
                    name="make", mangled="make", return_type="Public*",
                    params=[], visibility=Visibility.PUBLIC,
                )],
                types=[
                    RecordType(name="Public", kind="class", fields=[
                        TypeField(name="impl_", type="oneapi::dal::detail::pimpl<ns::detail::Impl>"),
                    ]),
                    RecordType(name="ns::detail::Impl", kind="struct", size_bits=size),
                ],
            )
        leaks = detect_internal_leaks(
            [Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Impl", description="size")],
            _snap(32), _snap(64),
        )
        assert leaks == [], f"pimpl<T> alias is a pointer wrapper (got: {leaks})"

    def test_signature_pointer_in_template_arg_is_suppressed(self) -> None:
        # `std::pair<int, ns::detail::Impl*> get()` reaches Impl only through the
        # pointer stored in the pair — the seed must mark that edge indirect.
        def _snap(size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="lib.so", version="1.0",
                functions=[Function(
                    name="get", mangled="get",
                    return_type="std::pair<int, ns::detail::Impl*>",
                    return_pointer_depth=0, params=[], visibility=Visibility.PUBLIC,
                )],
                types=[RecordType(name="ns::detail::Impl", kind="struct", size_bits=size)],
            )
        leaks = detect_internal_leaks(
            [Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Impl", description="size")],
            _snap(32), _snap(64),
        )
        assert leaks == [], (
            f"a pointer template arg in a signature must be demoted (got: {leaks})"
        )

    def test_by_value_return_signature_still_fires(self) -> None:
        # A by-value return embeds the type in the calling convention — must leak.
        def _snap(size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="lib.so", version="1.0",
                functions=[Function(
                    name="get", mangled="get", return_type="ns::detail::Impl",
                    return_pointer_depth=0, params=[], visibility=Visibility.PUBLIC,
                )],
                types=[RecordType(name="ns::detail::Impl", kind="struct", size_bits=size)],
            )
        leaks = detect_internal_leaks(
            [Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                    symbol="ns::detail::Impl", description="size")],
            _snap(32), _snap(64),
        )
        assert len(leaks) == 1, f"a by-value return must leak (got: {leaks})"
