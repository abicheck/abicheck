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

"""Whether a live comparison must resolve again with the header-order witness on.

A live ``compare`` resolves both sides with the ADR-082 witness's second dump
deferred (``extract.header_order_witness.deferred_order_witness``). This module
decides, from what the two resolved snapshots recorded, whether that comparison
reads the proof and whether resolving again could supply it.
"""

from __future__ import annotations

from typing import Any

__all__ = ["order_proof_needed", "should_resolve_with_order_witness"]


def order_proof_needed(old: Any, new: Any) -> bool:
    """Whether comparing *old* with *new* consults ``header_canonical``.

    Mirrors the ADR-082 comparison rule (``comparability_profile``): the proof is
    read only when the two recorded ``header_sequence`` values differ (a
    permutation, or an insertion or removal judged on the canonical sequences).
    Equal sequences, or both sides already proven, need nothing more.
    """
    old_c, new_c = getattr(old, "contract", None), getattr(new, "contract", None)
    if old_c is None or new_c is None:
        return False
    old_seq = old_c.profile_fields.get("header_sequence")
    new_seq = new_c.profile_fields.get("header_sequence")
    if old_seq == new_seq:
        return False
    return old_c.header_canonical is None or new_c.header_canonical is None


def should_resolve_with_order_witness(pair: Any) -> bool:
    """Whether resolving *pair* again with the witness on can change the result.

    Only when the comparison reads the proof and every side still missing one is
    a live binary (``*_fmt`` set): a stored snapshot without a proof cannot gain
    one, and the reorder carve-out needs both sides proven, so re-dumping the live
    side would cost a full resolution for nothing.
    """
    if not order_proof_needed(pair.old, pair.new):
        return False
    for snap, fmt in ((pair.old, pair.old_fmt), (pair.new, pair.new_fmt)):
        if snap.contract.header_canonical is None and fmt is None:
            return False
    return True
