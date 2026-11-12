"""castxml's compiler-declared builtins are not declarations of the library.

Bug class ``extraction.implicit_declaration_leaks_into_surface``: castxml emits
``<Function name="__builtin_memcpy" artificial="1">`` located at a record in
the user's header whenever an implicit copy of an array member uses it, and the
castxml backend recorded it as a public-header function. With the CLI's
public-header set (``abicheck dump -H``) every such header then read
``public_not_exported`` for ``__builtin_memcpy`` (found by the edit corpus's
``cxx_classes`` seed once its harness applied that provenance).

Several independently chosen records that make the compiler declare a builtin
are dumped for real; the oracle is the header text itself: the functions it
spells, and only those, are recorded.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.workflows.compare_policy import compare_snapshots
from abicheck.workflows.snapshot_factory import finish_provenance

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds ELF .so files"),
    pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml unavailable"),
]

RECORDS = {
    "char_array": "struct rec { int id; char tag[8]; };",
    "int_array": "struct grid { int cells[4][4]; };",
    "nested_array": "struct inner { short s[3]; };\nstruct outer { inner parts[2]; long n; };",
    "array_and_double": "struct sample { double w; unsigned char bytes[16]; };",
}


def _snapshot(tmp: Path, body: str, version: str):  # type: ignore[no-untyped-def]
    tmp.mkdir(parents=True, exist_ok=True)
    header = tmp / "lib.h"
    header.write_text(
        "#pragma GCC visibility push(default)\n"
        f"{body}\nint keep(int a);\nint my_memcpy(int a);\n"
        "#pragma GCC visibility pop\n"
    )
    (tmp / "lib.cpp").write_text(
        '#include "lib.h"\nint keep(int a) { return a; }\nint my_memcpy(int a) { return a; }\n'
    )
    so = tmp / "liblib.so"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-g",
            "-shared",
            "-fPIC",
            "-o",
            str(so),
            str(tmp / "lib.cpp"),
        ],
        check=True,
        capture_output=True,
    )
    snap = dump(
        so,
        [header],
        version=version,
        compiler="c++",
        lang="c++",
        header_backend="castxml",
        gcc_options="-std=c++17",
    )
    return finish_provenance(snap, [header], None), header


@pytest.mark.parametrize("record", sorted(RECORDS))
def test_only_the_functions_the_header_spells_are_recorded(
    tmp_path: Path, record: str
) -> None:
    snap, header = _snapshot(tmp_path, RECORDS[record], "1")
    spelled = set(re.findall(r"\b(\w+)\s*\(", header.read_text())) - {"push", "pop"}
    recorded = {
        f.name
        for f in snap.declarations.functions
        if not f.name.startswith(
            ("rec", "grid", "inner", "outer", "sample", "operator", "~")
        )
    }
    assert recorded == spelled == {"keep", "my_memcpy"}


@pytest.mark.parametrize("record", sorted(RECORDS))
def test_a_public_header_set_reads_no_unexported_builtin(
    tmp_path: Path, record: str
) -> None:
    old, _ = _snapshot(tmp_path / "old", RECORDS[record], "1")
    new, _ = _snapshot(tmp_path / "new", RECORDS[record], "2")
    result = compare_snapshots(old, new)
    assert {
        c.symbol for c in result.changes if c.kind.name == "PUBLIC_NOT_EXPORTED"
    } == set()
