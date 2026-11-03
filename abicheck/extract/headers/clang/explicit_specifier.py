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

"""The ``explicit`` specifier of a constructor/conversion function, read
from the declaration's own source tokens.

Clang's ``-ast-dump=json`` (checked on 18.1.3) never serializes the
``explicit`` specifier of a ``CXXConstructorDecl``/``CXXConversionDecl`` --
neither the JSON nor the text dump carries it. ``bool(node.get("explicit"))``
therefore read *absent evidence* as a confirmed ``False`` on both sides, and
``explicit operator int()`` vs ``operator int()`` compared equal
(case106). What the dump does carry is the declaration's source range: its
``range.begin`` is the first decl-specifier token and its ``loc`` is the
declarator name (``operator`` for a conversion function). The tokens
between them are exactly the decl-specifier sequence, so the specifier is
decided by tokenizing that slice -- not by a text search for the word.

Tri-state, never fabricated:

* ``explicit`` / ``explicit(true)`` -> ``True``;
* no ``explicit`` and every token is a recognized specifier/attribute ->
  ``False``;
* ``explicit(false)`` -> ``False``;
* ``explicit(<other expr>)`` (dependent or not a literal) -> UNSUPPORTED;
* an unrecognized identifier (a macro that may expand to ``explicit``), a
  qualified out-of-line definition, or unreadable source text ->
  NOT_COLLECTED. A macro whose *spelling* location is itself an
  ``explicit`` token is resolved through that spelling.

The comparison detector skips any side whose value is ``None``, so an
undecidable reading never produces ``ctor_explicit_added/removed``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from abicheck.model.fact import Fact

_PRODUCER = "clang-json-source-range"

# Tokens that may legally precede a constructor/conversion declarator name
# without being the `explicit` specifier.
_KNOWN_SPECIFIERS = frozenset(
    {
        "constexpr",
        "consteval",
        "inline",
        "virtual",
        "static",
        "friend",
        "extern",
        "__inline",
        "__inline__",
        "__forceinline",
    }
)
# Attribute-like constructs followed by a balanced parenthesized argument.
_PAREN_ATTRIBUTES = frozenset(
    {"__attribute__", "__declspec", "alignas", "_Alignas", "__attribute"}
)

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<line_comment>//[^\n]*)
  | (?P<block_comment>/\*.*?\*/)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<punct>::|\[\[|\]\]|.)
    """,
    re.VERBOSE | re.DOTALL,
)


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        if kind in ("ws", "line_comment", "block_comment"):
            continue
        out.append(m.group())
    return out


def _balanced(tokens: list[str], i: int, open_: str, close: str) -> int | None:
    """Index just past the group opened at ``tokens[i] == open_``."""
    depth = 0
    for j in range(i, len(tokens)):
        if tokens[j] == open_:
            depth += 1
        elif tokens[j] == close:
            depth -= 1
            if depth == 0:
                return j + 1
    return None


def classify_specifier_tokens(tokens: list[str]) -> bool | None | str:
    """Decide ``explicit`` from the decl-specifier tokens of one declaration.

    Returns ``True``/``False`` when decided, ``None`` when an unrecognized
    token (e.g. a macro) leaves it undecidable, and the string
    ``"dependent"`` for an ``explicit(expr)`` that is neither ``true`` nor
    ``false``. Public for the unit tests' independent token-level oracle.
    """
    result: bool | None | str = False
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "[[":
            depth = 1
            j = i + 1
            while j < n and depth:
                if tokens[j] == "[[":
                    depth += 1
                elif tokens[j] == "]]":
                    depth -= 1
                j += 1
            if depth:
                return None
            i = j
            continue
        if tok in _PAREN_ATTRIBUTES:
            if i + 1 < n and tokens[i + 1] == "(":
                end = _balanced(tokens, i + 1, "(", ")")
                if end is None:
                    return None
                i = end
                continue
            return None
        if tok in _KNOWN_SPECIFIERS:
            i += 1
            continue
        if tok == "explicit":
            if i + 1 < n and tokens[i + 1] == "(":
                end = _balanced(tokens, i + 1, "(", ")")
                if end is None:
                    return None
                inner = tokens[i + 2 : end - 1]
                if inner == ["true"]:
                    result = True
                elif inner == ["false"]:
                    result = result if result is True else False
                else:
                    return "dependent"
                i = end
                continue
            result = True
            i += 1
            continue
        # Anything else -- a macro, a qualifier (`S::`), a template-id --
        # is not a decl-specifier we can see through.
        return None
    return result


