# Copyright 2026 Nikolay Petrov
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

"""Decodes the four declaration collections (functions/variables/types/
enums) plus typedefs and their entity-id sidecars -- the largest single
contiguous slice of :mod:`abicheck.storage.snapshot_codec`'s own
``decode_snapshot``, split out purely to keep that module under the
ADR-061 new-file production line ceiling (mechanical extraction, not a
redesign).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..model import (
    AccessLevel,
    ElfVisibility,
    EnumMember,
    EnumType,
    Function,
    Param,
    ParamKind,
    RecordType,
    ScopeOrigin,
    SymbolBinding,
    TypeField,
    Variable,
    Visibility,
)
from .entity_id_codec import decode_entity_ids, decode_sidecar_entity_ids
from .fact_codec import (
    decode_enum_facts,
    decode_field_facts,
    decode_function_facts,
    decode_param_facts,
    decode_record_facts,
    decode_variable_facts,
)


def _scope_origin_or_unknown(raw: Any) -> ScopeOrigin:
    """Deserialize a ScopeOrigin, defaulting unknown/invalid values to UNKNOWN.

    A hand-edited or newer-schema snapshot may carry an origin string this
    build does not recognize; that must not abort the whole load."""
    try:
        return ScopeOrigin(raw if raw is not None else "unknown")
    except ValueError:
        return ScopeOrigin.UNKNOWN


def _enum_type_from_dict(e: dict[str, Any], schema_version: int) -> EnumType:
    return EnumType(
        name=e["name"],
        members=[
            EnumMember(name=m["name"], value=m["value"]) for m in e.get("members", [])
        ],
        underlying_type=e.get("underlying_type", "int"),
        source_location=e.get("source_location"),
        source_header=e.get("source_header"),
        origin=_scope_origin_or_unknown(e.get("origin")),
        is_scoped=e.get("is_scoped"),
        deprecated=e.get("deprecated"),
        qualified_name=e.get("qualified_name"),
        **decode_enum_facts(e, schema_version),
    )


@dataclass
class DecodedDeclarations:
    """The seven values :func:`decode_declarations` produces, bundled so the
    call site in ``snapshot_codec.decode_snapshot`` reads as one assignment
    instead of a seven-tuple unpack."""

    functions: list[Function]
    variables: list[Variable]
    types: list[RecordType]
    enums: list[EnumType]
    typedefs: dict[str, str]
    typedefs_qualified: dict[str, str]
    sidecar_entity_ids: dict[str, Any]


def decode_declarations(d: dict[str, Any], schema_version: int) -> DecodedDeclarations:
    """Decode ``d``'s ``functions``/``variables``/``types``/``enums``
    collections plus ``typedefs``/``typedefs_qualified`` and the entity-id
    sidecars ADR-063 Phase 2 added for the latter two -- everything
    :func:`~abicheck.storage.snapshot_codec.decode_snapshot` needs before it
    can even look at platform blocks, provenance, or reliability flags.
    """
    funcs = [
        Function(
            name=f["name"],
            mangled=f["mangled"],
            return_type=f["return_type"],
            params=[
                Param(
                    name=p.get("name", ""),
                    type=p.get("type", ""),
                    kind=ParamKind(p.get("kind", "value")),
                    default=p.get("default", None),
                    pointer_depth=p.get("pointer_depth", 0),
                    is_restrict=p.get("is_restrict", False),
                    is_va_list=p.get("is_va_list", False),
                    **decode_param_facts(p, schema_version),
                )
                for p in f.get("params", [])
            ],
            visibility=Visibility(f.get("visibility", "public")),
            is_virtual=f.get("is_virtual", False),
            is_noexcept=f.get("is_noexcept", False),
            vtable_index=f.get("vtable_index"),
            source_location=f.get("source_location"),
            is_static=f.get("is_static", False),
            is_const=f.get("is_const", False),
            is_volatile=f.get("is_volatile", False),
            is_pure_virtual=f.get("is_pure_virtual", False),
            is_deleted=f.get("is_deleted", False),
            # Provenance of is_deleted: True when set via DW_AT_deleted. Must be
            # rehydrated (asdict writes it) so the public-map bypass in
            # diff_symbols keeps DWARF-deleted unexported members out of the
            # public surface after a dump-to-file → compare-files round-trip,
            # rather than re-emitting FUNC_REMOVED against a stripped build.
            deleted_from_dwarf=f.get("deleted_from_dwarf", False),
            is_inline=f.get("is_inline", False),
            is_extern_c=f.get("is_extern_c", False),
            access=AccessLevel(f.get("access", "public")),
            return_pointer_depth=f.get("return_pointer_depth", 0),
            elf_visibility=ElfVisibility(f["elf_visibility"])
            if f.get("elf_visibility")
            else None,
            # Missing on an older snapshot (predates this field) → None,
            # same "not captured" default every other ELF-derived fact here
            # uses.
            elf_binding=SymbolBinding(f["elf_binding"])
            if f.get("elf_binding")
            else None,
            ref_qualifier=f.get("ref_qualifier", ""),
            # Tri-state: a missing key (older snapshot) loads as None,
            # which suppresses CTOR_EXPLICIT_ADDED/_REMOVED in the diff
            # rather than producing spurious findings from schema evolution.
            is_explicit=f.get("is_explicit"),
            # Tri-state, same rationale as is_explicit — a missing key on
            # an older snapshot loads as None and suppresses the
            # HIDDEN_FRIEND_ADDED/_REMOVED transition detector.
            is_hidden_friend=f.get("is_hidden_friend"),
            # Owner class of a hidden friend — missing on older snapshots (and
            # for non-friends) loads as None.
            hidden_friend_owner=f.get("hidden_friend_owner"),
            # Provenance (v6) — missing on older snapshots → None / UNKNOWN.
            source_header=f.get("source_header"),
            origin=_scope_origin_or_unknown(f.get("origin")),
            # Tri-state language-contract fields (coverage extension) —
            # missing keys on older snapshots load as None and suppress the
            # corresponding transition detectors.
            is_variadic=f.get("is_variadic"),
            contract_attributes=f.get("contract_attributes"),
            exception_spec=f.get("exception_spec"),
            deprecated=f.get("deprecated"),
            is_override=f.get("is_override"),
            # Tri-state (v27) — missing on a pre-v27 snapshot loads as None.
            is_compiler_generated=f.get("is_compiler_generated"),
            **decode_function_facts(f, schema_version),
        )
        for f in d.get("functions", [])
    ]
    variables = [
        Variable(
            name=v["name"],
            mangled=v["mangled"],
            type=v["type"],
            visibility=Visibility(v.get("visibility", "public")),
            source_location=v.get("source_location"),
            is_const=v.get("is_const", False),
            value=v.get("value"),
            access=AccessLevel(v.get("access", "public")),
            elf_visibility=ElfVisibility(v["elf_visibility"])
            if v.get("elf_visibility")
            else None,
            source_header=v.get("source_header"),
            origin=_scope_origin_or_unknown(v.get("origin")),
            alignment_bits=v.get("alignment_bits"),
            deprecated=v.get("deprecated"),
            elf_binding=SymbolBinding(v["elf_binding"])
            if v.get("elf_binding")
            else None,
            # Missing on a pre-v43 snapshot (predates this field) → False,
            # matching every prior reader's implicit assumption (the field
            # did not exist, so nothing distinguished a static variable
            # anyway).
            is_static=v.get("is_static", False),
            **decode_variable_facts(v, schema_version),
        )
        for v in d.get("variables", [])
    ]
    types = [
        RecordType(
            name=t["name"],
            kind=t["kind"],
            size_bits=t.get("size_bits"),
            alignment_bits=t.get("alignment_bits"),
            fields=[
                TypeField(
                    name=f["name"],
                    type=f["type"],
                    offset_bits=f.get("offset_bits"),
                    is_bitfield=f.get("is_bitfield", False),
                    bitfield_bits=f.get("bitfield_bits"),
                    is_const=f.get("is_const", False),
                    is_volatile=f.get("is_volatile", False),
                    is_mutable=f.get("is_mutable", False),
                    access=AccessLevel(f.get("access", "public")),
                    default=f.get("default"),
                    deprecated=f.get("deprecated"),
                    **decode_field_facts(f, schema_version),
                )
                for f in t.get("fields", [])
            ],
            bases=t.get("bases", []),
            virtual_bases=t.get("virtual_bases", []),
            vtable=t.get("vtable", []),
            source_location=t.get("source_location"),
            is_union=t.get("is_union", t.get("kind") == "union"),
            is_opaque=t.get("is_opaque", False),
            is_final=t.get("is_final"),  # tri-state; absent on pre-v? snapshots → None
            is_template_pattern=t.get("is_template_pattern", False),
            has_anonymous_aggregate_fields=t.get(
                "has_anonymous_aggregate_fields", False
            ),
            source_header=t.get("source_header"),
            origin=_scope_origin_or_unknown(t.get("origin")),
            # Fine-grained layout descriptor (layout-closure work); all
            # optional/tri-state, absent on snapshots predating these fields.
            data_size_bits=t.get("data_size_bits"),
            is_standard_layout=t.get("is_standard_layout"),
            is_trivially_copyable=t.get("is_trivially_copyable"),
            vptr_offset_bits=t.get("vptr_offset_bits"),
            base_offsets=t.get("base_offsets", {}),
            qualified_name=t.get("qualified_name"),
            is_abstract=t.get("is_abstract"),
            deprecated=t.get("deprecated"),
            **decode_record_facts(t, schema_version),
        )
        for t in d.get("types", [])
    ]
    enums = [_enum_type_from_dict(e, schema_version) for e in d.get("enums", [])]
    decode_entity_ids(d, functions=funcs, variables=variables, types=types, enums=enums)
    sidecar_entity_ids = decode_sidecar_entity_ids(d)
    typedefs: dict[str, str] = d.get("typedefs", {})
    typedefs_qualified: dict[str, str] = d.get("typedefs_qualified", {})

    return DecodedDeclarations(
        functions=funcs,
        variables=variables,
        types=types,
        enums=enums,
        typedefs=typedefs,
        typedefs_qualified=typedefs_qualified,
        sidecar_entity_ids=sidecar_entity_ids,
    )
