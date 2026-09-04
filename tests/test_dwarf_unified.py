"""tests/test_dwarf_unified.py — Unit tests for the unified DWARF pass.

Verifies that parse_dwarf() produces identical results to calling
parse_dwarf_metadata() + parse_advanced_dwarf() separately, and that
backward-compatible shims work correctly.

Note: Tests that compile real ELF binaries are Linux-only — macOS/Windows
compilers produce Mach-O/PE, and DWARF parsing requires ELF.
"""

from __future__ import annotations

import os
import shutil
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
from tests.test_dwarf_metadata_coverage import _CU, _Attr, _Die  # noqa: E402

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
        [
            compiler,
            "-shared",
            "-fPIC",
            "-g",
            "-fvisibility=default",
            "-o",
            str(so_file),
            str(src_file),
        ],
        capture_output=True,
        text=True,
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


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)",
)
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
        so = _compile_so(
            tmp_path,
            "libstruct",
            "typedef struct { int x; int y; } Point;\n"
            "Point make(int x, int y) { Point p = {x,y}; return p; }",
        )
        meta, _ = parse_dwarf(so)
        meta2 = parse_dwarf_metadata(so)
        assert meta.structs == meta2.structs

    def test_enums_identical(self, tmp_path: Path) -> None:
        _require_tool("gcc")
        so = _compile_so(
            tmp_path,
            "libenum",
            "typedef enum { RED=0, GREEN=1, BLUE=2 } Color;\n"
            "Color get(void) { return RED; }",
        )
        meta, _ = parse_dwarf(so)
        meta2 = parse_dwarf_metadata(so)
        assert meta.enums == meta2.enums

    def test_toolchain_identical(self, tmp_path: Path) -> None:
        _require_tool("gcc")
        so = _compile_so(tmp_path, "libtc", "int fn(void) { return 1; }")
        _, adv = parse_dwarf(so)
        adv2 = parse_advanced_dwarf(so)
        assert adv.toolchain.compiler == adv2.toolchain.compiler
        assert adv.toolchain.version == adv2.toolchain.version

    def test_calling_conventions_identical(self, tmp_path: Path) -> None:
        _require_tool("gcc")
        so = _compile_so(
            tmp_path, "libcc", "int __attribute__((cdecl)) fn(int x) { return x; }"
        )
        _, adv = parse_dwarf(so)
        adv2 = parse_advanced_dwarf(so)
        assert adv.calling_conventions == adv2.calling_conventions

    def test_packed_structs_identical(self, tmp_path: Path) -> None:
        _require_tool("gcc")
        so = _compile_so(
            tmp_path,
            "libpacked",
            "struct __attribute__((packed)) Hdr { char a; int b; };\n"
            "struct Hdr make(void) { struct Hdr h = {'x', 1}; return h; }",
        )
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

        with (
            patch("abicheck.dwarf_unified.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_unified.os.fstat") as mock_fstat,
        ):
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


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)",
)
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


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)",
)
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


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)",
)
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

        calls: list[bool] = []

        class _BadFile:
            def close(self) -> None:
                calls.append(True)
                raise OSError("handle already gone")

        sess = DwarfSession(
            path=Path("x"),
            _file=_BadFile(),
            elf=None,
            dwarf=None,
            arch="x86_64",  # type: ignore[arg-type]
        )
        sess.close()  # must not raise
        assert calls == [True], "close() should still attempt to close the handle"

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


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)",
)
class TestSplitDwarfSkeletonDetection:
    """A skeleton CU (``-gsplit-dwarf``) must never be reported as fully
    ``parsed`` -- its real type/calling-convention DIEs live in an
    unconsumed ``.dwo``/``.dwp`` file, so a skeleton "succeeds" at CU
    iteration while extracting almost nothing (P1 review: reproduced
    int->long struct-layout regression missed at NO_CHANGE/exit 0)."""

    def test_is_skeleton_cu_detects_gnu_dwo_name_attribute(self) -> None:
        from abicheck.dwarf_unified import _is_skeleton_cu

        class _FakeTopDIE:
            attributes = {"DW_AT_GNU_dwo_name": object()}

        class _FakeCU:
            header: dict = {}

            def get_top_DIE(self) -> _FakeTopDIE:
                return _FakeTopDIE()

        assert _is_skeleton_cu(_FakeCU()) is True

    def test_is_skeleton_cu_detects_dwarf5_unit_type(self) -> None:
        from abicheck.dwarf_unified import _is_skeleton_cu
        from abicheck.dwarf_utils import _DW_UT_SKELETON, _DW_UT_SPLIT_COMPILE

        class _FakeCU:
            def __init__(self, unit_type: int) -> None:
                self.header = {"unit_type": unit_type}

            def get_top_DIE(self) -> None:  # pragma: no cover - not reached
                raise AssertionError("should short-circuit on unit_type")

        assert _is_skeleton_cu(_FakeCU(_DW_UT_SKELETON)) is True
        assert _is_skeleton_cu(_FakeCU(_DW_UT_SPLIT_COMPILE)) is True

    def test_is_skeleton_cu_false_for_ordinary_cu(self) -> None:
        from abicheck.dwarf_unified import _is_skeleton_cu

        class _FakeTopDIE:
            attributes: dict = {"DW_AT_name": object()}

        class _FakeCU:
            header = {"unit_type": 0x01}  # DW_UT_compile — ordinary CU

            def get_top_DIE(self) -> _FakeTopDIE:
                return _FakeTopDIE()

        assert _is_skeleton_cu(_FakeCU()) is False

    def test_is_skeleton_cu_never_raises_on_broken_top_die(self) -> None:
        from abicheck.dwarf_unified import _is_skeleton_cu

        class _FakeCU:
            header: dict = {}

            def get_top_DIE(self) -> None:
                raise ValueError("corrupt DIE")

        assert _is_skeleton_cu(_FakeCU()) is False

    def test_split_dwarf_binary_marks_both_channels_partial(
        self, tmp_path: Path
    ) -> None:
        """End-to-end against a REAL ``-gsplit-dwarf`` binary: both DWARF
        channels must come back ``partial`` (never ``parsed``), even though
        every CU "successfully" iterates."""
        _require_tool("gcc")
        src = tmp_path / "split.c"
        src.write_text("int square(int x) { return x * x; }\n", encoding="utf-8")
        so = tmp_path / "libsplit.so"
        r = subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-g",
                "-gsplit-dwarf",
                "-fvisibility=default",
                "-o",
                str(so),
                str(src),
            ],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        if r.returncode != 0:
            pytest.skip(f"-gsplit-dwarf compilation failed: {r.stderr[:200]}")
        with open(so, "rb") as f:
            if f.read(4) != b"\x7fELF":
                pytest.skip("compiled binary is not ELF")

        sess = open_dwarf_session(so)
        assert sess is not None
        try:
            meta, adv = parse_dwarf_from_session(sess)
        finally:
            sess.close()

        assert meta.cu_total >= 1
        assert meta.evidence_state in ("partial", "failed"), meta.evidence_state
        assert adv.evidence_state in ("partial", "failed"), adv.evidence_state
        assert meta.evidence_state != "parsed"
        assert adv.evidence_state != "parsed"

    def test_ordinary_dwarf_binary_still_reports_parsed(self, tmp_path: Path) -> None:
        """Sanity check: an ordinary (non-split) binary is unaffected by the
        skeleton check and still reports ``parsed``."""
        _require_tool("g++")
        so = _compile_so(tmp_path, "libnotsplit", _SESSION_SRC, lang="cpp")
        sess = open_dwarf_session(so)
        assert sess is not None
        try:
            meta, adv = parse_dwarf_from_session(sess)
        finally:
            sess.close()
        assert meta.evidence_state == "parsed"

    def test_standalone_parsers_also_detect_split_dwarf(self, tmp_path: Path) -> None:
        """P2 review, fresh evidence: the standalone entry points
        (``dwarf_metadata.parse_dwarf_metadata``/``dwarf_advanced.
        parse_advanced_dwarf``) had no split-DWARF detection at all, so a
        ``-gsplit-dwarf`` library attached via either still-public function
        read back ``evidence_state="parsed"`` despite the real DIEs living in
        an unconsumed ``.dwo``. Both must now downgrade the same way the
        unified pass already does."""
        _require_tool("gcc")
        src = tmp_path / "split2.c"
        src.write_text("int cube(int x) { return x * x * x; }\n", encoding="utf-8")
        so = tmp_path / "libsplit2.so"
        r = subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-g",
                "-gsplit-dwarf",
                "-fvisibility=default",
                "-o",
                str(so),
                str(src),
            ],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        if r.returncode != 0:
            pytest.skip(f"-gsplit-dwarf compilation failed: {r.stderr[:200]}")
        with open(so, "rb") as f:
            if f.read(4) != b"\x7fELF":
                pytest.skip("compiled binary is not ELF")

        meta = parse_dwarf_metadata(so)
        assert meta.cu_total >= 1
        assert meta.evidence_state != "parsed"
        assert meta.evidence_state in ("partial", "failed")

        adv = parse_advanced_dwarf(so)
        assert adv.cu_total >= 1
        assert adv.evidence_state != "parsed"
        assert adv.evidence_state in ("partial", "failed")


