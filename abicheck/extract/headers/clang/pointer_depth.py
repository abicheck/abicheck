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

"""Pointer nesting depth from a clang ``qualType`` spelling, counted the way
castxml's type graph counts ``PointerType`` nodes."""

from __future__ import annotations

__all__ = ["_pointer_depth"]


def _pointer_depth(type_str: str) -> int:
    """Best-effort pointer nesting depth from a written type spelling.

    castxml computes this from the type graph; on the clang path we count
    top-level ``*`` tokens in the ``qualType`` spelling (``const char *`` → 1,
    ``int **`` → 2), ignoring any inside template/array brackets. Stable for the
    pointer-depth-change detector even though it is a spelling heuristic.
    """
    group = _declarator_group(type_str)
    if group is not None:
        # ``R (*)(A)`` / ``T (**)[N]``: castxml's type graph is PointerType
        # nodes over the FunctionType/ArrayType, so only the ``*``s of the
        # parenthesised declarator count -- not the return type's, and not a
        # member pointer's ``C::*`` (an OffsetType/MethodType, not a pointer).
        depth = 0
        nest = 0
        for idx, ch in enumerate(group):
            if ch in "<[(":
                nest += 1
            elif ch in ">])":
                nest = max(0, nest - 1)
            elif ch == "*" and nest == 0 and not group[:idx].rstrip().endswith("::"):
                depth += 1
        return depth
    depth = 0
    bracket = 0
    for ch in type_str:
        if ch in "<[(":
            bracket += 1
        elif ch in ">])":
            bracket = max(0, bracket - 1)
        elif ch == "*" and bracket == 0:
            depth += 1
    return depth


def _declarator_group(type_str: str) -> str | None:
    """The inside of the first top-level ``( ... )`` immediately followed by
    ``(`` or ``[`` (a grouped function/array-pointer declarator), else None."""
    angle = 0
    idx = 0
    n = len(type_str)
    while idx < n:
        ch = type_str[idx]
        if ch == "<":
            angle += 1
        elif ch == ">":
            angle = max(0, angle - 1)
        elif ch == "(" and angle == 0:
            nest = 0
            for end in range(idx, n):
                if type_str[end] == "(":
                    nest += 1
                elif type_str[end] == ")":
                    nest -= 1
                    if nest == 0:
                        break
            else:
                return None
            if type_str[end + 1 :].lstrip()[:1] in ("(", "["):
                return type_str[idx + 1 : end]
            return None
        idx += 1
    return None
