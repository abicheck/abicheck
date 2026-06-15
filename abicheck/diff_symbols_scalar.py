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

"""Integer-scalar ABI-equivalence checks for symbol-level diffing.

Determines whether two integer built-in/typedef spellings have identical binary
representation (width + signedness) on a given data model, so a name-only change
between equivalent spellings (``size_t`` ↔ ``unsigned long`` on LP64) is not
mistaken for a binary ABI break.

Leaf module (must not import from ``diff_symbols`` to avoid an import cycle).
The symbol-level public surface re-exports these names back from
``diff_symbols``.
"""

from __future__ import annotations

# Integer spellings whose width is *fixed* regardless of data model, mapped to
# (bit-width, is_signed). A name-only change between two spellings with the same
# representation is not a binary ABI break — storage and calling convention are
# identical.
_FIXED_SCALAR_REPR: dict[str, tuple[object, bool]] = {
    "int": (32, True),
    "signed int": (32, True),
    "signed": (32, True),
    "int32_t": (32, True),
    "unsigned int": (32, False),
    "unsigned": (32, False),
    "uint32_t": (32, False),
    "long long": (64, True),
    "long long int": (64, True),
    "signed long long": (64, True),
    "int64_t": (64, True),
    "unsigned long long": (64, False),
    "long long unsigned int": (64, False),
    "uint64_t": (64, False),
    "short": (16, True),
    "short int": (16, True),
    "int16_t": (16, True),
    "unsigned short": (16, False),
    "short unsigned int": (16, False),
    "uint16_t": (16, False),
    "signed char": (8, True),
    "int8_t": (8, True),
    "unsigned char": (8, False),
    "uint8_t": (8, False),
}
# Data-model-dependent spellings. On LP64 (Linux/macOS 64-bit) the ``long``
# family and the pointer-width types are all 64-bit; on ILP32 they are all
# 32-bit; on LLP64 (Windows) ``long`` is 32-bit while the pointer-width types
# stay 64-bit. The snapshot does not record target bitness, so for non-LLP64
# targets we cannot tell LP64 from ILP32 — but the ``long`` family and the
# pointer-width family *co-vary* there (both equal the pointer size), so they
# are equivalent to each other yet NOT to a fixed-width spelling (e.g. ``long``
# vs ``long long`` is a real width change on ILP32 and must not be suppressed).
# A shared ``"long"`` width sentinel captures exactly that: it is equal to
# itself (same sign) but never to a concrete bit-width, so ``size_t`` ↔
# ``unsigned long`` is suppressed on non-LLP64 while ``int`` ↔ ``long`` and
# ``long`` ↔ ``long long`` stay reportable everywhere.
_LONG_SIGNED_SPELLINGS = frozenset({"long", "long int", "signed long"})
_LONG_UNSIGNED_SPELLINGS = frozenset({"unsigned long", "long unsigned int"})
_PTR_SIGNED_SPELLINGS = frozenset({"ssize_t", "ptrdiff_t", "intptr_t"})
_PTR_UNSIGNED_SPELLINGS = frozenset({"size_t", "uintptr_t"})

# The words that make up a C integer built-in's declaration specifiers. A
# spelling composed *only* of these can be reordered freely by the language
# (``unsigned long int`` ≡ ``long unsigned int`` ≡ ``unsigned long``), and
# different toolchains/headers emit different orderings, so they are normalized
# to one canonical form before lookup. Typedefs (``size_t``) and fixed-width
# names (``uint32_t``) contain other words and pass through unchanged.
_INT_SPECIFIER_WORDS = frozenset({"signed", "unsigned", "short", "long", "int", "char"})


def _canonical_int_spelling(t: str) -> str:
    """Canonicalize a bare integer built-in spelling (specifier order and the
    redundant trailing ``int`` are not significant), or return ``t`` unchanged
    when it is not a pure specifier spelling (typedef, fixed-width, …)."""
    words = t.split()
    if not words or any(w not in _INT_SPECIFIER_WORDS for w in words):
        return t
    unsigned = "unsigned" in words
    if "char" in words:
        if unsigned:
            return "unsigned char"
        if "signed" in words:
            return "signed char"
        return t  # bare ``char`` — sign is implementation-defined, leave as-is
    if "short" in words:
        return "unsigned short" if unsigned else "short"
    longs = words.count("long")
    if longs >= 2:
        return "unsigned long long" if unsigned else "long long"
    if longs == 1:
        return "unsigned long" if unsigned else "long"
    return "unsigned int" if unsigned else "int"


