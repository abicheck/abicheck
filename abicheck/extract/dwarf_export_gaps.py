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

"""Export-table records for exports a DWARF-only snapshot has no declaration for.

A DWARF-only dump (no headers) builds its functions and variables from
``DW_TAG_subprogram``/``DW_TAG_variable`` DIEs whose linkage name the
library exports. Some exports have no such DIE under their own name: a
``.symver`` alias whose implementation DIE is ``foo_v2``, an IFUNC whose DIE
is the resolver's target, a function from a translation unit built without
``-g``. Without a record those exports were simply absent from the snapshot,
so comparing against a side that *did* describe them read ``func_removed``
although the symbol is still exported (and old consumers still bind it).

The symbol-only lane (no headers, no DWARF) already records every export as
an export-table-only record (``itanium_export_function``); this module gives
the DWARF lane the same records for the exports DWARF left undescribed, so
the two lanes agree on what exists. It is extraction parity, not a
compare-side "still exported means not removed" rule (known-gaps "Do not
re-attempt"): a removal is still read from the export table.

Not filled, because another owner already models them or they are not
declarations: Itanium class metadata (``_ZT*``: vtables, typeinfo, VTTs,
thunks, TLS wrappers), guard variables (``_ZGV``), function-local statics
(``_ZZ``), and a constructor/destructor variant (``C1``/``D1``...) whose
family DWARF already describes through another variant.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..model import Function, Variable
from ..model.name_decoration import itanium_structors
from .export_symbol_identity import itanium_export_function, itanium_export_variable

__all__ = ["undescribed_export_records"]

# Exported names that are not plain function/variable declarations.
_NOT_A_DECLARATION = ("_ZT", "_ZGV", "_ZZ")


def _family(name: str) -> str:
    """A ctor/dtor variant folded to its complete-object spelling."""
    return itanium_structors.complete_object_spelling(name) or name


def _described(decls: Iterable[Function | Variable]) -> set[str]:
    out: set[str] = set()
    for d in decls:
        for spelling in (d.mangled, d.name):
            if spelling:
                out.add(_family(spelling))
    return out


def undescribed_export_records(
    functions: list[Function],
    variables: list[Variable],
    exported_functions: Iterable[str],
    exported_objects: Iterable[str],
) -> tuple[list[Function], list[Variable]]:
    """Export-table-only records for the exports no DWARF declaration names.

    *functions*/*variables* are the DWARF-built declarations; the exported
    name sets are the ABI-relevant dynamic exports the symbol-only lane would
    record (``extract.elf_symbol_classify._elf_classify_symbols``).
    """
    described_f = _described(functions)
    described_v = _described(variables)
    new_f = [
        itanium_export_function(name)
        for name in sorted(set(exported_functions))
        if not name.startswith(_NOT_A_DECLARATION) and _family(name) not in described_f
    ]
    new_v = [
        itanium_export_variable(name)
        for name in sorted(set(exported_objects))
        if not name.startswith(_NOT_A_DECLARATION) and name not in described_v
    ]
    return new_f, new_v
