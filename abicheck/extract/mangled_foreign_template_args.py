# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 abicheck contributors
"""Detect a std-namespace symbol instantiated over a non-std type.

``_guess_symbol_origin`` attributes every ``_ZNSt``/``_ZSt``-prefixed export
to the C++ runtime.  That is wrong for an *implicit instantiation* of a
standard template over one of the library's own types, e.g.
``std::_Sp_counted_deleter<dnnl_memory*, ...>::_M_dispose()``: no runtime
library can export it, because the runtime never saw ``dnnl_memory``.  The
library itself emitted it, so it is native, not a leaked dependency symbol.

This module answers one narrow question over the *mangled* name (no
demangler is required): does any Itanium ``<source-name>`` in the symbol sit
outside a ``std``/``__gnu_cxx``/``__cxxabiv1`` scope?  The scan is a small,
deliberately conservative state machine: any construct it does not model
makes it answer ``False`` ("no evidence"), so an unparsed name keeps its
existing runtime attribution rather than being reclassified on a guess.
"""

from __future__ import annotations

from functools import lru_cache

#: Source-names that open a runtime-owned scope as a nested-name's first part.
_RUNTIME_SCOPES = frozenset(
    {
        "__gnu_cxx",
        "__cxxabiv1",
        "__cxx11",
        # Global C-library types libstdc++ itself instantiates std templates
        # over (codecvt<wchar_t, char, __mbstate_t>, fpos<__mbstate_t>, the
        # locale facets' __locale_t): runtime-owned, never the library's.
        "__mbstate_t",
        "__locale_t",
        "__locale_struct",
        "__va_list_tag",
    }
)

#: Two-letter standard substitutions (``St`` = ``::std::``; the rest name
#: std types). All of them are runtime-owned.
_STD_SUBSTITUTIONS = frozenset({"St", "Sa", "Sb", "Ss", "Si", "So", "Sd"})

#: Single-letter ``<builtin-type>`` codes and type qualifiers/prefixes that
#: carry no name and are simply skipped.
_SKIP_CHARS = frozenset("abcdefghijlmnostvwxyzPRKVO")


def _skip_seq_id(name: str, i: int, terminator: str) -> int | None:
    """Skip a ``[<seq-id>]<terminator>`` body; ``None`` if malformed."""
    while i < len(name) and (name[i].isdigit() or name[i].isupper()):
        i += 1
    if i < len(name) and name[i] == terminator:
        return i + 1
    return None


def _type_done(stack: list[tuple[str, bool]]) -> None:
    """Mark an open literal's type as consumed, so its value comes next."""
    if stack and stack[-1] == ("L", False):
        stack[-1] = ("L", True)


