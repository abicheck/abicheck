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

"""A virtual member function gaining or losing the ``final`` virt-specifier.

``final`` on a virtual function changes neither its vtable slot nor its
symbol, so built consumers are unaffected; a consumer class that overrides it
stops compiling ([class.virtual]/4), an ``API_BREAK``. Dropping ``final`` lets
overrides back in, but code compiled while the function was final may call it
devirtualized and never reach one, a risk.

``Function.is_final`` is tri-state: the pair is compared only when both sides
read the specifier (a header backend) and both declare the function virtual.
"""

from __future__ import annotations

from ..detector_registry import registry
from ..diff_helpers import make_change
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.snapshot import AbiSnapshot
from .public_surface import reconciled_function_surfaces

__all__ = ["final_specifier_changes"]


def final_specifier_changes(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    changes: list[Change] = []
    old_map, new_map = reconciled_function_surfaces(old, new)
    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None or not (f_old.is_virtual and f_new.is_virtual):
            continue
        if f_old.is_final is None or f_new.is_final is None:
            continue
        if f_old.is_final == f_new.is_final:
            continue
        kind = (
            ChangeKind.FUNC_BECAME_FINAL
            if f_new.is_final
            else ChangeKind.FUNC_LOST_FINAL
        )
        changes.append(
            make_change(
                kind,
                symbol=mangled,
                name=f_old.name,
                old="final" if f_old.is_final else "not final",
                new="final" if f_new.is_final else "not final",
                entity_id=f_old.entity_id or f_new.entity_id,
            )
        )
    return changes


@registry.detector("func_final_specifier")
def _diff_func_final_specifier(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    return final_specifier_changes(old, new)
