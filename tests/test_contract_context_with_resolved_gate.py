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

"""``contract_context.with_resolved_gate`` -- direct unit coverage.

Codex review, PR #817: when Phase 2 item 1 (dedup-and-convergence plan)
added ``GateConfig.require_complete_analysis``/``scope``,
``with_resolved_gate`` kept reconstructing ``GateConfig`` from only its
pre-existing fields (``exit_code_scheme``/``preset``/``packs``/``severity``),
silently resetting both new fields to their defaults on every call --
losing gate-affecting inputs from the persisted receipt the function's own
docstring says must be complete.
"""

from __future__ import annotations

import dataclasses

from abicheck.compatibility_evaluation_config import (
    CompatibilityEvaluationConfig,
    GateConfig,
    ScopedGateSelection,
)
from abicheck.contract_context import build_persisted_context, with_resolved_gate
from abicheck.contract_evidence import ContractEvidenceBlock, PersistedContractContext
from abicheck.contract_relevance_types import ContractMode
from abicheck.severity import SeverityConfig, SeverityLevel


def _base_context(
    *, require_complete_analysis: bool, scope: ScopedGateSelection | None
) -> PersistedContractContext:
    ctx = build_persisted_context(ContractEvidenceBlock(), mode=ContractMode.PUBLIC)
    config: CompatibilityEvaluationConfig = ctx.evaluation_context.resolved_config
    return dataclasses.replace(
        ctx,
        evaluation_context=dataclasses.replace(
            ctx.evaluation_context,
            resolved_config=dataclasses.replace(
                config,
                gate=GateConfig(
                    require_complete_analysis=require_complete_analysis, scope=scope
                ),
            ),
        ),
    )


def _apply(ctx: PersistedContractContext) -> PersistedContractContext:
    return with_resolved_gate(
        ctx,
        exit_code_scheme="legacy",
        severity=SeverityConfig(
            abi_breaking=SeverityLevel.ERROR,
            potential_breaking=SeverityLevel.ERROR,
            quality_issues=SeverityLevel.WARNING,
            addition=SeverityLevel.INFO,
        ),
        severity_provenance={},
    )


class TestWithResolvedGatePreservesScopedFields:
    def test_require_complete_analysis_true_is_preserved(self) -> None:
        ctx = _base_context(require_complete_analysis=True, scope=None)
        result = _apply(ctx)
        gate = result.evaluation_context.resolved_config.gate
        assert gate.require_complete_analysis is True

    def test_require_complete_analysis_false_is_preserved(self) -> None:
        ctx = _base_context(require_complete_analysis=False, scope=None)
        result = _apply(ctx)
        gate = result.evaluation_context.resolved_config.gate
        assert gate.require_complete_analysis is False

    def test_scope_is_preserved(self) -> None:
        scope = ScopedGateSelection(kind="required_symbol", targets=("plugin_init",))
        ctx = _base_context(require_complete_analysis=False, scope=scope)
        result = _apply(ctx)
        assert result.evaluation_context.resolved_config.gate.scope == scope

    def test_none_scope_stays_none(self) -> None:
        ctx = _base_context(require_complete_analysis=False, scope=None)
        result = _apply(ctx)
        assert result.evaluation_context.resolved_config.gate.scope is None

    def test_exit_code_scheme_and_severity_are_still_the_real_fix_this_function_makes(
        self,
    ) -> None:
        # Negative control: the function's own original purpose (recording the
        # front end's real gate scheme/severity, not a default GateConfig())
        # must keep working alongside the scoped-field preservation.
        ctx = _base_context(require_complete_analysis=True, scope=None)
        result = _apply(ctx)
        gate = result.evaluation_context.resolved_config.gate
        assert gate.exit_code_scheme == "legacy"
        assert gate.severity.abi_breaking == SeverityLevel.ERROR
