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

"""Resolve the record a finding names, by canonical identity (ADR-063 2B)."""

from __future__ import annotations

from collections.abc import Iterable

from ..model import RecordType


class RecordLookup:
    """The one record a finding names, by canonical identity (ADR-063 2B).

    For a pass that holds a finding (a ``Change``) and must find the
    ``RecordType`` it is about in one snapshot -- not an old/new pairing,
    which is :func:`lookup_matched_type`'s job. :meth:`resolve` answers with
    the record whose ``entity_id`` equals the finding's own, else the record
    named *name* only when exactly one record in the snapshot carries that
    bare name. A ``{t.name: t}`` dict answered with whichever same-leaf record
    (``ns1::S``/``ns2::S``) was listed last. ``None`` means "cannot tell";
    each caller treats that as its own safe direction.
    """

    def __init__(self, types: Iterable[RecordType]) -> None:
        self._by_entity: dict[object, RecordType] = {}
        self._by_name: dict[str, list[RecordType]] = {}
        for t in types:
            if t.entity_id is not None:
                self._by_entity[t.entity_id] = t
            self._by_name.setdefault(t.name, []).append(t)

    def resolve(self, name: str, entity_id: object = None) -> RecordType | None:
        if entity_id is not None and entity_id in self._by_entity:
            return self._by_entity[entity_id]
        named = self._by_name.get(name, [])
        return named[0] if len(named) == 1 else None
