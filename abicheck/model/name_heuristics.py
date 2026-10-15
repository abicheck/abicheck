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

"""The registration API for spelling-based classifiers (design-hardening
plan Phase 5, defect family F4 "structure over spelling").

A *name heuristic* decides something from a name's shape: a ``detail::``
segment, an ``*_MAX`` member, a ``*_reserved`` field, a ``::v2::`` inline
namespace. Every such classifier in a decision module registers here, and
its call sites consult it only through the handle registration returns.

The rule this module makes structural: **a name alone may only lower
confidence or route a finding to review.**

* :func:`register_name_heuristic` takes a :class:`NameHeuristicEffect`,
  which has exactly two members -- ``LOWER_CONFIDENCE`` and
  ``ROUTE_TO_REVIEW``. There is no way to spell "raise" through it.
* :func:`register_severity_raising_heuristic` is the only route to a
  heuristic that can raise a finding's severity, and it cannot be called
  without a :class:`StructuralFact` -- a named, callable structural check.
  Its handle has no ``matches()``: the only question it answers is
  :meth:`SeverityRaisingNameHeuristic.confirmed`, which is the name *and*
  the fact, so a call site cannot consult the name on its own.

The API lives in ``model`` because the detectors that register are in the
``compare`` layer, which may import only ``model`` (ADR-061). The catalogue
of registered heuristics, its validation and the repo gate are owned by
``policy/name_heuristics.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any

__all__ = [
    "LazyFactInput",
    "NameHeuristic",
    "NameHeuristicEffect",
    "SeverityRaisingNameHeuristic",
    "StructuralFact",
    "register_name_heuristic",
    "register_severity_raising_heuristic",
    "heuristic_callables",
    "registered_name_heuristics",
]

#: A structural fact's id: ``<layer>.<module>.<predicate>``, e.g.
#: ``compare.enum_sentinel.holds_enum_maximum``.
_FACT_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$")
_HEURISTIC_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class NameHeuristicEffect(Enum):
    """What a spelling-only classifier may do to a finding.

    Deliberately has no "raise" member: a heuristic that raises severity is
    a :class:`SeverityRaisingNameHeuristic` and must name its fact.
    """

    LOWER_CONFIDENCE = "lower_confidence"
    ROUTE_TO_REVIEW = "route_to_review"


#: The effect string a severity-raising heuristic reports.
RAISE_SEVERITY = "raise_severity"


class StructuralFact:
    """A named structural check that confirms or vetoes a name heuristic.

    *check* takes one argument -- whatever structural input the call site
    holds (a value map, a reachability path list, an export pair) -- and
    answers whether the structure supports the heuristic's conclusion.
    """

    __slots__ = ("check", "id")

    def __init__(self, id: str, check: Callable[[Any], object]) -> None:
        if not isinstance(id, str) or not _FACT_ID.match(id):
            raise ValueError(
                f"structural fact id {id!r} must be dotted <layer>.<module>.<predicate>"
            )
        if not callable(check):
            raise TypeError(f"structural fact {id!r}: check must be callable")
        self.id = id
        self.check = check

    def holds(self, fact_input: Any) -> bool:
        if isinstance(fact_input, LazyFactInput):
            fact_input = fact_input.value()
        return bool(self.check(fact_input))

    def __repr__(self) -> str:
        return f"StructuralFact({self.id!r})"


class LazyFactInput:
    """A fact input computed only if the name nominates the subject.

    Lets a call site keep the cheap spelling check first without consulting
    the name on its own: ``confirmed(name, LazyFactInput(build))`` evaluates
    *build* once, after the matcher, and :meth:`value` returns the cached
    result for the caller to reuse.
    """

    __slots__ = ("_build", "_done", "_value")

    def __init__(self, build: Callable[[], Any]) -> None:
        self._build = build
        self._done = False
        self._value: Any = None

    def value(self) -> Any:
        if not self._done:
            self._value = self._build()
            self._done = True
        return self._value


class _Registered:
    __slots__ = (
        "_matcher",
        "description",
        "helpers",
        "id",
        "owner",
        "patterns",
        "vocabularies",
    )

    def __init__(
        self,
        id: str,
        *,
        owner: str,
        description: str,
        matcher: Callable[..., object],
        helpers: tuple[Callable[..., object], ...],
        vocabularies: tuple[str, ...],
        patterns: tuple[re.Pattern[str], ...],
    ) -> None:
        if not _HEURISTIC_ID.match(id):
            raise ValueError(f"name heuristic id {id!r} must be snake_case")
        if not owner.startswith("abicheck."):
            raise ValueError(f"name heuristic {id!r}: owner must be a module name")
        if not description.strip():
            raise ValueError(f"name heuristic {id!r}: description is required")
        if not callable(matcher) or not all(callable(h) for h in helpers):
            raise TypeError(f"name heuristic {id!r}: matcher/helpers must be callable")
        if not all(isinstance(p, re.Pattern) for p in patterns):
            raise TypeError(f"name heuristic {id!r}: patterns must be compiled")
        self.id = id
        self.owner = owner
        self.description = description
        self._matcher = matcher
        self.helpers = helpers
        self.vocabularies = vocabularies
        self.patterns = patterns

    @property
    def effect_name(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.id!r}, owner={self.owner!r})"


def heuristic_callables(
    h: NameHeuristic | SeverityRaisingNameHeuristic,
) -> tuple[Callable[..., object], ...]:
    """The matcher and helpers that implement *h*'s spelling check.

    Introspection only -- for the catalogue and the H4 site resolver, which
    map source sites to the heuristic that owns them. Detector code must not
    call these: a severity-raising heuristic is consulted only through
    :meth:`SeverityRaisingNameHeuristic.confirmed`, and the gate
    (``tests/test_family_f4_heuristics.py``) fails on any other caller.
    """
    return (h._matcher, *h.helpers)


class NameHeuristic(_Registered):
    """A spelling-based classifier that may only lower or route to review."""

    __slots__ = ("confirmed_by", "effect", "lowers_from")

    def __init__(
        self,
        id: str,
        *,
        effect: NameHeuristicEffect,
        confirmed_by: StructuralFact | None,
        lowers_from: tuple[str, ...] = (),
        **kw: Any,
    ) -> None:
        if not isinstance(effect, NameHeuristicEffect):
            raise TypeError(
                f"name heuristic {id!r}: effect must be a NameHeuristicEffect "
                "(lower_confidence or route_to_review); a heuristic that raises "
                "severity registers through register_severity_raising_heuristic"
            )
        if confirmed_by is not None and not isinstance(confirmed_by, StructuralFact):
            raise TypeError(
                f"name heuristic {id!r}: confirmed_by must be a StructuralFact"
            )
        super().__init__(id, **kw)
        self.effect = effect
        self.confirmed_by = confirmed_by
        #: ``ChangeKind`` member names this heuristic may *demote*: the
        #: breaking kinds a call site emits when the name does not match.
        self.lowers_from = tuple(lowers_from)

    @property
    def effect_name(self) -> str:
        return self.effect.value

    def matches(self, subject: Any, /, **kw: Any) -> bool:
        """Whether *subject*'s spelling matches (the name evidence alone)."""
        return bool(self._matcher(subject, **kw))

    def apply(self, subject: Any, /, **kw: Any) -> Any:
        """The matcher's own result, for a heuristic that classifies or
        rewrites rather than answers yes/no."""
        return self._matcher(subject, **kw)

    def confirmed(self, subject: Any, fact_input: Any, /, **kw: Any) -> bool:
        """The name matches *and* the confirming structural fact holds."""
        if self.confirmed_by is None:
            raise TypeError(f"name heuristic {self.id!r} names no confirming fact")
        return self.matches(subject, **kw) and self.confirmed_by.holds(fact_input)


