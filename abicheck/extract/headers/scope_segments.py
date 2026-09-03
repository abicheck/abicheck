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
    EntityId,
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
    "entity_is_record_member",
    "flat_names",
    "namespace_segment",
    "record_segment",
    "strip_record_scopes",
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

    ``version_tag`` is deliberately left empty by this slice -- still true,
    but no longer for the reason first written here. This repository has
    exactly one definition of "what an inline-namespace version tag is" —
    :func:`abicheck.model.qualified_name_split.version_suffix`, the signal
    ADR-025's versioned-inline-namespace-alias handling
    (``diff_namespaces.detect_inline_namespace_version_bump``) already keys
    on. It originally lived in the ``compare``-layer
    ``qualified_name_segments`` module, which ``extract`` may not import
    under ADR-061's dependency direction (confirmed by
    ``scripts/check_architecture.py`` failing on exactly that edge) — that
    barrier is what this docstring used to cite. A second, independent
    ``extract``-adjacent need for the identical recognition --
    ``pdb_metadata._is_user_visible``, PDB's own struct/enum visibility
    filter, which feeds ``extract/pdb_scope.py``'s ``ScopePath``
    construction -- motivated moving :func:`version_suffix` (and
    its sibling :func:`~abicheck.model.qualified_name_split.
    is_inline_abi_namespace_segment`) down into ``model/qualified_name_split.py``
    (Codex review, PR #1025) — see that module's own docstring — so the
    signal now genuinely lives somewhere ``extract`` may read. Wiring it up
    here is nonetheless left for separate follow-up, not attempted as a
    drive-by alongside that move: no consumer of
    :class:`~abicheck.model.identity.InlineNamespace`'s ``version_tag`` field
    from a header-AST producer exists yet, and no discriminating power is
    lost meanwhile — ``InlineNamespace`` is identity on ``name`` too, so
    ``v1`` and ``v2`` are already distinct segments regardless of
    ``version_tag``.

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


def strip_record_scopes(path: ScopePath) -> ScopePath:
    """*path* with every record-nesting segment removed.

    A hidden friend (a friend function/function-template with no prior
    declaration reachable by ordinary lookup) is, per [namespace.memdef],
    injected as a member of the *nearest enclosing namespace* -- not of the
    befriending class it is lexically written inside. Both header-AST
    backends' own scope-building walk is lexical, though: it pushes a
    :class:`~abicheck.model.identity.Record` segment for the enclosing
    class regardless of whether a nested declaration is a genuine member or
    a hidden friend, so a hidden friend's raw ``scope_path`` wrongly still
    names the class.

    Confirmed by direct compilation (Codex review, PR #943): clang rejects
    ``struct A { template<class T> friend void f(T) {} }; struct B {
    template<class T> friend void f(T) {} };`` as a *redefinition* of `f`
    -- proof the two hidden friend templates are the same entity in the
    nearest enclosing namespace (here, the global namespace), not two
    distinct class-scoped ones -- yet the clang backend's own lexical walk
    had given them ``scope_path``s of ``(Record("A"),)`` and
    ``(Record("B"),)`` respectively, an ``EntityId`` collision in the
    opposite direction from every other identity fix in this module: two
    genuinely identical declarations wrongly compared *unequal*, rather
    than two distinct ones wrongly compared equal.

    Every :class:`~abicheck.model.identity.Namespace`,
    :class:`~abicheck.model.identity.InlineNamespace`, and
    :class:`~abicheck.model.identity.LocalToFunction` segment is kept
    unchanged, along with an :class:`~abicheck.model.identity.Anonymous`
    segment whose ``kind`` is an anonymous *namespace* -- none of those name
    a class, so none block a hidden friend's namespace injection. Only
    :class:`~abicheck.model.identity.Record` and a record-kind
    :class:`~abicheck.model.identity.Anonymous` (an anonymous
    struct/class/union) are dropped, and every one of them is, deliberately
    -- a friend hidden inside nested classes is injected past *all* of
    them, not just the innermost.

    This is the caller's decision to make, not this function's: only the
    ``EntityId`` computation for a hidden friend should see the stripped
    path (mirroring castxml's own ``befriending``-attribute resolution,
    which already places a hidden friend's ``context`` at the enclosing
    namespace and records the befriending class separately) -- a hidden
    friend's *display* qualified name and its recorded
    ``hidden_friend_owner`` still name the befriending class, unaffected by
    this helper.

    >>> strip_record_scopes((Namespace("ns"), Record("A"), Record("B")))
    (Namespace(name='ns'),)
    >>> strip_record_scopes((Record("A"), Anonymous("struct", 0)))
    ()
    >>> strip_record_scopes((Anonymous("namespace", 0), Record("A")))
    (Anonymous(kind='namespace', ordinal=0),)
    """
    return tuple(
        seg
        for seg in path
        if not (
            isinstance(seg, Record)
            or (isinstance(seg, Anonymous) and seg.kind != ANONYMOUS_NAMESPACE)
        )
    )


def entity_is_record_member(entity_id: EntityId | None) -> bool:
    """Whether *entity_id*'s own innermost scope segment is a
    :class:`~abicheck.model.identity.Record` -- i.e. this declaration is a
    class/struct/union MEMBER, not a file- or namespace-scope one (Codex
    review, PR #1024, fresh evidence beyond the reasoning
    ``tu_merge._function_key``/``_variable_key`` already documented).

    Exists to close a real gap in the "mangled == name means genuinely
    unmangled" heuristic those two functions (and their
    ``manifest_semantic_ir`` mirrors) use to fall back to a raw
    ``is_static``/mangled-marker linkage check: clang's header AST also
    reports NO ``mangledName`` for a declaration belonging to an
    *uninstantiated* class template (a static data member or a method),
    since mangling a member requires a concrete instantiation that does
    not exist yet -- confirmed empirically (``template<class T> struct A {
    static int x; };`` parses with no ``mangledName`` for ``x`` at all, the
    identical shape ``_function_key``'s own docstring already documents
    for a template method). That fallback previously assumed "no mangling
    at all" could only mean a plain-C/``extern "C"`` declaration, wrongly
    treating an uninstantiated template member's own ``is_static``/
    non-static default as a genuine internal-linkage signal. A record
    member's own ``EntityId`` always carries the enclosing class in its
    ``scope`` (populated from the real AST scope walk regardless of
    whether mangling succeeded), so checking for a trailing
    :class:`~abicheck.model.identity.Record` segment there is a signal
    independent of mangled-name availability -- unlike ``is_static``,
    which the AST sets identically whether or not mangling could be
    computed.
    """
    return (
        entity_id is not None
        and bool(entity_id.scope)
        and isinstance(entity_id.scope[-1], Record)
    )
