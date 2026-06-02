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

"""Shared DWARF attribute helpers for pyelftools DIE access.

Used by dwarf_metadata.py, dwarf_advanced.py, and dwarf_unified.py to avoid
duplicating low-level DIE attribute extraction logic.
"""
# pylint: disable=invalid-name  # CU is the standard DWARF term (Compilation Unit)
from __future__ import annotations

from typing import Any


def has_real_dwarf_info(elf: Any) -> bool:
    """Return True iff the ELF carries real DWARF debug info (``.debug_info``
    or its compressed ``.zdebug_info`` form).

    This is the ``strict=True`` semantics of pyelftools'
    ``ELFFile.has_dwarf_info()``: unlike the default, it does NOT count the
    always-present ``.eh_frame`` section, so stripped binaries (which keep
    ``.eh_frame`` but drop every ``.debug_*`` section) correctly report no
    DWARF.

    Implemented via direct section lookup rather than the
    ``has_dwarf_info(strict=...)`` keyword because that keyword only exists in
    pyelftools >= 0.30, while the project supports ``pyelftools >= 0.29``.
    ``get_section_by_name`` has been stable across all supported versions.
    """
    return bool(
        elf.get_section_by_name(".debug_info")
        or elf.get_section_by_name(".zdebug_info")
    )


def attr_str(die: Any, attr: str) -> str:
    """Return string value of a DIE attribute, or ''."""
    if attr not in die.attributes:
        return ""
    val = die.attributes[attr].value
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val) if val is not None else ""


def attr_int(die: Any, attr: str) -> int:
    """Return integer value of a DIE attribute, or 0.

    Handles signed DW_FORM_sdata values correctly (e.g. negative enum consts).
    """
    if attr not in die.attributes:
        return 0
    val = die.attributes[attr].value
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def attr_bool(die: Any, attr: str) -> bool:
    """Return boolean value of a DIE attribute, or False."""
    if attr not in die.attributes:
        return False
    return bool(die.attributes[attr].value)


def resolve_die_ref(die: Any, attr_name: str, CU: Any) -> Any:
    """Resolve a DW_AT_type (or similar) reference to the target DIE.

    Handles both CU-relative refs (DW_FORM_ref1/2/4/8/udata) and
    section-absolute refs (DW_FORM_ref_addr).  pyelftools stores the raw
    offset in .value; for CU-relative forms we add CU.cu_offset to get
    the absolute .debug_info position expected by get_DIE_from_refaddr().
    """
    attr = die.attributes[attr_name]
    form = attr.form
    raw_val: int = attr.value

    if form == "DW_FORM_ref_addr":
        # Section-relative: already an absolute offset
        abs_offset = raw_val
    else:
        # CU-relative (DW_FORM_ref1/2/4/8/ref_udata): add CU header offset
        abs_offset = raw_val + CU.cu_offset

    return CU.get_DIE_from_refaddr(abs_offset)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

#: Base set of DWARF tags to prune (skip subtrees).
#: Each module extends this with its own additions (e.g. DW_TAG_subprogram).
BASE_PRUNE_TAGS: frozenset[str] = frozenset({
    "DW_TAG_inlined_subroutine",
    "DW_TAG_lexical_block",
    "DW_TAG_GNU_call_site",
})


# ---------------------------------------------------------------------------
# Member location decoding
# ---------------------------------------------------------------------------

def _read_uleb128(items: list[object], i: int) -> tuple[int, int]:
    """Read a ULEB128-encoded value from a raw byte list starting at *i*.

    Returns ``(value, new_index)`` where *new_index* is the position after
    the last consumed byte.
    """
    result = 0
    shift = 0
    while i < len(items):
        b = items[i]
        if not isinstance(b, int):
            break
        i += 1
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
    return result, i


def _read_sleb128(items: list[object], i: int) -> tuple[int, int]:
    """Read a SLEB128-encoded value from a raw byte list starting at *i*.

    Returns ``(value, new_index)`` with correct sign extension.
    """
    result = 0
    shift = 0
    last_byte = 0
    while i < len(items):
        b = items[i]
        if not isinstance(b, int):
            break
        last_byte = b
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if (b & 0x80) == 0:
            break
    # Sign extend if the high bit of the last byte is set
    if shift < 64 and (last_byte & 0x40):
        result |= -(1 << shift)
    return result, i


