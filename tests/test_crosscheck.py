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
    CHECK_EXPORTED_NOT_PUBLIC,
    CHECK_HEADER_BUILD_CONTEXT_MISMATCH,
    CHECK_PRIVATE_HEADER_LEAK,
    CHECK_PUBLIC_NOT_EXPORTED,
    CROSSCHECK_VERSION,
    PROVIDER_BINARY_EXPORTS,
    PROVIDER_PUBLIC_HEADER_AST,
    PROVIDER_SOURCE_INDEX,
    CrosscheckConfig,
    run_crosschecks,
)
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import SourceGraphSummary
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
    snap.build_source = BuildSourcePack(root="", source_graph=SourceGraphSummary())
    res = run_crosschecks(snap)
    assert PROVIDER_SOURCE_INDEX in res.providers[CHECK_PRIVATE_HEADER_LEAK]


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
    }
    assert not (crosscheck_kinds & BREAKING_KINDS)
    # And HEADER_BUILD_CONTEXT_MISMATCH is the lone API_BREAK of the four.
    assert ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH in _api_break_kinds()
    assert Verdict.BREAKING is not None  # sanity: import wired
