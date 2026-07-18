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

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.include_graph import (
    ClangIncludeExtractor,
    augment_graph_with_includes,
    depfile_args_from_argv,
    include_map_from_recorded_inputs,
    parse_depfile,
)
from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary


def test_parse_depfile_basic() -> None:
    assert parse_depfile("foo.o: foo.cpp a.h b.h") == ["foo.cpp", "a.h", "b.h"]


def test_depfile_args_strips_compiler_and_output() -> None:
    # A compile-DB argv begins with the compiler exe and carries -c/-o; re-driving
    # it under `clang -MM` must drop those so the source + -I/-D/-std survive
    # (Codex review): without this the second compiler token is read as input.
    argv = [
        "clang++",
        "-c",
        "src/foo.cpp",
        "-o",
        "foo.o",
        "-I",
        "include",
        "-DFOO=1",
        "-std=c++17",
        "-MF",
        "foo.d",
    ]
    assert depfile_args_from_argv(argv) == [
        "src/foo.cpp",
        "-I",
        "include",
        "-DFOO=1",
        "-std=c++17",
    ]


def test_depfile_args_strips_compiler_launcher() -> None:
    # A ccache/sccache-wrapped command must drop BOTH the launcher and the real
    # compiler token, else `clang++ -MM ccache clang++ …` reads them as inputs
    # (Codex review).
    assert depfile_args_from_argv(
        ["ccache", "clang++", "-c", "foo.cpp", "-I", "x"]
    ) == ["foo.cpp", "-I", "x"]
    assert depfile_args_from_argv(["sccache", "g++", "-c", "a.cpp", "-std=c++20"]) == [
        "a.cpp",
        "-std=c++20",
    ]


def test_depfile_args_handles_glued_output_and_argv0_flag() -> None:
    # Glued -ofoo.o is dropped; an argv that already starts with a flag (no
    # leading compiler token) keeps every flag.
    assert depfile_args_from_argv(["cc", "-ofoo.o", "foo.c", "-I."]) == ["foo.c", "-I."]
    # GCC long --output=foo.o glued spelling is dropped too (Codex review).
    assert depfile_args_from_argv(["g++", "--output=foo.o", "foo.cpp", "-Iinc"]) == [
        "foo.cpp",
        "-Iinc",
    ]
    assert depfile_args_from_argv(["-Iinc", "foo.c"]) == ["-Iinc", "foo.c"]
    assert depfile_args_from_argv([]) == []


def test_depfile_args_preserves_safe_include_context_flags() -> None:
    assert depfile_args_from_argv(
        [
            "clang++",
            "-c",
            "foo.cpp",
            "-include",
            "forced.h",
            "-imacros",
            "config.macros",
            "-include-pch",
            "pch.pch",
            "-iquote",
            "quoted",
            "-idirafter",
            "after",
            "-I",
            "include",
        ]
    ) == [
        "foo.cpp",
        "-include",
        "forced.h",
        "-imacros",
        "config.macros",
        "-include-pch",
        "pch.pch",
        "-iquote",
        "quoted",
        "-idirafter",
        "after",
        "-I",
        "include",
    ]


def test_depfile_args_strips_warning_only_flags() -> None:
    # Bazel/GCC actions can record warning flags that make clang depfile replay
    # fail (`-Werror` + GCC-only diagnostics options), but they do not affect the
    # include closure. Keep the preprocessor context and source.
    assert depfile_args_from_argv(
        [
            "g++",
            "-c",
            "foo.cpp",
            "-Werror",
            "-Wformat-security",
            "-fdiagnostics-color=always",
            "-fno-canonical-system-headers",
            "-DABI=1",
            "-Iinclude",
        ]
    ) == ["foo.cpp", "-DABI=1", "-Iinclude"]


def test_depfile_args_preserves_preprocessor_escape_hatch() -> None:
    assert depfile_args_from_argv(
        [
            "g++",
            "-c",
            "foo.cpp",
            "-Wp,-Igenerated",
            "-Wp,-DPLATFORM=1",
            "-Werror",
            "-Iinclude",
        ]
    ) == ["foo.cpp", "-Wp,-Igenerated", "-Wp,-DPLATFORM=1", "-Iinclude"]


