"""Environment and explicit Clang selection preserve layout without debug."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF fixture"),
]


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("edit", ["layout", "deleted-copy"])
def test_environment_matches_explicit_clang_without_losing_layout(
    tmp_path: Path, monkeypatch, debug: bool, edit: str
):
    if not shutil.which("g++") or not shutil.which("clang++"):
        pytest.skip("requires g++ and clang++")
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.setenv("ABICHECK_SNAPSHOT_CACHE_WRITE", "0")
    monkeypatch.setenv("ABICHECK_SIDE_PROCESSES", "0")
    for side in ["old", "new"]:
        d = tmp_path / side
        d.mkdir()
        extra = "long extra;" if edit == "layout" and side == "new" else ""
        deleted = "delete" if edit == "deleted-copy" and side == "new" else "default"
        special = (
            f"Value() = default; Value(const Value&) = {deleted};"
            if edit == "deleted-copy"
            else ""
        )
        (d / "api.h").write_text(
            f'struct Value {{ {special} int x; {extra} }}; extern "C" int api(Value*);\n'
        )
        (d / "api.cpp").write_text(
            '#include "api.h"\nextern "C" int api(Value*p){return p->x;}\n'
        )
        subprocess.run(
            [
                "g++",
                "-g" if debug else "-g0",
                "-shared",
                "-fPIC",
                str(d / "api.cpp"),
                "-o",
                str(d / "lib.so"),
            ],
            check=True,
            capture_output=True,
        )
    args = [
        "compare",
        str(tmp_path / "old/lib.so"),
        str(tmp_path / "new/lib.so"),
        "-H",
        f"old={tmp_path}/old/api.h",
        "-H",
        f"new={tmp_path}/new/api.h",
    ]
    reports = []
    for frontend in ["environment", "clang"]:
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
        requested = "auto" if frontend == "environment" else "clang"
        dest = tmp_path / f"{frontend}.json"
        result = CliRunner().invoke(
            main,
            [*args, "--set", f"compile.frontend={requested}", "-o", f"json={dest}"],
        )
        assert dest.exists(), result.output
        doc = json.loads(dest.read_text())
        doc.get("evidence_metrics", {}).pop("extractor.duration_seconds", None)
        assert result.exit_code == 4 if edit == "layout" else result.exit_code in (2, 4)
        assert doc["verdict"] in ("BREAKING", "API_BREAK")
        assert ("type_size_changed" if edit == "layout" else "func_deleted") in {
            c["kind"] for c in doc["changes"]
        }
        reports.append(doc)
    assert reports[0] == reports[1]


@pytest.mark.parametrize("prefix", ["", "struct S { int x; }; ", "typedef int I; "])
def test_c_linkage_is_cpp_evidence_at_any_column(tmp_path, monkeypatch, prefix):
    from abicheck.extract.header_ast_backend import _resolve_header_backend
    from abicheck.extract.headers.clang.backend import resolve_clang_langmode

    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    header = tmp_path / "api.h"
    header.write_text(prefix + 'extern "C" int api(void);\n')
    assert _resolve_header_backend("auto") == "castxml"
    assert resolve_clang_langmode(None, [header], "clang++")[0] is True
    assert resolve_clang_langmode("c", [header], "clang++")[0] is False


@pytest.mark.parametrize(
    "text",
    [
        '#define CPP_LINKAGE extern "C"\nstruct CData { int new; };\n',
        '// extern "C"\nstruct CData { int new; };\n',
        '/* extern "C" */\nstruct CData { int new; };\n',
    ],
)
def test_non_declarations_do_not_select_cpp(tmp_path, text):
    from abicheck.extract.headers.clang.backend import resolve_clang_langmode

    header = tmp_path / "api.h"
    header.write_text(text)
    assert resolve_clang_langmode(None, [header], "clang++")[0] is False
