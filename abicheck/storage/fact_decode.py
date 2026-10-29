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

"""Decode one serialized ``Fact[T]`` from its JSON dict form (ADR-063).

Split out of :mod:`.fact_codec` (which is at its no-growth cap) so the decode
primitives and the schema-v69 ABI-fact rebuild have their own owner.
:mod:`.fact_codec` re-exports :func:`decode_fact`, so every existing importer
keeps its name.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ..model import Fact, FactStatus
from ..model.abi_fundamental import AbiFundamental, FundamentalEncoding
from .decode_memo import memo_fact
from .fact_schema_versions import (
    _FACT_FIELDS_SCHEMA_VERSION,
    _MIN_SCHEMA_VERSION_FOR_TYPE_ABI_FACTS,
)
from .guards import provenance_text

__all__ = [
    "abi_fundamental_from_json",
    "decode_abi_slot_fact",
    "decode_fact",
    "decode_fact_with_legacy_presence",
]


def decode_fact(
    raw: Any,
    schema_version: int,
    min_schema_version: int = _FACT_FIELDS_SCHEMA_VERSION,
    *,
    value_decoder: Callable[[Any], Any] | None = None,
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
    if value_decoder is None:
        return _decode_raw(raw, schema_version, min_schema_version)
    fact = _decode_raw(raw, schema_version, min_schema_version)
    if fact is None or fact.value is None:
        return fact
    try:
        rebuilt = value_decoder(fact.value)
    except (KeyError, TypeError, ValueError):
        return Fact.not_collected("malformed fact value")
    return replace(fact, value=rebuilt)


def _decode_raw(
    raw: Any, schema_version: int, min_schema_version: int
) -> Fact[Any] | None:
    if not raw:
        return Fact.not_collected() if schema_version >= min_schema_version else None
    # Repeat simple shapes come from `decode_memo` (same value as a fresh
    # build); `Fact._make` shares one instance per common valueless shape.
    return memo_fact(raw, _build_fact)


def _build_fact(raw: Any) -> Fact[Any]:
    return Fact._make(
        FactStatus(raw["status"]),
        raw.get("value"),
        tuple(raw.get("diagnostics") or ()),
        # T9: additive, unversioned field -- absent reads as `None`. Rejected
        # rather than coerced if not a string (`str(7)` and `str("7")` would
        # otherwise deserialize identically), the same discipline every other
        # provenance-shaped field here applies; `None` is the legitimate "no
        # attribution recorded" value and bypasses `provenance_text`.
        (
            None
            if raw.get("producer") is None
            else provenance_text(raw["producer"], "fact producer")
        ),
    )


def abi_fundamental_from_json(value: Any) -> AbiFundamental:
    """The :class:`AbiFundamental` a snapshot's JSON mapping spells."""
    return AbiFundamental(
        FundamentalEncoding(value["encoding"]),
        int(value["size"]),
        int(value["align"]),
        int(value.get("lanes", 0)),
    )


def decode_abi_slot_fact(raw: Any, schema_version: int) -> Fact[AbiFundamental] | None:
    """A slot's ``type_abi_fact``/``return_type_abi_fact`` as an
    :class:`AbiFundamental` fact. A missing key is unset before v69 and
    NOT_COLLECTED from it on; a value that does not rebuild is malformed
    evidence and reads as NOT_COLLECTED, which never proves two slots equal."""
    return decode_fact(
        raw,
        schema_version,
        min_schema_version=_MIN_SCHEMA_VERSION_FOR_TYPE_ABI_FACTS,
        value_decoder=abi_fundamental_from_json,
    )


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
