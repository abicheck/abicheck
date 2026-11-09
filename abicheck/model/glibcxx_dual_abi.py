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

"""libstdc++ dual-ABI facts: which ABI a side selected, and which spellings
the selection changes (ADR-050 amendment 2026-11-09).

``_GLIBCXX_USE_CXX11_ABI`` picks one of two libstdc++ ABIs. ``=1`` (the
default since GCC 5) puts ``std::string``, ``std::wstring`` and ``std::list``
(and the classes built on them) into the inline namespace ``std::__cxx11``,
and tags functions returning them ``[abi:cxx11]``. ``=0`` uses the old,
untagged types. The two are different types: their layout differs and every
mangled name that spells one changes.

Pure functions over recorded facts. Nothing here reads a binary.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from .build_mode_facts import GlibcxxDualAbi

__all__ = [
    "RUNTIME_ABI_MACROS",
    "DualAbiSelection",
    "macro_ops_differ_only_in_runtime_abi",
    "normalize_dual_abi_spelling",
    "parse_macro_ops",
    "runtime_abi_ops",
    "selection_from_macro_ops",
    "selection_from_producer_flags",
    "spells_dual_abi_type",
]

#: Macros that select a C++ runtime's ABI rather than configure the library's
#: own declarations. Flipping one is a compatibility finding
#: (``glibcxx_dual_abi_changed``), not a reason to refuse the comparison.
RUNTIME_ABI_MACROS: tuple[str, ...] = ("_GLIBCXX_USE_CXX11_ABI",)

_MACRO = "_GLIBCXX_USE_CXX11_ABI"


@dataclass(frozen=True)
class DualAbiSelection:
    """One side's libstdc++ dual-ABI selection and where it was read from.

    ``source`` is ``"header parse"`` (a ``-D`` the header frontend ran) or
    ``"DW_AT_producer"`` (a ``-D`` the compiler recorded). An inference from
    mangled names is not a selection fact and has no constructor here.
    """

    abi: GlibcxxDualAbi
    source: str

    def describe(self) -> str:
        value = "1" if self.abi is GlibcxxDualAbi.CXX11 else "0"
        return f"{_MACRO}={value} ({self.source})"


def parse_macro_ops(raw: object) -> list[tuple[str, str]]:
    """``profile_fields["macro_ops"]`` (a JSON list of ``[op, arg]``) as
    ``(op, arg)`` pairs; ``[]`` for anything unreadable."""
    try:
        ops = json.loads(raw) if isinstance(raw, str) else []
    except ValueError:
        return []
    if not isinstance(ops, list):
        return []
    return [
        (op[0], op[1])
        for op in ops
        if isinstance(op, list) and len(op) == 2 and all(isinstance(x, str) for x in op)
    ]


def _macro_name(arg: str) -> str:
    return arg.split("=", 1)[0].strip()


def _is_runtime_abi_op(op: tuple[str, str]) -> bool:
    return _macro_name(op[1]) in RUNTIME_ABI_MACROS


def runtime_abi_ops(raw: object) -> list[tuple[str, str]]:
    """The header parse's operations on a runtime-ABI macro, in order."""
    return [op for op in parse_macro_ops(raw) if _is_runtime_abi_op(op)]


def macro_ops_differ_only_in_runtime_abi(old_raw: object, new_raw: object) -> bool:
    """True when the two sides' macro operations differ, and every difference
    is an operation on a runtime-ABI macro (:data:`RUNTIME_ABI_MACROS`).

    The other operations must match in order: a second, unrelated ``-D``
    difference still changes the declarations the parse saw and keeps
    ADR-050's refusal.
    """
    old_ops, new_ops = parse_macro_ops(old_raw), parse_macro_ops(new_raw)
    if old_ops == new_ops:
        return False
    old_rest = [op for op in old_ops if not _is_runtime_abi_op(op)]
    new_rest = [op for op in new_ops if not _is_runtime_abi_op(op)]
    return old_rest == new_rest


def _abi_from_value(value: str) -> GlibcxxDualAbi | None:
    value = value.strip()
    if value in ("", "1"):  # ``-D_GLIBCXX_USE_CXX11_ABI`` defines it to 1
        return GlibcxxDualAbi.CXX11
    if value == "0":
        return GlibcxxDualAbi.OLD
    return None  # a value libstdc++ does not define a meaning for here


