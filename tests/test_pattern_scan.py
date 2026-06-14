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
        ('[[gnu::visibility("default")]] void f();', PatternKind.ATTRIBUTE_VISIBILITY),
        ("__declspec(dllexport) int api(void);", PatternKind.DECLSPEC_DLLEXPORT),
        ("__declspec(dllimport) int api(void);", PatternKind.DECLSPEC_DLLIMPORT),
        ('extern "C" void c_api(void);', PatternKind.EXTERN_C),
        ("int __stdcall winproc(void);", PatternKind.CALLING_CONVENTION),
        ("int WINAPI WinMain(void);", PatternKind.CALLING_CONVENTION),
        ("inline namespace v1 { struct S {}; }", PatternKind.INLINE_NAMESPACE),
        ("struct Base { virtual void f(); };", PatternKind.VIRTUAL_METHOD),
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
    "definition",
    [
        "template <typename T> class Foo {};",
        "template<typename T> void f(T x);",
        "template <class T, class U> struct Pair {};",
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
    ],
)
def test_dependent_template_disambiguator_not_flagged(disambiguator: str) -> None:
    # `x.template f<...>()` / `p->template ...` is a member-access disambiguator,
    # not an explicit instantiation.
    assert PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION not in _kinds(disambiguator)


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


def test_iter_source_files_changed_scope(tmp_path: Path) -> None:
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "public.h").write_text("int p;")
    (inc / "other.h").write_text("int o;")
    found = {
        p.name for p in iter_source_files([inc], changed_paths=["include/public.h"])
    }
    assert found == {"public.h"}


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
