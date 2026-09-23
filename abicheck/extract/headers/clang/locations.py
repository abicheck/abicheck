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

"""Make every source location in a ``clang -ast-dump=json`` tree explicit.

clang's JSON writer omits a location's ``file`` when it equals the file of
the *previously written* location, and its ``line`` when that is unchanged
(``JSONNodeDumper::writeBareSourceLocation``). "Previously written" means in
document order, across *every* location object: a declaration's ``loc``, both
ends of every ``range``, macro ``spellingLoc``/``expansionLoc`` pairs, and the
locations inside expression and statement subtrees.

A reader that tracks the "current file" only from the nodes it chooses to
visit therefore drifts. ``dumper_clang._walk`` deliberately does not descend
into function bodies, so a file change clang wrote inside one is never seen,
and the next declaration inherits the wrong file. Measured on Intel SVS:
``svs::threads::CACHE_LINE_BYTES`` (``threadlocal.h:55``) was recorded at
``/usr/include/c++/13/concepts:55``, and about 200 SVS functions were
misattributed the same way. Once dependency scoping keyed on that file, they
were dropped as libstdc++ declarations.

:func:`materialize_locations` replays the writer's own state machine once,
over the whole tree, and writes the inherited ``file``/``line`` into each
location that omitted them. After it runs, no consumer needs sticky state.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, TypeVar

_T = TypeVar("_T")

__all__ = ["materialize_locations"]


def _is_location(node: dict[str, Any]) -> bool:
    # A bare source location always carries `offset` and `col`. The
    # `includedFrom` object (`{"file": ...}`) carries neither, and must not
    # move the state: clang writes it without updating `LastLocFilename`.
    return "offset" in node and "col" in node


def materialize_locations(root: _T) -> _T:
    """Fill every omitted ``file``/``line`` in *root*, in place, and return it.

    Iterative, in document order (JSON object key order is preserved by
    :mod:`json`), so arbitrarily deep ASTs cannot hit the recursion limit.
    Idempotent; anything that is not a dict/list (a derived-artifact marker
    handed back by the AST cache) passes through untouched.
    """
    current_file: Any = None
    current_line: Any = None
    stack: list[Iterator[Any]] = [iter((root,))]
    while stack:
        item = next(stack[-1], _DONE)
        if item is _DONE:
            stack.pop()
            continue
        if isinstance(item, dict):
            if _is_location(item):
                if "file" in item:
                    current_file = item["file"]
                elif current_file is not None:
                    item["file"] = current_file
                if "line" in item:
                    current_line = item["line"]
                elif current_line is not None:
                    item["line"] = current_line
            stack.append(v for k, v in item.items() if k != "includedFrom")
        elif isinstance(item, list):
            stack.append(iter(item))
    return root


_DONE = object()
