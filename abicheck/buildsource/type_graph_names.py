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

"""Textual type spellings for the type/reference graph (ADR-041 P0).

clang's ``qualType`` prints a type *as written*. The helpers here reduce such
a spelling to the base names the graph keys on: stripping cv/pointer/reference
declarators, splitting template argument lists and function signatures,
recognising builtins that never become graph nodes, and reading a
declaration's own type spelling. Pure string work over AST dicts; nothing
here reads the graph or the index.
"""

from __future__ import annotations

import re
from typing import Any

from ..model.execution_cache import memoized

#: Fundamental/standard-library scalar spellings excluded from
#: DECL_HAS_TYPE/TYPE_HAS_FIELD_TYPE/TYPE_INHERITS edges — a public function
#: taking an ``int`` is not a meaningful type dependency and would otherwise
#: flood the graph with one node per primitive.
_BUILTIN_TYPES = frozenset(
    {
        "",
        "void",
        "bool",
        "char",
        "signed char",
        "unsigned char",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "short",
        "unsigned short",
        "int",
        "unsigned int",
        "long",
        "unsigned long",
        "long long",
        "unsigned long long",
        "float",
        "double",
        "long double",
        "size_t",
        "ssize_t",
        "ptrdiff_t",
        "int8_t",
        "int16_t",
        "int32_t",
        "int64_t",
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
        "auto",
    }
)

_LEADING_TYPE_QUALS = ("const ", "volatile ", "struct ", "class ", "enum ", "union ")


def _top_level_paren_index(s: str) -> int:
    """Index of the first ``(`` not nested inside a ``<...>`` template-angle
    group, or -1. Distinguishes the outer parameter-list paren of a
    callback-shaped spelling (``"detail::Impl ()"``) from one nested inside
    an enclosing template instantiation (``"std::function<detail::Impl
    ()>"`` — the first "(" there belongs to the *argument*, not the outer
    type, and must not be treated as ending the outer spelling)."""
    depth = 0
    for i, c in enumerate(s):
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
        elif c == "(" and depth == 0:
            return i
    return -1


@memoized(maxsize=1 << 16)
def _base_type_name(qual_type: str) -> str:
    """Strip cv/pointer/reference/array decoration down to a base type spelling.

    Best-effort textual normalization (``"const detail::Impl *"`` ->
    ``"detail::Impl"``), not a real type-identity resolution — matches the
    approximate/overapprox confidence this module labels its edges with.

    Memoized: a pure function of its argument, and one header graph asks it
    about the same spelling about twice (``_resolve_nested_type_names`` and
    ``header_graph.resolve``) -- 1.47M calls on one MKL side.
    """
    s = (qual_type or "").strip()
    if not s:
        return ""
    # clang glues a top-level cv/restrict-qualified pointer directly to the
    # star with no separating space (``"detail::Impl *const"``, not
    # ``"* const"`` — Codex review; likewise ``"*restrict"`` for a C API's
    # `T * restrict` parameter); normalize so the loop below strips it like
    # the spaced form it already handles.
    s = (
        s.replace("*const", "* const")
        .replace("*volatile", "* volatile")
        .replace("*restrict", "* restrict")
        .replace("*__restrict__", "* __restrict__")
        .replace("*__restrict", "* __restrict")
    )
    changed = True
    while changed:
        changed = False
        # Array bounds must be stripped inside the loop, not only once at the
        # end — an array of pointers ("detail::Impl *[4]") ends in "]", so
        # the suffix-stripping checks below never fire before this runs, and
        # a one-shot strip *after* the loop leaves the trailing "*" behind
        # (Codex review).
        bracket = s.find("[")
        if bracket != -1:
            s = s[:bracket].strip()
            changed = True
        # A callback-shaped template argument (``std::function<detail::Impl
        # ()>``'s single argument spells as ``"detail::Impl ()"``, the
        # written function-signature form) carries a parameter-list suffix
        # this loop's other checks don't recognize, so "Impl ()" was looked
        # up as a type literally named that instead of "Impl" (Codex
        # review). Same fix as the array-bracket case above: strip up to the
        # first *top-level* "(" (depth 0 relative to any enclosing "<...>",
        # since e.g. "std::function<detail::Impl ()>" itself must NOT be cut
        # at the nested paren — that would butcher the outer template's own
        # closing ">") and keep looping so the leftover pointer/cv decoration
        # on the return-type spelling still gets stripped too.
        paren = _top_level_paren_index(s)
        if paren != -1:
            s = s[:paren].strip()
            changed = True
        for suf in ("&&", "&", "*"):
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                changed = True
        for suf in (" const", " volatile", " __restrict__", " __restrict", " restrict"):
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                changed = True
        for pre in _LEADING_TYPE_QUALS:
            if s.startswith(pre):
                s = s[len(pre) :]
                changed = True
    return s.strip()


