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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bundle_manifest import InstantiationManifest
from .model import AbiSnapshot

if TYPE_CHECKING:
    from .bundle_models import BundleDiffResult, BundleSnapshot
    from .checker_types import DiffResult
    from .snapshot_io import SnapshotWriteResult

log = logging.getLogger(__name__)

#: Schema version for the persisted `BundleFacts` container itself --
#: independent of `AbiSnapshot.SCHEMA_VERSION` (each per-library snapshot
#: already carries its own), since the container's own shape (what fields
#: `BundleFacts` has) can evolve on its own timeline.
BUNDLE_FACTS_SCHEMA_VERSION = 1

#: Schema version for G40's content-addressed archive *container* --
#: independent of `BUNDLE_FACTS_SCHEMA_VERSION` above (the container's own
#: manifest shape vs. the `BundleFacts` shape it encodes), for the same
#: reason that field is independent of `AbiSnapshot.SCHEMA_VERSION`.
BUNDLE_ARCHIVE_SCHEMA_VERSION = 1

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
    new_signature_evidence: dict[str, Any] | None = None,
) -> BundleDiffResult:
    """Bundle-level comparison with the *old* side loaded from a stored
    :class:`BundleFacts` instead of live ``.so`` files (G38 Phase 2).

    A thin wrapper, deliberately: it reconstructs the old-side
    :class:`~abicheck.bundle_models.BundleSnapshot` via
    :func:`bundle_snapshot_from_facts` and then delegates to
    :func:`abicheck.bundle_analysis.analyze_bundle` -- the same orchestrator
    a live-directory-vs-live-directory ``compare --release`` uses -- so the
    two entry points share one detection implementation (both the core
    graph-native/diff-derived suite and, since G38 stabilization Phase 12,
    the C-boundary signature-evidence gate) and can never independently
    drift. This is what the mandatory dump/live parity test asserts.

    *manifest*, given explicitly, overrides *old_facts.manifest* (mirroring
    ``compare_bundle()``'s own ``manifest=`` parameter, which always wins
    over whatever a stored baseline recorded); otherwise the manifest
    captured in *old_facts* is reused.

    *new_signature_evidence* (G38 stabilization Phase 12), when given and
    non-empty, is the NEW side's bundle-canonical-key -> ``AbiSnapshot`` (or
    the compact ``BundleSignatureEvidence`` projection) map for
    ``find_unverified_signature_findings`` -- the OLD side's own map is
    always *old_facts.per_library_snapshots* itself, which is already
    exactly this shape (a real, mandatory ``dict[str, AbiSnapshot]`` -- see
    ``BundleFacts``'s own docstring for why it's mandatory). Omitted (the
    default): the Phase 4 gate does not run, matching every pre-Phase-12
    caller of this function exactly -- there is not yet a CLI producer for
    a live NEW-side evidence map here (G38 Phase 13, "stored-facts CLI
    consumer", is a separate, not-yet-implemented phase), so this parameter
    exists for a caller that already has one (a Python-API caller, or a
    future Phase 13 CLI path) rather than being wired to anything today.
    """
    from .bundle_analysis import analyze_bundle

    old_snapshot = bundle_snapshot_from_facts(old_facts)
    effective_manifest = manifest if manifest is not None else old_facts.manifest
    return analyze_bundle(
        old_snapshot,
        new_snapshot,
        per_library_results,
        manifest=effective_manifest,
        system_providers=system_providers,
        cohorts=cohorts,
        policy=policy,
        old_signature_evidence=old_facts.per_library_snapshots,
        new_signature_evidence=new_signature_evidence,
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


# ---------------------------------------------------------------------------
# G40: content-addressed archive format -- the BundleFacts<->blobs glue.
#
# Deliberately placed here rather than in `serialization.py` (ADR-061's
# `storage` responsibility owner for this behavior): `serialization.py` is
# a `debt.yaml`-tracked, no-growth module today (predates ADR-061, can't
# grow without moving responsibility elsewhere first). `abicheck/storage/
# bundle_archive.py` (the low-level, content-addressed zip-container
# primitive this glue calls into) is deliberately kept free of any
# `BundleFacts`/`AbiSnapshot` knowledge -- see that module's own docstring.
#
# This glue takes `snapshot_to_dict`/`snapshot_from_dict` as *parameters*
# rather than importing them from `serialization.py`: `serialization.py`
# already imports `BundleFacts` from this module (for
# `bundle_facts_to_dict`/`bundle_facts_from_dict`/`save_bundle_facts`/
# `load_bundle_facts`), so a `from .serialization import snapshot_to_dict`
# here -- even function-scoped -- makes the two modules mutually dependent.
# `scripts/check_ai_readiness.py`'s `import-cycle-growth` check flags this
# via static AST scanning regardless of whether the import is lazy (caught
# exactly this the first time this section was drafted, not assumed).
# `_validated_alias_map`/`_validated_filename_map` are small enough
# (~15 lines of dict validation each) to duplicate locally below instead of
# threading two more callables through for the same reason.
def maybe_write_bundle_facts_archive(
    facts: BundleFacts,
    path: str | Path,
    format: str,
    *,
    snapshot_to_dict: Callable[[AbiSnapshot], dict[str, Any]],
) -> SnapshotWriteResult | None:
    """``serialization.save_bundle_facts``'s ``format=`` dispatch: returns a
    real result for ``format="archive"``, ``None`` for ``format="json"``
    (telling the caller to fall through to its own plain-JSON path), and
    raises for anything else. *snapshot_to_dict* is
    ``serialization.snapshot_to_dict``, passed in rather than imported --
    see the module-level comment above."""
    if format == "archive":
        return write_bundle_facts_archive(facts, path, snapshot_to_dict=snapshot_to_dict)
    if format != "json":
        raise ValueError(f"save_bundle_facts: unknown format {format!r}")
    return None


def maybe_read_bundle_facts_archive(
    path: str | Path,
    format: str,
    *,
    snapshot_from_dict: Callable[[dict[str, Any]], AbiSnapshot],
) -> BundleFacts | None:
    """``serialization.load_bundle_facts``'s ``format=`` dispatch:
    ``"auto"`` sniffs *path*'s own bytes (delegating to
    ``storage.bundle_archive.sniff_bundle_archive_format``); returns a real
    result whenever the resolved format is ``"archive"``, ``None`` for
    ``"json"`` (fall through to plain-JSON), and raises for anything else.
    *snapshot_from_dict* is ``serialization.snapshot_from_dict``, passed in
    rather than imported -- see the module-level comment above.
    """
    from .storage.bundle_archive import sniff_bundle_archive_format

    resolved = sniff_bundle_archive_format(path) if format == "auto" else format
    if resolved == "archive":
        return read_bundle_facts_archive(path, snapshot_from_dict=snapshot_from_dict)
    if resolved != "json":
        raise ValueError(f"load_bundle_facts: unknown format {resolved!r}")
    return None


def write_bundle_facts_archive(
    facts: BundleFacts,
    path: str | Path,
    *,
    snapshot_to_dict: Callable[[AbiSnapshot], dict[str, Any]],
) -> SnapshotWriteResult:
    """Write *facts* as a G40 content-addressed zip archive at *path*.

    Deduplication is whole-per-library-snapshot granularity (see the G40
    plan's own scope note): two libraries whose entire serialized
    ``AbiSnapshot`` is byte-identical share one blob, via
    ``BundleArchiveWriter.put_blob``'s own hash-addressed dedup -- nothing
    in this function decides that; it simply calls ``put_blob`` once per
    library and lets identical payloads collapse on their own.
    """
    import json as _json

    from .snapshot_io import SnapshotCompression, SnapshotWriteResult
    from .storage.bundle_archive import BundleArchiveWriter, content_hash

    p = Path(path)
    library_blobs: dict[str, str] = {}
    decoded_size_bytes = 0
    with BundleArchiveWriter(p) as writer:
        for name, snap in facts.per_library_snapshots.items():
            payload = _json.dumps(snapshot_to_dict(snap), indent=2).encode("utf-8")
            decoded_size_bytes += len(payload)
            library_blobs[name] = writer.put_blob(payload)
        manifest_blob = None
        if facts.manifest is not None:
            from .bundle_manifest import manifest_to_dict

            manifest_payload = _json.dumps(
                manifest_to_dict(facts.manifest), indent=2
            ).encode("utf-8")
            decoded_size_bytes += len(manifest_payload)
            manifest_blob = writer.put_blob(manifest_payload)
        writer.write_manifest(
            {
                "schema_version": BUNDLE_ARCHIVE_SCHEMA_VERSION,
                "bundle_facts_schema_version": facts.schema_version,
                "variant_fingerprint": facts.variant_fingerprint,
                "library_blobs": library_blobs,
                "manifest_blob": manifest_blob,
                "filesystem_aliases": {
                    name: list(aliases)
                    for name, aliases in facts.filesystem_aliases.items()
                },
                "library_filenames": dict(facts.library_filenames),
            }
        )
    stored_bytes = p.read_bytes()
    return SnapshotWriteResult(
        path=p,
        compression=SnapshotCompression.ZSTD,
        decoded_size_bytes=decoded_size_bytes,
        stored_size_bytes=len(stored_bytes),
        stored_sha256=content_hash(stored_bytes),
    )


def read_bundle_facts_archive(
    path: str | Path,
    *,
    snapshot_from_dict: Callable[[dict[str, Any]], AbiSnapshot],
) -> BundleFacts:
    """Read a G40 content-addressed zip archive at *path* back into a
    :class:`BundleFacts`.

    Loads every library's blob (a whole-bundle load, matching plain
    ``load_bundle_facts``'s own contract) -- a caller wanting only one
    library's snapshot without paying for the rest uses
    ``storage.bundle_archive.BundleArchiveReader`` directly instead.
    """
    import json as _json

    from .bundle_manifest import manifest_from_dict
    from .errors import IncompatibleSnapshotSchemaError
    from .storage.bundle_archive import BundleArchiveReader

    with BundleArchiveReader.open(path) as reader:
        manifest = reader.read_manifest()
        bundle_facts_schema_version = int(
            manifest.get("bundle_facts_schema_version", BUNDLE_FACTS_SCHEMA_VERSION)
        )
        if bundle_facts_schema_version > BUNDLE_FACTS_SCHEMA_VERSION:
            raise IncompatibleSnapshotSchemaError(
                f"Bundle facts schema_version {bundle_facts_schema_version} is "
                "newer than this abicheck (supports up to schema_version "
                f"{BUNDLE_FACTS_SCHEMA_VERSION}). Upgrade abicheck to read "
                "this bundle archive."
            )
        library_blobs = manifest.get("library_blobs", {})
        if not isinstance(library_blobs, dict):
            raise ValueError(
                "bundle archive: 'library_blobs' must be a mapping, got "
                f"{type(library_blobs).__name__}"
            )
        per_library_snapshots = {
            name: snapshot_from_dict(_json.loads(reader.read_blob(h)))
            for name, h in library_blobs.items()
        }
        manifest_blob = manifest.get("manifest_blob")
        instantiation_manifest = None
        if manifest_blob is not None:
            instantiation_manifest = manifest_from_dict(
                _json.loads(reader.read_blob(manifest_blob))
            )
        return BundleFacts(
            schema_version=bundle_facts_schema_version,
            variant_fingerprint=str(
                manifest.get("variant_fingerprint", DEFAULT_VARIANT_FINGERPRINT)
            ),
            per_library_snapshots=per_library_snapshots,
            manifest=instantiation_manifest,
            filesystem_aliases=_validated_alias_map(
                manifest.get("filesystem_aliases", {})
            ),
            library_filenames=_validated_filename_map(
                manifest.get("library_filenames", {})
            ),
        )


def _validated_alias_map(raw: object) -> dict[str, tuple[str, ...]]:
    """Validate and convert a persisted ``filesystem_aliases`` mapping.

    Duplicated from ``serialization._validated_alias_map`` (not imported --
    see the module-level comment above): rejects a non-mapping container, a
    non-list value, and a list with a non-string element, rather than
    silently iterating a stray string's characters into single-letter
    "aliases".
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"bundle facts: 'filesystem_aliases' must be a mapping, got "
            f"{type(raw).__name__}"
        )
    aliases: dict[str, tuple[str, ...]] = {}
    for name, values in raw.items():
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(
                f"bundle facts: 'filesystem_aliases[{name!r}]' must be a list of "
                f"strings, got {values!r}"
            )
        aliases[name] = tuple(values)
    return aliases


def _validated_filename_map(raw: object) -> dict[str, str]:
    """Validate and convert a persisted ``library_filenames`` mapping.

    Duplicated from ``serialization._validated_filename_map`` (not imported
    -- see the module-level comment above): rejects a non-string value
    instead of silently coercing it (``str(None)`` -> the fabricated
    basename ``"None"``).
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"bundle facts: 'library_filenames' must be a mapping, got "
            f"{type(raw).__name__}"
        )
    filenames: dict[str, str] = {}
    for name, filename in raw.items():
        if not isinstance(filename, str):
            raise ValueError(
                f"bundle facts: 'library_filenames[{name!r}]' must be a string, "
                f"got {filename!r}"
            )
        filenames[name] = filename
    return filenames
