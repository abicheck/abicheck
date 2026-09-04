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

"""ADR-063 Track 4 (8B), third slice: typed DTOs for the six remaining
sparse legacy sections -- `binary`/`declarations`/`layout`/`debug`/`build`/
`provenance` -- closing the design gap `types_section_codec.py`'s own
module docstring named as blocking every section beyond `types`/`graph`.

**The gap, restated precisely.** `types`/`graph` could each become one
plain dataclass with a single always-present field because
`storage.legacy_sections._SECTION_FIELDS` names exactly one field for each,
and that field is safe to require unconditionally. The six sections here
each carry *multiple* fields, most of which are genuinely, schema-version-
dependent *optional* -- present only from the schema version that
introduced them onward (`platform`/`kabi`/`build_mode`/`source_mtime` on
`binary`, `typedefs_qualified`/`constants`/... on `declarations`, and so
on). A naive dataclass with `field(default=...)` per key would reconstruct
a value for a key the *source* document never carried at all, silently
corrupting `storage.import_v1.export_legacy_snapshot`'s byte-for-byte
round-trip contract the moment a schema-v1-v5-shaped fixture (or a
similarly old real document) passed through it -- the exact regression
`tests/fixtures/schema/v*.json`'s own CI-golden contract exists to catch,
and the reason those two prior PRs stopped at one field each rather than
attempting this.

**The fix: split each section's fields by what
`storage.legacy_sections._REQUIRED_SECTION_FIELDS` already, independently
proves about them.** That table is derived empirically from
`tests/fixtures/schema/v1.json` -- the format's own oldest fixture, before
any version-gated field existed -- so *any* key it lists is safe to require
unconditionally in any document this build can still read (that table's own
docstring states the guarantee; this module is the first consumer of it
beyond the post-hoc `missing_required_section_fields` check).
`_SparseSectionMixin`'s subclasses turn each such key into a real, always-
present, named dataclass attribute (`BinarySection.elf`, `.pe`, `.macho`,
...) instead of a generic dict lookup -- the same "no longer indistinguishable
from an arbitrary opaque blob" property `TypesSection`/`GraphSection`
already have for their one field, extended to the fields that can actually
support it. Every other field in the section's `_SECTION_FIELDS` allowlist
stays in `extra`, a validated (allowlist-checked, canonically-frozen)
pass-through mapping that a round trip never adds a key to or drops a key
from -- so a schema-v1 document missing `platform` entirely round-trips
with `binary.extra` simply lacking that key, exactly as `legacy_section_to_dto`'s
own generic pass-through already did, while a document that *does* carry it
keeps it, still exactly as given. Nothing here defaults, guesses, or
reconstructs a field's value; the split is "typed" only in the sense the
required half of D8 actually supports today.

**Required-field shape validation** (Codex review, PR #1044): a required
field's own top-level wire shape (mapping-or-null / mapping / list / str,
matching the real `AbiSnapshot` field it round-trips) is checked before
freezing -- `BinarySection.from_document({"elf": [], "pe": None, "macho":
None})` is now rejected rather than silently accepted, frozen, and later
read back by `serialization.snapshot_from_dict` as a *confirmed-absent*
`elf` (turning corrupted evidence into missing evidence, never the other
way, since a wrong-shaped value is always caught before storage). Shallow
by design, matching `TypesSection`/`GraphSection`'s own precedent for their
one field: only the field's own container type is checked, never what's
inside it -- decoding a `RecordType`/`ElfMetadata` entry's internal
structure remains real, separately-scoped future work.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, TypeVar

from .canonical import canonical_form

__all__ = [
    "BinarySection",
    "BuildSection",
    "DebugSection",
    "DeclarationsSection",
    "LayoutSection",
    "ProvenanceSection",
]

_T = TypeVar("_T", bound="_SparseSectionMixin")


def _freeze(value: Any) -> Any:
    """Mirrors `types_section_codec._freeze`/`dto._freeze` exactly."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _unfreeze(value: Any) -> Any:
    """The inverse of `_freeze` — mirrors `types_section_codec._unfreeze`
    exactly."""
    if isinstance(value, MappingProxyType):
        return {key: _unfreeze(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_unfreeze(item) for item in value]
    return value


#: The top-level wire shapes a required *or* optional field can be
#: validated against (Codex review, PR #1044, two rounds: first
#: `_freeze_required` accepted any value at all for a required field -- a
#: malformed `elf: []` would freeze and round-trip unchanged, then
#: `serialization.snapshot_from_dict` reads a non-mapping `elf` as absent,
#: silently turning corrupted evidence into confirmed-missing evidence;
#: second, the identical gap for `extra`'s own optional fields -- e.g.
#: `BuildSection.from_document({"build_source": []})`). Intentionally
#: shallow -- only a field's own top-level container type, never its
#: internal structure -- the same boundary `TypesSection`/`GraphSection`
#: already draw for their own one field (validate that `types` is a
#: `list`/`surface_graph` is a `Mapping`, never decode what's inside);
#: deep-decoding a `RecordType`/`ElfMetadata`/`BuildSourcePack` entry
#: remains real, separately-scoped future work this module's own docstring
#: already declines to attempt.
_MAPPING_OR_NONE = "mapping_or_none"
_MAPPING = "mapping"
#: An *ordered* sequence -- `list`/`tuple` only, never `set`/`frozenset`
#: (Codex review, PR #1044, fourth round: `ast_compile_args` is a real
#: compiler invocation's argument list, where position is semantically
#: significant -- accepting a `set` let `canonical_form`'s own sorting
#: silently invent an argument order a `set` never had to begin with,
#: turning real provenance into fabricated provenance rather than merely
#: rejecting a malformed one). Still accepts both `list` and `tuple` since
#: several real `AbiSnapshot` fields this shape covers are `tuple[...]`-
#: typed at the Python attribute level (`ast_compile_args`,
#: `dwarf_layout_coherence_mismatches`) and reach this check before any
#: JSON round trip has coerced them to a `list`.
_LIST = "list"
#: The `set[...]`-typed sibling of `_LIST` -- `build_context_defines` is
#: `AbiSnapshot`'s only field where the collection is genuinely unordered
#: (a set of preprocessor defines; `serialization.py` itself round-trips it
#: via `set(d.get(...))`), so accepting `set`/`frozenset` here does not
#: fabricate an order the way it would for `_LIST`.
_UNORDERED_LIST = "unordered_list"
_STR = "str"
_STR_OR_NONE = "str_or_none"
_BOOL = "bool"
_BOOL_OR_NONE = "bool_or_none"
#: `int | None` -- `source_size` specifically (Codex review, PR #1044,
#: fourth round: a fractional value passed `_NUMBER_OR_NONE` and then
#: silently broke `fold_l0_hard_removals`'s binary-identity comparison
#: against `Path.stat().st_size`, itself always an `int`).
_INT_OR_NONE = "int_or_none"
#: `float | int | None` -- reserved for a genuinely fractional-or-whole
#: numeric field (`source_mtime`, a Unix timestamp); `bool` is an `int`
#: subclass in Python and is excluded explicitly from both numeric shapes,
#: the same discipline `storage.import_v1`'s own schema_version guards
#: apply, so a stray `true`/`false` doesn't pass as a timestamp/size.
_NUMBER_OR_NONE = "number_or_none"


def _check_field_shape(section_kind: str, name: str, value: Any, shape: str) -> None:
    if shape == _MAPPING_OR_NONE:
        ok = value is None or isinstance(value, Mapping)
    elif shape == _MAPPING:
        ok = isinstance(value, Mapping)
    elif shape == _LIST:
        ok = isinstance(value, (list, tuple))
    elif shape == _UNORDERED_LIST:
        ok = isinstance(value, (list, tuple, set, frozenset))
    elif shape == _STR:
        ok = isinstance(value, str)
    elif shape == _STR_OR_NONE:
        ok = value is None or isinstance(value, str)
    elif shape == _BOOL:
        ok = isinstance(value, bool)
    elif shape == _BOOL_OR_NONE:
        ok = value is None or isinstance(value, bool)
    elif shape == _INT_OR_NONE:
        ok = value is None or (isinstance(value, int) and not isinstance(value, bool))
    elif shape == _NUMBER_OR_NONE:
        ok = value is None or (
            isinstance(value, (int, float)) and not isinstance(value, bool)
        )
    else:  # pragma: no cover - defensive: every *_FIELD_SHAPES entry below
        # is one of the constants above; a fifth value would be a typo in
        # this module itself, not reachable from any document content.
        raise AssertionError(f"unknown field shape {shape!r}")
    if not ok:
        raise ValueError(
            f"a {section_kind!r} section payload's {name!r} must be a "
            f"{shape.replace('_', ' ')}, not {type(value).__name__}"
        )


class _SparseSectionMixin:
    """Shared validation/freeze/document machinery for a sparse legacy
    section dataclass. Not itself a dataclass -- each concrete section below
    declares its own `REQUIRED_FIELDS` as real, named dataclass attributes
    (so `section.elf` works, the whole point of promoting them), which
    Python dataclasses cannot generate generically from a class-level tuple
    of names. This mixin only factors out the logic that *is* identical
    across every section: freezing/unfreezing, allowlist validation, and the
    document round trip.
    """

    #: The D8 section kind this class encodes (`"binary"`, `"debug"`, ...).
    SECTION_KIND: ClassVar[str] = ""
    #: Field names guaranteed present whenever this section is present at
    #: all (`storage.legacy_sections._REQUIRED_SECTION_FIELDS[SECTION_KIND]`,
    #: restated here as an ordered tuple matching each subclass's own
    #: declared attributes, in declaration order).
    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = ()
    #: Every other field `storage.legacy_sections._SECTION_FIELDS[SECTION_KIND]`
    #: allows -- genuinely optional, schema-version-dependent. Kept in
    #: `extra`, never defaulted.
    OPTIONAL_FIELDS: ClassVar[frozenset[str]] = frozenset()
    #: Each `REQUIRED_FIELDS` entry's own top-level wire shape (one of the
    #: constants above `_check_field_shape`), checked by `_freeze_required`
    #: before freezing -- see that function's own docstring for why this
    #: stays shallow. A name absent here is not shape-checked at all (no
    #: subclass currently needs that; every `REQUIRED_FIELDS` entry today
    #: has a real, always-present type).
    REQUIRED_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {}
    #: The identical idea, for `OPTIONAL_FIELDS` -- every key `extra` may
    #: carry, mapped to its own real `AbiSnapshot` field's top-level shape
    #: (Codex review, PR #1044, second round: `_freeze_extra` validated only
    #: which *keys* `extra` may carry, not each key's own value shape, so
    #: `BuildSection.from_document({"build_source": []})` froze and
    #: round-tripped a malformed record unchanged). Every `OPTIONAL_FIELDS`
    #: entry across all six sections has a declared shape here -- unlike
    #: `REQUIRED_FIELD_SHAPES`, this is meant to be exhaustive.
    OPTIONAL_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {}

    #: Declared by every concrete (`@dataclass`) subclass; only annotated
    #: here so mypy can see it through the mixin.
    extra: Mapping[str, Any]

    def _freeze_required(self) -> None:
        for name in self.REQUIRED_FIELDS:
            value = getattr(self, name)
            shape = self.REQUIRED_FIELD_SHAPES.get(name)
            if shape is not None:
                _check_field_shape(self.SECTION_KIND, name, value, shape)
            object.__setattr__(self, name, _freeze(canonical_form(value)))

    def _freeze_extra(self) -> None:
        extra = self.extra
        if not isinstance(extra, Mapping):
            raise ValueError(
                f"a {self.SECTION_KIND!r} section's 'extra' must be a "
                f"mapping, not {type(extra).__name__}"
            )
        unknown = set(extra) - self.OPTIONAL_FIELDS
        if unknown:
            raise ValueError(
                f"a {self.SECTION_KIND!r} section's 'extra' fields must be "
                f"a subset of {sorted(self.OPTIONAL_FIELDS)}, not "
                f"{sorted(unknown)}"
            )
        for name, value in extra.items():
            shape = self.OPTIONAL_FIELD_SHAPES.get(name)
            if shape is not None:
                _check_field_shape(self.SECTION_KIND, name, value, shape)
        object.__setattr__(self, "extra", _freeze(canonical_form(dict(extra))))

    def to_document(self) -> dict[str, Any]:
        """The full section payload -- every required field at the top
        level plus `extra`'s own keys, exactly as originally given (never
        reordered into a canonical key order beyond what `dict` merging
        already does, and never padded with a key the source lacked)."""
        doc: dict[str, Any] = {
            name: _unfreeze(getattr(self, name)) for name in self.REQUIRED_FIELDS
        }
        doc.update(_unfreeze(self.extra))
        return doc

    @classmethod
    def _split_document(
        cls: type[_T], payload: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Validate *payload* against this class's own required/optional
        allowlists and split it into `(required_kwargs, extra_kwargs)` --
        the shared half of every subclass's own `from_document`."""
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"a {cls.SECTION_KIND!r} section payload must be a mapping, "
                f"not {type(payload).__name__}"
            )
        missing = [name for name in cls.REQUIRED_FIELDS if name not in payload]
        if missing:
            raise ValueError(
                f"a {cls.SECTION_KIND!r} section payload must carry {sorted(missing)}"
            )
        allowed = set(cls.REQUIRED_FIELDS) | cls.OPTIONAL_FIELDS
        extra_keys = set(payload) - allowed
        if extra_keys:
            raise ValueError(
                f"a {cls.SECTION_KIND!r} section payload may only carry "
                f"{sorted(allowed)}, not {sorted(extra_keys)}"
            )
        required_kwargs = {name: payload[name] for name in cls.REQUIRED_FIELDS}
        extra_kwargs = {
            key: value for key, value in payload.items() if key not in required_kwargs
        }
        return required_kwargs, extra_kwargs


@dataclass(frozen=True, kw_only=True)
class BinarySection(_SparseSectionMixin):
    """The `"binary"` D8 legacy section's typed DTO. `elf`/`pe`/`macho` are
    real, always-present fields (`_REQUIRED_SECTION_FIELDS["binary"]`);
    every other allowed key (`kabi`/`platform`/`elf_only_mode`/`build_id`/
    `build_mode`/`source_path`/`source_mtime`/`source_mtime_epoch`/
    `source_size`) lives in `extra`, presence-preserving."""

    SECTION_KIND: ClassVar[str] = "binary"
    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = ("elf", "pe", "macho")
    #: `model.snapshot.AbiSnapshot.elf`/`.pe`/`.macho` are each
    #: `XxxMetadata | None` -- serialized as `null` or a nested mapping,
    #: never a list/string/bool (Codex review, PR #1044).
    REQUIRED_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {
        "elf": _MAPPING_OR_NONE,
        "pe": _MAPPING_OR_NONE,
        "macho": _MAPPING_OR_NONE,
    }
    OPTIONAL_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "kabi",
            "platform",
            "elf_only_mode",
            "build_id",
            "build_mode",
            "source_path",
            "source_mtime",
            "source_mtime_epoch",
            "source_size",
        }
    )
    #: `AbiSnapshot.kabi` is `KabiMetadata | None`; `.platform`/`.build_id`/
    #: `.source_path` are `str | None`; `.elf_only_mode`/`.source_mtime_epoch`
    #: are plain `bool`; `.build_mode` is `BuildMode | None` (itself a
    #: dataclass -- a mapping, not a string); `.source_mtime` is
    #: `float | None`; `.source_size` is `int | None`.
    OPTIONAL_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {
        "kabi": _MAPPING_OR_NONE,
        "platform": _STR_OR_NONE,
        "elf_only_mode": _BOOL,
        "build_id": _STR_OR_NONE,
        "build_mode": _MAPPING_OR_NONE,
        "source_path": _STR_OR_NONE,
        "source_mtime": _NUMBER_OR_NONE,
        "source_mtime_epoch": _BOOL,
        "source_size": _INT_OR_NONE,
    }

    elf: Any
    pe: Any
    macho: Any
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._freeze_required()
        self._freeze_extra()

    @classmethod
    def from_document(cls, payload: Mapping[str, Any]) -> BinarySection:
        required, extra = cls._split_document(payload)
        return cls(**required, extra=extra)


