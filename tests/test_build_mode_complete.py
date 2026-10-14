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

"""Closing known gap "A snapshot's `build_mode` is captured only for ELF, and
only from the main image".

Invariants:

* **Toolchain switch is never a clean comparison.** When both snapshots
  record a known compiler family and the families differ, ``compare`` keeps
  a verdict but bounds ``layout``/``runtime`` as unverified and says why in
  ``coverage_warnings``. A family unrecorded/unknown on either side is
  unknown -- never a mismatch. The oracle is a literal table of which pairs
  are switches, not the implementation's predicate.
* **Detached debuginfo is read** for the producer when the main image has
  none (and never in a shallow dump).
* **Mach-O** producer comes from a ``__DWARF`` segment (object file or dSYM);
  **PE** stays unknown.
"""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from elftools.dwarf.enums import ENUM_DW_LANG

from abicheck.checker import compare
from abicheck.extract import build_mode_capture, macho_build_signals
from abicheck.extract.build_mode_capture import (
    ElfBuildSignals,
    capture_build_mode,
    capture_elf_build_mode,
    read_elf_build_signals,
)
from abicheck.extract.macho_build_signals import (
    macho_dwarf_sections,
    read_macho_producer,
)
from abicheck.model import AbiSnapshot
from abicheck.model.build_mode_facts import BuildMode, CompilerFamily

# ── 1. compiler-family switch across snapshots ────────────────────────────

#: Independent oracle: the families a recorded capture can name that are a
#: real, known toolchain (``unknown`` means "no evidence").
_KNOWN_FAMILIES = {"gcc", "clang", "icx", "icc", "msvc"}
_ALL_DIMENSIONS = {"symbol", "declaration", "layout", "runtime", "source"}
_SWITCH_UNVERIFIED = {"layout", "runtime"}

_FAMILY_CASES = [
    ("gcc", "clang"),
    ("clang", "gcc"),
    ("gcc", "icx"),
    ("clang", "icx"),
    ("gcc", "gcc"),
    ("clang", "clang"),
    ("gcc", "unknown"),
    ("unknown", "clang"),
]


def _snap(version: str, family: str | None) -> AbiSnapshot:
    snap = AbiSnapshot(library="libx.so", version=version)
    if family is not None:
        snap.build_mode = BuildMode(compiler_family=CompilerFamily(family))
    return snap


def _expected_unverified(old: str | None, new: str | None) -> set[str]:
    if old is None or new is None:
        return set()
    if old not in _KNOWN_FAMILIES or new not in _KNOWN_FAMILIES:
        return set()
    return set() if old == new else set(_SWITCH_UNVERIFIED)


@pytest.mark.parametrize(("old_family", "new_family"), _FAMILY_CASES)
@pytest.mark.parametrize("recorded", ["both", "old_unrecorded", "new_unrecorded"])
def test_toolchain_switch_assurance_matrix(
    old_family: str, new_family: str, recorded: str
) -> None:
    old_f = None if recorded == "old_unrecorded" else old_family
    new_f = None if recorded == "new_unrecorded" else new_family
    result = compare(_snap("1", old_f), _snap("2", new_f))
    expected = _expected_unverified(old_f, new_f)

    # A bounded run is never a forced (refused-then-overridden) one.
    assert result.assurance is None
    if not expected:
        assert result.comparability_assurance is None
        assert not any("compiler family" in w for w in result.coverage_warnings)
        return
    assurance = result.comparability_assurance
    assert assurance is not None
    assert set(assurance) == _ALL_DIMENSIONS
    assert {d for d, s in assurance.items() if s == "unverified"} == expected
    warnings = [w for w in result.coverage_warnings if "compiler family" in w]
    assert len(warnings) == 1
    assert f"old: {old_f}" in warnings[0] and f"new: {new_f}" in warnings[0]


def test_fatal_axis_still_wins_over_toolchain_bound() -> None:
    # Two explicit, differing dependency_scope tags refuse; a co-occurring
    # compiler switch (non-fatal) must not mask that refusal.
    from abicheck.comparability import check_contracts_comparable
    from abicheck.errors import ScopeMismatchError

    old, new = _snap("1", "gcc"), _snap("2", "clang")
    old.from_headers = new.from_headers = True
    old.dependency_scope, new.dependency_scope = "filtered", "full"
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


# ── 2. detached debuginfo ─────────────────────────────────────────────────


class _FakeElf:
    """Stands in for pyelftools' ``ELFFile``: no ``.comment``, no DWARF."""

    def __init__(self, _fh: object) -> None:
        pass

    def get_section_by_name(self, _name: str) -> None:
        return None