def _is_excluded_type(name: str) -> bool:
    return name in _BUILTIN_TYPES


def _split_top_level_commas(s: str) -> list[str]:
    """Split *s* on commas at depth 0 (relative to both ``<...>`` and
    ``(...)``) — shared by a template-argument list and a callback-shaped
    parameter list, which use the identical splitting rule."""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for c in s:
        if c in "<(":
            depth += 1
        elif c in ">)":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur))
    return parts


def _matching_close_paren(s: str, open_idx: int) -> int:
    """Index of the ``)`` matching the ``(`` at *open_idx*, or -1."""
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


#: A parenthesized group that contains *only* a function-pointer/reference
#: declarator (``"*"``, ``"&"``, or a pointer-to-member-function
#: ``"Widget::*"``), not any real parameter. A direct (non-template)
#: callback parameter like ``"void (*)(detail::Impl)"`` has *two* top-level
#: paren groups — the declarator wrapper ``"(*)"`` and the actual parameter
#: list ``"(detail::Impl)"`` — and only the second is the real parameter
#: list (Codex review).
_DECLARATOR_ONLY_RE = re.compile(r"^(\*|&|(?P<owner>[\w:]+)::\*)?$")

#: A pointer-to-*member-data* spelling (no parens at all — distinct from a
#: pointer-to-member-*function*, which is parenthesized and matched by
#: ``_DECLARATOR_ONLY_RE`` instead): ``"int detail::Impl::*"`` names both a
#: member type (``int``) and an owner class (``detail::Impl``) — the owner
#: is the actual dependency a public field/parameter of this shape exposes,
#: but the plain trailing-``"*"``-stripping in ``_base_type_name`` leaves a
#: dangling ``"detail::Impl::"`` that matches no indexed declaration (Codex
#: review).
_PTR_TO_MEMBER_DATA_RE = re.compile(
    r"^(?P<member>.+?)\s+(?P<owner>[\w:]+)::\*\s*(?:const|volatile)?\s*$"
)


_BIT_INT_WIDTH_RE = re.compile(r"\b_BitInt\s*\(\s*\d+\s*\)")


def _resolve_nested_type_names(raw: str) -> list[str]:
    """Every type name reachable from one (possibly callback-shaped)
    type spelling: its own base name, its own template arguments
    (recursively), and — for a callback-shaped spelling, whether written as
    a template argument (``"void (detail::Impl)"``,
    ``std::function<void(detail::Impl)>``'s single argument), a direct
    function-pointer declarator (``"void (*)(detail::Impl)"``), or a
    pointer-to-member(-function) (``"void (Owner::*)(Args)"`` /
    ``"int Owner::*"``) — each of its written parameter/owner types too.

    Truncating a callback-shaped spelling at its first top-level ``(`` (the
    same split ``_base_type_name`` applies to a *non-nested* callback type)
    would discard the parameter list entirely, so a private type appearing
    only as a callback *parameter* — not the whole instantiation, not the
    return type — produced no edge at all (Codex review).
    """
    ptm = _PTR_TO_MEMBER_DATA_RE.match(raw.strip())
    if ptm:
        names: list[str] = []
        for group in ("owner", "member"):
            base = _base_type_name(ptm.group(group))
            if base:
                names.append(base)
                names.extend(_template_arg_types(base))
        return names
    # ``_BitInt(N)``'s parenthesised width is part of a builtin type's name,
    # not a parameter list: without this the width ``N`` was read back as a
    # referenced type (``type://128``), and a width change surfaced as a
    # bogus graph rename ``64`` -> ``128``.
    raw = _BIT_INT_WIDTH_RE.sub("_BitInt", raw)
    paren = _top_level_paren_index(raw)
    ret_raw = raw[:paren] if paren != -1 else raw
    names = []
    base = _base_type_name(ret_raw)
    if base:
        names.append(base)
        names.extend(_template_arg_types(base))
    if paren == -1:
        return names
    close = _matching_close_paren(raw, paren)
    inner = raw[paren + 1 : close] if close != -1 else raw[paren + 1 :]
    declarator = _DECLARATOR_ONLY_RE.match(inner.strip()) if close != -1 else None
    if declarator:
        # This first paren group is just a pointer/reference/pointer-to-
        # member-function declarator ("(*)", "(Owner::*)"), not the
        # parameter list — the real one is the *next* top-level paren group.
        owner = declarator.group("owner")
        if owner:
            owner_base = _base_type_name(owner)
            if owner_base:
                names.append(owner_base)
                names.extend(_template_arg_types(owner_base))
        next_rel = _top_level_paren_index(raw[close + 1 :])
        if next_rel == -1:
            return names
        next_paren = close + 1 + next_rel
        next_close = _matching_close_paren(raw, next_paren)
        params = (
            raw[next_paren + 1 : next_close]
            if next_close != -1
            else raw[next_paren + 1 :]
        )
    else:
        params = inner
    for p in _split_top_level_commas(params):
        if p.strip():
            names.extend(_resolve_nested_type_names(p))
    return names


