# SPDX-License-Identifier: Apache-2.0
"""A same-size VTT whose entry was retargeted, on real x86-64 ELF.

Reproducer from the standard-space follow-up research (PR #2154): D has a
virtual base; one entry of ``_ZTT1D`` (byte offset 16) is moved from the
construction vtable ``_ZTC1D0_1L + 56`` to ``_ZTV1V + 16``. The VTT keeps its
56 bytes, its exports and the class hierarchy. A client built once against
the original library sees the virtual call made during construction return
20 before and 10 after; on the complete object it returns 30 both times.

Controls move the tables without changing any logical target -- an
unrelated object linked first, ``-Bsymbolic`` (``R_X86_64_64`` becomes
``R_X86_64_RELATIVE``) and ``-z pack-relative-relocs`` (``DT_RELR``) -- and
the old client's results must stay the same with no VTT finding.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests._abicheck_subprocess import abicheck_command, abicheck_env

from abicheck.elf_metadata import parse_elf_metadata
from abicheck.model.elf_facts import VttEntryTarget

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux" or platform.machine() != "x86_64",
        reason="Linux x86-64 SysV / Itanium C++ ABI fixture",
    ),
    pytest.mark.skipif(
        not all(shutil.which(t) for t in ("g++", "readelf", "nm", "strip")),
        reason="needs g++ and binutils",
    ),
]

SRC = Path(__file__).parent / "data" / "vtt_entry_target"
VTT = "_ZTT1D"
ENTRY = 2  # byte offset 16
OLD_TARGET = VttEntryTarget("_ZTC1D0_1L", 56)
NEW_TARGET = VttEntryTarget("_ZTV1V", 16)
FLAGS = ["-std=c++11", "-O0", "-g", "-fPIC"]


def _run(cmd: list[str | Path], **kw) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [str(c) for c in cmd], capture_output=True, text=True, check=False, **kw
    )
    assert proc.returncode == 0, f"{cmd}\n{proc.stdout}\n{proc.stderr}"
    return proc


def _retarget_asm(asm: Path) -> None:
    """Retarget one VTT entry in the assembly, checking the fixture first."""
    text = asm.read_text()
    prefix, table = text.split(f"\n{VTT}:\n")
    entries = table.splitlines()
    assert entries[ENTRY].split() == [".quad", "_ZTC1D0_1L+56"], entries[:8]
    vtable_v = text.split("\n_ZTV1V:\n")[1].splitlines()
    # _ZTV1V + 16 is V's address point: its first slot is V::phase.
    assert vtable_v[2].split() == [".quad", "_ZN1V5phaseEv"], vtable_v[:4]
    table = table.replace(entries[ENTRY], "\t.quad\t_ZTV1V+16", 1)
    asm.write_text(prefix + f"\n{VTT}:\n" + table)


def _build(out: Path, variant: str) -> Path:
    out.mkdir()
    lib = out / "libp.so"
    if variant == "vtt-entry":
        asm = out / "provider.s"
        _run(["g++", *FLAGS, "-S", SRC / "provider.cc", "-o", asm])
        _retarget_asm(asm)
        _run(["g++", "-shared", asm, "-o", lib])
        return lib
    sources = [SRC / "provider.cc"]
    extra: list[str] = []
    if variant == "relink":
        sources = [SRC / "pad.cc", *sources]
    elif variant == "bsymbolic":
        extra = ["-Wl,-Bsymbolic"]
    elif variant == "relr":
        extra = ["-Wl,-z,pack-relative-relocs"]
    proc = subprocess.run(
        ["g++", *FLAGS, "-shared", *map(str, sources), *extra, "-o", str(lib)],
        capture_output=True,
        text=True,
        check=False,
    )
    if (
        proc.returncode != 0
        and variant == "relr"
        and "pack-relative-relocs" in proc.stderr
    ):
        pytest.skip("linker does not support -z pack-relative-relocs")
    assert proc.returncode == 0, proc.stderr
    return lib


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("vtt")
    libs = {v: _build(root / v, v) for v in ("old", "vtt-entry", "relink", "bsymbolic")}
    client = root / "client"
    _run(
        [
            "g++",
            "-std=c++11",
            "-O0",
            SRC / "client.cc",
            "-L",
            libs["old"].parent,
            "-lp",
            "-o",
            client,
        ]
    )
    libs["client"] = client
    return libs


def _client(client: Path, lib: Path) -> tuple[int, int]:
    env = {**os.environ, "LD_LIBRARY_PATH": str(lib.parent)}
    # The loader resolves libp.so to exactly the library under test.
    trace = _run([client], env={**env, "LD_TRACE_LOADED_OBJECTS": "1"}).stdout
    assert f"libp.so => {lib}" in trace, trace
    a, b = _run([client], env=env).stdout.strip().split(",")
    return int(a), int(b)


def _nm(lib: Path) -> dict[str, tuple[int, int]]:
    out = _run(["nm", "-S", lib]).stdout
    rows = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4:
            rows[parts[3]] = (int(parts[0], 16), int(parts[1], 16))
    return rows


def _entry_reloc(lib: Path) -> str:
    """readelf's relocation row for the VTT word at byte offset 16 ('' for RELR)."""
    return _reloc_at(lib, _nm(lib)[VTT][0] + 8 * ENTRY)


