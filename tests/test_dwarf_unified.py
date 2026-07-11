"""tests/test_dwarf_unified.py — Unit tests for the unified DWARF pass.

Verifies that parse_dwarf() produces identical results to calling
parse_dwarf_metadata() + parse_advanced_dwarf() separately, and that
backward-compatible shims work correctly.

Note: Tests that compile real ELF binaries are Linux-only — macOS/Windows
compilers produce Mach-O/PE, and DWARF parsing requires ELF.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from abicheck.dwarf_advanced import AdvancedDwarfMetadata  # noqa: E402
from abicheck.dwarf_metadata import DwarfMetadata  # noqa: E402
from abicheck.dwarf_unified import (  # noqa: E402
    DwarfSession,
    open_dwarf_session,
    parse_advanced_dwarf,
    parse_dwarf,
    parse_dwarf_from_session,
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


# ---------------------------------------------------------------------------
# Shared DWARF session — one open reused across the metadata + snapshot passes
# ---------------------------------------------------------------------------

_SESSION_SRC = """
    #include <string>
    #include <vector>
    namespace demo {
    enum class Color { Red, Green, Blue };
    struct Point { int x; int y; double z; };
    template <typename T> struct Box { T value; std::vector<T> hist; };
    struct Registry { std::vector<Box<int>> counters; Color tint; };
    }
    extern "C" int demo_area(int n) { int s = 0; for (int i = 0; i < n; ++i) s += i; return s; }
    extern "C" demo::Point demo_origin_pt(void) { return demo::Point{1, 2, 3.0}; }
    extern "C" demo::Color demo_pick_color(void) { return demo::Color::Green; }
    extern int demo_global; int demo_global = 7;
