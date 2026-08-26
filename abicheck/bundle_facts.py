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
already built to construct a fully-functional
:class:`~abicheck.bundle_models.BundleSnapshot` from already-parsed
:class:`~abicheck.elf_metadata.ElfMetadata` alone — exactly what
:attr:`abicheck.model.AbiSnapshot.elf` already stores. ``BundleFacts``
therefore stores only what isn't already reconstructible from an
``AbiSnapshot`` (the manifest, plus a variant-fingerprint slot G38 Phase 3
will populate), deriving everything else on load the same way a live
``compare_bundle()`` run does, so it can never drift out of sync with
:func:`abicheck.bundle._compute_resolution_graph`.

This is a leaf module with respect to :mod:`abicheck.bundle`: it imports
that module only lazily, inside function bodies, to avoid a needless import
cycle (:mod:`abicheck.bundle` already imports :mod:`abicheck.bundle_models`/
:mod:`abicheck.bundle_manifest` at module scope).
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bundle_manifest import InstantiationManifest
from .model import AbiSnapshot
from .storage.bundle_facts_validation import validated_alias_map, validated_filename_map

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

#: Aggregate decoded-size cap for a whole read_bundle_facts_archive() load
#: -- same 1 GiB value as `snapshot_io.DEFAULT_MAX_DECODED_BYTES`. A crafted
#: archive with many individually-valid blobs (or many library names
#: referencing one large blob) could otherwise amplify past `read_blob`'s
#: own per-call `max_decoded_bytes` across the whole load (Codex review).
DEFAULT_MAX_BUNDLE_DECODED_BYTES = 1024 * 1024 * 1024