class TestZeroCuDwarfIsNeverParsed:
    """An empty or truncated ``.debug_info`` section makes ``iter_CUs()``
    yield zero compilation units without raising -- the pyelftools-level
    presence check (``has_real_dwarf_info``) only confirmed the section
    exists, not that it holds anything. Before this fix ``cu_total`` and
    ``cu_failed`` both stayed 0 in that case, so none of the post-loop
    ``cu_failed``/skeleton downgrades fired and the constructor's
    ``evidence_state="parsed"`` default leaked through unchanged -- a false
    claim of complete DWARF evidence (P1 review: reproduced with an ELF
    carrying an empty ``.debug_info`` section --
    ``--require-complete-analysis`` exited 0). Covers all three DWARF entry
    points that independently do this cu_total/cu_failed accounting."""

    def test_unified_session_pass_marks_zero_cu_failed(self) -> None:
        class _EmptyDwarfInfo:
            def iter_CUs(self) -> list:
                return []

        sess = DwarfSession(
            path=Path("libzerocu.so"),
            _file=object(),  # type: ignore[arg-type]
            elf=object(),
            dwarf=_EmptyDwarfInfo(),
            arch="x86_64",  # type: ignore[arg-type]
        )
        meta, adv = parse_dwarf_from_session(sess)
        assert meta.cu_total == 0
        assert meta.cu_failed == 0
        assert meta.evidence_state == "failed"
        assert adv.cu_total == 0
        assert adv.cu_failed == 0
        assert adv.evidence_state == "failed"

    def test_standalone_dwarf_metadata_marks_zero_cu_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck import dwarf_metadata as dm

        class _EmptyDwarfInfo:
            def iter_CUs(self) -> list:
                return []

        class _FakeElf:
            def get_dwarf_info(self) -> _EmptyDwarfInfo:
                return _EmptyDwarfInfo()

        so = tmp_path / "libzerocu_meta.so"
        so.write_bytes(b"\x7fELF" + b"\x00" * 128)

        monkeypatch.setattr(dm, "has_real_dwarf_info", lambda _elf: True)
        monkeypatch.setattr(dm, "ELFFile", lambda _f: _FakeElf())

        meta = dm.parse_dwarf_metadata(so)
        assert meta.has_dwarf is True
        assert meta.cu_total == 0
        assert meta.cu_failed == 0
        assert meta.evidence_state == "failed"

    def test_standalone_advanced_dwarf_marks_zero_cu_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck import dwarf_advanced as da

        class _EmptyDwarfInfo:
            def iter_CUs(self) -> list:
                return []

        class _FakeElf:
            def get_dwarf_info(self) -> _EmptyDwarfInfo:
                return _EmptyDwarfInfo()

            def get_section_by_name(self, _name: str) -> None:
                return None

        so = tmp_path / "libzerocu_adv.so"
        so.write_bytes(b"\x7fELF" + b"\x00" * 128)

        monkeypatch.setattr(da, "has_real_dwarf_info", lambda _elf: True)
        monkeypatch.setattr(da, "ELFFile", lambda _f: _FakeElf())
        monkeypatch.setattr(da, "_normalize_arch", lambda _elf: "x86_64")

        meta = da.parse_advanced_dwarf(so)
        assert meta.has_dwarf is True
        assert meta.cu_total == 0
        assert meta.cu_failed == 0
        assert meta.evidence_state == "failed"


