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

"""Whether a public declaration's export obligation is *established*.

Known gap "An `-I` include root makes another library's public headers this
component's export obligations" (2026-09-16): an `-I` root widens public
*provenance* so an API change reached through an umbrella header is still
compared, but provenance alone then also charged the binary with exporting
every declaration in every header that root made findable -- including a
sibling library's (PVXS: `iochooks.h` includes `version.h`, whose symbols
`libpvxs` exports, not `libpvxsIoc`).

Paths cannot separate that case from an umbrella header including the
library's own sub-header, so the maintainer ruling (2026-10-01) is to
*narrow the conclusion*, never to drop it. ADR-075 ownership already records
the run's target roots; every `-H` entry -- file or directory -- is one
(``workflows.ownership_request.with_target_roots``). A declaration a target
root covers keeps its full obligation. One the run's declared surface does
not cover (contract ``unresolved``, rule ``no_root``) is still reported, at
LOW confidence and worded as not established, because whether this binary
owes it is exactly what the evidence cannot say.

Only that one combination is narrowed: a snapshot with no recorded target
root (a pre-v52 baseline, a run with no `-H`) has no declared surface to be
outside of, so it keeps the previous behaviour unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model.extraction_scope import ownership_of
from ..model.ownership_rules import CONTRACT_UNRESOLVED

__all__ = ["ObligationStanding", "obligation_standing"]

#: The ownership rule id a declaration gets when no recorded root covers it.
_NO_ROOT = "no_root"


@dataclass(frozen=True)
class ObligationStanding:
    """How firmly a declaration's export obligation rests on this run's scope."""

    #: True when the run declared a surface and this declaration lies outside
    #: it -- reached only through ``#include`` and an include root.
    outside_declared_surface: bool


def _declared_roots(snapshot: object) -> tuple[str, ...]:
    scope = getattr(snapshot, "extraction_scope", None)
    rules = getattr(scope, "ownership_rules", None)
    return tuple(getattr(rules, "target_roots", ()) or ())


def obligation_standing(snapshot: object, decl: object) -> ObligationStanding:
    """*decl*'s standing under *snapshot*'s recorded extraction scope."""
    if not _declared_roots(snapshot):
        return ObligationStanding(outside_declared_surface=False)
    decision = ownership_of(decl)
    outside = (
        decision is not None
        and decision.contract == CONTRACT_UNRESOLVED
        and decision.rule_id == _NO_ROOT
    )
    return ObligationStanding(outside_declared_surface=outside)
