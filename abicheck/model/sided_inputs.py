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

"""The one rule for composing a shared path list with a side-specific one.

``--header``/``--include`` (and their release/no-baseline/bundle-facts
equivalents) take a *both-sides* value plus optional ``old=``/``new=``
additions. :func:`abicheck.cli_options.split_sided_paths` has always
documented that as "both-sides + per-side extra", but every consumer
implemented **replacement** -- a side that named anything of its own lost
the shared roots entirely. A ``--include shared`` holding a dependency
header, plus ``--include old=...``/``--include new=...`` naming each
version's own tree, resolved to ``[old]``/``[new]`` with ``shared``
dropped, so the parse failed on the dependency it was given.

The additive contract wins because it is the more expressive of the two:
a caller who wants genuinely disjoint search paths supplies only ``old=``
and ``new=`` and names nothing shared, which under this rule yields
exactly the disjoint lists replacement gave. There is no input for which
replacement is expressible and addition is not.

**Side-specific entries come first.** Include-path order is search order,
so a side's own root must still shadow a shared one carrying the same
header name; the shared roots stay available underneath as a fallback.

Lives in ``model`` because both a ``frontends``-layer CLI helper
(``cli_helpers_compare``) and a ``workflows``-layer resolver
(``workflows.release_inputs``) must apply the identical rule, and
``model`` is the innermost ring both may import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path


def compose_sided_paths(
    shared: Iterable[Path], side_specific: Iterable[Path]
) -> list[Path]:
    """*side_specific* then *shared*, order-preserving and de-duplicated.

    De-duplication is by the path as spelled (not ``resolve()``d): these
    values are handed to a compiler as ``-I``/``-H`` operands, and two
    spellings of one directory are still two distinct operands to a caller
    reading back a dry-run receipt. Repeating one changes nothing about the
    search, so collapsing an exact repeat is safe; collapsing two different
    spellings would silently rewrite what the user asked for.
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for path in (*side_specific, *shared):
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def resolve_per_side_inputs(
    headers: Sequence[Path],
    includes: Sequence[Path],
    old_headers_only: Sequence[Path],
    new_headers_only: Sequence[Path],
    old_includes_only: Sequence[Path],
    new_includes_only: Sequence[Path],
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """Both roles, both sides: ``(old_h, new_h, old_inc, new_inc)``.

    Lives beside the rule rather than in the compare CLI helper that used to
    own it: header roots and include roots compose identically, and the
    release fan-out applies the same composition to the same two roles.
    """
    return (
        compose_sided_paths(headers, old_headers_only),
        compose_sided_paths(headers, new_headers_only),
        compose_sided_paths(includes, old_includes_only),
        compose_sided_paths(includes, new_includes_only),
    )
