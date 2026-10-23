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

import logging
import os
from typing import Any

from .extract.dwarf_subtree_index import drop_index

log = logging.getLogger(__name__)


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


_BIT_PRECISE_BASE_NAMES = frozenset({"_BitInt", "unsigned _BitInt"})


def base_type_name(die: Any) -> str:
    """Spelling of a ``DW_TAG_base_type`` DIE that keeps its ABI width.

    Clang names every C23 bit-precise integer just ``_BitInt`` (or
    ``unsigned _BitInt``) and carries the width only in attributes, so two
    different widths would otherwise share one spelling and every
    spelling-based detector would read ``_BitInt(64)`` → ``_BitInt(128)`` as
    no change. Prefer the precise ``DW_AT_bit_size`` (DWARF 5) as
    ``_BitInt(N)``; without it fall back to the storage width, spelled
    ``_BitInt[<bits>-bit storage]`` so it is never mistaken for a declared
    width. GCC already spells ``_BitInt(N)`` and is returned unchanged.
    """
    name = attr_str(die, "DW_AT_name") or "base"
    if name not in _BIT_PRECISE_BASE_NAMES:
        return name
    bits = attr_int(die, "DW_AT_bit_size")
    if bits:
        return f"{name}({bits})"
    byte_size = attr_int(die, "DW_AT_byte_size")
    if byte_size:
        return f"{name}[{byte_size * 8}-bit storage]"
    return name


def attr_bool(die: Any, attr: str) -> bool:
    """Return boolean value of a DIE attribute, or False."""
    if attr not in die.attributes:
        return False
    return bool(die.attributes[attr].value)


def vector_byte_size(die: Any, element_size: int | None) -> int | None:
    """Total bytes of a ``DW_AT_GNU_vector`` array DIE.

    A vector DIE usually carries no ``DW_AT_byte_size``: its size is the
    subrange count times the element size. Returns None when neither the
    declared size nor a bounded subrange with a known element size is present.
    """
    declared = attr_int(die, "DW_AT_byte_size")
    if declared:
        return declared
    for child in die.iter_children():
        if child.tag != "DW_TAG_subrange_type":
            continue
        if "DW_AT_count" in child.attributes:
            count = attr_int(child, "DW_AT_count")
        elif "DW_AT_upper_bound" in child.attributes:
            count = attr_int(child, "DW_AT_upper_bound") + 1
        else:
            return None
        return count * element_size if count and element_size else None
    return None


def array_type_spelling(element: str | None, size: int, is_vector: bool) -> str:
    """Spell a DW_TAG_array_type, distinguishing GCC/Clang vector types.

    A ``DW_AT_GNU_vector`` array is a ``vector_size`` vector: its lane count
    and vector-ness are part of its identity, so it spells as the source
    spelling ``T __attribute__((vector_size(N)))`` (``N`` in bytes, the same
    total size the ``DW_AT_byte_size`` carries). A plain array keeps the
    ``T[]`` form, which never names its element count.
    """
    if is_vector:
        return f"{element or '?'} __attribute__((vector_size({size or 0})))"
    if element is None:
        return "array"
    return f"{element}[]"


def array_type_info(die: Any, inner: tuple[str, int] | None) -> tuple[str, int]:
    """Name and byte size of a DW_TAG_array_type from its element's (name, size)."""
    is_vector = "DW_AT_GNU_vector" in die.attributes
    element_name = inner[0] if inner is not None else None
    if is_vector:
        size = vector_byte_size(die, inner[1] if inner is not None else None) or 0
    else:
        size = attr_int(die, "DW_AT_byte_size")
    return (array_type_spelling(element_name, size, is_vector), size)


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
BASE_PRUNE_TAGS: frozenset[str] = frozenset(
    {
        "DW_TAG_inlined_subroutine",
        "DW_TAG_lexical_block",
        "DW_TAG_GNU_call_site",
    }
)


# ---------------------------------------------------------------------------
# Member location decoding
# ---------------------------------------------------------------------------


class _UnevaluableLocation(Exception):
    """A location expression has no exact offset.

    Raised internally for a stack underflow, an opcode outside the supported
    set, a missing or truncated operand, or an item that is neither an opcode
    nor a tuple. :func:`_evaluate_location_expr` turns it into ``None``; it is
    never turned into a value, because a guessed offset manufactures layout
    facts (a false break or a hidden one).
    """


# Opcodes that carry one operand in the stream (DW_OP_plus_uconst, constu, consts).
_OPERAND_OPS = frozenset({0x23, 0x10, 0x11})


