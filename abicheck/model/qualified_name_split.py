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

"""Template-nesting-aware ``"::"`` splitting for a fully-qualified C++ name
-- the one primitive both ``qualified_name_segments.py`` (the ``compare``
layer's own namespace-segment splitter) and a producer building a typed
``ScopePath`` straight from a flat qualified-name STRING (rather than
walking a tree/AST that already carries scope structure, as DWARF's DIE
walk and the two header-AST backends' own AST walks do) need.

Lives in ``model/`` rather than at the package root specifically so
``extract`` may depend on it (``extract -> model``, ADR-061) --
``qualified_name_segments.py`` itself belongs to the ``compare`` layer, which
``extract`` may not import (confirmed by ``scripts/check_architecture.py``
failing on exactly that edge; see ``extract/headers/scope_segments.py``'s own
docstring for the identical constraint on that module's ``version_suffix``
case). Splitting a fully-qualified name back into segments is pure text
processing with no ``compare``-specific knowledge in it at all -- unlike
``version_suffix``'s inline-namespace-version *interpretation*, which
legitimately belongs to ``compare`` -- so extracting just this primitive
down to a leaf module both layers can share is a mechanical move, not a
compare-layer migration.

``qualified_name_segments.raw_segments`` now delegates here rather than
keeping its own independent copy of this loop -- two copies of the same
bracket-depth-aware splitting logic is exactly the "two independently
constructible representations of the same fact" shape this codebase's own
governing invariant (ADR-063) exists to forbid elsewhere; there is no reason
identity's namesake splitting primitive should be an exception.

Leaf module: no imports beyond the stdlib.
"""

from __future__ import annotations

__all__ = ["split_top_level_scopes"]


def split_top_level_scopes(qualified: str) -> list[str]:
    """Split *qualified* on ``"::"`` at template-nesting depth zero only,
    keeping each segment's own template arguments intact.

    ``ns::Map<std::pair<int, int>>::iterator`` yields three segments
    (``["ns", "Map<std::pair<int, int>>", "iterator"]``) -- the ``::``
    inside the argument list is not a separator, since bracket depth is
    tracked via a plain ``<``/``>`` counter (matching this splitter's
    existing use: real qualified type/function names, not arbitrary text
    that might contain an unrelated ``<``/``>`` comparison operator pair --
    a caller with that concern needs a bracket-KIND-aware scanner instead,
    e.g. :func:`~abicheck.extract.semantic_normalizer_artifacts.
    has_unresolved_component`'s own).

    >>> split_top_level_scopes("ns::Map<std::pair<int, int>>::iterator")
    ['ns', 'Map<std::pair<int, int>>', 'iterator']
    >>> split_top_level_scopes("Widget")
    ['Widget']
    >>> split_top_level_scopes("")
    []
    """
    if not qualified:
        return []
    if "::" not in qualified and "<" not in qualified:
        return [qualified]
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(qualified)
    while i < n:
        ch = qualified[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            if depth > 0:
                depth -= 1
        elif depth == 0 and ch == ":" and i + 1 < n and qualified[i + 1] == ":":
            if buf:
                out.append("".join(buf).strip())
                buf = []
            i += 2
            continue
        buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf).strip())
    return [s for s in out if s]
