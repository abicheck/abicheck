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

"""C++20 constraint signatures of function declarations (ADR-063 Phase 2).

``template<class T> requires C1<T> void f();`` and the same declaration
constrained by ``C2<T>`` are two legal overloads that share scope, leaf
name, parameter list and template-parameter kinds, and an uninstantiated
template has no mangled name. Their only difference is the constraint, so
:func:`entity_id_for_function` needs it as a discriminator.

clang's JSON dump of a ``ConceptSpecializationExpr`` does not name the
concept. It does carry an ``ImplicitConceptSpecializationDecl`` child whose
``loc`` is the source location of the named ``ConceptDecl`` itself
(verified on clang 18: the text dump prints ``Concept 0x... 'C1'`` on the
expression and ``<line:1:41>`` -- the concept's name token -- on the child).
:class:`FunctionConstraintIndex` keys every ``ConceptDecl`` by that location
and resolves each use back to the concept's namespace-qualified name, so no
header source text is read. A location no ``ConceptDecl`` (or more than one
differently-named one) owns resolves to ``"?"``: the overloads may then still
collide, which is the pre-existing behaviour, never a fabricated identity.

Every other constraint node is serialized structurally (kind, operator,
literal value, canonicalized type spelling, referenced name). Template
parameters are written by depth/index or canonicalized by position, so a
pure template-parameter rename keeps the signature. Parentheses are dropped.
Each constraint is tagged by where it was written -- ``param<i>`` (a
type-constraint on the i-th template parameter), ``requires`` (the
template's requires-clause) or ``trailing`` (a trailing requires-clause) --
because clang treats ``template<C T>`` and ``template<class T> requires C<T>``
as two distinct templates.

A declaration with no constraint gets ``()``, so every unconstrained
function's ``EntityId`` is unchanged.
"""

from __future__ import annotations

from typing import Any

from ....model.identity import canonicalize_type_param_references
from .ast_inner import ast_inner
from .function_template_defaults import FUNCTION_NODE_KINDS

__all__ = ["FunctionConstraintIndex"]

_TEMPLATE_PARAM_KINDS = frozenset(
    {"TemplateTypeParmDecl", "NonTypeTemplateParmDecl", "TemplateTemplateParmDecl"}
)
#: Containers a ``ConceptDecl`` can sit in (concepts are namespace-scope only).
_NAMESPACE_LIKE = frozenset(
    {"TranslationUnitDecl", "NamespaceDecl", "LinkageSpecDecl", "ExportDecl"}
)
#: Scalar keys that are AST bookkeeping, not part of what a constraint says.
_IGNORED_KEYS = frozenset(
    {
        "id",
        "kind",
        "loc",
        "range",
        "valueCategory",
        "isReferenced",
        "isUsed",
        "decl",
        "inner",
        "previousDecl",
        "parentDeclContextId",
        "isImplicit",
    }
)
_AMBIGUOUS = object()


def _loc_key(loc: Any) -> tuple[Any, ...] | None:
    """A hashable key for a (materialized) clang source location."""
    if not isinstance(loc, dict):
        return None
    if "spellingLoc" in loc or "expansionLoc" in loc:
        parts = (loc.get("spellingLoc") or {}, loc.get("expansionLoc") or {})
        return tuple((p.get("file"), p.get("offset")) for p in parts)
    if "offset" not in loc:
        return None
    return ((loc.get("file"), loc.get("offset")),)


def _is_expression(node: Any) -> bool:
    kind = node.get("kind", "") if isinstance(node, dict) else ""
    return kind.endswith(("Expr", "Operator"))


