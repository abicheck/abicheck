# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""A headerless PE dump names each export by its PE identity, as ``_dump_pe`` does.

``extract_pe`` (the live CLI/API route) builds the export-table functions of a
headerless dump. Its identity -- ``is_extern_c`` and ``entity_id`` -- must be
the one :func:`export_symbol_identity.msvc_export_function` gives, which the
legacy ``dumper._dump_pe`` route already uses. The bug class: a C++ export
spelled by its PE mangling (``_Z...`` on MinGW, ``?...`` on MSVC) was marked
``extern "C"`` by a bare prefix test on the export name.

Real DLLs are built with clang + lld (x86-64 and i686 windows-gnu targets),
so the MinGW-spelled rows run end to end through ``extract_pe``. An MSVC DLL
cannot be produced on this host, so the MSVC ``?`` rows are checked against
the identity function directly. A missing toolchain skips; a toolchain that
runs and fails fails the test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.extract.export_symbol_identity import msvc_export_function
from abicheck.workflows.dump.pe import extract_pe

pytestmark = pytest.mark.skipif(
    shutil.which("clang") is None or shutil.which("ld.lld") is None,
    reason="needs clang and ld.lld to build windows-gnu PE DLLs",
)

_ENTRY_C = "int DllMainCRTStartup(void *a, unsigned b, void *c) { return 1; }\n"
_ENTRY = 'extern "C" ' + _ENTRY_C

_C_SOURCE = "__attribute__((dllexport)) int plain_c(int a) { return a; }\n" + _ENTRY_C
_CXX_SOURCE = (
    'extern "C++" __attribute__((dllexport)) int cxx_fn(int a) { return a; }\n' + _ENTRY
)
_I686_STDCALL_SOURCE = (
    "__attribute__((dllexport)) int __stdcall stdcall_fn(int a) { return a; }\n"
    "__attribute__((dllexport)) int __fastcall fast_fn(int a) { return a; }\n"
    + _ENTRY_C
)


def _build(out: Path, source: str, target: str, *, cxx: bool) -> Path:
    src = out.with_suffix(".cpp" if cxx else ".c")
    src.write_text(source, encoding="utf-8")
    driver = "clang++" if cxx else "clang"
    cmd = [
        driver,
        f"--target={target}",
        "-shared",
        "-nostdlib",
        "-fuse-ld=lld",
        "-o",
        str(out),
        str(src),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, (cmd, proc.stderr)
    return out


def _identities(dll: Path) -> dict[str, tuple[bool, object]]:
    snap = extract_pe(dll, "1")
    return {
        fn.name: (fn.is_extern_c, fn.entity_id) for fn in snap.declarations.functions
    }


# (builder inputs, export-table name as the linker wrote it, expected is_extern_c)
_REAL_ROWS = [
    ("x64 plain C", "x86_64-windows-gnu", _C_SOURCE, False, "plain_c", True),
    ("x64 MinGW C++", "x86_64-windows-gnu", _CXX_SOURCE, True, "_Z6cxx_fni", False),
    ("i686 MinGW C++", "i686-windows-gnu", _CXX_SOURCE, True, "_Z6cxx_fni", False),
    ("i686 plain C", "i686-windows-gnu", _C_SOURCE, False, "plain_c", True),
    (
        "i686 stdcall",
        "i686-windows-gnu",
        _I686_STDCALL_SOURCE,
        False,
        "stdcall_fn@4",
        True,
    ),
    (
        "i686 fastcall",
        "i686-windows-gnu",
        _I686_STDCALL_SOURCE,
        False,
        "@fast_fn@4",
        True,
    ),
]


@pytest.mark.parametrize(
    ("label", "target", "source", "cxx", "export", "expect_extern_c"),
    _REAL_ROWS,
    ids=[row[0] for row in _REAL_ROWS],
)
def test_headerless_export_identity_matches_pe_mangling(
    tmp_path: Path,
    label: str,
    target: str,
    source: str,
    cxx: bool,
    export: str,
    expect_extern_c: bool,
) -> None:
    dll = _build(tmp_path / "lib.dll", source, target, cxx=cxx)
    identities = _identities(dll)
    assert export in identities, (label, sorted(identities))
    is_extern_c, entity_id = identities[export]
    assert is_extern_c is expect_extern_c, label
    # The identity is set, not left to a default: an unset entity_id is the
    # gap the legacy route never had.
    assert entity_id is not None, label


def test_real_mingw_cxx_export_is_not_extern_c(tmp_path: Path) -> None:
    """The reported failure: an Itanium-spelled C++ export read as extern "C"."""
    dll = _build(tmp_path / "cxx.dll", _CXX_SOURCE, "x86_64-windows-gnu", cxx=True)
    is_extern_c, _ = _identities(dll)["_Z6cxx_fni"]
    assert is_extern_c is False


@pytest.mark.parametrize(
    ("symbol", "is_x86_32", "expect_extern_c"),
    [
        # MSVC C++ decoration: the table-only rows, through the identity function.
        ("?cxx_fn@@YAHH@Z", False, False),
        ("?cxx_fn@@YAHH@Z", True, False),
        # Itanium/MinGW C++ on i686 carries one extra leading underscore.
        ("__Z6cxx_fni", True, False),
        ("_Z6cxx_fni", False, False),
        # extern "C" spellings.
        ("plain_c", False, True),
        ("_plain_c", True, True),
        ("_stdcall_fn@4", True, True),
    ],
)
def test_identity_table_for_pe_spellings(
    symbol: str, is_x86_32: bool, expect_extern_c: bool
) -> None:
    fn = msvc_export_function(symbol, is_x86_32=is_x86_32)
    assert fn.is_extern_c is expect_extern_c, (symbol, is_x86_32)
