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

__all__ = [
    "operand_is_live_binary",
    "order_proof_needed",
    "should_resolve_with_order_witness",
]


def order_proof_needed(old: Any, new: Any) -> bool:
    """Whether comparing *old* with *new* consults ``header_canonical``.

    Mirrors the ADR-082 comparison rule (``comparability_profile``): the proof is
    read only when the two recorded ``header_sequence`` values differ (a
    permutation, or an insertion or removal judged on the canonical sequences).
    Equal sequences, both sides already proven, or a difference the gate's
    scope-confirmed evolution carve-out already waives need nothing more.
    """
    old_c, new_c = getattr(old, "contract", None), getattr(new, "contract", None)
    if old_c is None or new_c is None:
        return False
    old_seq = old_c.profile_fields.get("header_sequence")
    new_seq = new_c.profile_fields.get("header_sequence")
    if old_seq == new_seq:
        return False
    if old_c.header_canonical is not None and new_c.header_canonical is not None:
        return False
    # The scope-confirmed evolution carve-out (an added or removed header,
    # survivors in order) waives the difference without reading the proof.
    from abicheck.comparability_profile import (
        header_sequence_change_is_scope_confirmed,
    )

    return not header_sequence_change_is_scope_confirmed(old_c, new_c)


def should_resolve_with_order_witness(
    pair: Any, *, live: tuple[bool, bool] | None = None
) -> bool:
    """Whether resolving *pair* again with the witness on can change the result.

    Only when the comparison reads the proof and every side still missing one is
    a live binary: a stored snapshot without a proof cannot gain one, and the
    reorder carve-out needs both sides proven, so re-dumping the live side would
    cost a full resolution for nothing. *live* gives each side's liveness as
    ``(old, new)``; without it a side is live when its ``*_fmt`` is set.
    """
    if not order_proof_needed(pair.old, pair.new):
        return False
    if live is None:
        live = (pair.old_fmt is not None, pair.new_fmt is not None)
    for snap, is_live in ((pair.old, live[0]), (pair.new, live[1])):
        if snap.contract.header_canonical is None and not is_live:
            return False
    return True


def operand_is_live_binary(path: Any) -> bool:
    """Whether a compare operand is dumped live rather than loaded as a snapshot.

    A GNU ld linker script (``libfoo.so`` holding ``INPUT(libfoo.so.1)``) has no
    binary format itself, but the resolver follows it to a live binary, so the
    chain's end decides (review: a script operand must not read as stored).
    """
    from abicheck.binary_utils import resolve_linker_script_chain

    from ..input_resolution import detect_binary_format

    if path is None:
        return False
    return detect_binary_format(resolve_linker_script_chain(path)) is not None
