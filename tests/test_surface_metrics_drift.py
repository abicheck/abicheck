# Copyright 2026 Nikolay Petrov
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

"""Tests for ADR-027 A1/D1.2 surface-metric drift (--surface-metrics)."""

from __future__ import annotations

from abicheck import checker
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.diff_surface_metrics import diff_surface_metrics
from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Visibility


def _snap(
    func_names: list[str], *, export_only: list[str] | None = None
) -> AbiSnapshot:
    export_only = export_only or []
    fns = [
        Function(
            name=n,
            mangled=n,
            return_type="void",
            params=[],
            visibility=Visibility.PUBLIC,
            origin=(
                ScopeOrigin.EXPORT_ONLY
                if n in export_only
                else ScopeOrigin.PUBLIC_HEADER
            ),
        )
        for n in func_names
    ]
    return AbiSnapshot(library="l", version="1", from_headers=True, functions=fns)


def test_public_surface_grew() -> None:
    old = _snap(["a", "b"])
    new = _snap(["a", "b", "c"])
    kinds = {c.kind for c in diff_surface_metrics(old, new)}
    assert ChangeKind.PUBLIC_SURFACE_GREW in kinds
    assert ChangeKind.PUBLIC_SURFACE_SHRANK not in kinds


def test_public_surface_shrank() -> None:
    old = _snap(["a", "b", "c"])
    new = _snap(["a"])
    kinds = {c.kind for c in diff_surface_metrics(old, new)}
    assert ChangeKind.PUBLIC_SURFACE_SHRANK in kinds


def test_no_drift_when_count_stable() -> None:
    old = _snap(["a", "b"])
    new = _snap(["a", "b"])
    assert diff_surface_metrics(old, new) == []


def test_undocumented_ratio_increased() -> None:
    # old: 0/3 undocumented; new: 2/3 undocumented → ratio rises.
    old = _snap(["a", "b", "c"])
    new = _snap(["a", "b", "c"], export_only=["b", "c"])
    kinds = {c.kind for c in diff_surface_metrics(old, new)}
    assert ChangeKind.UNDOCUMENTED_EXPORT_RATIO_INCREASED in kinds


def test_metric_kinds_are_compatible() -> None:
    # They must never break the verdict on their own.
    old = _snap(["a"])
    new = _snap(["a", "b", "c"], export_only=["b", "c"])
    result = checker.compare(
        old, new, scope_to_public_surface=False, surface_metrics=True
    )
    metric_kinds = {
        ChangeKind.PUBLIC_SURFACE_GREW,
        ChangeKind.UNDOCUMENTED_EXPORT_RATIO_INCREASED,
    }
    emitted = {c.kind for c in result.changes}
    assert metric_kinds & emitted  # at least the grow + ratio findings present
    # All metric findings classify compatible; verdict stays non-breaking. Since
    # changes were emitted it must be COMPATIBLE, not NO_CHANGE (ADR-027 review:
    # the verdict is recomputed after the surface-metrics roll-ups are appended).
    assert result.verdict == Verdict.COMPATIBLE


def test_surface_metrics_recompute_verdict_not_no_change() -> None:
    # Regression: surface-metric roll-ups are appended after the verdict is first
    # computed. Without a recompute the verdict would stay NO_CHANGE while
    # `changes` carries a public_surface_grew finding — an inconsistent report.
    old = _snap(["a"])
    new = _snap(["a", "b", "c"])
    result = checker.compare(
        old, new, scope_to_public_surface=False, surface_metrics=True
    )
    assert any(c.kind == ChangeKind.PUBLIC_SURFACE_GREW for c in result.changes)
    assert result.verdict == Verdict.COMPATIBLE
    assert result.verdict != Verdict.NO_CHANGE


def test_surface_metrics_off_by_default() -> None:
    old = _snap(["a"])
    new = _snap(["a", "b", "c"])
    result = checker.compare(old, new, scope_to_public_surface=False)
    assert not any(
        c.kind in (ChangeKind.PUBLIC_SURFACE_GREW, ChangeKind.PUBLIC_SURFACE_SHRANK)
        for c in result.changes
    )


def test_metric_findings_suppressible() -> None:
    from abicheck.suppression import Suppression, SuppressionList

    old = _snap(["a"])
    new = _snap(["a", "b", "c"])
    supp = SuppressionList(
        [Suppression(symbol="<surface>", change_kind="public_surface_grew")]
    )
    result = checker.compare(
        old, new, suppression=supp, scope_to_public_surface=False, surface_metrics=True
    )
    assert not any(c.kind == ChangeKind.PUBLIC_SURFACE_GREW for c in result.changes)
    assert any(
        c.kind == ChangeKind.PUBLIC_SURFACE_GREW for c in result.suppressed_changes
    )
