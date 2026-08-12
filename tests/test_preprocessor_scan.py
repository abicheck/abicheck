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

"""Tests for the ADR-035 D2 S2 preprocessor pre-scan (G19.1 conditional tier).

Pure tests over the macro/include analysis core — no compiler. The live
``clang -E`` extractor is integration-only and exercised elsewhere; here we
cover parsing, ABI-macro selection, divergence, include classification, leak
detection, and the honest coverage-skip when L3/clang are absent.
"""

from __future__ import annotations

from abicheck.buildsource.preprocessor_scan import (
    IncludeClass,
    classify_include,
    find_macro_divergence,
    find_private_header_leaks,
    is_abi_macro,
    parse_defined_macros,
    run_preprocessor_scan,
    select_abi_macros,
)

# ── macro parsing / selection ────────────────────────────────────────────────


def test_parse_defined_macros_skips_function_like() -> None:
    text = (
        "#define NDEBUG 1\n"
        "#define _GLIBCXX_USE_CXX11_ABI 0\n"
        "#define MAX(a, b) ((a) > (b) ? (a) : (b))\n"
        "#define EMPTY\n"
    )
    defs = parse_defined_macros(text)
    assert defs == {"NDEBUG": "1", "_GLIBCXX_USE_CXX11_ABI": "0", "EMPTY": ""}


def test_parse_defined_macros_compact_function_like_no_space_after_paren() -> None:
    # Codex review, fresh evidence: a real, valid compact function-like
    # definition with no space after `)` must still be recognized by
    # _DEFINE_RE (even though its replacement text is still discarded here
    # -- function-like macros have no single ABI value to diff).
    text = "#define ATTR(x)__attribute__((x))\n#define NDEBUG 1\n"
    defs = parse_defined_macros(text)
    assert defs == {"NDEBUG": "1"}
    assert "ATTR" not in defs


def test_is_abi_macro_name_and_prefix() -> None:
    assert is_abi_macro("NDEBUG")
    assert is_abi_macro("_GLIBCXX_USE_CXX11_ABI")  # prefix family
    assert is_abi_macro("_LIBCPP_ABI_VERSION")
    assert not is_abi_macro("SOME_RANDOM_FLAG")


def test_select_abi_macros_filters() -> None:
    defs = {"NDEBUG": "1", "FOO": "2", "_ITERATOR_DEBUG_LEVEL": "2"}
    assert select_abi_macros(defs) == {"NDEBUG": "1", "_ITERATOR_DEBUG_LEVEL": "2"}


# ── divergence ───────────────────────────────────────────────────────────────


def test_find_macro_divergence_flags_conflicting_values() -> None:
    per_tu = {
        "cu://a": {"_GLIBCXX_USE_CXX11_ABI": "0"},
        "cu://b": {"_GLIBCXX_USE_CXX11_ABI": "1"},
    }
    div = find_macro_divergence(per_tu)
    assert len(div) == 1
    assert div[0].macro == "_GLIBCXX_USE_CXX11_ABI"
    assert set(div[0].values) == {"0", "1"}


def test_no_divergence_when_macro_consistent() -> None:
    per_tu = {
        "cu://a": {"NDEBUG": "1"},
        "cu://b": {"NDEBUG": "1"},
    }
    assert find_macro_divergence(per_tu) == []


def test_divergence_ignores_non_abi_macros() -> None:
    per_tu = {"cu://a": {"FOO": "1"}, "cu://b": {"FOO": "2"}}
    assert find_macro_divergence(per_tu) == []


def test_divergence_single_tu_is_not_a_conflict() -> None:
    per_tu = {"cu://a": {"NDEBUG": "1"}}
    assert find_macro_divergence(per_tu) == []


# ── include classification ───────────────────────────────────────────────────


def test_classify_public_wins_on_known_set() -> None:
    assert (
        classify_include("include/foo.h", frozenset({"include/foo.h"}))
        is IncludeClass.PUBLIC
    )


def test_classify_private_segment_and_suffix() -> None:
    assert classify_include("src/detail/impl.h") is IncludeClass.PRIVATE
    assert classify_include("lib/foo_p.h") is IncludeClass.PRIVATE


def test_classify_generated() -> None:
    assert classify_include("build/generated/config.h") is IncludeClass.GENERATED
    assert classify_include("out/foo_config.h") is IncludeClass.GENERATED


def test_classify_system_never_a_leak() -> None:
    assert classify_include("/usr/include/c++/13/vector") is IncludeClass.SYSTEM
    # A libstdc++ "detail"-ish system path stays SYSTEM, not PRIVATE.
    assert classify_include("/usr/include/foo/detail/x.h") is IncludeClass.SYSTEM


def test_classify_unknown() -> None:
    assert classify_include("weird/place/thing.h") is IncludeClass.UNKNOWN


