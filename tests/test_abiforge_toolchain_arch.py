"""abiforge toolchain discovery reads the target arch from ``cc -dumpmachine``.

``Toolchain.arch`` must come from the driver's own target triple, not from the
default: a discovered aarch64 toolchain has to carry ``aarch64`` so that an
arch-conditioned kind is not expected on its cell (ADR-093 D7).
"""

from __future__ import annotations

import shutil

import pytest

from abiforge.model.config import Toolchain
from abiforge.run import toolchains


@pytest.mark.parametrize(
    ("triple", "arch"),
    [
        ("x86_64-linux-gnu", "x86_64"),
        ("aarch64-linux-gnu", "aarch64"),
        ("arm64-apple-darwin23", "aarch64"),
        ("amd64-pc-windows-msvc", "x86_64"),
        ("  aarch64-linux-gnu\n", "aarch64"),
    ],
)
def test_triple_maps_to_the_target_cpu(triple: str, arch: str) -> None:
    assert toolchains.arch_of_triple(triple) == arch


def test_discovered_non_x86_64_toolchain_carries_its_arch_in_its_id() -> None:
    tc = Toolchain(
        "gcc",
        13,
        "aarch64-linux-gnu-gcc-13",
        "aarch64-linux-gnu-g++-13",
        arch=toolchains.arch_of_triple("aarch64-linux-gnu"),
    )
    assert tc.arch == "aarch64"
    assert tc.id == "gcc13-aarch64"


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs a host gcc")
def test_host_gcc_arch_is_read_from_its_own_triple() -> None:
    import platform

    expected = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }[platform.machine().lower()]
    assert toolchains._machine_arch(shutil.which("gcc")) == expected
