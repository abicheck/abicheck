# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""End-to-end stack-check across a two-sysroot pair (G3).

The unit tests in ``test_stack_checker.py`` drive ``check_stack`` with
synthetic dependency graphs. This file builds a real two-sysroot fixture
— a root DSO whose only dependency (``libdep.so.1``) drops a symbol the
root imports between the baseline and candidate sysroots — and asserts
the stack-level verdict end-to-end.

The root is built ``-nostdlib`` so its sole ``DT_NEEDED`` is the in-tree
``libdep``; that keeps the sysroots self-contained (no libc to vendor)
so the clean control comparison resolves to PASS. Stock ``cc`` only (no
castxml); self-skips when ``cc`` is absent. GNU ld ``-Wl,-soname`` and
the ELF dependency walk make this Linux-only.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.stack_checker import StackVerdict, check_stack

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="stack-check sysroot fixture uses GNU ld + ELF DT_NEEDED (Linux only)",
)

ROOT_REL = Path("usr/lib/libapp.so.1")
DEP_V1 = "int dep_func(int x){return x+1;}\nint dep_other(int x){return x+2;}\n"
DEP_V2 = "int dep_other(int x){return x+2;}\n"  # dep_func removed
APP_SRC = "extern int dep_func(int);\nint app_entry(int x){return dep_func(x);}\n"


def _cc() -> str:
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("cc unavailable; cannot build stack-check sysroot fixture")
    return cc


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        pytest.fail(f"command failed: {' '.join(cmd)}\n{res.stderr}")


def _build_sysroots(tmp_path: Path) -> tuple[Path, Path]:
    """Build a baseline and candidate sysroot.

    Both contain ``usr/lib/libapp.so.1`` (identical) and
    ``usr/lib/libdep.so.1``; the candidate's libdep drops ``dep_func``.
    """
    cc = _cc()
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    for root, dep_src in ((baseline, DEP_V1), (candidate, DEP_V2)):
        libdir = root / "usr" / "lib"
        libdir.mkdir(parents=True)
        dep_c = tmp_path / f"dep_{root.name}.c"
        dep_c.write_text(dep_src, encoding="utf-8")
        _run([cc, "-shared", "-fPIC", "-g", "-Wl,-soname,libdep.so.1",
              str(dep_c), "-o", str(libdir / "libdep.so.1")])

    app_c = tmp_path / "app.c"
    app_c.write_text(APP_SRC, encoding="utf-8")
    app_so = baseline / "usr" / "lib" / "libapp.so.1"
    _run([cc, "-shared", "-fPIC", "-nostdlib", "-g", str(app_c),
          str(baseline / "usr" / "lib" / "libdep.so.1"),
          "-o", str(app_so), "-Wl,-soname,libapp.so.1", "-Wl,--no-as-needed"])
    shutil.copy(app_so, candidate / "usr" / "lib" / "libapp.so.1")
    return baseline, candidate


def test_stack_check_flags_removed_dependency_symbol(tmp_path: Path) -> None:
    baseline, candidate = _build_sysroots(tmp_path)
    result = check_stack(ROOT_REL, baseline, candidate)

    # The root imports dep_func, which the candidate's libdep no longer
    # provides → the stack cannot load.
    assert result.loadability == StackVerdict.FAIL
    assert "dep_func" in {b.symbol for b in result.missing_symbols}
    assert result.risk_score == "high"
    changed = {(c.library, c.change_type) for c in result.stack_changes}
    assert ("libdep.so.1", "content_changed") in changed


def test_stack_check_clean_when_sysroots_match(tmp_path: Path) -> None:
    baseline, _ = _build_sysroots(tmp_path)
    # Comparing the baseline against itself: nothing is missing, no change.
    result = check_stack(ROOT_REL, baseline, baseline)
    assert result.loadability == StackVerdict.PASS
    assert result.abi_risk == StackVerdict.PASS
    assert result.risk_score == "low"
    assert not result.missing_symbols


def test_stack_check_cli_reports_fail(tmp_path: Path) -> None:
    # Exercise the cli_stack.py surface, not just the library entry point.
    from click.testing import CliRunner

    from abicheck.cli import main

    baseline, candidate = _build_sysroots(tmp_path)
    result = CliRunner().invoke(
        main,
        ["stack-check", str(ROOT_REL),
         "--baseline", str(baseline), "--candidate", str(candidate)],
    )
    # FAIL → exit code 4 (see cli_stack.py).
    assert result.exit_code == 4, result.output
    assert "dep_func" in result.output or "libdep" in result.output
