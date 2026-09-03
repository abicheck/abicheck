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

"""Typed ``ScopePath``/``EntityId`` construction for PDB (ADR-063 Phase 6,
PDB EntityId slice, types only).

**Structurally different from DWARF's/the header-AST backends' own scope
construction (``extract/dwarf_scope.py``, ``extract/headers/*/scope.py``),
and harder for one specific reason.** Those three producers each walk a
real tree (a DIE tree, a Clang/castxml AST) with an explicit parent-scope
stack, so a declaration's ``ScopePath`` is built incrementally, for free,
as the walk descends. CodeView's own TPI type records carry no such
tree — ``TypeDatabase``/``pdb_parser.py`` name each struct/class/union/enum
with its own FULLY QUALIFIED spelling as one flat string, ``"::"`` already
embedded (e.g. ``"NS::Outer::Inner"``), with no separate parent-scope
reference to walk. This module's job is therefore the reverse of the other
three: PARSING a flat qualified name back into typed scope segments, not
building one while walking a tree.

**Namespace vs. record disambiguation is a heuristic, not a certainty --
verify against real MSVC output before trusting it further (this
environment has no MSVC/``cl.exe`` toolchain to do that with; see
``tests/test_msvc_pdb_e2e.py``'s own ``msvc``-marker gate).** A qualified
name's own text alone cannot say whether ``NS`` in ``"NS::Widget"`` is a
namespace or an enclosing class — CodeView's flat spelling loses that
distinction the same way it loses the tree DWARF/AST walkers get for free.
The one signal available post-hoc: check whether the ACCUMULATED prefix up
to that segment (e.g. ``"NS"``, then ``"NS::Middle"``, ...) is ITSELF a
name PDB separately recorded as a struct/class/union
(*known_record_names*, the caller's own ``DwarfMetadata.structs`` key set)
— if so, that segment is a :class:`~abicheck.model.identity.Record`;
otherwise it defaults to
:class:`~abicheck.model.identity.Namespace`. **Known, accepted limitation**:
a purely forward-declared enclosing class that is never itself given a
full definition anywhere in the same PDB would not appear in
*known_record_names* (``pdb_metadata._is_user_visible`` filters out a
forward-ref-only record entirely) and would therefore misclassify as a
namespace instead — an edge case this module cannot resolve from the
qualified-name text alone, matching the DWARF/header-AST backends' own
practice of documenting an evidence gap rather than guessing past it.

**No anonymous-type handling.** Unlike DWARF and the header-AST backends,
this module builds no :class:`~abicheck.model.identity.Anonymous` segment
at all — CodeView type records for an unnamed struct/union/enum are
synthesized by MSVC with an internal, compiler-generated name (not left
genuinely anonymous the way a DWARF DIE or a Clang/castxml AST node can
be), and ``pdb_metadata._is_user_visible`` already filters those
compiler-internal (``"<...>"``/``"__..."``-prefixed) names out entirely
before they ever reach this module — checking every top-level ``"::"``-
segment of the qualified name, not just its own leading prefix, so a
nested anonymous aggregate embedded partway through an otherwise-named
qualified spelling (e.g. ``"N::O::<unnamed-tag>"`` for an unnamed
struct/union nested inside ``N::O``, or a named member's own qualified
name if it is itself nested inside one, e.g.
``"N::<unnamed-tag>::Inner"``) is excluded too (Codex review, PR #1025).
The consequence is exclusion, not a wrong identity: a declaration whose
own qualified name embeds an anonymous segment anywhere never reaches
this module at all (dropped from ``pdb_metadata``'s ``structs``/``enums``
dicts entirely), rather than being assigned a plain named
:class:`~abicheck.model.identity.Record`/:class:`~abicheck.model.identity.Namespace`
segment for a scope CodeView never actually gave a real name. A real
anonymous nested aggregate that MSVC spells some other, unfiltered way is
an unverified gap (see the module docstring above on the lack of a real
MSVC toolchain here), not a case this module claims to handle.

**Function-local scopes are indistinguishable from namespaces here, and
this module does not attempt to tell them apart (Codex review, PR
#1025).** CodeView can, in principle, qualify a type declared inside a
function body by that function's own name (e.g. ``"f::Local"`` for a
``struct Local`` declared inside ``void f()``), which the real cross-
backend identity vocabulary represents as
:class:`~abicheck.model.identity.LocalToFunction`, not
:class:`~abicheck.model.identity.Namespace` — but this slice's own
*known_record_names* lookup only has struct/class/union names to check
an accumulated prefix against (this codebase's ``DwarfMetadata`` carries
no function inventory for PDB at all; the DBI stream's own function
records are, per this ADR's own scope, not parsed here), so there is no
signal available to recognize ``"f"`` as a function rather than a
namespace. Every ambiguous segment therefore defaults to
:class:`~abicheck.model.identity.Namespace`, which is wrong for a genuine
function-local declaration and would disagree with the
:class:`~abicheck.model.identity.LocalToFunction` identity a DWARF/
header-AST occurrence of the identical declaration would carry. Not
fixed in this slice: constructing a correct
:class:`~abicheck.model.identity.LocalToFunction` segment needs the
owning function's own :class:`~abicheck.model.identity.EntityId` (not
merely its name — see that class's own docstring) plus a per-parent
block ordinal, neither obtainable from a flat qualified-name string
alone; it needs the DBI function-parsing prerequisite this slice
explicitly does not attempt (per the module docstring's own "types
only" scope), not a heuristic guess here. Left as a documented,
accepted limitation rather than a guess this codebase's own identity-
heuristic-history caution (AGENTS.md) says deserves real fixture
verification first — matching the forward-declared-enclosing-class
limitation immediately above.

**Template-bearing names are split and keyed on their raw CodeView
spelling, unnormalized (Codex review, PR #1025) — a documented,
unverified-toolchain gap, not a silent one.** CodeView is reported to
normalize away optional whitespace after a template argument's comma
(e.g. spelling ``Box<Pair<int,long>,3>`` where castxml/clang would spell
``Box<Pair<int, long>, 3>``), and this module passes each raw, unmodified
``"::"``-segment straight into :func:`~abicheck.model.identity.
entity_id_for_type`/``entity_id_for_enum`` as *leaf_name* — since neither
constructor canonicalizes its argument, the same template specialization
would resolve to two different ``EntityId``s (and, downstream, two
different ``CanonicalEntity.canonical_spelling`` values once fed through
``normalize_header_ast``) purely because one side's evidence came from
PDB and the other from a header-AST/DWARF backend, breaking cross-backend
reconciliation for every PDB-observed template specialization. Not fixed
here: canonicalizing a template argument list's comma-spacing correctly
needs a real parser, not a blind ``re.sub(",", ", ")`` — a `,` can appear
inside a nested template's own argument list, a non-type template
argument's string/char literal, or (rarer, but real) a function-pointer
template argument's own parameter list, none of which this module can
safely handle without a way to verify the transformation against real
``clang-cl``/MSVC-produced PDB output, which this environment does not
have (see the module docstring above). Documented rather than guess-fixed,
the same call already made for the two limitations above; the same fix,
whenever it lands with real fixture verification, would apply equally to
any other PDB-sourced qualified name this module already builds an
``EntityId`` from.

**Inline namespaces are indistinguishable from ordinary namespaces here, and
this module always emits :class:`~abicheck.model.identity.Namespace`, never
:class:`~abicheck.model.identity.InlineNamespace` (Codex review, PR #1025,
carried over from a prior review round).** DWARF has ``DW_AT_export_symbols``
and Clang's AST has its own ``isInline`` flag on ``NamespaceDecl``
(``extract/dwarf_scope.py``'s ``namespace_scope_segment``,
``extract/headers/clang/scope.py``) — both real, source-derived signals this
module's two sibling scope-builders read directly. CodeView has no
equivalent available from what this module works from: **the input here is
not a symbol/DIE tree with per-scope attributes at all, but TPI's already-
flattened, fully-qualified name strings** (this module's own opening
paragraph), and the C++ ``inline`` keyword on a namespace is a source-level,
compile-time-only annotation that changes name *lookup*, not name
*mangling* or *spelling* — unlike libc++/libstdc++'s ABI-tag inline
namespaces (``__1``, ``__cxx11``), which are recognizable from the qualified
name text alone by convention
(``model.qualified_name_split.is_inline_abi_namespace_segment``),
a user's own ``inline namespace v1 { … }`` produces the byte-for-byte same
qualified spelling, ``api::v1::Widget``, whether or not it is declared
``inline`` — there is no CodeView bit or naming convention left to recover
that distinction from post hoc, the same way ``castxml``'s own scope module
documents itself as structurally incapable of producing
:class:`~abicheck.model.identity.InlineNamespace` at all
(``extract/headers/castxml/scope.py``). This is a **live**, not purely
theoretical, cross-backend mismatch: :class:`~abicheck.model.identity.
EntityId`'s ``_segment_key`` tags a ``Namespace`` segment ``"ns"`` and an
``InlineNamespace`` segment ``"ins"`` (plus ``InlineNamespace.version_tag`` as
additional identity), so any declaration nested inside a user-declared
inline namespace gets a different ``EntityId`` when observed via PDB than
via a header-AST backend, breaking the exact cross-backend reconciliation
this ADR-063 slice exists to establish. **Not fixed here**: inventing a
heuristic (e.g. treating every namespace segment as inline, or guessing from
naming conventions beyond the already-established ABI-tag family) would be
exactly the kind of unverified guess this module's own namespace-vs-record
and function-local limitations above already decline to make, and this
environment has no MSVC/``cl.exe``/real PDB sample to check a candidate
signal against (see the module docstring's opening caveat). What would
resolve it: a real MSVC-produced PDB confirming CodeView genuinely carries
no per-namespace debug record at all (only TPI's flat UDT names, as already
observed) — at which point the gap is permanent and structural, matching
castxml's — or, if some CodeView symbol stream this module does not parse
today (the DBI module-symbol stream, not TPI) turns out to carry a distinct
namespace-scope record with a source-level ``inline`` marker, a real fix
built from that evidence rather than from this string-splitting module
alone.

Leaf module: depends on ``model`` (allowed: ``extract -> model``, ADR-061),
``model.qualified_name_split`` (the shared, dependency-free ``"::"``-
splitting primitive; see that module's own docstring for why it lives in
``model/`` rather than in ``qualified_name_segments.py``, which belongs to
the ``compare`` layer ``extract`` may not import), and
``extract.headers.scope_segments`` for the same shared segment
constructors DWARF and the two header-AST backends already use — nothing
above.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet

from ..model.identity import (
    EntityId,
    ScopePath,
    ScopeSegment,
    entity_id_for_enum,
    entity_id_for_type,
)
from ..model.qualified_name_split import split_top_level_scopes
from .headers.scope_segments import (
    namespace_segment as _namespace_segment,
    record_segment as _record_segment,
)

__all__ = ["enum_entity_id", "record_entity_id", "scope_path_for_qualified_name"]


def scope_path_for_qualified_name(
    qualified: str, known_record_names: AbstractSet[str]
) -> tuple[ScopePath, str]:
    """Split *qualified* into ``(scope_path, leaf_name)``.

    *known_record_names* is the caller's own set of qualified names PDB
    separately recorded as a struct/class/union (typically
    ``DwarfMetadata.structs.keys()``) — see this module's own docstring for
    why that lookup, not the segment text alone, is what decides
    :class:`~abicheck.model.identity.Record` vs.
    :class:`~abicheck.model.identity.Namespace` for each enclosing segment.

    >>> scope_path_for_qualified_name("Widget", frozenset())
    ((), 'Widget')
    >>> scope_path_for_qualified_name("NS::Widget", frozenset())
    ((Namespace(name='NS'),), 'Widget')
    >>> scope_path_for_qualified_name("Outer::Inner", frozenset({"Outer"}))
    ((Record(name='Outer', access='public'),), 'Inner')
    """
    segments = split_top_level_scopes(qualified)
    if not segments:
        return (), qualified
    leaf = segments[-1]
    scope: list[ScopeSegment] = []
    prefix_parts: list[str] = []
    for segment in segments[:-1]:
        prefix_parts.append(segment)
        prefix = "::".join(prefix_parts)
        if prefix in known_record_names:
            scope.append(_record_segment(segment))
        else:
            scope.append(_namespace_segment(segment))
    return tuple(scope), leaf


def record_entity_id(qualified: str, known_record_names: AbstractSet[str]) -> EntityId:
    """``EntityId`` for a PDB struct/class/union named *qualified*."""
    scope, leaf = scope_path_for_qualified_name(qualified, known_record_names)
    return entity_id_for_type(scope, leaf)


def enum_entity_id(qualified: str, known_record_names: AbstractSet[str]) -> EntityId:
    """``EntityId`` for a PDB enum named *qualified*. See
    :func:`record_entity_id`."""
    scope, leaf = scope_path_for_qualified_name(qualified, known_record_names)
    return entity_id_for_enum(scope, leaf)
