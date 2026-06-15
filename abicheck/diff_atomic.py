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

"""C11 _Atomic qualifier change detection.

Adding or removing the ``_Atomic`` qualifier on a public field/param/return
type is an ABI hazard: per WG14 the size and alignment of an _Atomic-qualified
type may differ from the unqualified type and varies across implementations, so
layout and calling convention can diverge. The DWARF/type parser surfaces the
qualifier as the spelling ``_Atomic(T)`` (or a leading ``_Atomic`` keyword).
"""
from __future__ import annotations

import re

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .diff_type_spellings import iter_type_slot_changes
from .model import AbiSnapshot

# Match the _Atomic qualifier in either spelling: `_Atomic(T)` or `_Atomic T`.
_ATOMIC_RE = re.compile(r"\b_Atomic\b")


def _has_atomic(type_str: str) -> bool:
    return bool(_ATOMIC_RE.search(type_str))


@registry.detector("atomic_qualifier")
def _diff_atomic(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect _Atomic qualifier added/removed on a public type slot."""
    changes: list[Change] = []
    for ch in iter_type_slot_changes(old, new):
        old_a = _has_atomic(ch.old_type)
        new_a = _has_atomic(ch.new_type)
        if old_a == new_a:
            continue
        direction = "qualifier added" if new_a else "qualifier removed"
        changes.append(make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol=ch.symbol,
            name=f"{ch.slot} of '{ch.symbol}'",
            detail=direction,
            old=ch.old_type,
            new=ch.new_type,
        ))
    return changes