@dataclass(frozen=True, kw_only=True)
class DeclarationsSection(_SparseSectionMixin):
    """The `"declarations"` D8 legacy section's typed DTO. `functions`/
    `variables`/`enums`/`typedefs`/`sycl` are real, always-present fields;
    every other allowed key (`typedefs_qualified`/`typedef_entity_ids`/
    `constants`/`constant_entity_ids`/`python_ext`/`python_api`/
    `numpy_capi`) lives in `extra`."""

    SECTION_KIND: ClassVar[str] = "declarations"
    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = (
        "functions",
        "variables",
        "enums",
        "typedefs",
        "sycl",
    )
    #: `AbiSnapshot.functions`/`.variables`/`.enums` are plain `list[...]`
    #: fields (never `None`); `.typedefs` is `dict[str, str]` (never a
    #: bare list/string); `.sycl` is `SyclMetadata | None` (Codex review,
    #: PR #1044).
    REQUIRED_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {
        "functions": _LIST,
        "variables": _LIST,
        "enums": _LIST,
        "typedefs": _MAPPING,
        "sycl": _MAPPING_OR_NONE,
    }
    OPTIONAL_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "typedefs_qualified",
            "typedef_entity_ids",
            "constants",
            "constant_entity_ids",
            "python_ext",
            "python_api",
            "numpy_capi",
        }
    )
    #: `.typedefs_qualified`/`.constants` are `dict[str, str]`;
    #: `.typedef_entity_ids`/`.constant_entity_ids` are `dict[str, EntityId]`
    #: (still a mapping at the top level, the only level checked here);
    #: `.python_ext`/`.python_api`/`.numpy_capi` are each `XxxMetadata |
    #: None`.
    OPTIONAL_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {
        "typedefs_qualified": _MAPPING,
        "typedef_entity_ids": _MAPPING,
        "constants": _MAPPING,
        "constant_entity_ids": _MAPPING,
        "python_ext": _MAPPING_OR_NONE,
        "python_api": _MAPPING_OR_NONE,
        "numpy_capi": _MAPPING_OR_NONE,
    }

    functions: Any
    variables: Any
    enums: Any
    typedefs: Any
    sycl: Any
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._freeze_required()
        self._freeze_extra()

    @classmethod
    def from_document(cls, payload: Mapping[str, Any]) -> DeclarationsSection:
        required, extra = cls._split_document(payload)
        return cls(**required, extra=extra)


