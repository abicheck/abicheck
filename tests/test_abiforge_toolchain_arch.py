"""abiforge toolchain discovery reads the target arch from ``cc -dumpmachine``.

``Toolchain.arch`` must come from the driver's own target triple, not from the
default: a discovered aarch64 toolchain has to carry ``aarch64`` so that an
arch-conditioned kind is not expected on its cell (ADR-093 D7).
"""

from __future__ import annotations

import shutil
import subprocess

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


def test_machine_arch_reads_the_drivers_target_not_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: object = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "aarch64-linux-gnu\n", "")

    monkeypatch.setattr(toolchains, "_run", fake_run)
    assert toolchains._machine_arch("/opt/cross/aarch64-gcc") == "aarch64"
    assert calls == [["/opt/cross/aarch64-gcc", "-dumpmachine"]]


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs a host gcc")
def test_real_gcc_arch_matches_its_own_dumpmachine_triple() -> None:
    gcc = shutil.which("gcc")
    assert gcc is not None
    triple = subprocess.run(
        [gcc, "-dumpmachine"], capture_output=True, text=True, check=True
    ).stdout
    assert toolchains._machine_arch(gcc) == toolchains.arch_of_triple(triple)


def test_discovery_drops_a_pair_whose_drivers_target_different_cpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from abiforge.run import toolchains as tcs

    paths = {"gcc": "/x/gcc", "g++": "/x/gxx"}
    arch = {"/x/gcc": "x86_64", "/x/gxx": "aarch64"}
    monkeypatch.setattr(tcs.shutil, "which", lambda name: paths.get(name))
    monkeypatch.setattr(tcs.os.path, "realpath", lambda p: p)
    monkeypatch.setattr(tcs, "_identify", lambda path: ("gcc", 13))
    monkeypatch.setattr(tcs, "_machine_arch", lambda path: arch.get(path))
    monkeypatch.setattr(tcs.sys, "platform", "linux")
    tcs.discover.cache_clear()
    try:
        assert tcs.discover() == ()
    finally:
        tcs.discover.cache_clear()
