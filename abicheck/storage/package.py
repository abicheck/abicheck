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

"""The `ProjectSnapshot` package's manifest, refs, and object-store
abstraction — ADR-062 D6/D7 (storage-format-v2 plan A1.1).

D6 converges baseline sets, `BundleFacts`, and per-library snapshots onto
one content-addressed layout::

    project.abicheck/
      manifest.json            # small; loads immediately
      refs/variants/<variant-id>.json
      refs/artifacts/<artifact-id>.json
      objects/sha256/<aa>/<digest>.json.zst
      indexes/index.sqlite     # optional, rebuildable, never canonical truth

This module is the *logical* half of that layout: the manifest and ref
records a reader assembles before deciding what section content to load,
the path-layout functions every writer agrees on, and :class:`ObjectStore`
— the digest-addressed `put`/`get`/`has` abstraction D7's "stored once,
referenced by digest" evidence is built on.

It is deliberately **not** the physical half. ADR-059's envelope
(compression detection, atomic writes, decompression-bomb limits) stays in
`abicheck/snapshot_io.py` and is not reimplemented here — and per this
package's own `AGENTS.md`, a migrated layer may import only `model`, so this
module could not wrap `snapshot_io` even if that were the right layering.
:class:`ObjectStore` is therefore a protocol, not a filesystem client: a
real, `.tar.zst`-transportable store is a concrete implementation built over
both this module and `snapshot_io`, the same way `InMemoryObjectStore` is
built over nothing but this module's own digest functions. Nothing here
reads or writes a byte of an actual file.

`PackageManifest`, `VariantRef`, and `ArtifactRef` are the in-memory
document model of `manifest.json`'s content plus the per-variant and
per-artifact ref documents it names — one Python object graph, fanned out to
disk by `abicheck/project_snapshot_store.py`'s `write_project_manifest`
(`variant_ref_relpath`/`artifact_ref_relpath`/`object_relpath` fix the path
convention that writer follows, so a future implementation can't invent a
second layout). `PackageManifest.project_sections` (ADR-062 A1.4/A1.5) is
the multi-artifact counterpart: cross-library evidence stored once and
referenced by every `ArtifactRef` that shares it — `abicheck/
bundle_facts_store.py` is the first real producer of a `PackageManifest`
naming more than one `ArtifactRef`.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from .canonical import (
    copy_of_canonical_form,
    raw_digest,
    semantic_digest_of_canonical_form,
    strip_capture_metadata,
)
from .guards import (
    binary_buffer as _is_binary_buffer,
    decision_key as _decision_key,
    identity_text as _identity_text,
    instance_of as _instance_of,
    mapping as _mapping,
    provenance_text as _provenance_text,
    required_field as _required_field,
    row_sequence as _row_sequence,
)
from .versioning import StorageVersions

__all__ = [
    "MANIFEST_RELPATH",
    "SECTION_KINDS",
    "ObjectRef",
    "VariantRef",
    "ArtifactRef",
    "PackageManifest",
    "ObjectStore",
    "InMemoryObjectStore",
    "object_relpath",
    "variant_ref_relpath",
    "artifact_ref_relpath",
]

#: The manifest's fixed, package-relative path — D6.
MANIFEST_RELPATH = "manifest.json"

_VARIANT_REF_DIR = "refs/variants"
_ARTIFACT_REF_DIR = "refs/artifacts"
_OBJECT_DIR = "objects"

#: D8's section kinds. Membership in `ArtifactRef.sections` is deliberately
#: not restricted to this set at the data-model level — a new section kind
#: is a producer decision, not something this leaf should gate — but it is
#: the vocabulary D8 names, kept here for a caller that wants it explicitly.
SECTION_KINDS = (
    "binary",
    "declarations",
    "types",
    "layout",
    "debug",
    "build",
    "source_abi",
    "graph",
    "provenance",
    "diagnostics",
    "raw_refs",
)


#: Windows' reserved device names -- forbidden as a path component's stem
#: regardless of case or of what follows (`CON`, `con.json`, and `Con` are
#: all refused the same way a real Windows filesystem refuses them), so a
#: writer fanning this manifest out to `refs/variants/<id>.json` never hits
#: a name the target filesystem cannot create.
#: Superscript digits 0-9, index-aligned (`_SUPERSCRIPT_DIGITS[1]` is `"¹"`).
#: Windows treats `COM¹`/`LPT¹` and friends as reserved device names too --
#: a real, documented bypass of the plain-ASCII-digit restriction that a
#: Windows filesystem update closed by rejecting these spellings as well.
_SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"

_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
    | {f"COM{_SUPERSCRIPT_DIGITS[i]}" for i in range(1, 4)}
    | {f"LPT{_SUPERSCRIPT_DIGITS[i]}" for i in range(1, 4)}
)

#: Characters no Windows filesystem accepts in a path component, beyond the
#: separators `_safe_ref_id` already rejects on every platform.
_WINDOWS_FORBIDDEN_CHARS = frozenset('<>:"|?*')

#: The literal suffix `variant_ref_relpath`/`artifact_ref_relpath` append to
#: a ref id to form its ref document's filename.
_REF_SUFFIX = ".json"

#: The common filesystem path-component limit (ext4, NTFS, APFS, ...) an id
#: plus `_REF_SUFFIX` must fit under. Measured in encoded bytes, not
#: characters: POSIX filesystems count bytes, and the UTF-8 encoding of a
#: non-ASCII id can be several bytes per character, so a character count
#: alone would accept an id whose actual on-disk name is longer than this
#: limit.
_MAX_REF_COMPONENT_BYTES = 255


def _safe_ref_id(value: str, field_name: str) -> str:
    """A ref id, made safe to use as a bare, cross-platform filename component.

    A variant or artifact id becomes a literal path segment
    (`refs/variants/<variant-id>.json`), so a value containing a path
    separator or a `..` component could let a written package escape its own
    directory. Checked once here so every current and future path helper
    inherits the rule rather than each re-deriving it.

    The check is Windows-shaped, not just POSIX-shaped, even though nothing
    here runs on Windows yet: a package is meant to be written on one
    platform and read on another, so an id only POSIX would accept (a
    Windows reserved device name, a trailing dot or space, `:`/`*`/`?`/...)
    would make a manifest that validates here fail once a real writer tries
    to place it on a different filesystem. Refusing it at the one place
    every id passes through means a future writer never has to guess which
    ids are actually portable.
    """
    _identity_text(value, field_name)
    if (
        not value
        or "/" in value
        or "\\" in value
        or value in (".", "..")
        or any(ord(char) < 0x20 for char in value)
        or any(char in _WINDOWS_FORBIDDEN_CHARS for char in value)
        or value[-1] in (".", " ")
        or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        # The id never appears on disk alone -- it is always rendered as
        # `<id>.json` -- so what must fit under the filesystem's component
        # limit is the id *plus* that suffix, not the id by itself. Checked
        # against the UTF-8 encoding, since that is what actually reaches
        # the filesystem's own byte-counted limit.
        or len(value.encode("utf-8")) + len(_REF_SUFFIX) > _MAX_REF_COMPONENT_BYTES
    ):
        raise ValueError(
            f"{field_name} must be a non-empty, cross-platform-path-safe "
            f"identifier no more than "
            f"{_MAX_REF_COMPONENT_BYTES - len(_REF_SUFFIX)} UTF-8 bytes long, "
            f"got {value!r}"
        )
    return value


def _reject_filesystem_collisions(ids: list[str], record_kind: str) -> None:
    """Two ids a real filesystem would treat as the same path, refused early.

    Two ways two *distinct* Python strings still name one file:

    * **Case** -- `Foo`/`foo` are distinct strings but one file on a
      case-insensitive filesystem (the default on Windows and on macOS's
      usual volume format), so `variant_ref_relpath`/`artifact_ref_relpath`
      would write both as the same path, the second silently overwriting
      the first.
    * **Unicode normalization** -- `"é"` (`é`, one code point) and
      `"é"` (`e` + a combining acute accent) render identically and
      are canonically equivalent text, but compare unequal, and unequal
      under `casefold()` alone too. A normalization-insensitive filesystem
      (macOS's default APFS/HFS+ configuration) treats them as the same
      path component for exactly the reason a case-insensitive one treats
      `Foo`/`foo` as the same one.

    Folded via Unicode's own canonical-caseless-matching construction
    (`NFD(casefold(NFD(x)))`, Unicode Standard D145/D146) rather than a single
    normalize-then-casefold pass: `casefold()` is not guaranteed to preserve
    normalization, so two ids that are themselves already NFC/NFD and
    genuinely distinct can still fold to differently-normalized strings that
    a normalization-insensitive filesystem would treat as one path
    component. Renormalizing after casefolding is what closes that gap --
    either kind of collision (case, or Unicode normalization, or both at
    once) is caught here, at the one place every id is collected, rather
    than only once a real writer target reproduces it.
    """
    seen: dict[str, str] = {}
    for ref_id in ids:
        folded = unicodedata.normalize(
            "NFD", unicodedata.normalize("NFD", ref_id).casefold()
        )
        collision = seen.get(folded)
        if collision is not None and collision != ref_id:
            raise ValueError(
                f"{record_kind} {ref_id!r} and {collision!r} would collide on "
                "a case-insensitive or normalization-insensitive filesystem"
            )
        seen[folded] = ref_id


def variant_ref_relpath(variant_id: str) -> str:
    """The package-relative path of one variant's ref document — D6."""
    return f"{_VARIANT_REF_DIR}/{_safe_ref_id(variant_id, 'variant_id')}{_REF_SUFFIX}"


def artifact_ref_relpath(artifact_id: str) -> str:
    """The package-relative path of one artifact's ref document — D6."""
    return (
        f"{_ARTIFACT_REF_DIR}/{_safe_ref_id(artifact_id, 'artifact_id')}{_REF_SUFFIX}"
    )


def object_relpath(digest: str) -> str:
    """The deterministic package-relative path a content digest addresses.

    `digest` is the `"<algorithm>:<hex>"` form
    `abicheck.storage.canonical.semantic_digest` returns. D6 fans objects out
    under a two-character prefix of the hex digest
    (`objects/sha256/<aa>/<digest>.json`), matching Git's own object layout,
    so a project with hundreds of thousands of stored sections doesn't put
    them all in one directory.

    This decides only the *logical* path a manifest reference and a physical
    writer must agree on — never the bytes stored there; a real writer adds
    its own physical suffix (`.json`, `.json.zst`, ...) per ADR-059's envelope.

    Validated against `hashlib`'s own algorithm/digest-size semantics, not a
    hand-rolled character class: an earlier `str.isalnum()` check rejected
    `semantic_digest`'s own canonical spelling for some algorithms (`"sha3_
    256"` contains `_`) while still accepting an impossible address like
    `"sha256:ab"` or an unknown algorithm outright. The two checks must
    agree on what `semantic_digest` can actually produce.
    """
    if not isinstance(digest, str):
        raise TypeError(
            f"digest must be a string, not {type(digest).__name__} ({digest!r})"
        )
    algorithm, separator, hexdigest = digest.partition(":")
    if not separator or not algorithm or not hexdigest:
        raise ValueError(f"digest must be in '<algorithm>:<hex>' form, got {digest!r}")
    try:
        probe = hashlib.new(algorithm)
    except (ValueError, TypeError):
        raise ValueError(
            f"{algorithm!r} is not a hashlib-known digest algorithm: {digest!r}"
        ) from None
    if probe.name != algorithm:
        # `hashlib.new` accepts aliases (`SHA256`, `sha-256`) and resolves
        # them to one canonical spelling -- but `semantic_digest` always
        # writes that spelling (`digester.name`), never the caller's own, so
        # a reference built from an alias would compute a path the object
        # store never actually put anything under.
        raise ValueError(
            f"{algorithm!r} is not the canonical spelling of a digest "
            f"algorithm ({probe.name!r} is); a reference must use the exact "
            f"spelling semantic_digest() writes, not an alias: {digest!r}"
        )
    if algorithm not in hashlib.algorithms_guaranteed:
        # Available-but-not-guaranteed (`sm3`/`ripemd160` on a typical Linux
        # OpenSSL build) fails to load on a platform without it (Codex
        # review).
        raise ValueError(
            f"{algorithm!r} is not in hashlib.algorithms_guaranteed, so it "
            f"is not portable to every platform this package might be read "
            f"on: {digest!r}"
        )
    digest_size = probe.digest_size
    if digest_size == 0:
        # An extendable-output function (SHAKE and friends) has no fixed
        # digest size -- `semantic_digest` itself refuses it too.
        raise ValueError(
            f"{algorithm!r} has no fixed digest size, so it cannot address a "
            f"stored object: {digest!r}"
        )
    if len(hexdigest) != digest_size * 2 or not all(
        char in "0123456789abcdef" for char in hexdigest
    ):
        raise ValueError(
            f"{algorithm!r} produces a {digest_size * 2}-character lowercase "
            f"hex digest, not {digest!r}"
        )
    return f"{_OBJECT_DIR}/{algorithm}/{hexdigest[:2]}/{hexdigest}.json"


def _normalized_text_mapping(raw: Any, field_name: str) -> Mapping[str, str]:
    """A `str -> str` coordinate mapping, guarded and canonically sorted.

    Used for `VariantRef.declared`/`.captured` and `ArtifactRef.native_identity`
    alike: each is a small set of named facts (`target`, `compiler_family`,
    `build_id`, ...) that a decision compares to match or distinguish records,
    so both the keys and the values go through the same guards
    `AvailabilityLedger`'s family names and `ProducerIdentity`'s fields use —
    a key is a `decision_key` (coercing it would let two distinct axes
    collapse into one, with iteration order picking the survivor) and a
    value is `provenance_text` (coercing it would make two genuinely
    different facts compare equal).

    Returned as a read-only, key-sorted `MappingProxyType` so the state
    itself is canonical rather than only its serialized form — the same
    "a canonical view over non-canonical state still leaks through `__eq__`"
    rule `StorageVersions.section_schema_versions` already applies.
    """
    _mapping(raw, field_name)
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        normalized[_decision_key(key, f"{field_name} key")] = _provenance_text(
            value, f"{field_name}[{key!r}]"
        )
    return MappingProxyType(dict(sorted(normalized.items())))


def _object_ref_mapping(raw: Any, field_name: str) -> Mapping[str, ObjectRef]:
    """A `str -> ObjectRef` mapping, guarded and key-sorted -- shared by
    `ArtifactRef.sections` and `PackageManifest.project_sections` (ADR-062
    D8/A1.5). `_normalized_text_mapping`'s shape, one level over: a key is a
    `decision_key`, a value is checked to actually be an `ObjectRef`.
    """
    _mapping(raw, field_name)
    out: dict[str, ObjectRef] = {}
    for key, value in raw.items():
        out[_decision_key(key, f"{field_name} key")] = _instance_of(
            value, ObjectRef, f"{field_name}[{key!r}]"
        )
    return MappingProxyType(dict(sorted(out.items())))


@dataclass(frozen=True)
class ObjectRef:
    """A reference to one content-addressed object — ADR-062 D7.

    `kind` names the section or evidence class this object holds
    (`"declarations"`, `"graph"`, `"build_source_pack"`, ...) so a reader can
    tell what it is being asked to load before fetching it. `digest` is the
    object's content address (`semantic_digest` form) — two `ObjectRef`s with
    equal digests name the same stored object regardless of what `kind` or
    `size` either producer happened to record for it, since the object
    store's own identity is the digest alone.

    `size` is an informational, uncompressed byte count a reader may use to
    decide whether to defer loading a section; it is never part of the
    reference's identity, which is why it is not part of any equality this
    module treats as meaningful — two `ObjectRef`s differing only in `size`
    still point at one object.
    """

    kind: str
    digest: str
    size: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _identity_text(self.kind, "kind"))
        object.__setattr__(self, "digest", _identity_text(self.digest, "digest"))
        if not self.kind:
            raise ValueError("ObjectRef.kind must not be empty")
        if not self.digest:
            raise ValueError("ObjectRef.digest must not be empty")
        # A reference whose digest isn't `object_relpath`'s own grammar can
        # be constructed and serialized here, then fail only once a writer
        # tries to place it -- the same value the manifest already accepted
        # as a valid reference turning out not to address anything. Reusing
        # the parser itself (rather than restating the grammar) means the
        # two can never drift apart on what a well-formed digest looks like;
        # the resulting path is discarded, since only the validation is
        # wanted here.
        object_relpath(self.digest)
        size = self.size
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise TypeError(f"ObjectRef.size must be a non-negative int, not {size!r}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "digest": self.digest}
        if self.size:
            out["size"] = self.size
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ObjectRef:
        _mapping(data, "an object reference")
        size = data.get("size", 0)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            # `size` is informational — no decision reads it (see the class
            # docstring) — so a malformed value degrades to "unknown" rather
            # than aborting the load of an otherwise well-formed reference.
            size = 0
        return cls(
            kind=_required_field(data, "kind", "an object reference"),
            digest=_required_field(data, "digest", "an object reference"),
            size=size,
        )


@dataclass(frozen=True)
class VariantRef:
    """One matched build variant — ADR-062 D9.

    Stable variant identity (target, compiler family, feature toggles) is
    kept separate from state that may legitimately change between releases
    *inside* the same variant (compiler version, standard, flags, artifact
    membership): `declared` and `captured` are two independent coordinate
    maps rather than one merged one, so a later comparison can tell a
    genuine variant boundary apart from an ordinary version bump. Neither
    map's shape is fixed here — the coordinate vocabulary is a capture-time
    decision this leaf does not need to know.

    `artifact_ids` is membership: which artifacts (by id) this variant's
    captured evidence covers. It is stored as a stable-sorted tuple, not a
    set, because — unlike `declared`/`captured`, whose *keys* are what a
    decision compares — this field is itself the payload D5 says an
    unordered collection needs an explicit sort key for.
    """

    variant_id: str
    declared: Mapping[str, str] = field(default_factory=dict)
    captured: Mapping[str, str] = field(default_factory=dict)
    artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # `_safe_ref_id`, not `_identity_text`: this id becomes the literal
        # filename `variant_ref_relpath` builds, so a value that function
        # would refuse must be refused here too -- accepting it here and
        # rejecting it only once a writer tries to place the file would let
        # an otherwise-valid-looking manifest turn out to be unwritable.
        object.__setattr__(
            self, "variant_id", _safe_ref_id(self.variant_id, "variant_id")
        )
        object.__setattr__(
            self, "declared", _normalized_text_mapping(self.declared, "declared")
        )
        object.__setattr__(
            self, "captured", _normalized_text_mapping(self.captured, "captured")
        )
        ids = _row_sequence(self.artifact_ids, "artifact_ids")
        # Each entry is itself a foreign artifact_id, which becomes the
        # literal filename `artifact_ref_relpath` builds for that artifact --
        # the identical reasoning as `variant_id` above, one level over.
        checked_ids = [
            _safe_ref_id(artifact_id, f"artifact_ids[{index}]")
            for index, artifact_id in enumerate(ids)
        ]
        # Sorted and deduplicated: membership is a set of ids, and its
        # serialized order must not depend on the order a caller happened to
        # collect it in (D5's array-with-a-stable-sort-key rule, applied to a
        # plain identifier list rather than a JSON document).
        object.__setattr__(self, "artifact_ids", tuple(sorted(set(checked_ids))))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"variant_id": self.variant_id}
        if self.declared:
            out["declared"] = dict(self.declared)
        if self.captured:
            out["captured"] = dict(self.captured)
        if self.artifact_ids:
            out["artifact_ids"] = list(self.artifact_ids)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VariantRef:
        _mapping(data, "a variant reference")
        return cls(
            variant_id=_required_field(data, "variant_id", "a variant reference"),
            declared=data.get("declared", {}),
            captured=data.get("captured", {}),
            artifact_ids=data.get("artifact_ids", ()),
        )