class SeverityRaisingNameHeuristic(_Registered):
    """A spelling-nominated classifier whose finding can raise severity.

    Answers only :meth:`confirmed`: the name nominates, :attr:`fact`
    decides. There is intentionally no name-only query.
    """

    __slots__ = ("fact", "raises")

    def __init__(
        self, id: str, *, fact: StructuralFact, raises: tuple[str, ...], **kw: Any
    ) -> None:
        if not isinstance(fact, StructuralFact):
            raise TypeError(
                f"severity-raising heuristic {id!r} must name a StructuralFact"
            )
        if not raises or not all(isinstance(k, str) and k for k in raises):
            raise ValueError(
                f"severity-raising heuristic {id!r} must name the ChangeKind(s) it raises"
            )
        super().__init__(id, **kw)
        self.fact = fact
        #: ``ChangeKind`` member names whose emission this heuristic gates.
        self.raises = tuple(raises)

    @property
    def effect_name(self) -> str:
        return RAISE_SEVERITY

    def confirmed(self, subject: Any, fact_input: Any, /, **kw: Any) -> bool:
        """The name nominates *subject* and the structural fact holds."""
        return bool(self._matcher(subject, **kw)) and self.fact.holds(fact_input)


_REGISTRY: dict[str, NameHeuristic | SeverityRaisingNameHeuristic] = {}