# ── leak detection ───────────────────────────────────────────────────────────


def test_find_private_header_leaks() -> None:
    includes = {
        "include/foo.h": [
            "include/foo.h",  # self — ignored
            "src/detail/impl.h",  # private leak
            "/usr/include/vector",  # system — fine
            "build/config.h",  # generated leak
        ]
    }
    leaks = find_private_header_leaks(includes, frozenset({"include/foo.h"}))
    pairs = {(leak.leaked_header, leak.leak_class) for leak in leaks}
    assert ("src/detail/impl.h", IncludeClass.PRIVATE) in pairs
    assert ("build/config.h", IncludeClass.GENERATED) in pairs
    assert len(leaks) == 2


def test_no_leak_when_only_public_and_system() -> None:
    includes = {"include/foo.h": ["include/bar.h", "/usr/include/string"]}
    leaks = find_private_header_leaks(
        includes, frozenset({"include/foo.h", "include/bar.h"})
    )
    assert leaks == []


# ── orchestrator coverage honesty ────────────────────────────────────────────


def test_run_skips_without_build_evidence() -> None:
    result = run_preprocessor_scan(None, ["include/foo.h"])
    assert result.ran is False
    assert "no L3 build evidence" in result.skipped_reason
    assert result.coverage().status.value == "not_collected"


def test_classify_basename_match_only_for_basename_only_public_input() -> None:
    # A public include/config.h must NOT shadow a generated build/config.h by
    # basename — else the leak this pass exists to report is missed (Codex).
    assert (
        classify_include("build/config.h", frozenset({"include/config.h"}))
        is IncludeClass.GENERATED
    )
    # But a basename-only public input (no path) still matches by basename.
    assert (
        classify_include("build/config.h", frozenset({"config.h"}))
        is IncludeClass.PUBLIC
    )


def test_capture_header_includes_makes_header_absolute(monkeypatch) -> None:
    # The -I context is relative to the build dir (cwd), so the header path must
    # be absolute or clang looks for it under the build dir (Codex review).
    from abicheck.buildsource import preprocessor_scan as ps

    captured: dict[str, object] = {}

    def _fake_run(self, cmd, cwd, unit):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return "foo.o: include/foo.h src/detail/impl.h\n"

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fake_run)
    ex = ps.ClangPreprocessorExtractor()
    out = ex.capture_header_includes(["include/foo.h"], ["-Iinc"], cwd="/work/build")
    assert out  # parsed includes returned
    header_arg = captured["cmd"][-1]  # type: ignore[index]
    import os as _os

    assert _os.path.isabs(header_arg)
    assert captured["cwd"] == "/work/build"


def test_run_skips_when_clang_absent(monkeypatch) -> None:
    from abicheck.buildsource import build_evidence as be, preprocessor_scan as ps

    build = be.BuildEvidence(
        compile_units=[be.CompileUnit(id="cu://a", source="a.cpp", language="CXX")]
    )
    # Force the extractor to report clang unavailable.
    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "available", lambda self: False)
    result = run_preprocessor_scan(build, ["include/foo.h"], clang_bin="clang++")
    assert result.ran is False
    assert "not found" in result.skipped_reason


def test_coverage_downgraded_when_all_clang_runs_fail(monkeypatch) -> None:
    # clang present but every invocation fails → nothing inspected; the coverage
    # row must NOT read as a clean PRESENT scan (Codex review).
    from abicheck.buildsource import build_evidence as be, preprocessor_scan as ps

    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id="cu://a", source="a.cpp", language="CXX", argv=["c++", "a.cpp"]
            )
        ]
    )
    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "available", lambda self: True)

    def _fail_run(self, cmd, cwd, unit):
        self.runs_attempted += 1
        self.diagnostics.append(f"clang -E nonzero exit for {unit}: boom")
        return None

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fail_run)
    result = ps.run_preprocessor_scan(build, ["include/foo.h"])
    assert result.ran is True
    assert result.all_failed is True
    assert result.attempted > 0 and result.succeeded == 0
    assert result.coverage().status.value == "not_collected"


def test_coverage_partial_when_some_clang_runs_fail(monkeypatch) -> None:
    from abicheck.buildsource import build_evidence as be, preprocessor_scan as ps

    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id="cu://a", source="a.cpp", language="CXX", argv=["c++", "a.cpp"]
            )
        ]
    )
    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "available", lambda self: True)
    calls = {"n": 0}

    def _mixed_run(self, cmd, cwd, unit):
        self.runs_attempted += 1
        calls["n"] += 1
        if calls["n"] == 1:  # macro capture succeeds
            self.runs_ok += 1
            return "#define NDEBUG 1\n"
        self.diagnostics.append("header run failed")  # header capture fails
        return None

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _mixed_run)
    result = ps.run_preprocessor_scan(build, ["include/foo.h"])
    assert result.coverage().status.value == "partial"


