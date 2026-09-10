# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-061 gap D / Definition-of-Done item 8, closure package 4:
``ReleaseScopePlan``/``ReleaseScopeResult`` (``abicheck.workflows.
release_scope``) -- the release fan-out's own Request -> ResolvedPlan ->
Result pair for ADR-065's scope model, replacing the four independent
locals (``old_map``/``new_map``/``matched_keys``/``inventory_evidence``)
``cli_compare_release.py`` used to thread by hand.

**Characterization**: :class:`TestBuildReleaseScopeRecordUnaffected` pins
that routing the exact same inputs through :class:`ReleaseScopePlan` before
calling :func:`build_release_scope_record` produces a byte-identical
:class:`~abicheck.model.scope_acquisition.ScopeAcquisitionRecord` to calling
it directly with the raw values -- the wrapping introduced by this closure
package changes no observable behavior. The full existing
``tests/test_compare_release.py`` + ``tests/test_release_scope_*.py`` suite
(282 tests) was also run and confirmed to pass identically before and after
wiring ``cli_compare_release.py`` through ``scope_plan.*`` at its two
``build_*_scope_record`` call sites.

**Completion**: :class:`TestReleaseScopePlanParity` states the actual
convergence -- an equivalent CLI-shaped invocation (raw locals) and a
typed-plan-shaped invocation (``ReleaseScopePlan``) resolve to an *equal*
scope outcome, checked as five separate assertions (compatibility/
assurance/scope/gate/exit-relevant fields) rather than one aggregate
equality, per this closure package's completion-test contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, strategies as st
from test_compare_release import _invoke, _snap, _write_snap

from abicheck.model.scope_acquisition import AcquisitionState, InventoryCompleteness
from abicheck.workflows.release_scope import (
    DIRECT_PAIR_KEY,
    ReleaseInventoryEvidence,
    ReleaseScopePlan,
    ReleaseScopeResult,
    SideInventory,
    build_release_scope_record,
    resolve_release_scope_plan,
    resolve_release_scope_result,
)

_UNPROVEN = SideInventory(InventoryCompleteness.UNPROVEN, "test")
_PROVEN = SideInventory(InventoryCompleteness.PROVEN, "test")


def _maps(
    old_keys: list[str], new_keys: list[str]
) -> tuple[dict[str, Path], dict[str, Path]]:
    return (
        {k: Path(f"/old/{k}.so") for k in old_keys},
        {k: Path(f"/new/{k}.so") for k in new_keys},
    )


class TestReleaseScopePlanConstruction:
    def test_resolve_release_scope_plan_is_a_pure_wrapper(self) -> None:
        old_map, new_map = _maps(["a", "b"], ["a", "c"])
        matched = ["a"]
        evidence = ReleaseInventoryEvidence(old=_UNPROVEN, new=_UNPROVEN)

        plan = resolve_release_scope_plan(old_map, new_map, matched, evidence)

        assert plan.old_map == old_map
        assert plan.new_map == new_map
        assert plan.matched_keys == ("a",)
        assert plan.evidence is evidence

    def test_matched_keys_accepts_any_sequence_type(self) -> None:
        old_map, new_map = _maps(["a"], ["a"])
        evidence = ReleaseInventoryEvidence(old=_UNPROVEN, new=_UNPROVEN)
        as_list = resolve_release_scope_plan(old_map, new_map, ["a"], evidence)
        as_tuple = resolve_release_scope_plan(old_map, new_map, ("a",), evidence)
        assert as_list == as_tuple

    def test_resolve_release_scope_result_pairs_plan_and_record(self) -> None:
        old_map, new_map = _maps(["a"], ["a"])
        evidence = ReleaseInventoryEvidence(old=_UNPROVEN, new=_UNPROVEN)
        plan = resolve_release_scope_plan(old_map, new_map, ["a"], evidence)
        record = build_release_scope_record(
            old_map,
            new_map,
            ["a"],
            [{"library": "a.so", "verdict": "NO_CHANGE"}],
            evidence,
        )
        result = resolve_release_scope_result(plan, record)
        assert isinstance(result, ReleaseScopeResult)
        assert result.plan is plan
        assert result.record is record