def _template_arg_types(base_type: str) -> list[str]:
    """This type's template argument spellings (recursively), each resolved
    via :func:`_resolve_nested_type_names`.

    A field typed ``std::unique_ptr<detail::Impl>`` — the common PImpl/
    container pattern this module exists to catch — resolves the *whole*
    instantiation spelling as one endpoint (``std::unique_ptr<detail::Impl>``,
    which never matches an indexed declaration and stays unprovenanced), so
    the actual private-type dependency on ``detail::Impl`` was invisible
    (Codex review's exact example). Best-effort bracket/depth matching over
    the textual spelling, not real template-argument parsing: extracts each
    top-level comma-separated argument inside the outermost ``<...>``, then
    recurses so a nested instantiation (``std::vector<std::unique_ptr<X>>``)
    still reaches ``X``.
    """
    start = base_type.find("<")
    if start == -1:
        return []
    depth = 0
    end = -1
    for i in range(start, len(base_type)):
        c = base_type[i]
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return []
    inner = base_type[start + 1 : end]
    args: list[str] = []
    for raw in _split_top_level_commas(inner):
        args.extend(_resolve_nested_type_names(raw))
    return args


def _decl_type_name(node: dict[str, Any]) -> str:
    """A field/parameter decl's raw ``type.qualType`` spelling, unprocessed.

    Deliberately *not* run through :func:`_base_type_name` here — a direct
    (non-template) callback parameter like ``void (*)(detail::Impl)`` needs
    its parameter-list region intact for :func:`_resolve_nested_type_names`
    to still reach ``detail::Impl``; stripping to a bare base name this early
    would discard it before that extraction ever runs (Codex review).
    """
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        return str(type_obj.get("qualType", ""))
    return ""


def _decl_return_type_name(node: dict[str, Any]) -> str:
    """A function/method decl's raw return-type spelling, from its own
    ``type.qualType`` — unprocessed, like :func:`_decl_type_name`.

    clang spells a function decl's own type as the *whole signature*
    (``"detail::Impl *(int)"``, return type immediately followed by the
    parenthesized parameter list — Codex review: this was never read at all,
    so a public factory function returning a private type produced no
    ``DECL_HAS_TYPE`` edge). A **trailing** return type instead spells as
    ``"auto (Args) -> RetType"`` — the region before the parameter list is
    just the literal ``auto`` placeholder, not the real return type, so this
    checks for ``"->"`` first and uses what follows it when present (Codex
    review). Otherwise, best-effort split on the first *top-level* ``(``
    (not nested inside a ``<...>`` template-angle group — Codex review: a
    return type like ``std::function<detail::Impl ()>`` prints the whole
    function's own type as ``"std::function<detail::Impl ()> ()"``, and a
    naive ``find("(")`` stops at the callback's *inner* parameter list,
    truncating mid-template instead of at the function's own outer
    parameter list): correct for the overwhelmingly common case, but not
    real declarator parsing.

    A function *returning a function pointer* (``void (*make_cb())(detail::
    Impl)``) is a further wrinkle: clang nests the outer decl's own
    parameter list *inside* the first top-level paren group, alongside the
    return type's own declarator (``"void (*())(detail::Impl)"`` — the
    empty ``"()"`` right after ``"*"`` is ``make_cb``'s own args, not part
    of the return type). If that first group contains its own nested paren
    pair, this strips it out — collapsing ``"(*())"`` to ``"(*)"`` — leaving
    the properly-shaped ``"void (*)(detail::Impl)"`` return-type spelling
    :func:`_resolve_nested_type_names` already parses correctly (Codex
    review). Own-args content itself is discarded; the outer decl's real
    parameters are separately read from its ``ParmVarDecl`` children, not
    from this string.
    """
    type_obj = node.get("type")
    if not isinstance(type_obj, dict):
        return ""
    qual_type = str(type_obj.get("qualType", ""))
    arrow = qual_type.find("->")
    if arrow != -1:
        return qual_type[arrow + 2 :].strip()
    paren = _top_level_paren_index(qual_type)
    if paren == -1:
        return ""
    close = _matching_close_paren(qual_type, paren)
    if close == -1:
        return qual_type[:paren].strip()
    group = qual_type[paren + 1 : close]
    inner_paren = group.find("(")
    if inner_paren == -1:
        return qual_type[:paren].strip()
    inner_close = _matching_close_paren(group, inner_paren)
    declarator = group[:inner_paren] + (
        group[inner_close + 1 :] if inner_close != -1 else ""
    )
    return (qual_type[:paren] + "(" + declarator + ")" + qual_type[close + 1 :]).strip()
