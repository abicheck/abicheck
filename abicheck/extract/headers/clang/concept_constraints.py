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

"""C++20 concepts and template constraints of the public headers (L2, clang).

``AbiSnapshot.cxx_concept_constraints`` maps a subject to its constraint,
encoded as a conjunctive atom set (``model/concept_constraint.py``, the same
encoding and direction rule the L4 source lane uses):

* ``concept <qualified name>`` -- a ``ConceptDecl``'s constraint-expression;
* ``template <qualified name>`` (class template) or
  ``template <qualified name>(<function type>)`` (function template) -- the
  conjunction of every type-constraint (``template <C T>``), requires-clause
  and trailing requires-clause on the template; an unconstrained template is
  recorded with the empty set, so a requires-clause added later reads as a
  tightening.

An atom is the structural, template-parameter-rename-stable serialization of
one conjunct (``FunctionConstraintIndex.serialize``), hashed. A requirement of
a ``requires (params) { r1; r2; }`` expression is one atom each. A concept-id
in a *template's* constraint is kept symbolic -- ``ref:<concept>@<args>`` --
so the comparison (``compare/concept_constraints.py``) can expand it through
each side's own concept table: a concept renamed with the same body leaves
the template's expanded constraint unchanged, and a concept whose body
changed is reported once, on the concept.

Only declarations in a header the run classified as public are recorded.
Without a declared public set nothing can be classified, so the collector
returns ``None`` (not modelled) rather than an empty -- "no constraints" --
table.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ....model import ScopeOrigin
from ....model.concept_constraint import (
    CONCEPT_KEY_PREFIX,
    REF_ATOM_PREFIX,
    TEMPLATE_KEY_PREFIX,
    constraint_digest as _digest,
    encode_conjunctive,
)
from ....model.identity import canonicalize_type_param_references
from ...declaration_provenance import classify_origin, header_from_location
from .ast_inner import ast_inner
from .constraints import FunctionConstraintIndex
from .context import is_builtin_file

__all__ = ["collect_cxx_concept_constraints"]


_SCOPE_KINDS = frozenset({"NamespaceDecl", "CXXRecordDecl"})
_TRANSPARENT_KINDS = frozenset({"TranslationUnitDecl", "LinkageSpecDecl", "ExportDecl"})
_FUNCTION_KINDS = frozenset(
    {"FunctionDecl", "CXXMethodDecl", "CXXConstructorDecl", "CXXConversionDecl"}
)
_TEMPLATE_PARAM_KINDS = frozenset(
    {"TemplateTypeParmDecl", "NonTypeTemplateParmDecl", "TemplateTemplateParmDecl"}
)


def _is_expression(node: Any) -> bool:
    kind = node.get("kind", "") if isinstance(node, dict) else ""
    return kind.endswith(("Expr", "Operator"))


def _node_file(node: dict[str, Any], current: str) -> str:
    loc = node.get("loc")
    if isinstance(loc, dict):
        for part in (loc, loc.get("expansionLoc"), loc.get("spellingLoc")):
            if isinstance(part, dict):
                f = part.get("file")
                if isinstance(f, str) and f:
                    return f
    return current


def _node_line(node: dict[str, Any]) -> int | None:
    loc = node.get("loc")
    if not isinstance(loc, dict):
        return None
    for part in (loc, loc.get("expansionLoc"), loc.get("spellingLoc")):
        if isinstance(part, dict) and isinstance(part.get("line"), int):
            return int(part["line"])
    return None


def _type_param_names(children: list[Any]) -> tuple[str, ...]:
    return tuple(
        str(c.get("name") or "")
        for c in children
        if isinstance(c, dict) and c.get("kind") in _TEMPLATE_PARAM_KINDS
    )


class _Collector:
    def __init__(
        self,
        root: dict[str, Any],
        pub_header_segs: list[tuple[str, ...]],
        pub_dir_segs: list[tuple[str, ...]],
    ) -> None:
        self._index = FunctionConstraintIndex(root)
        self._pub = (pub_header_segs, pub_dir_segs)
        self.table: dict[str, str] = {}
        self._ambiguous: set[str] = set()
        self._walk(root, (), "")
        for key in self._ambiguous:
            self.table.pop(key, None)

    # -- traversal (pre-order, threading clang's sticky ``loc.file``) --------

    def _walk(self, node: Any, scope: tuple[str, ...], file: str) -> str:
        if not isinstance(node, dict):
            return file
        file = _node_file(node, file)
        kind = node.get("kind")
        children = ast_inner(node)
        if not node.get("isImplicit"):
            if kind == "ConceptDecl":
                self._concept(node, children, scope, file)
            elif kind in ("FunctionTemplateDecl", "ClassTemplateDecl"):
                self._template(node, kind, children, scope, file)
        child_scope = scope
        if kind in _SCOPE_KINDS or kind == "ClassTemplateDecl":
            name = str(node.get("name") or "")
            if not name:
                child_scope = (*scope, "(anonymous)")
            elif kind == "CXXRecordDecl" and scope and scope[-1] == name:
                child_scope = scope  # a template's own pattern record
            else:
                child_scope = (*scope, name)
        for child in children:
            file = self._walk(child, child_scope, file)
        return file

    def _public(self, node: dict[str, Any], file: str) -> bool:
        if not file or is_builtin_file(file):
            return False
        line = _node_line(node)
        header = header_from_location(f"{file}:{line}" if line else file)
        if not header:
            return False
        origin = classify_origin(header, *self._pub, have_public_set=True)
        return origin == ScopeOrigin.PUBLIC_HEADER

    def _record(self, key: str, value: str) -> None:
        if self.table.get(key, value) != value:
            self._ambiguous.add(key)
        self.table[key] = value

    # -- concepts ------------------------------------------------------------

    def _concept(
        self,
        node: dict[str, Any],
        children: list[Any],
        scope: tuple[str, ...],
        file: str,
    ) -> None:
        if not self._public(node, file):
            return
        names = _type_param_names(children)
        exprs = [c for c in children if _is_expression(c)]
        atoms: list[str] = []
        for expr in exprs:
            atoms.extend(self._atoms(expr, names, symbolic_refs=False))
        name = "::".join((*scope, str(node.get("name") or "")))
        self._record(CONCEPT_KEY_PREFIX + name, encode_conjunctive(atoms))

    # -- templates -----------------------------------------------------------

    def _template(
        self,
        node: dict[str, Any],
        kind: str,
        children: list[Any],
        scope: tuple[str, ...],
        file: str,
    ) -> None:
        names = _type_param_names(children)
        constraints: list[dict[str, Any]] = []
        pattern: dict[str, Any] | None = None
        for child in children:
            if not isinstance(child, dict):
                continue
            ckind = child.get("kind")
            if ckind in _TEMPLATE_PARAM_KINDS:
                constraints.extend(
                    c
                    for c in ast_inner(child)
                    if isinstance(c, dict)
                    and c.get("kind") == "ConceptSpecializationExpr"
                )
            elif _is_expression(child):
                constraints.append(child)
            elif ckind in _FUNCTION_KINDS or ckind == "CXXRecordDecl":
                pattern = pattern or child
        if pattern is None or not self._public(node, _node_file(pattern, file)):
            return
        name = "::".join((*scope, str(node.get("name") or "")))
        if kind == "FunctionTemplateDecl":
            constraints.extend(c for c in ast_inner(pattern) if _is_expression(c))
            spelled = str((pattern.get("type") or {}).get("qualType", ""))
            name += f"({canonicalize_type_param_references(spelled, names)})"
        atoms: list[str] = []
        for expr in constraints:
            atoms.extend(self._atoms(expr, names, symbolic_refs=True))
        self._record(TEMPLATE_KEY_PREFIX + name, encode_conjunctive(atoms))

    # -- atoms ---------------------------------------------------------------

    def _atoms(
        self, expr: dict[str, Any], names: tuple[str, ...], *, symbolic_refs: bool
    ) -> Iterator[str]:
        kind = expr.get("kind")
        inner = [c for c in ast_inner(expr) if isinstance(c, dict)]
        if kind == "ParenExpr" and len(inner) == 1:
            yield from self._atoms(inner[0], names, symbolic_refs=symbolic_refs)
            return
        if kind == "BinaryOperator" and expr.get("opcode") == "&&" and len(inner) == 2:
            for side in inner:
                yield from self._atoms(side, names, symbolic_refs=symbolic_refs)
            return
        if kind == "ConceptSpecializationExpr" and symbolic_refs:
            for child in inner:
                if child.get("kind") == "ImplicitConceptSpecializationDecl":
                    args = ",".join(
                        self._index.serialize(a, names) for a in ast_inner(child)
                    )
                    concept = self._index.concept_name(child)
                    yield f"{REF_ATOM_PREFIX}{concept}@{_digest(args)}"
                    return
        if kind == "RequiresExpr":
            params = "|".join(
                self._index.serialize(c, names)
                for c in inner
                if c.get("kind") == "ParmVarDecl"
            )
            reqs = [c for c in inner if c.get("kind") != "ParmVarDecl"]
            if reqs:
                for req in reqs:
                    yield _digest("req", params, self._index.serialize(req, names))
                return
        yield _digest("atom", self._index.serialize(expr, names))


def collect_cxx_concept_constraints(
    root: dict[str, Any] | None,
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> dict[str, str] | None:
    """The public headers' concept and template-constraint table, or ``None``
    when it cannot be modelled (no AST, or no declared public set)."""
    if not isinstance(root, dict) or not have_public_set:
        return None
    return dict(sorted(_Collector(root, pub_header_segs, pub_dir_segs).table.items()))
