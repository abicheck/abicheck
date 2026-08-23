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
from pathlib import Path
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

    ``filesystem_aliases`` records, per library, the extra soname
    spellings :func:`abicheck.bundle_soname.filesystem_alias_basenames` recovered
    from the *real* on-disk file at capture time (a resolved symlink
    target's basename, hard-linked sibling basenames) -- captured once, up
    front, so :func:`bundle_snapshot_from_facts`'s later, metadata-only
    reconstruction can still resolve a ``DT_NEEDED`` edge naming one of
    those aliases even though it never touches the filesystem itself
    (Codex review: a live ``build_bundle_snapshot()`` enables filesystem
    probing for exactly this; a persisted-facts reconstruction otherwise
    loses it, silently dropping a consumption-gated bundle finding for a
    provider without a usable ``DT_SONAME``). Empty for a caller that
    didn't have (or didn't pass) real paths at capture time -- the same
    "best effort, no aliases on failure" default
    ``filesystem_alias_basenames`` itself uses.

    ``library_filenames`` records, per library, the real on-disk
    *basename* at capture time (``libfoo_core.so.1``, not the canonical
    ``libfoo_core.so`` key) -- needed by ``bundle._detect_soname_skew``'s
    own ``path.name`` fallback for a versioned DSO with no usable
    ``DT_SONAME``: without it, ``bundle_snapshot_from_facts()`` has no real
    path to reconstruct and falls back to ``Path(canonical_key)``, whose
    ``.name`` is the canonical, *unversioned* key -- no derivable major, so
    ``bundle_soname_skew`` silently goes unreported for a stored-baseline
    comparison that a live one would have caught (Codex review, fresh
    evidence). Empty for a caller that didn't pass real paths at capture
    time, same as ``filesystem_aliases``.
    """

    schema_version: int = BUNDLE_FACTS_SCHEMA_VERSION
    variant_fingerprint: str = DEFAULT_VARIANT_FINGERPRINT
    per_library_snapshots: dict[str, AbiSnapshot] = field(default_factory=dict)
    manifest: InstantiationManifest | None = None
    filesystem_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    library_filenames: dict[str, str] = field(default_factory=dict)


def capture_bundle_facts(
    per_library_snapshots: dict[str, AbiSnapshot],
    *,
    manifest: InstantiationManifest | None = None,
    variant_fingerprint: str = DEFAULT_VARIANT_FINGERPRINT,
    library_paths: dict[str, Path] | None = None,
) -> BundleFacts:
    """Build a :class:`BundleFacts` from already-dumped per-library snapshots.

    No new *ABI* extraction happens here -- *per_library_snapshots* is
    expected to be exactly what a real ``dump``/``compare`` run already
    produced for each bundle member (each carrying its own
    ``AbiSnapshot.elf``).

    *library_paths*, when given, is a ``{library_name: Path}`` map of the
    real on-disk file each snapshot was dumped from -- used both to probe
    filesystem aliases (:func:`abicheck.bundle_soname.filesystem_alias_basenames`)
    while those files still exist, at the one point in this whole flow
    (capture time, right after a live comparison) they are guaranteed to,
    and to record each library's real on-disk *filename*
    (``BundleFacts.library_filenames``) for the SONAME-skew fallback (see
    that field's own docstring). A name absent from *library_paths* simply
    gets no recorded aliases/filename, same as when *library_paths* is
    omitted entirely.
    """
    from .bundle_soname import filesystem_alias_basenames, resolved_basename

    filesystem_aliases: dict[str, tuple[str, ...]] = {}
    library_filenames: dict[str, str] = {}
    if library_paths:
        for name, path in library_paths.items():
            if name not in per_library_snapshots:
                continue
            # The resolved target's basename, not path.name -- library_paths
            # commonly names a dev symlink (libfoo.so -> libfoo.so.1.2), and
            # a bare path.name would capture the symlink's own, unversioned
            # name instead of the real, versioned filename the SONAME-skew
            # fallback needs (Codex review, fresh evidence).
            library_filenames[name] = resolved_basename(path)
            aliases = filesystem_alias_basenames(path)
            if aliases:
                filesystem_aliases[name] = aliases
    return BundleFacts(
        schema_version=BUNDLE_FACTS_SCHEMA_VERSION,
        variant_fingerprint=variant_fingerprint,
        per_library_snapshots=dict(per_library_snapshots),
        manifest=manifest,
        filesystem_aliases=filesystem_aliases,
        library_filenames=library_filenames,
    )


def bundle_snapshot_from_facts(facts: BundleFacts) -> BundleSnapshot:
    """Reconstruct a live-equivalent :class:`BundleSnapshot` from *facts*,
    with no binaries read.

    A per-library entry whose ``AbiSnapshot.elf`` is ``None`` (a non-ELF or
    header-only dump) is dropped, the same way :func:`abicheck.bundle.
    build_bundle_snapshot` drops a file that doesn't parse as ELF -- both
    describe "this bundle member contributes no ELF-level bundle facts",
    not an error.

    ``facts.filesystem_aliases`` (real symlink-target/hard-link basenames
    captured while the original binaries still existed, see
    :func:`capture_bundle_facts`) is threaded through as
    ``build_bundle_snapshot_from_metadata``'s ``extra_aliases`` so this
    purely metadata-driven reconstruction can still resolve a
    ``DT_NEEDED`` edge naming one of those aliases -- without probing the
    filesystem itself, since the persisted facts may outlive the files
    they were captured from.

    ``facts.library_filenames`` is threaded through as that same function's
    ``paths`` -- a real on-disk filename (e.g. ``libfoo_core.so.1``)
    reconstructed as ``Path(filename)`` rather than the default
    ``Path(canonical_key)`` fallback, so ``bundle._detect_soname_skew``'s
    own ``path.name`` SONAME-major fallback sees the real, versioned
    filename instead of the canonical, unversioned key (Codex review, fresh
    evidence -- see that field's own docstring). A name absent from
    ``library_filenames`` (no real paths at capture time) falls back to
    ``build_bundle_snapshot_from_metadata``'s own ``Path(name)`` default,
    unchanged from before this field existed.
    """
    from .bundle import build_bundle_snapshot_from_metadata

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
