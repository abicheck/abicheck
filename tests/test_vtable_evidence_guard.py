# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``_vtable_transition_is_evidenced`` — what may suppress a vtable finding.

``RecordType.vtable`` cannot express "not captured", so an empty↔non-empty
difference is ambiguous between a real polymorphism change and one side's
debug info going missing. The guard suppresses only when it can positively
show layout held still; every "cannot tell" keeps the finding.

Tested here rather than through the FP-rate corpus on purpose: the corpus
measures *verdicts*, and ``diff_layout._check_vptr_introduced`` independently
reports the same transition, so a verdict-level case cannot tell whether this
guard did its own job or was carried by its neighbour.
"""
from __future__ import annotations

import pytest

from abicheck.diff_types import _vtable_transition_is_evidenced
from abicheck.model import Function, RecordType, Visibility

NAME = "Abstract"


def _cls(
    vtable: list[str],
    *,
    size_bits: int | None = 64,
    vptr_offset_bits: int | None = None,
    virtual_bases: list[str] | None = None,
) -> RecordType:
    return RecordType(
        name=NAME,
        kind="class",
        size_bits=size_bits,
        vtable=vtable,
        vptr_offset_bits=vptr_offset_bits,
        virtual_bases=list(virtual_bases or []),
    )


def _virtual() -> dict[str, Function]:
    fn = Function(
        name=f"{NAME}::f",
        mangled=f"_ZN8{NAME}1fEv",
        return_type="void",
        visibility=Visibility.PUBLIC,
        is_virtual=True,
    )
    return {fn.mangled: fn}


class TestVptrDescriptorIsIndependentEvidence:
    """A *pure* virtual has no out-of-line definition, so ``dwarf_snapshot``
    drops its declaration-only DIE from ``snapshot.functions`` while still
    counting it as a vtable child of the class — both owned-signature sets
    read empty. With ``alignas`` absorbing the new vptr the size does not
    move either, so the size backstop was reached and suppressed a class
    gaining its first vptr (Codex review; reproduced against g++ with
    ``struct alignas(8) A { virtual void f() = 0; }``).
    """

    def test_a_gained_vptr_is_kept_with_no_function_and_no_size_change(self) -> None:
        assert _vtable_transition_is_evidenced(
            NAME,
            _cls([], vptr_offset_bits=None),
            _cls([f"{NAME}::f()"], vptr_offset_bits=0),
            {},
            {},
        )

    def test_a_lost_vptr_is_kept_the_same_way(self) -> None:
        assert _vtable_transition_is_evidenced(
            NAME,
            _cls([f"{NAME}::f()"], vptr_offset_bits=0),
            _cls([], vptr_offset_bits=None),
            {},
            {},
        )

    @pytest.mark.parametrize("vptr", [None, 0])
    def test_an_unchanged_descriptor_still_falls_through_to_the_size_check(
        self, vptr: int | None
    ) -> None:
        """The descriptor agreeing on both sides is not evidence of a change,
        so it must not become a blanket "keep everything" — that would undo
        the capture-gap suppression this guard exists for."""
        assert not _vtable_transition_is_evidenced(
            NAME,
            _cls([], vptr_offset_bits=vptr),
            _cls([f"{NAME}::f()"], vptr_offset_bits=vptr),
            {},
            {},
        )


class TestPreExistingSignalsStillHold:
    def test_both_sides_captured_means_a_real_reorder(self) -> None:
        assert _vtable_transition_is_evidenced(
            NAME, _cls(["a()"]), _cls(["b()"]), {}, {}
        )

    def test_the_classs_own_virtual_functions_are_evidence(self) -> None:
        assert _vtable_transition_is_evidenced(
            NAME, _cls([]), _cls([f"{NAME}::f()"]), {}, _virtual()
        )

    def test_a_size_change_is_evidence(self) -> None:
        assert _vtable_transition_is_evidenced(
            NAME, _cls([], size_bits=64), _cls([f"{NAME}::f()"], size_bits=128), {}, {}
        )

    @pytest.mark.parametrize("side", ["old", "new"])
    def test_an_unknown_size_keeps_the_finding(self, side: str) -> None:
        """Corroborates nothing, but refutes nothing either — the suppression
        needs positive evidence that layout held still."""
        old = _cls([], size_bits=None if side == "old" else 64)
        new = _cls([f"{NAME}::f()"], size_bits=None if side == "new" else 64)
        assert _vtable_transition_is_evidenced(NAME, old, new, {}, {})

    def test_a_virtual_base_change_is_evidence(self) -> None:
        assert _vtable_transition_is_evidenced(
            NAME, _cls([]), _cls([f"{NAME}::f()"], virtual_bases=["B"]), {}, {}
        )

    def test_capture_asymmetry_alone_is_still_suppressed(self) -> None:
        """The false positive the guard was built for: identical layout, no
        owned virtuals on either side, one side's vtable list simply absent."""
        assert not _vtable_transition_is_evidenced(
            NAME, _cls([]), _cls([f"{NAME}::f()"]), {}, {}
        )
