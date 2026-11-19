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

"""Whether a type name occurs in a type spelling as a whole identifier.

The affected-symbol enrichment (``diff_filtering._build_type_to_funcs`` and
``_build_type_embed_index``) relates a changed type to every function or
field whose type spelling mentions it. Its Aho-Corasick prefilter finds
substrings, so on its own a change to ``struct s1`` was attributed to
functions taking ``struct s10 *`` (formal/FRAGMENT.md, formal/
RECORD_EDIT_FRAGMENT.md). A hit counts only when the characters around one
of its occurrences are not identifier characters. ``::`` is a boundary on
purpose: ``ns::Widget`` mentions ``Widget``; telling same-named types in
different scopes apart is ``compare/type_symbol_disambiguation``'s job.
"""

from __future__ import annotations

import string

from ..model.execution_cache import memoized

#: Characters that continue an identifier. A non-ASCII letter also counts
#: (``isalnum``), checked only when the character is not in this set.
_IDENT = frozenset(string.ascii_letters + string.digits + "_")


@memoized(maxsize=1 << 16)
def occurs_as_identifier(name: str, text: str) -> bool:
    """True when *name* occurs in *text* with no identifier character
    immediately before or after that occurrence."""
    if not name:
        return False
    start = text.find(name)
    while start != -1:
        end = start + len(name)
        before = text[start - 1] if start else " "
        after = text[end] if end < len(text) else " "
        if not (before in _IDENT or before.isalnum()) and not (
            after in _IDENT or after.isalnum()
        ):
            return True
        start = text.find(name, start + 1)
    return False


def bounded_hits(hits: set[str], text: str) -> set[str]:
    """The prefilter's *hits* in *text* that occur as whole identifiers."""
    return {h for h in hits if occurs_as_identifier(h, text)}