class TestUnifiedPassDowngradesOnIncompleteCfi:
    """P1 review, fresh evidence: parse_dwarf_from_session (the path
    dumper.py's real ELF dumps actually use) ran _parse_frame_registers but
    exposed no completion signal from it at all -- a malformed/unsupported
    FDE caught and skipped internally left evidence_state at whatever the
    (otherwise clean) CU accounting decided, "parsed", despite frame-
    register/callee-saved-register facts for that FDE never being
    extracted. Mirrors the identical dwarf_advanced.parse_advanced_dwarf
    fix, at the unified entry point."""

    def test_incomplete_cfi_downgrades_a_clean_parse_to_partial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _require_tool("g++")
        from abicheck import dwarf_unified as du

        so = _compile_so(tmp_path, "libcfiincomplete", _SESSION_SRC, lang="cpp")
        sess = open_dwarf_session(so)
        assert sess is not None
        monkeypatch.setattr(du, "_parse_frame_registers", lambda *_a: False)
        try:
            meta, adv = du.parse_dwarf_from_session(sess)
        finally:
            sess.close()

        assert meta.evidence_state == "parsed"  # basic channel unaffected
        assert adv.cu_failed == 0
        assert adv.evidence_state == "partial"

    def test_incomplete_cfi_never_upgrades_an_already_failed_parse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Downgrading must only ever apply to a clean "parsed" state."""
        _require_tool("g++")
        from abicheck import dwarf_unified as du

        so = _compile_so(tmp_path, "libcfifailed", _SESSION_SRC, lang="cpp")
        sess = open_dwarf_session(so)
        assert sess is not None
        monkeypatch.setattr(du, "_parse_frame_registers", lambda *_a: False)

        def boom(_cu, _meta):
            raise ValueError("corrupt CU")

        monkeypatch.setattr(du, "_adv_process_cu", boom)
        try:
            meta, adv = du.parse_dwarf_from_session(sess)
        finally:
            sess.close()

        assert adv.evidence_state == "failed"


class TestUnifiedPassDowngradesOnIncompleteValueAbiTraits:
    """P1 review, fresh evidence (Codex): sibling of the above CFI gap --
    a malformed DW_AT_type on an exported function's return/parameter
    type, caught deep inside the advanced channel's value-ABI-trait walk,
    previously left cu_failed untouched and evidence_state at "parsed" on
    this unified path (the one dumper.py's real ELF dumps actually use),
    silently omitting that function's value_abi_traits entry. Uses real
    DIE fixtures (not MagicMock) so an unresolvable DW_AT_type reproduces
    the same way pyelftools' own get_DIE_from_refaddr does (it raises)."""

    def test_malformed_return_type_marks_advanced_channel_partial(self) -> None:
        from abicheck import dwarf_unified as du

        subprogram = _Die(
            "DW_TAG_subprogram",
            {
                "DW_AT_external": _Attr(1),
                "DW_AT_linkage_name": "_Z3foov",
                "DW_AT_type": _Attr(999, "DW_FORM_ref_addr"),
            },
        )
        root = _Die("DW_TAG_compile_unit", children=[subprogram])
        cu = _CU(top_die=root, offset=0)
        cu._die_map = {}

        class _DwarfInfo:
            def iter_CUs(self):
                return [cu]

        sess = DwarfSession(
            path=Path("libtest.so"),
            _file=object(),  # type: ignore[arg-type]
            elf=object(),
            dwarf=_DwarfInfo(),
            arch="x86_64",  # type: ignore[arg-type]
        )
        with (
            patch("abicheck.dwarf_unified._parse_frame_registers", return_value=True),
            patch(
                "abicheck.dwarf_utils.resolve_die_ref",
                side_effect=RuntimeError("bad ref"),
            ),
        ):
            meta, adv = du.parse_dwarf_from_session(sess)

        assert meta.evidence_state == "parsed"  # basic channel unaffected
        assert adv.cu_failed == 0
        assert adv.evidence_state == "partial"
        assert "_Z3foov" not in adv.value_abi_traits

    def test_clean_function_traits_are_not_flagged(self) -> None:
        """Positive control: a fully-resolvable by-value struct return type
        must not be flagged, and its trait must still be recorded."""
        from abicheck import dwarf_unified as du

        struct_type = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "S", "DW_AT_byte_size": 8},
            offset=10,
        )
        subprogram = _Die(
            "DW_TAG_subprogram",
            {
                "DW_AT_external": _Attr(1),
                "DW_AT_linkage_name": "_Z3barv",
                "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
            },
        )
        root = _Die("DW_TAG_compile_unit", children=[subprogram])
        cu = _CU(top_die=root, offset=0)
        cu._die_map = {10: struct_type}

        class _DwarfInfo:
            def iter_CUs(self):
                return [cu]

        sess = DwarfSession(
            path=Path("libtest.so"),
            _file=object(),  # type: ignore[arg-type]
            elf=object(),
            dwarf=_DwarfInfo(),
            arch="x86_64",  # type: ignore[arg-type]
        )
        with patch("abicheck.dwarf_unified._parse_frame_registers", return_value=True):
            meta, adv = du.parse_dwarf_from_session(sess)

        assert adv.cu_failed == 0
        assert adv.evidence_state == "parsed"
        assert adv.value_abi_traits["_Z3barv"] == "ret:trivial"


