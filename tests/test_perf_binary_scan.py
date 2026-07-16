"""Performance guards for shallow scan-depth semantics.

The unit tests in ``tests/test_cli_scan.py`` prove the arguments are suppressed
before orchestration. This slow test exercises the real CLI over native ELF
inputs so the performance lane catches the regression class that made pvxs'
shallow scans spend seconds in source/DWARF work.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = pytest.mark.slow


def _compile_so(src: str, out: Path) -> None:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc required for native ELF binary scan performance guard")
    cmd = [gcc, "-shared", "-fPIC", "-g", "-O0", "-o", str(out), "-x", "c", "-"]
    res = subprocess.run(cmd, input=src.encode(), capture_output=True)
    if res.returncode != 0:
        pytest.skip(f"gcc failed: {res.stderr.decode(errors='replace')[:300]}")


@pytest.mark.skipif(sys.platform != "linux", reason="native ELF fast path is Linux-only")
def test_binary_depth_matrix_args_stays_artifact_only_and_fast(tmp_path: Path) -> None:
    old_so = tmp_path / "libold.so"
    new_so = tmp_path / "libnew.so"
    _compile_so(
        """
        __attribute__((visibility("default"))) int kept(void) { return 1; }
        __attribute__((visibility("default"))) int removed(void) { return 2; }
        """,
        old_so,
    )
    _compile_so(
        """
        __attribute__((visibility("default"))) int kept(void) { return 1; }
        """,
        new_so,
    )

    include = tmp_path / "include"
    include.mkdir()
    (include / "api.h").write_text(
        "int kept(void);\nint removed(void);\n",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    for i in range(250):
        (src / f"tu_{i}.cpp").write_text(
            f'extern "C" int tu_{i}(void) {{ return {i}; }}\n',
            encoding="utf-8",
        )
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text("[]", encoding="utf-8")

    start = time.monotonic()
    res = CliRunner().invoke(
        main,
        [
            "scan",
            str(new_so),
            "--against",
            str(old_so),
            "-H",
            str(include),
            "--sources",
            str(src),
            "--compile-db",
            str(cdb),
            "--depth",
            "binary",
            "--format",
            "json",
        ],
    )
    wall = time.monotonic() - start

    assert res.exit_code == 4, res.output
    json_start = res.output.find("{")
    assert json_start >= 0, res.output
    doc = json.loads(res.output[json_start:])
    rows = {row["layer"]: row for row in doc["coverage"]}
    assert doc["verdict"] == "BREAKING"
    assert rows["L0_binary"]["status"] == "present"
    assert rows["L1_debug"]["status"] == "present"
    assert rows["L2_header"]["status"] == "skipped"
    assert rows["pattern_scan"]["status"] == "not_collected"
    assert rows["L3_build"]["status"] == "not_collected"
    assert doc["pattern_scan"]["files_scanned"] == 0
    assert doc["elapsed_s"] < 3.0
    assert wall < 5.0


@pytest.mark.skipif(sys.platform != "linux", reason="native ELF fast path is Linux-only")
def test_headers_depth_matrix_args_stays_l2_only_and_fast(tmp_path: Path) -> None:
    if shutil.which("clang") is None:
        pytest.skip("clang required for header-depth scan performance guard")

    old_so = tmp_path / "libold.so"
    new_so = tmp_path / "libnew.so"
    _compile_so(
        """
        __attribute__((visibility("default"))) int kept(void) { return 1; }
        __attribute__((visibility("default"))) int removed(void) { return 2; }
        """,
        old_so,
    )
    _compile_so(
        """
        __attribute__((visibility("default"))) int kept(void) { return 1; }
        """,
        new_so,
    )

    old_include = tmp_path / "old-include"
    old_include.mkdir()
    (old_include / "api.h").write_text(
        "int kept(void);\nint removed(void);\n",
        encoding="utf-8",
    )
    new_include = tmp_path / "new-include"
    new_include.mkdir()
    (new_include / "api.h").write_text("int kept(void);\n", encoding="utf-8")

    src = tmp_path / "src"
    src.mkdir()
    for i in range(250):
        (src / f"tu_{i}.cpp").write_text(
            f"struct Impl{i} {{ virtual ~Impl{i}(); }};\n",
            encoding="utf-8",
        )
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text("[]", encoding="utf-8")

    start = time.monotonic()
    res = CliRunner().invoke(
        main,
        [
            "scan",
            str(new_so),
            "--against",
            str(old_so),
            "-H",
            f"new={new_include}",
            "-H",
            f"old={old_include}",
            "--sources",
            str(src),
            "--compile-db",
            str(cdb),
            "--depth",
            "headers",
            "--ast-frontend",
            "clang",
            "--lang",
            "c",
            "--format",
            "json",
        ],
    )
    wall = time.monotonic() - start

    assert res.exit_code == 4, res.output
    json_start = res.output.find("{")
    assert json_start >= 0, res.output
    doc = json.loads(res.output[json_start:])
    rows = {row["layer"]: row for row in doc["coverage"]}
    assert doc["verdict"] == "BREAKING"
    assert rows["L0_binary"]["status"] == "present"
    assert rows["L1_debug"]["status"] == "present"
    assert rows["L2_header"]["status"] == "present"
    assert rows["L3_build"]["status"] == "not_collected"
    assert doc["pattern_scan"]["files_scanned"] == 1
    assert "virtual_method" not in doc["pattern_scan"].get("counts_by_kind", {})
    # Timing budgets guard against a *catastrophic* regression (e.g. accidentally
    # doing L3 build work), not micro-variance — the structural assertions above
    # (L3 not_collected, files_scanned == 1) are the real "L2-only" guard. The
    # clang-AST header parse dominates wall time and is highly machine-dependent:
    # observed 3.4s locally but 6.26s and 11.28s on contended shared CI runners.
    # Chasing that variance with a tight budget only flakes the lane, so the
    # ceiling is deliberately generous — a real regression that pulls in a build
    # layer (cmake configure alone is 60s-timeout territory) blows far past it
    # while normal runner spikes stay well under.
    assert doc["elapsed_s"] < 30.0
    assert wall < 45.0
