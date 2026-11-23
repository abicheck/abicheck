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

from collections.abc import Iterable, KeysView, Mapping

from ..model import Function
from .dwarf_type_names import strip_type_decorators as _strip_type_decorators


def missing_record_definitions(
    declarations: Mapping[str, str],
    definitions: KeysView[str],
    leaf_owners: Mapping[str, str | None],
    typedefs: Mapping[str, str],
    functions: Iterable[Function],
) -> tuple[str, ...]:
    """Report direct signature uses of forward declarations lacking a definition."""
    unresolved = declarations.keys() - definitions
    if not unresolved:
        return ()
    names = {name: name for name in unresolved if "::" in name}
    # A short spelling can be matched only when it identifies one record.

    for name, short in declarations.items():
        if name in unresolved and leaf_owners.get(short) == name:
            names.setdefault(short, name)

    aliases: dict[str, str | None] = {}
    for alias in typedefs:
        leaf = alias.rsplit("::", 1)[-1]
        aliases[leaf] = None if leaf in aliases else alias

    findings = set()
    for func in functions:
        for role, spelling in [
            ("return", func.return_type),
            *[(f"parameter {i}", arg.type) for i, arg in enumerate(func.params)],
        ]:
            bare = _strip_type_decorators(spelling)
            seen = set()
            while bare not in seen:
                seen.add(bare)
                target = bare if "::" in bare else aliases.get(bare)
                if target is None or target not in typedefs:
                    break
                bare = _strip_type_decorators(typedefs[target])
            record = names.get(bare)
            if record:
                findings.add(
                    f"{func.name} {role}: DWARF only declares {record}; "
                    "record layout and virtual slots were not verified"
                )
    return tuple(sorted(findings))
