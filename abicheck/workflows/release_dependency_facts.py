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

"""The per-member dependency facts a release records, read from the binary
metadata each side's snapshot already carries.

One reader per container format, all producing the same per-side shape:

* ELF: ``DT_SONAME`` and ``DT_NEEDED``.
* PE/COFF: the import directory's DLL names, plus the delay-load directory's.
* Mach-O: ``LC_LOAD_DYLIB``, ``LC_LOAD_WEAK_DYLIB`` and ``LC_REEXPORT_DYLIB``
  install names, with ``LC_ID_DYLIB`` as the side's own name.

Nothing is inferred. A fact a snapshot did not capture is named in the side's
``incomplete`` list (a legacy snapshot written before the field existed), and a
side with no format metadata at all, or a member with neither side readable,
carries an explicit ``not_evaluated`` state -- never an empty edge list that
would read as "no dependencies".

The projection half (nodes, edges, layout) lives in
``report/release_dependency_graph.py``; this module only records facts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..model.snapshot import AbiSnapshot

_NOT_EVALUATED_NO_METADATA = "no ELF, PE/COFF or Mach-O metadata on this side"
_NOT_EVALUATED_MEMBER = "no dependency metadata was read from either side"


def _unique(names: list[str]) -> list[str]:
    """*names* with repeats dropped, first occurrence kept in order."""
    return list(dict.fromkeys(names))


def _side_facts(snapshot: AbiSnapshot | None) -> dict[str, object] | None:
    """One side's dependency facts; ``None`` when the side was not read."""
    if snapshot is None:
        return None
    elf = getattr(snapshot, "elf", None)
    if elf is not None:
        return {
            "format": "elf",
            "soname": elf.soname or None,
            "needed": list(elf.needed),
        }
    pe = getattr(snapshot, "pe", None)
    if pe is not None:
        needed = list(pe.imports) + list(pe.delay_imports or {})
        side: dict[str, object] = {
            "format": "pe",
            "soname": None,
            "needed": _unique(needed),
        }
        if pe.delay_imports is None:
            side["incomplete"] = ["delay_imports"]
        return side
    macho = getattr(snapshot, "macho", None)
    if macho is not None:
        weak = macho.weak_dependent_libs
        needed = (
            list(macho.dependent_libs) + list(weak or []) + list(macho.reexported_libs)
        )
        side = {
            "format": "macho",
            "soname": macho.install_name or None,
            "needed": _unique(needed),
        }
        if weak is None:
            side["incomplete"] = ["weak_dependent_libs"]
        return side
    return {"state": "not_evaluated", "reason": _NOT_EVALUATED_NO_METADATA}


def member_dependency_facts(
    old_snapshot: AbiSnapshot | None, new_snapshot: AbiSnapshot | None
) -> dict[str, object]:
    """``{"old": {...}, "new": {...}}`` for the sides that were read, or
    ``{"state": "not_evaluated", "reason": ...}`` when neither was."""
    block: dict[str, object] = {
        side: facts
        for side, facts in (
            ("old", _side_facts(old_snapshot)),
            ("new", _side_facts(new_snapshot)),
        )
        if facts is not None
    }
    if block:
        return block
    return {"state": "not_evaluated", "reason": _NOT_EVALUATED_MEMBER}
