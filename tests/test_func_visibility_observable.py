"""FUNC_VISIBILITY_CHANGED is observable from the binary on ELF (st_other).

The oracle is the hand table below: ELF carries the visibility bits in the
symbol table, PE and Mach-O declarations are unchanged. On base the ELF binary
row fails, because the declaration had no ELF {B} condition.
"""

from __future__ import annotations

import pytest

from abiforge.model.config import Toolchain, default_config
from abiforge.model.expect import kind_observable

GCC = Toolchain("gcc", 13, "gcc", "g++")

TABLE = {
    # (platform, evidence) -> observable
    (("linux", "elf"), "binary"): True,
    (("linux", "elf"), "headers"): True,
    (("win32", "pe"), "binary"): False,
    (("win32", "pe"), "headers"): True,
    (("darwin", "macho"), "binary"): True,
}


@pytest.mark.parametrize(("platform", "evidence"), sorted(TABLE))
def test_visibility_observability_per_platform(platform, evidence: str) -> None:
    cfg = default_config("c", GCC).with_(evidence=evidence, platform=platform)
    assert (
        kind_observable("FUNC_VISIBILITY_CHANGED", cfg) is TABLE[(platform, evidence)]
    )
