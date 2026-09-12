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

"""Stamp ``declared_fact``/``exported_fact`` onto header-AST declarations.

The two facts (see ``model/declaration_surface.py``) need a producer, and
a header-AST backend is the only producer that can answer *both*: it holds
the parsed declaration **and** the artifact's observed export sets.

Deliberately derived from the backend's own already-computed
``Visibility``, not from a second lookup in the export sets. Re-deriving
would mean re-implementing each backend's mangled-name candidate matching
(clang's Mach-O leading-underscore variants, castxml's prefix-free names)
here, and a second opinion that disagreed with the first is precisely the
class of drift this repository's architecture rules exist to prevent. The
mapping is exact, because every one of the three members is produced by
one branch of those backends' ``visibility()`` helpers:

* ``PUBLIC``   -- the symbol was found in the *dynamic* export set.
* ``ELF_ONLY`` -- found only in the static symbol table, so it is not a
  dynamic export.
* ``HIDDEN``   -- found in neither set.

with one exception the flag below carries: under
``no_binary_evidence`` (a header-only dump) the same helpers fall back to
``PUBLIC`` with no export set ever populated, so ``PUBLIC`` there means
"no contrary evidence", not "observed exported".

*export_table_observed* is that flag, and it is what keeps this honest in
both directions: when no export table was observed at all, every
``exported_fact`` stays ``NOT_COLLECTED`` rather than claiming a confirmed
``False`` -- the same fail-closed treatment ``export_surface.py`` gives an
uncaptured export table. ``declared_fact`` is unconditionally
``present(True)``: these objects exist because a header-AST backend parsed
them out of a header.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..model import Fact, Function, Variable, Visibility

__all__ = ["export_table_observed", "stamp_header_ast_surface"]

_NO_TABLE = "no export table observed for this artifact"


def export_table_observed(
    exported_dynamic: Iterable[str],
    exported_static: Iterable[str],
    *,
    no_binary_evidence: bool = False,
) -> bool:
    """Whether an export table was really observed for this artifact.

    ``False`` for a header-only dump (there is no binary), and ``False``
    when both sets came back empty -- an empty observed table and a table
    that was never captured are indistinguishable here, and reading the
    ambiguous case as "exports nothing" would let a capture failure
    fabricate a lost export for every symbol in the library.
    """
    if no_binary_evidence:
        return False
    return bool(set(exported_dynamic) or set(exported_static))


def stamp_header_ast_surface(
    functions: Iterable[Function],
    variables: Iterable[Variable],
    *,
    observed_export_table: bool,
    producer: str | None = None,
) -> None:
    """In place: give every header-AST declaration its two surface facts.

    In place rather than returning rebuilt objects because neither field
    has a legacy sibling: ``bridge_legacy_and_fact``'s "treat every bridged
    field as immutable after construction" caveat is about a legacy value
    and its fact drifting apart, which cannot happen for a fact with no
    legacy counterpart.
    """
    declarations: list[Function | Variable] = [*functions, *variables]
    for decl in declarations:
        decl.declared_fact = Fact.present(True, producer=producer)
        if not observed_export_table:
            decl.exported_fact = Fact.not_collected(_NO_TABLE, producer=producer)
            continue
        decl.exported_fact = Fact.present(
            decl.visibility is Visibility.PUBLIC, producer=producer
        )