class TestUnifiedPassDowngradesOnMissingCfiSections:
    """P2 review, fresh evidence (Codex): sibling of the above incomplete-
    FDE-decode gap -- a binary with real DWARF DIEs but neither .eh_frame
    nor .debug_frame present at all (independently stripped unwind
    sections) previously reported evidence_state="parsed" on this unified
    path too. Exercised through the real public entry point
    (parse_dwarf_from_session), letting the actual _parse_frame_registers/
    _get_cfi_source pipeline run rather than patching it out."""

    def test_no_unwind_sections_at_all_marks_advanced_channel_partial(
        self,
    ) -> None:
        from abicheck import dwarf_unified as du

        struct_type = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "S", "DW_AT_byte_size": 8},
            offset=10,
        )
        subprogram = _Die(
            "DW_TAG_subprogram",
            {
                "DW_AT_external": _Attr(1),
                "DW_AT_linkage_name": "_Z3barv",
                "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
            },
        )
        root = _Die("DW_TAG_compile_unit", children=[subprogram])
        cu = _CU(top_die=root, offset=0)
        cu._die_map = {10: struct_type}

        class _DwarfInfo:
            def iter_CUs(self):
                return [cu]

            def has_EH_CFI(self):
                return False

            def has_CFI(self):
                return False

        sess = DwarfSession(
            path=Path("libtest.so"),
            _file=object(),  # type: ignore[arg-type]
            elf=object(),
            dwarf=_DwarfInfo(),
            arch="x86_64",  # type: ignore[arg-type]
        )
        with (
            patch("abicheck.dwarf_advanced._normalize_arch", return_value="x86_64"),
            patch("abicheck.dwarf_advanced._build_addr_to_sym", return_value={}),
        ):
            meta, adv = du.parse_dwarf_from_session(sess)

        assert adv.cu_failed == 0
        assert adv.evidence_state == "partial"
        assert adv.frame_registers == {}
        # The value-ABI trait (unrelated to CFI) still resolves correctly.
        assert adv.value_abi_traits["_Z3barv"] == "ret:trivial"


