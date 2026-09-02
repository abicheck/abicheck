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

"""``parse_header_ast_fields`` -- the single-parser (non-manifest) choke
point for a header-AST parser's own ``parse_*()`` methods, feeding both the
legacy ``AbiSnapshot`` field shapes and a real :class:`~abicheck.model.
semantic_ir.SemanticIR` (ADR-063 Phase 6, third and fourth slices).

``dumper.py``'s ``_dump_pe``/``_dump_macho`` have no ``--dump-manifest``
support (ADR-050 D3 is ELF-scoped) and so never go through
``dumper_manifest.resolve_header_ast_result``'s manifest-aware merge -- this
module is their equivalent. Split out as its own leaf module, rather than
living alongside ``resolve_header_ast_result`` in ``dumper_manifest.py`` or
inlined at either PE/Mach-O construction site in ``dumper.py``, because
both of those files are already at (or, for ``dumper.py``, very near) their
own ``architecture/debt.yaml`` size baseline/cap -- this module absorbs the
actual parse-and-normalize logic so each construction site needs only one
function call plus one new ``semantic_ir=`` keyword instead of a second,
inline ``normalize_header_ast()`` call.

Leaf module: imports only ``model``/``extract.semantic_normalizer`` (ADR-061
D10) -- *parser* is typed structurally (:class:`_HeaderAstParser`, a
``Protocol``) rather than by importing ``dumper_castxml._CastxmlParser``/
``dumper_clang._ClangAstParser`` directly, which would be an ``extract ->``
(unclassified flat root module) edge the architecture gate forbids for a
migrated package -- both concrete parser classes already satisfy this
Protocol structurally, so no change is needed on their side.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from ..model import EnumType, Function, RecordType, Variable
from ..model.identity import EntityId
from ..model.semantic_ir import SemanticIR
from .semantic_normalizer import normalize_header_ast

__all__ = ["HeaderAstFields", "parse_header_ast_fields"]


class _HeaderAstParser(Protocol):
    """The subset of ``dumper_castxml._CastxmlParser``'s/
    ``dumper_clang._ClangAstParser``'s public surface this module needs --
    both already satisfy this structurally, with no import of either
    concrete class (see this module's own docstring)."""

    def parse_functions(self) -> Iterable[Function]: ...
    def parse_variables(self) -> Iterable[Variable]: ...
    def parse_types(self) -> Iterable[RecordType]: ...
    def parse_enums(self) -> Iterable[EnumType]: ...
    def parse_typedefs(self) -> dict[str, str]: ...
    def parse_typedefs_qualified(self) -> dict[str, str]: ...
    def parse_constants(self) -> dict[str, str]: ...
    def parse_typedef_entity_ids(self) -> dict[str, EntityId]: ...
    def parse_constant_entity_ids(self) -> dict[str, EntityId]: ...


@dataclass(frozen=True)
class HeaderAstFields:
    """The already-parsed-object fields one header-AST *parser* feeds into a
    single-TU ``AbiSnapshot`` construction, plus the ``SemanticIR``
    normalized from them.
    """

    functions: tuple[Function, ...]
    variables: tuple[Variable, ...]
    types: tuple[RecordType, ...]
    enums: tuple[EnumType, ...]
    typedefs: dict[str, str]
    typedefs_qualified: dict[str, str]
    constants: dict[str, str]
    typedef_entity_ids: dict[str, EntityId]
    constant_entity_ids: dict[str, EntityId]
    semantic_ir: SemanticIR


def parse_header_ast_fields(
    parser: _HeaderAstParser, *, producer: str
) -> HeaderAstFields:
    """Run *parser*'s own ``parse_*()`` methods once each and normalize the
    result into a :class:`SemanticIR` alongside them -- see this module's
    own docstring for why this exists as a dedicated function rather than
    inline calls at each call site.
    """
    functions = tuple(parser.parse_functions())
    variables = tuple(parser.parse_variables())
    types = tuple(parser.parse_types())
    enums = tuple(parser.parse_enums())
    typedefs_qualified = parser.parse_typedefs_qualified()
    typedef_entity_ids = parser.parse_typedef_entity_ids()
    constants = parser.parse_constants()
    constant_entity_ids = parser.parse_constant_entity_ids()
    return HeaderAstFields(
        functions=functions,
        variables=variables,
        types=types,
        enums=enums,
        typedefs=parser.parse_typedefs(),
        typedefs_qualified=typedefs_qualified,
        constants=constants,
        typedef_entity_ids=typedef_entity_ids,
        constant_entity_ids=constant_entity_ids,
        semantic_ir=normalize_header_ast(
            types=types,
            enums=enums,
            typedefs_qualified=typedefs_qualified,
            typedef_entity_ids=typedef_entity_ids,
            producer=producer,
            functions=functions,
            variables=variables,
            constants=constants,
            constant_entity_ids=constant_entity_ids,
        ),
    )