def test_detached_debug_consulted_only_when_image_has_no_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from elftools.elf import elffile

    monkeypatch.setattr(elffile, "ELFFile", _FakeElf)
    lib = tmp_path / "libx.so"
    lib.write_bytes(b"\x7fELF")
    lang = ENUM_DW_LANG["DW_LANG_C_plus_plus_14"]
    seen: list[Path] = []

    def _fake_detached(path: Path) -> tuple[str | None, int | None]:
        seen.append(path)
        return "GNU C++17 13.3.0 -g", lang

    monkeypatch.setattr(build_mode_capture, "_read_detached_producer", _fake_detached)
    debug = Path("/nonexistent/libx.debug")

    monkeypatch.setattr(build_mode_capture, "_read_producer", lambda _e: (None, None))
    sig = read_elf_build_signals(lib, debug_path=debug)
    assert sig is not None
    assert sig.producer == "GNU C++17 13.3.0 -g" and sig.dwarf_language == lang
    assert seen == [debug]

    # Shallow mode reads neither file's DWARF.
    seen.clear()
    sig = read_elf_build_signals(lib, read_dwarf=False, debug_path=debug)
    assert sig is not None and sig.producer is None and seen == []

    # The image's own producer wins; the debug file is not opened.
    monkeypatch.setattr(
        build_mode_capture, "_read_producer", lambda _e: ("clang version 18.1.3", 4)
    )
    sig = read_elf_build_signals(lib, debug_path=debug)
    assert sig is not None and sig.producer == "clang version 18.1.3"
    assert seen == []


def test_unreadable_detached_debug_is_unknown(tmp_path: Path) -> None:
    junk = tmp_path / "x.debug"
    junk.write_bytes(b"not an elf")
    assert build_mode_capture._read_detached_producer(junk) == (None, None)
    assert build_mode_capture._read_detached_producer(tmp_path / "missing") == (
        None,
        None,
    )


def test_capture_threads_debug_info_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = tmp_path / "libx.so"
    lib.write_bytes(b"")
    snap = AbiSnapshot(library="libx.so", version="1")
    snap.platform, snap.source_path = "elf", str(lib)
    got: dict[str, object] = {}

    def _fake(path: Path, **kw: object) -> ElfBuildSignals:
        got.update(kw)
        return ElfBuildSignals(producer="GNU C 13.3.0", dwarf_language=None)

    monkeypatch.setattr(build_mode_capture, "read_elf_build_signals", _fake)
    debug = tmp_path / "libx.debug"
    out = capture_build_mode(snap, debug_info_path=debug).build_mode
    assert out is not None and out.compiler_family is CompilerFamily.GCC
    assert got == {"read_dwarf": True, "debug_path": debug}


@pytest.mark.integration
def test_detached_debug_real_gcc(tmp_path: Path) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("needs an ELF-producing host")
    for tool in ("gcc", "objcopy"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")
    from abicheck.dumper import dump

    src = tmp_path / "a.c"
    src.write_text("int f(int x) { return x + 1; }\n")
    lib = tmp_path / "liba.so"
    debug = tmp_path / "liba.so.debug"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", str(src), "-o", str(lib)], check=True
    )
    subprocess.run(["objcopy", "--only-keep-debug", str(lib), str(debug)], check=True)
    # Strip debug info and .comment from the main image: no signal remains.
    subprocess.run(
        ["objcopy", "--strip-debug", "--remove-section=.comment", str(lib)],
        check=True,
    )
    subprocess.run(
        ["objcopy", f"--add-gnu-debuglink={debug}", str(lib)],
        check=True,
        cwd=tmp_path,
    )
    # No debug file: the compiler is unknown, never guessed.
    unknown = dump(lib, []).build_mode
    assert unknown is None or unknown.compiler_family is CompilerFamily.UNKNOWN
    bm = dump(lib, [], debug_info_path=debug).build_mode
    assert bm is not None
    assert bm.compiler_family is CompilerFamily.GCC
    assert bm.provenance.raw_producer and "GNU C" in bm.provenance.raw_producer


# ── 3. Mach-O and PE ──────────────────────────────────────────────────────


