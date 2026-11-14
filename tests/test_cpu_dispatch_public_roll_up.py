"""The CPU-dispatch roll-up never grades a header-declared removal below BREAKING.

Bug class: a name heuristic (``cpu_dispatch_isa``) lowered the removal of
public, header-declared functions to a risk. Invariant, checked over the whole
small domain of per-member visibilities: the roll-up's contributed verdict is
BREAKING iff at least one removed member was declared in a header
(``Visibility.PUBLIC``), and COMPATIBLE_WITH_RISK iff every removed member is
export-only (``Visibility.ELF_ONLY``). The oracle is that rule stated directly
on the inputs, plus the end-to-end ``compare()`` verdict.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.diff_cpp_patterns import detect_cpu_dispatch_isa_dropped
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change_catalog.registry import Verdict
from abicheck.pe_metadata import PeExport, PeMetadata

_ALGOS = ("kmeans", "knn", "linreg")
_SURVIVING = ("avx2", "sse42")


def _fn(algo: str, isa: str, vis: Visibility) -> Function:
    ident = f"{algo}_compute_{isa}"
    return Function(
        name=ident,
        mangled=f"_Z{len(ident)}{ident}i",
        return_type="int",
        visibility=vis,
    )


def _pair(dropped_vis: tuple[Visibility, ...]) -> tuple[AbiSnapshot, AbiSnapshot]:
    survivors = [_fn(a, isa, Visibility.PUBLIC) for a in _ALGOS for isa in _SURVIVING]
    dropped = [_fn(a, "avx512", v) for a, v in zip(_ALGOS, dropped_vis)]
    old = AbiSnapshot(library="lib.so", version="1", functions=survivors + dropped)
    new = AbiSnapshot(library="lib.so", version="2", functions=list(survivors))
    return old, new


_DOMAIN = list(itertools.product((Visibility.PUBLIC, Visibility.ELF_ONLY), repeat=3))


@pytest.mark.parametrize("dropped_vis", _DOMAIN)
def test_roll_up_grade_follows_header_declaration(
    dropped_vis: tuple[Visibility, ...],
) -> None:
    expected = (
        Verdict.BREAKING
        if Visibility.PUBLIC in dropped_vis
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
