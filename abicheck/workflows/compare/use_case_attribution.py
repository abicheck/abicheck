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

"""Typed workflow for ``--use-cases``: read the manifest, attach the attribution.

The CLI reads a manifest in its ``--dry-run`` preflight (a dry run must reject
what the real run rejects), in ``project validate``, and attaches the block
after the comparison. Every refusal raises :class:`UseCaseAttributionError`,
whose message is the text the CLI prints as its usage error (exit 64); this
module never imports ``click``.

The reachability walk (:func:`abicheck.policy.use_case_reachability.explain_use_case_impact`)
is policy-owned and called by ``build_use_case_impact`` directly, so this layer
takes no injected port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...errors import UseCaseManifestError
from ...policy.use_case_attribution import build_use_case_impact
from ...policy.use_cases import load_use_case_manifest

if TYPE_CHECKING:
    from pathlib import Path

    from ...policy.use_cases import UseCaseDefinition


class UseCaseAttributionError(Exception):
    """A ``--use-cases`` request the run cannot honor. ``str()`` is the user-facing message."""


def read_use_case_manifest(manifest: Path) -> list[UseCaseDefinition]:
    """Parse *manifest*'s structure, or refuse it with the usage-error text.

    ``OSError`` is caught alongside the manifest error: Click's ``exists=True``
    only proves the path was there at parse time, and an unhandled read error
    would exit 1 with a bare traceback instead of the usage-error path.
    """
    try:
        return load_use_case_manifest(manifest)
    except (UseCaseManifestError, OSError) as exc:
        raise UseCaseAttributionError(str(exc)) from exc


def attach_use_case_impact(
    result: Any,
    old: Any,
    new: Any,
    manifest: Path | None,
) -> None:
    """Attach ``compare --use-cases``'s attribution block to *result*.

    A no-op without a manifest. A malformed manifest, and a pair with no
    source graph on either side to resolve entrypoints against, raise
    :class:`UseCaseAttributionError` rather than leaving a silently missing
    section -- the user asked for an attribution the run cannot produce, and
    an absent block would read as "no use case is affected".
    """
    if manifest is None:
        return
    definitions = read_use_case_manifest(manifest)

    # Scoped-only findings included, for the same reason
    # `_attach_suppression_audit` below includes them: this runs *after*
    # --used-by/consumers.required_symbols scoping, which synthesizes fresh Change
    # objects onto `scoped_only_changes` (e.g. PE_ORDINAL_RETARGETED) that
    # `_fold_scoped_compat_into_text` then appends to the rendered report's
    # own findings list. Attributing only `result.changes` left
    # `total_changes` smaller than the list beside it, with the synthesized
    # findings neither attributed to a use case nor counted as unattributed
    # (Codex review).
    impact = build_use_case_impact(
        definitions,
        old,
        new,
        list(result.changes) + list(getattr(result, "scoped_only_changes", ()) or ()),
        manifest=str(manifest),
    )
    if impact is None:
        raise UseCaseAttributionError(
            f"--use-cases {manifest} needs a source graph to resolve "
            "entrypoints against, and neither side carries one (dump with "
            "--sources/--build-info, or ensure the always-on header-only "
            "graph attached)."
        )
    result.use_case_impact = impact
