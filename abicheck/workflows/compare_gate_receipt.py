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
the request's ``severity_preset`` and scores ``CompareResult.exit_decision``
from it, but until this fix never installed that same gate onto
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

*policy_file* (Codex review, fresh evidence, three rounds over): the caller
passes the same pack-folded ``PolicyFile`` it scores the comparison with,
keeping that file's own ``source_path``/``source_sha256`` (round 3: an
intermediate revision cleared them whenever a pack contributed, which threw
away the file's own real identity along with avoiding a false claim over
the pack's). :func:`_with_pack_forwarded_provenance` closes the actual gap
instead: ``CompareRequest.pack_policy_overrides``/``pack_internal_namespaces``
carry already-resolved values with no pack-manifest path to attribute an
identity to (unlike a real ``--pack <path>`` manifest, which
``compatibility_evaluation_frontend._overrides_provenance`` already
represents correctly via a ``selected_by`` entry per contributing pack) --
so this appends one more ``selected_by`` hop naming the forwarded
contribution honestly, by request field rather than by file, instead of
either crediting or discarding the real file's own provenance to
compensate for it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..contract_relevance_types import SelectorLayer

if TYPE_CHECKING:
    from ..api_types import CompareRequest
    from ..compatibility_evaluation_config import CompatibilityEvaluationConfig
    from ..policy.release_gate_options import GateOptions
    from ..policy_file import PolicyFile
    from ..suppression import SuppressionList


def install_resolved_gate_receipt(
    result: Any,
    request: CompareRequest,
    gate: GateOptions,
    policy_file: PolicyFile | None,
    suppression: SuppressionList | None,
) -> None:
    """Install *request*'s resolved gate onto *result* in place.

    Installs ``gate.exit_code_scheme`` directly -- since CLI cleanup phase
    two PR G2 deleted the manual algorithm selector, `GateOptions.
    exit_code_scheme` is unconditionally already ``"legacy"``/``"severity"``
    (never an unresolved ``"auto"``, which `GateConfig` never accepted and
    used to require the caller to resolve separately before calling this;
    see this repo's git history for the account of that now-moot gap).

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
        SEVERITY_CATEGORY_FIELDS,
        SuppressionSource,
        compatibility_config_from_compare_request,
    )

    config = compatibility_config_from_compare_request(
        request,
        policy_file=policy_file,
        suppression=SuppressionSource.from_loaded(suppression, path=request.suppress),
    )
    if request.pack_policy_overrides or request.pack_internal_namespaces is not None:
        config = _with_pack_forwarded_provenance(config, request)
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
        exit_code_scheme=gate.exit_code_scheme,
        severity=severity_for_receipt,
        severity_provenance={
            category: config.provenance[SEVERITY_CATEGORY_FIELDS[category]]
            for category in SEVERITY_CATEGORY_FIELDS
        },
    )


def _with_pack_forwarded_provenance(
    config: CompatibilityEvaluationConfig, request: CompareRequest
) -> CompatibilityEvaluationConfig:
    """*config* with an honest record of a forwarded pack's contribution.

    ``policy.overrides`` is additive (D8), so a request's own
    ``pack_policy_overrides`` gets one more ``selected_by`` hop appended
    (``compatibility_evaluation_frontend._overrides_provenance``'s own
    pattern for a real ``--pack`` manifest, extended to a contributor with
    no manifest path to name) -- the file's own path/sha256 stay, since its
    own entries genuinely are a subset of the merged mapping.
    ``surface.internal_namespaces`` is not additive: a pack that sets it
    *replaces* the file's value outright, so crediting the file's path/
    sha256 there would be false whenever a pack actually did -- that
    provenance entry is replaced outright instead of extended.
    """
    from ..compatibility_evaluation_config import SelectedByEntry, ValueProvenance
    from ..compatibility_evaluation_frontend import POLICY_OVERRIDES_FIELD
    from ..compatibility_evaluation_wiring import INTERNAL_NAMESPACES_FIELD

    provenance = dict(config.provenance)
    if request.pack_policy_overrides:
        prior = provenance.get(POLICY_OVERRIDES_FIELD)
        if prior is not None:
            provenance[POLICY_OVERRIDES_FIELD] = replace(
                prior,
                selected_by=(
                    *prior.selected_by,
                    SelectedByEntry(
                        layer=SelectorLayer.API_REQUEST,
                        option="pack_policy_overrides",
                    ),
                ),
            )
    if request.pack_internal_namespaces is not None:
        provenance[INTERNAL_NAMESPACES_FIELD] = ValueProvenance(
            layer=SelectorLayer.API_REQUEST,
            source_kind="api_request",
            selected_by=(
                SelectedByEntry(
                    layer=SelectorLayer.API_REQUEST,
                    option="pack_internal_namespaces",
                ),
            ),
        )
    return replace(config, provenance=provenance)
