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

"""The one place *declared in source* and *dynamically exported* are read apart.

``Visibility`` answers both questions with one enum member (``PUBLIC`` =
"declared in a parsed header **and** dynamically exported"), so every
``visibility == Visibility.PUBLIC`` guard in this codebase historically
answered whichever of the two its author happened to mean -- and answered
the other one by accident. The observable consequence is recorded in
``docs/contribute/known-gaps.md``: a public inline method that is still
declared in a byte-identical header, but stopped being dynamically
exported, dropped out of ``diff_namespaces``' source-surface index and was
reported as a **source** removal. It had not been removed from anywhere.

``Function.declared_fact``/``exported_fact`` (and ``Variable``'s) carry the
two facts separately. This module owns how a consumer reads them:

* :func:`declared_in_source` / :func:`dynamically_exported` -- the two raw
  ternary answers. ``UNKNOWN`` is a first-class result; absent evidence for
  one fact is *never* a negative answer for it (AGENTS.md, "weaker evidence
  narrows conclusions").
* :func:`in_source_surface` -- the source-axis membership predicate a
  "collect the public source surface" pass wants. Evidence first; when
  there is no evidence it falls back to the legacy ``Visibility`` proxy, so
  a pre-v46 snapshot keeps producing bit-for-bit identical findings.
* :func:`in_exported_public_api` -- the *conjunction* ``Visibility.PUBLIC``
  has always spelled: declared **and** exported. Every guard that genuinely
  meant that says so by calling this, instead of leaving a reader to guess
  which half its author cared about. Behaviourally identical to the legacy
  comparison; the point is that the question is now stated.
* :func:`source_removal_supported` -- the removal guard. An old-side member
  that is missing from a new-side *index* is only a source removal if the
  new side does not still declare it anywhere. This is the rule that stops an
  export-emission change from manufacturing a source removal, and it is
  deliberately evidence-positive: only a ``YES`` on the new side suppresses.
* :func:`export_only_loss` -- its complement, the export-axis event: still
  declared, no longer exported. Both sides must carry real evidence, so a
  mixed-version comparison (one side pre-v46) never invents one.

Leaf module: imports only ``fact``/``availability``/``vocabulary``, so
``compare``/``policy``/``report`` and the flat ``diff_*`` family can all
depend on it.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from .availability import FactStatus
from .fact import Fact
from .vocabulary import ScopeOrigin, Visibility

__all__ = [
    "NON_PUBLIC_SOURCE_ORIGINS",
    "SurfaceAnswer",
    "declared_in_source",
    "dynamically_exported",
    "export_only_loss",
    "in_exported_public_api",
    "in_exported_public_header_api",
    "in_source_surface",
    "source_removal_supported",
]


class SurfaceAnswer(Enum):
    """A three-valued answer. ``UNKNOWN`` is not ``NO``."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


#: Origins whose declarations are not part of the *public* source surface.
#: ``EXPORT_ONLY`` is deliberately absent: it is not a source declaration at
#: all, and :func:`declared_in_source` already answers ``NO``/``UNKNOWN``
#: for such an entry, so listing it here would be dead weight that reads as
#: if a declared entity could carry it.
NON_PUBLIC_SOURCE_ORIGINS = frozenset(
    {
        ScopeOrigin.PRIVATE_HEADER,
        ScopeOrigin.SYSTEM_HEADER,
        ScopeOrigin.GENERATED,
    }
)


class _SurfaceDecl(Protocol):
    """The structural subset of ``Function``/``Variable`` this module reads."""

    visibility: Visibility
    origin: ScopeOrigin
    declared_fact: Fact[bool] | None
    exported_fact: Fact[bool] | None


def _answer(fact: Fact[bool] | None) -> SurfaceAnswer:
    """A ``Fact[bool]`` as a ternary, with no implicit truthiness.

    Only ``PRESENT``/``PARTIAL`` with a real boolean value is an answer.
    ``NOT_COLLECTED``/``UNSUPPORTED``/``FAILED``/``NOT_APPLICABLE`` -- and a
    ``PRESENT`` carrying ``None``, which no producer should emit for a
    ``Fact[bool]`` but which a hand-built or round-tripped instance can --
    are ``UNKNOWN``.
    """
    if fact is None:
        return SurfaceAnswer.UNKNOWN
    if fact.status not in (FactStatus.PRESENT, FactStatus.PARTIAL):
        return SurfaceAnswer.UNKNOWN
    if fact.value is None:
        return SurfaceAnswer.UNKNOWN
    return SurfaceAnswer.YES if fact.value else SurfaceAnswer.NO


def declared_in_source(decl: _SurfaceDecl) -> SurfaceAnswer:
    """Is this declaration present in the source surface a producer parsed?

    Reads ``declared_fact`` and nothing else -- in particular it never
    consults ``exported_fact`` or ``visibility``, so the two axes cannot
    leak into each other here.
    """
    return _answer(decl.declared_fact)


