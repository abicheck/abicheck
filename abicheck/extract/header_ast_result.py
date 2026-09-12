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

"""``ElfHeaderAstResult`` -- the one shape a header-AST resolve returns.

A pure DTO, lifted out of ``dumper_manifest.py`` (which owns the
*resolution*, not the shape) so that module stays under its 800-line
production ceiling, and so the shape lives in ``extract`` with the rest of
the header-AST extraction vocabulary (ADR-061). ``dumper_manifest``
re-exports it, so every existing ``from .dumper_manifest import
ElfHeaderAstResult`` import is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..model import EnumType, Function, RecordType, Variable
from ..model.identity import EntityId
from ..model.semantic_ir import SemanticIR

__all__ = ["ElfHeaderAstResult"]


@dataclass(frozen=True)
class ElfHeaderAstResult:
    """The single result shape :func:`resolve_header_ast_result` returns for
    both the legacy single-header path and a real manifest -- everything a
    format handler's snapshot-assembly step needs, so it never has to know
    which of the two actually ran.
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
    ast_producer: str
    ast_toolchain: dict[str, str]
    ast_fallback_reason: str | None
    ast_toolchain_supported: bool | None
    ast_toolchain_unsupported_reasons: tuple[str, ...]
    is_clang: bool
    provenance_headers: tuple[Path, ...]
    frontend_context_kind: str | None = None
    # ADR-063 Phase 6 (second/third/fourth slices): the canonical SemanticIR
    # projection of this same merged result -- computed once, here, so both
    # the legacy single-TU dump (`_dump_elf`) and a real manifest dump share
    # one normalizer call instead of each format handler recomputing it from
    # `functions`/`variables`/`types`/`enums`/`typedefs_qualified`/
    # `typedef_entity_ids`/`constants`/`constant_entity_ids` above. See
    # `extract/semantic_normalizer.py`'s own docstring for each slice's scope.
    semantic_ir: SemanticIR | None = None
