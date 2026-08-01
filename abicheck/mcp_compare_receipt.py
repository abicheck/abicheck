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
``suppression_file`` and the four severity levels, so those resolve from real
arguments. It takes no scope, contract-mode, public-symbol, or exit-code-scheme
parameter at all -- so unlike ``CompareRequest.scope_public``, whose dataclass
default is still a caller's choice, there is nothing for a caller to have
chosen here and those fields resolve as built-in defaults. That is not an
under-claim: ``service.compare_snapshots``' own ``scope_to_public_surface``
default is what runs, and it agrees with
``BUILT_IN_DEFAULT_CONTRACT_MODE`` by construction.

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


def _suppression_source(suppression: Any, path: Any) -> Any:
    """The already-loaded ``suppression_file``, as a resolver input.

    Built from the list the run really used rather than re-reading *path* --
    the single-read rule every file-derived receipt entry in this codebase
    follows, so the digest cannot describe content that did not score the
    run (see ``cli_compare_receipt._suppression_source``, which this
    mirrors).
    """
    if suppression is None:
        return None
    from .compatibility_evaluation_frontend import SuppressionSource

    return SuppressionSource(
        path=str(path) if path is not None else None,
        sha256=getattr(suppression, "source_sha256", None) or "",
        rules=tuple(suppression.rule_identities()),
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
) -> Any:
    """Resolve one :class:`CompatibilityEvaluationConfig` for this call.

    *policy* is read as stated, matching
    :func:`~abicheck.compatibility_evaluation_frontend.compare_request_inputs`:
    the typed surface has no "unset" for it, so a caller accepting the
    default still chose it.

    Raises whatever the canonical resolver raises (a D7 same-tier conflict, a
    D8 pack conflict); mapping those to a tool error is the caller's job.
    """
    from .compatibility_evaluation_frontend import (
        ExplicitCompatibilityInputs,
        FrontEnd,
        resolve_compatibility_evaluation_config,
    )

    return resolve_compatibility_evaluation_config(
        front_end=FrontEnd.API,
        explicit=ExplicitCompatibilityInputs(
            policy_base=policy,
            policy_file=policy_file,
            suppression=_suppression_source(suppression, suppression_path),
            severity_preset=severity_preset,
            severity_abi_breaking=severity_abi_breaking,
            severity_potential_breaking=severity_potential_breaking,
            severity_quality_issues=severity_quality_issues,
            severity_addition=severity_addition,
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
