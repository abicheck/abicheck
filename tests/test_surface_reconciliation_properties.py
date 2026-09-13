# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""The reconciliation *mechanism*, as invariants about the join itself.

Split from `test_surface_evidence_asymmetry_properties.py`, which states
what the *detectors* must report; these state what the shared join must do
regardless of any detector: whose declarations it may pair (identity, never
position or a leaf name that two namespaces share), how long its memo may
live, and that it is computed once per pair. Both halves grew out of the
same defect, but a merge primitive's contract is not a detector's, and the
combined file had outgrown the repository's test-size ceiling.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.declarations import Function, Param, Variable, Visibility
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.model.fact import Fact
from abicheck.model.snapshot import AbiSnapshot

#: The kinds this class manufactures. Both directions, deliberately: the
#: asymmetry is symmetric, so a declaration can be pushed *out* of the
#: compared surface (a removal/visibility finding) or pulled *into* it (an
#: addition), depending on which side happens to hold the contract
#: evidence. An earlier revision of this set listed only the exit kinds,
#: which left the entry half of the same defect passing (Codex review, P2)
#: -- the fix and the invariant must cover both or the claim overstates.
_SURFACE_EXIT_KINDS = frozenset(
    {
        ChangeKind.FUNC_VISIBILITY_CHANGED,
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_REMOVED_ELF_ONLY,
        ChangeKind.INLINE_FUNCTION_REMOVED,
        ChangeKind.VAR_REMOVED,
        ChangeKind.FUNC_ADDED,
        ChangeKind.VAR_ADDED,
    }
)


def _contract(evidence: bool) -> Fact[bool] | None:
    """A producer-established (b), or nothing at all.

    ``None`` -- no producer fact -- is exactly what a dump given no public-
    header set leaves behind, and is *not* the same as a confirmed negative:
    ``in_public_contract`` then re-derives a value from the legacy
    ``visibility`` enum, which is the trap this class is about.
    """
    return Fact.present(True) if evidence else None


def _snapshot(
    names: list[str],
    *,
    evidence: bool,
    exported: frozenset[str] = frozenset(),
    variables: bool = False,
) -> AbiSnapshot:
    """One library whose declarations are hidden and (mostly) unexported."""
    decls = [
        (Variable if variables else Function)(
            name=n,
            mangled=n,
            **({"type": "int"} if variables else {"return_type": "void"}),
            visibility=Visibility.HIDDEN,
            in_public_contract_fact=_contract(evidence),
        )
        for n in names
    ]
    elf = ElfMetadata(
        soname="libgen.so.1",
        symbols=[
            ElfSymbol(name=n, visibility="default")
            for n in sorted({"_Z6anchorv", *exported})
        ],
    )
    return AbiSnapshot(
        library="libgen.so",
        version="1",
        functions=[] if variables else decls,
        variables=decls if variables else [],
        elf=elf,
        from_headers=True,
    )


_names = st.lists(
    st.from_regex(r"\A_Z[0-9]{1,2}[a-z]{1,8}v\Z", fullmatch=True),
    min_size=1,
    max_size=6,
    unique=True,
)


