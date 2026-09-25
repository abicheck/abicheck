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

"""The one graph node identity for a declaration or type (evidence-entity-model
plan, Phase 1, invariant I1: "one ID per entity").

Every graph producer that names a declaration or a type resolves its node id
here: the L2 header graph (``buildsource/header_graph.py`` and its clang-AST
projection), the ADR-063 public-surface builder (``compare/surface_graph.py``),
the L4/L5 AST replay passes (through ``model.source_graph.
function_decl_identity``, which delegates to :func:`declaration_identity`),
and the L4 source-ABI fold (``buildsource/source_graph_build_source_abi.py``).
Two producers that see the same declaration therefore compute the same id.

**Join key.** A declaration's key is its *linker name* whenever a producer
observed one: the Itanium/MSVC mangled name, or for C linkage the plain symbol
(clang and castxml both report ``mangledName == name`` there). It is the one
identity signal every evidence layer carries -- L0 export tables, L1 DWARF
``DW_AT_linkage_name``, L2 castxml/clang, L4/L5 per-TU replay -- and it is
exactly the ``("mangled", ...)``/``("extern_c",)`` tier of the ADR-063
:class:`~abicheck.model.identity.EntityId` (a real mangling encodes the scope
and signature that ``EntityId`` carries structurally, so the two tiers are in
bijection). ``EntityId`` itself cannot be the key: only a header-AST producer
may construct one (``tests/test_entity_id_carrier.py``), and the AST replay
passes and the L4 source extractors never do. A declaration with no linker
name falls back to the source-qualified identity the AST/L4 producers already
use (``qualified_name#<signature hash>``), and only when that evidence exists.
A type's key is its qualified name, as the producer spelled it (an inline
namespace the producer observed is kept, never guessed -- G15).

**No merge without shared evidence.** Anything else -- a castxml synthetic
constructor/destructor placeholder (``__abicheck_ctor__ns::W(int)``,
``~ns::W``), an anonymous type with no spelling, a callable with neither a
linker name nor a signature -- is an explicit ``unresolved`` identity under
its own ``unresolved://`` prefix, keyed on the producer's own evidence. It
can never collide with a resolved node, and two different pieces of evidence
never share one. :class:`UnresolvedOccurrences` separates repeats of
*identical* evidence within one graph build.

**Constructors and destructors.** castxml records no mangling for either,
so a castxml dump carries the placeholder above. When the snapshot's export
table pairs a placeholder one-to-one with an exported Itanium variant family,
the placeholder resolves to the complete-object spelling (``C1``/``D1`` --
what clang reports) and the family's other observed variants (``C2``,
``D0``/``D2``) become its aliases; a real ctor/dtor linker name gains the same
aliases. The rule and its refusals live in :mod:`.special_member_identity`;
a spelling another declaration already owns is refused here.

**Aliases.** A second spelling of a proven-same entity (today: the Mach-O
linker decoration of an Itanium or C-linkage name) is returned in
:attr:`GraphEntityIdentity.aliases`; producers record it through
``SourceGraphSummary.add_identity_alias``, which redirects any later use of
the spelling onto the canonical node rather than minting a second one.

Function/variable identity for PDB/BTF/CTF-only evidence is out of scope
(ADR-063 Phase 6): such a record has no linker name here and resolves as
``unresolved``, never as an invented identity.
"""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from .graph_identity import (
    _UNRESOLVED_PREFIX,
    _decl_node_id,
    _normalize_graph_identity,
    _type_node_id,
)
from .identity import _packed
from .mangled_name import strip_macho_itanium_decoration
from .special_member_identity import (
    resolve_special_member_linker_names,
    special_member_variant_aliases,
)

if TYPE_CHECKING:
    from .declarations import Function, Variable
    from .entities import EnumType, RecordType
    from .graph_facts import SurfaceGraphLike

__all__ = [
    "UNRESOLVED_PREFIX",
    "GraphEntityIdentity",
    "IdentityState",
    "UnresolvedOccurrences",
    "declaration_identity",
    "declaration_key",
    "endpoint_key",
    "identity_for_enum",
    "identity_for_function",
    "identity_for_record",
    "identity_for_typedef",
    "identity_for_variable",
    "is_linker_name",
    "is_unresolved_node_id",
    "register_identity_alias",
    "SnapshotIdentities",
    "signature_key",
    "snapshot_identities",
    "type_identity",
    "unresolved_identity",
]