class TestBuildReleaseScopeRecordUnaffected:
    """The characterization property: plan-mediated construction produces
    the identical record a direct call produces."""

    @given(
        old_keys=st.lists(st.sampled_from(["a", "b", "c", "d"]), unique=True),
        new_keys=st.lists(st.sampled_from(["a", "b", "c", "d"]), unique=True),
    )
    def test_plan_mediated_record_matches_direct_call(
        self, old_keys: list[str], new_keys: list[str]
    ) -> None:
        old_map, new_map = _maps(old_keys, new_keys)
        matched_keys = sorted(set(old_keys) & set(new_keys))
        library_results = [
            {"library": old_map[k].name, "verdict": "NO_CHANGE"} for k in matched_keys
        ]
        evidence = ReleaseInventoryEvidence(old=_UNPROVEN, new=_UNPROVEN)

        direct = build_release_scope_record(
            old_map, new_map, matched_keys, library_results, evidence
        )

        plan = resolve_release_scope_plan(old_map, new_map, matched_keys, evidence)
        via_plan = build_release_scope_record(
            plan.old_map,
            plan.new_map,
            plan.matched_keys,
            library_results,
            plan.evidence,
        )

        assert via_plan == direct

    def test_direct_pair_shape_is_unaffected(self) -> None:
        old_map = {DIRECT_PAIR_KEY: Path("/o/libx.so")}
        new_map = {DIRECT_PAIR_KEY: Path("/n/libx.so")}
        evidence = ReleaseInventoryEvidence(
            old=_UNPROVEN, new=_UNPROVEN, direct_pair=True
        )
        library_results = [{"library": "libx.so", "verdict": "NO_CHANGE"}]

        direct = build_release_scope_record(
            old_map, new_map, [DIRECT_PAIR_KEY], library_results, evidence
        )
        plan = resolve_release_scope_plan(old_map, new_map, [DIRECT_PAIR_KEY], evidence)
        via_plan = build_release_scope_record(
            plan.old_map,
            plan.new_map,
            plan.matched_keys,
            library_results,
            plan.evidence,
        )
        assert via_plan == direct
        assert direct.selection == "direct_pair"


class TestReleaseScopePlanParity:
    """Completion test: a CLI-shaped call (raw locals) and a typed-plan-
    shaped call resolve to an equal scope outcome, asserted along five
    separate axes rather than only an aggregate equality."""

    def test_raw_locals_and_typed_plan_agree_on_every_axis(self) -> None:
        old_map, new_map = _maps(["a", "b"], ["a"])
        matched_keys = ["a"]
        evidence = ReleaseInventoryEvidence(old=_PROVEN, new=_UNPROVEN)
        library_results = [{"library": "a.so", "verdict": "NO_CHANGE"}]

        # "CLI-shaped": the raw locals cli_compare_release.py used to pass
        # directly, before this closure package's plan wrapper existed.
        cli_shaped = build_release_scope_record(
            old_map, new_map, matched_keys, library_results, evidence
        )

        # "typed-plan-shaped": resolved through ReleaseScopePlan first.
        plan = resolve_release_scope_plan(old_map, new_map, matched_keys, evidence)
        plan_shaped = build_release_scope_record(
            plan.old_map,
            plan.new_map,
            plan.matched_keys,
            library_results,
            plan.evidence,
        )

        # 1. compatibility-relevant: the same members reach AVAILABLE.
        assert {
            m.member for m in cli_shaped.members_in(AcquisitionState.AVAILABLE)
        } == {m.member for m in plan_shaped.members_in(AcquisitionState.AVAILABLE)}
        # 2. assurance/coverage-relevant: unchecked members agree.
        assert {m.member for m in cli_shaped.unchecked_members} == {
            m.member for m in plan_shaped.unchecked_members
        }
        # 3. scope: the selection rule and out-of-scope members agree.
        assert cli_shaped.selection == plan_shaped.selection
        assert cli_shaped.out_of_scope_members == plan_shaped.out_of_scope_members
        # 4. gate-relevant: proven removals (the exit-8 input) agree.
        assert cli_shaped.proven_removed_members == plan_shaped.proven_removed_members
        # 5. the whole resolved outcome is equal.
        assert cli_shaped == plan_shaped


