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

"""The evidence rung a comparison may judge at when its operands differ.

Header-parsed declarations and DWARF/symbol-derived ones name and shape the
same entities differently (a header destructor ``~KLU`` against the
``_ZN3KLUD2Ev`` export, a record header parsing saw against one DWARF never
emitted). Comparing a header-derived operand against a header-less one reads
that difference in *evidence* as a difference in the *library*: an
identical binary compared with headers given for the old side only reported
removed types and functions and exited 4.

A difference in evidence must narrow the conclusion, never manufacture one
(AGENTS.md, "Weaker evidence narrows conclusions"). So when exactly one
operand is header-derived, the pair is judged at the highest rung both
operands reach -- ``binary`` -- through the same joint projection an
explicit ``--depth binary`` uses (``project_pair_to_depth``). The asymmetry
itself stays reported (``layer_coverage_asymmetric``, partial assurance).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "asymmetric_header_evidence_warning",
    "common_evidence_depth",
    "effective_compare_depth",
]


def common_evidence_depth(old: AbiSnapshot, new: AbiSnapshot) -> str | None:
    """``"binary"`` when exactly one operand is header-derived, else ``None``
    (both operands reach the same rung: nothing to cap)."""
    return "binary" if bool(old.from_headers) != bool(new.from_headers) else None


def effective_compare_depth(
    requested: str | None, old: AbiSnapshot, new: AbiSnapshot
) -> str | None:
    """The rung a comparison classifies at: an explicitly requested depth
    wins (its floor check reports a rung an operand cannot reach); otherwise
    the operands' common rung."""
    return requested if requested is not None else common_evidence_depth(old, new)


def asymmetric_header_evidence_warning(old: AbiSnapshot) -> str:
    """The coverage warning a pair judged at its common rung carries."""
    side, other = ("old", "new") if old.from_headers else ("new", "old")
    return (
        f"headers were given for the {side} side only: the {other} side has no "
        "header-level evidence, so the pair was compared at depth binary "
        "(exported symbols) and header-level changes were not assessed; give "
        "headers for both sides to compare declarations"
    )
