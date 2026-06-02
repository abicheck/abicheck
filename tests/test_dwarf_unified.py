"""tests/test_dwarf_unified.py — Unit tests for the unified DWARF pass.

Verifies that parse_dwarf() produces identical results to calling
parse_dwarf_metadata() + parse_advanced_dwarf() separately, and that
backward-compatible shims work correctly.

Note: Tests that compile real ELF binaries are Linux-only — macOS/Windows
compilers produce Mach-O/PE, and DWARF parsing requires ELF.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from abicheck.dwarf_advanced import AdvancedDwarfMetadata  # noqa: E402
from abicheck.dwarf_metadata import DwarfMetadata  # noqa: E402
from abicheck.dwarf_unified import (  # noqa: E402
    parse_advanced_dwarf,
    parse_dwarf,
    parse_dwarf_metadata,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_tool(name: str) -> None:
    import shutil
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


def _compile_so(tmp_path: Path, name: str, src: str, lang: str = "c") -> Path:
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
    # On macOS, gcc/clang produces Mach-O, not ELF — skip if not ELF
    with open(so_file, "rb") as f:
        if f.read(4) != b"\x7fELF":
            pytest.skip("Compiled binary is not ELF (non-Linux platform)")
    return so_file


# ---------------------------------------------------------------------------
# Core correctness: unified output == separate output
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "linux", reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)")
class TestUnifiedEqualsSepaRate:
    """parse_dwarf() must produce identical data to calling both parsers separately."""

    def test_has_dwarf_matches(self, tmp_path: Path) -> None:
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libtest", "int add(int a, int b) { return a+b; }")
        meta, adv = parse_dwarf(so)
        meta2 = parse_dwarf_metadata(so)
        adv2 = parse_advanced_dwarf(so)
        assert meta.has_dwarf == meta2.has_dwarf
        assert adv.has_dwarf == adv2.has_dwarf

    def test_structs_identical(self, tmp_path: Path) -> None:
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libstruct",
            "typedef struct { int x; int y; } Point;\n"
            "Point make(int x, int y) { Point p = {x,y}; return p; }")
        meta, _ = parse_dwarf(so)
        meta2 = parse_dwarf_metadata(so)
        assert meta.structs == meta2.structs

    def test_enums_identical(self, tmp_path: Path) -> None:
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libenum",
            "typedef enum { RED=0, GREEN=1, BLUE=2 } Color;\n"
            "Color get(void) { return RED; }")
        meta, _ = parse_dwarf(so)
        meta2 = parse_dwarf_metadata(so)
        assert meta.enums == meta2.enums

    def test_toolchain_identical(self, tmp_path: Path) -> None:
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libtc",
            "int fn(void) { return 1; }")
        _, adv = parse_dwarf(so)
        adv2 = parse_advanced_dwarf(so)
        assert adv.toolchain.compiler == adv2.toolchain.compiler
        assert adv.toolchain.version == adv2.toolchain.version

    def test_calling_conventions_identical(self, tmp_path: Path) -> None:
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libcc",
            "int __attribute__((cdecl)) fn(int x) { return x; }")
        _, adv = parse_dwarf(so)
        adv2 = parse_advanced_dwarf(so)
        assert adv.calling_conventions == adv2.calling_conventions

    def test_packed_structs_identical(self, tmp_path: Path) -> None:
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libpacked",
            "struct __attribute__((packed)) Hdr { char a; int b; };\n"
            "struct Hdr make(void) { struct Hdr h = {'x', 1}; return h; }")
        _, adv = parse_dwarf(so)
        adv2 = parse_advanced_dwarf(so)
        assert adv.packed_structs == adv2.packed_structs


# ---------------------------------------------------------------------------
# Error / edge cases
# ---------------------------------------------------------------------------

class TestUnifiedEdgeCases:
    def test_non_elf_file_returns_empty(self, tmp_path: Path) -> None:
        bad = tmp_path / "not_elf.so"
        bad.write_bytes(b"not an ELF file")
        meta, adv = parse_dwarf(bad)
        assert not meta.has_dwarf
        assert not adv.has_dwarf

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        meta, adv = parse_dwarf(tmp_path / "missing.so")
        assert not meta.has_dwarf
        assert not adv.has_dwarf

    def test_non_regular_file_returns_empty(self, tmp_path: Path) -> None:
        """Directories and other non-regular files should not crash."""
        meta, adv = parse_dwarf(tmp_path)  # directory
        assert not meta.has_dwarf
        assert not adv.has_dwarf

    def test_so_without_debug_info_returns_empty(self, tmp_path: Path) -> None:
        """Binary with no DWARF sections → has_dwarf=False.

        Note: GCC on Linux always emits at least .debug_frame for stack
        unwinding, so stripping is not reliable cross-platform. We simulate
        a DWARF-less binary by mocking get_section_by_name to return None for
        the .debug_info / .zdebug_info sections (the strict DWARF check).
        """
        from unittest.mock import MagicMock, patch

        mock_elf = MagicMock()
        mock_elf.get_section_by_name.return_value = None

        with patch("abicheck.dwarf_unified.ELFFile", return_value=mock_elf), \
             patch("abicheck.dwarf_unified.os.fstat") as mock_fstat:
            import stat as stat_mod
            mock_fstat.return_value = MagicMock(st_mode=stat_mod.S_IFREG | 0o644)
            so = tmp_path / "fake.so"
            so.write_bytes(b"\x7fELF" + b"\x00" * 60)
            meta, adv = parse_dwarf(so)

        assert not meta.has_dwarf
        assert not adv.has_dwarf

    def test_never_raises(self, tmp_path: Path) -> None:
        """parse_dwarf must never propagate exceptions."""
        bad = tmp_path / "truncated.so"
        bad.write_bytes(b"\x7fELF" + b"\x00" * 10)  # valid magic, truncated
        try:
            parse_dwarf(bad)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"parse_dwarf raised: {exc}")


# ---------------------------------------------------------------------------
# Backward-compatible shims
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "linux", reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)")
class TestShims:
    def test_parse_dwarf_metadata_shim_returns_dwarf_metadata(
        self, tmp_path: Path
    ) -> None:
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libshim1", "int f(void) { return 0; }")
        result = parse_dwarf_metadata(so)
        assert isinstance(result, DwarfMetadata)
        assert result.has_dwarf is True

    def test_parse_advanced_dwarf_shim_returns_advanced_metadata(
        self, tmp_path: Path
    ) -> None:
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libshim2", "int f(void) { return 0; }")
        result = parse_advanced_dwarf(so)
        assert isinstance(result, AdvancedDwarfMetadata)
        assert result.has_dwarf is True

    def test_shims_call_parse_dwarf_once_each(self, tmp_path: Path) -> None:
        """Each shim calls parse_dwarf exactly once (no double-open)."""
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libshimcount", "int f(void) { return 0; }")
        with patch("abicheck.dwarf_unified.parse_dwarf", wraps=parse_dwarf) as mock:
            parse_dwarf_metadata(so)
            assert mock.call_count == 1
        with patch("abicheck.dwarf_unified.parse_dwarf", wraps=parse_dwarf) as mock:
            parse_advanced_dwarf(so)
            assert mock.call_count == 1


# ---------------------------------------------------------------------------
# Performance sanity: single open vs two opens
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "linux", reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)")
class TestSingleOpen:
    def test_file_opened_once(self, tmp_path: Path) -> None:
        """parse_dwarf opens the file exactly once (not twice)."""
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libopen", "int f(void) { return 0; }")
        open_calls: list[str] = []
        original_open = open

        def counting_open(path, mode="r", **kwargs):  # type: ignore[override]
            if "rb" in str(mode) and str(so) in str(path):
                open_calls.append(str(path))
            return original_open(path, mode, **kwargs)

        with patch("builtins.open", side_effect=counting_open):
            parse_dwarf(so)

        assert len(open_calls) == 1, (
            f"Expected 1 file open, got {len(open_calls)}: {open_calls}"
        )
