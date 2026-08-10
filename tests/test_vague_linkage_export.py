# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A dropped export the build *proved* was vague-linkage is a risk, not a break.

The export table alone cannot tell a removed strong definition from a removed
COMDAT copy the consumer already carries: both are ``WEAK`` in ``.dynsym``,
including an ``__attribute__((weak))`` out-of-line function whose consumers
hold no copy at all. Only the old build's object files settle it, and only
positively — no evidence is never a licence to demote.

Tested at the predicate *and* through ``compare()``: two detectors in two
modules observe this one event, and a predicate-level test cannot see one of
them re-reporting the symbol at BREAKING and dominating the verdict, which is
exactly what happened on the first attempt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.comdat_groups import ComdatScan
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.checker import Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.symbol_linkage import vague_linkage_export_dropped, vague_linkage_symbols

MANGLED = "_ZN2ns5vagueEv"
OTHER = "_ZN2ns6strongEv"


def _fn(mangled: str = MANGLED) -> Function:
    return Function(
        name="ns::vague",
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
    )


def _snap(
    funcs: list[Function],
    exported: list[str],
    *,
    vague: frozenset[str] | None = None,
    scanned: int = 1,
) -> AbiSnapshot:
    """A snapshot whose ELF exports narrow the public map, optionally carrying
    L3 COMDAT evidence.

    ``vague=None`` models a build with no scan at all — the common case, and
    the one that must never demote.
    """
    snap = AbiSnapshot(
        library="libx.so",
        version="1",
        functions=funcs,
        elf=ElfMetadata(
            soname="libx.so.1",
            symbols=[
                ElfSymbol(name=n, binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC)
                for n in exported
            ],
        ),
    )
    if vague is not None:
        evidence = BuildEvidence(
            compile_units=[CompileUnit(id="1", output="x.o")],
            comdat=ComdatScan(symbols=vague, objects_scanned=scanned),
        )
        snap.build_source = BuildSourcePack(root=Path(""), build_evidence=evidence)
    return snap


def _new_side() -> AbiSnapshot:
    """The export is gone; the entity is still declared in the headers."""
    return _snap([_fn()], ["_Z5otherv"])


class TestThePredicate:
    def test_a_proven_vague_symbol_still_declared_is_a_carried_copy(self) -> None:
        assert vague_linkage_export_dropped(_fn(), _fn(), frozenset({MANGLED}))

    def test_a_symbol_the_build_did_not_prove_is_a_real_removal(self) -> None:
        """The `__attribute__((weak))` case: WEAK in `.dynsym`, but never in a
        COMDAT group, so consumers hold no copy of their own."""
        assert not vague_linkage_export_dropped(_fn(), _fn(), frozenset({OTHER}))

    def test_no_evidence_never_demotes(self) -> None:
        """`None` is "not established", not "nothing is vague"."""
        assert not vague_linkage_export_dropped(_fn(), _fn(), None)

    def test_gone_from_the_headers_too_is_a_real_removal(self) -> None:
        assert not vague_linkage_export_dropped(_fn(), None, frozenset({MANGLED}))


class TestTheEvidenceGate:
    def test_a_snapshot_without_build_source_is_unestablished(self) -> None:
        assert vague_linkage_symbols(_snap([_fn()], [MANGLED])) is None

    def test_an_unresolvable_scan_is_unestablished(self) -> None:
        """A scan that read nothing must not present its empty symbol set as
        "this build has nothing vague"."""
        snap = _snap([_fn()], [MANGLED], vague=frozenset({MANGLED}), scanned=0)
        assert vague_linkage_symbols(snap) is None

    def test_a_real_scan_is_established(self) -> None:
        snap = _snap([_fn()], [MANGLED], vague=frozenset({MANGLED}))
        assert vague_linkage_symbols(snap) == frozenset({MANGLED})


class TestThroughCompare:
    def test_the_demoted_event_is_reported_once_as_a_risk(self) -> None:
        result = compare(
            _snap([_fn()], [MANGLED], vague=frozenset({MANGLED})), _new_side()
        )
        kinds = sorted(c.kind.value for c in result.changes)
        assert kinds == ["func_vague_export_dropped"], kinds
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_the_elf_fallback_detector_does_not_re_report_it(self) -> None:
        """The regression a predicate-level test cannot see: `diff_platform`
        fired BREAKING on the same symbol, both double-reporting it and
        dominating the verdict, undoing the demotion entirely."""
        result = compare(
            _snap([_fn()], [MANGLED], vague=frozenset({MANGLED})), _new_side()
        )
        assert not [
            c for c in result.changes if c.kind.value == "func_deleted_elf_fallback"
        ]

    @pytest.mark.parametrize(
        "vague",
        [None, frozenset(), frozenset({OTHER})],
        ids=["no-evidence", "scanned-none", "other-symbol"],
    )
    def test_without_proof_the_removal_still_breaks(
        self, vague: frozenset[str] | None
    ) -> None:
        old = _snap([_fn()], [MANGLED], vague=vague)
        result = compare(old, _new_side())
        kinds = {c.kind.value for c in result.changes}
        assert "func_removed" in kinds, kinds
        assert "func_vague_export_dropped" not in kinds
        assert result.verdict is Verdict.BREAKING

    def test_a_proven_symbol_gone_from_the_headers_still_breaks(self) -> None:
        result = compare(
            _snap([_fn()], [MANGLED], vague=frozenset({MANGLED})),
            _snap([], ["_Z5otherv"]),
        )
        assert result.verdict is Verdict.BREAKING
