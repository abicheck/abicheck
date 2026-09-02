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

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ..model import Fact, FactStatus, SymbolBinding

if TYPE_CHECKING:
    from ..model import Function, RecordType

__all__ = [
    "CaseAFactRule",
    "apply_case_a_fact_backfill",
    "apply_legacy_fact_backfill",
    "decode_enum_facts",
    "decode_fact",
    "decode_fact_with_legacy_presence",
    "decode_field_facts",
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
)

# ADR-063 Phase 5 (fourth batch): Variable's own case-(b) *_fact siblings --
# a distinct tuple since Variable is a different owner/collection
# ("variables", not "types"/"enums").
_VARIABLE_FACT_KEYS = (
    "source_header_fact",
    "alignment_bits_fact",
    "elf_binding_fact",
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
            _encode_one(param_dict.get("is_va_list_fact"))
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


# The schema_version this phase bumped SCHEMA_VERSION to when it started
# persisting a *_fact sibling for every legacy field it emits (serialization.py).
_FACT_FIELDS_SCHEMA_VERSION = 26

# ADR-063 Phase 5: the schema_version RecordType.is_final_fact started being
# persisted at — independent of _FACT_FIELDS_SCHEMA_VERSION above, since a
# document between the two thresholds (v26..v29) genuinely never carried
# this key at all, the same way a pre-v26 document never carried any *_fact
# key. See decode_fact's own docstring for why this threshold matters.
_MIN_SCHEMA_VERSION_FOR_IS_FINAL_FACT = 30

# ADR-063 Phase 5 (second batch): the schema_version RecordType's remaining
# case-(b) *_fact siblings (is_abstract/data_size_bits/is_standard_layout/
# is_trivially_copyable/qualified_name/source_header) started being
# persisted at — one shared threshold, since all six land together in the
# same schema bump. Same reasoning as _MIN_SCHEMA_VERSION_FOR_IS_FINAL_FACT
# above: a document below this version never carried these keys at all.
_MIN_SCHEMA_VERSION_FOR_RECORDTYPE_CASE_B_FACTS = 32

# ADR-063 Phase 5 (third batch): the schema_version EnumType's own
# qualified_name_fact/source_header_fact siblings started being persisted
# at.
_MIN_SCHEMA_VERSION_FOR_ENUMTYPE_FACTS = 33

# ADR-063 Phase 5 (fourth batch): the schema_version Variable's own
# source_header_fact/alignment_bits_fact/elf_binding_fact siblings started
# being persisted at.
_MIN_SCHEMA_VERSION_FOR_VARIABLE_CASE_B_FACTS = 34

# ADR-063 Phase 5 (fifth batch): the schema_version Function's own ten
# case-(b) *_fact siblings started being persisted at.
_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS = 35

# ADR-063 Phase 5 (sixth batch): the schema_version AbiSnapshot's own
# ast_resolved_standard_fact sibling started being persisted at.
_MIN_SCHEMA_VERSION_FOR_SNAPSHOT_CASE_B_FACTS = 36


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


# ADR-063 Phase 5 (eighth batch): the schema_version TypeField's own
# case-(a) is_const_fact/is_volatile_fact/is_mutable_fact siblings started
# being persisted at.
_MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS = 38

# The same threshold for TypeField's other two case-(a) fields (`default`,
# `deprecated`) -- they land in the same schema bump, but each is guarded by
# its own reliability flag (clang_field_initializer_facts_reliable /
# clang_deprecation_facts_reliable), so they are named separately here
# rather than folded into the CV constant above.
_MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS = 38


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


@dataclass(frozen=True)
class CaseAFactRule:
    """One case-(a) field's legacy-load correction (ADR-063 Phase 5).

    ``owner``/``field`` name the legacy field; ``min_schema_version`` is the
    schema_version that field's own ``<field>_fact`` sibling started being
    persisted at; ``reliable`` is this snapshot's already-resolved answer to
    "is the flag guarding this field's availability trustworthy here"
    (``serialization.py`` computes every ``*_facts_reliable`` value, folding
    in any producer gate — see ``apply_legacy_fact_backfill``'s own
    ``ast_producer`` note); ``normalized_default`` is the value the legacy
    field is reset to when the fact is downgraded, so the pair cannot be
    left holding a placeholder beside a NOT_COLLECTED status.
    """

    owner: str
    field: str
    min_schema_version: int
    reliable: bool
    normalized_default: Any


def _owner_pairs(
    d: dict[str, Any],
    owner: str,
    decoded: dict[str, list[Any]],
) -> Iterator[tuple[dict[str, Any], Any]]:
    """Every ``(raw dict, decoded object)`` pair for one *owner* dataclass.

    The one place this module knows how a given owner's instances are
    reached from the raw snapshot document — two owners (``TypeField``,
    ``Param``) live one level below a collection rather than in one, and a
    per-field ``zip`` open-coded at each call site is exactly the kind of
    duplication a later owner's conversion would get subtly wrong.
    """
    if owner == "RecordType":
        yield from zip(d.get("types", []), decoded.get("types", []), strict=False)
    elif owner == "EnumType":
        yield from zip(d.get("enums", []), decoded.get("enums", []), strict=False)
    elif owner == "Variable":
        yield from zip(
            d.get("variables", []), decoded.get("variables", []), strict=False
        )
    elif owner == "Function":
        yield from zip(
            d.get("functions", []), decoded.get("functions", []), strict=False
        )
    elif owner == "TypeField":
        for type_dict, record in zip(
            d.get("types", []), decoded.get("types", []), strict=False
        ):
            yield from zip(type_dict.get("fields", []), record.fields, strict=False)
    elif owner == "Param":
        for func_dict, func in zip(
            d.get("functions", []), decoded.get("functions", []), strict=False
        ):
            yield from zip(func_dict.get("params", []), func.params, strict=False)
    else:  # pragma: no cover - guarded by the caller's own closed rule set
        raise ValueError(f"no raw-document navigation known for owner {owner!r}")


def apply_case_a_fact_backfill(
    d: dict[str, Any],
    *,
    schema_version: int,
    rules: tuple[CaseAFactRule, ...],
    **decoded: list[Any],
) -> None:
    """Downgrade every case-(a) fact a legacy document cannot vouch for.

    A document below a field's own ``min_schema_version`` carries no
    ``<field>_fact`` key at all, so the owning dataclass's ``__post_init__``
    bridge already backfilled ``Fact.present(raw_value)`` — correct when the
    snapshot-level reliability flag guarding that field says this producer's
    values are trustworthy, and exactly the "placeholder read as a confirmed
    fact" bug ``Fact[T]`` exists to prevent when it doesn't. This is the one
    correction for that whole class: :func:`apply_legacy_fact_backfill` (the
    three fields ADR-063 Phase 0 converted) is a thin wrapper over it, and
    every case-(a) field a later batch converts adds a rule rather than
    another hand-written loop.

    Only ever *downgrades*, and only for a document that predates the
    field's own conversion: a v(N)+ document's ``<field>_fact`` was decoded
    explicitly at construction time and is authoritative.
    """
    for rule in rules:
        if schema_version >= rule.min_schema_version or rule.reliable:
            continue
        fact_key = f"{rule.field}_fact"
        for raw, obj in _owner_pairs(d, rule.owner, decoded):
            if fact_key in raw:
                continue
            setattr(obj, rule.field, rule.normalized_default)
            setattr(obj, fact_key, Fact.not_collected())


def apply_legacy_fact_backfill(
    d: dict[str, Any],
    types: list[RecordType],
    funcs: list[Function],
    schema_version: int,
    clang_vtable_facts_reliable_value: bool,
    clang_va_list_facts_reliable_value: bool,
    ast_producer_value: str | None,
    *,
    header_cv_facts_reliable_value: bool = True,
    clang_field_initializer_facts_reliable_value: bool = True,
    clang_deprecation_facts_reliable_value: bool = True,
) -> None:
    """Correct the legacy backfill for every case-(a) fact a document predates.

    A pre-v26 snapshot carries no ``vtable_fact``/``vptr_offset_bits_fact``/
    ``is_va_list_fact`` keys at all, so each ``RecordType``/``Param``'s own
    ``__post_init__`` bridge already backfilled these to
    ``Fact.present(raw_value)`` unconditionally (there is no sentinel to
    distinguish "legacy, key absent" from "legacy, key present" here — both
    look like an ordinary explicit value to that bridge). That is correct
    for ``bases``/``virtual_bases`` (no independent reliability signal —
    see AGENTS.md's ``type_base_changed`` entry), but wrong for
    ``vtable``/``vptr_offset_bits``/``is_va_list`` when the *existing*
    reliability flags say this producer's own facts for this snapshot are
    untrustworthy: ``Fact.present(raw)`` would misread a placeholder value
    as a confirmed fact, exactly the bug this phase exists to make
    unrepresentable. Only runs for a legacy (pre-v26) load — a fresh v26+
    snapshot's ``*_fact`` keys were decoded explicitly at construction time
    via :func:`decode_fact` and must not be overridden here.

    Phase 5's own case-(a) batches extend the same correction to the fields
    they convert, each with its own ``min_schema_version`` and its own
    guarding flag (``header_cv_facts_reliable_value`` for ``TypeField``'s
    CV facts, schema v38) — one rule added to the tuple below, never a
    second hand-written loop. The keyword-only spelling keeps every
    pre-existing caller (and every test constructing this call) unchanged:
    a flag left at its default ``True`` states "trustworthy", which is what
    a caller that never heard of that field was already asserting by not
    correcting it at all.

    ``is_va_list`` needs an extra gate ``vtable``/``vptr_offset_bits`` don't
    (Codex review, fresh evidence): CastXML never determines va_list-ness at
    all — its own ``is_va_list`` is always a blanket ``False`` placeholder,
    not a computed fact the way CastXML's vtable *is* one (see
    ``clang_vtable_facts_reliable_value``'s own computation in
    ``serialization.py``: "a castxml... snapshot's own vtable extraction
    predates this field entirely, so it's always reliable"). But
    ``clang_va_list_facts_reliable_value`` reads ``True`` for a CastXML
    snapshot too, since that flag's actual meaning is "safe to trust
    `False` as not-wrong" (CastXML never reports a real va_list parameter
    as anything but `False`, so the polarity is never wrong) — a different
    question from "was this fact actually collected". Reusing that flag
    alone would silently turn "never observed" into "confirmed not
    va_list" on every legacy CastXML load. Gated here on
    ``ast_producer_value == "clang"`` in addition to the reliability flag,
    so only an actual clang-family load can reach ``Fact.present(...)``.
    """
    apply_case_a_fact_backfill(
        d,
        schema_version=schema_version,
        rules=(
            CaseAFactRule(
                "RecordType",
                "vtable",
                _FACT_FIELDS_SCHEMA_VERSION,
                clang_vtable_facts_reliable_value,
                [],
            ),
            CaseAFactRule(
                "RecordType",
                "vptr_offset_bits",
                _FACT_FIELDS_SCHEMA_VERSION,
                clang_vtable_facts_reliable_value,
                None,
            ),
            CaseAFactRule(
                "Param",
                "is_va_list",
                _FACT_FIELDS_SCHEMA_VERSION,
                ast_producer_value == "clang" and clang_va_list_facts_reliable_value,
                False,
            ),
            # ADR-063 Phase 5 (eighth batch, schema v38): TypeField's own CV
            # facts. A pre-v38 document carries no is_const_fact/
            # is_volatile_fact/is_mutable_fact key, so its blanket False
            # values were bridged to Fact.present(False);
            # header_cv_facts_reliable is exactly the signal saying whether
            # that reading is a real fact or a pre-fix castxml placeholder.
            CaseAFactRule(
                "TypeField",
                "is_const",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
                header_cv_facts_reliable_value,
                False,
            ),
            CaseAFactRule(
                "TypeField",
                "is_volatile",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
                header_cv_facts_reliable_value,
                False,
            ),
            CaseAFactRule(
                "TypeField",
                "is_mutable",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
                header_cv_facts_reliable_value,
                False,
            ),
            # TypeField's other two case-(a) fields, each with its own
            # guarding flag: a pre-v38 clang document's blanket `None`
            # default-initializer/deprecation is the same placeholder shape
            # the CV facts have, and the same two flags the detectors
            # already consult say so.
            CaseAFactRule(
                "TypeField",
                "default",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
                clang_field_initializer_facts_reliable_value,
                None,
            ),
            CaseAFactRule(
                "TypeField",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
            ),
        ),
        types=types,
        functions=funcs,
    )