# ── P0 follow-up: deadline-bounded, process-group-safe subprocess ───────────


def test_run_uses_deadline_bounded_not_raw_subprocess(monkeypatch) -> None:
    # Same fix family as the L2 header-AST subprocess (abicheck/deadline.py):
    # ClangPreprocessorExtractor._run must go through deadline.run_bounded
    # (shrinking --budget deadline + process-group kill on timeout), not a
    # bare subprocess.run(timeout=120) with no process-group isolation.
    from abicheck import deadline
    from abicheck.buildsource import preprocessor_scan as ps

    class _P:
        stdout = "#define NDEBUG 1\n"
        stderr = ""
        returncode = 0

    seen: dict[str, object] = {}

    def _fake_run_bounded(cmd, **kwargs):
        seen.update(kwargs)
        return _P()

    monkeypatch.setattr(deadline, "run_bounded", _fake_run_bounded)
    ex = ps.ClangPreprocessorExtractor()
    text = ex._run(["clang++", "-E", "-dM", "a.cpp"], None, "cu://a")
    assert text == "#define NDEBUG 1\n"
    assert seen.get("timeout") == 120
    assert ex.runs_ok == 1


def test_run_nests_local_cap_deadline_scope_not_full_scan_budget(monkeypatch) -> None:
    # Codex review (PR #591, round 5): deadline.run_bounded() honors an
    # active outer deadline verbatim (not min(timeout, left)), so a bare
    # timeout=120 alone did nothing once a generous --budget was active --
    # a hung clang -E/-M could consume the whole remaining scan budget
    # instead of this pre-scan's own 120s per-unit cap. Must nest a
    # narrower deadline_scope bound by whichever is tighter.
    from abicheck import deadline
    from abicheck.buildsource import preprocessor_scan as ps

    class _P:
        stdout = "#define NDEBUG 1\n"
        stderr = ""
        returncode = 0

    seen_remaining: list[float | None] = []

    def _fake_run_bounded(cmd, **kwargs):
        seen_remaining.append(deadline.remaining())
        return _P()

    monkeypatch.setattr(deadline, "run_bounded", _fake_run_bounded)
    ex = ps.ClangPreprocessorExtractor()
    with deadline.deadline_scope(1800.0):  # generous 30-minute --budget
        ex._run(["clang++", "-E", "-dM", "a.cpp"], None, "cu://a")
    assert seen_remaining
    assert seen_remaining[0] is not None and seen_remaining[0] <= 120.5


def test_run_local_cap_timeout_with_generous_budget_is_ordinary_per_unit_failure(
    monkeypatch,
) -> None:
    # Codex review (PR #591, round 5): hitting this pre-scan's OWN 120s
    # per-unit cap under a generous outer --budget must degrade to an
    # ordinary per-unit diagnostic -- not scan-budget exhaustion, which
    # would wrongly stop processing every remaining compile unit too.
    from abicheck import deadline
    from abicheck.buildsource import preprocessor_scan as ps

    def _raise(cmd, **kwargs):
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(deadline, "run_bounded", _raise)
    ex = ps.ClangPreprocessorExtractor()
    with deadline.deadline_scope(1800.0):  # generous 30-minute --budget
        text = ex._run(["clang++", "-E", "-dM", "a.cpp"], None, "cu://a")
    assert text is None
    assert ex.deadline_exhausted is False
    assert any("timed out" in d for d in ex.diagnostics)


def test_deadline_exceeded_degrades_to_diagnostic_not_crash(monkeypatch) -> None:
    # Unlike the L2 header path (authoritative evidence, must abort the scan),
    # this pre-scan is advisory (ADR-028 D3): a --budget deadline expiring
    # mid-preprocess must degrade to a diagnostic + skipped unit, never
    # propagate and crash the whole `scan` command.
    #
    # Round-5 note: DeadlineExceeded is attributed to the OUTER scan deadline
    # (not just this pre-scan's own 120s per-unit cap) only when an active
    # deadline_scope tighter than 120s makes that the case (Codex review,
    # PR #591, round 5, mirrors the include-map/build-query classification
    # fix).
    from abicheck import deadline
    from abicheck.buildsource import preprocessor_scan as ps

    def _raise(cmd, **kwargs):
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(deadline, "run_bounded", _raise)
    ex = ps.ClangPreprocessorExtractor()
    with deadline.deadline_scope(5.0):  # tighter than the 120s per-unit cap
        text = ex._run(["clang++", "-E", "-dM", "a.cpp"], None, "cu://a")
    assert text is None
    assert ex.deadline_exhausted is True
    assert any("budget" in d.lower() for d in ex.diagnostics)