class TestUnifiedPassDowngradesOnIncompletePackedTypedef:
    """P1 review, fresh evidence (Codex): sibling of the above value-ABI-
    trait gap -- the separate anonymous-struct-typedef packed-check walk
    (_check_packed_typedef) previously left a malformed DW_AT_type
    invisible on this unified path too."""

    def test_malformed_typedef_target_marks_advanced_channel_partial(
        self,
    ) -> None:
        from abicheck import dwarf_unified as du

        typedef = _Die(
            "DW_TAG_typedef",
            {"DW_AT_name": "MyAlias", "DW_AT_type": _Attr(999, "DW_FORM_ref_addr")},
        )
        root = _Die("DW_TAG_compile_unit", children=[typedef])
        cu = _CU(top_die=root, offset=0)
        cu._die_map = {}

        class _DwarfInfo:
            def iter_CUs(self):
                return [cu]

            def has_EH_CFI(self):
                return True

            def EH_CFI_entries(self):
                return []

        sess = DwarfSession(
            path=Path("libtest.so"),
            _file=object(),  # type: ignore[arg-type]
            elf=object(),
            dwarf=_DwarfInfo(),
            arch="x86_64",  # type: ignore[arg-type]
        )
        with (
            patch("abicheck.dwarf_advanced._normalize_arch", return_value="x86_64"),
            patch("abicheck.dwarf_advanced._build_addr_to_sym", return_value={}),
            patch(
                "abicheck.dwarf_advanced._resolve_die_ref",
                side_effect=RuntimeError("bad ref"),
            ),
        ):
            meta, adv = du.parse_dwarf_from_session(sess)

        assert adv.cu_failed == 0
        assert adv.evidence_state == "partial"
        assert "MyAlias" not in adv.all_struct_names


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)",
)
class TestDetachedDebugCfiSource:
    """P1 review, fresh evidence (Codex): a detached-debug sidecar resolved
    via --debug-root/--debuginfod (objcopy --only-keep-debug) retains its
    own .eh_frame/.debug_frame as SHT_NOBITS -- real unwind data lives only
    in the primary (stripped) binary alongside it. Reading CFI from the
    sidecar alone (the shape before this fix) either fails outright or
    finds no FDEs, stamping the advanced channel "partial" and making
    --require-complete-analysis fail for this repository's own supported
    detached-debug workflow. Verified against real objcopy-produced
    files, not synthetic section-flag mocks."""

    @staticmethod
    def _split_debug(tmp_path: Path, so: Path) -> tuple[Path, Path]:
        """Split *so* into (primary, sidecar) the way a real packaging
        pipeline does: objcopy --only-keep-debug + --strip-debug."""
        primary = tmp_path / "primary.so"
        sidecar = tmp_path / "primary.debug"
        shutil.copy(so, primary)
        subprocess.run(
            ["objcopy", "--only-keep-debug", str(so), str(sidecar)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "objcopy",
                "--strip-debug",
                f"--add-gnu-debuglink={sidecar}",
                str(primary),
            ],
            check=True,
            capture_output=True,
        )
        return primary, sidecar

    def test_sidecar_alone_reports_partial(self, tmp_path: Path) -> None:
        """Baseline: parsing the sidecar alone (no CFI source attached) is
        the shape the reviewer reported -- CFI extraction cannot succeed
        from the sidecar's own NOBITS .eh_frame."""
        _require_tool("gcc")
        _require_tool("objcopy")
        from abicheck.dwarf_unified import parse_dwarf

        so = _compile_so(
            tmp_path, "libsplit", "int add(int a, int b) { return a + b; }"
        )
        _primary, sidecar = self._split_debug(tmp_path, so)

        meta, adv = parse_dwarf(sidecar)
        assert meta.has_dwarf is True
        assert adv.evidence_state == "partial"
        assert adv.frame_registers == {}

    def test_cfi_source_path_reads_real_unwind_data_from_primary(
        self, tmp_path: Path
    ) -> None:
        """The fix: pointing cfi_source_path at the primary binary reads
        real CFI data and reports the advanced channel complete."""
        _require_tool("gcc")
        _require_tool("objcopy")
        from abicheck.dwarf_unified import parse_dwarf

        so = _compile_so(
            tmp_path, "libsplit2", "int add(int a, int b) { return a + b; }"
        )
        primary, sidecar = self._split_debug(tmp_path, so)

        meta, adv = parse_dwarf(sidecar, cfi_source_path=primary)
        assert meta.has_dwarf is True
        assert adv.evidence_state == "parsed"
        assert "add" in adv.frame_registers

    def test_cfi_source_path_equal_to_so_path_is_a_no_op(self, tmp_path: Path) -> None:
        """Positive control: the ordinary (non-split-debug) case, where
        cfi_source_path is unset or equals so_path, is unaffected."""
        _require_tool("g++")
        so = _compile_so(tmp_path, "libnosplit", _SESSION_SRC, lang="cpp")

        meta, adv = parse_dwarf(so, cfi_source_path=so)
        meta2, adv2 = parse_dwarf(so)
        assert adv.evidence_state == adv2.evidence_state == "parsed"
        assert adv.frame_registers == adv2.frame_registers

    def test_missing_cfi_source_falls_back_gracefully(self, tmp_path: Path) -> None:
        """A cfi_source_path that cannot be opened must not break the
        DWARF session -- it degrades to the sidecar's own (NOBITS) CFI,
        same as not passing cfi_source_path at all, rather than raising."""
        _require_tool("gcc")
        _require_tool("objcopy")
        from abicheck.dwarf_unified import parse_dwarf

        so = _compile_so(
            tmp_path, "libsplit3", "int add(int a, int b) { return a + b; }"
        )
        _primary, sidecar = self._split_debug(tmp_path, so)

        meta, adv = parse_dwarf(sidecar, cfi_source_path=tmp_path / "nope.so")
        assert meta.has_dwarf is True
        assert adv.evidence_state == "partial"