def _reloc_at(lib: Path, address: int) -> str:
    for line in _run(["readelf", "-Wr", lib]).stdout.splitlines():
        if line.startswith(f"{address:016x} "):
            return " ".join(line.split()[2:])
    return ""


def _compare(old: Path, new: Path, out: Path) -> tuple[int, dict]:
    proc = subprocess.run(
        abicheck_command("compare", old, new, "-o", f"json={out}"),
        env=abicheck_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.exists(), proc.stderr
    return proc.returncode, json.loads(out.read_text())


def _vtt_findings(report: dict) -> list[dict]:
    return [c for c in report["changes"] if c["kind"] == "vtt_entry_target_changed"]


def _vtt_declined(report: dict) -> list:
    """The detector's declines; a detector with no finding and no decline
    is not listed in the report."""
    return next(
        (d["declined"] for d in report["detectors"] if d["name"] == "elf_vtt_entries"),
        [],
    )


def test_retargeted_entry_is_breaking_and_named(built, tmp_path):
    old, new = built["old"], built["vtt-entry"]
    # Fixture preconditions: same 56-byte VTT, one different relocation.
    assert _nm(old)[VTT][1] == _nm(new)[VTT][1] == 56
    assert _entry_reloc(old).startswith("R_X86_64_RELATIVE")
    assert _entry_reloc(new).startswith(
        "R_X86_64_64"
    ) and "_ZTV1V + 10" in _entry_reloc(new)
    assert _client(built["client"], old) == (20, 30)
    assert _client(built["client"], new) == (10, 30)

    code, report = _compare(old, new, tmp_path / "r.json")
    (finding,) = _vtt_findings(report)
    assert finding["symbol"] == VTT
    assert "'D'" in finding["description"]
    assert "entry 2 (byte offset 16)" in finding["description"]
    ev = finding["review_evidence"]
    assert (ev["old_table"], ev["old_address_point_offset"]) == (
        OLD_TARGET.table,
        OLD_TARGET.offset,
    )
    assert (ev["new_table"], ev["new_address_point_offset"]) == (
        NEW_TARGET.table,
        NEW_TARGET.offset,
    )
    # It is the run's only finding, so nothing else can make the verdict.
    assert [c["kind"] for c in report["changes"]] == ["vtt_entry_target_changed"]
    assert report["verdict"] == "BREAKING" and code == 4
    assert report["analysis_assurance"]["status"] == "complete"


@pytest.mark.parametrize("variant", ["relink", "bsymbolic", "relr"])
def test_relink_that_moves_tables_is_not_a_vtt_change(built, tmp_path, variant):
    old = built["old"]
    new = built[variant] if variant in built else _build(tmp_path / variant, variant)
    o_nm, n_nm = _nm(old), _nm(new)
    if variant == "relink":
        # The tables really moved ...
        assert o_nm[VTT][0] != n_nm[VTT][0]
        assert o_nm["_ZTC1D0_1L"][0] != n_nm["_ZTC1D0_1L"][0]
        assert o_nm["_ZTV1D"][0] != n_nm["_ZTV1D"][0]
    # ... while every logical target, and the old client's results, stayed.
    o_entries = parse_elf_metadata(old).vtt_entries[VTT]
    n_entries = parse_elf_metadata(new).vtt_entries[VTT]
    assert o_entries[ENTRY] == OLD_TARGET and None not in o_entries
    assert n_entries == o_entries
    assert _client(built["client"], new) == (20, 30)
    if variant == "bsymbolic":
        # Entry 0 (_ZTV1D + 24) changes form: R_X86_64_64 -> RELATIVE.
        assert "_ZTV1D + 18" in _reloc_at(old, o_nm[VTT][0])
        assert _reloc_at(new, n_nm[VTT][0]).startswith("R_X86_64_RELATIVE")
    if variant == "relr":
        assert _entry_reloc(new) == ""  # the entry is a DT_RELR word now

    _code, report = _compare(old, new, tmp_path / "r.json")
    assert _vtt_findings(report) == []
    assert _vtt_declined(report) == []


def test_stripped_pair_keeps_the_unknown(built, tmp_path):
    libs = {}
    for name in ("old", "vtt-entry"):
        (tmp_path / name).mkdir()
        libs[name] = shutil.copy2(built[name], tmp_path / name / "libp.so")
        _run(["strip", "--strip-unneeded", libs[name]])
    old, new = Path(libs["old"]), Path(libs["vtt-entry"])
    assert _client(built["client"], new) == (10, 30)
    # Facts actually available: the local _ZTC1D0_1L symbol is gone, so the
    # old entry's RELATIVE address has no covering table; _ZTV1V is exported.
    assert "_ZTC1D0_1L" not in _nm_dynamic(old)
    assert parse_elf_metadata(old).vtt_entries[VTT][ENTRY] is None
    assert parse_elf_metadata(new).vtt_entries[VTT][ENTRY] == NEW_TARGET

    code, report = _compare(old, new, tmp_path / "r.json")
    assert _vtt_findings(report) == []
    ((entity, reason),) = [(d["entity"], d["reason"]) for d in _vtt_declined(report)]
    assert entity == VTT and str(ENTRY) in reason
    assert any("elf_vtt_entries" in n for n in report["analysis_assurance"]["notes"])
    assert report["verdict"] == "UNCONFIRMED" and code == 1


def _nm_dynamic(lib: Path) -> str:
    return subprocess.run(["nm", "-D", str(lib)], capture_output=True, text=True).stdout


def _dump(lib: Path, out: Path) -> Path:
    _run(abicheck_command("dump", lib, "-o", out), env=abicheck_env())
    return out


def test_dump_save_load_compare_keeps_the_finding(built, tmp_path):
    old = _dump(built["old"], tmp_path / "old.json")
    new = _dump(built["vtt-entry"], tmp_path / "new.json")
    _code, report = _compare(old, new, tmp_path / "r.json")
    (finding,) = _vtt_findings(report)
    assert finding["review_evidence"]["entry_index"] == ENTRY
    assert finding["review_evidence"]["new_table"] == NEW_TARGET.table


def test_old_snapshot_without_the_fact_is_unknown(built, tmp_path):
    old = _dump(built["old"], tmp_path / "old.json")
    doc = json.loads(old.read_text())
    extensions = doc["sections"]["binary"]["extensions"]
    assert extensions.pop("abicheck.elf_vtt_entries")[VTT][ENTRY] == [
        OLD_TARGET.table,
        OLD_TARGET.offset,
    ]
    old.write_text(json.dumps(doc))
    _code, report = _compare(old, built["vtt-entry"], tmp_path / "r.json")
    assert _vtt_findings(report) == []
    declined = {d["entity"]: d["reason"] for d in _vtt_declined(report)}
    # Every matched VTT is unknown on the old side, not only the changed one.
    assert set(declined) == {VTT, "_ZTT1L", "_ZTT1R"}
    assert all("old side" in r for r in declined.values())