def test_capture_macros_stops_after_deadline_exhausted(monkeypatch) -> None:
    # Once the budget is gone, capture_macros must stop probing remaining
    # compile contexts rather than calling _run() (and hitting the same
    # DeadlineExceeded) for every one of them. Each unit here shares the
    # same (language, cwd, flags) signature — an empty argv after the
    # source token is stripped — so they dedup into a single probe anyway;
    # forcing serial jobs (ABICHECK_PREPROCESSOR_SCAN_JOBS=1) keeps the
    # assertion deterministic regardless of that dedup detail.
    from abicheck import deadline
    from abicheck.buildsource import build_evidence as be, preprocessor_scan as ps

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id=f"cu://{i}",
                source=f"{i}.cpp",
                language="CXX",
                argv=["c++", f"{i}.cpp"],
            )
            for i in range(5)
        ]
    )
    calls = {"n": 0}

    def _raise_once(cmd, **kwargs):
        calls["n"] += 1
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(deadline, "run_bounded", _raise_once)
    ex = ps.ClangPreprocessorExtractor()
    with deadline.deadline_scope(5.0):  # tighter than the 120s per-unit cap
        out = ex.capture_macros(build)
    assert out == {}
    assert calls["n"] == 1, (
        f"expected exactly one attempt before the loop stopped, got {calls['n']}"
    )


def test_capture_header_includes_stops_after_deadline_exhausted(monkeypatch) -> None:
    # Same stop-early contract as capture_macros, for the sibling per-header
    # -M depfile pass. Forced serial (ABICHECK_PREPROCESSOR_SCAN_JOBS=1) so
    # the "exactly one attempt" assertion isn't racing a thread pool.
    from abicheck import deadline
    from abicheck.buildsource import preprocessor_scan as ps

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    calls = {"n": 0}

    def _raise_once(cmd, **kwargs):
        calls["n"] += 1
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(deadline, "run_bounded", _raise_once)
    ex = ps.ClangPreprocessorExtractor()
    with deadline.deadline_scope(5.0):  # tighter than the 120s per-unit cap
        out = ex.capture_header_includes(
            ["include/a.h", "include/b.h", "include/c.h"], ["-Iinclude"]
        )
    assert out == {}
    assert calls["n"] == 1, (
        f"expected exactly one attempt before the loop stopped, got {calls['n']}"
    )


def test_run_passes_compile_unit_directory_as_cwd(monkeypatch) -> None:
    # Relative -I flags from a CMake/Ninja compile DB only resolve when the
    # depfile pass runs from the CU's directory — that dir must reach the live
    # header-include capture as cwd (Codex review).
    from abicheck.buildsource import build_evidence as be, preprocessor_scan as ps

    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id="cu://a",
                source="src/a.cpp",
                language="CXX",
                directory="/work/build",
                argv=["clang++", "-c", "src/a.cpp", "-Iinclude"],
            )
        ]
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "available", lambda self: True)
    monkeypatch.setattr(
        ps.ClangPreprocessorExtractor, "capture_macros", lambda self, b: {}
    )

    def _fake_includes(self, headers, context, language="c++", cwd=None):
        captured["cwd"] = cwd
        captured["context"] = context
        return {headers[0]: ["include/detail/impl.h"]}

    monkeypatch.setattr(
        ps.ClangPreprocessorExtractor, "capture_header_includes", _fake_includes
    )

    result = ps.run_preprocessor_scan(build, ["include/foo.h"])
    assert result.ran is True
    assert captured["cwd"] == "/work/build"
    # The source token is stripped from the reused include context.
    assert "src/a.cpp" not in captured["context"]
    assert "-Iinclude" in captured["context"]


def test_expand_public_headers_expands_directories(tmp_path) -> None:
    # cli_scan must hand the S2 leak pass the individual header *files*, not a
    # directory (which clang would preprocess as one bogus TU) (Codex review).
    from pathlib import Path

    from abicheck.cli_scan import _expand_public_headers

    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "a.h").write_text("// a\n", encoding="utf-8")
    (inc / "b.hpp").write_text("// b\n", encoding="utf-8")
    expanded = _expand_public_headers([Path(inc)])
    assert {Path(p).name for p in expanded} == {"a.h", "b.hpp"}


