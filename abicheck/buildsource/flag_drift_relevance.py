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

"""Relevance of an enum-size build-flag flip to the compared surface.

``-fshort-enums`` (``ENUM_SIZE_FLAG_CHANGED``, ``build_diff.py``) only changes
the storage of an *enum* type. When both sides' established surfaces -- the
parsed public headers and the DWARF type table -- declare no enum at all, the
flip cannot change any size, offset or calling convention the library exposes,
so the finding is a toolchain-hygiene note, not a compatibility risk
(case103_toolchain_flag_drift).

The finding is never dropped (record before disposing): it keeps its kind and
is demoted to ``COMPATIBLE`` through ``Change.effective_verdict`` with a
disclosed ``modulation_reason``/``modulation_rule``. A side whose enum set is
not established (no header AST and no DWARF) keeps the RISK verdict -- absent
evidence is not evidence of no enums.
"""

from __future__ import annotations

from ..model import AbiSnapshot
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict

MODULATION_RULE = "build-flag-relevance/enum-size"
MODULATION_REASON = "no-enum-in-compared-surface"


def _enum_free(snapshot: AbiSnapshot | None) -> bool:
    """True only when *snapshot*'s enum set is established and empty.

    Established means a confirmed public-header AST (``from_headers`` and not
    inferred from a legacy document). DWARF alone is not enough: the CU walk
    leaves no marker for a unit it skipped, so an empty DWARF enum table does
    not prove the binary has none. When DWARF is present its enums must also
    be empty, so an enum in a struct the headers do not show still counts.
    """
    if snapshot is None:
        return False
    if not snapshot.from_headers or snapshot.from_headers_inferred:
        return False
    if snapshot.declarations.enums:
        return False
    dwarf = snapshot.declarations.debug_layout
    return not (dwarf is not None and dwarf.has_dwarf and dwarf.enums)


def demote_unexposed_enum_size_flag(
    changes: list[Change],
    old_snapshot: AbiSnapshot | None,
    new_snapshot: AbiSnapshot | None,
) -> None:
    """Demote ``ENUM_SIZE_FLAG_CHANGED`` when neither side declares an enum."""
    if not (_enum_free(old_snapshot) and _enum_free(new_snapshot)):
        return
    for change in changes:
        if change.kind is ChangeKind.ENUM_SIZE_FLAG_CHANGED and (
            change.effective_verdict is None
        ):
            change.effective_verdict = Verdict.COMPATIBLE
            change.modulation_reason = MODULATION_REASON
            change.modulation_rule = MODULATION_RULE