def test_depfile_args_strips_output_side_effect_options() -> None:
    # Compile DB argv is untrusted: do not forward clang options that can write
    # files during the S2 preprocessor/include replay (for example time traces).
    assert depfile_args_from_argv(
        [
            "clang++",
            "-c",
            "foo.cpp",
            "-I",
            "include",
            "-ftime-trace=/attack/victim.json",
            "-ftime-trace",
            "/attack/victim2.json",
            "-serialize-diagnostic-file=/attack/diag",
            "-fmodules-cache-path",
            "/attack/cache",
            "-save-temps",
            "--save-temps=obj",
            "-MJ/attack/compile.json",
        ]
    ) == ["foo.cpp", "-I", "include"]


def test_depfile_args_strips_clang_plugin_loading_options() -> None:
    # compile_commands.json is untrusted input for source-ABI replay.  The
    # depfile pass must not forward Clang escape hatches that load plugins or
    # LLVM passes while preserving the source and ordinary preprocessor context.
    assert depfile_args_from_argv(
        [
            "clang++",
            "-c",
            "foo.cpp",
            "-I",
            "include",
            "-Xclang",
            "-load",
            "-Xclang",
            "./evil.so",
            "-fplugin=./plugin.so",
            "-fpass-plugin=./pass.so",
            "-mllvm",
            "-load=./legacy-pass.so",
            "-mllvm=-load=./joined-pass.so",
            "@/tmp/args.rsp",
            "--config",
            "evil.cfg",
            "--config=evil.cfg",
        ]
    ) == ["foo.cpp", "-I", "include"]
    assert depfile_args_from_argv(
        [
            "clang++",
            "-cc1",
            "-load",
            "./evil.so",
            "foo.cpp",
            "-DABI=1",
        ]
    ) == ["foo.cpp", "-DABI=1"]


def test_parse_depfile_line_continuations() -> None:
    text = "foo.o: foo.cpp \\\n  inc/a.h \\\n  inc/b.h\n"
    assert parse_depfile(text) == ["foo.cpp", "inc/a.h", "inc/b.h"]


def test_parse_depfile_dedupes_and_skips_no_colon() -> None:
    text = "garbage line\nfoo.o: a.h a.h b.h"
    assert parse_depfile(text) == ["a.h", "b.h"]


def test_parse_depfile_windows_drive_letter_target() -> None:
    # The drive-letter colon must not be mistaken for the rule separator.
    assert parse_depfile(r"C:\build\foo.o: C:\src\foo.cpp inc\a.h") == [
        r"C:\src\foo.cpp",
        r"inc\a.h",
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


def test_include_map_from_recorded_inputs() -> None:
    build = BuildEvidence(
        compile_units=[
            CompileUnit(
                id="cu://foo",
                source="foo.cpp",
                input_files=["foo.cpp", "include/foo.h"],
            ),
            CompileUnit(id="cu://bar", source="bar.cpp"),
        ]
    )
    assert include_map_from_recorded_inputs(build) == {
        "cu://foo": ["foo.cpp", "include/foo.h"],
    }


def test_extractor_missing_clang_returns_empty() -> None:
    ext = ClangIncludeExtractor(clang_bin="definitely-not-clang-xyz")
    assert ext.available() is False
    assert (
        ext.extract_from_build(
            BuildEvidence(compile_units=[CompileUnit(id="cu://x", source="x.cpp")])
        )
        == {}
    )
    assert ext.diagnostics


def test_extractor_parses_mocked_clang(monkeypatch) -> None:
    import abicheck.buildsource.include_graph as ig

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")

    class _Proc:
        stdout = "foo.o: foo.cpp inc/foo.h"
        stderr = ""

    seen = {}

    def _run(cmd, **_kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(ig.deadline, "run_bounded", _run)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(
                id="cu://foo",
                source="foo.cpp",
                argv=[
                    "/usr/bin/c++",
                    "-fplugin=/tmp/evil.so",
                    "foo.cpp",
                ],
            ),
            CompileUnit(id="cu://nosrc", source=""),  # skipped
        ]
    )
    includes = ClangIncludeExtractor().extract_from_build(build)
    assert includes == {"cu://foo": ["foo.cpp", "inc/foo.h"]}
    assert seen["cmd"] == ["clang++", "-M", "foo.cpp"]
    assert "-fplugin=/tmp/evil.so" not in seen["cmd"]


