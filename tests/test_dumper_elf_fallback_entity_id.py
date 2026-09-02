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

See ``_elf_fallback_mangled_name``'s own docstring for the full "why":
symbol-table-only evidence cannot distinguish a genuine plain-C export from
a real, explicitly-linked non-Itanium C++ export (both are just a bare,
un-prefixed identifier string), so this producer defaults to matching the
two header-AST/DWARF backends' own extern-"C" convention for the far more
common plain-C case, an accepted, documented residual gap for the rarer
asm-labeled case (Codex review, PR #1015, two rounds).
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


def test_plain_c_export_matches_the_extern_c_convention() -> None:
    """No ``("mangled", ...)`` tag here: this matches the two header-AST/
    DWARF backends' own ``("extern_c",)`` convention for a genuine plain-C
    export -- see this module's own docstring for why that is the accepted
    default rather than always trusting the raw symbol as "mangled"."""
    snap = _snap({"plain_c_fn"})
    func = next(f for f in snap.functions if f.name == "plain_c_fn")
    assert func.entity_id is not None
    assert func.entity_id.extra == ("extern_c",)
    assert func.entity_id.leaf_name == "plain_c_fn"


def test_asm_labeled_export_is_a_documented_residual_gap() -> None:
    """A real, explicitly-linked, non-Itanium export (e.g. an
    ``asm("custom_name")``-labeled C++ function) is structurally
    indistinguishable from a genuine plain-C export in symbol-table-only
    evidence, so it takes the identical extern-"C"-shaped branch here --
    an accepted, documented limitation (see this module's own docstring
    and ``_elf_fallback_mangled_name``'s), not a bug this test is pinning
    as "correct": it exists so a future attempt to disambiguate this case
    doesn't silently regress the far more common plain-C one instead."""
    snap = _snap({"custom_cpp_name"})
    func = next(f for f in snap.functions if f.name == "custom_cpp_name")
    assert func.is_extern_c is True
    assert func.entity_id is not None
    assert func.entity_id.extra == ("extern_c",)


def test_variable_export_matches_the_extern_c_convention() -> None:
    snap = _snap(set(), {"plain_c_var"})
    var = next(v for v in snap.variables if v.name == "plain_c_var")
    assert var.entity_id is not None
    assert var.entity_id.kind == EntityKind.VARIABLE
    assert var.entity_id.extra == ("extern_c",)


def test_distinct_exports_never_collide_regardless_of_mangling() -> None:
    snap = _snap({"_Z3addii", "custom_cpp_name", "plain_c_fn"})
    ids = {f.entity_id for f in snap.functions}
    assert len(ids) == 3
