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

"""Removal decisions for a template instantiation the new header no longer
enumerates.

castxml records an instantiation only when some header translation unit
instantiates it, or an ``extern template`` names it. When the new header stops
using a template, the new snapshot has no record for that instantiation, and
the header-depth removal path reads the absence as a removal. But the primary
template (for a member template or a class-template member, the template that
owns it) is still declared in the new header graph, so the declaration did not
go. What can have changed is only whether the library still exports the
instantiation, and the new export table answers that question.

For an old instantiation whose primary template is still declared in the new
header graph (:meth:`TemplateInstanceAbsence._primary_still_declared`: one
primary, or every old overload of the name kept with its signature):

* the new binary still exports the same symbol: no finding, since the binary
  still provides what consumers bind to;
* the new export table was read and lacks the symbol, and the old binary
  exported it: ``func_visibility_changed``, the export-level change that
  occurred, at the same BREAKING severity as every other export loss;
* the old binary never exported it: no finding, since there was no symbol for a
  consumer to bind to and the declaration remains.

The rule never emits a declaration-removal kind for such an *implicit*
instantiation, and it never demotes an export loss: a WEAK loss keeps its
binding and its verdict (``case217``/``case218`` pin that contract). It
returns ``None`` whenever it makes no claim -- not a template instance, no
declared primary or a deleted overload, an unknown export table, or an
ELF-only old record -- and the caller falls back to the existing removal path.

An instance the old header spelled explicitly -- ``extern template`` or
``template`` instantiation, or an explicit specialization -- is a declaration
the new header no longer contains, so its absence is a removal, not an export
change: the rule makes no claim for it and the removal path reports
``func_removed`` / ``var_removed``. That needs the old snapshot's
``explicit_instantiation_fact`` (``Function``/``Variable``), which only the
clang header backend records; a castxml or pre-v65 side is not collected, and
the implicit-instance path above applies.

An explicit declaration the new header still spells, whose export the new
binary dropped, is not an absence: the declaration matches and the export loss
is judged on the matched symbol, as for any other declared function.

The same holds one level up, for a class-template specialization the new
header stops instantiating (:meth:`TemplateInstanceAbsence.specialization_vanished`).
When the old record is proven an instantiation of a pattern, the new header
graph still declares that pattern, and the new snapshot has no record of that
specialization, then:

* the specialization itself is not ``type_removed`` -- the type is still
  nameable and its definition still comes from the same declaration;
* each of its members -- implicit constructors, destructor and ``operator=``,
  inline methods, static data members -- is judged by the export table exactly
  as above (a synthetic constructor/destructor key, which names no symbol,
  yields no finding), with ``var_visibility_changed`` for a static data member
  whose export the new binary dropped.

Two producers can prove the origin (:func:`_proven_origin`); either suffices,
and one that contradicts the other voids the proof:

* castxml's declaring-line reading (``RecordType.specialization_declared_fact``
  ``PRESENT(False)``): instantiated from the primary template, still declared
  when the new graph has a ``type://<template>`` node;
* the clang template graph (``instantiated_from`` on the old
  ``template_instantiation`` node, read from the AST's structure, so a
  macro-produced class-head is covered): from the primary, as above, or from
  a partial specialization, still declared when the new graph has the same
  ``template_decl://<template>#<signature>`` node.

An explicit specialization, an explicit instantiation, an undetermined origin
(neither producer proved it: an older snapshot, a contradiction), a pattern
the new header no longer declares (a deleted partial specialization, a removed
template), and a non-template class all keep the existing removal path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..demangle import demangle
from ..diff_helpers import make_change
from ..elf_symbol_filter import FUNCTION_SYMBOL_TYPES, VARIABLE_SYMBOL_TYPES
from ..model import Function, RecordType, Variable
from ..model.availability import FactStatus
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.edge_coverage import EdgeAnswer
from ..model.explicit_instantiation import ExplicitInstantiation
from ..model.export_entity_name import entity_name_components, has_synthetic_component
from ..model.graph_identity import decl_node_identity, header_declaration_ids
from ..model.qualified_name_split import split_top_level_scopes
from ..model.surface_facts import (
    is_binary_exported,
    is_export_confirmed_absent,
    is_export_table_only_record,
    surface_fact_summary,
    visibility_label,
)
from .edge_query import ObservedExportTable
from .finding_identity.ctor_dtor import (
    _synthetic_ctor_dtor_scope as synthetic_ctor_dtor_scope,
)
from .public_surface import observed_exports

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.export_entity_name import EntityName

__all__ = ["TemplateInstanceAbsence", "member_owner"]


def _primary_stem_of(parsed: EntityName | None) -> str | None:
    """The header spelling of the template a parsed name instantiates.

    ``_ZN2ns3BoxIdE3setEd`` -> ``ns::Box::set``. ``None`` unless the name is an
    Itanium name with at least one template-argument list and every scope
    component a plain identifier: a constructor, operator or substitution has no
    header spelling this module can match, so it makes no claim.
    """
    if parsed is None or not parsed.template_positions:
        return None
    if has_synthetic_component(parsed):
        return None
    return "::".join(parsed.components)


def _top_level_index(text: str, char: str) -> int:
    """Index of the first *char* outside ``<>``/``()`` nesting, or ``-1``."""
    depth = 0
    for i, ch in enumerate(text):
        if ch == char and depth == 0:
            return i
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth = max(depth - 1, 0)
    return -1


def _scope_of(qualified: str) -> str | None:
    """``ns::Box<double>::set`` -> ``ns::Box<double>``; ``None`` when the name
    has no enclosing scope."""
    scopes = split_top_level_scopes(qualified)
    return "::".join(scopes[:-1]) if len(scopes) > 1 else None


def _spelling_key(name: str) -> str:
    """Whitespace-insensitive key: castxml writes ``Box<int *>``, the
    demangler ``Box<int*>``."""
    return "".join(name.split())


def member_owner(symbol: str) -> str | None:
    """The class-template specialization *symbol* is a member of, as a
    whitespace-free qualified spelling (``ns::Box<double>``), or ``None``.

    Reads the three spellings a member record carries: a synthetic constructor
    key, a synthetic destructor key (``~ns::Box<double>``), and an Itanium name
    whose enclosing component carries a template-argument list (demangled to
    recover the arguments). Anything else -- a free function, a member of a
    non-template class, a name the demangler cannot read -- makes no claim.
    """
    return _member_owner_of(symbol, *_spellings(symbol))


def _spellings(symbol: str) -> tuple[EntityName | None, str | None]:
    """*symbol*'s parsed Itanium name and, only when it has none, its castxml
    synthetic constructor/destructor scope.

    The two are exclusive: :func:`entity_name_components` reads only a
    ``_Z`` name, and a synthetic key starts with ``__abicheck_ctor__`` or
    ``~``. Reading the parse first lets a caller that needs both rules share
    one read of *symbol*.
    """
    parsed = entity_name_components(symbol)
    return parsed, (synthetic_ctor_dtor_scope(symbol) if parsed is None else None)


def _member_owner_of(
    symbol: str, parsed: EntityName | None, scope: str | None
) -> str | None:
    """:func:`member_owner` over :func:`_spellings`' answer for *symbol*."""
    owner = scope
    if owner is None:
        if parsed is None or not parsed.nested or len(parsed.components) < 2:
            return None
        if len(parsed.components) - 2 not in parsed.template_positions:
            return None
        text = demangle(symbol)
        if text is None:
            return None
        cut = _top_level_index(text, "(")
        owner = _scope_of(text[:cut] if cut >= 0 else text)
    if owner is None or _template_of(_spelling_key(owner)) is None:
        return None
    return _spelling_key(owner)


