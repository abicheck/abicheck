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

"""Identify incomplete records in exported DWARF signatures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .dwarf_type_names import strip_type_decorators as _strip_type_decorators

if TYPE_CHECKING:
    from ..dwarf_snapshot import _DwarfSnapshotBuilder


def missing_record_definitions(builder: _DwarfSnapshotBuilder) -> tuple[str, ...]:
    """Report direct signature uses of forward declarations lacking a definition."""
    unresolved = (
        builder._declaration_only.keys() - builder._record_by_qualified_name.keys()
    )
    if not unresolved:
        return ()
    names = {name: name for name in unresolved if "::" in name}
    # A short spelling can be matched only when it identifies one record.

    for name, short in builder._declaration_only.items():
        if name in unresolved and builder._record_leaf_owner.get(short) == name:
            names.setdefault(short, name)

    aliases: dict[str, str | None] = {}
    for alias in builder.typedefs:
        leaf = alias.rsplit("::", 1)[-1]
        aliases[leaf] = None if leaf in aliases else alias

    findings = set()
    for func in builder.functions:
        for role, spelling in [
            ("return", func.return_type),
            *[(f"parameter {i}", arg.type) for i, arg in enumerate(func.params)],
        ]:
            bare = _strip_type_decorators(spelling)
            seen = set()
            while bare not in seen:
                seen.add(bare)
                target = bare if "::" in bare else aliases.get(bare)
                if target is None or target not in builder.typedefs:
                    break
                bare = _strip_type_decorators(builder.typedefs[target])
            record = names.get(bare)
            if record:
                findings.add(
                    f"{func.name} {role}: DWARF only declares {record}; "
                    "record layout and virtual slots were not verified"
                )
    return tuple(sorted(findings))
