"""ADR-093 D7 in abiforge: an arch-conditioned kind follows the producer's target CPU.

``kind_observable`` must reject a declared condition whose arch set excludes the
cell's arch. The arch comes from the producer toolchain the cell builds with
(``Toolchain.arch``), not from the host: each arch here is a hand-built Config.
"""

from __future__ import annotations

from abiforge.model import config as cfgmod
from abiforge.model.config import Config, Toolchain
from abiforge.model.expect import kind_observable

KIND = "STRUCT_RETURN_CONVENTION_CHANGED"  # declared ELF x86-64 (DH:ELF_X86_64)


def _cell(arch: str) -> Config:
    return Config(
        std=cfgmod.STANDARD_BY_NAME["c17"],
        producer=Toolchain("gcc", 0, "gcc", "g++", arch=arch),
        evidence="headers",
        platform=("linux", "elf"),
    )


def test_arch_conditioned_kind_is_expected_on_an_x86_64_cell() -> None:
    assert kind_observable(KIND, _cell("x86_64"))


def test_arch_conditioned_kind_is_not_expected_on_an_aarch64_cell() -> None:
    assert not kind_observable(KIND, _cell("aarch64"))


def test_config_arch_is_the_producer_toolchains_not_the_hosts() -> None:
    assert _cell("aarch64").arch == "aarch64"
    assert _cell("x86_64").arch == "x86_64"


def test_default_toolchain_arch_keeps_the_build_id_spelling() -> None:
    # The corpus default stays x86-64, so existing toolchain ids do not change.
    assert Toolchain("gcc", 0, "gcc", "g++").id == "gcc0"
    assert Toolchain("gcc", 0, "gcc", "g++", arch="aarch64").id == "gcc0-aarch64"
