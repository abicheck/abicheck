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

"""A library whose export table was not read never reads as "exports nothing".

Bug class ``evidence.silent_degradation_to_clean_verdict``
(``tests/regressions/manifest.py``), library side -- the consumer side is
``tests/unit/workflows/test_failed_consumer_read.py``. The platform parsers
swallow their own errors and return empty metadata, so before this fix a
library that could not be parsed, or whose ``.dynsym`` had to be skipped, read
as a library exporting nothing: a NEW library then "lost" every symbol the
consumer needs (a break nobody observed). ``read_library_export_facts`` now
reads such a library as ``FAILED`` and the workflow refuses to scope against
it (``LibraryExportsUnreadableError``).

The oracle is an independent ``pyelftools`` read of the ``SHT_DYNSYM``
section, never abicheck's own parser.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.checker_types import DiffResult
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, parse_elf_metadata
from abicheck.extract.library_export_facts import read_library_export_facts
from abicheck.extract.parse_failures import (
    note_export_read_failure,
    recording_export_read_failures,
)
from abicheck.macho_metadata import MachoExport, MachoMetadata
from abicheck.model import AbiSnapshot
from abicheck.model.availability import FactStatus
from abicheck.model.consumer_requirements import LibraryExportsUnreadableError
from abicheck.model.consumer_spec import ConsumerRequirement, ConsumerSpec
from abicheck.pe_metadata import PeExport, PeMetadata
from abicheck.workflows.consumer_scope import check_against, scope_diff_to_app

_SHT_DYNSYM = "SHT_DYNSYM"


def _elf_library(names: tuple[str, ...] = ("foo", "bar"), *, dynsym_at=None) -> bytes:
    """A minimal ELF64 shared object exporting *names* from ``.dynsym``.

    *dynsym_at* overrides the ``.dynsym`` section's file offset, which leaves
    the ELF header and section table readable but the symbol table not.
    """
    shstr = b"\0.shstrtab\0.dynstr\0.dynsym\0"
    dynstr = b"\0" + b"".join(n.encode() + b"\0" for n in names)
    syms, pos = b"\0" * 24, 1
    for n in names:  # STB_GLOBAL|STT_FUNC, defined in a non-zero section
        syms += struct.pack("<IBBHQQ", pos, 0x12, 0, 4, 0, 0)
        pos += len(n) + 1
    o_shstr = 64
    o_dynstr = o_shstr + len(shstr)
    o_syms = (o_dynstr + len(dynstr) + 7) & ~7
    shoff = (o_syms + len(syms) + 7) & ~7
    header = (
        b"\x7fELF"
        + bytes([2, 1, 1, 0])
        + b"\0" * 8
        + struct.pack("<HHIQQQIHHHHHH", 3, 62, 1, 0, 0, shoff, 0, 64, 0, 0, 64, 4, 1)
    )

    def shdr(name, kind, off, size, link=0, info=0, entsize=0):
        return struct.pack(
            "<IIQQQQIIQQ", name, kind, 0, 0, off, size, link, info, 8, entsize
        )

    body = bytearray(shoff)
    body[:64] = header
    body[o_shstr : o_shstr + len(shstr)] = shstr
    body[o_dynstr : o_dynstr + len(dynstr)] = dynstr
    body[o_syms : o_syms + len(syms)] = syms
    table = (
        shdr(0, 0, 0, 0)
        + shdr(1, 3, o_shstr, len(shstr))
        + shdr(11, 3, o_dynstr, len(dynstr))
        + shdr(19, 11, o_syms if dynsym_at is None else dynsym_at, len(syms), 2, 1, 24)
    )
    return bytes(body) + table


def _oracle_exports(path: Path) -> frozenset[str] | None:
    """Defined, non-local ``.dynsym`` names read by pyelftools, or ``None``
    when that independent read fails. Deliberately a superset of what
    abicheck counts as exported: the property below checks that nothing is
    *invented* or *lost to a failed read*, not abicheck's binding filter."""
    try:
        with open(path, "rb") as f:
            elf = ELFFile(f)
            names: set[str] = set()
            for section in elf.iter_sections():
                if section["sh_type"] != _SHT_DYNSYM:
                    continue
                for sym in section.iter_symbols():
                    if (
                        sym.name
                        and sym["st_shndx"] != "SHN_UNDEF"
                        and sym["st_info"]["bind"] != "STB_LOCAL"
                    ):
                        names.add(sym.name)
            return frozenset(names)
    except Exception:  # noqa: BLE001 - any failure means "not read"
        return None