def _declared_by_own_head(record: RecordType) -> bool | None:
    """The castxml line reading (``specialization_declared_fact``): ``True``
    for a specialization's own class-head, ``False`` for the primary
    template's, ``None`` when undetermined."""
    fact = record.specialization_declared_fact
    if fact is None or fact.status is not FactStatus.PRESENT:
        return None
    return bool(fact.value)


#: ``(origin, pattern node id)`` an old graph proves for an instantiation.
_GraphOrigin = tuple[str, str]


def _graph_origins(snapshot: AbiSnapshot) -> dict[str, _GraphOrigin]:
    """Whitespace-free specialization spelling -> the pattern the clang
    template graph proved it instantiated from (``instantiated_from`` on its
    ``template_instantiation`` node, ``buildsource/template_class_origin.py``)
    and the ``template_decl`` node it instantiates. A spelling two nodes
    disagree on, or whose node has several patterns, is left out."""
    graph = getattr(snapshot, "surface_graph", None)
    if graph is None:
        return {}
    labels: dict[str, tuple[str, str]] = {}
    for node in graph.nodes:
        origin = (node.attrs or {}).get("instantiated_from")
        if node.kind == "template_instantiation" and isinstance(origin, str):
            labels[node.id] = (_spelling_key(node.label), origin)
    patterns: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.kind == "DECL_INSTANTIATES_TEMPLATE" and edge.src in labels:
            patterns.setdefault(edge.src, set()).add(edge.dst)
    found: dict[str, set[_GraphOrigin]] = {}
    for node_id, (spelling, origin) in labels.items():
        dsts = patterns.get(node_id, set())
        if len(dsts) == 1:
            found.setdefault(spelling, set()).add((origin, next(iter(dsts))))
    return {k: next(iter(v)) for k, v in found.items() if len(v) == 1}


