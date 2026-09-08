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

"""``compare --dry-run``'s "Comparison plan" section (ADR-065 S1).

A new, dedicated leaf module rather than an addition to
:mod:`abicheck.cli_compare_helpers` or
:mod:`abicheck.cli_compare_release_helpers`: both of those already sit at
their own ``architecture/debt.yaml`` ``no_growth`` baseline with no room for
a new function (see this file's own PR for the measurement) -- exactly the
"prefer extending a split-out module over growing the parent toward the cap"
guidance in the root ``CLAUDE.md``, applied by creating a fresh, correctly
classified (``frontends``) home instead of bumping either legacy baseline.
The actual plan *computation*, including discovering what counts as a
comparable input in a plain directory the same way the real fan-out does,
lives in :func:`abicheck.workflows.release_plan.
build_release_plan_from_directories`; this module only renders it into
``DryRunResult`` section lines, mirroring ``frontends/cli/scan_dry_run.py``'s
own split between a workflow's computed plan and its CLI rendering.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...dry_run import DryRunResult
    from ...model.release_selection import ReleaseSelection

__all__ = ["add_comparison_plan_section", "release_dry_run_plan_lines"]


def add_comparison_plan_section(
    result: DryRunResult,
    old_input: Path,
    new_input: Path,
    old_kind: str,
    new_kind: str,
    select: tuple[str, ...],
    select_required: tuple[str, ...],
) -> None:
    """Parse ``--select``/``--select-required`` (warning on a bad one rather
    than raising -- a dry run reports, it doesn't fail) and add the
    "Comparison plan" section. The one call site
    (:mod:`abicheck.cli_compare_helpers`'s ``_render_compare_dry_run``) has
    no growth budget of its own, so this whole step -- not just the plan
    computation -- lives here instead.
    """
    release_selection = None
    if select or select_required:
        from ...model.release_selection import ReleaseSelection

        try:
            release_selection = ReleaseSelection.from_lists(
                required=select_required, optional=select
            )
        except ValueError as exc:
            result.warn(f"--select/--select-required: {exc}")
    result.add(
        "Comparison plan",
        *release_dry_run_plan_lines(
            old_input, new_input, old_kind, new_kind, release_selection
        ),
    )


def release_dry_run_plan_lines(
    old_input: Path,
    new_input: Path,
    old_kind: str,
    new_kind: str,
    release_selection: ReleaseSelection | None,
) -> list[str]:
    """The ``compare --dry-run`` "Comparison plan" section body: what a real
    release fan-out would pair, computed with no per-library dump/compare
    and -- for a plain directory pair -- no package extraction either,
    matching this renderer's existing side-effect-free contract. A package
    operand is not extracted for a preview (extraction is exactly the
    filesystem work a dry run avoids everywhere else); the declared
    selection is still shown, so a caller invoking ``--dry-run`` mainly to
    validate ``--select``/``--select-required`` still gets an answer.
    """
    if old_kind != "directory" or new_kind != "directory":
        if release_selection is None:
            return [
                "preview skipped for a package operand (would require "
                "extraction); pass a plain directory pair to preview pairing, "
                "or --select/--select-required to validate a declared selection"
            ]
        return [
            "preview skipped for a package operand (would require "
            "extraction) -- declared selection: "
            + ", ".join(
                f"{k}{'' if req else ' (optional)'}"
                for k, req in sorted(release_selection.members.items())
            )
        ]
    from ...workflows.release_plan import build_release_plan_from_directories

    try:
        plan = build_release_plan_from_directories(
            old_input, new_input, selection=release_selection
        )
    except (OSError, ValueError) as exc:  # best-effort preview
        return [f"could not discover libraries: {exc}"]
    lines = [
        f"{len(plan.would_compare_members)} member(s) would be compared "
        f"({plan.selection_kind} selection)"
    ]
    for entry in plan.entries:
        if entry.would_compare:
            marker = "compare"
        elif entry.required:
            marker = "MISSING"
        else:
            marker = "optional"
        lines.append(f"  {marker}: {entry.name} -- {entry.note}")
    if plan.missing_required:
        lines.append(
            f"{len(plan.missing_required)} required member(s) would not be "
            "compared -- see .abicheck.yml's scope.on_incomplete"
        )
    return lines
