# Copyright 2026 Nikolay Petrov
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

"""The Mach-O dump routes agree on the binary-level facts of one library.

A header-free CLI dump is built by the workflow extractor (``extract_macho``); a
dump with headers goes through the dumper (``_dump_macho``). Both read the same
adjacent dSYM under the same request options, so the DWARF presence and the
exported symbol set of one library must not depend on the route taken.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.dumper import dump
from abicheck.model.debug_evidence import debug_info_evidence
from abicheck.serialization import load_snapshot
from abicheck.workflows.dump.macho import extract_macho
from tests.test_macho_dsym_reader import (
    _MH_DSYM,
    _NO_GCC,
    _bundle_beside,
    _exporting_dylib,
    _macho,
    _sections_from_elf,
)

UID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def library(tmp_path: Path) -> tuple[Path, Path]:
    """A dylib that exports one function, with a real DWARF dSYM beside it."""
    src = tmp_path / "a.c"
    src.write_text("struct P { int x; int y; };\nint twice(int x) { return 2 * x; }\n")
    elf = tmp_path / "libk.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", "-o", str(elf), str(src)], check=True
    )
    lib = _exporting_dylib(tmp_path / "libk.dylib", UID)
    _bundle_beside(
        lib,
        _macho(
            tmp_path / "x.bin",
            filetype=_MH_DSYM,
            uuid=UID,
            sections=_sections_from_elf(elf),
        ),
    )
    header = tmp_path / "a.h"
    header.write_text("struct P { int x; int y; };\nint twice(int x);\n")
    return lib, header


def _binary_facts(snapshot) -> tuple[bool, frozenset[str]]:
    """What a route must agree on: whether DWARF was read, and the exported names."""
    has_dwarf = debug_info_evidence(snapshot).basic
    exports = frozenset(e.name for e in snapshot.macho.exports if e.name)
    return has_dwarf, exports


@_NO_GCC
def test_cli_routes_agree_on_dwarf_and_exports(
    tmp_path: Path, library: tuple[Path, Path]
) -> None:
    lib, header = library
    facts = {}
    for label, extra in (("no-headers", []), ("headers", ["-H", str(header)])):
        out = tmp_path / f"{label}.json"
        result = CliRunner().invoke(main, ["dump", str(lib), *extra, "-o", str(out)])
        assert result.exit_code == 0, result.output
        facts[label] = _binary_facts(load_snapshot(out))
    assert facts["no-headers"] == facts["headers"]
    assert facts["no-headers"][0] is True, "the adjacent dSYM must be read"
    assert "twice" in facts["no-headers"][1] or "_twice" in facts["no-headers"][1]


@_NO_GCC
def test_symbols_only_reads_no_dwarf_on_either_route(
    library: tuple[Path, Path],
) -> None:
    lib, header = library
    header_free = extract_macho(lib, "1", symbols_only=True)
    assert debug_info_evidence(header_free).basic is False
    dumper_route = dump(lib, [], version="1", symbols_only=True)
    assert debug_info_evidence(dumper_route).basic is False
    default = extract_macho(lib, "1")
    assert debug_info_evidence(default).basic is True


_LINUX_GCC = pytest.mark.skipif(
    shutil.which("gcc") is None or not sys.platform.startswith("linux"),
    reason="needs a Linux gcc that emits ELF",
)
_PE_TOOLS = pytest.mark.skipif(
    shutil.which("clang") is None or shutil.which("lld-link") is None,
    reason="needs clang and lld-link to build a PE DLL with a PDB",
)


@_LINUX_GCC
def test_elf_cli_routes_agree_on_dwarf(tmp_path: Path) -> None:
    src = tmp_path / "a.c"
    src.write_text(
        "struct P { int x; int y; };\nint area(struct P p) { return p.x * p.y; }\n"
    )
    lib = tmp_path / "libk.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", "-o", str(lib), str(src)], check=True
    )
    header = tmp_path / "a.h"
    header.write_text("struct P { int x; int y; };\nint area(struct P p);\n")
    facts = {}
    for label, extra in (("no-headers", []), ("headers", ["-H", str(header)])):
        out = tmp_path / f"{label}.json"
        result = CliRunner().invoke(main, ["dump", str(lib), *extra, "-o", str(out)])
        assert result.exit_code == 0, result.output
        facts[label] = debug_info_evidence(load_snapshot(out)).basic
    assert facts["no-headers"] is True
    assert facts["no-headers"] == facts["headers"]


@_PE_TOOLS
def test_pe_cli_routes_agree_on_the_pdb(tmp_path: Path) -> None:
    src = tmp_path / "pe.c"
    src.write_text("__declspec(dllexport) int twice(int x) { return 2 * x; }\n")
    obj = tmp_path / "pe.obj"
    subprocess.run(
        [
            "clang",
            "--target=x86_64-pc-windows-msvc",
            "-g",
            "-c",
            str(src),
            "-o",
            str(obj),
        ],
        check=True,
    )
    dll = tmp_path / "pe.dll"
    subprocess.run(
        [
            "lld-link",
            "/dll",
            "/noentry",
            "/debug",
            "/export:twice",
            f"/pdb:{tmp_path / 'pe.pdb'}",
            f"/out:{dll}",
            str(obj),
        ],
        check=True,
    )
    header = tmp_path / "a.h"
    header.write_text("__declspec(dllexport) int twice(int x);\n")
    facts = {}
    for label, extra in (("no-headers", []), ("headers", ["-H", str(header)])):
        out = tmp_path / f"{label}.json"
        result = CliRunner().invoke(main, ["dump", str(dll), *extra, "-o", str(out)])
        assert result.exit_code == 0, result.output
        facts[label] = debug_info_evidence(load_snapshot(out)).basic
    assert facts["no-headers"] is True
    assert facts["no-headers"] == facts["headers"]