@dataclass(frozen=True, kw_only=True)
class LayoutSection(_SparseSectionMixin):
    """The `"layout"` D8 legacy section's typed DTO. This section has no
    field `_REQUIRED_SECTION_FIELDS` proves present since schema v1 (the
    whole section postdates v1), so every allowed key
    (`dwarf_layout_coherence`/`dwarf_layout_coherence_mismatches`/
    `scope_fallback`/`conditional_fields`/`contract`/`dependency_scope`)
    lives in `extra` -- still a real, dedicated, versioned class rather than
    the generic `legacy_section_to_dto` pass-through, per this module's own
    "typed only in the sense the required half of D8 actually supports
    today" scoping note."""

    SECTION_KIND: ClassVar[str] = "layout"
    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = ()
    OPTIONAL_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "dwarf_layout_coherence",
            "dwarf_layout_coherence_mismatches",
            "scope_fallback",
            "conditional_fields",
            "contract",
            "dependency_scope",
        }
    )
    #: `.dwarf_layout_coherence`/`.scope_fallback`/`.dependency_scope` are
    #: `str | None`; `.dwarf_layout_coherence_mismatches` is
    #: `tuple[str, ...]` (a JSON list); `.conditional_fields` is a nested
    #: `dict`; `.contract` is `ExtractionContract | None`.
    OPTIONAL_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {
        "dwarf_layout_coherence": _STR_OR_NONE,
        "dwarf_layout_coherence_mismatches": _LIST,
        "scope_fallback": _STR_OR_NONE,
        "conditional_fields": _MAPPING,
        "contract": _MAPPING_OR_NONE,
        "dependency_scope": _STR_OR_NONE,
    }

    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._freeze_extra()

    @classmethod
    def from_document(cls, payload: Mapping[str, Any]) -> LayoutSection:
        _required, extra = cls._split_document(payload)
        return cls(extra=extra)


