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

"""Every constructor/destructor linker name a clang header AST declares.

clang reports a ``mangledName`` (the complete-object ``C1``/``D1`` spelling)
on every non-dependent ``CXXConstructorDecl``/``CXXDestructorDecl``, whether
or not the binary exports it. That is identity evidence taken from the
*headers*: two versions whose declaration did not change report the same
name, so a castxml placeholder resolved through it keeps one node id across
versions even when the export table changes
(``model.snapshot_identity_table``). Both header-graph AST projections --
the in-memory one and the streaming one -- collect it through
:func:`collect_special_member_names`, so they agree.
"""

from __future__ import annotations

from typing import Any

from ..model.mangled_name import strip_macho_itanium_decoration

__all__ = ["collect_special_member_names"]

_SPECIAL_MEMBER_KINDS = frozenset({"CXXConstructorDecl", "CXXDestructorDecl"})


def collect_special_member_names(node: Any, out: set[str]) -> None:
    """Add every ctor/dtor ``mangledName`` under *node* to *out*.

    Iterative, so a deep AST cannot exhaust the recursion limit."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if cur.get("kind") in _SPECIAL_MEMBER_KINDS:
                mangled = cur.get("mangledName")
                if isinstance(mangled, str) and mangled.startswith(("_Z", "__Z")):
                    out.add(strip_macho_itanium_decoration(mangled))
            inner = cur.get("inner")
            if isinstance(inner, list):
                stack.extend(inner)
        elif isinstance(cur, list):
            stack.extend(cur)