def test_extractor_handles_subprocess_error(monkeypatch) -> None:
    import abicheck.buildsource.include_graph as ig

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")

    def _boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(ig.deadline, "run_bounded", _boom)
    build = BuildEvidence(compile_units=[CompileUnit(id="cu://foo", source="foo.cpp")])
    assert ClangIncludeExtractor().extract_from_build(build) == {}


def test_extractor_is_bound_by_active_scan_deadline(monkeypatch) -> None:
    """Codex review (PR #591): this had the identical bare-subprocess.run
    anti-pattern already fixed for call_graph.py/type_graph.py — a
    `--budget`-bound scan deadline was never consulted, only this
    extractor's own local aggregate_timeout_s/per_unit_timeout_s. Must go
    through deadline.run_bounded and stop the loop (not just log and keep
    going) once the active scan deadline is exhausted.

    Round-3 note: the DeadlineExceeded here must be attributable to the
    OUTER scan deadline (not just this extractor's own local cap) to
    correctly stop the loop -- an active deadline_scope tighter than the
    local per-unit/aggregate cap makes that the case (Codex review, PR
    #591, round 3)."""
    import abicheck.buildsource.include_graph as ig
    from abicheck import deadline

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")

    def _raise(*_a, **_k):
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(ig.deadline, "run_bounded", _raise)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
        ]
    )
    ext = ClangIncludeExtractor()
    with deadline.deadline_scope(5.0):  # tighter than the 120s per-unit cap
        out = ext.extract_from_build(build)
    assert out == {}
    assert any("scan deadline exceeded" in d for d in ext.diagnostics)


def test_extractor_local_cap_timeout_does_not_abort_remaining_compile_units(
    monkeypatch,
) -> None:
    """Codex review (PR #591), round 3: when the extractor's OWN local
    per-unit/aggregate cap is what fires (no active --budget, or one with
    plenty left), that's an ordinary per-CU timeout -- not a scan-budget
    overflow. Must degrade to a per-CU diagnostic and keep probing later
    compile units, not discard include maps for all of them."""
    import abicheck.buildsource.include_graph as ig
    from abicheck import deadline

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    calls: list[str] = []

    def _fake_run(cmd, **_kwargs):
        calls.append(cmd[-1])
        if cmd[-1] == "a.cpp":
            raise deadline.DeadlineExceeded(-1.0)

        class _R:
            stdout = "foo.o: foo.cpp inc/foo.h"
            stderr = ""

        return _R()

    monkeypatch.setattr(ig.deadline, "run_bounded", _fake_run)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
        ]
    )
    ext = ClangIncludeExtractor()
    # No active outer --budget at all: the local cap is unambiguously what
    # bound the nested scope.
    out = ext.extract_from_build(build)
    assert calls == ["a.cpp", "b.cpp"]  # b.cpp still probed after a.cpp's timeout
    assert out == {"cu://b": ["foo.cpp", "inc/foo.h"]}
    assert any("clang -M timed out for cu://a" in d for d in ext.diagnostics)