def dynamically_exported(decl: _SurfaceDecl) -> SurfaceAnswer:
    """Is this declaration available as a dynamic export of the artifact?

    Reads ``exported_fact`` and nothing else, for the same reason
    :func:`declared_in_source` reads only its own fact.
    """
    return _answer(decl.exported_fact)


def in_source_surface(decl: _SurfaceDecl) -> bool:
    """Whether *decl* belongs to the **public source** surface.

    The replacement for ``decl.visibility == Visibility.PUBLIC`` at every
    call site that meant "is this declared in the public API", rather than
    "does the binary export this".

    Evidence first:

    * declared ``YES`` -> a member, unless its own ``origin`` places its
      defining header outside the public set (``-H``/``--header``). Export
      status is deliberately not consulted: an inline member that the
      compiler never emitted a dynamic symbol for is still declared API.
    * declared ``NO`` -> not a member. An export-table-only entry has no
      source declaration to be part of the source surface.
    * declared ``UNKNOWN`` -> the legacy ``Visibility`` proxy, unchanged.
      This is what keeps every pre-v46 snapshot's findings identical: no
      producer wrote the fact, so nothing about the decision changes.
    """
    answer = declared_in_source(decl)
    if answer is SurfaceAnswer.YES:
        return decl.origin not in NON_PUBLIC_SOURCE_ORIGINS
    if answer is SurfaceAnswer.NO:
        return False
    return decl.visibility == Visibility.PUBLIC


def in_exported_public_api(decl: _SurfaceDecl) -> bool:
    """Whether *decl* is both declared in the public source surface **and**
    live in the artifact's dynamic export table.

    The replacement for ``decl.visibility == Visibility.PUBLIC`` at every
    call site that really did want both halves -- which is most of them:
    under the near-universal ``-fvisibility=hidden`` build convention, a
    header declaration that is *not* exported is usually an internal helper
    that happens to live in a header, so "declared" alone over-selects
    badly as a stand-in for "public API". Requiring the export conjunct is
    the right call there; what was wrong before was that it was implicit.

    Deliberately identical in behaviour to the legacy comparison, on every
    snapshot: with evidence, a header-AST declaration's ``exported_fact`` is
    derived from the very ``Visibility`` member this used to read
    (``extract/declaration_surface_stamp.py``); without it, the legacy
    comparison is what runs. Use :func:`in_source_surface` instead wherever
    the question is about the declaration alone -- above all where the
    answer decides whether something counts as *removed*.
    """
    declared = declared_in_source(decl)
    exported = dynamically_exported(decl)
    if declared is SurfaceAnswer.YES and exported is SurfaceAnswer.YES:
        return True
    if declared is SurfaceAnswer.NO or exported is SurfaceAnswer.NO:
        return False
    return decl.visibility == Visibility.PUBLIC


def in_exported_public_header_api(decl: _SurfaceDecl) -> bool:
    """:func:`in_exported_public_api`, additionally requiring the
    declaration's defining header to be inside the public set.

    The conjunction ``dumper_scoping``'s public-root selection wants. The
    origin half is the same ``NON_PUBLIC_SOURCE_ORIGINS`` set
    :func:`in_source_surface` applies, so the two predicates cannot drift
    on what "public header" means -- which is exactly what a second,
    open-coded ``origin not in ...`` beside each call would invite (the
    same set is already hand-copied in two flat modules).
    """
    return (
        in_exported_public_api(decl)
        and decl.origin not in NON_PUBLIC_SOURCE_ORIGINS
    )


def source_removal_supported(new: _SurfaceDecl | None) -> bool:
    """May an old-side member absent from a new-side index be called *removed*?

    *new* is the matching new-side declaration when one exists at all (in
    any state -- exported or not, public or not), or ``None`` when the new
    snapshot carries no such declaration. The old side is deliberately not
    a parameter: whether the removal is *supported* is entirely a question
    about what the new side still declares.

    ``False`` for exactly one case: the new side still positively declares
    it (``declared_fact`` PRESENT/True). That is not a source removal, it is
    an export change, and :func:`export_only_loss` is where it goes instead.

    Evidence-positive by construction -- ``UNKNOWN`` on the new side leaves
    the answer ``True``, i.e. today's behavior. A run must not *manufacture*
    a removal from absent evidence, and it equally must not *suppress* a
    real one from absent evidence; only a positive "still declared" reading
    can do that.
    """
    if new is None:
        return True
    return declared_in_source(new) is not SurfaceAnswer.YES


def export_only_loss(old: _SurfaceDecl, new: _SurfaceDecl | None) -> bool:
    """The export-axis event: still declared, no longer dynamically exported.

    Requires positive evidence on **both** sides -- exported ``YES`` then,
    exported ``NO`` now, and still declared ``YES`` now. Anything unknown
    yields ``False``, so a pre-v46 side (or a snapshot whose export table
    was never captured) can never produce one of these.
    """
    if new is None:
        return False
    return (
        dynamically_exported(old) is SurfaceAnswer.YES
        and dynamically_exported(new) is SurfaceAnswer.NO
        and declared_in_source(new) is SurfaceAnswer.YES
    )
