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

"""A snapshot's ``build_mode`` is captured at dump time (known-gaps entry
"A snapshot's `build_mode` is never captured at dump time").

Invariant: a dumped ELF image records the compiler family its own toolchain
banner names whenever the image still carries ``DW_AT_producer`` or a
``.comment`` string, and records nothing (``None`` = unknown) -- never a
guessed family -- when every signal is gone. Oracles are independent of the
parser: the compiler's own ``--version`` banner, literal producer strings
those compilers ship, and pyelftools' ``ENUM_DW_LANG`` table.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from elftools.dwarf.enums import ENUM_DW_LANG

from abicheck.build_mode import detect_cxx_standard
from abicheck.diff_stdlib_impl import _effective_build_mode
from abicheck.extract import build_mode_capture
from abicheck.extract.build_mode_capture import (
    ElfBuildSignals,
    capture_elf_build_mode,
)
from abicheck.model import AbiSnapshot, Function
from abicheck.model.build_mode_facts import (
    BuildMode,
    CompilerFamily,
    CxxStandard,
    StdlibFamily,
)
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.snapshot_encode import snapshot_to_dict

# ── DW_AT_language mapping vs. the DWARF registry (pyelftools) ────────────

_C_NAMES = {"DW_LANG_C", "DW_LANG_C89", "DW_LANG_C99", "DW_LANG_C11", "DW_LANG_C17"}
_CXX_NAMES = {n for n in ENUM_DW_LANG if n.startswith("DW_LANG_C_plus_plus")}


@pytest.mark.parametrize("name", sorted(_C_NAMES | _CXX_NAMES))
def test_dwarf_language_bucket_matches_registry(name: str) -> None:
    std = detect_cxx_standard(ENUM_DW_LANG[name])
    if name in _C_NAMES:
        assert std is CxxStandard.C
    else:
        assert std not in (CxxStandard.C, CxxStandard.UNKNOWN)


@pytest.mark.parametrize(
    "name", ["DW_LANG_Fortran90", "DW_LANG_Rust", "DW_LANG_ObjC", "DW_LANG_Go"]
)
def test_non_c_family_language_is_unknown(name: str) -> None:
    assert detect_cxx_standard(ENUM_DW_LANG[name]) is CxxStandard.UNKNOWN


# ── capture over {GCC, Clang, ICX} x {producer, .comment only, nothing} ───

#: Literal producer / .comment strings these compilers emit, with the family
#: and version their own banners name.
_TOOLCHAINS = {
    "gcc": (
        "GNU C++17 13.3.0 -mtune=generic -march=x86-64 -g",
        "GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
        CompilerFamily.GCC,
        "13.3.0",
    ),
    "clang": (
        "Ubuntu clang version 18.1.3 (1ubuntu1)",
        "GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0\nUbuntu clang version 18.1.3 (1ubuntu1)",
        CompilerFamily.CLANG,
        "18.1.3",
    ),
    "icx": (
        "Intel(R) oneAPI DPC++/C++ Compiler 2024.1.0 (2024.1.0.20240308)",
        "Intel(R) oneAPI DPC++/C++ Compiler 2024.1.0 (2024.1.0.20240308)",
        CompilerFamily.ICX,
        "2024.1.0",
    ),
}


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("GNU C++17 13.3.0 -mtune=generic -march=x86-64 -g -O2", "13.3.0"),
        ("GNU C17 11.4.0 -mtune=generic -march=x86-64 -g", "11.4.0"),
        ("GNU C++14 9.4.0 -m64 -O3 -fPIC", "9.4.0"),
        ("GNU C99 12.2.1 20230201 -march=armv8-a", "12.2.1"),
        ("GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0", "13.3.0"),
        ("GCC: (GNU) 14.2.1 20240910", "14.2.1"),
    ],
)
def test_gcc_version_is_the_banner_version_not_a_flag_digit(
    text: str, version: str
) -> None:
    from abicheck.build_mode import detect_compiler_family

    assert detect_compiler_family(text, None) == (CompilerFamily.GCC, version)


def _elf_snap(tmp_path: Path, mangled: list[str] | None = None) -> AbiSnapshot:
    lib = tmp_path / "libx.so"
    lib.write_bytes(b"")
    snap = AbiSnapshot(library="libx.so", version="1")
    snap.platform = "elf"
    snap.source_path = str(lib)
    for m in mangled or []:
        snap.declarations.functions.append(
            Function(name=m, mangled=m, return_type="void")
        )
    return snap


@pytest.mark.parametrize("tc", sorted(_TOOLCHAINS))
@pytest.mark.parametrize("evidence", ["producer", "comment_only", "nothing"])
def test_capture_matrix(
    tc: str, evidence: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer, comment, family, version = _TOOLCHAINS[tc]
    signals = {
        "producer": ElfBuildSignals(producer, 0x21, comment),
        "comment_only": ElfBuildSignals(None, None, comment),
        "nothing": ElfBuildSignals(None, None, None),
    }[evidence]
    monkeypatch.setattr(
        build_mode_capture, "read_elf_build_signals", lambda _p, **_k: signals
    )
    snap = capture_elf_build_mode(_elf_snap(tmp_path))
    if evidence == "nothing":
        assert snap.build_mode is None
        return
    bm = snap.build_mode
    assert bm is not None
    assert bm.compiler_family is family
    assert bm.provenance.compiler_version == version
    assert bm.provenance.raw_comment == comment
    if evidence == "producer":
        assert bm.provenance.raw_producer == producer
        assert bm.language_std is CxxStandard.CXX14_OR_LATER
    else:
        assert bm.provenance.raw_producer is None
        assert bm.language_std is CxxStandard.UNKNOWN


def test_existing_build_mode_and_non_elf_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_p: Path, **_k: object) -> ElfBuildSignals:
        raise AssertionError("must not read")

    monkeypatch.setattr(build_mode_capture, "read_elf_build_signals", _boom)
    recorded = BuildMode(compiler_family=CompilerFamily.MSVC)
    snap = _elf_snap(tmp_path)
    snap.build_mode = recorded
    assert capture_elf_build_mode(snap).build_mode is recorded
    pe = _elf_snap(tmp_path)
    pe.platform = "pe"
    assert capture_elf_build_mode(pe).build_mode is None


def test_unreadable_image_leaves_unknown(tmp_path: Path) -> None:
    snap = _elf_snap(tmp_path)  # empty file: not ELF
    assert capture_elf_build_mode(snap).build_mode is None


def test_language_only_cu_still_records_build_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A CU with only DW_AT_language (no producer, no .comment, no mangled
    # symbol) is still evidence: language_std must be recorded.
    for lang_name, expected_c in (
        ("DW_LANG_C_plus_plus_14", False),
        ("DW_LANG_C99", True),
    ):
        signals = ElfBuildSignals(None, ENUM_DW_LANG[lang_name], None)
        monkeypatch.setattr(
            build_mode_capture, "read_elf_build_signals", lambda _p, **_k: signals
        )
        bm = capture_elf_build_mode(_elf_snap(tmp_path)).build_mode
        assert bm is not None
        assert (bm.language_std is CxxStandard.C) is expected_c
        assert bm.language_std is not CxxStandard.UNKNOWN


@pytest.mark.parametrize("flag", ["symbols_only", "debug_presence_only"])
def test_shallow_dump_never_loads_dwarf_for_build_mode(
    flag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from abicheck import dumper
    from abicheck.workflows import snapshot_factory

    seen: list[bool] = []
    real = snapshot_factory.finish_binary_dump

    def _spy(*a: object, **k: object) -> AbiSnapshot:
        seen.append(bool(k.get("read_dwarf", True)))
        return real(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(snapshot_factory, "finish_binary_dump", _spy)
    called: list[int] = []

    def _no_dwarf(*_a: object, **_k: object) -> ElfBuildSignals:
        called.append(1)
        raise AssertionError("shallow capture must not read DWARF")

    monkeypatch.setattr(build_mode_capture, "_read_producer", _no_dwarf)
    lib = Path(sys.executable).resolve()
    if not sys.platform.startswith("linux") or lib.read_bytes()[:4] != b"\x7fELF":
        pytest.skip("needs an ELF host binary")
    dumper.dump(lib, [], **{flag: True})
    assert seen == [False]
    assert called == []
    # Direct reader: shallow mode skips get_dwarf_info entirely.
    from elftools.elf.elffile import ELFFile

    monkeypatch.setattr(ELFFile, "get_dwarf_info", lambda *_a, **_k: called.append(2))
    sig = build_mode_capture.read_elf_build_signals(lib, read_dwarf=False)
    assert sig is not None and called == []


# ── compare-time consumer prefers the recorded value ──────────────────────


def test_compare_prefers_recorded_build_mode(tmp_path: Path) -> None:
    # Symbols say libstdc++; the recorded capture says libc++ v1 -> recorded wins.
    snap = _elf_snap(tmp_path, ["_ZNSt6vectorIiSaIiEE9push_backEOi"])
    snap.build_mode = BuildMode(stdlib=StdlibFamily.LIBCXX, libcpp_abi_version=1)
    assert _effective_build_mode(snap) is snap.build_mode


def test_compare_falls_back_to_symbols_when_unrecorded(tmp_path: Path) -> None:
    snap = _elf_snap(tmp_path, ["_ZNSt3__16vectorIiNS_9allocatorIiEEE9push_backEOi"])
    bm = _effective_build_mode(snap)
    assert bm is not None and bm.stdlib is StdlibFamily.LIBCXX


# ── real compilers ────────────────────────────────────────────────────────

_SRC = '#include <string>\nstd::string f(std::string s) { return s + "x"; }\n'


def _banner(cxx: str) -> tuple[CompilerFamily, str]:
    out = subprocess.run(
        [cxx, "--version"], capture_output=True, text=True, check=True
    ).stdout.splitlines()[0]
    if "clang" in out:
        m = re.search(r"clang version (\d+\.\d+\.\d+)", out)
        family = CompilerFamily.CLANG
    else:  # GCC banners end with the bare version: "g++ (...) 13.3.0"
        m = re.search(r"(\d+\.\d+\.\d+)\s*$", out)
        family = CompilerFamily.GCC
    assert m is not None, out
    return family, m.group(1)


@pytest.mark.integration
@pytest.mark.parametrize("cxx", ["g++", "clang++"])
@pytest.mark.parametrize("variant", ["debug", "strip-debug", "strip-all-comment"])
def test_real_compiler_capture(cxx: str, variant: str, tmp_path: Path) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("needs an ELF-producing host (GNU strip, .comment, DWARF)")
    if shutil.which(cxx) is None:
        pytest.skip(f"{cxx} not available")
    if variant != "debug" and shutil.which("strip") is None:
        pytest.skip("strip not available")
    from abicheck.dumper import dump

    src = tmp_path / "a.cpp"
    src.write_text(_SRC)
    lib = tmp_path / "liba.so"
    subprocess.run(
        [cxx, "-std=c++17", "-g", "-shared", "-fPIC", str(src), "-o", str(lib)],
        check=True,
    )
    if variant == "strip-debug":
        subprocess.run(["strip", "--strip-debug", str(lib)], check=True)
    elif variant == "strip-all-comment":
        subprocess.run(
            ["strip", "--strip-unneeded", "-R", ".comment", str(lib)], check=True
        )
    family, version = _banner(cxx)

    snap = dump(lib, [])
    bm = snap.build_mode
    if variant == "strip-all-comment":
        # No producer, no .comment: the compiler is unknown, never guessed.
        assert bm is None or bm.compiler_family is CompilerFamily.UNKNOWN
        return
    assert bm is not None
    assert bm.compiler_family is family
    assert bm.provenance.compiler_version == version
    if variant == "debug":
        assert bm.provenance.raw_producer
        assert bm.language_std not in (CxxStandard.UNKNOWN, CxxStandard.C)
    else:
        assert bm.provenance.raw_producer is None
    # The capture survives the stored round trip.
    back = snapshot_from_dict(snapshot_to_dict(snap)).build_mode
    assert back is not None and back.compiler_family is family
