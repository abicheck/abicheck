"""Integration tests for parse_elf_metadata() using real compiled .so files.

These tests compile minimal C shared libraries and verify the full
pyelftools parse round-trip: compile → parse_elf_metadata → assert fields.

Requires: gcc on Linux (produces ELF output).  On macOS/Windows gcc produces
Mach-O/PE binaries, so these tests are Linux-only.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from abicheck.elf_metadata import (
    ElfMetadata,
    SymbolBinding,
    SymbolType,
    parse_elf_metadata,
)

# gcc on macOS produces Mach-O (not ELF); on Windows MinGW produces PE.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="ELF integration tests require Linux (gcc produces Mach-O on macOS, PE on Windows)",
)

# ── helpers ────────────────────────────────────────────────────────────────

def _compile_so(src: str, name: str, tmp: Path, extra_flags: list[str] | None = None) -> Path:
    """Compile C source to a shared library; skip test if gcc unavailable."""
    gcc = ["gcc"] + (extra_flags or []) + ["-shared", "-fPIC", "-o", str(tmp / name), "-x", "c", "-"]
    result = subprocess.run(gcc, input=src.encode(), capture_output=True)
    if result.returncode != 0:
        pytest.skip(f"gcc failed: {result.stderr.decode()[:200]}")
    return tmp / name


# ── tests ──────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_parse_basic_so_symbols() -> None:
    """Real .so with two exported symbols → both appear in ElfMetadata.symbols."""
    src = """
    int foo(int x) { return x + 1; }
    int bar = 42;
    """
    with tempfile.TemporaryDirectory() as td:
        so = _compile_so(src, "libtest.so", Path(td))
        meta = parse_elf_metadata(so)

    assert isinstance(meta, ElfMetadata)
    names = {s.name for s in meta.symbols}
    assert "foo" in names, f"Expected 'foo' in symbols, got: {names}"
    assert "bar" in names, f"Expected 'bar' in symbols, got: {names}"

    # foo should be STT_FUNC
    foo_sym = next(s for s in meta.symbols if s.name == "foo")
    assert foo_sym.sym_type == SymbolType.FUNC
    assert foo_sym.binding == SymbolBinding.GLOBAL

    # bar should be STT_OBJECT
    bar_sym = next(s for s in meta.symbols if s.name == "bar")
    assert bar_sym.sym_type == SymbolType.OBJECT
    assert bar_sym.size > 0


@pytest.mark.integration
def test_parse_so_with_soname() -> None:
    """Library compiled with -soname → SONAME captured."""
    src = "int fn(void) { return 0; }"
    with tempfile.TemporaryDirectory() as td:
        so = _compile_so(src, "libsoname.so", Path(td),
                         extra_flags=["-Wl,-soname,libsoname.so.1"])
        meta = parse_elf_metadata(so)

    assert meta.soname == "libsoname.so.1", f"Expected soname, got: {meta.soname!r}"


@pytest.mark.integration
def test_parse_stripped_so_returns_metadata() -> None:
    """Stripped .so (no debug info) must still parse — no crash, symbols present."""
    src = "int stripped_fn(int x) { return x * 2; }"
    with tempfile.TemporaryDirectory() as td:
        so = _compile_so(src, "libstripped.so", Path(td))
        subprocess.run(["strip", str(so)], capture_output=True)  # strip debug info
        meta = parse_elf_metadata(so)

    assert isinstance(meta, ElfMetadata)
    # .dynsym survives strip; debug sections go away
    names = {s.name for s in meta.symbols}
    assert "stripped_fn" in names, f"Expected symbol after strip, got: {names}"


@pytest.mark.integration
def test_parse_so_with_needed() -> None:
    """Library with DT_NEEDED (linked against libc) → needed list non-empty."""
    src = "#include <stdlib.h>\nvoid* fn(size_t n) { return malloc(n); }"
    with tempfile.TemporaryDirectory() as td:
        so = _compile_so(src, "libneeded.so", Path(td), extra_flags=["-lc"])
        meta = parse_elf_metadata(so)

    # libc.so.6 (or similar) should appear in needed
    assert len(meta.needed) > 0, "Expected at least one DT_NEEDED entry"
    assert any("libc" in n for n in meta.needed), f"Expected libc in needed: {meta.needed}"


@pytest.mark.integration
def test_parse_hidden_symbols_excluded() -> None:
    """Hidden-visibility symbols must NOT appear in ElfMetadata.symbols."""
    src = """
    __attribute__((visibility("hidden"))) int hidden_fn(void) { return 1; }
    __attribute__((visibility("default"))) int public_fn(void) { return 2; }
    """
    with tempfile.TemporaryDirectory() as td:
        so = _compile_so(src, "libvisibility.so", Path(td))
        meta = parse_elf_metadata(so)

    names = {s.name for s in meta.symbols}
    assert "public_fn" in names, f"Expected public_fn, got: {names}"
    assert "hidden_fn" not in names, f"hidden_fn must be excluded, got: {names}"


@pytest.mark.integration
def test_parse_nonexistent_path_returns_empty() -> None:
    """Non-existent path → empty ElfMetadata, no exception."""
    meta = parse_elf_metadata(Path("/nonexistent/path/libfoo.so"))
    assert isinstance(meta, ElfMetadata)
    assert meta.symbols == []
    assert meta.soname == ""


@pytest.mark.integration
def test_parse_non_elf_file_returns_empty(tmp_path: Path) -> None:
    """Non-ELF file (plain text) → empty ElfMetadata, no exception."""
    bad = tmp_path / "notanelf.so"
    bad.write_text("this is not an ELF binary\n")
    meta = parse_elf_metadata(bad)
    assert isinstance(meta, ElfMetadata)
    assert meta.symbols == []


# ── security-hardening surface (G12) ────────────────────────────────────────

@pytest.mark.integration
def test_parse_hardened_so_captures_relro_and_canary() -> None:
    """A hardened .so reports full RELRO + BIND_NOW + stack canary."""
    src = """
    #include <string.h>
    int copy_fn(const char *s, char *out) { strcpy(out, s); return (int)strlen(out); }
    """
    with tempfile.TemporaryDirectory() as td:
        so = _compile_so(
            src, "libhardened.so", Path(td),
            extra_flags=["-O2", "-fstack-protector-all",
                         "-Wl,-z,relro,-z,now"],
        )
        meta = parse_elf_metadata(so)
    assert meta.relro == "full"
    assert meta.bind_now is True
    assert meta.has_stack_canary is True
    assert meta.has_writable_executable_segment is False


@pytest.mark.integration
def test_parse_unhardened_so_drops_relro_and_canary() -> None:
    """An unhardened .so reports no RELRO and no stack canary."""
    src = """
    int add_fn(int a, int b) { return a + b; }
    """
    with tempfile.TemporaryDirectory() as td:
        so = _compile_so(
            src, "libplain.so", Path(td),
            extra_flags=["-O0", "-fno-stack-protector",
                         "-Wl,-z,norelro,-z,lazy"],
        )
        meta = parse_elf_metadata(so)
    assert meta.relro == "none"
    assert meta.has_stack_canary is False


@pytest.mark.integration
def test_parse_cet_gnu_property_and_identity() -> None:
    """G23-A2/A3: a real -fcf-protection=full .so exposes IBT/SHSTK from
    .note.gnu.property, and the ELF identity fields are captured.

    Regression guard: pyelftools reports the note type as the string
    'NT_GNU_PROPERTY_TYPE_0' (not numeric 5), so the parser must accept it —
    otherwise the CET/branch-protection detectors never fire on real binaries.
    """
    src = "int cet_fn(int x) { return x * 2; }\n"
    with tempfile.TemporaryDirectory() as td:
        so = _compile_so(
            src, "libcet.so", Path(td),
            extra_flags=["-fcf-protection=full"],
        )
        meta = parse_elf_metadata(so)
    if not meta.gnu_properties:
        pytest.skip("toolchain did not emit .note.gnu.property CET features")
    assert "IBT" in meta.gnu_properties
    assert "SHSTK" in meta.gnu_properties
    # Identity fields are always captured on a real ELF.
    assert meta.machine.startswith("EM_")
    assert meta.elf_class in (32, 64)
    assert meta.osabi.startswith("ELFOSABI_")


@pytest.mark.integration
def test_gnu_unique_symbol_binding() -> None:
    """G23-A4: an inline static under -fgnu-unique exports an STB_GNU_UNIQUE
    symbol, which parse_elf_metadata classifies as SymbolBinding.UNIQUE."""
    src = """
    struct Counter { static int& value() { static int n = 0; return n; } };
    int use_counter() { return Counter::value(); }
    """
    with tempfile.TemporaryDirectory() as td:
        gpp = ["g++", "-fgnu-unique", "-shared", "-fPIC",
               "-o", str(Path(td) / "libuniq.so"), "-x", "c++", "-"]
        result = subprocess.run(gpp, input=src.encode(), capture_output=True)
        if result.returncode != 0:
            pytest.skip(f"g++ failed: {result.stderr.decode()[:200]}")
        meta = parse_elf_metadata(Path(td) / "libuniq.so")
    unique_syms = [s for s in meta.symbols if s.binding == SymbolBinding.UNIQUE]
    if not unique_syms:
        pytest.skip("toolchain did not emit STB_GNU_UNIQUE symbols")
    assert unique_syms, "expected at least one STB_GNU_UNIQUE symbol"
