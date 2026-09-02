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

"""The directory-backed half of ADR-062 A1.1 — `storage/package.py` owns the
`ProjectSnapshot` package's *logical* object model (`PackageManifest`,
`VariantRef`, `ArtifactRef`, `ObjectRef`, the `ObjectStore` protocol); this
module is the concrete, filesystem-backed `ObjectStore` implementation and
the manifest/ref writer/reader over ADR-059's physical envelope
(`abicheck/snapshot_io.py`) — the "real writer" that module's own docstring
says lives outside `storage/` because that package may depend only on
`model` (`storage/AGENTS.md`, "Permitted imports") and therefore cannot
itself import `snapshot_io`.

D6's layout, exactly:

    project.abicheck/
      manifest.json            # small; loads immediately
      refs/variants/<variant-id>.json
      refs/artifacts/<artifact-id>.json
      objects/sha256/<aa>/<digest>.json.zst

`manifest.json` deliberately does **not** embed full `variant_refs`/
`artifact_refs` records the way `PackageManifest.to_dict()` does — that
in-memory convenience is explicitly what `package.py`'s own docstring flags
as "premature" until a real writer exists to make the split meaningful. This
module is that writer: `manifest.json` carries only `versions` plus the two
id lists, and each variant/artifact's full record lives at its own
`refs/*.json` path (`variant_ref_relpath`/`artifact_ref_relpath`), read
lazily — `read_variant_ref`/`read_artifact_ref` load exactly one, and
`read_manifest_summary` loads only the small root document, matching D8's
"a project comparison loads two manifests... then one matched library pair
at a time" access pattern. `read_project_manifest` assembles a full,
in-memory `PackageManifest` (every ref eagerly loaded) purely as a
convenience for a caller — a test, a one-shot inspection tool — that wants
the whole thing at once; it is built from the same lazy primitives, not a
second read path.

The transport `.tar.zst` form D6 also describes is not implemented here —
see `docs/contribute/adr/062-project-snapshot-storage-v2.md`'s Status for
what remains open.

**Registers `docs/_meta/topics.yaml`'s `project-snapshot-storage` topic**
(`docs/reference/project-snapshot-format.md`) — this is the module that
first actually persists a `ProjectSnapshot`, the trigger `storage-format-
v2.md`'s "Documentation ownership" section names explicitly.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .snapshot_io import (
    SnapshotCompression,
    read_snapshot_bytes,
    write_snapshot_bytes,
)
from .storage.canonical import (
    canonical_form,
    canonical_json,
    raw_digest,
    semantic_digest,
    strip_capture_metadata,
)
from .storage.package import (
    MANIFEST_RELPATH,
    ArtifactRef,
    PackageManifest,
    VariantRef,
    _reject_filesystem_collisions,
    artifact_ref_relpath,
    object_relpath,
    variant_ref_relpath,
)
from .storage.versioning import StorageVersions, check_reader_compatibility

__all__ = [
    "DirectoryObjectStore",
    "ManifestSummary",
    "read_artifact_ref",
    "read_manifest_summary",
    "read_project_manifest",
    "read_variant_artifact_pair",
    "read_variant_ref",
    "variant_and_artifact_ids",
    "write_project_manifest",
]

#: `zstd` level object content is written at. D12 reserves a real policy
#: table (extraction-cache/CI-baseline/archival levels); this module writes
#: everything at the middle, ordinary-CI-baseline level until a caller that
#: actually distinguishes those three cases exists to pick between them —
#: inventing that distinction here, with nothing yet calling it, would be
#: exactly the guessed-shape risk `package.py`'s own deferred-gap notes warn
#: against.
_OBJECT_ZSTD_LEVEL = 10


def _object_json_relpath(digest: str) -> str:
    return f"{object_relpath(digest)}.zst"


def _object_raw_relpath(digest: str) -> str:
    json_relpath = object_relpath(digest)
    assert json_relpath.endswith(".json")
    return f"{json_relpath[: -len('.json')]}.bin.zst"


def _is_binary_buffer(value: Any) -> bool:
    return isinstance(value, (bytes, bytearray, memoryview))


def _write_canonical_json_text(
    text: str,
    path: Path,
    *,
    compression: SnapshotCompression,
    zstd_level: int | None = None,
) -> None:
    """Write *text* -- already `canonical_json`'s output -- to *path*, safe
    for content containing a lone surrogate.

    `canonical_json` deliberately keeps `ensure_ascii=False` for
    readability (its own docstring), so a value round-tripped through
    `surrogateescape` -- a real POSIX path carrying a non-UTF-8 byte,
    `os.fsdecode(b"caf\\xe9")` == `"caf\\udce9"` -- survives `canonical_json`
    with the lone surrogate intact, even though `semantic_digest` already
    accepts and addresses that same content (its own docstring names this
    exact asymmetry and closes it for the *digest* path specifically).
    `write_snapshot_text`'s plain `text.encode("utf-8")` is strict, so
    writing that text as-is raises `UnicodeEncodeError` mid-write --
    content this store's own digest accepted, and `InMemoryObjectStore`
    already accepts, rejected only once persisted to a real directory
    (Codex review).

    `errors="backslashreplace"` replaces only the unencodable surrogate
    scalar with its literal `\\udcXX` text — which, since the surrogate
    sits inside an already-JSON-quoted string, is indistinguishable from a
    real JSON `\\u` escape and round-trips through `json.loads` back to the
    identical lone surrogate (verified by property test). Every other
    character, including ordinary non-ASCII text, still encodes normally,
    so this only changes the on-disk representation for the one case that
    would otherwise fail outright.
    """
    write_snapshot_bytes(
        text.encode("utf-8", errors="backslashreplace"),
        path,
        compression=compression,
        zstd_level=zstd_level,
    )


class DirectoryObjectStore:
    """A real, `ObjectStore`-conforming filesystem directory — D7's
    `put`/`get`/`has` over ADR-059's compressed, atomic, decompression-bomb-
    guarded envelope.

    Every object is written zstd-compressed, matching D6's own example
    layout (`objects/sha256/<aa>/<digest>.json.zst`) — plain/gzip storage is
    a snapshot-envelope choice this store does not expose, since nothing
    about a content-addressed, deduplicated object benefits from the
    per-file choice a top-level snapshot's `--compression` flag exists for.

    A JSON-shaped and a raw-binary object can share one hex digest only in
    the same astronomically unlikely case `storage/canonical.py`'s own
    domain separation already accepts (two different domain tags hashed
    ahead of unrelated payloads) — this store additionally never writes both
    a `.json.zst` and a `.bin.zst` object under the same digest, so `get()`
    can distinguish them by which file exists rather than needing a separate
    index.
    """

    def __init__(
        self, root: str | Path, *, zstd_level: int = _OBJECT_ZSTD_LEVEL
    ) -> None:
        self._root = Path(root)
        self._zstd_level = zstd_level

    def _json_path(self, digest: str) -> Path:
        return self._root / _object_json_relpath(digest)

    def _raw_path(self, digest: str) -> Path:
        return self._root / _object_raw_relpath(digest)

    def put(self, content: Any, *, algorithm: str = "sha256") -> str:
        if _is_binary_buffer(content):
            payload = bytes(content)
            digest = raw_digest(payload, algorithm=algorithm)
            path = self._raw_path(digest)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                write_snapshot_bytes(
                    payload,
                    path,
                    compression=SnapshotCompression.ZSTD,
                    zstd_level=self._zstd_level,
                )
            return digest
        stripped = strip_capture_metadata(content)
        digest = semantic_digest(stripped, algorithm=algorithm)
        path = self._json_path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_canonical_json_text(
                canonical_json(stripped),
                path,
                compression=SnapshotCompression.ZSTD,
                zstd_level=self._zstd_level,
            )
        return digest

    def get(self, digest: str) -> Any:
        if not isinstance(digest, str):
            raise TypeError(f"digest must be a string, not {type(digest).__name__}")
        # The digest names the *algorithm* too (`object_relpath` already
        # requires this to parse), so a corrupted or hand-edited object file
        # is verified against exactly the algorithm the caller asked for --
        # not merely "some hash of whatever bytes happen to be on disk".
        algorithm, _separator, _hexdigest = digest.partition(":")
        json_path = self._json_path(digest)
        if json_path.exists():
            content = canonical_form(json.loads(read_snapshot_bytes(json_path)))
            actual = semantic_digest(content, algorithm=algorithm)
            if actual != digest:
                raise ValueError(
                    f"{json_path} does not match its requested digest {digest!r} "
                    f"(recomputed {actual!r}) -- the object may be corrupted or "
                    "was hand-edited"
                )
            # `semantic_digest` strips the reserved root `capture` block
            # before hashing (D3), so a hand-edited object file that adds an
            # arbitrary `capture` subtree back in still matches `digest` --
            # the subtree isn't in the hash domain. Returning `content`
            # as-is would then hand back data the requested digest does not
            # actually address, disagreeing with both this store's own
            # `put()` (which never persists that subtree in the first place)
            # and `ObjectStore.get()`'s documented contract ("with the
            # reserved root `capture` block removed, matching what
            # `semantic_digest` hashed") that `InMemoryObjectStore` already
            # satisfies (Codex review).
            return strip_capture_metadata(content)
        raw_path = self._raw_path(digest)
        if raw_path.exists():
            payload = read_snapshot_bytes(raw_path)
            actual = raw_digest(payload, algorithm=algorithm)
            if actual != digest:
                raise ValueError(
                    f"{raw_path} does not match its requested digest {digest!r} "
                    f"(recomputed {actual!r}) -- the object may be corrupted or "
                    "was hand-edited"
                )
            return payload
        raise KeyError(f"no object stored under digest {digest!r} in {self._root}")

    def has(self, digest: str) -> bool:
        if not isinstance(digest, str):
            raise TypeError(f"digest must be a string, not {type(digest).__name__}")
        return self._json_path(digest).exists() or self._raw_path(digest).exists()


def write_project_manifest(root: str | Path, manifest: PackageManifest) -> None:
    """Fan *manifest* out across the D6 directory tree rooted at *root*:
    the small `manifest.json` plus one `refs/variants/*.json`/
    `refs/artifacts/*.json` document per record. Does not touch `objects/` —
    a caller populates those separately (typically via `DirectoryObjectStore
    .put`, e.g. through `storage.import_v1.import_legacy_snapshot`) before
    or after writing the manifest; nothing here depends on `objects/`
    ordering, since `ObjectStore.put` is idempotent and content-addressed.

    **`manifest.json` is written last, as this function's own commit
    point.** Every `refs/*.json` document is written first: an interruption
    partway through would then leave, at worst, ref documents `manifest.json`
    does not yet name (harmless — nothing reads a ref it wasn't told to load)
    rather than a durable `manifest.json` naming refs that were never
    written, which every subsequent reader would treat as a corrupted
    package (Codex review).

    **A known, deliberately deferred gap** (flagged in the same review
    round as a distinct, further finding once the ordering above was
    fixed): this ordering makes *first publication* of a set of ids safe,
    but not *republishing changed content under ids that are already
    live*. Calling this function a second time against a package another
    reader might concurrently be loading — with a `VariantRef`/
    `ArtifactRef` whose `variant_id`/`artifact_id` repeats but whose
    content differs — can overwrite a `refs/*.json` file the *currently
    published* `manifest.json` still names, so a concurrent reader (or an
    interruption before this call's own final `manifest.json` write lands)
    can observe a mix of the old manifest and the new ref content, which
    neither commit represents. Closing this needs either a staged-
    directory-then-atomic-root-swap publish protocol or content-addressed
    (never-overwritten) ref paths — a real design decision, not a small
    fix, and out of scope for A1.1's first landing: nothing in this
    package today calls this function more than once against one root
    (every current caller — the tests, `storage.import_v1`'s own tests —
    creates a fresh package once), so there is no real update/republish
    caller yet to design the fix against. Revisit once A1.6/A1.7
    (variant capture, stored/live comparison) gives this a real caller
    that republishes an existing package.
    """
    root_path = Path(root)
    for variant in manifest.variant_refs:
        variant_path = root_path / variant_ref_relpath(variant.variant_id)
        variant_path.parent.mkdir(parents=True, exist_ok=True)
        _write_canonical_json_text(
            canonical_json(variant.to_dict(), indent=2),
            variant_path,
            compression=SnapshotCompression.NONE,
        )
    for artifact in manifest.artifact_refs:
        artifact_path = root_path / artifact_ref_relpath(artifact.artifact_id)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        _write_canonical_json_text(
            canonical_json(artifact.to_dict(), indent=2),
            artifact_path,
            compression=SnapshotCompression.NONE,
        )
    summary = {
        "versions": manifest.versions.to_dict(),
        "variant_ids": [variant.variant_id for variant in manifest.variant_refs],
        "artifact_ids": [artifact.artifact_id for artifact in manifest.artifact_refs],
    }
    manifest_path = root_path / MANIFEST_RELPATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_canonical_json_text(
        canonical_json(summary, indent=2),
        manifest_path,
        compression=SnapshotCompression.NONE,
    )


class ManifestSummary:
    """`manifest.json`'s own small, always-loaded content — `versions` plus
    which variant/artifact ids exist, without either's full record."""

    __slots__ = ("versions", "variant_ids", "artifact_ids")

    def __init__(
        self,
        versions: StorageVersions,
        variant_ids: tuple[str, ...],
        artifact_ids: tuple[str, ...],
    ) -> None:
        self.versions = versions
        self.variant_ids = variant_ids
        self.artifact_ids = artifact_ids


def _required_string_id_list(
    data: Mapping[str, Any], field_name: str, record: str
) -> tuple[str, ...]:
    """*data[field_name]* as a tuple of ids, requiring the field to be
    present as a real JSON array of strings -- never inferred from absence.

    Two distinct malformed-input mistakes, closed together:

    `tuple(raw)` alone accepts far more than a well-formed manifest ever
    writes: a bare string iterates into one id per *character*, and a
    mapping iterates into its keys -- either can produce a plausible-looking
    tuple of strings from a document that is not actually a list at all
    (Codex review). A missing key is a second, separate case: this
    function's own caller previously read a missing `variant_ids`/
    `artifact_ids` via `.get(field_name)`, so `None` reached here and was
    treated the same as an explicitly-written empty list -- silently
    accepting a truncated manifest as a valid, empty package rather than
    refusing a document that never stated its membership at all (Codex
    review, a second round on the same field). Requiring the key present
    closes both: this module's writer (`write_project_manifest`) always
    writes both fields, even as `[]` when a package genuinely has no
    variants/artifacts, so "key absent" is a fact about the *document*, not
    about a package this module ever produces.

    Also refuses a duplicate id. `PackageManifest.__post_init__` already
    catches this, but only on the *eager* `read_project_manifest` path,
    after both this function and every named `refs/*.json` document have
    already been loaded -- `read_manifest_summary`/`variant_and_artifact_ids`
    (the lazy primitives D8 exists for, and the ones a real section-aware
    reader is meant to use directly) never construct a `PackageManifest` at
    all, so a manifest listing the same variant or artifact id twice passed
    silently through them and could be processed more than once by a caller
    that trusts `variant_ids`/`artifact_ids` to name a package's membership
    (Codex review). Checked here, the one place both read paths already
    share, rather than duplicating the check in each caller.

    Also refuses two distinct ids that a real filesystem would still treat
    as the same path -- differing only by case, or by Unicode normalization
    -- for the identical reason and via the identical
    `_reject_filesystem_collisions` helper `PackageManifest.__post_init__`
    already applies to `variant_refs`/`artifact_refs`. That check is, once
    again, only reachable through the eager `read_project_manifest` path:
    the lazy primitives this function backs never construct a
    `PackageManifest`, so on a case- or normalization-insensitive
    filesystem two members that collide on one `refs/*.json` path would
    otherwise pass this door and fail only later, deep inside whichever ref
    happens to load second (Codex review, a second finding on this same
    field).
    """
    if field_name not in data:
        raise ValueError(f"{record} is missing required field {field_name!r}")
    raw = data[field_name]
    if not isinstance(raw, list):
        raise ValueError(
            f"{field_name} must be a JSON array of strings, not {type(raw).__name__}"
        )
    for index, entry in enumerate(raw):
        if not isinstance(entry, str):
            raise ValueError(
                f"{field_name}[{index}] must be a string, got "
                f"{type(entry).__name__} ({entry!r})"
            )
    if len(set(raw)) != len(raw):
        counts: dict[str, int] = {}
        for entry in raw:
            counts[entry] = counts.get(entry, 0) + 1
        duplicates = sorted(entry for entry, count in counts.items() if count > 1)
        raise ValueError(
            f"{record} {field_name} contains duplicate id(s): {duplicates} -- "
            "a package's declared membership cannot name the same variant or "
            "artifact twice"
        )
    _reject_filesystem_collisions(raw, field_name.removesuffix("s"))
    return tuple(raw)


def read_manifest_summary(root: str | Path) -> ManifestSummary:
    """Load only `manifest.json` — the one document D8 requires be small
    enough to load unconditionally.

    Refuses a package this build cannot safely read *before* returning
    anything a caller might go on to load `refs/*.json` from: D2's two
    fail-closed version axes (`package_format_version`/
    `comparison_contract_version`) exist precisely so an unrecognized
    container layout or comparison contract is refused rather than silently
    parsed as if it were this build's own (Codex review — no other
    production call site performed this check yet).
    """
    root_path = Path(root)
    data = json.loads(read_snapshot_bytes(root_path / MANIFEST_RELPATH))
    if not isinstance(data, dict):
        raise ValueError(f"{root_path / MANIFEST_RELPATH} is not a JSON object")
    versions = StorageVersions.from_dict(data.get("versions", {}))
    compatibility = check_reader_compatibility(versions)
    if not compatibility.readable:
        raise ValueError(
            f"{root_path / MANIFEST_RELPATH} is not readable by this build: "
            f"{compatibility.reason}"
        )
    record = str(root_path / MANIFEST_RELPATH)
    return ManifestSummary(
        versions=versions,
        variant_ids=_required_string_id_list(data, "variant_ids", record),
        artifact_ids=_required_string_id_list(data, "artifact_ids", record),
    )


def read_variant_ref(root: str | Path, variant_id: str) -> VariantRef:
    """Load exactly one variant's own ref document.

    Refuses a document whose own embedded `variant_id` disagrees with the
    one requested (and therefore with the filename it was loaded from): a
    corrupted or hand-edited `refs/variants/<id>.json` could otherwise name
    a *different* variant, and the caller — which asked for `variant_id` by
    name — would silently receive that other variant's record instead
    (Codex review). `object_relpath`-style validation is not enough here,
    since the mismatch is between two otherwise well-formed values, not a
    malformed one.
    """
    root_path = Path(root)
    data = json.loads(read_snapshot_bytes(root_path / variant_ref_relpath(variant_id)))
    if not isinstance(data, dict):
        raise ValueError(f"variant ref for {variant_id!r} is not a JSON object")
    ref = VariantRef.from_dict(data)
    if ref.variant_id != variant_id:
        raise ValueError(
            f"refs/variants/{variant_id}.json names variant_id "
            f"{ref.variant_id!r}, not the requested {variant_id!r} -- the "
            "package's membership cannot be trusted"
        )
    return ref


def read_artifact_ref(root: str | Path, artifact_id: str) -> ArtifactRef:
    """Load exactly one artifact's own ref document.

    Refuses a document whose own embedded `artifact_id` disagrees with the
    one requested, for the identical reason `read_variant_ref` does (Codex
    review).
    """
    root_path = Path(root)
    data = json.loads(
        read_snapshot_bytes(root_path / artifact_ref_relpath(artifact_id))
    )
    if not isinstance(data, dict):
        raise ValueError(f"artifact ref for {artifact_id!r} is not a JSON object")
    ref = ArtifactRef.from_dict(data)
    if ref.artifact_id != artifact_id:
        raise ValueError(
            f"refs/artifacts/{artifact_id}.json names artifact_id "
            f"{ref.artifact_id!r}, not the requested {artifact_id!r} -- the "
            "package's membership cannot be trusted"
        )
    return ref


def read_variant_artifact_pair(
    root: str | Path, variant_id: str, artifact_id: str
) -> tuple[VariantRef, ArtifactRef]:
    """Load one (variant, artifact) pair together, cross-checking the
    two-way membership `PackageManifest.__post_init__` already enforces
    eagerly for the *whole* graph (a variant's `artifact_ids` must be
    exactly the set of artifacts whose own `variant_id` names it) --
    scoped here to the one pair a caller actually selected.

    `read_variant_ref`/`read_artifact_ref` alone each validate only their
    own document's self-consistency (its embedded id matches the id it was
    requested by) -- neither knows the other exists, so a malformed
    package where an artifact's own `variant_id` names a real, declared
    variant that simply omits it from that variant's own `artifact_ids`
    (or the reverse) passes through either lazy primitive alone, silently,
    even though the eager `read_project_manifest` path already rejects
    that exact graph (Codex review). Since these are documented as the
    intended section-aware lazy read path -- the one a real two-sided
    comparison is meant to build on once one exists -- a caller loading a
    specific (variant, artifact) pair through them should get the same
    guarantee `PackageManifest` already gives a caller that loads
    everything.

    Raises `ValueError` if the two documents disagree about whether this
    pair belongs together, in either direction.
    """
    variant = read_variant_ref(root, variant_id)
    artifact = read_artifact_ref(root, artifact_id)
    if artifact.variant_id != variant_id:
        raise ValueError(
            f"refs/artifacts/{artifact_id}.json names variant_id "
            f"{artifact.variant_id!r}, not the requested {variant_id!r} -- "
            "this artifact does not belong to the requested variant"
        )
    if artifact_id not in variant.artifact_ids:
        raise ValueError(
            f"refs/variants/{variant_id}.json does not list artifact_id "
            f"{artifact_id!r} in its own artifact_ids, even though "
            f"refs/artifacts/{artifact_id}.json names variant_id "
            f"{variant_id!r} -- the package's membership graph is "
            "self-contradictory"
        )
    return variant, artifact


def variant_and_artifact_ids(
    root: str | Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(variant_ids, artifact_ids)` from `manifest.json` alone — the
    membership a caller needs before deciding which `refs/*.json` documents
    to load next."""
    summary = read_manifest_summary(root)
    return summary.variant_ids, summary.artifact_ids


def read_project_manifest(root: str | Path) -> PackageManifest:
    """The whole package's `PackageManifest`, every ref eagerly loaded.

    A convenience assembled from `read_manifest_summary`/`read_variant_ref`/
    `read_artifact_ref` — the same lazy primitives a real, section-aware
    reader uses — never a second, independent read path. Prefer the lazy
    primitives directly for anything that does not genuinely need every
    variant and artifact in memory at once (D8's whole reason for existing).
    """
    root_path = Path(root)
    summary = read_manifest_summary(root_path)
    variants = tuple(
        read_variant_ref(root_path, variant_id) for variant_id in summary.variant_ids
    )
    artifacts = tuple(
        read_artifact_ref(root_path, artifact_id)
        for artifact_id in summary.artifact_ids
    )
    return PackageManifest(
        versions=summary.versions, variant_refs=variants, artifact_refs=artifacts
    )
