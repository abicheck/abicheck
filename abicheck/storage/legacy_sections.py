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

"""ADR-063 Phase 8's D8 full section split for a legacy `AbiSnapshot`
document — the promised follow-up to `storage/import_v1.py`'s first-slice
"everything but `semantic_ir` travels as one opaque `legacy_document`" gap.

**What this module does, and does not, do.** `serialization.snapshot_to_dict()`
already turns a live `AbiSnapshot` into one explicit, hand-maintained JSON
document (not `asdict()` alone — see that function's own docstring for the
platform-enum/`Fact[T]`/`BuildSourcePack`/`surface_graph`/`semantic_ir`
encodings layered on top of it). This module does not re-derive or duplicate
that encoding: it takes the *already-produced* document (the same
`Mapping[str, Any]` shape `storage.import_v1.import_legacy_snapshot` already
accepts) and partitions its top-level keys across D8's named section
vocabulary (`storage.package.SECTION_KINDS`) via one explicit, reviewed
allowlist per section — `_SECTION_FIELDS` below. Each key belongs to exactly
one section; a key not in any list is an error, not a silent drop into a
catch-all, so a new `AbiSnapshot` field added without updating this module's
allowlist fails loudly (`split_legacy_document`) instead of vanishing into an
opaque remainder the way the first-slice `legacy_document` blob could.

**Why this satisfies D8 without inventing a second per-field domain
representation.** D8 forbids a *generic* identity/availability scheme
reinvented at the storage layer, and a mirrored `asdict`-shaped deserializer
that silently drifts from the domain object it copies. Neither applies here:
this module invents no new encoding for `ElfMetadata`/`DwarfMetadata`/etc —
every value stays exactly the JSON shape `snapshot_to_dict()` already
produced (the same reasoning `storage/dto.py`'s own docstring gives for
building `semantic_ir_to_dto` on `semantic_ir_codec` rather than duplicating
it). What *is* new, and what makes each section a real, typed D8 unit rather
than a bigger opaque blob, is the explicit, versioned *partition*: each
section's `_SECTION_FIELDS` entry is its schema (exactly which keys it may
carry), checked on both ends (`split_legacy_document` refuses an unknown key;
`join_legacy_document` refuses a section payload carrying a key outside its
own declared set), and each section is independently versioned via
`storage.dto.SECTION_SCHEMA_VERSIONS` the same way `semantic_ir` already is.

**What is deliberately left as a single key's worth of un-split JSON.** A
nested value that is itself a whole subdocument (`elf`, `dwarf`,
`build_source`, ...) is not decomposed further — the *field*, not its
internal shape, is D8's grain here, matching every other section this
module defines. Splitting `dwarf`'s own internal shape into further D8
sub-sections is real, separately-scoped future work with no scheduled phase,
the same way ADR-063's own Phase 10 checklist names several "real, scheduled,
separately-justified future work" residuals rather than pretending they are
covered.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .guards import mapping as _mapping

__all__ = [
    "LEGACY_SECTION_KINDS",
    "SCHEMA_VERSION_KEY",
    "join_legacy_document",
    "split_legacy_document",
]

#: `schema_version` and the two keys `storage.dto.semantic_ir_to_dto`/
#: `storage.import_v1.import_legacy_snapshot` already promote onto their own,
#: non-legacy sections — never assigned to a legacy section here, and
#: rejected as "unknown key" if a caller's document is missing them from
#: this exclusion set for some reason (it never is, in practice: this
#: constant exists once and every user of it agrees on the same three names).
SCHEMA_VERSION_KEY = "schema_version"
_PROMOTED_KEYS = ("semantic_ir", "semantic_ir_conflicts", SCHEMA_VERSION_KEY)

#: One explicit, reviewed allowlist per D8 legacy section kind — every
#: `AbiSnapshot` field `serialization.snapshot_to_dict()` emits, other than
#: the three `_PROMOTED_KEYS` above, appears in exactly one of these tuples.
#: `tests/test_storage_legacy_sections.py`'s own completeness test enumerates
#: `AbiSnapshot`'s real dataclass fields and asserts this invariant directly,
#: so this list cannot silently go stale as new fields are added — the same
#: "an omission produces no failure anywhere" defect class ADR-063's "Adding
#: a new ChangeKind" step 5 already guards against, applied here.
#:
#: Three of `storage.package.SECTION_KINDS` are unused today, by design, not
#: by omission: `"source_abi"`/`"raw_refs"` have no `AbiSnapshot` field to
#: carry yet (L3-L5 build-source evidence is a separate `BuildSourcePack`
#: object folded under `"build"` below, not a distinct raw-refs concept), and
#: `"diagnostics"` likewise has no dedicated `AbiSnapshot` field distinct from
#: the toolchain-fallback facts already folded into `"debug"`.
_SECTION_FIELDS: Mapping[str, tuple[str, ...]] = {
    "binary": (
        "elf",
        "pe",
        "macho",
        "kabi",
        "platform",
        "elf_only_mode",
        "build_id",
        "build_mode",
        "source_path",
        "source_mtime",
        "source_mtime_epoch",
        "source_size",
    ),
    "declarations": (
        "functions",
        "variables",
        "enums",
        "typedefs",
        "typedefs_qualified",
        "typedef_entity_ids",
        "constants",
        "constant_entity_ids",
        "sycl",
        "python_ext",
        "python_api",
        "numpy_capi",
    ),
    "types": ("types",),
    "layout": (
        "dwarf_layout_coherence",
        "dwarf_layout_coherence_mismatches",
        "scope_fallback",
        "conditional_fields",
        "contract",
        "dependency_scope",
    ),
    "debug": (
        "dwarf",
        "dwarf_advanced",
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
    ),
    "build": ("build_source_pack", "build_source"),
    "graph": ("surface_graph",),
    "provenance": (
        "library",
        "version",
        "language_profile",
        "dependency_info",
        "git_commit",
        "git_tag",
        "created_at",
        # Not an `AbiSnapshot` dataclass field at all -- `cli_dump_helpers
        # .fold_dump_provenance_into_dict` adds this key to the *document*
        # dict a real `dump` invocation writes, after `snapshot_to_dict()`
        # already ran, so any document a real `dump` produces carries it.
        # It records exactly the same category of fact ("what/how this was
        # produced") every other key in this section does, so it belongs
        # here rather than forcing a new, one-key section kind.
        "dump_provenance",
    ),
}

#: Every legacy section kind this module partitions into, in the fixed order
#: `_SECTION_FIELDS` declares them — the vocabulary a writer/reader of a
#: full-split `ProjectSnapshot` package iterates over.
LEGACY_SECTION_KINDS: tuple[str, ...] = tuple(_SECTION_FIELDS)

#: Reverse index: field name -> owning section kind, built once at import
#: time. A field appearing in two `_SECTION_FIELDS` entries would silently
#: let the *second* one win here — refused instead, at import time, by the
#: assertion immediately below, so that mistake fails every test collection
#: rather than surfacing only once some caller happens to hit the
#: double-assigned key.
_FIELD_TO_SECTION: dict[str, str] = {}
for _section_kind, _fields in _SECTION_FIELDS.items():
    for _field in _fields:
        if _field in _FIELD_TO_SECTION:
            raise AssertionError(
                f"legacy field {_field!r} is assigned to both "
                f"{_FIELD_TO_SECTION[_field]!r} and {_section_kind!r} in "
                "storage.legacy_sections._SECTION_FIELDS"
            )
        _FIELD_TO_SECTION[_field] = _section_kind
del _section_kind, _fields, _field


def split_legacy_document(
    legacy_document: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Partition *legacy_document* (a `snapshot_to_dict()`-shaped mapping,
    minus `storage.import_v1._PROMOTED_KEYS`) into one plain `dict` per D8
    legacy section kind, keyed by `LEGACY_SECTION_KINDS`.

    A section with no keys present in *legacy_document* is omitted from the
    result entirely — the caller (`storage.import_v1.import_legacy_snapshot`)
    only ever writes an `ObjectRef` for a section that actually has content,
    matching `ArtifactRef.sections`' own "a header-only target has no
    `'binary'` section" convention (`storage/package.py`).

    Raises `ValueError` for a key that is neither one of `_PROMOTED_KEYS` nor
    assigned to any section in `_SECTION_FIELDS` — a document this module's
    allowlist has not been updated for, rather than a silent drop.
    """
    _mapping(legacy_document, "legacy_document")
    sections: dict[str, dict[str, Any]] = {}
    unknown: list[str] = []
    for key, value in legacy_document.items():
        if key in _PROMOTED_KEYS:
            continue
        section_kind = _FIELD_TO_SECTION.get(key)
        if section_kind is None:
            unknown.append(key)
            continue
        sections.setdefault(section_kind, {})[key] = value
    if unknown:
        raise ValueError(
            "legacy_document has field(s) with no assigned D8 section in "
            f"storage.legacy_sections._SECTION_FIELDS: {sorted(unknown)} -- "
            "add each to exactly one section's allowlist before this "
            "document can be split"
        )
    return sections