#: Prefix of every node id this module mints for an entity with no
#: resolvable identity. Disjoint from ``decl://``/``type://`` by construction.
UNRESOLVED_PREFIX = _UNRESOLVED_PREFIX

#: The attrs key a producer stamps an ``unresolved`` node with, so a reader
#: can tell it apart without parsing the id.
IDENTITY_STATE_ATTR = "identity"


class IdentityState(str, enum.Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class GraphEntityIdentity:
    """The resolved graph identity of one declaration or type occurrence."""

    node_id: str
    state: IdentityState
    #: Node ids of other spellings proven to name this same entity. Never
    #: contains :attr:`node_id` itself.
    aliases: tuple[str, ...] = field(default=())

    @property
    def resolved(self) -> bool:
        return self.state is IdentityState.RESOLVED


def is_unresolved_node_id(node_id: str) -> bool:
    return node_id.startswith(UNRESOLVED_PREFIX)


def is_linker_name(spelling: str | None) -> bool:
    """Whether *spelling* can be a real linker symbol.

    Structural, not a demangling check: no Itanium, MSVC or C-linkage symbol
    contains whitespace, ``::`` or a parenthesis, or starts with ``~``. The
    castxml placeholders for constructors (``__abicheck_ctor__ns::W(int)``)
    and destructors (``~ns::W``) always do, so they are never taken for a
    linker name.
    """
    if not spelling:
        return False
    if spelling.startswith("~"):
        return False
    return not any(ch in spelling for ch in " \t\n():")


def _canonical_linker_name(linker_name: str, plain_name: str) -> tuple[str, str]:
    """``(canonical, decorated)`` -- the Mach-O decoration stripped when it is
    provably a decoration, with the original spelling returned as the second
    element (``""`` when nothing was stripped)."""
    stripped = strip_macho_itanium_decoration(linker_name)
    if stripped != linker_name:
        return stripped, linker_name
    # C linkage on Mach-O: the symbol is the plain name plus one leading
    # underscore. Only claimed when the producer handed us the plain name and
    # it is a bare identifier (a C++ name is never a bare identifier with a
    # `_`-prefixed linker spelling -- it would be mangled).
    if (
        plain_name
        and linker_name == f"_{plain_name}"
        and "::" not in plain_name
        and plain_name.isidentifier()
    ):
        return plain_name, linker_name
    return linker_name, ""


def signature_key(type_spelling: str) -> str:
    """The signature discriminator the L4 source extractors and the AST
    replay passes already use (``"sha256:" + sha256("sig\\0" + qualType)``),
    so a declaration walked by either producer resolves identically."""
    return "sha256:" + hashlib.sha256(f"sig\x00{type_spelling}".encode()).hexdigest()


def unresolved_identity(kind: str, *evidence: str) -> GraphEntityIdentity:
    """An explicit ``unresolved`` identity keyed on the producer's own
    evidence. Distinct evidence always yields a distinct id (the parts are
    length-prefixed, so no two part lists pack to the same string)."""
    parts = tuple(_normalize_graph_identity(p) for p in evidence)
    return GraphEntityIdentity(
        f"{UNRESOLVED_PREFIX}{kind}/{_packed(*parts)}", IdentityState.UNRESOLVED
    )


def declaration_key(
    *,
    linker_name: str = "",
    plain_name: str = "",
    qualified_name: str = "",
    signature: str = "",
    callable: bool = True,
) -> tuple[str, str]:
    """``(key, decorated)`` -- the un-prefixed resolved key of a declaration
    (``""`` when it has none) and the Mach-O-decorated linker spelling it was
    derived from (``""`` when nothing was stripped). The string form the AST
    replay passes thread through their edge tuples before
    ``_decl_node_id`` prefixes it; :func:`declaration_identity` is the same
    decision as a node identity."""
    if is_linker_name(linker_name):
        return _canonical_linker_name(linker_name, plain_name)
    if qualified_name and (signature or not callable):
        return (f"{qualified_name}#{signature}" if signature else qualified_name), ""
    return "", ""


def declaration_identity(
    *,
    linker_name: str = "",
    plain_name: str = "",
    qualified_name: str = "",
    signature: str = "",
    callable: bool = True,
) -> GraphEntityIdentity:
    """The node identity of a function/variable/constant declaration.

    1. A real linker name -> ``decl://<linker name>`` (Mach-O decoration
       stripped, the decorated spelling returned as an alias).
    2. No linker name, but a qualified name and -- for a *callable*, which
       C++ can overload -- a signature -> ``decl://<qualified>#<signature>``
       (``decl://<qualified>`` for a non-callable, which cannot be
       overloaded). This is the source-qualified identity the AST/L4
       producers already use for an unmangled declaration.
    3. Anything else -> ``unresolved``, keyed on whatever was supplied.
    """
    key, decorated = declaration_key(
        linker_name=linker_name,
        plain_name=plain_name,
        qualified_name=qualified_name,
        signature=signature,
        callable=callable,
    )
    if key:
        return GraphEntityIdentity(
            _decl_node_id(key),
            IdentityState.RESOLVED,
            (_decl_node_id(decorated),) if decorated else (),
        )
    return unresolved_identity(
        "decl", linker_name, plain_name, qualified_name, signature
    )


def type_identity(
    qualified_name: str, *, discriminator: str = ""
) -> GraphEntityIdentity:
    """The node identity of a record/enum/typedef: ``type://<qualified>``.

    *discriminator* separates two entities that legitimately share one
    qualified spelling (C's tag namespace: ``struct stat`` next to an
    unrelated ``typedef ... stat``). An empty name is ``unresolved``.
    """
    if not qualified_name:
        return unresolved_identity("type", discriminator)
    key = f"{qualified_name}#{discriminator}" if discriminator else qualified_name
    return GraphEntityIdentity(_type_node_id(key), IdentityState.RESOLVED)


def endpoint_key(identity: GraphEntityIdentity) -> str:
    """The string a producer threads through an edge tuple for *identity*,
    which ``_decl_node_id``/``_type_node_id`` turn back into exactly
    :attr:`GraphEntityIdentity.node_id`: the un-prefixed key of a resolved
    identity, the whole id of an unresolved one (passed through unchanged)."""
    if not identity.resolved:
        return identity.node_id
    return identity.node_id.split("://", 1)[1]


def register_identity_alias(
    graph: SurfaceGraphLike, alias: str, canonical: str
) -> bool:
    """Record *alias* -> *canonical* on *graph* unless the spelling already
    names something else there (a node of its own, or an alias of another
    entity). Such a spelling is ambiguous evidence, so it joins neither
    entity and the build carries on; returns whether the alias was recorded.
    The one registration path every producer uses, so none can abort a graph
    build or merge two entities on a colliding spelling."""
    try:
        graph.add_identity_alias(alias, canonical)
    except ValueError:
        return False
    return True


class UnresolvedOccurrences:
    """Keeps repeated, *identical* unresolved evidence apart within one graph
    build: the n-th repeat (n >= 2) gets ``#<n>``. Resolved identities pass
    through untouched. Which of several identical occurrences gets which
    ordinal is immaterial -- they are indistinguishable by construction --
    so the resulting id *set* does not depend on input order."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def allocate(self, identity: GraphEntityIdentity) -> GraphEntityIdentity:
        if identity.resolved:
            return identity
        n = self._seen.get(identity.node_id, 0) + 1
        self._seen[identity.node_id] = n
        if n == 1:
            return identity
        return GraphEntityIdentity(f"{identity.node_id}#{n}", identity.state)


# ---------------------------------------------------------------------------
# Adapters for the L2 model records. Every graph producer that walks an
# ``AbiSnapshot`` goes through these, never through its own field choice.
# ---------------------------------------------------------------------------


def _entity_key(entity_id: object) -> str:
    key = getattr(entity_id, "key", "")
    return key if isinstance(key, str) else ""


def _unresolved_declaration(
    spelling: str, name: str, entity_id: object
) -> GraphEntityIdentity:
    """A snapshot declaration with no linker name, keyed on the least
    evidence that still tells it apart. A producer's own non-linker spelling
    (castxml's ``__abicheck_ctor__ns::W(int)``/``~ns::W`` placeholder) already
    carries the qualified name and signature, so it alone is the key -- the
    ``EntityId`` key would only repeat it (measured on oneDAL: the repeat made
    the persisted graph section ~9% larger). With no such spelling, the name
    plus the ``EntityId`` key (which carries the scope and signature tag) is
    the evidence."""
    if spelling:
        return unresolved_identity("decl", spelling)
    return unresolved_identity("decl", name, _entity_key(entity_id))


def identity_for_function(fn: Function) -> GraphEntityIdentity:
    """A snapshot function. No signature spelling is available at L2 in the
    form the AST/L4 producers hash, so an unmangled callable is
    ``unresolved`` rather than collapsed onto its bare name."""
    ident = declaration_identity(linker_name=fn.mangled, plain_name=fn.name)
    if ident.resolved:
        return ident
    return _unresolved_declaration(fn.mangled, fn.name, fn.entity_id)


def identity_for_variable(var: Variable) -> GraphEntityIdentity:
    """A snapshot variable. A variable cannot be overloaded, but a bare name
    is not a qualified one either, so with no linker name it is
    ``unresolved`` too."""
    ident = declaration_identity(linker_name=var.mangled, plain_name=var.name)
    if ident.resolved:
        return ident
    return _unresolved_declaration(var.mangled, var.name, var.entity_id)


def _type_name(qualified_name: str | None, name: str) -> str:
    return qualified_name or name


def identity_for_record(rec: RecordType) -> GraphEntityIdentity:
    name = _type_name(rec.qualified_name, rec.name)
    if name:
        return type_identity(name)
    return unresolved_identity(
        "type", _entity_key(rec.entity_id), rec.source_header or ""
    )


def identity_for_enum(en: EnumType) -> GraphEntityIdentity:
    name = _type_name(en.qualified_name, en.name)
    if name:
        return type_identity(name)
    return unresolved_identity(
        "type", _entity_key(en.entity_id), en.source_header or ""
    )


_ELABORATED = ("struct ", "class ", "union ", "enum ")


def _strip_elaborated(spelling: str) -> str:
    s = spelling.strip()
    for kw in _ELABORATED:
        if s.startswith(kw):
            return s[len(kw) :].strip()
    return s


def identity_for_typedef(
    alias: str, target: str, tag_names: Collection[str]
) -> GraphEntityIdentity:
    """A typedef shares the ``type://`` space with records and enums -- the
    AST replay passes key a typedef'd name the same way. The one exception
    is C's separate tag namespace: when a record/enum of the same name exists
    and the typedef does *not* alias it (``typedef struct Foo Foo;`` does,
    and is then one node), the typedef gets its own discriminated id rather
    than merging with an unrelated tag."""
    if alias in tag_names and _strip_elaborated(target) != alias:
        return type_identity(alias, discriminator="typedef")
    return type_identity(alias)


class SnapshotLike(Protocol):
    """The parts of an ``AbiSnapshot`` the identity table reads -- structural,
    so this leaf module never imports ``model.snapshot`` (which sits on the
    ``buildsource.pack -> model.source_graph`` import path)."""

    functions: list[Function]
    variables: list[Variable]
    types: list[RecordType]
    enums: list[EnumType]
    typedefs: dict[str, str]


@dataclass(frozen=True)
class SnapshotIdentities:
    """Every declaration/type identity of one ``AbiSnapshot``, computed once.

    The header graph and the public-surface builder both read their node ids
    from this table, so the two producers cannot drift apart -- including on
    the ordinal an ``unresolved`` repeat receives. Tuples run parallel to the
    snapshot's own lists; ``typedefs`` is keyed by alias."""

    functions: tuple[GraphEntityIdentity, ...]
    variables: tuple[GraphEntityIdentity, ...]
    records: tuple[GraphEntityIdentity, ...]
    enums: tuple[GraphEntityIdentity, ...]
    typedefs: dict[str, GraphEntityIdentity]


def _without_aliases_in(
    ident: GraphEntityIdentity, taken: set[str]
) -> GraphEntityIdentity:
    kept = tuple(a for a in ident.aliases if a not in taken)
    if kept == ident.aliases:
        return ident
    return GraphEntityIdentity(ident.node_id, ident.state, kept)


def _with_variant_aliases(
    ident: GraphEntityIdentity, linker_names: Collection[str]
) -> GraphEntityIdentity:
    if not linker_names:
        return ident
    extra = tuple(_decl_node_id(n) for n in linker_names)
    kept = tuple(a for a in extra if a != ident.node_id and a not in ident.aliases)
    if not kept:
        return ident
    return GraphEntityIdentity(ident.node_id, ident.state, (*ident.aliases, *kept))


def _function_identities(
    snap: SnapshotLike, exports: frozenset[str]
) -> list[GraphEntityIdentity]:
    """Each function's identity, with the ctor/dtor variant rule
    (:mod:`.special_member_identity`) applied: a castxml placeholder the
    export table pairs one-to-one resolves to its complete-object spelling,
    and a real ctor/dtor linker name gains its observed sibling variants as
    aliases. A resolution whose spelling another declaration already owns
    is refused -- the placeholder stays ``unresolved``."""
    idents = [identity_for_function(f) for f in snap.functions]
    if not exports:
        return idents
    resolved = resolve_special_member_linker_names(
        snap.functions, exports, getattr(snap, "typedefs_qualified", None) or {}
    )
    owned = {i.node_id for i in idents if i.resolved}
    for idx, names in resolved.items():
        node = _decl_node_id(names.canonical)
        if node in owned:
            continue
        idents[idx] = _with_variant_aliases(
            GraphEntityIdentity(node, IdentityState.RESOLVED), names.variants
        )
    for idx, (fn, ident) in enumerate(zip(snap.functions, idents)):
        if idx not in resolved and ident.resolved and is_linker_name(fn.mangled):
            idents[idx] = _with_variant_aliases(
                ident, special_member_variant_aliases(fn.mangled, exports)
            )
    return idents


def snapshot_identities(
    snap: SnapshotLike, *, export_names: Collection[str] = ()
) -> SnapshotIdentities:
    """Build the table. A record/enum qualified spelling shared by more than
    one declaration (two ODR-distinct occurrences, or a scope-less legacy
    snapshot naming two different types ``Impl``) proves nothing about which
    one a reference means, so each such declaration becomes its own explicit
    ``unresolved`` node instead of one shared ``type://`` node.

    *export_names* is every spelling the snapshot's export tables carry
    (``model.export_index.snapshot_export_names``) -- the evidence the
    ctor/dtor variant rule needs. Every graph producer passes it, so they
    agree; this leaf module cannot compute it itself without importing
    ``model.snapshot`` (see :class:`SnapshotLike`)."""
    occ = UnresolvedOccurrences()
    functions = tuple(
        occ.allocate(i) for i in _function_identities(snap, frozenset(export_names))
    )
    variables = tuple(occ.allocate(identity_for_variable(v)) for v in snap.variables)
    # An alias is only a second spelling of *this* entity while no other
    # declaration owns that spelling as its canonical id (Mach-O: `exit`'s
    # decorated `_exit` is also the plain name of a distinct `_exit`). Such an
    # alias is ambiguous evidence, so it joins neither (CodeRabbit review).
    canonical = {i.node_id for i in (*functions, *variables)}
    functions = tuple(_without_aliases_in(i, canonical) for i in functions)
    variables = tuple(_without_aliases_in(i, canonical) for i in variables)
    spellings: dict[str, int] = {}
    for name in (
        *(r.qualified_name or r.name for r in snap.types),
        *(e.qualified_name or e.name for e in snap.enums),
    ):
        spellings[name] = spellings.get(name, 0) + 1

    def _type(
        t: RecordType | EnumType, ident: GraphEntityIdentity
    ) -> GraphEntityIdentity:
        name = t.qualified_name or t.name
        if name and spellings[name] > 1:
            ident = unresolved_identity(
                "type", name, t.source_header or "", _entity_key(t.entity_id)
            )
        return occ.allocate(ident)

    records = tuple(_type(r, identity_for_record(r)) for r in snap.types)
    enums = tuple(_type(e, identity_for_enum(e)) for e in snap.enums)
    tag_names = set(spellings)
    typedefs = {
        alias: identity_for_typedef(alias, target, tag_names)
        for alias, target in snap.typedefs.items()
    }
    return SnapshotIdentities(functions, variables, records, enums, typedefs)
