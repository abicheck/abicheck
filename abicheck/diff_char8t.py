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

"""char8_t migration detection (C++20: char-family <-> char8_t).

C++20 introduced ``char8_t`` as a distinct type (not an alias for ``char`` or
``unsigned char``). Because it participates in overload resolution and name
mangling (Itanium mangling code ``Du``), changing a public parameter, return,
or field type between a char-family spelling and ``char8_t`` changes the mangled
symbol: an old binary fails to resolve it.
"""
from __future__ import annotations

import re

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .diff_type_spellings import iter_type_slot_changes
from .model import AbiSnapshot

# Match char8_t as a whole word (avoid matching a substring of another ident).
_CHAR8T_RE = re.compile(r"\bchar8_t\b")


def _has_char8t(type_str: str) -> bool:
    return bool(_CHAR8T_RE.search(type_str))


@registry.detector("char8t_migration")
def _diff_char8t(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a public type slot migrating between a char family and char8_t."""
    changes: list[Change] = []
    for ch in iter_type_slot_changes(old, new):
        old_c8 = _has_char8t(ch.old_type)
        new_c8 = _has_char8t(ch.new_type)
        # Fire only when char8_t is involved on exactly one side (a migration).
        if old_c8 == new_c8:
            continue
        direction = "char-family → char8_t" if new_c8 else "char8_t → char-family"
        changes.append(make_change(
            ChangeKind.CHAR8T_MIGRATION,
            symbol=ch.symbol,
            name=f"{ch.slot} of '{ch.symbol}'",
            detail=direction,
            old=ch.old_type,
            new=ch.new_type,
        ))
    return changes