def selection_from_macro_ops(raw: object) -> DualAbiSelection | None:
    """The selection the header parse's last ``_GLIBCXX_USE_CXX11_ABI``
    operation made, or ``None`` when the parse left it at the compiler's
    default (no operation, or a final ``-U``).

    A default is not read as ``=1``: a GCC configured with
    ``--with-default-libstdcxx-abi=gcc4-compatible`` defaults to ``=0``.
    """
    selected: GlibcxxDualAbi | None = None
    for op, arg in parse_macro_ops(raw):
        if _macro_name(arg) != _MACRO:
            continue
        if op == "U":
            selected = None
        elif op == "D":
            _, _, value = arg.partition("=")
            selected = _abi_from_value(value)
    return DualAbiSelection(selected, "header parse") if selected else None


_PRODUCER_FLAG_RE = re.compile(r"-D_GLIBCXX_USE_CXX11_ABI(?:=(\d+))?")


def selection_from_producer_flags(flags: Iterable[str]) -> DualAbiSelection | None:
    """The selection a ``DW_AT_producer`` ``-D_GLIBCXX_USE_CXX11_ABI=N`` flag
    records (GCC writes ``-D`` only under ``-grecord-gcc-switches`` with the
    flag in the command line), or ``None``. Conflicting flags (two units
    built differently) select nothing."""
    seen: set[GlibcxxDualAbi] = set()
    for flag in flags:
        m = _PRODUCER_FLAG_RE.fullmatch(flag.strip())
        if m:
            abi = _abi_from_value(m.group(1) or "")
            if abi is not None:
                seen.add(abi)
    if len(seen) != 1:
        return None
    return DualAbiSelection(next(iter(seen)), "DW_AT_producer")


# ── spellings ──────────────────────────────────────────────────────────────

_CHAR_TYPES = ("char", "wchar_t", "char8_t", "char16_t", "char32_t")
_STRING_ALIAS = {
    "char": "std::string",
    "wchar_t": "std::wstring",
    "char8_t": "std::u8string",
    "char16_t": "std::u16string",
    "char32_t": "std::u32string",
}
_FULL_STRING_RE = re.compile(
    r"std::basic_string<\s*(?P<ch>"
    + "|".join(_CHAR_TYPES)
    + r")\s*,\s*std::char_traits<\s*(?P=ch)\s*>\s*,\s*std::allocator<\s*(?P=ch)\s*>\s*>"
)
_SHORT_STRING_RE = re.compile(
    r"std::basic_string<\s*(?P<ch>" + "|".join(_CHAR_TYPES) + r")\s*>"
)


def normalize_dual_abi_spelling(spelling: str) -> str:
    """``spelling`` (a demangled name or type) with the dual-ABI distinction
    removed: no ``__cxx11::``, no ``[abi:cxx11]``, and every
    ``std::basic_string<C, std::char_traits<C>, std::allocator<C> >`` written
    as its ``std::string`` alias, the form the old ABI demangles to.

    Two exports whose spellings differ but normalize equal are the same
    declaration built under the two ABIs.
    """
    out = spelling.replace("[abi:cxx11]", "").replace("__cxx11::", "")
    out = _FULL_STRING_RE.sub(lambda m: _STRING_ALIAS[m.group("ch")], out)
    out = _SHORT_STRING_RE.sub(lambda m: _STRING_ALIAS[m.group("ch")], out)
    return re.sub(r"\s+", " ", out).strip()


# A type spelling the dual ABI changes: the string family (with or without
# ``std::``/``__cxx11::``, as the header backends write fields: ``string``,
# ``list<int>``) or ``std::list``. An unqualified ``list<`` is accepted only
# because the caller already holds a selection or mangling fact naming
# libstdc++; on its own it proves nothing.
_DUAL_ABI_TYPE_RE = re.compile(
    r"(?<![\w:])(?:std::)?(?:__cxx11::)?"
    r"(?:(?:w|u8|u16|u32)?string\b|basic_string\s*<|list\s*<)"
)


def spells_dual_abi_type(spelling: str | None) -> bool:
    """Whether a field, parameter or return type spelling names a type the
    dual-ABI selection changes, by value or through a pointer, reference or
    template argument: a consumer built under the other ABI reads any of them
    as a different type."""
    if not spelling:
        return False
    return bool(_DUAL_ABI_TYPE_RE.search(spelling))
