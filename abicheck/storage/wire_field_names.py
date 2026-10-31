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

"""The keys the snapshot encoder writes for a dataclass record.

A leaf shared by the encoder (`snapshot_encode.py`) and the ADR-062 D14
record reader (`wire_record_extensions.py`), which compares a stored record
against exactly these keys, so the two cannot drift and neither imports the
other.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields

__all__ = ["wire_field_names"]


def wire_field_names(cls: type) -> tuple[str, ...]:
    """*cls*'s persisted fields in declaration order: its stored fields plus
    any retired bridge views it inherits (ADR-063 Phase 10), computed per
    concrete class so a subclass's own fields are never dropped."""
    stored = {f.name for f in dataclass_fields(cls)}
    retired: frozenset[str] = getattr(cls, "__retired_bridge_fields__", frozenset())
    if not retired:
        return tuple(f.name for f in dataclass_fields(cls))
    declared = cls.__dataclass_fields__  # type: ignore[attr-defined]
    return tuple(n for n in declared if n in stored or n in retired)
