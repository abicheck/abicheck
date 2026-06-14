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

"""Tests for the ADR-035 D2 compiler-free lexical ABI-risk pattern pre-scan.

Every construct in the ADR-035 D2 list has a positive fixture; comment/string
blanking, escalation grouping, coverage reporting, and the changed+public file
walk are covered. Pure-Python, no external tools — runs in the default lane.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.model import CoverageStatus, LayerConfidence
from abicheck.buildsource.pattern_scan import (
    PATTERN_SCAN_VERSION,
    PatternCategory,
    PatternKind,
    PatternScanResult,
    iter_source_files,
    scan_files,
    scan_text,
)


def _kinds(text: str) -> set[PatternKind]:
    return {f.kind for f in scan_text(text)}


# ── Each ADR-035 D2 construct is recognized ──────────────────────────────────


@pytest.mark.parametrize(
    "snippet,kind",
    [
        ("#pragma pack(1)", PatternKind.PRAGMA_PACK),
        ("#  pragma   pack ( push, 1 )", PatternKind.PRAGMA_PACK),
        ('_Pragma("pack(push, 1)")', PatternKind.PRAGMA_PACK),
        ("struct alignas(16) S { int x; };", PatternKind.ALIGNAS),
        ("_Alignas(8) char buf[8];", PatternKind.ALIGNAS),
        (
            "struct __attribute__((packed)) S { char a; int b; };",
            PatternKind.ATTRIBUTE_PACKED,
        ),
        (
            "struct __attribute__((aligned(8), packed)) S { char a; int b; };",
            PatternKind.ATTRIBUTE_PACKED,
        ),
        (
            "struct __attribute__((__packed__)) S { char a; int b; };",
            PatternKind.ATTRIBUTE_PACKED,
        ),
        (
            "struct __attribute__((aligned(sizeof(int)), packed)) S {};",
            PatternKind.ATTRIBUTE_PACKED,
        ),
        ("struct [[gnu::packed]] S { char a; int b; };", PatternKind.ATTRIBUTE_PACKED),
        (
            "template void api<int>();",
            PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION,
        ),
        ("extern template int api<int>();", PatternKind.EXTERN_TEMPLATE),
        (
            'void f() __attribute__((visibility("hidden")));',
            PatternKind.ATTRIBUTE_VISIBILITY,
        ),
        (
            'void f() __attribute__((aligned(8), visibility("hidden")));',
            PatternKind.ATTRIBUTE_VISIBILITY,
        ),
        ('[[gnu::visibility("default")]] void f();', PatternKind.ATTRIBUTE_VISIBILITY),
        ("__declspec(dllexport) int api(void);", PatternKind.DECLSPEC_DLLEXPORT),
        ("__declspec(dllimport) int api(void);", PatternKind.DECLSPEC_DLLIMPORT),
        ('extern "C" void c_api(void);', PatternKind.EXTERN_C),
        ("int __stdcall winproc(void);", PatternKind.CALLING_CONVENTION),
        ("int WINAPI WinMain(void);", PatternKind.CALLING_CONVENTION),
        (
            "void __attribute__((ms_abi)) f(void);",
            PatternKind.CALLING_CONVENTION,
        ),
        (
            "void __attribute__((sysv_abi)) f(void);",
            PatternKind.CALLING_CONVENTION,
        ),
        (
            "void __attribute__((stdcall)) f(void);",
            PatternKind.CALLING_CONVENTION,
        ),
        (
            "void __attribute__((regparm(3))) f(int);",
            PatternKind.CALLING_CONVENTION,
        ),
        (
            '[[nodiscard, gnu::visibility("default")]] int f();',
            PatternKind.ATTRIBUTE_VISIBILITY,
        ),
        ("inline namespace v1 { struct S {}; }", PatternKind.INLINE_NAMESPACE),
        ("struct Base { virtual void f(); };", PatternKind.VIRTUAL_METHOD),
        ("struct Derived : Base { void f() override; };", PatternKind.VIRTUAL_METHOD),
        ("struct Derived : Base { void f() final; };", PatternKind.VIRTUAL_METHOD),
        (
            "struct Derived : Base { void f() final override; };",
            PatternKind.VIRTUAL_METHOD,
        ),
        (
            "struct Derived : Base { void f() & noexcept override; };",
            PatternKind.VIRTUAL_METHOD,
        ),
        (
            "struct Derived : Base { auto f() -> int override; };",
            PatternKind.VIRTUAL_METHOD,
        ),
        ("void* operator new(size_t n);", PatternKind.OPERATOR_NEW_DELETE),
        ("void operator delete(void* p) noexcept;", PatternKind.OPERATOR_NEW_DELETE),
        ("template class Vector<int>;", PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION),
        ("extern template class Vector<int>;", PatternKind.EXTERN_TEMPLATE),
    ],
)
def test_recognizes_each_construct(snippet: str, kind: PatternKind) -> None:
    assert kind in _kinds(snippet)


def test_extern_template_not_double_counted_as_explicit() -> None:
    # `extern template class X<int>;` must be classified once, as EXTERN_TEMPLATE.
    facts = scan_text("extern template class X<int>;")
    template_kinds = [
        f.kind
        for f in facts
        if f.kind
        in (PatternKind.EXTERN_TEMPLATE, PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION)
    ]
    assert template_kinds == [PatternKind.EXTERN_TEMPLATE]


def test_extern_cpp_not_flagged_as_extern_c() -> None:
    assert PatternKind.EXTERN_C not in _kinds('extern "C++" void f();')


@pytest.mark.parametrize(
    "src",
    [
        "__attribute__((aligned(8))) int packed;",  # identifier named packed
        "[[nodiscard]] bool packed();",  # method named packed, non-packing attr
        "int packed = 1;",  # plain identifier
    ],
)
def test_identifier_named_packed_not_flagged(src: str) -> None:
    # The packed rule must stay inside the attribute parens, not match a later
    # identifier that merely happens to be spelled `packed`.
    assert PatternKind.ATTRIBUTE_PACKED not in _kinds(src)


@pytest.mark.parametrize(
    "src",
    [
        "__attribute__((aligned(8))) int visibility;",
        "[[nodiscard]] int visibility();",
    ],
)
def test_identifier_named_visibility_not_flagged(src: str) -> None:
    assert PatternKind.ATTRIBUTE_VISIBILITY not in _kinds(src)


def test_non_cc_attribute_not_flagged_as_calling_convention() -> None:
    # A plain attribute without a calling-convention keyword must not flag.
    assert PatternKind.CALLING_CONVENTION not in _kinds(
        "void __attribute__((noreturn)) f(void);"
    )


@pytest.mark.parametrize(
    "definition",
    [
        "template <typename T> class Foo {};",
        "template<typename T> void f(T x);",
        "template <class T, class U> struct Pair {};",
        "template  <typename T> class Foo {};",  # two spaces before `<`
        "template\n<typename T> class Foo {};",  # newline before `<`
        "template\n  <typename T> struct S {};",  # newline + spaces
    ],
)
def test_template_definition_not_flagged_as_instantiation(definition: str) -> None:
    # A template *definition* (keyword followed by `<`) is not an instantiation.
    kinds = _kinds(definition)
    assert PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION not in kinds
    assert PatternKind.EXTERN_TEMPLATE not in kinds


@pytest.mark.parametrize(
    "disambiguator",
    [
        "obj.template foo<int>();",
        "ptr->template bar<int>();",
        "typename Alloc::template rebind<U>::other a;",
        "Alloc::template rebind<U> r;",
        "typename A<T>:: template rebind<int>::other o;",  # space after ::
        "obj. template foo<int>();",  # space after .
        "ptr-> template bar<int>();",  # space after ->
    ],
)
def test_dependent_template_disambiguator_not_flagged(disambiguator: str) -> None:
    # `x.template f<...>()`, `p->template ...`, and `Alloc::template rebind<...>`
    # are dependent-name disambiguators, not explicit instantiations — even with
    # whitespace after the `.`/`->`/`::`.
    assert PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION not in _kinds(disambiguator)


@pytest.mark.parametrize(
    "src,kind",
    [
        # C++14 digit separators must not be read as char-literal openers (which
        # would blank the rest of the file and hide later constructs).
        (
            "constexpr auto n = 1'000;\nstruct B { virtual void f(); };",
            PatternKind.VIRTUAL_METHOD,
        ),
        ("constexpr auto h = 0xFF'FF;\n#pragma pack(1)", PatternKind.PRAGMA_PACK),
    ],
)
def test_digit_separator_does_not_hide_later_constructs(
    src: str, kind: PatternKind
) -> None:
    assert kind in _kinds(src)


@pytest.mark.parametrize(
    "src",
    [
        "auto c = u8'a';\nstruct B { virtual void f(); };",
        "auto c = u'0';\n#pragma pack(1)",
        "auto c = U'9';\nstruct B { virtual void g(); };",
        "auto c = L'7';\n#pragma pack(1)",
    ],
)
def test_prefixed_char_literal_not_treated_as_digit_separator(src: str) -> None:
    # `u8'a'`/`u'0'`/`U'9'`/`L'7'` are prefixed char literals, not digit
    # separators — the closing quote must not blank the rest of the file.
    kinds = _kinds(src)
    assert kinds & {PatternKind.VIRTUAL_METHOD, PatternKind.PRAGMA_PACK}


def test_real_char_literal_still_blanks_its_contents() -> None:
    # A genuine char literal must still hide an ABI keyword spelled inside it.
    assert PatternKind.PRAGMA_PACK not in _kinds('const char* s = "#pragma pack";')


def test_function_template_instantiation_escalates() -> None:
    res = PatternScanResult(
        facts=scan_text("template void api<int>();", path="h.h"), files_scanned=1
    )
    assert res.should_escalate is True


# ── Comment / string-literal blanking avoids false positives ─────────────────


def test_keyword_in_line_comment_ignored() -> None:
    assert _kinds("// use #pragma pack here\nint x;") == set()


def test_keyword_in_block_comment_ignored() -> None:
    src = "/* virtual and operator new are documented */\nint x;"
    assert _kinds(src) == set()


def test_keyword_in_string_literal_ignored() -> None:
    assert _kinds('const char* s = "#pragma pack(1)";') == set()


def test_line_numbers_preserved_through_comments() -> None:
    src = (
        "/* multi\n"  # line 1-2 block comment
        "   line */\n"
        "struct alignas(4) S {};\n"  # line 3
    )
    facts = [f for f in scan_text(src) if f.kind is PatternKind.ALIGNAS]
    assert len(facts) == 1
    assert facts[0].line == 3


def test_code_after_block_comment_still_scanned() -> None:
    src = "/* c */ virtual void f();"
    assert PatternKind.VIRTUAL_METHOD in _kinds(src)


def test_line_continuation_in_line_comment_stays_commented() -> None:
    # A `//` comment ending in a backslash splices the next line into the
    # comment (C/C++ line continuation), so the `virtual` is commented out.
    assert PatternKind.VIRTUAL_METHOD not in _kinds("// note \\\nvirtual void f();")


def test_line_comment_without_continuation_resumes_code() -> None:
    # Without the trailing backslash, the next line is real code again.
    assert PatternKind.VIRTUAL_METHOD in _kinds("// note\nvirtual void f();")


def test_plain_override_identifier_not_flagged_as_virtual_method() -> None:
    assert PatternKind.VIRTUAL_METHOD not in _kinds("int override = 0;")


def test_raw_string_embedded_quote_does_not_hide_later_construct() -> None:
    src = 'const char* s = R"(contains " quote)";\nstruct B { virtual void f(); };'
    assert PatternKind.VIRTUAL_METHOD in _kinds(src)


def test_raw_string_contents_not_flagged() -> None:
    assert _kinds('const char* s = R"(#pragma pack(1) virtual void f();)";') == set()


def test_raw_string_contents_not_flagged_by_string_preserving_rules() -> None:
    src = r"""const char* s = R"abi(_Pragma("pack(push, 1)") extern "C")abi";"""
    assert PatternKind.PRAGMA_PACK not in _kinds(src)
    assert PatternKind.EXTERN_C not in _kinds(src)


@pytest.mark.parametrize(
    "src",
    [
        '_Pragma("GCC diagnostic ignored \\"-Wpragma-pack\\"")',
        '_Pragma("message(\\"pack\\")")',
    ],
)
def test_non_pack_pragma_not_flagged(src: str) -> None:
    # `_Pragma` whose payload merely mentions `pack` (not as the directive
    # token) must not emit a PRAGMA_PACK layout fact.
    assert PatternKind.PRAGMA_PACK not in _kinds(src)


def test_unterminated_string_at_eof_does_not_crash() -> None:
    # An unterminated string literal reaching EOF must be handled (the blanker
    # stays in string state to the end) and a preceding construct still fires.
    facts = scan_text('struct alignas(4) S {};\nconst char* s = "oops')
    assert PatternKind.ALIGNAS in {f.kind for f in facts}


def test_raw_string_edge_cases_fall_back_to_normal_string() -> None:
    # _raw_string_end returns -1 (not a raw string) for: an alnum char before
    # the prefix, no opening paren near the quote, a whitespace delimiter, and a
    # missing close sequence — exercising each guard.
    from abicheck.buildsource.pattern_scan import _raw_string_end

    assert _raw_string_end('aR"(x)"', 2) == -1  # `a` before the `R` prefix
    assert _raw_string_end('R"' + "x" * 30, 1) == -1  # no `(` near the quote
    assert _raw_string_end('R"de lim(x)de lim"', 1) == -1  # space in delimiter
    assert _raw_string_end('R"d(unterminated', 1) == -1  # no closing `)d"`
    s = 'R"d(body)d"'
    assert _raw_string_end(s, 1) == len(s) - 1  # well-formed → closing offset


def test_snippet_out_of_range_returns_empty() -> None:
    from abicheck.buildsource.pattern_scan import _snippet

    assert _snippet(["only one line"], 5) == ""


# ── Escalation triggers + categories ─────────────────────────────────────────


def test_layout_construct_escalates_to_s5() -> None:
    facts = scan_text("#pragma pack(1)\nstruct S { int x; };", path="h.h")
    res = PatternScanResult(facts=facts, files_scanned=1)
    assert res.should_escalate is True
    triggers = res.escalation_triggers
    assert len(triggers) == 1
    assert triggers[0].kind is PatternKind.PRAGMA_PACK
    assert triggers[0].recommended_method == "s5"
    assert triggers[0].category is PatternCategory.LAYOUT
    assert triggers[0].sample_location == "h.h:1"


def test_advisory_only_construct_does_not_escalate() -> None:
    facts = scan_text('extern "C" void f();', path="h.h")
    res = PatternScanResult(facts=facts, files_scanned=1)
    assert res.should_escalate is False
    assert res.escalation_triggers == []


def test_escalation_triggers_grouped_per_kind() -> None:
    src = "struct B { virtual void a(); virtual void b(); virtual void c(); };"
    res = PatternScanResult(facts=scan_text(src, path="h.h"), files_scanned=1)
    triggers = [
        t for t in res.escalation_triggers if t.kind is PatternKind.VIRTUAL_METHOD
    ]
    assert len(triggers) == 1
    assert triggers[0].count == 3


def test_escalation_triggers_sorted_deterministically() -> None:
    src = "virtual void f();\n#pragma pack(1)\ninline namespace v1 {}"
    res = PatternScanResult(facts=scan_text(src), files_scanned=1)
    kinds = [t.kind.value for t in res.escalation_triggers]
    assert kinds == sorted(kinds)


# ── Result aggregation, coverage, serialization ──────────────────────────────


def test_counts_by_kind_and_to_dict_roundtrip() -> None:
    src = "virtual void a();\nvirtual void b();\n#pragma pack(1)"
    res = PatternScanResult(facts=scan_text(src, path="h.h"), files_scanned=1)
    counts = res.counts_by_kind()
    assert counts[PatternKind.VIRTUAL_METHOD.value] == 2
    assert counts[PatternKind.PRAGMA_PACK.value] == 1
    payload = res.to_dict()
    assert payload["version"] == PATTERN_SCAN_VERSION
    assert payload["counts_by_kind"] == counts
    assert len(payload["facts"]) == 3
    assert payload["escalation_triggers"]  # non-empty (layout + vtable escalate)


def test_coverage_present_when_files_scanned() -> None:
    res = PatternScanResult(facts=[], files_scanned=3)
    cov = res.coverage()
    assert cov.status is CoverageStatus.PRESENT
    assert cov.confidence is LayerConfidence.REDUCED
    assert "3 file" in cov.detail


def test_coverage_partial_when_files_skipped() -> None:
    res = PatternScanResult(facts=[], files_scanned=2, files_skipped=1)
    assert res.coverage().status is CoverageStatus.PARTIAL


def test_coverage_not_collected_when_nothing_scanned() -> None:
    assert PatternScanResult().coverage().status is CoverageStatus.NOT_COLLECTED


# ── File walking + changed-path scoping ──────────────────────────────────────


def test_iter_source_files_filters_by_suffix(tmp_path: Path) -> None:
    (tmp_path / "a.hpp").write_text("int x;")
    (tmp_path / "b.cpp").write_text("int y;")
    (tmp_path / "README.md").write_text("# docs")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")
    found = {p.name for p in iter_source_files([tmp_path])}
    assert found == {"a.hpp", "b.cpp"}


@pytest.mark.parametrize("name", ["detail.tpp", "Config.inc"])
def test_iter_source_files_includes_tpp_and_inc(tmp_path: Path, name: str) -> None:
    # These are header-like extensions the repo already recognizes elsewhere.
    (tmp_path / name).write_text("#pragma pack(1)\nstruct S { int x; };")
    found = {p.name for p in iter_source_files([tmp_path])}
    assert name in found


def test_iter_source_files_changed_scope(tmp_path: Path) -> None:
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "public.h").write_text("int p;")
    (inc / "other.h").write_text("int o;")
    found = {
        p.name for p in iter_source_files([inc], changed_paths=["include/public.h"])
    }
    assert found == {"public.h"}


def test_iter_source_files_includes_extensionless_headers(tmp_path: Path) -> None:
    inc = tmp_path / "include" / "mylib"
    inc.mkdir(parents=True)
    (inc / "Core").write_text("struct S { virtual void f(); };")  # extensionless
    (inc / "notes.md").write_text("# docs")
    found = {p.name for p in iter_source_files([tmp_path / "include"])}
    assert "Core" in found
    assert "notes.md" not in found


def test_iter_source_files_extensionless_changed_scope(tmp_path: Path) -> None:
    inc = tmp_path / "include" / "mylib"
    inc.mkdir(parents=True)
    (inc / "Core").write_text("struct S { virtual void f(); };")
    found = iter_source_files(
        [tmp_path / "include"], changed_paths=["include/mylib/Core"]
    )
    assert [p.name for p in found] == ["Core"]


def test_iter_source_files_explicit_file_honored_regardless_of_suffix(
    tmp_path: Path,
) -> None:
    f = tmp_path / "PublicHeader"  # extensionless, passed directly
    f.write_text("struct S { virtual void f(); };")
    found = iter_source_files([f])
    assert found == [f]


def test_scan_files_finds_constructs_in_extensionless_header(tmp_path: Path) -> None:
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "Core").write_text("#pragma pack(1)\nstruct S { int x; };")
    res = scan_files([inc], changed_paths=["include/Core"])
    assert res.files_scanned == 1
    assert PatternKind.PRAGMA_PACK in {f.kind for f in res.facts}


def test_iter_source_files_changed_scope_bare_name(tmp_path: Path) -> None:
    (tmp_path / "public.h").write_text("int p;")
    (tmp_path / "other.h").write_text("int o;")
    found = {p.name for p in iter_source_files([tmp_path], changed_paths=["public.h"])}
    assert found == {"public.h"}


def test_scan_files_aggregates_and_records_paths(tmp_path: Path) -> None:
    h = tmp_path / "api.h"
    h.write_text('extern "C" void f();\n#pragma pack(1)\nstruct S { int x; };')
    res = scan_files([tmp_path])
    assert res.files_scanned == 1
    assert res.files_skipped == 0
    assert any(f.path.endswith("api.h") for f in res.facts)
    assert PatternKind.PRAGMA_PACK in {f.kind for f in res.facts}
    assert res.should_escalate is True


def test_scan_files_skips_missing_root_gracefully(tmp_path: Path) -> None:
    res = scan_files([tmp_path / "does-not-exist"])
    assert res.files_scanned == 0
    assert res.facts == []
    assert res.coverage().status is CoverageStatus.NOT_COLLECTED


def test_scan_files_counts_unreadable_as_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.h").write_text("int x;")

    def _boom(self: Path, *a: object, **k: object) -> str:
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "read_text", _boom)
    res = scan_files([tmp_path])
    assert res.files_scanned == 0
    assert res.files_skipped == 1
    assert res.coverage().status is CoverageStatus.NOT_COLLECTED


# ── Char literals and escaped strings don't derail the scanner ───────────────


def test_char_literal_with_escape_ignored() -> None:
    # The quote/keyword inside a char literal must not be matched, and the
    # escaped-char path in the blanker must keep line accounting intact.
    src = "char q = '\"';\nchar n = '\\n';\nstruct alignas(4) S {};"
    facts = [f for f in scan_text(src) if f.kind is PatternKind.ALIGNAS]
    assert len(facts) == 1
    assert facts[0].line == 3


def test_escaped_quote_in_string_then_extern_c() -> None:
    # A string with an escaped quote (exercises the string-preserving escape
    # branch) followed by a real `extern "C"`.
    src = 'const char* s = "a\\"b";\nextern "C" void f();'
    kinds = _kinds(src)
    assert PatternKind.EXTERN_C in kinds
