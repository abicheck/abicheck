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

"""The CLI dump of a Mach-O library reads its adjacent dSYM with or without headers.

A header-free CLI dump is built by the workflow extractor; a dump with headers
goes through the dumper. Both must make the same DWARF decision, so the dSYM
DWARF is recorded in either snapshot. The dSYM and the library are built by the
reader test helpers, so this runs on any platform, not only where gcc emits ELF.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from tests.test_macho_dsym_reader import (
    _MH_DSYM,
    _NO_GCC,
    _bundle_beside,
    _exporting_dylib,
    _macho,
    _sections_from_elf,
)


@_NO_GCC
@pytest.mark.parametrize("with_headers", [False, True], ids=["no-headers", "headers"])
def test_cli_dump_records_the_adjacent_dsym_dwarf_with_or_without_headers(
    tmp_path: Path, with_headers: bool
) -> None:
    src = tmp_path / "a.c"
    src.write_text(
        "struct P { int x; int y; };\nint area(struct P p) { return p.x * p.y; }\n"
    )
    elf = tmp_path / "libk.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", "-o", str(elf), str(src)], check=True
    )
    uid = "11111111-2222-3333-4444-555555555555"
    lib = _exporting_dylib(tmp_path / "libk.dylib", uid)
    _bundle_beside(
        lib,
        _macho(
            tmp_path / "x.bin",
            filetype=_MH_DSYM,
            uuid=uid,
            sections=_sections_from_elf(elf),
        ),
    )
    out = tmp_path / "snap.json"
    args = ["dump", str(lib), "-o", str(out)]
    if with_headers:
        header = tmp_path / "a.h"
        header.write_text("struct P { int x; int y; };\nint area(struct P p);\n")
        args += ["-H", str(header)]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert '"has_dwarf": true' in out.read_text(encoding="utf-8")