def _read_uleb128(items: list[object], i: int) -> tuple[int, int]:
    """Read a ULEB128 operand from a raw item list starting at *i*.

    Returns ``(value, new_index)`` where *new_index* is the position after
    the terminating byte. Raises :class:`_UnevaluableLocation` if the operand
    is truncated: a partial value is not an offset.
    """
    result = 0
    shift = 0
    while True:
        b = items[i] if i < len(items) else None
        if not isinstance(b, int):
            raise _UnevaluableLocation("truncated ULEB128 operand")
        i += 1
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return result, i
        shift += 7


def _read_sleb128(items: list[object], i: int) -> tuple[int, int]:
    """Read a SLEB128 operand from a raw item list starting at *i*.

    Returns ``(value, new_index)`` with correct sign extension. Raises
    :class:`_UnevaluableLocation` if the operand is truncated.
    """
    result = 0
    shift = 0
    while True:
        b = items[i] if i < len(items) else None
        if not isinstance(b, int):
            raise _UnevaluableLocation("truncated SLEB128 operand")
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if (b & 0x80) == 0:
            break
    # Sign extend if the high bit of the last byte is set
    if shift < 64 and (b & 0x40):
        result |= -(1 << shift)
    return result, i


def _evaluate_location_expr(expr: list[object]) -> int | None:
    """Evaluate a DWARF location expression list to a byte offset.

    DWARF ``DW_AT_data_member_location`` can be a list of opcodes/operands
    rather than a plain integer.  Supported operations:
    - ``DW_OP_plus_uconst N`` — add N to the top of the stack
    - ``DW_OP_constu N`` / ``DW_OP_consts N`` — push N
    - ``DW_OP_lit0..DW_OP_lit31`` — push literal 0–31
    - ``DW_OP_plus`` — pop two values, push their sum

    Handles three item formats:
    1. **pyelftools DWARFExprOp** — namedtuple with ``.op`` (int) and
       ``.args`` (list); ``item[1]`` is the *op_name* string, not an operand.
    2. **Plain (opcode, operand) tuples** — used by tests and some toolchains.
    3. **Raw integer streams** — opcode bytes with LEB128-encoded operands.

    The stack starts with the containing object's base address, modelled as
    0, so an empty expression evaluates to 0.

    Returns ``None`` when the expression has no exact offset: a stack
    underflow, an unsupported opcode, a missing or truncated operand, or a
    non-opcode item. A value is never guessed from such an expression.
    """
    stack: list[int] = [0]  # implicit base address
    items = list(expr)
    i = 0
    try:
        while i < len(items):
            item = items[i]
            if isinstance(item, tuple):
                _eval_tuple_item(item, stack)
                i += 1
            elif isinstance(item, int):
                i = _eval_raw_int_item(item, items, i, stack)
            else:
                raise _UnevaluableLocation(f"non-opcode item {item!r}")
    except _UnevaluableLocation:
        return None
    return stack[-1]


def _apply_op(op: int, operand: int | None, stack: list[int]) -> None:
    """Apply one opcode to *stack*, or raise if it cannot be computed exactly.

    Every op that consumes stack entries checks for underflow here, so none
    can fall through to a partial result.
    """
    if op in _OPERAND_OPS and operand is None:
        raise _UnevaluableLocation(f"DW_OP 0x{op:02x} has no operand")
    if op == 0x23:  # DW_OP_plus_uconst: unary, needs one entry
        if not stack:
            raise _UnevaluableLocation("stack underflow in DW_OP_plus_uconst")
        stack[-1] += operand or 0
    elif op in (0x10, 0x11):  # DW_OP_constu, DW_OP_consts
        stack.append(operand or 0)
    elif 0x30 <= op <= 0x4F:  # DW_OP_lit0..DW_OP_lit31
        stack.append(op - 0x30)
    elif op == 0x22:  # DW_OP_plus: binary, needs two entries
        if len(stack) < 2:
            raise _UnevaluableLocation("stack underflow in DW_OP_plus")
        b = stack.pop()
        stack[-1] += b
    else:
        raise _UnevaluableLocation(f"unsupported DW_OP 0x{op:02x}")


def _eval_tuple_item(item: tuple[object, ...], stack: list[int]) -> None:
    """Evaluate a single tuple item (DWARFExprOp or plain tuple) onto *stack*.

    Raises :class:`_UnevaluableLocation` for an opcode that is not an int, or
    for any opcode :func:`_apply_op` cannot compute exactly.
    """
    # pyelftools DWARFExprOp: hasattr(item, 'args')
    if hasattr(item, "args"):
        op = getattr(item, "op", None)
        args = getattr(item, "args", [])
        raw_operand = args[0] if args else None
    else:
        # Plain (opcode, operand, ...) tuple
        op = item[0] if len(item) > 0 else None
        raw_operand = item[1] if len(item) > 1 else None
    if not isinstance(op, int):
        raise _UnevaluableLocation(f"non-integer opcode {op!r}")
    operand = raw_operand if isinstance(raw_operand, int) else None
    _apply_op(op, operand, stack)


