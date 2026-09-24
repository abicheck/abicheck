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

"""Top-level splitting primitives for :mod:`abicheck.model.signature_normalization`.

Moved out of that module (which had grown past the 800-line production cap)
because they are generic scanners over a parameter-list spelling -- depth-0
comma splitting and paren matching -- with no canonicalization policy of
their own. Other modules carry look-alike ``_split_top_level_commas`` helpers
with deliberately different bracket/quote rules; these are the exact ones
parameter-type canonicalization uses and are not a shared replacement.
"""

from __future__ import annotations

__all__ = ["_find_matching_paren", "_split_top_level_commas"]


def _split_top_level_commas(s: str) -> list[str]:
    """Split *s* on commas that sit at nesting depth 0 (outside any
    ``<...>``/``(...)``/``[...]``) -- the boundaries between a parameter
    list's own individual parameters, as opposed to a comma nested inside
    one parameter's own type (a template-argument list, a nested callback's
    own parameter list).
    """
    depth = 0
    parts: list[str] = []
    current: list[str] = []
    for ch in s:
        if ch in "<([":
            depth += 1
            current.append(ch)
        elif ch in ">)]":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _find_matching_paren(s: str, open_idx: int) -> int:
    """Index of the ``)`` matching the ``(`` at *open_idx* (which must
    itself be ``"("``), tracking only paren nesting -- ``s[open_idx]`` is
    always ``(`` at every call site. Defensively returns ``len(s)`` for a
    malformed, unmatched string rather than raising.
    """
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(s)