def test_capture_macros_argv_is_output_flag_sanitized(monkeypatch) -> None:
    # The `clang -E -dM` macro pass must route the recorded compile argv through
    # the #426-hardened depfile sanitizer, so output-producing instrumentation
    # from an untrusted PR artifact (-save-temps / -ftime-trace / -o) never
    # reaches the replay. Guards against the macro pass drifting off the shared
    # sanitizer and re-opening the field "clang -E failed every invocation" shape.
    import abicheck.buildsource.build_evidence as be
    import abicheck.buildsource.preprocessor_scan as ps

    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id="cu://a",
                source="src/a.cpp",
                language="CXX",
                directory="/work/build",
                argv=[
                    "clang++",
                    "-c",
                    "src/a.cpp",
                    "-Iinclude",
                    "-DUSE_X=1",
                    "-o",
                    "a.o",
                    "-save-temps",
                    "-ftime-trace=/tmp/victim.json",
                ],
            )
        ]
    )
    seen: dict[str, list[str]] = {}

    def _fake_run(self, cmd, cwd, unit):
        seen["cmd"] = list(cmd)
        return "#define NDEBUG 1\n"

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fake_run)
    out = ps.ClangPreprocessorExtractor().capture_macros(build)

    cmd = seen["cmd"]
    assert cmd[:3] == ["clang++", "-E", "-dM"]
    # ABI-relevant context is preserved; output/instrumentation flags are stripped.
    assert "-Iinclude" in cmd and "-DUSE_X=1" in cmd and "src/a.cpp" in cmd
    assert "-o" not in cmd and "a.o" not in cmd
    assert "-save-temps" not in cmd
    assert not any(a.startswith("-ftime-trace") for a in cmd)
    assert out["cu://a"] == {"NDEBUG": "1"}


# ── scan_engine's clang-binary resolution for run_preprocessor_scan ────────────


def test_scan_engine_clang_bin_defaults_to_clang_plusplus() -> None:
    from abicheck.scan_engine import _preprocessor_scan_clang_bin

    assert _preprocessor_scan_clang_bin(None) == "clang++"


def test_scan_engine_clang_bin_honors_clang_family_gcc_path() -> None:
    """A ``--gcc-path`` pointing at a clang-family binary (icx/icpx/dpcpp/…)
    must be threaded through to the S2 pre-scan's own ``clang -E`` invocation
    instead of a hardcoded ``clang++`` — otherwise every preprocessor pass
    fails on an Intel-only flag like ``-no-intel-lib`` that a real ``clang++``
    on PATH rejects (the bug this test guards against)."""
    from abicheck.scan_engine import _preprocessor_scan_clang_bin
    from abicheck.service_scan import CompileContext

    assert _preprocessor_scan_clang_bin(CompileContext(gcc_path="icpx")) == "icpx"
    assert _preprocessor_scan_clang_bin(CompileContext(gcc_path="/opt/bin/icx")) == (
        "/opt/bin/icx"
    )


def test_scan_engine_clang_bin_ignores_non_clang_gcc_path() -> None:
    """A ``--gcc-path`` pointing at a real GCC binary (castxml's compiler
    emulation target) is never dereferenced for the clang-only ``-E -dM``/``-M``
    pre-scan — same "clang-family only" rule as
    :func:`abicheck.dumper_clang._resolve_clang_bin`."""
    from abicheck.scan_engine import _preprocessor_scan_clang_bin
    from abicheck.service_scan import CompileContext

    assert _preprocessor_scan_clang_bin(CompileContext(gcc_path="g++")) == "clang++"


def test_scan_engine_clang_bin_honors_gcc_prefix_when_available(monkeypatch) -> None:
    """A ``--gcc-prefix`` composes a prefixed clang driver name, but only wins
    when that specific binary is actually resolvable on PATH."""
    import abicheck.scan_engine as se
    from abicheck import dumper_clang
    from abicheck.service_scan import CompileContext

    monkeypatch.setattr(
        dumper_clang.shutil,
        "which",
        lambda name: name if name.endswith("clang++") else None,
    )
    ctx = CompileContext(gcc_prefix="/opt/x86_64-linux-gnu-")
    assert se._preprocessor_scan_clang_bin(ctx) == "/opt/x86_64-linux-gnu-clang++"


def test_scan_engine_clang_bin_falls_back_when_prefixed_clang_missing(
    monkeypatch,
) -> None:
    """A documented GCC cross-toolchain prefix (e.g. ``aarch64-linux-gnu-``)
    is not evidence a same-prefixed Clang driver exists — a system providing
    only ``aarch64-linux-gnu-g++`` must fall back to the plain ``clang++``
    that worked before this resolver existed, not silently downgrade S2 to
    ``not_collected`` by guessing a nonexistent binary name (Codex review)."""
    import abicheck.scan_engine as se
    from abicheck import dumper_clang
    from abicheck.service_scan import CompileContext

    monkeypatch.setattr(dumper_clang.shutil, "which", lambda name: None)
    ctx = CompileContext(gcc_prefix="aarch64-linux-gnu-")
    assert se._preprocessor_scan_clang_bin(ctx) == "clang++"