"""


@pytest.mark.skipif(sys.platform != "linux", reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)")
class TestDwarfSession:
    """open_dwarf_session + parse_dwarf_from_session must match the one-shot API,
    and reusing a session for the snapshot build must be byte-for-byte identical."""

    def test_session_parse_matches_parse_dwarf(self, tmp_path: Path) -> None:
        _require_tool("g++")
        so = _compile_so(tmp_path, "libsess", _SESSION_SRC, lang="cpp")

        meta_a, adv_a = parse_dwarf(so)

        sess = open_dwarf_session(so)
        assert isinstance(sess, DwarfSession)
        try:
            meta_b, adv_b = parse_dwarf_from_session(sess)
        finally:
            sess.close()

        # Identical metadata regardless of whether the file was opened once
        # (session) or once per call (parse_dwarf).
        assert meta_a.structs == meta_b.structs
        assert meta_a.enums == meta_b.enums
        assert adv_a.target_arch == adv_b.target_arch
        assert adv_a.calling_conventions == adv_b.calling_conventions
        assert adv_a.packed_structs == adv_b.packed_structs

    def test_snapshot_via_session_is_byte_identical(self, tmp_path: Path) -> None:
        """build_snapshot_from_dwarf(session=…) must serialize identically to the
        legacy re-open path — the core correctness bar for the single-pass merge."""
        _require_tool("g++")
        from abicheck.dwarf_snapshot import build_snapshot_from_dwarf
        from abicheck.elf_metadata import parse_elf_metadata
        from abicheck.serialization import snapshot_to_json

        so = _compile_so(tmp_path, "libsesssnap", _SESSION_SRC, lang="cpp")
        elf_meta = parse_elf_metadata(so)

        # Path A: independent opens (legacy).
        meta_a, adv_a = parse_dwarf(so)
        snap_a = build_snapshot_from_dwarf(so, elf_meta, meta_a, adv_a, version="t")

        # Path B: one shared session reused by the snapshot walk.
        sess = open_dwarf_session(so)
        assert sess is not None
        try:
            meta_b, adv_b = parse_dwarf_from_session(sess)
            snap_b = build_snapshot_from_dwarf(
                so, elf_meta, meta_b, adv_b, version="t", session=sess
            )
        finally:
            sess.close()

        assert snapshot_to_json(snap_a) == snapshot_to_json(snap_b)
        # And the snapshot genuinely exercised the type/function/enum paths.
        assert snap_b.types
        assert snap_b.functions
        assert snap_b.enums

    def test_snapshot_usable_after_session_closed(self, tmp_path: Path) -> None:
        """The built snapshot holds extracted model objects, not live DIEs, so it
        stays fully serializable after the session file handle is closed."""
        _require_tool("g++")
        from abicheck.dwarf_snapshot import build_snapshot_from_dwarf
        from abicheck.elf_metadata import parse_elf_metadata
        from abicheck.serialization import snapshot_to_json

        so = _compile_so(tmp_path, "libsessclose", _SESSION_SRC, lang="cpp")
        elf_meta = parse_elf_metadata(so)
        sess = open_dwarf_session(so)
        assert sess is not None
        meta, adv = parse_dwarf_from_session(sess)
        snap = build_snapshot_from_dwarf(so, elf_meta, meta, adv, session=sess)
        sess.close()  # close BEFORE serializing
        assert snapshot_to_json(snap)  # must not raise / must be non-empty

    def test_open_dwarf_session_none_cases(self, tmp_path: Path) -> None:
        """Non-regular / non-ELF / missing inputs return None (no leaked handle)."""
        assert open_dwarf_session(tmp_path) is None  # directory
        assert open_dwarf_session(tmp_path / "missing.so") is None  # nonexistent
        bad = tmp_path / "not_elf.so"
        bad.write_bytes(b"not an ELF file")
        assert open_dwarf_session(bad) is None

    def test_open_session_never_raises_and_no_leak_on_unexpected_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pyelftools can raise beyond (ELFError, OSError, ValueError) on corrupt
        DWARF; open_dwarf_session must still return None and release the handle
        (the "never raises" / no-descriptor-leak contract, F-D-leak review)."""
        from abicheck import dwarf_unified as du

        so = tmp_path / "corrupt.so"
        so.write_bytes(b"\x7fELF" + b"\x00" * 128)

        def boom(*_a: object, **_k: object) -> object:
            raise struct.error("truncated header")  # not in the narrow tuple

        monkeypatch.setattr(du, "ELFFile", boom)

        def nfds() -> int:
            try:
                return len(os.listdir("/proc/self/fd"))
            except OSError:
                return -1

        base = nfds()
        for _ in range(30):
            assert du.open_dwarf_session(so) is None  # must not raise
        assert nfds() - base <= 1, "open_dwarf_session leaked a file descriptor"

    def test_parse_dwarf_survives_cu_iteration_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """iter_CUs() can raise on malformed CU headers before the per-CU guard
        runs. parse_dwarf must swallow it, close the session (not hand it back),
        and return empty metadata so the dumper degrades to symbol-only."""
        _require_tool("g++")
        from abicheck import dwarf_unified as du

        so = _compile_so(tmp_path, "libcuerr", _SESSION_SRC, lang="cpp")

        def boom(_session: object) -> object:
            raise ValueError("iter_CUs blew up")

        monkeypatch.setattr(du, "parse_dwarf_from_session", boom)

        out: list = []
        meta, adv = du.parse_dwarf(so, _session_out=out)  # must not raise
        assert not meta.has_dwarf
        assert not adv.has_dwarf
        assert out == [], "failed parse must close the session, not append it"

    def test_close_is_safe_to_call(self, tmp_path: Path) -> None:
        _require_tool("g++")
        so = _compile_so(tmp_path, "libsessdbl", _SESSION_SRC, lang="cpp")
        sess = open_dwarf_session(so)
        assert sess is not None
        sess.close()
        # Double close must not raise.
        sess.close()

    def test_close_swallows_file_error(self) -> None:
        """close() must not propagate an OSError from the underlying handle."""

        class _BadFile:
            def close(self) -> None:
                raise OSError("handle already gone")

        sess = DwarfSession(
            path=Path("x"), _file=_BadFile(), elf=None, dwarf=None, arch="x86_64"  # type: ignore[arg-type]
        )
        sess.close()  # must not raise

    def test_snapshot_reuses_session_without_reopening(self, tmp_path: Path) -> None:
        """When a session is supplied, the snapshot build must NOT open the ELF
        again — the whole point of the single-pass merge."""
        _require_tool("g++")
        from abicheck.dwarf_snapshot import build_snapshot_from_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        so = _compile_so(tmp_path, "libsessreopen", _SESSION_SRC, lang="cpp")
        elf_meta = parse_elf_metadata(so)
        sess = open_dwarf_session(so)
        assert sess is not None
        meta, adv = parse_dwarf_from_session(sess)

        reopens: list[str] = []
        original_open = open

        def counting_open(path, mode="r", **kwargs):  # type: ignore[override]
            if "rb" in str(mode) and str(so) in str(path):
                reopens.append(str(path))
            return original_open(path, mode, **kwargs)

        try:
            with patch("builtins.open", side_effect=counting_open):
                build_snapshot_from_dwarf(so, elf_meta, meta, adv, session=sess)
        finally:
            sess.close()

        assert reopens == [], f"snapshot re-opened the ELF despite a session: {reopens}"
