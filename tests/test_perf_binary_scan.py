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
            "--binary",
            str(new_so),
            "--baseline",
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
            "--binary",
            str(new_so),
            "--baseline",
            str(old_so),
            "-H",
            str(new_include),
            "--baseline-header",
            str(old_include),
            "--sources",
            str(src),
            "--compile-db",
            str(cdb),
            "--depth",
            "headers",
            "--lang",
            "c",
            "--format",
            "json",
        ],
    )
    wall = time.monotonic() - start

    assert res.exit_code == 0, res.output
    json_start = res.output.find("{")
    assert json_start >= 0, res.output
    doc = json.loads(res.output[json_start:])
    rows = {row["layer"]: row for row in doc["coverage"]}
    assert doc["verdict"] == "COMPATIBLE_WITH_RISK"
    assert rows["L0_binary"]["status"] == "present"
    assert rows["L1_debug"]["status"] == "present"
    assert rows["L2_header"]["status"] == "present"
    assert rows["L3_build"]["status"] == "not_collected"
    assert doc["pattern_scan"]["files_scanned"] == 1
    assert "virtual_method" not in doc["pattern_scan"].get("counts_by_kind", {})
    assert doc["elapsed_s"] < 5.0
    assert wall < 8.0
