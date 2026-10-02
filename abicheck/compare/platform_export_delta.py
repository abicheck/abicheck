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

"""Preconditions for the raw PE/Mach-O export-table delta.

``diff_platform._diff_pe``/``_diff_macho_exports`` compare the two export
tables as name sets. Two rules decide what that comparison may conclude:

* a name missing from one table is a removal or an addition only when **both**
  tables were read (``compare.edge_query.export_table_covered``) -- a default
  or parse-failed block holds no exports, and diffing it reported every export
  of the other side as removed (design-hardening Phase 1, F1);
* a name a declaration already carries (a function *or* a variable) is the
  declaration-level detectors' to report, not this delta's.
"""

from __future__ import annotations

from ..model import AbiSnapshot
from .edge_query import export_table_covered

__all__ = ["both_export_tables_read", "declared_export_names"]


def declared_export_names(snap: AbiSnapshot) -> set[str]:
    """Display and link names of every declared function and variable.

    Variables were missing, so a removed variable's export was re-reported
    here as ``func_removed`` next to the declaration-level ``var_removed``.
    """
    names: set[str] = set()
    decls = snap.declarations
    for spelled in (
        *((f.name, f.mangled) for f in decls.functions),
        *((v.name, v.mangled) for v in decls.variables),
    ):
        names.update(n for n in spelled if n)
    return names


def both_export_tables_read(old: AbiSnapshot, new: AbiSnapshot, platform: str) -> bool:
    """Whether both sides' *platform* export tables were read."""
    return export_table_covered(old, platform) and export_table_covered(new, platform)
