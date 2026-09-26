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

"""The one whole-AST traversal the template-parameter indexes share.

Split out of :mod:`.templates` (which imports everything here back) so that
module stays within its size budget; :data:`_SCOPE_NODE_KINDS` moved with it
because the walk is its first user here.
"""

from __future__ import annotations

from typing import Any

#: Shared with ``dumper_clang._ClangAstParser._walk``'s own public-surface
#: qualified-name building and ``dumper_clang_expr.py``'s own scope
#: tracking (both import it back from here) — kept as ONE definition
#: rather than independently-drifting copies.
_SCOPE_NODE_KINDS = frozenset(
    {"NamespaceDecl", "CXXRecordDecl", "RecordDecl", "LinkageSpecDecl"}
)


#: Every named ``ClassTemplateDecl`` of one AST with its scope-qualified
#: name, in document order -- see :func:`class_template_decls`.
ClassTemplateDecls = list[tuple[str, dict[str, Any]]]


def class_template_decls(root: dict[str, Any]) -> ClassTemplateDecls:
    """``(qualname, node)`` for every named ``ClassTemplateDecl`` under
    *root*, in document order, scope-tracked the way every template-param
    index below tracks it.

    The three indexes used to walk the whole AST once each (four times,
    counting the names walk the defaults index ran internally) only to act
    on these nodes. They now replay their own registration over this list:
    each walk's only state was its own index/ambiguity set, which is kept
    per index, and it visited these nodes in this order -- so the result is
    the same, for one whole-document traversal instead of four.
    """
    out: ClassTemplateDecls = []

    def walk(node: Any, scope: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = str(node.get("name") or "")
        if kind == "ClassTemplateDecl" and name:
            out.append(("::".join((*scope, name)) if scope else name, node))
        child_scope = (*scope, name) if kind in _SCOPE_NODE_KINDS and name else scope
        for child in node.get("inner", []) or []:
            walk(child, child_scope)

    walk(root, ())
    return out