def _proven_origin(
    record: RecordType, graph: _GraphOrigin | None
) -> _GraphOrigin | None:
    """The pattern *record* was proven instantiated from, from the castxml
    line reading and the clang template graph together: ``("primary", "")``,
    ``("partial", <template_decl node id>)``, or ``None``. Either producer
    may prove it alone; a producer that contradicts the other voids it."""
    head = _declared_by_own_head(record)
    origin = graph[0] if graph is not None else ""
    if origin == "primary" and head is not True:
        return ("primary", "")
    if origin == "partial" and head is not False:
        return graph
    if head is False and origin == "":
        return ("primary", "")
    return None


def _specialization_spelling(record: RecordType) -> str:
    return _spelling_key(record.qualified_name or record.name)


def _template_of(spelling: str) -> str | None:
    """``ns::Box<double>`` -> ``ns::Box``, only when one argument list closes
    the name (a nested template's specialization makes no claim)."""
    cut = spelling.find("<")
    if (
        cut <= 0
        or _top_level_index(spelling[cut + 1 :], ">") != len(spelling) - cut - 2
    ):
        return None
    return spelling[:cut]


#: Sentinel: the old side recorded no header declarations.
_NO_OLD_DECLS: dict[str, frozenset[str]] = {}


def _primaries_by_stem(node_ids: frozenset[str]) -> dict[str, frozenset[str]]:
    """Declared ``decl://`` ids grouped by their declaration identity (the
    qualified name without the ``#sha256:`` overload discriminator)."""
    found: dict[str, set[str]] = {}
    for node_id in node_ids:
        identity = decl_node_identity(node_id)
        if identity is not None:
            found.setdefault(identity, set()).add(node_id)
    return {k: frozenset(v) for k, v in found.items()}


