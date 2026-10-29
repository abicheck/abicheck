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

__all__ = ["decode_abi_slot_fact", "decode_fact"]


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


def decode_abi_slot_fact(raw: Any, schema_version: int) -> Fact[AbiFundamental] | None:
    """A slot's ``type_abi_fact``/``return_type_abi_fact`` as an
    :class:`AbiFundamental` fact (JSON carries a plain mapping). A missing key is
    unset before v69 and NOT_COLLECTED from it on. A value that does not rebuild
    is malformed evidence and reads as NOT_COLLECTED, which never proves two
    slots equal."""
    fact = decode_fact(
        raw, schema_version, min_schema_version=_MIN_SCHEMA_VERSION_FOR_TYPE_ABI_FACTS
    )
    if fact is None or fact.value is None or isinstance(fact.value, AbiFundamental):
        return fact
    value = fact.value
    try:
        rebuilt = AbiFundamental(
            FundamentalEncoding(value["encoding"]),
            int(value["size"]),
            int(value["align"]),
            int(value.get("lanes", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return Fact.not_collected("malformed type_abi_fact value")
    return replace(fact, value=rebuilt)
