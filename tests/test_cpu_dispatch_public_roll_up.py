"""The CPU-dispatch roll-up never grades a header-declared removal below BREAKING.

Bug class: a name heuristic (``cpu_dispatch_isa``) lowered the removal of
public, header-declared functions to a risk. Invariant, checked over the whole
small domain of per-member visibilities: the roll-up's contributed verdict is
BREAKING iff at least one removed member was declared in a public header
(``ScopeOrigin.PUBLIC_HEADER``), and COMPATIBLE_WITH_RISK otherwise -- an
export-only member (``Visibility.ELF_ONLY``) and a DWARF-only one (visibility
``PUBLIC``, no header origin) alike. The oracle is that rule stated directly
on the inputs, plus the end-to-end ``compare()`` verdict.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.compare.cpu_dispatch_public_members import header_declared_members
from abicheck.diff_cpp_patterns import detect_cpu_dispatch_isa_dropped
from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Visibility
from abicheck.model.change_catalog.registry import Verdict
from abicheck.pe_metadata import PeExport, PeMetadata

_ALGOS = ("kmeans", "knn", "linreg")
_SURVIVING = ("avx2", "sse42")


#: A removed member's evidence: (visibility, origin).
_HEADER = (Visibility.PUBLIC, ScopeOrigin.PUBLIC_HEADER)
_EXPORT_ONLY = (Visibility.ELF_ONLY, ScopeOrigin.UNKNOWN)
_DWARF_ONLY = (Visibility.PUBLIC, ScopeOrigin.UNKNOWN)


def _fn(algo: str, isa: str, kind: tuple[Visibility, ScopeOrigin]) -> Function:
    ident = f"{algo}_compute_{isa}"
    return Function(
        name=ident,
        mangled=f"_Z{len(ident)}{ident}i",
        return_type="int",
        visibility=kind[0],
        origin=kind[1],
    )


def _pair(
    dropped_vis: tuple[tuple[Visibility, ScopeOrigin], ...],
) -> tuple[AbiSnapshot, AbiSnapshot]:
    survivors = [_fn(a, isa, _HEADER) for a in _ALGOS for isa in _SURVIVING]
    dropped = [_fn(a, "avx512", v) for a, v in zip(_ALGOS, dropped_vis)]
    old = AbiSnapshot(library="lib.so", version="1", functions=survivors + dropped)
    new = AbiSnapshot(library="lib.so", version="2", functions=list(survivors))
    return old, new


_DOMAIN = list(itertools.product((_HEADER, _EXPORT_ONLY, _DWARF_ONLY), repeat=3))


@pytest.mark.parametrize("dropped_vis", _DOMAIN)
def test_roll_up_grade_follows_header_declaration(
    dropped_vis: tuple[tuple[Visibility, ScopeOrigin], ...],
) -> None:
    expected = (
        Verdict.BREAKING
        if any(origin is ScopeOrigin.PUBLIC_HEADER for _, origin in dropped_vis)
        else Verdict.COMPATIBLE_WITH_RISK
    )
    old, new = _pair(dropped_vis)
    findings, suppressed = detect_cpu_dispatch_isa_dropped(old, new)
    # The roll-up is kept either way: one finding naming the family.
    assert [f.kind for f in findings] == [ChangeKind.CPU_DISPATCH_ISA_DROPPED]
    contributed = findings[0].effective_verdict or Verdict.COMPATIBLE_WITH_RISK
    assert contributed == expected
    assert {f"_Z{len(a) + 15}{a}_compute_avx512i" for a in _ALGOS} <= suppressed

    result = compare(old, new)
    assert result.verdict == expected


def test_pe_raw_export_fallback_still_sees_header_declaration() -> None:
    """Mismatch-HIDDEN header functions reached only via raw PE ids count as declared."""

    def snap(names: list[str]) -> AbiSnapshot:
        return AbiSnapshot(
            library="mylib.dll",
            version="1",
            functions=[
                Function(
                    name=f"mylib::{n}",
                    mangled=f"?{n}@mylib@@YAHH@Z_GUESSED",
                    return_type="int",
                    visibility=Visibility.HIDDEN,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
                for n in names
            ],
            pe=PeMetadata(
                exports=[PeExport(name=f"?{n}@mylib@@YAHH@Z") for n in names]
            ),
        )

    old_names = [f"{a}_compute_{i}" for a in _ALGOS for i in ("avx512", *_SURVIVING)]
    new_names = [f"{a}_compute_{i}" for a in _ALGOS for i in _SURVIVING]
    findings, _ = detect_cpu_dispatch_isa_dropped(snap(old_names), snap(new_names))
    assert len(findings) == 1
    assert findings[0].effective_verdict == Verdict.BREAKING


def _hdr(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", origin=ScopeOrigin.PUBLIC_HEADER
    )


@pytest.mark.parametrize(
    ("declared", "removed", "expected"),
    [
        # The qualified name matches: public.
        (["api::f_avx512"], ["?f_avx512@api@@YAHH@Z"], ["?f_avx512@api@@YAHH@Z"]),
        # Same bare identifier, different scope: export-only, not public.
        (["api::f_avx512"], ["?f_avx512@impl@@YAHH@Z"], []),
        # A global header function does not vouch for a scoped export.
        (["f_avx512"], ["?f_avx512@impl@@YAHH@Z"], []),
        # Two removed overloads under one header name: which one the header
        # declares is not decoded, so neither borrows it.
        (
            ["api::f_avx512"],
            ["?f_avx512@api@@YAHH@Z", "?f_avx512@api@@YAHN@Z"],
            [],
        ),
        # Two header overloads, one removed export: still ambiguous.
        (["api::f_avx512", "api::f_avx512"], ["?f_avx512@api@@YAHH@Z"], []),
    ],
)
def test_msvc_export_matches_by_qualified_identity(
    declared: list[str], removed: list[str], expected: list[str]
) -> None:
    funcs = [_hdr(n, f"_GUESSED_{i}") for i, n in enumerate(declared)]
    assert header_declared_members(removed, funcs) == expected


def test_dwarf_only_function_is_not_header_declared() -> None:
    dwarf = Function(name="f_avx512", mangled="_Z8f_avx512i", return_type="int")
    assert dwarf.visibility is Visibility.PUBLIC
    assert header_declared_members(["_Z8f_avx512i"], [dwarf]) == []
    hidden_header = Function(
        name="f_avx512",
        mangled="_Z8f_avx512i",
        return_type="int",
        visibility=Visibility.HIDDEN,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    assert header_declared_members(["_Z8f_avx512i"], [hidden_header]) == [
        "_Z8f_avx512i"
    ]


def test_a_member_still_declared_in_new_is_no_header_removal() -> None:
    """``lib::scale(V<avx2>)`` re-mangled because a header-only dependency's
    ``V`` changed: its export disappears, but ``lib::scale`` is still
    declared, so its own ``func_params_changed`` reports it -- the roll-up
    must not grade it as a removed public member."""
    from abicheck.compare.cpu_dispatch_public_members import redeclared_in

    old_fn = Function(
        name="scale",
        mangled="_ZN3lib5scaleEN1d3vecINS0_8avx2_tagEEE",
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    new_fn = Function(
        name="scale",
        mangled="_ZN3lib5scaleEN1d3vecINS0_10avx512_tagEEE",
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    gone = Function(
        name="other_avx2",
        mangled="_ZN3lib10other_avx2Ev",
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    old = AbiSnapshot(library="l.so", version="1", functions=[old_fn, gone])
    new = AbiSnapshot(library="l.so", version="2", functions=[new_fn])
    ids = [old_fn.mangled, gone.mangled]
    still = redeclared_in(old, new)
    assert header_declared_members(ids, old.declarations.functions, still) == [
        gone.mangled
    ]
    assert header_declared_members(ids, old.declarations.functions) == ids