@dataclass(frozen=True)
class ArtifactRef:
    """One artifact — a native binary, or a header-only/Python-visible
    member with no binary at all — ADR-062 D6.

    `kind` names what the artifact *is* (`"elf"`, `"pe"`, `"macho"`,
    `"python"`, `"header_only"`, ...), never how it should be resolved: D6
    requires every artifact kind to be representable, with bundle-level
    *resolution* declared as an ELF-only capability by whatever consumes this
    reference rather than by this leaf silently excluding non-ELF entries.

    `native_identity` carries whatever content/build identity the artifact's
    own kind actually has (`content_sha256`, ELF `build_id`, Mach-O `uuid`,
    PE/PDB `pdb_guid_age`, ...) as a small `str -> str` fact map, the same
    shape `VariantRef.declared`/`.captured` use — the top-level
    `AbiSnapshot.build_id` field this replaces means an opaque CI identifier
    and is deliberately not reused here (see the ADR's D6).

    `sections` maps a D8 section kind to the object holding it. An artifact
    need not carry every section — a header-only target has no `"binary"`
    section at all — and D8 sections are independently addressable precisely
    so that absence here is a real, representable fact rather than a default
    standing in for one.
    """

    artifact_id: str
    variant_id: str
    kind: str
    native_identity: Mapping[str, str] = field(default_factory=dict)
    sections: Mapping[str, ObjectRef] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # `_safe_ref_id`, not `_identity_text`: both ids become literal
        # filenames (this artifact's own `artifact_ref_relpath`, and its
        # owning variant's `variant_ref_relpath`), so a value either helper
        # would refuse must be refused here too, at construction, rather
        # than accepted into a manifest that later can't be written.
        object.__setattr__(
            self, "artifact_id", _safe_ref_id(self.artifact_id, "artifact_id")
        )
        object.__setattr__(
            self, "variant_id", _safe_ref_id(self.variant_id, "variant_id")
        )
        object.__setattr__(self, "kind", _identity_text(self.kind, "kind"))
        if not self.kind:
            raise ValueError("ArtifactRef.kind must not be empty")
        object.__setattr__(
            self,
            "native_identity",
            _normalized_text_mapping(self.native_identity, "native_identity"),
        )
        object.__setattr__(
            self, "sections", _object_ref_mapping(self.sections, "sections")
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "variant_id": self.variant_id,
            "kind": self.kind,
        }
        if self.native_identity:
            out["native_identity"] = dict(self.native_identity)
        if self.sections:
            out["sections"] = {k: v.to_dict() for k, v in self.sections.items()}
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArtifactRef:
        _mapping(data, "an artifact reference")
        sections_raw = data.get("sections", {})
        _mapping(sections_raw, "sections")
        sections = {
            key: ObjectRef.from_dict(value) for key, value in sections_raw.items()
        }
        return cls(
            artifact_id=_required_field(data, "artifact_id", "an artifact reference"),
            variant_id=_required_field(data, "variant_id", "an artifact reference"),
            kind=_required_field(data, "kind", "an artifact reference"),
            native_identity=data.get("native_identity", {}),
            sections=sections,
        )


