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

"""The export axis of the declared-vs-exported split, as a finding.

``model/declaration_surface.py`` answers *whether* a declaration lost its
dynamic export while staying in the headers; this module is the one place
that turns that answer into a ``Change``. It lives beside the flat
``diff_symbols`` detectors rather than inside them because both the
function and the variable path need the identical construction, and
because ``diff_symbols.py`` sits at its ``architecture/debt.yaml``
no-growth baseline.

Why a distinct ``ChangeKind`` rather than a softened ``FUNC_REMOVED``:
the removal wording is simply false -- a consumer recompiling against the
new headers still finds the declaration -- while the break is entirely
real, because an already-linked consumer resolves that symbol at load
time and will not find it. Both kinds are therefore BREAKING; only the
description changes.

Both functions answer ``None`` unless *both* sides carry positive
evidence, so a pre-v46 snapshot on either side keeps the pre-existing
``FUNC_VISIBILITY_CHANGED``/``FUNC_REMOVED`` wording rather than silently
switching vocabulary on a comparison that cannot support the claim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..diff_helpers import make_change
from ..model import Function, Variable, export_only_loss
from ..model.change_catalog.kinds import ChangeKind

if TYPE_CHECKING:
    from ..checker_types import Change

__all__ = ["export_removed_still_declared"]


def export_removed_still_declared(
    mangled: str,
    old: Function | Variable,
    new: Function | Variable | None,
) -> Change | None:
    """The export-axis finding for *old*/*new*, or ``None`` if this is not
    one -- see the module docstring for when that is."""
    if not export_only_loss(old, new):
        return None
    assert new is not None  # export_only_loss is False for a missing new side
    kind = (
        ChangeKind.FUNC_EXPORT_REMOVED_STILL_DECLARED
        if isinstance(old, Function)
        else ChangeKind.VAR_EXPORT_REMOVED_STILL_DECLARED
    )
    return make_change(
        kind,
        symbol=mangled,
        name=old.name,
        old_value=old.visibility.value,
        new_value=new.visibility.value,
        # See Change.symbol_binding's docstring -- None when not captured.
        symbol_binding=old.elf_binding.value if old.elf_binding else None,
        entity_id=old.entity_id or new.entity_id,
    )
