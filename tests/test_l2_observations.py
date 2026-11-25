"""Report provider, contract and client evidence independently through compare."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from abicheck.checker import compare
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Fact,
    RecordType,
    TypeField,
)
from abicheck.model.extraction_scope import EntityOwnership
from abicheck.report.l2_evidence import dependency_changes
from tests._abicheck_subprocess import abicheck_command


@pytest.mark.parametrize("exposed", [False, True])
def test_dependency_enumerator_observed_by_two_libraries(exposed):
    def snapshot(value):
        enum = EnumType(
            "Mode",
            [EnumMember("last", value)],
            qualified_name="dep::Mode",
            source_header="include/dep/mode.hpp",
        )
        enum.ownership_fact = Fact.present(
            EntityOwnership("dependency:dep", "external", "dep-root")
        )
        record = RecordType(
            "Api",
            kind="struct",
            fields=[TypeField("mode", "dep::Mode")] if exposed else [],
        )
        record.ownership_fact = Fact.present(
            EntityOwnership("target", "public", "api-root")
        )
        return AbiSnapshot(
            "libapi.so", str(value), enums=[enum], types=[record], from_headers=True
        )

    change = next(
        c
        for c in compare(snapshot(21), snapshot(22)).changes
        if c.symbol.endswith("::last")
    )
    evidence = change.review_evidence
    decl = evidence["enum_declarations"]["old"]
    assert decl["owner"] == "dependency:dep"
    assert decl["public_field_paths"] == (
        [["Api", "mode", "dep::Mode"]] if exposed else []
    )
    finding = dict(
        symbol=change.symbol,
        old_value=change.old_value,
        new_value=change.new_value,
        review_evidence=evidence,
    )
    result = dependency_changes(
        [{"library": lib, "findings": [finding]} for lib in ["cpu.so", "gpu.so"]]
    )
    assert len(result) == 1
    assert result[0]["libraries"] == ["cpu.so", "gpu.so"]


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "linux", reason="uses GNU ELF version scripts and SONAME"
)
def test_hidden_inline_and_control_object(tmp_path):
    if not shutil.which("g++") or not shutil.which("ld"):
        pytest.skip("requires g++ and ld")

    def compile(*args):
        subprocess.run(["g++", "-fPIC", *args], cwd=tmp_path, check=True)

    (tmp_path / "api.hpp").write_text("struct Api { virtual ~Api() {} };\n")
    (tmp_path / "lib.cc").write_text(
        '#include "api.hpp"\nextern "C" void drop(Api *p){p->Api::~Api();}'
    )
    (tmp_path / "api.map").write_text("API_1 { global: *; };")
    for side, flags in [("old", []), ("new", ["-fvisibility-inlines-hidden"])]:
        (tmp_path / side).mkdir()
        compile(
            "-shared",
            *flags,
            "lib.cc",
            "-Wl,-soname,libapi.so,--version-script=api.map",
            "-o",
            side + "/libapi.so",
        )
    (tmp_path / "inline.cc").write_text(
        '#include "api.hpp"\nextern "C" void local(Api *p){p->Api::~Api();}'
    )
    compile("-shared", "inline.cc", "-o", "inline.so")
    (tmp_path / "external.cc").write_text(
        'extern void d(void*) asm("_ZN3ApiD2Ev");\nvoid use(void *p){d(p);}'
    )
    compile("-c", "external.cc", "-o", "control.o")
    compile("-shared", "control.o", "old/libapi.so", "-o", "shipped.so")

    def report(*options):
        result = subprocess.run(
            abicheck_command(
                "compare",
                "old/libapi.so",
                "new/libapi.so",
                *options,
                "--contract",
                "exports",
                "-o",
                "json=report.json",
            ),
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 4, result.stderr
        document = json.loads((tmp_path / "report.json").read_text())
        variants = {
            v["name"]
            for group in document["removed_export_groups"]
            for v in group["variants"]
        }
        assert {"_ZN3ApiD1Ev", "_ZN3ApiD2Ev"} <= variants
        changes = document["changes"]
        return next(c for c in changes if c["symbol"] == "_ZN3ApiD2Ev")

    base = report("-H", "api.hpp")
    assert base["gate_contribution"] == 4
    assert base["review_evidence"]["declaration_header"].endswith("api.hpp")
    assert base["review_evidence"]["client_check_status"] == "not_checked"
    (tmp_path / "clients.json").write_text(
        json.dumps(
            {
                "consumers": [
                    {"path": "inline.so", "artifact_role": "released_client"},
                    {"path": "shipped.so", "artifact_role": "released_client"},
                    {"path": "control.o", "artifact_role": "control_object"},
                ]
            }
        )
    )
    (tmp_path / ".abicheck.yml").write_text("consumers:\n  manifests: [clients.json]\n")
    change = report()
    assert change["gate_contribution"] == base["gate_contribution"] == 4
    evidence = change["review_evidence"]
    assert evidence["removed_exports"][0]["binding"] == "weak"
    inline, shipped, control = evidence["client_observations"]
    assert inline["match_count"] == 0
    assert shipped["direct_name_matches"][0]["binding"] == "global"
    assert shipped["direct_name_matches"][0]["version"] == "API_1"
    assert evidence["removed_exports"][0]["version"] == "API_1"
    assert shipped["resolution"]["status"] == "not_tested"
    assert control["resolution"]["old"]["exit_code"] == 0
    assert control["resolution"]["new"]["exit_code"] != 0
    assert "_ZN3ApiD2Ev" in control["resolution"]["new"]["diagnostic"]


def test_public_inline_passes_exact_enumerator_to_exported_function():
    from abicheck.buildsource.type_graph import augment_graph_with_types
    from abicheck.buildsource.type_graph_walk import parse_clang_ast_types
    from abicheck.compare.l2_evidence import public_enum_arguments
    from abicheck.model import Function
    from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
    from abicheck.model.source_graph import SourceGraphSummary

    def ref(node):
        return {"kind": "DeclRefExpr", "referencedDecl": node}

    member = {"kind": "EnumConstantDecl", "name": "last", "id": "e"}
    target = {
        "kind": "FunctionDecl",
        "name": "set_mode",
        "id": "f",
        "mangledName": "set_mode",
    }
    call = {
        "kind": "CallExpr",
        "inner": [ref(target), {"kind": "ImplicitCastExpr", "inner": [ref(member)]}],
    }
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "dep",
                "inner": [{"kind": "EnumDecl", "name": "Mode", "inner": [member]}],
            },
            target,
            {
                "kind": "FunctionDecl",
                "name": "apply",
                "mangledName": "apply",
                "inner": [{"kind": "CompoundStmt", "inner": [call]}],
            },
        ],
    }
    graph = SourceGraphSummary()
    augment_graph_with_types(graph, parse_clang_ast_types(ast))
    fn = Function("apply", "apply", "void", is_inline=True)
    fn.ownership_fact = Fact.present(EntityOwnership("target", "public", "api-root"))
    snap = AbiSnapshot(
        "libapi.so",
        "1",
        functions=[fn],
        elf=ElfMetadata(symbols=[ElfSymbol("set_mode")]),
    )
    snap.surface_graph = graph
    assert public_enum_arguments(snap)["dep::Mode::last"] == [
        ["apply", "dep::Mode::last", "set_mode"]
    ]


@pytest.mark.integration
def test_release_groups_dependency_header_across_two_real_libraries(tmp_path):
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    for side, value in (("old", 21), ("new", 22)):
        root = tmp_path / side
        write(
            root / "vendor/mode.hpp",
            f"namespace dep {{ enum Mode {{ last = {value} }}; }}",
        )
        write(
            root / "include/api.hpp",
            '#include "../vendor/mode.hpp"\nextern "C" int api();',
        )
        write(root / "api.cc", 'extern "C" int api(){return 0;}')
        (root / "lib").mkdir()
        for name in ("libcpu.so", "libgpu.so"):
            subprocess.run(
                [
                    "g++",
                    "-shared",
                    "-fPIC",
                    str(root / "api.cc"),
                    "-o",
                    str(root / "lib" / name),
                ],
                check=True,
            )
    write(
        tmp_path / ".abicheck.yml",
        "scope:\n  dependencies:\n    - name: vendor\n      header_roots: [old/vendor, new/vendor]\n",
    )
    run = subprocess.run(
        abicheck_command(
            "compare",
            "old/lib",
            "new/lib",
            "-H",
            "old=old/include/api.hpp",
            "-H",
            "new=new/include/api.hpp",
            "--contract",
            "all",
            "-o",
            "json=report.json",
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert run.returncode in (0, 4), run.stderr
    document = json.loads((tmp_path / "report.json").read_text())
    [dependency] = document["dependency_changes"]
    assert dependency["libraries"] == ["libcpu.so", "libgpu.so"]
    assert (dependency["old_value"], dependency["new_value"]) == ("21", "22")
    assert dependency["declarations"]["old"]["owner"] == "dependency:vendor"