#: Whole-file failures across all three formats, each failing at a different
#: point of its parser.
_CORRUPT = {
    "elf_truncated_section_table": _elf_library()[:-40],
    "elf_zero_shentsize": _elf_library()[:58] + b"\0\0" + _elf_library()[60:],
    "pe_bad_e_lfanew": b"MZ" + b"\x90" * 200,
    "macho64_bad_load_command": struct.pack("<I", 0xFEEDFACF) + b"\x01" * 200,
    "macho32_truncated_header": struct.pack("<I", 0xFEEDFACE) + b"\x00" * 8,
}


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


class TestRawBinary:
    def test_readable_library_is_present(self, tmp_path):
        path = _write(tmp_path, "libok.so", _elf_library())
        facts = read_library_export_facts(path)
        assert _oracle_exports(path) == {"foo", "bar"}
        assert facts.status is FactStatus.PRESENT
        assert facts.failure_reason is None
        assert facts.export_names == {"foo", "bar"}

    def test_parsed_library_exporting_nothing_is_present(self, tmp_path):
        # A real, empty table is an observation, not a failure.
        path = _write(tmp_path, "libempty.so", _elf_library(names=()))
        assert _oracle_exports(path) == frozenset()
        facts = read_library_export_facts(path)
        assert facts.status is FactStatus.PRESENT
        assert facts.export_names == frozenset()

    def test_skipped_dynsym_is_failed(self, tmp_path):
        path = _write(tmp_path, "libbad.so", _elf_library(dynsym_at=10**6))
        assert _oracle_exports(path) is None
        # The parser itself still returns a header-parsed block: the failure
        # is visible only through the recorded note.
        assert parse_elf_metadata(path).machine
        facts = read_library_export_facts(path)
        assert facts.status is FactStatus.FAILED
        assert ".dynsym" in (facts.failure_reason or "")

    def test_a_memoised_parse_still_reports_its_failure(self, tmp_path):
        path = _write(tmp_path, "libbad.so", _elf_library(dynsym_at=10**6))
        first = read_library_export_facts(path)
        second = read_library_export_facts(path)  # served from the parse memo
        assert first.status is second.status is FactStatus.FAILED
        assert first.failure_reason == second.failure_reason

    @pytest.mark.parametrize("name", sorted(_CORRUPT))
    def test_unparseable_library_is_failed(self, tmp_path, name):
        path = _write(tmp_path, name, _CORRUPT[name])
        facts = read_library_export_facts(path)
        assert facts.binary_format is not None  # a recognised magic number
        assert facts.status is FactStatus.FAILED
        assert facts.failure_reason
        assert facts.label in facts.failure_reason


class TestStoredSnapshot:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"elf": ElfMetadata()},
            {"pe": PeMetadata()},
            {"macho": MachoMetadata()},
        ],
        ids=["elf", "pe", "macho"],
    )
    def test_parse_failed_block_is_failed(self, kwargs):
        snap = AbiSnapshot(library="libfoo", version="1", **kwargs)
        facts = read_library_export_facts(snap)
        assert facts.status is FactStatus.FAILED
        assert facts.failure_reason == "libfoo: export table was not read"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"elf": ElfMetadata(machine="EM_X86_64")},
            {"pe": PeMetadata(machine="AMD64")},
            {"macho": MachoMetadata(cpu_type="ARM64")},
            {"elf": ElfMetadata(symbols=[ElfSymbol(name="foo")])},
            {"pe": PeMetadata(exports=[PeExport(name="foo", ordinal=1)])},
            {"macho": MachoMetadata(exports=[MachoExport(name="foo")])},
        ],
        ids=["elf-empty", "pe-empty", "macho-empty", "elf", "pe", "macho"],
    )
    def test_read_block_is_present(self, kwargs):
        snap = AbiSnapshot(library="libfoo", version="1", **kwargs)
        assert read_library_export_facts(snap).status is FactStatus.PRESENT


class TestRecorder:
    def test_a_note_outside_a_recording_is_dropped(self):
        note_export_read_failure("nobody listens")  # must not raise
        with recording_export_read_failures() as failures:
            pass
        assert failures == []

    def test_recordings_nest_without_leaking(self):
        with recording_export_read_failures() as outer:
            note_export_read_failure("a")
            with recording_export_read_failures() as inner:
                note_export_read_failure("b")
            note_export_read_failure("c")
        assert outer == ["a", "c"]
        assert inner == ["b"]


def _diff() -> DiffResult:
    return DiffResult(old_version="1", new_version="2", library="libfoo.so.1")


def _snap(version: str, names: list[str] | None) -> AbiSnapshot:
    elf = (
        ElfMetadata()
        if names is None
        else ElfMetadata(
            soname="libfoo.so.1", symbols=[ElfSymbol(name=n) for n in names]
        )
    )
    return AbiSnapshot(library="libfoo.so.1", version=version, elf=elf)