class TestTheAliasTierSeparatesNamespaces:
    """The second tier pairs a declaration whose *mangling* changed -- not
    two declarations that merely share a leaf name.

    Header-AST backends store only the leaf, so `A::foo` and `B::foo` were
    one key; each being unique under it, the "single peer or nothing" rule
    paired them instead of declining, and an unrelated
    `A::foo(char *)`/`B::foo(char8_t *)` pair reported a false
    `CHAR8T_MIGRATION` with a BREAKING verdict (Codex review, P1).
    """

    @staticmethod
    def _snapshot(mangled: str, ptype: str, *, evidence: bool) -> AbiSnapshot:
        return AbiSnapshot(
            library="libgen.so",
            version="1",
            functions=[
                Function(
                    name="foo",  # the leaf name a header-AST backend stores
                    mangled=mangled,
                    return_type="void",
                    params=[Param(name="p", type=ptype)],
                    visibility=Visibility.HIDDEN,
                    in_public_contract_fact=_contract(evidence),
                )
            ],
            elf=ElfMetadata(
                soname="libgen.so.1",
                symbols=[ElfSymbol(name="_Z6anchorv", visibility="default")],
            ),
            from_headers=True,
        )

    @given(evidence_on_old=st.booleans())
    @settings(deadline=None, max_examples=10)
    def test_two_namespaces_are_not_one_declaration(
        self, evidence_on_old: bool
    ) -> None:
        """`A::foo` going away while `B::foo` appears is a removal and an
        addition, never a migration of one into the other."""
        old = self._snapshot("_ZN1A3fooEPKc", "char *", evidence=evidence_on_old)
        new = self._snapshot(
            "_ZN1B3fooEPKDu", "char8_t *", evidence=not evidence_on_old
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.CHAR8T_MIGRATION not in kinds, sorted(k.value for k in kinds)

    @given(evidence_on_old=st.booleans())
    @settings(deadline=None, max_examples=10)
    def test_the_same_namespace_still_pairs_across_a_mangling_change(
        self, evidence_on_old: bool
    ) -> None:
        """The complement, and the reason the tier exists at all: one entity
        whose mangling moved is still one entity."""
        old = self._snapshot("_ZN1A3fooEPKc", "char *", evidence=evidence_on_old)
        new = self._snapshot(
            "_ZN1A3fooEPKDu", "char8_t *", evidence=not evidence_on_old
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.CHAR8T_MIGRATION in kinds, sorted(k.value for k in kinds)


class TestCrossKindReconciliationIsOrderIndependent:
    """The cross-kind join keys on identity, so its result cannot depend on
    the order declarations happen to appear in.

    It keyed on the bare declared name, and castxml does not
    namespace-qualify a variable's name -- so two `foo`s in different
    namespaces collided on one key and the first-seen rule resolved the
    collision by *list order* (Codex review, P2). `AGENTS.md` treats
    order-dependence in a shared merge primitive as a defect in itself,
    whatever any one caller currently exercises, which is why this asserts
    the invariant rather than the reported scenario.
    """

    @staticmethod
    def _old(manglings: list[str]) -> AbiSnapshot:
        """An evidence-poor side whose declarations share a leaf name and
        differ only in namespace -- the shape castxml produces."""
        return AbiSnapshot(
            library="libgen.so",
            version="1",
            functions=[
                Function(
                    name="foo",
                    mangled=mangled,
                    return_type="void",
                    visibility=Visibility.HIDDEN,
                    in_public_contract_fact=None,
                )
                for mangled in manglings
            ],
            elf=ElfMetadata(
                soname="libgen.so.1",
                symbols=[ElfSymbol(name="_Z6anchorv", visibility="default")],
            ),
            from_headers=True,
        )

    @staticmethod
    def _new() -> AbiSnapshot:
        """The evidence-bearing side: `ns1::foo` is now a variable. Only the
        variable, so the finding turns entirely on *which* of OLD's
        same-leaf-name functions reconciliation pairs it with."""
        return AbiSnapshot(
            library="libgen.so",
            version="1",
            variables=[
                Variable(
                    name="foo",
                    mangled="_ZN3ns13fooE",
                    type="S",
                    visibility=Visibility.HIDDEN,
                    in_public_contract_fact=Fact.present(True),
                )
            ],
            elf=ElfMetadata(
                soname="libgen.so.1",
                symbols=[ElfSymbol(name="_Z6anchorv", visibility="default")],
            ),
            from_headers=True,
        )

    @given(
        manglings=st.permutations(["_ZN3ns13fooEv", "_ZN3ns23fooEv", "_ZN3ns33fooEv"])
    )
    @settings(deadline=None, max_examples=12)
    def test_every_declaration_order_reports_the_real_transition(
        self, manglings: list[str]
    ) -> None:
        """`ns1::foo` became a variable, so every permutation must report it.

        The oracle is the transition the fixture was *built* to contain, not
        a reference run of the same code -- comparing permutations against
        each other would pass just as well if every one of them were wrong
        in the same way. Under the bare-name key this reported the finding
        when `ns1` happened to come first and nothing when it did not.
        """
        from abicheck.diff_templates import detect_cpo_kind_changed

        assert [
            c.kind for c in detect_cpo_kind_changed(self._old(manglings), self._new())
        ] == [ChangeKind.CPO_KIND_CHANGED]

    def test_a_namespace_qualified_peer_is_not_resolved_by_position(self) -> None:
        """The identity itself: two same-leaf-name declarations resolve to
        their own namespace's peer or to nothing -- never to whichever came
        first."""
        from abicheck.compare.template_surface import cpo_identity
        from abicheck.diff_templates import _cpo_function_stem

        def identity(decl: Function | Variable) -> str:
            return cpo_identity(decl, function_stem=_cpo_function_stem)

        first = Function(name="foo", mangled="_ZN3ns13fooEv", return_type="void")
        second = Function(name="foo", mangled="_ZN3ns23fooEv", return_type="void")
        assert identity(first) != identity(second)
        assert identity(first) == "ns1::foo"
        # And it agrees across kinds: the function and the variable it became
        # are one identity, which is what lets the cross-kind join pair them
        # at all -- the raw qualified names differ (`ns1::foo()` vs
        # `ns1::foo`).
        assert identity(Variable(name="foo", mangled="_ZN3ns13fooE", type="S")) == (
            identity(first)
        )


class TestReconciliationDoesNotOutliveTheComparison:
    """The memo must not keep either snapshot's data alive after `compare()`
    returns.

    It hangs on OLD and holds NEW, so a caller keeping a baseline alive kept
    the last candidate -- and both reconciled declaration maps -- alive with
    it. For the typed API, where a deep snapshot can be hundreds of MiB,
    that is a leak, and clearing at the *start* of the next comparison does
    not bound it: there may be no next comparison (Codex review, P2).
    """

    @given(names=_names)
    @settings(deadline=None, max_examples=15)
    def test_no_slot_survives_a_completed_comparison(self, names: list[str]) -> None:
        from abicheck.compare.surface_reconcile import (
            RECONCILED_FUNCTIONS,
            RECONCILED_VARIABLES,
        )

        old = _snapshot(names, evidence=True)
        new = _snapshot(names, evidence=False)
        compare(old, new)
        assert RECONCILED_FUNCTIONS not in old.__dict__
        assert RECONCILED_VARIABLES not in old.__dict__

    def test_the_slot_is_released_even_when_the_comparison_raises(self) -> None:
        """A `finally`, not a trailing statement: an exception must not leave
        the memo -- and the candidate's declarations -- attached to a
        baseline the caller goes on holding."""
        from abicheck.compare.surface_reconcile import (
            RECONCILED_FUNCTIONS,
            releases_reconciliation,
        )

        old = _snapshot(["_Z3foov"], evidence=True)

        @releases_reconciliation
        def _boom(old_snap: AbiSnapshot, new_snap: AbiSnapshot) -> None:
            old_snap.__dict__[RECONCILED_FUNCTIONS] = (new_snap, ({}, {}))
            raise RuntimeError("detector blew up")

        with pytest.raises(RuntimeError):
            _boom(old, _snapshot(["_Z3foov"], evidence=False))
        assert RECONCILED_FUNCTIONS not in old.__dict__

    def test_the_candidate_is_collectible_once_the_comparison_is_done(self) -> None:
        """The property the slot check stands for, asserted directly: with
        the caller still holding OLD, nothing reachable from it keeps NEW
        alive."""
        import gc
        import weakref

        old = _snapshot(["_Z3foov"], evidence=True)
        new = _snapshot(["_Z3foov"], evidence=False)
        compare(old, new)
        ref = weakref.ref(new)
        del new
        gc.collect()
        assert ref() is None


class TestReconciliationIsScopedToOneComparison:
    """The per-pair memo may not outlive the comparison that built it.

    It matches on object identity, which is sound only while the snapshots
    are read-only. Nothing in the typed API requires a caller to rebuild its
    snapshots between comparisons, so the same two objects can be compared,
    mutated, and compared again -- and the second call was served the first
    call's surfaces, reporting nothing where an equivalent fresh pair reports
    a finding (Codex review, P2).

    The oracle is that fresh pair: same inputs, built from scratch.
    """

    @given(names=_names, variables=st.booleans())
    @settings(deadline=None, max_examples=25)
    def test_mutating_a_snapshot_between_comparisons_is_not_served_a_stale_surface(
        self, names: list[str], variables: bool
    ) -> None:
        def pair() -> tuple[AbiSnapshot, AbiSnapshot]:
            return (
                _snapshot(names, evidence=True, variables=variables),
                _snapshot(names, evidence=False, variables=variables),
            )

        old, new = pair()
        assert not [
            c for c in compare(old, new).changes if c.kind in _SURFACE_EXIT_KINDS
        ]
        # The producer now *observed* these out of the public contract: a real
        # finding, not an evidence gap.
        for decl in new.variables if variables else new.functions:
            decl.in_public_contract_fact = Fact.present(False)
        reused = {
            c.symbol for c in compare(old, new).changes if c.kind in _SURFACE_EXIT_KINDS
        }
        fresh_old, fresh_new = pair()
        for decl in fresh_new.variables if variables else fresh_new.functions:
            decl.in_public_contract_fact = Fact.present(False)
        expected = {
            c.symbol
            for c in compare(fresh_old, fresh_new).changes
            if c.kind in _SURFACE_EXIT_KINDS
        }
        assert expected == set(names), expected
        assert reused == expected


class TestReconciliationIsComputedOncePerPair:
    """The reconciled surfaces are shared, not recomputed per detector.

    Every per-pair detector asks for the same reconciled pair, and building
    one resolves a canonical identity for every declaration in both FULL
    maps. Recomputing it per detector made a comparison 25-70% slower across
    the scaling benchmarks -- caught by the PR-vs-base performance gate, not
    by any test, which is why this one exists: the memo is load-bearing, and
    a refactor that drops it reintroduces the regression silently.

    Asserted structurally (how many times the work happens) rather than by
    wall-clock, so it cannot flake on a noisy runner.
    """

    def test_one_reconciliation_per_declaration_kind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck import diff_symbols
        from abicheck.compare import surface_reconcile

        calls: list[int] = []
        real = surface_reconcile.reconcile_surfaces

        def _counting(*args: object, **kwargs: object) -> object:
            calls.append(1)
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(diff_symbols, "reconcile_surfaces", _counting)

        names = ["_Z3foov", "_Z3barv"]
        compare(_snapshot(names, evidence=True), _snapshot(names, evidence=False))

        # One for functions, one for variables -- not one per detector.
        assert len(calls) == 2, (
            f"reconciliation ran {len(calls)} times; the per-pair memo is gone"
        )

    def test_the_memo_does_not_leak_across_different_pairs(self) -> None:
        """A cached entry is matched on the NEW snapshot's identity, so a
        second comparison of the same OLD against a *different* NEW must not
        be served the first one's surfaces."""
        names = ["_Z3foov"]
        old = _snapshot(names, evidence=True)
        unchanged = _snapshot(names, evidence=False)
        without = _snapshot([], evidence=False)

        assert not [
            c for c in compare(old, unchanged).changes if c.kind in _SURFACE_EXIT_KINDS
        ]
        # Same OLD object, a different NEW: the declaration really is gone.
        assert [
            c for c in compare(old, without).changes if c.kind in _SURFACE_EXIT_KINDS
        ]