class TestReleaseScopeResultWiredIntoRealProductionFlow:
    """Codex review (PR #1192): ``resolve_release_scope_result``/
    ``ReleaseScopeResult`` must be a real production consumer of
    ``compare-release``'s own flow, not merely unit-tested surface --
    ``cli_compare_release.py`` now threads ``scope_result.record``
    end-to-end after resolving it, replacing the bare
    ``ScopeAcquisitionRecord`` everywhere downstream (bundle analysis,
    scope-decision resolution, the stderr notices, the JSON writer).

    Patches ``resolve_release_scope_result`` where
    ``cli_compare_release.py`` resolves it (module-level import binding --
    see ``abicheck/workflows/AGENTS.md``'s own note on this), and drives the
    real CLI (``abicheck compare <dir> <dir>``) end to end, exactly the way
    ``tests/test_compare_release.py``'s own ``TestDirVsDir`` does."""

    def test_a_real_directory_compare_constructs_and_uses_the_result(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        snap = _snap()
        _write_snap(old_dir / "libfoo.json", snap)
        _write_snap(new_dir / "libfoo.json", snap)

        calls: list[tuple[ReleaseScopePlan, object]] = []
        real = resolve_release_scope_result

        def _spy(plan: ReleaseScopePlan, record: object) -> ReleaseScopeResult:
            calls.append((plan, record))
            return real(plan, record)

        with patch(
            "abicheck.cli_compare_release.resolve_release_scope_result",
            side_effect=_spy,
        ):
            code, out = _invoke("compare", str(old_dir), str(new_dir))

        assert code == 0
        assert "NO_CHANGE" in out
        # The real production call happened exactly once, with a genuine
        # plan (not a stub) and a record naming the one compared library --
        # proof this is a live consumer, not dead, unreachable code.
        assert len(calls) == 1
        plan, record = calls[0]
        assert isinstance(plan, ReleaseScopePlan)
        available = record.members_in(AcquisitionState.AVAILABLE)
        assert len(available) == 1
        assert available[0].name == "libfoo.json"

    def test_result_wiring_does_not_change_a_breaking_release_outcome(
        self, tmp_path: Path
    ) -> None:
        """Characterization: routing through ``ReleaseScopeResult`` changes
        no observable behavior -- the exact same exit code and verdict a
        pre-wiring run produced."""
        from test_compare_release import _breaking_pair

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libbar.json", _snap())
        _write_snap(new_dir / "libbar.json", _snap())
        code, out = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 4
        assert "BREAKING" in out


class TestScopePlanIsExecutionAuthoritative:
    """Codex review (PR #1192, follow-up finding): `scope_plan` must be what
    execution actually consumes, not a DTO only read by the post-execution
    record builder -- a future normalization/selection rule added to
    `resolve_release_scope_plan` must change *which pairs run*, not merely
    how the reported `ReleaseScopeResult` describes them.

    Proven here by patching `resolve_release_scope_plan` (where
    `cli_compare_release.py` resolves it) to return a plan whose
    `matched_keys` *narrows out* one of the two real, on-disk members, and
    asserting the real execution never compares it -- if execution still
    read the original locals instead of the plan, this member would still
    be compared and reported."""

    def test_a_plan_narrowed_matched_keys_actually_narrows_execution(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        for name in ("libfoo.json", "libbar.json"):
            snap = _snap(library=name.replace(".json", ".so"))
            _write_snap(old_dir / name, snap)
            _write_snap(new_dir / name, snap)

        real = resolve_release_scope_plan

        def _narrow_to_libfoo_only(old_map, new_map, matched_keys, evidence):
            plan = real(old_map, new_map, matched_keys, evidence)
            narrowed_keys = tuple(k for k in plan.matched_keys if "libbar" not in k)
            assert narrowed_keys and narrowed_keys != plan.matched_keys
            return ReleaseScopePlan(
                old_map=plan.old_map,
                new_map=plan.new_map,
                matched_keys=narrowed_keys,
                evidence=plan.evidence,
            )

        with patch(
            "abicheck.cli_compare_release.resolve_release_scope_plan",
            side_effect=_narrow_to_libfoo_only,
        ):
            code, out = _invoke(
                "compare", str(old_dir), str(new_dir), "--format", "json"
            )

        assert code == 0
        data = json.loads(out)
        compared_names = {lib["library"] for lib in data["libraries"]}
        # libbar was narrowed out of the plan before execution -- if
        # execution still read the raw `matched_keys` local instead of
        # `scope_plan.matched_keys`, libbar would still appear here.
        assert compared_names == {"libfoo.json"}


class TestExplicitSelectionFoldedIntoScopePlan:
    """Codex review (PR #1192, third follow-up finding): the same class of
    bug ``TestScopePlanIsExecutionAuthoritative`` fixed for the discovery
    path, for the ``--select``/``--select-required`` path instead --
    ``ReleaseScopePlan.matched_keys`` must already be narrowed to the
    declared selection, not merely used unfiltered while a separate,
    post-hoc filter on ``compare_keys`` narrows what actually executes.
    Before the fix, ``resolve_release_scope_plan`` was called with every
    discovered key and only ``compare_keys`` was narrowed afterward, so the
    real ``ReleaseScopePlan`` object execution reads from -- and
    ``ReleaseScopeResult.plan`` reports -- still claimed the excluded
    member was matched."""

    def test_select_narrows_the_resolved_plan_itself(self, tmp_path: Path) -> None:
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        _write_snap(old_dir / "libbar.json", _snap("libbar.so"))
        _write_snap(new_dir / "libbar.json", _snap("libbar.so"))

        calls: list[ReleaseScopePlan] = []
        real = resolve_release_scope_plan

        def _spy(old_map, new_map, matched_keys, evidence):
            plan = real(old_map, new_map, matched_keys, evidence)
            calls.append(plan)
            return plan

        with patch(
            "abicheck.cli_compare_release.resolve_release_scope_plan",
            side_effect=_spy,
        ):
            code, out = _invoke(
                "compare",
                str(old_dir),
                str(new_dir),
                "--select",
                "libfoo.json",
                "--format",
                "json",
            )

        assert code == 0
        # The real production call happened exactly once.
        assert len(calls) == 1
        plan = calls[0]
        # The bug: pre-fix, `resolve_release_scope_plan` was called with
        # every discovered key, so the resolved `ReleaseScopePlan` itself
        # (not merely a later `compare_keys` local) still claimed libbar
        # was matched even though it is out of the declared selection.
        assert "libbar.json" not in plan.matched_keys
        assert "libfoo.json" in plan.matched_keys

        # Execution and the reported scope record agree with the plan.
        data = json.loads(out)
        assert [lib["library"] for lib in data["libraries"]] == ["libfoo.json"]
        by_member = {m["member"]: m for m in data["comparison_scope"]["members"]}
        assert by_member["libbar.json"]["state"] == "out_of_scope"
