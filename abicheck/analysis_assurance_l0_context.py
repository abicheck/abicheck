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

"""Per-side L0 binary/export-table presence status.

Split out of ``analysis_assurance.py`` (which sits at this repo's
``architecture/debt.yaml`` no-growth baseline) rather than added there --
mirrors ``analysis_assurance_layout.py``'s/``analysis_assurance_schema_
staleness.py``'s own splits for the identical reason, and this module
depends only on ``model.AbiSnapshot``, a real leaf.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import AbiSnapshot

__all__ = ["l0_context_status"]


def l0_context_status(old: AbiSnapshot, new: AbiSnapshot) -> tuple[str, list[str]]:
    """The L0 analogue of ``analysis_assurance``'s other context-status
    helpers' presence-then-asymmetry shape.

    Self-audit finding (P0.4 review): an ``AbiSnapshot`` need not carry any
    binary at all -- ``cli_scan_helpers._intrinsic_coverage`` already
    documents and reports this exact state as ``"no binary export table
    (snapshot-only input)"`` (``has_binary = bool(snap.elf or snap.pe or
    snap.macho)``), the same predicate this helper uses. A comparison where
    only one side carries a real binary (the other a synthetic/
    snapshot-only, headers-or-hand-built-only input) means every L0-derived
    signal -- exported-symbol presence/removal, SONAME, binding/visibility --
    was never even attempted for the binary-lacking side, the identical
    shape of gap already closed for L1 (DWARF)/L2 (headers)/L3 (build
    evidence). Fixed the same way: check each side's own binary presence
    directly and report the asymmetric case distinctly from "both sides have
    a binary" and "neither does" (a pure header/source-only comparison,
    which is a legitimate, supported, symmetric shape and not itself an
    asymmetry).
    """

    def _has_binary(snap: AbiSnapshot) -> bool:
        return bool(snap.elf or snap.pe or snap.macho)

    old_binary = _has_binary(old)
    new_binary = _has_binary(new)
    if not old_binary and not new_binary:
        return "not_evaluated", []
    if old_binary != new_binary:
        missing = "new" if old_binary else "old"
        return "asymmetric", [
            f"L0 binary context asymmetric: the {missing} side carries no "
            "binary export table at all (snapshot-only input) -- symbol-table "
            "-level evidence (exports, SONAME, binding/visibility) was never "
            "examined for that side"
        ]
    return "clean", []
