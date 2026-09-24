"""End to end: ``--exclude-header`` scopes constants and typedefs too.

Reproduces the reported defect through the real ``dump``/``compare`` CLI on
both header backends: a constant declared only in an excluded header used to
survive into the snapshot and *gate* the comparison (``Gate: REJECTED``),
while an unused typedef from that header survived too. Requires gcc plus the
backend's front end.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.serialization import load_snapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so"),
    pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not found"),
]

DEP_H = (
    "#pragma once\nstatic const int DEP_CONSTANT = {value};\n"
    "typedef int dep_unused_t;\ntypedef long dep_used_t;\n"
)
API_H = (
    '#pragma once\n#include "dep.h"\nstatic const int API_CONSTANT = 3;\n'
    "int api_function(dep_used_t x);\n"
)
LIB_C = '#include "api.h"\nint api_function(dep_used_t x){return (int)x;}\n'


def _build(root: Path, value: int) -> Path:
    root.mkdir()
    (root / "dep.h").write_text(DEP_H.format(value=value))
    (root / "api.h").write_text(API_H)
    (root / "lib.c").write_text(LIB_C)
    so = root / "libx.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-o", str(so), str(root / "lib.c")],
        check=True,
    )
    return so


def _run(*args: str, frontend: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "ABICHECK_AST_FRONTEND": frontend}
    return subprocess.run(
        [sys.executable, "-m", "abicheck", *args],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.parametrize("frontend", ["castxml", "clang"])
def test_excluded_header_constants_and_typedefs_are_scoped(tmp_path, frontend):
    tool = "castxml" if frontend == "castxml" else "clang"
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not found")
    snaps = []
    for label, value in (("old", 1), ("new", 2)):
        so = _build(tmp_path / label, value)
        out = tmp_path / f"{label}.json"
        r = _run(
            "dump", str(so), "-H", str(so.parent), "--exclude-header", "dep.h",
            "-o", str(out), frontend=frontend,
        )  # fmt: skip
        assert r.returncode == 0, r.stderr
        snaps.append(out)

    snap = load_snapshot(snaps[0])
    assert set(snap.constants) == {"API_CONSTANT"}
    assert set(snap.constant_entity_ids) == {"API_CONSTANT"}
    # The used alias stays (the public signature names it); the unused one goes.
    assert "dep_used_t" in snap.typedefs and "dep_unused_t" not in snap.typedefs
    assert "dep_unused_t" not in snap.typedefs_qualified

    # DEP_CONSTANT changed 1 -> 2 in the excluded header: not a finding.
    r = _run("compare", str(snaps[0]), str(snaps[1]), frontend=frontend)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DEP_CONSTANT" not in r.stdout