class TemplateInstanceAbsence:
    """The facts a template-absence decision reads, for one comparison.

    Each export table is the new snapshot's function or variable table as an I4
    answer, so a name is only "absent" when the table was actually read. The
    header graph's declarations and the vanished specializations are read
    lazily, once, and only if a removal candidate needs them. *old* is needed
    only by the class-template specialization rule; without it that rule makes
    no claim.
    """

    def __init__(self, new: AbiSnapshot, old: AbiSnapshot | None = None) -> None:
        self._new = new
        self._old = old
        self._exports: dict[frozenset[str], ObservedExportTable] = {}
        self._node_ids: frozenset[str] | None = None
        self._declared: dict[str, frozenset[str]] | None = None
        self._old_declared: dict[str, frozenset[str]] | None = None
        self._vanished: frozenset[str] | None = None

    def _export_table(self, types: frozenset[str]) -> ObservedExportTable:
        table = self._exports.get(types)
        if table is None:
            table = self._exports[types] = observed_exports(self._new, types)
        return table

    def _graph_node_ids(self) -> frozenset[str]:
        """The new header's declaration node ids -- from its graph, or the
        L2 ids a header-depth projection kept (``header_declaration_ids``)."""
        if self._node_ids is None:
            self._node_ids = header_declaration_ids(self._new) or frozenset()
        return self._node_ids

    def _declared_primaries(self) -> dict[str, frozenset[str]]:
        if self._declared is None:
            self._declared = _primaries_by_stem(self._graph_node_ids())
        return self._declared

    def _old_declared_primaries(self) -> dict[str, frozenset[str]] | None:
        """The old header's primaries by stem, or ``None`` when the old side
        recorded no header declarations (no graph, nothing kept)."""
        if self._old_declared is None:
            ids = header_declaration_ids(self._old) if self._old is not None else None
            self._old_declared = (
                _primaries_by_stem(ids) if ids is not None else _NO_OLD_DECLS
            )
        return None if self._old_declared is _NO_OLD_DECLS else self._old_declared

    def _primary_still_declared(self, stem: str) -> bool:
        """Whether the primary template an instance of *stem* came from is
        still declared in the new header.

        A ``decl://`` primary's id is ``<stem>#sha256:<hash of its dependent
        signature>``: a one-way hash, so an instance's argument list cannot be
        matched to one overload. Instead:

        * exactly one primary on the new side, and at most one on the old
          side: the same template (the original rule);
        * several overloads, and the old header's are known: every old
          overload of *stem* must still be declared with the identical
          signature -- a deleted overload leaves the instance's origin
          unproven, so the removal path keeps reporting it;
        * several overloads, old header unknown: any declared primary of
          *stem* (the stem fallback).
        """
        new = self._declared_primaries().get(stem, frozenset())
        if not new:
            return False
        old_all = self._old_declared_primaries()
        if old_all is None:
            return True
        old = old_all.get(stem, frozenset())
        if len(new) == 1 and len(old) <= 1:
            return True
        return old <= new

    def _vanished_specializations(self) -> frozenset[str]:
        """Old specializations proven instantiated from a pattern the new
        header graph still declares -- the primary class template (a
        ``type://<template>`` node) or the same partial specialization (its
        ``template_decl://<template>#<signature>`` node) -- which the new
        snapshot does not enumerate."""
        if self._vanished is None:
            vanished: set[str] = set()
            if self._old is not None:
                still_enumerated = {
                    _specialization_spelling(t) for t in self._new.declarations.types
                }
                origins = _graph_origins(self._old)
                for record in self._old.declarations.types:
                    spelling = _specialization_spelling(record)
                    template = _template_of(spelling)
                    if template is None or spelling in still_enumerated:
                        continue
                    proven = _proven_origin(record, origins.get(spelling))
                    if proven is None:
                        continue
                    pattern = (
                        f"type://{template}" if proven[0] == "primary" else proven[1]
                    )
                    if pattern in self._graph_node_ids():
                        vanished.add(spelling)
            self._vanished = frozenset(vanished)
        return self._vanished

    def specialization_vanished(self, record: RecordType) -> bool:
        """Whether *record*, an old type the new side does not match, is a
        specialization the new header merely stopped instantiating, and so not
        a removed type."""
        return _specialization_spelling(record) in self._vanished_specializations()

    def changes_for(
        self, mangled: str, f_old: Function, elf_only_mode: bool
    ) -> list[Change] | None:
        """The findings for *f_old*, an old function the new declaration map does
        not match, or ``None`` when this rule makes no claim about it.

        An export-table-only old record (``elf_only_mode``) keeps the existing
        ELF-only removal path, as ``_check_removed_function`` already does.
        """
        if elf_only_mode and is_export_table_only_record(f_old):
            return None
        if _declared_explicitly(f_old):
            return None  # the removed explicit declaration is the change
        parsed, scope = _spellings(mangled)  # read once, shared by both rules
        owner = _member_owner_of(mangled, parsed, scope)
        if owner is not None and owner in self._vanished_specializations():
            if scope is not None:
                return []  # a synthetic constructor/destructor key: no symbol
            return self._judged(mangled, f_old, FUNCTION_SYMBOL_TYPES)
        stem = _primary_stem_of(parsed)
        if stem is None or not self._primary_still_declared(stem):
            return None
        return self._judged(mangled, f_old, FUNCTION_SYMBOL_TYPES)

    def instance_still_exported(self, mangled: str) -> bool:
        """Whether *mangled* names a template instance the new export table
        still carries -- the export evidence that keeps an instance the new
        declarations omit from reading as removed when the header graph that
        :meth:`changes_for` needs is unreadable."""
        parsed, _scope = _spellings(mangled)
        return (
            _primary_stem_of(parsed) is not None
            and self._export_table(FUNCTION_SYMBOL_TYPES).answer(mangled)
            is EdgeAnswer.PRESENT
        )

    def variable_changes_for(
        self, mangled: str, v_old: Variable
    ) -> list[Change] | None:
        """The findings for *v_old*, an old variable with no new peer, or
        ``None`` when this rule makes no claim: only a static data member of a
        vanished specialization is judged here."""
        if _declared_explicitly(v_old):
            return None  # the removed explicit declaration is the change
        owner = member_owner(mangled)
        if owner is None or owner not in self._vanished_specializations():
            return None
        return self._judged(mangled, v_old, VARIABLE_SYMBOL_TYPES)

    def _judged(
        self, mangled: str, old: Function | Variable, types: frozenset[str]
    ) -> list[Change] | None:
        answer = self._export_table(types).answer(mangled)
        if answer is EdgeAnswer.PRESENT:
            return []
        if answer is not EdgeAnswer.PROVEN_ABSENT:
            return None
        if is_binary_exported(old):
            return [_export_lost(mangled, old)]
        if is_export_confirmed_absent(old):
            return []
        return None