def _read(path: str, cache: dict[str, bytes | None]) -> bytes | None:
    if path not in cache:
        try:
            cache[path] = Path(path).read_bytes()
        except OSError:
            cache[path] = None
    return cache[path]


def _point(loc: Mapping[str, Any], which: str) -> Mapping[str, Any]:
    inner = loc.get(which)
    return inner if isinstance(inner, Mapping) else loc


def explicit_specifier_fact(
    node: Mapping[str, Any],
    *,
    file_cache: dict[str, bytes | None] | None = None,
) -> Fact[bool | None]:
    """The ``explicit`` Fact of a Clang ``CXXConstructorDecl``/``CXXConversionDecl``.

    Expects a node whose locations were materialized (every location dict
    carries ``file``), as the Clang header backend's walk produces.
    """
    if "explicit" in node:
        # A Clang that does serialize the specifier is authoritative.
        return Fact.present(bool(node["explicit"]), producer="clang-json")
    cache = file_cache if file_cache is not None else {}
    rng = node.get("range")
    loc = node.get("loc")
    if not isinstance(rng, Mapping) or not isinstance(loc, Mapping):
        return Fact.not_collected("no source range in the AST", producer=_PRODUCER)
    begin = rng.get("begin")
    if not isinstance(begin, Mapping):
        return Fact.not_collected("no source range in the AST", producer=_PRODUCER)
    b_exp = _point(begin, "expansionLoc")
    name = _point(loc, "expansionLoc")
    file = b_exp.get("file")
    start, stop = b_exp.get("offset"), name.get("offset")
    if (
        not isinstance(file, str)
        or file != name.get("file")
        or not isinstance(start, int)
        or not isinstance(stop, int)
        or stop < start
    ):
        return Fact.not_collected(
            "declaration range spans files or lacks offsets", producer=_PRODUCER
        )
    data = _read(file, cache)
    if data is None or stop > len(data):
        return Fact.not_collected(
            f"source text unavailable: {file}", producer=_PRODUCER
        )
    tokens = _tokens(data[start:stop].decode("utf-8", errors="replace"))
    verdict = classify_specifier_tokens(tokens)
    if verdict is None and "spellingLoc" in begin:
        # The first specifier token came from a macro: if its spelling is
        # the bare `explicit` keyword, re-read with that token substituted.
        sp = begin["spellingLoc"]
        sp_file, sp_off = sp.get("file"), sp.get("offset")
        sp_data = _read(sp_file, cache) if isinstance(sp_file, str) else None
        if sp_data is not None and isinstance(sp_off, int) and tokens:
            spelled = _tokens(
                sp_data[sp_off : sp_off + 64].decode("utf-8", errors="replace")
            )
            if spelled[:1] == ["explicit"] and spelled[1:2] != ["("]:
                verdict = classify_specifier_tokens(["explicit", *tokens[1:]])
    if verdict == "dependent":
        return Fact.unsupported(
            "explicit(expr) with a non-literal condition", producer=_PRODUCER
        )
    if verdict is None:
        return Fact.not_collected(
            "decl-specifier sequence contains a token that is not a known "
            "specifier (macro or qualified out-of-line definition)",
            producer=_PRODUCER,
        )
    return Fact.present(bool(verdict), producer=_PRODUCER)
