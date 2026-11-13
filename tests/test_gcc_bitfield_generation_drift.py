"""The pre-generation-60 gcc-plugin byte-aligned bit-field drift is annotated, nothing else.

Before generation 60 the native gcc header plugin stored a byte-aligned,
whole-byte bit-field (``unsigned a : 8`` at offset 0) as a plain member. The
oracle below states, independently of the implementation, exactly which
``FIELD_BITFIELD_CHANGED`` findings that artifact can produce: across the
generation-60 boundary, with a gcc-plugin stale operand, the stale side not a
bit-field and the fresh side a bit-field of a positive multiple of 8 bits.
Every other combination must keep its verdict.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.snapshot import AbiSnapshot
from abicheck.policy.bitfield_generation_drift import demote_gcc_byte_bitfield_drift
from abicheck.workflows import generation_drift

_PRODUCERS = ("gcc-experimental", "castxml", "clang", None)
_GENERATIONS = (0, 58, 59, 60, 61)
_BITS = ("None", "1", "4", "8", "16", "24", "32")


def _snap(producer: str | None) -> AbiSnapshot:
    return AbiSnapshot(library="libx.so", version="1", ast_producer=producer)


def _artifact(
    old_gen: int, new_gen: int, old_prod, new_prod, old_bits, new_bits
) -> bool:
    if old_gen == new_gen:
        return False
    if old_gen < new_gen:
        stale_gen, fresh_gen, stale_prod = old_gen, new_gen, old_prod
        stale_bits, fresh_bits = old_bits, new_bits
    else:
        stale_gen, fresh_gen, stale_prod = new_gen, old_gen, new_prod
        stale_bits, fresh_bits = new_bits, old_bits
    return (
        stale_gen < 60 <= fresh_gen
        and stale_prod == "gcc-experimental"
        and stale_bits == "None"
        and fresh_bits != "None"
        and int(fresh_bits) % 8 == 0
    )


CASES = list(
    itertools.product(_GENERATIONS, _GENERATIONS, _PRODUCERS, _PRODUCERS, _BITS, _BITS)
)


@pytest.mark.parametrize("case", [CASES], ids=["exhaustive"])
def test_only_the_pre60_gcc_byte_bitfield_shape_is_annotated(case) -> None:
    mismatches = []
    for old_gen, new_gen, old_prod, new_prod, old_bits, new_bits in case:
        change = Change(
            ChangeKind.FIELD_BITFIELD_CHANGED,
            "S",
            "bit-field changed",
            old_value=f"bits={old_bits}",
            new_value=f"bits={new_bits}",
        )
        demote_gcc_byte_bitfield_drift(
            [change], _snap(old_prod), _snap(new_prod), old_gen, new_gen
        )
        want = _artifact(old_gen, new_gen, old_prod, new_prod, old_bits, new_bits)
        got = change.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
        if got != want:
            mismatches.append(
                (old_gen, new_gen, old_prod, new_prod, old_bits, new_bits)
            )
    assert mismatches == []


def test_annotation_keeps_the_finding_with_rule_and_reason() -> None:
    change = Change(
        ChangeKind.FIELD_BITFIELD_CHANGED,
        "S",
        "bit-field changed",
        old_value="bits=None",
        new_value="bits=8",
    )
    out = demote_gcc_byte_bitfield_drift(
        [change], _snap("gcc-experimental"), _snap("gcc-experimental"), 59, 60
    )
    assert out == [change]
    assert change.modulation_rule == "extractor_generation_drift"
    assert "byte-aligned bit-field (bits=8)" in (change.modulation_reason or "")


def test_other_kinds_and_modulated_findings_are_untouched() -> None:
    other = Change(
        ChangeKind.TYPE_SIZE_CHANGED,
        "S",
        "size",
        old_value="bits=None",
        new_value="bits=8",
    )
    prior = Change(
        ChangeKind.FIELD_BITFIELD_CHANGED,
        "S",
        "bit-field",
        old_value="bits=None",
        new_value="bits=8",
    )
    prior.effective_verdict = Verdict.BREAKING
    demote_gcc_byte_bitfield_drift(
        [other, prior], _snap("gcc-experimental"), _snap(None), 59, 60
    )
    assert other.effective_verdict is None
    assert prior.effective_verdict is Verdict.BREAKING


def test_a_stored_gen59_gcc_baseline_against_a_live_dump_is_annotated() -> None:
    """Through the workflow: a stored pre-60 gcc-plugin baseline (stamped
    generation 59) against a live dump (current generation)."""
    stored = AbiSnapshot(
        library="libx.so",
        version="1",
        ast_producer="gcc-experimental",
        fact_generations=(59, 0, 0),
    )
    live = _snap("gcc-experimental")
    change = Change(
        ChangeKind.FIELD_BITFIELD_CHANGED,
        "S",
        "bit-field changed",
        old_value="bits=None",
        new_value="bits=8",
    )
    generation_drift.demote_cross_generation_drift([change], stored, live)
    assert change.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
    # Same generation on both sides: the identical finding keeps its verdict.
    same = Change(
        ChangeKind.FIELD_BITFIELD_CHANGED,
        "S",
        "bit-field changed",
        old_value="bits=None",
        new_value="bits=8",
    )
    generation_drift.demote_cross_generation_drift([same], live, live)
    assert same.effective_verdict is None