def test_scan_engine_clang_bin_excludes_cl_style_drivers() -> None:
    """A CL-compatible-mode driver (``clang-cl``, Intel's ``dpcpp-cl``) must
    never be selected here: :class:`ClangPreprocessorExtractor` always shells
    out with fixed GNU-mode flags (``-E -dM``, ``-M``), which a CL-mode
    driver either rejects or silently misinterprets as ordinary compile
    input (``/E``/``/d1PP`` are the CL-mode spellings) — Codex review."""
    from abicheck.scan_engine import _preprocessor_scan_clang_bin
    from abicheck.service_scan import CompileContext

    assert _preprocessor_scan_clang_bin(CompileContext(gcc_path="dpcpp-cl")) == (
        "clang++"
    )
    assert _preprocessor_scan_clang_bin(CompileContext(gcc_path="clang-cl")) == (
        "clang++"
    )
    assert (
        _preprocessor_scan_clang_bin(
            CompileContext(gcc_path=r"C:\llvm\bin\clang-cl.exe")
        )
        == "clang++"
    )


# ── perf controls: dedup / parallel jobs / probe cap / disable knob ──────────


def test_capture_macros_dedups_identical_compile_contexts(monkeypatch) -> None:
    # Compile units sharing the same (language, cwd, flags) signature must
    # collapse into ONE clang -E -dM probe, fanned out to every unit in the
    # group — the fix for oneDAL's --depth build scan cost (AGENTS.md
    # "Known gaps"): thousands of TUs sharing a build-wide flag set no
    # longer pay one subprocess each.
    import abicheck.buildsource.build_evidence as be
    import abicheck.buildsource.preprocessor_scan as ps

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id=f"cu://{i}",
                source=f"src/{i}.cpp",
                language="CXX",
                directory="/work/build",
                argv=["clang++", "-c", f"src/{i}.cpp", "-Iinclude", "-DNDEBUG=1"],
            )
            for i in range(50)
        ]
    )
    probes = {"n": 0}

    def _fake_run(self, cmd, cwd, unit):
        probes["n"] += 1
        return "#define NDEBUG 1\n"

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fake_run)
    out = ps.ClangPreprocessorExtractor().capture_macros(build)

    assert probes["n"] == 1, f"expected one deduped probe, got {probes['n']}"
    assert len(out) == 50
    assert all(v == {"NDEBUG": "1"} for v in out.values())


def test_capture_macros_probes_each_distinct_compile_context(monkeypatch) -> None:
    # Two genuinely different flag sets must each get their own probe.
    import abicheck.buildsource.build_evidence as be
    import abicheck.buildsource.preprocessor_scan as ps

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id="cu://a",
                source="a.cpp",
                language="CXX",
                argv=["clang++", "-c", "a.cpp", "-DNDEBUG=1"],
            ),
            be.CompileUnit(
                id="cu://b",
                source="b.cpp",
                language="CXX",
                argv=["clang++", "-c", "b.cpp", "-UNDEBUG"],
            ),
        ]
    )
    seen_cmds: list[list[str]] = []

    def _fake_run(self, cmd, cwd, unit):
        seen_cmds.append(list(cmd))
        return "#define NDEBUG 1\n" if "-DNDEBUG=1" in cmd else "#define NDEBUG 0\n"

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fake_run)
    out = ps.ClangPreprocessorExtractor().capture_macros(build)

    assert len(seen_cmds) == 2
    assert out["cu://a"] == {"NDEBUG": "1"}
    assert out["cu://b"] == {"NDEBUG": "0"}


def test_preprocessor_scan_jobs_env_override(monkeypatch) -> None:
    from abicheck.buildsource import preprocessor_scan as ps

    monkeypatch.delenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", raising=False)
    assert ps._preprocessor_scan_jobs(1) == 1
    assert ps._preprocessor_scan_jobs(0) == 1

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    assert ps._preprocessor_scan_jobs(20) == 1

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "not-a-number")
    assert ps._preprocessor_scan_jobs(20) >= 1  # falls back to auto, never crashes

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "4")
    assert ps._preprocessor_scan_jobs(20) == 4


def test_capture_macros_respects_max_probes_cap(monkeypatch) -> None:
    # A build with many DISTINCT compile contexts still bounds worst-case
    # cost; the truncation is reported, never silent (AGENTS.md "No silent
    # caps").
    import abicheck.buildsource.build_evidence as be
    import abicheck.buildsource.preprocessor_scan as ps

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", "3")
    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id=f"cu://{i}",
                source=f"{i}.cpp",
                language="CXX",
                argv=["clang++", "-c", f"{i}.cpp", f"-DTAG={i}"],
            )
            for i in range(10)
        ]
    )

    def _fake_run(self, cmd, cwd, unit):
        return "#define NDEBUG 1\n"

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fake_run)
    ex = ps.ClangPreprocessorExtractor()
    out = ex.capture_macros(build)

    assert len(out) == 3  # only the first 3 distinct contexts were probed
    assert ex.probes_truncated == 7
    assert any("capped at 3" in d for d in ex.diagnostics)