def test_extractor_local_cap_binding_at_entry_but_outer_deadline_drains_during_escalation_aborts(
    monkeypatch,
) -> None:
    """Codex review (PR #591, round 3): run_bounded's own escalation
    (SIGTERM -> grace -> SIGKILL, plus a fixed 5s pipe-drain) can push real
    elapsed time past what scan_remaining showed when this extractor
    decided whether its own local cap or the outer scan deadline was
    binding. A call correctly classified 'local-cap-only' at entry must
    still abort the loop if the outer deadline is exhausted by the time
    the except clause runs -- trusting a stale entry-time snapshot alone
    would misreport a genuine budget overflow as an ordinary per-CU
    timeout."""
    import abicheck.buildsource.include_graph as ig
    from abicheck import deadline

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    clock = {"t": 1000.0}
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock["t"])

    def _fake_run(cmd, **_kwargs):
        clock["t"] += 40.0  # simulate run_bounded's real escalation cost
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(ig.deadline, "run_bounded", _fake_run)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
        ]
    )
    ext = ClangIncludeExtractor()
    with deadline.deadline_scope(35.0):  # just over the 30s local aggregate cap
        out = ext.extract_from_build(build)
    assert out == {}
    assert any("scan deadline exceeded" in d for d in ext.diagnostics)


def test_collect_evidence_include_graph_missing_clang_degrades(
    tmp_path, monkeypatch
) -> None:
    # Include-graph folding is automatic whenever L4 source-abi replay and the
    # L5 graph are both collected (`--depth source` on the current `dump
    # --sources` public surface; the deleted `collect --source-abi
    # --source-graph summary` combo used to exercise the identical
    # cli_buildsource_helpers._collect_source_graph -> inline_graph_fold path).
    # A missing clang records a failed extractor row but still writes the
    # pack with the build graph.
    import json

    from click.testing import CliRunner

    import abicheck.buildsource.include_graph as ig
    from abicheck.cli import main
    from abicheck.serialization import load_snapshot

    monkeypatch.setattr(ig.shutil, "which", lambda _b: None)
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "foo.cpp").write_text("int foo(){return 1;}\n")
    (tree / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tree),
                    "file": "foo.cpp",
                    "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
                }
            ]
        )
    )
    out = tmp_path / "ev.json"
    res = CliRunner().invoke(
        main,
        [
            "dump",
            "--sources",
            str(tree),
            "--depth",
            "source",
            "-o",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    bs = load_snapshot(out).build_source
    assert bs is not None and bs.source_graph is not None
    assert any(
        e.name == "include_graph:clang" and e.status == "failed"
        for e in bs.manifest.extractors
    )


def test_extract_from_build_unredacts_home(monkeypatch) -> None:
    # argv/cwd persist with the home dir redacted to `~`; the depfile pass must
    # un-redact them before subprocess, which does not expand `~` (Codex review).
    import abicheck.buildsource.include_graph as ig

    captured: dict = {}

    class _Result:
        stdout = "foo.o: foo.cpp a.h"
        stderr = ""

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        return _Result()

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    monkeypatch.setattr(ig.deadline, "run_bounded", _fake_run)

    cu = CompileUnit(
        id="cu://a",
        source="~/proj/foo.cpp",
        directory="~/proj",
        argv=["clang++", "-c", "~/proj/foo.cpp", "-I", "~/proj/include"],
    )
    out = ig.ClangIncludeExtractor().extract_from_build(
        BuildEvidence(compile_units=[cu])
    )
    assert out == {"cu://a": ["foo.cpp", "a.h"]}
    assert not any("~" in str(tok) for tok in captured["cmd"])
    assert "~" not in (captured["cwd"] or "")


def test_lang_flag_preserves_language() -> None:
    from abicheck.buildsource.include_graph import _lang_flag

    assert _lang_flag("C") == ["-x", "c"]
    assert _lang_flag("CXX") == ["-x", "c++"]
    assert _lang_flag("C++") == ["-x", "c++"]
    assert _lang_flag("") == []


def test_extract_uses_dash_m_and_preserves_c_language(monkeypatch) -> None:
    # -M (not -MM) so system-classified public headers appear; -x c so a C unit
    # replayed through clang++ is parsed as C (Codex review).
    import abicheck.buildsource.include_graph as ig

    captured: dict = {}

    class _R:
        stdout = "foo.o: foo.c sys.h"
        stderr = ""

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    monkeypatch.setattr(
        ig.deadline, "run_bounded", lambda cmd, **kw: captured.update(cmd=cmd) or _R()
    )
    cu = CompileUnit(
        id="cu://c", source="foo.c", language="C", argv=["cc", "-c", "foo.c"]
    )
    out = ClangIncludeExtractor().extract_from_build(BuildEvidence(compile_units=[cu]))
    assert out == {"cu://c": ["foo.c", "sys.h"]}
    assert "-M" in captured["cmd"] and "-MM" not in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("-x") + 1] == "c"


def test_extract_from_build_caps_compile_units(monkeypatch) -> None:
    import abicheck.buildsource.include_graph as ig

    calls = []

    class _R:
        stdout = "foo.o: foo.cpp inc/foo.h"
        stderr = ""

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        return _R()

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    monkeypatch.setattr(ig.deadline, "run_bounded", _fake_run)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id=f"cu://{i}", source=f"foo{i}.cpp") for i in range(3)
        ]
    )

    ext = ClangIncludeExtractor(max_compile_units=2)
    out = ext.extract_from_build(build)

    assert len(calls) == 2
    assert set(out) == {"cu://0", "cu://1"}
    assert any("budget exhausted" in d for d in ext.diagnostics)


