# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Clang call sites passing an exact enumerator as a direct argument."""

from __future__ import annotations

from typing import Any

from ..model.graph_facts import CONF_HIGH
from .type_graph import EDGE_DECL_REFERENCES_DECL, TypeEdge
from .type_graph_index import _AstIndexes


def _direct_argument_ref(node: Any) -> str | None:
    """A declaration id passed directly, possibly parenthesized or converted."""
    while node.get("kind") in {"ImplicitCastExpr", "ParenExpr"}:
        children = node.get("inner", ())
        if len(children) != 1:
            return None
        node = children[0]
    if node.get("kind") == "DeclRefExpr":
        identifier = node.get("referencedDecl", {}).get("id")
        return identifier if isinstance(identifier, str) else None
    return None


def enum_call_arguments(
    node: dict[str, Any], caller: str, edges: list[TypeEdge], idx: _AstIndexes
) -> None:
    children = node.get("inner", ())
    if not children:
        return
    callee = idx.function_symbols.get(_direct_argument_ref(children[0]) or "")
    if callee:
        for argument in children[1:]:
            member = idx.enum_members.get(_direct_argument_ref(argument) or "")
            if member:
                edges.append(
                    TypeEdge(
                        caller,
                        member,
                        EDGE_DECL_REFERENCES_DECL,
                        CONF_HIGH,
                        "enum_argument",
                        argument_callees=(callee,),
                    )
                )
