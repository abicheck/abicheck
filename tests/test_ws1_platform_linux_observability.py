"""ADR-093 oracle for the Linux platform batch of ``observable_at`` (WS-1).

Each declaration in ``model/change_catalog/platform_1.py`` is checked against
the public ``python -m abicheck compare`` on real ELF objects built with the
host's ELF toolchain:

* ``B``  -- stripped binaries, no headers, no DWARF;
* ``D``  -- the same objects built with ``-g`` (DWARF), no headers;
* ``H``  -- ``--header`` for each side (the public declarations).

What the oracle showed:

* ``func_deleted_elf_fallback``: emitted only when the public header declares
  the function on both sides (``BH``, ``DH``); binary-only and DWARF-only runs
  emit the weaker ``FUNC_REMOVED*`` kinds instead. Minimal set ``{B,H}``.
* ``exported_object_alignment_reduced``, ``inline_namespace_moved`` and
  ``abi_surface_explosion``: emitted from the binary alone, so ``{B}``.

The capability gate is ``tests/_elf_capability.py`` (an ELF-capable compiler
probe), not the operating system: a host with no ELF toolchain is a named
skip, and the registry checks in ``test_seed_observable_at.py`` still run.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.observability import UNCLASSIFIED
from tests._abicheck_subprocess import abicheck_command
from tests._elf_capability import elf_capability, skip_reason

pytestmark = pytest.mark.integration

_DECLARED = {
    "func_deleted_elf_fallback",
    "exported_object_alignment_reduced",
    "inline_namespace_moved",
    "abi_surface_explosion",
}


@lru_cache(maxsize=1)
def _elf_prefix() -> tuple[str, ...] | None:
    prefix = elf_capability("gcc", "shared")
    return tuple(prefix) if prefix is not None else None


def _build(source: str, out: Path, *, dbg: bool, c_source: bool, soname: str) -> None:
    prefix = _elf_prefix()
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    src = out.with_suffix(".c" if c_source else ".cpp")
    src.write_text(source)
    cmd = [*prefix]
    if c_source:
        cmd += ["-x", "c"]
    cmd += [
        "-shared",
        "-fPIC",
        "-O0",
        "-g" if dbg else "-s",
        str(src),
        "-o",
        str(out),
        f"-Wl,-soname,{soname}",
    ]
    if not c_source:
        cmd += ["-std=c++17"]
    subprocess.run(cmd, check=True, capture_output=True)


def _kinds(
    old: Path, new: Path, tmp: Path, headers: tuple[Path, Path] | None = None
) -> set[str]:
    out = tmp / "report.json"
    out.unlink(missing_ok=True)
    args = ["compare", str(old), str(new), "-o", f"json={out}"]
    if headers is not None:
        args += ["--header", f"old={headers[0]}", "--header", f"new={headers[1]}"]
    proc = subprocess.run(abicheck_command(*args), capture_output=True, text=True)
    assert proc.returncode != 64, proc.stderr
    assert out.exists(), proc.stderr
    kinds: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            value = node.get("kind")
            if isinstance(value, str) and value in REGISTRY.entries:
                kinds.add(value)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(json.loads(out.read_text()))
    return kinds


def test_declarations_are_no_longer_unclassified() -> None:
    for kind in _DECLARED:
        assert REGISTRY.entries[kind].observable_at is not UNCLASSIFIED, kind


# -- func_deleted_elf_fallback: the namesake (C++ mangled name lost, bare name kept) --

_NS_HEADER_OLD = "int kdl_keep(int a);\nint kdl_gone(int a);\n"
_NS_OLD = (
    '#include "api.h"\nint kdl_keep(int a) { return a; }\n'
    "int kdl_gone(int a) { return a + 1; }\n"
)
_NS_NEW = (
    '#include "api.h"\nint kdl_keep(int a) { return a; }\n'
    'extern "C" int kdl_gone_c(int a) __asm__("kdl_gone");\n'
    'extern "C" int kdl_gone_c(int a) { return a + 1; }\n'
)


def _namesake_pair(tmp: Path, *, dbg: bool) -> tuple[Path, Path, Path]:
    tag = "D" if dbg else "B"
    d = tmp / f"ns_{tag}"
    d.mkdir()
    (d / "api.h").write_text(_NS_HEADER_OLD)
    old = d / "old.so"
    new = d / "new.so"
    prefix = _elf_prefix()
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    for name, src in (("old", _NS_OLD), ("new", _NS_NEW)):
        (d / f"{name}.cpp").write_text(src)
        subprocess.run(
            [
                *prefix,
                "-std=c++17",
                "-O0",
                "-shared",
                "-fPIC",
                "-g" if dbg else "-s",
                "-I",
                str(d),
                str(d / f"{name}.cpp"),
                "-o",
                str(d / f"{name}.so"),
                "-Wl,-soname,libk.so",
            ],
            check=True,
            capture_output=True,
        )
    return old, new, d / "api.h"


@pytest.mark.parametrize("dbg", [False, True], ids=["B", "D"])
def test_namesake_is_not_emitted_without_the_public_header(
    tmp_path: Path, dbg: bool
) -> None:
    old, new, _ = _namesake_pair(tmp_path, dbg=dbg)
    assert "func_deleted_elf_fallback" not in _kinds(old, new, tmp_path)


@pytest.mark.parametrize("dbg", [False, True], ids=["BH", "DH"])
def test_namesake_is_emitted_with_the_public_header_on_both_sides(
    tmp_path: Path, dbg: bool
) -> None:
    old, new, header = _namesake_pair(tmp_path, dbg=dbg)
    kinds = _kinds(old, new, tmp_path, headers=(header, header))
    assert "func_deleted_elf_fallback" in kinds


# -- exported_object_alignment_reduced (binary alone) ---------------------------


def test_alignment_reduction_is_emitted_from_the_binary_alone(tmp_path: Path) -> None:
    old, new = tmp_path / "old.so", tmp_path / "new.so"
    _build(
        "__attribute__((aligned(64))) int obj_v = 1;\n",
        old,
        dbg=False,
        c_source=True,
        soname="libal.so",
    )
    _build(
        "__attribute__((aligned(8))) int obj_v = 1;\n",
        new,
        dbg=False,
        c_source=True,
        soname="libal.so",
    )
    assert "exported_object_alignment_reduced" in _kinds(old, new, tmp_path)


# -- inline_namespace_moved (binary alone, two symbols move) ---------------------


def test_inline_namespace_move_is_emitted_from_the_binary_alone(tmp_path: Path) -> None:
    old, new = tmp_path / "old.so", tmp_path / "new.so"
    _build(
        "namespace ns { inline namespace v1 { int f(int a){return a;} "
        "int g(int a){return a+1;} } }\n",
        old,
        dbg=False,
        c_source=False,
        soname="libi.so",
    )
    _build(
        "namespace ns { inline namespace v2 { int f(int a){return a;} "
        "int g(int a){return a+1;} } }\n",
        new,
        dbg=False,
        c_source=False,
        soname="libi.so",
    )
    assert "inline_namespace_moved" in _kinds(old, new, tmp_path)


# -- abi_surface_explosion (binary alone: export count grows >2x and by >=50) --


def test_surface_growth_is_emitted_from_the_binary_alone(tmp_path: Path) -> None:
    old, new = tmp_path / "old.so", tmp_path / "new.so"
    _build(
        "\n".join(f"int s{i}(int a){{return a+{i};}}" for i in range(12)) + "\n",
        old,
        dbg=False,
        c_source=True,
        soname="libsurf.so",
    )
    _build(
        "\n".join(f"int s{i}(int a){{return a+{i};}}" for i in range(120)) + "\n",
        new,
        dbg=False,
        c_source=True,
        soname="libsurf.so",
    )
    assert "abi_surface_explosion" in _kinds(old, new, tmp_path)
