"""The clang header backend records a variable's member access as castxml does.

Bug class: a header-AST backend drops a declaration fact the reference
backend records, so a change to it reads NO_CHANGE under that backend only.
The clang backend built every ``Variable`` with the default ``public``
access, so a static data member moved under ``private:``/``protected:``
reported no ``var_access_changed`` (edit corpus operators
``static_member_access_narrowed``/``_widened``).

Oracle: the C++ access rules for each member (the access specifier in
force, else ``class`` -> private / ``struct`` -> public), stated per case,
and castxml's own record of the same header -- never the clang backend's
helper.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.compare.declaration_facts import (
    CLANG_VARIABLE_ACCESS_GENERATION,
    records_variable_access,
)
from abicheck.dumper import dump
from abicheck.model.snapshot import AbiSnapshot

# (header, the access C++ gives ``S::count``)
_CASES = {
    "struct_default": ("struct S { int x; static int count; };", "public"),
    "class_default": ("class S { int x; static int count; };", "private"),
    "struct_private": ("struct S { private: static int count; };", "private"),
    "struct_protected": ("struct S { protected: static int count; };", "protected"),
    "class_public": ("class S { public: static int count; };", "public"),
    "reopened_public": (
        "class S { static int a; public: static int count; private: int y; };",
        "public",
    ),
    "nested_ns": (
        "namespace n { struct S { int f(); protected: static int count; }; }",
        "protected",
    ),
}


def _require() -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("ELF shared library")
    for tool in ("g++", "castxml", "clang++"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


def _build(root: Path, name: str, header: str) -> tuple[Path, Path]:
    d = root / name
    d.mkdir()
    (d / "a.hpp").write_text(header + "\n")
    qual = "n::S" if "namespace n" in header else "S"
    (d / "a.cpp").write_text(f'#include "a.hpp"\nint {qual}::count = 0;\n')
    so = d / "liba.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(d / "a.cpp")], check=True
    )
    return so, d / "a.hpp"


def _dump(so: Path, hdr: Path, backend: str) -> AbiSnapshot:
    return dump(
        so,
        [hdr],
        version=so.parent.name,
        compiler="c++",
        lang="c++",
        header_backend=backend,
        gcc_options="-std=c++17",
    )


def _count_access(snap: AbiSnapshot) -> str:
    (var,) = [v for v in snap.declarations.variables if v.name.endswith("count")]
    return var.access.value


@pytest.mark.integration
@pytest.mark.parametrize("case", sorted(_CASES))
def test_clang_records_static_member_access_like_castxml(
    case: str, tmp_path: Path
) -> None:
    _require()
    header, expected = _CASES[case]
    so, hdr = _build(tmp_path, case, header)
    assert _count_access(_dump(so, hdr, "castxml")) == expected
    assert _count_access(_dump(so, hdr, "clang")) == expected


@pytest.mark.integration
@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        ("struct_default", "struct_private", "var_access_changed"),
        ("struct_default", "struct_protected", "var_access_changed"),
        ("struct_private", "struct_default", "var_access_widened"),
        ("class_default", "class_public", "var_access_widened"),
    ],
)
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_static_member_access_change_is_reported_by_both_backends(
    old: str, new: str, kind: str, backend: str, tmp_path: Path
) -> None:
    _require()
    snaps = [_dump(*_build(tmp_path, c, _CASES[c][0]), backend) for c in (old, new)]
    kinds = {c.kind.value for c in compare(*snaps).changes}
    assert kind in kinds


@pytest.mark.integration
def test_unchanged_private_static_member_reports_nothing_on_clang(
    tmp_path: Path,
) -> None:
    """Negative control: a private member on both sides is no change."""
    _require()
    snaps = [
        _dump(*_build(tmp_path, f"v{i}", _CASES["class_default"][0]), "clang")
        for i in (1, 2)
    ]
    kinds = {c.kind.value for c in compare(*snaps).changes}
    assert not kinds & {"var_access_changed", "var_access_widened"}


class _Snap:
    def __init__(self, producer: str | None, gens: tuple[int, int, int] | None):
        self.ast_producer = producer
        self.fact_generations = gens


@pytest.mark.parametrize(
    ("producer", "gens", "expected"),
    [
        ("castxml", None, True),
        ("castxml", (1, 0, 0), True),
        ("clang", None, True),
        ("clang", (CLANG_VARIABLE_ACCESS_GENERATION, 0, 0), True),
        ("clang", (CLANG_VARIABLE_ACCESS_GENERATION + 3, 4, 0), True),
        # An older clang snapshot spelled every variable public.
        ("clang", (CLANG_VARIABLE_ACCESS_GENERATION - 1, 4, 0), False),
        ("clang", (0, 0, 0), False),
        ("hybrid", None, False),
        (None, None, False),
    ],
)
def test_records_variable_access_by_producer_and_generation(
    producer: str | None, gens: tuple[int, int, int] | None, expected: bool
) -> None:
    assert records_variable_access(_Snap(producer, gens)) is expected  # type: ignore[arg-type]