def test_capture_header_includes_respects_max_probes_cap(monkeypatch) -> None:
    # Codex review: the probe cap must also bound capture_header_includes
    # (the clang -M half of the pre-scan), not only capture_macros -- else
    # a project with hundreds/thousands of public headers still reproduces
    # the excessive scan time this cap exists to prevent.
    from abicheck.buildsource import preprocessor_scan as ps

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", "3")
    headers = [f"include/h{i}.h" for i in range(10)]

    def _fake_run(self, cmd, cwd, unit):
        return "h.o: h.h\n"

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fake_run)
    ex = ps.ClangPreprocessorExtractor()
    out = ex.capture_header_includes(headers, ["-Iinclude"])

    assert len(out) == 3
    assert ex.probes_truncated == 7
    assert any("capped at 3" in d for d in ex.diagnostics)


def test_capture_macros_and_header_includes_truncation_accumulates(
    monkeypatch,
) -> None:
    # A single extractor instance used for both passes (run_preprocessor_scan's
    # shape) must accumulate truncation from both, not overwrite one with the
    # other.
    import abicheck.buildsource.build_evidence as be
    import abicheck.buildsource.preprocessor_scan as ps

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", "2")
    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id=f"cu://{i}",
                source=f"{i}.cpp",
                language="CXX",
                argv=["clang++", "-c", f"{i}.cpp", f"-DTAG={i}"],
            )
            for i in range(5)
        ]
    )

    def _fake_run(self, cmd, cwd, unit):
        return "#define NDEBUG 1\n"

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fake_run)
    ex = ps.ClangPreprocessorExtractor()
    ex.capture_macros(build)  # 5 distinct contexts, cap 2 -> truncates 3
    ex.capture_header_includes([f"h{i}.h" for i in range(5)], [])  # truncates 3 more

    assert ex.probes_truncated == 6


def test_coverage_downgrades_to_partial_when_probes_truncated(monkeypatch) -> None:
    # Codex review: a capped scan never inspected some distinct compile
    # contexts / public headers at all -- their macro values/divergences/
    # leaks are genuinely unknown, not absent, so the coverage row must not
    # read as a clean PRESENT even when every ATTEMPTED probe succeeded.
    from abicheck.buildsource import build_evidence as be, preprocessor_scan as ps

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", "1")
    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id=f"cu://{i}",
                source=f"{i}.cpp",
                language="CXX",
                argv=["clang++", "-c", f"{i}.cpp", f"-DTAG={i}"],
            )
            for i in range(3)
        ]
    )

    def _fake_run(self, cmd, cwd, unit):
        return "#define NDEBUG 1\n"

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "available", lambda self: True)
    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fake_run)
    result = ps.run_preprocessor_scan(build, [], clang_bin="clang++")

    assert result.probes_truncated == 2
    assert result.attempted == result.succeeded  # every attempted probe "succeeded"
    cov = result.coverage()
    assert cov.status.value == "partial"
    assert "compile-context probe(s) skipped" in cov.detail
    # Must not misreport a failed-run count that never happened.
    assert "0 clang run(s) failed" not in cov.detail


def test_capture_macros_dedup_key_preserves_source_looking_flag_operand(
    monkeypatch,
) -> None:
    # Codex review: a flag operand that happens to end in a source extension
    # (e.g. `-include config_a.c`) must survive into the dedup signature --
    # only the TU's OWN source token is stripped. Two units differing only in
    # that operand are genuinely different compile contexts and must not be
    # merged (which would silently hide a real per-TU macro divergence).
    import abicheck.buildsource.build_evidence as be
    import abicheck.buildsource.preprocessor_scan as ps

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id="cu://a",
                source="a.cpp",
                language="CXX",
                argv=["clang++", "-c", "a.cpp", "-include", "config_a.c"],
            ),
            be.CompileUnit(
                id="cu://b",
                source="b.cpp",
                language="CXX",
                argv=["clang++", "-c", "b.cpp", "-include", "config_b.c"],
            ),
        ]
    )
    seen_cmds: list[list[str]] = []

    def _fake_run(self, cmd, cwd, unit):
        seen_cmds.append(list(cmd))
        return "#define NDEBUG 1\n" if "config_a.c" in cmd else "#define NDEBUG 0\n"

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fake_run)
    out = ps.ClangPreprocessorExtractor().capture_macros(build)

    assert len(seen_cmds) == 2  # NOT deduped into one probe
    assert out["cu://a"] == {"NDEBUG": "1"}
    assert out["cu://b"] == {"NDEBUG": "0"}


