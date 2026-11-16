"""The binary-fact oracle reads each format with its own tool, and never fails a cell.

``nm`` is replaced by a fake that returns the output a real binutils/llvm nm
prints for the format, so the test runs on Linux. The fake files carry only the
format's magic bytes. Covered: ELF (``nm -D``), Mach-O (``nm -u`` with one
leading underscore stripped), PE and unknown formats (unavailable, no tool run),
and a missing or failing ``nm`` (unavailable). When a fact is unavailable,
``binary_facts`` is empty and the operator's licence keeps its strict ceiling.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import abiforge.run.binfacts as binfacts
from abiforge.model.config import default_config
from abiforge.operators import OPERATORS
from abiforge.run.toolchains import toolchain

ELF = b"\x7fELF" + b"\x02\x01\x01" + b"\x00" * 16
MACHO = b"\xcf\xfa\xed\xfe" + b"\x00" * 16
PE = b"MZ" + b"\x00" * 16
UNKNOWN = b"\x00\x01\x02\x03" + b"\x00" * 16

# GNU nm -P prints the name first: "name type value size" (here "name U").
ELF_WITH_CANARY = "__stack_chk_fail@GLIBC_2.4 U\nputs@GLIBC_2.2.5 U\n"
ELF_WITHOUT_CANARY = "puts@GLIBC_2.2.5 U\n"
# Mach-O C symbols carry one extra leading underscore: _ + __stack_chk_fail.
MACHO_WITH_CANARY = "                 U ___stack_chk_fail\n                 U _puts\n"
MACHO_WITHOUT_CANARY = "                 U _puts\n"


def _lib(tmp: Path, name: str, magic: bytes) -> Path:
    path = tmp / name
    path.write_bytes(magic)
    return path


class FakeNm:
    """Records each call and answers with a fixed output, or fails the way nm does."""

    def __init__(
        self, stdout: str = "", *, exit_status: int = 0, missing: bool = False
    ):
        self.calls: list[list[str]] = []
        self.stdout = stdout
        self.exit_status = exit_status
        self.missing = missing

    def __call__(self, argv, **kwargs):
        # The reader must ask for a checked run, or a failing nm would read as an
        # empty import set instead of "unavailable".
        assert kwargs.get("check") is True, "nm must run with check=True"
        self.calls.append(list(argv))
        if self.missing:
            raise FileNotFoundError("nm")
        if self.exit_status:
            raise subprocess.CalledProcessError(self.exit_status, argv)
        return subprocess.CompletedProcess(argv, 0, stdout=self.stdout, stderr="")


def _patch(monkeypatch, fake: FakeNm) -> None:
    monkeypatch.setattr(binfacts.subprocess, "run", fake)


# --- ELF --------------------------------------------------------------------


def test_elf_reads_the_dynamic_undefined_table(tmp_path, monkeypatch) -> None:
    lib = _lib(tmp_path, "lib.so", ELF)
    fake = FakeNm(ELF_WITH_CANARY)
    _patch(monkeypatch, fake)
    assert binfacts.undefined_symbols(lib) == {"__stack_chk_fail", "puts"}
    assert fake.calls == [["nm", "-D", "--undefined-only", "-P", str(lib)]]


def test_elf_fact_holds_only_when_the_canary_import_is_lost(
    tmp_path, monkeypatch
) -> None:
    old = _lib(tmp_path, "old.so", ELF)
    new = _lib(tmp_path, "new.so", ELF)
    outputs = {str(old): ELF_WITH_CANARY, str(new): ELF_WITHOUT_CANARY}

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=outputs[argv[-1]], stderr="")

    monkeypatch.setattr(binfacts.subprocess, "run", run)
    assert binfacts.binary_facts(old, new) == {"stack_chk_fail_dropped": True}
    assert binfacts.binary_facts(new, old) == {"stack_chk_fail_dropped": False}


# --- Mach-O -----------------------------------------------------------------


def test_macho_reads_undefined_symbols_with_the_leading_underscore_stripped(
    tmp_path, monkeypatch
) -> None:
    lib = _lib(tmp_path, "lib.dylib", MACHO)
    fake = FakeNm(MACHO_WITH_CANARY)
    _patch(monkeypatch, fake)
    assert binfacts.undefined_symbols(lib) == {"__stack_chk_fail", "puts"}
    assert fake.calls == [["nm", "-u", str(lib)]]


def test_macho_fact_matches_the_elf_spelling(tmp_path, monkeypatch) -> None:
    old = _lib(tmp_path, "old.dylib", MACHO)
    new = _lib(tmp_path, "new.dylib", MACHO)
    outputs = {str(old): MACHO_WITH_CANARY, str(new): MACHO_WITHOUT_CANARY}

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=outputs[argv[-1]], stderr="")

    monkeypatch.setattr(binfacts.subprocess, "run", run)
    assert binfacts.binary_facts(old, new) == {"stack_chk_fail_dropped": True}


# --- PE and unknown: no tool, unavailable -----------------------------------


@pytest.mark.parametrize("magic", [PE, UNKNOWN], ids=["pe", "unknown"])
def test_pe_and_unknown_formats_are_unavailable_without_running_nm(
    tmp_path, monkeypatch, magic
) -> None:
    lib = _lib(tmp_path, "lib.bin", magic)
    fake = FakeNm(ELF_WITH_CANARY)
    _patch(monkeypatch, fake)
    assert binfacts.undefined_symbols(lib) is None
    assert binfacts.binary_facts(lib, lib) == {}
    assert fake.calls == []


# --- tool failures: unavailable, never an exception ------------------------


def test_nonzero_nm_exit_is_unavailable_not_an_empty_import_set(
    tmp_path, monkeypatch
) -> None:
    old = _lib(tmp_path, "old.so", ELF)
    new = _lib(tmp_path, "new.so", ELF)
    fake = FakeNm(exit_status=1)
    _patch(monkeypatch, fake)
    assert binfacts.undefined_symbols(old) is None
    assert binfacts.binary_facts(old, new) == {}
    assert fake.calls, "nm must run before its failure is read as unavailable"


def test_missing_nm_is_unavailable(tmp_path, monkeypatch) -> None:
    old = _lib(tmp_path, "old.dylib", MACHO)
    new = _lib(tmp_path, "new.dylib", MACHO)
    fake = FakeNm(missing=True)
    _patch(monkeypatch, fake)
    assert binfacts.binary_facts(old, new) == {}
    assert fake.calls, "the reader must try nm before reading it as missing"


def test_one_unreadable_side_gives_no_fact(tmp_path, monkeypatch) -> None:
    old = _lib(tmp_path, "old.so", ELF)
    new = _lib(tmp_path, "new.bin", PE)
    _patch(monkeypatch, FakeNm(ELF_WITH_CANARY))
    assert binfacts.binary_facts(old, new) == {}


# --- the ceiling: an unavailable oracle keeps the strict one ----------------


def test_unavailable_facts_keep_the_strict_ceiling_on_the_operator(
    tmp_path, monkeypatch
) -> None:
    op = next(o for o in OPERATORS if o.name == "rebuild_optimized")
    config = default_config("c", toolchain("gcc", 13)).with_(
        evidence="headers", stripped=True
    )
    old = _lib(tmp_path, "old.so", ELF)
    new = _lib(tmp_path, "new.so", ELF)
    _patch(monkeypatch, FakeNm(exit_status=1))
    facts = binfacts.binary_facts(old, new)
    assert op.expect(config, facts).verdict_max == "COMPATIBLE"