def _dwarf4(producer: str, lang: int, addr_size: int) -> tuple[bytes, bytes]:
    """A one-CU DWARF 4 ``(debug_info, debug_abbrev)`` pair, hand-encoded
    from the DWARF 4 spec (DW_TAG_compile_unit 0x11, DW_AT_producer 0x25 /
    DW_FORM_string 0x08, DW_AT_language 0x13 / DW_FORM_data2 0x05)."""
    abbrev = bytes([1, 0x11, 0, 0x25, 0x08, 0x13, 0x05, 0, 0, 0])
    die = bytes([1]) + producer.encode() + b"\0" + struct.pack("<H", lang)
    body = struct.pack("<HIB", 4, 0, addr_size) + die
    return struct.pack("<I", len(body)) + body, abbrev


def _macho(producer: str, lang: int, *, is_64: bool = True) -> bytes:
    info, abbrev = _dwarf4(producer, lang, 8 if is_64 else 4)
    hdr_size, seg_size, sect_size = (32, 72, 80) if is_64 else (28, 56, 68)
    nsects = 3  # __debug_info, __debug_abbrev, plus a non-DWARF __text
    cmdsize = seg_size + sect_size * nsects
    data_off = hdr_size + cmdsize
    blobs = [("__text", "__TEXT", b"\x90"), ("__debug_info", "__DWARF", info)]
    blobs.append(("__debug_abbrev", "__DWARF", abbrev))
    out = io.BytesIO()
    if is_64:
        out.write(
            struct.pack("<IiiIIIII", 0xFEEDFACF, 0x01000007, 3, 1, 1, cmdsize, 0, 0)
        )
        out.write(
            struct.pack(
                "<II16sQQQQiiII", 0x19, cmdsize, b"", 0, 0, 0, 0, 7, 7, nsects, 0
            )
        )
    else:
        out.write(struct.pack("<IiiIIII", 0xFEEDFACE, 7, 3, 1, 1, cmdsize, 0))
        out.write(
            struct.pack(
                "<II16sIIIIiiII", 0x1, cmdsize, b"", 0, 0, 0, 0, 7, 7, nsects, 0
            )
        )
    offset = data_off
    for name, seg, blob in blobs:
        if is_64:
            out.write(
                struct.pack(
                    "<16s16sQQIIIIIIII",
                    name.encode(),
                    seg.encode(),
                    0,
                    len(blob),
                    offset,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
            )
        else:
            out.write(
                struct.pack(
                    "<16s16sIIIIIIIII",
                    name.encode(),
                    seg.encode(),
                    0,
                    len(blob),
                    offset,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
            )
        offset += len(blob)
    for _n, _s, blob in blobs:
        out.write(blob)
    return out.getvalue()


def _fat(thin: bytes, *, fat64: bool = False) -> bytes:
    offset = 4096
    if fat64:
        head = struct.pack(">II", 0xCAFEBABF, 1) + struct.pack(
            ">iiQQII", 7, 3, offset, len(thin), 12, 0
        )
    else:
        head = struct.pack(">II", 0xCAFEBABE, 1) + struct.pack(
            ">iiIII", 7, 3, offset, len(thin), 12
        )
    return head.ljust(offset, b"\0") + thin


_CLANG = "Apple clang version 15.0.0 (clang-1500.3.9.4)"
_LANG_CXX14 = ENUM_DW_LANG["DW_LANG_C_plus_plus_14"]


@pytest.mark.parametrize("shape", ["thin64", "thin32", "fat", "fat64"])
def test_macho_dwarf_producer_from_object(shape: str, tmp_path: Path) -> None:
    thin = _macho(_CLANG, _LANG_CXX14, is_64=shape != "thin32")
    data = _fat(thin, fat64=shape == "fat64") if shape.startswith("fat") else thin
    assert set(macho_dwarf_sections(data)) == {"__debug_info", "__debug_abbrev"}
    obj = tmp_path / "a.o"
    obj.write_bytes(data)
    assert read_macho_producer(obj) == (_CLANG, _LANG_CXX14)


def test_macho_dylib_reads_dsym(tmp_path: Path) -> None:
    dylib = tmp_path / "libx.dylib"
    dylib.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 28)  # header, no load commands
    assert read_macho_producer(dylib) == (None, None)
    member = tmp_path / "libx.dylib.dSYM" / "Contents" / "Resources" / "DWARF"
    member.mkdir(parents=True)
    (member / "libx.dylib").write_bytes(_macho(_CLANG, _LANG_CXX14))
    assert read_macho_producer(dylib) == (_CLANG, _LANG_CXX14)
    explicit = tmp_path / "elsewhere.dwarf"
    explicit.write_bytes(_macho("GNU C++17 13.3.0", _LANG_CXX14))
    assert read_macho_producer(dylib, debug_path=explicit)[0] == "GNU C++17 13.3.0"


@pytest.mark.parametrize(
    "blob", [b"", b"\x7fELF" + b"\0" * 60, b"\xfe\xed\xfa\xcf" + b"\0" * 28]
)
def test_non_macho_or_big_endian_yields_nothing(blob: bytes, tmp_path: Path) -> None:
    assert macho_dwarf_sections(blob) == {}
    path = tmp_path / "x"
    path.write_bytes(blob)
    assert read_macho_producer(path) == (None, None)


def test_corrupt_macho_dwarf_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    obj = tmp_path / "a.o"
    obj.write_bytes(_macho(_CLANG, _LANG_CXX14))

    def _boom(_d: object) -> tuple[str | None, int | None]:
        raise ValueError("bad DWARF")

    from abicheck.extract import dwarf_producer

    monkeypatch.setattr(dwarf_producer, "best_cu_producer", _boom)
    assert read_macho_producer(obj) == (None, None)


def _macho_snap(tmp_path: Path, data: bytes) -> AbiSnapshot:
    obj = tmp_path / "a.o"
    obj.write_bytes(data)
    snap = AbiSnapshot(library="a.o", version="1")
    snap.platform, snap.source_path = "macho", str(obj)
    return snap


def test_capture_build_mode_macho_and_pe(tmp_path: Path) -> None:
    snap = _macho_snap(tmp_path, _macho(_CLANG, _LANG_CXX14))
    bm = capture_build_mode(snap).build_mode
    assert bm is not None and bm.compiler_family is CompilerFamily.CLANG
    assert bm.provenance.raw_producer == _CLANG
    # Shallow: no DWARF read, no mangled symbols -> nothing recorded.
    shallow = _macho_snap(tmp_path, _macho(_CLANG, _LANG_CXX14))
    assert capture_build_mode(shallow, read_dwarf=False).build_mode is None
    # An existing capture is kept.
    kept = _macho_snap(tmp_path, b"")
    kept.build_mode = recorded = BuildMode(compiler_family=CompilerFamily.GCC)
    assert capture_build_mode(kept).build_mode is recorded
    # PE has no reliable compiler signal: unknown, never guessed.
    pe = _macho_snap(tmp_path, b"MZ")
    pe.platform = "pe"
    assert capture_build_mode(pe).build_mode is None
    # capture_elf_build_mode still ignores a non-ELF snapshot.
    assert capture_elf_build_mode(pe).build_mode is None


def test_macho_reader_tolerates_old_dwarfinfo_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A pyelftools DWARFInfo without debug_line_str/str_offsets keywords
    # still gets every section it does accept.
    from elftools.dwarf import dwarfinfo

    captured: dict[str, object] = {}

    class _Old:
        def __init__(
            self,
            config: object,
            debug_info_sec: object,
            debug_abbrev_sec: object,
            debug_str_sec: object,
        ) -> None:
            captured.update(
                info=debug_info_sec, abbrev=debug_abbrev_sec, str=debug_str_sec
            )

    monkeypatch.setattr(dwarfinfo, "DWARFInfo", _Old)
    data = _macho(_CLANG, _LANG_CXX14)
    sections = dict(macho_dwarf_sections(data))
    sections["__debug_line_str"] = sections["__debug_info"]
    monkeypatch.setattr(
        macho_build_signals, "macho_dwarf_sections", lambda _d: sections
    )
    assert isinstance(macho_build_signals._dwarf_info(data), _Old)
    assert captured["info"] is not None and captured["str"] is None


@pytest.mark.integration
def test_macho_object_real_clang(tmp_path: Path) -> None:
    if shutil.which("clang") is None:
        pytest.skip("clang not available")
    src = tmp_path / "a.cpp"
    src.write_text("int f(int x) { return x + 1; }\n")
    obj = tmp_path / "a.o"
    try:
        subprocess.run(
            [
                "clang",
                "-g",
                "-target",
                "x86_64-apple-macos11",
                "-c",
                str(src),
                "-o",
                str(obj),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        pytest.skip("clang cannot target Mach-O here")
    banner = subprocess.run(
        ["clang", "--version"], capture_output=True, text=True, check=True
    ).stdout.splitlines()[0]
    producer, lang = read_macho_producer(obj)
    assert producer == banner.strip()
    assert lang is not None


# ── pure-Python branch coverage (no host binary, no external tools) ──────


class _Attr:
    def __init__(self, value: object) -> None:
        self.value = value


class _CU:
    def __init__(self, attrs: dict[str, object] | None) -> None:
        self._attrs = attrs

    def get_top_DIE(self) -> object:
        if self._attrs is None:
            raise ValueError("truncated CU")
        attrs = {k: _Attr(v) for k, v in self._attrs.items()}
        return type("_Die", (), {"attributes": attrs})()


class _Dwarf:
    def __init__(self, *cus: dict[str, object] | None) -> None:
        self._cus = [_CU(a) for a in cus]

    def iter_CUs(self) -> list[_CU]:
        return self._cus


_GCC = "GNU C++17 13.3.0 -g"
_DW_C99 = ENUM_DW_LANG["DW_LANG_C99"]


@pytest.mark.parametrize(
    ("cus", "expected"),
    [
        ((), (None, None)),
        ((None, {}), (None, None)),  # broken CU, attribute-less CU
        (({"DW_AT_language": "x"},), (None, None)),  # non-int language
        (({"DW_AT_producer": b"  "},), (None, None)),  # blank producer
        (({"DW_AT_producer": 7},), (None, None)),  # non-string producer
        # rank 5 (unknown, no lang) loses to rank 3 (unknown C) ...
        (
            ({"DW_AT_producer": "asm"}, {"DW_AT_language": _DW_C99}),
            (None, _DW_C99),
        ),
        # ... which loses to rank 1 (known C) ...
        (
            (
                {"DW_AT_language": _DW_C99},
                {"DW_AT_producer": b"GNU C99 13.3.0", "DW_AT_language": _DW_C99},
            ),
            ("GNU C99 13.3.0", _DW_C99),
        ),
        # ... and rank 2 (unknown C++) loses to rank 0 (known C++), which stops
        # the walk: the trailing broken CU is never read.
        (
            (
                {"DW_AT_language": _LANG_CXX14},
                {"DW_AT_producer": _GCC, "DW_AT_language": _LANG_CXX14},
                None,
            ),
            (_GCC, _LANG_CXX14),
        ),
        # rank 4 (known, no language) is kept over nothing.
        (({"DW_AT_producer": _GCC},), (_GCC, None)),
    ],
)
def test_best_cu_producer_ranking(
    cus: tuple[dict[str, object] | None, ...],
    expected: tuple[str | None, int | None],
) -> None:
    from abicheck.extract.dwarf_producer import best_cu_producer

    assert best_cu_producer(_Dwarf(*cus)) == expected


def test_read_producer_needs_real_dwarf(monkeypatch: pytest.MonkeyPatch) -> None:
    from abicheck import dwarf_utils

    elf = type(
        "_E", (), {"get_dwarf_info": lambda self: _Dwarf({"DW_AT_producer": _GCC})}
    )()
    monkeypatch.setattr(dwarf_utils, "has_real_dwarf_info", lambda _e: False)
    assert build_mode_capture._read_producer(elf) == (None, None)
    monkeypatch.setattr(dwarf_utils, "has_real_dwarf_info", lambda _e: True)
    assert build_mode_capture._read_producer(elf) == (_GCC, None)


def test_read_detached_producer_reads_the_debug_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from elftools.elf import elffile

    monkeypatch.setattr(elffile, "ELFFile", _FakeElf)
    monkeypatch.setattr(
        build_mode_capture,
        "_read_producer",
        lambda e: (_GCC, None) if isinstance(e, _FakeElf) else (None, None),
    )
    debug = tmp_path / "libx.debug"
    debug.write_bytes(b"\x7fELF")
    assert build_mode_capture._read_detached_producer(debug) == (_GCC, None)


def test_capture_elf_without_source_path_is_untouched() -> None:
    snap = AbiSnapshot(library="libx.so", version="1")
    snap.platform = "elf"
    assert capture_elf_build_mode(snap).build_mode is None


def test_macho_load_command_walk_edges() -> None:
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x01000007, 3, 1, 3, 24, 0, 0)
    # LC_SYMTAB-like non-segment command, then a malformed (cmdsize 0) one.
    cmds = struct.pack("<II", 0x2, 8) + struct.pack("<II", 0x19, 0) + b"\0" * 8
    assert macho_dwarf_sections(header + cmds) == {}
    # A universal header listing no slices.
    assert macho_dwarf_sections(struct.pack(">II", 0xCAFEBABE, 0) + b"\0" * 24) == {}


def test_macho_candidate_without_cu_attributes_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from abicheck.extract import dwarf_producer

    obj = tmp_path / "a.o"
    obj.write_bytes(_macho(_CLANG, _LANG_CXX14))
    monkeypatch.setattr(dwarf_producer, "best_cu_producer", lambda _d: (None, None))
    assert read_macho_producer(obj) == (None, None)