def _scalar_repr(type_name: str, is_llp64: bool) -> tuple[object, bool] | None:
    """Map a *bare* integer spelling to (width, is_signed), or None.

    Width is an ``int`` (fixed bit count) or one of two abstract sentinels for
    data-model-dependent spellings whose absolute width the snapshot does not
    record:

    * ``"ptr"`` — pointer-width types (``size_t``, ``ptrdiff_t``, …). Their
      absolute width is unknown (64-bit on LP64/LLP64, 32-bit on ILP32 and
      32-bit Windows), so they must never be equated with a *fixed* width such
      as ``uint64_t``. Used on every platform.
    * ``"long"`` — the ``long`` family on LLP64 only, where ``long`` is 32-bit
      and thus a distinct representation from the 64-bit pointer-width types.

    On non-LLP64 the ``long`` family co-varies with the pointer-width types
    (``size_t`` *is* ``unsigned long`` there), so it shares the ``"ptr"``
    sentinel — making ``size_t`` ↔ ``unsigned long`` a non-break while keeping
    ``long`` ↔ ``long long`` (sentinel vs fixed 64) reportable. Neither
    sentinel ever equals a fixed width, so a distinct built-in change such as
    ``int`` vs ``long`` is reported even where the widths coincide. Returns
    None for anything that is not a plain integer scalar (pointers, references,
    templates, cv-qualified or unknown spellings).
    """
    t = " ".join(type_name.split())
    if not t or any(c in t for c in "*&<>([,") or "const" in t or "volatile" in t:
        return None
    # Fold legal specifier-order variants (``unsigned long int`` -> ``unsigned
    # long``) so a toolchain's spelling choice is not mistaken for an ABI change.
    t = _canonical_int_spelling(t)
    fixed = _FIXED_SCALAR_REPR.get(t)
    if fixed is not None:
        return fixed
    # The ``long`` family is its own distinct built-in. On LLP64 it is 32-bit
    # and must stay distinct from both fixed widths and the 64-bit pointer-width
    # types, so it gets its own ``"long"`` sentinel. Elsewhere it co-varies with
    # the pointer-width types and shares the ``"ptr"`` sentinel.
    if t in _LONG_SIGNED_SPELLINGS:
        return ("long", True) if is_llp64 else ("ptr", True)
    if t in _LONG_UNSIGNED_SPELLINGS:
        return ("long", False) if is_llp64 else ("ptr", False)
    # Pointer-width typedefs have an unknown absolute width on every platform
    # (64-bit on LP64/LLP64, 32-bit on ILP32 and 32-bit Windows), so they map to
    # the ``"ptr"`` sentinel and are never equated with a fixed width such as
    # ``uint64_t``.
    if t in _PTR_SIGNED_SPELLINGS:
        return ("ptr", True)
    if t in _PTR_UNSIGNED_SPELLINGS:
        return ("ptr", False)
    return None


def _abi_equivalent_scalar(old_type: str, new_type: str, is_llp64: bool) -> bool:
    """Whether two integer spellings have identical binary representation.

    True only when both resolve to the same width *and* signedness on the
    target data model — i.e. the change is a spelling/typedef difference, not a
    binary ABI break (e.g. ``size_t`` ↔ ``unsigned long``). A signedness
    difference (``long`` ↔ ``unsigned long``) is not equivalent, and a
    data-model-dependent spelling is never equated with a fixed width
    (``long`` ↔ ``long long`` stays a reportable change, since it is a real
    width change on ILP32 and the snapshot does not record target bitness).
    """
    old_r = _scalar_repr(old_type, is_llp64)
    return old_r is not None and old_r == _scalar_repr(new_type, is_llp64)
