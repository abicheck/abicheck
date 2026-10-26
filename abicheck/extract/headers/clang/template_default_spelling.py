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

"""Normalized spelling of a template-parameter default (ADR-085 D1).

The clang walk records the default of every parameter of a class-template
pattern. A type default is its ``qualType``, which :mod:`templates` already
reads. The two other kinds are spelled here:

* a **non-type** default from its expression: a literal by value, a named
  constant by name, an enumerator qualified by its enum type, an operator
  structurally (``(lhs op rhs)``);
* a **template-template** default from its source name, since clang's JSON
  carries no name for a ``template`` argument (only its source range).

Any default outside that set (a cast, a call, ``sizeof``, a dependent form) is
not spelled. The caller then records the whole pattern as unrecorded rather
than as a spelling that could be wrong.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .ast_inner import ast_inner

__all__ = ["_template_param_default_spellings"]


#: A plain, possibly ``::``-qualified name: the only shape a template-template
#: default is recorded as. Any other source text is not spelled.
_PLAIN_TEMPLATE_NAME = re.compile(r"(?:::)?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*")
#: Literal expressions; their ``value`` is the normalized spelling.
_LITERAL_EXPR_KINDS = frozenset(
    {"IntegerLiteral", "CharacterLiteral", "FloatingLiteral", "CXXBoolLiteralExpr"}
)
#: Wrappers whose spelling is their single operand's (no node of their own).
_TRANSPARENT_EXPR_KINDS = frozenset({"ParenExpr", "ImplicitCastExpr", "ConstantExpr"})
#: Declarations a non-type default may name: variables, enumerators, and
#: earlier template parameters (renamed by :func:`_positional_row`).
_REFERENCED_DECL_KINDS = frozenset(
    {"VarDecl", "EnumConstantDecl", "NonTypeTemplateParmDecl"}
)


def _expression_spelling(node: dict[str, Any]) -> str | None:
    """Normalized spelling of a non-type default expression, or ``None``.

    A literal spells as its value, a named constant as its name (an enumerator
    qualified by its enum type), and an operator as ``(lhs op rhs)``. Any other
    expression shape -- a cast, a call, ``sizeof``, a dependent form -- is
    ``None``: the caller then records the template as not recorded, rather than
    as a spelling that could be wrong. The spelling is at the level of names,
    as a type spelling is: a changed constant with an unchanged name is not seen.
    """
    kind = node.get("kind")
    operands = [c for c in ast_inner(node) if isinstance(c, dict)]
    if kind in _LITERAL_EXPR_KINDS:
        value = node.get("value")
        if isinstance(value, bool):
            return "true" if value else "false"
        return None if value is None else str(value)
    if kind in _TRANSPARENT_EXPR_KINDS and len(operands) == 1:
        return _expression_spelling(operands[0])
    if kind == "DeclRefExpr":
        decl = node.get("referencedDecl") or {}
        name = decl.get("name")
        if not name or decl.get("kind") not in _REFERENCED_DECL_KINDS:
            return None
        if decl.get("kind") == "EnumConstantDecl":
            enum_type = (node.get("type") or {}).get("qualType")
            return f"{enum_type}::{name}" if enum_type else None
        return str(name)
    opcode = node.get("opcode")
    if kind == "UnaryOperator" and len(operands) == 1 and opcode:
        if node.get("isPostfix"):
            return None
        operand = _expression_spelling(operands[0])
        return None if operand is None else f"({opcode}{operand})"
    if kind == "BinaryOperator" and len(operands) == 2 and opcode:
        lhs = _expression_spelling(operands[0])
        rhs = _expression_spelling(operands[1])
        return None if lhs is None or rhs is None else f"({lhs} {opcode} {rhs})"
    return None


def _template_name_spelling(
    arg: dict[str, Any], source_span: Callable[[str, int, int], str | None]
) -> str | None:
    """Spelling of a template-template default, read from its source text.

    clang's JSON carries no name for a ``template`` argument, only its source
    range. The span runs from the first to the last token of the range, and is
    read through *source_span* (byte offsets into the file the range names).
    It must be a plain name; anything else, including a span a macro expanded
    into, is ``None``.
    """
    range_ = arg.get("range") or {}
    begin = range_.get("begin") or {}
    end = range_.get("end") or {}
    path = begin.get("file")
    start = begin.get("offset")
    stop = end.get("offset")
    tok_len = end.get("tokLen")
    if not (
        isinstance(path, str)
        and isinstance(start, int)
        and isinstance(stop, int)
        and isinstance(tok_len, int)
    ):
        return None
    text = source_span(path, start, stop + tok_len)
    if text is None or not _PLAIN_TEMPLATE_NAME.fullmatch(text):
        return None
    return text


def _template_param_default_spellings(
    class_template_decl: dict[str, Any],
    source_span: Callable[[str, int, int], str | None],
) -> list[str | None] | None:
    """Per-position spelled defaults of a ``ClassTemplateDecl``, for every
    parameter kind (ADR-085 D1), or ``None`` when some default is present but
    its spelling is not read.

    This is the recorded fact. :func:`_template_param_defaults` is the narrower
    base-resolution row (it deliberately reads a non-type default as ``None``)
    and is not changed by this. A type default is its ``qualType``; a non-type
    default is :func:`_expression_spelling` of its expression; a template-
    template default is :func:`_template_name_spelling` of its source name.

    ``None`` for the whole pattern, not a ``None`` entry, when any default is
    unspelled: a ``None`` entry means "no default", so an unspelled default
    recorded that way would read as a change or a match it is not.
    """
    defaults: list[str | None] = []
    for child in ast_inner(class_template_decl):
        if not isinstance(child, dict):
            continue
        kind = child.get("kind")
        if kind == "TemplateTypeParmDecl":
            default = child.get("defaultArg")
            spelling = None
            if isinstance(default, dict):
                type_obj = default.get("type")
                if isinstance(type_obj, dict) and type_obj.get("qualType"):
                    spelling = str(type_obj["qualType"])
            defaults.append(spelling)
            continue
        if kind not in ("NonTypeTemplateParmDecl", "TemplateTemplateParmDecl"):
            break
        if child.get("defaultArg") is None:
            defaults.append(None)
            continue
        arguments = [
            c
            for c in ast_inner(child)
            if isinstance(c, dict) and c.get("kind") == "TemplateArgument"
        ]
        if not arguments:
            return None
        # A template-template parameter's own nested parameter list precedes
        # its default argument, so the default is the last TemplateArgument.
        arg = arguments[-1]
        if kind == "NonTypeTemplateParmDecl":
            operands = [c for c in ast_inner(arg) if isinstance(c, dict)]
            spelled = _expression_spelling(operands[0]) if operands else None
        else:
            spelled = _template_name_spelling(arg, source_span)
        if spelled is None:
            return None
        defaults.append(spelled)
    return defaults
