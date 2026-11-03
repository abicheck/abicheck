# Copyright 2026 Nikolay Petrov
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

"""Structured C++20 concept constraints: a conjunctive set of atoms.

A concept's constraint-expression is decomposed along its top-level
conjunctions -- ``A && B``, parentheses, and the requirements of a
``requires (params) { r1; r2; }`` expression, which are conjunctive by
definition -- into atoms. Each atom is an alpha-normalized structural
fingerprint (``clang_nodes._subtree_hash``), keyed for a requirement by the
requires-expression's parameter types, so renaming ``a``/``b`` or reordering
independent requirements yields the same set. A disjunction, a fold, a
nested concept-id or any other expression is one opaque atom: no attempt is
made to decide C++ constraint equivalence in general.

The set is encoded in ``SourceEntity.value`` as ``conj:<atom>,<atom>,...``
(sorted). A value without the prefix -- every snapshot written before this
encoding -- is an opaque fingerprint, and the comparison then knows only
*that* the constraint changed, never in which direction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..model import LayerConfidence
from ..source_abi import SourceAbiTu, SourceEntity
from .clang_node_predicates import _ClassifyContext, _location
from .clang_nodes import (
    _entity_names,
    _entity_ownership,
    _hash,
    _node_line,
    _qualified,
    _subtree_hash,
)

CONJUNCTIVE_PREFIX = "conj:"


def _param_ids_and_types(req_expr: Mapping[str, Any]) -> tuple[tuple[str, ...], str]:
    ids: list[str] = []
    types: list[str] = []
    for child in req_expr.get("inner", []) or []:
        if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
            cid = child.get("id")
            if isinstance(cid, str):
                ids.append(cid)
            types.append(str((child.get("type") or {}).get("qualType", "")))
    return tuple(ids), "|".join(types)


def _atoms(expr: Mapping[str, Any]) -> Iterable[str]:
    kind = expr.get("kind")
    inner = [c for c in expr.get("inner", []) or [] if isinstance(c, dict)]
    if kind == "ParenExpr" and len(inner) == 1:
        yield from _atoms(inner[0])
        return
    if kind == "BinaryOperator" and expr.get("opcode") == "&&" and len(inner) == 2:
        yield from _atoms(inner[0])
        yield from _atoms(inner[1])
        return
    if kind == "RequiresExpr":
        ids, types = _param_ids_and_types(expr)
        reqs = [c for c in inner if c.get("kind") != "ParmVarDecl"]
        if reqs:
            for req in reqs:
                yield _hash("concept-req", types, _subtree_hash(dict(req), ids))
            return
    yield _hash("concept-atom", _subtree_hash(dict(expr)))


def concept_constraint_value(concept_decl: Mapping[str, Any]) -> str | None:
    """``conj:...`` for a ``ConceptDecl``, or ``None`` when it has no
    constraint-expression child to decompose."""
    exprs = [
        c
        for c in concept_decl.get("inner", []) or []
        if isinstance(c, dict) and not str(c.get("kind", "")).endswith("Decl")
    ]
    if len(exprs) != 1:
        return None
    return CONJUNCTIVE_PREFIX + ",".join(sorted(set(_atoms(exprs[0]))))


def conjunctive_atoms(value: str | None) -> frozenset[str] | None:
    """The atom set a ``conj:`` value encodes; ``None`` for an opaque value."""
    if not value or not value.startswith(CONJUNCTIVE_PREFIX):
        return None
    body = value[len(CONJUNCTIVE_PREFIX) :]
    return frozenset(a for a in body.split(",") if a)


def emit_concept(
    node: dict[str, Any],
    ctx: _ClassifyContext,
    tu: SourceAbiTu,
    scope: list[str],
    file: str,
) -> None:
    visibility, origin, public = ctx.classify(file)
    if not public:
        return
    name = _qualified(scope, str(node.get("name", "")))
    constraint_hash = _subtree_hash(node)
    loc = _location(file, _node_line(node), origin)
    tu.functions.append(
        SourceEntity(
            id=_hash("concept", name, constraint_hash),
            kind="concept",
            qualified_name=name,
            signature_hash=_hash("concept-signature", name),
            body_hash=constraint_hash,
            value=concept_constraint_value(node) or constraint_hash,
            names=_entity_names(name),
            ownership=_entity_ownership(visibility, origin),
            source_location=loc,
            visibility=visibility,
            api_relevant=True,
            confidence=LayerConfidence.HIGH,
        )
    )
