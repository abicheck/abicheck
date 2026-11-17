"""abiforge's producer-capability table, checked against the compilers' own DWARF.

The oracle is ``readelf --debug-dump=info`` on objects the producer compiled,
not abicheck's reader: for each non-default x86 convention, does the compiler
write ``DW_AT_calling_convention`` on the function's own DIE? The table must say
the same, per producer family, and a minimum-evidence derivation must follow it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from abiforge.model.config import default_config
from abiforge.model.observable import minimal_evidence_sets
from abiforge.model.producer_capabilities import (
    DW_AT_CALLING_CONVENTION_NON_DEFAULT,
    producer_writes,
)
from abiforge.model.seeds import SEEDS
from abiforge.operators import OPERATORS
from abiforge.run.toolchains import toolchain
from tests._elf_capability import elf_capability, skip_reason, subprogram_conventions

pytestmark = pytest.mark.integration


#: One function per convention, each named after it. ``sysv_abi`` is the x86-64
#: default, so a producer writes nothing for it; the others are the non-default
#: conventions a DWARF witness could carry.
_CONVENTIONS = {
    "ms_abi": "__attribute__((ms_abi))",
    "sysv_abi": "__attribute__((sysv_abi))",
    "regcall": "__attribute__((regcall))",
    "vectorcall": "__attribute__((vectorcall))",
}
_DIE_START = re.compile(r"^\s*<\d+><[0-9a-f]+>: Abbrev Number: \d+ \((DW_TAG_\w+)\)")
_NON_DEFAULT = ("ms_abi", "regcall", "vectorcall")


def _source() -> str:
    return "\n".join(
        f"{attr} int cc_{name}(int x) {{ return x + 1; }}"
        for name, attr in _CONVENTIONS.items()
    )


def _cc_written_by(prefix: list[str], family: str, tmp: Path) -> dict[str, bool]:
    """For each convention name, whether the compiler's DIE carries the attribute.

    The compiler's warnings (gcc ignores ``regcall``/``vectorcall`` on x86-64
    Linux) are part of the answer, not an error: an ignored attribute writes
    nothing, which is what the oracle then reports.
    """
    src = tmp / f"{family}_cc.c"
    obj = tmp / f"{family}_cc.o"
    src.write_text(_source() + "\n", encoding="utf-8")
    subprocess.run(
        [*prefix, "-g", "-O0", "-c", str(src), "-o", str(obj)],
        check=True,
        capture_output=True,
        text=True,
    )
    by_name = dict(subprogram_conventions(obj))
    return {name: by_name.get(f"cc_{name}") is not None for name in _CONVENTIONS}


def _producers() -> list[tuple[str, str]]:
    found = []
    for family, exe in (("gcc", "gcc"), ("clang", "clang")):
        path = shutil.which(exe)
        if path is not None:
            found.append((family, path))
    return found


@pytest.mark.parametrize("family", ["gcc", "clang"])
def test_capability_row_matches_the_compiler(family: str, tmp_path: Path) -> None:
    prefix = elf_capability(family, "object")
    if prefix is None:
        pytest.skip(skip_reason(family, "object"))
    written = _cc_written_by(prefix, family, tmp_path)
    oracle_writes_non_default = any(written[name] for name in _NON_DEFAULT)
    assert (
        producer_writes(
            DW_AT_CALLING_CONVENTION_NON_DEFAULT, SimpleNamespace(family=family)
        )
        is oracle_writes_non_default
    ), written
    # sysv_abi is the default on x86-64: no producer records it.
    assert written["sysv_abi"] is False, written


@pytest.mark.parametrize(
    ("family", "expected_sets"),
    [
        ("gcc", {frozenset({"B", "H"})}),
        ("clang", {frozenset({"B", "D"}), frozenset({"B", "H"})}),
    ],
)
def test_minimal_evidence_follows_the_producer(family: str, expected_sets: set) -> None:
    """The ms_abi edit's floor: {B,D} only where the producer writes the convention."""
    op = next(o for o in OPERATORS if o.name == "func_became_ms_abi")
    seed = next(s for s in SEEDS if s.name == "c_elf")
    producer = toolchain(family, 13 if family == "gcc" else 18)
    base = default_config(seed.lang, producer)
    assert set(minimal_evidence_sets(op, base)) == expected_sets


def test_llvm_convention_codes_name_what_clang_writes(tmp_path: Path) -> None:
    """Each code abicheck names for a clang x86-64 convention is the one clang writes.

    Oracle: pyelftools reads the numeric ``DW_AT_calling_convention`` and
    ``llvm-dwarfdump`` prints the symbolic name, both in DIE order, so the two
    lists pair up one to one.
    """
    from abicheck.extract.dwarf_calling_conventions import CC_NAMES as _CC_NAMES

    prefix = elf_capability("clang", "object")
    if prefix is None:
        pytest.skip(skip_reason("clang", "object"))
    if shutil.which("llvm-dwarfdump") is None:
        pytest.skip(
            "llvm-dwarfdump is the symbolic-name oracle and is not on this host"
        )
    src = tmp_path / "llvm_cc.c"
    obj = tmp_path / "llvm_cc.o"
    src.write_text(_source() + "\n", encoding="utf-8")
    subprocess.run(
        [*prefix, "-g", "-O0", "-c", str(src), "-o", str(obj)],
        check=True,
        capture_output=True,
        text=True,
    )
    numbers = [cc for _, cc in subprogram_conventions(obj) if cc is not None]
    names = re.findall(
        r"DW_AT_calling_convention\s+\(DW_CC_(\w+)\)",
        _dump(["llvm-dwarfdump", "--debug-info"], obj),
    )
    assert numbers and len(numbers) == len(names), (numbers, names)
    for code, name in zip(numbers, names, strict=True):
        assert _CC_NAMES[code] == name, (hex(code), name)


def _dump(argv: list[str], obj: Path) -> str:
    return subprocess.run(
        [*argv, str(obj)], check=True, capture_output=True, text=True
    ).stdout


def test_legacy_unknown_convention_codes_compare_equal_to_their_names() -> None:
    """A generation-79 snapshot stores ``unknown(0xc1)`` for a function that is
    ``LLVM_Win64`` now; the diff must not call the unchanged function changed.
    Both operand orders are checked."""
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _diff_calling_conventions

    legacy = AdvancedDwarfMetadata()
    legacy.calling_conventions["f"] = "unknown(0xc1)"
    named = AdvancedDwarfMetadata()
    named.calling_conventions["f"] = "LLVM_Win64"
    for old, new in ((legacy, named), (named, legacy)):
        results, reported = _diff_calling_conventions(old, new)
        assert results == [] and reported == set()
    changed = AdvancedDwarfMetadata()
    changed.calling_conventions["f"] = "LLVM_vectorcall"
    results, _ = _diff_calling_conventions(legacy, changed)
    assert [r[0] for r in results] == ["calling_convention_changed"]
