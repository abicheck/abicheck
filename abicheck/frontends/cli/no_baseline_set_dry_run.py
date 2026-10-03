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

"""``compare --no-baseline DIR --dry-run``'s report (one-comparison-product
F-23): which members a real N-library audit would audit or skip, and why --
with no analysis and no extraction.

A sibling of :mod:`.no_baseline_dry_run` (the scalar audit's preview) for
the same import reason that module states -- it reaches only
:mod:`abicheck.dry_run` and the workflow that computes the preview
(:func:`abicheck.workflows.no_baseline_set.preview_no_baseline_set`), so
rendering a plan never pulls the two-sided dry-run machinery in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from ...model.release_selection import ReleaseSelection

__all__ = ["build_no_baseline_set_dry_run_result"]


def build_no_baseline_set_dry_run_result(
    *,
    candidate: Path,
    operand_kind: str,
    selection: ReleaseSelection | None,
    fmt: str,
    on_incomplete: str,
    depth: str | None,
    contract_mode: str | None,
) -> Any:
    """The ``DryRunResult`` for an N-library audit preview."""
    from ...dry_run import DryRunResult, tool_status
    from ...workflows.no_baseline_set import preview_no_baseline_set

    result = DryRunResult(command="compare --no-baseline")
    result.add(
        "Inputs",
        f"candidate: {candidate} ({operand_kind} of libraries -- each member "
        "is audited on its own)",
        "baseline: (none -- OLD declared absent via --no-baseline)",
    )
    result.add(
        "Resolved depth and source scope",
        f"requested depth: {depth or '(not given)'}",
        "source scope: each member alone (an audit has no second side)",
    )
    result.add("Tools and frontends", *tool_status("castxml", "clang", "gcc", "g++"))
    result.add(
        "Consumer/contract scoping",
        f"--contract: {contract_mode or '(not given -- no contract evaluation)'}",
    )
    result.add(
        "Output and exit-code behavior",
        f"format: {fmt}",
        "no compatibility verdict is reported; the exit code folds every "
        "member's coverage, analysis-assurance, evidence-contract and audit-gate "
        "axes, a failed member's operational-error axis, and the scope axis "
        f"(scope.on_incomplete: {on_incomplete}); zero audited members exits 1",
    )
    entries = preview_no_baseline_set(
        candidate, operand_kind=operand_kind, selection=selection
    )
    if entries is None:
        declared = (
            ", ".join(
                f"{k}{'' if req else ' (optional)'}"
                for k, req in sorted(selection.members.items())
            )
            if selection is not None
            else ""
        )
        result.add(
            "Comparison plan",
            "preview skipped for a package operand (would require extraction "
            "or unpacking)"
            + (f" -- declared selection: {declared}" if declared else ""),
        )
        return result
    audit = [e for e in entries if e.would_audit]
    lines = [f"{len(audit)} member(s) would be audited"]
    for entry in entries:
        if entry.would_audit:
            marker = "audit"
        elif entry.required:
            marker = "MISSING"
        else:
            marker = "skip"
        lines.append(f"  {marker}: {entry.name} -- {entry.note}")
    missing = [e for e in entries if e.required and not e.would_audit]
    if missing:
        lines.append(
            f"{len(missing)} required member(s) would not be audited -- see "
            ".abicheck.yml's scope.on_incomplete"
        )
    if not audit:
        lines.append(
            "no member would be audited: the real run exits 1 (no audit "
            "completed is never a clean pass)"
        )
    result.add("Comparison plan", *lines)
    return result