def _declared_explicitly(decl: Function | Variable) -> bool:
    """Whether the old header spelled *decl*'s instance explicitly (a fact the
    clang header backend recorded): its absence is a removed declaration."""
    fact = decl.explicit_instantiation_fact
    return (
        fact is not None
        and fact.status is FactStatus.PRESENT
        and fact.value is not None
        and fact.value is not ExplicitInstantiation.ABSENT
    )


def _export_lost(mangled: str, old: Function | Variable) -> Change:
    """``func_visibility_changed`` (``var_visibility_changed`` for a variable)
    for an export the new binary dropped while its declaration remained.

    The proof is the export table's confirmed absence, not a visibility
    attribute, so the text says "no longer exported" and never "hidden": the
    new side's value is the absence the table established. The facts are the
    old side's, with the new side's export fact replaced by that absence.
    """
    kind = (
        ChangeKind.FUNC_VISIBILITY_CHANGED
        if isinstance(old, Function)
        else ChangeKind.VAR_VISIBILITY_CHANGED
    )
    what = "Function" if isinstance(old, Function) else "Variable"
    return make_change(
        kind,
        symbol=mangled,
        name=old.name,
        description=(
            f"{what} {old.name} is no longer exported by the new binary; "
            "its declaration remains in the new header"
        ),
        old_value=visibility_label(old),
        new_value="not exported",
        symbol_binding=old.elf_binding.value if old.elf_binding else None,
        entity_id=old.entity_id,
        surface_facts={**surface_fact_summary(old), "binary_exported": "false"},
    )