def _store(h: NameHeuristic | SeverityRaisingNameHeuristic) -> None:
    prior = _REGISTRY.get(h.id)
    if prior is not None and prior.owner != h.owner:
        raise ValueError(f"name heuristic {h.id!r} already registered by {prior.owner}")
    # Same owner re-registering (a module reload) replaces the entry.
    _REGISTRY[h.id] = h


def register_name_heuristic(
    id: str,
    *,
    owner: str,
    effect: NameHeuristicEffect,
    description: str,
    matcher: Callable[..., object],
    confirmed_by: StructuralFact | None = None,
    lowers_from: tuple[str, ...] = (),
    helpers: tuple[Callable[..., object], ...] = (),
    vocabularies: tuple[str, ...] = (),
    patterns: tuple[re.Pattern[str], ...] = (),
) -> NameHeuristic:
    """Register a classifier that may only lower confidence or route to
    review. *vocabularies*/*patterns*/*helpers* name the module constants,
    compiled patterns and helper functions that make up its spelling check.
    """
    h = NameHeuristic(
        id,
        owner=owner,
        effect=effect,
        confirmed_by=confirmed_by,
        lowers_from=lowers_from,
        description=description,
        matcher=matcher,
        helpers=helpers,
        vocabularies=vocabularies,
        patterns=patterns,
    )
    _store(h)
    return h


def register_severity_raising_heuristic(
    id: str,
    *,
    owner: str,
    fact: StructuralFact,
    raises: tuple[str, ...],
    description: str,
    matcher: Callable[..., object],
    helpers: tuple[Callable[..., object], ...] = (),
    vocabularies: tuple[str, ...] = (),
    patterns: tuple[re.Pattern[str], ...] = (),
) -> SeverityRaisingNameHeuristic:
    """Register a name-nominated classifier that can raise severity; *fact*
    is required and decides every use, and *raises* names the ``ChangeKind``
    members whose emission it gates (the H4 gate checks every such emission
    is reachable only through :meth:`SeverityRaisingNameHeuristic.confirmed`)."""
    h = SeverityRaisingNameHeuristic(
        id,
        owner=owner,
        fact=fact,
        raises=raises,
        description=description,
        matcher=matcher,
        helpers=helpers,
        vocabularies=vocabularies,
        patterns=patterns,
    )
    _store(h)
    return h


def registered_name_heuristics() -> Mapping[
    str, NameHeuristic | SeverityRaisingNameHeuristic
]:
    """Every heuristic registered so far (import-order dependent; use
    ``policy.name_heuristics.name_heuristic_registry`` for the complete set).
    """
    return dict(_REGISTRY)
