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

from abicheck.diff_types_vtable import (
    _owned_virtual_signatures,
    _vtable_transition_is_evidenced,
)
from abicheck.model import Fact, Function, RecordType, Visibility

NAME = "Abstract"


_UNSET = object()


def _cls(
    vtable: list[str],
    *,
    size_bits: int | None = 64,
    vptr_offset_bits: object = _UNSET,
    virtual_bases: list[str] | None = None,
) -> RecordType:
    """A record shaped the way a real dump produces one.

    ``vptr_offset_bits`` defaults to a producer's own derivation rather
    than to ``None``: ``dumper_castxml`` still assigns it as
    ``0 if vtable else None`` (``dwarf_snapshot`` no longer does, since G31
    Phase C -- see ``TestVptrIsNotEvidence``'s own docstring). Leaving it
    ``None`` on both sides built a record no backend can emit, and that is
    precisely how a guard that had become inert on every real snapshot
    still passed its own tests.
    """
    return RecordType(
        name=NAME,
        kind="class",
        size_bits=size_bits,
        vtable=vtable,
        vptr_offset_bits=(0 if vtable else None)
        if vptr_offset_bits is _UNSET
        else vptr_offset_bits,  # type: ignore[arg-type]
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


class TestVptrIsNotEvidence:
    """``vptr_offset_bits`` must never gate this guard — it is circular.

    ``dumper_castxml.py`` assigns it ``0 if vtable else None``, so on a real
    CastXML snapshot the descriptor's None↔set transition *is* the vtable
    transition being guarded, not a second witness of it. A revision that
    read it here was true by construction for every input reaching that
    branch, which made the whole guard inert and let the capture-gap false
    positive back through (Codex review).

    ``dwarf_snapshot.py`` is deliberately NOT pinned here (it was, until G31
    Phase C): it no longer derives ``vptr_offset_bits`` purely from the
    vtable list -- it now reads a real ``_vptr.<Class>``/base-chain offset
    from DWARF in the common case, only falling back to the same
    ``0 if vtable`` heuristic for the residual set DWARF can't resolve. This
    guard still doesn't consult the field on that backend either (see
    ``diff_types._vtable_transition_is_evidenced``'s own updated comment) --
    declining to use an available signal is always safe, so nothing here
    needed to change behaviorally. But the field is no longer *provably*
    circular on DWARF the way it still is on castxml, so pinning it to the
    same premise would be testing something no longer true. Whether it's
    now trustworthy enough to become a genuine independent witness for the
    DWARF-derived case is its own careful design + FP-verification effort
    (see this guard's own docstring caution against "a cleverer reading of
    the fields already here"), not something this pin test should assume
    either way.
    """

    def test_the_producer_derivation_this_rests_on(self) -> None:
        """Pin the premise, not just the conclusion.

        The argument above is only sound while castxml derives the field
        from the vtable list. If it ever computes a real vptr too, this
        fails and the reasoning must be revisited before the field is
        trusted as evidence again.

        The derivation itself moved from ``abicheck.dumper_castxml`` to
        ``abicheck.extract.headers.castxml.records`` (ADR-061 Phase 5's
        record-entity split) — ``dumper_castxml.py`` now only delegates to
        it, so the source text this test pins must be read from its real
        home rather than the thin wrapper.
        """
        import importlib
        from pathlib import Path

        src = Path(
            importlib.import_module("abicheck.extract.headers.castxml.records").__file__
            or ""
        ).read_text(encoding="utf-8")
        assert "vptr_offset_bits = 0 if vtable else None" in src, (
            "abicheck.extract.headers.castxml.records no longer derives "
            "vptr_offset_bits from the vtable list"
        )

    def test_capture_gap_is_suppressed_on_producer_shaped_records(self) -> None:
        """The regression: identical layout, no owned virtuals either side,
        one side's vtable simply absent — with the vptr set the way a real
        dump sets it."""
        assert not _vtable_transition_is_evidenced(
            NAME, _cls([]), _cls([f"{NAME}::f()"]), {}, {}
        )

    def test_the_reverse_direction_too(self) -> None:
        assert not _vtable_transition_is_evidenced(
            NAME, _cls([f"{NAME}::f()"]), _cls([]), {}, {}
        )

    @pytest.mark.parametrize(
        "old_vptr,new_vptr", [(None, 0), (0, None), (0, 0), (None, None)]
    )
    def test_no_descriptor_pairing_changes_the_answer(
        self, old_vptr: int | None, new_vptr: int | None
    ) -> None:
        """Including the pairing an ``ABICHECK_CLANG_LAYOUT_TOOL`` backfill
        could produce: the field is simply not read."""
        assert not _vtable_transition_is_evidenced(
            NAME,
            _cls([], vptr_offset_bits=old_vptr),
            _cls([f"{NAME}::f()"], vptr_offset_bits=new_vptr),
            {},
            {},
        )


class TestTheAcceptedFalseNegativeDoesNotHideTheBreak:
    """The guard's docstring leans on a *neighbouring* detector, so pin it.

    Suppressing ``TYPE_VTABLE_CHANGED`` for an over-aligned class gaining its
    first pure virtual is only acceptable because ``diff_layout``'s
    independent ``_check_vptr_introduced`` reports the same transition and
    the verdict stays BREAKING. If that ever stops holding, this guard is
    hiding a real ABI break and must be revisited — a unit test on the
    predicate alone cannot see that.
    """

    @staticmethod
    def _snap(vtable: list[str]) -> object:
        from abicheck.model import AbiSnapshot

        return AbiSnapshot(
            library="libx.so",
            version="1",
            types=[
                RecordType(
                    name="A",
                    kind="class",
                    size_bits=64,  # alignas absorbs the new vptr
                    vtable=list(vtable),
                    vptr_offset_bits=0 if vtable else None,
                )
            ],
        )

    def test_the_verdict_is_still_breaking(self) -> None:
        from abicheck.checker import Verdict, compare

        result = compare(self._snap([]), self._snap(["A::f()"]))
        kinds = {c.kind.value for c in result.changes}
        assert "type_vtable_changed" not in kinds, "the guard should suppress its own"
        assert "vptr_introduced" in kinds, "the break must still be reported"
        assert result.verdict is Verdict.BREAKING


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


class TestExplicitFactStatusWouldNotSafelyGateThisGuard:
    """ADR-063 Track 4, 5B final closure: an early decline keyed off
    ``vtable_fact.status`` (``NOT_COLLECTED``/``FAILED``) was investigated and
    rejected for this guard specifically — these tests are the executable
    half of that finding. Full reasoning: ``diff_types_vtable.py``'s own
    module docstring ("Track 4, 5B final closure" section), the canonical
    writeup — not repeated here beyond the summary needed to read these
    tests.

    ``_vtable_transition_is_evidenced`` already treats a ``NOT_COLLECTED``/
    ``FAILED`` ``vtable_fact`` identically to a confirmed-empty one via
    ``resolved_fact_value``'s default collapse, so a direct status read
    would be redundant for the "both sides captured something" branch.
    What it would NOT be redundant for is short-circuiting the two
    independent fallback evidence streams below (the class's own virtual
    functions from ``snapshot.functions``, and ``size_bits``/
    ``virtual_bases_fact``) before they run — both read facts unrelated to
    ``vtable_fact``'s own status, so gating on it would silence real,
    independently-evidenced findings.
    """

    _BAD_FACTS = [Fact.not_collected(), Fact.failed("simulated producer error")]
    _BAD_FACT_IDS = ["not_collected", "failed"]

    @pytest.mark.parametrize("bad_fact", _BAD_FACTS, ids=_BAD_FACT_IDS)
    @pytest.mark.parametrize("bad_side", ["old", "new"])
    def test_own_functions_fallback_survives_an_uncollected_vtable_fact(
        self, bad_side: str, bad_fact: Fact[list[str]]
    ) -> None:
        """The exact mixed shape a real capture gap produces: one side
        genuinely populated, the other carrying an explicit
        ``NOT_COLLECTED``/``FAILED`` status rather than a confirmed-empty
        ``PRESENT([])`` -- not the same status on both sides, which would
        let a regression that only guards this specific mixed shape slip
        through undetected (Codex review). The class's own retained
        virtual-function stream must still settle it regardless of which
        side carries the bad status."""
        populated = RecordType(
            name=NAME, kind="class", size_bits=64, vtable=[f"{NAME}::f()"]
        )
        uncollected = RecordType(
            name=NAME, kind="class", size_bits=64, vtable_fact=bad_fact
        )
        assert uncollected.vtable_fact is not None
        assert uncollected.vtable_fact.status is bad_fact.status
        old, old_funcs = (
            (uncollected, {}) if bad_side == "old" else (populated, _virtual())
        )
        new, new_funcs = (
            (populated, _virtual()) if bad_side == "old" else (uncollected, {})
        )
        assert _vtable_transition_is_evidenced(NAME, old, new, old_funcs, new_funcs)

    @pytest.mark.parametrize("bad_fact", _BAD_FACTS, ids=_BAD_FACT_IDS)
    @pytest.mark.parametrize("bad_side", ["old", "new"])
    def test_size_delta_fallback_survives_an_uncollected_vtable_fact(
        self, bad_side: str, bad_fact: Fact[list[str]]
    ) -> None:
        """Same mixed populated/uncollected shape as above, but with no
        owned-function evidence on either side (both empty), so only the
        ``size_bits`` delta can settle it -- and must, regardless of which
        side's ``vtable_fact`` carries the bad status."""
        populated = RecordType(
            name=NAME, kind="class", size_bits=64, vtable=[f"{NAME}::f()"]
        )
        uncollected = RecordType(
            name=NAME, kind="class", size_bits=128, vtable_fact=bad_fact
        )
        old = uncollected if bad_side == "old" else populated
        new = populated if bad_side == "old" else uncollected
        assert _vtable_transition_is_evidenced(NAME, old, new, {}, {})

    def test_the_both_sides_populated_branch_is_already_unreachable_when_uncollected(
        self,
    ) -> None:
        """The one branch a direct status check *could* have replaced is
        already unreachable via ``resolved_fact_value``'s existing collapse
        -- confirming the status read really would be redundant there, not
        just declined for style. A populated old side plus an uncollected
        new side falls through to the fallback streams exactly as a
        confirmed-empty new side would, and (with no other evidence
        differing) is correctly suppressed either way."""
        old = RecordType(
            name=NAME,
            kind="class",
            size_bits=64,
            vtable=["A::f()"],
        )
        new = RecordType(
            name=NAME,
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
        )
        assert not _vtable_transition_is_evidenced(NAME, old, new, {}, {})


class TestOwnedVirtualSignaturesBackCompatWrapper:
    """``diff_types_vtable._owned_virtual_signatures`` is a thin back-compat
    wrapper over ``compare.vtable_evidence``'s own private helper of the
    same name (ADR-063 Track 2, 5B closure) -- kept purely so
    ``abicheck.diff_types._owned_virtual_signatures`` (a re-export by value)
    keeps resolving for any existing call site. Not called from within this
    module any more, so nothing else exercises it; pinned directly here."""

    def test_delegates_to_the_shared_implementation(self) -> None:
        assert _owned_virtual_signatures(NAME, _virtual()) == {f"_ZN8{NAME}1fEv"}

    def test_an_unrelated_owner_is_excluded(self) -> None:
        assert _owned_virtual_signatures("Unrelated", _virtual()) == set()
