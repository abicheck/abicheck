# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A reordered vtable names each moved virtual, not only its class.

A header snapshot (castxml, the clang backend) records a record's declared
virtual sequence but no per-function ``vtable_index``, so the per-method
``TYPE_VTABLE_CHANGED`` that ``compare.function_signature`` derives from that
index never fired there. The report named the class and dropped the methods an
old client now calls through the wrong slot. This was found by the
abiforge ``virtual_reorder`` cell: DWARF named ``Shape::area`` and
``Shape::kind``, but headers named only ``Shape``.

The oracle here is positional comparison of the two sequences, computed in the
test, never ``_moved_entries`` itself. The domain is every permutation of a
three- and four-slot sequence.
"""

from __future__ import annotations

from itertools import permutations

import pytest

from abicheck.diff_types_vtable import _diff_type_vtable
from abicheck.model import Function, RecordType, Visibility

NAME = "Shape"
SLOTS = [f"_ZNK5Shape{len(m)}{m}Ev" for m in ("area", "kind", "name", "size")]


def _cls(vtable: list[str]) -> RecordType:
    return RecordType(
        name=NAME, kind="class", size_bits=64, vtable=vtable, vptr_offset_bits=0
    )


def _funcs(entries: list[str], *, indexed: bool = False) -> dict[str, Function]:
    return {
        m: Function(
            name=f"{NAME}::{m[10:-2]}",
            mangled=m,
            return_type="int",
            visibility=Visibility.PUBLIC,
            is_virtual=True,
            vtable_index=entries.index(m) + 2 if indexed else None,
        )
        for m in entries
    }


def _members(old: list[str], new: list[str], **kw: bool) -> list[str]:
    changes = _diff_type_vtable(
        NAME, _cls(old), _cls(new), _funcs(old, **kw), _funcs(new, **kw), {}, {}
    )
    return sorted(c.symbol for c in changes if c.symbol != NAME)


def _oracle(old: list[str], new: list[str]) -> list[str]:
    return sorted(m for i, m in enumerate(old) if new[i] != m)


@pytest.mark.parametrize(
    "new",
    [list(p) for n in (3, 4) for p in permutations(SLOTS[:n])],
    ids=lambda p: "-".join(m[10:-2] for m in p),
)
def test_every_reorder_names_exactly_the_moved_virtuals(new: list[str]) -> None:
    old = SLOTS[: len(new)]
    assert _members(old, new) == _oracle(old, new)


def test_the_class_finding_stays() -> None:
    changes = _diff_type_vtable(
        NAME,
        _cls(SLOTS[:2]),
        _cls(SLOTS[1::-1]),
        _funcs(SLOTS[:2]),
        _funcs(SLOTS[:2]),
        {},
        {},
    )
    assert [c.symbol for c in changes].count(NAME) == 1
    moved = {c.symbol: (c.old_value, c.new_value) for c in changes if c.symbol != NAME}
    assert moved == {
        SLOTS[0]: ("position 0", "position 1"),
        SLOTS[1]: ("position 1", "position 0"),
    }


def test_indexed_virtuals_are_left_to_the_index_path() -> None:
    """DWARF records an index: function_signature names them, not this."""
    assert _members(SLOTS[:2], SLOTS[1::-1], indexed=True) == []


def test_an_added_slot_is_not_split_into_moves() -> None:
    """An addition shifts later slots; its own finding names it."""
    assert _members(SLOTS[:2], [SLOTS[2], *SLOTS[:2]]) == []


def test_an_unchanged_sequence_reports_nothing() -> None:
    changes = _diff_type_vtable(
        NAME, _cls(SLOTS), _cls(SLOTS), _funcs(SLOTS), _funcs(SLOTS), {}, {}
    )
    assert changes == []


def test_an_entry_naming_no_declared_function_stays_with_the_class() -> None:
    """No function to name: the class finding alone carries the reorder."""
    changes = _diff_type_vtable(
        NAME, _cls(SLOTS[:2]), _cls(SLOTS[1::-1]), {}, {}, {}, {}
    )
    assert [c.symbol for c in changes] == [NAME]
