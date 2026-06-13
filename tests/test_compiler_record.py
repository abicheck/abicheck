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

"""Compiler-recorded metadata extractor coverage (ADR-029 D8).

The pure byte/string parsers are tested directly; the ELF wrapper is exercised
with a faked ``ELFFile`` (success path) and real pyelftools (failure paths), so
no compiled fixture is needed on the fast lane.
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from abicheck.buildsource import compiler_record as cr
from abicheck.buildsource.compiler_record import (
    extract_compiler_record,
    parse_gcc_command_line,
    parse_producer,
)
from abicheck.cli import main

# ── pure parsers ─────────────────────────────────────────────────────────────


def test_parse_gcc_command_line_splits_on_nul():
    data = b"gcc -std=c++20 -c a.cpp\x00clang -c b.c\x00\x00"
    assert parse_gcc_command_line(data) == ["gcc -std=c++20 -c a.cpp", "clang -c b.c"]


@pytest.mark.parametrize("producer,cid,ver,lang", [
    ("GNU C++17 13.2.0 -std=c++17 -O2", "GNU", "13.2.0", "CXX"),
    ("GNU C11 12.3.0", "GNU", "12.3.0", "C"),
    ("clang version 17.0.6 (…)", "Clang", "17.0.6", ""),
    ("Intel(R) oneAPI 2024.1", "Intel", "2024.1", ""),
])
def test_parse_producer_variants(producer, cid, ver, lang):
    tc = parse_producer(producer)
    assert tc is not None
    assert (tc.compiler_id, tc.version, tc.language) == (cid, ver, lang)


def test_parse_producer_empty_is_none():
    assert parse_producer("   ") is None


def test_parse_producer_unknown_compiler_uses_first_token():
    tc = parse_producer("weirdcc 5.0 stuff")
    assert tc is not None
    assert tc.compiler_id == "weirdcc"
    assert tc.version == "5.0"


# ── ELF wrapper (faked success path) ─────────────────────────────────────────


class _FakeSection:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def data(self) -> bytes:
        return self._data


class _FakeAttr:
    def __init__(self, value: bytes) -> None:
        self.value = value


class _FakeDIE:
    def __init__(self, producer: bytes) -> None:
        self.attributes = {"DW_AT_producer": _FakeAttr(producer)}


class _FakeCU:
    def __init__(self, die: _FakeDIE) -> None:
        self._die = die

    def get_top_DIE(self) -> _FakeDIE:
        return self._die


class _FakeDwarf:
    def __init__(self, cus: list[_FakeCU]) -> None:
        self._cus = cus

    def iter_CUs(self):
        return iter(self._cus)


class _FakeELF:
    def __init__(self, section: _FakeSection | None, dwarf: _FakeDwarf | None) -> None:
        self._section = section
        self._dwarf = dwarf

    def get_section_by_name(self, name: str):
        return self._section if name == ".GCC.command.line" else None

    def has_dwarf_info(self) -> bool:
        return self._dwarf is not None

    def get_dwarf_info(self) -> _FakeDwarf | None:
        return self._dwarf


def test_extract_compiler_record_success(tmp_path, monkeypatch):
    binpath = tmp_path / "libfoo.so"
    binpath.write_bytes(b"\x7fELF placeholder")
    fake = _FakeELF(
        section=_FakeSection(b"gcc -std=c++20 -D_GLIBCXX_USE_CXX11_ABI=0 -c src/a.cpp\x00"),
        dwarf=_FakeDwarf([_FakeCU(_FakeDIE(b"GNU C++17 13.2.0"))]),
    )
    monkeypatch.setattr(cr, "ELFFile", lambda _fh: fake)
    ev = extract_compiler_record(binpath)

    assert [t.compiler_id for t in ev.toolchains] == ["GNU"]
    assert [(c.source, c.standard) for c in ev.compile_units] == [("src/a.cpp", "c++20")]
    opts = {(o.key, o.value) for o in ev.build_options}
    assert ("std:CXX", "c++20") in opts
    assert ("define:_GLIBCXX_USE_CXX11_ABI", "0") in opts
    assert any("advisory" in d for d in ev.diagnostics)


def test_extract_compiler_record_switches_only_record(tmp_path, monkeypatch):
    # -frecord-gcc-switches records switches with no source token; the ABI
    # options must still be recovered even though no compile unit is emitted.
    binpath = tmp_path / "switches.so"
    binpath.write_bytes(b"\x7fELF")
    section = _FakeSection(b"GNU C11 13.3.0 -std=c11 -D_GLIBCXX_USE_CXX11_ABI=0 -O2\x00")
    monkeypatch.setattr(cr, "ELFFile", lambda _fh: _FakeELF(section=section, dwarf=None))
    ev = extract_compiler_record(binpath)
    assert not ev.compile_units  # no source → no unit
    opts = {(o.key, o.value) for o in ev.build_options}
    # No source token → language unknown, so the std option key is the generic "std".
    assert ("std", "c11") in opts
    assert ("define:_GLIBCXX_USE_CXX11_ABI", "0") in opts


def test_extract_compiler_record_no_section_no_dwarf(tmp_path, monkeypatch):
    binpath = tmp_path / "bare.so"
    binpath.write_bytes(b"\x7fELF")
    monkeypatch.setattr(cr, "ELFFile", lambda _fh: _FakeELF(section=None, dwarf=None))
    ev = extract_compiler_record(binpath)
    assert not ev.toolchains and not ev.compile_units


def test_extract_compiler_record_skips_malformed_and_sourceless_commands(tmp_path, monkeypatch):
    binpath = tmp_path / "x.so"
    binpath.write_bytes(b"\x7fELF")
    # 1st entry: unbalanced quote (shlex error → skipped); 2nd: no source (skipped);
    # 3rd: a real compile that must be kept.
    section = _FakeSection(b'gcc -c "oops\x00gcc -v\x00gcc -std=c17 -c ok.c\x00')
    monkeypatch.setattr(cr, "ELFFile", lambda _fh: _FakeELF(section=section, dwarf=None))
    ev = extract_compiler_record(binpath)
    assert [c.source for c in ev.compile_units] == ["ok.c"]


def test_extract_compiler_record_producer_attr_absent(tmp_path, monkeypatch):
    binpath = tmp_path / "nd.so"
    binpath.write_bytes(b"\x7fELF")

    class _NoProducerDIE:
        attributes: dict = {}

    class _NoProducerCU:
        def get_top_DIE(self):
            return _NoProducerDIE()

    fake = _FakeELF(section=None, dwarf=_FakeDwarf([_NoProducerCU()]))
    monkeypatch.setattr(cr, "ELFFile", lambda _fh: fake)
    ev = extract_compiler_record(binpath)
    assert not ev.toolchains


def test_extract_compiler_record_not_elf(tmp_path):
    p = tmp_path / "plain.txt"
    p.write_text("not an ELF file")
    ev = extract_compiler_record(p)
    assert any("cannot read" in d for d in ev.diagnostics)


def test_extract_compiler_record_missing_file(tmp_path):
    ev = extract_compiler_record(tmp_path / "absent.so")
    assert any("cannot read" in d for d in ev.diagnostics)


# ── CLI wiring ───────────────────────────────────────────────────────────────


def test_collect_evidence_read_compiler_record_requires_binary(tmp_path):
    out = tmp_path / "e"
    result = CliRunner().invoke(main, ["collect", "--read-compiler-record", "-o", str(out)])
    assert result.exit_code != 0
    assert "requires --binary" in result.output


def test_collect_evidence_preserves_option_only_compiler_record(tmp_path, monkeypatch):
    # A stripped ELF whose only provenance is `.GCC.command.line` switches (no
    # source TU, no DWARF producer) yields build_options but no units/toolchains.
    # The pack must still persist that build evidence rather than drop it.
    from abicheck.buildsource.pack import BuildSourcePack

    binpath = tmp_path / "switches.so"
    binpath.write_bytes(b"\x7fELF")
    section = _FakeSection(b"GNU C11 13.3.0 -std=c11 -D_GLIBCXX_USE_CXX11_ABI=0 -O2\x00")
    monkeypatch.setattr(cr, "ELFFile", lambda _fh: _FakeELF(section=section, dwarf=None))

    out = tmp_path / "e"
    result = CliRunner().invoke(
        main, ["collect", "--read-compiler-record", "--binary", str(binpath), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    pack = BuildSourcePack.load(out)
    assert pack.build_evidence is not None  # option-only evidence not dropped
    assert not pack.build_evidence.compile_units and not pack.build_evidence.toolchains
    opts = {(o.key, o.value) for o in pack.build_evidence.build_options}
    assert ("define:_GLIBCXX_USE_CXX11_ABI", "0") in opts
