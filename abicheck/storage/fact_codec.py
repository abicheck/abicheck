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

"""``Fact[T]`` encode/decode/legacy-backfill helpers for ``serialization.py``.

Split out into a ``storage``-owned leaf module rather than inlined in
``serialization.py`` (ADR-063 Phase 0, schema v26): that module is already
at this repo's 2000-line AI-readiness hard cap, and this encode/decode
concern only depends on ``model`` — exactly what ADR-061's ``storage``
layer owns — the same way `snapshot_io.py` carries the storage-envelope
concern out of `serialization.py`'s neighbours.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..model import AccessLevel, Fact, FactStatus, SymbolBinding

# ADR-061's 800-line production ceiling: the case-(a) legacy-load
# corrections live in ``fact_backfill.py`` and are re-exported here, so
# ``serialization.py`` and every test importing them from this module are
# unaffected by that split. One-directional: ``fact_backfill`` imports the
# per-field schema-version thresholds from the ``fact_schema_versions.py``
# leaf, never from here, so this re-export forms no cycle.
from .fact_backfill import (
    CaseAFactRule as CaseAFactRule,
    apply_case_a_fact_backfill as apply_case_a_fact_backfill,
    apply_legacy_fact_backfill as apply_legacy_fact_backfill,
    evidenced_producers as evidenced_producers,
)
from .fact_schema_versions import (
    _FACT_FIELDS_SCHEMA_VERSION,
    _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
    _MIN_SCHEMA_VERSION_FOR_ENUMTYPE_FACTS,
    _MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
    _MIN_SCHEMA_VERSION_FOR_IS_FINAL_FACT,
    _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS,
    _MIN_SCHEMA_VERSION_FOR_RECORDTYPE_CASE_B_FACTS,
    _MIN_SCHEMA_VERSION_FOR_SNAPSHOT_CASE_B_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
    _MIN_SCHEMA_VERSION_FOR_VARIABLE_CASE_B_FACTS,
)
from .guards import provenance_text

if TYPE_CHECKING:
    pass

__all__ = [
    "CaseAFactRule",
    "apply_case_a_fact_backfill",
    "apply_legacy_fact_backfill",
    "evidenced_producers",
    "decode_enum_facts",
    "decode_fact",
    "decode_fact_with_legacy_presence",
    "decode_field_facts",
    "decode_param_facts",
    "decode_function_facts",
    "decode_record_facts",
    "decode_snapshot_facts",
    "decode_variable_facts",
    "encode_fact_fields",
]

_TYPE_FACT_KEYS = (
    "bases_fact",
    "virtual_bases_fact",
    "vtable_fact",
    "vptr_offset_bits_fact",
    "is_final_fact",
    "is_abstract_fact",
    "data_size_bits_fact",
    "is_standard_layout_fact",
    "is_trivially_copyable_fact",
    "qualified_name_fact",
    "source_header_fact",
    "deprecated_fact",
)

# ADR-063 Phase 5 (eighth batch): TypeField's own case-(a) *_fact siblings.
# Nested one level deeper than every tuple below -- a TypeField dict lives
# under a type dict's own "fields" list, not directly under a top-level
# collection -- so `encode_fact_fields` loops it inside the "types" loop.
_FIELD_FACT_KEYS = (
    "is_const_fact",
    "is_volatile_fact",
    "is_mutable_fact",
    "default_fact",
    "deprecated_fact",
)

# ADR-063 Phase 5 (third batch): EnumType's own qualified_name_fact/
# source_header_fact -- same shape as _TYPE_FACT_KEYS, a distinct tuple
# since EnumType is a different owner/collection ("enums", not "types").
_ENUM_FACT_KEYS = (
    "qualified_name_fact",
    "source_header_fact",
    "deprecated_fact",
    "is_scoped_fact",
)

# ADR-063 Phase 5 (fourth batch): Variable's own case-(b) *_fact siblings --
# a distinct tuple since Variable is a different owner/collection
# ("variables", not "types"/"enums").
_VARIABLE_FACT_KEYS = (
    "source_header_fact",
    "alignment_bits_fact",
    "elf_binding_fact",
    "deprecated_fact",
    "access_fact",
)

# ADR-063 Phase 5 (fifth batch): Function's own ten case-(b) *_fact
# siblings -- a distinct tuple since Function is a different owner/
# collection ("functions", not "variables"/"types"/"enums").
_FUNCTION_FACT_KEYS = (
    "contract_attributes_fact",
    "is_explicit_fact",
    "is_hidden_friend_fact",
    "source_header_fact",
    "is_variadic_fact",
    "exception_spec_fact",
    "is_override_fact",
    "hidden_friend_owner_fact",
    "elf_binding_fact",
    "is_compiler_generated_fact",
    "deprecated_fact",
)

# ADR-063 Phase 5 (seventh batch): the three binary-format metadata blocks'
# own case-(b) *_fact siblings. Each is a single nested sub-dict under the
# top-level "elf"/"pe"/"macho" key (ElfMetadata/PeMetadata/MachoMetadata are
# not list-of-declaration collections like the four dataclasses above), so
# `encode_fact_fields` handles them with one `_encode_one` call per key
# rather than a per-item loop.
_ELF_FACT_KEYS = (
    "dynamic_flags_fact",
    "has_init_fact",
    "has_fini_fact",
)
# ADR-063 Phase 5 (tenth batch): Param's own *_fact siblings -- nested one
# level below "functions", the same shape _FIELD_FACT_KEYS has under
# "types". Phase 0's is_va_list_fact was encoded by a single hardcoded
# .get() line until this batch gave Param a second one.
_PARAM_FACT_KEYS = (
    "is_va_list_fact",
    "is_restrict_fact",
)

_PE_FACT_KEYS = ("delay_imports_fact",)
_MACHO_FACT_KEYS = ("rpaths_fact",)


def encode_fact_fields(d: dict[str, Any]) -> None:
    """In-place: encode every ``Fact[...]``-typed field's ``status`` as a string.

    `dataclasses.asdict()` already recursed into each `Fact[...]` field by
    the time this runs, producing a plain dict with `status` still holding
    the raw `FactStatus` enum member; `json.dump()` rejects that directly.
    Mirrors the pre-existing `ElfMetadata`-enum-to-string pattern in
    `serialization.snapshot_to_dict()`.
    """
    for type_dict in d.get("types", []):
        for fact_key in _TYPE_FACT_KEYS:
            _encode_one(type_dict.get(fact_key))
        for field_dict in type_dict.get("fields", []):
            for fact_key in _FIELD_FACT_KEYS:
                _encode_one(field_dict.get(fact_key))
    for enum_dict in d.get("enums", []):
        for fact_key in _ENUM_FACT_KEYS:
            _encode_one(enum_dict.get(fact_key))
    for var_dict in d.get("variables", []):
        for fact_key in _VARIABLE_FACT_KEYS:
            _encode_one(var_dict.get(fact_key))
    for func_dict in d.get("functions", []):
        for fact_key in _FUNCTION_FACT_KEYS:
            _encode_one(func_dict.get(fact_key))
        for param_dict in func_dict.get("params", []):
            for fact_key in _PARAM_FACT_KEYS:
                _encode_one(param_dict.get(fact_key))
    # AbiSnapshot's own case-(b) field -- a single top-level key, not
    # nested in a list like the four declaration dataclasses above.
    _encode_one(d.get("ast_resolved_standard_fact"))
    elf_dict = d.get("elf")
    if elf_dict is not None:
        for fact_key in _ELF_FACT_KEYS:
            _encode_one(elf_dict.get(fact_key))
    pe_dict = d.get("pe")
    if pe_dict is not None:
        for fact_key in _PE_FACT_KEYS:
            _encode_one(pe_dict.get(fact_key))
    macho_dict = d.get("macho")
    if macho_dict is not None:
        for fact_key in _MACHO_FACT_KEYS:
            _encode_one(macho_dict.get(fact_key))


def _encode_one(fact_dict: dict[str, Any] | None) -> None:
    if fact_dict is None:
        return
    status = fact_dict.get("status")
    if isinstance(status, FactStatus):
        fact_dict["status"] = status.value
    # T9 (duplication-and-convergence-assessment Phase 6 item 4): `dataclasses.
    # asdict()` already put a `"producer": None` key into every fact dict here
    # (the overwhelming majority, since only PDB's vtable_fact/
    # vptr_offset_bits_fact sets one today) -- dropped rather than left as an
    # explicit null, matching this codec's own established sparse-field
    # convention (a document predating this field simply lacks the key, and
    # `decode_fact` already reads a missing key as `None`) and keeping every
    # pre-existing persisted document/fixture byte-for-byte unchanged unless
    # it actually carries a producer. Symmetric with `storage/
    # semantic_ir_codec.py`'s own `_fact_to_dict`, which applies the identical
    # omit-when-unset rule for the same reason.
    if fact_dict.get("producer") is None:
        fact_dict.pop("producer", None)


def decode_fact(
    raw: Any, schema_version: int, min_schema_version: int = _FACT_FIELDS_SCHEMA_VERSION
) -> Fact[Any] | None:
    """Reconstruct a ``Fact[T]`` from its serialized dict form, or ``None``.

    A missing key means one of two different things depending on whether
    ``schema_version`` reaches ``min_schema_version`` — the schema_version
    *this field's own* ``*_fact`` sibling started being unconditionally
    persisted at (``_FACT_FIELDS_SCHEMA_VERSION`` for the five siblings
    Phase 0 introduced; a later, field-specific threshold for anything a
    later phase adds, since that field's sibling genuinely didn't exist in
    the document format before its own conversion, independent of when the
    *first* ``Fact[T]`` sibling shipped) — and conflating them would misread
    absent evidence as confirmed. Below ``min_schema_version``, a missing key
    means "this snapshot predates this field's Fact[T] conversion" —
    returning ``None`` here lets the owning dataclass's own
    ``__post_init__`` bridge (or, for the original four fields,
    :func:`apply_legacy_fact_backfill`'s reliability-aware correction) derive
    the right ``Fact[T]`` from the legacy value instead. At or above it, the
    document already commits to serializing this sibling whenever the owning
    dataclass emits one, so a missing key means a malformed/truncated/
    hand-authored document — returning ``None`` here would let the owning
    dataclass's ``__post_init__`` bridge read the caller-supplied legacy
    default (e.g. ``bases=[]`` from ``t.get("bases", [])``) as a confirmed,
    freshly-supplied value rather than missing evidence (Codex review), so
    this returns :meth:`Fact.not_collected` explicitly instead.
    """
    if not raw:
        return Fact.not_collected() if schema_version >= min_schema_version else None
    return Fact(
        status=FactStatus(raw["status"]),
        value=raw.get("value"),
        diagnostics=tuple(raw.get("diagnostics") or ()),
        # T9 (duplication-and-convergence-assessment Phase 6 item 4):
        # additive, unversioned field -- a document predating it simply has
        # no "producer" key, and `raw.get(...)` already reads that as
        # `None`, the identical default every pre-existing `Fact[...]`
        # construction site already carries. No schema-version gate needed
        # (unlike `value`/`diagnostics`' own siblings above, whose *absence*
        # can mean something at an older schema version): `producer` was
        # never required for correctness, only ever additional attribution.
        # Codex review: rejected rather than coerced if not a string, the
        # same discipline every other provenance-shaped field in this
        # package already applies (`fact_availability.py`'s own
        # `producer`/`recipe`/`scope`, `semantic_ir_codec.py`'s entity
        # `producer`) -- `str(7)` and `str("7")` would otherwise
        # deserialize identically, letting a malformed/hand-edited
        # document's producer claim look like real attribution. `None`
        # (absent key, or an explicit JSON `null`) is the field's own,
        # legitimate "no attribution recorded" value and is passed through
        # rather than into `provenance_text`, which rejects non-str
        # unconditionally and would raise on this legitimate case.
        producer=(
            None
            if raw.get("producer") is None
            else provenance_text(raw["producer"], "fact producer")
        ),
    )


def decode_record_facts(t: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """Decode every ``RecordType`` ``Fact[...]`` sibling from one type dict.

    One call, spread into the ``RecordType(**decode_record_facts(t), ...)``
    constructor call, in place of one individual keyword argument per field.
    """
    return {
        "bases_fact": decode_fact(t.get("bases_fact"), schema_version),
        "virtual_bases_fact": decode_fact(t.get("virtual_bases_fact"), schema_version),
        "vtable_fact": decode_fact(t.get("vtable_fact"), schema_version),
        "vptr_offset_bits_fact": decode_fact(
            t.get("vptr_offset_bits_fact"), schema_version
        ),
        "is_final_fact": decode_fact(
            t.get("is_final_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_IS_FINAL_FACT,
        ),
        "is_abstract_fact": decode_fact(
            t.get("is_abstract_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_RECORDTYPE_CASE_B_FACTS,
        ),
        "data_size_bits_fact": decode_fact(
            t.get("data_size_bits_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_RECORDTYPE_CASE_B_FACTS,
        ),
        "is_standard_layout_fact": decode_fact(
            t.get("is_standard_layout_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_RECORDTYPE_CASE_B_FACTS,
        ),
        "is_trivially_copyable_fact": decode_fact(
            t.get("is_trivially_copyable_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_RECORDTYPE_CASE_B_FACTS,
        ),
        "qualified_name_fact": decode_fact(
            t.get("qualified_name_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_RECORDTYPE_CASE_B_FACTS,
        ),
        "source_header_fact": decode_fact(
            t.get("source_header_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_RECORDTYPE_CASE_B_FACTS,
        ),
        "deprecated_fact": decode_fact_with_legacy_presence(
            t, "deprecated", schema_version, _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS
        ),
    }


def decode_enum_facts(e: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """Decode every ``EnumType`` ``Fact[...]`` sibling from one enum dict.

    One call, spread into the ``EnumType(**decode_enum_facts(e), ...)``
    constructor call, mirroring :func:`decode_record_facts`.
    """
    return {
        "qualified_name_fact": decode_fact(
            e.get("qualified_name_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_ENUMTYPE_FACTS,
        ),
        "source_header_fact": decode_fact(
            e.get("source_header_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_ENUMTYPE_FACTS,
        ),
        "deprecated_fact": decode_fact_with_legacy_presence(
            e, "deprecated", schema_version, _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS
        ),
        "is_scoped_fact": decode_fact_with_legacy_presence(
            e, "is_scoped", schema_version, _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS
        ),
    }


def decode_variable_facts(v: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """Decode every ``Variable`` ``Fact[...]`` sibling from one variable dict.

    One call, spread into the ``Variable(**decode_variable_facts(v), ...)``
    constructor call, mirroring :func:`decode_record_facts`/
    :func:`decode_enum_facts`. ``elf_binding_fact``'s ``value`` needs one
    extra step the other two fields don't: JSON has no enum type, so the
    raw decoded value is a plain string, and the legacy (non-Fact)
    ``Variable.elf_binding``/``Function.elf_binding`` reader convention
    (``diff_symbols.py``, ``diff_platform.py``) unconditionally accesses
    ``.value`` on it, which only a real ``SymbolBinding`` member supports —
    a bare ``str`` would raise ``AttributeError``. ``bridge_legacy_and_fact``
    then carries this same converted value back into the legacy
    ``elf_binding`` field too, so the two representations stay a real
    ``SymbolBinding`` instance together, not a str/enum split.
    """
    elf_binding_fact = decode_fact(
        v.get("elf_binding_fact"),
        schema_version,
        min_schema_version=_MIN_SCHEMA_VERSION_FOR_VARIABLE_CASE_B_FACTS,
    )
    if elf_binding_fact is not None and elf_binding_fact.value is not None:
        elf_binding_fact = replace(
            elf_binding_fact, value=SymbolBinding(elf_binding_fact.value)
        )
    access_fact = decode_fact_with_legacy_presence(
        v, "access", schema_version, _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS
    )
    if access_fact is not None and access_fact.value is not None:
        # Same non-JSON-native value-type reconstruction elf_binding_fact
        # needs just above: AccessLevel is a str-Enum, so a decoded value is
        # a bare str until it is rebuilt, and `bridge_legacy_and_fact` then
        # carries this same object back into the legacy `access` field --
        # where every reader expects a real AccessLevel member.
        access_fact = replace(access_fact, value=AccessLevel(access_fact.value))
    return {
        "source_header_fact": decode_fact(
            v.get("source_header_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_VARIABLE_CASE_B_FACTS,
        ),
        "alignment_bits_fact": decode_fact(
            v.get("alignment_bits_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_VARIABLE_CASE_B_FACTS,
        ),
        "elf_binding_fact": elf_binding_fact,
        "deprecated_fact": decode_fact_with_legacy_presence(
            v, "deprecated", schema_version, _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS
        ),
        "access_fact": access_fact,
    }


def decode_function_facts(f: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """Decode every ``Function`` ``Fact[...]`` sibling from one function dict.

    One call, spread into the ``Function(**decode_function_facts(f), ...)``
    constructor call, mirroring :func:`decode_record_facts`/
    :func:`decode_variable_facts`. ``elf_binding_fact`` needs the same
    ``SymbolBinding`` reconstruction :func:`decode_variable_facts` performs,
    for the identical reason (``Function.elf_binding`` shares the same
    ``.value``-accessing reader convention as ``Variable.elf_binding``).
    """
    elf_binding_fact = decode_fact(
        f.get("elf_binding_fact"),
        schema_version,
        min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
    )
    if elf_binding_fact is not None and elf_binding_fact.value is not None:
        elf_binding_fact = replace(
            elf_binding_fact, value=SymbolBinding(elf_binding_fact.value)
        )
    return {
        "contract_attributes_fact": decode_fact(
            f.get("contract_attributes_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
        ),
        "is_explicit_fact": decode_fact(
            f.get("is_explicit_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
        ),
        "is_hidden_friend_fact": decode_fact(
            f.get("is_hidden_friend_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
        ),
        "source_header_fact": decode_fact(
            f.get("source_header_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
        ),
        "is_variadic_fact": decode_fact(
            f.get("is_variadic_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
        ),
        "exception_spec_fact": decode_fact(
            f.get("exception_spec_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
        ),
        "is_override_fact": decode_fact(
            f.get("is_override_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
        ),
        "hidden_friend_owner_fact": decode_fact(
            f.get("hidden_friend_owner_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
        ),
        "elf_binding_fact": elf_binding_fact,
        "is_compiler_generated_fact": decode_fact(
            f.get("is_compiler_generated_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
        ),
        "deprecated_fact": decode_fact_with_legacy_presence(
            f, "deprecated", schema_version, _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS
        ),
    }


def decode_snapshot_facts(d: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """Decode ``AbiSnapshot``'s own ``Fact[...]`` sibling from the top-level
    snapshot dict. One call, spread into the ``AbiSnapshot(**decode_
    snapshot_facts(d, schema_version), ...)`` constructor call, mirroring
    :func:`decode_record_facts` and siblings -- the receiver here is the
    whole snapshot dict itself, not one item nested in a list, since
    ``AbiSnapshot`` is the single top-level object.
    """
    return {
        "ast_resolved_standard_fact": decode_fact(
            d.get("ast_resolved_standard_fact"),
            schema_version,
            min_schema_version=_MIN_SCHEMA_VERSION_FOR_SNAPSHOT_CASE_B_FACTS,
        ),
    }


def decode_fact_with_legacy_presence(
    owner_dict: dict[str, Any],
    legacy_key: str,
    schema_version: int,
    min_schema_version: int,
) -> Fact[Any] | None:
    """:func:`decode_fact` for a case-(a) field, honouring the *legacy* key too.

    A case-(a) field's own resting value (``False``, ``AccessLevel.PUBLIC``)
    is a legitimate value, so a document that omits the legacy key entirely
    is genuinely saying "no evidence", not "confirmed false" — but the
    caller's own ``f.get("is_const", False)`` default hands the owning
    dataclass a real ``False`` regardless, which its ``__post_init__``
    bridge then reads as an explicitly-supplied value and backfills to
    ``Fact.present(False)``. Returning :meth:`Fact.not_collected` here for
    that case is what keeps "the document never carried this fact" from
    being laundered into a confirmed one. Every other case defers to
    :func:`decode_fact` unchanged.
    """
    raw = owner_dict.get(f"{legacy_key}_fact")
    if not raw and schema_version < min_schema_version and legacy_key not in owner_dict:
        return Fact.not_collected()
    return decode_fact(raw, schema_version, min_schema_version=min_schema_version)


def decode_param_facts(p: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """Decode every ``Param`` ``Fact[...]`` sibling from one param dict.

    One call, spread into the ``Param(**decode_param_facts(p), ...)``
    constructor call, mirroring :func:`decode_record_facts` and siblings.
    ``is_va_list_fact`` (ADR-063 Phase 0) was decoded by a single inline
    ``decode_fact`` call in ``serialization.py`` until Phase 5's tenth
    batch gave ``Param`` a second sibling; both live here now, so this
    owner's decode wiring is one place rather than two.
    """
    return {
        "is_va_list_fact": decode_fact(p.get("is_va_list_fact"), schema_version),
        "is_restrict_fact": decode_fact_with_legacy_presence(
            p, "is_restrict", schema_version, _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS
        ),
    }


def decode_field_facts(fld: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """Decode every ``TypeField`` ``Fact[...]`` sibling from one field dict.

    One call, spread into the ``TypeField(**decode_field_facts(fld), ...)``
    constructor call, mirroring :func:`decode_record_facts` and siblings --
    the ``fld`` parameter name, rather than the ``f`` this module's
    neighbours use for a raw dict, is deliberate: ``scripts/fact_registry_
    completeness.py`` resolves a decode call's owner from its receiver
    *name*, and ``f`` already means ``Function`` there (the same collision
    the binary-format batch's own ``elf``/``pe``/``macho`` renames avoided).
    Unlike those, every field here is case-(a) — availability is carried by
    ``AbiSnapshot.header_cv_facts_reliable``, not by the value — so each
    routes through :func:`decode_fact_with_legacy_presence`, and a legacy
    document whose flag says its blanket ``False``s are untrustworthy is
    corrected afterwards by :func:`apply_case_a_fact_backfill`.
    """
    return {
        "is_const_fact": decode_fact_with_legacy_presence(
            fld,
            "is_const",
            schema_version,
            _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
        ),
        "is_volatile_fact": decode_fact_with_legacy_presence(
            fld,
            "is_volatile",
            schema_version,
            _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
        ),
        "is_mutable_fact": decode_fact_with_legacy_presence(
            fld,
            "is_mutable",
            schema_version,
            _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
        ),
        "default_fact": decode_fact_with_legacy_presence(
            fld,
            "default",
            schema_version,
            _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
        ),
        "deprecated_fact": decode_fact_with_legacy_presence(
            fld,
            "deprecated",
            schema_version,
            _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
        ),
    }
