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
(A1.1's "reads and writes the D6 layout"), landed jointly with ADR-063's own
Phase 8 (`one-semantic-pipeline.md`), which requires this joint landing to
satisfy its D8 constraint: *every DTO is a distinct, versioned class from the
domain `SemanticIR`/`Fact[T]`/`EntityId` objects, with an explicit
`to_dto()`/`from_dto()` (never `asdict`/a 500-line mirror deserializer) and a
migration adapter per DTO version.*

**Why this is a thin envelope, not a generic mirror of every domain type.**
D8's own text is explicit about what it forbids: a *second, generic*
identity/availability scheme invented at the storage layer, and an `asdict`-
shaped deserializer that silently drifts from the domain object it mirrors
(the whole-document `asdict()` deep copy ADR-062's Context names as the
defect this format replaces). It does not ask for a hand-written mirror of
every field on every domain dataclass — that would itself be exactly the
"second representation kept in sync by hand" this plan's own Governing
Invariant elsewhere warns against. Each concrete section type owns its own
explicit, field-by-field encoding already: `semantic_ir_codec.py`'s
`semantic_ir_to_document`/`semantic_ir_from_document` is the first of these
(ADR-063 Phase 6, extracted to a pure object<->document pair for this module
to build on rather than duplicated here). `SectionDTO` below is the one
*shared* piece all of those encodings plug into: a versioned envelope a
writer stores as one content-addressed D7 object and a reader can recognize
and migrate before trusting the payload underneath it.

A migration adapter is a plain function
`Callable[[Mapping[str, Any]], Mapping[str, Any]]` keyed by the DTO version
it accepts, registered per section kind (`_MIGRATIONS`) — there is
deliberately no generic "diff two dataclasses" machinery, because a real
migration is domain-specific by nature (rename a field, split one field into
two, change a value's unit) and a generic differ cannot express any of those
safely. `migrate_section_dto` walks the registered chain from a stored
version up to the section's current version, one registered step at a time,
so a version this build does not recognize the far side of (no chain reaches
it) is refused rather than silently accepted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..model.semantic_ir import SemanticIR
from .canonical import canonical_form
from .guards import (
    identity_text as _identity_text,
    mapping as _mapping,
    required_field as _required_field,
    strict_int as _strict_int,
)
from .semantic_ir_codec import semantic_ir_from_document, semantic_ir_to_document

__all__ = [
    "SECTION_SCHEMA_VERSIONS",
    "SEMANTIC_IR_SECTION_KIND",
    "SectionDTO",
    "migrate_section_dto",
    "semantic_ir_from_dto",
    "semantic_ir_to_dto",
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

#: Every section kind this module knows how to encode, and the current DTO
#: version each one is written at today — `StorageVersions.
#: section_schema_versions` (D2) is keyed by exactly this vocabulary, so a
#: package's manifest states which version *this* module wrote each section
#: under, independent of every other axis.
SECTION_SCHEMA_VERSIONS: Mapping[str, int] = {SEMANTIC_IR_SECTION_KIND: 1}

#: Per-section-kind migration chains, keyed by the DTO version a step reads
#: *from* — `{1: step_from_1_to_2, 2: step_from_2_to_3, ...}`. Empty for
#: every section kind today: `SEMANTIC_IR_SECTION_KIND` has shipped exactly
#: one version, so there is nothing yet to migrate from. The registry exists
#: now, ahead of a real version 2, so the *pattern* — one small, reviewed
#: function per version bump, chained rather than replaced — is established
#: before it is needed, the same way `StorageVersions`' own axes were
#: reserved ahead of a producer that fills them.
_MIGRATIONS: Mapping[
    str, Mapping[int, Callable[[Mapping[str, Any]], Mapping[str, Any]]]
] = {
    SEMANTIC_IR_SECTION_KIND: {},
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
        if version in seen:
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
