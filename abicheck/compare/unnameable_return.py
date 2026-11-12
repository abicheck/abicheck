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

"""Whether a return-type spelling names a type, or stands for one no consumer can spell.

A deduced closure, an undeduced placeholder or an anonymous class has no name
a consumer can write; each header backend and DWARF spells it differently, and
``diff_templates.detect_unspecified_return_now_named`` reads all of them
through :func:`return_is_unspecified`.
"""

from __future__ import annotations

import re

__all__ = ["return_is_unspecified"]

# Return-type spellings we treat as "unspecified". These all share the
# property that the consumer must use ``auto`` to capture the result.
_UNSPECIFIED_RETURN_MARKERS: tuple[str, ...] = (
    "__lambda",
    "(unnamed",
    "(anonymous",
    "{lambda",
    "<lambda",
    # clang's closure type: ``(lambda at f.h:3:10)`` or ``(lambda:f.h#1)``.
    "(lambda",
    "decltype(",
)
#: Whole spellings a header backend writes for a type it could not name:
#: castxml 0.7 renders an undeduced (closure) return type as ``?``.
_UNSPECIFIED_RETURN_SPELLINGS = frozenset({"auto", "?"})
_AUTO_TOKEN_RE = re.compile(r"(?<![\w:])auto(?![\w:])")


def return_is_unspecified(rt: str) -> bool:
    if not rt:
        return False
    rt = rt.strip()
    if rt in _UNSPECIFIED_RETURN_SPELLINGS:
        return True
    # ``auto`` as a whole word (``const auto &``), never a part of a name
    # (``automaton *``).
    if _AUTO_TOKEN_RE.search(rt):
        return True
    return any(m in rt for m in _UNSPECIFIED_RETURN_MARKERS)
