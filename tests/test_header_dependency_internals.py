"""A header-only dependency's internals are out of contract on a header compare.

:func:`abicheck.workflows.dump.dependency_internals.dependency_internals`
decides, per header-derived snapshot, which dependency entities no public
declaration of the library reaches, and
:func:`abicheck.policy.contract_dependency_origin.exclude_unreachable_dependency_findings`
moves the findings on them out of contract (ADR-089's 2026-10-25 amendment).

The bug class: a dependency's entities (records, hidden friends and other
functions, inline variables, member typedefs, weak template instances) gate a
comparison the library did not change. The invariant, stated over an
exhaustive small domain of library/dependency layouts, relations and export
table formats: an entity is internal exactly when its declaring header is
classifiable as a dependency's, its namespace is not the library's, and the
library neither names it in a public declaration nor defines it in its
binary. "Defines" is read from the export table: an ELF ``GLOBAL`` binding, a
Mach-O export that is not a weak definition, any PE export (a PE export
directory records no coalescing, so there the dependency's exported
instances stay the library's: ADR-089's PE limit). The oracle is that truth
table, written here from the layout, never from the implementation's own
helpers.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import pytest

from abicheck.contract_relevance_types import ContractAssurance, ContractRelevance
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, SymbolBinding
from abicheck.model.macho_facts import MachoExport, MachoMetadata, MachoSymbolType
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.policy.contract_dependency_origin import (
    DEPENDENCY_INTERNAL_UNREACHABLE_REASON,
    exclude_unreachable_dependency_findings,
)
from abicheck.workflows.dump.dependency_internals import (
    dependency_internals,
    include_roots,
    itanium_top_namespace,
)

API = "/w/lib/api.h"


@dataclass(frozen=True)
class Layout:
    """Where the dependency's header sits, and what the snapshot recorded."""

    name: str
    header: str
    compile_args: tuple[str, ...]
    namespace: str
    #: The oracle's answer: can a header compare tell this header is the
    #: dependency's, not the library's?
    classifiable: bool


LAYOUTS = (
    # -I dep/include next to the library's own tree.
    Layout(
        "sibling-root",
        "/w/dep/include/d/x.h",
        ("-I", "/w/dep/include", "-I/w/lib"),
        "d",
        True,
    ),
    # The field report: the dependency vendored under the library's own -I .
    Layout(
        "nested-root",
        "/w/lib/third_party/d/include/d/x.h",
        ("-I", "/w/lib", "-isystem", "/w/lib/third_party/d/include"),
        "d",
        True,
    ),
    # No recorded include directory (no build data): outside every public
    # header's directory is outside the library.
    Layout("unrecorded-sibling", "/w/dep/include/d/x.h", (), "d", True),
    # Vendored under the library's directory with nothing recorded: the path
    # cannot separate it, so nothing is classified.
    Layout("unrecorded-nested", "/w/lib/third_party/d/x.h", (), "d", False),
    # The library's own generated header outside its public directory: its
    # namespace is the library's, so path evidence alone never excludes it.
    Layout("own-generated", "/w/gen/config.h", ("-I", "/w/gen"), "lib", False),
)

BINDINGS = ("global", "weak", "absent")
#: Export-table formats, and whether each records a coalesced (COMDAT)
#: definition: ELF's WEAK binding, Mach-O's weak-definition flag; PE none.
FORMATS = {"elf": True, "macho": True, "pe": False}


def _mangled(namespace: str, name: str, suffix: str = "Ev") -> str:
    return f"_ZN{len(namespace)}{namespace}{len(name)}{name}{suffix}"


def _export_table(fmt: str, exports: list[tuple[str, bool]]) -> dict:
    """``AbiSnapshot`` keyword for *exports* (``(name, weak)``) read as *fmt*
    would record them."""
    if fmt == "elf":
        symbols = [
            ElfSymbol(name=n, binding=SymbolBinding.WEAK if w else SymbolBinding.GLOBAL)
            for n, w in exports
        ]
        return {"elf": ElfMetadata(soname="lib.so", needed=[], symbols=symbols)}
    if fmt == "macho":
        rows = [
            MachoExport(
                name=n,
                sym_type=MachoSymbolType.WEAK if w else MachoSymbolType.EXPORTED,
                is_weak=w,
            )
            for n, w in exports
        ]
        return {"macho": MachoMetadata(install_name="@rpath/lib.dylib", exports=rows)}
    return {"pe": PeMetadata(exports=[PeExport(name=n) for n, _ in exports])}


