# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-061 gap D / Definition-of-Done item 8, closure package 4's next
slice: :class:`~abicheck.frontends.cli.release_compare_request.
ReleaseCompareRequest`/:class:`~abicheck.frontends.cli.
release_compare_request.ReleaseComparePlan` and
:func:`~abicheck.frontends.cli.release_compare_request.
resolve_release_compare_plan`.

**Direct-call half**: :class:`TestResolveReleaseComparePlanDirectly` proves
the completion test's core claim -- the release fan-out's scope/inventory/
gate resolution is reachable with one ordinary Python function call, no
Click context, no ``click.Context``, no CLI invocation of any kind.

**Parity half**: :class:`TestReleaseCompareRequestParity` is the actual
completion test this closure package's slice states: a CLI-shaped
invocation of ``compare-release`` and a direct, typed-request-shaped call
to :func:`resolve_release_compare_plan`, given equivalent operands and
options, resolve to the *same* scope, gate configuration, and degraded-
member markers -- proven by spying on the one production call site
(``cli_compare_release.py``'s lazy import of ``resolve_release_compare_plan``)
to capture the real ``ReleaseComparePlan`` the CLI run actually used, then
comparing it against a plan built by calling the function directly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from test_compare_release import _invoke, _snap, _write_snap

from abicheck.frontends.cli.release_compare_request import (
    ReleaseComparePlan,
    ReleaseCompareRequest,
    resolve_release_compare_plan,
)


class TestResolveReleaseComparePlanDirectly:
    """No Click, no CLI invocation -- a plain function call."""

    def test_resolves_a_plan_from_two_directories(self, tmp_path: Path) -> None:
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())

        request = ReleaseCompareRequest(old_dir=old_dir, new_dir=new_dir)
        plan = resolve_release_compare_plan(request)

        assert isinstance(plan, ReleaseComparePlan)
        assert plan.scope.matched_keys == ("libfoo.json",)
        assert plan.compare_keys == ["libfoo.json"]
        # No severity setting given -> the legacy (non-severity-aware) gate.
        assert plan.gate.severity is None

    def test_selection_narrows_the_resolved_scope(self, tmp_path: Path) -> None:
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        _write_snap(old_dir / "libbar.json", _snap("libbar.so"))
        _write_snap(new_dir / "libbar.json", _snap("libbar.so"))

        from abicheck.model.release_selection import ReleaseSelection

        request = ReleaseCompareRequest(
            old_dir=old_dir,
            new_dir=new_dir,
            release_selection=ReleaseSelection.from_lists(
                required=[], optional=["libfoo.json"]
            ),
        )
        plan = resolve_release_compare_plan(request)

        assert plan.scope.matched_keys == ("libfoo.json",)

    def test_severity_preset_reaches_the_resolved_gate(self, tmp_path: Path) -> None:
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())

        request = ReleaseCompareRequest(
            old_dir=old_dir, new_dir=new_dir, severity_preset="strict"
        )
        plan = resolve_release_compare_plan(request)

        assert plan.gate.severity is not None
        assert plan.gate.severity_preset == "strict"


class TestReleaseCompareRequestParity:
    """Completion test: CLI-shaped and typed-request-shaped invocations
    agree on the resolved scope, gate configuration, and degraded-member
    markers."""

    def test_cli_and_direct_call_resolve_the_same_plan(self, tmp_path: Path) -> None:
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        _write_snap(old_dir / "libbar.json", _snap("libbar.so"))
        _write_snap(new_dir / "libbar.json", _snap("libbar.so"))

        captured: list[ReleaseComparePlan] = []
        real = resolve_release_compare_plan

        def _spy(request, **kwargs):
            plan = real(request, **kwargs)
            captured.append(plan)
            return plan

        with patch(
            "abicheck.frontends.cli.release_compare_request.resolve_release_compare_plan",
            side_effect=_spy,
        ):
            code, _out = _invoke(
                "compare",
                str(old_dir),
                str(new_dir),
                "--format",
                "json",
                "--severity-preset",
                "strict",
            )
        assert code in (
            0,
            1,
            2,
            4,
        )  # a real run completed (exit code is not the point here)
        assert len(captured) == 1
        cli_plan = captured[0]

        direct_request = ReleaseCompareRequest(
            old_dir=old_dir, new_dir=new_dir, severity_preset="strict"
        )
        direct_plan = resolve_release_compare_plan(direct_request)

        # 1. scope: the same members are matched.
        assert cli_plan.scope.matched_keys == direct_plan.scope.matched_keys
        # 2. inventory evidence: the same completeness proof on each side.
        assert cli_plan.scope.evidence == direct_plan.scope.evidence
        # 3. gate configuration: the same resolved severity/exit-code-scheme.
        assert cli_plan.gate == direct_plan.gate
        # 4. acquisition-relevant: the same degraded-member markers.
        assert cli_plan.degraded == direct_plan.degraded
        # 5. the actual execution set agrees.
        assert cli_plan.compare_keys == direct_plan.compare_keys


