"""Cross-platform integration tests for PE and Mach-O metadata parsing.

These tests compile native shared libraries on the host platform and verify
the full round-trip: compile → parse metadata → assert fields.

Platform requirements:
- macOS: clang (ships with Xcode Command Line Tools)
- Windows: gcc (MinGW) — produces PE DLLs with export tables
- Linux: gcc — produces ELF .so files (covered by test_elf_parse_integration.py)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None


_IS_MACOS = sys.platform == "darwin"
_IS_WINDOWS = sys.platform == "win32"

# On macOS, clang is the standard compiler; on Windows, MinGW gcc produces DLLs.
_HAS_MACOS_COMPILER = _IS_MACOS and _has_tool("clang")
_HAS_WINDOWS_COMPILER = _IS_WINDOWS and _has_tool("gcc")

skip_unless_macos = pytest.mark.skipif(
    not _HAS_MACOS_COMPILER,
    reason="Requires macOS with clang",
)
skip_unless_windows = pytest.mark.skipif(
    not _HAS_WINDOWS_COMPILER,
    reason="Requires Windows with MinGW gcc",
)


def _compile_dylib(src: str, name: str, tmp: Path,
                   extra_flags: list[str] | None = None) -> Path:
    """Compile C source to a macOS dynamic library (.dylib)."""
    out = tmp / name
    cmd = [
        "clang", "-shared", "-fPIC",
        "-o", str(out), "-x", "c", "-",
        *(extra_flags or []),
    ]
    result = subprocess.run(cmd, input=src.encode(), capture_output=True)
    if result.returncode != 0:
        pytest.skip(f"clang failed: {result.stderr.decode()[:200]}")
    return out


def _compile_dll(src: str, name: str, tmp: Path,
                 extra_flags: list[str] | None = None) -> Path:
    """Compile C source to a Windows DLL using MinGW gcc."""
    out = tmp / name
    cmd = [
        "gcc", "-shared",
        "-o", str(out), "-x", "c", "-",
        *(extra_flags or []),
    ]
    result = subprocess.run(cmd, input=src.encode(), capture_output=True)
    if result.returncode != 0:
        pytest.skip(f"gcc failed: {result.stderr.decode()[:200]}")
    return out


# ---------------------------------------------------------------------------
# macOS / Mach-O integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
@skip_unless_macos
class TestMachoIntegration:
    """Integration tests that compile real .dylib files on macOS."""

    def test_parse_basic_dylib_symbols(self) -> None:
        """Real .dylib with two exported symbols → both appear in MachoMetadata."""
        from abicheck.macho_metadata import MachoMetadata, parse_macho_metadata

        src = """
        int foo(int x) { return x + 1; }
        int bar = 42;
        """
        with tempfile.TemporaryDirectory() as td:
            dylib = _compile_dylib(src, "libtest.dylib", Path(td))
            meta = parse_macho_metadata(dylib)

        assert isinstance(meta, MachoMetadata)
        names = {e.name for e in meta.exports}
        assert "foo" in names, f"Expected 'foo' in exports, got: {names}"
        assert "bar" in names, f"Expected 'bar' in exports, got: {names}"

    def test_parse_dylib_filetype(self) -> None:
        """Compiled .dylib should have MH_DYLIB filetype."""
        from abicheck.macho_metadata import parse_macho_metadata

        src = "int fn(void) { return 0; }"
        with tempfile.TemporaryDirectory() as td:
            dylib = _compile_dylib(src, "libtype.dylib", Path(td))
            meta = parse_macho_metadata(dylib)

        assert meta.filetype == "MH_DYLIB"

    def test_parse_dylib_with_install_name(self) -> None:
        """Library compiled with -install_name → install_name captured."""
        from abicheck.macho_metadata import parse_macho_metadata

        src = "int fn(void) { return 0; }"
        with tempfile.TemporaryDirectory() as td:
            dylib = _compile_dylib(
                src, "libinstname.dylib", Path(td),
                extra_flags=["-install_name", "@rpath/libinstname.1.dylib"],
            )
            meta = parse_macho_metadata(dylib)

        assert "libinstname.1.dylib" in meta.install_name

    def test_parse_dylib_with_deps(self) -> None:
        """Library linked against libSystem → dependent_libs non-empty."""
        from abicheck.macho_metadata import parse_macho_metadata

        src = "#include <stdlib.h>\nvoid* fn(size_t n) { return malloc(n); }"
        with tempfile.TemporaryDirectory() as td:
            dylib = _compile_dylib(src, "libdeps.dylib", Path(td))
            meta = parse_macho_metadata(dylib)

        assert len(meta.dependent_libs) > 0, "Expected at least one dependent lib"
        # macOS links against libSystem.B.dylib (or similar)
        assert any("libSystem" in d or "libc" in d.lower()
                    for d in meta.dependent_libs), \
            f"Expected system lib in deps: {meta.dependent_libs}"

    def test_parse_hidden_symbols_excluded(self) -> None:
        """Hidden-visibility symbols must NOT appear in MachoMetadata.exports."""
        from abicheck.macho_metadata import parse_macho_metadata

        src = """
        __attribute__((visibility("hidden"))) int hidden_fn(void) { return 1; }
        __attribute__((visibility("default"))) int public_fn(void) { return 2; }
        """
        with tempfile.TemporaryDirectory() as td:
            dylib = _compile_dylib(src, "libvis.dylib", Path(td))
            meta = parse_macho_metadata(dylib)

        names = {e.name for e in meta.exports}
        assert "public_fn" in names, f"Expected public_fn, got: {names}"
        assert "hidden_fn" not in names, f"hidden_fn must be excluded, got: {names}"

    def test_parse_nonexistent_path_returns_empty(self) -> None:
        """Non-existent path → empty MachoMetadata, no exception."""
        from abicheck.macho_metadata import MachoMetadata, parse_macho_metadata

        meta = parse_macho_metadata(Path("/nonexistent/path/libfoo.dylib"))
        assert isinstance(meta, MachoMetadata)
        assert meta.exports == []
        assert meta.install_name == ""

    def test_parse_non_macho_file_returns_empty(self, tmp_path: Path) -> None:
        """Non-Mach-O file → empty MachoMetadata, no exception."""
        from abicheck.macho_metadata import MachoMetadata, parse_macho_metadata

        bad = tmp_path / "notamacho.dylib"
        bad.write_text("this is not a Mach-O binary\n")
        meta = parse_macho_metadata(bad)
        assert isinstance(meta, MachoMetadata)
        assert meta.exports == []

    def test_cli_dump_macho(self) -> None:
        """CLI dump command works with Mach-O input."""
        import json

        src = "int api_fn(void) { return 42; }"
        with tempfile.TemporaryDirectory() as td:
            dylib = _compile_dylib(src, "libcli.dylib", Path(td))
            result = subprocess.run(
                [sys.executable, "-m", "abicheck.cli", "dump", str(dylib)],
                capture_output=True, text=True, check=False,
            )

        assert result.returncode == 0, f"dump failed: {result.stderr}"
        snap = json.loads(result.stdout)
        assert snap["platform"] == "macho"
        func_names = [f["name"] for f in snap.get("functions", [])]
        assert "api_fn" in func_names

    def test_cli_compare_macho(self) -> None:
        """CLI compare detects symbol removal in Mach-O dylibs."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            old = _compile_dylib(
                "int api_fn(void) { return 1; }\nint other_fn(void) { return 2; }",
                "libold.dylib", td_path,
            )
            new = _compile_dylib(
                "int other_fn(void) { return 2; }",
                "libnew.dylib", td_path,
            )

            # Dump both
            for lib, out in [(old, "old.json"), (new, "new.json")]:
                r = subprocess.run(
                    [sys.executable, "-m", "abicheck.cli", "dump", str(lib),
                     "-o", str(td_path / out)],
                    capture_output=True, text=True, check=False,
                )
                assert r.returncode == 0, f"dump failed: {r.stderr}"

            # Compare
            cmp = subprocess.run(
                [sys.executable, "-m", "abicheck.cli", "compare",
                 str(td_path / "old.json"), str(td_path / "new.json"),
                 "--format", "markdown"],
                capture_output=True, text=True, check=False,
            )

        assert cmp.returncode == 4, (
            f"Expected BREAKING (exit 4), got {cmp.returncode}\n"
            f"stderr: {cmp.stderr}\nstdout: {cmp.stdout}"
        )
        assert "api_fn" in cmp.stdout

    def test_native_dylib_to_dylib_compare_breaking(self) -> None:
        """compare two native .dylib files DIRECTLY (no JSON dump) → BREAKING.

        Exercises the native Mach-O resolve_input → compare workflow end-to-end
        on the export table, which needs no DWARF.
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            old = _compile_dylib(
                "int api_fn(void){return 1;}\nint other_fn(void){return 2;}",
                "libold.dylib", td_path,
            )
            new = _compile_dylib("int other_fn(void){return 2;}", "libnew.dylib", td_path)
            cmp = subprocess.run(
                [sys.executable, "-m", "abicheck.cli", "compare", str(old), str(new)],
                capture_output=True, text=True, check=False,
            )
        assert cmp.returncode == 4, (
            f"Expected BREAKING (exit 4), got {cmp.returncode}\n{cmp.stderr}\n{cmp.stdout}"
        )
        assert "api_fn" in cmp.stdout

    def test_native_identical_dylib_is_compatible(self) -> None:
        """Identical native dylibs compare COMPATIBLE (exit 0)."""
        src = "int api_fn(void){return 1;}\nint other_fn(void){return 2;}"
        # Pin a *shared* install_name on both builds: without -install_name,
        # clang derives LC_ID_DYLIB from the (differing) output path, which the
        # Mach-O diff would report as SONAME_CHANGED → BREAKING and defeat this
        # no-change control. Both being identical, their install_names must match.
        same_id = ["-Wl,-install_name,@rpath/libidentical.dylib"]
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            a = _compile_dylib(src, "liba.dylib", td_path, extra_flags=same_id)
            b = _compile_dylib(src, "libb.dylib", td_path, extra_flags=same_id)
            cmp = subprocess.run(
                [sys.executable, "-m", "abicheck.cli", "compare", str(a), str(b)],
                capture_output=True, text=True, check=False,
            )
        assert cmp.returncode == 0, (
            f"Expected COMPATIBLE (exit 0), got {cmp.returncode}\n{cmp.stderr}\n{cmp.stdout}"
        )


# ---------------------------------------------------------------------------
# Windows / PE integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
@skip_unless_windows
class TestPeIntegration:
    """Integration tests that compile real DLLs on Windows (MinGW)."""

    def test_parse_basic_dll_exports(self) -> None:
        """Real DLL with exported symbols → appear in PeMetadata."""
        from abicheck.pe_metadata import PeMetadata, parse_pe_metadata

        # __declspec(dllexport) is needed on Windows to export symbols
        src = """
        __declspec(dllexport) int foo(int x) { return x + 1; }
        __declspec(dllexport) int bar = 42;
        """
        with tempfile.TemporaryDirectory() as td:
            dll = _compile_dll(src, "test.dll", Path(td))
            meta = parse_pe_metadata(dll)

        assert isinstance(meta, PeMetadata)
        names = {e.name for e in meta.exports}
        assert "foo" in names, f"Expected 'foo' in exports, got: {names}"
        assert "bar" in names, f"Expected 'bar' in exports, got: {names}"

    def test_parse_dll_machine_type(self) -> None:
        """DLL should report a valid machine type."""
        from abicheck.pe_metadata import parse_pe_metadata

        src = "__declspec(dllexport) int fn(void) { return 0; }"
        with tempfile.TemporaryDirectory() as td:
            dll = _compile_dll(src, "machine.dll", Path(td))
            meta = parse_pe_metadata(dll)

        assert meta.machine != "", "Expected non-empty machine type"
        assert "MACHINE" in meta.machine, f"Unexpected machine: {meta.machine}"

    def test_parse_dll_with_imports(self) -> None:
        """DLL linked against system libs → imports dict non-empty."""
        from abicheck.pe_metadata import parse_pe_metadata

        src = """
        #include <stdlib.h>
        __declspec(dllexport) void* fn(size_t n) { return malloc(n); }
        """
        with tempfile.TemporaryDirectory() as td:
            dll = _compile_dll(src, "imports.dll", Path(td))
            meta = parse_pe_metadata(dll)

        assert len(meta.imports) > 0, "Expected at least one import DLL"

    def test_parse_nonexistent_path_returns_empty(self) -> None:
        """Non-existent path → empty PeMetadata, no exception."""
        from abicheck.pe_metadata import PeMetadata, parse_pe_metadata

        meta = parse_pe_metadata(Path("C:\\nonexistent\\path\\foo.dll"))
        assert isinstance(meta, PeMetadata)
        assert meta.exports == []

    def test_parse_non_pe_file_returns_empty(self, tmp_path: Path) -> None:
        """Non-PE file → empty PeMetadata, no exception."""
        from abicheck.pe_metadata import PeMetadata, parse_pe_metadata

        bad = tmp_path / "notape.dll"
        bad.write_text("this is not a PE binary\n")
        meta = parse_pe_metadata(bad)
        assert isinstance(meta, PeMetadata)
        assert meta.exports == []

    def test_cli_dump_pe(self) -> None:
        """CLI dump command works with PE DLL input."""
        import json

        src = "__declspec(dllexport) int api_fn(void) { return 42; }"
        with tempfile.TemporaryDirectory() as td:
            dll = _compile_dll(src, "cli.dll", Path(td))
            result = subprocess.run(
                [sys.executable, "-m", "abicheck.cli", "dump", str(dll)],
                capture_output=True, text=True, check=False,
            )

        assert result.returncode == 0, f"dump failed: {result.stderr}"
        snap = json.loads(result.stdout)
        assert snap["platform"] == "pe"
        func_names = [f["name"] for f in snap.get("functions", [])]
        assert "api_fn" in func_names

    def test_cli_compare_pe(self) -> None:
        """CLI compare detects symbol removal in PE DLLs."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            old = _compile_dll(
                "__declspec(dllexport) int api_fn(void) { return 1; }\n"
                "__declspec(dllexport) int other_fn(void) { return 2; }",
                "old.dll", td_path,
            )
            new = _compile_dll(
                "__declspec(dllexport) int other_fn(void) { return 2; }",
                "new.dll", td_path,
            )

            # Dump both
            for lib, out in [(old, "old.json"), (new, "new.json")]:
                r = subprocess.run(
                    [sys.executable, "-m", "abicheck.cli", "dump", str(lib),
                     "-o", str(td_path / out)],
                    capture_output=True, text=True, check=False,
                )
                assert r.returncode == 0, f"dump failed: {r.stderr}"

            # Compare — use PYTHONUTF8=1 so the child process writes UTF-8
            # (avoids UnicodeEncodeError from emoji on Windows cp1252), and
            # encoding="utf-8" so the *parent* decodes the pipe correctly.
            env = {**subprocess.os.environ, "PYTHONUTF8": "1"}
            cmp = subprocess.run(
                [sys.executable, "-m", "abicheck.cli", "compare",
                 str(td_path / "old.json"), str(td_path / "new.json"),
                 "--format", "markdown"],
                capture_output=True, encoding="utf-8", check=False, env=env,
            )

        assert cmp.returncode == 4, (
            f"Expected BREAKING (exit 4), got {cmp.returncode}\n"
            f"stderr: {cmp.stderr}\nstdout: {cmp.stdout}"
        )
        assert "api_fn" in (cmp.stdout or "")

    def test_native_dll_to_dll_compare_breaking(self) -> None:
        """compare two native .dll files DIRECTLY (no JSON dump) → BREAKING."""
        env = {**subprocess.os.environ, "PYTHONUTF8": "1"}
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            old = _compile_dll(
                "__declspec(dllexport) int api_fn(void){return 1;}\n"
                "__declspec(dllexport) int other_fn(void){return 2;}",
                "old.dll", td_path,
            )
            new = _compile_dll(
                "__declspec(dllexport) int other_fn(void){return 2;}", "new.dll", td_path,
            )
            cmp = subprocess.run(
                [sys.executable, "-m", "abicheck.cli", "compare", str(old), str(new)],
                capture_output=True, encoding="utf-8", check=False, env=env,
            )
        assert cmp.returncode == 4, (
            f"Expected BREAKING (exit 4), got {cmp.returncode}\n{cmp.stderr}\n{cmp.stdout}"
        )
        assert "api_fn" in (cmp.stdout or "")

    def test_native_identical_dll_is_compatible(self) -> None:
        """Identical native DLLs compare COMPATIBLE (exit 0)."""
        env = {**subprocess.os.environ, "PYTHONUTF8": "1"}
        src = ("__declspec(dllexport) int api_fn(void){return 1;}\n"
               "__declspec(dllexport) int other_fn(void){return 2;}")
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            a = _compile_dll(src, "a.dll", td_path)
            b = _compile_dll(src, "b.dll", td_path)
            cmp = subprocess.run(
                [sys.executable, "-m", "abicheck.cli", "compare", str(a), str(b)],
                capture_output=True, encoding="utf-8", check=False, env=env,
            )
        assert cmp.returncode == 0, (
            f"Expected COMPATIBLE (exit 0), got {cmp.returncode}\n{cmp.stderr}\n{cmp.stdout}"
        )
