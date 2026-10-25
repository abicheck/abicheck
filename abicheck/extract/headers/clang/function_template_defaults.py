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

"""ADR-085 D1, function-template slice: the spelled default arguments of a
function template, read from the clang AST.

A function template's defaults live on its ``FunctionTemplateDecl``, but the
declaration that becomes a ``Function`` is the ``FunctionDecl`` (or method) nested
inside it. :func:`function_template_default_index` maps each such declaration to
its enclosing template's defaults in one pass over the AST, so the per-node
parser never has to see its parent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ....model import Fact
from .template_default_spelling import _template_param_default_spellings
from .template_param_indexes import _source_span_reader

#: The declaration kinds a ``FunctionTemplateDecl`` can wrap as its pattern.
FUNCTION_NODE_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)


def function_template_default_index(
    root: dict[str, Any],
) -> dict[int, tuple[str | None, ...] | None]:
    """``id(pattern node) -> spelled defaults`` for every function template of
    *root*.

    Keyed by object identity because the parser hands the same node objects
    back. A template with an unspelled default maps to ``None``, never to a
    partial tuple (see :func:`_template_param_default_spellings`): the node is
    a pattern whose defaults the clang backend could not record, which
    :func:`function_template_defaults_fact` reports as UNSUPPORTED so the
    assurance rollup names the gap instead of reading the pattern as an
    ordinary function.
    """
    span = _source_span_reader()
    index: dict[int, tuple[str | None, ...] | None] = {}
    stack: list[Any] = [root]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        children = node.get("inner") or []
        if node.get("kind") == "FunctionTemplateDecl":
            spelled = _template_param_default_spellings(node, span)
            for child in children:
                if isinstance(child, dict) and child.get("kind") in FUNCTION_NODE_KINDS:
                    index[id(child)] = None if spelled is None else tuple(spelled)
        stack.extend(children)
    return index


def function_template_defaults_fact(
    node: dict[str, Any],
    index: Mapping[int, tuple[str | None, ...] | None] | None,
) -> Fact[tuple[str | None, ...]] | None:
    """The defaults fact of a function-template pattern *node*.

    PRESENT with the spellings when every default was spelled; UNSUPPORTED when
    *node* is a pattern with a default the backend could not spell; ``None``
    (not collected) when *node* is not a function-template pattern at all.
    """
    if not index or id(node) not in index:
        return None
    spelled = index[id(node)]
    if spelled is None:
        return Fact.unsupported(
            "a template-parameter default of this function template could not "
            "be spelled from the clang AST",
            producer="clang",
        )
    return Fact.present(spelled)
