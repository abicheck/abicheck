# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Which castxml source files are provably C++ headers.

castxml parses every header in C++ mode, so a declaration in a C header
without an ``extern "C"`` guard gets a guessed C++ mangling. The extern-C
recovery (``functions.parse_function`` and the variable sibling in
``dumper.py``) rewrites such a declaration onto its bare-name export when the
guessed mangling is not exported but the bare name is. That recovery is right
only for a header that could be a C header: in a header that declares
something C cannot express, an unguarded declaration genuinely has C++
linkage, and "the mangled export is gone while a bare C namesake exists" is a
lost export, not proof of C linkage.

A file is provably C++ when it declares (non-artificially) a class with the
``class`` key, a member function, constructor, destructor, operator or
conversion function, an entity inside a named namespace, or a global
function or variable whose guessed C++ mangling *is* exported -- the library
compiled that header's declarations with C++ linkage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import CastxmlParserContext

__all__ = ["cxx_only_files", "is_itanium_global_variable_guess"]

_CXX_ONLY_TAGS = frozenset(
    {
        "Class",
        "Method",
        "Constructor",
        "Destructor",
        "OperatorMethod",
        "OperatorFunction",
        "Converter",
    }
)
_NAMESPACE_MEMBER_TAGS = frozenset(
    {"Function", "Variable", "Struct", "Union", "Enumeration", "Typedef"}
)


def cxx_only_files(ctx: CastxmlParserContext) -> frozenset[str]:
    """The castxml ``File`` ids that are provably C++ headers (memoized)."""
    cached = ctx.cxx_only_files
    if cached is not None:
        return cached
    exported = ctx.exported_dynamic | ctx.exported_static
    out: set[str] = set()
    for el in ctx.root:
        file = el.get("file")
        if not file or el.get("artificial") == "1":
            continue
        tag = el.tag
        if tag in _CXX_ONLY_TAGS:
            out.add(file)
            continue
        if tag not in _NAMESPACE_MEMBER_TAGS:
            continue
        owner = ctx.id_map.get(el.get("context", ""))
        if owner is not None and owner.tag == "Namespace":
            if owner.get("name") not in ("::", None, ""):
                out.add(file)
                continue
        mangled = el.get("mangled", "")
        if mangled and mangled != el.get("name") and mangled in exported:
            out.add(file)
    ctx.cxx_only_files = frozenset(out)
    return ctx.cxx_only_files


def is_itanium_global_variable_guess(mangled: str, name: str) -> bool:
    """Whether *mangled* is castxml's ``_Z<len><name>`` guess for a variable
    declared at global scope.

    The Itanium ABI never mangles a variable in the global namespace: its
    symbol is the bare name whether it has C or C++ language linkage. castxml
    nevertheless writes ``_Z5c_var`` even for an explicit ``extern "C" int
    c_var;``, so on an Itanium target this guess is never a real symbol, in a
    C header or a C++ one, and the C-header gate above does not apply to it.
    (MSVC decorates a C++-linkage global, ``?c_var@@3HA``, so its guess stays
    behind the gate.)
    """
    return bool(name) and mangled == f"_Z{len(name)}{name}"
