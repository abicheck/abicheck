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

from typing import TYPE_CHECKING, Any

from ..model import Fact, FactStatus

if TYPE_CHECKING:
    from ..model import Function, RecordType

__all__ = [
    "apply_legacy_fact_backfill",
    "decode_fact",
    "decode_record_facts",
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
    for func_dict in d.get("functions", []):
        for param_dict in func_dict.get("params", []):
            _encode_one(param_dict.get("is_va_list_fact"))


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
_MIN_SCHEMA_VERSION_FOR_RECORDTYPE_CASE_B_FACTS = 31


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
        "vptr_offset_bits_fact": decode_fact(t.get("vptr_offset_bits_fact"), schema_version),
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


def apply_legacy_fact_backfill(
    d: dict[str, Any],
    types: list[RecordType],
    funcs: list[Function],
    schema_version: int,
    clang_vtable_facts_reliable_value: bool,
    clang_va_list_facts_reliable_value: bool,
    ast_producer_value: str | None,
) -> None:
    """Correct the legacy (pre-v26) backfill for vtable/vptr/is_va_list.

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
    if schema_version >= 26:
        return
    if not clang_vtable_facts_reliable_value:
        for type_dict, record in zip(d.get("types", []), types, strict=False):
            if "vtable_fact" not in type_dict:
                record.vtable = []
                record.vtable_fact = Fact.not_collected()
            if "vptr_offset_bits_fact" not in type_dict:
                record.vptr_offset_bits = None
                record.vptr_offset_bits_fact = Fact.not_collected()
    if ast_producer_value != "clang" or not clang_va_list_facts_reliable_value:
        for func_dict, func in zip(d.get("functions", []), funcs, strict=False):
            for param_dict, param in zip(
                func_dict.get("params", []), func.params, strict=False
            ):
                if "is_va_list_fact" not in param_dict:
                    param.is_va_list = False
                    param.is_va_list_fact = Fact.not_collected()
