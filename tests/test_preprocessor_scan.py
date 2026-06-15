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
    from abicheck.buildsource import build_evidence as be
    from abicheck.buildsource import preprocessor_scan as ps

    build = be.BuildEvidence(
        compile_units=[be.CompileUnit(id="cu://a", source="a.cpp", language="CXX")]
    )
    # Force the extractor to report clang unavailable.
    monkeypatch.setattr(ps.ClangPreprocessorExtractor, "available", lambda self: False)
    result = run_preprocessor_scan(build, ["include/foo.h"], clang_bin="clang++")
    assert result.ran is False
    assert "not found" in result.skipped_reason


def test_run_passes_compile_unit_directory_as_cwd(monkeypatch) -> None:
    # Relative -I flags from a CMake/Ninja compile DB only resolve when the
    # depfile pass runs from the CU's directory — that dir must reach the live
    # header-include capture as cwd (Codex review).
    from abicheck.buildsource import build_evidence as be
    from abicheck.buildsource import preprocessor_scan as ps

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
