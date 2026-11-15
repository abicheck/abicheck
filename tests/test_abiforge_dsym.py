"""The Mach-O dSYM step of the abiforge harness fails closed.

A DWARF cell on Mach-O claims debug info. If ``dsymutil`` exits 0 but writes no
bundle, the cell must fail the build rather than be checked without DWARF. The
real link runs only on macOS (``test_macho_abiforge_cells.py``); these pin the
assertion and the exact command on every host, scripted with
``StrictProcessRunner`` so an extra, missing or wrong-input call is caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _strict_process import StrictProcessRunner, proc

from abiforge.run import harness


def test_make_dsym_fails_when_dsymutil_writes_no_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    so = tmp_path / "libbase.dylib"
    so.write_bytes(b"")
    dsym = tmp_path / "libbase.dylib.dSYM"
    runner = StrictProcessRunner().expect(
        argv=["dsymutil", str(so), "-o", str(dsym)],
        returns=proc(returncode=0),
        label="dsymutil writes no bundle",
    )
    runner.install(monkeypatch, harness.subprocess, "run")
    with pytest.raises(harness.CompileError, match="wrote no"):
        harness.make_dsym(so)
    runner.assert_exhausted()


def test_make_dsym_returns_the_bundle_dsymutil_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    so = tmp_path / "libbase.dylib"
    so.write_bytes(b"")
    dsym = tmp_path / "libbase.dylib.dSYM"
    dsym.mkdir()
    runner = StrictProcessRunner().expect(
        argv=["dsymutil", str(so), "-o", str(dsym)],
        returns=proc(returncode=0),
        label="dsymutil collects DWARF into the bundle",
    )
    runner.install(monkeypatch, harness.subprocess, "run")
    assert harness.make_dsym(so) == dsym
    runner.assert_exhausted()
