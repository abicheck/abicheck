# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A stripped shared object dumps to the same exports as the intact one.

Bug class: ``export_table_unreadable`` -- a shared object whose section header
table is missing, zeroed or stripped was refused, although the dynamic loader
reads only the dynamic table (``PT_DYNAMIC``) and finds every export there. A
``sstrip``'d library is a normal artifact; refusing it hid a library that
loads fine.

The fixture is a library with two symbol versions and one weak symbol, built
with a real version script. Each stripped variant -- ``e_shoff`` zeroed,
``e_shnum`` zeroed, the section table zeroed, and ``sstrip`` (or, when that
tool is not installed, a truncation after the last ``PT_LOAD`` with the
section fields cleared, which is what ``sstrip`` produces) -- must dump to the
intact build's ELF facts: the same exported names, GNU version names and
bindings, visibility, code hashes, imports and version definitions. The oracle
is the intact build's own dump, and the explicit expected export set, not the
parser.
"""

from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile
from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required"),
    pytest.mark.skipif(sys.platform != "linux", reason="builds and strips an ELF .so"),
]

_SOURCE = """\
int old_fn(int x) { return x + 1; }
int old_var = 7;
int new_fn(int x) { return x * 2; }
__attribute__((weak)) int weak_fn(int x) { return x - 1; }
static int hidden_helper(int x) { return x; }
int use_helper(int x) { return hidden_helper(x); }
"""

_VERSION_SCRIPT = """\
VER_1.0 { global: old_fn; old_var; local: *; };
VER_2.0 { global: new_fn; weak_fn; } VER_1.0;
"""

#: The exported surface the intact fixture must expose: name, binding, version.
_EXPECTED_EXPORTS = {
    ("old_fn", "global", "VER_1.0"),
    ("old_var", "global", "VER_1.0"),
    ("new_fn", "global", "VER_2.0"),
    ("weak_fn", "weak", "VER_2.0"),
}


def _build_intact(tmp_path: Path) -> Path:
    src = tmp_path / "stripdemo.c"
    src.write_text(_SOURCE)
    script = tmp_path / "stripdemo.map"
    script.write_text(_VERSION_SCRIPT)
    so = tmp_path / "libstripdemo.so"
    built = subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-g",
            f"-Wl,--version-script={script}",
            "-o",
            str(so),
            str(src),
        ],
        capture_output=True,
        text=True,
    )
    # gcc is present (the skipif above): a failing build is a failure.
    assert built.returncode == 0, built.stderr
    return so


def _last_pt_load_end(data: bytes) -> int:
    end = 0
    for seg in ELFFile(io.BytesIO(data)).iter_segments(type="PT_LOAD"):
        end = max(end, int(seg["p_offset"]) + int(seg["p_filesz"]))
    return end


def _clear_section_fields(out: bytearray) -> None:
    """What sstrip leaves: no section header table, no section name table."""
    struct.pack_into("<Q", out, 0x28, 0)  # e_shoff
    struct.pack_into("<H", out, 0x3C, 0)  # e_shnum
    struct.pack_into("<H", out, 0x3E, 0)  # e_shstrndx


def _zero_section_table(data: bytes) -> bytes:
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    entsize, count = struct.unpack_from("<HH", data, 0x3A)
    out = bytearray(data)
    out[shoff : shoff + entsize * count] = b"\0" * (entsize * count)
    return bytes(out)


def _e_shoff_zero(data: bytes) -> bytes:
    out = bytearray(data)
    struct.pack_into("<Q", out, 0x28, 0)
    return bytes(out)


def _e_shnum_zero(data: bytes) -> bytes:
    out = bytearray(data)
    struct.pack_into("<H", out, 0x3C, 0)
    return bytes(out)


def _truncated_after_last_load(data: bytes) -> bytes:
    """sstrip's effect, emulated: drop every byte past the last PT_LOAD."""
    out = bytearray(data[: _last_pt_load_end(data)])
    _clear_section_fields(out)
    return bytes(out)


def _sstrip(tmp_path: Path, data: bytes) -> bytes:
    """The real ``sstrip`` tool, on the intact build."""
    src = tmp_path / "sstrip_in.so"
    dst = tmp_path / "sstrip_out.so"
    src.write_bytes(data)
    proc = subprocess.run(
        ["sstrip", "-o", str(dst), str(src)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return dst.read_bytes()


def _stripper(tmp_path: Path) -> Callable[[bytes], bytes]:
    """``sstrip`` when the machine has it, else the truncation that emulates it."""
    if shutil.which("sstrip") is not None:
        return lambda data: _sstrip(tmp_path, data)
    return _truncated_after_last_load


def _variants(tmp_path: Path) -> dict[str, Callable[[bytes], bytes]]:
    return {
        "e_shoff_zero": _e_shoff_zero,
        "e_shnum_zero": _e_shnum_zero,
        "section_table_zeroed": _zero_section_table,
        "stripped": _stripper(tmp_path),
    }


def _dump(binary: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        abicheck_command("dump", str(binary), "-o", str(out)),
        capture_output=True,
        text=True,
        timeout=300,
    )


def _elf_facts(snapshot: Path) -> dict:
    return json.loads(snapshot.read_text())["sections"]["binary"]["payload"]["elf"]


def _export_set(elf: dict) -> set[tuple[str, str, str]]:
    return {
        (sym["name"], sym["binding"], sym["version"])
        for sym in elf["symbols"]
        if sym["visibility"] == "default"
    }


@pytest.fixture
def intact(tmp_path: Path) -> tuple[bytes, dict]:
    """The intact build's bytes and its dump's ELF facts (the oracle)."""
    binary = _build_intact(tmp_path)
    out = tmp_path / "intact.json"
    proc = _dump(binary, out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return binary.read_bytes(), _elf_facts(out)


def test_intact_fixture_exposes_the_expected_exports(
    intact: tuple[bytes, dict],
) -> None:
    """Oracle sanity: two versions, one weak symbol, the hidden helper absent."""
    _data, facts = intact
    assert _export_set(facts) == _EXPECTED_EXPORTS
    assert facts["versions_defined"] == ["VER_1.0", "VER_2.0"]


@pytest.mark.parametrize(
    "variant", ["e_shoff_zero", "e_shnum_zero", "section_table_zeroed", "stripped"]
)
def test_stripped_variant_dumps_the_intact_export_set(
    tmp_path: Path, intact: tuple[bytes, dict], variant: str
) -> None:
    intact_bytes, intact_facts = intact
    data = _variants(tmp_path)[variant](intact_bytes)

    # The variant really has no section header table to fall back on.
    stripped = tmp_path / f"{variant}.so"
    stripped.write_bytes(data)
    assert ELFFile(io.BytesIO(data)).get_section_by_name(".dynsym") is None

    out = tmp_path / f"{variant}.json"
    proc = _dump(stripped, out)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert "Traceback" not in output
    facts = _elf_facts(out)

    assert _export_set(facts) == _EXPECTED_EXPORTS
    assert facts == intact_facts
