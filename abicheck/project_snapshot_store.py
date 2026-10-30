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

D6's layout as amended by D17 (package format v2):

    project.abicheck/
      manifest.json            # small; loads immediately
      refs/variants/<variant-id>/<ref-digest>.json
      refs/artifacts/<artifact-id>/<ref-digest>.json
      objects/sha256/<aa>/<digest>.json.zst

A ref document is written once and never overwritten; `manifest.json`
names it by `{id, ref_digest}`, so republishing never touches a live ref.
A v1 package's refs at `refs/<kind>/<id>.json` are still read.

`manifest.json` deliberately does **not** embed full `variant_refs`/
`artifact_refs` records the way `PackageManifest.to_dict()` does — that
in-memory convenience is explicitly what `package.py`'s own docstring flags
as "premature" until a real writer exists to make the split meaningful. This
module is that writer: `manifest.json` carries only `versions` plus one
`{id, ref_digest}` entry per variant/artifact, and each full record lives
at its own ref path, read
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
from types import MappingProxyType
from typing import Any

from .errors import SnapshotError
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
    ObjectRef,
    PackageManifest,
    VariantRef,
    artifact_ref_relpath,
    object_relpath,
    variant_ref_relpath,
)
from .storage.package_publish import (
    REF_ENTRY_FIELDS,
    publish_lock_name,
    ref_digest_entries,
    ref_digest_of,
    ref_document,
    ref_record,
    versioned_ref_relpath,
)
from .storage.ref_ids import (
    reject_filesystem_collisions as _reject_filesystem_collisions,
    safe_ref_id as _safe_ref_id,
)
from .storage.versioning import (
    PACKAGE_FORMAT_VERSION,
    StorageVersions,
    reader_generation_compatibility,
)
from .storage.wire_extensions import check_must_understand

