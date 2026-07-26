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

"""Tests for abicheck.diff_layout_coherence (P0 evidence-coherence audit
follow-up) — surfaces AbiSnapshot.dwarf_layout_coherence == "mismatch" as an
ordinary HEADER_BINARY_CONTEXT_MISMATCH finding at compare time."""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.diff_layout_coherence import _diff_dwarf_layout_coherence
from abicheck.model import AbiSnapshot


def _snap(**kw) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version="1.0", **kw)


class TestDiffDwarfLayoutCoherenceDirect:
    """Direct unit tests of the detector function, bypassing compare()'s
    detector-registry gating."""

    def test_neither_side_mismatch_yields_no_changes(self) -> None:
        old = _snap(dwarf_layout_coherence="matched")
        new = _snap(dwarf_layout_coherence="partial")
        assert _diff_dwarf_layout_coherence(old, new) == []

    def test_none_coherence_yields_no_changes(self) -> None:
        old = _snap(dwarf_layout_coherence=None)
        new = _snap(dwarf_layout_coherence=None)
        assert _diff_dwarf_layout_coherence(old, new) == []

    def test_new_side_mismatch_yields_one_change(self) -> None:
        old = _snap(dwarf_layout_coherence="matched")
        new = _snap(
            dwarf_layout_coherence="mismatch",
            dwarf_layout_coherence_mismatches=("Foo", "Bar"),
        )
        changes = _diff_dwarf_layout_coherence(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH
        assert changes[0].affected_symbols == ["Foo", "Bar"]
        assert "new" in changes[0].description
        assert "Foo" in changes[0].description

    def test_old_side_mismatch_yields_one_change(self) -> None:
        old = _snap(
            dwarf_layout_coherence="mismatch",
            dwarf_layout_coherence_mismatches=("Foo",),
        )
        new = _snap(dwarf_layout_coherence="matched")
        changes = _diff_dwarf_layout_coherence(old, new)
        assert len(changes) == 1
        assert "old" in changes[0].description

    def test_both_sides_mismatch_yields_two_changes(self) -> None:
        old = _snap(
            dwarf_layout_coherence="mismatch",
            dwarf_layout_coherence_mismatches=("Foo",),
        )
        new = _snap(
            dwarf_layout_coherence="mismatch",
            dwarf_layout_coherence_mismatches=("Bar",),
        )
        changes = _diff_dwarf_layout_coherence(old, new)
        assert len(changes) == 2

    def test_long_mismatch_list_is_truncated_in_description(self) -> None:
        names = tuple(f"Type{i}" for i in range(8))
        old = _snap(dwarf_layout_coherence="matched")
        new = _snap(
            dwarf_layout_coherence="mismatch", dwarf_layout_coherence_mismatches=names
        )
        changes = _diff_dwarf_layout_coherence(old, new)
        assert "and 3 more" in changes[0].description


class TestDiffDwarfLayoutCoherenceViaCompare:
    """End-to-end through checker.compare() — proves detector-registry
    wiring (requires_support gate, RISK verdict, never escalates a clean
    comparison to BREAKING on its own)."""

    def test_mismatch_surfaces_as_risk_finding(self) -> None:
        old = _snap(dwarf_layout_coherence="matched")
        new = _snap(
            dwarf_layout_coherence="mismatch",
            dwarf_layout_coherence_mismatches=("Foo",),
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH in kinds
        assert result.verdict == Verdict.COMPATIBLE_WITH_RISK

    def test_no_mismatch_produces_no_finding(self) -> None:
        old = _snap(dwarf_layout_coherence="matched")
        new = _snap(dwarf_layout_coherence="partial")
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH not in kinds
        assert result.verdict == Verdict.NO_CHANGE

    def test_mismatch_does_not_escalate_an_otherwise_breaking_verdict(self) -> None:
        """The coherence finding is RISK-tier; it must not itself downgrade
        or override a real, independently-proven BREAKING verdict from
        another detector — it just rides alongside it."""
        from abicheck.checker_types import Change

        old = _snap(
            dwarf_layout_coherence="mismatch",
            dwarf_layout_coherence_mismatches=("Foo",),
        )
        new = _snap(dwarf_layout_coherence="mismatch")
        breaking_change = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="_Z1fv", description="removed"
        )
        result = compare(old, new, extra_changes=[breaking_change])
        assert result.verdict == Verdict.BREAKING
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH in kinds
        assert ChangeKind.FUNC_REMOVED in kinds
