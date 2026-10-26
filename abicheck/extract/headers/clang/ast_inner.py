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

"""The one shape contract for a clang AST node's ``inner`` children.

Every clang-backend reader walks ``node["inner"]`` as a list. An absent key
is a leaf and reads as no children. A key that is present with any other
JSON type -- ``null`` included, which clang never emits -- is malformed: ``or []`` would coerce a string or a
mapping to an empty walk, and the projection would then read as a complete,
empty surface (ADR-028 D3: failed evidence is an explicit failure, never an
empty one). :func:`ast_inner` raises instead.
"""

from __future__ import annotations

from typing import Any


class MalformedClangAstError(ValueError):
    """A clang AST whose container shapes the readers assume are violated
    (``inner`` present but not a list, or a child that is not an object).

    One type for the header readers and the header-graph projection
    (``buildsource.header_graph_ast_projection.validate_ast_shape``), which
    apply the same rule, so every refusal path catches both."""


def ast_inner(node: Any) -> list[Any]:
    """Return the children of *node*, or ``[]`` when it has no ``inner`` key.

    Raises :class:`MalformedClangAstError` when ``inner`` is present and not
    a list (``null`` included, as ``validate_ast_shape`` rules).
    """
    if "inner" not in node:
        return []
    children = node["inner"]
    if not isinstance(children, list):
        raise MalformedClangAstError(
            f"clang AST node {node.get('kind')!r} has 'inner' of type "
            f"{type(children).__name__}, expected a list"
        )
    return children
