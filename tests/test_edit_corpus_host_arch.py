"""The host CPU is a run-time capability of a corpus cell, never a config fact.

Ten operators emit x86 asm or pass x86 ``-m`` switches, and the corpus builds
natively, so they need an x86 host. That need is ``EditOp.host_arch``, read
by ``capability_gap`` when the cell runs: on another CPU the cell is
*unavailable* (a host gap, counted as such), not *not-applicable* (missing
from the cell list). ``requires`` stays pure, so the cell list, and every
check built from it, is the same on every host.
"""

from __future__ import annotations

import platform
from typing import Any, cast

import pytest

from tests import _edit_corpus_cells as cells
from tests._edit_corpus_config import Toolchain, default_config
from tests._edit_corpus_operators import OP_BY_NAME, OPERATORS
from tests._edit_corpus_seeds import SEEDS

GCC = Toolchain("gcc", 13, "gcc", "g++")
LINUX_ELF = ("linux", "elf")

# Written out from what each edit emits (x86 asm, ``-m``/``-z x86-64-v3``
# switches, the SysV x86-64 calling convention), not read from the operators.
X86_ONLY = {
    "text_relocation_introduced",
    "text_relocation_removed",
    "x86_isa_level_raised",
    "target_isa_raised",
    "vector_math_abi_changed",
    "glibc_older_version_required",
    "returned_struct_grew_past_registers",
    "long_double_made_64bit",
    "integer_typedef_widened",
    "by_value_struct_became_nontrivial",
}
X86_MACHINES = ["x86_64", "AMD64", "i686"]
OTHER_MACHINES = ["aarch64", "arm64", "ppc64le", "riscv64"]


def _cells(op_name: str) -> list[tuple[Any, Any, Any]]:
    op = OP_BY_NAME[op_name]
    return [
        (op, s, default_config(s.lang, GCC).with_(platform=LINUX_ELF))
        for s in SEEDS
        if op.applies_to(s)
    ]


def test_the_operators_that_declare_a_host_cpu_are_the_x86_only_edits() -> None:
    assert {op.name for op in OPERATORS if op.host_arch} == X86_ONLY
    for name in X86_ONLY:
        assert _cells(name), f"{name} applies to no seed"


@pytest.mark.parametrize("machine", X86_MACHINES + OTHER_MACHINES)
def test_requires_never_reads_the_host_cpu(
    monkeypatch: pytest.MonkeyPatch, machine: str
) -> None:
    """Every operator on every seed: the same answer whatever the CPU."""
    pairs = [
        (op, default_config(s.lang, GCC).with_(platform=LINUX_ELF))
        for op in OPERATORS
        for s in SEEDS
        if op.applies_to(s)
    ]
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    on_x86 = [op.requires(cfg) for op, cfg in pairs]
    monkeypatch.setattr(platform, "machine", lambda: machine)
    assert [op.requires(cfg) for op, cfg in pairs] == on_x86


@pytest.mark.parametrize("machine", OTHER_MACHINES)
@pytest.mark.parametrize("op_name", sorted(X86_ONLY))
def test_a_non_x86_host_reads_an_x86_only_cell_as_unavailable_not_missing(
    monkeypatch: pytest.MonkeyPatch, op_name: str, machine: str
) -> None:
    # Clear the gates that do not concern the CPU (toolchain installed, ELF
    # linker), so the status below can come only from the host-CPU check.
    monkeypatch.setattr(cells, "unavailable", lambda config: None)
    monkeypatch.setattr(cells, "elf_link_unavailable", lambda seed, config: None)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    for op, seed, cfg in _cells(op_name):
        assert op.requires(cfg) is None, "the cell left the cell list"
        result = cells.run_cell(op, seed, cfg, cast(cells.CellContext, None))
        assert result.status == "unavailable", (seed.name, result.status)
        assert machine in result.reason, result.reason


@pytest.mark.parametrize("machine", X86_MACHINES)
@pytest.mark.parametrize("op_name", sorted(X86_ONLY))
def test_an_x86_host_has_no_cpu_gap(
    monkeypatch: pytest.MonkeyPatch, op_name: str, machine: str
) -> None:
    """The control: on x86 the CPU check passes, whatever the spelling of
    ``platform.machine()`` (Linux ``x86_64``, Windows ``AMD64``, 32-bit)."""
    from tests._edit_corpus_toolchains import capability_gap

    monkeypatch.setattr(platform, "machine", lambda: machine)
    # Stop at the CPU check: the op's later probes run a compiler.
    op = OP_BY_NAME[op_name]
    bare = type(
        "CpuOnly", (), {"host_arch": op.host_arch}
    )()  # no probe_flags, features, rebuild
    for _, _, cfg in _cells(op_name):
        assert capability_gap(bare, cfg) is None
