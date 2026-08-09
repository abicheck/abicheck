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

"""tests/test_dwarf_low_memory.py — DWARF low-memory DIE-cache freeing.

Covers ``dwarf_utils.free_cu_die_cache``/``dwarf_low_memory_mode`` (the unit
mechanics) and, on Linux with a real compiler, that forcing low-memory mode
on for a real compiled binary produces byte-for-byte identical
DwarfMetadata/AdvancedDwarfMetadata/AbiSnapshot output to the default
(cache-reusing) path -- freeing a CU's DIE cache mid-walk must change only
peak memory / CPU cost, never a single field of the result.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from abicheck.dwarf_utils import (
    DEFAULT_DWARF_LOW_MEMORY_THRESHOLD_MB,
    dwarf_low_memory_mode,
    free_cu_die_cache,
)


def _require_tool(name: str) -> None:
    import shutil
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


def _compile_so(tmp_path: Path, name: str, src: str, lang: str = "cpp") -> Path:
    ext = ".c" if lang == "c" else ".cpp"
    compiler = "gcc" if lang == "c" else "g++"
    src_file = tmp_path / f"{name}{ext}"
    so_file = tmp_path / f"{name}.so"
    src_file.write_text(textwrap.dedent(src).strip(), encoding="utf-8")
    r = subprocess.run(
        [compiler, "-shared", "-fPIC", "-g", "-fvisibility=default",
         "-o", str(so_file), str(src_file)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        pytest.skip(f"Compilation failed: {r.stderr[:200]}")
    with open(so_file, "rb") as f:
        if f.read(4) != b"\x7fELF":
            pytest.skip("Compiled binary is not ELF (non-Linux platform)")
    return so_file


_SRC = """
    namespace demo {
    struct Point { int x; int y; double weight; };
    class Base {
    public:
        virtual ~Base() {}
        virtual int value() const { return 0; }
    };
    class Derived : public Base {
    public:
        int value() const override { return 42; }
        Point p;
    };
    enum class Color { Red, Green, Blue };
    }
    extern "C" int demo_add(int a, int b) { return a + b; }
    demo::Derived make_derived() { return demo::Derived(); }
    extern demo::Point demo_origin;
    demo::Point demo_origin = {0, 0, 0.0};
"""


# ---------------------------------------------------------------------------
# free_cu_die_cache — unit mechanics
# ---------------------------------------------------------------------------

class _FakeCU:
    """Minimal stand-in for pyelftools' CompileUnit cache attributes."""

    def __init__(self, with_cache: bool = True):
        if with_cache:
            self._dielist = ["die0", "die1", "die2"]
            self._diemap = [0, 10, 20]


class TestFreeCuDieCache:
    def test_clears_populated_cache(self) -> None:
        cu = _FakeCU(with_cache=True)
        assert cu._dielist and cu._diemap
        free_cu_die_cache(cu)
        assert cu._dielist == []
        assert cu._diemap == []

    def test_noop_on_cu_without_cache_attrs(self) -> None:
        cu = object()  # no _dielist/_diemap at all
        assert free_cu_die_cache(cu) is None  # must not raise

    def test_idempotent(self) -> None:
        cu = _FakeCU(with_cache=True)
        free_cu_die_cache(cu)
        free_cu_die_cache(cu)  # second call on an already-empty cache
        assert cu._dielist == []
        assert cu._diemap == []


# ---------------------------------------------------------------------------
# dwarf_low_memory_mode — threshold decision
# ---------------------------------------------------------------------------

class _FakeSection:
    def __init__(self, size: int):
        self.size = size


class _FakeDwarfInfo:
    def __init__(self, debug_info_size: int | None):
        self.debug_info_sec = None if debug_info_size is None else _FakeSection(debug_info_size)


class TestDwarfLowMemoryMode:
    def test_small_binary_below_default_threshold(self, monkeypatch) -> None:
        monkeypatch.delenv("ABICHECK_DWARF_LOW_MEMORY_MB", raising=False)
        small = _FakeDwarfInfo(1024 * 1024)  # 1 MiB
        assert dwarf_low_memory_mode(small) is False

    def test_large_binary_above_default_threshold(self, monkeypatch) -> None:
        monkeypatch.delenv("ABICHECK_DWARF_LOW_MEMORY_MB", raising=False)
        big_mb = DEFAULT_DWARF_LOW_MEMORY_THRESHOLD_MB + 10
        big = _FakeDwarfInfo(int(big_mb * 1024 * 1024))
        assert dwarf_low_memory_mode(big) is True

    def test_missing_debug_info_sec_is_never_low_memory(self, monkeypatch) -> None:
        monkeypatch.delenv("ABICHECK_DWARF_LOW_MEMORY_MB", raising=False)
        assert dwarf_low_memory_mode(_FakeDwarfInfo(None)) is False

    def test_env_override_lowers_threshold(self, monkeypatch) -> None:
        monkeypatch.setenv("ABICHECK_DWARF_LOW_MEMORY_MB", "0")
        tiny = _FakeDwarfInfo(1024)  # 1 KiB — above a 0 MiB threshold
        assert dwarf_low_memory_mode(tiny) is True

    def test_env_override_raises_threshold_effectively_disabling(self, monkeypatch) -> None:
        monkeypatch.setenv("ABICHECK_DWARF_LOW_MEMORY_MB", "1000000")
        huge = _FakeDwarfInfo(500 * 1024 * 1024)  # 500 MiB
        assert dwarf_low_memory_mode(huge) is False

    def test_negative_env_override_disables_low_memory_mode(self, monkeypatch) -> None:
        # A negative threshold is the documented disable switch. A naive
        # ``size_mb > threshold_mb`` alone would do the *opposite* here,
        # since every real (non-negative) size compares greater than a
        # negative threshold -- this must return False regardless of size.
        monkeypatch.setenv("ABICHECK_DWARF_LOW_MEMORY_MB", "-1")
        huge = _FakeDwarfInfo(500 * 1024 * 1024)  # 500 MiB
        assert dwarf_low_memory_mode(huge) is False
        assert dwarf_low_memory_mode(_FakeDwarfInfo(0)) is False

    def test_invalid_env_override_falls_back_to_default(self, monkeypatch) -> None:
        monkeypatch.setenv("ABICHECK_DWARF_LOW_MEMORY_MB", "not-a-number")
        small = _FakeDwarfInfo(1024 * 1024)  # 1 MiB, below default threshold
        assert dwarf_low_memory_mode(small) is False
        big_mb = DEFAULT_DWARF_LOW_MEMORY_THRESHOLD_MB + 10
        big = _FakeDwarfInfo(int(big_mb * 1024 * 1024))
        assert dwarf_low_memory_mode(big) is True


