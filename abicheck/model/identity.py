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

"""``ScopePath``/``EntityId`` — the one identity primitive (ADR-063 Phase 2).

**This module is a first, isolated slice of Phase 2, not the whole
phase.** See ``docs/contribute/plans/one-semantic-pipeline.md``'s "Phase 2
— EntityId/ScopePath as the one identity primitive" section for the full
design, including two questions this slice deliberately leaves open:

1. *Where ``ScopePath`` gets built from.* This module's ``entity_id_for_*``
   constructors take an already-built :data:`ScopePath` as input — they do
   not derive one from a parser's internal scope-tracking state
   (``entry.scope`` in ``dumper_clang.py``/``dumper_castxml.py`` today is a
   bare ``list[str]``, structurally insufficient to build a typed
   ``ScopePath`` from; see the plan). Widening that parser state, and
   deciding whether the resulting ``EntityId`` is computed once and carried
   on the model object or recomputed on demand (the plan's "no carrier
   field" open question, options (a)/(b)), is separate follow-on work.
2. *The mangled-name-is-genuine determination.* ``entity_id_for_function``/
   ``entity_id_for_variable`` take a caller-supplied ``mangled_name`` and
   trust it is a real mangling, not a bare name that merely rode in the
   mangled field (the ``extern "C"`` case). That determination stays owned
   by ``finding_identity.is_real_mangled_name``/``normalize_mangled_name``
   for now — this slice does not migrate that ~450-line, independently
   reviewed Itanium-mangling-validation machinery, and
   ``finding_identity.py`` does not yet delegate to this module. A future
   slice is expected to move that algorithm here and make
   ``finding_identity.resolve_function_identity`` a thin wrapper, per the
   plan's "direction of reuse" note — not attempted here, to keep this
   slice reviewable on its own.

What *is* real and load-bearing in this slice: the ``ScopePath`` segment
types and their identity-vs-payload field split, the ``EntityId`` shape
itself (``scope``, ``kind``, ``leaf_name``, ``extra`` — never a bare
``(ScopePath, kind)``, which collides sibling declarations), and the
``EntityKind``/``ObservationKind`` relocation from ``storage.entity_ids``
(domain vocabulary belongs in ``model``, not the storage wire layer, per
ADR-061's ``storage -> model`` import direction — ``storage.entity_ids`` now
imports these two enums rather than redefining them).

Leaf module: no dependency on ``checker_types``/``diff_*``/anything above
``model``, per ADR-063 D10.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

__all__ = [
    "Anonymous",
    "EntityId",
    "EntityKind",
    "InlineNamespace",
    "LocalToFunction",
    "Namespace",
    "ObservationKind",
    "Record",
    "ScopePath",
    "ScopeSegment",
    "entity_id_for_constant",
    "entity_id_for_enum",
    "entity_id_for_function",
    "entity_id_for_type",
    "entity_id_for_typedef",
    "entity_id_for_variable",
]


class EntityKind(enum.Enum):
    """What kind of logical thing an :class:`EntityId` names.

    Relocated here from ``storage.entity_ids`` (ADR-063 Phase 2 note above):
    this is domain vocabulary, not a storage wire concern.
    ``storage.entity_ids.EntityKind`` is this same enum, imported rather
    than redefined — exactly one ``EntityKind`` exists in the repository.
    """

    FUNCTION = "function"
    VARIABLE = "variable"
    TYPE = "type"
    ENUM = "enum"
    TYPEDEF = "typedef"
    CONSTANT = "constant"
    SYMBOL = "symbol"
    FIELD = "field"
    BASE = "base"


class ObservationKind(enum.Enum):
    """Where an occurrence of an entity was observed.

    Relocated from ``storage.entity_ids`` alongside :class:`EntityKind`,
    for the same reason. See ``storage.entity_ids.OccurrenceId`` for what
    consumes it today.
    """

    AST = "ast"
    DWARF = "dwarf"
    PDB = "pdb"
    EXPORT_TABLE = "export_table"
    TRANSLATION_UNIT = "translation_unit"
    SOURCE_LOCATION = "source_location"
    BUILD_UNIT = "build_unit"


# --------------------------------------------------------------------------
# ScopePath segment types
#
# Each segment states which of its own fields are identity and which are
# payload -- a bare frozen dataclass would make every field identity by
# default, which is wrong for `Record.access`. `field(compare=False)`
# excludes a field from both `__eq__` and the frozen-dataclass-generated
# `__hash__` (dataclass's `hash=None` default follows `compare`), so no
# separate `__eq__`/`__hash__` override is needed anywhere below.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Namespace:
    """An ordinary (non-inline) namespace segment. Every field is identity."""

    name: str


@dataclass(frozen=True)
class Record:
    """A record (class/struct/union) nesting scope.

    ``access`` (public/protected/private) is carried on the segment because
    a nested record's access is a real fact a consumer may want, but it is
    not part of *where* the nesting scope is: two snapshots of the same
    class with a member's access level changed still name the identical
    containing scope. Making ``access`` part of identity would turn an
    access-level change into a spurious identity mismatch -- the matcher
    would see "removed, then added" at a different ``EntityId`` instead of
    "this declaration changed."
    """

    name: str
    access: str = field(default="", compare=False)


@dataclass(frozen=True)
class InlineNamespace:
    """An inline namespace segment. Every field is identity.

    ``version_tag`` is exactly the dimension ADR-025's versioned-inline-
    namespace-alias handling already keys matching on; excluding it here
    would silently re-widen the ``v1``/``v2``-shaped collision that
    machinery exists to avoid.
    """

    name: str
    version_tag: str = ""


@dataclass(frozen=True)
class Anonymous:
    """An anonymous struct/union/enum/namespace scope.

    Both fields are identity, deliberately: nothing else disambiguates two
    sibling anonymous scopes coexisting in the same parent. ``ordinal`` is a
    deterministic per-parent sequence number assigned at parse time --
    stable *within one parse*, which is what makes it a legitimate
    disambiguator for two anonymous siblings in one snapshot. It is **not**
    stable across revisions: inserting a new anonymous sibling ahead of
    existing ones shifts every later sibling's ordinal, and therefore its
    whole ``EntityId``, even though nothing about those later declarations
    changed. No stable across-snapshot discriminator is adopted here -- see
    the plan's Phase 2 Design section for why (a source-location anchor and
    a structural fingerprint of the anonymous scope's own members were both
    considered and are each independently documented elsewhere in this
    codebase's AGENTS.md as unreliable for this exact purpose). This is an
    accepted, documented limitation of ``Anonymous`` identity specifically,
    not a silent gap.
    """

    kind: str
    ordinal: int


@dataclass(frozen=True)
class LocalToFunction:
    """A scope local to one function body. Both fields are identity --
    nothing else disambiguates two same-named locals in one function."""

    owner: str


ScopeSegment = Namespace | Record | InlineNamespace | Anonymous | LocalToFunction

#: An immutable, ordered sequence of typed scope segments, outermost first,
#: naming only the *containing* scope -- never the leaf declaration itself.
ScopePath = tuple[ScopeSegment, ...]


# --------------------------------------------------------------------------
# EntityId
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityId:
    """A logical declaration's identity.

    Never a bare ``(scope, kind)`` pair -- that collides any two sibling
    declarations of the same kind in the same scope (two enums, two
    variables, two typedefs, and -- the case that first exposed this --
    two function overloads). ``leaf_name`` is the declaration's own
    (unqualified) name, carried explicitly for every kind. ``extra`` is
    kind-specific and empty for most kinds (a record/enum/typedef/
    constant); a variable's and a function's constructors below populate it
    with the discriminator each of those two kinds specifically needs.
    """

    scope: ScopePath
    kind: EntityKind
    leaf_name: str
    extra: tuple[str, ...] = ()


def _scope_path(scope: ScopePath) -> ScopePath:
    """Normalize *scope* to a real ``tuple`` regardless of what iterable a
    caller passed, so two calls built from value-equal-but-not-identical
    scope sequences (e.g. a list vs. a tuple) produce ``EntityId``s that
    compare equal -- "computed once" here means one algorithm producing one
    answer for one input, not object identity of the scope argument.
    """
    return tuple(scope)


def entity_id_for_type(scope: ScopePath, leaf_name: str) -> EntityId:
    """``EntityId`` for a record/class/struct/union type. No kind-specific
    discriminator: a bare name is unambiguous once ``ScopePath`` disambiguates
    the containing scope, since two types cannot share one name in one
    scope in valid C/C++.
    """
    return EntityId(scope=_scope_path(scope), kind=EntityKind.TYPE, leaf_name=leaf_name)


def entity_id_for_enum(scope: ScopePath, leaf_name: str) -> EntityId:
    """``EntityId`` for an enum type. See :func:`entity_id_for_type`."""
    return EntityId(scope=_scope_path(scope), kind=EntityKind.ENUM, leaf_name=leaf_name)


def entity_id_for_typedef(scope: ScopePath, leaf_name: str) -> EntityId:
    """``EntityId`` for a typedef/alias. See :func:`entity_id_for_type`."""
    return EntityId(
        scope=_scope_path(scope), kind=EntityKind.TYPEDEF, leaf_name=leaf_name
    )


def entity_id_for_constant(scope: ScopePath, leaf_name: str) -> EntityId:
    """``EntityId`` for a manifest/macro constant. See
    :func:`entity_id_for_type`."""
    return EntityId(
        scope=_scope_path(scope), kind=EntityKind.CONSTANT, leaf_name=leaf_name
    )


def entity_id_for_variable(
    scope: ScopePath,
    leaf_name: str,
    *,
    mangled_name: str | None = None,
) -> EntityId:
    """``EntityId`` for a variable.

    A bare ``(scope, "variable", leaf_name, ())`` is not enough: two
    exported variables sharing scope and leaf name but differing mangled
    names (two distinct, non-overloadable template-instantiation statics,
    or a declaration-vs-definition spelling mismatch the mangler doesn't
    collapse) are two different exports, not one -- "variables enable no
    alias tier at all ... a display-name join would hide a real removal"
    (AGENTS.md's own ``finding_identity.py``/``SymbolIdentityIndex`` entry,
    which this constructor generalizes rather than contradicts). So
    ``extra`` carries the mangled spelling whenever one exists, falling back
    to ``()`` only for the genuinely mangling-free case (no linker symbol at
    all -- e.g. a variable known only from a header declaration with no
    corresponding binary evidence).

    *mangled_name* must already be established as a genuine mangling by the
    caller, never a bare name that merely rode in the mangled field (an
    ``extern "C"`` producer's ``mangled == name``) -- see this module's
    docstring for why that determination is not made here.
    """
    extra = ("mangled", mangled_name) if mangled_name else ()
    return EntityId(
        scope=_scope_path(scope),
        kind=EntityKind.VARIABLE,
        leaf_name=leaf_name,
        extra=extra,
    )


def entity_id_for_function(
    scope: ScopePath,
    leaf_name: str,
    *,
    mangled_name: str | None = None,
    param_types: tuple[str, ...] = (),
    cv_qualifiers: tuple[str, ...] = (),
) -> EntityId:
    """``EntityId`` for a function.

    ``scope`` plus a bare name is not enough: ``f(int)`` and ``f(double)``
    share the same ``ScopePath`` and the same ``function`` kind, so without
    a third component two genuinely distinct overloads collapse into one
    id. Mirrors the existing tiered resolution
    ``finding_identity.resolve_function_identity``/``SymbolIdentityIndex``
    and ADR-048's normalized identity already establish: mangled name first
    when one exists (the common case, already globally unique per
    overload) -- ``extra`` becomes ``("mangled", mangled_name)`` -- and only
    for the genuinely mangling-free case (a non-``extern "C"`` function on a
    DWARF-only snapshot) does ``extra`` fall back to the normalized
    signature discriminator, ``("sig", *param_types, *cv_qualifiers)``.
    Unlike ``finding_identity.normalized_signature``'s own fallback tuple,
    the callable's qualified name does *not* need to be repeated inside
    ``extra`` here -- ``scope``/``leaf_name`` already carry it losslessly,
    with no string-joining involved, so there is nothing left for the
    fallback tuple to lose by omitting it.

    Both branches are tagged (``"mangled"``/``"sig"``) rather than left as a
    bare tuple, so a mangled name that happens to equal some function's
    literal signature-tuple spelling can never collide with it -- the two
    branches occupy disjoint regions of ``extra``'s value space by
    construction, not by coincidence of what real mangled names or type
    spellings look like.

    *mangled_name* must already be established as a genuine mangling by the
    caller -- see this module's docstring for why that determination is not
    made here. When a genuine mangled name is supplied, *param_types*/
    *cv_qualifiers* are ignored (an ``extern "C"`` function has no
    overloading, so a changed parameter list is a modification of the one
    function named ``leaf_name``, not a different overload -- the same rule
    ``finding_identity.resolve_function_identity`` already documents and
    applies).
    """
    extra = (
        ("mangled", mangled_name)
        if mangled_name
        else ("sig", *param_types, *cv_qualifiers)
    )
    return EntityId(
        scope=_scope_path(scope),
        kind=EntityKind.FUNCTION,
        leaf_name=leaf_name,
        extra=extra,
    )
