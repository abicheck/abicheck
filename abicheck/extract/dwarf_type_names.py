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

"""Type-name helpers for the DWARF walk (moved out of dwarf_snapshot)."""

from __future__ import annotations

__all__ = ["strip_type_decorators"]


def strip_type_decorators(type_name: str) -> str:
    """Strip pointer/reference/const/volatile/array suffixes from a type name.

    Used for reachability analysis — we need the base type name to follow
    type references transitively.
    """
    name = type_name.strip()
    # Remove trailing array brackets
    while name.endswith("[]"):
        name = name[:-2].strip()
    # Remove trailing pointer/reference markers
    while name.endswith(("*", "&", "&&")):
        if name.endswith("&&"):
            name = name[:-2].strip()
        else:
            name = name[:-1].strip()
    # Remove leading qualifiers (loop until none remain)
    _qualifiers = ("const ", "volatile ", "restrict ")
    changed = True
    while changed:
        changed = False
        for prefix in _qualifiers:
            if name.startswith(prefix):
                name = name[len(prefix) :]
                changed = True
    return name.strip()
