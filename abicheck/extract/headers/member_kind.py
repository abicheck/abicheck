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

"""``Function.member_kind_fact`` from each header backend's AST node kind.

The node kind is the one fact that says whether a function is a member: a
class member is a ``CXXMethodDecl`` (clang) / ``Method`` (castxml) wherever
it is defined, and a friend defined inside a class is a ``FunctionDecl`` /
``Function``. ``static`` on a member is a static member function. A node kind
outside the tables (a deduction guide) is not recorded.
"""

from __future__ import annotations

from typing import Any

from ...model import Fact, MemberKind

__all__ = ["castxml_member_kind", "clang_member_kind", "member_kind_fact"]

_CLANG_KINDS = {
    "FunctionDecl": MemberKind.FREE,
    "CXXMethodDecl": MemberKind.INSTANCE,
    "CXXConversionDecl": MemberKind.INSTANCE,
    "CXXConstructorDecl": MemberKind.CONSTRUCTOR,
    "CXXDestructorDecl": MemberKind.DESTRUCTOR,
}

_CASTXML_KINDS = {
    "Function": MemberKind.FREE,
    "OperatorFunction": MemberKind.FREE,
    "Method": MemberKind.INSTANCE,
    "OperatorMethod": MemberKind.INSTANCE,
    "Converter": MemberKind.INSTANCE,
    "Constructor": MemberKind.CONSTRUCTOR,
    "Destructor": MemberKind.DESTRUCTOR,
}


def member_kind_fact(
    kind: MemberKind | None, is_static: bool
) -> Fact[MemberKind] | None:
    """The fact for a node of member kind *kind* (``None``: not recorded)."""
    if kind is None:
        return None
    if kind is MemberKind.INSTANCE and is_static:
        kind = MemberKind.STATIC
    return Fact.present(kind)


def clang_member_kind(node: dict[str, Any]) -> Fact[MemberKind] | None:
    """From a clang JSON function node's ``kind`` and ``storageClass``."""
    return member_kind_fact(
        _CLANG_KINDS.get(str(node.get("kind"))), node.get("storageClass") == "static"
    )


def castxml_member_kind(tag: str, static: bool) -> Fact[MemberKind] | None:
    """From a castxml element's tag and its ``static`` attribute."""
    return member_kind_fact(_CASTXML_KINDS.get(tag), static)
