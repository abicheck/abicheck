"""A RISK verdict is licensed only by a binary fact the oracle confirms (WS-6 A2.11).

The oracle is ``objdump -d``: a lost canary is a function that stops calling
``__stack_chk_fail``, read from the disassembly, not from the dynamic symbol
table that ``abiforge.run.binfacts`` reads. The shapes differ in optimisation
level and in ``-fstack-protector-strong``; a dead local array keeps a canary
only at ``-O0`` (where gcc keeps the frame), so the lost-canary fact follows
from the flags, and the test checks both directions.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from abiforge.model.config import default_config
from abiforge.model.expect import BinaryLicence, Expectation
from abiforge.operators import OPERATORS
from abiforge.run.binfacts import (
    STACK_CHK_FAIL,
    binary_facts,
    library_format,
    undefined_symbols,
)
from abiforge.run.toolchains import toolchain

pytestmark = pytest.mark.skipif(
    shutil.which("gcc") is None or shutil.which("objdump") is None,
    reason="needs gcc and objdump",
)

# A local array the optimiser deletes at -O1 and above: the frame needs no
# canary there, and keeps one at -O0.
SOURCE = """\
#include <string.h>
int shape(int n) {
    char buf[16];
    memset(buf, 0, sizeof buf);
    return n + 1;
}
int keep(int n) {
    char buf[16];
    memset(buf, n, sizeof buf);
    return buf[n & 15];
}
"""

OPT_LEVELS = ("-O0", "-O1", "-O2", "-O3", "-Os")
PROTECTORS = ("-fstack-protector-strong", "-fno-stack-protector")


def _build(tmp: Path, name: str, opt: str, protector: str) -> Path:
    src = tmp / f"{name}.c"
    src.write_text(SOURCE, encoding="utf-8")
    lib = tmp / f"lib{name}.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", opt, protector, str(src), "-o", str(lib)],
        check=True,
        capture_output=True,
    )
    return lib


def _calls_canary(lib: Path) -> bool:
    """Oracle: does any function's disassembly call the protector's check?

    x86 spells the call ``call``; AArch64 spells it ``bl``.
    """
    text = subprocess.run(
        ["objdump", "-d", str(lib)], check=True, capture_output=True, text=True
    ).stdout
    return bool(re.search(r"\b(?:call|bl)\s+\S+\s+<__stack_chk_fail(?:@plt)?>", text))


def _supported(*libs: Path) -> None:
    """Skip when a built library is not ELF or Mach-O (MinGW builds PE)."""
    for lib in libs:
        if library_format(lib) not in ("elf", "macho"):
            pytest.skip(f"{library_format(lib)} libraries are not read by the oracle")


@pytest.mark.integration
@pytest.mark.parametrize("opt", OPT_LEVELS)
@pytest.mark.parametrize("protector", PROTECTORS)
def test_nm_fact_agrees_with_the_disassembly_oracle(tmp_path, opt, protector) -> None:
    """Each shape's imported-canary bit is the one the disassembly shows."""
    lib = _build(tmp_path, "a", opt, protector)
    _supported(lib)
    assert (STACK_CHK_FAIL in undefined_symbols(lib)) is _calls_canary(lib)


@pytest.mark.integration
@pytest.mark.parametrize("opt", OPT_LEVELS)
@pytest.mark.parametrize("protector", PROTECTORS)
def test_dropped_fact_follows_from_the_flags(tmp_path, opt, protector) -> None:
    """A -O0 build keeps the canary of a protected frame; an optimised one may lose it.

    The rebuild pair is -O0 (old) against this shape (new), both with the same
    protector. The fact holds exactly when the protector is on and the
    optimiser removed the canary-checking frame.
    """
    old = _build(tmp_path, "old", "-O0", protector)
    new = _build(tmp_path, "new", opt, protector)
    _supported(old, new)
    fact = binary_facts(old, new)["stack_chk_fail_dropped"]
    if protector == "-fno-stack-protector":
        assert fact is False
    elif opt == "-O0":
        assert fact is False  # same frame on both sides: nothing dropped
    else:
        assert fact is (_calls_canary(old) and not _calls_canary(new))


def test_licence_raises_the_ceiling_only_when_confirmed() -> None:
    lic = BinaryLicence("stack_chk_fail_dropped", "COMPATIBLE_WITH_RISK")
    strict = Expectation(verdict_max="COMPATIBLE")
    assert lic.apply(strict, {"stack_chk_fail_dropped": True}).verdict_max == (
        "COMPATIBLE_WITH_RISK"
    )
    # Not confirmed: the strict ceiling stands, so a RISK here is a false positive.
    assert lic.apply(strict, {"stack_chk_fail_dropped": False}) is strict
    assert lic.apply(strict, {}) is strict


def test_licence_never_lowers_a_wider_ceiling() -> None:
    lic = BinaryLicence("stack_chk_fail_dropped", "COMPATIBLE_WITH_RISK")
    wider = Expectation(verdict_max="API_BREAK")
    assert lic.apply(wider, {"stack_chk_fail_dropped": True}).verdict_max == "API_BREAK"


def test_rebuild_optimized_expects_risk_only_with_the_confirmed_fact() -> None:
    op = next(o for o in OPERATORS if o.name == "rebuild_optimized")
    base = default_config("c", toolchain("gcc", 13))
    stripped = base.with_(evidence="headers", stripped=True)
    assert op.expect(stripped, {}).verdict_max == "COMPATIBLE"
    confirmed = op.expect(stripped, {"stack_chk_fail_dropped": True})
    assert confirmed.verdict_max == "COMPATIBLE_WITH_RISK"
    assert confirmed.observable is True
