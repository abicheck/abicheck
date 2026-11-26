# SPDX-License-Identifier: Apache-2.0
"""Compare stripped ELF/header classifications to the compiler's return signature."""

import json
import platform
import re
import shutil
import subprocess
import sys

import pytest
from click.testing import CliRunner

from abicheck.cli import main


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "linux" or platform.machine() != "x86_64", reason="SysV AMD64"
)
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_small_subobjects_use_compiler_return_registers(tmp_path, monkeypatch, backend):
    if not all(shutil.which(tool) for tool in ("clang++", backend)):
        pytest.skip("requires Clang and requested header backend")
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", backend)
    runner = CliRunner()
    snapshots = []
    for side, scalar in (("old", "double"), ("new", "long")):
        d = tmp_path / side
        d.mkdir()
        header = d / "api.hpp"
        header.write_text(
            f"struct Leaf {{ {scalar} a; }};\n"
            "struct Nested { Leaf x; long b; }; struct Derived: Leaf { long b; };\n"
            "struct Aligned { alignas(16) double x; }; struct Large { double x[3]; };\n"
            'extern "C" { Nested nested(); Derived derived(); Aligned aligned(); Large large(); }\n'
        )
        src = d / "lib.cpp"
        src.write_text(
            '#include "api.hpp"\nNested nested(){return {};} Derived derived(){return {};} Aligned aligned(){return {};} Large large(){return {};}'
        )
        ir = subprocess.check_output(
            ["clang++", "-S", "-emit-llvm", src, "-o", "-"], text=True
        )
        for fn in ("nested", "derived"):
            abi = re.search(rf"^define .*?(@{fn}\(.*)$", ir, re.M)
            assert abi
            assert (
                "{ double, i64 }" if side == "old" else "{ i64, i64 }"
            ) in abi.group()
        assert re.search(r"define .*double @aligned\(", ir)
        assert re.search(r"define .*void @large\([^\n]*sret", ir)
        lib = d / "api.so"
        subprocess.run(
            ["clang++", "-shared", "-fPIC", "-s", src, "-o", lib], check=True
        )
        snap = d / "api.json"
        res = runner.invoke(
            main, ["dump", str(lib), "-H", str(header), "-o", str(snap)]
        )
        assert res.exit_code == 0, res.output
        snapshots.append(str(snap))
    report = tmp_path / "compare.json"
    res = runner.invoke(main, ["compare", *snapshots, "-o", f"json={report}"])
    assert res.exit_code == 4, res.output
    changes = json.loads(report.read_text())["changes"]
    assert {
        (c["symbol"], c["old_value"], c["new_value"])
        for c in changes
        if c["kind"] == "struct_return_convention_changed"
    } == {
        (k, "regs(SSE,INTEGER)", "regs(INTEGER,INTEGER)") for k in ("Derived", "Nested")
    }
