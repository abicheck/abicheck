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

"""ADR-063 Track 4 (8B): the `"types"` D8 legacy section's own typed DTO --
the first section beyond `semantic_ir` to be promoted off `storage.dto
.legacy_section_to_dto`'s generic "pass the payload through verbatim"
envelope, per that sub-phase's own goal ("Typed DTOs for the remaining
sections beyond `semantic_ir`").

**Why `"types"` is the section this lands for first.** `storage.
legacy_sections._SECTION_FIELDS["types"]` is exactly one field
(`("types",)`), and `_REQUIRED_SECTION_FIELDS["types"]` names that same
single field as required whenever the section is present at all -- so,
unlike every other legacy section (several of which carry a genuinely
sparse, schema-version-dependent set of *optional* fields -- see
`legacy_sections.py`'s own `_REQUIRED_SECTION_FIELDS` docstring for why that
sparsity can't be papered over with typed-dataclass defaults without risking
`storage.import_v1.export_legacy_snapshot`'s byte-for-byte round-trip
contract across every real `tests/fixtures/schema/v*.json` fixture), a
`"types"` section's payload has exactly one possible shape: `{"types":
[...]}`. There is no field-presence ambiguity here for a typed wrapper to
get wrong, which is what makes this section safe to promote as a first,
narrowly-scoped slice rather than attempting all eight legacy sections (each
with its own sparsity/back-compat profile) in one pass.

**What "typed" means here, precisely.** Per `storage/dto.py`'s own module
docstring (D8's own text on this point): the grain D8 asks for is the
*field*, not that field's internal shape -- `AbiSnapshot.types`' own list
entries stay exactly the JSON `serialization.snapshot_to_dict()` already
produces for each `RecordType`/`EnumType`/... (decoding *that* structure
into typed domain objects is real, separately-scoped future work this slice
does not attempt, the same boundary `semantic_ir_codec.py` itself draws for
its own payload's internal shape). What this module adds is the explicit,
versioned wrapper *around* that one field: a real `TypesSection` dataclass
with its own `to_document()`/`from_document()`, so a `"types"` section is no
longer indistinguishable, in the DTO layer, from an arbitrary opaque legacy
blob.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .canonical import canonical_form

__all__ = ["TypesSection"]


def _freeze(value: Any) -> Any:
    """*value* — already `canonical_form`'s output, so a tree of only
    `dict`/`list`/`str`/`int`/`float`/`bool`/`None` — rebuilt so nothing
    reachable from a `TypesSection` after construction is mutable.

    Mirrors `storage.dto._freeze` exactly (a private sibling this module
    may not import), for the identical reason that function exists: a
    `frozen=True` dataclass whose one field is a plain `tuple` of ordinary,
    mutable `dict`/`list` entries is not actually immutable -- the caller's
    own entry objects (or a document later handed back by `to_document`)
    stay reachable and mutable, so a caller mutating either one could
    silently change this `TypesSection`'s own content after construction
    (Codex review, fresh evidence — the exact two-round aliasing defect
    `storage.dto.SectionDTO`'s own `_freeze` docstring already documents,
    reproduced here for this DTO's own untouched field).
    """
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _unfreeze(value: Any) -> Any:
    """The inverse of `_freeze` — a fresh, ordinary, mutable `dict`/`list`
    tree, detached from this DTO's own frozen storage. Mirrors
    `storage.dto._unfreeze` exactly, for the same reason."""
    if isinstance(value, MappingProxyType):
        return {key: _unfreeze(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_unfreeze(item) for item in value]
    return value


@dataclass(frozen=True)
class TypesSection:
    """The `"types"` D8 legacy section's one field, typed.

    `types` holds the already-serialized `AbiSnapshot.types` list verbatim
    (each entry is a plain JSON dict, one per `RecordType`/`EnumType`/...) --
    see this module's own docstring for why decoding that internal shape
    further is out of scope here. `__post_init__` runs every entry through
    `canonical_form` + `_freeze` (the identical two-step
    `storage.dto.SectionDTO.__post_init__` already applies to its own
    `payload` field), so nothing reachable from a constructed `TypesSection`
    ever aliases a caller's own mutable objects; `to_document()` deep-thaws
    it back to ordinary `dict`/`list` for JSON-shaped storage.
    """

    types: tuple[Any, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "types", _freeze(canonical_form(list(self.types))))

    def to_document(self) -> dict[str, Any]:
        """The `{"types": [...]}` payload shape `storage.dto.types_to_dto`
        stores -- the exact section-payload shape
        `storage.legacy_sections.split_legacy_document` already produces for
        this section, so a round trip through this wrapper changes nothing
        about the stored bytes."""
        return {"types": _unfreeze(self.types)}

    @classmethod
    def from_document(cls, payload: Mapping[str, Any]) -> TypesSection:
        """The inverse of `to_document` — *payload* is a `"types"` section's
        own payload mapping (already validated, by the caller, to carry only
        this section's own allowlisted key).

        Raises `ValueError` if `types` is missing or is not a list -- the
        same "a section whose object hashes and decodes fine can still have
        lost content within its own JSON" defect
        `storage.import_v1.export_legacy_snapshot`'s own
        `missing_required_section_fields` check exists to catch for every
        other legacy section, made structural here instead of a separate
        post-hoc check.
        """
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"a 'types' section payload must be a mapping, not "
                f"{type(payload).__name__}"
            )
        raw = payload.get("types")
        # `list` for a fresh `split_legacy_document` payload; `tuple` when
        # *payload* comes from an already-migrated `SectionDTO.payload`
        # (`storage.dto.SectionDTO`'s own frozen storage converts every
        # list to a tuple -- see that class's `_freeze`) -- both are the
        # identical logical sequence, so both are accepted here.
        if not isinstance(raw, (list, tuple)):
            raise ValueError(
                f"a 'types' section payload must carry a 'types' list -- got {raw!r}"
            )
        extra = set(payload) - {"types"}
        if extra:
            raise ValueError(
                f"a 'types' section payload may only carry 'types', not {sorted(extra)}"
            )
        # `__post_init__` freezes this, so the constructor's own tuple(...)
        # here need not defend against aliasing itself.
        return cls(types=tuple(raw))