@dataclass(frozen=True, kw_only=True)
class DebugSection(_SparseSectionMixin):
    """The `"debug"` D8 legacy section's typed DTO. `dwarf`/`dwarf_advanced`
    are real, always-present fields; every other allowed key (the AST/
    toolchain/fact-reliability flags) lives in `extra`."""

    SECTION_KIND: ClassVar[str] = "debug"
    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = ("dwarf", "dwarf_advanced")
    #: `AbiSnapshot.dwarf`/`.dwarf_advanced` are each `XxxMetadata | None`
    #: (Codex review, PR #1044).
    REQUIRED_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {
        "dwarf": _MAPPING_OR_NONE,
        "dwarf_advanced": _MAPPING_OR_NONE,
    }
    OPTIONAL_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "from_headers",
            "ast_producer",
            "ast_toolchain",
            "ast_fallback_reason",
            "ast_toolchain_supported",
            "ast_toolchain_unsupported_reasons",
            "frontend_context_kind",
            "ast_resolved_standard",
            "ast_resolved_standard_fact",
            "ast_cplusplus_macro",
            "ast_compile_args",
            "ast_sysroot",
            "fact_provenance",
            "header_cv_facts_reliable",
            "clang_deprecation_facts_reliable",
            "clang_field_initializer_facts_reliable",
            "clang_vtable_facts_reliable",
            "clang_restrict_facts_reliable",
            "clang_va_list_facts_reliable",
            "castxml_var_access_facts_reliable",
            "parsed_with_build_context",
            "build_context_defines",
        }
    )
    #: Each mapped to `AbiSnapshot`'s own declared type: `.from_headers`/
    #: `.parsed_with_build_context` and the seven `*_facts_reliable` flags
    #: are plain `bool`; `.ast_toolchain_supported` is `bool | None`;
    #: `.ast_producer`/`.ast_fallback_reason`/`.frontend_context_kind`/
    #: `.ast_resolved_standard`/`.ast_cplusplus_macro`/`.ast_sysroot` are
    #: `str | None`; `.ast_toolchain`/`.fact_provenance` are
    #: `dict[str, str]`; `.ast_toolchain_unsupported_reasons` is
    #: `list[str]`; `.ast_compile_args` is `tuple[str, ...]` (a JSON list);
    #: `.build_context_defines` is a `set[str]` (also a JSON list --
    #: `serialization.py` round-trips it via `set(d.get(...))`);
    #: `.ast_resolved_standard_fact` is `Fact[str | None] | None`, encoded
    #: as `null` or a mapping (`storage/fact_codec.py`).
    OPTIONAL_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {
        "from_headers": _BOOL,
        "ast_producer": _STR_OR_NONE,
        "ast_toolchain": _MAPPING,
        "ast_fallback_reason": _STR_OR_NONE,
        "ast_toolchain_supported": _BOOL_OR_NONE,
        "ast_toolchain_unsupported_reasons": _LIST,
        "frontend_context_kind": _STR_OR_NONE,
        "ast_resolved_standard": _STR_OR_NONE,
        "ast_resolved_standard_fact": _MAPPING_OR_NONE,
        "ast_cplusplus_macro": _STR_OR_NONE,
        "ast_compile_args": _LIST,
        "ast_sysroot": _STR_OR_NONE,
        "fact_provenance": _MAPPING,
        "header_cv_facts_reliable": _BOOL,
        "clang_deprecation_facts_reliable": _BOOL,
        "clang_field_initializer_facts_reliable": _BOOL,
        "clang_vtable_facts_reliable": _BOOL,
        "clang_restrict_facts_reliable": _BOOL,
        "clang_va_list_facts_reliable": _BOOL,
        "castxml_var_access_facts_reliable": _BOOL,
        "parsed_with_build_context": _BOOL,
        "build_context_defines": _UNORDERED_LIST,
    }

    dwarf: Any
    dwarf_advanced: Any
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._freeze_required()
        self._freeze_extra()

    @classmethod
    def from_document(cls, payload: Mapping[str, Any]) -> DebugSection:
        required, extra = cls._split_document(payload)
        return cls(**required, extra=extra)


