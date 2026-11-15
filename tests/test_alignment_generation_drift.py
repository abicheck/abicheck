"""The pre-generation-74 clang builtin-alignment artifact is annotated, nothing else.

Before generation 74 the clang builtin probe measured the host ABI, so an
``extern long value`` parsed for i386 recorded the x86-64 alignment (64 bits)
in a pre-74 snapshot and the i386 alignment (32 bits) from 74. The oracle below
states, independently of the implementation, which ``VAR_ALIGNMENT_CHANGED``
findings that artifact can produce: across the generation-74 boundary, with the
stale operand's alignment the LP64 natural alignment of its builtin or pointer
type and the fresh operand's the ILP32 natural alignment of the same type. A
real change, a same-generation pair or an unknown type keeps its verdict.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.declarations import Variable
from abicheck.model.snapshot import AbiSnapshot
from abicheck.policy.alignment_generation_drift import (
    demote_pre74_builtin_alignment_drift,
)

#: type -> (LP64 natural bits, ILP32 natural bits), written out by hand.
_NATURAL = {
    "long": (64, 32),
    "long double": (128, 32),
    "size_t": (64, 32),
    "void *": (64, 32),
}
_TYPES = tuple(_NATURAL) + ("int", "double")
_GENERATIONS = (0, 73, 74, 75)
#: Alignments a variable can carry: the two natural values of each type, an
#: explicit ``alignas(16)`` (128) and an absent alignment (None).
_ALIGNS = (None, 16, 32, 64, 128)


def _snap(gen_type: str, align: int | None, producer: str = "clang") -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version="1",
        ast_producer=producer,
        variables=[
            Variable(name="value", mangled="value", type=gen_type, alignment_bits=align)
        ],
    )


def _artifact(
    old_gen: int,
    new_gen: int,
    typ: str,
    old_align: int | None,
    new_align: int | None,
) -> bool:
    if old_gen == new_gen or typ not in _NATURAL:
        return False
    if old_gen < new_gen:
        stale_gen, fresh_gen = old_gen, new_gen
        stale_align, fresh_align = old_align, new_align
    else:
        stale_gen, fresh_gen = new_gen, old_gen
        stale_align, fresh_align = new_align, old_align
    lp64, ilp32 = _NATURAL[typ]
    return stale_gen < 74 <= fresh_gen and stale_align == lp64 and fresh_align == ilp32


CASES = list(itertools.product(_GENERATIONS, _GENERATIONS, _TYPES, _ALIGNS, _ALIGNS))


def test_only_the_pre74_host_alignment_artifact_is_annotated() -> None:
    mismatches = []
    for old_gen, new_gen, typ, old_align, new_align in CASES:
        change = Change(
            ChangeKind.VAR_ALIGNMENT_CHANGED,
            "value",
            "alignment changed",
            old_value=str(old_align),
            new_value=str(new_align),
        )
        demote_pre74_builtin_alignment_drift(
            [change],
            _snap(typ, old_align),
            _snap(typ, new_align),
            old_gen,
            new_gen,
        )
        want = _artifact(old_gen, new_gen, typ, old_align, new_align)
        got = change.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
        if got != want:
            mismatches.append((old_gen, new_gen, typ, old_align, new_align))
    assert mismatches == []


@pytest.mark.parametrize("typ", list(_NATURAL))
def test_host_to_target_alignment_is_demoted_and_recorded(typ: str) -> None:
    lp64, ilp32 = _NATURAL[typ]
    change = Change(
        ChangeKind.VAR_ALIGNMENT_CHANGED,
        "value",
        "alignment changed",
        old_value=str(lp64),
        new_value=str(ilp32),
    )
    demote_pre74_builtin_alignment_drift(
        [change], _snap(typ, lp64), _snap(typ, ilp32), 73, 74
    )
    assert change.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
    assert change.modulation_rule == "extractor_generation_drift"
    assert str(lp64) in change.modulation_reason


@pytest.mark.parametrize("typ", list(_NATURAL))
def test_a_real_alignment_change_across_the_boundary_is_kept(typ: str) -> None:
    # An explicit alignas(16) on the fresh side is not the target natural value.
    change = Change(
        ChangeKind.VAR_ALIGNMENT_CHANGED,
        "value",
        "alignment changed",
        old_value="64",
        new_value="128",
    )
    demote_pre74_builtin_alignment_drift(
        [change], _snap(typ, 64), _snap(typ, 128), 73, 74
    )
    assert change.effective_verdict is None


@pytest.mark.parametrize("typ", list(_NATURAL))
def test_same_generation_operands_are_never_touched(typ: str) -> None:
    lp64, ilp32 = _NATURAL[typ]
    change = Change(
        ChangeKind.VAR_ALIGNMENT_CHANGED,
        "value",
        "alignment changed",
        old_value=str(lp64),
        new_value=str(ilp32),
    )
    demote_pre74_builtin_alignment_drift(
        [change], _snap(typ, lp64), _snap(typ, ilp32), 74, 74
    )
    assert change.effective_verdict is None


def test_a_type_outside_the_probed_builtins_is_never_touched() -> None:
    change = Change(
        ChangeKind.VAR_ALIGNMENT_CHANGED,
        "value",
        "alignment changed",
        old_value="64",
        new_value="32",
    )
    demote_pre74_builtin_alignment_drift(
        [change], _snap("int", 64), _snap("int", 32), 73, 74
    )
    assert change.effective_verdict is None


def test_a_symbol_missing_on_one_side_is_never_touched() -> None:
    change = Change(
        ChangeKind.VAR_ALIGNMENT_CHANGED,
        "value",
        "alignment changed",
        old_value="64",
        new_value="32",
    )
    old = _snap("long", 64)
    new = AbiSnapshot(library="libx.so", version="1", ast_producer="clang")
    demote_pre74_builtin_alignment_drift([change], old, new, 73, 74)
    assert change.effective_verdict is None


def test_a_retyped_variable_is_never_touched() -> None:
    change = Change(
        ChangeKind.VAR_ALIGNMENT_CHANGED,
        "value",
        "alignment changed",
        old_value="64",
        new_value="32",
    )
    demote_pre74_builtin_alignment_drift(
        [change], _snap("long", 64), _snap("long long", 32), 73, 74
    )
    assert change.effective_verdict is None


def test_other_kinds_and_already_dispositioned_changes_are_never_touched() -> None:
    other = Change(
        ChangeKind.VAR_TYPE_CHANGED,
        "value",
        "type changed",
        old_value="long",
        new_value="int",
    )
    settled = Change(
        ChangeKind.VAR_ALIGNMENT_CHANGED,
        "value",
        "alignment changed",
        old_value="64",
        new_value="32",
    )
    settled.effective_verdict = Verdict.BREAKING
    demote_pre74_builtin_alignment_drift(
        [other, settled], _snap("long", 64), _snap("long", 32), 73, 74
    )
    assert other.effective_verdict is None
    assert settled.effective_verdict is Verdict.BREAKING