def test_capture_macros_dedups_compile_db_shape_absolute_source_relative_argv(
    monkeypatch,
) -> None:
    # Codex review: CompileDbAdapter resolves CompileUnit.source to an
    # ABSOLUTE path (CompileEntry.from_dict joins a relative "file" onto
    # "directory") while argv keeps the compile database's own, still
    # RELATIVE spelling -- the real-world shape a compile_commands.json
    # produces, not a synthetic edge case. A bare string-equality filter
    # would strip nothing here and silently fall back to one probe per TU.
    import abicheck.buildsource.build_evidence as be
    import abicheck.buildsource.preprocessor_scan as ps

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "1")
    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id=f"cu://{i}",
                source=f"/work/build/src/{i}.cpp",  # absolute, like CompileDbAdapter
                directory="/work/build",
                language="CXX",
                argv=["clang++", "-c", f"src/{i}.cpp", "-DNDEBUG=1"],  # relative
            )
            for i in range(10)
        ]
    )
    probes = {"n": 0}

    def _fake_run(self, cmd, cwd, unit):
        probes["n"] += 1
        return "#define NDEBUG 1\n"

    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "_run", _fake_run)
    out = ps.ClangPreprocessorExtractor().capture_macros(build)

    assert probes["n"] == 1, f"expected one deduped probe, got {probes['n']}"
    assert len(out) == 10


def test_normalize_source_path() -> None:
    from abicheck.buildsource.preprocessor_scan import _normalize_source_path

    assert _normalize_source_path("", "/work/build") == ""
    # Relative path joined onto directory, then normalized.
    assert _normalize_source_path("src/a.cpp", "/work/build") == "/work/build/src/a.cpp"
    # Already-absolute path: directory is ignored by os.path.join, just
    # normpath'd.
    assert (
        _normalize_source_path("/work/build/src/a.cpp", "/elsewhere")
        == "/work/build/src/a.cpp"
    )
    # No directory: bare normpath.
    assert _normalize_source_path("src/./a.cpp", None) == "src/a.cpp"


def test_preprocessor_scan_version_bumped_for_probes_truncated() -> None:
    # Codex review: the additive probes_truncated field needs its schema
    # version bumped so a consumer negotiating either version can detect
    # the changed shape.
    from abicheck.buildsource.preprocessor_scan import PREPROCESSOR_SCAN_VERSION
    from abicheck.schemas import SCAN_SCHEMA_VERSION

    assert PREPROCESSOR_SCAN_VERSION >= 2
    assert SCAN_SCHEMA_VERSION != "1.10"


def test_run_preprocessor_scan_disabled_via_env(monkeypatch) -> None:
    from abicheck.buildsource import build_evidence as be

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN", "0")
    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(id="cu://a", source="a.cpp", language="CXX", argv=["a.cpp"])
        ]
    )
    result = run_preprocessor_scan(build, [])
    assert result.ran is False
    assert "ABICHECK_PREPROCESSOR_SCAN=0" in result.skipped_reason
    cov = result.coverage()
    assert cov.status.value == "not_collected"


def test_capture_macros_parallel_probe_counts_are_race_free(monkeypatch) -> None:
    # Distinct compile contexts probed via the real thread pool (no
    # ABICHECK_PREPROCESSOR_SCAN_JOBS=1 override here), through the REAL
    # ``_run`` (only ``deadline.run_bounded`` — the actual subprocess call —
    # is faked) so its runs_attempted/runs_ok increments genuinely race
    # across threads. Regression guard for the non-atomic ``+= 1`` a naive
    # parallel refactor would introduce.
    import time

    import abicheck.buildsource.build_evidence as be
    import abicheck.buildsource.preprocessor_scan as ps
    from abicheck import deadline

    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_JOBS", "8")
    n = 40
    build = be.BuildEvidence(
        compile_units=[
            be.CompileUnit(
                id=f"cu://{i}",
                source=f"{i}.cpp",
                language="CXX",
                argv=["clang++", "-c", f"{i}.cpp", f"-DTAG={i}"],
            )
            for i in range(n)
        ]
    )

    class _P:
        stdout = "#define NDEBUG 1\n"
        stderr = ""
        returncode = 0

    def _fake_run_bounded(cmd, **kwargs):
        time.sleep(0.001)  # encourage real thread interleaving
        return _P()

    monkeypatch.setattr(deadline, "run_bounded", _fake_run_bounded)
    ex = ps.ClangPreprocessorExtractor()
    out = ex.capture_macros(build)

    assert len(out) == n
    assert ex.runs_attempted == n
    assert ex.runs_ok == n


def test_preprocessor_scan_enabled_default_and_falsy_values(monkeypatch) -> None:
    from abicheck.buildsource import preprocessor_scan as ps

    monkeypatch.delenv("ABICHECK_PREPROCESSOR_SCAN", raising=False)
    assert ps.preprocessor_scan_enabled() is True
    for falsy in ("0", "false", "no", "off", "FALSE", "Off"):
        monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN", falsy)
        assert ps.preprocessor_scan_enabled() is False
    monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN", "1")
    assert ps.preprocessor_scan_enabled() is True
