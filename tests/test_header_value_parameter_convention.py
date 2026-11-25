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
            "template<class U, class Tag=int> struct Box { U item; }; using BoxR = Box<R>;\n"
            "namespace api { struct Result { int x;"
            + (" ~Result();" if changed else "")
            + " }; } namespace abi = api; abi::Result make_result(); abi::Result* pointer_result(); void observe_result(const abi::Result&);\n"
            "static_assert(sizeof(BoxR) == sizeof(R));\n"
            'extern "C" { int templ(int, BoxR); int val(int, R); struct Big { long words[3]; }; Big late(int, double, R); int ptr(R*); int ref(R&); int attr(T); int assign(S); }\n'
        )
        src = root / "api.cpp"
        src.write_text(
            '#include "api.hpp"\nabi::Result make_result(){return {};} abi::Result* pointer_result(){return nullptr;} void observe_result(const abi::Result&){} int templ(int i,BoxR b){return i+b.item.x;} Big late(int i,double d,R r){return {{i,(long)d,r.x}};} int val(int i,R r){return i+r.x;} int ptr(R* r){return r->x;} '
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
    old.declarations.debug_advanced.calling_conventions["val"] = "normal"
    new.declarations.debug_advanced.calling_conventions["val"] = "LLVM_Win64"
    assert "val" in {
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
        returns = [
            c
            for c in changes
            if c["kind"] == "struct_return_convention_changed"
            and c["symbol"] == "Result"
        ]
        assert [(c["old_value"], c["new_value"]) for c in returns] == [
            ("regs(INTEGER)", "memory (sret)")
            if operands == snapshots
            else ("memory (sret)", "regs(INTEGER)")
        ]
        assert returns[0]["affected_symbols"] == ["_Z11make_resultv"]
        assert sorted(rows) == [
            ("late", before.replace("p1:", "p2:"), after.replace("p1:", "p2:")),
            ("templ", before, after),
            ("val", before, after),
        ]


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "linux" or platform.machine() != "x86_64",
    reason="Itanium ELF constructor variants",
)
def test_castxml_constructor_only_record_argument(tmp_path, monkeypatch):
    if not shutil.which("castxml") or not shutil.which("g++"):
        pytest.skip("requires castxml and g++")
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "castxml")
    runner = CliRunner()
    snapshots = []
    for side, member in (("old", ""), ("new", "~V();")):
        d = tmp_path / side
        d.mkdir()
        header = d / "api.hpp"
        header.write_text("struct V { int x; " + member + " }; struct S { S(V); };")
        src = d / "api.cpp"
        src.write_text('#include "api.hpp"\nS::S(V) {}')
        lib = d / "libapi.so"
        subprocess.run(["g++", "-shared", "-fPIC", "-s", src, "-o", lib], check=True)
        snap = d / "api.json"
        result = runner.invoke(
            main, ["dump", str(lib), "-H", str(header), "-o", str(snap)]
        )
        assert result.exit_code == 0, result.output
        snapshots.append(str(snap))
    report = tmp_path / "report.json"
    result = runner.invoke(main, ["compare", *snapshots, "-o", f"json={report}"])
    changes = json.loads(report.read_text())["changes"]
    assert {c["symbol"] for c in changes if c["kind"] == "value_abi_trait_changed"} == {
        "_ZN1SC1E1V",
        "_ZN1SC2E1V",
    }
    assert result.exit_code == 4
