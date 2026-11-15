"""The Mach-O dSYM step of the abiforge harness fails closed.

A DWARF cell on Mach-O claims debug info. If ``dsymutil`` exits 0 but writes no
bundle, the cell must fail the build rather than be checked without DWARF. The
real link runs only on macOS (``test_macho_abiforge_cells.py``); this pins the
assertion on every host.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from abiforge.run import harness


def test_make_dsym_fails_when_dsymutil_writes_no_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    so = tmp_path / "libbase.dylib"
    so.write_bytes(b"")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    with pytest.raises(harness.CompileError, match="wrote no"):
        harness.make_dsym(so)
    assert calls[0][0] == "dsymutil"
    assert calls[0][-2:] == ["-o", str(so.with_name("libbase.dylib.dSYM"))]


def test_make_dsym_returns_the_bundle_dsymutil_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    so = tmp_path / "libbase.dylib"
    so.write_bytes(b"")

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        (tmp_path / "libbase.dylib.dSYM").mkdir()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    assert harness.make_dsym(so) == tmp_path / "libbase.dylib.dSYM"