class FunctionConstraintIndex:
    """``id(function node) -> constraint nodes`` plus the concept-name index,
    built in one pass over *root*; :meth:`signature` serializes on demand."""

    def __init__(self, root: dict[str, Any]) -> None:
        self._concepts: dict[tuple[Any, ...], Any] = {}
        self._by_function: dict[int, list[tuple[str, dict[str, Any]]]] = {}
        stack: list[tuple[Any, tuple[str, ...]]] = [(root, ())]
        while stack:
            node, ns = stack.pop()
            if not isinstance(node, dict):
                continue
            kind = node.get("kind")
            children = ast_inner(node)
            if kind == "ConceptDecl":
                self._record_concept(node, ns)
                continue
            if kind == "FunctionTemplateDecl":
                self._record_template(children)
            elif kind in FUNCTION_NODE_KINDS:
                trailing = [("trailing", c) for c in children if _is_expression(c)]
                if trailing:
                    self._by_function.setdefault(id(node), []).extend(trailing)
                continue  # a body holds no further declaration of interest
            child_ns = ns
            if kind == "NamespaceDecl":
                child_ns = (*ns, str(node.get("name") or "(anonymous namespace)"))
            stack.extend((c, child_ns) for c in children)

    def _record_concept(self, node: dict[str, Any], ns: tuple[str, ...]) -> None:
        key = _loc_key(node.get("loc"))
        if key is None:
            return
        name = "::".join((*ns, str(node.get("name", ""))))
        if self._concepts.get(key, name) != name:
            self._concepts[key] = _AMBIGUOUS
        else:
            self._concepts[key] = name

    def _record_template(self, children: list[Any]) -> None:
        found: list[tuple[str, dict[str, Any]]] = []
        position = 0
        for child in children:
            if not isinstance(child, dict):
                continue
            kind = child.get("kind")
            if kind in _TEMPLATE_PARAM_KINDS:
                found.extend(
                    (f"param{position}", c)
                    for c in ast_inner(child)
                    if isinstance(c, dict)
                    and c.get("kind") == "ConceptSpecializationExpr"
                )
                position += 1
            elif _is_expression(child):
                found.append(("requires", child))
        if not found:
            return
        for child in children:
            if isinstance(child, dict) and child.get("kind") in FUNCTION_NODE_KINDS:
                # Template-level constraints precede the trailing ones the
                # function node itself records when the walk reaches it.
                self._by_function.setdefault(id(child), [])[:0] = found

    def signature(
        self, node: dict[str, Any], type_param_names: tuple[str, ...] = ()
    ) -> tuple[str, ...]:
        """The tagged constraint signature of function *node*, or ``()``."""
        return tuple(
            f"{tag}:{self._serialize(expr, type_param_names)}"
            for tag, expr in self._by_function.get(id(node), ())
        )

    def _concept_name(self, specialization: dict[str, Any]) -> str:
        name = self._concepts.get(_loc_key(specialization.get("loc")) or ())
        return name if isinstance(name, str) else "?"

    def _serialize(self, node: Any, names: tuple[str, ...]) -> str:
        if not isinstance(node, dict):
            return ""
        kind = str(node.get("kind", ""))
        inner = [c for c in ast_inner(node) if isinstance(c, dict)]
        if kind == "ParenExpr" and len(inner) == 1:
            return self._serialize(inner[0], names)
        if kind == "TemplateTypeParmType":
            return f"tparm-{node.get('depth')}-{node.get('index')}"
        if kind == "ConceptSpecializationExpr":
            for child in inner:
                if child.get("kind") == "ImplicitConceptSpecializationDecl":
                    # Its arguments are written in canonical
                    # `type-parameter-D-I` form, independent of renames.
                    args = ",".join(self._serialize(a, names) for a in ast_inner(child))
                    return f"concept {self._concept_name(child)}<{args}>"
        parts = [kind]
        for key, value in node.items():
            if key in _IGNORED_KEYS:
                continue
            if isinstance(value, (str, int, bool)):
                if key == "name" and kind == "ParmVarDecl":
                    continue  # a requires-expression parameter's own name
                parts.append(
                    f"{key}={canonicalize_type_param_references(str(value), names)}"
                )
            elif key == "referencedDecl" and value.get("kind") == "ParmVarDecl":
                parm_type = str(value.get("type", {}).get("qualType", ""))
                canonical = canonicalize_type_param_references(parm_type, names)
                parts.append(f"{key}=parm:{canonical}")
            elif isinstance(value, dict):
                spelled = (
                    value.get("qualType") if "qualType" in value else value.get("name")
                )
                if isinstance(spelled, str):
                    parts.append(
                        f"{key}={canonicalize_type_param_references(spelled, names)}"
                    )
        body = ",".join(self._serialize(c, names) for c in inner)
        return f"{' '.join(parts)}({body})" if inner else " ".join(parts)
