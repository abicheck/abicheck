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

"""One ``EntityId`` for an out-of-line definition and its in-class declaration.

``struct A { template<class T> void f(T); }; template<class T> void A::f(T) {}``
makes clang emit two ``FunctionTemplateDecl``/``CXXMethodDecl`` pairs: the
in-class one, lexically inside ``A``, and the out-of-line one, lexically at
namespace scope. The out-of-line node carries ``parentDeclContextId`` (its
semantic owner, ``A``) and ``previousDecl`` (the id of the in-class
declaration it redeclares). ``_ClangAstParser._walk`` builds scope from
lexical nesting, so the out-of-line node got the namespace's scope -- and,
for a member template of a class template, it also lacks the enclosing
template's parameter list, so its canonical parameter positions differ.

clang's ``previousDecl`` link already states that both nodes are one entity,
so :class:`RedeclarationIdentity` hands the out-of-line node the identity
computed for the declaration it redeclares, rather than reconstructing the
semantic scope (which would also need the outer template headers clang does
not dump). Only a node with BOTH links is touched -- an ordinary
same-scope redeclaration keeps the identity it computes itself -- and when
the earlier declaration was never parsed (filtered as builtin or dependency)
the node keeps its own identity, the behaviour before this module existed.
"""

from __future__ import annotations

from typing import Any, TypeVar

__all__ = ["RedeclarationIdentity"]

_Id = TypeVar("_Id")


class RedeclarationIdentity:
    """Per-parse map from a clang decl id to the identity given to it.

    Declarations are resolved in walk (document) order, and C++ requires the
    in-class declaration to precede an out-of-line definition, so the
    earlier identity is always recorded first.
    """

    __slots__ = ("_by_decl_id",)

    def __init__(self) -> None:
        self._by_decl_id: dict[str, Any] = {}

    def resolve(self, node: dict[str, Any], own: _Id) -> _Id:
        """The identity for *node*: its redeclared entity's when *node* is an
        out-of-line redeclaration of one already resolved, else *own*."""
        resolved = own
        previous = node.get("previousDecl")
        if node.get("parentDeclContextId") and isinstance(previous, str):
            resolved = self._by_decl_id.get(previous, own)
        decl_id = node.get("id")
        if isinstance(decl_id, str):
            self._by_decl_id[decl_id] = resolved
        return resolved
