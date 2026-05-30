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

"""C23 _BitInt(N) width-change detection.

C23 ``_BitInt(N)`` (and ``unsigned _BitInt(N)``) is a bit-precise integer whose
width N is part of the type. Changing N — or changing a field/param type to or
from ``_BitInt(N)`` — changes the storage size and the calling-convention
treatment, so old code reads/writes the value with the wrong width.
"""
from __future__ import annotations

import re

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_type_spellings import iter_type_slot_changes
from .model import AbiSnapshot

# Match `_BitInt(<N>)` and capture the width. Whitespace inside the parens is
# tolerated. The leading boundary avoids matching e.g. `my_BitInt`.
_BIT_INT_RE = re.compile(r"\b_BitInt\s*\(\s*(\d+)\s*\)")


def _bit_int_width(type_str: str) -> int | None:
    """Return the N from a `_BitInt(N)` spelling, or None if not present."""
    m = _BIT_INT_RE.search(type_str)
    return int(m.group(1)) if m else None


@registry.detector("bit_int_width")
def _diff_bit_int(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect _BitInt(N) width changes or migrations to/from _BitInt."""
    changes: list[Change] = []
    for ch in iter_type_slot_changes(old, new):
        old_w = _bit_int_width(ch.old_type)
        new_w = _bit_int_width(ch.new_type)
        # Fire when _BitInt is involved on at least one side and the width
        # (or presence) differs. Equal widths with identical spelling never
        # reach here (iter only yields differing spellings).
        if old_w is None and new_w is None:
            continue
        if old_w == new_w:
            continue
        if old_w is None:
            detail = f"type became _BitInt({new_w})"
        elif new_w is None:
            detail = f"type was _BitInt({old_w})"
        else:
            detail = f"_BitInt width changed {old_w} → {new_w}"
        changes.append(Change(
            kind=ChangeKind.BIT_INT_WIDTH_CHANGED,
            symbol=ch.symbol,
            description=(
                f"_BitInt change on {ch.slot} of '{ch.symbol}': {detail} "
                f"({ch.old_type} → {ch.new_type}). The bit width determines "
                f"storage size and ABI treatment."
            ),
            old_value=ch.old_type,
            new_value=ch.new_type,
        ))
    return changes