def _snapshot(
    layout: Layout,
    *,
    vec_named: bool,
    helper_binding: str,
    traits_named: bool,
    fmt: str = "elf",
) -> AbiSnapshot:
    ns, header = layout.namespace, layout.header
    state_fields = [("acc", f"{ns}::Vec" if vec_named else "int"), ("n", "int")]
    helper = _mangled(ns, "helper")
    table = f"_ZN{len(ns)}{ns}5tableE"
    fill = _mangled(ns, "fill", "IfEEvv")  # a weak instance no header declares
    own_fn = _mangled("lib", "f")
    own_inline = _mangled("lib", "inl")  # the library's own coalesced inline
    exports = [(own_fn, False), (own_inline, True), (fill, True)]
    if helper_binding != "absent":
        exports.append((helper, helper_binding == "weak"))
    return AbiSnapshot(
        library="lib.so",
        version="1",
        from_headers=True,
        ast_compile_args=layout.compile_args,
        functions=[
            Function(
                name="f",
                mangled=own_fn,
                return_type="void",
                params=[Param(name="s", type="lib::State*")],
                visibility=Visibility.PUBLIC,
                source_header=API,
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            Function(
                name="inl",
                mangled=own_inline,
                return_type="void",
                visibility=Visibility.PUBLIC,
                source_header=API,
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            Function(
                name="helper",
                mangled=helper,
                return_type="void",
                visibility=Visibility.PUBLIC,
                source_header=header,
            ),
        ],
        variables=[
            Variable(
                name="table",
                mangled=table,
                type="const int[4]",
                visibility=Visibility.PUBLIC,
                source_header=header,
            )
        ],
        types=[
            RecordType(
                name="State",
                qualified_name="lib::State",
                kind="struct",
                fields=[TypeField(name=n, type=t) for n, t in state_fields],
                source_header=API,
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            RecordType(
                name="Vec",
                qualified_name=f"{ns}::Vec",
                kind="struct",
                source_header=header,
            ),
            RecordType(
                name="Tag",
                qualified_name=f"{ns}::Tag",
                kind="struct",
                source_header=header,
            ),
        ],
        typedefs={"traits": "Tag"},
        typedefs_qualified={f"{ns}::Vec::traits": "Tag"},
        public_header_identifiers=frozenset(
            {"lib", "State", "f"} | ({"traits"} if traits_named else set())
        ),
        **_export_table(fmt, exports),
    )


def _oracle(
    layout: Layout,
    *,
    vec_named: bool,
    helper_binding: str,
    traits_named: bool,
    fmt: str = "elf",
) -> dict[str, bool]:
    """Entity -> internal?, from the layout alone."""
    dep = layout.classifiable
    ns = layout.namespace
    coalesced = FORMATS[fmt]
    # The library defines the helper when its binary exports it as its own: a
    # coalesced export is a COMDAT copy, where the format records one.
    helper_defined = helper_binding == "global" or (
        helper_binding == "weak" and not coalesced
    )
    return {
        "Tag": dep,
        "Vec": dep and not vec_named,
        "traits": dep and not traits_named,
        _mangled(ns, "helper"): dep and not helper_defined,
        f"_ZN{len(ns)}{ns}5tableE": dep,
        # No header declares it: only a recorded coalesced export names it.
        _mangled(ns, "fill", "IfEEvv"): dep and coalesced,
        _mangled("lib", "f"): False,
        _mangled("lib", "inl"): False,
        "State": False,
    }


CASES = list(
    itertools.product(LAYOUTS, (True, False), BINDINGS, (True, False), FORMATS)
)


def _id(case: tuple) -> str:
    layout, vec_named, binding, traits_named, fmt = case
    return f"{fmt}-{layout.name}-vec{'+' if vec_named else '-'}-{binding}-traits{'+' if traits_named else '-'}"


def _kwargs(case: tuple) -> dict:
    _, vec_named, binding, traits_named, fmt = case
    return {
        "vec_named": vec_named,
        "helper_binding": binding,
        "traits_named": traits_named,
        "fmt": fmt,
    }


@pytest.mark.parametrize("case", CASES, ids=[_id(c) for c in CASES])
def test_internal_entities_match_the_layout_truth_table(case: tuple) -> None:
    layout = case[0]
    kwargs = _kwargs(case)
    internals = dependency_internals(_snapshot(layout, **kwargs))
    named = internals.types | internals.typedefs | internals.symbols
    expected = _oracle(layout, **kwargs)
    assert {
        e for e, internal in expected.items() if internal
    } == named & expected.keys()
    assert not (named - expected.keys()), named - expected.keys()


_FINDINGS = (
    # (kind, entity): presence, typedef and declaration kinds the rule moves.
    (ChangeKind.TYPE_ADDED, "Tag"),
    (ChangeKind.TYPE_REMOVED, "Vec"),
    (ChangeKind.TYPEDEF_REMOVED, "traits"),
    (ChangeKind.HIDDEN_FRIEND_REMOVED, "helper"),
    (ChangeKind.VAR_ADDED, "table"),
    (ChangeKind.FUNC_REMOVED_ELF_ONLY, "fill"),
    (ChangeKind.FUNC_REMOVED, "f"),
    (ChangeKind.FUNC_REMOVED, "inl"),
    (ChangeKind.TYPE_SIZE_CHANGED, "Tag"),
)


@pytest.mark.parametrize("case", CASES, ids=[_id(c) for c in CASES])
def test_findings_leave_the_contract_exactly_when_their_entity_is_internal(
    case: tuple,
) -> None:
    layout = case[0]
    kwargs = _kwargs(case)
    internals = dependency_internals(_snapshot(layout, **kwargs))
    expected = _oracle(layout, **kwargs)
    ns = layout.namespace
    symbol_of = {
        "helper": _mangled(ns, "helper"),
        "table": f"_ZN{len(ns)}{ns}5tableE",
        "fill": _mangled(ns, "fill", "IfEEvv"),
        "f": _mangled("lib", "f"),
        "inl": _mangled("lib", "inl"),
    }
    changes = [
        Change(kind=kind, symbol=symbol_of.get(entity, entity), description="")
        for kind, entity in _FINDINGS
    ]
    decisions = [
        _Decision(ContractRelevance.IN_CONTRACT, "public_root_membership")
        for _ in changes
    ]
    out = exclude_unreachable_dependency_findings(
        changes, decisions, old=internals, new=internals
    )
    for change, decision in zip(changes, out, strict=True):
        moved = decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
        # A layout change is not presence evidence: never moved (ADR-089 D5).
        want = (
            expected[change.symbol] and change.kind is not ChangeKind.TYPE_SIZE_CHANGED
        )
        assert moved is want, (change.kind, change.symbol)
        if moved:
            assert decision.reason_code == DEPENDENCY_INTERNAL_UNREACHABLE_REASON


@dataclass(frozen=True)
class _Decision:
    relevance: ContractRelevance
    reason_code: str | None
    assurance: ContractAssurance = ContractAssurance.COMPLETE


def test_no_public_header_classifies_nothing() -> None:
    snap = _snapshot(
        LAYOUTS[0], vec_named=False, helper_binding="absent", traits_named=False
    )
    for decl in [*snap.declarations.functions, *snap.declarations.types]:
        decl.origin = ScopeOrigin.UNKNOWN
    assert not dependency_internals(snap)


def test_no_export_table_keeps_every_function_and_variable() -> None:
    snap = _snapshot(
        LAYOUTS[0], vec_named=False, helper_binding="absent", traits_named=False
    )
    snap.elf = None
    internals = dependency_internals(snap)
    assert internals.symbols == frozenset()
    assert internals.types == {"Tag", "Vec"}


@pytest.mark.parametrize(
    ("args", "roots"),
    [
        (("-I", "/a", "-I/b"), ("/a", "/b")),
        (("-isystem", "/s", "-isystem/t", "-iquote", "/q"), ("/s", "/t", "/q")),
        (("-idirafter", "/z/../y", "-I", "/a/"), ("/y", "/a")),
        (("-I", "/a", "-I", "/a", "-DX", "-std=c++20"), ("/a",)),
        (("-I",), ()),
    ],
)
def test_include_roots_reads_every_include_flag_form(
    args: tuple[str, ...], roots: tuple[str, ...]
) -> None:
    assert include_roots(args) == roots


@pytest.mark.parametrize(
    ("mangled", "namespace"),
    [
        ("_ZN8fakesimd6reduceIfEET_RKNS_9basic_vecIS1_EE", "fakesimd"),
        ("_ZNK3lib1A1fEv", "lib"),
        ("_ZNKSt6vectorIiE3endEv", "std"),
        ("_ZSt4moveIRiEOT_S1_", "std"),
        ("_Z3fooi", ""),
        ("_ZTV3Foo", None),
        ("foo", None),
    ],
)
def test_itanium_top_namespace(mangled: str, namespace: str | None) -> None:
    assert itanium_top_namespace(mangled) == namespace
