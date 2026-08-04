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

"""ADR-049 Phase 5: the MCP ``abi_compare`` tool's own resolved configuration.

The sibling of :mod:`abicheck.cli_compare_receipt`, one front end over, and
the second half of D7's "every front end consumes one
:class:`CompatibilityEvaluationConfig`". Both hand their real inputs to the
same canonical resolver
(:func:`~abicheck.compatibility_evaluation_frontend.resolve_compatibility_evaluation_config`)
and install the result over the narrower object ``checker.compare``
reconstructs from its arguments; only the front-end layer differs, which is
exactly what :func:`~abicheck.compatibility_evaluation_frontend.cross_front_end_differences`
declares legitimate.

**What this tool can and cannot state**, since a receipt may only name inputs
that exist. ``abi_compare`` takes ``policy``/``policy_file``/
``suppression_file``, the four severity levels, and -- since G33 Phase 5 gave
it the argument -- ``contract_mode``, so those resolve from real arguments.
**That last one is why this paragraph had to change**: while the tool took no
``--contract`` equivalent, ``contract.mode`` honestly resolved as a built-in
default. Now a caller can select ``exports``/``all``, and since ADR-049 Phase 7
the selected domain decides which findings compatibility policy scores, so a
receipt still naming the default would misreport the domain that produced the
verdict and the coverage gate -- exactly what a replay/audit consumer reads it
for (Codex review).

It still takes no public-symbol or exit-code-scheme parameter -- so unlike
``CompareRequest.scope_public``, whose dataclass
default is still a caller's choice, there is nothing for a caller to have
chosen there and those fields resolve as built-in defaults. That is not an
under-claim: ``service.compare_snapshots``' own ``scope_to_public_surface``
default is what runs, and it agrees with
``BUILT_IN_DEFAULT_CONTRACT_MODE`` by construction when no mode is stated.

It *does* take two consumer-scope arguments, ``used_by`` and
``required_symbols``, and those are a genuine under-claim rather than an
honest default -- see :func:`resolve_tool_config` for the recorded gap.

Its own module rather than more lines in ``mcp_server.py``: that file is
already allowlisted as oversized, and this is one cohesive concern with one
caller. A **leaf** -- nothing imports back into the server.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The four :class:`~abicheck.severity.SeverityConfig` categories, in the
#: spelling the resolver keys its ``gate.severity.<category>`` entries by.
_SEVERITY_CATEGORIES = (
    "abi_breaking",
    "potential_breaking",
    "quality_issues",
    "addition",
)


def resolve_tool_config(
    *,
    policy: str,
    policy_file: Any = None,
    policy_file_path: Any = None,
    suppression: Any = None,
    suppression_path: Any = None,
    severity_preset: str | None = None,
    severity_abi_breaking: str | None = None,
    severity_potential_breaking: str | None = None,
    severity_quality_issues: str | None = None,
    severity_addition: str | None = None,
    contract_mode: str | None = None,
) -> Any:
    """Resolve one :class:`CompatibilityEvaluationConfig` for this call.

    *policy* is read as stated, matching
    :func:`~abicheck.compatibility_evaluation_frontend.compare_request_inputs`:
    the typed surface has no "unset" for it, so a caller accepting the
    default still chose it.

    **The tool's two consumer-scope arguments are deliberately absent, and
    that is a known gap, not a design choice** (Codex review). ``used_by``
    and ``required_symbols`` are authoritative -- the scoping pass in
    ``mcp_server`` rewrites the verdict and exit code from them -- but no
    field of :class:`CompatibilityEvaluationConfig` models a consumer scope,
    so passing them here would have nowhere to land. A scoped call therefore
    resolves the same object an unscoped one does
    (``surface.explicit_scope`` ``None`` at ``BUILT_IN_DEFAULT``), and the
    gap is wider than "which scope": ``"scoped"`` is not a value
    ``GateConfig`` accepts, so :func:`record_resolved_config` records the
    *underlying* scheme instead. Nothing in the resolved config indicates a
    consumer scope was in effect **at all** -- a scoped run's config
    compares equal to an unscoped one's.

    ``compare --used-by`` has the same gap; ``--required-symbol`` is a
    partial exception on the CLI only, where the contract switches an
    untouched ``--policy`` to ``plugin_abi`` and ``policy_base_option``
    records that indirectly. This tool performs no such switch, so nothing
    traces it. Closing it needs a typed field plus an identity scheme for
    application binaries, shared with the comparison that already read them
    -- its own slice, on the same footing as the unclosed ``packs`` axis.

    Raises whatever the canonical resolver raises (a D7 same-tier conflict, a
    D8 pack conflict); mapping those to a tool error is the caller's job.
    """
    from .compatibility_evaluation_frontend import (
        ExplicitCompatibilityInputs,
        FrontEnd,
        SuppressionSource,
        resolve_compatibility_evaluation_config,
        stated_policy_base,
    )

    return resolve_compatibility_evaluation_config(
        front_end=FrontEnd.API,
        explicit=ExplicitCompatibilityInputs(
            policy_base=stated_policy_base(policy, policy_file),
            policy_file=policy_file,
            suppression=SuppressionSource.from_loaded(suppression, suppression_path),
            severity_preset=severity_preset,
            severity_abi_breaking=severity_abi_breaking,
            severity_potential_breaking=severity_potential_breaking,
            severity_quality_issues=severity_quality_issues,
            severity_addition=severity_addition,
            contract_mode=contract_mode,
        ),
    )


def record_resolved_config(
    result: Any,
    *,
    exit_code_scheme: str,
    severity_config: Any = None,
    policy: str = "strict_abi",
    policy_file: Any = None,
    policy_file_path: Any = None,
    suppression: Any = None,
    suppression_path: Any = None,
    severity: Mapping[str, str | None] | None = None,
    contract_mode: str | None = None,
) -> None:
    """Install this tool's resolved configuration onto the persisted context.

    A no-op unless ``contract_evaluation`` produced a context. Runs before
    the report is rendered, so ``response["report"]["contract_context"]``
    describes the configuration ``response["exit_code"]`` was computed under.

    The gate keeps the CLI's split, for the same reason: its *values* are
    what the tool actually scored with (a ``used_by``/``required_symbols``
    scope reports a ``"scoped"`` scheme, which is not one of the two
    resolved values :class:`GateConfig` accepts, so the scheme it resolved
    from is recorded instead), while its *provenance* comes from the
    canonical resolver.
    """
    from .contract_evidence import PersistedContractContext

    ctx = getattr(result, "contract_context", None)
    if not isinstance(ctx, PersistedContractContext):
        return
    from .compatibility_evaluation_frontend import (
        EXIT_CODE_SCHEME_FIELD,
        SEVERITY_CATEGORY_FIELDS,
    )
    from .contract_context import with_resolved_config, with_resolved_gate
    from .severity import SeverityConfig

    levels = severity or {}
    config = resolve_tool_config(
        policy=policy,
        policy_file=policy_file,
        policy_file_path=policy_file_path,
        suppression=suppression,
        suppression_path=suppression_path,
        severity_preset=levels.get("preset"),
        severity_abi_breaking=levels.get("abi_breaking"),
        severity_potential_breaking=levels.get("potential_breaking"),
        severity_quality_issues=levels.get("quality_issues"),
        severity_addition=levels.get("addition"),
        contract_mode=contract_mode,
    )
    scheme = exit_code_scheme
    if scheme == "scoped":
        scheme = getattr(result, "scoped_exit_code_scheme", "legacy")
    result.contract_context = with_resolved_gate(
        with_resolved_config(ctx, config),
        exit_code_scheme=scheme,
        severity=severity_config if severity_config is not None else SeverityConfig(),
        scheme_provenance=config.provenance[EXIT_CODE_SCHEME_FIELD],
        severity_provenance={
            category: config.provenance[SEVERITY_CATEGORY_FIELDS[category]]
            for category in _SEVERITY_CATEGORIES
        },
    )
