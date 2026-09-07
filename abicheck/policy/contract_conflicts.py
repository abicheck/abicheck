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

"""Workstream E slice S3, cases 1-2: exported-but-undeclared and manifest
narrowing since baseline.

Each function is a pure, independently testable detector over
already-computed evidence -- neither re-parses a binary or re-walks a type
graph; they cross-reference :mod:`abicheck.export_surface`'s own matching
pass and turn a genuine disagreement into a
:class:`~abicheck.model.contract_conflicts.ContractSourceConflict` naming
both sides' claims (never resolved to one side, ADR-067). Case 3
(package-claim vs. contained binary) lives in
:mod:`abicheck.workflows.contract_conflicts` instead -- it depends on
:mod:`abicheck.debian_symbols`, a layer above this one.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..export_surface import ExportSurface
from ..model.contract_conflicts import (
    CONFLICT_EXPORTED_BUT_UNDECLARED,
    CONFLICT_MANIFEST_NARROWED_SINCE_BASELINE,
    ConflictSourceClaim,
    ContractSourceConflict,
)


def detect_exported_but_undeclared(
    export_surface: ExportSurface,
    *,
    side: str | None = None,
) -> list[ContractSourceConflict]:
    """Symbols the binary exports that no public-header declaration matches.

    Reuses :attr:`~abicheck.export_surface.ExportSurface.unmatched_exports`
    -- every ABI-relevant observed export name that
    :func:`~abicheck.export_surface.compute_export_surface`'s own matching
    pass (against this same snapshot's ``functions``/``variables``) could
    not account for at all. That is precisely "the export-table evidence
    says this symbol is in the contract; the public-header evidence has no
    declaration for it" -- the mirror image of the existing
    ``PUBLIC_NOT_EXPORTED``/``exported_not_public`` checks, which start from
    a *declaration* and ask whether the binary exported it.

    Requires :attr:`~abicheck.export_surface.ExportSurface.resolvable`
    (an export table was actually observed) -- with no observed table there
    is no export-side claim to conflict with the header side at all, so no
    conflict is recorded rather than one built on absent evidence.
    """
    if not export_surface.resolvable:
        return []
    conflicts: list[ContractSourceConflict] = []
    for name in sorted(export_surface.unmatched_exports):
        conflicts.append(
            ContractSourceConflict(
                conflict_kind=CONFLICT_EXPORTED_BUT_UNDECLARED,
                entity=name,
                sources=(
                    ConflictSourceClaim(
                        source_kind="export_table",
                        claim=f"binary exports symbol {name!r}",
                        detail={"symbol": name},
                    ),
                    ConflictSourceClaim(
                        source_kind="public_header",
                        claim=("no public header declaration matches this export"),
                        detail={"symbol": name},
                    ),
                ),
                reason_code="export_unmatched_by_header_surface",
                side=side,
            )
        )
    return conflicts


def detect_manifest_narrowing_since_baseline(
    baseline_public_symbols: Iterable[str],
    current_public_symbols: Iterable[str],
    manifest_allowlist: Iterable[str] | None,
    *,
    side: str | None = None,
) -> list[ContractSourceConflict]:
    """Symbols a ``--post-manifest``/forced-public overlay excludes from this
    run's committed exports, despite a baseline declaring them public.

    ``baseline_public_symbols`` is the declared-public symbol-key set from a
    prior run's (or, within one comparison, the "old" side's) header
    evidence with **no** manifest overlay applied -- what the contract was
    *before* this run's overlay. ``current_public_symbols`` is the same kind
    of set for the side the manifest actually narrows (the "new" side, or a
    freshly re-dumped current snapshot); a symbol must still be declared
    public in the current headers too, or its absence is an ordinary
    removal, not something the manifest did. ``manifest_allowlist`` is
    ``None`` when no ``--post-manifest``/overlay is in effect at all -- in
    that case there is no narrowing *source* to conflict with the baseline,
    so no conflict is recorded (as opposed to an empty allowlist, which is a
    real, active manifest committing to zero exports and does narrow).

    The result names every symbol present in *both* the baseline and current
    declared-public sets but absent from the manifest allowlist -- the
    contract shrank via the overlay alone, independent of any header/code
    change between baseline and current.
    """
    if manifest_allowlist is None:
        return []
    allowlist = frozenset(manifest_allowlist)
    baseline = frozenset(baseline_public_symbols)
    current = frozenset(current_public_symbols)
    narrowed = sorted((baseline & current) - allowlist)
    conflicts: list[ContractSourceConflict] = []
    for name in narrowed:
        conflicts.append(
            ContractSourceConflict(
                conflict_kind=CONFLICT_MANIFEST_NARROWED_SINCE_BASELINE,
                entity=name,
                sources=(
                    ConflictSourceClaim(
                        source_kind="baseline_contract",
                        claim=f"{name!r} was declared public at baseline",
                        detail={"symbol": name},
                    ),
                    ConflictSourceClaim(
                        source_kind="post_manifest",
                        claim=(
                            f"{name!r} is not present in the current "
                            "manifest-narrowed contract"
                        ),
                        detail={"symbol": name},
                    ),
                ),
                reason_code="manifest_excludes_baseline_public_symbol",
                side=side,
            )
        )
    return conflicts


__all__ = [
    "detect_exported_but_undeclared",
    "detect_manifest_narrowing_since_baseline",
]
