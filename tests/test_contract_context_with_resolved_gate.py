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
from abicheck.contract_relevance_types import ContractMode, SelectorLayer
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


def _apply(
    ctx: PersistedContractContext,
    *,
    require_complete_analysis: bool | None = None,
) -> PersistedContractContext:
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
        require_complete_analysis=require_complete_analysis,
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


class TestWithResolvedGateThreadsRequireCompleteAnalysis:
    """P2 (Codex review, fresh evidence on the require-complete-analysis
    retirement PR): before this fix, ``with_resolved_gate`` always fell back
    to the *pre-existing* ``config.gate.require_complete_analysis`` (the
    compatibility resolver's own built-in default, ``False``) regardless of
    what the front end actually resolved the field to -- so a ``--contract``
    compare with ``assurance.require_complete: true`` persisted a receipt
    where ``effective_config_fields["gate.require_complete_analysis"]``
    (sourced from the real resolved config) read ``True`` while
    ``evaluation_context.resolved_config.gate.require_complete_analysis``
    read ``False``, an internal inconsistency in the one receipt documented
    as the complete resolved configuration. The fix adds an explicit
    ``require_complete_analysis`` parameter the caller can pass its own
    resolved value through -- these tests exercise that parameter directly,
    independent of the pre-existing scoped-field-preservation tests above
    (which never pass it, and so must keep observing the fallback)."""

    def test_explicit_true_overrides_the_context_s_existing_false(self) -> None:
        ctx = _base_context(require_complete_analysis=False, scope=None)
        result = _apply(ctx, require_complete_analysis=True)
        assert (
            result.evaluation_context.resolved_config.gate.require_complete_analysis
            is True
        )

    def test_explicit_false_overrides_the_context_s_existing_true(self) -> None:
        ctx = _base_context(require_complete_analysis=True, scope=None)
        result = _apply(ctx, require_complete_analysis=False)
        assert (
            result.evaluation_context.resolved_config.gate.require_complete_analysis
            is False
        )

    def test_omitted_falls_back_to_the_context_s_existing_value(self) -> None:
        # The pre-existing fallback behavior (None is the default), which
        # every test above this class already exercises via `_apply`'s own
        # default -- restated here explicitly as this class's own baseline.
        ctx = _base_context(require_complete_analysis=True, scope=None)
        result = _apply(ctx, require_complete_analysis=None)
        assert (
            result.evaluation_context.resolved_config.gate.require_complete_analysis
            is True
        )


class TestWithResolvedGateStampsRequireCompleteAnalysisProvenance:
    """P2 (Codex review, fresh evidence after the fix above landed): the
    resolved *value* was threaded through, but ``field_provenance["gate.
    require_complete_analysis"]`` stayed absent -- the receipt could show
    *that* the gate was enabled but not *why*. Fixed by having
    ``with_resolved_gate`` itself stamp a ``PROJECT_CONFIG``-layer entry
    whenever an explicit ``True`` is passed -- that field has no CLI
    override and no pack route, so ``True`` can only ever have come from
    ``.abicheck.yml``'s ``assurance.require_complete``."""

    def test_provenance_entry_is_stamped_when_true(self) -> None:
        ctx = _base_context(require_complete_analysis=False, scope=None)
        result = _apply(ctx, require_complete_analysis=True)
        provenance = result.evaluation_context.resolved_config.provenance
        entry = provenance["gate.require_complete_analysis"]
        assert entry.layer is SelectorLayer.PROJECT_CONFIG
        assert entry.field_location == "assurance.require_complete"

    def test_false_leaves_the_field_absent(self) -> None:
        """The "absent, not defaulted" rule: an unset/false field gets no
        fabricated provenance entry, mirroring an unsupplied severity
        category."""
        ctx = _base_context(require_complete_analysis=False, scope=None)
        result = _apply(ctx, require_complete_analysis=False)
        provenance = result.evaluation_context.resolved_config.provenance
        assert "gate.require_complete_analysis" not in provenance

    def test_omitted_leaves_the_field_absent(self) -> None:
        """Same rule for the omitted (``None``, fallback-to-context) case."""
        ctx = _base_context(require_complete_analysis=False, scope=None)
        result = _apply(ctx, require_complete_analysis=None)
        provenance = result.evaluation_context.resolved_config.provenance
        assert "gate.require_complete_analysis" not in provenance
