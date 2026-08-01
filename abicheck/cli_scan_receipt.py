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
    "pack_paths",
)


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
) -> Any:
    """Resolve one :class:`CompatibilityEvaluationConfig` for this scan.

    *params* is ``scan_cmd``'s own parameter mapping; *typed* names the
    parameters Click reports the user actually typed, which matters for the
    ones whose click default is indistinguishable from a stated value.

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
    suppression_source = None
    if suppression is not None:
        suppression_source = SuppressionSource(
            path=str(suppress_path) if suppress_path is not None else None,
            # From the single load the comparison itself used, so the digest
            # always describes the rules that scored the run.
            sha256=getattr(suppression, "source_sha256", None) or "",
            rules=tuple(suppression.rule_identities()),
        )
    project = None
    if project_cfg is not None:
        project = ProjectCompatibilityInputs.from_build_config(
            project_cfg,
            path=str(project_path) if project_path is not None else None,
            sha256=project_sha256,
        )
    return resolve_compatibility_evaluation_config(
        front_end=FrontEnd.CLI,
        explicit=compare_cli_inputs(
            params,
            explicit_parameters=typed,
            policy_file=policy_file,
            suppression=suppression_source,
            public_symbols_list=symbols_list,
        ),
        project=project,
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
