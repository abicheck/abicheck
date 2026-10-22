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

"""Which declared pattern a class-template specialization was instantiated
from, read from the structure of a ``clang -ast-dump=json`` tree.

clang nests an *implicit* instantiation's complete definition directly under
the first ``ClassTemplateDecl`` of its template, and gives it the location of
the pattern it was instantiated from (``Sema::InstantiateClass``): the primary
template's class-head, or the selected partial specialization's. An explicit
specialization, an explicit instantiation and an ``extern template`` appear
there only as an empty stub, their definition elsewhere. So:

* a complete nested specialization whose location equals a class-head of the
  primary template (any redeclaration of it) and no partial specialization's
  -> instantiated from the primary (:data:`ORIGIN_PRIMARY`);
* one whose location equals exactly one partial specialization of the same
  template, and no primary class-head -> instantiated from that partial
  specialization (:data:`ORIGIN_PARTIAL`, with its pattern signature);
* anything else (a stub, an incomplete specialization, a location two
  patterns share, no location) -> no answer.

The location is compared as its offsets (``offset``, and the
``spellingLoc``/``expansionLoc`` offsets of a macro-produced declaration),
never its text, so a class-head a macro produces is read like any other. File
names are not compared (clang writes them only when they change), so two
patterns in different files at the same offsets read as ambiguous: no answer.

The index also lists every declared partial specialization
(:meth:`ClassOriginIndex.declared_partials`), instantiated or not, which is how
a later comparison knows a partial specialization is still declared.

A leaf module, like ``template_graph_value_decls``: ``template_graph`` passes
its own pattern-signature reader in instead of this module importing it back.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

__all__ = [
    "ORIGIN_PARTIAL",
    "ORIGIN_PRIMARY",
    "PARTIAL_PATTERN_KIND",
    "ClassOriginIndex",
    "LocKey",
    "loc_key",
]

ORIGIN_PRIMARY = "primary"
ORIGIN_PARTIAL = "partial"

#: ``TemplateInstantiation.kind`` of a declared partial-specialization pattern
#: (:meth:`ClassOriginIndex.declared_partials`): no instantiation, only the
#: ``template_decl://<template>#<signature>`` node it keeps in the graph.
PARTIAL_PATTERN_KIND = "partial_pattern"

#: ``(offset, spellingLoc.offset, expansionLoc.offset)``.
LocKey = tuple[int | None, int | None, int | None]

_CLASS_TEMPLATE = "ClassTemplateDecl"
_SPECIALIZATION = "ClassTemplateSpecializationDecl"
_PARTIAL = "ClassTemplatePartialSpecializationDecl"
_NAMED_SCOPES = frozenset({"NamespaceDecl", "CXXRecordDecl", "RecordDecl"})
_TRANSPARENT_SCOPES = frozenset({"LinkageSpecDecl", "ExportDecl"})


def _offset(loc: Any) -> int | None:
    value = loc.get("offset") if isinstance(loc, dict) else None
    return value if isinstance(value, int) else None


def loc_key(node: dict[str, Any]) -> LocKey | None:
    """*node*'s location as offsets, or ``None`` when it carries none."""
    loc = node.get("loc")
    if not isinstance(loc, dict):
        return None
    key = (
        _offset(loc),
        _offset(loc.get("spellingLoc")),
        _offset(loc.get("expansionLoc")),
    )
    return None if key == (None, None, None) else key


class ClassOriginIndex:
    """One translation unit's class-template patterns and nested
    instantiations, fed each top-level declaration by :meth:`walk`."""

    def __init__(self, partial_signature: Callable[[dict[str, Any]], str]) -> None:
        self._partial_signature = partial_signature
        self._primary_heads: defaultdict[str, set[LocKey]] = defaultdict(set)
        self._partials: defaultdict[str, dict[str, set[LocKey]]] = defaultdict(dict)
        self._nested: dict[str, tuple[str, LocKey]] = {}
        self._conflicting: set[str] = set()

    def walk(self, node: Any, scope: tuple[str, ...] = ()) -> None:
        if not isinstance(node, dict):
            return
        kind = str(node.get("kind", ""))
        name = str(node.get("name") or "")
        if kind == _CLASS_TEMPLATE:
            if name:
                self._register_template(node, "::".join((*scope, name)))
            return
        if kind == _PARTIAL:
            signature = self._partial_signature(node)
            key = loc_key(node)
            if name and signature and key is not None:
                qname = "::".join((*scope, name))
                self._partials[qname].setdefault(signature, set()).add(key)
            return
        if (
            kind in _NAMED_SCOPES
            or kind in _TRANSPARENT_SCOPES
            or kind == "TranslationUnitDecl"
        ):
            if node.get("isImplicit"):
                return
            inner = (*scope, name) if kind in _NAMED_SCOPES and name else scope
            for child in node.get("inner", []) or []:
                self.walk(child, inner)

    def _register_template(self, node: dict[str, Any], qname: str) -> None:
        for child in node.get("inner", []) or []:
            if not isinstance(child, dict):
                continue
            kind = child.get("kind")
            key = loc_key(child)
            if key is None:
                continue
            if kind == "CXXRecordDecl" and not child.get("isImplicit"):
                self._primary_heads[qname].add(key)
            elif kind == _SPECIALIZATION and child.get("completeDefinition"):
                spec_id = str(child.get("id") or "")
                if not spec_id:
                    continue
                if self._nested.get(spec_id, (qname, key)) != (qname, key):
                    self._conflicting.add(spec_id)
                self._nested[spec_id] = (qname, key)

    def origin(self, spec_id: str) -> tuple[str, str]:
        """``(origin, partial signature)`` for the specialization *spec_id*:
        ``(ORIGIN_PRIMARY, "")``, ``(ORIGIN_PARTIAL, <signature>)``, or
        ``("", "")`` when the structure proves neither."""
        nested = self._nested.get(spec_id)
        if nested is None or spec_id in self._conflicting:
            return "", ""
        qname, key = nested
        partials = [
            sig for sig, keys in self._partials.get(qname, {}).items() if key in keys
        ]
        from_primary = key in self._primary_heads.get(qname, set())
        if from_primary and not partials:
            return ORIGIN_PRIMARY, ""
        if len(partials) == 1 and not from_primary:
            return ORIGIN_PARTIAL, partials[0]
        return "", ""

    def instantiation_fields(
        self, spec_id: str, offset_signature: str
    ) -> dict[str, str]:
        """``template_signature``/``instantiated_from`` for a class
        instantiation whose offset-matched partial signature (the template
        graph's own ``(file, offset)`` join) is *offset_signature*. A proven
        partial origin supplies the signature that join misses for a
        macro-produced pattern; an origin that disagrees with the join is
        dropped and the join kept."""
        origin, signature = self.origin(spec_id)
        if origin == ORIGIN_PARTIAL and offset_signature in ("", signature):
            return {"template_signature": signature, "instantiated_from": origin}
        if origin == ORIGIN_PRIMARY and not offset_signature:
            return {"template_signature": "", "instantiated_from": origin}
        return {"template_signature": offset_signature, "instantiated_from": ""}

    def declared_partials(self) -> list[tuple[str, str]]:
        """Every declared partial specialization, as ``(template qname,
        pattern signature)``, in a stable order."""
        return sorted(
            (qname, sig) for qname, sigs in self._partials.items() for sig in sigs
        )