__all__ = [
    "DirectoryObjectStore",
    "ManifestSummary",
    "read_artifact_ref",
    "read_manifest_summary",
    "read_variant_artifact_pair",
    "read_variant_ref",
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

    @staticmethod
    def _existing_json_object_is_valid(path: Path, digest: str, algorithm: str) -> bool:
        """Whether *path* already holds content that hashes to *digest*
        under *algorithm* -- read defensively, since any read/parse
        failure here means the existing file is not trustworthy
        (corrupted, truncated, hand-edited into something unparseable),
        not a reason for `put()` to raise instead of just overwriting it.
        """
        try:
            content = canonical_form(json.loads(read_snapshot_bytes(path)))
        except (SnapshotError, OSError, ValueError, TypeError):
            return False
        return semantic_digest(content, algorithm=algorithm) == digest

    @staticmethod
    def _existing_raw_object_is_valid(path: Path, digest: str, algorithm: str) -> bool:
        try:
            payload = read_snapshot_bytes(path)
        except (SnapshotError, OSError):
            return False
        return raw_digest(payload, algorithm=algorithm) == digest

    def put(self, content: Any, *, algorithm: str = "sha256") -> str:
        if _is_binary_buffer(content):
            payload = bytes(content)
            digest = raw_digest(payload, algorithm=algorithm)
            path = self._raw_path(digest)
            # Verify, never merely trust, an existing path before skipping
            # the write: a previously stored object corrupted or partially
            # replaced out from under this store -- on disk, not through
            # `put()`/`get()` -- would otherwise let a `put()` for the
            # *original*, correct content silently "succeed" over the
            # known-bad file, publishing a manifest that references it; the
            # corruption would then surface only once a later `get()` call
            # happened to read it (Codex review). Idempotent either way:
            # matching content is left alone, non-matching or unreadable
            # content is overwritten with the content this call was
            # actually asked to store.
            if not (
                path.exists()
                and self._existing_raw_object_is_valid(path, digest, algorithm)
            ):
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
        if not (
            path.exists()
            and self._existing_json_object_is_valid(path, digest, algorithm)
        ):
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
    """Publish *manifest* at *root* (ADR-062 D17, package format v2): verify
    every section object, write each ref document once to its digest-named
    path, then replace `manifest.json` -- `{id, ref_digest}` per ref -- by
    temp file plus atomic rename, the single commit point. Section objects
    are the caller's to `put()` first (`DirectoryObjectStore.put`).

    Raises `ValueError`, before writing anything, if any section digest
    cannot be read back valid via `DirectoryObjectStore.get()`. No file the
    live manifest names is ever rewritten, so a failed or interrupted
    publish -- even a changed-content republish under the same ids -- leaves
    the previous package readable. Writers to one *root* are serialized by
    `cross_process_lock.single_flight`; where it cannot lock, interleaved
    writers still cannot mix packages (one atomic rename over immutable refs
    each). Superseded refs and objects stay behind as unnamed garbage.
    """
    from .storage.cross_process_lock import single_flight

    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    with single_flight(publish_lock_name(str(root_path.resolve()))):
        store = DirectoryObjectStore(root_path)
        owners = [
            (f"artifact {a.artifact_id!r}", a.sections) for a in manifest.artifact_refs
        ]
        owners += [
            (f"variant {v.variant_id!r}", v.sections) for v in manifest.variant_refs
        ]
        owners.append(("project_sections", manifest.project_sections))
        invalid: list[str] = []
        for owner, sections in owners:
            for section_kind, ref in sections.items():
                try:
                    store.get(ref.digest)
                except (KeyError, ValueError, SnapshotError) as exc:
                    invalid.append(
                        f"{owner} section {section_kind!r} -> {ref.digest!r} ({exc})"
                    )
        if invalid:
            raise ValueError(
                "refusing to publish manifest.json referencing object(s) not "
                f"yet durable and valid in objects/ under {root_path}: "
                f"{sorted(invalid)} -- populate objects/ (e.g. via "
                "DirectoryObjectStore.put) before calling write_project_manifest"
            )
        versions = manifest.versions.to_dict()
        versions["package_format_version"] = PACKAGE_FORMAT_VERSION
        summary: dict[str, Any] = {"versions": versions}
        for kind, refs in (
            ("variant", [(v.variant_id, v.to_dict()) for v in manifest.variant_refs]),
            (
                "artifact",
                [(a.artifact_id, a.to_dict()) for a in manifest.artifact_refs],
            ),
        ):
            summary[REF_ENTRY_FIELDS[kind]] = [
                {
                    "id": ref_id,
                    "ref_digest": _write_ref(root_path, kind, ref_id, record),
                }
                for ref_id, record in refs
            ]
        if manifest.must_understand:  # ADR-062 D14; omitted when empty
            summary["must_understand"] = list(manifest.must_understand)
        if manifest.project_sections:  # omitted when empty
            summary["project_sections"] = {
                key: ref.to_dict() for key, ref in manifest.project_sections.items()
            }
        _write_canonical_json_text(
            canonical_json(summary, indent=2),
            root_path / MANIFEST_RELPATH,
            compression=SnapshotCompression.NONE,
        )


def _write_ref(root: Path, kind: str, ref_id: str, record: dict[str, Any]) -> str:
    """Store one ref document at its digest-named path, at most once, and
    return its `ref_digest`. An existing file there is kept only if it
    verifies; a corrupted one is replaced with the content it must hold."""
    document = ref_document(record)
    digest = ref_digest_of(document)
    path = root / versioned_ref_relpath(kind, ref_id, digest)
    try:
        intact = _read_verified_ref(path, digest) == document
    except (OSError, ValueError, SnapshotError):
        intact = False
    if not intact:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_canonical_json_text(
            canonical_json(document, indent=2),
            path,
            compression=SnapshotCompression.NONE,
        )
    return digest


def _read_verified_ref(path: Path, digest: str) -> Any:
    """The ref document at *path*, refused unless it hashes to *digest*."""
    document = canonical_form(json.loads(read_snapshot_bytes(path)))
    actual = ref_digest_of(document)
    if actual != digest:
        raise ValueError(
            f"{path} does not match its ref_digest {digest!r} (recomputed "
            f"{actual!r}) -- the package is corrupted or was hand-edited"
        )
    return document


class ManifestSummary:
    """`manifest.json`'s own small, always-loaded content — `versions`,
    which variant/artifact ids exist (without either's full record), and
    `project_sections` (ADR-062 A1.4/A1.5's cross-artifact evidence refs).

    `semantics_notice` is ADR-062 D2's non-fail-closed half: non-empty when
    the package is readable but was produced under a different extractor or
    resolver generation than this build's, so a run loading it can say so.
    `ref_digests` names each ref document by content digest (D17).
    """

    __slots__ = (
        "versions",
        "variant_ids",
        "artifact_ids",
        "project_sections",
        "semantics_notice",
        "ref_digests",
    )

    def __init__(
        self,
        versions: StorageVersions,
        variant_ids: tuple[str, ...],
        artifact_ids: tuple[str, ...],
        project_sections: Mapping[str, ObjectRef] = MappingProxyType({}),
        semantics_notice: str = "",
        ref_digests: Mapping[str, Mapping[str, str]] = MappingProxyType({}),
    ) -> None:
        self.versions = versions
        self.variant_ids = variant_ids
        self.artifact_ids = artifact_ids
        self.project_sections = project_sections
        self.semantics_notice = semantics_notice
        #: `{"variant"|"artifact": {id: ref object digest}}` (format v2, D17);
        #: empty for a v1 package, whose refs live at `refs/<kind>/<id>.json`.
        self.ref_digests = ref_digests


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
    already been loaded -- `read_manifest_summary` (the lazy primitive D8
    exists for, and the one a real section-aware reader is meant to use
    directly) never constructs a `PackageManifest` at all, so a manifest
    listing the same variant or artifact id twice passed silently through it
    and could be processed more than once by a caller
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

    Also refuses an id that isn't a safe, representable ref-path component
    at all -- via the identical `_safe_ref_id` grammar
    `VariantRef`/`ArtifactRef` (and so `variant_ref_relpath`/
    `artifact_ref_relpath`) already enforce on construction. Without this,
    an id like `"../x"` passed every check above (a real string, not a
    duplicate, no filesystem collision with another entry) and was handed
    back from `read_manifest_summary` as valid
    package membership, even though building a `VariantRef`/`ArtifactRef`
    from it -- the eager `read_project_manifest` path -- already refuses
    it; the lazy path exposed an id no ref could ever actually be loaded
    or written under (Codex review, a third finding on this same field).
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
    for entry in raw:
        _safe_ref_id(entry, field_name.removesuffix("s"))
    return tuple(raw)


def _optional_project_sections(
    data: Mapping[str, Any], record: str
) -> Mapping[str, ObjectRef]:
    """`data["project_sections"]`, or `{}` if absent -- unlike
    `variant_ids`/`artifact_ids`, this is an *additive* field (ADR-062
    A1.4/A1.5): a package written before it existed has no cross-artifact
    evidence to name, a valid state, not a truncated document. A *present
    but malformed* value is still refused (not a JSON object, or a
    malformed object reference), since that means hand-edited/corrupted.
    """
    if "project_sections" not in data:
        return MappingProxyType({})
    raw = data["project_sections"]
    if not isinstance(raw, dict):
        raise ValueError(
            f"{record} project_sections must be a JSON object, not {type(raw).__name__}"
        )
    return {key: ObjectRef.from_dict(value) for key, value in raw.items()}


def read_manifest_summary(root: str | Path) -> ManifestSummary:
    """Load only `manifest.json` — the one document D8 requires be small
    enough to load unconditionally.

    Refuses a package this build cannot safely read *before* returning
    anything a caller might load `refs/*.json` from: D2's fail-closed axes
    (`package_format_version`/`comparison_contract_version`) and an unknown
    D14 manifest `must_understand` name are refused, never silently parsed.
    """
    root_path = Path(root)
    data = json.loads(read_snapshot_bytes(root_path / MANIFEST_RELPATH))
    if not isinstance(data, dict):
        raise ValueError(f"{root_path / MANIFEST_RELPATH} is not a JSON object")
    versions = StorageVersions.from_dict(data.get("versions", {}))
    compatibility = reader_generation_compatibility(versions)
    if not compatibility.readable:
        raise ValueError(
            f"{root_path / MANIFEST_RELPATH} is not readable by this build: "
            f"{compatibility.reason}"
        )
    record = str(root_path / MANIFEST_RELPATH)
    check_must_understand(data.get("must_understand"), record)  # ADR-062 D14
    ref_digests: Mapping[str, Mapping[str, str]] = MappingProxyType({})
    id_lists: Mapping[str, Any] = data
    if versions.package_format_version >= 2:
        # D17: `{id, ref_digest}` per ref; the id lists derive from them and
        # pass the same guards a v1 manifest's bare id lists do.
        ref_digests = {
            kind: ref_digest_entries(data, kind, record) for kind in REF_ENTRY_FIELDS
        }
        id_lists = {f"{kind}_ids": list(ref_digests[kind]) for kind in ref_digests}
    variant_ids = _required_string_id_list(id_lists, "variant_ids", record)
    artifact_ids = _required_string_id_list(id_lists, "artifact_ids", record)
    return ManifestSummary(
        versions=versions,
        variant_ids=variant_ids,
        artifact_ids=artifact_ids,
        project_sections=_optional_project_sections(data, record),
        semantics_notice=(
            compatibility.reason if compatibility.semantics_differ else ""
        ),
        ref_digests=ref_digests,
    )


def _ref_document(
    root: Path, kind: str, ref_id: str, summary: ManifestSummary | None
) -> Any:
    """One ref record's JSON: the digest-verified document the manifest names
    (format v2), or a v1 package's `refs/<kind>/<id>.json` file (the D13
    adapter for the id-path layout)."""
    if summary is None:
        summary = read_manifest_summary(root)
    if summary.ref_digests:
        digests = summary.ref_digests[kind]
        if ref_id not in digests:
            raise ValueError(
                f"{ref_id!r} is not a {kind}_id published in {root / MANIFEST_RELPATH}"
            )
        digest = digests[ref_id]
        path = root / versioned_ref_relpath(kind, ref_id, digest)
        return ref_record(_read_verified_ref(path, digest), str(path))
    relpath = variant_ref_relpath if kind == "variant" else artifact_ref_relpath
    return json.loads(read_snapshot_bytes(root / relpath(ref_id)))


def read_variant_ref(
    root: str | Path, variant_id: str, *, summary: ManifestSummary | None = None
) -> VariantRef:
    """Load exactly one variant's own ref document.

    Refuses a document whose own embedded `variant_id` disagrees with the
    one requested (and therefore with the filename it was loaded from): a
    corrupted or hand-edited `refs/variants/<id>.json` could otherwise name
    a *different* variant, and the caller — which asked for `variant_id` by
    name — would silently receive that other variant's record instead
    (Codex review). `object_relpath`-style validation is not enough here,
    since the mismatch is between two otherwise well-formed values, not a
    malformed one. Pass an already-read *summary* to skip re-reading
    `manifest.json`, which a v2 package needs to locate the ref (D17).
    """
    data = _ref_document(Path(root), "variant", variant_id, summary)
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


def read_artifact_ref(
    root: str | Path, artifact_id: str, *, summary: ManifestSummary | None = None
) -> ArtifactRef:
    """Load exactly one artifact's own ref document.

    Refuses a document whose own embedded `artifact_id` disagrees with the
    one requested, for the identical reason `read_variant_ref` does (Codex
    review).
    """
    data = _ref_document(Path(root), "artifact", artifact_id, summary)
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

    First checks both ids against `manifest.json`'s own published
    membership via `read_manifest_summary` (Codex review, a second finding
    on this same function): checking only that the two *ref documents*
    agree with each other says nothing about whether either one is
    actually part of *this* package -- a directory of stale, injected, or
    since-removed `refs/*.json` files with no `manifest.json` at all (or
    one that no longer lists either id) would otherwise still return a
    self-consistent-looking pair. Routing through `read_manifest_summary`
    first also means this function inherits its fail-closed D2 version
    check (`check_reader_compatibility`) for free, rather than bypassing it
    the way going straight to the two ref files would.

    Raises `ValueError` if either id isn't published in `manifest.json`, or
    if the two ref documents disagree about whether this pair belongs
    together, in either direction.
    """
    summary = read_manifest_summary(root)
    if variant_id not in summary.variant_ids:
        raise ValueError(
            f"{variant_id!r} is not a variant_id published in this "
            "package's manifest.json"
        )
    if artifact_id not in summary.artifact_ids:
        raise ValueError(
            f"{artifact_id!r} is not an artifact_id published in this "
            "package's manifest.json"
        )
    variant = read_variant_ref(root, variant_id, summary=summary)
    artifact = read_artifact_ref(root, artifact_id, summary=summary)
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
    # The requested pair itself checks out, but `variant.artifact_ids` is
    # the variant's *complete* declared membership, not just the one id
    # this call asked about -- a sibling entry there that manifest.json
    # never published is exactly the same "lazy path exposes membership
    # the eager path would refuse" gap the checks above close for the
    # requested id alone (Codex review, a third finding on this same
    # function): `PackageManifest.__post_init__`'s own `declared == owned`
    # invariant is over a variant's *whole* `artifact_ids`, not one
    # element of it, so returning `variant` unchecked here would still let
    # a caller observe -- or try to load -- an unpublished sibling id.
    unpublished = sorted(set(variant.artifact_ids) - set(summary.artifact_ids))
    if unpublished:
        raise ValueError(
            f"refs/variants/{variant_id}.json lists artifact_id(s) "
            f"{unpublished} that are not published in this package's "
            "manifest.json"
        )
    # Deferred gap (Codex review): the reverse "owned" direction of
    # `PackageManifest.__post_init__`'s `declared == owned` invariant -- a
    # *different* published artifact naming this variant that
    # `variant.artifact_ids` omits -- is not checked here. Finding it means
    # loading every artifact ref (manifest.json has no per-variant
    # partition), turning this bounded one-pair read into an O(artifacts)
    # one. `read_project_manifest` checks it at its own eager cost; revisit
    # once a real multi-artifact lazy caller needs the guarantee.
    return variant, artifact
