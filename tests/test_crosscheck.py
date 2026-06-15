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
    CHECK_ODR_TYPE_VARIANT,
    CHECK_PRIVATE_HEADER_LEAK,
    CHECK_PUBLIC_NOT_EXPORTED,
    CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY,
    CHECK_RTTI_FOR_INTERNAL_TYPE,
    CHECK_UNVERSIONED_EXPORTED_SYMBOL,
    CROSSCHECK_VERSION,
    PROVIDER_BINARY_EXPORTS,
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
                id="decl://impl", kind="source_decl", label="implHelper",
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
