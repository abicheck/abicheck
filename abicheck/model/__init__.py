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

"""ABI data model — the shapes every other responsibility package agrees on.

``abicheck.model`` is ADR-061's innermost ring: it declares what an ABI fact
*is* and imports nothing that produces, compares, judges or renders one. This
module is the supported import surface (``from abicheck.model import
AbiSnapshot`` and friends); the submodules below are where each shape is
owned.

| submodule | owns |
|---|---|
| ``vocabulary`` | the closed enums an entity is described with |
| ``declarations`` | ``Param``/``Function``/``Variable`` |
| ``entities`` | ``TypeField``/``RecordType``/``EnumMember``/``EnumType`` |
| ``extraction_contract`` | ADR-050 comparability fingerprints, dependency ledger |
| ``snapshot`` | ``AbiSnapshot`` itself |
| ``first_wins_index`` | the keyed-index primitive ``AbiSnapshot.index`` builds on |
| ``*_facts`` | binary/debug facts per format (ELF, PE, Mach-O, DWARF, SYCL, kABI) |
| ``stdlib_surface`` | the snapshot-aware ``std::``-filtering predicate |

The ``*_facts`` modules are the dataclass halves of the flat ``*_metadata.py``
parsers, split out so ``model`` can own a fact's shape without depending on
the extractor that fills it in. Each parser re-exports its own types, so the
historical ``from abicheck.elf_metadata import ElfMetadata`` spelling still
resolves.
"""

from __future__ import annotations

from ..name_classification import (
    COMPILER_INTERNAL_TYPES as COMPILER_INTERNAL_TYPES,
    canonicalize_type_name as canonicalize_type_name,
    cv_qualifiers_only_differ as cv_qualifiers_only_differ,
    func_signature_cv_only_differ as func_signature_cv_only_differ,
    is_abi_surface_type_name as is_abi_surface_type_name,
    is_compiler_internal_type as is_compiler_internal_type,
    is_cxx_runtime_library as is_cxx_runtime_library,
    is_non_abi_surface_type as is_non_abi_surface_type,
)
from .availability import FactStatus as FactStatus
from .declarations import Function as Function, Param as Param, Variable as Variable
from .elf_facts import SymbolBinding as SymbolBinding
from .entities import (
    EnumMember as EnumMember,
    EnumType as EnumType,
    RecordType as RecordType,
    TypeField as TypeField,
    record_layout_facts as record_layout_facts,
    resolve_vptr_offset_bits as resolve_vptr_offset_bits,
)
from .extraction_contract import (
    DependencyInfo as DependencyInfo,
    ExtractionContract as ExtractionContract,
)
from .fact import Fact as Fact, replace_with_fact_sync as replace_with_fact_sync
from .fact_registry import (
    FACT_REGISTRY as FACT_REGISTRY,
    FactDefinition as FactDefinition,
    FactLifecycle as FactLifecycle,
)
from .snapshot import AbiSnapshot as AbiSnapshot
from .stdlib_surface import stdlib_namespaces_excluded as stdlib_namespaces_excluded
from .vocabulary import (
    AccessLevel as AccessLevel,
    ElfVisibility as ElfVisibility,
    ParamKind as ParamKind,
    ScopeOrigin as ScopeOrigin,
    Visibility as Visibility,
)

__all__ = [
    "COMPILER_INTERNAL_TYPES",
    "FACT_REGISTRY",
    "AbiSnapshot",
    "AccessLevel",
    "DependencyInfo",
    "ElfVisibility",
    "EnumMember",
    "EnumType",
    "ExtractionContract",
    "Fact",
    "FactDefinition",
    "FactLifecycle",
    "FactStatus",
    "Function",
    "Param",
    "ParamKind",
    "RecordType",
    "ScopeOrigin",
    "SymbolBinding",
    "TypeField",
    "Variable",
    "Visibility",
    "canonicalize_type_name",
    "cv_qualifiers_only_differ",
    "func_signature_cv_only_differ",
    "is_abi_surface_type_name",
    "is_compiler_internal_type",
    "is_cxx_runtime_library",
    "is_non_abi_surface_type",
    "record_layout_facts",
    "replace_with_fact_sync",
    "resolve_vptr_offset_bits",
    "stdlib_namespaces_excluded",
]
