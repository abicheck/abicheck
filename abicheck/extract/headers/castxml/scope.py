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

"""Typed scope segments from castxml's ``context`` chain (ADR-063 Phase 2).

castxml has no push-down AST walk of its own: an element names its
containing scope by id (``context="_7"``), and
:func:`~.location.qualified_name` recovers the flat spelling by walking that
chain and keeping each parent's bare ``name``. This module is that same walk
with the information ``qualified_name`` discards kept instead — each
parent's own XML tag (``<Namespace>`` vs. ``<Struct>``/``<Class>``/
``<Union>``) and its ``access`` attribute. The point a parent element is
resolved *is* castxml's equivalent of "the moment a scope is pushed"; it is
the only place the tag is still in hand.

Confirmed against real castxml 0.7 output (``--castxml-output=1``, C++17),
not inferred:

* only ``<Namespace>``, ``<Struct>``, ``<Class>`` and ``<Union>`` are ever
  referenced as another element's ``context`` (checked across a full
  ``<string>``/``<vector>``-including dump);
* an anonymous namespace and an anonymous union member each appear with no
  ``name`` attribute at all;
* a class-scope record carries an explicit ``access`` attribute; a
  namespace- or global-scope record carries none;
* the global scope is ``<Namespace id="_1" name="::">`` — matched by name,
  exactly as :func:`~.location.qualified_name` already does;
* **castxml does not emit inline namespaces at all.** A declaration inside
  ``inline namespace v1`` is attributed directly to the *enclosing* named
  namespace, and the ``v1`` element does not exist anywhere in the output
  (verified both on a hand-written header and on libstdc++'s own
  ``std::__cxx11``). So :class:`~abicheck.model.identity.InlineNamespace`
  is structurally unproducible from this backend — a documented backend
  capability difference, not an omission here. The flat spelling has the
  identical gap today, so nothing regresses.
* castxml emits no function-local types at all (a ``struct`` declared inside
  a function body is absent from the output entirely), so
  :class:`~abicheck.model.identity.LocalToFunction` is likewise
  unproducible here.
"""

from __future__ import annotations

from typing import Any

from ....model.identity import ScopePath, ScopeSegment
from ....name_classification import strip_anonymous_type_location
from ..scope_segments import (
    ANONYMOUS_NAMESPACE,
    NO_ACCESS,
    anonymous_segment,
    namespace_segment,
    record_segment,
)
from .context import CastxmlParserContext

__all__ = ["scope_path"]

#: XML tags that introduce a *record* nesting scope, mapped to the
#: ``Anonymous.kind`` spelling used when the element is unnamed. The
#: spellings match clang's own ``tagUsed`` values so one construct produces
#: one segment across both backends.
_RECORD_TAGS = {"Struct": "struct", "Class": "class", "Union": "union"}

#: castxml's spelling of the global scope, skipped exactly as
#: :func:`~.location.qualified_name` skips it.
_GLOBAL_SCOPE_NAME = "::"


def scope_path(ctx: CastxmlParserContext, el: Any) -> ScopePath:
    """The typed containing-scope path of *el*, outermost segment first.

    The structural counterpart of :func:`~.location.qualified_name`: the
    same ``context``-chain walk, with the same global-scope skip, the same
    :func:`~abicheck.name_classification.strip_anonymous_type_location`
    normalization of each parent's name, and the same cycle guard — so
    ``scope_segments.flat_names(scope_path(ctx, el))`` reproduces exactly
    the parent names ``qualified_name`` prepends to *el*'s own name.

    Names only the *containing* scope; *el* itself never contributes a
    segment.
    """
    segments: list[ScopeSegment] = []
    ctx_id = el.get("context", "")
    seen: set[str] = set()
    while ctx_id and ctx_id not in seen:
        seen.add(ctx_id)
        parent = ctx.id_map.get(ctx_id)
        if parent is None:
            break
        segment = _segment_for(ctx, parent)
        if segment is not None:
            segments.append(segment)
        ctx_id = parent.get("context", "")
    return tuple(reversed(segments))


def _segment_for(ctx: CastxmlParserContext, parent: Any) -> ScopeSegment | None:
    """The segment *parent* contributes as a containing scope, or ``None``.

    ``None`` for the global scope and for any tag that is not a scope this
    backend can classify — never a guessed segment, since a wrong segment
    kind is an identity collision rather than a missing fact.
    """
    tag = getattr(parent, "tag", "")
    name = strip_anonymous_type_location(parent.get("name", "") or "")
    if name == _GLOBAL_SCOPE_NAME:
        return None
    if name:
        if tag == "Namespace":
            # No is_inline: castxml never emits an inline namespace element
            # (see this module's docstring).
            return namespace_segment(name)
        if tag in _RECORD_TAGS:
            return record_segment(name, access=parent.get("access", "") or NO_ACCESS)
        return None
    if tag == "Namespace":
        anon_kind = ANONYMOUS_NAMESPACE
    elif tag in _RECORD_TAGS:
        anon_kind = _RECORD_TAGS[tag]
    else:
        return None
    return anonymous_segment(anon_kind, _anonymous_ordinal(ctx, parent))


def _anonymous_ordinal(ctx: CastxmlParserContext, el: Any) -> int:
    """*el*'s position among the anonymous scopes sharing its own parent.

    A deterministic per-parent sequence, counted across *all* anonymous
    sibling scopes regardless of kind — the same rule
    :func:`~..scope_segments.anonymous_segment` documents, so the two
    backends number siblings identically.

    Sibling order comes from the parent's own ``members`` attribute, which
    is castxml's record of declaration order; when that is unavailable the
    document order of the id map (a plain insertion-ordered dict built in
    one pass over the XML) is used instead. Both are stable for one parse,
    which is all ``Anonymous.ordinal`` claims to be.
    """
    parent_id = el.get("context", "")
    own_id = el.get("id", "")
    parent = ctx.id_map.get(parent_id) if parent_id else None
    if parent is not None:
        members = (parent.get("members", "") or "").split()
        if members:
            ordinal = _index_among_anonymous(ctx, members, own_id)
            if ordinal is not None:
                return ordinal
    siblings = [
        eid
        for eid, sib in ctx.id_map.items()
        if (sib.get("context", "") or "") == parent_id
    ]
    ordinal = _index_among_anonymous(ctx, siblings, own_id)
    return 0 if ordinal is None else ordinal


def _index_among_anonymous(
    ctx: CastxmlParserContext, sibling_ids: list[str], own_id: str
) -> int | None:
    """*own_id*'s index among the anonymous scopes in *sibling_ids*, or ``None``.

    ``None`` when *own_id* is not in the list at all, so the caller can fall
    back rather than silently returning a wrong ``0``.
    """
    ordinal = 0
    for sid in sibling_ids:
        sibling = ctx.id_map.get(sid)
        if sibling is None:
            continue
        tag = getattr(sibling, "tag", "")
        if tag != "Namespace" and tag not in _RECORD_TAGS:
            continue
        if strip_anonymous_type_location(sibling.get("name", "") or ""):
            continue
        if sid == own_id:
            return ordinal
        ordinal += 1
    return None