@dataclass(frozen=True, kw_only=True)
class BuildSection(_SparseSectionMixin):
    """The `"build"` D8 legacy section's typed DTO. No field is proven
    present since v1 (this section, like `layout`, postdates it entirely),
    so `build_source_pack`/`build_source`/`evidence_pack` all live in
    `extra` -- `evidence_pack` in particular is the documented pre-schema-v8
    spelling of `build_source_pack` (`legacy_sections._SECTION_FIELDS`'s own
    comment), never both at once in a document this build itself writes,
    but a real older document may carry either."""

    SECTION_KIND: ClassVar[str] = "build"
    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = ()
    OPTIONAL_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"build_source_pack", "build_source", "evidence_pack"}
    )
    #: `.build_source_pack` is `BuildSourceRef | None`, `.build_source` is
    #: `BuildSourcePack | None` -- both dataclasses, so a mapping or `null`.
    #: `.evidence_pack` is the pre-schema-v8 spelling of the same field
    #: (this class's own docstring), so it shares the identical shape.
    OPTIONAL_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {
        "build_source_pack": _MAPPING_OR_NONE,
        "build_source": _MAPPING_OR_NONE,
        "evidence_pack": _MAPPING_OR_NONE,
    }

    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._freeze_extra()

    @classmethod
    def from_document(cls, payload: Mapping[str, Any]) -> BuildSection:
        _required, extra = cls._split_document(payload)
        return cls(extra=extra)


