# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""End-to-end coverage for the shipped probe-harness manifests (G2).

The repository ships self-contained probe manifests under
``examples/probes/`` that, unlike ``onedpl.yaml``, need no external
toolchain — stock ``cc`` / ``c++`` is enough. These tests drive them
through the *mainline* ``compare`` command via ``--probe-matrix-old`` /
``--probe-matrix-new`` so the build-config matrix findings are proven to
reach the verdict and the JSON/SARIF output, not only the standalone
``probe compare`` path.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.diff_build_config import diff_matrix
from abicheck.probe_harness import (
    load_probe_spec,
    run_probe_matrix,
    write_matrix_snapshot,
)

# These exercise stock cc/c++ only (no castxml), so they run in the default
# lane and self-skip when a compiler is absent — mirroring the gcc-only
# bundle E2E tests in test_bundle.py rather than the castxml `integration`
# marker. The .so-building tests use GNU ld's -Wl,-soname, hence Linux-only.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="probe-compare fixtures use GNU ld -Wl,-soname (Linux only)",
)

PROBES_DIR = Path(__file__).parent.parent / "examples" / "probes"


def _build_trivial_so(out: Path, soname: str) -> None:
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("cc unavailable; cannot build probe-compare fixture")
    src = out.parent / "lib.c"
    src.write_text("int api(int x){return x;}\n", encoding="utf-8")
    res = subprocess.run(
        [cc, "-shared", "-fPIC", "-g", str(src), "-o", str(out),
         f"-Wl,-soname,{soname}"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        pytest.fail(f"cc failed building {soname}: {res.stderr}")


def test_shipped_probe_specs_parse() -> None:
    """All manifests under examples/probes/ must parse (fast, no compiler)."""
    specs = sorted(PROBES_DIR.glob("*.yaml"))
    assert specs, "expected at least one probe manifest"
    for path in specs:
        spec = load_probe_spec(path)
        assert spec.name
        assert spec.configurations


def test_cxx_standard_floor_raised_through_compare(tmp_path: Path) -> None:
    """cxx_standard.yaml → CXX_STANDARD_FLOOR_RAISED reaches the compare
    verdict and the JSON + SARIF output (criteria 1 & 2 of plan G2).

    The new release drops the ``cxx14`` configuration, raising the C++
    standard floor. This is a source-level break the per-binary diff
    cannot see; the matrix carries it into the mainline command.
    """
    from click.testing import CliRunner

    from abicheck.cli import main

    spec = load_probe_spec(PROBES_DIR / "cxx_standard.yaml")
    # Floor detection reads only the parsed -std flags, so no compilation
    # is required (snapshot=False keeps the fixture fast and hermetic).
    m_old = run_probe_matrix(
        spec, library_name="stddemo", version="1.0", snapshot=False,
    )
    new_spec = dataclasses.replace(
        spec,
        configurations=tuple(c for c in spec.configurations if c.id != "cxx14"),
    )
    m_new = run_probe_matrix(
        new_spec, library_name="stddemo", version="2.0", snapshot=False,
    )
    assert m_old.cxx_stds == {"cxx14": 14, "cxx17": 17}
    assert m_new.cxx_stds == {"cxx17": 17}

    old_matrix = tmp_path / "std-1.0.json"
    new_matrix = tmp_path / "std-2.0.json"
    write_matrix_snapshot(m_old, old_matrix)
    write_matrix_snapshot(m_new, new_matrix)

    old_so = tmp_path / "libstddemo.so.1"
    new_so = tmp_path / "libstddemo.so.2"
    _build_trivial_so(old_so, "libstddemo.so.1")
    _build_trivial_so(new_so, "libstddemo.so.2")

    runner = CliRunner()
    json_res = runner.invoke(
        main,
        ["compare", str(old_so), str(new_so),
         "--probe-matrix-old", str(old_matrix),
         "--probe-matrix-new", str(new_matrix),
         "--format", "json"],
    )
    assert json_res.exit_code in (2, 4), json_res.output
    data = json.loads(json_res.stdout)
    kinds = {c["kind"] for c in data.get("changes", [])}
    assert "cxx_standard_floor_raised" in kinds

    sarif_res = runner.invoke(
        main,
        ["compare", str(old_so), str(new_so),
         "--probe-matrix-old", str(old_matrix),
         "--probe-matrix-new", str(new_matrix),
         "--format", "sarif"],
    )
    assert "cxx_standard_floor_raised" in sarif_res.stdout


def test_cxx_standard_floor_raised_through_compare_release(tmp_path: Path) -> None:
    """The same matrix finding folds into ``compare-release`` (not only the
    single-pair ``compare``): build-config findings are release-global, so
    they must reach the release verdict and JSON output.

    The two library directories carry an identical ``libstddemo.so`` so the
    per-library diff is NO_CHANGE — the verdict and the ``matrix_findings``
    section therefore come solely from the probe matrix.
    """
    from click.testing import CliRunner

    from abicheck.cli import main

    spec = load_probe_spec(PROBES_DIR / "cxx_standard.yaml")
    m_old = run_probe_matrix(
        spec, library_name="stddemo", version="1.0", snapshot=False,
    )
    new_spec = dataclasses.replace(
        spec,
        configurations=tuple(c for c in spec.configurations if c.id != "cxx14"),
    )
    m_new = run_probe_matrix(
        new_spec, library_name="stddemo", version="2.0", snapshot=False,
    )
    old_matrix = tmp_path / "std-1.0.json"
    new_matrix = tmp_path / "std-2.0.json"
    write_matrix_snapshot(m_old, old_matrix)
    write_matrix_snapshot(m_new, new_matrix)

    old_dir = tmp_path / "rel-old"
    new_dir = tmp_path / "rel-new"
    old_dir.mkdir()
    new_dir.mkdir()
    # Identical soname in both dirs → matched, NO_CHANGE per-library.
    _build_trivial_so(old_dir / "libstddemo.so", "libstddemo.so")
    _build_trivial_so(new_dir / "libstddemo.so", "libstddemo.so")

    runner = CliRunner()
    res = runner.invoke(
        main,
        ["compare-release", str(old_dir), str(new_dir),
         "--probe-matrix-old", str(old_matrix),
         "--probe-matrix-new", str(new_matrix),
         "--format", "json"],
    )
    # Floor-raised is a source-level break → API_BREAK → release exit 2.
    assert res.exit_code == 2, res.output
    data = json.loads(res.stdout)
    matrix_kinds = {c["kind"] for c in data.get("matrix_findings", [])}
    assert "cxx_standard_floor_raised" in matrix_kinds
    # The matrix verdict folded into the release-level worst-of.
    assert data["verdict"] == "API_BREAK"

    # Without the matrix flags the same release is clean (regression guard
    # against the matrix path firing unconditionally).
    res_clean = runner.invoke(
        main, ["compare-release", str(old_dir), str(new_dir), "--format", "json"],
    )
    assert res_clean.exit_code == 0, res_clean.output
    assert "matrix_findings" not in json.loads(res_clean.stdout)


def test_feature_macro_spec_compiles_cleanly(tmp_path: Path) -> None:
    """feature_macro.yaml compiles under stock ``cc`` for every config.

    This is the ``feature-macro C library`` manifest from plan G2. We
    assert the matrix runs without per-configuration compile errors so the
    shipped fixture cannot rot. The API_DEPENDS_ON_CONSUMER_ENV *detector*
    is unit-tested in tests/test_diff_build_config.py; capturing a probe's
    surface from a relocatable ``.o`` is a separate harness gap, so here we
    only assert the diff machinery runs end-to-end without raising.
    """
    if shutil.which("cc") is None:
        pytest.skip("cc unavailable; cannot compile probe matrix")
    spec = load_probe_spec(PROBES_DIR / "feature_macro.yaml")
    m_old = run_probe_matrix(
        spec, library_name="featuredemo", version="1.0",
        work_dir=tmp_path / "old",
    )
    m_new = run_probe_matrix(
        spec, library_name="featuredemo", version="2.0",
        work_dir=tmp_path / "new",
    )
    compile_errors = [r.error for r in m_old.results if r.error]
    assert not compile_errors, compile_errors
    # diff_matrix must run cleanly over real matrices (no exceptions).
    changes = diff_matrix(m_old, m_new)
    assert isinstance(changes, list)
