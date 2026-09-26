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

"""Which requested headers a snapshot's header AST actually covers
(evidence-entity-model gap A3).

When a whole public include directory is parsed, clang's ``#error`` retry
(``dumper_clang_errors.retry_excluding_error_headers``) drops the top-level
headers that cannot be included directly and parses the rest. Their
declarations are then absent from the header AST for a reason that is not
"the library does not declare them" -- so every conclusion drawn from a
declaration's *absence* in that snapshot (a debug type no header names, an
edge the header AST did not produce) is ``unknown``, not ``proven_absent``.

Recorded in ``AbiSnapshot.ast_toolchain`` (an already-persisted free-form
provenance map) rather than a new schema field: no reader of a pre-existing
snapshot changes, and the answer for a snapshot that predates the record is
"not recorded", which this module reports as ``None``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = ["HEADER_PARSE_EXCLUDED_METADATA", "AllBut", "header_parse_excluded"]

#: ``ast_toolchain`` key holding the JSON list of dropped header paths.
HEADER_PARSE_EXCLUDED_METADATA = "header_parse_excluded"


def header_parse_excluded(snap: AbiSnapshot) -> tuple[str, ...]:
    """The headers *snap*'s header parse dropped (empty when none were, or
    the snapshot predates the record)."""
    raw = (getattr(snap, "ast_toolchain", None) or {}).get(
        HEADER_PARSE_EXCLUDED_METADATA
    )
    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except ValueError:
        return ()
    return tuple(str(v) for v in value) if isinstance(value, list) else ()


class AllBut:
    """``name in AllBut(s)`` iff *name* is not in *s* -- the keep-set a
    name-filter uses when only the excluded side is known, e.g. the
    dependency types of a snapshot whose header parse dropped headers (a
    DWARF type no parsed header names is then unknown, not a dependency's).
    """

    def __init__(self, excluded: set[str]) -> None:
        self._excluded = frozenset(excluded)

    def __contains__(self, name: object) -> bool:
        return name not in self._excluded
