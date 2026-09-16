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

"""Inverted index over public signature sites, for idiom recognition.

Split out of :mod:`abicheck.idioms` so the index has an owner of its own
rather than pushing that module past its architecture line budget.

This module introduces **no new rule**. It is a mechanical inversion of the
``(referenced_by_public, only_ever_by_pointer)`` predicate that module has
always applied: same public-surface admission test, same short-name and
qualified matching clauses kept separate, same by-value test. In particular it
adds no C++ type resolver and no ambiguity policy -- the existing matching's
limitations are preserved as they are.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

from ..model import Function
from ..model.surface_facts import in_public_surface
from .type_spelling import strip_ptr


def _is_pointer(type_str: str) -> bool:
    return "*" in type_str


class PublicUseIndex(NamedTuple):
    """The inverse of ``idioms._public_pointer_only``, built once per recognition.

    The predicate asks, per record, "does any public signature site name this
    type, and is every such use by pointer?" -- records x sites of work. These
    two maps answer it for every record from one pass over the sites.

    The inversion is exact clause by clause, which is why the two maps stay
    separate: ``by_short`` keys the short-name clause (a site's trailing ``::``
    segment plus each word of its normalised spelling, the set the predicate
    tests a queried short name against), ``by_exact`` keys the qualified clause
    (the site's raw and normalised spellings, tested against the queried *full*
    name). Each value is "some matching site used this by value", OR-ed across
    sites, so one by-value use defeats "only pointer" exactly as the site loop's
    ``only_pointer = False`` did. A type no site matches is in neither map and
    still answers ``(False, True)``.

    Values are strings and bools -- never ``Function``/``Param`` objects, never
    a per-record copy of the site list. Local to one recognition pass and
    dropped when it returns: never cached by ``id(graph)``, never attached to
    the mutable snapshot.
    """

    by_short: dict[str, bool]
    by_exact: dict[str, bool]


def build_public_use_index(functions: Iterable[Function]) -> PublicUseIndex:
    """One pass over every public signature site (see :class:`PublicUseIndex`).

    Takes the declarations rather than a ``SurfaceGraph`` so this stays a
    ``policy`` leaf over ``model`` types, with no dependency on the graph.
    """
    by_short: dict[str, bool] = {}
    by_exact: dict[str, bool] = {}
    for fn in functions:
        # Use the function's own visibility, not demangled-name membership in
        # public_roots(): a *hidden* C++ overload sharing a public overload's
        # name must not contribute its by-value parameter as "public" evidence.
        if not in_public_surface(fn):
            continue
        sites: list[tuple[str, int]] = [(fn.return_type, fn.return_pointer_depth)]
        for p in fn.params:
            sites.append((getattr(p, "type", "") or "", getattr(p, "pointer_depth", 0)))
        for type_str, depth in sites:
            # One normalisation per *site*: ``_strip_ptr`` is pure, so a second
            # call could only ever return the same value.
            stripped = strip_ptr(type_str)
            # The by-value test is the predicate's, unchanged -- pointer depth
            # *and* a literal ``*`` in the spelling. A reference spells ``&``,
            # carries no ``*`` and usually no depth, so it counted as by-value
            # before and still does; this is not the place to reinterpret that.
            by_value = depth < 1 and not _is_pointer(type_str)
            for key in {type_str.rsplit("::", 1)[-1]} | set(stripped.split()):
                by_short[key] = by_short.get(key, False) or by_value
            for key in (type_str, stripped):
                by_exact[key] = by_exact.get(key, False) or by_value
    return PublicUseIndex(by_short, by_exact)


def query_public_use(index: PublicUseIndex, type_name: str) -> tuple[bool, bool]:
    """Answer ``(referenced_by_public, only_ever_by_pointer)`` from *index*."""
    referenced = False
    seen_by_value = False
    short = type_name.rsplit("::", 1)[-1]
    if short in index.by_short:
        referenced = True
        seen_by_value = index.by_short[short]
    if type_name in index.by_exact:
        referenced = True
        seen_by_value = seen_by_value or index.by_exact[type_name]
    return referenced, not seen_by_value
