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

import re
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

__all__ = [
    "collect_cxx_concept_constraints",
    "concept_constraint_entries",
    "public_concept_constraints",
]


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


#: A type-constraint before ``auto`` (``std::integral auto``,
#: ``std::convertible_to<int> auto``), not a cv-qualifier.
_AUTO_CONSTRAINT = re.compile(
    r"(?<![\w:])(?!const\b|volatile\b)(?:::)?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*"
    r"(?:<[^()]*?>)?\s+(?=auto\b)"
)


def _without_auto_constraints(spelled: str) -> str:
    """*spelled* with every type-constraint on an ``auto`` placeholder removed."""
    return _AUTO_CONSTRAINT.sub("", spelled)


def _type_param_names(children: list[Any]) -> tuple[str, ...]:
    return tuple(
        str(c.get("name") or "")
        for c in children
        if isinstance(c, dict) and c.get("kind") in _TEMPLATE_PARAM_KINDS
    )


class _Collector:
    def __init__(self, root: dict[str, Any]) -> None:
        self._index = FunctionConstraintIndex(root)
        #: ``(key, encoded constraint, declaring header)`` per declaration
        #: outside a compiler-builtin file, in walk order.
        self.entries: list[tuple[str, str, str]] = []
        self._walk(root, (), "")

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

    def _header(self, node: dict[str, Any], file: str) -> str:
        """The declaring header of *node* ("" for a builtin or unknown file)."""
        if not file or is_builtin_file(file):
            return ""
        line = _node_line(node)
        return header_from_location(f"{file}:{line}" if line else file) or ""

    def _record(self, key: str, value: str, header: str) -> None:
        self.entries.append((key, value, header))

    # -- concepts ------------------------------------------------------------

    def _concept(
        self,
        node: dict[str, Any],
        children: list[Any],
        scope: tuple[str, ...],
        file: str,
    ) -> None:
        header = self._header(node, file)
        if not header:
            return
        names = _type_param_names(children)
        exprs = [c for c in children if _is_expression(c)]
        atoms: list[str] = []
        for expr in exprs:
            atoms.extend(self._atoms(expr, names, symbolic_refs=False))
        name = "::".join((*scope, str(node.get("name") or "")))
        self._record(CONCEPT_KEY_PREFIX + name, encode_conjunctive(atoms), header)

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
        header = (
            "" if pattern is None else self._header(node, _node_file(pattern, file))
        )
        if pattern is None or not header:
            return
        name = "::".join((*scope, str(node.get("name") or "")))
        if kind == "FunctionTemplateDecl":
            constraints.extend(c for c in ast_inner(pattern) if _is_expression(c))
            # The key is the template's identity, not its constraint: an
            # abbreviated template's ``std::integral auto`` parameter is
            # spelled ``auto`` here, so constraining it pairs with the
            # unconstrained ``auto`` and is compared as a tightening.
            spelled = _without_auto_constraints(
                str((pattern.get("type") or {}).get("qualType", ""))
            )
            name += f"({canonicalize_type_param_references(spelled, names)})"
        atoms: list[str] = []
        for expr in constraints:
            atoms.extend(self._atoms(expr, names, symbolic_refs=True))
        self._record(TEMPLATE_KEY_PREFIX + name, encode_conjunctive(atoms), header)

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


def concept_constraint_entries(root: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every concept and template constraint of a clang ``-ast-dump=json``
    root, as ``(key, encoded constraint, declaring header)``, before any
    public-set filtering. JSON-serializable, so a castxml run can carry the
    entries of its clang join and filter them by its own public set."""
    return list(_Collector(root).entries)


def public_concept_constraints(
    entries: list[tuple[str, str, str]],
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
) -> dict[str, str]:
    """The table of the *entries* declared in a public header. A key two
    public declarations give different constraints is ambiguous and left
    out."""
    table: dict[str, str] = {}
    ambiguous: set[str] = set()
    for key, value, header in entries:
        origin = classify_origin(
            header, pub_header_segs, pub_dir_segs, have_public_set=True
        )
        if origin != ScopeOrigin.PUBLIC_HEADER:
            continue
        if table.get(key, value) != value:
            ambiguous.add(key)
        table[key] = value
    for key in ambiguous:
        table.pop(key, None)
    return dict(sorted(table.items()))


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
    return public_concept_constraints(
        concept_constraint_entries(root), pub_header_segs, pub_dir_segs
    )