#: Hard cap on the number of `library_blobs` entries one archive manifest
#: may name -- independent of any byte-size cap (a shared blob's decoded
#: bytes are charged once, but each name sharing it still materializes its
#: own AbiSnapshot object graph). No real bundle approaches this.
DEFAULT_MAX_LIBRARY_COUNT = 20_000

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
    *per-library* ``DiffResult``. A ``BundleFacts`` carrying only
    resolution-graph-level data would have nowhere for
    :func:`compare_bundle_from_facts` to get those per-library diffs from
    when the *old* side is a stored dump rather than a live directory.

    ``filesystem_aliases`` records, per library, the extra soname
    spellings :func:`abicheck.bundle_soname.filesystem_alias_basenames` recovered
    from the *real* on-disk file at capture time -- captured once, up
    front, so :func:`bundle_snapshot_from_facts`'s later, metadata-only
    reconstruction can still resolve a ``DT_NEEDED`` edge naming one of
    those aliases without touching the filesystem itself (Codex review).
    Empty for a caller that didn't pass real paths at capture time, same
    default as ``filesystem_alias_basenames``.

    ``library_filenames`` records, per library, the real on-disk
    *basename* at capture time (``libfoo_core.so.1``, not the canonical
    ``libfoo_core.so`` key) -- needed by ``bundle._detect_soname_skew``'s
    own ``path.name`` fallback for a versioned DSO with no usable
    ``DT_SONAME``, else it silently goes unreported for a stored-baseline
    comparison a live one would have caught (Codex review). Empty for a
    caller that didn't pass real paths, same as ``filesystem_aliases``."""

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
    while those files still exist, at the one point in this flow (capture
    time) they are guaranteed to, and to record each library's real
    on-disk *filename* (``BundleFacts.library_filenames``) for the
    SONAME-skew fallback. A name absent from *library_paths* simply gets
    no recorded aliases/filename, same as when it's omitted entirely.
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
    describe "this bundle member contributes no ELF-level bundle facts".

    ``facts.filesystem_aliases`` (real symlink-target/hard-link basenames
    captured while the original binaries still existed, see
    :func:`capture_bundle_facts`) is threaded through as
    ``build_bundle_snapshot_from_metadata``'s ``extra_aliases`` so this
    purely metadata-driven reconstruction can still resolve a
    ``DT_NEEDED`` edge naming one of those aliases without probing the
    filesystem itself, since the persisted facts may outlive the files
    they were captured from.

    ``facts.library_filenames`` is threaded through as that same
    function's ``paths`` -- a real on-disk filename reconstructed as
    ``Path(filename)`` rather than the default ``Path(canonical_key)``
    fallback, so ``bundle._detect_soname_skew``'s own SONAME-major
    fallback sees the real, versioned filename (Codex review). A name
    absent from ``library_filenames`` falls back to the default."""
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
    a live-directory-vs-live-directory ``compare --release`` uses, so the
    two entry points share one detection implementation and can never
    independently drift. This is what the mandatory dump/live parity test
    asserts.

    *manifest*, given explicitly, overrides *old_facts.manifest* (mirroring
    ``compare_bundle()``'s own ``manifest=`` parameter); otherwise the
    manifest captured in *old_facts* is reused.

    *new_signature_evidence* (G38 stabilization Phase 12), when given and
    non-empty, is the NEW side's bundle-canonical-key -> ``AbiSnapshot``
    map for ``find_unverified_signature_findings`` -- the OLD side's own
    map is always *old_facts.per_library_snapshots* itself. Omitted (the
    default): the Phase 4 gate does not run, matching every pre-Phase-12
    caller -- there is not yet a CLI producer for a live NEW-side evidence
    map here (G38 Phase 13 is separate, not-yet-implemented).
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
# rather than importing them from `serialization.py`: that module already
# imports `BundleFacts` from here, so importing back -- even function-
# scoped -- makes the two mutually dependent, which `scripts/
# check_ai_readiness.py`'s `import-cycle-growth` check flags via static
# AST scanning regardless of laziness (caught on this module's first
# draft, not assumed). `validated_alias_map`/`validated_filename_map`
# (`bundle_facts_validation.py`, a dependency-free leaf) duplicate
# `serialization`'s own private validators for the same reason.
def maybe_write_bundle_facts_archive(
    facts: BundleFacts,
    path: str | Path,
    format: str,
    *,
    snapshot_to_dict: Callable[[AbiSnapshot], dict[str, Any]],
) -> SnapshotWriteResult | None:
    """``serialization.save_bundle_facts``'s ``format=`` dispatch: returns
    a real result for ``format="archive"``, ``None`` for ``format="json"``
    (fall through to plain-JSON), raises otherwise. *snapshot_to_dict* is
    ``serialization.snapshot_to_dict``, passed in -- see the module-level
    comment above."""
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
    ``"auto"`` sniffs *path*'s own bytes; returns a real result whenever
    the resolved format is ``"archive"``, ``None`` for ``"json"`` (fall
    through to plain-JSON), and raises for anything else.
    *snapshot_from_dict* is ``serialization.snapshot_from_dict``, passed
    in -- see the module-level comment above.

    The sniff and the follow-up archive parse share one fd, else a
    concurrent atomic replacement between two separate opens could swap
    in a different generation (Codex review)."""
    from .storage.bundle_archive import open_regular_file_for_format_sniff

    fp = None
    if format == "auto":
        fp, resolved = open_regular_file_for_format_sniff(path)
    else:
        resolved = format
    if resolved == "archive":
        return read_bundle_facts_archive(path, snapshot_from_dict=snapshot_from_dict, _fp=fp)
    # Known residual gap: for "json", *fp* is closed rather than handed to
    # `read_snapshot_text(path)`'s own open, so the sniff-then-reopen race
    # closed above still applies here -- needs a new parameter on a
    # `debt.yaml`-locked `no_growth` module (its own ADR-061 migration).
    if fp is not None:
        fp.close()
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
    ``BundleArchiveWriter.put_blob``'s own hash-addressed dedup."""
    import json as _json
    from collections import Counter

    from .errors import SnapshotError
    from .snapshot_io import SnapshotCompression, SnapshotWriteResult
    from .storage.bundle_archive import (
        DEFAULT_MAX_MANIFEST_BYTES,
        MAX_ARCHIVE_MEMBERS,
        BundleArchiveWriter,
        content_hash,
    )
    from .storage.bundle_archive_json_guard import (
        bounded_encode_utf8,
        oversized_raw_string,
    )

    p = Path(path)
    # Everything below is computed -- and every cap the reader will later
    # enforce is checked -- *before* `BundleArchiveWriter` is ever opened,
    # on the *same* unique-hash map the write further down populates.
    # Sorted by name, not dict insertion order: two logically-equal
    # BundleFacts populated differently would else produce different
    # archive bytes. (a) Library-name-count cap, independent of the
    # distinct-blob cap below (many names can share one blob) -- checked
    # before the loop, else many names sharing one large snapshot would
    # serialize it once per name first.
    if len(facts.per_library_snapshots) > DEFAULT_MAX_LIBRARY_COUNT:
        raise SnapshotError(
            f"{p}: writing this bundle's {len(facts.per_library_snapshots)} "
            f"library names would exceed the {DEFAULT_MAX_LIBRARY_COUNT} "
            "name safety limit read_bundle_facts_archive() enforces on "
            "load -- refusing to write an archive that could not be "
            "reopened."
        )
    library_blobs: dict[str, str] = {}
    decoded_size_bytes = 0
    unique_payloads: dict[str, bytes] = {}  # content hash -> its own payload
    # Keyed by id(snap), not content -- many names can legitimately share
    # the *same* AbiSnapshot object, and re-serializing it once per name
    # is real, unbounded work the dedup below never prevented. Safe:
    # every snap here stays referenced by `facts.per_library_snapshots`
    # for the loop's duration, so no id() can be reused mid-loop.
    serialized_by_identity: dict[int, tuple[str, bytes]] = {}

    def _oversized_bundle_message() -> str:
        return (
            f"{p}: writing this bundle's content already exceeds the "
            f"{DEFAULT_MAX_BUNDLE_DECODED_BYTES} byte aggregate safety "
            "limit read_bundle_facts_archive() enforces on load, once "
            "every duplicate library name's own copy is counted -- "
            "refusing to write an archive that could not be reopened."
        )

    for name, snap in sorted(facts.per_library_snapshots.items()):
        cached = serialized_by_identity.get(id(snap))
        if cached is not None:
            h, payload = cached
        else:
            # bounded_encode_utf8() streams against the *remaining*
            # allowance -- json.dumps()+.encode() would otherwise
            # materialize a full oversized copy of this one snapshot
            # before the aggregate check below ever ran (Codex review).
            remaining = max(DEFAULT_MAX_BUNDLE_DECODED_BYTES - decoded_size_bytes, 0)
            encoded = bounded_encode_utf8(snapshot_to_dict(snap), remaining)
            if encoded is None:
                raise SnapshotError(_oversized_bundle_message())
            payload = encoded
            h = content_hash(payload)
            serialized_by_identity[id(snap)] = (h, payload)
        # Charged here too, not only unique_payloads' own deduped total --
        # distinct AbiSnapshot objects that happen to serialize
        # identically (not the identity-cache case above) still each cost
        # a real serialization before their shared hash is known. Already
        # equals what reader_charged_bytes computes for names so far, so
        # this check and that one can never disagree.
        decoded_size_bytes += len(payload)
        if decoded_size_bytes > DEFAULT_MAX_BUNDLE_DECODED_BYTES:
            raise SnapshotError(_oversized_bundle_message())
        unique_payloads.setdefault(h, payload)
        library_blobs[name] = h
    manifest_blob = None
    if facts.manifest is not None:
        from .bundle_manifest import manifest_to_dict

        # Streamed the same way the per-snapshot loop above is (Codex
        # review, fresh evidence): only snapshots were bounded, so an
        # InstantiationManifest itself could still exhaust memory via a
        # full json.dumps()+.encode() before this check ever ran.
        remaining = max(DEFAULT_MAX_BUNDLE_DECODED_BYTES - decoded_size_bytes, 0)
        encoded_manifest = bounded_encode_utf8(manifest_to_dict(facts.manifest), remaining)
        if encoded_manifest is None:
            raise SnapshotError(_oversized_bundle_message())
        manifest_payload = encoded_manifest
        decoded_size_bytes += len(manifest_payload)
        manifest_blob = content_hash(manifest_payload)
        unique_payloads.setdefault(manifest_blob, manifest_payload)

    # (a) Member-count cap: one member per *distinct* blob hash, plus one
    # for `manifest.json` -- `manifest_blob`'s own hash is already inside
    # `unique_payloads` above, so nothing further to reserve for it.
    if len(unique_payloads) + 1 > MAX_ARCHIVE_MEMBERS:
        raise SnapshotError(
            f"{p}: writing this bundle's {len(unique_payloads)} distinct "
            f"blobs would produce more than {MAX_ARCHIVE_MEMBERS} zip "
            "members (the reader's own safety limit) -- refusing to write "
            "an archive that could not be reopened."
        )
    # (b) Aggregate decoded-byte cap, mirroring read_bundle_facts_archive()
    # exactly -- every *duplicate* name's own deep-copied AbiSnapshot too,
    # not each unique blob's bytes once.
    hash_counts = Counter(library_blobs.values())
    reader_charged_bytes = sum(len(unique_payloads[h]) * n for h, n in hash_counts.items())
    # manifest_blob's bytes are charged once more, unconditionally,
    # whenever present -- a raw-fetch charge on a cache miss, or the
    # reader's "second materialization" charge on a hit (hash shared with
    # a library). Charging only the not-shared case missed the shared
    # one, letting the writer accept what its own reader rejects.
    if manifest_blob is not None:
        reader_charged_bytes += len(unique_payloads[manifest_blob])
    if reader_charged_bytes > DEFAULT_MAX_BUNDLE_DECODED_BYTES:
        raise SnapshotError(
            f"{p}: writing this bundle's content, once every duplicate "
            f"library name's own copy is counted ({reader_charged_bytes} "
            f"bytes), would exceed the {DEFAULT_MAX_BUNDLE_DECODED_BYTES} "
            "byte aggregate safety limit read_bundle_facts_archive() "
            "enforces on load -- refusing to write an archive that could "
            "not be reopened."
        )

    container_manifest = {
        "schema_version": BUNDLE_ARCHIVE_SCHEMA_VERSION,
        "bundle_facts_schema_version": facts.schema_version,
        "variant_fingerprint": facts.variant_fingerprint,
        "library_blobs": library_blobs,
        "manifest_blob": manifest_blob,
        # Also sorted, same reasoning -- these two maps are unordered-by-
        # name key/value data (a library's own set of aliases/filename),
        # not order-sensitive content the way an instantiation manifest's
        # `provides:` list is (that one is deliberately left untouched,
        # per the same review).
        "filesystem_aliases": {
            name: list(aliases) for name, aliases in sorted(facts.filesystem_aliases.items())
        },
        "library_filenames": dict(sorted(facts.library_filenames.items())),
    }
    # A third cap: manifest.json's own reader-side size ceiling, never
    # covered above. Checked incrementally via iterencode(), not
    # `json.dumps()` then `.encode("utf-8")`, which would fully
    # materialize the string (and a second UTF-8 copy) first (Codex
    # review). write_manifest() re-checks identically when writing the
    # member below; checked here too so a reject happens before any blob
    # write. A single oversized string value needs its own pre-check
    # first, same reasoning as write_manifest()'s own docstring:
    # iterencode() yields one whole escaped string as a single chunk, so
    # the chunk-by-chunk loop below can't reject it on its own.
    oversized = oversized_raw_string(container_manifest, DEFAULT_MAX_MANIFEST_BYTES)
    if oversized is not None:
        _, oversized_bytes = oversized
        raise SnapshotError(
            f"{p}: this bundle's manifest.json contains a single string "
            f"value of at least {oversized_bytes} bytes, alone exceeding "
            f"the {DEFAULT_MAX_MANIFEST_BYTES} byte safety limit "
            "read_bundle_facts_archive() enforces on load -- refusing to "
            "write an archive that could not be reopened."
        )
    manifest_member_bytes = 0
    for chunk in _json.JSONEncoder(indent=2).iterencode(container_manifest):
        manifest_member_bytes += len(chunk.encode("utf-8"))
        if manifest_member_bytes > DEFAULT_MAX_MANIFEST_BYTES:
            raise SnapshotError(
                f"{p}: this bundle's manifest.json would be more than "
                f"{manifest_member_bytes} bytes, exceeding the "
                f"{DEFAULT_MAX_MANIFEST_BYTES} byte safety limit "
                "read_bundle_facts_archive() enforces on load -- refusing "
                "to write an archive that could not be reopened."
            )

    with BundleArchiveWriter(p) as writer:
        for h in sorted(unique_payloads):
            writer.put_blob(unique_payloads[h])
        writer.write_manifest(container_manifest)
    # `writer.stored_sha256`/`writer.stored_size_bytes` are computed by
    # BundleArchiveWriter.close() from the still-private temp file, before
    # os.replace() publishes it -- not re-derived via a fresh `open(p,
    # "rb")`/`p.stat()` after the `with` block: a concurrent writer
    # publishing a *different* generation to the same destination in
    # between could otherwise make the reported hash/size describe
    # someone else's write, a real hazard for a receipt (Codex review).
    assert writer.stored_sha256 is not None
    assert writer.stored_size_bytes is not None
    return SnapshotWriteResult(
        path=p,
        # NONE, not ZSTD: `compression` describes the *outer envelope*
        # `detect_snapshot_compression()`/`read_snapshot_storage_info()`
        # would independently discover by sniffing `path`'s own magic
        # bytes -- and the envelope here is a ZIP (`PK\x03\x04`), which
        # neither sniffer recognizes as a zstd frame. The zstd compression
        # is real but internal, applied per-member to each
        # `blobs/<hash>.json.zst` entry -- claiming ZSTD here would
        # mislead a caller cross-checking against an independent sniff.
        compression=SnapshotCompression.NONE,
        decoded_size_bytes=decoded_size_bytes,
        stored_size_bytes=writer.stored_size_bytes,
        stored_sha256=writer.stored_sha256,
    )


