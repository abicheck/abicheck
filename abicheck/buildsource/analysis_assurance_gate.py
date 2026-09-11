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

"""Truthfulness gate for ``CheckSpec.analysis_assurance`` (product-gaps
audit, "First vertical slice" -- see
``docs/contribute/plans/product-gaps-2026-09-audit.md``).

``checks[].analysis.assurance`` was accepted as any non-empty identifier
string, forwarded verbatim through ``RunPlanCheck`` into the generated run
plan, and honored by nothing: the only analysis-completeness axis this
codebase actually wires into an exit code is
``compare``'s ``.abicheck.yml`` ``assurance.require_complete: true`` boolean
gate (``abicheck.analysis_assurance.AnalysisAssurance``/
``fold_analysis_assurance_exit`` -- config-only since the CLI's own former
``--require-complete-analysis`` flag was demoted, rulings.py deferred-option
followup) -- there is no graduated or otherwise distinct assurance-level
mechanism a declared check could select between. So a project author
writing e.g. ``analysis: {assurance: partial}`` got a structurally-valid
config that silently did nothing, ever -- exactly the "declared but
unhonored setting" failure the project-checks contract exists to prevent.

This module's own validation (below) still accepts only ``'complete'`` at
run-plan generation time. The Action layer used to translate a declared
``'complete'`` value into the CLI's own now-retired boolean flag; since that
flag is gone, ``actions/check-target/action.yml``'s
``analysis-assurance-complete`` input is the config-overlay replacement (the
way ``dso-only``/``fail-on-removed-library`` already have one): it merges an
``assurance: {require_complete: true}`` fragment into whatever build-config
the internal analysis step reads, the same effect a project author's own
``.abicheck.yml`` line would have. ``.github/workflows/check-project.yml``
forwards ``checks[].analysis.assurance == 'complete'`` into that input for
every matrix cell, so a declared value is enforced end-to-end again. A
caller of ``check-target`` directly (bypassing ``check-project.yml``) must
set ``analysis-assurance-complete`` itself -- this module has no way to
reach an Action input from here, so that remains the one caller-side step
this validation cannot perform on its own behalf.

Split into its own leaf module (rather than living inline in
``project_targets.py``) purely to respect that file's ``architecture/
debt.yaml`` ``no_growth`` baseline -- see that file's own header comment.
"""

from __future__ import annotations

#: The only value with a real, wired enforcement mechanism today (see the
#: module docstring). ``_check_issues`` rejects any other non-empty value
#: before a run plan is even generated -- never silently accepted.
SUPPORTED_ANALYSIS_ASSURANCE_VALUES = frozenset({"complete"})


def analysis_assurance_issues(value: str, *, where: str) -> list[str]:
    """Validation issues for one ``checks[].analysis.assurance`` value.

    Empty (not declared) always yields no issues. A non-empty value outside
    :data:`SUPPORTED_ANALYSIS_ASSURANCE_VALUES` yields one actionable issue
    naming the one supported value and the gate it maps onto -- a typed,
    pre-extraction rejection rather than a silent accept-and-ignore.
    """
    if not value or value in SUPPORTED_ANALYSIS_ASSURANCE_VALUES:
        return []
    return [
        f"{where}: analysis.assurance {value!r} is not a supported assurance "
        "level -- no gate in this codebase enforces any value other than "
        f"{sorted(SUPPORTED_ANALYSIS_ASSURANCE_VALUES)!r} (which maps onto "
        "`compare`'s `.abicheck.yml` `assurance.require_complete: true` "
        "gate). Declaring an unsupported value would be silently accepted "
        "and never honored by any check execution, report, or gate -- "
        "remove it or use a supported value."
    ]
