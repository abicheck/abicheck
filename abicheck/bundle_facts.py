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

"""Persisted bundle facts (G38 Phase 2, amendment to ADR-023).

:mod:`abicheck.bundle`'s ``compare_bundle()`` only ever reopens live ``.so``
files — there is no way to get a bundle-level verdict from a *stored*
baseline the way every other surface this tool supports (``scan
--against``, a persisted per-library ``dump``) already can. This module
closes that gap with a serializable ``BundleFacts`` object and a
``compare_bundle_from_facts()`` entry point, without adding any new
*extraction*: it reuses :func:`abicheck.bundle.build_bundle_snapshot_from_metadata`,
the primitive already built to construct a fully-functional
:class:`~abicheck.bundle_models.BundleSnapshot` (cross-DSO ``DT_NEEDED``/
symbol-version resolution included) from already-parsed
:class:`~abicheck.elf_metadata.ElfMetadata` alone — which is exactly what
:attr:`abicheck.model.AbiSnapshot.elf` already stores for every ELF
``dump``. ``BundleFacts`` therefore does not duplicate a separate
resolution-graph/artifact-metadata schema (as an earlier draft of the G38
plan sketched): it stores the one thing that is not already reconstructible
from an ``AbiSnapshot`` (the manifest, plus a variant-fingerprint slot G38
Phase 3 will populate), and derives everything else — the resolution graph,
provider/consumer tables, SONAME/version data — from each library's own
``ElfMetadata`` on load, the same way a live ``compare_bundle()`` run does
from freshly-parsed binaries. This keeps ``BundleFacts`` from drifting out
of sync with whatever :func:`abicheck.bundle._compute_resolution_graph`
computes, since there is only ever one implementation of that computation.

This is a leaf module with respect to :mod:`abicheck.bundle`: it imports
that module only lazily, inside function bodies, to avoid a needless import
cycle (:mod:`abicheck.bundle` already imports :mod:`abicheck.bundle_models`/
:mod:`abicheck.bundle_manifest` at module scope).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .bundle_manifest import InstantiationManifest
from .model import AbiSnapshot

if TYPE_CHECKING:
    from .bundle_models import BundleDiffResult, BundleSnapshot
    from .checker_types import DiffResult

log = logging.getLogger(__name__)

#: Schema version for the persisted `BundleFacts` container itself --
#: independent of `AbiSnapshot.SCHEMA_VERSION` (each per-library snapshot
#: already carries its own), since the container's own shape (what fields
#: `BundleFacts` has) can evolve on its own timeline.
BUNDLE_FACTS_SCHEMA_VERSION = 1

#: The fingerprint value used when no multibuild variant applies (every
#: caller today) -- G38 Phase 3 populates a real per-variant fingerprint;
#: Phase 2 only needs the field to always be present so a future
#: Phase-3-aware comparability check has something to compare against
#: unconditionally, never `None`/absent on an older-shaped facts file.
DEFAULT_VARIANT_FINGERPRINT = "default"


@dataclass
class BundleFacts:
    """Serializable projection of everything ``compare_bundle()`` needs,
    decoupled from live ``.so`` files -- the bundle-level counterpart to
    :class:`~abicheck.model.AbiSnapshot` for a single library.

    ``per_library_snapshots`` is mandatory, not optional: ``compare_bundle()``'s
    cross-DSO findings (``bundle_intra_dep_signature_changed``,
    ``bundle_intra_type_changed``, ``bundle_provider_changed``) are not
    derived from the resolution graph alone -- they are each keyed off a
    *per-library* ``DiffResult`` (``func_params_changed``/
    ``type_size_changed``/``func_removed``+``func_added`` pairs). A
    ``BundleFacts`` carrying only resolution-graph-level data would have
    nowhere for :func:`compare_bundle_from_facts` to get those per-library
    diffs from when the *old* side is a stored dump rather than a live
    directory.
    """

    schema_version: int = BUNDLE_FACTS_SCHEMA_VERSION
    variant_fingerprint: str = DEFAULT_VARIANT_FINGERPRINT
    per_library_snapshots: dict[str, AbiSnapshot] = field(default_factory=dict)
    manifest: InstantiationManifest | None = None


def capture_bundle_facts(
    per_library_snapshots: dict[str, AbiSnapshot],
    *,
    manifest: InstantiationManifest | None = None,
    variant_fingerprint: str = DEFAULT_VARIANT_FINGERPRINT,
) -> BundleFacts:
    """Build a :class:`BundleFacts` from already-dumped per-library snapshots.

    No new extraction happens here -- *per_library_snapshots* is expected to
    be exactly what a real ``dump``/``compare`` run already produced for
    each bundle member (each carrying its own ``AbiSnapshot.elf``).
    """
    return BundleFacts(
        schema_version=BUNDLE_FACTS_SCHEMA_VERSION,
        variant_fingerprint=variant_fingerprint,
        per_library_snapshots=dict(per_library_snapshots),
        manifest=manifest,
    )


def bundle_snapshot_from_facts(facts: BundleFacts) -> BundleSnapshot:
    """Reconstruct a live-equivalent :class:`BundleSnapshot` from *facts*,
    with no binaries read.

    A per-library entry whose ``AbiSnapshot.elf`` is ``None`` (a non-ELF or
    header-only dump) is dropped, the same way :func:`abicheck.bundle.
    build_bundle_snapshot` drops a file that doesn't parse as ELF -- both
    describe "this bundle member contributes no ELF-level bundle facts",
    not an error.
    """
    from .bundle import build_bundle_snapshot_from_metadata

    metadata = {}
    for name, snap in facts.per_library_snapshots.items():
        if snap.elf is None:
            log.debug(
                "bundle_facts: %s carries no ELF metadata (non-ELF or "
                "header-only dump) -- excluded from the reconstructed bundle",
                name,
            )
            continue
        metadata[name] = snap.elf
    return build_bundle_snapshot_from_metadata(metadata)


def compare_bundle_from_facts(
    old_facts: BundleFacts,
    new_snapshot: BundleSnapshot,
    per_library_results: list[DiffResult],
    *,
    manifest: InstantiationManifest | None = None,
    system_providers: Any = None,
    cohorts: list[str] | None = None,
    policy: str = "strict_abi",
) -> BundleDiffResult:
    """Bundle-level comparison with the *old* side loaded from a stored
    :class:`BundleFacts` instead of live ``.so`` files (G38 Phase 2).

    A thin wrapper, deliberately: it reconstructs the old-side
    :class:`~abicheck.bundle_models.BundleSnapshot` via
    :func:`bundle_snapshot_from_facts` and then delegates to
    :func:`abicheck.bundle.compare_bundle` unchanged -- the same function a
    live-directory-vs-live-directory ``compare`` uses -- so the two entry
    points share one detection implementation and can never independently
    drift. This is what the mandatory dump/live parity test asserts.

    *manifest*, given explicitly, overrides *old_facts.manifest* (mirroring
    ``compare_bundle()``'s own ``manifest=`` parameter, which always wins
    over whatever a stored baseline recorded); otherwise the manifest
    captured in *old_facts* is reused.
    """
    from .bundle import compare_bundle

    old_snapshot = bundle_snapshot_from_facts(old_facts)
    effective_manifest = manifest if manifest is not None else old_facts.manifest
    return compare_bundle(
        old_snapshot,
        new_snapshot,
        per_library_results,
        manifest=effective_manifest,
        system_providers=system_providers,
        cohorts=cohorts,
        policy=policy,
    )


# Note: `bundle_facts_to_dict`/`bundle_facts_from_dict` live in
# `serialization.py`, not here — the same split `AbiSnapshot`/
# `snapshot_to_dict`/`snapshot_from_dict` already use (the model module
# stays a leaf; its serialization lives in the module that already owns
# every other snapshot's serialization). Keeping them here instead would
# create a real `bundle_facts <-> serialization` import cycle: this
# module's own `capture_bundle_facts`/`compare_bundle_from_facts` are
# needed by `serialization.py`'s docstrings/type hints only, but the
# to_dict/from_dict pair would need `serialization.snapshot_to_dict`/
# `snapshot_from_dict` at the same time `serialization.py` needs
# `BundleFacts` for its own `save_bundle_facts`/`load_bundle_facts` --
# see `scripts/check_ai_readiness.py`'s `import-cycle-growth` check, which
# caught exactly this the first time this module was drafted.
