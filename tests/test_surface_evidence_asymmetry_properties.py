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

"""The compared public surface may not manufacture a break out of an
*evidence* asymmetry between the two sides.

The bug class (`analysis.surface_evidence_asymmetry` in
`tests/regressions/manifest.py`): each side's public surface is built from
that side's own facts, and one of them -- `in_public_contract` (b) -- is
only ever *established* when the run gave that side's producer a public-
header set. Two snapshots of an unchanged library, captured with and
without one, therefore disagree about which declarations are in the
surface: every promised-but-unexported declaration (a public inline member,
one a version script keeps out of `.dynsym`) is in one surface and not the
other. The removal path read that as a transition and reported it --
`func_visibility_changed` with `old_value == new_value == "hidden"`, and
`var_removed` for a variable still declared on both sides.

These are invariants over *generated* declaration populations, not the one
reported input: the oracle is "compare a snapshot against itself, with only
the contract-evidence axis varied" -- derived from the definition of the
defect, not from `export_transition`'s own predicate.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.diff_templates import detect_internal_template_leaks
from abicheck.diff_types_abicc_parity import _diff_var_values
from abicheck.extract.surface_fact_producers import header_ast_surface_facts
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


class TestSurfaceEvidenceAsymmetry:
    """No finding may rest on one side having contract evidence the other
    was never given."""

    @given(names=_names, variables=st.booleans())
    @settings(deadline=None, max_examples=60)
    def test_self_comparison_is_clean_across_the_evidence_axis(
        self, names: list[str], variables: bool
    ) -> None:
        """The library is byte-identical on both sides; only whether the
        run established (b) differs. Every direction must be clean."""
        for old_ev, new_ev in (
            (True, False),
            (False, True),
            (True, True),
            (False, False),
        ):
            result = compare(
                _snapshot(names, evidence=old_ev, variables=variables),
                _snapshot(names, evidence=new_ev, variables=variables),
            )
            offending = [c for c in result.changes if c.kind in _SURFACE_EXIT_KINDS]
            assert offending == [], (
                f"evidence axis old={old_ev} new={new_ev} manufactured "
                f"{[(c.kind, c.symbol) for c in offending]}"
            )

    @given(names=_names, variables=st.booleans())
    @settings(deadline=None, max_examples=60)
    def test_a_real_removal_still_reports_under_the_same_asymmetry(
        self, names: list[str], variables: bool
    ) -> None:
        """The guard narrows conclusions, it does not hide removals: a
        declaration genuinely *gone* from the new side is still reported,
        under the identical evidence asymmetry the previous test pins.

        This is the vacuity guard on the invariant above -- an
        implementation that simply dropped every removal would pass that
        test and fail this one.
        """
        removed, kept = names[0], names[1:]
        result = compare(
            _snapshot(names, evidence=True, variables=variables),
            _snapshot(kept, evidence=False, variables=variables),
        )
        reported = {c.symbol for c in result.changes if c.kind in _SURFACE_EXIT_KINDS}
        assert removed in reported

    @given(names=_names)
    @settings(deadline=None, max_examples=60)
    def test_a_real_export_loss_still_reports_under_the_same_asymmetry(
        self, names: list[str]
    ) -> None:
        """The other half of the vacuity guard, on the axis the guard
        reads: a declaration the OLD side *confirmed* exported and the NEW
        side does not export is an observed transition, and stays one even
        though the NEW side lacks contract evidence."""
        old = _snapshot(names, evidence=True, exported=frozenset(names))
        new = _snapshot(names, evidence=False)
        reported = {
            c.symbol for c in compare(old, new).changes if c.kind in _SURFACE_EXIT_KINDS
        }
        assert reported == set(names)

    @given(names=_names)
    @settings(deadline=None, max_examples=40)
    def test_a_real_signature_change_on_a_surviving_declaration_reports(
        self, names: list[str]
    ) -> None:
        """The surviving declaration is *matched*, not discarded.

        Narrowing the conclusion about one question ("is this still in the
        promised surface?") must not silence a different one the evidence
        does answer ("did its signature change?"). The declaration is on
        both sides, so a real return-type change on it is still comparable
        -- and is reported by neither the pre-fix behaviour (a manufactured
        visibility finding, and no signature diff) nor a bare "emit
        nothing". Codex review, P1.
        """
        old = _snapshot(names, evidence=True)
        new = _snapshot(names, evidence=False)
        for fn in new.functions:
            fn.return_type = "int"  # was "void"
        reported = {
            c.symbol
            for c in compare(old, new).changes
            if c.kind is ChangeKind.FUNC_RETURN_CHANGED
        }
        assert reported == set(names)

    @given(names=_names)
    @settings(deadline=None, max_examples=40)
    def test_a_real_variable_type_change_on_a_survivor_reports(
        self, names: list[str]
    ) -> None:
        """:meth:`test_a_real_signature_change_on_a_surviving_declaration_reports`
        for data symbols, whose surviving-pair comparison is reached through
        a separate call site and so needs its own guard."""
        old = _snapshot(names, evidence=True, variables=True)
        new = _snapshot(names, evidence=False, variables=True)
        for var in new.variables:
            var.type = "long"  # was "int"
        reported = {
            c.symbol
            for c in compare(old, new).changes
            if c.kind is ChangeKind.VAR_TYPE_CHANGED
        }
        assert reported == set(names)

    @given(names=_names)
    @settings(deadline=None, max_examples=30)
    def test_every_matched_pair_detector_sees_the_reconciled_pair(
        self, names: list[str]
    ) -> None:
        """Not just the signature comparison (Codex review, P1).

        Each per-pair detector rebuilds the filtered surfaces itself, so
        reconciling at one disposition site left all the others blind: a
        parameter default moving from 1 to 2 on such a declaration -- the
        reviewer's own example -- produced a completely clean result. The
        surfaces are now reconciled before any detector runs, so this
        asserts through a detector that never knew about the asymmetry.
        """
        old = _snapshot(names, evidence=True)
        new = _snapshot(names, evidence=False)
        for snap, default in ((old, "1"), (new, "2")):
            for fn in snap.functions:
                fn.params = [Param(name="n", type="int", default=default)]
        reported = {
            c.symbol
            for c in compare(old, new).changes
            if c.kind is ChangeKind.PARAM_DEFAULT_VALUE_CHANGED
        }
        assert reported == set(names)

    @pytest.mark.parametrize("evidence_on_old", [True, False])
    def test_an_extern_c_linkage_change_is_still_paired(
        self, evidence_on_old: bool
    ) -> None:
        """The pair survives a key change, not just an evidence gap.

        An ``extern "C"`` declaration is spelled by its bare name on one side
        and by a C++ mangling on the other, so an exact-key lookup misses the
        peer: the surviving pair read as a removal plus an addition instead
        of the linkage change it is (Codex review, P2). Asserted in both
        directions, since either side can be the one lacking evidence.
        """
        old = _snapshot(["c_func"], evidence=evidence_on_old)
        new = _snapshot(["_Z6c_funcv"], evidence=not evidence_on_old)
        old.functions[0].name = new.functions[0].name = "c_func"
        old.functions[0].is_extern_c = True
        new.functions[0].is_extern_c = False
        kinds = {c.kind for c in compare(old, new).changes}
        assert not (kinds & _SURFACE_EXIT_KINDS), (
            f"the pair was split into a removal/addition: {kinds}"
        )
        assert ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED in kinds

    def test_a_sibling_modules_detector_sees_the_reconciled_pair(self) -> None:
        """Not just this module's detectors (Codex review, P1).

        `diff_types`'s overload and method-qualifier detectors rebuild the
        old/new surfaces themselves, so routing only `diff_symbols` left them
        on the unreconciled maps: an unchanged exported `foo()` beside a
        promised-but-unexported `foo(int)` produced a false `OVERLOAD_ADDED`
        when only one side carried the evidence. Asserted through
        `compare()`, so it fails if any consumer is left behind.
        """

        # Both sides declare `foo()` and `foo(int)` and export only `foo()`,
        # with the export recorded the way a real producer records it. The
        # contract evidence is on NEW, so OLD's surface holds just the
        # exported one -- one declaration under the group key against two on
        # NEW, exactly the "gained an overload" shape the detector fires on.
        def _side(evidence: bool) -> AbiSnapshot:
            exported = Function(
                name="foo",
                mangled="_Z3foov",
                return_type="void",
                params=[],
                **header_ast_surface_facts(exported=True, producer="castxml"),
            )
            promised = Function(
                name="foo",
                mangled="_Z3fooi",
                return_type="void",
                params=[Param(name="n", type="int")],
                visibility=Visibility.HIDDEN,
                **header_ast_surface_facts(exported=False, producer="castxml"),
            )
            if evidence:
                promised.in_public_contract_fact = Fact.present(
                    True, producer="public_header"
                )
            return AbiSnapshot(
                library="libgen.so",
                version="1",
                functions=[exported, promised],
                elf=ElfMetadata(
                    soname="libgen.so.1",
                    symbols=[
                        ElfSymbol(name=n, visibility="default")
                        for n in ("_Z6anchorv", "_Z3foov")
                    ],
                ),
                from_headers=True,
            )

        kinds = [c.kind for c in compare(_side(False), _side(True)).changes]
        assert ChangeKind.OVERLOAD_ADDED not in kinds, kinds

    @pytest.mark.parametrize("evidence_on_old", [True, False])
    def test_an_overload_set_never_collapses_onto_one_alias_peer(
        self, evidence_on_old: bool
    ) -> None:
        """Admission is one-to-one, or it does not happen.

        The alias tier answers "the single peer carrying this name", which is
        unique per *lookup* and says nothing about the reverse direction: an
        overload set on the evidence-bearing side against one unexported
        `extern "C"` declaration of that name resolves every overload key to
        the same object. Admitting each would place one declaration under
        every overload key, so the join compares every overload against it --
        hiding genuine removals and inventing signature changes (Codex
        review, P2).
        """
        overloads = _snapshot(["_Z3fooi", "_Z3food"], evidence=evidence_on_old)
        for fn in overloads.functions:
            fn.name = "foo"
        single = _snapshot(["foo"], evidence=not evidence_on_old)
        single.functions[0].name = "foo"
        single.functions[0].is_extern_c = True
        old, new = (overloads, single) if evidence_on_old else (single, overloads)
        changes = compare(old, new).changes
        # No declaration may be compared against a peer it reached only
        # through a collapsed alias: the overloads have no counterpart, so a
        # removal/addition is the honest answer and a signature comparison
        # against the single `extern "C"` declaration is not. This holds in
        # both directions -- it is what the one-to-one rule buys.
        assert not [c for c in changes if c.kind is ChangeKind.FUNC_RETURN_CHANGED]
        reported = {c.symbol for c in changes if c.kind in _SURFACE_EXIT_KINDS}
        if evidence_on_old:
            # The overloads carry the contract evidence, so they are in their
            # own side's surface and their disappearance is reported.
            assert {"_Z3fooi", "_Z3food"} <= reported, reported
        else:
            # The overloads carry no evidence and are hidden, so they are not
            # in their side's surface at all and nothing reports them -- the
            # pre-existing narrowing, unchanged by this PR and deliberately
            # not widened by it.
            assert reported == {"foo"}, reported

    @given(names=_names, variables=st.booleans())
    @settings(deadline=None, max_examples=40)
    def test_an_observed_negative_on_the_new_side_still_reports(
        self, names: list[str], variables: bool
    ) -> None:
        """A NEW side whose producer *observed* the declaration out of the
        public contract (moved to a private header) is a real finding --
        only an unestablished (b) is an evidence gap."""
        old = _snapshot(names, evidence=True, variables=variables)
        new = _snapshot(names, evidence=True, variables=variables)
        for decl in new.variables if variables else new.functions:
            decl.in_public_contract_fact = Fact.present(False)
        reported = {
            c.symbol for c in compare(old, new).changes if c.kind in _SURFACE_EXIT_KINDS
        }
        assert reported == set(names)


class TestListShapedSurfaceDetectors:
    """The same invariant for the two detectors that select a *list* off
    ``AbiSnapshot.functions`` with their own predicate, plus the variable
    value detector that joins its own filtered maps.

    These reach the surface by a different route than the mangled-keyed
    ``diff_symbols`` join, so routing that join alone left them reporting
    the defect unchanged (Codex review, P1): an internal-template
    instantiation present on both sides read as removed, and a real value
    change on a surviving variable reported nothing at all. The oracle is
    the same one this module uses throughout -- a comparison of a library
    against itself with only the evidence axis varied has no findings, and
    a real change made on top of it still has its own.
    """

    @staticmethod
    def _template_snapshot(stems: list[str], *, evidence: bool) -> AbiSnapshot:
        """Internal-namespace template instantiations, hidden and unexported.

        Declared names rather than manglings: the detector's stem grouping
        reads the demangled spelling, and an unmangled name is what a
        header-only capture of an internal instantiation actually carries.
        """
        return AbiSnapshot(
            library="libgen.so",
            version="1",
            functions=[
                Function(
                    name=stem,
                    mangled=stem,
                    return_type="void",
                    visibility=Visibility.HIDDEN,
                    in_public_contract_fact=_contract(evidence),
                )
                for stem in stems
            ],
            elf=ElfMetadata(
                soname="libgen.so.1",
                symbols=[ElfSymbol(name="_Z6anchorv", visibility="default")],
            ),
            from_headers=True,
        )

    @given(
        stems=st.lists(
            st.from_regex(r"\Adetail::[a-z]{1,6}<(int|double|char)>\Z", fullmatch=True),
            min_size=1,
            max_size=5,
            unique=True,
        ),
        evidence_on_old=st.booleans(),
    )
    @settings(deadline=None, max_examples=40)
    def test_internal_template_leak_is_not_manufactured_by_an_evidence_gap(
        self, stems: list[str], evidence_on_old: bool
    ) -> None:
        old = self._template_snapshot(stems, evidence=evidence_on_old)
        new = self._template_snapshot(stems, evidence=not evidence_on_old)
        assert detect_internal_template_leaks(old, new) == []

    @given(
        stems=st.lists(
            st.from_regex(r"\Adetail::[a-z]{1,6}<(int|double|char)>\Z", fullmatch=True),
            min_size=2,
            max_size=5,
            unique=True,
        ),
        evidence_on_old=st.booleans(),
    )
    @settings(deadline=None, max_examples=40)
    def test_a_real_instantiation_removal_still_reports_under_an_evidence_gap(
        self, stems: list[str], evidence_on_old: bool
    ) -> None:
        """The complement, so the fix cannot be "report nothing here": one
        instantiation genuinely gone is still a leak finding when OLD -- the
        side that loses it -- is the side holding the contract evidence.

        When OLD is instead the evidence-less side, the removed
        instantiation is in neither compared surface (OLD's own selector
        never admitted it, and NEW no longer declares it, so there is
        nothing to reconcile it against) and nothing reports it. That is the
        pre-existing narrowing this class does not widen -- the same
        asymmetry the overload case above records -- and asserting it here
        keeps the claim honest in both directions rather than only in the
        one that happens to fire.
        """
        old = self._template_snapshot(stems, evidence=evidence_on_old)
        new = self._template_snapshot(stems[:-1], evidence=not evidence_on_old)
        reported = [c.kind for c in detect_internal_template_leaks(old, new)]
        assert reported == (
            [ChangeKind.INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API]
            if evidence_on_old
            else []
        )

    @given(
        names=_names,
        evidence_on_old=st.booleans(),
        old_value=st.integers(min_value=-8, max_value=8),
    )
    @settings(deadline=None, max_examples=40)
    def test_a_real_variable_value_change_survives_an_evidence_gap(
        self, names: list[str], evidence_on_old: bool, old_value: int
    ) -> None:
        """Every promised-but-unexported variable whose captured value moved
        is reported, even though only one side's run established (b).

        The oracle is the population the test *built* -- every name, with a
        value chosen to differ -- not the detector's own filtering.
        """
        old = _snapshot(names, evidence=evidence_on_old, variables=True)
        new = _snapshot(names, evidence=not evidence_on_old, variables=True)
        for i, (v_old, v_new) in enumerate(zip(old.variables, new.variables)):
            v_old.value = old_value + i
            v_new.value = old_value + i + 1
        reported = {
            c.symbol
            for c in _diff_var_values(old, new)
            if c.kind is ChangeKind.VAR_VALUE_CHANGED
        }
        assert reported == set(names)

    @given(names=_names, evidence_on_old=st.booleans())
    @settings(deadline=None, max_examples=40)
    def test_an_unchanged_variable_value_reports_nothing_under_an_evidence_gap(
        self, names: list[str], evidence_on_old: bool
    ) -> None:
        old = _snapshot(names, evidence=evidence_on_old, variables=True)
        new = _snapshot(names, evidence=not evidence_on_old, variables=True)
        for v_old, v_new in zip(old.variables, new.variables):
            v_old.value = v_new.value = 7
        assert _diff_var_values(old, new) == []


class TestTypeSpellingAndIntegerModelDetectors:
    """The last two detectors that key their own surface off
    `in_public_surface` see the reconciled pair too.

    They select the same population the template detectors do and then key it
    themselves, so the asymmetry cost them the same pair -- and with it a
    real finding rather than a false one: a `char *` -> `char8_t *` return
    change reported `CHAR8T_MIGRATION` only when both sides happened to carry
    contract evidence (Codex review, P2).
    """

    @staticmethod
    def _param_snapshot(
        decls: dict[str, tuple[str, str]], *, evidence: bool
    ) -> AbiSnapshot:
        """Functions differing in a *parameter* type, which moves the mangled
        name with it -- `_Z1fPKc` -> `_Z1fPKDu` for `char *` -> `char8_t *`.

        The realistic shape, and the one an exact-key reconciliation cannot
        pair: the key it would look the peer up by is the thing that changed
        (Codex review, P2). Keyed by declared name, which is what the second
        tier resolves on.
        """
        return AbiSnapshot(
            library="libgen.so",
            version="1",
            functions=[
                Function(
                    name=name,
                    mangled=mangled,
                    return_type="void",
                    params=[Param(name="p", type=ptype)],
                    visibility=Visibility.HIDDEN,
                    in_public_contract_fact=_contract(evidence),
                )
                for name, (mangled, ptype) in decls.items()
            ],
            elf=ElfMetadata(
                soname="libgen.so.1",
                symbols=[ElfSymbol(name="_Z6anchorv", visibility="default")],
            ),
            from_headers=True,
        )

    @given(evidence_on_old=st.booleans(), count=st.integers(min_value=1, max_value=4))
    @settings(deadline=None, max_examples=20)
    def test_a_migration_that_moves_the_mangled_key_survives_the_gap(
        self, evidence_on_old: bool, count: int
    ) -> None:
        """The mangled key is what the change moves, so the exact-key join
        finds no peer and the finding is lost outright -- reported as a bare
        removal or addition instead."""
        old = self._param_snapshot(
            {f"fn{i}": (f"_Z3fn{i}PKc", "char *") for i in range(count)},
            evidence=evidence_on_old,
        )
        new = self._param_snapshot(
            {f"fn{i}": (f"_Z3fn{i}PKDu", "char8_t *") for i in range(count)},
            evidence=not evidence_on_old,
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.CHAR8T_MIGRATION in kinds, sorted(k.value for k in kinds)

    @given(evidence_on_old=st.booleans())
    @settings(deadline=None, max_examples=10)
    def test_an_overload_set_is_not_paired_through_the_alias_tier(
        self, evidence_on_old: bool
    ) -> None:
        """The second tier is ambiguity-safe: two declarations sharing a
        name resolve to neither, so a reconciliation can never pair an
        overload set onto one peer -- the same rule the mangled-key join
        already applies in its own direction."""
        old = self._param_snapshot(
            {"fn": ("_Z2fnPKc", "char *")}, evidence=evidence_on_old
        )
        new = self._param_snapshot(
            {"fn": ("_Z2fnPKDu", "char8_t *")}, evidence=not evidence_on_old
        )
        # A second overload of the same name on the evidence-poor side makes
        # the alias ambiguous; nothing may be paired through it.
        ambiguous = new if evidence_on_old else old
        ambiguous.functions.append(
            Function(
                name="fn",
                mangled="_Z2fni",
                return_type="void",
                params=[Param(name="p", type="int")],
                visibility=Visibility.HIDDEN,
                in_public_contract_fact=_contract(not evidence_on_old)
                if ambiguous is new
                else _contract(evidence_on_old),
            )
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.CHAR8T_MIGRATION not in kinds, sorted(k.value for k in kinds)

    @staticmethod
    def _snapshot(returns: dict[str, str], *, evidence: bool) -> AbiSnapshot:
        return AbiSnapshot(
            library="libgen.so",
            version="1",
            functions=[
                Function(
                    name=name,
                    mangled=name,
                    return_type=ret,
                    visibility=Visibility.HIDDEN,
                    in_public_contract_fact=_contract(evidence),
                )
                for name, ret in returns.items()
            ],
            elf=ElfMetadata(
                soname="libgen.so.1",
                symbols=[ElfSymbol(name="_Z6anchorv", visibility="default")],
            ),
            from_headers=True,
        )

    @given(evidence_on_old=st.booleans(), count=st.integers(min_value=1, max_value=5))
    @settings(deadline=None, max_examples=20)
    def test_a_char8_t_migration_survives_an_evidence_gap(
        self, evidence_on_old: bool, count: int
    ) -> None:
        names = [f"_Z3fn{i}v" for i in range(count)]
        old = self._snapshot(dict.fromkeys(names, "char *"), evidence=evidence_on_old)
        new = self._snapshot(
            dict.fromkeys(names, "char8_t *"), evidence=not evidence_on_old
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.CHAR8T_MIGRATION in kinds, sorted(k.value for k in kinds)

    @given(evidence_on_old=st.booleans())
    @settings(deadline=None, max_examples=10)
    def test_an_integer_model_flip_survives_an_evidence_gap(
        self, evidence_on_old: bool
    ) -> None:
        names = [f"_Z3fn{i}v" for i in range(6)]
        old = self._snapshot(dict.fromkeys(names, "int"), evidence=evidence_on_old)
        new = self._snapshot(dict.fromkeys(names, "long"), evidence=not evidence_on_old)
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.INTEGER_MODEL_CHANGED in kinds, sorted(k.value for k in kinds)


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


class TestReleaseJobMemoryBudgetIsDepthAware:
    """The release fan-out's per-worker RAM budget follows the evidence
    depth the run actually asks for.

    Not part of the surface-evidence class above -- it rides with it because
    the same measured bundle run exposed both -- but the same failure shape:
    a number calibrated on one shape, applied to another where it is wrong
    by an order of magnitude (there, an OOM-killed job rather than a
    manufactured finding).
    """

    def test_deeper_evidence_never_gets_a_smaller_budget(self) -> None:
        from abicheck.workflows.release_jobs import release_job_mem_budget_gib

        ladder = ("binary", "headers", "build", "source")
        budgets = [release_job_mem_budget_gib(d) for d in ladder]
        assert budgets == sorted(budgets), dict(zip(ladder, budgets))
        assert budgets[0] < budgets[1], "header depth must cost more than binary"

    def test_unknown_or_absent_depth_keeps_the_pre_existing_default(self) -> None:
        from abicheck.workflows.release_jobs import (
            _RELEASE_JOB_MEM_BUDGET_GIB,
            release_job_mem_budget_gib,
        )

        for depth in (None, "", "not-a-rung"):
            assert release_job_mem_budget_gib(depth) == _RELEASE_JOB_MEM_BUDGET_GIB

    def test_header_roots_alone_size_the_worker_as_header_depth(self) -> None:
        """`--depth` is a floor, not a description of the run.

        It is `None` for an ordinary `compare OLD_DIR NEW_DIR --header ...`,
        and the pipeline then infers header evidence from the roots. Sizing
        off the raw option budgets such a worker as binary depth -- four to
        six times too many workers, the overcommit the table exists to
        prevent (Codex review, P1).
        """
        from abicheck.workflows.release_jobs import (
            release_job_mem_budget_gib,
            sizing_depth,
        )

        assert sizing_depth(None, header_roots=True) == "headers"
        assert sizing_depth(None, header_roots=False) is None
        # An explicit rung always wins, in both directions: a run pinned to
        # binary depth is not re-sized upward merely for carrying roots, and
        # a deeper pin is never narrowed to "headers".
        assert sizing_depth("binary", header_roots=True) == "binary"
        assert sizing_depth("source", header_roots=False) == "source"
        assert release_job_mem_budget_gib(
            sizing_depth(None, header_roots=True)
        ) > release_job_mem_budget_gib(sizing_depth(None, header_roots=False))

    def test_an_explicit_override_wins_at_every_depth(
        self, monkeypatch: object
    ) -> None:
        from abicheck.workflows.release_jobs import release_job_mem_budget_gib

        monkeypatch.setenv("ABICHECK_RELEASE_JOB_MEM_GIB", "2.5")  # type: ignore[attr-defined]
        for depth in (None, "binary", "headers", "build", "source"):
            assert release_job_mem_budget_gib(depth) == 2.5


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