@dataclass(frozen=True)
class PackageManifest:
    """The small, always-loaded root document — ADR-062 D6.

    Everything a reader needs before deciding what else to load: the version
    axes (D2, via `StorageVersions`), which variants and artifacts exist, and
    (once a writer assigns them) how to find each one's own ref document via
    `variant_ref_relpath`/`artifact_ref_relpath`. It carries no section
    *content* itself, which is exactly what keeps it small enough to load
    unconditionally — D8's "a project comparison loads two manifests... all
    small L0 binary sections, then one matched library pair at a time."

    `variant_refs`/`artifact_refs` embed full records rather than pointers to
    the on-disk `refs/*.json` files D6 describes, because this module owns
    the in-memory model, not a filesystem layout — see the module docstring.
    A future writer that fans this manifest's content out across the D6
    directory tree does so from this one object, using the same
    `variant_ref_relpath`/`artifact_ref_relpath` helpers a reader would use to
    reassemble it.
    """

    versions: StorageVersions = field(default_factory=StorageVersions)
    variant_refs: tuple[VariantRef, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()
    #: Project-level, cross-artifact objects — ADR-062 D7/A1.4/A1.5: an
    #: instantiation manifest, a shared `BuildSourcePack`/source graph, and
    #: similar evidence belonging to the *project*, not one artifact's own
    #: D8 `sections`. Keyed like `ArtifactRef.sections` (a section-kind
    #: string), but lives once here instead of once per artifact, so
    #: byte-identical shared evidence collapses to one stored object across
    #: every artifact that references it (digest addressing, no extra dedup).
    project_sections: Mapping[str, ObjectRef] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _instance_of(self.versions, StorageVersions, "versions")

        variants = _row_sequence(self.variant_refs, "variant_refs")
        for index, variant in enumerate(variants):
            _instance_of(variant, VariantRef, f"variant_refs[{index}]")
        variant_ids = [variant.variant_id for variant in variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError(
                "PackageManifest.variant_refs contains a duplicate variant_id"
            )
        _reject_filesystem_collisions(variant_ids, "variant_id")
        object.__setattr__(
            self,
            "variant_refs",
            tuple(sorted(variants, key=lambda variant: variant.variant_id)),
        )

        artifacts = _row_sequence(self.artifact_refs, "artifact_refs")
        for index, artifact in enumerate(artifacts):
            _instance_of(artifact, ArtifactRef, f"artifact_refs[{index}]")
        artifact_ids = [artifact.artifact_id for artifact in artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError(
                "PackageManifest.artifact_refs contains a duplicate artifact_id"
            )
        _reject_filesystem_collisions(artifact_ids, "artifact_id")
        known_variant_ids = {variant.variant_id for variant in variants}
        unknown = sorted(
            {artifact.variant_id for artifact in artifacts} - known_variant_ids
        )
        if unknown:
            raise ValueError(
                "PackageManifest.artifact_refs references undeclared "
                f"variant_id(s): {unknown}"
            )
        object.__setattr__(
            self,
            "artifact_refs",
            tuple(sorted(artifacts, key=lambda artifact: artifact.artifact_id)),
        )

        # Membership is stated twice -- an `ArtifactRef.variant_id` pointing
        # up, and a `VariantRef.artifact_ids` listing down -- and nothing
        # above cross-checks one against the other. Checking only that every
        # *listed* id resolves to a matching artifact (variant -> artifact)
        # still missed the reverse: an artifact whose own `variant_id` names
        # a real variant that simply omits it from `artifact_ids`. Either
        # gap serializes a self-contradictory graph, so the two directions
        # are checked as one exact-equality invariant instead of two partial
        # ones: a variant's `artifact_ids` must be *precisely* the set of
        # artifacts whose own `variant_id` names it -- no more, no fewer.
        artifacts_by_id = {artifact.artifact_id: artifact for artifact in artifacts}
        owned_by_variant: dict[str, set[str]] = {
            variant.variant_id: set() for variant in variants
        }
        for artifact in artifacts:
            owned_by_variant[artifact.variant_id].add(artifact.artifact_id)
        for variant in variants:
            declared = set(variant.artifact_ids)
            owned = owned_by_variant[variant.variant_id]
            missing_artifact = sorted(declared - artifacts_by_id.keys())
            if missing_artifact:
                raise ValueError(
                    f"VariantRef {variant.variant_id!r} names artifact_id(s) "
                    f"{missing_artifact}, which are not in artifact_refs"
                )
            if declared != owned:
                raise ValueError(
                    f"VariantRef {variant.variant_id!r}.artifact_ids "
                    f"{sorted(declared)} does not match the artifacts whose own "
                    f"variant_id names it {sorted(owned)}"
                )

        object.__setattr__(
            self,
            "project_sections",
            _object_ref_mapping(self.project_sections, "project_sections"),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"versions": self.versions.to_dict()}
        if self.variant_refs:
            out["variant_refs"] = [variant.to_dict() for variant in self.variant_refs]
        if self.artifact_refs:
            out["artifact_refs"] = [
                artifact.to_dict() for artifact in self.artifact_refs
            ]
        if self.project_sections:
            out["project_sections"] = {
                key: ref.to_dict() for key, ref in self.project_sections.items()
            }
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PackageManifest:
        _mapping(data, "a package manifest")
        raw_variants = _row_sequence(data.get("variant_refs", ()), "variant_refs")
        raw_artifacts = _row_sequence(data.get("artifact_refs", ()), "artifact_refs")
        raw_project_sections = data.get("project_sections", {})
        _mapping(raw_project_sections, "project_sections")
        return cls(
            versions=StorageVersions.from_dict(data.get("versions", {})),
            variant_refs=tuple(VariantRef.from_dict(row) for row in raw_variants),
            artifact_refs=tuple(ArtifactRef.from_dict(row) for row in raw_artifacts),
            project_sections={
                key: ObjectRef.from_dict(value)
                for key, value in raw_project_sections.items()
            },
        )


@runtime_checkable
class ObjectStore(Protocol):
    """The content-addressed object store abstraction — ADR-062 D7.

    Deliberately narrow: three operations, no notion of compression, atomic
    writes, or physical layout. Those belong to ADR-059's envelope
    (`abicheck/snapshot_io.py`), which a concrete implementation of this
    protocol wraps — this migrated layer may depend only on `model` (see
    `storage/AGENTS.md`'s "Permitted imports"), so it cannot itself import
    `snapshot_io`. A filesystem-backed store lives outside this package.

    `content` is either a value `semantic_digest` accepts (JSON-shaped
    facts) or a raw binary buffer (`bytes`/`bytearray`/`memoryview`, hashed
    via `raw_digest`) -- this package's job is "stores and retrieves bytes
    a caller already produced" (`AGENTS.md`), not just JSON facts (Codex
    review). Either way the digest a caller uses to build an `ObjectRef`
    and the digest the store assigns can never disagree.
    """

    def put(self, content: Any, *, algorithm: str = "sha256") -> str:
        """Store `content`, returning its content digest.

        Storing the same content twice must return the same digest and must
        not create a second copy. `algorithm` is forwarded to
        `semantic_digest`/`raw_digest` as-is (Codex review).
        """
        ...

    def get(self, digest: str) -> Any:
        """The stored object's hash-domain form -- what `digest` addresses.

        For JSON-shaped content, `canonical_form(content)` with the
        reserved root `capture` block removed, matching what
        `semantic_digest` hashed (D3). For a raw payload, the stored bytes
        unchanged. Raises `KeyError` if nothing is stored under `digest`.
        """
        ...

    def has(self, digest: str) -> bool:
        """Whether an object is already stored under `digest`."""
        ...


class InMemoryObjectStore:
    """A process-local `ObjectStore`, addressed exactly the way a real one is.

    Not a stub: `put`/`get`/`has` behave exactly as any conforming store
    must, just without ever touching a filesystem or transport. Useful on
    its own for a one-process comparison that never needs to persist a
    package, and as the fixture every other implementation's contract can be
    checked against.
    """

    def __init__(self) -> None:
        self._objects: dict[str, Any] = {}

    def put(self, content: Any, *, algorithm: str = "sha256") -> str:
        algorithm = _identity_text(algorithm, "algorithm")
        if _is_binary_buffer(content):
            # `canonical_form` rejects a binary buffer, so `raw_digest`
            # hashes it directly; `bytes(content)` copies a `bytearray`/
            # `memoryview`, closing the mutation hazard `get()` guards below.
            payload = bytes(content)
            digest = raw_digest(payload, algorithm=algorithm)
            if digest not in self._objects:
                self._objects[digest] = payload
            return digest
        # Normalize once, then hash and store from that same snapshot -- a second independent traversal of a
        # stateful `Mapping` could digest different content than what gets stored (Codex review). Hashed via
        # `semantic_digest_of_canonical_form`, not `semantic_digest`, since `stripped` is already canonical.
        stripped = strip_capture_metadata(content)
        digest = semantic_digest_of_canonical_form(stripped, algorithm=algorithm)
        if digest not in self._objects:
            self._objects[digest] = stripped
        return digest

    def get(self, digest: str) -> Any:
        digest = _identity_text(digest, "digest")
        try:
            stored = self._objects[digest]
        except KeyError:
            raise KeyError(f"no object stored under digest {digest!r}") from None
        if isinstance(stored, bytes):
            return stored  # immutable already -- no copy needed
        # An isolated copy, not the store's own object -- else a caller could mutate it in place, uncorrectably
        # (Codex review). `stored` is already canonical, so `copy_of_canonical_form`'s plain copy suffices.
        return copy_of_canonical_form(stored)

    def has(self, digest: str) -> bool:
        return _identity_text(digest, "digest") in self._objects