def _evaluate_location_expr(expr: list[object]) -> int:
    """Evaluate a DWARF location expression list to a byte offset.

    DWARF ``DW_AT_data_member_location`` can be a list of opcodes/operands
    rather than a plain integer.  Common patterns:
    - ``[DW_OP_plus_uconst, N]`` — offset is N
    - ``[DW_OP_constu, N]`` or ``[DW_OP_consts, N]`` — push N
    - ``[DW_OP_lit0..DW_OP_lit31]`` — push literal 0–31

    Handles three item formats:
    1. **pyelftools DWARFExprOp** — namedtuple with ``.op`` (int) and
       ``.args`` (list); ``item[1]`` is the *op_name* string, not an operand.
    2. **Plain (opcode, operand) tuples** — used by tests and some toolchains.
    3. **Raw integer streams** — opcode bytes with LEB128-encoded operands.
    """
    stack: list[int] = [0]  # implicit base address
    i = 0
    items = list(expr)
    while i < len(items):
        item = items[i]

        # ---- DWARFExprOp or plain tuple ----
        if isinstance(item, tuple):
            _eval_tuple_item(item, stack)
            i += 1
            continue

        # ---- Raw integer stream ----
        if isinstance(item, int):
            i = _eval_raw_int_item(item, items, i, stack)
            continue

        i += 1

    return stack[-1] if stack else 0


def _eval_tuple_item(item: tuple[object, ...], stack: list[int]) -> None:
    """Evaluate a single tuple item (DWARFExprOp or plain tuple) onto *stack*."""
    # pyelftools DWARFExprOp: hasattr(item, 'args')
    if hasattr(item, "args"):
        op = getattr(item, "op", 0)
        args = getattr(item, "args", [])
        operand = args[0] if args else 0
    else:
        # Plain (opcode, operand, ...) tuple
        op = item[0] if len(item) > 0 else 0
        operand = item[1] if len(item) > 1 else 0
        if not (isinstance(op, int) and isinstance(operand, int)):
            return

    if isinstance(op, int):
        # DW_OP_plus_uconst = 0x23
        if op == 0x23:
            stack[-1] = stack[-1] + operand if stack else operand
        # DW_OP_constu = 0x10, DW_OP_consts = 0x11
        elif op in (0x10, 0x11):
            stack.append(operand)
        # DW_OP_lit0..DW_OP_lit31 = 0x30..0x4f
        elif 0x30 <= op <= 0x4F:
            stack.append(op - 0x30)
        # DW_OP_plus = 0x22
        elif op == 0x22 and len(stack) >= 2:
            b = stack.pop()
            stack[-1] += b


def _eval_raw_int_item(
    item: int, items: list[object], i: int, stack: list[int],
) -> int:
    """Evaluate a single raw-integer item onto *stack*.

    Returns the updated index *i* (already advanced past operands).
    """
    # DW_OP_plus_uconst = 0x23 (operand: ULEB128)
    if item == 0x23:
        val, i = _read_uleb128(items, i + 1)
        stack[-1] = stack[-1] + val if stack else val
        return i
    # DW_OP_constu = 0x10 (operand: ULEB128)
    if item == 0x10:
        val, i = _read_uleb128(items, i + 1)
        stack.append(val)
        return i
    # DW_OP_consts = 0x11 (operand: SLEB128)
    if item == 0x11:
        val, i = _read_sleb128(items, i + 1)
        stack.append(val)
        return i
    # DW_OP_lit0..DW_OP_lit31 (0x30..0x4f) — no operand
    if 0x30 <= item <= 0x4F:
        stack.append(item - 0x30)
        return i + 1
    # DW_OP_plus = 0x22 — no operand
    if item == 0x22 and len(stack) >= 2:
        b = stack.pop()
        stack[-1] += b
        return i + 1
    # Unknown opcode or bare integer — treat as constant
    stack.append(item)
    return i + 1


def decode_member_location(val: int | list[object] | None) -> int:
    """Decode DW_AT_data_member_location to a byte offset.

    Handles all forms produced by different DWARF versions:
    - None → 0
    - Constant integer (DWARF 3+) → value directly
    - Location expression list (DWARF 2/3) → evaluated via stack machine
    """
    if val is None:
        return 0
    if isinstance(val, int):
        return val
    return _evaluate_location_expr(val)


# ---------------------------------------------------------------------------
# DIE reference resolution
# ---------------------------------------------------------------------------

def resolve_type_die(die: Any, CU: Any) -> Any | None:
    """Resolve DW_AT_type reference on *die* to a target DIE, or None."""
    if "DW_AT_type" not in die.attributes:
        return None
    try:
        return resolve_die_ref(die, "DW_AT_type", CU)
    except Exception:  # noqa: BLE001
        return None