@dataclass(frozen=True, kw_only=True)
class ProvenanceSection(_SparseSectionMixin):
    """The `"provenance"` D8 legacy section's typed DTO. `library`/
    `version` are real, always-present fields; every other allowed key
    (`language_profile`/`dependency_info`/`git_commit`/`git_tag`/
    `created_at`/`dump_provenance`) lives in `extra`."""

    SECTION_KIND: ClassVar[str] = "provenance"
    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = ("library", "version")
    #: `AbiSnapshot.library`/`.version` are plain `str` fields, no default,
    #: never `None` (Codex review, PR #1044).
    REQUIRED_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {
        "library": _STR,
        "version": _STR,
    }
    OPTIONAL_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "language_profile",
            "dependency_info",
            "git_commit",
            "git_tag",
            "created_at",
            "dump_provenance",
        }
    )
    #: `.language_profile`/`.git_commit`/`.git_tag`/`.created_at` are
    #: `str | None`; `.dependency_info` is `DependencyInfo | None`.
    #: `.dump_provenance` is not an `AbiSnapshot` field at all -- it's
    #: unconditionally assigned a `dict` literal by `cli_dump_helpers
    #: .fold_dump_provenance_into_dict` whenever it adds the key at all
    #: (`legacy_sections._SECTION_FIELDS`'s own comment on this key), so it
    #: is always a mapping, never `None`.
    OPTIONAL_FIELD_SHAPES: ClassVar[Mapping[str, str]] = {
        "language_profile": _STR_OR_NONE,
        "dependency_info": _MAPPING_OR_NONE,
        "git_commit": _STR_OR_NONE,
        "git_tag": _STR_OR_NONE,
        "created_at": _STR_OR_NONE,
        "dump_provenance": _MAPPING,
    }

    library: Any
    version: Any
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._freeze_required()
        self._freeze_extra()

    @classmethod
    def from_document(cls, payload: Mapping[str, Any]) -> ProvenanceSection:
        required, extra = cls._split_document(payload)
        return cls(**required, extra=extra)
