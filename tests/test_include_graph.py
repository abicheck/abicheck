# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for ADR-031 D3 include graph: the depfile parser, graph augmentation,
and graceful clang-absent degrade. The live `clang -MM` path is integration."""

from __future__ import annotations

from abicheck.evidence.build_evidence import BuildEvidence, CompileUnit
from abicheck.evidence.include_graph import (
    ClangIncludeExtractor,
    augment_graph_with_includes,
    build_clang_dep_command,
    parse_depfile,
)
from abicheck.evidence.source_graph import GraphNode, SourceGraphSummary


def test_parse_depfile_basic() -> None:
    assert parse_depfile("foo.o: foo.cpp a.h b.h") == ["foo.cpp", "a.h", "b.h"]


def test_parse_depfile_line_continuations() -> None:
    text = "foo.o: foo.cpp \\\n  inc/a.h \\\n  inc/b.h\n"
    assert parse_depfile(text) == ["foo.cpp", "inc/a.h", "inc/b.h"]


def test_parse_depfile_dedupes_and_skips_no_colon() -> None:
    text = "garbage line\nfoo.o: a.h a.h b.h"
    assert parse_depfile(text) == ["a.h", "b.h"]


def test_parse_depfile_windows_drive_letter_target() -> None:
    # The drive-letter colon must not be mistaken for the rule separator.
    assert parse_depfile(r"C:\build\foo.o: C:\src\foo.cpp inc\a.h") == [
        r"C:\src\foo.cpp", r"inc\a.h",
    ]


def test_augment_reuses_existing_header_node() -> None:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="header://inc/foo.h", kind="header", label="inc/foo.h"))
    added = augment_graph_with_includes(g, {"cu://foo": ["inc/foo.h"]})
    assert added == 1
    edge = next(e for e in g.edges if e.kind == "COMPILE_UNIT_INCLUDES_FILE")
    assert edge.src == "cu://foo" and edge.dst == "header://inc/foo.h"


def test_augment_creates_file_node_when_unknown() -> None:
    g = SourceGraphSummary()
    augment_graph_with_includes(g, {"cu://foo": ["sys/stdio.h"]})
    node = next(n for n in g.nodes if n.label == "sys/stdio.h")
    assert node.kind == "file" and node.id == "file://sys/stdio.h"


def test_augment_dedupes_and_skips_blank() -> None:
    g = SourceGraphSummary()
    augment_graph_with_includes(g, {"cu://foo": ["a.h", ""]})
    added = augment_graph_with_includes(g, {"cu://foo": ["a.h"]})
    assert added == 0
    assert not any(n.label == "" for n in g.nodes)


def test_extractor_missing_clang_returns_empty() -> None:
    ext = ClangIncludeExtractor(clang_bin="definitely-not-clang-xyz")
    assert ext.available() is False
    assert ext.extract_from_build(
        BuildEvidence(compile_units=[CompileUnit(id="cu://x", source="x.cpp")])
    ) == {}
    assert ext.diagnostics


def test_extractor_parses_mocked_clang(monkeypatch) -> None:
    import abicheck.evidence.include_graph as ig

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")

    class _Proc:
        stdout = "foo.o: foo.cpp inc/foo.h"
        stderr = ""

    seen = {}

    def _run(cmd, **_kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(ig.subprocess, "run", _run)
    build = BuildEvidence(compile_units=[
        CompileUnit(id="cu://foo", source="foo.cpp", argv=[
            "/usr/bin/c++", "-fplugin=/tmp/evil.so", "foo.cpp",
        ]),
        CompileUnit(id="cu://nosrc", source=""),  # skipped
    ])
    includes = ClangIncludeExtractor().extract_from_build(build)
    assert includes == {"cu://foo": ["foo.cpp", "inc/foo.h"]}
    assert seen["cmd"] == ["clang++", "-MM", "-x", "c++", "--", "foo.cpp"]
    assert "-fplugin=/tmp/evil.so" not in seen["cmd"]


def test_build_clang_dep_command_uses_normalized_context_only() -> None:
    cu = CompileUnit(
        id="cu://foo",
        source="foo.cpp",
        argv=["/usr/bin/c++", "-Xclang", "-load", "-Xclang", "/tmp/evil.so", "foo.cpp"],
        language="CXX",
        standard="c++20",
        defines={"FEATURE": "1"},
        undefines=["OLD"],
        include_paths=["inc"],
        system_include_paths=["sys"],
        sysroot="/sdk",
        target_triple="x86_64-linux-gnu",
    )

    assert build_clang_dep_command(cu, clang_bin="clang++") == [
        "clang++", "-MM", "-x", "c++", "-std=c++20", "-DFEATURE=1", "-UOLD",
        "-I", "inc", "-isystem", "sys", "--sysroot=/sdk",
        "--target=x86_64-linux-gnu", "--", "foo.cpp",
    ]


def test_build_clang_dep_command_rejects_response_file_source() -> None:
    cu = CompileUnit(id="cu://rsp", source="@args.rsp")
    try:
        build_clang_dep_command(cu)
    except ValueError as exc:
        assert "starts with '@'" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected response-file source to be rejected")


def test_extractor_handles_subprocess_error(monkeypatch) -> None:
    import abicheck.evidence.include_graph as ig

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")

    def _boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(ig.subprocess, "run", _boom)
    build = BuildEvidence(compile_units=[CompileUnit(id="cu://foo", source="foo.cpp")])
    assert ClangIncludeExtractor().extract_from_build(build) == {}


def test_collect_evidence_include_graph_missing_clang_degrades(tmp_path, monkeypatch) -> None:
    # --include-graph implies --source-graph summary; a missing clang records a
    # failed extractor row but still writes the pack with the build graph.
    import json

    from click.testing import CliRunner

    import abicheck.evidence.include_graph as ig
    from abicheck.cli import main
    from abicheck.evidence.pack import EvidencePack

    monkeypatch.setattr(ig.shutil, "which", lambda _b: None)
    src = tmp_path / "foo.cpp"
    src.write_text("int foo(){return 1;}\n")
    cdb = tmp_path / "cc.json"
    cdb.write_text(json.dumps([{
        "directory": str(tmp_path), "file": str(src), "command": f"c++ -c {src} -o foo.o",
    }]))
    out = tmp_path / "ev"
    res = CliRunner().invoke(main, [
        "collect-evidence", "--compile-db", str(cdb), "--include-graph", "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    pack = EvidencePack.load(out)
    assert pack.source_graph is not None
    assert any(e.name == "include_graph:clang" and e.status == "failed"
               for e in pack.manifest.extractors)