@lru_cache(maxsize=65536)
def has_foreign_template_argument(name: str) -> bool:
    """True when a std-prefixed *name* names a non-runtime ``<source-name>``.

    Only meaningful for names whose outer scope is ``std`` (``_ZNSt``,
    ``_ZNKSt``, ``_ZSt``, and the ``_ZTV``/``_ZTT``/``_ZTI``/``_ZTS`` special
    names of a std type); anything else returns ``False``.
    """
    # vtable / VTT / typeinfo / typeinfo-name of a std type: the rest of the
    # name is that type's encoding, scanned exactly like a function's.
    body = name[4:] if name.startswith(("_ZTV", "_ZTT", "_ZTI", "_ZTS")) else name[2:]
    if not name.startswith("_Z"):
        return False
    stack: list[tuple[str, bool]]
    if body.startswith(("NSt", "NKSt")):
        i = len(name) - len(body) + body.index("St") + 2
        # Outer nested-name chain is std-scoped.
        stack = [("N", True)]
    elif body.startswith("St"):
        i = len(name) - len(body) + 2
        stack = []
    else:
        return False
    # ``prev_std`` marks "the previous token was St", which scopes the next
    # source-name into std even outside an N chain (``St14default_delete``).
    prev_std = True
    # Whether the next source-name begins a fresh nested-name (first part).
    n = len(name)
    found = False
    while i < n:
        c = name[i]
        if stack and stack[-1] == ("L", True):
            # The literal's type is done; what follows is its value.
            while i < n and name[i] != "E":
                if not (name[i].isalnum() or name[i] == "_"):
                    return False
                i += 1
            if i >= n:
                return False
            stack.pop()  # the literal's closing E
            i += 1
            _type_done(stack)
            continue
        if c.isdigit():
            j = i
            while j < n and name[j].isdigit():
                j += 1
            length = int(name[i:j])
            if length <= 0 or j + length > n:
                return False
            ident = name[j : j + length]
            i = j + length
            if stack and stack[-1][0] == "N":
                kind, scoped = stack[-1]
                if scoped is None:  # type: ignore[comparison-overlap]
                    scoped = prev_std or ident in _RUNTIME_SCOPES
                    stack[-1] = (kind, scoped)
                if not scoped:
                    found = True
            else:
                if not prev_std and ident not in _RUNTIME_SCOPES:
                    found = True
                _type_done(stack)
            prev_std = False
            continue
        if c == "N":
            stack.append(("N", None))  # type: ignore[arg-type]
            i += 1
            # cv-qualifiers on a member function's nested-name.
            while i < n and name[i] in "rVKRO":
                i += 1
            if name.startswith("St", i):
                stack[-1] = ("N", True)
                i += 2
                prev_std = True
                continue
            prev_std = False
            continue
        if c in "IJ":
            stack.append((c, False))
            i += 1
            prev_std = False
            continue
        if c == "L":
            # <expr-primary>: L <type> <value> E, or L _Z <encoding> E.
            stack.append(("L", False))
            i += 1
            if name.startswith("_Z", i):
                return False
            continue
        if c == "E":
            if not stack:
                return False
            closed = stack.pop()
            i += 1
            prev_std = False
            if closed[0] == "N":
                _type_done(stack)
            continue
        if c == "S":
            two = name[i : i + 2]
            if two in _STD_SUBSTITUTIONS:
                i += 2
                prev_std = two == "St"
                if stack and stack[-1][0] == "N" and stack[-1][1] is None:
                    stack[-1] = ("N", True)
                continue
            nxt = _skip_seq_id(name, i + 1, "_")
            if nxt is None:
                return False
            # A back-reference to an earlier component; its foreignness was
            # already judged where it first appeared. Opening a nested name
            # with one leaves that chain's scope unknown here, so its later
            # parts are never counted as evidence (treated as runtime-owned).
            if stack and stack[-1][0] == "N" and stack[-1][1] is None:
                stack[-1] = ("N", True)
            i = nxt
            prev_std = False
            continue
        if c == "T":
            nxt = _skip_seq_id(name, i + 1, "_")
            if nxt is None:
                return False
            i = nxt
            prev_std = False
            continue
        if c == "C" and i + 1 < n and name[i + 1] in "12345I":
            i += 2  # constructor name
            continue
        if c == "C":
            i += 1  # complex-type prefix
            continue
        if c == "D":
            if i + 1 < n and name[i + 1] in "012345":
                i += 2  # destructor name
                continue
            if i + 1 < n and name[i + 1] == "p":
                i += 2  # pack expansion prefix, like a pointer qualifier
                continue
            # Dn (nullptr_t), Di/Ds/Du/Dh/Df/Dd/De/Da/Dc builtins.
            if i + 1 < n and name[i + 1] in "nisuhfdeac":
                _type_done(stack)
                i += 2
                prev_std = False
                continue
            return False
        if c in _SKIP_CHARS:
            i += 1
            if c not in "PRKVO":
                prev_std = False
                _type_done(stack)
            continue
        if c == "F":
            stack.append(("F", False))
            i += 1
            continue
        return False
    return found and not stack
