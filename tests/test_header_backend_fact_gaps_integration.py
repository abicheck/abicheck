"""Canaries for header-backend fact gaps the generated edit corpus found.

Each test dumps a tiny real library with one header backend and asserts the
fact that backend currently fails to record (castxml and the native gcc plugin
against the clang backend, which records all of them). Every test is a
``known_gap_canary`` for a ``docs/contribute/known-gaps.md`` entry and for the
strict-xfail ``tests/_edit_corpus_known.py`` entries that name the same gap:
when one fails, the backend learned the fact -- archive the entry, drop the
corpus ``Known`` rows, and turn the canary into a regression test.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.model import AbiSnapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds ELF .so files"),
]


def _build(tmp: Path, header: str, source: str, lang: str) -> tuple[Path, Path]:
    ext = "c" if lang == "c" else "cpp"
    h = tmp / "lib.h"
    h.write_text(
        f"#pragma GCC visibility push(default)\n{header}\n#pragma GCC visibility pop\n"
    )
    src = tmp / f"lib.{ext}"
    src.write_text(f'#include "lib.h"\n{source}\n')
    so = tmp / "libgap.so"
    cc = "gcc" if lang == "c" else "g++"
    std = "-std=c17" if lang == "c" else "-std=c++14"
    subprocess.run(
        [cc, std, "-g", "-shared", "-fPIC", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
        text=True,
    )
    return so, h


def _dump(tmp: Path, header: str, source: str, lang: str, backend: str) -> AbiSnapshot:
    if backend == "gcc":
        from abicheck.extract.headers.gcc.discovery import find_plugin

        try:
            find_plugin(shutil.which("gcc" if lang == "c" else "g++") or "", lang)
        except Exception as exc:  # the plugin is built on demand, not by default
            pytest.skip(f"native gcc plugin unavailable: {exc}")
    elif shutil.which(backend) is None and backend == "castxml":
        pytest.skip("castxml unavailable")
    tmp.mkdir(parents=True, exist_ok=True)
    so, h = _build(tmp, header, source, lang)
    std = "c17" if lang == "c" else "c++14"
    return dump(
        so,
        [h],
        version="1",
        compiler="cc" if lang == "c" else "c++",
        lang=lang,
        header_backend=backend,
        gcc_options=f"-std={std}",
    )


def _function(snap: AbiSnapshot, name: str):  # type: ignore[no-untyped-def]
    found = [f for f in snap.declarations.functions if f.name == name]
    assert len(found) == 1, [f.name for f in snap.declarations.functions]
    return found[0]


@pytest.mark.known_gap_canary
def test_castxml_records_no_gnu_contract_attribute(tmp_path: Path) -> None:
    """castxml 0.7 writes no ``nonnull``/``returns_nonnull`` on a <Function>,
    so the castxml snapshot has no contract tokens where clang has both."""
    header = "int put(char *p) __attribute__((nonnull(1)));"
    source = "int put(char *p) { return *p; }"
    castxml = _function(_dump(tmp_path / "x", header, source, "c", "castxml"), "put")
    clang = _function(_dump(tmp_path / "c", header, source, "c", "clang"), "put")
    assert not (castxml.contract_attributes or [])
    assert any("nonnull" in t for t in clang.contract_attributes or [])


def _va_list_fact(backend: str, tmp: Path) -> object:
    header = "#include <stdarg.h>\nint vlog(const char *fmt, va_list ap);"
    source = (
        "int vlog(const char *fmt, va_list ap) { return fmt[0] + va_arg(ap, int); }"
    )
    f = _function(_dump(tmp, header, source, "c", backend), "vlog")
    return f.params[1].is_va_list_fact


@pytest.mark.known_gap_canary
@pytest.mark.parametrize("backend", ["castxml", "gcc"])
def test_only_the_clang_backend_marks_a_va_list_parameter(
    tmp_path: Path, backend: str
) -> None:
    """castxml and the native gcc plugin never establish ``Param.is_va_list``;
    clang reads the x86-64 ``__va_list_tag *`` spelling."""
    if sys.maxsize <= 2**32:
        pytest.skip("the clang reading is x86-64 SysV only")
    gapped = _va_list_fact(backend, tmp_path / backend)
    clang = _va_list_fact("clang", tmp_path / "clang")
    assert getattr(gapped, "value", None) is not True
    assert getattr(clang, "value", None) is True


@pytest.mark.known_gap_canary
def test_the_gcc_backend_records_no_dynamic_exception_spec(tmp_path: Path) -> None:
    header = "int thrower(int a) throw(int);"
    source = "int thrower(int a) throw(int) { if (a < 0) throw 1; return a; }"
    gcc = _function(_dump(tmp_path / "g", header, source, "c++", "gcc"), "thrower")
    castxml = _function(
        _dump(tmp_path / "x", header, source, "c++", "castxml"), "thrower"
    )
    assert gcc.exception_spec is None
    assert castxml.exception_spec


@pytest.mark.known_gap_canary
def test_the_gcc_backend_keeps_an_unexported_declaration_public(tmp_path: Path) -> None:
    """A header-declared function the library does not export: castxml and
    clang record it HIDDEN (binary_exported false), the gcc plugin PUBLIC."""
    header = "int late_f(int x);\nint keep(int x);"
    source = "int keep(int x) { return x; }"
    gcc = _function(_dump(tmp_path / "g", header, source, "c", "gcc"), "late_f")
    castxml = _function(_dump(tmp_path / "x", header, source, "c", "castxml"), "late_f")
    assert gcc.binary_exported_fact.value is False
    assert gcc.visibility.name == "PUBLIC"
    assert castxml.visibility.name == "HIDDEN"