class TestWorkflowRefusesAnUnreadLibrary:
    """``_MINIMAL_ELF`` stand-in: any recognised consumer works -- the library
    check runs before its requirements are evaluated."""

    @pytest.fixture
    def consumer(self, tmp_path) -> Path:
        return _write(tmp_path, "app", _elf_library(names=()))

    @pytest.mark.parametrize(
        ("old", "new"),
        [(None, ["foo"]), (["foo"], None), (None, None)],
        ids=["old", "new", "both"],
    )
    @pytest.mark.parametrize("requirement", list(ConsumerRequirement))
    def test_scope_diff_to_app_raises(self, consumer, old, new, requirement):
        spec = ConsumerSpec(path=consumer, requirement=requirement)
        with pytest.raises(LibraryExportsUnreadableError):
            scope_diff_to_app(_diff(), spec, _snap("1", old), _snap("2", new))

    def test_readable_libraries_still_scope(self, consumer):
        result = scope_diff_to_app(
            _diff(), consumer, _snap("1", ["foo"]), _snap("2", ["foo"])
        )
        assert result.unreadable is False

    def test_check_against_raises(self, consumer, tmp_path):
        lib = _write(tmp_path, "libbad.so", _elf_library(dynsym_at=10**6))
        with pytest.raises(LibraryExportsUnreadableError, match=r"\.dynsym"):
            check_against(consumer, lib)

    def test_error_is_a_value_error(self):
        # Existing `except ValueError` callers keep working.
        assert issubclass(LibraryExportsUnreadableError, ValueError)


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    offset=st.integers(min_value=0, max_value=len(_elf_library()) - 1),
    value=st.integers(min_value=0, max_value=255),
)
def test_a_present_fact_is_a_table_that_was_really_read(tmp_path, offset, value):
    """For any single-byte corruption of a valid library: a ``PRESENT`` fact
    only ever reports a table the independent oracle could read too, and
    never claims an export the oracle did not see. Whatever the parser had to
    skip comes back ``FAILED``, never as a smaller (or empty) export set."""
    data = bytearray(_elf_library())
    data[offset] = value
    path = _write(tmp_path, "libmut.so", bytes(data))
    facts = read_library_export_facts(path)
    truth = _oracle_exports(path)
    if facts.status is FactStatus.PRESENT:
        assert truth is not None
        assert facts.export_names <= truth
    elif truth is not None and facts.binary_format == "elf":
        # FAILED while the oracle read the table: the parser recorded a real
        # failure elsewhere (another export-bearing section) -- never silence.
        assert facts.failure_reason


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("gcc") is None or not sys.platform.startswith("linux"),
    reason="needs gcc producing ELF shared objects",
)
def test_cli_refuses_a_stored_library_whose_table_was_not_read(tmp_path):
    """End to end through ``compare --used-by``: a stored NEW snapshot whose
    ELF block records no parse used to report every required symbol as
    missing; it is now refused with one line and exit 1."""
    import json

    from click.testing import CliRunner

    from abicheck.cli import main

    def cc(*args):
        subprocess.run(["gcc", *args], check=True, cwd=tmp_path)

    (tmp_path / "v1.c").write_text(
        "int foo(void){return 1;}\nint bar(void){return 2;}\n"
    )
    (tmp_path / "v2.c").write_text("int foo(void){return 1;}\n")
    (tmp_path / "app.c").write_text(
        "int foo(void);\nint bar(void);\nint main(void){return foo()+bar();}\n"
    )
    cc("-shared", "-fPIC", "-Wl,-soname,libx.so.1", "-o", "old.so", "v1.c")
    cc("-shared", "-fPIC", "-Wl,-soname,libx.so.1", "-o", "new.so", "v2.c")
    cc("-o", "app", "app.c", "./old.so")
    runner = CliRunner()
    for side in ("old", "new"):
        dumped = runner.invoke(
            main,
            [
                "dump",
                str(tmp_path / f"{side}.so"),
                "-o",
                str(tmp_path / f"{side}.json"),
            ],
        )
        assert dumped.exit_code == 0, dumped.output
    doc = json.loads((tmp_path / "new.json").read_text())
    elf = doc["sections"]["binary"]["payload"]["elf"]
    elf["machine"], elf["symbols"] = "", []  # a parse-failed default block
    (tmp_path / "new_unread.json").write_text(json.dumps(doc))

    def run(new):
        return runner.invoke(
            main,
            ["compare", str(tmp_path / "old.json"), str(tmp_path / new),
             "--used-by", str(tmp_path / "app")],
        )  # fmt: skip

    good = run("new.json")
    assert good.exit_code == 4, good.output  # bar really was removed
    assert "missing 1 symbol" in good.output
    bad = run("new_unread.json")
    assert bad.exit_code == 1, bad.output
    assert "--used-by library:" in bad.output
    assert "export table was not read" in bad.output
    assert "Traceback" not in bad.output