def join_legacy_document(
    sections: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """The inverse of `split_legacy_document`: every field from every
    section in *sections*, merged back into one flat `dict` — the
    `_PROMOTED_KEYS` fields (`semantic_ir`/`semantic_ir_conflicts`/
    `schema_version`) are the caller's own responsibility to add back
    (`storage.import_v1.export_legacy_snapshot` does so), since this
    function only ever reverses what `split_legacy_document` produced.

    Raises `ValueError` if *sections* names a section kind this module does
    not recognize, or a section payload carries a key outside that section's
    own declared `_SECTION_FIELDS` allowlist -- either means the payload was
    not actually produced by `split_legacy_document` (or was produced by an
    older or newer, incompatibly-partitioned version of it), and merging it
    as-is would silently misfile that key's value under the wrong section's
    trust boundary rather than surfacing the mismatch.
    """
    merged: dict[str, Any] = {}
    for section_kind, payload in sections.items():
        _mapping(payload, f"legacy section {section_kind!r}")
        allowed = _SECTION_FIELDS.get(section_kind)
        if allowed is None:
            raise ValueError(
                f"unknown legacy section kind {section_kind!r} -- expected "
                f"one of {LEGACY_SECTION_KINDS}"
            )
        allowed_set = set(allowed)
        misfiled = [key for key in payload if key not in allowed_set]
        if misfiled:
            raise ValueError(
                f"legacy section {section_kind!r} carries field(s) not in "
                f"its own allowlist: {sorted(misfiled)}"
            )
        overlap = set(payload) & set(merged)
        if overlap:
            raise ValueError(
                f"field(s) {sorted(overlap)} appear in more than one legacy "
                "section payload"
            )
        merged.update(payload)
    return merged
