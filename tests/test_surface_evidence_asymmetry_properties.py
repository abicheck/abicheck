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

from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.declarations import Function, Variable, Visibility
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
