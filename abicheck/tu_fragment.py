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

"""ADR-050 D3/D4 (G32 Phase B/C) — the ``TuFragment``/``MergedTuFragments``
shapes and ``entity_key``, the cross-TU identity both the per-TU dump loop
(``dumper_manifest.py``) and the real compatible merge (``tu_merge.py``)
build on.

Lives in its own leaf module — importing only :mod:`abicheck.model` — so
``dumper_manifest.py`` and ``tu_merge.py`` can each depend on these shapes
without forming a ``dumper_manifest -> tu_merge -> dumper_manifest`` import
cycle: ``dumper_manifest.py`` calls into ``tu_merge.merge_fragments``, and
``tu_merge.py`` needs these same dataclasses, so neither of those two
modules can be the one that *defines* them without the other importing
back. ``dumper_manifest.py`` re-exports the names from here for backward
compatibility — existing ``from abicheck.dumper_manifest import
TuFragment`` call sites (tests included) keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import EnumType, Function, RecordType, Variable
from .model.identity import EntityId
from .model.semantic_ir import SemanticIR


@dataclass(frozen=True)
class TuFragment:
    """One translation unit's own header-AST parse, normalized to plain
    model entities (not raw AST) -- ADR-050 D3's "each producing a
    normalized ``TuFragment``".

    ``ast_producer``/``ast_toolchain``/``ast_fallback_reason``/
    ``ast_toolchain_supported``/``ast_toolchain_unsupported_reasons`` mirror
    the same per-parser provenance fields ``dumper._dump_elf``/``_dump_pe``/
    ``_dump_macho`` already stamp onto a single-TU ``AbiSnapshot`` -- kept
    per-fragment here (not just on the merged result) since a future
    heterogeneous-toolchain diagnostic (D4's ``HETEROGENEOUS_ABI_CONTEXT``)
    needs each TU's own value to compare, even though D3's own parse-time
    rule already forces one compiler/target per manifest today.
    """

    tu_name: str
    functions: tuple[Function, ...] = ()
    variables: tuple[Variable, ...] = ()
    types: tuple[RecordType, ...] = ()
    enums: tuple[EnumType, ...] = ()
    typedefs: dict[str, str] = field(default_factory=dict)
    # Qualified-name-keyed twin of ``typedefs`` (schema v25, G31 Phase C —
    # see ``AbiSnapshot.typedefs_qualified``). Carried per-fragment so
    # ``tu_merge.merge_fragments`` can fold it the same way it already
    # folds ``typedefs`` itself.
    typedefs_qualified: dict[str, str] = field(default_factory=dict)
    constants: dict[str, str] = field(default_factory=dict)
    # ``EntityId`` sidecars for the two dicts above (ADR-063 Phase 2, schema
    # v31 — see ``AbiSnapshot.typedef_entity_ids``), carried per-fragment for
    # the same reason ``typedefs_qualified`` is.
    typedef_entity_ids: dict[str, EntityId] = field(default_factory=dict)
    constant_entity_ids: dict[str, EntityId] = field(default_factory=dict)
    ast_producer: str = "castxml"
    ast_toolchain: dict[str, str] = field(default_factory=dict)
    ast_fallback_reason: str | None = None
    ast_toolchain_supported: bool | None = None
    ast_toolchain_unsupported_reasons: tuple[str, ...] = ()
    # ADR-050 D5, G32 Phase D: the resolved SYCL/DPC++ "host"/"device" kind,
    # None for an ordinary non-DPC++ TU parse. Mirrors the other AST
    # provenance fields above.
    frontend_context_kind: str | None = None


@dataclass(frozen=True)
class MergedTuFragments:
    """The merged result across every contributing TU's :class:`TuFragment`
    -- entity lists/dicts folded via :func:`abicheck.tu_merge.merge_fragments`
    (ADR-050 D4), plus one representative fragment's AST provenance (see
    that function's own docstring for why using any single contributing
    fragment's provenance is correct, not just convenient, under D3's own
    single-compiler-per-manifest rule).
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
    frontend_context_kind: str | None
    #: ADR-063 Phase 6 (multi-TU slice): built from the RAW, pre-merge
    #: fragments themselves, not from the flat fields above -- see
    #: ``dumper_manifest._manifest_semantic_ir``'s own docstring for why a
    #: real cross-TU declaration split (public forward declaration, private
    #: full definition) needs the per-fragment candidates
    #: ``merge_fragments`` itself has already folded away by the time it
    #: returns. ``None`` only for a caller that never sets it (there is
    #: currently none -- ``run_tu_loop`` always populates this).
    semantic_ir: SemanticIR | None = None


def entity_key(kind: str, name: str) -> tuple[str, str]:
    """The cross-TU identity a duplicate is detected against.

    Deliberately just ``(kind, name)`` -- for a :class:`Function`/
    :class:`Variable`, *name* is its mangled linker symbol (already
    excludes return type for every C++ mangling scheme this repo targets,
    and equals the plain name for C, which has no mangling); for a
    :class:`RecordType`/:class:`EnumType`/a typedef/a constant, *name* is
    the model's own (already possibly namespace-qualified) ``name``/dict
    key. ADR-050 D4's own text is explicit that ``entity_key`` "deliberately
    excludes return type" for exactly this reason -- folding it in would
    turn a same-TU return-type edit into an unrelated add+remove pair
    instead of one detected change, so this helper never looks at
    ``return_type``/``type`` at all.
    """
    return (kind, name)
