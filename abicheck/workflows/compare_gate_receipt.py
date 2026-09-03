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

"""``classify_compare_pair``'s gate-receipt installer, split into its own
``workflows`` leaf module (coordinating ``compare`` behavior, per
``abicheck/workflows/AGENTS.md`` -- the same routing ``scan_gate_options.py``
follows for its ``scan`` counterpart) both because that is the ADR-061
responsibility-package home for this kind of coordination and to keep
``service_compare_pipeline.py`` under the ``new-file-size`` gate's 800-line
production cap (that module carries no ``no_growth`` debt entry, unlike a
pre-ADR-061 legacy file -- growing it past the cap is a hard error, not a
reviewed baseline bump).

Round-6 review (Codex, fresh evidence, PR #1032): ``classify_compare_pair``
resolves a :class:`~abicheck.policy.release_gate_options.GateOptions` from
the request's ``severity_preset``/``exit_code_scheme`` and scores
``CompareResult.exit_decision`` from it, but until this fix never installed
that same gate onto
``result.contract_context.evaluation_context.resolved_config`` -- so a
request combining ``contract_evaluation=True`` with a non-default gate
persisted a context whose resolved config still described
``checker.compare``'s own built-in defaults, disagreeing with the exit
decision actually computed.

Mirrors :func:`abicheck.cli_compare_receipt.record_resolved_config` (the
native ``compare`` CLI's identical call), made unreachable for this typed-
API path since that module is a ``cli_*`` sibling the engine-layer compare
pipeline may not import (``engine-cli-boundary``) -- the same primitives
(:mod:`abicheck.contract_context`) are used directly here instead of a
second, diverging implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..api_types import CompareRequest
    from ..policy.release_gate_options import GateOptions
    from ..policy_file import PolicyFile
    from ..suppression import SuppressionList


def install_resolved_gate_receipt(
    result: Any,
    request: CompareRequest,
    gate: GateOptions,
    policy_file: PolicyFile | None,
    suppression: SuppressionList | None,
    effective_scheme: str,
) -> None:
    """Install *request*'s resolved gate onto *result* in place.

    *effective_scheme* is the exact ``"severity"``/``"legacy"`` string the
    caller already resolved and scored ``exit_decision`` with (never
    ``gate.exit_code_scheme`` itself, which can still be the unresolved
    ``"auto"`` -- `GateConfig` only accepts ``"legacy"``/``"severity"``, so
    installing the raw value here raised `ValueError` for a valid `auto`
    request after the comparison had already completed, Codex review, fresh
    evidence).

    *suppression* is the already-loaded `SuppressionList` `classify_compare_
    pair` scored the comparison with -- passed through rather than left for
    this adapter to re-read `request.suppress` a second time, which could
    digest a different file than the one that actually scored the findings
    if it changed between the two reads (Codex review, fresh evidence).

    Always stamps ``result.evaluation_config`` (a request with no
    ``--contract`` equivalent never builds a ``PersistedContractContext``,
    so ``effective_config_digest``'s rich tier would otherwise be silently
    unreachable for it even though *config* is a real, fully-resolved
    ``CompatibilityEvaluationConfig`` -- same reasoning as
    ``record_resolved_config``'s own leading comment); additionally merges
    the gate into ``result.contract_context`` when one exists.
    """
    from ..compatibility_evaluation_frontend import (
        EXIT_CODE_SCHEME_FIELD,
        SEVERITY_CATEGORY_FIELDS,
        SuppressionSource,
        compatibility_config_from_compare_request,
    )

    config = compatibility_config_from_compare_request(
        request,
        policy_file=policy_file,
        suppression=SuppressionSource.from_loaded(suppression, path=request.suppress),
    )
    result.evaluation_config = config

    # `DiffResult.contract_context` is deliberately typed `object | None`
    # (not `PersistedContractContext | None`) so `checker_types.py` (a
    # `model`-classified module) never has to import `contract_evidence.py`
    # -- the same reason this function checks presence via `is None` rather
    # than `isinstance(...)` against that class: `contract_evidence.py` is
    # itself unclassified in `architecture/modules.yaml`, and this module,
    # physically inside `workflows/`, is strictly checked against that
    # registry (unlike `contract_context.py`, a `workflows`-classified flat
    # file that isn't physically under the package and so isn't). By
    # construction the only two values this field ever holds are `None` and
    # a real `PersistedContractContext` (`contract_context.build_persisted_
    # context`'s own return type) -- nothing else ever assigns it.
    ctx = getattr(result, "contract_context", None)
    if ctx is None:
        return

    from ..contract_context import with_resolved_config, with_resolved_gate
    from ..policy.severity import resolve_severity_config

    ctx = with_resolved_config(ctx, config)
    # `gate.severity` is `None` under an explicit legacy scheme
    # (`resolve_release_gate_options`'s own contract), but
    # `with_resolved_gate` -- like `ResolvedCompareConfig.severity`, the
    # native CLI's equivalent -- records the receipt's severity
    # configuration unconditionally; re-resolve the same preset with no
    # scheme gating so a legacy-scheme run still receives a real
    # `SeverityConfig` rather than none at all.
    severity_for_receipt = (
        gate.severity
        if gate.severity is not None
        else resolve_severity_config(preset=gate.severity_preset)
    )
    result.contract_context = with_resolved_gate(
        ctx,
        exit_code_scheme=effective_scheme,
        severity=severity_for_receipt,
        scheme_provenance=config.provenance[EXIT_CODE_SCHEME_FIELD],
        severity_provenance={
            category: config.provenance[SEVERITY_CATEGORY_FIELDS[category]]
            for category in SEVERITY_CATEGORY_FIELDS
        },
    )
