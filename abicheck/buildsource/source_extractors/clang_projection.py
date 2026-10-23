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

"""Project the clang AST parser's public surface into L4 source entities.

Plan ``l4-l2-extraction-convergence`` Phase 4 (ADR-063): the L2 clang parser
(``dumper_clang._ClangAstParser``) is a source of L4 entities alongside the
L4 clang emitter. This module is the production projection. It only *reads*
the parser through the ``parse_public_*`` methods, which apply the
public-surface provenance filter to the declaration entries before the
canonical parse. A projection built from the unfiltered ``parse_*`` methods
would carry every declaration in the AST, system headers included (66953
out-of-scope declarations on the 253-TU catalog corpus).

The mapping itself is ``base.entity_from_*``; this module adds no fields.
The fields that still do not agree with the current L4 emitter (``origin``,
qualified member names, mangled names of declarations clang emits without a
``mangledName``, and hash/default spelling) are recorded in
``docs/contribute/known-gaps.md`` and stay excluded from comparability until
they are closed.
"""

from __future__ import annotations

from typing import Any

from ...dumper_clang import _ClangAstParser, _Decl
from ..source_abi import SourceEntity
from . import base


class PublicSurfaceClangParser(_ClangAstParser):
    """A clang AST parser narrowed to the public-header surface.

    The declaration lists are filtered by provenance once, here, before any
    ``parse_*`` call reads them, so :func:`project_clang_surface` never sees a
    system-header declaration. The base class's lookup indexes (base classes,
    vtables) were built from the full AST in ``__init__`` and keep resolving
    against it; only the projected lists are narrowed. Empty when the parser
    was built without a public set.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._functions = self._public(self._functions)
        self._variables = self._public(self._variables)
        self._records = self._public(self._records)
        self._enums = self._public(self._enums)

    def _is_public(self, entry: _Decl) -> bool:
        return self._have_public_set and self._decl_is_public(entry)

    def _public(self, entries: list[_Decl]) -> list[_Decl]:
        return [e for e in entries if self._is_public(e)]

    def _named_typedefs(self) -> list[tuple[_Decl, str]]:
        return [(e, n) for e, n in super()._named_typedefs() if self._is_public(e)]


def project_clang_surface(parser: PublicSurfaceClangParser) -> list[SourceEntity]:
    """Entities for every public-surface declaration *parser* holds.

    Empty when the parser was built without a public set.
    """
    entities = [base.entity_from_function(f) for f in parser.parse_functions()]
    entities += [base.entity_from_variable(v) for v in parser.parse_variables()]
    entities += [base.entity_from_record(r) for r in parser.parse_types()]
    entities += [base.entity_from_enum(e) for e in parser.parse_enums()]
    entities += [
        base.entity_from_typedef(name, target)
        for name, target in parser.parse_typedefs_qualified().items()
    ]
    return entities
