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
    depfile_args_from_argv,
    parse_depfile,
)
from abicheck.evidence.source_graph import GraphNode, SourceGraphSummary


def test_parse_depfile_basic() -> None:
    assert parse_depfile("foo.o: foo.cpp a.h b.h") == ["foo.cpp", "a.h", "b.h"]


def test_depfile_args_strips_compiler_and_output() -> None:
    # A compile-DB argv begins with the compiler exe and carries -c/-o; re-driving
    # it under `clang -MM` must drop those so the source + -I/-D/-std survive
    # (Codex review): without this the second compiler token is read as input.
    argv = ["clang++", "-c", "src/foo.cpp", "-o", "foo.o",
            "-I", "include", "-DFOO=1", "-std=c++17", "-MF", "foo.d"]
    assert depfile_args_from_argv(argv) == [
        "src/foo.cpp", "-I", "include", "-DFOO=1", "-std=c++17",
    ]


def test_depfile_args_strips_compiler_launcher() -> None:
    # A ccache/sccache-wrapped command must drop BOTH the launcher and the real
    # compiler token, else `clang++ -MM ccache clang++ …` reads them as inputs
    # (Codex review).
    assert depfile_args_from_argv(
        ["ccache", "clang++", "-c", "foo.cpp", "-I", "x"]
    ) == ["foo.cpp", "-I", "x"]
    assert depfile_args_from_argv(
        ["sccache", "g++", "-c", "a.cpp", "-std=c++20"]
    ) == ["a.cpp", "-std=c++20"]


def test_depfile_args_handles_glued_output_and_argv0_flag() -> None:
    # Glued -ofoo.o is dropped; an argv that already starts with a flag (no
    # leading compiler token) keeps every flag.
    assert depfile_args_from_argv(["cc", "-ofoo.o", "foo.c", "-I."]) == ["foo.c", "-I."]
    # GCC long --output=foo.o glued spelling is dropped too (Codex review).
    assert depfile_args_from_argv(
        ["g++", "--output=foo.o", "foo.cpp", "-Iinc"]
    ) == ["foo.cpp", "-Iinc"]
    assert depfile_args_from_argv(["-Iinc", "foo.c"]) == ["-Iinc", "foo.c"]
    assert depfile_args_from_argv([]) == []


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

    monkeypatch.setattr(ig.subprocess, "run", lambda *_a, **_k: _Proc())
    build = BuildEvidence(compile_units=[
        CompileUnit(id="cu://foo", source="foo.cpp", argv=["foo.cpp"]),
        CompileUnit(id="cu://nosrc", source=""),  # skipped
    ])
    includes = ClangIncludeExtractor().extract_from_build(build)
    assert includes == {"cu://foo": ["foo.cpp", "inc/foo.h"]}


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


def test_extract_from_build_unredacts_home(monkeypatch) -> None:
    # argv/cwd persist with the home dir redacted to `~`; the depfile pass must
    # un-redact them before subprocess, which does not expand `~` (Codex review).
    import abicheck.evidence.include_graph as ig

    captured: dict = {}

    class _Result:
        stdout = "foo.o: foo.cpp a.h"
        stderr = ""

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        return _Result()

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    monkeypatch.setattr(ig.subprocess, "run", _fake_run)

    cu = CompileUnit(
        id="cu://a", source="~/proj/foo.cpp", directory="~/proj",
        argv=["clang++", "-c", "~/proj/foo.cpp", "-I", "~/proj/include"],
    )
    out = ig.ClangIncludeExtractor().extract_from_build(BuildEvidence(compile_units=[cu]))
    assert out == {"cu://a": ["foo.cpp", "a.h"]}
    assert not any("~" in str(tok) for tok in captured["cmd"])
    assert "~" not in (captured["cwd"] or "")


def test_lang_flag_preserves_language() -> None:
    from abicheck.evidence.include_graph import _lang_flag
    assert _lang_flag("C") == ["-x", "c"]
    assert _lang_flag("CXX") == ["-x", "c++"]
    assert _lang_flag("C++") == ["-x", "c++"]
    assert _lang_flag("") == []


def test_extract_uses_dash_m_and_preserves_c_language(monkeypatch) -> None:
    # -M (not -MM) so system-classified public headers appear; -x c so a C unit
    # replayed through clang++ is parsed as C (Codex review).
    import abicheck.evidence.include_graph as ig

    captured: dict = {}

    class _R:
        stdout = "foo.o: foo.c sys.h"
        stderr = ""

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    monkeypatch.setattr(ig.subprocess, "run",
                        lambda cmd, **kw: (captured.update(cmd=cmd) or _R()))
    cu = CompileUnit(id="cu://c", source="foo.c", language="C", argv=["cc", "-c", "foo.c"])
    out = ClangIncludeExtractor().extract_from_build(BuildEvidence(compile_units=[cu]))
    assert out == {"cu://c": ["foo.c", "sys.h"]}
    assert "-M" in captured["cmd"] and "-MM" not in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("-x") + 1] == "c"
