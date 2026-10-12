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

"""The release's cross-member unreached-type pass, and the member re-stamp
it needs.

A member's verdict is stamped inside its own fan-out worker, before any
sibling has been compared. Whether a type the member provably does not reach
is *reached by a sibling* -- the condition for taking that type off the
member's verdict (``policy.member_type_attribution.
unreached_types_owned_elsewhere``) -- is known only once every worker is
done. This pass runs then, before the release folds member verdicts, and
re-derives every per-member field the moved findings fed, from the same
helpers the worker used.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ...checker_types import DiffResult

if TYPE_CHECKING:
    from ...workflows.gate import SeverityConfig

__all__ = ["dispose_release_unreached_types"]


def dispose_release_unreached_types(
    library_results: list[dict[str, object]],
    *,
    severity_config: SeverityConfig | None,
    require_complete_analysis: bool,
    contract_evaluation: bool,
    output_dir: Path | None,
) -> int:
    """Take each member's sibling-owned unreached type findings off its
    verdict; returns how many findings moved.

    Runs only over entries the fan-out scoped to the public surface: the
    moved findings are recorded in that scope's ledger, so a run that turned
    scoping off keeps counting them rather than hiding them somewhere it
    does not render.
    """
    from ...workflows.release_member_attribution import (
        dispose_unreached_type_findings,
        unreached_types_owned_elsewhere,
    )

    attributions = {
        str(entry.get("library", "")): attribution
        for entry in library_results
        if isinstance(attribution := entry.get("_type_attribution"), dict)
    }
    plan = unreached_types_owned_elsewhere(attributions)
    moved_total = 0
    for entry in library_results:
        owners = plan.get(str(entry.get("library", "")))
        result = entry.get("_diff_result")
        if not owners or not isinstance(result, DiffResult):
            continue
        if not result.scope_to_public_surface:
            continue
        moved = dispose_unreached_type_findings(result, owners)
        if not moved:
            continue
        moved_total += len(moved)
        _restamp(
            entry,
            result,
            severity_config=severity_config,
            require_complete_analysis=require_complete_analysis,
            contract_evaluation=contract_evaluation,
            output_dir=output_dir,
        )
    return moved_total


def _restamp(
    entry: dict[str, object],
    result: DiffResult,
    *,
    severity_config: SeverityConfig | None,
    require_complete_analysis: bool,
    contract_evaluation: bool,
    output_dir: Path | None,
) -> None:
    from ...cli_audit import ledger_lines_for
    from ...report.release_member_summary import add_member_review_summary
    from ...report_summary import build_summary
    from ...reporter import disposition_ledger_blocks, to_json
    from ...workflows.gate import evaluate
    from ...workflows.member_compare import stamp_member_exit_contributions
    from .runtime import _safe_write_output

    summary = build_summary(result)
    classified = evaluate(result)
    entry["verdict"] = result.verdict.value
    entry["breaking"] = len(classified.breaking)
    entry["source_breaks"] = len(classified.source_breaks)
    entry["risk_changes"] = len(classified.risk)
    entry["compatible_additions"] = summary.compatible_additions
    entry["quality_issues"] = summary.quality_issues
    entry["informational"] = summary.informational
    add_member_review_summary(entry, result, severity_config)
    entry.update(disposition_ledger_blocks(result))
    stamp_member_exit_contributions(
        entry,
        result,
        severity_config,
        require_complete_analysis=require_complete_analysis,
        contract_evaluation=contract_evaluation,
    )
    library = str(entry.get("library", ""))
    entry["filtered_internal_count"] = result.out_of_surface_count
    count = result.out_of_surface_count
    entry["_scope_ledger_text"] = "\n".join(
        [
            f"\nFiltered as non-public ABI surface in {library} ({count} "
            f"{'finding' if count == 1 else 'findings'}):",
            *ledger_lines_for(
                result.out_of_surface_changes,
                contract_evaluation=contract_evaluation,
            ),
        ]
    )
    if output_dir is not None:
        _safe_write_output(
            output_dir / f"{Path(library).stem}.json",
            to_json(
                result,
                severity_config=severity_config,
                require_complete_analysis=require_complete_analysis,
            ),
        )
