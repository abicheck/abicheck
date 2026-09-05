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

"""The one place a frontend gets its process response.

ADR-061 Phase 4 item 4 -- "derive every frontend's process response from the
same ``GateDecision``" -- made executable. A frontend translates already-
validated input into a workflow request and a workflow result into a process
response; deciding *what* that response is belongs to the policy layer, which
the dependency rules say a frontend may not reach into directly
(``frontends -> policy`` is forbidden; ``workflows -> policy`` is not).

That rule is not bureaucracy here. Three separate axes feed one exit code --
the compatibility verdict, the ADR-049 contract-coverage floor, and the
assurance floor -- and each has its own diagnostic. A frontend that imported
them separately would be free to fold two of the three and forget the
orthogonal one, which is precisely the class of bug
``contract_coverage_exit``'s own module docstring exists to prevent. Routing
them through one workflow-layer surface means a new frontend inherits the
whole decision or none of it.

Re-export only: every name below keeps its own owner's definition and
semantics. Nothing is re-implemented here, so there is no second opinion to
drift, and ``severity``/``contract_coverage_exit``/``analysis_assurance``/
``exit_decision``/``release_gate_options`` remain the modules to read and to
change. ``GateOptions``/``resolve_release_gate_options``/
``apply_release_gate_pack`` (ADR-064) are the directory/package release
fan-out's own severity/exit-code-scheme resolution -- reached this way
because the fan-out's frontend module (``cli_compare_release_helpers.py``)
may not import ``policy`` directly (``frontends -> policy`` is forbidden,
same rule as every other name here).

``gate_exit_code_scheme``/``fold_gate_pack_severity``/
``GATE_SEVERITY_CATEGORIES`` (duplication-and-convergence-assessment T6) are
the one gate-algorithm derivation and the one gate-pack severity fold, owned
by ``policy/gate_pack_fold.py`` and re-exported here for the same
``frontends -> policy`` reason as the release-gate names above.

``note_if_same_binary_compared`` (Codex review) is not an exit-code axis, but
it belongs here rather than in ``extraction.py`` for the same reason as the
axes above: it decides part of the *process response* a completed comparison
returns (a coverage warning surfaced in every report format), not an
operation performed on an input before extraction -- which is exactly what
``extraction.py``'s own docstring scopes that module to.
"""

from __future__ import annotations

from ..analysis_assurance import (
    analysis_assurance_exit_contribution,
    analysis_assurance_report_dict,
    assurance_floor_diagnostic,
    compute_analysis_assurance,
    fold_analysis_assurance_exit,
)
from ..confidence import note_if_same_binary_compared
from ..policy.contract_coverage_exit import (
    announce_coverage_floor,
    coverage_diagnostic_from_summary,
    coverage_exit_floor,
    coverage_exit_for_context,
    fold_coverage_exit,
)
from ..policy.exit_decision import ExitDecision, resolve_compare_exit_decision
from ..policy.exit_decision_precedence import (
    resolve_release_exit_decision,
    resolve_release_exit_decision_for_report,
)
from ..policy.gate_decision import gate_decision_for_result
from ..policy.gate_pack_fold import (
    GATE_SEVERITY_CATEGORIES,
    fold_gate_pack_severity,
    gate_exit_code_scheme,
)
from ..policy.outcome import ScopeCompleteness
from ..policy.release_gate_options import (
    GateOptions,
    _resolve_release_severity_config as _resolve_release_severity_config,  # re-exported, ADR-064 (private; __all__ excludes it by convention)
    apply_release_gate_pack,
    resolve_release_gate_options,
)
from ..policy.scope_completeness import (
    INCOMPLETE_SCOPE_POLICIES,
    ScopeDecision,
    incomplete_scope_diagnostic,
    incomplete_scope_exit_contribution,
    no_comparison_completed_exit_contribution,
    resolve_scope_decision,
    scope_completeness_for_record,
    validate_incomplete_scope_policy,
)
from ..policy.severity import (
    PRESET_DEFAULT,
    IssueCategory,
    SeverityConfig,
    SeverityLevel,
    categorize_changes,
    classify_change_object,
    compute_exit_code,
    compute_gate_decision,
    legacy_exit_code,
    missing_contract_exit_code,
    resolve_severity_config,
)

__all__ = [
    "ExitDecision",
    "GATE_SEVERITY_CATEGORIES",
    "GateOptions",
    "INCOMPLETE_SCOPE_POLICIES",
    "IssueCategory",
    "PRESET_DEFAULT",
    "ScopeCompleteness",
    "ScopeDecision",
    "SeverityConfig",
    "SeverityLevel",
    "analysis_assurance_exit_contribution",
    "analysis_assurance_report_dict",
    "announce_coverage_floor",
    "apply_release_gate_pack",
    "assurance_floor_diagnostic",
    "categorize_changes",
    "classify_change_object",
    "compute_analysis_assurance",
    "compute_exit_code",
    "compute_gate_decision",
    "coverage_diagnostic_from_summary",
    "coverage_exit_floor",
    "coverage_exit_for_context",
    "fold_analysis_assurance_exit",
    "fold_coverage_exit",
    "fold_gate_pack_severity",
    "gate_decision_for_result",
    "gate_exit_code_scheme",
    "incomplete_scope_diagnostic",
    "incomplete_scope_exit_contribution",
    "legacy_exit_code",
    "missing_contract_exit_code",
    "no_comparison_completed_exit_contribution",
    "note_if_same_binary_compared",
    "resolve_compare_exit_decision",
    "resolve_release_exit_decision",
    "resolve_release_exit_decision_for_report",
    "resolve_release_gate_options",
    "resolve_scope_decision",
    "resolve_severity_config",
    "scope_completeness_for_record",
    "validate_incomplete_scope_policy",
]
