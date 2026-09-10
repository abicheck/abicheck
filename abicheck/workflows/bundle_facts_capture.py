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

"""Captures a :class:`~abicheck.model.bundle_facts.BundleFacts` from
already-dumped per-library snapshots, and reconstructs a live-equivalent
:class:`~abicheck.bundle_models.BundleSnapshot` back out of one -- the two
``workflows``-owned operations G38 Phase 2 needs around the ``BundleFacts``
value type itself (ADR-061 gap E: moved out of the flat ``bundle_facts.py``,
which conflated this orchestration with the value type and its persistence).

Neither function performs new *ABI* extraction: :func:`capture_bundle_facts`
packages what a real ``dump``/``compare`` run already produced, probing the
filesystem only for alias/filename evidence; :func:`bundle_snapshot_from_facts`
reads no binaries at all, reusing
:func:`abicheck.bundle.build_bundle_snapshot_from_metadata` (already built
to construct a fully-functional ``BundleSnapshot`` from already-parsed
``ElfMetadata`` alone). See ``workflows/bundle_facts_compare.py`` for the
sibling that runs an actual comparison from a captured/reconstructed
``BundleFacts``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..bundle_manifest import InstantiationManifest
from ..model import AbiSnapshot
from ..model.bundle_facts import DEFAULT_VARIANT_FINGERPRINT, BundleFacts

if TYPE_CHECKING:
    from ..bundle_models import BundleSnapshot

log = logging.getLogger(__name__)


def capture_bundle_facts(
    per_library_snapshots: dict[str, AbiSnapshot],
    *,
    manifest: InstantiationManifest | None = None,
    variant_fingerprint: str = DEFAULT_VARIANT_FINGERPRINT,
    library_paths: dict[str, Path] | None = None,
    degraded_members: dict[str, str] | None = None,
    inventory_complete: bool = False,
) -> BundleFacts:
    """Build a :class:`~abicheck.model.bundle_facts.BundleFacts` from
    already-dumped per-library snapshots.

    No new *ABI* extraction happens here -- *per_library_snapshots* is what
    a real ``dump``/``compare`` run already produced (each with its ``.elf``).

    *library_paths*, when given, is a ``{library_name: Path}`` map of each
    snapshot's real on-disk file (or a stored member's materialized
    sub-package directory, via `bundle.stored_capture_identity`) -- probed
    for filesystem aliases and the real *filename* (SONAME-skew fallback).
    """
    from ..bundle import stored_capture_identity
    from ..bundle_soname import filesystem_alias_basenames, resolved_basename
    from ..model.bundle_facts import (
        BUNDLE_FACTS_BASE_SCHEMA_VERSION,
        BUNDLE_FACTS_SCHEMA_VERSION,
    )

    filesystem_aliases: dict[str, tuple[str, ...]] = {}
    library_filenames: dict[str, str] = {}
    alias_nodes_so_far = 0
    if library_paths:
        for name, path in library_paths.items():
            if name not in per_library_snapshots:
                continue
            if path.is_dir():
                stored = stored_capture_identity(path, alias_nodes_so_far)
                stored_name, stored_aliases, alias_nodes_so_far = stored
            else:
                stored_name = resolved_basename(path)  # not path.name
                stored_aliases = filesystem_alias_basenames(path)
            if stored_name:
                library_filenames[name] = stored_name
            if stored_aliases:
                filesystem_aliases[name] = stored_aliases
    return BundleFacts(
        schema_version=(
            BUNDLE_FACTS_SCHEMA_VERSION if degraded_members else BUNDLE_FACTS_BASE_SCHEMA_VERSION
        ),
        variant_fingerprint=variant_fingerprint,
        per_library_snapshots=dict(per_library_snapshots),
        manifest=manifest,
        filesystem_aliases=filesystem_aliases,
        library_filenames=library_filenames,
        degraded_members=dict(degraded_members or {}),
        inventory_complete=inventory_complete,
    )


def bundle_snapshot_from_facts(facts: BundleFacts) -> BundleSnapshot:
    """Reconstruct a live-equivalent :class:`~abicheck.bundle_models.
    BundleSnapshot` from *facts*, with no binaries read.

    A per-library entry whose ``AbiSnapshot.elf`` is ``None`` is dropped,
    as ``bundle.build_bundle_snapshot`` drops a non-ELF file.

    ``facts.filesystem_aliases`` (captured symlink/hard-link basenames)
    feeds ``build_bundle_snapshot_from_metadata``'s ``extra_aliases`` so a
    ``DT_NEEDED`` edge still resolves without probing the filesystem;
    ``facts.library_filenames`` feeds its ``paths`` so SONAME-skew sees the
    real, versioned filename (Codex review).

    Refuses *facts* carrying a ``degraded_members`` marker (ADR-065 D8,
    Codex review): a stand-in is not evidence, and a direct API caller must
    resolve the scope first (``workflows.release_scope.restrict_bundle_facts``
    under a ``ScopeAcquisitionRecord``, as every compare driver does)."""
    from ..bundle import build_bundle_snapshot_from_metadata

    if facts.degraded_members:
        raise ValueError(
            f"bundle facts mark {len(facts.degraded_members)} member(s) degraded "
            f"({', '.join(sorted(facts.degraded_members))}): an ELF-only stand-in "
            "is not bundle evidence (ADR-065 D8); resolve the scope with "
            "workflows.release_scope.restrict_bundle_facts first"
        )
    metadata = {}
    paths = {}
    for name, snap in facts.per_library_snapshots.items():
        if snap.elf is None:
            log.debug(
                "bundle_facts: %s carries no ELF metadata (non-ELF or "
                "header-only dump) -- excluded from the reconstructed bundle",
                name,
            )
            continue
        metadata[name] = snap.elf
        filename = facts.library_filenames.get(name)
        if filename:
            paths[name] = Path(filename)
    return build_bundle_snapshot_from_metadata(
        metadata, paths=paths or None, extra_aliases=facts.filesystem_aliases or None
    )