def test_extract_from_build_enforces_aggregate_timeout(monkeypatch) -> None:
    import abicheck.buildsource.include_graph as ig

    calls = []
    now = {"value": 0.0}

    class _R:
        stdout = "foo.o: foo.cpp inc/foo.h"
        stderr = ""

    def _fake_run(cmd, **kw):
        calls.append((cmd, kw["timeout"]))
        now["value"] += 2.0
        return _R()

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    monkeypatch.setattr(ig.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(ig.deadline, "run_bounded", _fake_run)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id=f"cu://{i}", source=f"foo{i}.cpp") for i in range(3)
        ]
    )

    ext = ClangIncludeExtractor(aggregate_timeout_s=1.0)
    out = ext.extract_from_build(build)

    assert len(calls) == 1
    assert calls[0][1] == 1.0
    assert out == {"cu://0": ["foo.cpp", "inc/foo.h"]}
    assert any("time budget exhausted" in d for d in ext.diagnostics)


def test_extractor_call_bounded_by_local_cap_not_full_scan_budget(monkeypatch) -> None:
    """Codex review (PR #591), round 2: deadline.run_bounded() honors an
    active outer deadline verbatim -- not min(timeout, left) -- so passing
    timeout=min(local_cap, local_remaining) alone did nothing once a scan
    deadline was active: the call would still be bounded by the FULL
    remaining scan budget instead of this extractor's own per-unit/aggregate
    ceiling. A hung depfile replay under a generous `--budget 30m` could
    therefore eat the whole scan instead of stopping at ~aggregate_timeout_s.
    The fix nests a narrower deadline.deadline_scope() around each call;
    assert the ContextVar deadline observed by the call is bound to the
    tight local cap, not the much larger outer scan budget."""
    import abicheck.buildsource.include_graph as ig
    from abicheck import deadline

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    seen_remaining: list[float | None] = []

    def fake_run(*_a, **_k):
        seen_remaining.append(deadline.remaining())

        class _R:
            stdout = "foo.o: foo.cpp inc/foo.h"
            stderr = ""

        return _R()

    monkeypatch.setattr(ig.deadline, "run_bounded", fake_run)
    ext = ClangIncludeExtractor(aggregate_timeout_s=5.0, per_unit_timeout_s=5.0)
    build = BuildEvidence(compile_units=[CompileUnit(id="cu://a", source="a.cpp")])
    with deadline.deadline_scope(1800.0):  # a generous 30-minute --budget
        ext.extract_from_build(build)

    assert seen_remaining
    # Bound by the extractor's own ~5s local cap, not the 1800s scan budget.
    assert seen_remaining[0] is not None and seen_remaining[0] <= 5.5
