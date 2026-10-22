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

"""Whether consumers provably cannot see a type's layout (decision 2A of the
design-hardening plan).

``internal_leak.detect_internal_leaks`` lets a pointer-only, layout-only
change to an internal type pass only on this structural proof, never on the
namespace's name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.vocabulary import ScopeOrigin

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot


def layout_proven_invisible(tname: str, old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """Whether consumers provably cannot see *tname*'s layout on either side.

    Proven means: every record for *tname* that a side carries is opaque (only
    forward-declared to consumers) or defined in a project header outside the
    public set. A record with no resolved qualified name whose bare name is
    *tname*'s leaf counts as a candidate, so it needs the same proof. A side without the record contributes nothing; no record at
    all, or any other origin (``UNKNOWN`` included), is not proof.
    """
    leaf = tname.rsplit("::", 1)[-1]
    seen = False
    for snap in (old, new):
        for rec in snap.declarations.types:
            # A record whose qualified identity is unresolved may be *tname*
            # under a bare name: it must be proven too, never skipped.
            unresolved_bare = rec.qualified_name is None and rec.name == leaf
            if tname not in (rec.name, rec.qualified_name) and not unresolved_bare:
                continue
            seen = True
            if not (rec.is_opaque or rec.origin is ScopeOrigin.PRIVATE_HEADER):
                return False
    return seen


__all__ = ["layout_proven_invisible"]