# ---------------------------------------------------------------------------
# End-to-end: low-memory mode must not change any parsed output
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform != "linux",
    reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)",
)
class TestLowMemoryModeIsOutputNeutral:
    def test_parse_dwarf_identical_with_low_memory_forced_on(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        _require_tool("g++")
        from abicheck.dwarf_unified import parse_dwarf

        so = _compile_so(tmp_path, "liblowmem1", _SRC)

        monkeypatch.delenv("ABICHECK_DWARF_LOW_MEMORY_MB", raising=False)
        meta_normal, adv_normal = parse_dwarf(so)

        monkeypatch.setenv("ABICHECK_DWARF_LOW_MEMORY_MB", "0")
        meta_low, adv_low = parse_dwarf(so)

        assert meta_normal.structs == meta_low.structs
        assert meta_normal.enums == meta_low.enums
        assert meta_normal.base_types == meta_low.base_types
        assert adv_normal.target_arch == adv_low.target_arch
        assert adv_normal.calling_conventions == adv_low.calling_conventions
        assert adv_normal.packed_structs == adv_low.packed_structs
        # And the walk genuinely exercised the struct/class paths.
        assert meta_normal.structs

    def test_snapshot_via_session_identical_with_low_memory_forced_on(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        _require_tool("g++")
        from abicheck.dwarf_snapshot import build_snapshot_from_dwarf
        from abicheck.dwarf_unified import open_dwarf_session, parse_dwarf_from_session
        from abicheck.elf_metadata import parse_elf_metadata
        from abicheck.serialization import snapshot_to_json

        so = _compile_so(tmp_path, "liblowmem2", _SRC)
        elf_meta = parse_elf_metadata(so)

        monkeypatch.delenv("ABICHECK_DWARF_LOW_MEMORY_MB", raising=False)
        sess_a = open_dwarf_session(so)
        assert sess_a is not None
        try:
            meta_a, adv_a = parse_dwarf_from_session(sess_a)
            snap_a = build_snapshot_from_dwarf(
                so, elf_meta, meta_a, adv_a, version="t", session=sess_a
            )
        finally:
            sess_a.close()

        monkeypatch.setenv("ABICHECK_DWARF_LOW_MEMORY_MB", "0")
        sess_b = open_dwarf_session(so)
        assert sess_b is not None
        try:
            meta_b, adv_b = parse_dwarf_from_session(sess_b)
            # Sanity: the CU cache was actually freed by the low-memory pass.
            for CU in sess_b.dwarf.iter_CUs():
                assert CU._dielist == []
                assert CU._diemap == []
            snap_b = build_snapshot_from_dwarf(
                so, elf_meta, meta_b, adv_b, version="t", session=sess_b
            )
        finally:
            sess_b.close()

        assert snapshot_to_json(snap_a) == snapshot_to_json(snap_b)
        assert snap_b.types
        assert snap_b.functions

    def test_low_memory_snapshot_matches_non_low_memory_direct_open(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """Same check without session reuse -- the DWARF-only ``dump``
        (``--depth binary``) code path opens fresh rather than sharing a
        session when none is supplied."""
        _require_tool("g++")
        from abicheck.dwarf_snapshot import build_snapshot_from_dwarf
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata
        from abicheck.serialization import snapshot_to_json

        so = _compile_so(tmp_path, "liblowmem3", _SRC)
        elf_meta = parse_elf_metadata(so)

        monkeypatch.delenv("ABICHECK_DWARF_LOW_MEMORY_MB", raising=False)
        meta_a, adv_a = parse_dwarf(so)
        snap_a = build_snapshot_from_dwarf(so, elf_meta, meta_a, adv_a, version="t")

        monkeypatch.setenv("ABICHECK_DWARF_LOW_MEMORY_MB", "0")
        meta_b, adv_b = parse_dwarf(so)
        snap_b = build_snapshot_from_dwarf(so, elf_meta, meta_b, adv_b, version="t")

        assert snapshot_to_json(snap_a) == snapshot_to_json(snap_b)
