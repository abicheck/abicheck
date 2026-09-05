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

"""The `ProjectSnapshot` package's per-section DTO envelope — ADR-062 Phase 1
(A1.1's "reads and writes the D6 layout"), landed jointly with ADR-063 Phase 8
(`one-semantic-pipeline.md`), whose D8 constraint it satisfies: *every DTO is
a distinct, versioned class from the domain `SemanticIR`/`Fact[T]`/`EntityId`
objects, with an explicit `to_dto()`/`from_dto()` (never `asdict`/a 500-line
mirror deserializer) and a migration adapter per DTO version.*

A thin envelope, not a generic mirror of every domain type: D8 forbids a
*second, generic* identity/availability scheme at the storage layer and an
`asdict`-shaped deserializer that drifts from the domain object it mirrors,
not a hand-written mirror of every field (that would be the "second
representation kept in sync by hand" the plan warns against). Each concrete
section type owns its own explicit encoding already (`semantic_ir_codec.py`
was the first); `SectionDTO` is the one *shared* piece they plug into: a
versioned envelope a writer stores as one content-addressed D7 object and a
reader can recognize and migrate before trusting the payload underneath.

A migration adapter is a plain `Callable[[Mapping], Mapping]` keyed by the
DTO version it accepts, registered per section kind (`_MIGRATIONS`); there is
deliberately no generic "diff two dataclasses" machinery, since a real
migration is domain-specific (rename, split, change a unit).
`migrate_section_dto` walks the chain one registered step at a time, so a
version this build cannot reach is refused rather than silently accepted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..model.semantic_ir import SemanticIR
from .canonical import canonical_form
from .graph_section_codec import GraphSection
from .guards import (
    identity_text as _identity_text,
    mapping as _mapping,
    required_field as _required_field,
    strict_int as _strict_int,
)
from .legacy_sections import LEGACY_SECTION_KINDS
from .semantic_ir_codec import semantic_ir_from_document, semantic_ir_to_document
from .sparse_section_codec import (
    BinarySection,
    BuildSection,
    DebugSection,
    DeclarationsSection,
    LayoutSection,
    ProvenanceSection,
)
from .types_section_codec import TypesSection

__all__ = [
    "BASELINE_SET_SECTION_KIND",
    "BINARY_SECTION_KIND",
    "BUILD_SECTION_KIND",
    "BUNDLE_COMPOSITION_SECTION_KIND",
    "DEBUG_SECTION_KIND",
    "DECLARATIONS_SECTION_KIND",
    "GRAPH_SECTION_KIND",
    "LAYOUT_SECTION_KIND",
    "PROVENANCE_SECTION_KIND",
    "SECTION_SCHEMA_VERSIONS",
    "SEMANTIC_IR_SECTION_KIND",
    "TYPES_SECTION_KIND",
    "SectionDTO",
    "baseline_set_metadata_from_dto",
    "baseline_set_metadata_to_dto",
    "binary_from_dto",
    "binary_to_dto",
    "build_from_dto",
    "build_to_dto",
    "bundle_composition_from_dto",
    "bundle_composition_to_dto",
    "debug_from_dto",
    "debug_to_dto",
    "declarations_from_dto",
    "declarations_to_dto",
    "graph_from_dto",
    "graph_to_dto",
    "layout_from_dto",
    "layout_to_dto",
    "legacy_section_from_dto",
    "legacy_section_to_dto",
    "migrate_section_dto",
    "provenance_from_dto",
    "provenance_to_dto",
    "semantic_ir_from_dto",
    "semantic_ir_to_dto",
    "types_from_dto",
    "types_to_dto",
]

#: This module's own D8-constrained section kind for the domain `SemanticIR`
#: object — one of D8's own named section kinds ("declarations"/"types"/...)
#: is deliberately not reused here: `SemanticIR` is ADR-063 Phase 6's single
#: cross-backend representation of declaration *and* type facts together, not
#: a per-legacy-category split, and inventing a placement into one of those
#: categories now would be a guess this module has no producer yet to
#: validate against — the same reasoning `package.py`'s own "known,
#: deliberately deferred gap" note already applies to `ArtifactRef.sections`.
SEMANTIC_IR_SECTION_KIND = "semantic_ir"

#: ADR-063 Track 4 (8B): the one `storage.legacy_sections.LEGACY_SECTION_
#: KINDS` member with a real, dedicated `TypesSection` DTO instead of the
#: generic `legacy_section_to_dto` pass-through -- see
#: `types_section_codec.py`'s own module docstring for why `"types"` is the
#: section this lands for first. Unlike `SEMANTIC_IR_SECTION_KIND` above,
#: this name IS one of `LEGACY_SECTION_KINDS` (the D8 field-partition in
#: `legacy_sections.py` still assigns the `types` field to this section
#: kind) -- only its *DTO encoding function* is specialized here, not its
#: membership in that vocabulary.
TYPES_SECTION_KIND = "types"

#: ADR-063 Track C 8B: two more section kinds, each independent of the
#: eight-member `LEGACY_SECTION_KINDS` vocabulary the same way
#: `SEMANTIC_IR_SECTION_KIND` is -- neither describes a single `AbiSnapshot`
#: field-partition, so folding either into that vocabulary (or into the
#: generic `legacy_section_to_dto` pass-through, which `legacy_section_from
#: _dto` restricts to `LEGACY_SECTION_KINDS` members specifically) would
#: misfile it. Both hold *variant*-level, not artifact-level, content --
#: `storage.package.VariantRef.sections`, never `ArtifactRef.sections` --
#: since neither names a single binary/header-only member of the matched
#: build, the same reasoning `VariantRef.declared`/`.captured` already state
#: for `variant_fingerprint`-shaped coordinates. `storage.
#: import_bundle_facts`/`storage.import_baseline_set` are the only
#: producers/consumers of either.
BUNDLE_COMPOSITION_SECTION_KIND = "bundle_composition"
BASELINE_SET_SECTION_KIND = "baseline_set_metadata"

#: ADR-063 Track 4 (8B), second slice: the second `LEGACY_SECTION_KINDS`
#: member with a real, dedicated DTO (`GraphSection`) instead of the generic
#: `legacy_section_to_dto` pass-through -- see `graph_section_codec.py`'s
#: own module docstring for why `"graph"` is the next section promoted by
#: `types_section_codec.py`'s own heuristic. Like `TYPES_SECTION_KIND`
#: above, this name IS one of `LEGACY_SECTION_KINDS`; only its DTO encoding
#: function is specialized here.
GRAPH_SECTION_KIND = "graph"

#: ADR-063 Track 4 (8B), third slice: the six remaining `LEGACY_SECTION_
#: KINDS` members, each with a real, dedicated DTO from
#: `sparse_section_codec.py` -- see that module's own docstring for the
#: required/optional field split that made this slice possible where the
#: first two ("types"/"graph") could not have generalized to it directly.
#: Every one of D8's eight named legacy section kinds now has its own DTO;
#: none is left on the generic `legacy_section_to_dto` pass-through.
BINARY_SECTION_KIND = "binary"
DECLARATIONS_SECTION_KIND = "declarations"
LAYOUT_SECTION_KIND = "layout"
DEBUG_SECTION_KIND = "debug"
BUILD_SECTION_KIND = "build"
PROVENANCE_SECTION_KIND = "provenance"

#: Section kinds `legacy_section_to_dto`/`legacy_section_from_dto` must
#: refuse (each has a dedicated codec); every `SECTION_SCHEMA_VERSIONS` entry today.
_SPECIALIZED_SECTION_KINDS = frozenset(
    {
        SEMANTIC_IR_SECTION_KIND,
        TYPES_SECTION_KIND,
        GRAPH_SECTION_KIND,
        BINARY_SECTION_KIND,
        DECLARATIONS_SECTION_KIND,
        LAYOUT_SECTION_KIND,
        DEBUG_SECTION_KIND,
        BUILD_SECTION_KIND,
        PROVENANCE_SECTION_KIND,
        BUNDLE_COMPOSITION_SECTION_KIND,
        BASELINE_SET_SECTION_KIND,
    }
)

#: Every section kind this module knows how to encode, and the current DTO
#: version each one is written at today — `StorageVersions.
#: section_schema_versions` (D2) is keyed by exactly this vocabulary, so a
#: package's manifest states which version *this* module wrote each section
#: under, independent of every other axis.
SECTION_SCHEMA_VERSIONS: Mapping[str, int] = {
    SEMANTIC_IR_SECTION_KIND: 1,
    # ADR-063 Phase 8's full D8 split: every `storage.legacy_sections
    # .LEGACY_SECTION_KINDS` entry starts at version 1, the same way
    # `SEMANTIC_IR_SECTION_KIND` did before it shipped its own first real
    # producer — each is its own independent axis from here on, so a future
    # `"binary"` schema change never forces a bump on `"declarations"`.
    **{kind: 1 for kind in LEGACY_SECTION_KINDS},
    # ADR-063 Track C 8B: the two variant-level section kinds, independent
    # of every axis above for the identical reason. Composition v2 (ADR-065
    # D8) adds the decision-bearing `degraded_members` map; a composition
    # with no degraded member is still *written* at v1 (see
    # `bundle_composition_to_dto`) so a pre-S2 reader keeps opening it.
    BUNDLE_COMPOSITION_SECTION_KIND: 2,
    BASELINE_SET_SECTION_KIND: 1,
}


def _bundle_composition_v1_to_v2(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    # v1 predates the marker; a v1 section *carrying* one would still open in
    # a pre-S2 reader that ignores it, so it is refused, not migrated (Codex).
    """Composition v1 -> v2: supply the empty ``degraded_members`` map v1 predates."""
    if payload.get("degraded_members"):
        raise ValueError(
            f"{BUNDLE_COMPOSITION_SECTION_KIND!r} section v1 carries a non-empty "
            "'degraded_members' marker, which requires section version 2 (ADR-065 D8)"
        )
    return {**payload, "degraded_members": {}}


#: Per-section-kind migration chains, keyed by the DTO version a step reads
#: *from* — `{1: step_from_1_to_2, 2: step_from_2_to_3, ...}`: one small,
#: reviewed function per version bump, chained rather than replaced.
_MIGRATIONS: Mapping[
    str, Mapping[int, Callable[[Mapping[str, Any]], Mapping[str, Any]]]
] = {
    **{kind: {} for kind in SECTION_SCHEMA_VERSIONS},
    BUNDLE_COMPOSITION_SECTION_KIND: {1: _bundle_composition_v1_to_v2},
}


def _freeze(value: Any) -> Any:
    """*value* — already `canonical_form`'s output, so a tree of only
    `dict`/`list`/`str`/`int`/`float`/`bool`/`None` — rebuilt so nothing
    reachable from a `SectionDTO` after construction is mutable: every
    mapping becomes a `MappingProxyType` over a `dict` of already-frozen
    values, every list becomes a `tuple` of already-frozen values.

    `canonical_form` already rebuilds every container, so `__post_init__`'s
    stored `payload` never aliases the caller's own object — but the
    rebuilt containers were still ordinary, mutable `dict`/`list`, reachable
    both through `dto.payload` directly and through a *nested* value inside
    an earlier `to_dict()` call's return, either of which could then mutate
    this frozen DTO's own stored content after construction already
    validated it (Codex review, a second finding on the same field after
    the copy-on-construction fix: a shallow copy stops the *caller's*
    mapping from aliasing this DTO's storage, but does nothing once the
    values inside that storage are themselves mutable)."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _unfreeze(value: Any) -> Any:
    """The inverse of `_freeze` — a fresh, ordinary, mutable `dict`/`list`
    tree, detached from this DTO's own frozen storage. `to_dict()` returns
    this rather than a shallow copy of `payload` so a caller holding its
    return value can mutate it freely without reaching back into (or being
    confused for) this DTO's immutable internal state."""
    if isinstance(value, MappingProxyType):
        return {key: _unfreeze(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_unfreeze(item) for item in value]
    return value


@dataclass(frozen=True)
class SectionDTO:
    """One D7 content-addressed object's wire envelope.

    `section_kind` names which D8 vocabulary entry (or this module's own
    `SEMANTIC_IR_SECTION_KIND`) the payload holds; `section_schema_version`
    is the DTO version *that payload* was encoded at — D2's per-section axis,
    carried alongside the content rather than only in the owning
    `ArtifactRef`/manifest, so a payload is self-describing even if extracted
    from its package. `payload` is already the section's own canonical,
    JSON-shaped document (e.g. `semantic_ir_codec.semantic_ir_to_document`'s
    return value) — this class never inspects or re-derives it; it only
    carries it, the same "stores and retrieves bytes a caller already
    produced" contract `ObjectStore` itself holds (`storage/AGENTS.md`,
    "Prohibited responsibilities").
    """

    section_kind: str
    section_schema_version: int
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "section_kind", _identity_text(self.section_kind, "section_kind")
        )
        if not self.section_kind:
            raise ValueError("SectionDTO.section_kind must not be empty")
        object.__setattr__(
            self,
            "section_schema_version",
            _strict_int(self.section_schema_version, "section_schema_version"),
        )
        if self.section_schema_version <= 0:
            raise ValueError(
                "SectionDTO.section_schema_version must be a positive int, "
                f"got {self.section_schema_version!r}"
            )
        _mapping(self.payload, "payload")
        # A fresh, normalized, and now fully immutable structure -- never
        # the caller's own mapping, and never reachable for mutation through
        # `dto.payload` or a value nested inside it either. `payload` is a
        # frozen dataclass field, but `Mapping`/`dict` are themselves
        # mutable, so holding the caller's object -- or even a fresh but
        # ordinary `dict`/`list` copy of it -- let a later mutation of that
        # object, or of `dto.payload` (or something nested inside it)
        # directly, silently change this DTO's content -- including the
        # bytes `to_dict()` later returns -- after construction already
        # validated it (Codex review, two rounds: first the caller's own
        # mapping aliasing this DTO's storage, then the storage itself
        # staying mutable underneath the top level). `canonical_form` copies
        # and normalizes (so two payloads that are the same content in a
        # different key/insertion order compare and serialize identically),
        # and `_freeze` then makes every mapping and sequence in the result
        # immutable, recursively.
        object.__setattr__(self, "payload", _freeze(canonical_form(self.payload)))

    def to_dict(self) -> dict[str, Any]:
        # `_unfreeze`, not `dict(self.payload)`: the latter only copies the
        # outer mapping, leaving every nested value shared with this DTO's
        # own frozen storage -- immutable now, per `_freeze` above, but a
        # caller expecting a plain, JSON-serializable, mutable document
        # would otherwise receive `MappingProxyType`/`tuple` values it
        # cannot write back into a `dict`/`list`-shaped shape without
        # rebuilding it itself.
        return {
            "section_kind": self.section_kind,
            "section_schema_version": self.section_schema_version,
            "payload": _unfreeze(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SectionDTO:
        # Validated fully by `__post_init__` below (including `payload`
        # itself, which the constructor also requires to be a mapping) —
        # this door only needs to confirm the outer document is one, so
        # `.get`/`required_field` below have something to subscript.
        _mapping(data, "a section DTO")
        return cls(
            section_kind=_required_field(data, "section_kind", "a section DTO"),
            section_schema_version=_strict_int(
                _required_field(data, "section_schema_version", "a section DTO"),
                "section_schema_version",
            ),
            payload=_required_field(data, "payload", "a section DTO"),
        )


def migrate_section_dto(dto: SectionDTO) -> SectionDTO:
    """*dto*, advanced to its section kind's current version via the
    registered migration chain — a no-op if it is already current.

    Raises `ValueError` for a stored version this build has no registered
    step *from* and that is not already current: an unreadable payload must
    fail loudly here, not be silently accepted as whatever version happens
    to deserialize without raising (the same fail-closed reasoning D2 applies
    to `package_format_version`/`comparison_contract_version`, specialized to
    one section's own DTO version instead of the whole package).
    """
    current = SECTION_SCHEMA_VERSIONS.get(dto.section_kind)
    if current is None:
        raise ValueError(
            f"no DTO version is registered for section kind {dto.section_kind!r}"
        )
    chain = _MIGRATIONS.get(dto.section_kind, {})
    payload: Mapping[str, Any] = dto.payload
    version = dto.section_schema_version
    seen: set[int] = set()
    while version != current:
        if version in seen:  # pragma: no cover - defensive: version strictly
            # increases by 1 every iteration (see the unconditional
            # `version += 1` below), so no value can ever repeat -- this
            # guards against a *future* migration step's own bug (e.g. one
            # that doesn't actually advance the version), not a reachable
            # path in this build.
            raise ValueError(
                f"migration chain for section kind {dto.section_kind!r} cycles "
                f"at version {version}"
            )
        seen.add(version)
        step = chain.get(version)
        if step is None:
            raise ValueError(
                f"section kind {dto.section_kind!r} version {version} has no "
                f"registered migration to {current} — this build cannot read it"
            )
        payload = step(payload)
        version += 1
    return SectionDTO(
        section_kind=dto.section_kind, section_schema_version=current, payload=payload
    )


def semantic_ir_to_dto(
    ir: SemanticIR | None, conflicts: Mapping[str, str]
) -> SectionDTO:
    """The domain `SemanticIR` (plus its sibling conflict map), as a
    `SectionDTO` — the D8-constrained counterpart of `AbiSnapshot.
    semantic_ir`/`.semantic_ir_conflicts` for a `ProjectSnapshot` package.

    Built on `semantic_ir_codec.semantic_ir_to_document`, never `asdict`: the
    payload is exactly what that function already emits, so this is a
    version stamp over an existing, reviewed encoding rather than a second
    one.
    """
    return SectionDTO(
        section_kind=SEMANTIC_IR_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[SEMANTIC_IR_SECTION_KIND],
        payload=semantic_ir_to_document(ir, conflicts),
    )


def semantic_ir_from_dto(dto: SectionDTO) -> tuple[SemanticIR | None, dict[str, str]]:
    """The inverse of `semantic_ir_to_dto` — migrates *dto* to the current
    version first, then decodes via `semantic_ir_codec.
    semantic_ir_from_document`."""
    if dto.section_kind != SEMANTIC_IR_SECTION_KIND:
        raise ValueError(
            f"expected section kind {SEMANTIC_IR_SECTION_KIND!r}, got "
            f"{dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    return semantic_ir_from_document(current.payload)


def legacy_section_to_dto(section_kind: str, payload: Mapping[str, Any]) -> SectionDTO:
    """One `storage.legacy_sections.LEGACY_SECTION_KINDS` payload (already an
    explicit, allowlisted subset of a legacy document — see that module's own
    docstring), as a `SectionDTO`.

    There is nothing to encode beyond the version stamp: each legacy section
    is already the exact JSON shape `serialization.snapshot_to_dict()`
    produced for those keys, and `storage/` may not import `serialization.py`
    to re-derive it (`storage/AGENTS.md`, "Permitted imports") — the same
    "reuse the existing, reviewed encoding, never a second one" reasoning
    `semantic_ir_to_dto` already documents, applied to a section whose
    payload is already flat JSON rather than a typed domain object.

    As of ADR-063 Track 4 (8B)'s third slice every `LEGACY_SECTION_KINDS`
    member has its own dedicated DTO (`types_to_dto`/`graph_to_dto`/
    `binary_to_dto`/`declarations_to_dto`/`layout_to_dto`/`debug_to_dto`/
    `build_to_dto`/`provenance_to_dto`), so this generic pass-through is no
    longer reachable for any of today's eight -- it remains defined as the
    fallback a future, not-yet-specialized ninth section kind would use, the
    same "generic first, promoted once a real design exists" path `types`/
    `graph` themselves came from.
    """
    if (
        section_kind not in SECTION_SCHEMA_VERSIONS
        or section_kind in _SPECIALIZED_SECTION_KINDS
    ):
        raise ValueError(
            f"{section_kind!r} is not a legacy section kind -- expected one "
            f"of {sorted(set(SECTION_SCHEMA_VERSIONS) - _SPECIALIZED_SECTION_KINDS)}"
        )
    return SectionDTO(
        section_kind=section_kind,
        section_schema_version=SECTION_SCHEMA_VERSIONS[section_kind],
        payload=payload,
    )


def legacy_section_from_dto(dto: SectionDTO) -> dict[str, Any]:
    """The inverse of `legacy_section_to_dto` — migrates *dto* to its
    section's current version first, then returns a fresh, mutable, fully
    unfrozen `dict` of its payload (`to_dict()["payload"]`, never the DTO's
    own frozen `MappingProxyType`/`tuple` storage — a shallow `dict(...)`
    copy would leave every nested container still frozen).

    Rejects a `semantic_ir`- or `types`-kind `dto`, symmetrically with
    `legacy_section_to_dto`'s own refusal to *encode* either -- each has its
    own dedicated decoder (`semantic_ir_from_dto`/`types_from_dto`), and
    returning its raw payload here would silently bypass it rather than
    raising the identical error `legacy_section_to_dto` already gives a
    caller that gets this backwards (CodeRabbit review, extended to `types`
    for the identical reason when that section gained its own DTO).
    """
    if dto.section_kind not in LEGACY_SECTION_KINDS or dto.section_kind in (
        _SPECIALIZED_SECTION_KINDS
    ):
        raise ValueError(
            f"{dto.section_kind!r} is not a legacy section kind -- expected "
            f"one of {sorted(set(LEGACY_SECTION_KINDS) - _SPECIALIZED_SECTION_KINDS)}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return payload


def bundle_composition_to_dto(payload: Mapping[str, Any]) -> SectionDTO:
    """The small set of bundle-composition facts a persisted `BundleFacts`
    document (`abicheck.bundle_facts_serialization.bundle_facts_to_dict()`)
    carries beyond its `per_library_snapshots` -- `variant_fingerprint`,
    `manifest`, `filesystem_aliases`, `library_filenames` -- as a
    `SectionDTO` (ADR-063 Track C 8B).

    None of these facts names one particular library, so this is its own
    section kind (not one of ADR-062 D8's eight per-`ArtifactRef` ones),
    attached by `import_bundle_facts` to the owning `VariantRef`.

    Nothing to encode beyond the version stamp, for `legacy_section_to_dto`'s
    reason: the caller hands over the already-serialized sub-mapping
    (`storage/` may not import the modules that produce it -- `storage/
    AGENTS.md`, "Permitted imports").
    """
    degraded = payload.get("degraded_members")
    if degraded:
        return SectionDTO(
            section_kind=BUNDLE_COMPOSITION_SECTION_KIND,
            section_schema_version=SECTION_SCHEMA_VERSIONS[
                BUNDLE_COMPOSITION_SECTION_KIND
            ],
            payload=payload,
        )
    # No degraded member: v1 shape (key dropped) so a pre-S2 reader still opens it.
    return SectionDTO(
        section_kind=BUNDLE_COMPOSITION_SECTION_KIND,
        section_schema_version=1,
        payload={k: v for k, v in payload.items() if k != "degraded_members"},
    )


def bundle_composition_from_dto(dto: SectionDTO) -> dict[str, Any]:
    """The inverse of `bundle_composition_to_dto` -- migrates *dto* to its
    section's current version first, then returns a fresh, mutable dict of
    its payload."""
    if dto.section_kind != BUNDLE_COMPOSITION_SECTION_KIND:
        raise ValueError(
            f"expected section kind {BUNDLE_COMPOSITION_SECTION_KIND!r}, got "
            f"{dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return payload


def baseline_set_metadata_to_dto(payload: Mapping[str, Any]) -> SectionDTO:
    """An `actions/baseline`-produced baseline set's own `manifest.json`
    metadata (`manifest_version`, `project_ref`, `profile`, `snapshot_schema`,
    `fact_set`, `baseline_generation`, `generator` -- everything in that
    document other than its `artifacts[]` list, which
    `storage.import_baseline_set.import_baseline_set` instead resolves into
    one per-library `ArtifactRef` each, via `storage.import_v1.
    import_legacy_snapshot`) as a `SectionDTO` (ADR-063 Track C 8B).

    Mirrors `bundle_composition_to_dto` exactly: none of these facts names a
    single library, so they are attached to the owning `VariantRef` rather
    than any one `ArtifactRef`, and there is nothing to encode beyond the
    version stamp -- this is already the flat JSON `buildsource.baseline_set
    .load_baseline_manifest` itself reads, and `storage/` may not import
    that module to re-derive it.
    """
    return SectionDTO(
        section_kind=BASELINE_SET_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[BASELINE_SET_SECTION_KIND],
        payload=payload,
    )


def baseline_set_metadata_from_dto(dto: SectionDTO) -> dict[str, Any]:
    """The inverse of `baseline_set_metadata_to_dto`."""
    if dto.section_kind != BASELINE_SET_SECTION_KIND:
        raise ValueError(
            f"expected section kind {BASELINE_SET_SECTION_KIND!r}, got "
            f"{dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return payload


def types_to_dto(section: TypesSection) -> SectionDTO:
    """The domain `TypesSection` (ADR-063 Track 4 / 8B) as a `SectionDTO` --
    built on `TypesSection.to_document`, never `asdict`, mirroring
    `semantic_ir_to_dto`'s own "version stamp over an existing, reviewed
    encoding" shape for its own single-field payload."""
    return SectionDTO(
        section_kind=TYPES_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[TYPES_SECTION_KIND],
        payload=section.to_document(),
    )


def types_from_dto(dto: SectionDTO) -> TypesSection:
    """The inverse of `types_to_dto` — migrates *dto* to the current version
    first, then decodes via `TypesSection.from_document`.

    Reads `current.to_dict()["payload"]`, never `current.payload` directly
    (Codex review, fresh evidence): `SectionDTO.payload` is *recursively*
    frozen (`_freeze` turns every nested mapping into a `MappingProxyType`
    and every nested list into a `tuple`, all the way down -- see that
    function's own docstring), so a `types` entry's own nested lists (e.g. a
    `RecordType`'s `bases`) would otherwise stay tuples after a round trip
    through this DTO while a freshly-dumped comparison side holds plain
    lists -- silently producing a `('Base',) != ['Base']` mismatch a
    downstream detector reads as a real change, and leaving the
    `MappingProxyType`s unable to `json.dumps`. `to_dict()`'s own
    `_unfreeze` is the one place this DTO already reverses that recursively;
    `legacy_section_from_dto` already reads through it for the identical
    reason, so this mirrors that rather than introducing a second,
    shallower unwrap.
    """
    if dto.section_kind != TYPES_SECTION_KIND:
        raise ValueError(
            f"expected section kind {TYPES_SECTION_KIND!r}, got {dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return TypesSection.from_document(payload)


def graph_to_dto(section: GraphSection) -> SectionDTO:
    """The domain `GraphSection` (ADR-063 Track 4 / 8B, second slice) as a
    `SectionDTO` -- built on `GraphSection.to_document`, never `asdict`,
    mirroring `types_to_dto`'s own "version stamp over an existing,
    reviewed encoding" shape for its own single-field payload."""
    return SectionDTO(
        section_kind=GRAPH_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[GRAPH_SECTION_KIND],
        payload=section.to_document(),
    )


def graph_from_dto(dto: SectionDTO) -> GraphSection:
    """The inverse of `graph_to_dto` — migrates *dto* to the current version
    first, then decodes via `GraphSection.from_document`.

    Reads `current.to_dict()["payload"]`, never `current.payload` directly
    -- identical reasoning to `types_from_dto`'s own docstring: `SectionDTO
    .payload` is recursively frozen, so `surface_graph`'s own nested lists
    would otherwise round-trip as tuples while a freshly-dumped comparison
    side holds plain lists.
    """
    if dto.section_kind != GRAPH_SECTION_KIND:
        raise ValueError(
            f"expected section kind {GRAPH_SECTION_KIND!r}, got {dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return GraphSection.from_document(payload)


# ADR-063 Track 4 (8B), third slice: the six remaining sparse legacy
# sections. Each pair below is the identical shape as `types_to_dto`/
# `types_from_dto` and `graph_to_dto`/`graph_from_dto` above -- a version
# stamp over `sparse_section_codec.py`'s own `to_document`/`from_document`,
# reading back through `current.to_dict()["payload"]` rather than
# `current.payload` directly for the same recursive-freeze reason those two
# docstrings already explain (a section's own nested lists must come back
# as plain `list`s, not leftover `tuple`s, to compare equal against a
# freshly-dumped document).


def binary_to_dto(section: BinarySection) -> SectionDTO:
    """The domain `BinarySection` as a `SectionDTO` -- see
    `sparse_section_codec.py`'s own module docstring."""
    return SectionDTO(
        section_kind=BINARY_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[BINARY_SECTION_KIND],
        payload=section.to_document(),
    )


def binary_from_dto(dto: SectionDTO) -> BinarySection:
    """The inverse of `binary_to_dto`."""
    if dto.section_kind != BINARY_SECTION_KIND:
        raise ValueError(
            f"expected section kind {BINARY_SECTION_KIND!r}, got {dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return BinarySection.from_document(payload)


def declarations_to_dto(section: DeclarationsSection) -> SectionDTO:
    """The domain `DeclarationsSection` as a `SectionDTO` -- see
    `sparse_section_codec.py`'s own module docstring."""
    return SectionDTO(
        section_kind=DECLARATIONS_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[DECLARATIONS_SECTION_KIND],
        payload=section.to_document(),
    )


def declarations_from_dto(dto: SectionDTO) -> DeclarationsSection:
    """The inverse of `declarations_to_dto`."""
    if dto.section_kind != DECLARATIONS_SECTION_KIND:
        raise ValueError(
            f"expected section kind {DECLARATIONS_SECTION_KIND!r}, got "
            f"{dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return DeclarationsSection.from_document(payload)


def layout_to_dto(section: LayoutSection) -> SectionDTO:
    """The domain `LayoutSection` as a `SectionDTO` -- see
    `sparse_section_codec.py`'s own module docstring."""
    return SectionDTO(
        section_kind=LAYOUT_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[LAYOUT_SECTION_KIND],
        payload=section.to_document(),
    )


def layout_from_dto(dto: SectionDTO) -> LayoutSection:
    """The inverse of `layout_to_dto`."""
    if dto.section_kind != LAYOUT_SECTION_KIND:
        raise ValueError(
            f"expected section kind {LAYOUT_SECTION_KIND!r}, got {dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return LayoutSection.from_document(payload)


def debug_to_dto(section: DebugSection) -> SectionDTO:
    """The domain `DebugSection` as a `SectionDTO` -- see
    `sparse_section_codec.py`'s own module docstring."""
    return SectionDTO(
        section_kind=DEBUG_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[DEBUG_SECTION_KIND],
        payload=section.to_document(),
    )


def debug_from_dto(dto: SectionDTO) -> DebugSection:
    """The inverse of `debug_to_dto`."""
    if dto.section_kind != DEBUG_SECTION_KIND:
        raise ValueError(
            f"expected section kind {DEBUG_SECTION_KIND!r}, got {dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return DebugSection.from_document(payload)


def build_to_dto(section: BuildSection) -> SectionDTO:
    """The domain `BuildSection` as a `SectionDTO` -- see
    `sparse_section_codec.py`'s own module docstring."""
    return SectionDTO(
        section_kind=BUILD_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[BUILD_SECTION_KIND],
        payload=section.to_document(),
    )


def build_from_dto(dto: SectionDTO) -> BuildSection:
    """The inverse of `build_to_dto`."""
    if dto.section_kind != BUILD_SECTION_KIND:
        raise ValueError(
            f"expected section kind {BUILD_SECTION_KIND!r}, got {dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return BuildSection.from_document(payload)


def provenance_to_dto(section: ProvenanceSection) -> SectionDTO:
    """The domain `ProvenanceSection` as a `SectionDTO` -- see
    `sparse_section_codec.py`'s own module docstring."""
    return SectionDTO(
        section_kind=PROVENANCE_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[PROVENANCE_SECTION_KIND],
        payload=section.to_document(),
    )


def provenance_from_dto(dto: SectionDTO) -> ProvenanceSection:
    """The inverse of `provenance_to_dto`."""
    if dto.section_kind != PROVENANCE_SECTION_KIND:
        raise ValueError(
            f"expected section kind {PROVENANCE_SECTION_KIND!r}, got "
            f"{dto.section_kind!r}"
        )
    current = migrate_section_dto(dto)
    payload = current.to_dict()["payload"]
    assert isinstance(payload, dict)
    return ProvenanceSection.from_document(payload)
