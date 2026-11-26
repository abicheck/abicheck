"""Report provider, contract and client evidence independently through compare."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from abicheck.checker import compare
from abicheck.contract_relevance_types import ContractRelevance
from abicheck.contract_replay import reevaluate_from_evidence
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Fact,
    Function,
    Param,
    RecordType,
    TypeField,
)
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.model.extraction_scope import EntityOwnership
from abicheck.report.diff_report import to_json
from abicheck.report.l2_evidence import dependency_changes
from abicheck.serialization import load_snapshot, save_snapshot
from tests._abicheck_subprocess import abicheck_command


@pytest.mark.parametrize("member", ["LastKnownDataLessPropKind", "active"])
@pytest.mark.parametrize(
    ("old_path", "new_path", "contract", "forced", "relevance", "contribution"),
    [
        (None, None, "public", False, ContractRelevance.UNKNOWN_UNRESOLVED, 0),
        ("field", "field", "public", False, ContractRelevance.IN_CONTRACT, 4),
        ("field", None, "public", False, ContractRelevance.IN_CONTRACT, 4),
        (None, "field", "public", False, ContractRelevance.UNKNOWN_UNRESOLVED, 0),
        ("parameter", "parameter", "public", False, ContractRelevance.IN_CONTRACT, 4),
        ("return", None, "public", False, ContractRelevance.IN_CONTRACT, 4),
        (None, "parameter", "public", False, ContractRelevance.UNKNOWN_UNRESOLVED, 0),
        (
            "unexported",
            "return",
            "public",
            False,
            ContractRelevance.UNKNOWN_UNRESOLVED,
            0,
        ),
        ("private", "return", "public", False, ContractRelevance.UNKNOWN_UNRESOLVED, 0),
        (None, None, "public", True, ContractRelevance.IN_CONTRACT, 4),
        (None, None, "all", False, ContractRelevance.IN_CONTRACT, 4),
    ],
)
def test_dependency_enumerator_observed_by_two_libraries(
    tmp_path, member, old_path, new_path, contract, forced, relevance, contribution
):
    def snapshot(value, path):
        enum = EnumType(
            "Mode",
            [EnumMember(member, value)],
            qualified_name="dep::Mode",
            source_header="include/dep/mode.hpp",
        )
        enum.ownership_fact = Fact.present(
            EntityOwnership("dependency:dep", "external", "dep-root")
        )
        record = RecordType(
            "Api",
            kind="struct",
            fields=[TypeField("mode", "dep::Mode")] if path == "field" else [],
        )
        record.ownership_fact = Fact.present(
            EntityOwnership("target", "public", "api-root")
        )
        function = Function(
            "api",
            "api",
            "dep::Mode" if path in {"return", "unexported", "private"} else "void",
            params=[Param("mode", "dep::Mode")] if path == "parameter" else [],
        )
        function.ownership_fact = Fact.present(
            EntityOwnership(
                "target", "private" if path == "private" else "public", "api-root"
            )
        )
        return AbiSnapshot(
            "libapi.so",
            str(value),
            enums=[enum],
            types=[record],
            functions=[function],
            elf=ElfMetadata(symbols=[] if path == "unexported" else [ElfSymbol("api")]),
            from_headers=True,
        )

    snapshot_path = tmp_path / "new.json"
    save_snapshot(snapshot(22, new_path), snapshot_path)
    comparison = compare(
        snapshot(21, old_path),
        load_snapshot(snapshot_path),
        contract_evaluation=True,
        contract_mode=contract,
        force_public_symbols={f"Mode::{member}"} if forced else None,
    )
    change = next(c for c in comparison.changes if c.symbol == f"Mode::{member}")
    assert change.contract_relevance is relevance
    report = json.loads(to_json(comparison))
    [finding] = [c for c in report["changes"] if c["symbol"] == change.symbol]
    assert finding["gate_contribution"] == contribution
    if not forced:
        [replayed] = reevaluate_from_evidence(
            comparison.contract_context, [change]
        ).values()
        assert replayed.relevance is relevance
    evidence = change.review_evidence
    decl = evidence["enum_declarations"]["old"]
    assert decl["owner"] == "dependency:dep"
    assert decl["public_field_paths"] == (
        [["Api", "mode", "dep::Mode"]] if old_path == "field" else []
    )
    slots = {"parameter": "parameter 1", "return": "return"}
    assert decl["public_exported_signatures"] == (
        [["api", slots[old_path], "dep::Mode"]] if old_path in slots else []
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
    from abicheck.compare.l2_evidence import enum_paths
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
    assert enum_paths(snap).inline_arguments["dep::Mode::last"] == [
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
            f"namespace dep {{ enum Mode {{ LastKnownDataLessPropKind = {value} }}; }}",
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
            "public",
            "-o",
            "json=report.json",
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1, run.stderr
    document = json.loads((tmp_path / "report.json").read_text())
    for library in document["libraries"]:
        [finding] = library["findings"]
        assert finding["contract_relevance"] == "UNKNOWN_UNRESOLVED"
        assert finding["gate_contribution"] == 0
    [dependency] = document["dependency_changes"]
    assert dependency["libraries"] == ["libcpu.so", "libgpu.so"]
    assert (dependency["old_value"], dependency["new_value"]) == ("21", "22")
    assert dependency["declarations"]["old"]["owner"] == "dependency:vendor"

    pair = subprocess.run(
        abicheck_command(
            "compare",
            "old/lib/libgpu.so",
            "new/lib/libgpu.so",
            "-H",
            "old=old/include/api.hpp",
            "-H",
            "new=new/include/api.hpp",
            "--contract",
            "public",
            "-o",
            "json=public.json",
            "-o",
            "markdown=public.md",
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert pair.returncode == 1, pair.stderr
    public = json.loads((tmp_path / "public.json").read_text())
    [change] = [
        c
        for c in public["changes"]
        if c["symbol"].endswith("::LastKnownDataLessPropKind")
    ]
    assert change["contract_relevance"] == "UNKNOWN_UNRESOLVED"
    assert change["contract_reason_code"] == "dependency_enum_public_path_unresolved"
    assert change["gate_contribution"] == 0
    assert public["contract_coverage_exit_contribution"] == 1
    assert "do not establish a product ABI dependency" in (
        tmp_path / "public.md"
    ).read_text(encoding="utf-8")


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="uses ELF shared libraries")
def test_dependency_enum_in_exported_signature_gates(tmp_path):
    for side, value in (("old", 21), ("new", 22)):
        root = tmp_path / side
        (root / "vendor").mkdir(parents=True)
        (root / "include").mkdir()
        (root / "vendor/mode.hpp").write_text(
            f"namespace dep {{ enum Mode {{ active = {value} }}; }}"
        )
        (root / "include/api.hpp").write_text(
            '#include "../vendor/mode.hpp"\nextern "C" dep::Mode api(dep::Mode mode);'
        )
        source = root / "api.cc"
        source.write_text(
            '#include "include/api.hpp"\ndep::Mode api(dep::Mode mode){return mode;}'
        )
        subprocess.run(
            ["g++", "-shared", "-fPIC", str(source), "-o", str(root / "libapi.so")],
            check=True,
        )
    (tmp_path / ".abicheck.yml").write_text(
        "scope:\n  dependencies:\n    - name: vendor\n      header_roots: [old/vendor, new/vendor]\n"
    )

    result = subprocess.run(
        abicheck_command(
            "compare",
            "old/libapi.so",
            "new/libapi.so",
            "-H",
            "old=old/include/api.hpp",
            "-H",
            "new=new/include/api.hpp",
            "--contract",
            "public",
            "-o",
            "json=report.json",
            "-o",
            "markdown=report.md",
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    report = json.loads((tmp_path / "report.json").read_text())
    [finding] = [c for c in report["changes"] if c["symbol"].endswith("::active")]
    assert finding["contract_relevance"] == "IN_CONTRACT"
    assert finding["gate_contribution"] == result.returncode == 4
    assert finding["review_evidence"]["enum_declarations"]["old"][
        "public_exported_signatures"
    ] == [["api", "return", "dep::Mode"], ["api", "parameter 1", "dep::Mode"]]
    assert "Exported function signature" in (tmp_path / "report.md").read_text()
