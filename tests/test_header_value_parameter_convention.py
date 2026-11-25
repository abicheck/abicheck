# SPDX-License-Identifier: Apache-2.0
"""Stored L2 snapshots identify exactly the functions whose parameter ABI flips."""

import json
import platform
import shutil
import subprocess
import sys

import pytest
from click.testing import CliRunner

from abicheck.cli import main


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "linux" or platform.machine() != "x86_64",
    reason="tests the SysV AMD64 calling convention",
)
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_by_value_call_trait_survives_dump_compare(tmp_path, monkeypatch, backend):
    if not shutil.which("clang++") or not shutil.which(backend):
        pytest.skip(f"needs clang++ and {backend}")
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", backend)
    runner = CliRunner()
    snapshots = []
    for side, changed in (("old", False), ("new", True)):
        root = tmp_path / side
        root.mkdir()
        header = root / "api.hpp"
        header.write_text(
            "struct R { int x;" + (" R(const R&);" if changed else "") + " };\n"
            "struct [[clang::trivial_abi]] T { int x;"
            + (" T(const T&);" if changed else "")
            + " };\n"
            "struct S { int x;"
            + (" S& operator=(const S&);" if changed else "")
            + " };\n"
            "template<class U> struct Box { U item; }; using BoxR = Box<R>;\n"
            "static_assert(sizeof(BoxR) == sizeof(R));\n"
            'extern "C" { int templ(int, BoxR); int val(int, R); int ptr(R*); int ref(R&); int attr(T); int assign(S); }\n'
        )
        src = root / "api.cpp"
        src.write_text(
            '#include "api.hpp"\nint templ(int i,BoxR b){return i+b.item.x;} int val(int i,R r){return i+r.x;} int ptr(R* r){return r->x;} '
            "int ref(R& r){return r.x;} int attr(T t){return t.x;} int assign(S s){return s.x;}\n"
        )
        lib = root / "libapi.so"
        subprocess.run(
            ["clang++", "-shared", "-fPIC", "-s", str(src), "-o", str(lib)], check=True
        )
        snap = root / "api.json"
        result = runner.invoke(
            main, ["dump", str(lib), "-H", str(header), "-o", str(snap)]
        )
        assert result.exit_code == 0, (result.output, result.exception)
        snapshots.append(str(snap))
    from abicheck.compare.value_parameter_convention import value_parameter_changes
    from abicheck.model.dwarf_facts import AdvancedDwarfMetadata
    from abicheck.serialization import load_snapshot

    old, new = (load_snapshot(tmp_path / path) for path in snapshots)
    for snap in (old, new):
        snap.declarations.debug_advanced = AdvancedDwarfMetadata(
            value_abi_traits={"val": "ret:nontrivial|p1:unknown"}
        )
    assert "val" in {
        c.symbol
        for c in value_parameter_changes(old, new, old.function_map, new.function_map)
    }
    old.declarations.debug_advanced.value_abi_traits["val"] = "p1:trivial"
    new.declarations.debug_advanced.value_abi_traits["val"] = "p1:nontrivial"
    assert "val" not in {
        c.symbol
        for c in value_parameter_changes(old, new, old.function_map, new.function_map)
    }
    report = tmp_path / "report.json"
    for operands, before, after in (
        (snapshots, "p1:trivial", "p1:nontrivial"),
        (snapshots[::-1], "p1:nontrivial", "p1:trivial"),
    ):
        result = runner.invoke(main, ["compare", *operands, "-o", f"json={report}"])
        assert result.exit_code == 4, (result.output, result.exception)
        changes = json.loads(report.read_text())["changes"]
        rows = [
            (c["symbol"], c["old_value"], c["new_value"])
            for c in changes
            if c["kind"] == "value_abi_trait_changed"
        ]
        assert sorted(rows) == [("templ", before, after), ("val", before, after)]
