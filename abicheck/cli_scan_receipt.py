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
2. **No context was emitted.** ``scan --against --contract``
   stamped each finding's contract decision but its JSON payload carried no
   ``contract_context`` block at all, so the receipt those decisions rest on
   -- the observed provider evidence, the resolved configuration, the
   decision receipt -- was computed and then dropped. Resolving a config
   into a block nobody serializes would have changed nothing observable.

**What this front end can state**, since a receipt may only name inputs that
exist. ``scan`` shares ``compare``'s policy/suppression/scope surface
(``--policy``/``--policy-file``/``--suppress``/``--scope-public-headers``/
``--public-symbol``/``--public-symbols-list``/``--contract``), so those
resolve from real flags with real D7 layers.

``scan --against`` *does* now accept ``--severity-preset``/
``--exit-code-scheme`` (mirroring ``compare``, since the fix that closed the
"scan never consults severity" gap documented in AGENTS.md's "Known gaps"),
and those flags really do drive a severity-scheme run's exit code -- see
:func:`abicheck.cli_scan_baseline._run_baseline_compare`. This ADR-049
receipt is still a **separate, narrower resolution** from the one that
actually scores the run (``cli_scan.scan_cmd``'s own ``resolve_compare_
config`` call), the same split ``compare`` avoids via one combined
``resolve_and_apply`` call -- unifying the two here needs the same
combined-resolution machinery ``compare`` has, not attempted in this module.
``severity_preset``/``exit_code_scheme`` **are** now in
:data:`SCAN_CONFIG_PARAMS`, and the project-config tier of the same two
fields (plus the four per-category ``severity_abi_breaking``/etc.) is no
longer blanked either (CLI cleanup phase two, "PR B" -- see
:func:`abicheck.pack_application.apply_to_compare_config`'s docstring for
why this matters: a ``kind: gate`` pack folded onto the real
``resolved_cfg`` must not override a value an explicit ``--severity-
preset``/``--exit-code-scheme`` or ``.abicheck.yml`` already stated, and
this resolver's D7 precedence is where "was it stated" is answered --
without these fields reaching it, every gate pack looked unopposed here
regardless of what the CLI or project config actually said, which is a
real precedence bug, not just an inaccurate receipt: Codex review on #801
reproduced the explicit-CLI case (a removed export with ``--severity-
preset strict`` and a ``gate.severity.abi_breaking: warning`` pack wrongly
exited 0), and the identical mechanism applies to a project-config-sourced
value). Both tiers are safe to include for ``scan`` specifically: unlike
``compare``, ``scan`` has no ``--profile`` option, so nothing sits between
"explicit CLI" and "project config" here that this resolver's simpler
precedence chain could miss, and both `ProjectCompatibilityInputs.
from_build_config` and `resolve_compare_config` read the identical six
fields off the identical ``project_cfg``/``cfg`` object -- so this
resolver's answer to "was severity/exit-code-scheme stated, and by what"
cannot disagree with what actually scores the run, and this is the
"leftover" reason for the `_without_gate_settings` history note further
down this module.

A **leaf**, like its two siblings: it imports nothing from ``cli_scan`` or
``cli_scan_baseline``, so no cycle forms. "Which flags did the user really
type" is the caller's question (it holds the Click context), so the answer
arrives here as data rather than this module reaching back for it.
"""

from __future__ import annotations

from typing import Any

#: The ``scan`` parameters that feed the resolver, in the spelling
# ADR-061 Phase 4: these describe the resolver's own input contract, so they
# moved with it. Re-exported here for the existing import sites.
from .workflows.scan_config import (  # noqa: E402
    SCAN_CONFIG_PARAMS as SCAN_CONFIG_PARAMS,
    SCAN_REQUEST_SPELLINGS as SCAN_REQUEST_SPELLINGS,
)


def resolve_scan_config(*args: Any, **kwargs: Any) -> Any:
    """Alias for ``workflows.scan_config.resolve_scan_config``.

    Moved to the engine in ADR-061 Phase 4: ``service_scan`` resolves the same
    config and had to import upward for it. This spelling stays for the CLI
    call sites and tests that use it.
    """
    from .workflows.scan_config import resolve_scan_config as _impl

    return _impl(*args, **kwargs)


#: History note, not a live function: this module used to run every
#: `ProjectCompatibilityInputs` through a `_without_gate_settings` helper
#: that blanked its six severity/exit-code-scheme fields, because this
#: receipt resolver (:func:`resolve_scan_config`) was not the one that
#: scored a scan's gate -- ``cli_scan.scan_cmd``'s own ``resolve_compare_
#: config`` call was, and the two could in principle disagree. CLI cleanup
#: phase two, "PR B" removed the blanking: `ProjectCompatibilityInputs.
#: from_build_config` and `resolve_compare_config` read the identical six
#: fields off the identical `project_cfg`/`cfg` object with the identical
#: `explicit CLI > project config > built-in default` precedence, and
#: `scan` has no `--profile` option (unlike `compare`) to introduce a tier
#: the other resolver doesn't know about -- so, for `scan` specifically,
#: the two resolutions cannot actually disagree on a project-config-sourced
#: value, and blanking it left a real precedence bug: a selected gate pack
#: folded onto the real `resolved_cfg` (`pack_application.
#: apply_to_compare_config`, called from `cli_scan._resolve_scan_
#: evaluation_config`) saw a project-config value as merely "unstated" and
#: silently overrode it -- the same class of bug Codex review found for the
#: explicit-CLI tier on #801, reproduced here for the project-config tier
#: by the identical mechanism. `severity_preset`/`exit_code_scheme` also
#: joined `SCAN_CONFIG_PARAMS` in the same change, closing the
#: explicit-CLI tier of the identical bug.


def record_resolved_config(result: Any, config: Any) -> None:
    """Install *config* on *result*'s persisted context, if it has one, and
    unconditionally on ``result.evaluation_config`` for the effective-
    config digest's rich tier (CLI cleanup phase two, PR B).

    Not a no-op without ``--contract`` (an earlier revision of this
    docstring said it was, before that stamp existed) -- a ``--pack``-only
    ``scan --against`` never builds a ``PersistedContractContext`` either,
    so ``effective_config_digest``'s rich tier needs ``result.
    evaluation_config`` regardless of the ``contract_context`` branch
    below, mirroring ``cli_compare_receipt.record_resolved_config``'s
    identical fix.

    ``config`` (from :func:`resolve_scan_config`) has its own severity/
    exit-code-scheme fields blanked to built-in defaults regardless of what
    real ``--severity-preset``/``--exit-code-scheme`` flags the run was
    given (see :func:`_without_gate_settings`) -- unlike `compare`'s
    equivalent, there is no gate half to reconcile *here*: this receipt
    never needs those blanked fields, because
    :func:`~abicheck.effective_config_digest.effective_config_fields_
    from_full_config` reads ``gate.exit_code_scheme``/``gate.severity.*``
    from its own *severity_config*/*exit_code_scheme* parameters -- the
    same already-resolved pair ``_run_baseline_compare`` threads for the
    sibling ``exit`` block -- rather than from ``config.gate`` at all
    (CodeRabbit review, PR #803, fresh evidence: an earlier revision of
    that function read the gate axes off *resolved_config* directly, so
    stamping this blanked ``config`` here silently discarded the run's
    real gate from the digest).
    """
    if config is None:
        return
    # CLI cleanup phase two, PR B (Codex review, PR #803): stamped
    # unconditionally, mirroring `cli_compare_receipt.record_resolved_
    # config`'s identical fix -- a `--pack`-only `scan --against` (no
    # `--contract`) never builds a `PersistedContractContext` either, so
    # `effective_config_digest`'s rich tier needs this regardless of the
    # branch below.
    result.evaluation_config = config

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
