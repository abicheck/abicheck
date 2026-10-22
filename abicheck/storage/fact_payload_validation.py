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

"""Validators for decoded ``Fact[...]`` payloads that are not plain scalars.

A fact's value arrives from JSON as a list. Each validator here checks that
shape and rebuilds the tuple the model holds, reading a malformed value as
``NOT_COLLECTED`` rather than trusting it. Split out of ``fact_codec.py``, which
is at its size cap.
"""

from __future__ import annotations

from typing import Any

from ..model.fact import Fact


def validated_identities(fact: Fact[Any] | None) -> Fact[Any] | None:
    """Schema v54's per-slot resolved type identities, after
    :func:`decode_fact` (whose missing-key rule applies: ``None`` below v54,
    ``NOT_COLLECTED`` at or above it, where only castxml writes the key).

    The value arrives as a JSON list and is rebuilt into the tuple the model
    holds; a value that is not a list of non-empty strings is a malformed
    document and reads as ``NOT_COLLECTED`` -- never trusted, since the fact
    only ever *adds* confirmation and dropping it is the safe side.
    """
    if fact is None or fact.value is None:
        return fact
    value = fact.value
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        return Fact.not_collected()
    # Through `Fact._make`, not `dataclasses.replace`: most slots name no
    # record (`int`, `void`), and `_make` serves that `present(())` from its
    # shared table instead of allocating one fact per slot on every load.
    return Fact._make(fact.status, tuple(value), fact.diagnostics, fact.producer)


def validated_template_param_defaults(fact: Fact[Any] | None) -> Fact[Any] | None:
    """Schema v62's per-parameter default spellings, after ``decode_fact``.

    The value arrives as a JSON list of ``str``/``null`` (or the tuple an
    in-memory round trip keeps) and is rebuilt into the tuple the model holds.
    A value that is not a list of that shape is a malformed document and reads
    as ``NOT_COLLECTED`` -- never trusted, so a default that cannot be read back
    never proves "unchanged".
    """
    if fact is None or fact.value is None:
        return fact
    value = fact.value
    if not isinstance(value, (list, tuple)) or not all(
        item is None or (isinstance(item, str) and item) for item in value
    ):
        return Fact.not_collected()
    return Fact._make(fact.status, tuple(value), fact.diagnostics, fact.producer)
