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

"""Tests for the ADR-035 D4 intra-version cross-source validation engine.

Each cross-check (``exported_not_public``, ``public_not_exported``,
``header_build_context_mismatch``, ``private_header_leak``) has positive and
negative fixtures, plus the coverage-honesty contract: a check whose evidence is
absent is reported skipped, never emits a finding, and never reads as clean.
Pure-Python, no external tools — runs in the default lane.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, BuildOption
from abicheck.buildsource.crosscheck import (
    ALL_CHECKS,
    CHECK_COMPILE_CONTEXT_CONFLICT,
    CHECK_EXPORTED_NOT_PUBLIC,
    CHECK_HEADER_BUILD_CONTEXT_MISMATCH,
    CHECK_IDENTITY_COLLISION,
    CHECK_ODR_TYPE_VARIANT,
    CHECK_PRIVATE_HEADER_LEAK,
    CHECK_PUBLIC_NOT_EXPORTED,
    CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY,
    CHECK_RTTI_FOR_INTERNAL_TYPE,
    CHECK_SOURCE_SURFACE_DSO_MISMATCH,
    CHECK_UNVERSIONED_EXPORTED_SYMBOL,
    CROSSCHECK_VERSION,
    PROVIDER_BINARY_EXPORTS,
    PROVIDER_BUILD_CONFIG,
    PROVIDER_PUBLIC_HEADER_AST,
    PROVIDER_SOURCE_INDEX,
    CrosscheckConfig,
    run_crosschecks,
)
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import SourceAbiSurface
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.checker_policy import ChangeKind, Confidence, Verdict
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.macho_metadata import MachoExport, MachoMetadata
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Variable,
    Visibility,
)
from abicheck.pe_metadata import PeExport, PeMetadata

# --------------------------------------------------------------------------- #
# fixtures / builders
# --------------------------------------------------------------------------- #


def _snap(**kw) -> AbiSnapshot:
    kw.setdefault("library", "libfoo.so")
    kw.setdefault("version", "1.0")
    kw.setdefault("from_headers", True)
    return AbiSnapshot(**kw)


def _elf(*names: str) -> ElfMetadata:
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names])


def _findings_of(result, kind: ChangeKind):
    return [c for c in result.findings if c.kind == kind]


def _coverage(result, check: str) -> dict:
    row = next(r for r in result.coverage if r["layer"] == f"crosscheck:{check}")
    return row


# --------------------------------------------------------------------------- #
# exported_not_public
# --------------------------------------------------------------------------- #


def test_exported_not_public_flags_export_only_symbol():
    snap = _snap(elf=_elf("_Z3fooi", "_Z6secretv"))
    snap.functions = [
        Function(
            name="foo",
            mangled="_Z3fooi",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
        Function(
            name="secret",
            mangled="_Z6secretv",
            return_type="void",
            origin=ScopeOrigin.EXPORT_ONLY,
        ),
    ]
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)
    assert [c.symbol for c in hits] == ["_Z6secretv"]
    assert hits[0].confidence == Confidence.HIGH
    assert _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["status"] == "present"
    assert res.providers[CHECK_EXPORTED_NOT_PUBLIC] == [
        PROVIDER_BINARY_EXPORTS,
        PROVIDER_PUBLIC_HEADER_AST,
    ]


def test_exported_not_public_covers_variables():
    snap = _snap(elf=_elf("g_secret"))
    snap.variables = [
        Variable(
            name="g_secret",
            mangled="g_secret",
            type="int",
            origin=ScopeOrigin.EXPORT_ONLY,
        ),
    ]
    res = run_crosschecks(snap)
    assert len(_findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)) == 1


def test_exported_not_public_flags_elf_only_visibility_symbol():
    # Real export-only symbols carry Visibility.ELF_ONLY (not PUBLIC) — the
    # provenance pass only tags EXPORT_ONLY for ELF_ONLY-visibility decls, so the
    # check must not require PUBLIC visibility (Codex review).
    snap = _snap(elf=_elf("_Z6secretv"))
    snap.functions = [
        Function(
            name="secret",
            mangled="_Z6secretv",
            return_type="void",
            visibility=Visibility.ELF_ONLY,
            origin=ScopeOrigin.EXPORT_ONLY,
        ),
    ]
    res = run_crosschecks(snap)
    assert [c.symbol for c in _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)] == [
        "_Z6secretv"
    ]


def test_exported_not_public_flags_exported_private_header_symbol():
    # A symbol declared only in a private header (origin PRIVATE_HEADER, not
    # EXPORT_ONLY) but actually exported is undocumented ABI surface too (Codex
    # review). An un-exported private decl must NOT be flagged.
    snap = _snap(elf=_elf("_Z8exportedv"))
    snap.functions = [
        Function(
            name="exported",
            mangled="_Z8exportedv",
            return_type="void",
            origin=ScopeOrigin.PRIVATE_HEADER,
        ),
        Function(
            name="internal",
            mangled="_Z8internalv",
            return_type="void",
            origin=ScopeOrigin.PRIVATE_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert [c.symbol for c in _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)] == [
        "_Z8exportedv"
    ]


def test_exported_not_public_flags_export_with_no_decl_object():
    # In a header-backed dump castxml only emits decls it parsed; a symbol that
    # lives ONLY in the export table has no Function object, so the check must be
    # driven by the export table itself (Codex review).
    snap = _snap(elf=_elf("_Z3fooi", "_Z6secretv"))
    snap.functions = [
        Function(
            name="foo",
            mangled="_Z3fooi",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
        # No object exists for _Z6secretv — it is only in the export table.
    ]
    res = run_crosschecks(snap)
    assert [c.symbol for c in _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)] == [
        "_Z6secretv"
    ]


def test_exported_not_public_skips_constructor_exports():
    # castxml leaves ctors/dtors unmangled, so an exported _ZN6WidgetC1Ev would
    # never match the class's decls; skip structor exports to avoid a false
    # positive (Codex review).
    snap = _snap(elf=_elf("_ZN6WidgetC1Ev", "_ZN6WidgetD1Ev"))
    snap.functions = [
        Function(
            name="Widget::Widget",
            mangled="Widget",
            return_type="",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC) == []


def test_exported_not_public_cxx_variable_does_not_document_bare_name():
    # A public C++ global `g` exports as `_Z1g`, not `g`. An unrelated accidental
    # export literally named `g` must NOT be treated as documented by the public
    # variable (Codex review).
    snap = _snap(elf=_elf("_Z1g", "g"))
    snap.variables = [
        Variable(
            name="g",
            mangled="_Z1g",
            type="int",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert [c.symbol for c in _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)] == [
        "g"
    ]


def test_exported_not_public_skips_msvc_constructor_exports():
    # MSVC decorates ctors as ??0.. / dtors as ??1.. while castxml leaves the
    # header-side member unmangled; skip them to avoid a false positive (Codex
    # review).
    snap = _snap(pe=PeMetadata(exports=[PeExport(name="??0Widget@@QEAA@XZ")]))
    snap.functions = [
        Function(
            name="Widget::Widget",
            mangled="Widget",
            return_type="",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC) == []


def test_exported_not_public_skips_rtti_and_vtable_exports():
    # A public polymorphic class exports _ZTV/_ZTI/_ZTS; castxml records it as a
    # RecordType (not a Function/Variable), so these compiler artifacts must be
    # exempt, not reported as undocumented (Codex review).
    snap = _snap(elf=_elf("_Z3fooi", "_ZTV6Widget", "_ZTI6Widget", "_ZTS6Widget"))
    snap.functions = [
        Function(
            name="foo",
            mangled="_Z3fooi",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(name="Widget", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC) == []


def test_exported_not_public_clean_when_everything_declared():
    snap = _snap(elf=_elf("_Z3fooi"))
    snap.functions = [
        Function(
            name="foo",
            mangled="_Z3fooi",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC) == []
    assert _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["status"] == "present"


# --------------------------------------------------------------------------- #
# exported_not_public — precise export accounting (ADR-035 D4)
# --------------------------------------------------------------------------- #


def _public_fn() -> Function:
    """A documented public function so provenance is resolvable (``_Z3fooi``)."""
    return Function(
        name="foo",
        mangled="_Z3fooi",
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def test_exported_not_public_marks_external_dependency_leak():
    # A leaked libstdc++ symbol (``_ZNSt…``) is not this library's API: it must be
    # accounted as an external dependency, name the originating library, and say so
    # in the message — a maintainer fixes a leak differently from an API mistake.
    snap = _snap(elf=_elf("_Z3fooi", "_ZNSt6vectorIiSaIiEE9push_backEOi"))
    snap.functions = [
        Function(
            name="foo",
            mangled="_Z3fooi",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    hits = _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)
    assert [c.symbol for c in hits] == ["_ZNSt6vectorIiSaIiEE9push_backEOi"]
    assert hits[0].old_value == "libstdc++.so.6"
    assert "external dependency" in hits[0].description
    assert "libstdc++.so.6" in hits[0].description
    counters = _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["counters"]
    assert counters["external_dependency"] == 1
    assert counters["documented_public_api"] == 1


def test_exported_not_public_marks_vendored_third_party():
    # A statically-linked, re-exported {fmt} symbol is a vendored third-party leak.
    snap = _snap(elf=_elf("_Z3fooi", "_ZN3fmt3v106detail11format_errorEPKc"))
    snap.functions = [_public_fn()]
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    hits = _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)
    assert len(hits) == 1
    assert hits[0].old_value == "{fmt} (vendored third-party)"
    counters = _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["counters"]
    assert counters["external_dependency"] == 1


def test_exported_not_public_marks_internal_namespace():
    # An export in the library's own ``::impl`` namespace is a visibility leak,
    # distinct from an external dependency: no origin lib, internal_namespace bucket.
    snap = _snap(elf=_elf("_Z3fooi", "_ZN3lib4impl6secretEv"))
    snap.functions = [_public_fn()]
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    hits = _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)
    assert len(hits) == 1
    assert hits[0].old_value is None
    assert "internal namespace" in hits[0].description
    counters = _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["counters"]
    assert counters["internal_namespace"] == 1


def test_exported_not_public_marks_template_instantiation():
    # An exported template instantiation (Itanium ``I…E`` args) with no matching
    # public decl is its own accounted reason, not a bare undeclared export.
    snap = _snap(elf=_elf("_Z3fooi", "_ZN3lib9transformIdEEvT_"))
    snap.functions = [_public_fn()]
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    hits = _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)
    assert len(hits) == 1
    counters = _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["counters"]
    assert counters["template_instantiation"] == 1


def test_exported_not_public_malformed_long_length_is_conservative():
    # Malformed Itanium length fields come from untrusted export tables. They must
    # not abort the audit when the digit run exceeds Python's int-string limit.
    malformed = "_ZN" + ("9" * 5000) + "Av"
    snap = _snap(elf=_elf("_Z3fooi", malformed))
    snap.functions = [_public_fn()]
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    hits = _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)
    assert [c.symbol for c in hits] == [malformed]
    counters = _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["counters"]
    assert counters["undeclared_export"] == 1


@pytest.mark.parametrize(
    "text, start, expected",
    [
        ("3foo", 0, (3, 1)),
        ("x03foo", 1, (3, 3)),
        ("0", 0, (0, 1)),
        ("foo", 0, None),
        ("3foo", 4, None),
        ("٣foo", 0, None),  # Itanium lengths are ASCII decimal digits only.
    ],
)
def test_read_decimal_length(text, start, expected):
    from abicheck.buildsource.export_accounting import _read_decimal_length

    assert _read_decimal_length(text, start) == expected


def test_read_decimal_length_consumes_overlong_digit_run_without_int_conversion():
    from abicheck.buildsource.export_accounting import _read_decimal_length

    digits = "9" * 5000
    length, end = _read_decimal_length(digits + "name", 0) or (None, None)
    assert length is not None and length > len(digits) + 4
    assert end == len(digits)


def test_exported_not_public_accounting_sums_to_all_exports():
    # The accounting partitions the whole export table — documented API +
    # compiler artifact + every undocumented reason == number of exports, so the
    # report can honestly state "100 % accounted".
    snap = _snap(
        elf=_elf(
            "_Z3fooi",  # documented
            "_ZTV6Widget",  # cxx artifact
            "_ZNSt6vectorIiSaIiEE9push_backEOi",  # external dependency
            "_ZN3lib4impl6secretEv",  # internal namespace
            "_ZN3lib9transformIdEEvT_",  # template instantiation
            "raw_entry",  # undeclared
        )
    )
    snap.functions = [
        Function(
            name="foo",
            mangled="_Z3fooi",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(name="Widget", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER),
    ]
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    counters = _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["counters"]
    assert sum(counters.values()) == 6
    assert counters == {
        "documented_public_api": 1,
        "cxx_abi_artifact": 1,
        "external_dependency": 1,
        "internal_namespace": 1,
        "template_instantiation": 1,
        "undeclared_export": 1,
    }


def test_exported_not_public_leaked_dependency_rtti_is_external_not_artifact():
    # Regression (Codex review): a leaked libstdc++/{fmt} vtable or typeinfo is
    # checked for external origin BEFORE the C++ compiler-artifact exemption, so it
    # counts as the leaked surface these counters measure — not silently exempted
    # as a legitimate class artifact. A *native* class's vtable still is exempted.
    snap = _snap(
        elf=_elf(
            "_Z3fooi",  # documented
            "_ZTVNSt7__cxx1112basic_stringIcEE",  # leaked std vtable -> external
            "_ZTIN3fmt3v106detail5errorE",  # leaked {fmt} typeinfo -> external
            "_ZThn16_N3fmt3v106detail5errorE",  # leaked {fmt} thunk -> external
            "_ZTV6Widget",  # native class vtable -> cxx artifact
            "_ZThn8_N6Widget3fooEv",  # native class thunk -> cxx artifact
        )
    )
    snap.functions = [_public_fn()]
    snap.types = [
        RecordType(name="Widget", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER),
    ]
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    counters = _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["counters"]
    assert counters["external_dependency"] == 3  # std vtable + fmt typeinfo + fmt thunk
    assert counters["cxx_abi_artifact"] == 2  # native Widget vtable + native thunk
    ext = {
        c.symbol: c.old_value for c in _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)
    }
    assert ext["_ZTVNSt7__cxx1112basic_stringIcEE"] == "libstdc++.so.6"
    assert ext["_ZTIN3fmt3v106detail5errorE"] == "{fmt} (vendored third-party)"
    assert ext["_ZThn16_N3fmt3v106detail5errorE"] == "{fmt} (vendored third-party)"


@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("_ZNSt6vectorIiSaIiEE9push_backEOi", "libstdc++.so.6"),  # std prefix
        ("_ZTVNSt7__cxx1112basic_stringIcEE", "libstdc++.so.6"),  # leaked std vtable
        ("_ZTISt9exception", "libstdc++.so.6"),  # leaked std typeinfo
        # an un-nested std substitution the prefix table misses (Ss = std::string)
        # resolves via the owner path.
        ("_ZSsD1Ev", "libstdc++.so.6"),
        # std forms the _guess_symbol_origin prefix table misses, caught by the
        # owner-namespace check: a std guard variable and the libstdc++
        # implementation namespaces (__gnu_cxx / __cxxabiv1).
        ("_ZGVZNSt3_V216generic_categoryEvE7c", "libstdc++.so.6"),
        ("_ZN9__gnu_cxx17__normal_iteratorEv", "libstdc++.so.6"),
        ("_ZN10__cxxabiv117__class_type_infoE", "libstdc++.so.6"),
        ("_ZGVZN3fmt3v107formatterISt6localeEE", "{fmt} (vendored third-party)"),
        ("_ZN5boost6system10error_codeC1Ev", "Boost (vendored third-party)"),
        ("_ZN4absl4TimeEv", "Abseil (vendored third-party)"),
        # CV-/ref-qualified member exports keep their namespace owner (Codex review).
        ("_ZNK3fmt9formatterEv", "{fmt} (vendored third-party)"),
        ("_ZNKO5boost3barEv", "Boost (vendored third-party)"),
        # thunks: the numeric call-offset before the operand must be peeled so the
        # dependency owner is still read (Codex review).
        ("_ZThn16_N3fmt3v106detail5errorE", "{fmt} (vendored third-party)"),
        ("_ZTv0_n24_N5boost6system5errorE", "Boost (vendored third-party)"),
        ("_ZThn8_N6Widget3fooEv", None),  # native thunk -> stays a class artifact
        # a native internal symbol that merely *references* std in a parameter is
        # NOT external — the owner (dnnl), not the argument type, decides.
        ("_ZN4dnnl4impl3fooENSt7__cxx1112basic_stringIcEE", None),
        ("_ZN3lib3barEv", None),  # native, nested
        ("_Z3fooi", None),  # native, non-nested (_Z, not _ZN)
        # an un-nested global named after a vendor (a *function* fmt(), not the fmt
        # namespace) has no namespace owner and must not be a vendored leak (Codex).
        ("_Z3fmtv", None),
        ("_Z6googlev", None),
        ("_ZTV3Foo", None),  # vtable for a top-level class — no namespace owner
        # construction vtable (``_ZTC``) for a leaked dependency type: the operand
        # nested name must be peeled so the {fmt} owner is read (Codex review).
        ("_ZTCN3fmt3FooE0_NS_3BarE", "{fmt} (vendored third-party)"),
        ("_ZTCN5boost3FooE0_NS_3BarE", "Boost (vendored third-party)"),
        ("_ZTC3Foo0_3Bar", None),  # native construction vtable — no namespace owner
        ("_ZN", None),  # degenerate — owner unparseable
        ("plain_c_symbol", None),  # not mangled
    ],
)
def test_external_dependency_origin_owner_based(symbol, expected):
    from abicheck.buildsource.crosscheck import _external_dependency_origin

    assert _external_dependency_origin(symbol, ["libstdc++.so.6"]) == expected


@pytest.mark.parametrize(
    "symbol, needed, expected",
    [
        # libc++ std uses the std::__1 inline namespace: a leaked std guard var the
        # prefix table misses must name libc++, not libstdc++ (Codex review).
        ("_ZGVZNSt3__116generic_categoryEvE3loc", ["libc++.so.1"], "libc++.so.1"),
        # No __1 marker but the binary links libc++ -> prefer libc++.
        ("_ZN10__cxxabiv117__class_type_infoE", ["libc++.so.1"], "libc++.so.1"),
        # __gnu_cxx is libstdc++-only, even alongside a libc++ DT_NEEDED.
        ("_ZN9__gnu_cxx17__normal_iteratorEv", ["libc++.so.1"], "libstdc++.so.6"),
        # no __1 marker, libc++ absent: skip past a non-runtime DT_NEEDED entry and
        # pick the linked libstdc++.
        (
            "_ZGVZNSt3_V216generic_categoryEvE7c",
            ["libz.so.1", "libstdc++.so.6"],
            "libstdc++.so.6",
        ),
        # neither C++ runtime linked -> fall back to the libstdc++ default.
        ("_ZGVZNSt3_V216generic_categoryEvE7c", ["libz.so.1"], "libstdc++.so.6"),
        # Mach-O libc++ names the actual loaded dylib, not a hard-coded ELF soname.
        (
            "_ZGVZNSt3__116generic_categoryEvE3loc",
            ["/usr/lib/libc++.1.dylib"],
            "libc++.1.dylib",
        ),
        # a libc++ marker skips a non-runtime dependency before matching the dylib.
        (
            "_ZGVZNSt3__116generic_categoryEvE3loc",
            ["libz.so.1", "/usr/lib/libc++.1.dylib"],
            "libc++.1.dylib",
        ),
        # a libc++ marker with no dependency list falls back to the canonical soname.
        ("_ZGVZNSt3__116generic_categoryEvE3loc", [], "libc++.so.1"),
        # libc++abi is a *different* runtime — a std::__1 symbol must resolve to
        # libc++ even when libc++abi precedes it in the dependency list (Codex).
        (
            "_ZGVZNSt3__116generic_categoryEvE3loc",
            ["libc++abi.so.1", "libc++.so.1"],
            "libc++.so.1",
        ),
        # even a plain std::__1 export the prefix table resolves to libc++abi first
        # is normalized to the real libc++ runtime (Codex review).
        (
            "_ZNSt3__16vectorIiEE9push_backEOi",
            ["libc++abi.so.1", "libc++.so.1"],
            "libc++.so.1",
        ),
        # a __cxxabiv1 ABI symbol is owned by libc++abi (NOT excluded like the std
        # runtime), preferring the linked ABI library, else libstdc++ (Codex review).
        (
            "_ZN10__cxxabiv121__vmi_class_type_infoD2Ev",
            ["libc++abi.so.1", "libc++.so.1"],
            "libc++abi.so.1",
        ),
        (
            "_ZN10__cxxabiv121__vmi_class_type_infoD2Ev",
            ["libstdc++.so.6"],
            "libstdc++.so.6",
        ),
        # no C++ runtime linked at all -> libstdc++ default.
        ("_ZN10__cxxabiv121__vmi_class_type_infoD2Ev", [], "libstdc++.so.6"),
        # a libc++ toolchain without a separate libc++abi still owns its ABI symbols.
        (
            "_ZN10__cxxabiv121__vmi_class_type_infoD2Ev",
            ["libc++.so.1"],
            "libc++.so.1",
        ),
        # covariant thunk with h/v-tagged call-offsets still resolves its owner.
        ("_ZTchn16_h16_N3fmt3v105eventE", [], "{fmt} (vendored third-party)"),
    ],
)
def test_external_dependency_origin_runtime_and_covariant_thunk(
    symbol, needed, expected
):
    from abicheck.buildsource.crosscheck import _external_dependency_origin

    assert _external_dependency_origin(symbol, needed) == expected


def test_linked_library_names_across_platforms():
    # The linked-library list is gathered from whichever binary format the snapshot
    # carries (ELF DT_NEEDED / Mach-O LC_LOAD_DYLIB / PE imports) so the C++-runtime
    # picker can name the real dependency on each platform.
    from abicheck.buildsource.crosscheck import _linked_library_names

    elf_snap = _snap(elf=ElfMetadata(symbols=[], needed=["libstdc++.so.6"]))
    assert _linked_library_names(elf_snap) == ["libstdc++.so.6"]

    macho_snap = _snap(
        macho=MachoMetadata(exports=[], dependent_libs=["/usr/lib/libc++.1.dylib"])
    )
    assert _linked_library_names(macho_snap) == ["/usr/lib/libc++.1.dylib"]

    pe_snap = _snap(pe=PeMetadata(exports=[], imports={"msvcp140.dll": ["?x@@"]}))
    assert _linked_library_names(pe_snap) == ["msvcp140.dll"]


def test_external_dependency_origin_ignores_audited_library_own_namespace():
    # Auditing a vendored library itself (libfmt): its own ``fmt::detail`` symbols
    # are native, not a leaked dependency — the vendored-namespace fallback is gated
    # on the audited library's identity (Codex review).
    from abicheck.buildsource.crosscheck import (
        _external_dependency_origin,
        _library_self_names,
    )

    sym = "_ZN3fmt6detail6secretEv"
    # libfmt scanning itself -> native (no external finding).
    assert _external_dependency_origin(sym, [], ("libfmt.so.9",)) is None
    # a different library that statically linked and re-exported fmt -> leak.
    assert (
        _external_dependency_origin(sym, [], ("libmylib.so.1",))
        == "{fmt} (vendored third-party)"
    )
    # a wrapper/plugin whose name merely *contains* the token is NOT self — its
    # leaked fmt surface must still flag (boundary-aware match, Codex review).
    assert (
        _external_dependency_origin(sym, [], ("libfmtshim.so",))
        == "{fmt} (vendored third-party)"
    )
    # a C++ library whose soname carries ``+`` (``libgrpc++.so``) is self for the
    # ``grpc`` owner — its own ``grpc::`` surface is native, not a vendored leak
    # (the ``+`` must be a recognised stem boundary, Codex review).
    grpc_sym = "_ZN4grpc6Status2OKEv"
    assert _external_dependency_origin(grpc_sym, [], ("libgrpc++.so",)) is None
    assert _external_dependency_origin(grpc_sym, [], ("libgrpc++.so.1",)) is None
    # but a plain wrapper that merely re-exports grpc still flags.
    assert (
        _external_dependency_origin(grpc_sym, [], ("libmyplugin.so",))
        == "gRPC (vendored third-party)"
    )
    # a per-component vendored lib (libboost_system) scanning its own boost:: is
    # still recognised as self.
    assert (
        _external_dependency_origin(
            "_ZN5boost6system3barEv", [], ("libboost_system.so.1",)
        )
        is None
    )
    # protobuf's google::protobuf namespace ships in libprotobuf — auditing it is
    # self, not a leaked Google/protobuf dependency (Codex review).
    assert (
        _external_dependency_origin(
            "_ZN6google8protobuf7MessageEv", [], ("libprotobuf.so.32",)
        )
        is None
    )
    assert (
        _external_dependency_origin(
            "_ZN6google8protobuf7MessageEv", [], ("libother.so",)
        )
        == "Google/protobuf (vendored third-party)"
    )
    # protobuf's library is libprotobuf, not any libgoogle_* — a wrapper like
    # libgoogle_cloud_cpp that re-exports protobuf must still flag (Codex review).
    assert (
        _external_dependency_origin(
            "_ZN6google8protobuf7MessageEv", [], ("libgoogle_cloud_cpp.so",)
        )
        == "Google/protobuf (vendored third-party)"
    )
    # ``google::`` is shared by many Google libraries — only google::protobuf is
    # protobuf. glog's google::LogMessage / gflags are native, not a leak (Codex).
    assert (
        _external_dependency_origin("_ZN6google10LogMessageEv", [], ("libglog.so",))
        is None
    )
    # auditing a runtime library itself: its own std/runtime exports are native,
    # not a leak — the self gate covers the _guess_symbol_origin path too (Codex).
    assert (
        _external_dependency_origin(
            "_ZNSt6vectorIiEE9push_backEOi", ["libstdc++.so.6"], ("libstdc++.so.6",)
        )
        is None
    )
    assert (
        _external_dependency_origin(
            "_ZNSt6vectorIiEE9push_backEOi", ["libstdc++.so.6"], ("libmylib.so",)
        )
        == "libstdc++.so.6"
    )
    # the owner-fallback runtime path (a guard variable / __gnu_cxx form the prefix
    # table misses) is self-gated the same way (Codex review).
    assert (
        _external_dependency_origin(
            "_ZGVZNSt3_V216generic_categoryEvE7c", [], ("libstdc++.so.6",)
        )
        is None
    )
    assert (
        _external_dependency_origin("_ZN9__gnu_cxx5xyz_Ev", [], ("libstdc++.so.6",))
        is None
    )
    # auditing libc++abi itself (no self-dep in DT_NEEDED): its own __cxxabiv1 ABI
    # exports are native (Codex review).
    assert (
        _external_dependency_origin(
            "_ZN10__cxxabiv121__vmi_class_type_infoD2Ev", [], ("libc++abi.so.1",)
        )
        is None
    )
    assert (
        _external_dependency_origin(
            "_ZN6google20ParseCommandLineFlagsEPiPPPcb", [], ("libgflags.so",)
        )
        is None
    )
    # self-names are derived from library name / soname / Mach-O install-name.
    snap = _snap(library="libfmt", elf=ElfMetadata(symbols=[], soname="libfmt.so.9"))
    assert set(_library_self_names(snap)) == {"libfmt", "libfmt.so.9"}
    macho_snap = _snap(
        library="",
        macho=MachoMetadata(exports=[], install_name="/usr/lib/libboost.dylib"),
    )
    assert _library_self_names(macho_snap) == ("libboost.dylib",)


@pytest.mark.parametrize(
    "symbol, self_names, expected",
    [
        # MSVC scopes are inner-to-outer, so the top-level namespace is the last
        # ``@``-component. A statically re-exported Boost/{fmt}/protobuf symbol on
        # Windows is a vendored-dependency leak, not an undeclared export (Codex).
        ("?bar@fmt@@YAXXZ", (), "{fmt} (vendored third-party)"),
        ("?foo@system@boost@@YAXXZ", (), "Boost (vendored third-party)"),
        (
            "?Msg@protobuf@google@@YAXXZ",
            (),
            "Google/protobuf (vendored third-party)",
        ),
        # a bare ``google::`` scope (glog/gflags) is not a protobuf leak.
        ("?LogMessage@google@@YAXXZ", (), None),
        # an MSVC ``std::`` symbol stays native here (MSVC STL attribution is a
        # separate concern; not mislabelled with an ELF soname).
        ("?x@std@@YAXXZ", (), None),
        # an un-nested MSVC name (no enclosing scope) has no owner.
        ("?globalfunc@@YAXXZ", (), None),
        # ctor/special-name form still resolves its outer vendored scope.
        ("??0Foo@boost@@QEAA@XZ", (), "Boost (vendored third-party)"),
        # auditing the vendored library itself (a Windows fmt.dll): native, not a
        # leak — the self gate covers MSVC owners too.
        ("?bar@fmt@@YAXXZ", ("fmt.dll",), None),
        # not an MSVC name -> no MSVC owner path.
        ("plain_c_symbol", (), None),
    ],
)
def test_external_dependency_origin_msvc_scopes(symbol, self_names, expected):
    from abicheck.buildsource.crosscheck import _external_dependency_origin

    assert _external_dependency_origin(symbol, [], self_names) == expected


@pytest.mark.parametrize(
    "symbol, index, expected",
    [
        ("_ZN6google8protobuf7MessageEv", 0, "google"),
        ("_ZN6google8protobuf7MessageEv", 1, "protobuf"),
        ("_ZN6google8protobuf7MessageEv", 2, "Message"),
        ("_ZN6google8protobuf7MessageEv", 3, None),  # past the last component
        # template arguments on a component are skipped, not counted as components.
        ("_ZN3lib3BoxIiE3barEv", 1, "Box"),
        ("_ZN3lib3BoxIiE3barEv", 2, "bar"),
        ("_ZN999nameE", 0, None),  # length runs beyond malformed input
        ("_ZN" + ("9" * 5000) + "nameE", 0, None),
        ("_ZN٣nameE", 0, None),  # non-ASCII digit is not a length field
        ("_Z3fooi", 0, None),  # un-nested name has no nested components
    ],
)
def test_nested_component(symbol, index, expected):
    from abicheck.buildsource.export_accounting import _nested_component

    assert _nested_component(symbol, index) == expected


@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("_ZN3lib4impl6secretEv", "internal_namespace"),
        ("_ZN3lib8internal6secretEv", "internal_namespace"),
        ("_ZN12_GLOBAL__N_13fooEv", "internal_namespace"),
        # a parameter type referencing an internal namespace does NOT make the
        # exported entity internal — only the entity's own name counts (Codex).
        ("_ZN3lib3fooEPN3lib6detail4TypeE", "undeclared_export"),
        # a name merely *containing* an internal token is not internal — the match
        # is against a whole component, not a substring (Codex review).
        ("_ZN3lib6SimpleEv", "undeclared_export"),  # "Simple" contains "impl"
        ("_ZN3lib11implementEv", "undeclared_export"),  # "implement" ≠ "impl"
        ("_ZN3lib9transformIdEEvT_", "template_instantiation"),
        ("_ZNSt6vectorIiEE9push_backEOi", "template_instantiation"),
        ("_Z9transformIdEv", "template_instantiation"),  # un-nested template fn
        ("_ZNK3lib9transformIdEEv", "template_instantiation"),  # const template method
        # a member of an enclosing class-template specialization is a template
        # instantiation even though the final component isn't templated (Codex).
        ("_ZN3lib3BoxIiE3barEv", "template_instantiation"),
        ("_ZNK3lib3BoxIiE3barEv", "template_instantiation"),
        # nested template arguments (Box<vector<int>>) keep the depth bookkeeping
        # correct — still one template instantiation.
        ("_ZN3lib3BoxISt6vectorIiEEE3barEv", "template_instantiation"),
        # an ``I`` *inside* an identifier is not a template — must not be
        # misclassified (Codex review).
        ("_ZL4mainv", "undeclared_export"),  # un-nested, no leading length digit
        ("_ZN3foo", "undeclared_export"),  # truncated nested name (no closing E)
        ("_ZN999nameE", "undeclared_export"),  # length runs past input
        ("_ZN" + ("9" * 5000) + "nameE", "undeclared_export"),
        ("_Z" + ("9" * 5000) + "nameI", "undeclared_export"),
        ("_ZN3lib10InitEngineEv", "undeclared_export"),
        ("_ZN3lib9InterfaceEv", "undeclared_export"),
        # a template argument in a *parameter* type does not make the entity a
        # template instantiation — the entity (lib::foo) is not one (Codex review).
        ("_ZN3lib3fooESt6vectorIiSaIiEE", "undeclared_export"),
        ("_Z3fooi", "undeclared_export"),
        ("raw_c_entry", "undeclared_export"),
        # a function whose *own name* is detail/impl but whose enclosing namespace
        # is not internal is NOT internal — only enclosing scopes count (Codex).
        ("_ZN3lib6detailEv", "undeclared_export"),  # lib::detail()
        ("_ZN3lib4implEv", "undeclared_export"),  # lib::impl()
        # MSVC decorated names keep the internal-namespace reason on PE/COFF (Codex).
        ("?secret@detail@lib@@YAXXZ", "internal_namespace"),
        ("?Compute@impl@dnnl@@YAXXZ", "internal_namespace"),
        ("?PublicApi@dnnl@@YAXXZ", "undeclared_export"),
        ("?detail@lib@@YAXXZ", "undeclared_export"),  # lib::detail() — name, not scope
    ],
)
def test_account_undocumented_export_categories(symbol, expected):
    from abicheck.buildsource.crosscheck import _account_undocumented_export

    assert _account_undocumented_export(symbol) == expected


def test_exported_not_public_allocator_interposer_is_native_not_leak():
    # A malloc-proxy library (it exports the __TBB_malloc_proxy marker) deliberately
    # replaces the global allocator; its operator new / malloc exports are native,
    # not a leaked libstdc++/libc dependency (Codex review).
    # Includes the C++14 sized delete (_ZdlPvm) and C++17 aligned new
    # (_ZnwmSt11align_val_t) forms — a proxy replaces those overloads too (Codex).
    proxy = _snap(
        elf=_elf(
            "__TBB_malloc_proxy",
            "_Znwm",
            "malloc",
            "_ZdlPvm",
            "_ZnwmSt11align_val_t",
        )
    )
    proxy.functions = [_public_fn()]
    res = run_crosschecks(proxy, CrosscheckConfig(max_per_check=0))
    counters = _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["counters"]
    assert counters.get("external_dependency", 0) == 0
    # accounted as a legitimate interposer category — and no finding advises hiding
    # the allocator replacements OR the proxy marker itself.
    assert counters.get("allocator_interposer", 0) == 5  # marker + 4 allocator hooks
    finding_syms = {c.symbol for c in _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)}
    assert not (
        {"_Znwm", "malloc", "_ZdlPvm", "_ZnwmSt11align_val_t", "__TBB_malloc_proxy"}
        & finding_syms
    )

    # The same operator new in a library that is NOT an interposer is a real leak.
    leaky = _snap(elf=_elf("_Znwm"))
    leaky.functions = [_public_fn()]
    res2 = run_crosschecks(leaky, CrosscheckConfig(max_per_check=0))
    counters2 = _coverage(res2, CHECK_EXPORTED_NOT_PUBLIC)["counters"]
    assert counters2.get("external_dependency", 0) == 1


# --------------------------------------------------------------------------- #
# public_not_exported
# --------------------------------------------------------------------------- #


def test_public_not_exported_flags_missing_symbol():
    # `bar` is declared in a public header but the binary exports only `foo`.
    snap = _snap(elf=_elf("_Z3fooi"))
    snap.functions = [
        Function(
            name="foo",
            mangled="_Z3fooi",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
        Function(
            name="bar",
            mangled="_Z3barv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
            source_location="api.h:9",
        ),
    ]
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED)
    assert [c.symbol for c in hits] == ["_Z3barv"]
    assert hits[0].source_location == "api.h:9"
    assert hits[0].confidence == Confidence.HIGH


def test_public_not_exported_reconciles_l4_variant_export():
    # Two-way reconciliation: a public ctor decl mangled `_ZN6WidgetC1Ev` whose
    # binary lists only the base-object clone `_ZN6WidgetC2Ev` is NOT missing — the
    # L4 source-linker already tied the C1 decl to the exported C2 clone. Without
    # the reconciliation set it would false-positive; with the L4 mapping attached
    # it must stay silent. A genuinely-absent decl in the same snapshot still fires.
    snap = _snap(elf=_elf("_ZN6WidgetC2Ev"))
    snap.functions = [
        Function(
            name="Widget::Widget",
            mangled="_ZN6WidgetC1Ev",  # complete-object ctor; binary lists only C2
            return_type="",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
        Function(
            name="gone",
            mangled="_Z4gonev",  # truly not exported, not reconciled
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    surface = SourceAbiSurface(library="libfoo.so")
    surface.mappings["source_decl_to_binary_symbol"] = {
        "_ZN6WidgetC1Ev": "_ZN6WidgetC2Ev",  # linker reconciled the clone
        "_Z4gonev": "",  # linker could not match it
    }
    snap.build_source = BuildSourcePack(root="", source_abi=surface)
    res = run_crosschecks(snap)
    hits = [c.symbol for c in _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED)]
    # Only the genuinely-missing symbol is flagged; the reconciled ctor is exempt.
    assert hits == ["_Z4gonev"]


def test_public_not_exported_reconciles_l4_variant_variable():
    # Parity with the function case (CodeRabbit): the same L4 reconciliation
    # suppression is applied to snapshot.variables. A public extern variable the L4
    # linker tied to a currently-exported symbol under a spelling drift (here an
    # ABI-tag) is not flagged; a genuinely-absent one still is.
    snap = _snap(elf=_elf("_ZN2ns3fooB5cxx11E"))
    snap.variables = [
        Variable(
            name="ns::foo",
            mangled="_ZN2ns3fooE",  # drifts from the exported ABI-tag spelling
            type="int",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
        Variable(
            name="ns::gone",
            mangled="_ZN2ns4goneE",  # truly not exported, not reconciled
            type="int",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    surface = SourceAbiSurface(library="libfoo.so")
    surface.mappings["source_decl_to_binary_symbol"] = {
        "_ZN2ns3fooE": "_ZN2ns3fooB5cxx11E",  # reconciled to the exported symbol
        "_ZN2ns4goneE": "",  # linker could not match it
    }
    snap.build_source = BuildSourcePack(root="", source_abi=surface)
    res = run_crosschecks(snap)
    hits = [c.symbol for c in _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED)]
    assert hits == ["_ZN2ns4goneE"]


def test_public_not_exported_reconciles_macho_underscore_variant():
    # Reconciliation keys are Mach-O-normalized: a plugin-recorded `__ZN…` decl key
    # must still exempt the L2 `_ZN…` mangled decl (Codex Mach-O normalization).
    snap = _snap(elf=_elf("_ZN1A3fooEv"))
    snap.functions = [
        Function(
            name="A::foo",
            mangled="_ZN1A3fooEv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    surface = SourceAbiSurface(library="libfoo.so")
    # The L4 linker recorded the raw Mach-O key spelling.
    surface.mappings["source_decl_to_binary_symbol"] = {
        "__ZN1A3fooEv": "_ZN1A3fooEv",
    }
    snap.build_source = BuildSourcePack(root="", source_abi=surface)
    # (This symbol IS exported here, so it would not flag anyway; the point is the
    # normalized key builds without error and is present in the reconciled set.)
    from abicheck.buildsource.crosscheck import _l4_reconciled_symbols

    assert "_ZN1A3fooEv" in _l4_reconciled_symbols(snap, {"_ZN1A3fooEv"})


def test_public_not_exported_reconciliation_ignores_stale_mapping():
    # A merge pack whose exported_symbols were pre-set is NOT relinked, so its L4
    # mapping can reference an OLDER binary. A decl mapped to a symbol the CURRENT
    # snapshot no longer exports must still be flagged — the reconciliation only
    # trusts a mapping whose target is in the current export table (Codex review).
    snap = _snap(elf=_elf("_Z4livev"))  # current binary exports only `live`
    snap.functions = [
        Function(
            name="stale",
            mangled="_Z5stalev",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    surface = SourceAbiSurface(library="libfoo.so")
    # L4 mapping from an older binary that still "exported" `stale`.
    surface.mappings["source_decl_to_binary_symbol"] = {"_Z5stalev": "_Z5stalev"}
    snap.build_source = BuildSourcePack(root="", source_abi=surface)
    res = run_crosschecks(snap)
    hits = [c.symbol for c in _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED)]
    # The stale mapping must NOT suppress the finding — `stale` is genuinely gone.
    assert hits == ["_Z5stalev"]
    # And the direct helper drops the stale key (its target is not in exports).
    from abicheck.buildsource.crosscheck import _l4_reconciled_symbols

    assert _l4_reconciled_symbols(snap, {"_Z4livev"}) == set()


def test_reconciliation_underscore_strip_is_macho_only():
    from abicheck.buildsource.crosscheck import _l4_reconciled_symbols

    # ELF: the single-underscore strip must NOT apply. A stale mapping to a
    # leading-underscore C symbol `_bar` (no longer exported) must NOT be
    # reconciled just because an unrelated `bar` is exported (Codex review).
    elf_snap = _snap(elf=_elf("bar"))
    surf = SourceAbiSurface(library="l")
    surf.mappings["source_decl_to_binary_symbol"] = {"_bar": "_bar"}
    elf_snap.build_source = BuildSourcePack(root="", source_abi=surf)
    assert _l4_reconciled_symbols(elf_snap, {"bar"}) == set()

    # Mach-O: the export table strips one underscore, so a raw `__ZN…`/`_foo`
    # mapping value still reconciles against the stripped export set.
    macho_snap = _snap(macho=MachoMetadata(exports=[MachoExport(name="__ZN1A3fooEv")]))
    surf2 = SourceAbiSurface(library="l")
    surf2.mappings["source_decl_to_binary_symbol"] = {"__ZN1A3fooEv": "__ZN1A3fooEv"}
    macho_snap.build_source = BuildSourcePack(root="", source_abi=surf2)
    # _exported_symbol_names strips one underscore → {"_ZN1A3fooEv"}; the mapping
    # value "__ZN1A3fooEv" reconciles via the Mach-O strip.
    assert _l4_reconciled_symbols(macho_snap, {"_ZN1A3fooEv"}) == {"_ZN1A3fooEv"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: setattr(f, "is_inline", True),
        lambda f: setattr(f, "is_pure_virtual", True),
        lambda f: setattr(f, "is_deleted", True),
        lambda f: setattr(f, "is_static", True),
        lambda f: setattr(f, "access", AccessLevel.PRIVATE),
        lambda f: setattr(f, "mangled", ""),
        lambda f: setattr(f, "name", "vec<int>"),
    ],
)
def test_public_not_exported_excludes_non_exporting_decls(mutate):
    # A declaration without an export obligation must never trip the check.
    snap = _snap(elf=_elf("_Z3fooi"))
    fn = Function(
        name="bar",
        mangled="_Z3barv",
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    mutate(fn)
    snap.functions = [fn]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED) == []


@pytest.mark.parametrize("vis", [Visibility.HIDDEN, Visibility.ELF_ONLY])
def test_public_not_exported_flags_non_public_visibility(vis):
    # castxml derives visibility from the export table, so a public-header decl
    # that the binary fails to export is HIDDEN/ELF_ONLY here — it must still be
    # flagged, not skipped on visibility (Codex review).
    snap = _snap(elf=_elf("_Z3fooi"))
    snap.functions = [
        Function(
            name="bar",
            mangled="_Z3barv",
            return_type="void",
            visibility=vis,
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert [c.symbol for c in _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED)] == [
        "_Z3barv"
    ]


def test_public_not_exported_skips_member_with_mangle_fallback():
    # castxml can leave a C++ ctor unmangled (mangled == display name); comparing
    # that bare name against the binary's real _ZN6WidgetC1Ev would false-positive,
    # so a non-extern-C decl without a real mangled symbol has no obligation
    # (Codex review).
    snap = _snap(elf=_elf("_ZN6WidgetC1Ev"))
    snap.functions = [
        Function(
            name="Widget::Widget",
            mangled="Widget",  # castxml fallback, not a real symbol
            return_type="",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED) == []


def test_public_not_exported_ignores_non_default_version_alias():
    # `foo` exists only as a non-default version alias (foo@LIB_1); an unversioned
    # consumer needs a default foo@@... export, so the header decl is unsatisfied
    # and must still be flagged (Codex review).
    snap = _snap(
        elf=ElfMetadata(
            symbols=[ElfSymbol(name="foo", version="LIB_1", is_default=False)]
        )
    )
    snap.functions = [
        Function(
            name="foo",
            mangled="foo",
            return_type="void",
            is_extern_c=True,
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert [c.symbol for c in _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED)] == [
        "foo"
    ]
    # A default-versioned export of the same name DOES satisfy the obligation.
    snap.elf.symbols = [ElfSymbol(name="foo", version="LIB_1", is_default=True)]
    res2 = run_crosschecks(snap)
    assert _findings_of(res2, ChangeKind.PUBLIC_NOT_EXPORTED) == []


@pytest.mark.parametrize("op_name", ["operator<", "operator<<", "operator<=>"])
def test_public_not_exported_flags_missing_operator(op_name):
    # Operators legitimately contain '<' but are not templates — a missing
    # exported operator must still be reported (Codex review).
    snap = _snap(elf=_elf("_Z3fooi"))
    snap.functions = [
        Function(
            name=op_name,
            mangled="_ZltRK1AS1_",
            return_type="bool",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert len(_findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED)) == 1


def test_public_not_exported_skips_header_constants():
    # A const header constant with a baked-in value emits no symbol.
    snap = _snap(elf=_elf())
    snap.variables = [
        Variable(
            name="kMax",
            mangled="kMax",
            type="int",
            value="42",
            is_const=True,
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED) == []


def test_public_not_exported_skips_parsed_const_constant_no_value():
    # castxml stores a const/constexpr initializer in snapshot.constants, leaving
    # Variable.value None — the constant still emits no symbol and must not be
    # flagged as a missing export (Codex review).
    snap = _snap(elf=_elf())
    snap.variables = [
        Variable(
            name="kMax",
            mangled="_ZL4kMax",
            type="int",
            value=None,
            is_const=True,
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED) == []


def test_public_not_exported_uses_pe_exports():
    snap = _snap(pe=PeMetadata(exports=[PeExport(name="foo")]))
    snap.functions = [
        Function(
            name="bar",
            mangled="bar",
            return_type="void",
            is_extern_c=True,
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert len(_findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED)) == 1


def test_public_not_exported_normalizes_macho_underscore():
    # The dumper stores Function.mangled without the Mach-O leading underscore,
    # but the export table keeps it. A `foo` decl whose `_foo` is exported must
    # be treated as present, not flagged (Codex review).
    snap = _snap(macho=MachoMetadata(exports=[MachoExport(name="_foo")]))
    snap.functions = [
        Function(
            name="foo",
            mangled="foo",
            return_type="void",
            is_extern_c=True,
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
        # `bar` is declared but not exported even after normalization → flagged.
        Function(
            name="bar",
            mangled="bar",
            return_type="void",
            is_extern_c=True,
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED)
    assert [c.symbol for c in hits] == ["bar"]


# --------------------------------------------------------------------------- #
# header_build_context_mismatch
# --------------------------------------------------------------------------- #


def _pack_with_flags(*flags: str, **opts) -> BuildSourcePack:
    be = BuildEvidence(
        build_options=[BuildOption(key=k, value="1", abi_relevant=True) for k in flags]
    )
    pack = BuildSourcePack(root="", build_evidence=be, **opts)
    return pack


def test_header_build_context_mismatch_flags_contextfree_parse():
    snap = _snap(
        build_source=_pack_with_flags("glibcxx_use_cxx11_abi", "define:NDEBUG")
    )
    snap.parsed_with_build_context = False
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH)
    assert len(hits) == 1
    assert hits[0].confidence == Confidence.MEDIUM
    assert "glibcxx_use_cxx11_abi" in (hits[0].new_value or "")
    # API_BREAK partition, per ADR-035 D4.
    assert ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH in _api_break_kinds()


def test_header_build_context_mismatch_silent_when_parsed_with_context():
    snap = _snap(build_source=_pack_with_flags("glibcxx_use_cxx11_abi"))
    snap.parsed_with_build_context = True
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH) == []
    assert _coverage(res, CHECK_HEADER_BUILD_CONTEXT_MISMATCH)["status"] == "present"


def test_header_build_context_mismatch_silent_without_abi_flags():
    be = BuildEvidence(build_options=[BuildOption(key="warnings", abi_relevant=False)])
    snap = _snap(build_source=BuildSourcePack(root="", build_evidence=be))
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH) == []
    assert _coverage(res, CHECK_HEADER_BUILD_CONTEXT_MISMATCH)["status"] == "present"


def test_header_build_context_mismatch_skipped_without_build_evidence():
    snap = _snap()
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_HEADER_BUILD_CONTEXT_MISMATCH)["status"] == "skipped"
    assert _findings_of(res, ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH) == []


# --------------------------------------------------------------------------- #
# private_header_leak
# --------------------------------------------------------------------------- #


def test_private_header_leak_flags_public_api_exposing_private_type():
    snap = _snap(elf=_elf("_Z3usev"))
    snap.functions = [
        Function(
            name="use",
            mangled="_Z3usev",
            return_type="Impl *",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(name="Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER),
    ]
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.PRIVATE_HEADER_LEAK)
    assert len(hits) == 1
    assert hits[0].caused_by_type == "Impl"
    assert hits[0].confidence == Confidence.MEDIUM


def test_private_header_leak_flags_non_public_generated_type():
    # A type from a non-public generated header (origin GENERATED) is private,
    # not public — exposing it in a public API leaks an un-installed header
    # (Codex review).
    snap = _snap(elf=_elf("_Z3usev"))
    snap.functions = [
        Function(
            name="use",
            mangled="_Z3usev",
            return_type="InternalConfig *",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(name="InternalConfig", kind="struct", origin=ScopeOrigin.GENERATED),
    ]
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.PRIVATE_HEADER_LEAK)
    assert [c.caused_by_type for c in hits] == ["InternalConfig"]


def test_private_header_leak_skips_pimpl_with_public_forward_decl():
    # Opaque-handle/PIMPL: `class Impl;` is forward-declared in a public header
    # and defined in a private one. The type IS on the public surface, so a
    # public API taking `Impl *` is not a leak (Codex review).
    snap = _snap(elf=_elf("_Z3usev"))
    snap.functions = [
        Function(
            name="use",
            mangled="_Z3usev",
            return_type="Impl *",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(name="Impl", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER),
        RecordType(name="Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PRIVATE_HEADER_LEAK) == []


def test_private_header_leak_basename_collision_with_public_type():
    # Public `Impl` and private `detail::Impl` share the bare token `Impl`. A
    # public `Impl *` signature uses the public type and must not leak; only an
    # explicit `detail::Impl` reference is a genuine private leak (Codex review).
    snap = _snap(elf=_elf("_Z4makev", "_Z6make2v"))
    snap.functions = [
        Function(
            name="make",
            mangled="_Z4makev",
            return_type="Impl *",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
        Function(
            name="make2",
            mangled="_Z6make2v",
            return_type="detail::Impl *",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(name="Impl", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER),
        RecordType(
            name="detail::Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER
        ),
    ]
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.PRIVATE_HEADER_LEAK)
    assert [c.symbol for c in hits] == ["_Z6make2v"]
    assert hits[0].caused_by_type == "detail::Impl"


def test_private_header_leak_matches_namespaced_param_type():
    snap = _snap(elf=_elf("_Z3usev"))
    snap.functions = [
        Function(
            name="use",
            mangled="_Z3usev",
            return_type="void",
            params=[Param(name="p", type="ns::detail::Impl &")],
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(
            name="ns::detail::Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER
        ),
    ]
    res = run_crosschecks(snap)
    assert len(_findings_of(res, ChangeKind.PRIVATE_HEADER_LEAK)) == 1


def test_private_header_leak_clean_when_type_is_public():
    snap = _snap(elf=_elf("_Z3usev"))
    snap.functions = [
        Function(
            name="use",
            mangled="_Z3usev",
            return_type="Widget *",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(name="Widget", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PRIVATE_HEADER_LEAK) == []


def test_private_header_leak_adds_source_index_provider_with_graph():
    snap = _snap(elf=_elf("_Z3usev"))
    snap.functions = [
        Function(
            name="use",
            mangled="_Z3usev",
            return_type="Impl *",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(name="Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER),
    ]
    # An empty graph object alone must NOT claim source_index corroboration —
    # the provider is recorded only when the graph actually indexed nodes.
    snap.build_source = BuildSourcePack(root="", source_graph=SourceGraphSummary())
    empty = run_crosschecks(snap)
    assert PROVIDER_SOURCE_INDEX not in empty.providers[CHECK_PRIVATE_HEADER_LEAK]

    snap.build_source = BuildSourcePack(
        root="",
        source_graph=SourceGraphSummary(
            nodes=[
                GraphNode(id="decl://use", kind="source_decl", label="use"),
                GraphNode(id="type://Impl", kind="record_type", label="Impl"),
            ]
        ),
    )
    res = run_crosschecks(snap)
    assert PROVIDER_SOURCE_INDEX in res.providers[CHECK_PRIVATE_HEADER_LEAK]


# --------------------------------------------------------------------------- #
# odr_type_variant
# --------------------------------------------------------------------------- #


def _pack_with_surface(*conflicts: dict) -> BuildSourcePack:
    surface = SourceAbiSurface(odr_conflicts=list(conflicts))
    return BuildSourcePack(root="", source_abi=surface)


def test_odr_type_variant_flags_recorded_conflict():
    snap = _snap(
        build_source=_pack_with_surface(
            {
                "qualified_name": "ns::Widget",
                "header": "widget.h",
                "old_type_hash": "aaa",
                "new_type_hash": "bbb",
            }
        )
    )
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.ODR_TYPE_VARIANT)
    assert [c.symbol for c in hits] == ["ns::Widget"]
    assert hits[0].caused_by_type == "ns::Widget"
    assert hits[0].source_location == "widget.h"
    assert "widget.h" in hits[0].description
    # API_BREAK partition, per ADR-035 D4.
    assert ChangeKind.ODR_TYPE_VARIANT in _api_break_kinds()
    assert res.providers[CHECK_ODR_TYPE_VARIANT] == [PROVIDER_SOURCE_INDEX]


def test_odr_type_variant_clean_surface_present_no_findings():
    # A surface with real L4 facts (a parsed/reachable type) but no ODR conflict
    # is genuinely clean → present, 0 findings.
    from abicheck.buildsource.source_abi import SourceEntity

    surface = SourceAbiSurface(
        reachable_types=[SourceEntity(id="t1", kind="record", qualified_name="Widget")]
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_abi=surface))
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.ODR_TYPE_VARIANT) == []
    assert _coverage(res, CHECK_ODR_TYPE_VARIANT)["status"] == "present"


def test_odr_type_variant_skipped_without_l4_surface():
    snap = _snap()
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_ODR_TYPE_VARIANT)["status"] == "skipped"
    assert _findings_of(res, ChangeKind.ODR_TYPE_VARIANT) == []


def test_odr_type_variant_skipped_on_empty_surface_no_facts():
    # L4 replay ran but parsed zero TUs (empty surface) — must skip, not read as a
    # clean ODR audit (Codex review / ADR-035 D4 coverage honesty).
    snap = _snap(build_source=_pack_with_surface())  # SourceAbiSurface(), no facts
    res = run_crosschecks(snap)
    row = _coverage(res, CHECK_ODR_TYPE_VARIANT)
    assert row["status"] == "skipped"
    assert "empty" in row["detail"]
    assert _findings_of(res, ChangeKind.ODR_TYPE_VARIANT) == []


def test_odr_type_variant_handles_anonymous_and_missing_header():
    snap = _snap(build_source=_pack_with_surface({"old_type_hash": "x"}))
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.ODR_TYPE_VARIANT)
    assert [c.symbol for c in hits] == ["<anonymous>"]
    assert hits[0].source_location is None


# --------------------------------------------------------------------------- #
# identity_collision_detected
# --------------------------------------------------------------------------- #


def _pack_with_identity_collisions(*collisions: dict) -> BuildSourcePack:
    from abicheck.buildsource.source_abi import SourceEntity

    surface = SourceAbiSurface(
        identity_collisions=list(collisions),
        # Any real L4 fact makes the surface non-empty so the check runs.
        reachable_declarations=[
            SourceEntity(id="d1", kind="function", qualified_name="f")
        ],
    )
    return BuildSourcePack(root="", source_abi=surface)


def test_identity_collision_flags_recorded_collision():
    snap = _snap(
        build_source=_pack_with_identity_collisions(
            {
                "identity": "f#sha256:abc",
                "qualified_name": "f",
                "usr_a": "c:@F@f#",
                "usr_b": "c:@N@ns@F@f#",
            }
        )
    )
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.IDENTITY_COLLISION_DETECTED)
    assert [c.symbol for c in hits] == ["f"]
    assert hits[0].new_value == "f#sha256:abc"
    assert "c:@F@f#" in hits[0].description
    assert "c:@N@ns@F@f#" in hits[0].description
    # RISK partition (not API_BREAK), per ADR-041 P1 #5.
    assert ChangeKind.IDENTITY_COLLISION_DETECTED not in _api_break_kinds()
    assert res.providers[CHECK_IDENTITY_COLLISION] == [PROVIDER_SOURCE_INDEX]


def test_identity_collision_clean_surface_present_no_findings():
    snap = _snap(build_source=_pack_with_identity_collisions())
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.IDENTITY_COLLISION_DETECTED) == []
    assert _coverage(res, CHECK_IDENTITY_COLLISION)["status"] == "present"


def test_identity_collision_skipped_without_l4_surface():
    snap = _snap()
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_IDENTITY_COLLISION)["status"] == "skipped"
    assert _findings_of(res, ChangeKind.IDENTITY_COLLISION_DETECTED) == []


def test_identity_collision_skipped_on_empty_surface_no_facts():
    snap = _snap(build_source=_pack_with_surface())  # SourceAbiSurface(), no facts
    res = run_crosschecks(snap)
    row = _coverage(res, CHECK_IDENTITY_COLLISION)
    assert row["status"] == "skipped"
    assert "empty" in row["detail"]
    assert _findings_of(res, ChangeKind.IDENTITY_COLLISION_DETECTED) == []


def test_identity_collision_handles_missing_qualified_name():
    snap = _snap(
        build_source=_pack_with_identity_collisions(
            {"identity": "anon#sha256:zzz", "usr_a": "u1", "usr_b": "u2"}
        )
    )
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.IDENTITY_COLLISION_DETECTED)
    assert [c.symbol for c in hits] == ["anon#sha256:zzz"]


# --------------------------------------------------------------------------- #
# public_to_internal_dependency
# --------------------------------------------------------------------------- #


def _graph(nodes, edges) -> SourceGraphSummary:
    return SourceGraphSummary(nodes=list(nodes), edges=list(edges))


def _decl(node_id: str, label: str, visibility: str) -> GraphNode:
    return GraphNode(
        id=node_id, kind="source_decl", label=label, attrs={"visibility": visibility}
    )


def test_public_to_internal_dependency_flags_public_reaching_internal():
    g = _graph(
        [
            _decl("decl://pub", "pubFn", "public_header"),
            _decl("decl://int", "internalImpl", "source"),
        ],
        [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY)
    assert [(c.symbol, c.new_value) for c in hits] == [("pubFn", "internalImpl")]
    assert hits[0].confidence == Confidence.MEDIUM
    assert res.providers[CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY] == [PROVIDER_SOURCE_INDEX]
    # RISK partition (never BREAKING / API_BREAK).
    assert ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY not in _api_break_kinds()


def test_public_to_internal_dependency_exported_decl_counts_as_public():
    # A decl mapped to an exported symbol is public even without public_header
    # visibility; the internal type it embeds is still flagged.
    g = _graph(
        [
            _decl("decl://api", "apiFn", "unknown"),
            _decl("type://impl", "ImplType", "private_header"),
            GraphNode(
                id="binary_symbol://_Z6apiFnv", kind="binary_symbol", label="_Z6apiFnv"
            ),
        ],
        [
            GraphEdge(
                src="decl://api",
                dst="binary_symbol://_Z6apiFnv",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
            GraphEdge(src="decl://api", dst="type://impl", kind="DECL_HAS_TYPE"),
        ],
    )
    # The type node must be classifiable, so give it a decl node kind.
    g.nodes[1] = GraphNode(
        id="type://impl",
        kind="record_type",
        label="ImplType",
        attrs={"visibility": "private_header"},
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY)
    assert [(c.symbol, c.new_value) for c in hits] == [("apiFn", "ImplType")]


def test_public_to_internal_dependency_elevates_changed_file():
    g = _graph(
        [
            _decl("decl://pub", "pubFn", "public_header"),
            _decl("decl://int", "internalImpl", "source"),
            GraphNode(id="header://src/impl.cc", kind="header", label="src/impl.cc"),
        ],
        [
            GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_REFERENCES_DECL"),
            GraphEdge(
                src="header://src/impl.cc", dst="decl://int", kind="SOURCE_DECLARES"
            ),
        ],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(
        snap, CrosscheckConfig(changed_paths=frozenset({"src/impl.cc"}))
    )
    hits = _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY)
    assert len(hits) == 1
    assert hits[0].confidence == Confidence.HIGH
    assert hits[0].source_location == "src/impl.cc"
    assert "changed file" in hits[0].description


def test_public_to_internal_dependency_changed_file_path_normalization():
    # Graph labels are often absolute build paths while `scan` passes repo-relative
    # changed paths from `git diff`; the elevation must match on a path-suffix, not
    # exact string equality (Codex review).
    g = _graph(
        [
            _decl("decl://pub", "pubFn", "public_header"),
            _decl("decl://int", "internalImpl", "source"),
            GraphNode(
                id="header:///work/build/src/impl.cc",
                kind="header",
                label="/work/build/src/impl.cc",
            ),
        ],
        [
            GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL"),
            GraphEdge(
                src="header:///work/build/src/impl.cc",
                dst="decl://int",
                kind="SOURCE_DECLARES",
            ),
        ],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(
        snap, CrosscheckConfig(changed_paths=frozenset({"src/impl.cc"}))
    )
    hits = _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY)
    assert len(hits) == 1
    assert hits[0].confidence == Confidence.HIGH  # elevated despite differing spelling


def test_public_to_internal_dependency_flags_callgraph_only_impl_callee():
    # The built-in call-graph extractor creates callee source_decl nodes with NO
    # visibility attr for functions only present in implementation code. A public
    # exported caller reaching such an unannotated callee is the check's main
    # source-file case — flagged when the project declares it (a SOURCE_DECLARES
    # edge from a project file provides the provenance; Codex review).
    g = _graph(
        [
            _decl("decl://pub", "pubFn", "public_header"),
            # No visibility attr — exactly what augment_graph_with_calls emits.
            GraphNode(id="decl://impl", kind="source_decl", label="implHelper"),
            GraphNode(id="header://src/impl.cc", kind="source", label="src/impl.cc"),
        ],
        [
            GraphEdge(src="decl://pub", dst="decl://impl", kind="DECL_CALLS_DECL"),
            GraphEdge(
                src="header://src/impl.cc", dst="decl://impl", kind="SOURCE_DECLARES"
            ),
        ],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY)
    assert [(c.symbol, c.new_value) for c in hits] == [("pubFn", "implHelper")]


def test_public_to_internal_dependency_skips_thirdparty_callee():
    # A public function calling a bare third-party / system C API (malloc,
    # pthread_*, SSL_new) gets an unannotated call-graph node with NO project
    # provenance (no SOURCE_DECLARES edge). It must NOT be flagged as a
    # project-internal dependency (Codex review).
    g = _graph(
        [
            _decl("decl://pub", "pubFn", "public_header"),
            GraphNode(id="decl://malloc", kind="source_decl", label="malloc"),
            GraphNode(id="decl://ssl", kind="source_decl", label="SSL_new"),
        ],
        [
            GraphEdge(src="decl://pub", dst="decl://malloc", kind="DECL_CALLS_DECL"),
            GraphEdge(src="decl://pub", dst="decl://ssl", kind="DECL_CALLS_DECL"),
        ],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY) == []
    assert _coverage(res, CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY)["status"] == "present"


def test_public_to_internal_dependency_skips_stdlib_callee():
    # A public API calling a std:: / compiler helper (also unannotated in the
    # call graph) must NOT light up — otherwise the check floods on stdlib use.
    g = _graph(
        [
            _decl("decl://pub", "pubFn", "public_header"),
            GraphNode(
                id="decl://std",
                kind="source_decl",
                label="_ZNSt6vectorIiE9push_backEi",
            ),
            GraphNode(id="decl://gnu", kind="source_decl", label="__gnu_cxx::__ops"),
        ],
        [
            GraphEdge(src="decl://pub", dst="decl://std", kind="DECL_CALLS_DECL"),
            GraphEdge(src="decl://pub", dst="decl://gnu", kind="DECL_REFERENCES_DECL"),
        ],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY) == []
    assert _coverage(res, CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY)["status"] == "present"


def test_public_to_internal_dependency_callgraph_only_graph_no_public_caller():
    # In a pure call-graph graph (no L4/export annotation) the caller is also
    # unannotated, so it is not "public" — no finding, no false positive.
    g = _graph(
        [
            GraphNode(id="decl://a", kind="source_decl", label="aFn"),
            GraphNode(id="decl://b", kind="source_decl", label="bFn"),
        ],
        [GraphEdge(src="decl://a", dst="decl://b", kind="DECL_CALLS_DECL")],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY) == []


def test_public_to_internal_dependency_exported_caller_callgraph_callee():
    # The reviewer's exact shape via the real export path: caller mapped to an
    # exported symbol (public) → project-declared impl callee (internal).
    g = _graph(
        [
            GraphNode(id="decl://api", kind="source_decl", label="apiFn"),
            GraphNode(id="decl://impl", kind="source_decl", label="implHelper"),
            GraphNode(id="header://src/impl.cc", kind="source", label="src/impl.cc"),
            GraphNode(
                id="binary_symbol://_Z5apiFnv",
                kind="binary_symbol",
                label="_Z5apiFnv",
            ),
        ],
        [
            GraphEdge(
                src="decl://api",
                dst="binary_symbol://_Z5apiFnv",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
            GraphEdge(src="decl://api", dst="decl://impl", kind="DECL_CALLS_DECL"),
            GraphEdge(
                src="header://src/impl.cc", dst="decl://impl", kind="SOURCE_DECLARES"
            ),
        ],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY)
    assert [(c.symbol, c.new_value) for c in hits] == [("apiFn", "implHelper")]


def test_public_to_internal_dependency_flags_defined_in_project_callee():
    # The built-in call graph marks a callee whose body is in a project source
    # file `defined_in_project` (source-location provenance) — no SOURCE_DECLARES
    # edge needed. A public caller reaching it is flagged (Codex review).
    g = _graph(
        [
            _decl("decl://pub", "pubFn", "public_header"),
            GraphNode(
                id="decl://impl",
                kind="source_decl",
                label="implHelper",
                attrs={"defined_in_project": True},
            ),
        ],
        [GraphEdge(src="decl://pub", dst="decl://impl", kind="DECL_CALLS_DECL")],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY)
    assert [(c.symbol, c.new_value) for c in hits] == [("pubFn", "implHelper")]


def test_public_to_internal_dependency_skips_generated_public_target():
    # `visibility="generated"` in the L5 graph means a generated header under the
    # public roots — a public, consumer-visible entity (source_link._is_public
    # treats it as public). A public API referencing it (e.g. an installed
    # generated config.h type) must NOT be flagged (Codex review).
    g = _graph(
        [
            _decl("decl://pub", "pubFn", "public_header"),
            GraphNode(
                id="type://cfg",
                kind="record_type",
                label="GeneratedConfig",
                attrs={"visibility": "generated"},
            ),
        ],
        [GraphEdge(src="decl://pub", dst="type://cfg", kind="DECL_HAS_TYPE")],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY) == []
    assert _coverage(res, CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY)["status"] == "present"


def test_public_to_internal_dependency_clean_when_target_is_public():
    g = _graph(
        [
            _decl("decl://pub", "pubFn", "public_header"),
            _decl("decl://pub2", "otherPub", "public_header"),
        ],
        [GraphEdge(src="decl://pub", dst="decl://pub2", kind="DECL_CALLS_DECL")],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY) == []
    assert _coverage(res, CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY)["status"] == "present"


def test_public_to_internal_dependency_skipped_without_graph():
    snap = _snap()
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY)["status"] == "skipped"


def test_public_to_internal_dependency_soft_advisory_on_structural_only_graph():
    # A structural-only graph (no decl-dependency edges) cannot run the check; it
    # must skip with an advisory naming the method to enable, never read clean.
    g = _graph([GraphNode(id="target://x", kind="target", label="x")], [])
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(snap)
    row = _coverage(res, CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY)
    assert row["status"] == "skipped"
    assert "call edges" in row["detail"]
    assert _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY) == []


# --------------------------------------------------------------------------- #
# unversioned_exported_symbol (ADR-035 D8 audit)
# --------------------------------------------------------------------------- #


def _velf(symbols, versions_defined=()) -> ElfMetadata:
    return ElfMetadata(
        symbols=[
            ElfSymbol(name=n, version=v, visibility=vis) for (n, v, vis) in symbols
        ],
        versions_defined=list(versions_defined),
    )


def test_unversioned_exported_symbol_flags_unversioned_under_scheme():
    snap = _snap(
        elf=_velf(
            [("_Z3apiv", "FOO_1.0", "default"), ("_Z6legacyv", "", "default")],
            versions_defined=["FOO_1.0"],
        )
    )
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.UNVERSIONED_EXPORTED_SYMBOL)
    assert [c.symbol for c in hits] == ["_Z6legacyv"]
    assert ChangeKind.UNVERSIONED_EXPORTED_SYMBOL not in _api_break_kinds()
    assert res.providers[CHECK_UNVERSIONED_EXPORTED_SYMBOL] == [PROVIDER_BINARY_EXPORTS]


def test_unversioned_exported_symbol_silent_without_scheme():
    snap = _snap(elf=_velf([("_Z3apiv", "", "default")], versions_defined=[]))
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.UNVERSIONED_EXPORTED_SYMBOL) == []
    assert _coverage(res, CHECK_UNVERSIONED_EXPORTED_SYMBOL)["status"] == "present"


def test_unversioned_exported_symbol_skips_hidden_and_structors():
    snap = _snap(
        elf=_velf(
            [
                ("_Z3apiv", "FOO_1.0", "default"),
                ("_Z6hiddenv", "", "hidden"),  # not exported-visible
                ("_ZN6WidgetC1Ev", "", "default"),  # ctor artifact
            ],
            versions_defined=["FOO_1.0"],
        )
    )
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.UNVERSIONED_EXPORTED_SYMBOL) == []


def test_unversioned_exported_symbol_skipped_on_non_elf():
    snap = _snap(elf=None)
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_UNVERSIONED_EXPORTED_SYMBOL)["status"] == "skipped"


# --------------------------------------------------------------------------- #
# rtti_for_internal_type (ADR-035 D8 audit)
# --------------------------------------------------------------------------- #


def test_rtti_for_internal_type_flags_typeinfo_of_private_type():
    snap = _snap(elf=_elf("_ZTI8Internal", "_ZTV8Internal", "_ZTI6Widget"))
    snap.functions = [
        Function(
            name="api",
            mangled="_Z3apiv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(name="Internal", kind="class", origin=ScopeOrigin.PRIVATE_HEADER),
        RecordType(name="Widget", kind="class", origin=ScopeOrigin.PUBLIC_HEADER),
    ]
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.RTTI_FOR_INTERNAL_TYPE)
    assert sorted(c.symbol for c in hits) == ["_ZTI8Internal", "_ZTV8Internal"]
    assert all(c.caused_by_type == "Internal" for c in hits)


def test_rtti_for_internal_type_clean_for_public_type():
    snap = _snap(elf=_elf("_ZTI6Widget", "_ZTV6Widget"))
    snap.functions = [
        Function(
            name="api",
            mangled="_Z3apiv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(name="Widget", kind="class", origin=ScopeOrigin.PUBLIC_HEADER),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.RTTI_FOR_INTERNAL_TYPE) == []


def test_rtti_for_internal_type_handles_nested_name():
    snap = _snap(elf=_elf("_ZTIN2ns8InternalE"))
    snap.functions = [
        Function(
            name="api",
            mangled="_Z3apiv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(
            name="ns::Internal", kind="class", origin=ScopeOrigin.PRIVATE_HEADER
        ),
    ]
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.RTTI_FOR_INTERNAL_TYPE)
    assert [c.caused_by_type for c in hits] == ["ns::Internal"]


def test_rtti_for_internal_type_matches_private_template_instantiation():
    # RTTI for a private class template `detail::Box<int>` mangles as
    # `_ZTIN6detail3BoxIiEE`; the symbol parser reduces to `detail::Box`/`Box`
    # (no template args), so the private type's base spelling must resolve back to
    # the instantiation (Codex review).
    snap = _snap(elf=_elf("_ZTIN6detail3BoxIiEE"))
    snap.functions = [
        Function(
            name="api",
            mangled="_Z3apiv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(
            name="detail::Box<int>", kind="class", origin=ScopeOrigin.PRIVATE_HEADER
        ),
    ]
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.RTTI_FOR_INTERNAL_TYPE)
    assert [c.caused_by_type for c in hits] == ["detail::Box<int>"]


def test_rtti_for_internal_type_template_base_collision_with_public_skips():
    # A public `api::Box<int>` and a private `detail::Box<long>` share the leaf
    # base `Box`. RTTI for the PUBLIC template must NOT be flagged (the leaf base
    # alias is suppressed by the public-collision guard).
    snap = _snap(elf=_elf("_ZTIN3api3BoxIiEE"))
    snap.functions = [
        Function(
            name="api",
            mangled="_Z3apiv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(
            name="api::Box<int>", kind="class", origin=ScopeOrigin.PUBLIC_HEADER
        ),
        RecordType(
            name="detail::Box<long>", kind="class", origin=ScopeOrigin.PRIVATE_HEADER
        ),
    ]
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.RTTI_FOR_INTERNAL_TYPE) == []


def test_rtti_for_internal_type_matches_qualified_over_leaf_collision():
    # A public api::Internal and a private detail::Internal share the leaf token
    # "Internal" (so _private_type_names suppresses the bare alias). RTTI for the
    # private detail::Internal must still be matched on its qualified name from the
    # nested mangling (Codex review).
    snap = _snap(elf=_elf("_ZTIN6detail8InternalE", "_Z3apiv"))
    snap.functions = [
        Function(
            name="api",
            mangled="_Z3apiv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(
            name="api::Internal", kind="class", origin=ScopeOrigin.PUBLIC_HEADER
        ),
        RecordType(
            name="detail::Internal", kind="class", origin=ScopeOrigin.PRIVATE_HEADER
        ),
    ]
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.RTTI_FOR_INTERNAL_TYPE)
    assert [c.caused_by_type for c in hits] == ["detail::Internal"]


def test_rtti_for_internal_type_skipped_without_provenance():
    snap = _snap(from_headers=False, elf=_elf("_ZTI8Internal"))
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_RTTI_FOR_INTERNAL_TYPE)["status"] == "skipped"


# --------------------------------------------------------------------------- #
# coverage honesty / engine plumbing
# --------------------------------------------------------------------------- #


def test_elf_only_snapshot_skips_origin_checks_no_false_positives():
    # No public-header provenance: every origin-based check must skip cleanly.
    snap = _snap(from_headers=False, elf=_elf("_Z3fooi", "_Z6secretv"))
    snap.functions = [
        Function(name="secret", mangled="_Z6secretv", return_type="void"),
        Function(name="foo", mangled="_Z3fooi", return_type="void"),
    ]
    res = run_crosschecks(snap)
    assert res.findings == []
    for check in (
        CHECK_EXPORTED_NOT_PUBLIC,
        CHECK_PUBLIC_NOT_EXPORTED,
        CHECK_PRIVATE_HEADER_LEAK,
    ):
        assert _coverage(res, check)["status"] == "skipped"


def test_disabled_check_reports_not_collected():
    snap = _snap(elf=_elf())
    cfg = CrosscheckConfig(enabled=frozenset({CHECK_PUBLIC_NOT_EXPORTED}))
    res = run_crosschecks(snap, cfg)
    row = _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)
    assert row["status"] == "not_collected"
    assert "disabled" in row["detail"]
    assert CHECK_EXPORTED_NOT_PUBLIC not in res.providers


def test_every_check_has_a_coverage_row():
    res = run_crosschecks(_snap(elf=_elf()))
    rows = {r["layer"] for r in res.coverage}
    assert rows == {f"crosscheck:{c}" for c in ALL_CHECKS}


def test_max_per_check_caps_findings_and_marks_partial():
    # One documented export makes provenance resolvable; five undocumented
    # exports in the table are capped to 2 → partial.
    snap = _snap(elf=_elf("_Z3fooi", *(f"_Z2s{i}v" for i in range(5))))
    snap.functions = [
        Function(
            name="foo",
            mangled="_Z3fooi",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=2))
    assert len(_findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)) == 2
    assert _coverage(res, CHECK_EXPORTED_NOT_PUBLIC)["status"] == "partial"


def test_result_to_dict_roundtrips_counts():
    snap = _snap(elf=_elf("g"))
    snap.variables = [
        Variable(name="g", mangled="g", type="int", origin=ScopeOrigin.EXPORT_ONLY),
    ]
    res = run_crosschecks(snap)
    d = res.to_dict()
    assert d["version"] == CROSSCHECK_VERSION
    assert d["counts_by_check"]["exported_not_public"] == 1
    assert d["findings"] == 1


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _api_break_kinds():
    from abicheck.checker_policy import API_BREAK_KINDS

    return API_BREAK_KINDS


def test_crosscheck_kinds_are_risk_or_api_break_never_breaking():
    from abicheck.checker_policy import BREAKING_KINDS

    crosscheck_kinds = {
        ChangeKind.EXPORTED_NOT_PUBLIC,
        ChangeKind.PUBLIC_NOT_EXPORTED,
        ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH,
        ChangeKind.PRIVATE_HEADER_LEAK,
        ChangeKind.ODR_TYPE_VARIANT,
        ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY,
    }
    assert not (crosscheck_kinds & BREAKING_KINDS)
    # The two API_BREAK cross-checks; the rest are RISK.
    assert ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH in _api_break_kinds()
    assert ChangeKind.ODR_TYPE_VARIANT in _api_break_kinds()
    assert ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY not in _api_break_kinds()
    assert Verdict.BREAKING is not None  # sanity: import wired


def test_public_to_internal_dependency_elevates_via_callgraph_def_file():
    # A call-graph-only internal helper (no SOURCE_DECLARES) carries its source
    # path in def_file; a changed-path match must elevate to HIGH with a location
    # (Codex review).
    g = _graph(
        [
            _decl("decl://pub", "pubFn", "public_header"),
            GraphNode(
                id="decl://impl",
                kind="source_decl",
                label="implHelper",
                attrs={"defined_in_project": True, "def_file": "/work/src/impl.cc"},
            ),
        ],
        [GraphEdge(src="decl://pub", dst="decl://impl", kind="DECL_CALLS_DECL")],
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_graph=g))
    res = run_crosschecks(
        snap, CrosscheckConfig(changed_paths=frozenset({"src/impl.cc"}))
    )
    hits = _findings_of(res, ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY)
    assert len(hits) == 1
    assert hits[0].confidence == Confidence.HIGH
    assert hits[0].source_location == "/work/src/impl.cc"


def test_odr_type_variant_skipped_when_only_export_table_coverage():
    # Replay parsed 0 TUs but link_source_abi still records export-table coverage;
    # that must NOT read as a clean ODR audit (Codex review).
    surface = SourceAbiSurface(coverage={"exported_symbols": 12, "matched_symbols": 0})
    snap = _snap(build_source=BuildSourcePack(root="", source_abi=surface))
    res = run_crosschecks(snap)
    row = _coverage(res, CHECK_ODR_TYPE_VARIANT)
    assert row["status"] == "skipped"
    assert _findings_of(res, ChangeKind.ODR_TYPE_VARIANT) == []


def test_odr_type_variant_present_with_parsed_tu_coverage():
    surface = SourceAbiSurface(coverage={"compile_units_parsed": 3})
    snap = _snap(build_source=BuildSourcePack(root="", source_abi=surface))
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_ODR_TYPE_VARIANT)["status"] == "present"


# --------------------------------------------------------------------------- #
# compile_context_conflict (AC-008)
# --------------------------------------------------------------------------- #


def _pack_with_units(*units) -> BuildSourcePack:
    from abicheck.buildsource.build_evidence import CompileUnit  # noqa: F401

    be = BuildEvidence(compile_units=list(units))
    return BuildSourcePack(root="", build_evidence=be)


def _cu(uid: str, target: str, *, flags=(), defines=None):
    from abicheck.buildsource.build_evidence import CompileUnit

    return CompileUnit(
        id=uid,
        target_id=target,
        abi_relevant_flags=list(flags),
        defines=dict(defines or {}),
    )


def test_compile_context_conflict_flags_rtti_disagreement():
    # AC-008: one build target compiled part of itself -frtti and part -fno-rtti
    # (oneTBB umbrella case) — aggregating hides it, so flag it as RISK.
    snap = _snap(
        build_source=_pack_with_units(
            _cu("a", "target://libtbb.so", flags=["-frtti"]),
            _cu("b", "target://libtbb.so", flags=["-fno-rtti"]),
        )
    )
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.COMPILE_CONTEXT_CONFLICT)
    assert len(hits) == 1
    assert hits[0].old_value == "-frtti" and hits[0].new_value == "-fno-rtti"
    assert "target://libtbb.so" in hits[0].symbol
    assert res.providers[CHECK_COMPILE_CONTEXT_CONFLICT] == [PROVIDER_BUILD_CONFIG]
    # RISK partition, never an artifact break.
    assert ChangeKind.COMPILE_CONTEXT_CONFLICT not in _api_break_kinds()


def test_compile_context_conflict_flags_default_vs_negative():
    # AC-008 (Codex): the common umbrella case — most TUs compiled with the
    # language default (RTTI on, no flag) and one built -fno-rtti. The default
    # unit carries no explicit -frtti, so an explicit-positive-only check would
    # miss it; effective-mode comparison must still flag the conflict.
    snap = _snap(
        build_source=_pack_with_units(
            _cu("a", "target://libtbb.so"),  # default RTTI (no flag)
            _cu("b", "target://libtbb.so", flags=["-fno-rtti"]),
        )
    )
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.COMPILE_CONTEXT_CONFLICT)
    assert len(hits) == 1
    assert hits[0].old_value == "-frtti" and hits[0].new_value == "-fno-rtti"


def test_compile_context_conflict_clean_when_all_negative():
    # All units share the same -fno-rtti mode → coherent, no conflict (the
    # effective-mode fix must not fire when every unit is negative).
    snap = _snap(
        build_source=_pack_with_units(
            _cu("a", "target://libtbb.so", flags=["-fno-rtti"]),
            _cu("b", "target://libtbb.so", flags=["-fno-rtti"]),
        )
    )
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.COMPILE_CONTEXT_CONFLICT) == []


def test_compile_context_conflict_flags_define_value_disagreement():
    snap = _snap(
        build_source=_pack_with_units(
            _cu("a", "target://libdal.so", defines={"DAL_VARIANT": "avx2"}),
            _cu("b", "target://libdal.so", defines={"DAL_VARIANT": "avx512"}),
        )
    )
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.COMPILE_CONTEXT_CONFLICT)
    assert len(hits) == 1
    assert "DAL_VARIANT" in hits[0].description
    assert "avx2" in hits[0].new_value and "avx512" in hits[0].new_value


def test_compile_context_conflict_clean_when_units_agree():
    snap = _snap(
        build_source=_pack_with_units(
            _cu("a", "target://libtbb.so", flags=["-fno-rtti"]),
            _cu("b", "target://libtbb.so", flags=["-fno-rtti"], defines={"X": "1"}),
        )
    )
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.COMPILE_CONTEXT_CONFLICT) == []
    assert _coverage(res, CHECK_COMPILE_CONTEXT_CONFLICT)["status"] == "present"


def test_compile_context_conflict_does_not_cross_targets():
    # Two DIFFERENT targets legitimately differ on -frtti — not a conflict.
    snap = _snap(
        build_source=_pack_with_units(
            _cu("a", "target://libone.so", flags=["-frtti"]),
            _cu("b", "target://libtwo.so", flags=["-fno-rtti"]),
        )
    )
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.COMPILE_CONTEXT_CONFLICT) == []


def test_compile_context_conflict_bare_define_no_value_is_not_conflict():
    # A bare -DFOO (no value) on one unit and -DFOO=1 on another: only value-
    # carrying binds are compared, so a bare/valued mix is not flagged as a
    # two-value conflict.
    snap = _snap(
        build_source=_pack_with_units(
            _cu("a", "target://lib.so", defines={"FOO": ""}),
            _cu("b", "target://lib.so", defines={"FOO": "1"}),
        )
    )
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.COMPILE_CONTEXT_CONFLICT) == []


def test_compile_context_conflict_skipped_without_l3():
    snap = _snap()
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_COMPILE_CONTEXT_CONFLICT)["status"] == "skipped"
    assert _findings_of(res, ChangeKind.COMPILE_CONTEXT_CONFLICT) == []


# --------------------------------------------------------------------------- #
# source_surface_dso_mismatch (AC-009)
# --------------------------------------------------------------------------- #


def _surface_with_decls(n: int, *, matched: int) -> SourceAbiSurface:
    from abicheck.buildsource.source_abi import SourceEntity

    return SourceAbiSurface(
        library="libfoo.so",
        reachable_declarations=[
            SourceEntity(id=f"d{i}", kind="function", qualified_name=f"f{i}")
            for i in range(n)
        ],
        coverage={"matched_symbols": matched},
    )


def test_source_surface_dso_mismatch_flags_zero_match_surface():
    # AC-009: one source surface reused across DSOs maps to none of THIS binary's
    # exports — likely a different/shared DSO, RISK.
    surface = _surface_with_decls(3, matched=0)
    snap = _snap(elf=_elf("_Z3foov", "_Z3barv"))
    snap.build_source = BuildSourcePack(root="", source_abi=surface)
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.SOURCE_SURFACE_DSO_MISMATCH)
    assert len(hits) == 1
    assert hits[0].symbol == "libfoo.so"
    assert res.providers[CHECK_SOURCE_SURFACE_DSO_MISMATCH] == [
        PROVIDER_SOURCE_INDEX,
        PROVIDER_BINARY_EXPORTS,
    ]
    assert ChangeKind.SOURCE_SURFACE_DSO_MISMATCH not in _api_break_kinds()


def test_source_surface_dso_mismatch_clean_when_surface_matches():
    surface = _surface_with_decls(3, matched=2)
    snap = _snap(elf=_elf("_Z3foov", "_Z3barv"))
    snap.build_source = BuildSourcePack(root="", source_abi=surface)
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.SOURCE_SURFACE_DSO_MISMATCH) == []
    assert _coverage(res, CHECK_SOURCE_SURFACE_DSO_MISMATCH)["status"] == "present"


def test_source_surface_dso_mismatch_clean_when_matched_via_synthesized():
    # AC-009 (Codex): a C++ surface whose exports are attributed entirely through
    # synthesized (RTTI/vtable) / template / allocator counters — with decl
    # `matched_symbols` still 0 — DID match this DSO, so it must NOT be flagged.
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity

    surface = SourceAbiSurface(
        library="libfoo.so",
        reachable_declarations=[
            SourceEntity(id="d0", kind="record", qualified_name="Widget")
        ],
        coverage={
            "matched_symbols": 0,
            "synthesized_symbols_matched": 2,  # RTTI/vtable attributed to Widget
            "exported_symbols": 2,
            "unmatched_symbols": 0,  # all exports attributed
        },
    )
    snap = _snap(elf=_elf("_ZTV6Widget", "_ZTI6Widget"))
    snap.build_source = BuildSourcePack(root="", source_abi=surface)
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.SOURCE_SURFACE_DSO_MISMATCH) == []
    assert _coverage(res, CHECK_SOURCE_SURFACE_DSO_MISMATCH)["status"] == "present"


def test_source_surface_dso_mismatch_uses_unmatched_symbols_counter():
    # When unmatched_symbols == exported_symbols (nothing attributed by any tier)
    # the surface truly matched nothing → fire, even though matched_symbols alone
    # is not the signal used.
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity

    surface = SourceAbiSurface(
        library="libfoo.so",
        reachable_declarations=[
            SourceEntity(id="d0", kind="function", qualified_name="f")
        ],
        coverage={
            "matched_symbols": 0,
            "synthesized_symbols_matched": 0,
            "exported_symbols": 3,
            "unmatched_symbols": 3,  # nothing attributed
        },
    )
    snap = _snap(elf=_elf("_Z3barv", "_Z3bazv", "_Z3quxv"))
    snap.build_source = BuildSourcePack(root="", source_abi=surface)
    res = run_crosschecks(snap)
    assert len(_findings_of(res, ChangeKind.SOURCE_SURFACE_DSO_MISMATCH)) == 1


def test_source_surface_dso_mismatch_skipped_without_binary_exports():
    # A source-only snapshot (no export table) must skip, never false-positive.
    surface = _surface_with_decls(3, matched=0)
    snap = _snap(build_source=BuildSourcePack(root="", source_abi=surface))
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_SOURCE_SURFACE_DSO_MISMATCH)["status"] == "skipped"
    assert _findings_of(res, ChangeKind.SOURCE_SURFACE_DSO_MISMATCH) == []


def test_source_surface_dso_mismatch_skipped_without_l4_surface():
    snap = _snap(elf=_elf("_Z3foov"))
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_SOURCE_SURFACE_DSO_MISMATCH)["status"] == "skipped"


def test_source_surface_dso_mismatch_present_empty_surface_no_findings():
    # Surface with an export table on the binary but no reachable decls: present,
    # no finding (nothing to mis-scope).
    surface = SourceAbiSurface(library="libfoo.so", coverage={"matched_symbols": 0})
    snap = _snap(elf=_elf("_Z3foov"))
    snap.build_source = BuildSourcePack(root="", source_abi=surface)
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.SOURCE_SURFACE_DSO_MISMATCH) == []
    assert _coverage(res, CHECK_SOURCE_SURFACE_DSO_MISMATCH)["status"] == "present"
