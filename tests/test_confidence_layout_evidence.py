"""HIGH confidence requires record-layout evidence (report claims audit).

A header-plus-binary comparison used to report HIGH whether or not anything
could see a struct's size, alignment or field offsets. The clang header
frontend parses declarations but computes no record layout, so with no DWARF
on a side a layout change reads NO_CHANGE at HIGH. HIGH now needs layout
evidence on every compared side: DWARF layout, or a header frontend that
computes layout (CastXML, or the clang layout companion). Anything less is
capped at MEDIUM with a warning that says why.

The truth table below is written by hand from that rule, not generated from
the implementation, so a change to the rule fails it.
"""

from __future__ import annotations

import pytest

from abicheck.checker import Verdict, compare
from abicheck.checker_policy import Confidence
from abicheck.confidence import compute_confidence
from abicheck.dwarf_metadata import DwarfMetadata, StructLayout
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility

_LAYOUT_WARNING = "without record layout on every compared side"


def _elf() -> ElfMetadata:
    return ElfMetadata(
        soname="libtest.so.1",
        symbols=[
            ElfSymbol(
                name="_Z3apiv",
                binding=SymbolBinding.GLOBAL,
                sym_type=SymbolType.FUNC,
            )
        ],
    )


def _snap(
    *,
    dwarf: bool,
    header_layout: bool,
    size_bits: int | None = None,
    headers: bool = True,
) -> AbiSnapshot:
    """A snapshot with an ELF table, headers-derived unless *headers* is off.

    *header_layout* selects the header frontend: CastXML computes layout,
    the bare clang parse does not. *size_bits* sets the record size; with a
    clang frontend a size is present only when the layout companion filled it.
    The record is a parameter of the public function, so its layout is part
    of the compared surface.
    """
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=[
            Function(
                name="api",
                mangled="_Z3apiv",
                return_type="void",
                params=[Param(name="c", type="Cfg")],
                visibility=Visibility.PUBLIC,
            )
        ]
        if headers
        else [],
        types=[RecordType(name="Cfg", kind="struct", size_bits=size_bits)]
        if headers
        else [],
        elf=_elf(),
        dwarf=(
            DwarfMetadata(
                structs={"Cfg": StructLayout(name="Cfg", byte_size=4)},
                has_dwarf=True,
            )
            if dwarf
            else None
        ),
        from_headers=headers,
        ast_producer=("castxml" if header_layout else "clang") if headers else None,
    )


# (old side has DWARF, new side has DWARF, header frontend computes layout)
#: -> expected maximum confidence for a headers + ELF comparison.
#: Layout must be known on both sides: a side with neither DWARF nor a
#: layout-computing header frontend leaves a layout change unestablishable.
TRUTH_TABLE = [
    (False, False, False, Confidence.MEDIUM),
    (True, False, False, Confidence.MEDIUM),
    (False, True, False, Confidence.MEDIUM),
    (True, True, False, Confidence.HIGH),
    (False, False, True, Confidence.HIGH),
    (True, False, True, Confidence.HIGH),
    (False, True, True, Confidence.HIGH),
    (True, True, True, Confidence.HIGH),
]


@pytest.mark.parametrize(
    ("old_dwarf", "new_dwarf", "header_layout", "expected"),
    TRUTH_TABLE,
    ids=[
        f"old_dwarf={o}-new_dwarf={n}-header_layout={h}" for o, n, h, _ in TRUTH_TABLE
    ],
)
def test_headers_plus_elf_confidence_truth_table(
    old_dwarf: bool,
    new_dwarf: bool,
    header_layout: bool,
    expected: Confidence,
) -> None:
    old = _snap(dwarf=old_dwarf, header_layout=header_layout)
    new = _snap(dwarf=new_dwarf, header_layout=header_layout)
    _, confidence, warnings, _ = compute_confidence([], old, new)
    assert confidence is expected
    capped = expected is Confidence.MEDIUM
    assert any(_LAYOUT_WARNING in w for w in warnings) is capped


@pytest.mark.parametrize(
    ("new_dwarf", "header_layout", "expected"),
    [
        (False, False, Confidence.MEDIUM),
        (True, False, Confidence.HIGH),
        (False, True, Confidence.HIGH),
    ],
)
def test_baseline_less_audit_judges_the_candidate_alone(
    new_dwarf: bool, header_layout: bool, expected: Confidence
) -> None:
    new = _snap(dwarf=new_dwarf, header_layout=header_layout)
    _, confidence, _, _ = compute_confidence([], None, new)
    assert confidence is expected


def test_clang_layout_companion_size_counts_as_header_layout() -> None:
    # A clang snapshot whose records carry sizes (the layout companion ran)
    # has header-computed layout, so HIGH is earned without DWARF.
    old = _snap(dwarf=False, header_layout=False, size_bits=64)
    new = _snap(dwarf=False, header_layout=False, size_bits=64)
    _, confidence, _, _ = compute_confidence([], old, new)
    assert confidence is Confidence.HIGH


def test_headers_without_binary_are_unaffected_by_layout_rule() -> None:
    # Header-only (no ELF) stays MEDIUM as before; the layout rule only
    # narrows the headers-plus-binary HIGH case.
    old = AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=[
            Function(
                name="api",
                mangled="_Z3apiv",
                return_type="void",
                params=[],
                visibility=Visibility.PUBLIC,
            )
        ],
        from_headers=True,
        ast_producer="castxml",
    )
    _, confidence, _, _ = compute_confidence([], old, old)
    assert confidence is Confidence.MEDIUM


def test_binary_only_without_debug_stays_low() -> None:
    # No headers at all: the binary-only floor is unchanged.
    snap = _snap(dwarf=False, header_layout=True, headers=False)
    _, confidence, _, _ = compute_confidence([], snap, snap)
    assert confidence is Confidence.LOW


def test_clang_without_layout_reports_no_change_at_medium_not_high() -> None:
    # The field report's shape: a layout change exists, but neither side
    # carries a size (clang computes none, no DWARF), so the verdict cannot
    # see it. Confidence must say the comparison was not layout-capable.
    old = _snap(dwarf=False, header_layout=False, size_bits=None)
    new = _snap(dwarf=False, header_layout=False, size_bits=None)
    result = compare(old, new)
    assert result.verdict is Verdict.NO_CHANGE
    assert result.confidence is Confidence.MEDIUM


def test_castxml_layout_change_is_seen_and_stays_high() -> None:
    # Same record, a real size change, and the header frontend computes
    # layout: the change is detected and the comparison is HIGH.
    old = _snap(dwarf=False, header_layout=True, size_bits=64)
    new = _snap(dwarf=False, header_layout=True, size_bits=128)
    result = compare(old, new)
    assert result.verdict is Verdict.BREAKING
    assert result.confidence is Confidence.HIGH
