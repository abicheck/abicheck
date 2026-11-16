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

"""Variable alignment facts from a clang JSON AST ``VarDecl`` node."""

from __future__ import annotations

from typing import Any

from ....model.abi_fundamental import AbiFundamental
from ....model.fact import Fact
from ...builtin_abi import BuiltinAbi, builtin_type_fact, natural_alignment_bits
from .ast_inner import ast_inner
from .context import qualtype, qualtype_desugared

__all__ = [
    "clang_var_alignment_bits",
    "clang_var_type_abi_fact",
    "clang_variable_alignment_map",
]

#: Scopes whose members castxml names by a mangled name (not file scope).
_SCOPE_KINDS = frozenset({"NamespaceDecl", "CXXRecordDecl"})

#: Nodes whose bodies cannot declare a namespace-scope variable: the walk does
#: not descend into them (a ``static`` local is not part of the public surface).
_BODY_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)


def _aligned_attrs(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        c
        for c in ast_inner(node)
        if isinstance(c, dict) and c.get("kind") == "AlignedAttr"
    ]


def _attr_alignment_bits(attr: dict[str, Any]) -> int | None:
    stack: list[Any] = list(ast_inner(attr))
    while stack:
        sub = stack.pop()
        if not isinstance(sub, dict):
            continue
        value = sub.get("value")
        if isinstance(value, int):
            return value * 8
        if isinstance(value, str) and value.isdigit():
            return int(value) * 8
        stack.extend(ast_inner(sub))
    return None


def _has_unresolved_aligned(node: dict[str, Any]) -> bool:
    """A bare ``aligned`` (no argument) means the target's maximum alignment,
    which the JSON AST does not spell: the alignment is unknown."""
    return any(_attr_alignment_bits(a) is None for a in _aligned_attrs(node))


def clang_var_alignment_bits(
    node: dict[str, Any], abi: BuiltinAbi | None = None
) -> int | None:
    """Explicit alignment (bits) from an AlignedAttr, else the natural one of a
    builtin type probed from the real compiler (``builtin_abi.
    natural_alignment_bits``, castxml's computed ``align`` for the same
    variable). A bare ``aligned`` and any non-builtin type stay ``None``."""
    attrs = _aligned_attrs(node)
    if attrs:
        return _attr_alignment_bits(attrs[0])
    # Not through a typedef: an aligned typedef desugars to its plain builtin.
    typ = node.get("type") or {}
    return (
        None
        if "desugaredQualType" in typ
        else natural_alignment_bits(qualtype(node), abi)
    )


def clang_variable_alignment_map(clang_root: dict[str, Any]) -> dict[str, int]:
    """Declared alignment (bits) of each variable whose declarations spell one.

    castxml 0.7 writes no alignment specifier on a ``Variable``; the clang AST
    of the same header records it on an ``AlignedAttr``. Keyed by the mangled
    name (the name for C). A declaration with no specifier contributes nothing;
    a bare ``aligned`` or two declarations that disagree leave the variable out,
    so its alignment stays a lower bound rather than a guessed value.
    """
    # Per key, the value each declaration states (None: unresolved).
    stated: dict[str, list[int | None]] = {}

    def walk(node: Any, nested: bool) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child, nested)
            return
        if not isinstance(node, dict) or node.get("kind") in _BODY_KINDS:
            return
        if node.get("kind") == "VarDecl" and not node.get("isImplicit"):
            # File-scope: castxml writes no mangled name, so the plain name is its
            # key. Mach-O clang mangles a C global as "_name", so mangledName would
            # never match. A namespace or class member is keyed by its mangled name.
            if nested:
                key = str(node.get("mangledName") or "")
            else:
                key = str(node.get("name") or "")
            values = [_attr_alignment_bits(a) for a in _aligned_attrs(node)]
            if key and values:
                if None in values:
                    stated.setdefault(key, []).append(None)
                elif any(values):
                    # An all-zero specifier (valid _Alignas(0)) has no effect:
                    # it states nothing, so the declaration contributes nothing.
                    stated.setdefault(key, []).append(
                        max(v for v in values if v is not None)
                    )
        inner_nested = nested or node.get("kind") in _SCOPE_KINDS
        walk(ast_inner(node), inner_nested)

    walk(ast_inner(clang_root), False)
    out: dict[str, int] = {}
    for key, values in stated.items():
        first = values[0]
        if first is not None and all(v == first for v in values):
            out[key] = first
    return out


def clang_var_type_abi_fact(
    node: dict[str, Any], abi: BuiltinAbi | None
) -> Fact[AbiFundamental] | None:
    """The builtin size/alignment fact for the variable's type, withheld when
    a bare ``aligned`` makes the real alignment unknown -- otherwise a consumer
    falling back to the natural alignment (``compare.declared_alignment``)
    would read an over-aligned variable as naturally aligned."""
    if _has_unresolved_aligned(node):
        return None
    return builtin_type_fact(qualtype_desugared(node), abi)