def _eval_raw_int_item(
    item: int,
    items: list[object],
    i: int,
    stack: list[int],
) -> int:
    """Evaluate a single raw-integer item onto *stack*.

    Returns the updated index *i* (already advanced past operands). A raw
    integer is always an opcode here: an unsupported or underflowing one is
    an error, not a constant.
    """
    if item == 0x11:  # DW_OP_consts: SLEB128 operand
        operand, i = _read_sleb128(items, i + 1)
        _apply_op(item, operand, stack)
        return i
    if item in _OPERAND_OPS:  # DW_OP_plus_uconst, DW_OP_constu: ULEB128 operand
        operand, i = _read_uleb128(items, i + 1)
        _apply_op(item, operand, stack)
        return i
    _apply_op(item, None, stack)
    return i + 1


def decode_member_location(val: int | list[object] | None) -> int | None:
    """Decode DW_AT_data_member_location to a byte offset.

    Handles all forms produced by different DWARF versions:
    - None (attribute absent) → 0
    - Constant integer (DWARF 3+) → value directly
    - Location expression list (DWARF 2/3) → evaluated via stack machine

    Returns ``None`` when a location expression is unevaluable (see
    :func:`_evaluate_location_expr`). Callers must record the member's offset
    as unknown; they must not substitute 0.
    """
    if val is None:
        return 0
    if isinstance(val, int):
        return val
    return _evaluate_location_expr(val)


def member_offset_bits(val: int | list[object] | None) -> int | None:
    """``DW_AT_data_member_location`` as a bit offset, or ``None`` if unevaluable."""
    byte_offset = decode_member_location(val)
    return None if byte_offset is None else byte_offset * 8


def add_bit_offsets(a: int | None, b: int | None) -> int | None:
    """Sum two bit offsets; an unknown (``None``) operand keeps the sum unknown."""
    return None if a is None or b is None else a + b


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


#: DIE tags a parameter/variable-kind reader unwraps to see the true
#: underlying type -- shared by ``dwarf_snapshot.py``'s pointer-depth count
#: and its reference/rvalue-reference detection, so both agree on what
#: counts as "just a wrapper" around the real type.
_CV_TYPEDEF_TAGS = ("DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_typedef")


def unwrap_cv_typedef(type_die: Any, CU: Any, depth: int = 0) -> Any | None:
    """*type_die*, unwrapped past any const/volatile/typedef wrapper DIE.

    Used by ``dwarf_snapshot.py``'s ``_process_param`` to see past a
    typedef'd reference/rvalue-reference the way its own pointer-depth count
    already sees past a typedef'd pointer -- a plain :func:`resolve_type_die`
    stops at the immediate DW_AT_type target, which for a typedef'd
    parameter is the DW_TAG_typedef DIE itself, not the reference-type DIE
    it names, silently reading a real reference as VALUE (Codex review, PR
    #1200). Deliberately does NOT unwrap ``DW_TAG_pointer_type`` -- a
    pointer's own referent kind is not the outer indirection kind
    (``int *&`` is a REFERENCE to a pointer, matching castxml's/clang's
    "outermost token wins" convention for the same spelling).
    """
    if type_die is None or depth > 10:
        return type_die
    if type_die.tag in _CV_TYPEDEF_TAGS:
        return unwrap_cv_typedef(resolve_type_die(type_die, CU), CU, depth + 1)
    return type_die


# ---------------------------------------------------------------------------
# Low-memory DIE-cache management
#
# pyelftools permanently caches every DIE object it has ever parsed inside
# ``CompileUnit._dielist``/``_diemap`` (see ``compileunit.py``'s
# ``_get_cached_DIE``/``iter_DIEs``) for as long as the ``CU``/``DWARFInfo``
# objects stay alive — there is no built-in eviction. A single full-tree
# walk of one CU therefore leaves every DIE it visited resident afterwards,
# and abicheck's ``DwarfSession`` (dwarf_unified.py) deliberately shares one
# ``DWARFInfo`` across up to three separate full-tree walks (basic metadata,
# advanced metadata, snapshot build/layout backfill) specifically so later
# walks hit that cache instead of re-parsing. For a small binary this is a
# clear win; for a template-heavy C++ library with millions of DIEs, the
# per-DIE Python object overhead (each DIE holds an attributes dict of
# ``AttributeValue`` records, plus CU/offset/parent bookkeeping) inflates the
# resident set to 50-100x the raw ``.debug_info``/``.debug_loc`` section
# bytes — a real dump measured 108 MB of ``.debug_info`` peaking above 12 GB
# of RSS purely from this. ``free_cu_die_cache``/``dwarf_low_memory_mode``
# let a walk trade the session-cache reuse's "zero re-parse" benefit for
# bounded peak memory on binaries large enough that the trade is worth it.
# ---------------------------------------------------------------------------

