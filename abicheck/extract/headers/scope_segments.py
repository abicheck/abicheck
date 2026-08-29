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

"""Typed ``ScopeSegment`` construction shared by both header-AST backends.

ADR-063 Phase 2, second slice. ``dumper_clang.py``'s ``_walk`` and
``dumper_castxml.py``'s context-chain walk each know, *at the exact point a
containing scope is determined*, which AST node kind that scope is — a
clang ``NamespaceDecl`` vs. ``CXXRecordDecl``, a castxml ``<Namespace>`` vs.
``<Struct>``/``<Class>``/``<Union>`` — plus the kind-specific payload that
node already carries (a record's access specifier, an inline namespace's
version tag). The flat ``"::"``-joined spelling both backends build from
that walk discards every one of those distinctions; this module is where
they are recorded instead, as :mod:`abicheck.model.identity` segments.

**One construction point for both backends, deliberately.** Two producers
building segments independently is exactly how an identity fragments
(``ScopePath`` exists to *prevent* that class of collision, not to add a
new one) — so the mapping from "what the AST said" to "which segment type,
with which fields" lives here once, and each backend supplies only its own
node/element inspection.

Leaf module: depends on ``model`` (allowed: ``extract -> model``, ADR-061)
and on the dependency-free ``qualified_name_segments`` helper, nothing
above.
"""

from __future__ import annotations

from ...model.identity import (
    Anonymous,
    InlineNamespace,
    Namespace,
    Record,
    ScopePath,
    ScopeSegment,
)

__all__ = [
    "ANONYMOUS_KINDS",
    "ANONYMOUS_NAMESPACE",
    "NO_ACCESS",
    "RECORD_TAG_KINDS",
    "anonymous_segment",
    "flat_names",
    "namespace_segment",
    "record_segment",
]

#: ``Anonymous.kind`` for an unnamed namespace. Deliberately distinct from
#: every record tag below so an anonymous namespace and an anonymous struct
#: sharing one parent and one ordinal can never produce an equal segment.
ANONYMOUS_NAMESPACE = "namespace"

#: ``Anonymous.kind`` values for an unnamed record, spelled exactly as the
#: C++ tag is (clang's ``tagUsed``, castxml's lower-cased element tag).
RECORD_TAG_KINDS = frozenset({"struct", "class", "union"})

#: Every ``Anonymous.kind`` this codebase's producers may emit.
ANONYMOUS_KINDS = frozenset({ANONYMOUS_NAMESPACE}) | RECORD_TAG_KINDS

#: ``Record.access`` for a record whose containing scope is a namespace (or
#: the global scope), where C++ has no access specifier at all. Spelled
#: ``"public"`` rather than left empty to match what ``dumper_clang``'s own
#: ``_walk`` already threads as the access of a namespace-scope declaration
#: — one spelling for one fact, across both backends. ``Record.access`` is
#: non-identity payload (``field(compare=False)``), so this choice can never
#: affect whether two segments compare equal; it only affects what a
#: consumer reads off the segment.
NO_ACCESS = "public"


def namespace_segment(name: str, *, is_inline: bool = False) -> ScopeSegment:
    """A namespace scope segment for *name*.

    *is_inline* selects :class:`~abicheck.model.identity.InlineNamespace`
    over :class:`~abicheck.model.identity.Namespace`. The two are distinct
    segment types on purpose: an inline namespace is transparent to name
    lookup but *not* to mangling, which is exactly the distinction a flat
    ``"::"``-joined spelling cannot express.

    ``version_tag`` is deliberately left empty by this slice, and that is a
    scope boundary rather than an oversight. This repository has exactly one
    definition of "what an inline-namespace version tag is" —
    :func:`abicheck.qualified_name_segments.version_suffix`, the signal
    ADR-025's versioned-inline-namespace-alias handling
    (``diff_namespaces.detect_inline_namespace_version_bump``) already keys
    on — and that module belongs to the ``compare`` layer, which ``extract``
    may not import under ADR-061's dependency direction (confirmed by
    ``scripts/check_architecture.py`` failing on exactly that edge). The two
    ways to fill the field today would each be worse than leaving it empty:
    re-deriving the rule here creates a second, independently-drifting
    notion of a version tag, and relocating the existing one into ``model``
    is a real ``compare``-layer migration of its own, not a drive-by. No
    discriminating power is lost meanwhile —
    :class:`~abicheck.model.identity.InlineNamespace` is identity on
    ``name`` too, so ``v1`` and ``v2`` are already distinct segments; the
    tag is a convenience payload a later slice can populate once the signal
    lives somewhere ``extract`` may read.

    >>> namespace_segment("inner")
    Namespace(name='inner')
    >>> namespace_segment("v1", is_inline=True)
    InlineNamespace(name='v1', version_tag='')
    >>> namespace_segment("v1", is_inline=True) != namespace_segment("v2", is_inline=True)
    True
    """
    if not is_inline:
        return Namespace(name)
    return InlineNamespace(name)


def record_segment(name: str, *, access: str = NO_ACCESS) -> Record:
    """A record (class/struct/union) nesting-scope segment for *name*.

    *access* is the record's own access specifier within *its* parent, which
    is what the producer knows at the point this scope is entered. It is
    carried as payload, never as identity — see
    :class:`~abicheck.model.identity.Record`.

    >>> record_segment("B", access="private")
    Record(name='B', access='private')
    """
    return Record(name, access or NO_ACCESS)


def anonymous_segment(kind: str, ordinal: int) -> Anonymous:
    """An unnamed scope segment.

    *kind* must be one of :data:`ANONYMOUS_KINDS` — a producer passing an
    unrecognized spelling is a bug in that producer's own node inspection,
    not something to encode into an identity, so it raises rather than
    silently manufacturing a segment that would compare unequal to every
    other producer's spelling of the same construct.

    *ordinal* is a deterministic per-parent-scope sequence number, assigned
    across *all* anonymous siblings of one parent regardless of kind (so two
    siblings can never share one ordinal even before ``kind`` is consulted).
    Stable within one parse only — see
    :class:`~abicheck.model.identity.Anonymous` for that documented
    limitation.

    >>> anonymous_segment("union", 1)
    Anonymous(kind='union', ordinal=1)
    """
    if kind not in ANONYMOUS_KINDS:
        raise ValueError(f"unknown anonymous scope kind: {kind!r}")
    if ordinal < 0:
        raise ValueError(f"anonymous scope ordinal must be non-negative: {ordinal!r}")
    return Anonymous(kind, ordinal)


def flat_names(path: ScopePath) -> tuple[str, ...]:
    """The flat, ``"::"``-joinable names of *path*'s **named** segments.

    The parity primitive for this slice: both backends keep building their
    pre-existing flat scope spelling exactly as before, and this function is
    what lets a test assert the typed path did not silently drift from it.
    An :class:`~abicheck.model.identity.Anonymous` segment contributes
    nothing, matching both backends' existing flat spelling (neither ever
    emitted a name for an unnamed scope) — which is precisely the
    information the typed path adds and the flat one cannot carry. A
    :class:`~abicheck.model.identity.LocalToFunction` segment likewise
    contributes nothing: it has no name of its own at all (its owner is an
    ``EntityId``, not a spelling), and neither header-AST backend can
    produce one today — clang's walk stops at a function node and castxml
    emits no function-local declarations at all.

    >>> flat_names((Namespace("ns"), Anonymous("union", 0), Record("A")))
    ('ns', 'A')
    """
    return tuple(
        seg.name
        for seg in path
        if isinstance(seg, Namespace | Record | InlineNamespace)
    )
