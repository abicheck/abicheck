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

"""ADR-063 Phase 2 (ELF-symbol-only slice): ``entity_id`` population in
``dumper_elf_fallback.py``'s header-less, export-table-only snapshot path.

A raw ELF dynamic-symbol-table entry is unconditionally a real, observed,
distinguishing linker identity -- unlike DWARF's own ``linkage_name or
name`` fallback (see ``extract/dwarf_scope.py``'s own docstring), this
producer never constructs a ``Function``/``Variable`` except FROM an actual
export, so there is no "no real distinguishing spelling" case to guard
against. Every exported symbol therefore takes the genuine-mangled-name
branch, regardless of whether it happens to carry an Itanium ``_Z`` prefix
(Codex review, PR #1015, mirroring the identical DWARF finding).
"""

from __future__ import annotations

from pathlib import Path

from abicheck.dumper_elf_fallback import _build_symbol_only_snapshot
from abicheck.elf_metadata import ElfMetadata
from abicheck.model.dwarf_facts import AdvancedDwarfMetadata, DwarfMetadata
from abicheck.model.identity import EntityKind


def _snap(funcs: set[str], variables: set[str] = frozenset()):
    return _build_symbol_only_snapshot(
        Path("/nonexistent/lib.so"),
        "1.0",
        ElfMetadata(),
        DwarfMetadata(),
        AdvancedDwarfMetadata(),
        funcs,
        variables,
        set(),
        [],
        None,
    )


def test_itanium_mangled_export_gets_mangled_entity_id() -> None:
    snap = _snap({"_Z3addii"})
    func = next(f for f in snap.functions if f.name == "_Z3addii")
    assert func.entity_id is not None
    assert func.entity_id.kind == EntityKind.FUNCTION
    assert func.entity_id.extra == ("mangled", "_Z3addii")


def test_plain_c_export_gets_mangled_entity_id_too() -> None:
    """No `("extern_c",)` tag here: a plain C symbol is just as real and
    distinguishing an export as a mangled one, so it takes the identical
    genuine-mangled branch — see this module's own docstring."""
    snap = _snap({"plain_c_fn"})
    func = next(f for f in snap.functions if f.name == "plain_c_fn")
    assert func.entity_id is not None
    assert func.entity_id.extra == ("mangled", "plain_c_fn")


def test_non_itanium_but_real_linkage_name_is_not_misclassified() -> None:
    """The bug this test guards: a real, explicitly-linked, non-Itanium
    export (e.g. an ``asm("custom_name")``-labeled C++ function) must not
    collapse onto the same scope-free ``extern_c`` tag a genuinely
    unrelated plain-C symbol of the same name elsewhere would use --
    `Function.is_extern_c`'s own `_Z`-prefix heuristic is not a
    trustworthy signal, but `entity_id` no longer reads it for this gate."""
    snap = _snap({"custom_cpp_name"})
    func = next(f for f in snap.functions if f.name == "custom_cpp_name")
    # The pre-existing, unrelated heuristic still misreads this as
    # extern-"C" -- not this producer's `entity_id` construction to fix.
    assert func.is_extern_c is True
    assert func.entity_id is not None
    assert func.entity_id.extra == ("mangled", "custom_cpp_name")


def test_variable_export_gets_mangled_entity_id() -> None:
    snap = _snap(set(), {"plain_c_var"})
    var = next(v for v in snap.variables if v.name == "plain_c_var")
    assert var.entity_id is not None
    assert var.entity_id.kind == EntityKind.VARIABLE
    assert var.entity_id.extra == ("mangled", "plain_c_var")


def test_two_distinct_exports_never_collide() -> None:
    snap = _snap({"_Z3addii", "custom_cpp_name", "plain_c_fn"})
    ids = {f.entity_id for f in snap.functions}
    assert len(ids) == 3
