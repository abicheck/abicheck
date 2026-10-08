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
from collections.abc import Iterator
from itertools import chain

from .detector_registry import registry
from .diff_helpers import make_change
from .diff_type_spellings import TypeSlotChange, iter_type_slot_changes
from .model import AbiSnapshot
from .model.change import Change
from .model.change_catalog.kinds import ChangeKind

# Match `_BitInt(<N>)` and capture the width. Whitespace inside the parens is
# tolerated. The leading boundary avoids matching e.g. `my_BitInt`. Also match
# the `_BitInt[<bits>-bit storage]` spelling dwarf_utils.base_type_name()
# gives a Clang DIE that records only the storage size: different storage is
# a different width as far as layout and calling convention are concerned.
_BIT_INT_RE = re.compile(r"\b_BitInt\s*(?:\(\s*(\d+)\s*\)|\[\s*(\d+)-bit storage\s*\])")


def _bit_int_width(type_str: str) -> int | None:
    """Return the N from a `_BitInt(N)` (or storage-width) spelling, or None."""
    m = _BIT_INT_RE.search(type_str)
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def _debug_field_slot_changes(
    old: AbiSnapshot, new: AbiSnapshot
) -> Iterator[TypeSlotChange]:
    """Field type spellings of header-scoped debug-info records.

    CastXML (0.7) emits a bit-precise integer as an ``<Unimplemented
    type_class="BitInt"/>`` node with no width, so a header-only slot reads
    ``Unimplemented`` on both sides and the width change is invisible there.
    The debug-info layout keeps the width (see
    ``dwarf_utils.base_type_name``), scoped to the same header-declared
    records the layout detectors diff.
    """
    from .compare.debug_layout_view import debug_layout_view
    from .compare.debug_type_scope import debug_layout_scope

    o = debug_layout_view(old).layout
    n = debug_layout_view(new).layout
    if not (o.has_dwarf and n.has_dwarf):
        return
    scope, _ = debug_layout_scope(old, new)
    for name, o_struct in o.structs.items():
        if scope is not None and name not in scope:
            continue
        n_struct = n.structs.get(name)
        if n_struct is None:
            continue
        n_fields = {f.name: f for f in n_struct.fields}
        for o_field in o_struct.fields:
            n_field = n_fields.get(o_field.name)
            if n_field is not None and o_field.type_name != n_field.type_name:
                yield TypeSlotChange(
                    name,
                    f"field '{o_field.name}'",
                    o_field.type_name,
                    n_field.type_name,
                    owner="type",
                )


@registry.detector("bit_int_width")
def _diff_bit_int(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect _BitInt(N) width changes or migrations to/from _BitInt."""
    changes: list[Change] = []
    seen: set[tuple[str, str]] = set()
    for ch in chain(
        iter_type_slot_changes(old, new), _debug_field_slot_changes(old, new)
    ):
        old_w = _bit_int_width(ch.old_type)
        new_w = _bit_int_width(ch.new_type)
        # Fire when _BitInt is involved on at least one side and the width
        # (or presence) differs. Equal widths with identical spelling never
        # reach here (iter only yields differing spellings).
        if old_w is None and new_w is None:
            continue
        if old_w == new_w:
            continue
        # One slot can be seen by both the header and the debug-info view.
        if (ch.symbol, ch.slot) in seen:
            continue
        seen.add((ch.symbol, ch.slot))
        if old_w is None:
            detail = f"type became _BitInt({new_w})"
        elif new_w is None:
            detail = f"type was _BitInt({old_w})"
        else:
            detail = f"_BitInt width changed {old_w} → {new_w}"
        changes.append(
            make_change(
                ChangeKind.BIT_INT_WIDTH_CHANGED,
                symbol=ch.symbol,
                entity_discriminator=ch.owner,
                name=f"{ch.slot} of '{ch.symbol}'",
                detail=detail,
                old=ch.old_type,
                new=ch.new_type,
            )
        )
    return changes