class TestTempDirTracking:
    """Codex review (PR #1215): a direct caller that lets a package/
    stored-package operand resolve through the default ``make_temp_dir``
    factory must not leak the directories it allocates -- every directory
    either factory creates is recorded on the resolved plan's ``temp_dirs``,
    and :func:`cleanup_release_compare_plan` removes them.

    No real stored ``ProjectSnapshot`` package fixture is built here --
    ``_resolve_release_package_side`` (the one call site that actually
    invokes ``make_temp_dir`` for a package operand) is patched to call it
    directly, isolating the tracking wrapper's own contract from that
    unrelated package-format machinery."""

    def test_a_directory_ever_created_via_make_temp_dir_is_tracked(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())

        created: list[Path] = []

        def _fake_resolve_release_package_side(side_dir, variant_id, make_temp_dir, *, side):
            # Simulate what a real stored-package side does: ask for one
            # temp dir, then decline (this fixture is not a real package).
            path = make_temp_dir(f"abicheck_relpkg_{side}_")
            created.append(path)
            return None

        with patch(
            "abicheck.workflows.release_inputs.resolve_release_package_side",
            side_effect=_fake_resolve_release_package_side,
        ):
            request = ReleaseCompareRequest(old_dir=old_dir, new_dir=new_dir)
            plan = resolve_release_compare_plan(request)

        assert created  # the fake actually ran and asked for temp dirs
        assert all(p.is_dir() for p in created)
        assert set(plan.temp_dirs) == set(created)

        from abicheck.frontends.cli.release_compare_request import (
            cleanup_release_compare_plan,
        )

        cleanup_release_compare_plan(plan)
        assert not any(p.exists() for p in created)

    def test_cleanup_is_a_no_op_on_an_empty_plan(self, tmp_path: Path) -> None:
        from abicheck.frontends.cli.release_compare_request import (
            cleanup_release_compare_plan,
        )

        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())

        plan = resolve_release_compare_plan(
            ReleaseCompareRequest(old_dir=old_dir, new_dir=new_dir)
        )
        assert plan.temp_dirs == ()
        cleanup_release_compare_plan(plan)  # must not raise

    def test_a_directory_allocated_before_a_later_failure_is_still_removed(
        self, tmp_path: Path
    ) -> None:
        """Codex review (PR #1215, second finding): a temp dir allocated by
        ``_prepare_compare_release_inputs`` (e.g. for the old side) must not
        survive a failure raised *after* that allocation but *before* a
        ``ReleaseComparePlan`` is ever returned -- there is no plan for a
        caller to pass to :func:`cleanup_release_compare_plan` in that case,
        so this module's own resolution must clean up after itself."""
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())

        created: list[Path] = []

        def _fake_resolve_release_package_side(side_dir, variant_id, make_temp_dir, *, side):
            path = make_temp_dir(f"abicheck_relpkg_{side}_")
            created.append(path)
            if side == "new":
                raise RuntimeError("simulated failure after the old side allocated")
            return None

        with patch(
            "abicheck.workflows.release_inputs.resolve_release_package_side",
            side_effect=_fake_resolve_release_package_side,
        ):
            request = ReleaseCompareRequest(old_dir=old_dir, new_dir=new_dir)
            with pytest.raises(RuntimeError, match="simulated failure"):
                resolve_release_compare_plan(request)

        assert created  # both sides' factories ran before the raise
        assert not any(p.exists() for p in created)
