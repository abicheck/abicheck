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

"""ADR-049 Phase 5: ``scan --against``'s own resolved configuration.

The third and last front end, after :mod:`abicheck.cli_compare_receipt` (the
native ``compare`` CLI) and :mod:`abicheck.mcp_compare_receipt` (the MCP
tool). Phase 5's own sentence is "route both direct compare and scan
baseline compare through the same core **and same typed config**"; routing
through the same core landed first (``l0_export_delta``, then the shared
``compare_snapshots`` call), and this closes the other half.

Two things were missing, and the second is why fixing only the first would
have been invisible:

1. **No config was resolved.** ``checker.compare`` builds its context from
   the arguments it was handed, so every field could claim no more than
   ``API_REQUEST`` -- honest, but useless to an audit consumer asking
   *which* layer selected a value. ``compare`` and the MCP tool were fixed
   earlier in this phase; ``scan --against`` still reported the core verb's
   reconstruction.
2. **No context was emitted.** ``scan --against --contract-evaluation``
   stamped each finding's contract decision but its JSON payload carried no
   ``contract_context`` block at all, so the receipt those decisions rest on
   -- the observed provider evidence, the resolved configuration, the
   decision receipt -- was computed and then dropped. Resolving a config
   into a block nobody serializes would have changed nothing observable.

**What this front end can state**, since a receipt may only name inputs that
exist. ``scan`` shares ``compare``'s policy/suppression/scope surface
(``--policy``/``--policy-file``/``--suppress``/``--scope-public-headers``/
``--public-symbol``/``--public-symbols-list``/``--contract``), so those
resolve from real flags with real D7 layers. It has **no severity or
exit-code-scheme flags** -- a scan's exit code follows its verdict -- so
those fields resolve as built-in defaults. That is accurate rather than an
under-claim, and it is the same shape the MCP receipt documents for its own
missing parameters: nothing was passed, so nobody chose.

A **leaf**, like its two siblings: it imports nothing from ``cli_scan`` or
``cli_scan_baseline``, so no cycle forms. "Which flags did the user really
type" is the caller's question (it holds the Click context), so the answer
arrives here as data rather than this module reaching back for it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

#: The ``scan`` parameters that feed the resolver, in the spelling
#: ``scan_cmd`` gives them. A caller passing a partial mapping is rejected
#: rather than letting a dropped key resolve as "not stated" -- the same
#: guard ``cli_compare_receipt.COMPARE_CONFIG_PARAMS`` carries, for the same
#: reason: a renamed option should fail loudly, not silently change a
#: receipt's meaning.
SCAN_CONFIG_PARAMS: tuple[str, ...] = (
    "policy",
    "policy_file_path",
    "suppress",
    "scope_public_headers",
    "public_symbols",
    "public_symbols_list",
    "contract_mode",
)

#: How a :class:`~abicheck.service_scan.ScanRequest` spells the inputs whose
#: default API name comes from :class:`~abicheck.api_types.CompareRequest`.
#: "The API" is not one namespace: resolving a ``ScanRequest`` at
#: ``FrontEnd.API`` alone still recorded ``scope_public``/``policy_file_path``/
#: ``suppress``, none of which that entity has, so a replay consumer could not
#: identify the input that produced the value (Codex review).
#:
#: Only the three that actually differ are listed -- ``policy``,
#: ``force_public_symbols``, and ``contract_mode`` are spelled identically on
#: both requests, and an unmapped field keeps its default spelling.
#: ``tests/test_scan_compare_parity.py`` pins every entry against
#: ``ScanRequest``'s real fields, so a renamed field fails there rather than
#: silently reintroducing a name nobody can replay.
SCAN_REQUEST_SPELLINGS: Mapping[str, str] = {
    "scope_public": "scope_to_public_surface",
    "policy_file_path": "policy_file",
    "suppress": "suppression",
}


def resolve_scan_config(
    params: Mapping[str, Any],
    *,
    typed: Collection[str],
    project_cfg: Any = None,
    project_path: Path | None = None,
    project_sha256: str | None = None,
    policy_file: Any = None,
    suppression: Any = None,
    suppress_path: Path | None = None,
    symbols_list: Any = None,
    front_end: Any = None,
) -> Any:
    """Resolve one :class:`CompatibilityEvaluationConfig` for this scan.

    *params* is ``scan_cmd``'s own parameter mapping; *typed* names the
    parameters Click reports the user actually typed, which matters for the
    ones whose click default is indistinguishable from a stated value.

    *front_end* defaults to the ``scan`` CLI. ``service_scan.run_scan``
    passes :attr:`FrontEnd.API`, because a receipt may only name inputs its
    caller really has: a ``ScanRequest`` sets ``policy`` and
    ``scope_to_public_surface`` as typed fields, and recording those as
    ``--policy``/``--scope-public-headers`` describes a command line nobody
    ran, so the receipt could not identify the input that selected the
    value (Codex review). Only the selector *spelling* and layer differ --
    the normalization below is shared deliberately, see *params*.

    The normalization itself is :func:`compare_cli_inputs`, **reused rather
    than re-implemented**: ``scan``'s shared config surface deliberately uses
    the same option destinations as ``compare``'s (``policy``,
    ``policy_file_path``, ``suppress``, ``scope_public_headers``,
    ``public_symbols``, ``public_symbols_list``, ``contract_mode``), because
    §6.4's Gate is that the two commands agree -- and two front ends that
    normalize the same flags through two functions is exactly how they would
    stop agreeing. A second normalizer would also have to re-derive
    :data:`DEFAULTED_COMPARE_PARAMETERS`' typed-vs-defaulted rule, the
    load-once affordances for the policy file/suppression/symbols list, and
    the union rule for ``--public-symbol``/``--public-symbols-list``.

    *policy_file*/*suppression*/*symbols_list* are the same "pass what you
    already loaded" affordance ``compare`` uses, and for the same reason: the
    scan loaded all three long before configuration is resolved, so
    re-reading here could pair a digest with content that did not score the
    run, and a file deleted mid-scan would fail an otherwise-finished
    comparison.

    Raises whatever the canonical resolver raises (a D7 same-tier conflict,
    a D8 pack conflict); mapping those to a ``click.UsageError`` is the
    caller's job, exactly as it is for ``compare``.
    """
    from .compatibility_evaluation_frontend import (
        FrontEnd,
        ProjectCompatibilityInputs,
        SuppressionSource,
        compare_cli_inputs,
        resolve_compatibility_evaluation_config,
    )

    missing = [name for name in SCAN_CONFIG_PARAMS if name not in params]
    if missing:
        raise KeyError(
            "scan config params missing from the caller's mapping: "
            f"{', '.join(sorted(missing))}. Every entry of SCAN_CONFIG_PARAMS "
            "must be present, so a renamed option fails here rather than "
            "silently resolving as 'not stated'."
        )
    # From the single load the comparison itself used, so the digest always
    # describes the rules that scored the run -- and falling back to a
    # content digest of the rules when the list carries none, which an
    # in-memory `SuppressionList` always does (Codex review; the inline
    # version here took `""` and failed the whole scan).
    suppression_source = SuppressionSource.from_loaded(suppression, suppress_path)
    project = None
    if project_cfg is not None:
        project = _without_gate_settings(
            ProjectCompatibilityInputs.from_build_config(
                project_cfg,
                path=str(project_path) if project_path is not None else None,
                sha256=project_sha256,
            )
        )
    resolved_front_end = front_end if front_end is not None else FrontEnd.CLI
    return resolve_compatibility_evaluation_config(
        front_end=resolved_front_end,
        api_spellings=(
            SCAN_REQUEST_SPELLINGS if resolved_front_end is FrontEnd.API else None
        ),
        explicit=compare_cli_inputs(
            params,
            explicit_parameters=typed,
            policy_file=policy_file,
            suppression=suppression_source,
            public_symbols_list=symbols_list,
        ),
        project=project,
    )


def _without_gate_settings(project: Any) -> Any:
    """*project* with its gate fields blanked, for a front end that ignores them.

    A ``scan``'s exit code follows its compatibility verdict directly: it has
    no severity or exit-code-scheme flags, and it never consults the project
    config's ``severity``/``exit_code_scheme`` either. Passing them through
    anyway produced a receipt that *claimed* a gate the run did not use --
    an ``info-only`` preset in ``.abicheck.yml`` would be recorded with every
    level at ``info`` while the scan still exited 4 on a breaking diff
    (Codex review).

    Blanked rather than left out of the receipt entirely: the fields still
    resolve, to their built-in defaults, which is the true statement --
    nothing selected them for this run. That is the same answer the MCP
    receipt gives for the parameters ``abi_compare`` does not have.
    """
    if project is None:
        return None
    from dataclasses import replace

    return replace(
        project,
        exit_code_scheme=None,
        severity_preset=None,
        severity_abi_breaking=None,
        severity_potential_breaking=None,
        severity_quality_issues=None,
        severity_addition=None,
    )


def record_resolved_config(result: Any, config: Any) -> None:
    """Install *config* on *result*'s persisted context, if it has one.

    A no-op unless ``--contract-evaluation`` produced a context. Unlike
    ``compare``'s equivalent there is no gate half to reconcile: ``scan`` has
    no severity or exit-code-scheme flags, so the gate resolves entirely from
    built-in defaults and the "values from the run, provenance from the
    resolver" split those two front ends need does not arise here.
    """
    from .contract_evidence import PersistedContractContext

    ctx = getattr(result, "contract_context", None)
    if not isinstance(ctx, PersistedContractContext):
        return
    from .contract_context import with_resolved_config

    result.contract_context = with_resolved_config(ctx, config)


def context_block(result: Any) -> dict[str, Any] | None:
    """*result*'s persisted context, serialized, or ``None`` if it has none.

    Serialized through :mod:`abicheck.contract_context_io`, the same encoder
    ``reporter._add_contract_context`` uses, so the block ``scan`` writes is
    byte-for-byte the one ``compare`` writes and
    :func:`~abicheck.contract_replay.replay_original_decisions` reads back.
    A second, scan-local encoding is exactly how a round-trip guarantee stops
    holding.
    """
    from .contract_evidence import PersistedContractContext

    ctx = getattr(result, "contract_context", None)
    if not isinstance(ctx, PersistedContractContext):
        return None
    from .contract_context_io import persisted_context_to_dict

    return persisted_context_to_dict(ctx)
