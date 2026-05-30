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
from abicheck.model import AbiSnapshot, Function, RecordType, Visibility

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