#: Default ``.debug_info`` size (MiB) above which DWARF walks free each
#: CompileUnit's DIE cache as soon as they finish with it, instead of
#: leaving it cached for the rest of the process (see module docstring
#: above). Override with ``ABICHECK_DWARF_LOW_MEMORY_MB`` (a negative value
#: disables low-memory mode entirely -- every ``.debug_info`` size is >= 0,
#: so it can never exceed a negative threshold).
#:
#: ``0``: on for every binary with any ``.debug_info``. Measured, the
#: cross-pass DIE reuse the cache buys does not pay for itself at any size:
#: a 1.6 MiB-``.debug_info`` C++ fixture dumped ~5% faster with the cache
#: freed (23.0/24.3 s vs 24.5/25.6 s, alternating) at 405 MB instead of
#: 471-690 MB peak, and a real oneDAL dump 0:49 / 447 MB instead of
#: 0:52 / 1.93 GB -- byte-identical snapshots in both. Retaining every DIE
#: object costs more in allocation and GC than re-decoding the few a later
#: pass touches.
DEFAULT_DWARF_LOW_MEMORY_THRESHOLD_MB = 0


def dwarf_low_memory_mode(dwarf_info: Any) -> bool:
    """Return True when *dwarf_info*'s ``.debug_info`` is large enough that
    DWARF walks over it should free each CU's DIE cache as they finish with
    it (see :func:`free_cu_die_cache`), rather than retaining the whole
    binary's DIE tree simultaneously for the F5b cross-pass cache reuse.

    Threshold is ``ABICHECK_DWARF_LOW_MEMORY_MB``
    (default :data:`DEFAULT_DWARF_LOW_MEMORY_THRESHOLD_MB`), compared
    against the raw ``.debug_info`` section size in MiB -- the same section
    whose in-memory DIE representation is what actually drives peak RSS on
    a large binary (see module docstring). A negative override always
    returns False (documented disable switch -- a plain ``>`` comparison
    alone would do the opposite, since every real size is >= 0 and so
    would compare greater than any negative threshold). An unparsable
    override falls back to the default rather than raising.
    """
    threshold_mb: float = DEFAULT_DWARF_LOW_MEMORY_THRESHOLD_MB
    env = os.environ.get("ABICHECK_DWARF_LOW_MEMORY_MB")
    if env:
        try:
            threshold_mb = float(env)
        except ValueError:
            log.warning(
                "ABICHECK_DWARF_LOW_MEMORY_MB=%r is not a valid number; "
                "falling back to the default (%s MiB).",
                env,
                DEFAULT_DWARF_LOW_MEMORY_THRESHOLD_MB,
            )
            threshold_mb = DEFAULT_DWARF_LOW_MEMORY_THRESHOLD_MB
    if threshold_mb < 0:
        return False
    debug_info_sec = getattr(dwarf_info, "debug_info_sec", None)
    try:
        size = int(getattr(debug_info_sec, "size", 0) or 0)
    except (TypeError, ValueError):
        # A test double (e.g. MagicMock) or otherwise non-numeric ``.size``
        # carries no real size signal — treat as unknown/small rather than
        # raise, matching every other "never raise" DWARF helper here.
        size = 0
    return (size / (1024 * 1024)) > threshold_mb


def free_cu_die_cache(CU: Any) -> None:
    """Drop a CompileUnit's cached DIE objects so the GC can reclaim them.

    Clears (rather than reassigns) the two pyelftools cache containers in
    place, so this works regardless of the exact container type a given
    pyelftools release uses internally (parallel lists as of the versions
    this project has checked, but ``.clear()`` degrades safely instead of
    raising ``AttributeError``/type-mismatches if that ever changes) --
    every DIE accessor (``get_top_DIE``, ``iter_DIEs``, ``_get_cached_DIE``,
    ``get_DIE_from_refaddr``) re-parses transparently from the underlying
    stream on a cache miss once cleared, so a caller that revisits this CU
    later gets a byte-for-byte identical (if freshly allocated) DIE tree --
    the cost is pure CPU (re-decoding), never a correctness change. See the
    module docstring above for why this trade is worth making on a large
    binary.

    No-ops if *CU* doesn't carry the private pyelftools cache attributes (a
    version mismatch, or a CU that hasn't cached anything yet) rather than
    raising -- freeing is always best-effort.
    """
    dielist = getattr(CU, "_dielist", None)
    if dielist is not None:
        dielist.clear()
    diemap = getattr(CU, "_diemap", None)
    if diemap is not None:
        diemap.clear()
    # The indexed child iterator's per-unit table holds the unit's raw
    # bytes; it is rebuilt on next use, so dropping it keeps the bound.
    drop_index(CU)