def read_bundle_facts_archive(
    path: str | Path,
    *,
    snapshot_from_dict: Callable[[dict[str, Any]], AbiSnapshot],
    _fp: Any | None = None,
) -> BundleFacts:
    """Read a G40 content-addressed zip archive at *path* back into a
    :class:`BundleFacts`.

    Loads every library's blob -- a caller wanting only one library's
    snapshot uses ``storage.bundle_archive.BundleArchiveReader`` directly.

    *_fp*, when given, is an already-open fd reused from
    ``maybe_read_bundle_facts_archive``'s own format sniff (Codex)."""
    import json as _json

    from .bundle_manifest import manifest_from_dict
    from .errors import IncompatibleSnapshotSchemaError, SnapshotError
    from .storage.bundle_archive import BundleArchiveReader

    reader_cm = (
        BundleArchiveReader.from_open_file(_fp, path)
        if _fp is not None
        else BundleArchiveReader.open(path)
    )
    with reader_cm as reader:
        manifest = reader.read_manifest()
        # The *container's* own schema_version (BUNDLE_ARCHIVE_SCHEMA_VERSION
        # -- the manifest/blob shape itself) is a separate axis from
        # bundle_facts_schema_version (the BundleFacts shape it encodes) --
        # see the two constants' own module-level comments. Fails closed
        # rather than silently misreading a newer container as version 1.
        archive_schema_version = int(manifest.get("schema_version", 1))
        if archive_schema_version > BUNDLE_ARCHIVE_SCHEMA_VERSION:
            raise IncompatibleSnapshotSchemaError(
                f"Bundle archive container schema_version {archive_schema_version} "
                "is newer than this abicheck (supports up to schema_version "
                f"{BUNDLE_ARCHIVE_SCHEMA_VERSION}). Upgrade abicheck to read "
                "this bundle archive."
            )
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
        if "library_blobs" not in manifest:
            raise ValueError(
                "bundle archive: manifest.json is missing required key "
                "'library_blobs' -- not a BundleArchiveWriter-produced archive"
            )
        library_blobs = manifest["library_blobs"]
        if not isinstance(library_blobs, dict):
            raise ValueError(
                "bundle archive: 'library_blobs' must be a mapping, got "
                f"{type(library_blobs).__name__}"
            )
        # Each value must be a content-hash string -- an unvalidated list/
        # dict reaches snapshot_cache/blob_cache below and raises a raw
        # TypeError instead of this module's own error type.
        for _name, _h in library_blobs.items():
            if not isinstance(_h, str):
                raise ValueError(
                    f"bundle archive: 'library_blobs[{_name!r}]' must be a "
                    f"content-hash string, got {type(_h).__name__}"
                )
        # Bound the *name count* independently of decoded-byte size: each
        # *name* referencing a shared blob still gets its own materialized
        # AbiSnapshot object graph (snapshot_cache's own deep copy), so an
        # oversized manifest could amplify one small blob into an
        # unbounded object count. Checked before any blob is read.
        if len(library_blobs) > DEFAULT_MAX_LIBRARY_COUNT:
            raise SnapshotError(
                f"{path}: this bundle archive's manifest names "
                f"{len(library_blobs)} libraries, exceeding the "
                f"{DEFAULT_MAX_LIBRARY_COUNT} safety limit -- refusing to "
                "continue loading (possible object-count amplification "
                "attack, or a genuinely oversized bundle)."
            )
        # Aggregate cap across the whole load, plus a cache keyed by hash
        # so many library names sharing one blob decompress it once, not
        # once per name.
        total_decoded = 0
        blob_cache: dict[str, bytes] = {}

        def _cached_blob(h: str) -> bytes:
            nonlocal total_decoded
            cached = blob_cache.get(h)
            if cached is not None:
                return cached
            # Pass the *remaining* aggregate allowance as this read's own
            # per-blob cap, not read_blob's full 1 GiB default -- else peak
            # decoded memory could exceed the whole-load limit.
            remaining = DEFAULT_MAX_BUNDLE_DECODED_BYTES - total_decoded
            if remaining <= 0:
                raise SnapshotError(
                    f"{path}: this bundle archive's total decoded size "
                    f"exceeds the {DEFAULT_MAX_BUNDLE_DECODED_BYTES} byte "
                    "safety limit across its library_blobs -- refusing to "
                    "continue loading (possible decompression bomb, or a "
                    "genuinely oversized bundle)."
                )
            raw = reader.read_blob(h, max_decoded_bytes=remaining)
            total_decoded += len(raw)
            blob_cache[h] = raw
            return raw

        # read_blob() verifies the blob's *hash*, not its JSON *shape* -- a
        # blob that legitimately decodes to a list/scalar must raise this
        # module's own error type below, not a raw AttributeError.
        #
        # snapshot_cache is keyed by hash too, alongside blob_cache above,
        # avoiding a repeat snapshot_from_dict() call per name sharing one
        # blob. Each *name* still gets its own AbiSnapshot instance in the
        # returned mapping (AbiSnapshot is mutable, BundleFacts is public
        # API, so no two names may alias one object): the first-built
        # instance is held unmodified, every later name sharing that hash
        # gets a deep copy, matching `bundle_facts_from_dict()`'s own
        # one-instance-per-entry contract.
        snapshot_cache: dict[str, AbiSnapshot] = {}
        snapshot_blob_len: dict[str, int] = {}
        per_library_snapshots: dict[str, AbiSnapshot] = {}
        for name, h in library_blobs.items():
            cached_snapshot = snapshot_cache.get(h)
            if cached_snapshot is not None:
                # blob_cache/total_decoded above only charge a shared
                # blob's bytes *once* -- but every duplicate name here
                # still materializes its own deep-copied AbiSnapshot
                # object graph, so many names sharing one blob could
                # amplify past the aggregate cap in live objects alone.
                # Charge each copy's own blob byte length against budget.
                copy_bytes = snapshot_blob_len[h]
                if total_decoded + copy_bytes > DEFAULT_MAX_BUNDLE_DECODED_BYTES:
                    raise SnapshotError(
                        f"{path}: this bundle archive's total decoded "
                        f"size exceeds the {DEFAULT_MAX_BUNDLE_DECODED_BYTES} "
                        "byte safety limit once every duplicate library "
                        "name's own materialized copy is counted -- "
                        "refusing to continue loading (possible "
                        "object-count amplification attack, or a "
                        "genuinely oversized bundle)."
                    )
                total_decoded += copy_bytes
                per_library_snapshots[name] = copy.deepcopy(cached_snapshot)
                continue
            raw = _cached_blob(h)
            blob = _json.loads(raw)
            if not isinstance(blob, dict):
                raise ValueError(
                    f"bundle archive: blob for library {name!r} must decode "
                    f"to a JSON object, got {type(blob).__name__}"
                )
            snap = snapshot_from_dict(blob)
            snapshot_cache[h] = snap
            snapshot_blob_len[h] = len(raw)
            per_library_snapshots[name] = snap
        manifest_blob = manifest.get("manifest_blob")
        instantiation_manifest = None
        if manifest_blob is not None:
            # Same validation as library_blobs' values above -- an
            # unvalidated non-string reaches blob_cache.get(h), a keyed
            # dict, raising a raw TypeError (Codex review).
            if not isinstance(manifest_blob, str):
                raise ValueError(
                    "bundle archive: 'manifest_blob' must be a content-hash "
                    f"string, got {type(manifest_blob).__name__}"
                )
            # _cached_blob() only charges a hash's raw bytes against
            # total_decoded once, on its first fetch -- but json.loads()+
            # manifest_from_dict() below always build a *fresh* object
            # graph regardless of cache hit. On a hit (manifest_blob shares
            # a hash with an already-fetched library blob) this is the same
            # "duplicate materialization" the per-library-name loop above
            # already re-charges for its own deep-copied AbiSnapshot --
            # uncharged here would let a shared blob parse twice, billed
            # once (Codex review, fresh evidence). The writer mirrors this
            # exact accounting in its own aggregate-byte preflight.
            was_cached = manifest_blob in blob_cache
            raw_manifest = _cached_blob(manifest_blob)
            if was_cached:
                copy_bytes = len(raw_manifest)
                if total_decoded + copy_bytes > DEFAULT_MAX_BUNDLE_DECODED_BYTES:
                    raise SnapshotError(
                        f"{path}: this bundle archive's total decoded size "
                        f"exceeds the {DEFAULT_MAX_BUNDLE_DECODED_BYTES} "
                        "byte safety limit once the instantiation "
                        "manifest's own second materialization is counted "
                        "-- refusing to continue loading (possible "
                        "object-count amplification attack, or a "
                        "genuinely oversized bundle)."
                    )
                total_decoded += copy_bytes
            instantiation_manifest = manifest_from_dict(_json.loads(raw_manifest))
        return BundleFacts(
            schema_version=bundle_facts_schema_version,
            variant_fingerprint=str(
                manifest.get("variant_fingerprint", DEFAULT_VARIANT_FINGERPRINT)
            ),
            per_library_snapshots=per_library_snapshots,
            manifest=instantiation_manifest,
            filesystem_aliases=validated_alias_map(
                manifest.get("filesystem_aliases", {})
            ),
            library_filenames=validated_filename_map(
                manifest.get("library_filenames", {})
            ),
        )
