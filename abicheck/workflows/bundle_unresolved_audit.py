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

"""The single-bundle audit: unresolved imports with no OLD side to compare.

``bundle compare``'s :func:`~abicheck.bundle_detectors._detect_intra_dep_removed`
asks whether *this release* dropped a provider. This detector runs where
there is no release pair at all -- one bundle, audited on its own -- so it
can only ask whether every import is accounted for *now*.

That difference is why the two do not share an emptiness policy for outward
``DT_NEEDED`` edges. A consumer with zero edges is *vacuously* "all edges
resolve to system providers"; the comparison path may read that as evidence
of externality when OLD carried the same import, because the previous
release shipped it and loaded. Audit mode has no previous release to point
at, so it keeps its non-empty requirement: no edges is no evidence.
:func:`~abicheck.workflows.bundle_import_evidence.extra_needed_all_system`
answers only the all-system question, and each caller keeps its own
emptiness rule on top.

Split out of ``bundle_detectors.py``, which carries a ``no_growth`` baseline
in ``architecture/debt.yaml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..bundle_detector_heuristics import (
    _import_is_external,
)
from ..bundle_models import BundleFinding
from ..bundle_resolution_reachability import (
    reachable_intra_libraries as _reachable_intra_libraries,
)
from ..model.change_catalog.kinds import ChangeKind
from .bundle_import_evidence import extra_needed_all_system

if TYPE_CHECKING:
    from ..bundle_models import BundleSnapshot


def _detect_unresolved_intra_dependency(
    new: BundleSnapshot,
    system_providers: set[str],
) -> list[BundleFinding]:
    """Audit-mode (no old side) sibling of :func:`_detect_intra_dep_removed`.

    ADR-056 D2: ``scan --artifact-set`` has no per-library diff to read, so
    this operates purely off the new-side resolution graph. Deliberately
    **not** a call into :func:`_detect_intra_dep_removed`; differs in three
    ways that matter for soundness here:

    1. **Version-aware, reachability-constrained provider matching.**
       ``providers_for(symbol)`` is name-only and set-wide.
       ``ProviderEntry.version``/``ConsumerEntry.version`` are consulted so a
       version mismatch (consumer needs ``foo@V2``, set only provides
       ``foo@V1``) is not mistaken for a resolved import; when the precise
       ``ConsumerEntry.version_soname`` is known, the match is pinned to
       that exact provider library (GNU version *labels* are not globally
       unique). Every candidate provider must additionally be reachable
       from the consumer through :func:`_reachable_intra_libraries` — a
       match on a library the consumer has no ``DT_NEEDED`` path to would
       never actually be loaded together with the consumer.
    2. **A narrower, explicitly-approximate suppression path for unversioned
       imports.** Mirrors ``_detect_intra_dep_removed``'s allow-list union
       and its non-empty guard (``extra_edges and all(...)``, never a bare
       ``all([])``), but adds a requirement one-sided audit needs and the
       diff-driven detector does not: the consumer must have **zero**
       intra-bundle ``DT_NEEDED`` edges — a consumer still depending on an
       intra-set library that simply stopped exporting the symbol has a
       real, in-set candidate provider this coarse check cannot rule out.
       Deliberately has **no** symbol-name-shape fallback
       (``_looks_system_symbol``): ``.abicheck.yml``'s ``bundle.
       system_providers:`` (CLI cleanup phase two, PR J; formerly
       ``--bundle-system-providers``) exists specifically to cover a
       legitimate, non-system-shaped custom export
       (e.g. ``vendor_init``) that a shape heuristic would never match.
    3. Emits ``ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY`` (not
       ``BUNDLE_INTRA_DEP_REMOVED`` — that kind implies a diff-confirmed
       removal, which this finding cannot claim) at
       ``COMPATIBLE_WITH_RISK``, not ``BREAKING``: an audit has no old side
       to confirm the symbol ever resolved.
    """
    findings: list[BundleFinding] = []
    reachable_cache: dict[str, set[str]] = {}

    def _reachable(lib: str) -> set[str]:
        if lib not in reachable_cache:
            reachable_cache[lib] = _reachable_intra_libraries(new, lib)
        return reachable_cache[lib]

    for symbol, consumers in new.resolution.consumers.items():
        providers = new.resolution.providers_for(symbol)
        for consumer in consumers:
            if consumer.weak:
                continue
            consumer_meta = new.metadata.get(consumer.library)
            if consumer_meta is None:
                continue
            reachable = _reachable(consumer.library)

            if consumer.version:
                if consumer.version_soname:
                    # Same soname_to_name map _reachable_intra_libraries()
                    # uses (Codex review) -- provider_library_for_soname()'s
                    # independent heuristic has the identical
                    # resolved-through-symlink gap, so a version_soname
                    # naming a provider's real on-disk filename could fail
                    # to resolve here even when that provider is genuinely
                    # reachable.
                    target_lib = new.resolution.soname_to_name.get(
                        consumer.version_soname
                    )
                    resolved = target_lib is not None and target_lib in reachable
                    resolved = resolved and any(
                        p.library == target_lib and p.version == consumer.version
                        for p in providers
                    )
                else:
                    resolved = any(
                        p.version == consumer.version and p.library in reachable
                        for p in providers
                    )
            else:
                # P2 regression (Codex review): an unversioned consumer
                # reference can only be satisfied by an unversioned or
                # default-version ("@@default") provider definition -- a
                # provider that exports this symbol *only* as a non-default
                # versioned definition ("foo@V1", not "foo@@V1") cannot
                # satisfy it, even though the bare symbol name is reachable.
                resolved = any(
                    p.library in reachable and p.is_default for p in providers
                )

            if resolved:
                continue
            if _import_is_external(consumer, consumer_meta, new):
                continue

            if not consumer.version:
                intra_edges = new.resolution.intra_needed.get(consumer.library, [])
                extra_edges = new.resolution.extra_needed.get(consumer.library, [])
                # `extra_edges and ...`: audit mode deliberately does *not*
                # take the vacuous-true reading of the empty case that
                # `extra_needed_all_system` supplies for its other caller.
                # With no OLD side there is nothing to establish that a
                # zero-DT_NEEDED consumer's import was ever satisfied at
                # all, so it stays reported (at RISK) --
                # `test_unversioned_zero_edges_not_suppressed`. The
                # *coverage* rule is still shared, so a change to what
                # counts as system-provided lands on both detectors.
                if (
                    not intra_edges
                    and extra_edges
                    and extra_needed_all_system(
                        consumer.library, new.resolution, system_providers
                    )
                ):
                    continue

            findings.append(
                BundleFinding(
                    kind=ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY,
                    symbol=symbol,
                    description=(
                        f"{consumer.library} imports {symbol}, but no provider "
                        "was found for it in this artifact set (audit mode — "
                        "no old side to confirm this ever resolved)."
                    ),
                    consumer_library=consumer.library,
                    affected_libraries=[consumer.library],
                ),
            )
    return findings
