# SPDX-License-Identifier: Apache-2.0
"""The public headers' template-parameter defaults as one table (ADR-085 D1).

``AbiSnapshot.cxx_template_defaults`` maps a template to the spelled default
of each of its template parameters, in declaration order, read from the clang
AST of the parsed headers by ``extract/headers/clang/template_default_entries``
-- the same reading on both header backends (castxml gets it through its
clang join, ``extract/headers/castxml/clang_semantics_join.py``):

* key ``<family> <qualified name>``, family ``class``, ``function``, ``alias``
  or ``variable``; a function template's key also carries its function type
  (``function f(void (type-param-0))``), so two overloads never share one;
* value: a tuple with one entry per template parameter, ``None`` where the
  parameter has no default; or ``None`` for the whole template when a default
  was present but could not be spelled, or two declarations of it disagree
  (the template is known, its defaults are not: the assurance rollup names
  it). A reference to an earlier parameter is spelled by position
  (``type-param-0``), so renaming a parameter is not a change.

The table is ``None`` when it was not modelled (castxml whose clang join could
not run, a C parse, an older dump); then no default change is assessed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

__all__ = [
    "TEMPLATE_DEFAULT_FAMILIES",
    "TemplateDefaults",
    "added_defaults_only",
    "merge_template_defaults",
    "split_template_default_key",
    "template_default_key",
]

#: The template families a key can name.
TEMPLATE_DEFAULT_FAMILIES: tuple[str, ...] = ("class", "function", "alias", "variable")

#: One template's spelled defaults (``None`` per parameter without one), or
#: ``None`` when they are not known.
TemplateDefaults = tuple[str | None, ...] | None


def template_default_key(family: str, name: str) -> str:
    """The table key of the *family* template *name*."""
    if family not in TEMPLATE_DEFAULT_FAMILIES:
        raise ValueError(f"unknown template family {family!r}")
    return f"{family} {name}"


def split_template_default_key(key: str) -> tuple[str | None, str]:
    """``(family, name)`` of a table key; family ``None`` for any other key."""
    family, sep, name = key.partition(" ")
    if sep and family in TEMPLATE_DEFAULT_FAMILIES:
        return family, name
    return None, key


def merge_template_defaults(rows: Iterable[TemplateDefaults]) -> TemplateDefaults:
    """The defaults several declarations of one template agree on.

    C++ states a default once, on whichever declaration introduces it, so a
    parameter's default is taken from any declaration that spells one. A row
    that is not known, a differing parameter count, or two different spellings
    of one parameter's default make the whole template unknown (``None``).
    """
    known: list[tuple[str | None, ...]] = []
    for row in rows:
        if row is None:
            return None
        known.append(tuple(row))
    if not known or len({len(row) for row in known}) != 1:
        return None
    merged: list[str | None] = []
    for column in zip(*known, strict=True):
        spelled = {spelling for spelling in column if spelling is not None}
        if len(spelled) > 1:
            return None
        merged.append(spelled.pop() if spelled else None)
    return tuple(merged)


def added_defaults_only(
    before: Sequence[str | None], after: Sequence[str | None]
) -> bool:
    """Whether every position that differs only gained a default (no default
    before). For a class, alias or variable template that leaves every
    previously valid spelling valid with the same meaning: the argument had
    to be written before."""
    return all(b is None for b, a in zip(before, after, strict=True) if b != a)
